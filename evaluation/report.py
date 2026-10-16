"""Compare benchmark passes and count traced runs.

Architecture layer: a read-only layer on top of layer 7 (Benchmark). It reads
`benchmark/results/` and `logs/`; it never runs a task or calls an LLM. See
`docs/specs/evaluation.md` (sections 5.3 and 5.4).

Terminal use: `python -m evaluation.report --passes main-off-1,main-on-1 [--write docs/results.md]`.
"""

import argparse
import sys
from pathlib import Path
from typing import Any

from evaluation.passes import (
    PassData,
    PassGroup,
    ReportError,
    group_passes,
    load_pass,
    pass_metrics,
)
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


START_MARKER = "<!-- evaluation-report:start -->"
END_MARKER = "<!-- evaluation-report:end -->"
CATEGORY_ORDER = ("calc", "search", "file", "multi-tool")
POINTS_NOTE = (
    "30 tasks per pass: one task = 3.3 percentage points. Intervals are 95% Wilson intervals."
)


def _pct(rate: float | None) -> str:
    """A rate as a percentage with one decimal, or `n/a`."""
    return "n/a" if rate is None else f"{rate * 100:.1f}%"


def _success_text(successes: int, tasks: int, interval: tuple[float, float] | None) -> str:
    """`n/N (rate) [low–high]`: a success rate always travels with its interval."""
    if tasks == 0 or interval is None:
        return f"{successes}/{tasks} (n/a)"
    low, high = interval
    return f"{successes}/{tasks} ({_pct(successes / tasks)}) [{_pct(low)}–{_pct(high)}]"


def _failed_text(failed: int, calls: int, rate: float | None) -> str:
    """`failed/calls (rate)` for the failed-tool-call metric."""
    return f"{failed}/{calls} ({_pct(rate)})"


def _cell(text: Any) -> str:
    """One Markdown table cell: no pipe or newline can break the table."""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(header: list[str], rows: list[list[Any]]) -> list[str]:
    """Render a Markdown table."""
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines.extend("| " + " | ".join(_cell(item) for item in row) + " |" for row in rows)
    return lines


def _category_text(metrics: dict[str, Any], category: str) -> str:
    """`successes/tasks` of one category, or `-` if the pass has none."""
    entry = metrics["by_category"].get(category)
    return f"{entry['successes']}/{entry['tasks']}" if entry else "-"


def _passes_table(passes: list[PassData]) -> list[str]:
    """One row per pass with all the per-pass numbers (spec section 5.3, item 1)."""
    header = [
        "pass", "model", "validation", "retry", "code", "complete", "success",
        *CATEGORY_ORDER, "tool calls", "failed calls", "validation errors", "tool retries",
        "step retries", "replans", "crashed", "pauses", "median s",
    ]  # fmt: skip
    rows = []
    for data in passes:
        m = pass_metrics(data)
        planned = len(data.manifest["task_ids"])
        rows.append(
            [
                data.pass_id,
                m["model"],
                m["enable_validation"],
                m["enable_retry"],
                m["fingerprint_short"],
                "yes" if m["complete"] else f"incomplete {m['tasks_done']}/{planned}",
                _success_text(m["successes"], m["tasks_done"], m["success_ci"]),
                *[_category_text(m, category) for category in CATEGORY_ORDER],
                m["tool_calls"],
                _failed_text(m["failed_tool_calls"], m["tool_calls"], m["failed_tool_call_rate"]),
                m["validation_errors"],
                m["tool_retries"],
                m["step_retries"],
                m["replans"],
                m["crashed"],
                m["infra_pauses"],
                "n/a" if m["median_duration_s"] is None else f"{m['median_duration_s']:.1f}",
            ]
        )
    return _table(header, rows)


def _groups_table(groups: list[PassGroup]) -> list[str]:
    """One row per pooled group."""
    rows = []
    for group in groups:
        m = group.metrics
        rows.append(
            [
                group_label(group),
                group.model,
                m["passes"],
                _success_text(m["successes"], m["tasks_done"], m["success_ci"]),
                _failed_text(m["failed_tool_calls"], m["tool_calls"], m["failed_tool_call_rate"]),
                m["step_retries"],
                m["replans"],
            ]
        )
    header = ["passes", "model", "count", "success", "failed calls", "step retries", "replans"]
    return _table(header, rows)


def _reduction_text(value: float | None) -> str:
    """The relative reduction; a negative value is shown as an increase, never hidden."""
    if value is None:
        return "n/a"
    if value < 0:
        return f"{value * 100:.1f}% (the gate added failed calls)"
    return f"{value * 100:.1f}%"


def _ablation_table(rows: list[Comparison]) -> list[str]:
    """Gate off vs gate on."""
    header = [
        "off", "on", "failed calls off", "failed calls on", "difference (points)",
        "relative reduction", "success off", "success on", "validation errors (on)",
        "tool retries (on)",
    ]  # fmt: skip
    body = []
    for r in rows:
        difference = r["difference_points"]
        body.append(
            [
                r["off"],
                r["on"],
                _failed_text(r["failed_calls_off"], r["tool_calls_off"], r["failed_rate_off"]),
                _failed_text(r["failed_calls_on"], r["tool_calls_on"], r["failed_rate_on"]),
                "n/a" if difference is None else f"{difference:+.1f}",
                _reduction_text(r["relative_reduction"]),
                _success_text(r["success_off"], r["tasks_off"], r["success_ci_off"]),
                _success_text(r["success_on"], r["tasks_on"], r["success_ci_on"]),
                r["validation_errors_on"],
                r["tool_retries_on"],
            ]
        )
    return _table(header, body)


def _model_table(rows: list[Comparison]) -> list[str]:
    """Model against model, with the categories."""
    header = ["passes", "model", "success", "failed calls", *CATEGORY_ORDER]
    body = []
    for r in rows:
        for g in r["groups"]:
            categories = []
            for category in CATEGORY_ORDER:
                entry = g["by_category"].get(category)
                categories.append(f"{entry['successes']}/{entry['tasks']}" if entry else "-")
            body.append(
                [
                    g["label"],
                    g["model"],
                    _success_text(g["successes"], g["tasks"], g["success_ci"]),
                    _failed_text(g["failed_calls"], g["tool_calls"], g["failed_rate"]),
                    *categories,
                ]
            )
    return _table(header, body)


def _fix_table(rows: list[Comparison]) -> list[str]:
    """Before a fix against after it."""
    header = [
        "side", "passes", "success", "failed calls", "step retries", "replans",
        "success / done but wrong / failed",
    ]  # fmt: skip
    body = []
    for r in rows:
        for s in r["sides"]:
            o = s["outcomes"]
            body.append(
                [
                    s["side"],
                    s["label"],
                    _success_text(s["successes"], s["tasks"], s["success_ci"]),
                    _failed_text(s["failed_calls"], s["tool_calls"], s["failed_rate"]),
                    s["step_retries"],
                    s["replans"],
                    f"{o['success']} / {o['done_but_wrong']} / {o['failed']}",
                ]
            )
    return _table(header, body)


def render_markdown(passes: list[PassData], logs_dir: Path = LOGS_DIR) -> str:
    """Build the results block for `docs/results.md`.

    Args:
        passes: the passes to report, in the order they should appear.
        logs_dir: the folder with the trace files.

    Returns:
        Markdown text. It holds no time stamp or anything else that changes between two runs
        on the same files, so running it twice gives the same text.

    WHY: `docs/results.md` must be regenerated, never hand-edited, so a number cannot be
    copied wrongly onto a CV.
    """
    groups = group_passes(passes)
    rows, not_compared = compare_groups(groups)
    runs, unreadable = _scan_traces(logs_dir)

    lines: list[str] = ["### Passes", "", *_passes_table(passes)]
    lines += ["", "### Pooled groups (repeat passes with the same model, config and code)", ""]
    lines += _groups_table(groups) if groups else ["none (no complete pass)"]
    for title, kind, make_table in (
        ("Ablation: gate off vs gate on", "ablation", _ablation_table),
        ("Model comparison", "model", _model_table),
        ("Before vs after fixes", "fix", _fix_table),
    ):
        selected = [row for row in rows if row["kind"] == kind]
        lines += ["", f"### {title}", ""]
        lines += make_table(selected) if selected else ["none"]
    lines += ["", "### Not compared", ""]
    lines += [f"- {reason}" for reason in not_compared] if not_compared else ["none"]
    lines += ["", "### Traced runs", ""]
    lines.append(f"- `run_end` events in `logs/`: **{runs}**")
    if unreadable:
        lines.append(f"- trace files that could not be read: {unreadable}")
    for data in passes:
        covered, total = trace_coverage(data, logs_dir)
        lines.append(f"- {data.pass_id}: {covered}/{total} results have a complete trace")
    lines += ["", POINTS_NOTE]
    return "\n".join(lines)


def write_block(doc_path: Path, block: str) -> None:
    """Put the results block between the markers of `doc_path`.

    Args:
        doc_path: the Markdown file (normally `docs/results.md`).
        block: the text from `render_markdown`.

    Raises:
        ReportError: if the file has no markers, one marker, two start markers, or the end
            marker before the start marker. The file is left unchanged.

    WHY: the hand-written analysis around the block must never be touched, and a broken
    marker set is a sign the file was edited by mistake.
    """
    wrapped = f"{START_MARKER}\n{block}\n{END_MARKER}"
    if not doc_path.exists():
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(f"# Results\n\n{wrapped}\n", encoding="utf-8")
        return
    text = doc_path.read_text(encoding="utf-8")
    if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise ReportError(f"{doc_path.name} must contain exactly one start and one end marker")
    start = text.index(START_MARKER)
    end = text.index(END_MARKER)
    if end < start:
        raise ReportError(f"{doc_path.name}: the end marker comes before the start marker")
    doc_path.write_text(text[:start] + wrapped + text[end + len(END_MARKER) :], encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Command line: `python -m evaluation.report --passes a,b [--write docs/results.md]`.

    Returns:
        0 when the block was printed (and written), 2 when a pass or the file is refused.
        This tool only reads results and traces; it never runs a task.
    """
    parser = argparse.ArgumentParser(prog="python -m evaluation.report", description=__doc__)
    parser.add_argument("--passes", required=True, help="comma-separated pass ids")
    parser.add_argument("--write", type=Path, help="Markdown file to update between markers")
    args = parser.parse_args(argv)

    ids = [item.strip() for item in args.passes.split(",") if item.strip()]
    try:
        # WHY: a repeated id would count one pass twice and inflate every pooled number.
        if not ids or len(set(ids)) != len(ids):
            raise ReportError("--passes needs at least one pass id and no repeated ids")
        passes = [load_pass(pass_id) for pass_id in ids]
        block = render_markdown(passes)
        if args.write is not None:
            write_block(args.write, block)
    except ReportError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(block)
    if args.write is not None:
        print(f"\nwritten to {args.write}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
