"""Unit tests for `src/console.py` (spec foundation-tools-validation, AC7)."""

import io
from typing import Any

import pytest
from rich.console import Console

from src import console as console_module
from src.logger import EVENT_TYPES


def test_console_constants_match_colour_coding_rule() -> None:
    """AC7: the 7 role colours equal the colour-coding rule."""
    # WHY: one fixed colour per role so output is readable and consistent.
    assert console_module.PLANNER == "magenta"
    assert console_module.EXECUTOR == "cyan"
    assert console_module.CRITIC == "yellow"
    assert console_module.TOOL == "blue"
    assert console_module.RETRY == "dark_orange"
    assert console_module.SUCCESS == "green"
    assert console_module.ERROR == "red"


def test_console_shared_instance_is_rich_console() -> None:
    """AC7: a shared rich Console exists."""
    # WHY: all output must go through one stream and settings.
    assert isinstance(console_module.console, Console)


# ---- Session 5: event colours and print_record (tracing spec AC31-AC33) ----

ROLE_COLOURS = {
    console_module.PLANNER,
    console_module.EXECUTOR,
    console_module.CRITIC,
    console_module.TOOL,
    console_module.RETRY,
    console_module.SUCCESS,
    console_module.ERROR,
}

SAMPLE_DATA: dict[str, dict[str, Any]] = {
    "plan_created": {"steps": ["first step", "second step"]},
    "replan": {"replans": 1, "steps": ["new step"], "reason": "the old plan failed"},
    "tool_call": {
        "tool": "calculator",
        "args": {"expression": "1+1"},
        "ok": True,
        "output": "2",
        "attempts": 1,
        "duration_ms": 3,
    },
    "validation_error": {
        "tool": "calculator",
        "args": {"expr": "1"},
        "errors": [{"field": "expression", "problem": "Field required"}],
    },
    "retry": {"kind": "step", "step": 0, "attempt": 1, "reason": "try again"},
    "critic_verdict": {
        "step": 0,
        "verdict": "retry",
        "feedback": "not done",
        "output": "the executor answer",
    },
    "run_end": {
        "task": "t",
        "status": "done",
        "final_answer": "42",
        "error": "",
        "counters": {"tool_calls": 1},
        "config": {},
        "duration_ms": 10,
    },
}


def _recording_console() -> Console:
    """A wide console that records its output and prints nowhere."""
    return Console(record=True, file=io.StringIO(), width=200)


def _record(event: str, data: dict[str, Any]) -> dict[str, Any]:
    """Build a trace record like the logger writes."""
    return {"ts": "t", "run_id": "r", "model": "m", "event": event, "data": data}


def test_ac31_event_colours_have_exactly_the_event_type_keys() -> None:
    # WHY: a new event without a colour (or a stale colour) would be noticed here.
    assert set(console_module.EVENT_COLOURS) == EVENT_TYPES


def test_ac31_event_colours_values_are_role_constants() -> None:
    # WHY: the colour-coding rule allows only the fixed role colours.
    assert set(console_module.EVENT_COLOURS.values()) <= ROLE_COLOURS


def test_ac32_sample_data_covers_every_event_type() -> None:
    # WHY: guards the next test against silently skipping a new event type.
    assert set(SAMPLE_DATA) == EVENT_TYPES


@pytest.mark.parametrize("event", sorted(EVENT_TYPES))
def test_ac32_print_record_each_event_type_prints_without_raising(event: str) -> None:
    out = _recording_console()
    console_module.print_record(_record(event, SAMPLE_DATA[event]), out=out)
    # WHY: a display bug must never crash a run; every event type must print something.
    assert out.export_text().strip() != ""


def test_ac32_print_record_plan_shows_numbered_steps() -> None:
    out = _recording_console()
    console_module.print_record(_record("plan_created", SAMPLE_DATA["plan_created"]), out=out)
    text = out.export_text()
    # WHY: the plan is the first thing a user reads in the live view.
    assert "1. first step" in text
    assert "2. second step" in text


def test_ac32_print_record_failed_tool_call_shows_the_error() -> None:
    out = _recording_console()
    data = {"tool": "read_file", "args": {}, "ok": False, "error": "file not found"}
    console_module.print_record(_record("tool_call", data), out=out)
    # WHY: a failed call must show its error text, not an empty output.
    assert "file not found" in out.export_text()


def test_ac32_print_record_unknown_event_and_odd_data_do_not_raise() -> None:
    out = _recording_console()
    console_module.print_record({"event": "mystery", "data": "not a dict"}, out=out)
    console_module.print_record({}, out=out)
    # WHY: the display must survive a malformed record instead of crashing the CLI.
    assert out.export_text() != ""


def test_ac33_print_record_markup_in_critic_text_is_printed_literally() -> None:
    out = _recording_console()
    data = {"step": 0, "verdict": "retry", "feedback": "[red]x[/red]", "output": "[link=x]y[/link]"}
    console_module.print_record(_record("critic_verdict", data), out=out)
    text = out.export_text()
    # WHY: LLM and web text must not be able to change the terminal output with rich markup.
    assert "[red]x[/red]" in text
    assert "[link=x]y[/link]" in text


MARKER = "[red]x[/red]"


@pytest.mark.parametrize(
    ("event", "field"),
    [
        ("plan_created", "steps"),
        ("replan", "reason"),
        ("tool_call", "output"),
        ("validation_error", "tool"),
        ("retry", "reason"),
        ("critic_verdict", "output"),
        ("run_end", "status"),
    ],
)
def test_ac33_print_record_markup_in_any_event_text_is_printed_literally(
    event: str, field: str
) -> None:
    data = dict(SAMPLE_DATA[event])
    data[field] = [MARKER] if field == "steps" else MARKER
    out = _recording_console()
    console_module.print_record(_record(event, data), out=out)
    # WHY: every event type shows LLM or web text; none may let markup change the output.
    assert MARKER in out.export_text()


# ---- Review fixes (session 5 code review) ----


def _terminal_console() -> Console:
    """A recording console that behaves like a colour terminal, so styles are kept."""
    return Console(record=True, file=io.StringIO(), width=200, force_terminal=True)


def test_review_sanitize_replaces_escape_and_control_characters() -> None:
    cleaned = console_module.sanitize("a\x1b[2Jb\x07c\x7fd\x9be\tf")
    # WHY: a terminal runs ESC sequences as commands; tab is harmless and stays.
    assert cleaned == "a?[2Jb?c?d?e\tf"


def test_review_sanitize_indents_continuation_lines() -> None:
    # WHY: text after a newline must not look like a new top-level event line.
    assert console_module.sanitize("one\nCritic: approve") == "one\n  Critic: approve"


def test_review_print_record_never_sends_escape_codes_from_untrusted_text() -> None:
    out = _terminal_console()
    record = {
        "event": "tool_call",
        "data": {"tool": "t", "args": {}, "ok": True, "output": "x\x1b]0;title\x07y"},
    }
    console_module.print_record(record, out=out)
    printed = out.export_text()
    assert "\x1b]0;" not in printed
    assert "x?]0;title?y" in printed


def test_review_print_record_uses_the_event_colours_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(console_module.EVENT_COLOURS, "plan_created", "green")
    out = _terminal_console()
    console_module.print_record({"event": "plan_created", "data": {"steps": ["a"]}}, out=out)
    styled = out.export_text(styles=True)
    # WHY: the table must be the one place that decides the colour (green here, not magenta).
    assert "\x1b[32m" in styled
    assert "\x1b[35m" not in styled


@pytest.mark.parametrize("step", [None, "x", 1.5])
def test_review_print_record_critic_verdict_with_odd_step_does_not_raise(step: object) -> None:
    out = _terminal_console()
    record = {"event": "critic_verdict", "data": {"step": step, "verdict": "retry", "output": "o"}}
    console_module.print_record(record, out=out)
    assert "Step ? output: o" in out.export_text()
