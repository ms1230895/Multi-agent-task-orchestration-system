"""Unit tests for the validation gate `src/validation.py` (AC23-AC33).

Fake tools are registered with monkeypatch.setitem on TOOLS, sleep is a recorder, and
on_event collects events. No real waiting, LLM, or network.
"""

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from src import validation
from src.config import Settings, load_settings
from src.tools import TOOLS
from src.tools.base import TRANSIENT_PREFIX, ToolResult, failure, success
from src.tools.calculator import CalculatorArgs, calculator
from src.validation import call_tool


class NoArgs(BaseModel):
    """Schema for fake tools: no arguments allowed."""

    model_config = ConfigDict(extra="forbid")


class Recorder:
    """Collects sleep delays and events."""

    def __init__(self) -> None:
        self.delays: list[float] = []
        self.events: list[tuple[str, dict[str, Any]]] = []

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)

    def on_event(self, name: str, data: dict[str, Any]) -> None:
        self.events.append((name, data))

    def named(self, name: str) -> list[dict[str, Any]]:
        return [data for event, data in self.events if event == name]


def make_settings(**env: str) -> Settings:
    """Build Settings from a dict, never reading the real .env."""
    return load_settings({"OLLAMA_MODEL": "m", **env})


def register(
    monkeypatch: pytest.MonkeyPatch, function: Callable[..., ToolResult], name: str = "fake"
) -> None:
    """Register a fake tool taking no arguments."""
    monkeypatch.setitem(TOOLS, name, (NoArgs, function))


def transient_then_ok(failures: int) -> tuple[Callable[..., ToolResult], list[int]]:
    """A tool that fails transiently `failures` times, then succeeds; with a call counter."""
    calls: list[int] = []

    def tool() -> ToolResult:
        calls.append(1)
        if len(calls) <= failures:
            return failure(TRANSIENT_PREFIX + "flaky")
        return success("done")

    return tool, calls


@pytest.fixture
def rec() -> Recorder:
    """A fresh recorder per test."""
    return Recorder()


def test_call_tool_unknown_name_returns_error_listing_tools(rec: Recorder) -> None:
    """AC23: unknown tool error names it and lists the 4 tools."""
    # WHY: the LLM reads this to pick a real tool.
    result = call_tool("nope", {}, settings=make_settings(), sleep=rec.sleep)
    assert result["ok"] is False
    error = result["error"] or ""
    assert "nope" in error
    for name in ("calculator", "read_file", "web_search", "write_file"):
        assert name in error
    assert rec.delays == []


@pytest.mark.parametrize("args", [{"expr": "1+1"}, {"expression": 5}])
def test_call_tool_invalid_args_returns_field_error_and_skips_tool(
    monkeypatch: pytest.MonkeyPatch, args: dict[str, Any]
) -> None:
    """AC24: invalid arguments name the field and the tool function is never called."""
    # WHY: a bad argument must become a fixable message, not reach the tool.
    calls: list[dict[str, Any]] = []

    def spy(**kwargs: Any) -> ToolResult:
        calls.append(kwargs)
        return success("x")

    monkeypatch.setitem(TOOLS, "calculator", (CalculatorArgs, spy))
    result = call_tool("calculator", args, settings=make_settings(), sleep=lambda s: None)
    assert result["ok"] is False
    error = result["error"] or ""
    assert error.startswith("invalid arguments for calculator:")
    assert "expression" in error
    assert calls == []


def test_call_tool_args_not_a_dict_with_validation_on_returns_validation_error() -> None:
    """AC24: a non-dict args value is a validation error, not a crash."""
    # WHY: LLMs sometimes send a string (spec section 7).
    result = call_tool(
        "calculator",
        "1+1",  # type: ignore[arg-type]
        settings=make_settings(),
        sleep=lambda s: None,
    )
    assert result["ok"] is False
    assert "invalid arguments" in (result["error"] or "")


def test_call_tool_valid_args_runs_real_calculator() -> None:
    """AC24: valid arguments reach the tool and give its result."""
    # WHY: the happy path through the gate.
    result = call_tool(
        "calculator", {"expression": "2+3"}, settings=make_settings(), sleep=lambda s: None
    )
    assert result == {"ok": True, "output": "5", "error": None}


def test_call_tool_valid_args_get_schema_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC24: the tool is called with model_dump(), so schema defaults are filled."""
    # WHY: the gate passes validated arguments, not the raw ones.
    seen: list[dict[str, Any]] = []

    class WithDefault(BaseModel):
        model_config = ConfigDict(extra="forbid")
        n: int = 7

    def tool(**kwargs: Any) -> ToolResult:
        seen.append(kwargs)
        return success("ok")

    monkeypatch.setitem(TOOLS, "fake", (WithDefault, tool))
    call_tool("fake", {}, settings=make_settings(), sleep=lambda s: None)
    assert seen == [{"n": 7}]


def test_call_tool_invalid_args_emits_validation_error_event(rec: Recorder) -> None:
    """AC25: a validation_error event carries tool, args and errors."""
    # WHY: the logger needs to count validation errors for the benchmark metric.
    call_tool(
        "calculator",
        {"expr": "1+1"},
        settings=make_settings(),
        sleep=rec.sleep,
        on_event=rec.on_event,
    )
    events = rec.named("validation_error")
    assert len(events) == 1
    assert events[0]["tool"] == "calculator"
    assert events[0]["args"] == {"expr": "1+1"}
    assert {e["field"] for e in events[0]["errors"]} >= {"expression", "expr"}


def test_call_tool_valid_args_emits_no_validation_error_event(rec: Recorder) -> None:
    """AC25: no validation_error event for valid arguments."""
    # WHY: events must reflect real problems only.
    call_tool(
        "calculator",
        {"expression": "1"},
        settings=make_settings(),
        sleep=rec.sleep,
        on_event=rec.on_event,
    )
    assert rec.named("validation_error") == []


def test_call_tool_validation_off_bad_args_reach_tool_and_fail_safely(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC26: with validation off, raw args reach the tool; result is ok False, no raise."""
    # WHY: the 'off' side of the ablation; the gate's try/except is the safety net.
    seen: list[dict[str, Any]] = []

    def spy(**kwargs: Any) -> ToolResult:
        seen.append(kwargs)
        return calculator(**kwargs)  # wrong kwarg name -> TypeError

    monkeypatch.setitem(TOOLS, "calculator", (CalculatorArgs, spy))
    result = call_tool(
        "calculator",
        {"expr": "1+1"},
        settings=make_settings(ENABLE_VALIDATION="false"),
        sleep=rec.sleep,
        on_event=rec.on_event,
    )
    assert seen == [{"expr": "1+1"}]
    assert result["ok"] is False
    assert (result["error"] or "").startswith("TypeError")
    assert rec.named("validation_error") == []


def test_call_tool_validation_off_args_not_a_dict_is_caught() -> None:
    """AC26: with validation off, a string args value gives a TypeError result."""
    # WHY: spec section 7.
    result = call_tool(
        "calculator",
        "1+1",  # type: ignore[arg-type]
        settings=make_settings(ENABLE_VALIDATION="false"),
        sleep=lambda s: None,
    )
    assert result["ok"] is False
    assert (result["error"] or "").startswith("TypeError")


def test_call_tool_tool_raises_runtime_error_returns_exact_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC27: an exception becomes {ok False, output None, error 'RuntimeError: boom'}."""

    # WHY: Hard rule 1, a buggy tool must never crash the run.
    def boom() -> ToolResult:
        raise RuntimeError("boom")

    register(monkeypatch, boom)
    result = call_tool("fake", {}, settings=make_settings(), sleep=lambda s: None)
    assert result == {"ok": False, "output": None, "error": "RuntimeError: boom"}


def test_call_tool_tool_returns_wrong_shape_gives_invalid_result_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC27: a return value that is not a ToolResult becomes a failure."""
    # WHY: layers above rely on the exact format.
    register(monkeypatch, lambda: "just a string")  # type: ignore[arg-type,return-value]
    result = call_tool("fake", {}, settings=make_settings(), sleep=lambda s: None)
    assert result == {"ok": False, "output": None, "error": "tool returned an invalid result"}


def test_call_tool_transient_twice_then_ok_retries_with_backoff(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC28: two transient failures then success: ok, 3 calls, sleep [1, 2], 2 retry events."""
    # WHY: this is the retry feature the 'failed tool calls' metric measures.
    tool, calls = transient_then_ok(2)
    register(monkeypatch, tool)
    result = call_tool(
        "fake",
        {},
        settings=make_settings(TOOL_RETRY_ATTEMPTS="2"),
        sleep=rec.sleep,
        on_event=rec.on_event,
    )
    assert result["ok"] is True
    assert result["output"] == "done"
    assert len(calls) == 3
    assert rec.delays == [1, 2]
    retries = rec.named("retry")
    assert len(retries) == 2
    assert [r["attempt"] for r in retries] == [1, 2]
    assert all(r["kind"] == "tool" and "flaky" in r["reason"] for r in retries)


def test_call_tool_always_transient_calls_one_plus_attempts_times(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC29: always-transient tool is called 1 + tool_retry_attempts times, then ok False."""
    # WHY: Hard rule 5, retries are bounded.
    tool, calls = transient_then_ok(failures=999)
    register(monkeypatch, tool)
    result = call_tool("fake", {}, settings=make_settings(TOOL_RETRY_ATTEMPTS="2"), sleep=rec.sleep)
    assert result["ok"] is False
    assert (result["error"] or "").startswith(TRANSIENT_PREFIX)
    assert len(calls) == 3
    assert rec.delays == [1, 2]


def test_call_tool_backoff_is_capped_at_4_seconds(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC29: delays go 1, 2, 4, 4, 4 with five extra attempts."""
    # WHY: spec says min(2 ** (k - 1), 4).
    tool, calls = transient_then_ok(failures=999)
    register(monkeypatch, tool)
    call_tool("fake", {}, settings=make_settings(TOOL_RETRY_ATTEMPTS="5"), sleep=rec.sleep)
    assert len(calls) == 6
    assert rec.delays == [1, 2, 4, 4, 4]


def test_call_tool_zero_retry_attempts_calls_once(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC29: tool_retry_attempts=0 means one call and no sleep."""
    # WHY: boundary of the retry limit.
    tool, calls = transient_then_ok(failures=999)
    register(monkeypatch, tool)
    call_tool("fake", {}, settings=make_settings(TOOL_RETRY_ATTEMPTS="0"), sleep=rec.sleep)
    assert len(calls) == 1
    assert rec.delays == []


def test_call_tool_retry_off_transient_failure_called_once_no_sleep(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC30: with retry off, a transient failure is called once and sleep never runs."""
    # WHY: the 'off' side of the retry ablation.
    tool, calls = transient_then_ok(failures=999)
    register(monkeypatch, tool)
    result = call_tool(
        "fake",
        {},
        settings=make_settings(ENABLE_RETRY="false", TOOL_RETRY_ATTEMPTS="3"),
        sleep=rec.sleep,
        on_event=rec.on_event,
    )
    assert result["ok"] is False
    assert len(calls) == 1
    assert rec.delays == []
    assert rec.named("retry") == []


def test_call_tool_timeout_error_raised_by_tool_is_retried(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC28: a raised TimeoutError is transient and gets retried."""
    # WHY: spec step 3, TimeoutError and ConnectionError get the transient prefix.
    calls: list[int] = []

    def tool() -> ToolResult:
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("slow")
        if len(calls) == 2:
            raise ConnectionError("down")
        return success("fine")

    register(monkeypatch, tool)
    result = call_tool("fake", {}, settings=make_settings(), sleep=rec.sleep)
    assert result["ok"] is True
    assert len(calls) == 3
    assert rec.delays == [1, 2]


def test_call_tool_non_transient_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC31: a normal failure is called once and never slept on."""
    # WHY: retrying the same bad call wastes time.
    calls: list[int] = []

    def tool() -> ToolResult:
        calls.append(1)
        return failure("permanent")

    register(monkeypatch, tool)
    result = call_tool("fake", {}, settings=make_settings(), sleep=rec.sleep)
    assert result["error"] == "permanent"
    assert len(calls) == 1
    assert rec.delays == []


def test_call_tool_runtime_error_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC31: an ordinary exception is not transient."""
    # WHY: only timeouts and connection errors are retried.
    calls: list[int] = []

    def tool() -> ToolResult:
        calls.append(1)
        raise RuntimeError("boom")

    register(monkeypatch, tool)
    call_tool("fake", {}, settings=make_settings(), sleep=rec.sleep)
    assert len(calls) == 1
    assert rec.delays == []


def test_call_tool_validation_error_is_not_retried_and_tool_not_called(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC31: a validation error means zero tool calls and no sleep."""
    # WHY: the same bad arguments would fail again.
    calls: list[int] = []

    def tool() -> ToolResult:
        calls.append(1)
        return success("x")

    register(monkeypatch, tool)
    result = call_tool("fake", {"unexpected": 1}, settings=make_settings(), sleep=rec.sleep)
    assert result["ok"] is False
    assert calls == []
    assert rec.delays == []


def test_call_tool_unknown_tool_is_not_retried(rec: Recorder) -> None:
    """AC31: an unknown tool never sleeps."""
    # WHY: spec step 1, never retried.
    call_tool("nope", {}, settings=make_settings(), sleep=rec.sleep)
    assert rec.delays == []


def _assert_tool_call_event_shape(data: dict[str, Any]) -> None:
    """Check the keys of a tool_call event."""
    common = {"tool", "args", "ok", "attempts", "duration_ms"}
    extra = {"output"} if data["ok"] else {"error"}
    assert set(data) == common | extra
    assert isinstance(data["duration_ms"], int) and data["duration_ms"] >= 0


def test_call_tool_success_emits_exactly_one_tool_call_event(rec: Recorder) -> None:
    """AC32: success emits one tool_call event with output."""
    # WHY: the trace logger records one line per tool call.
    call_tool(
        "calculator",
        {"expression": "1+1"},
        settings=make_settings(),
        sleep=rec.sleep,
        on_event=rec.on_event,
    )
    events = rec.named("tool_call")
    assert len(events) == 1
    _assert_tool_call_event_shape(events[0])
    assert events[0]["tool"] == "calculator"
    assert events[0]["ok"] is True
    assert events[0]["output"] == "2"
    assert events[0]["attempts"] == 1


@pytest.mark.parametrize(
    ("name", "args", "expected_attempts"),
    [
        ("nope", {}, 0),
        ("calculator", {"expr": "1"}, 0),
        ("calculator", {"expression": "1/0"}, 1),
    ],
)
def test_call_tool_failure_emits_exactly_one_tool_call_event_with_error(
    rec: Recorder, name: str, args: dict[str, Any], expected_attempts: int
) -> None:
    """AC32: unknown tool, validation error and tool error each emit one tool_call event."""
    # WHY: failures are what the benchmark counts. The tool never ran for the first two,
    # so attempts is 0; the third ran once.
    call_tool(name, args, settings=make_settings(), sleep=rec.sleep, on_event=rec.on_event)
    events = rec.named("tool_call")
    assert len(events) == 1
    _assert_tool_call_event_shape(events[0])
    assert events[0]["ok"] is False
    assert events[0]["attempts"] == expected_attempts


def test_call_tool_retries_emit_single_tool_call_event_with_attempts_count(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC32: after retries there is still one tool_call event, attempts counts them."""
    # WHY: one line per call, not per attempt.
    tool, _ = transient_then_ok(2)
    register(monkeypatch, tool)
    call_tool("fake", {}, settings=make_settings(), sleep=rec.sleep, on_event=rec.on_event)
    events = rec.named("tool_call")
    assert len(events) == 1
    assert events[0]["attempts"] == 3


def test_call_tool_event_args_and_output_are_trimmed(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC32: long output and long argument strings are cut in the event."""

    # WHY: trace lines must stay small (write_file content can be 100 000 chars).
    class Big(BaseModel):
        text: str

    monkeypatch.setitem(TOOLS, "fake", (Big, lambda text: success("o" * 5000)))
    call_tool(
        "fake",
        {"text": "a" * 5000},
        settings=make_settings(),
        sleep=rec.sleep,
        on_event=rec.on_event,
    )
    data = rec.named("tool_call")[0]
    # WHY: exact lengths, so a cut that adds the "…" on top of the limit would fail.
    assert len(data["output"]) == validation.MAX_LOGGED_CHARS == 500
    assert len(data["args"]["text"]) == validation.MAX_LOGGED_ARG_CHARS == 200
    assert data["output"].endswith("…")


def test_call_tool_long_error_is_trimmed_to_exactly_the_limit(
    monkeypatch: pytest.MonkeyPatch, rec: Recorder
) -> None:
    """AC32: a long error text is cut to 500 characters in total in the event."""
    # WHY: the error path trims separately from the output path.
    register(monkeypatch, lambda: failure("e" * 5000))
    call_tool("fake", {}, settings=make_settings(), sleep=rec.sleep, on_event=rec.on_event)
    assert len(rec.named("tool_call")[0]["error"]) == 500


@pytest.mark.parametrize("bad_name", [["calculator"], {"a": 1}, 5, None])
def test_call_tool_non_string_name_returns_error_and_does_not_raise(
    rec: Recorder, bad_name: Any
) -> None:
    """Hard rule 1: a list, dict or number as the tool name is an error result, not a crash."""
    # WHY: a list or dict is unhashable and used to raise TypeError inside `TOOLS.get`.
    result = call_tool(
        bad_name, {}, settings=make_settings(), sleep=rec.sleep, on_event=rec.on_event
    )
    assert result == {"ok": False, "output": None, "error": "tool name must be a string"}
    events = rec.named("tool_call")
    assert len(events) == 1
    assert events[0]["attempts"] == 0


def test_call_tool_long_unknown_name_is_trimmed_in_error_and_event(rec: Recorder) -> None:
    """The LLM-chosen name is cut to 100 characters where it is shown or logged."""
    # WHY: an unbounded name would put an unbounded string into the LLM's context and the trace.
    result = call_tool(
        "x" * 5000, {}, settings=make_settings(), sleep=rec.sleep, on_event=rec.on_event
    )
    assert result["ok"] is False
    assert len(result["error"] or "") < 300
    assert len(rec.named("tool_call")[0]["tool"]) == validation.MAX_LOGGED_NAME_CHARS


def test_call_tool_on_event_raising_does_not_change_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC33: a failing callback gives the same result as no callback."""

    # WHY: logging is a side job and must never break a tool call.
    def bad_callback(name: str, data: dict[str, Any]) -> None:
        raise RuntimeError("logger down")

    tool, _ = transient_then_ok(1)
    register(monkeypatch, tool)
    with_cb = call_tool(
        "fake", {}, settings=make_settings(), sleep=lambda s: None, on_event=bad_callback
    )
    tool2, _ = transient_then_ok(1)
    register(monkeypatch, tool2)
    without_cb = call_tool("fake", {}, settings=make_settings(), sleep=lambda s: None)
    assert with_cb == without_cb == {"ok": True, "output": "done", "error": None}


def test_call_tool_on_event_raising_on_error_paths_still_returns_failure() -> None:
    """AC33: a failing callback does not hide unknown-tool or validation errors."""

    # WHY: all event paths must be protected.
    def bad_callback(name: str, data: dict[str, Any]) -> None:
        raise ValueError("x")

    unknown = call_tool(
        "nope", {}, settings=make_settings(), sleep=lambda s: None, on_event=bad_callback
    )
    invalid = call_tool(
        "calculator",
        {"expr": "1"},
        settings=make_settings(),
        sleep=lambda s: None,
        on_event=bad_callback,
    )
    assert unknown["ok"] is False and "unknown tool" in (unknown["error"] or "")
    assert invalid["ok"] is False and "invalid arguments" in (invalid["error"] or "")


def test_call_tool_no_settings_uses_get_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC28/AC30: without a settings argument the gate reads get_settings()."""
    # WHY: default path; patched so the real .env is never read.
    monkeypatch.setattr(validation, "get_settings", lambda: make_settings(ENABLE_RETRY="false"))
    tool, calls = transient_then_ok(failures=999)
    register(monkeypatch, tool)
    call_tool("fake", {}, sleep=lambda s: None)
    assert len(calls) == 1
