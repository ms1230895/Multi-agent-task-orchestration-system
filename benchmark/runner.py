"""Benchmark runner: runs the tasks of a pass through `run_task`, checks them, saves results.

Architecture layer: 7 (Benchmark). A pass is one model and one config running the tasks. It has
a folder in `benchmark/results/<pass_id>/` and can be paused (usage cap, connection error) and
resumed later. See `docs/specs/benchmark.md` for the full design.

Terminal use: `python -m benchmark.runner --pass <pass_id>`.
"""

import hashlib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from rich.markup import escape

from benchmark.checkers import CheckResult, run_checks
from benchmark.tasks import TASKS_PATH, BenchmarkTask, load_tasks
from src.config import MAX_PLAN_STEPS, PROJECT_ROOT, WORKSPACE_DIR, Settings, get_settings
from src.console import ERROR, RETRY, SUCCESS, console, print_record, sanitize
from src.logger import trim
from src.run import run_task
from src.state import AgentState

RESULTS_DIR = PROJECT_ROOT / "benchmark" / "results"

# NOTE: the one file the project keeps inside `workspace/`, so the empty folder stays in git.
KEEP_FILE = ".gitkeep"

MANIFEST_NAME = "manifest.json"
RESULTS_NAME = "results.jsonl"
PAUSED_NAME = "paused.jsonl"
MANIFEST_SCHEMA_VERSION = 1

# WHY: the pass id becomes a folder name, so only lower-case letters, digits and hyphens are
# allowed, and it cannot start with a hyphen. This blocks `..`, `/` and other surprises.
PASS_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

GIT_TIMEOUT_S = 10

# WHY: the result line keeps the answer short; the full text is in the trace (`run_end`).
MAX_RESULT_ANSWER_CHARS = 500
MAX_TOOL_NAME_CHARS = 100

PassOutcome = Literal["complete", "paused"]
# NOTE: same call shape as `src.run.run_task`; tests pass a fake with scripted states.
RunFn = Callable[..., AgentState]

# WHY: these errors mean "Ollama or the network could not serve the call", never "the agent
# did badly". The agents cannot cause them, so the task must not be recorded as a failure
# (architecture 5, D12, D24). The Ollama client turns `httpx.ConnectError` into
# `ConnectionError` and raises `ResponseError` for every server answer that is an error
# (usage cap, rate limit, not signed in, server error); httpx timeouts pass through.
# RISK: a guess from reading the client code (like D6). The first real usage cap will show
# the real text in `paused.jsonl`; adjust this list then.
INFRA_ERROR_TYPES = frozenset(
    {
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "ConnectionAbortedError",
        "TimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "ReadError",
        "WriteError",
        "RemoteProtocolError",
        "NetworkError",
        "ResponseError",
    }
)


class PassRefused(Exception):
    """Raised when a pass must not start or resume.

    The message says why, and names differing fields without printing their values.
    """


def reset_workspace(workspace: Path) -> None:
    """Empty the workspace folder, keeping only `.gitkeep`. Create the folder if missing.

    Args:
        workspace: the sandbox folder (normally `workspace/`).

    WARNING: this deletes everything in the folder, including files put there by hand.

    WHY: a file left by one task (for example `total.txt`) must never make another task pass
    or fail (architecture 2.7). A symlink is removed as a link and its target is never
    touched, so a link the agent could not create (Hard rule 4) still cannot make us delete
    something outside the workspace. A failure raises on purpose: carrying on with a dirty
    workspace would silently mix tasks.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    for entry in workspace.iterdir():
        if entry.name == KEEP_FILE:
            continue
        # WHY: check `is_symlink` first, because a link to a folder is also `is_dir()`, and
        # `rmtree` on it would raise instead of removing just the link.
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        else:
            shutil.rmtree(entry)


def prepare_workspace(task: BenchmarkTask, workspace: Path) -> None:
    """Reset the workspace and write the task's input files into it.

    Args:
        task: the task about to run.
        workspace: the sandbox folder.

    Raises:
        ValueError: if a setup path would land outside the workspace. `load_tasks` already
            refuses such paths; this is a second guard before anything is written.

    WHY: file tasks must find their input files ready (first-run problem P4: a missing file
    made the executor guess paths and fail 42 tool calls).
    """
    reset_workspace(workspace)
    root = workspace.resolve()
    for relative, content in task.setup_files.items():
        target = (root / relative).resolve()
        # WHY: the workspace was just emptied, so no symlink can redirect this; the check
        # still guards against a bad path that slipped past the task-file validation.
        if not target.is_relative_to(root):
            raise ValueError(f"setup file path leaves the workspace: {relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _check_pass_id(pass_id: str) -> None:
    """Refuse a pass id that is not a safe folder name."""
    if not isinstance(pass_id, str) or not PASS_ID_PATTERN.fullmatch(pass_id):
        raise PassRefused(
            "invalid pass id: use 1 to 40 lower-case letters, digits or hyphens, "
            "starting with a letter or digit"
        )


def _timestamp() -> str:
    """Return the current UTC time as ISO 8601 with milliseconds and a `Z`."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def code_fingerprint(root: Path = PROJECT_ROOT) -> str:
    """Hash the code and the task file that decide a pass's results.

    Args:
        root: the project folder.

    Returns:
        `"sha256:<hex>"` over the sorted relative paths and contents of `src/**/*.py`,
        `benchmark/*.py` and `benchmark/tasks.json`.

    WHY: session 7 fixes code between passes, and results from before and after a fix must
    never be mixed (roadmap 7). The git commit alone is not enough: a docs-only commit would
    block a resume, and uncommitted code changes would not be seen.
    """
    files: set[Path] = set()
    src_dir = root / "src"
    if src_dir.is_dir():
        files.update(src_dir.rglob("*.py"))
    bench_dir = root / "benchmark"
    if bench_dir.is_dir():
        files.update(bench_dir.glob("*.py"))
        tasks_file = bench_dir / "tasks.json"
        if tasks_file.is_file():
            files.add(tasks_file)

    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        # WHY: the NUL separators stop two different file sets from hashing the same.
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return f"sha256:{digest.hexdigest()}"


def _git_info(root: Path) -> tuple[str, bool]:
    """Return `(commit, dirty)` for the manifest. `("unknown", False)` if git is unusable.

    `dirty` is true when code under `src/` or `benchmark/` (not `benchmark/results/`) has
    uncommitted changes. It is information only; the fingerprint is what blocks a resume.
    """
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
        status = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--",
                "src",
                "benchmark",
                ":(exclude)benchmark/results",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", False
    if head.returncode != 0 or status.returncode != 0:
        return "unknown", False
    return head.stdout.strip() or "unknown", bool(status.stdout.strip())


def _build_manifest(
    pass_id: str,
    settings: Settings,
    task_ids: list[str],
    fingerprint: str,
    git_commit: str,
    git_dirty: bool,
) -> dict[str, Any]:
    """Build the manifest written once when a pass starts (spec section 4).

    WHY: `config` has the same shape as `run_end.config` (settings plus `max_plan_steps`),
    which a session 5 test fixes so no secret can enter it (Hard rule 7).
    """
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "pass_id": pass_id,
        "model": settings.ollama_model,
        "config": {**settings.model_dump(), "max_plan_steps": MAX_PLAN_STEPS},
        "code_fingerprint": fingerprint,
        "task_ids": task_ids,
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "started_at": _timestamp(),
    }


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write the manifest, creating the pass folder. Fails if the manifest already exists.

    WHY: a manifest is written once. Mode `x` means a second runner can never overwrite the
    record of what the pass was started with.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, indent=2) + "\n")


def _read_manifest(path: Path) -> dict[str, Any]:
    """Read a pass's manifest, or refuse if it is missing or broken."""
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise PassRefused(f"{MANIFEST_NAME} is missing or not valid JSON") from None
    required = ("model", "config", "code_fingerprint", "task_ids")
    if not isinstance(manifest, dict) or any(key not in manifest for key in required):
        raise PassRefused(f"{MANIFEST_NAME} is missing required fields")
    return manifest


def _compare_manifest(manifest: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Return the names of the fields that differ between a manifest and the current run.

    Args:
        manifest: the manifest on disk.
        current: a dict with `model`, `config`, `code_fingerprint` and `task_ids`.

    Returns:
        Field names such as `model`, `config.max_replans`, `code_fingerprint`. Values are
        never included (Hard rule 7: nothing secret may reach a message).

    WHY: this is how one pass never mixes models, settings, tasks or code (architecture 5).
    """
    differing: list[str] = []
    if manifest["model"] != current["model"]:
        differing.append("model")
    old_config, new_config = manifest["config"], current["config"]
    if not isinstance(old_config, dict):
        differing.append("config")
    else:
        for key in sorted(set(old_config) | set(new_config)):
            if old_config.get(key, object()) != new_config.get(key, object()):
                differing.append(f"config.{key}")
    if manifest["code_fingerprint"] != current["code_fingerprint"]:
        differing.append("code_fingerprint")
    if manifest["task_ids"] != current["task_ids"]:
        differing.append("task_ids")
    return differing


def _read_results(path: Path) -> list[dict[str, Any]]:
    """Read `results.jsonl`: one dict per line. A missing file means no results yet.

    Raises:
        PassRefused: if a line is not a JSON object (for example a half-written last line).

    WHY: silently dropping a broken line would re-run a paid task, and keeping it would hide
    a gap. Gaurav deletes the line by hand and resumes.
    """
    if not path.exists():
        return []
    results: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if not isinstance(record, dict) or not isinstance(record.get("task_id"), str):
            raise PassRefused(
                f"{RESULTS_NAME} line {number} is broken; delete that line by hand and resume"
            )
        results.append(record)
    return results


def _select_tasks(tasks: list[BenchmarkTask], task_ids: list[str] | None) -> list[BenchmarkTask]:
    """Pick the tasks of a pass, in file order. `None` means all of them.

    Raises:
        PassRefused: if an id is not in the task file.
    """
    if task_ids is None:
        return list(tasks)
    known = {task.id for task in tasks}
    for task_id in task_ids:
        if task_id not in known:
            raise PassRefused(f"unknown task id: {task_id[:40]}")
    wanted = set(task_ids)
    return [task for task in tasks if task.id in wanted]


def is_infra_error(error: str) -> bool:
    """Tell whether a state's `error` text is an infrastructure error (see `INFRA_ERROR_TYPES`).

    Args:
        error: `"<ExceptionType>: <message>"`, or `""` for a run that did not crash.

    Returns:
        True when the exception type before the first `:` is an infrastructure type. Anything
        else (`""`, `GraphRecursionError`, `ValueError`, a bug) is not.
    """
    return error.split(":", 1)[0].strip() in INFRA_ERROR_TYPES


class _RunCollector:
    """An `on_record` callback that keeps what the runner needs from one run's events.

    WHY: `run_task` returns the final state, but the model, the counters and the duration
    only exist in the `run_end` record, and the tool names only in `tool_call` records.
    """

    def __init__(self, verbose: bool) -> None:
        self.run_end: dict[str, Any] | None = None
        self.tools: list[str] = []
        self._verbose = verbose

    def __call__(self, record: dict[str, Any]) -> None:
        """Take one trace record. With `--verbose` also show it live."""
        event = record.get("event")
        data = record.get("data")
        if event == "run_end":
            self.run_end = record
        elif event == "tool_call" and isinstance(data, dict):
            name = data.get("tool")
            if isinstance(name, str):
                name = trim(name, MAX_TOOL_NAME_CHARS)
                if name not in self.tools:
                    self.tools.append(name)
        if self._verbose:
            print_record(record)


def _append_line(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line and close the file.

    WHY: one open-write-close per line means a crash or Ctrl-C keeps every finished task,
    and resume continues from there. `ensure_ascii` keeps U+2028 in web text from splitting
    a line (same rule as the traces, D31).
    """
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, default=str) + "\n")


def _result_line(
    task: BenchmarkTask,
    state: AgentState,
    run_end: dict[str, Any],
    tools_used: list[str],
    checks: list[CheckResult],
) -> dict[str, Any]:
    """Build the `results.jsonl` line of one finished task (spec section 4).

    WHY: success needs the run to be `done` AND every check to pass. A run the agent gave up
    on carries "Task failed: ..." plus a partial result; counting it would make the agent
    look better than it is. `checks_passed` is stored too, so that case stays visible.
    """
    checks_passed = all(check.passed for check in checks)
    data = run_end.get("data", {})
    return {
        "task_id": task.id,
        "category": task.category,
        "run_id": state["run_id"],
        "model": run_end["model"],
        "success": state["status"] == "done" and checks_passed,
        "status": state["status"],
        "checks_passed": checks_passed,
        "checks": [check.model_dump() for check in checks],
        "error": state["error"],
        "final_answer": trim(state["final_answer"], MAX_RESULT_ANSWER_CHARS),
        "tools_used": tools_used,
        "counters": dict(data.get("counters", {})),
        "duration_ms": data.get("duration_ms", 0),
        "finished_at": _timestamp(),
    }


def _say(text: str, colour: str | None = None) -> None:
    """Print one line, safe against markup and control characters in LLM or web text."""
    console.print(escape(sanitize(text)), style=colour, highlight=False, emoji=False)


def _progress_text(position: int, total: int, line: dict[str, Any]) -> str:
    """Write the one-line progress message, with the reason when a task fails."""
    seconds = line["duration_ms"] / 1000
    head = f"[{position}/{total}] {line['task_id']} {'PASS' if line['success'] else 'FAIL'}"
    if line["success"]:
        return f"{head} {seconds:.1f} s"
    reasons: list[str] = []
    if line["status"] != "done":
        reasons.append(f"run {line['status']}" + (f" ({line['error']})" if line["error"] else ""))
    failed = [check for check in line["checks"] if not check["passed"]]
    if failed:
        reasons.append(f"{failed[0]['type']}: {failed[0]['detail']}")
    return f"{head} {seconds:.1f} s - " + "; ".join(reasons)


def run_pass(
    pass_id: str,
    *,
    task_ids: list[str] | None = None,
    settings: Settings | None = None,
    run_fn: RunFn = run_task,
    tasks_path: Path = TASKS_PATH,
    results_root: Path = RESULTS_DIR,
    workspace: Path = WORKSPACE_DIR,
    root: Path = PROJECT_ROOT,
    verbose: bool = False,
) -> PassOutcome:
    """Run (or resume) a pass: every task through `run_fn`, checked, saved one line at a time.

    Args:
        pass_id: the pass name, also its folder name under `results_root`.
        task_ids: run only these tasks (new pass), or assert the pass has exactly these
            (resume). Default: all tasks (new pass), the manifest's tasks (resume).
        settings: limits, flags and model. Default: `get_settings()`.
        run_fn: the function that runs a task. Default: `run_task`; tests pass a fake.
        tasks_path: the task file.
        results_root: the folder that holds the pass folders.
        workspace: the sandbox folder that is emptied before every task.
        root: the project folder, for the code fingerprint and git info.
        verbose: show every trace event live.

    Returns:
        `"complete"` when every task has a result, `"paused"` after an infrastructure error.

    Raises:
        PassRefused: bad pass id or task id, a pass whose model, settings, tasks or code differ
            from its manifest, a broken `results.jsonl`, or a run on another model.
        TasksFileError: the task file is invalid.
        ConfigError: the settings are missing or invalid.
    """
    # STEP 1: check everything that can be refused BEFORE a folder is created, so a refusal
    # leaves no trace on disk.
    _check_pass_id(pass_id)
    if settings is None:
        settings = get_settings()
    tasks = load_tasks(tasks_path)
    requested = _select_tasks(tasks, task_ids)
    fingerprint = code_fingerprint(root)
    config = {**settings.model_dump(), "max_plan_steps": MAX_PLAN_STEPS}

    pass_dir = results_root / pass_id
    manifest_path = pass_dir / MANIFEST_NAME
    results_path = pass_dir / RESULTS_NAME
    is_new = not manifest_path.exists() and (not pass_dir.exists() or not any(pass_dir.iterdir()))

    # STEP 2: a new pass writes its manifest; a resumed pass must match it exactly.
    if is_new:
        planned = requested
        commit, dirty = _git_info(root)
        manifest = _build_manifest(
            pass_id, settings, [task.id for task in planned], fingerprint, commit, dirty
        )
        _write_manifest(manifest_path, manifest)
        results_path.touch()
        if dirty:
            _say("warning: code in src/ or benchmark/ has uncommitted changes (git_dirty)", RETRY)
    else:
        manifest = _read_manifest(manifest_path)
        wanted_ids = [task.id for task in requested] if task_ids is not None else None
        current = {
            "model": settings.ollama_model,
            "config": config,
            "code_fingerprint": fingerprint,
            "task_ids": wanted_ids if wanted_ids is not None else manifest["task_ids"],
        }
        differing = _compare_manifest(manifest, current)
        if differing:
            raise PassRefused(
                f"cannot resume pass '{pass_id}': these differ from its manifest: "
                + ", ".join(differing)
                + ". Start a new pass id instead."
            )
        planned = _select_tasks(tasks, manifest["task_ids"])

    # STEP 3: skip the tasks that already have a result.
    results = _read_results(results_path)
    done_ids = {record["task_id"] for record in results}
    remaining = [task for task in planned if task.id not in done_ids]
    finished = len(planned) - len(remaining)
    _say(
        f"Pass {pass_id}: model {manifest['model']}, {finished}/{len(planned)} tasks already done."
    )
    # WARNING: the workspace is emptied before every task; files put there by hand are lost.
    _say(f"warning: {workspace} is emptied before every task", RETRY)

    # STEP 4: one task at a time.
    for task in remaining:
        prepare_workspace(task, workspace)
        collector = _RunCollector(verbose)
        state = run_fn(task.task, settings=settings, on_record=collector)

        # WHY: a usage cap or connection error says nothing about the agent. Write no result,
        # so the task runs again on resume (architecture 5).
        if is_infra_error(state["error"]):
            _append_line(
                pass_dir / PAUSED_NAME,
                {
                    "task_id": task.id,
                    "run_id": state["run_id"],
                    "error": state["error"],
                    "at": _timestamp(),
                },
            )
            _say(
                f"paused at {task.id}: {state['error']}. "
                f"Resume later with: python -m benchmark.runner --pass {pass_id}",
                RETRY,
            )
            return "paused"

        # WHY: `run_task` always writes a `run_end`; without one the model and counters are
        # unknown, and a result without them would be unreliable evidence.
        run_end = collector.run_end
        if run_end is None:
            raise PassRefused(f"the run for {task.id} produced no run_end event; pass stopped")
        # WHY: this should never happen. If it does, the pass would mix models.
        if run_end.get("model") != manifest["model"]:
            raise PassRefused(
                f"the run for {task.id} used a different model than the manifest; pass stopped"
            )

        checks = run_checks(task, state["final_answer"], workspace)
        line = _result_line(task, state, run_end, collector.tools, checks)
        _append_line(results_path, line)
        finished += 1
        _say(_progress_text(finished, len(planned), line), SUCCESS if line["success"] else ERROR)

    return "complete"
