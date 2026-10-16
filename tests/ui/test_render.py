"""Tests for `ui/render.py` (spec streamlit-ui, AC5 to AC7): colours, escaping, tolerance.

Pure Python: the HTML is checked as text, no browser and no Streamlit.
"""

import re
from typing import Any

from src.console import CRITIC, ERROR, EXECUTOR, HTML_COLOURS, PLANNER, RETRY, SUCCESS, TOOL
from ui.render import record_html


def _record(event: str, data: Any) -> dict[str, Any]:
    """Build a trace record like the logger writes."""
    return {"ts": "t", "run_id": "r", "model": "m", "event": event, "data": data}


def _div_with(html_text: str, snippet: str) -> str:
    """Return the one `<div>` line of the HTML that contains `snippet`."""
    for part in html_text.split("</div>"):
        if snippet in part:
            return part
    raise AssertionError(f"no line contains {snippet!r} in {html_text!r}")


def test_record_html_each_event_uses_its_role_colour_ac5() -> None:
    """AC5: each event line carries the hex colour of its role."""
    # WHY: the browser must show the same colours as the terminal; a wrong role colour
    # (for example a failed run in green) would mislead the reader.
    cases: list[tuple[str, dict[str, Any], str, str]] = [
        ("plan_created", {"steps": ["plan-step"]}, "plan-step", PLANNER),
        ("tool_call", {"tool": "t", "args": {}, "ok": True, "output": "ok-out"}, "ok-out", TOOL),
        ("tool_call", {"tool": "t", "args": {}, "ok": False, "error": "bad-err"}, "bad-err", ERROR),
        (
            "critic_verdict",
            {"step": 0, "verdict": "retry", "feedback": "f", "output": "exec-out"},
            "exec-out",
            EXECUTOR,
        ),
        (
            "critic_verdict",
            {"step": 0, "verdict": "retry", "feedback": "f", "output": "o"},
            "Critic: retry",
            CRITIC,
        ),
        ("retry", {"kind": "step", "attempt": 1, "reason": "retry-why"}, "retry-why", RETRY),
        ("replan", {"replans": 1, "steps": [], "reason": "replan-why"}, "replan-why", RETRY),
        ("run_end", {"status": "done", "counters": {}}, "Run done", SUCCESS),
        ("run_end", {"status": "failed", "counters": {}}, "Run failed", ERROR),
    ]
    for event, data, snippet, role in cases:
        line = _div_with(record_html(_record(event, data)), snippet)
        assert f"color:{HTML_COLOURS[role]};" in line, (event, snippet)


def test_record_html_hostile_text_is_escaped_and_control_chars_removed_ac6() -> None:
    """AC6: script and img tags from an LLM or web page stay inert text."""
    # WHY: trace text is untrusted; if a tag got through, a viewer of a shared trace could run
    # script in their browser.
    payload = (
        "<script>alert(1)</script> <img src=x onerror=alert(1)> ![x](http://example.com/t.png)"
    )
    records = [
        _record("tool_call", {"tool": "t", "args": {}, "ok": True, "output": payload + "\x1b[2J"}),
        _record(
            "critic_verdict",
            {"step": 0, "verdict": "retry", "feedback": payload, "output": payload},
        ),
    ]
    for record in records:
        out = record_html(record)
        assert "&lt;script&gt;" in out
        assert "&lt;img" in out
        assert "<script" not in out
        assert "<img" not in out
        # NOTE: the only tags left are our own <div> lines.
        assert re.findall(r"<(?!/?div\b)", out) == []
        assert "\x1b" not in out
        assert "![x](http://example.com/t.png)" in out


def test_record_html_odd_input_returns_text_and_never_raises_ac7() -> None:
    """AC7: odd records give a string; a broken plan gives one fallback line."""
    # WHY: a display function must not stop the page; one bad record in a trace must not hide
    # the other records.
    odd_inputs: list[Any] = [
        {},
        _record("tool_call", None),
        _record("tool_call", "x"),
        _record("mystery_event", {"a": 1}),
        None,
        [],
        "text",
    ]
    for record in odd_inputs:
        assert isinstance(record_html(record), str), record

    broken_plan = record_html(_record("plan_created", {"steps": None}))
    assert "(record could not be shown)" in broken_plan
    assert broken_plan.count("<div") == 1
