"""Trace logger: one JSONL file per run in `logs/`, one line per event.

Architecture layer: cross-cutting trace logger. Every layer from the validation gate up sends
events here. The trace is the source of truth for a run (the console only displays it), and
it is what we count for "reasoning traces across 100+ runs".
"""

import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.config import LOGS_DIR

# WHY: a fixed list, so a typo in an event name is caught by a test instead of silently
# creating a new kind of line that no analysis code knows about.
EVENT_TYPES = frozenset(
    {
        "plan_created",
        "tool_call",
        "validation_error",
        "retry",
        "critic_verdict",
        "replan",
        "run_end",
    }
)

# WHY: outputs, feedback and reasons can be long; a trace line should stay small.
MAX_TRACE_TEXT_CHARS = 500

# NOTE: inside a run, layers send `(event, data)`; the caller of `run_task` receives the
# whole record (with `ts`, `run_id` and `model` added) through a `RecordCallback`.
EventCallback = Callable[[str, dict[str, Any]], None]
RecordCallback = Callable[[dict[str, Any]], None]


def trim(text: str, limit: int) -> str:
    """Cut text to at most `limit` characters in total, ending with `…` when cut."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def emit_safely(on_event: EventCallback | None, event: str, data: dict[str, Any]) -> None:
    """Send an event to the optional callback, never letting it break the caller.

    Args:
        on_event: the callback, or None.
        event: the event name.
        data: the event data.

    WHY: logging is a side job. If the logger or a display callback fails, the tool result
    or agent output must still go on, so a callback error is ignored (D5).
    """
    if on_event is None:
        return
    try:
        on_event(event, data)
    except Exception:  # noqa: BLE001 - see WHY above
        pass


def new_run_id() -> str:
    """Return a new run id like `20261016-120000-a1b2c3`.

    WHY: the time part makes files sort by start time in `logs/`; the random part stops two
    runs in the same second from sharing a file; and the id has no characters that are
    unsafe in a file name.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


def trace_path(run_id: str, logs_dir: Path = LOGS_DIR) -> Path:
    """Return the path of the trace file of a run."""
    return logs_dir / f"{run_id}.jsonl"


def _utc_now() -> datetime:
    """Return the current time in UTC (a function, so tests can pass a fixed clock)."""
    return datetime.now(UTC)


class TraceLogger:
    """Appends the events of one run to `logs/<run_id>.jsonl`.

    Attributes:
        write_errors: how many lines could not be written. The run goes on anyway.
    """

    def __init__(
        self,
        run_id: str,
        model: str,
        *,
        logs_dir: Path = LOGS_DIR,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Prepare a logger. Nothing is written until the first `log` call.

        Args:
            run_id: the id of the run; also the file name.
            model: the model name, written on every line so traces can never be mixed up.
            logs_dir: the folder for trace files.
            now: the clock, replaced by a fixed one in tests.
        """
        self.run_id = run_id
        self.model = model
        self.path = trace_path(run_id, logs_dir)
        self._now = now
        self.write_errors = 0

    def log(self, event: str, data: dict[str, Any]) -> dict[str, Any]:
        """Write one event line and return the record.

        Args:
            event: one of `EVENT_TYPES`.
            data: the event fields.

        Returns:
            The record `{ts, run_id, model, event, data}`, also when the write failed.

        Raises:
            ValueError: if `event` is not in `EVENT_TYPES`.
        """
        # WHY: event names come from our code, never from the LLM, so an unknown name is a
        # bug that should stop a test.
        if event not in EVENT_TYPES:
            raise ValueError(f"unknown event type: {event}")

        timestamp = self._now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        record = {
            "ts": timestamp,
            "run_id": self.run_id,
            "model": self.model,
            "event": event,
            "data": data,
        }

        # WHY: one open-write-close per line keeps every line written before a crash, and no
        # file handle stays open. `default=str` means a strange value in tool arguments (the
        # LLM chooses them) can never break logging. `ensure_ascii=True` writes every non-ASCII
        # character as \\uXXXX, so a line separator inside web text (U+2028) cannot split the
        # line, and a lone surrogate cannot make the write fail and lose the line.
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=True, default=str)
            with self.path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
        except (OSError, TypeError, ValueError):
            # WHY: the run matters more than one lost line; the count shows the loss. A
            # TypeError or ValueError can come from data JSON cannot encode even with `str`
            # (for example a dict key that is a tuple).
            self.write_errors += 1
        return record


def read_trace(path: Path) -> list[dict[str, Any]]:
    """Read a trace file and return its records in order, skipping empty lines."""
    records = []
    # WHY: split on "\n" only. `splitlines()` also splits on U+2028 and similar characters,
    # which would cut a record in the middle.
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.strip():
            records.append(json.loads(line))
    return records
