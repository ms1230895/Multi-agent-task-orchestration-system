"""Unit tests for the tool registry `src/tools/__init__.py` (AC8, AC9).

File tools run against a tmp_path workspace and web_search against a fake DDGS.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from src import config
from src.tools import TOOLS
from src.tools import web_search as web_search_module

EXPECTED_NAMES = {"calculator", "read_file", "write_file", "web_search"}


class _OneResultDDGS:
    """Fake DDGS whose search always succeeds; keeps tests offline.

    WHY: it must succeed. If it raised, a bad web_search call would return ok=False even
    without the tool's own input checks, and the AC8 rows would test nothing.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def text(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return [{"title": "t", "href": "https://example.com", "body": "b"}]


@pytest.fixture(autouse=True)
def _sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the workspace at tmp_path and fake the network."""
    # WHY: no test may touch the real workspace/ or the internet.
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(web_search_module, "DDGS", _OneResultDDGS)


def test_registry_has_exactly_four_tool_names() -> None:
    """AC9: exactly calculator, read_file, write_file, web_search."""
    # WHY: the gate and error messages list tools from this dict.
    assert set(TOOLS) == EXPECTED_NAMES


@pytest.mark.parametrize("name", sorted(EXPECTED_NAMES))
def test_registry_entry_is_model_class_and_callable_pair(name: str) -> None:
    """AC9: each entry is (Pydantic model class, callable)."""
    # WHY: call_tool unpacks `schema, function = TOOLS[name]`.
    schema, function = TOOLS[name]
    assert isinstance(schema, type) and issubclass(schema, BaseModel)
    assert callable(function)


# (tool name, bad kwargs for the function, bad dict for the schema)
_BAD_CALLS: list[tuple[str, dict[str, Any], dict[str, Any]]] = [
    ("calculator", {"expression": 5}, {"expression": 5}),
    ("calculator", {"expression": ""}, {"expression": ""}),
    ("calculator", {"expression": "1+" * 200}, {"expression": "1+" * 200}),
    ("read_file", {"path": 5}, {"path": 5}),
    ("read_file", {"path": ""}, {"path": ""}),
    ("read_file", {"path": "a" * 300}, {"path": "a" * 300}),
    ("write_file", {"path": "a.txt", "content": 5}, {"path": "a.txt", "content": 5}),
    ("write_file", {"path": "", "content": "x"}, {"path": "", "content": "x"}),
    (
        "write_file",
        {"path": "a.txt", "content": "x" * 100_001},
        {"path": "a.txt", "content": "x" * 100_001},
    ),
    ("web_search", {"query": 5}, {"query": 5}),
    ("web_search", {"query": ""}, {"query": ""}),
    ("web_search", {"query": "q", "max_results": 0}, {"query": "q", "max_results": 0}),
    ("web_search", {"query": "q", "max_results": 11}, {"query": "q", "max_results": 11}),
    ("web_search", {"query": "q" * 301}, {"query": "q" * 301}),
]


@pytest.mark.parametrize(("name", "kwargs", "_schema_args"), _BAD_CALLS)
def test_tool_bad_input_returns_failed_result_and_does_not_raise(
    name: str, kwargs: dict[str, Any], _schema_args: dict[str, Any]
) -> None:
    """AC8: bad input gives exactly {ok, output, error} with ok False, no exception."""
    # WHY: Hard rule 1, tools never raise, even with validation switched off.
    function: Callable[..., Any] = TOOLS[name][1]
    result = function(**kwargs)
    assert set(result) == {"ok", "output", "error"}
    assert result["ok"] is False
    assert result["output"] is None
    assert isinstance(result["error"], str) and result["error"]


@pytest.mark.parametrize(("name", "_kwargs", "schema_args"), _BAD_CALLS)
def test_schema_bad_input_raises_validation_error(
    name: str, _kwargs: dict[str, Any], schema_args: dict[str, Any]
) -> None:
    """AC8: every schema rejects the bad input (wrong type, empty, oversized)."""
    # WHY: the gate relies on the schema to catch these before the tool runs.
    schema = TOOLS[name][0]
    with pytest.raises(ValidationError):
        schema.model_validate(schema_args)


@pytest.mark.parametrize("name", sorted(EXPECTED_NAMES))
def test_schema_unknown_argument_raises_validation_error(name: str) -> None:
    """AC8: schemas use extra=forbid, so an unknown argument name is an error."""
    # WHY: the LLM often invents argument names; they must be reported, not ignored.
    valid: dict[str, dict[str, Any]] = {
        "calculator": {"expression": "1"},
        "read_file": {"path": "a"},
        "write_file": {"path": "a", "content": "b"},
        "web_search": {"query": "q"},
    }
    with pytest.raises(ValidationError):
        TOOLS[name][0].model_validate({**valid[name], "bogus": 1})
