"""Read the traces of a pass and group its failures, so the top causes come from evidence.

Architecture layer: a read-only layer on top of layer 7 (Benchmark). It reads
`benchmark/results/<pass_id>/` and `logs/`; it never runs a task or calls an LLM. The counts
are code heuristics: people (Gaurav and Claude) read them and the digests and name the causes.
See `docs/specs/evaluation.md` (section 5.4).

Terminal use: `python -m evaluation.analyze --pass main-on-1 [--out docs/analysis/main-on-1.md]`.
"""

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from benchmark.runner import is_infra_error
from evaluation.passes import PassData
from src.config import LOGS_DIR
from src.logger import read_trace, trace_path

OutcomeClass = Literal[
    "success",
    "infra",
    "crash",
    "wrong_answer",
    "planner_failed",
    "gave_up_right_answer",
    "gave_up",
    "other",
]
OUTCOME_CLASSES: tuple[OutcomeClass, ...] = (
    "success",
    "infra",
    "crash",
    "wrong_answer",
    "planner_failed",
    "gave_up_right_answer",
    "gave_up",
    "other",
)

MAX_ERROR_KIND_CHARS = 60

# WHY: a run id comes from a committed results file, which is untrusted data. It becomes part
# of a file path, so only the characters of a real run id are allowed (no `/` or `..`).
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DIGITS = re.compile(r"\d")
_SPACES = re.compile(r"\s+")


def load_run_trace(run_id: Any, logs_dir: Path = LOGS_DIR) -> list[dict[str, Any]] | None:
    """Read the trace of one run.

    Returns:
        The records in order, or None when the id is not a valid run id, the file is missing,
        or the file cannot be read. Records that are not JSON objects are dropped.

    WHY: a missing trace must not stop the analysis of the other runs (it is listed instead).
    """
    if not isinstance(run_id, str) or not _RUN_ID_PATTERN.match(run_id):
        return None
    try:
        records = read_trace(trace_path(run_id, logs_dir))
    except (OSError, ValueError, RecursionError):
        return None
    return [record for record in records if isinstance(record, dict)]


def events_of(trace: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    """The `data` dicts of every event with this name, in order."""
    found = []
    for record in trace:
        data = record.get("data")
        if record.get("event") == name and isinstance(data, dict):
            found.append(data)
    return found


def classify(result: dict[str, Any], trace: list[dict[str, Any]] | None) -> OutcomeClass:
    """Sort one result line into an outcome class. The first rule that matches wins.

    Args:
        result: a line of `results.jsonl`.
        trace: the run's trace, or None when it is not available.

    Returns:
        One of `OUTCOME_CLASSES`.

    WHY: the success rate says how many tasks failed; the class says how. "Gave up with the
    right answer" and "done with a wrong answer" need different fixes.
    """
    if result.get("success") is True:
        return "success"
    error = result.get("error")
    if isinstance(error, str) and error:
        # WHY: a task recorded after 3 pauses (D40) keeps its infrastructure error text.
        return "infra" if is_infra_error(error) else "crash"
    status = result.get("status")
    if status == "done":
        return "wrong_answer"
    if status == "failed":
        # WHY: without a trace the planner cannot be told apart from a later giving up, so
        # that rule is skipped and the next ones decide.
        if trace is not None and not events_of(trace, "plan_created"):
            return "planner_failed"
        return "gave_up_right_answer" if result.get("checks_passed") is True else "gave_up"
    return "other"


def error_kind(error: Any) -> str:
    """A short, stable label for an error text, so equal failures group together.

    Examples:
        `"file not found: notes2.txt"` -> `file not found`;
        `"transient: timed out after 3 s"` -> `transient`;
        `"no such file 12"` -> `no such file ##`.

    The text before the first `:` is lower-cased, digits become `#`, spaces collapse, and the
    result is cut to 60 characters.
    """
    head = str(error if error is not None else "").split(":", 1)[0].lower()
    head = _SPACES.sub(" ", _DIGITS.sub("#", head)).strip()
    return head[:MAX_ERROR_KIND_CHARS]


def failed_calls(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The `data` of every `tool_call` event with `ok` false, in order."""
    return [data for data in events_of(trace, "tool_call") if data.get("ok") is False]


def _args_key(data: dict[str, Any]) -> tuple[str, str]:
    """A comparable key for a call: the tool and its arguments as sorted JSON.

    WHY: the same arguments with a different key order are the same call.
    """
    return str(data.get("tool")), json.dumps(data.get("args"), sort_keys=True, default=str)


def count_repeats(trace: list[dict[str, Any]]) -> int:
    """How many failed calls repeat an earlier failed call of the same run.

    WHY: first-run problem P4 (a missing file gave 45 calls, 42 failed): the model repeated
    the same failing call. A call that succeeds is not a failed call, so it is never a repeat.
    """
    seen: set[tuple[str, str]] = set()
    repeats = 0
    for data in failed_calls(trace):
        key = _args_key(data)
        if key in seen:
            repeats += 1
        seen.add(key)
    return repeats


def analyze_pass(data: PassData, logs_dir: Path = LOGS_DIR) -> dict[str, Any]:
    """Analyse one pass: outcome classes, failed tool calls, repeated failing calls.

    Args:
        data: a pass read with `load_pass`.
        logs_dir: the folder with the trace files.

    Returns:
        A dict (see spec section 4). Runs whose trace is missing are listed in
        `missing_traces` (by run id); only the numbers that need a trace skip them.
    """
    outcomes: Counter[str] = Counter({name: 0 for name in OUTCOME_CLASSES})
    failed: dict[tuple[str, str], dict[str, Any]] = {}
    repeated_runs = 0
    repeated_calls = 0
    missing: list[str] = []

    for result in data.results:
        trace = load_run_trace(result.get("run_id"), logs_dir)
        outcomes[classify(result, trace)] += 1
        if trace is None:
            missing.append(str(result.get("run_id")))
            continue
        for call in failed_calls(trace):
            key = (str(call.get("tool")), error_kind(call.get("error")))
            entry = failed.setdefault(
                key,
                {"tool": key[0], "kind": key[1], "count": 0, "example_run": result.get("run_id")},
            )
            entry["count"] += 1
        repeats = count_repeats(trace)
        if repeats:
            repeated_runs += 1
            repeated_calls += repeats

    return {
        "pass_id": data.pass_id,
        "outcomes": dict(outcomes),
        "failed_tool_calls": sorted(
            failed.values(), key=lambda item: (-item["count"], item["tool"], item["kind"])
        ),
        "repeated_failing_calls": {"runs": repeated_runs, "repeats": repeated_calls},
        "missing_traces": missing,
    }
