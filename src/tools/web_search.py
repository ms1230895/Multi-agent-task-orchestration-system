"""Web search tool: DuckDuckGo search through the `ddgs` package, with trimmed output.

Architecture layer: 2 (tools). This is the only tool that uses the network, so it is also
the main source of transient errors that the validation gate may retry.
"""

import re
from typing import Any

from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
from pydantic import BaseModel, ConfigDict, Field

from src.tools.base import TRANSIENT_PREFIX, ToolResult, failure, success

DEFAULT_MAX_RESULTS = 3
MAX_RESULTS_LIMIT = 10
MAX_QUERY_CHARS = 300
MAX_ERROR_CHARS = 200
SEARCH_TIMEOUT_S = 10
MAX_SNIPPET_CHARS = 300
MAX_OUTPUT_CHARS = 2000
NO_RESULTS_TEXT = "No results found."

# NOTE: in ddgs 9.x only timeouts get their own exception type. A rate limit from a search
# engine arrives as a plain DDGSException with the engine's message, so we also look for
# these hints in the text.
_TRANSIENT_HINTS = ("ratelimit", "rate limit", "429", "timed out", "timeout", "connection")
_URL_PATTERN = re.compile(r"\w+://\S+")


class WebSearchArgs(BaseModel):
    """Input schema for `web_search`."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    max_results: int = Field(default=DEFAULT_MAX_RESULTS, ge=1, le=MAX_RESULTS_LIMIT)


def _trim(text: str, limit: int) -> str:
    """Collapse whitespace and cut the text to `limit` characters, ending with `…`.

    Args:
        text: any text.
        limit: maximum number of characters before the `…` is added.

    Returns:
        The cleaned text, at most `limit + 1` characters long.
    """
    clean = " ".join(text.split())
    return clean if len(clean) <= limit else clean[:limit].rstrip() + "…"


def _format_results(results: list[dict[str, Any]]) -> str:
    """Turn raw search results into a short numbered list.

    Args:
        results: dicts from ddgs with `title`, `href` and `body` keys.

    Returns:
        Text of at most `MAX_OUTPUT_CHARS` characters.

    WHY: trimming keeps the LLM's context small, so one search cannot crowd out the task.
    """
    blocks: list[str] = []
    for number, result in enumerate(results, start=1):
        title = _trim(str(result.get("title") or "(no title)"), 200)
        url = str(result.get("href") or result.get("url") or "")
        snippet = _trim(str(result.get("body") or ""), MAX_SNIPPET_CHARS)
        blocks.append(f"{number}. {title}\n   {url}\n   {snippet}")
    text = "\n".join(blocks)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[: MAX_OUTPUT_CHARS - 1] + "…"
    return text


def _is_transient(exc: Exception) -> bool:
    """Say whether a search error is temporary and worth retrying.

    Args:
        exc: the exception raised by ddgs or the network layer.

    Returns:
        True for timeouts, rate limits and connection problems.
    """
    if isinstance(exc, RatelimitException | TimeoutException | TimeoutError | ConnectionError):
        return True
    return isinstance(exc, DDGSException) and any(
        hint in str(exc).lower() for hint in _TRANSIENT_HINTS
    )


def web_search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> ToolResult:
    """Search the web and return the top results as trimmed text.

    Args:
        query: what to search for.
        max_results: how many results to return (the schema allows 1 to 10).

    Returns:
        `ok=True` with numbered results (or "No results found."), or `ok=False` with a
        readable error. A temporary error starts with `TRANSIENT_PREFIX`. Never raises
        (Hard rule 1).
    """
    try:
        # WHY: with ENABLE_VALIDATION off the gate passes raw arguments, so the tool checks
        # its own input like the calculator and file tools do. Nothing bad reaches the network.
        if not isinstance(query, str) or not 1 <= len(query) <= MAX_QUERY_CHARS:
            return failure(f"query must be 1 to {MAX_QUERY_CHARS} characters")
        if (
            isinstance(max_results, bool)
            or not isinstance(max_results, int)
            or not 1 <= max_results <= MAX_RESULTS_LIMIT
        ):
            return failure(f"max_results must be a whole number from 1 to {MAX_RESULTS_LIMIT}")

        results = DDGS(timeout=SEARCH_TIMEOUT_S).text(query, max_results=max_results)
        # WHY: an empty search is a valid answer, not a tool failure.
        if not results:
            return success(NO_RESULTS_TEXT)
        return success(_format_results(results))
    except Exception as exc:
        # WHY: ddgs raises DDGSException("No results found.") instead of returning [].
        if isinstance(exc, DDGSException) and NO_RESULTS_TEXT in str(exc):
            return success(NO_RESULTS_TEXT)
        # WHY: tell the gate which failures are temporary, so it retries a timeout but
        # not a bad query.
        prefix = TRANSIENT_PREFIX if _is_transient(exc) else ""
        # WHY: network errors can contain URLs (a proxy URL may hold a password), and the
        # message goes back to the LLM and into the trace. Replace URLs and cut the length.
        message = _URL_PATTERN.sub("<url>", str(exc))
        return failure(f"{prefix}{type(exc).__name__}: {_trim(message, MAX_ERROR_CHARS)}")
