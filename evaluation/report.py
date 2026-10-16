"""Compare benchmark passes and count traced runs.

Architecture layer: a read-only layer on top of layer 7 (Benchmark). It reads
`benchmark/results/` and `logs/`; it never runs a task or calls an LLM. See
`docs/specs/evaluation.md` (sections 5.3 and 5.4).

Terminal use: `python -m evaluation.report --passes main-off-1,main-on-1 [--write docs/results.md]`.
"""

from pathlib import Path
from typing import Any

from evaluation.passes import PassData, PassGroup
from src.config import LOGS_DIR
from src.logger import read_trace, trace_path

# NOTE: a comparison row is a plain dict so it can be printed as a table or saved as JSON.
Comparison = dict[str, Any]

ABLATION_FLAGS = ("config.enable_validation", "config.enable_retry")


def group_label(group: PassGroup) -> str:
    """A short name for a group: its pass ids joined with `+`."""
    return "+".join(group.pass_ids)


def _differences(first: PassGroup, second: PassGroup) -> set[str]:
    """Name every setting in which two groups differ.

    Returns:
        A set such as `{"model"}`, `{"code_fingerprint"}` or `{"config.max_replans"}`.

    WHY: the model name is in both `model` and `config.ollama_model`; it is reported once, as
    `model`, so "different model" is one difference and not two.
    """
    names: set[str] = set()
    if first.model != second.model:
        names.add("model")
    if first.fingerprint != second.fingerprint:
        names.add("code_fingerprint")
    for key in set(first.config) | set(second.config):
        if key == "ollama_model":
            continue
        if first.config.get(key, object()) != second.config.get(key, object()):
            names.add(f"config.{key}")
    return names


def _relative_reduction(off_rate: float | None, on_rate: float | None) -> float | None:
    """(off - on) / off, or None when it cannot be computed.

    WHY: with no failed calls in the off pass there is nothing to reduce, so the CV must not
    claim a reduction. A negative value is kept: the gate added failed calls.
    """
    if off_rate is None or on_rate is None or off_rate == 0:
        return None
    return (off_rate - on_rate) / off_rate


def _ablation_row(off: PassGroup, on: PassGroup) -> Comparison:
    """Build the gate-off vs gate-on row."""
    off_m, on_m = off.metrics, on.metrics
    off_rate, on_rate = off_m["failed_tool_call_rate"], on_m["failed_tool_call_rate"]
    return {
        "kind": "ablation",
        "off": group_label(off),
        "on": group_label(on),
        "failed_calls_off": off_m["failed_tool_calls"],
        "tool_calls_off": off_m["tool_calls"],
        "failed_calls_on": on_m["failed_tool_calls"],
        "tool_calls_on": on_m["tool_calls"],
        "failed_rate_off": off_rate,
        "failed_rate_on": on_rate,
        "difference_points": (
            None if off_rate is None or on_rate is None else (on_rate - off_rate) * 100
        ),
        "relative_reduction": _relative_reduction(off_rate, on_rate),
        "success_off": off_m["successes"],
        "tasks_off": off_m["tasks_done"],
        "success_ci_off": off_m["success_ci"],
        "success_on": on_m["successes"],
        "tasks_on": on_m["tasks_done"],
        "success_ci_on": on_m["success_ci"],
        "validation_errors_on": on_m["validation_errors"],
        "tool_retries_on": on_m["tool_retries"],
    }


def _model_row(first: PassGroup, second: PassGroup) -> Comparison:
    """Build the model-vs-model row."""
    row: Comparison = {"kind": "model", "groups": []}
    for group in (first, second):
        metrics = group.metrics
        row["groups"].append(
            {
                "label": group_label(group),
                "model": group.model,
                "successes": metrics["successes"],
                "tasks": metrics["tasks_done"],
                "success_ci": metrics["success_ci"],
                "failed_rate": metrics["failed_tool_call_rate"],
                "failed_calls": metrics["failed_tool_calls"],
                "tool_calls": metrics["tool_calls"],
                "by_category": metrics["by_category"],
            }
        )
    return row


def _fix_row(before: PassGroup, after: PassGroup) -> Comparison:
    """Build the before-fix vs after-fix row."""
    row: Comparison = {"kind": "fix", "sides": []}
    for name, group in (("before", before), ("after", after)):
        metrics = group.metrics
        row["sides"].append(
            {
                "side": name,
                "label": group_label(group),
                "successes": metrics["successes"],
                "tasks": metrics["tasks_done"],
                "success_ci": metrics["success_ci"],
                "failed_rate": metrics["failed_tool_call_rate"],
                "failed_calls": metrics["failed_tool_calls"],
                "tool_calls": metrics["tool_calls"],
                "step_retries": metrics["step_retries"],
                "replans": metrics["replans"],
                "outcomes": metrics["outcomes"],
            }
        )
    return row


def compare_groups(groups: list[PassGroup]) -> tuple[list[Comparison], list[str]]:
    """Compare every pair of groups that differ in exactly one thing.

    Args:
        groups: pooled groups from `group_passes`, ordered by `started_at`.

    Returns:
        `(rows, not_compared)`. A row is made for an `ablation` pair (only the two gate flags
        differ, one group with both off and one with both on), a `model` pair (only the model
        differs) or a `fix` pair (only the code fingerprint differs; "before" is the group
        with the earlier `started_at`). A pair with the same tasks and model that fits no
        kind gives a line in `not_compared` that names what differs.

    WHY: only a pair that differs in one thing measures that thing (architecture 5). The
    "not compared" lines make a missing row visible; it usually means `.env` was changed by
    mistake between two passes.
    """
    rows: list[Comparison] = []
    not_compared: list[str] = []
    for index, first in enumerate(groups):
        for second in groups[index + 1 :]:
            if first.task_ids != second.task_ids:
                continue
            differences = _differences(first, second)
            if not differences:
                continue
            if differences == {"model"}:
                rows.append(_model_row(first, second))
            elif differences == {"code_fingerprint"}:
                before, after = (
                    (first, second) if first.started_at <= second.started_at else (second, first)
                )
                rows.append(_fix_row(before, after))
            elif differences == set(ABLATION_FLAGS):
                off = _flags_state(first)
                on = _flags_state(second)
                if off is False and on is True:
                    rows.append(_ablation_row(first, second))
                elif off is True and on is False:
                    rows.append(_ablation_row(second, first))
                else:
                    not_compared.append(_reason(first, second, differences))
            elif first.model == second.model:
                # WHY: only same-model pairs are listed; a pair that differs in the model and
                # more is not a mistake worth a line each (spec section 5.3).
                not_compared.append(_reason(first, second, differences))
    return rows, not_compared


def _flags_state(group: PassGroup) -> bool | None:
    """True if both gate flags are on, False if both are off, None if they are mixed."""
    validation = group.config.get("enable_validation")
    retry = group.config.get("enable_retry")
    if validation is True and retry is True:
        return True
    if validation is False and retry is False:
        return False
    return None


def _reason(first: PassGroup, second: PassGroup, differences: set[str]) -> str:
    """A "not compared" line naming the passes and what differs."""
    return (
        f"{group_label(first)} vs {group_label(second)}: not compared, "
        f"they differ in {', '.join(sorted(differences))}"
    )


def _scan_traces(logs_dir: Path) -> tuple[int, int]:
    """Count `run_end` events in `logs_dir/*.jsonl`.

    Returns:
        `(run_end_count, unreadable_files)`.
    """
    if not logs_dir.is_dir():
        return 0, 0
    runs = 0
    unreadable = 0
    for path in sorted(logs_dir.glob("*.jsonl")):
        try:
            records = read_trace(path)
        except (OSError, ValueError, RecursionError):
            # WHY: one broken trace must not hide the others; it is counted so the report can
            # say how many files could not be read.
            unreadable += 1
            continue
        runs += sum(
            1 for record in records if isinstance(record, dict) and record.get("event") == "run_end"
        )
    return runs, unreadable


def count_traces(logs_dir: Path = LOGS_DIR) -> int:
    """The number of `run_end` events in `logs/`: the "traced runs" number (architecture 5)."""
    return _scan_traces(logs_dir)[0]


def trace_coverage(data: PassData, logs_dir: Path = LOGS_DIR) -> tuple[int, int]:
    """How many result lines of a pass have a trace that ends in exactly one `run_end`.

    Returns:
        `(with_trace, results)`.

    WHY: the CV number "traces across 100+ runs" is only true if each result has its trace.
    """
    covered = 0
    for record in data.results:
        run_id = record.get("run_id")
        if not isinstance(run_id, str):
            continue
        try:
            events = [item.get("event") for item in read_trace(trace_path(run_id, logs_dir))]
        except (OSError, ValueError, RecursionError, AttributeError):
            continue
        if events.count("run_end") == 1 and events[-1] == "run_end":
            covered += 1
    return covered, len(data.results)
