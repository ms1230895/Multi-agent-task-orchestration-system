"""Read the traces of a pass and group its failures, so the top causes come from evidence.

Architecture layer: a read-only layer on top of layer 7 (Benchmark). It reads
`benchmark/results/<pass_id>/` and `logs/`; it never runs a task or calls an LLM. The counts
are code heuristics: people (Gaurav and Claude) read them and the digests and name the causes.
See `docs/specs/evaluation.md` (section 5.4).

Terminal use: `python -m evaluation.analyze --pass main-on-1 [--out docs/analysis/main-on-1.md]`.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from benchmark.runner import _redact, is_infra_error
from evaluation.passes import PassData, ReportError, load_pass
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

ITERATION_LIMIT_TEXT = "Step not completed: tool iteration limit reached."

# WHY: caps for the digests (spec section 5.4). A digest goes into a committed Markdown file,
# so every field is short.
MAX_DIGEST_ANSWER = 300
MAX_DIGEST_FEEDBACK = 200
MAX_DIGEST_DETAIL = 200
MAX_DIGEST_FIELD = 150
MAX_DIGEST_FAILED_CALLS = 10

# WHY: a run id comes from a committed results file, which is untrusted data. It becomes part
# of a file path, so only the characters of a real run id are allowed (no `/` or `..`).
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DIGITS = re.compile(r"\d")
_SPACES = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


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


def rejected_without_tool(trace: list[dict[str, Any]]) -> int:
    """Count critic rejections of a step attempt in which no tool was called.

    A `critic_verdict` other than `approve` counts when no `tool_call` happened since the
    previous `critic_verdict`, `plan_created` or `replan` event.

    WHY: first-run problem P2 (and file-02 in smoke-2): the critic asked for a tool call on a
    step that only needed earlier results. This is the mechanical trace of that; it shows
    where to read, it does not prove the cause.
    """
    count = 0
    tool_seen = False
    for record in trace:
        event = record.get("event")
        data = record.get("data")
        if event == "tool_call":
            tool_seen = True
        elif event in ("plan_created", "replan"):
            tool_seen = False
        elif event == "critic_verdict":
            verdict = data.get("verdict") if isinstance(data, dict) else None
            if verdict != "approve" and not tool_seen:
                count += 1
            tool_seen = False
    return count


def iteration_limit_hits(trace: list[dict[str, Any]]) -> int:
    """Count critic verdicts whose judged output says the tool iteration limit was reached."""
    return sum(
        1
        for data in events_of(trace, "critic_verdict")
        if str(data.get("output", "")).startswith(ITERATION_LIMIT_TEXT)
    )


def unused_expected_tool(result: dict[str, Any]) -> str | None:
    """What a task of this category was expected to use but did not, or None.

    WHY: information only (known weak spot D39). The model knows many search facts without
    searching, so a task can succeed without its tool. Success is decided by the checkers.
    """
    tools = result.get("tools_used")
    used = {item for item in tools if isinstance(item, str)} if isinstance(tools, list) else set()
    category = result.get("category")
    if category == "search" and "web_search" not in used:
        return "web_search"
    if category == "calc" and "calculator" not in used:
        return "calculator"
    if category == "file" and not used & {"read_file", "write_file"}:
        return "read_file or write_file"
    if category == "multi-tool" and len(used) < 2:
        return "at least 2 different tools"
    return None


def _one_line(value: Any, limit: int) -> str:
    """Make untrusted text safe for a committed Markdown file.

    The text is redacted (local paths, URL queries), stripped of control characters, put on
    one line, has its backticks replaced by `'`, and is cut to `limit` characters.

    WHY: results and traces hold web text and model output. A digest must not hold a local
    path, break the Markdown, or carry a line that looks like a heading.
    """
    text = _redact(str(value if value is not None else ""))
    text = _SPACES.sub(" ", _CONTROL.sub(" ", text)).replace("`", "'").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def make_digest(
    result: dict[str, Any], outcome: OutcomeClass, trace: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Summarise one run that did not succeed, so a person can read why.

    Returns:
        Task id, outcome class, run id, the failed checks, the last plan, every critic
        verdict, up to 10 failed tool calls and the final answer, all cut short.
    """
    checks = result.get("checks")
    failed_checks = [
        _one_line(f"{c.get('type')} on {c.get('source')}: {c.get('detail')}", MAX_DIGEST_DETAIL)
        for c in (checks if isinstance(checks, list) else [])
        if isinstance(c, dict) and c.get("passed") is not True
    ]
    plan: list[str] = []
    verdicts: list[str] = []
    calls: list[str] = []
    if trace is not None:
        for name in ("plan_created", "replan"):
            for data in events_of(trace, name):
                steps = data.get("steps")
                plan = (
                    [_one_line(s, MAX_DIGEST_FIELD) for s in steps]
                    if isinstance(steps, list)
                    else plan
                )
        for data in events_of(trace, "critic_verdict"):
            verdicts.append(
                f"step {data.get('step')} {data.get('verdict')}: "
                + _one_line(data.get("feedback"), MAX_DIGEST_FEEDBACK)
            )
        for data in failed_calls(trace)[:MAX_DIGEST_FAILED_CALLS]:
            args = json.dumps(data.get("args"), sort_keys=True, default=str)
            calls.append(
                f"{_one_line(data.get('tool'), MAX_DIGEST_FIELD)}"
                f"({_one_line(args, MAX_DIGEST_FIELD)}) -> "
                + _one_line(data.get("error"), MAX_DIGEST_FIELD)
            )
    return {
        "task_id": str(result.get("task_id")),
        "outcome": outcome,
        "run_id": _one_line(result.get("run_id"), 64),
        "failed_checks": failed_checks,
        "plan": plan,
        "critic": verdicts,
        "failed_calls": calls,
        "final_answer": _one_line(result.get("final_answer"), MAX_DIGEST_ANSWER),
    }


def analyze_pass(data: PassData, logs_dir: Path = LOGS_DIR) -> dict[str, Any]:
    """Analyse one pass (see spec section 4 for the shape of the result).

    Args:
        data: a pass read with `load_pass`.
        logs_dir: the folder with the trace files.

    Returns:
        Outcome classes, failed tool calls by tool and kind, repeated failing calls,
        rejections without a tool call, iteration-limit hits, tools not used, plan steps and
        step retries per category, the run ids with a missing trace, and one digest per
        result that is not a success. Runs whose trace is missing are listed in
        `missing_traces`; only the numbers that need a trace skip them.
    """
    outcomes: Counter[str] = Counter({name: 0 for name in OUTCOME_CLASSES})
    failed: dict[tuple[str, str], dict[str, Any]] = {}
    repeated_runs = 0
    repeated_calls = 0
    rejected_total = 0
    rejected_by_category: Counter[str] = Counter()
    limit_hits = 0
    not_used: list[dict[str, str]] = []
    plan_steps: dict[str, list[int]] = {}
    step_retries: Counter[str] = Counter()
    digests: list[dict[str, Any]] = []
    missing: list[str] = []

    for result in data.results:
        trace = load_run_trace(result.get("run_id"), logs_dir)
        outcome = classify(result, trace)
        outcomes[outcome] += 1
        category = str(result.get("category", "unknown"))

        raw_counters = result.get("counters")
        counters: dict[str, Any] = raw_counters if isinstance(raw_counters, dict) else {}
        steps = counters.get("plan_steps")
        if isinstance(steps, int) and not isinstance(steps, bool):
            plan_steps.setdefault(category, []).append(steps)
        retries = counters.get("step_retries")
        if isinstance(retries, int) and not isinstance(retries, bool):
            step_retries[category] += retries

        expected = unused_expected_tool(result)
        if expected is not None:
            not_used.append({"task_id": str(result.get("task_id")), "expected": expected})

        if trace is None:
            missing.append(str(result.get("run_id")))
        else:
            for call in failed_calls(trace):
                key = (str(call.get("tool")), error_kind(call.get("error")))
                entry = failed.setdefault(
                    key,
                    {
                        "tool": key[0],
                        "kind": key[1],
                        "count": 0,
                        "example_run": result.get("run_id"),
                    },
                )
                entry["count"] += 1
            repeats = count_repeats(trace)
            if repeats:
                repeated_runs += 1
                repeated_calls += repeats
            rejected = rejected_without_tool(trace)
            rejected_total += rejected
            if rejected:
                rejected_by_category[category] += rejected
            limit_hits += iteration_limit_hits(trace)

        if outcome != "success":
            digests.append(make_digest(result, outcome, trace))

    return {
        "pass_id": data.pass_id,
        "outcomes": dict(outcomes),
        "failed_tool_calls": sorted(
            failed.values(), key=lambda item: (-item["count"], item["tool"], item["kind"])
        ),
        "repeated_failing_calls": {"runs": repeated_runs, "repeats": repeated_calls},
        "rejected_without_tool": {
            "total": rejected_total,
            "by_category": dict(rejected_by_category),
        },
        "iteration_limit_hits": limit_hits,
        "tool_not_used": not_used,
        "plan_steps": plan_steps,
        "step_retries": dict(step_retries),
        "missing_traces": missing,
        "digests": digests,
    }


def render_analysis(pass_id: str, analysis: dict[str, Any]) -> str:
    """Write an analysis dict as Markdown: counts first, then one digest per failed task."""
    lines = [f"# Analysis of pass {pass_id}", "", "## Outcomes", ""]
    lines += ["| class | tasks |", "|---|---|"]
    lines += [f"| {name} | {count} |" for name, count in analysis["outcomes"].items()]

    lines += ["", "## Failed tool calls (by tool and kind)", ""]
    if analysis["failed_tool_calls"]:
        lines += ["| tool | kind | count | example run |", "|---|---|---|---|"]
        lines += [
            f"| {c['tool']} | {c['kind']} | {c['count']} | {c['example_run']} |"
            for c in analysis["failed_tool_calls"]
        ]
    else:
        lines.append("none")

    repeated = analysis["repeated_failing_calls"]
    rejected = analysis["rejected_without_tool"]
    lines += ["", "## Known problems", ""]
    lines.append(
        f"- repeated failing calls (P4): {repeated['repeats']} repeats in {repeated['runs']} runs"
    )
    by_category = ", ".join(f"{k} {v}" for k, v in sorted(rejected["by_category"].items()))
    lines.append(
        f"- critic rejections with no tool call in the attempt (P2): {rejected['total']}"
        + (f" ({by_category})" if by_category else "")
    )
    lines.append(f"- iteration-limit hits: {analysis['iteration_limit_hits']}")

    lines += [
        "",
        "## Per category",
        "",
        "| category | plan steps per task | step retries |",
        "|---|---|---|",
    ]
    categories = sorted(set(analysis["plan_steps"]) | set(analysis["step_retries"]))
    for category in categories:
        steps = analysis["plan_steps"].get(category, [])
        lines.append(f"| {category} | {steps} | {analysis['step_retries'].get(category, 0)} |")

    lines += ["", "## Tool not used (information only)", ""]
    unused = analysis["tool_not_used"]
    lines += [f"- {u['task_id']}: expected {u['expected']}" for u in unused] if unused else ["none"]

    lines += ["", "## Missing traces", ""]
    missing = analysis["missing_traces"]
    lines += [f"- {run_id}" for run_id in missing] if missing else ["none"]

    lines += ["", "## Digests (one per task that did not succeed)", ""]
    if not analysis["digests"]:
        lines.append("none")
    for d in analysis["digests"]:
        lines.append(f"### {d['task_id']}: {d['outcome']} (run {d['run_id']})")
        lines.append("")
        lines += [f"- failed check: {item}" for item in d["failed_checks"]]
        if d["plan"]:
            lines.append("- plan: " + " | ".join(d["plan"]))
        lines += [f"- critic {item}" for item in d["critic"]]
        lines += [f"- failed call: {item}" for item in d["failed_calls"]]
        lines += [f"- final answer: {d['final_answer']}", ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """Command line: `python -m evaluation.analyze --pass <id> [--out file.md]`.

    Returns:
        0 when the analysis was printed (and written), 2 when the pass is invalid or missing.
        This tool only reads results and traces; it never runs a task.
    """
    parser = argparse.ArgumentParser(prog="python -m evaluation.analyze", description=__doc__)
    parser.add_argument("--pass", dest="pass_id", required=True, help="the pass id to analyse")
    parser.add_argument("--out", type=Path, help="write the Markdown to this file too")
    args = parser.parse_args(argv)
    try:
        data = load_pass(args.pass_id)
    except ReportError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    text = render_analysis(data.pass_id, analyze_pass(data))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
