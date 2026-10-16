"""Read benchmark passes and turn them into numbers that can be compared.

Architecture layer: a read-only layer on top of layer 7 (Benchmark). It reads
`benchmark/results/<pass_id>/` and never runs a task or calls an LLM. See
`docs/specs/evaluation.md` (sections 4, 5.1 and 5.2).

WHY this lives in `evaluation/` and not in `benchmark/`: the code fingerprint covers
`benchmark/*.py`, so every edit here would change it and block resuming a baseline pass.
"""

import json
import math
import os
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# WHY: the three private helpers are imported on purpose. Results must be read and redacted
# exactly the way the runner wrote them, and a copy could drift. Making them public is
# deferred (spec section 9, question 3) because renaming would change the code fingerprint
# before the baseline passes run. A test imports them, so a rename fails loudly.
from benchmark.runner import (
    MANIFEST_NAME,
    PASS_ID_PATTERN,
    PAUSED_NAME,
    RESULTS_DIR,
    RESULTS_NAME,
    PassRefused,
    _read_manifest,
    _read_results,
    _redact,
    summarize,
)
from src.config import LOGS_DIR, PROJECT_ROOT
from src.logger import read_trace, trace_path, trim

# NOTE: 1.96 is the z value of a two-sided 95% interval.
Z_95 = 1.96


class ReportError(Exception):
    """Raised when a pass is missing or broken, or a report input is invalid."""


@dataclass(frozen=True)
class PassData:
    """One pass folder, read from disk.

    Attributes:
        pass_id: the folder name.
        manifest: the parsed `manifest.json`.
        results: the lines of `results.jsonl`, one dict per finished task.
        pauses: the number of lines in `paused.jsonl`.
    """

    pass_id: str
    manifest: dict[str, Any]
    results: list[dict[str, Any]]
    pauses: int

    @property
    def complete(self) -> bool:
        """True when every task of the manifest has a result."""
        return len(self.results) == len(self.manifest["task_ids"])


@dataclass(frozen=True)
class PassGroup:
    """Complete passes that are repeats of one experiment, pooled into one set of numbers.

    Attributes:
        pass_ids: the passes in the group, in `started_at` order.
        model: the model name from the manifests.
        config: the config from the manifests (the same for every pass in the group).
        fingerprint: the code fingerprint (the same for every pass in the group).
        task_ids: the task ids (the same for every pass in the group).
        started_at: the earliest `started_at` of the passes.
        metrics: counts summed over the passes, rates recomputed from the sums.
    """

    pass_ids: list[str]
    model: str
    config: dict[str, Any]
    fingerprint: str
    task_ids: list[str]
    started_at: str
    metrics: dict[str, Any] = field(default_factory=dict)


def load_pass(pass_id: str, results_root: Path = RESULTS_DIR) -> PassData:
    """Read one pass folder.

    Args:
        pass_id: the pass folder name, for example `main-on-1`.
        results_root: the folder that holds all pass folders.

    Returns:
        The manifest, the result lines and the number of pause lines.

    Raises:
        ReportError: if the id is invalid, or the pass is missing or broken. A broken results
            line is reported with its line number, as the runner does.

    WHY: every report and analysis reads passes through this one function, so they all see
    the same data and refuse the same broken input.
    """
    # WHY: the id becomes a folder name; the same pattern as the runner blocks `..` and `/`.
    # WHY: `fullmatch`, because `$` also accepts a trailing newline (review finding S1).
    if not isinstance(pass_id, str) or not PASS_ID_PATTERN.fullmatch(pass_id):
        raise ReportError(f"invalid pass id: {str(pass_id)[:40]!r}")
    pass_dir = results_root / pass_id
    if not (pass_dir / MANIFEST_NAME).is_file():
        raise ReportError(f"no such pass: {pass_id}")
    try:
        manifest = _read_manifest(pass_dir / MANIFEST_NAME)
        results = _read_results(pass_dir / RESULTS_NAME)
    except PassRefused as error:
        raise ReportError(f"pass {pass_id}: {error}") from None
    pauses = 0
    paused_path = pass_dir / PAUSED_NAME
    if paused_path.is_file():
        text = paused_path.read_text(encoding="utf-8")
        pauses = sum(1 for line in text.split("\n") if line.strip())
    return PassData(pass_id=pass_id, manifest=manifest, results=results, pauses=pauses)


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float] | None:
    """The 95% Wilson score interval for `successes` out of `total`.

    Args:
        successes: how many tasks succeeded.
        total: how many tasks have a result.
        z: the z value (default for 95%).

    Returns:
        `(low, high)` between 0 and 1, or None when `total` is 0.

    WHY: with 30 tasks one task is 3.3 percentage points. The interval shows how far apart
    two rates must be before the difference means anything. Wilson is used (not the plain
    normal interval) because it stays inside 0..1 and works for small totals.
    """
    if total <= 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    # WHY: floating point can leave 1e-17 around the edges; the exact edge cases are fixed.
    low = 0.0 if successes == 0 else max(0.0, centre - half)
    high = 1.0 if successes == total else min(1.0, centre + half)
    return (low, high)


def _sum_counter(results: list[dict[str, Any]], key: str) -> int:
    """Sum one run counter over result lines; a missing or non-int counter counts as 0."""
    total = 0
    for record in results:
        counters = record.get("counters")
        value = counters.get(key, 0) if isinstance(counters, dict) else 0
        # WHY: `bool` is an int in Python; a `True` counter must not add 1.
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def _compute(results: list[dict[str, Any]], *, planned: int, pauses: int) -> dict[str, Any]:
    """The numbers shared by a single pass and a pooled group.

    Args:
        results: result lines (one pass, or several passes put together).
        planned: how many tasks were planned over those results.
        pauses: how many infrastructure pauses happened.

    Returns:
        The runner's `summarize` dict plus the secondary sums of spec section 5.2.

    WHY: the headline numbers come from the runner's own `summarize`, so they use exactly
    the session 6 definitions (architecture 5). Only the extras are added here.
    """
    metrics = summarize(results, planned=planned, infra_pauses=pauses)
    metrics["success_ci"] = wilson_interval(metrics["successes"], metrics["tasks_done"])
    for key in ("validation_errors", "tool_retries", "step_retries", "replans"):
        metrics[key] = _sum_counter(results, key)
    metrics["runs_with_failed_call"] = sum(
        1 for record in results if _sum_counter([record], "failed_tool_calls") > 0
    )
    durations = [
        record["duration_ms"] / 1000
        for record in results
        if isinstance(record.get("duration_ms"), int | float)
        and not isinstance(record.get("duration_ms"), bool)
    ]
    metrics["median_duration_s"] = statistics.median(durations) if durations else None
    # WHY: a short split of the non-successes that needs no trace, so a before/after row can
    # show whether a fix moved failures from "gave up" to "wrong answer" or the other way.
    metrics["outcomes"] = {
        "success": metrics["successes"],
        "done_but_wrong": sum(
            1 for r in results if r.get("success") is not True and r.get("status") == "done"
        ),
        "failed": sum(
            1 for r in results if r.get("success") is not True and r.get("status") != "done"
        ),
    }
    return metrics


def pass_metrics(data: PassData) -> dict[str, Any]:
    """All numbers of one pass: the headline metrics, the extras and the pass settings.

    Args:
        data: a pass read with `load_pass`.

    Returns:
        The `_compute` numbers plus `complete`, `model`, `enable_validation`, `enable_retry`,
        `fingerprint_short` (8 hex characters), `git_commit` (7 characters) and `git_dirty`.
    """
    metrics = _compute(data.results, planned=len(data.manifest["task_ids"]), pauses=data.pauses)
    config = data.manifest["config"]
    fingerprint = data.manifest["code_fingerprint"]
    metrics.update(
        complete=data.complete,
        model=data.manifest["model"],
        enable_validation=config.get("enable_validation"),
        enable_retry=config.get("enable_retry"),
        fingerprint_short=fingerprint.removeprefix("sha256:")[:8],
        git_commit=str(data.manifest.get("git_commit", "unknown"))[:7],
        git_dirty=data.manifest.get("git_dirty"),
    )
    return metrics


def group_key(data: PassData) -> tuple[str, str, str, tuple[str, ...]]:
    """What two passes must share to be repeats of one experiment.

    Returns:
        `(model, config as sorted JSON, code fingerprint, task ids)`.

    WHY: passes with a different model, setting, code or task list measure something
    different, so they are never pooled (roadmap 7: before and after a fix are never mixed).
    """
    manifest = data.manifest
    return (
        manifest["model"],
        json.dumps(manifest["config"], sort_keys=True),
        manifest["code_fingerprint"],
        tuple(manifest["task_ids"]),
    )


def group_passes(passes: list[PassData]) -> list[PassGroup]:
    """Put complete passes with the same `group_key` into pooled groups.

    Args:
        passes: passes read with `load_pass`. Incomplete passes are ignored.

    Returns:
        Groups in order of their earliest `started_at`. Counts are summed over the passes and
        rates are recomputed from the sums, never averaged.

    WHY pooled sums: a repeat pass adds evidence. Averaging the rates of passes would give a
    small pass the same weight as a big one.
    """
    buckets: dict[tuple[str, str, str, tuple[str, ...]], list[PassData]] = {}
    for data in passes:
        # WHY: an unfinished pass has a biased sample (the tasks run first are not random).
        if data.complete:
            buckets.setdefault(group_key(data), []).append(data)

    groups = []
    for key, members in buckets.items():
        members = sorted(members, key=lambda item: str(item.manifest.get("started_at", "")))
        pooled_results = [record for member in members for record in member.results]
        metrics = _compute(
            pooled_results,
            planned=sum(len(member.manifest["task_ids"]) for member in members),
            pauses=sum(member.pauses for member in members),
        )
        metrics["passes"] = len(members)
        groups.append(
            PassGroup(
                pass_ids=[member.pass_id for member in members],
                model=key[0],
                config=members[0].manifest["config"],
                fingerprint=key[2],
                task_ids=list(key[3]),
                started_at=str(members[0].manifest.get("started_at", "")),
                metrics=metrics,
            )
        )
    return sorted(groups, key=lambda group: group.started_at)


# ---- Shared helpers: reading traces and cleaning untrusted text (review findings 1 to 4) ----

# WHY: a run id comes from a committed results file, which is untrusted data. It becomes part of
# a file path, so only the characters of a real run id are allowed (no `/` or `..`).
RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")

# WHY: the only folder the tools may write into. A wrong or injected `--out CLAUDE.md` must not
# overwrite a project file (review finding 3). Tests point this at a temporary folder.
OUTPUT_ROOT = PROJECT_ROOT / "docs"

_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_SPACES = re.compile(r"\s+")

# WHY: `_redact` (from the runner) only knows this machine's folders and URL queries. Trace text
# also holds exception text we do not control, so common secret shapes and other people's home
# folders are removed too (review finding 4). `benchmark/runner.py` is not edited: that would
# change the code fingerprint.
_EXTRA_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<secret>"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "<secret>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE), "Bearer <secret>"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "<secret>"),
    (re.compile(r"://[^/\s@]+@"), "://<secret>@"),
    (re.compile(r"/(?:Users|home)/[^/\s]+", re.IGNORECASE), "/<user>"),
)


def load_run_trace(run_id: object, logs_dir: Path = LOGS_DIR) -> list[dict[str, Any]] | None:
    """Read the trace of one run.

    Returns:
        The records in order, or None when the id is not a valid run id, the file is missing,
        or the file cannot be read. Records that are not JSON objects are dropped.

    WHY: one function for both tools, so they validate a run id the same way. A missing trace
    must not stop the analysis of the other runs (it is listed instead).
    """
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        return None
    try:
        records = read_trace(trace_path(run_id, logs_dir))
    except (OSError, ValueError, RecursionError):
        return None
    return [record for record in records if isinstance(record, dict)]


def one_line(value: object) -> str:
    """Control characters (including U+2028) become spaces, and all whitespace is collapsed."""
    return _SPACES.sub(" ", _CONTROL.sub(" ", str(value if value is not None else ""))).strip()


def md_safe(value: object, limit: int) -> str:
    """Make untrusted text safe to write into a committed Markdown file.

    Args:
        value: any value from a result line, a trace or a manifest.
        limit: the most characters the result may have.

    Returns:
        One line of text: control characters removed, backticks changed to `'`, the characters
        `|`, `<` and `[` escaped, local paths, URL queries and secret-like strings redacted,
        and the whole cut to `limit` characters.

    WHY: results and traces hold web text and model output. A task id, tool name or run id with
    `\\n# heading`, a `|`, `<!-- ... -->` or `![](url)` must not break a table, hide text or add
    a link to the docs, and a forged end marker must not make `write_block` refuse forever.
    The escaping comes before the redaction on purpose: the `<project>` label the redaction
    adds is ours and must stay readable.
    """
    text = one_line(value).replace("`", "'")
    text = text.replace("|", "\\|").replace("<", "&lt;").replace("[", "\\[")
    text = _redact(text)
    for pattern, replacement in _EXTRA_REDACTIONS:
        text = pattern.sub(replacement, text)
    return trim(text, limit)


def checked_output_path(path: Path) -> Path:
    """Return the path to write to, or refuse it.

    Raises:
        ReportError: if the file is not a `.md` file inside `OUTPUT_ROOT` once symlinks are
            resolved.

    WHY: a wrong or injected `--out CLAUDE.md` or `--write .env` would silently overwrite a
    project file (review finding 3).
    """
    resolved = path.resolve()
    root = OUTPUT_ROOT.resolve()
    if resolved.suffix != ".md" or not resolved.is_relative_to(root):
        raise ReportError(f"output must be a .md file inside {root.name}/: {path.name}")
    return resolved


def read_text_exact(path: Path) -> str:
    """Read a UTF-8 file without changing its line endings.

    Raises:
        ReportError: if the file cannot be read or is not valid UTF-8.

    WHY: `read_text` turns `\\r\\n` into `\\n`, so a later write would change the text outside the
    markers, which must stay byte for byte (AC10).
    """
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeError):
        raise ReportError(f"cannot read {path.name}") from None


def write_text_atomic(path: Path, text: str) -> None:
    """Write a UTF-8 file by writing a temporary file and renaming it.

    Raises:
        ReportError: if the folder or file cannot be written.

    WHY: `write_text` truncates the file first, so a crash would lose the hand-written parts of
    `docs/results.md`.
    """
    temporary = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(text.encode("utf-8"))
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise ReportError(f"cannot write {path.name}") from None
