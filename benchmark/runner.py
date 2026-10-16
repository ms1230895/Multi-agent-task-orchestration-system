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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from benchmark.tasks import BenchmarkTask
from src.config import MAX_PLAN_STEPS, PROJECT_ROOT, Settings

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
