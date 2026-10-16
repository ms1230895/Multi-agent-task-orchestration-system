"""Read traces and benchmark passes from disk for the Streamlit pages.

Architecture layer: 9 (interface). No Streamlit import. Read-only: it never writes a file and
never calls an LLM. Passes are read with the evaluation code, so the dashboard shows exactly
the numbers in `docs/results.md`.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from benchmark.runner import PASS_ID_PATTERN, RESULTS_DIR
from evaluation.passes import (
    RUN_ID_PATTERN,
    PassData,
    ReportError,
    load_pass,
    load_run_trace,
)
from src.config import LOGS_DIR
from src.logger import read_trace, trim

# WHY: a public copy of the app must not read a huge file into memory. A real trace is a few
# kilobytes; this is far above that.
MAX_TRACE_BYTES = 5 * 1024 * 1024

# WHY: an error text shown on a page stays short.
MAX_ERROR_CHARS = 200


@dataclass(frozen=True)
class TraceSummary:
    """One line of the trace list.

    Attributes:
        run_id: the id, also the file name.
        model: the model name from the first record (`""` when unreadable).
        task: the task from `run_end` (`""` when there is no `run_end`).
        status: `done` or `failed` (from `run_end`), `incomplete` (no `run_end`: the run was
            interrupted or is still going) or `unreadable` (too big, or not valid JSON lines).
        duration_ms: the run time from `run_end`, or None.
        counters: the integer counters from `run_end`.
    """

    run_id: str
    model: str = ""
    task: str = ""
    status: str = "unreadable"
    duration_ms: int | None = None
    counters: dict[str, int] = field(default_factory=dict)


def _is_int(value: object) -> bool:
    """True for a real int; `bool` is an int in Python and must not count."""
    return isinstance(value, int) and not isinstance(value, bool)


def _summarize(path: Path, run_id: str) -> TraceSummary:
    """Summarise one trace file; any problem gives an `unreadable` row."""
    # WHY: check the size before reading, so a huge file is never loaded.
    try:
        if path.stat().st_size > MAX_TRACE_BYTES:
            return TraceSummary(run_id=run_id)
        records = [r for r in read_trace(path) if isinstance(r, dict)]
    except (OSError, ValueError, RecursionError):
        return TraceSummary(run_id=run_id)

    model = next((str(r["model"]) for r in records if isinstance(r.get("model"), str)), "")
    run_end = next((r for r in reversed(records) if r.get("event") == "run_end"), None)
    if run_end is None:
        return TraceSummary(run_id=run_id, model=model, status="incomplete")

    data = run_end.get("data")
    data = data if isinstance(data, dict) else {}
    counters = data.get("counters")
    duration = data.get("duration_ms")
    return TraceSummary(
        run_id=run_id,
        model=model,
        task=str(data.get("task", "")),
        status="done" if data.get("status") == "done" else "failed",
        duration_ms=duration if _is_int(duration) else None,
        counters={k: v for k, v in counters.items() if _is_int(v)}
        if isinstance(counters, dict)
        else {},
    )


def list_traces(logs_dir: Path = LOGS_DIR) -> list[TraceSummary]:
    """List the traces in a folder, newest first.

    Args:
        logs_dir: the folder with the `<run_id>.jsonl` files.

    Returns:
        One `TraceSummary` per file whose name is a valid run id. Other files are skipped. A
        missing folder gives an empty list. It never raises.

    WHY: the run id starts with the UTC time, so sorting the ids backwards puts the newest run
    first. A broken file is listed as `unreadable` instead of hiding it or stopping the page.
    """
    if not logs_dir.is_dir():
        return []
    summaries = []
    try:
        paths = sorted(logs_dir.glob("*.jsonl"), reverse=True)
    except OSError:
        return []
    for path in paths:
        if RUN_ID_PATTERN.fullmatch(path.stem):
            summaries.append(_summarize(path, path.stem))
    return sorted(summaries, key=lambda item: item.run_id, reverse=True)


def load_trace(run_id: str, logs_dir: Path = LOGS_DIR) -> list[dict[str, Any]] | None:
    """Read the records of one run, or None when the id is invalid or the file unreadable.

    WHY: it goes through the evaluation code, so the explorer and the analysis validate a run
    id in the same way. Files over `MAX_TRACE_BYTES` are refused here too.
    """
    path = logs_dir / f"{run_id}.jsonl"
    try:
        if path.stat().st_size > MAX_TRACE_BYTES:
            return None
    except (OSError, TypeError):
        return None
    return load_run_trace(run_id, logs_dir)


def list_passes(
    results_root: Path = RESULTS_DIR,
) -> tuple[list[PassData], list[tuple[str, str]]]:
    """Read every pass folder.

    Args:
        results_root: the folder that holds the pass folders.

    Returns:
        `(passes, broken)`: the passes that loaded, in folder-name order, and `(pass_id,
        error text)` for each folder that did not. It never raises.

    WHY: one broken or half-written pass must not hide the others, so it is listed with the
    reason instead.
    """
    if not results_root.is_dir():
        return [], []
    passes: list[PassData] = []
    broken: list[tuple[str, str]] = []
    try:
        folders = sorted(path for path in results_root.iterdir() if path.is_dir())
    except OSError:
        return [], []
    for folder in folders:
        if not PASS_ID_PATTERN.fullmatch(folder.name):
            continue
        try:
            passes.append(load_pass(folder.name, results_root))
        except (ReportError, OSError) as error:
            broken.append((folder.name, trim(str(error), MAX_ERROR_CHARS)))
    return passes, broken
