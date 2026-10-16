"""Unit tests for `src/tools/web_search.py` (AC19-AC22). DDGS is always faked."""

from typing import Any

import pytest
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
from pydantic import ValidationError

from src.tools import web_search as web_search_module
from src.tools.base import TRANSIENT_PREFIX
from src.tools.web_search import WebSearchArgs, web_search


class FakeDDGS:
    """Stand-in for DDGS: records calls, returns or raises what the test configured."""

    results: list[dict[str, Any]] = []
    error: Exception | None = None
    init_kwargs: list[dict[str, Any]] = []
    text_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        FakeDDGS.init_kwargs.append(kwargs)

    def text(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        FakeDDGS.text_calls.append((args, kwargs))
        if FakeDDGS.error is not None:
            raise FakeDDGS.error
        return FakeDDGS.results


@pytest.fixture(autouse=True)
def fake_ddgs(monkeypatch: pytest.MonkeyPatch) -> type[FakeDDGS]:
    """Replace DDGS in web_search with a clean FakeDDGS for every test."""
    # WHY: no test may reach the internet; state is reset so tests stay independent.
    FakeDDGS.results = []
    FakeDDGS.error = None
    FakeDDGS.init_kwargs = []
    FakeDDGS.text_calls = []
    monkeypatch.setattr(web_search_module, "DDGS", FakeDDGS)
    return FakeDDGS


def _result(n: int, body: str = "short body", title: str | None = None) -> dict[str, Any]:
    """Build one fake ddgs result."""
    return {"title": title or f"Title {n}", "href": f"https://example.com/{n}", "body": body}


def test_web_search_three_results_output_has_titles_and_urls() -> None:
    """AC19: each title and URL appears in the numbered output."""
    # WHY: the executor needs titles and links.
    FakeDDGS.results = [_result(1), _result(2), _result(3)]
    result = web_search("python")
    assert result["ok"] is True and result["error"] is None
    output = result["output"] or ""
    for n in (1, 2, 3):
        assert f"{n}. Title {n}" in output
        assert f"https://example.com/{n}" in output


def test_web_search_long_snippet_is_trimmed_to_300_chars_plus_ellipsis() -> None:
    """AC19: a snippet is at most 300 chars plus the ellipsis."""
    # WHY: keeps the LLM context small.
    FakeDDGS.results = [_result(n, body="word " * 400) for n in (1, 2, 3)]
    output = web_search("python")["output"] or ""
    lines = output.split("\n")
    snippet_lines = [lines[2], lines[5], lines[8]]
    for line in snippet_lines:
        snippet = line.strip()
        assert snippet.endswith("…")
        assert len(snippet) <= 301


def test_web_search_short_snippet_is_not_modified() -> None:
    """AC19: a short snippet is kept whole, with no ellipsis."""
    # WHY: trimming must only cut when needed.
    FakeDDGS.results = [_result(1, body="tiny")]
    output = web_search("q")["output"] or ""
    assert output.split("\n")[2].strip() == "tiny"


def test_web_search_many_long_results_output_capped_at_2000_chars() -> None:
    """AC19: the whole output is at most 2000 characters."""
    # WHY: one search must not crowd out the task in the LLM context.
    FakeDDGS.results = [_result(n, body="w " * 500, title="T" * 500) for n in range(1, 11)]
    output = web_search("q", max_results=10)["output"] or ""
    assert len(output) <= 2000


def test_web_search_default_max_results_is_3_and_passed_to_ddgs() -> None:
    """AC20: omitted max_results is 3 and DDGS().text receives it."""
    # WHY: spec default is 3 (not the architecture's 5).
    FakeDDGS.results = [_result(1)]
    web_search("python")
    args, kwargs = FakeDDGS.text_calls[0]
    assert args == ("python",)
    assert kwargs == {"max_results": 3}
    assert FakeDDGS.init_kwargs == [{"timeout": 10}]


def test_web_search_explicit_max_results_is_passed_to_ddgs() -> None:
    """AC20: an explicit max_results reaches DDGS().text."""
    # WHY: the LLM controls how many results it wants.
    web_search("python", max_results=7)
    assert FakeDDGS.text_calls[0][1] == {"max_results": 7}


def test_web_search_schema_default_max_results_is_3() -> None:
    """AC20: the schema default is 3."""
    # WHY: the gate fills this default when the LLM omits it.
    assert WebSearchArgs(query="q").max_results == 3


@pytest.mark.parametrize("bad", [0, 11, -1])
def test_web_search_schema_max_results_out_of_range_fails_validation(bad: int) -> None:
    """AC20: max_results 0 and 11 (and negatives) are rejected."""
    # WHY: range 1-10 protects against huge searches.
    with pytest.raises(ValidationError):
        WebSearchArgs(query="q", max_results=bad)


@pytest.mark.parametrize("good", [1, 10])
def test_web_search_schema_max_results_bounds_are_allowed(good: int) -> None:
    """AC20: 1 and 10 are valid (boundaries)."""
    # WHY: range is inclusive.
    assert WebSearchArgs(query="q", max_results=good).max_results == good


@pytest.mark.parametrize(
    "error",
    [
        RatelimitException("slow down"),
        TimeoutException("too slow"),
        TimeoutError("t"),
        ConnectionError("c"),
        DDGSException("Error: 429 Too Many Requests"),
    ],
)
def test_web_search_transient_error_has_transient_prefix(error: Exception) -> None:
    """AC21: rate limit, timeout and connection errors are marked transient."""
    # WHY: the gate retries only errors with this prefix.
    FakeDDGS.error = error
    result = web_search("q")
    assert result["ok"] is False
    assert result["output"] is None
    assert (result["error"] or "").startswith(TRANSIENT_PREFIX)
    assert type(error).__name__ in (result["error"] or "")


@pytest.mark.parametrize("error", [ValueError("bad"), DDGSException("invalid query syntax")])
def test_web_search_other_error_has_no_transient_prefix(error: Exception) -> None:
    """AC21: other errors are failures without the transient prefix."""
    # WHY: retrying a bad query would fail the same way again.
    FakeDDGS.error = error
    result = web_search("q")
    assert result["ok"] is False
    assert not (result["error"] or "").startswith(TRANSIENT_PREFIX)
    assert (result["error"] or "").startswith(type(error).__name__)


def test_web_search_zero_results_returns_ok_with_no_results_text() -> None:
    """AC22: an empty list is a success with 'No results found.'."""
    # WHY: an empty search is not a tool failure.
    FakeDDGS.results = []
    assert web_search("q") == {"ok": True, "output": "No results found.", "error": None}


def test_web_search_ddgs_no_results_exception_returns_ok() -> None:
    """AC22: ddgs 9.x raises DDGSException('No results found.'); treat as zero results."""
    # WHY: real ddgs behaviour on an empty search.
    FakeDDGS.error = DDGSException("No results found.")
    assert web_search("q") == {"ok": True, "output": "No results found.", "error": None}


def test_web_search_result_missing_fields_does_not_raise() -> None:
    """AC19: results missing title, href or body are still formatted."""
    # WHY: Hard rule 1, odd ddgs data must not crash the tool.
    FakeDDGS.results = [{}]
    result = web_search("q")
    assert result["ok"] is True
    assert "1. " in (result["output"] or "")


@pytest.mark.parametrize(
    ("query", "max_results"),
    [("", 3), ("q" * 301, 3), (5, 3), ("q", 0), ("q", 11), ("q", True), ("q", "3"), ("q", 2.5)],
)
def test_web_search_bad_input_is_rejected_by_the_tool_without_searching(
    query: Any, max_results: Any
) -> None:
    """With validation off the tool checks its own input and never reaches ddgs."""
    # WHY: the ablation baseline passes raw arguments; bad ones must not hit the network.
    FakeDDGS.results = [_result(1)]
    result = web_search(query, max_results)
    assert result["ok"] is False
    assert result["output"] is None
    assert FakeDDGS.text_calls == []


def test_web_search_error_message_has_urls_removed_and_is_trimmed() -> None:
    """Catch-all errors hide URLs (a proxy URL may hold a password) and are cut to 200 chars."""
    # WHY: the message goes to the LLM and into the trace.
    FakeDDGS.error = ValueError("failed via https://user:secret@proxy.local:8080/x " + "z" * 500)
    result = web_search("q")
    error = result["error"] or ""
    assert result["ok"] is False
    assert "secret" not in error and "proxy.local" not in error
    assert "<url>" in error
    assert len(error) <= len("ValueError: ") + web_search_module.MAX_ERROR_CHARS + 1


def test_web_search_transient_detection_uses_the_original_message_not_the_cleaned_one() -> None:
    """A rate-limit hint is still found when the message also contains a URL."""
    # WHY: cleaning the message for display must not break the retry decision.
    FakeDDGS.error = DDGSException("HTTP 429 from https://example.com/search")
    result = web_search("q")
    assert (result["error"] or "").startswith(TRANSIENT_PREFIX)
