"""Tests for `src/logger.py`: run ids, the JSONL trace writer and the shared helpers.

Covers AC1-AC9 of `docs/specs/tracing-and-first-runs.md`, plus AC35 (the helpers moved out of
`src/validation.py`). Every trace goes to pytest's `tmp_path` and the clock is a fixed
function, so the tests are fast and give the same result every time. No LLM, no network.
"""

import ast
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

import src.validation as validation_module
from src.logger import (
    EVENT_TYPES,
    TraceLogger,
    emit_safely,
    new_run_id,
    read_trace,
    trace_path,
    trim,
)

FIXED_TIME = datetime(2026, 10, 16, 12, 0, 0, 123000, tzinfo=UTC)


def _fixed_clock() -> datetime:
    """Return the same moment on every call."""
    return FIXED_TIME


def _logger(tmp_path: Path, run_id: str = "run-1") -> TraceLogger:
    """Build a logger that writes into `tmp_path` with the fixed clock."""
    return TraceLogger(run_id, "fake-model", logs_dir=tmp_path, now=_fixed_clock)


# ---- AC1: run id ----


def test_ac1_new_run_id_matches_format() -> None:
    # WHY: the id is also the file name; the format keeps files sorted and path-safe.
    assert re.fullmatch(r"\d{8}-\d{6}-[0-9a-f]{6}", new_run_id())


def test_ac1_new_run_id_two_calls_differ() -> None:
    # WHY: two runs in the same second must not share a trace file.
    assert new_run_id() != new_run_id()


# ---- AC2: one line, exact content ----


def test_ac2_log_writes_one_exact_line_and_returns_the_record(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    record = logger.log("tool_call", {"a": 1})
    lines = (tmp_path / "run-1.jsonl").read_text(encoding="utf-8").splitlines()
    # WHY: the trace format is what every later analysis reads; one wrong key breaks it all.
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "ts": "2026-10-16T12:00:00.123Z",
        "run_id": "run-1",
        "model": "fake-model",
        "event": "tool_call",
        "data": {"a": 1},
    }
    assert record == json.loads(lines[0])


def test_ac2_trace_path_is_logs_dir_slash_run_id_jsonl(tmp_path: Path) -> None:
    # WHY: the CLI prints this path and the benchmark reads it.
    assert trace_path("abc", tmp_path) == tmp_path / "abc.jsonl"
    assert _logger(tmp_path, "abc").path == tmp_path / "abc.jsonl"


# ---- AC3: order and folder creation ----


def test_ac3_log_three_calls_give_three_lines_in_order(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    for number in range(3):
        logger.log("retry", {"n": number})
    records = read_trace(logger.path)
    # WHY: a trace is read as a story; the order of lines must be the order of events.
    assert [r["data"]["n"] for r in records] == [0, 1, 2]


def test_ac3_log_missing_logs_dir_is_created(tmp_path: Path) -> None:
    folder = tmp_path / "nested" / "logs"
    logger = TraceLogger("run-1", "m", logs_dir=folder, now=_fixed_clock)
    logger.log("run_end", {})
    # WHY: a fresh checkout has no logs folder; the first run must still be traced.
    assert (folder / "run-1.jsonl").is_file()


# ---- AC4: odd values ----


def test_ac4_log_set_and_object_in_data_are_written_with_str(tmp_path: Path) -> None:
    class Odd:
        def __str__(self) -> str:
            return "odd-object"

    logger = _logger(tmp_path)
    record = logger.log("tool_call", {"s": {1}, "o": Odd()})
    # WHY: the LLM chooses tool arguments; a strange value must never break logging.
    assert logger.write_errors == 0
    assert record["event"] == "tool_call"
    written = read_trace(logger.path)[0]["data"]
    assert written == {"s": "{1}", "o": "odd-object"}


# ---- AC5: event names ----


def test_ac5_log_unknown_event_raises_value_error(tmp_path: Path) -> None:
    # WHY: event names come from our code; a typo must fail a test, not make a new line type.
    with pytest.raises(ValueError):
        _logger(tmp_path).log("made_up", {})


def test_ac5_log_unknown_event_writes_nothing(tmp_path: Path) -> None:
    logger = _logger(tmp_path)
    with pytest.raises(ValueError):
        logger.log("made_up", {})
    # WHY: a rejected event must not leave a half line in the trace.
    assert not logger.path.exists()


def test_ac5_event_types_are_exactly_the_seven_names() -> None:
    # WHY: the architecture defines 7 events; analysis code depends on this closed list.
    assert EVENT_TYPES == {
        "plan_created",
        "tool_call",
        "validation_error",
        "retry",
        "critic_verdict",
        "replan",
        "run_end",
    }


# ---- AC6: write failure ----


def test_ac6_log_unwritable_logs_dir_does_not_raise_and_counts_error(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("i am a file", encoding="utf-8")
    logger = TraceLogger("run-1", "m", logs_dir=blocker, now=_fixed_clock)
    record = logger.log("tool_call", {"a": 1})
    # WHY: the run matters more than one lost line; the counter shows the loss.
    assert logger.write_errors == 1
    assert record["event"] == "tool_call"
    assert record["data"] == {"a": 1}


def test_ac6_log_write_errors_accumulate(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    logger = TraceLogger("run-1", "m", logs_dir=blocker, now=_fixed_clock)
    logger.log("retry", {})
    logger.log("retry", {})
    # WHY: the counter must count every lost line, not only the first.
    assert logger.write_errors == 2


# ---- AC7: read_trace ----


def test_ac7_read_trace_returns_records_in_order_and_skips_empty_lines(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text('{"n": 1}\n\n   \n{"n": 2}\n', encoding="utf-8")
    # WHY: a blank line (for example a hand edit) must not crash trace analysis.
    assert read_trace(path) == [{"n": 1}, {"n": 2}]


# ---- AC8: emit_safely ----


def test_ac8_emit_safely_none_callback_does_nothing() -> None:
    # WHY: on_event defaults to None everywhere; that must be a no-op.
    emit_safely(None, "tool_call", {})


def test_ac8_emit_safely_calls_the_callback_with_event_and_data() -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    emit_safely(lambda event, data: seen.append((event, data)), "retry", {"a": 1})
    # WHY: the callback must receive exactly what was emitted.
    assert seen == [("retry", {"a": 1})]


def test_ac8_emit_safely_raising_callback_does_not_raise() -> None:
    def broken(event: str, data: dict[str, Any]) -> None:
        raise RuntimeError("display crashed")

    # WHY: a broken display must never break a tool call or an agent (D5).
    emit_safely(broken, "tool_call", {})


# ---- AC9: trim ----


def test_ac9_trim_long_text_has_limit_length_and_ends_with_ellipsis() -> None:
    result = trim("x" * 600, 500)
    # WHY: the limit is a total length, so trace lines stay small and predictable.
    assert len(result) == 500
    assert result.endswith("…")


@pytest.mark.parametrize("text", ["", "short", "x" * 500])
def test_ac9_trim_text_within_limit_is_unchanged(text: str) -> None:
    # WHY: only text that is too long may be cut; 500 chars with limit 500 is allowed.
    assert trim(text, 500) == text


# ---- AC35: helpers moved out of validation.py ----


def test_ac35_validation_no_longer_defines_trim_or_emit() -> None:
    source = Path(validation_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined.update(t.id for t in node.targets if isinstance(t, ast.Name))
    # WHY: one shared copy in the logger; a second copy could drift from it.
    assert "_trim" not in defined
    assert "_emit" not in defined


def test_ac35_validation_uses_the_logger_helpers() -> None:
    from src import logger

    # WHY: validation must import the shared helpers, not keep its own versions.
    assert validation_module.trim is logger.trim
    assert validation_module.emit_safely is logger.emit_safely
