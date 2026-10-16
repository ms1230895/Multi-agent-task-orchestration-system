"""Benchmark runner: runs the tasks of a pass through `run_task`, checks them, saves results.

Architecture layer: 7 (Benchmark). A pass is one model and one config running the tasks. It has
a folder in `benchmark/results/<pass_id>/` and can be paused (usage cap, connection error) and
resumed later. See `docs/specs/benchmark.md` for the full design.

Terminal use: `python -m benchmark.runner --pass <pass_id>`.
"""

import shutil
from pathlib import Path

from benchmark.tasks import BenchmarkTask
from src.config import PROJECT_ROOT

RESULTS_DIR = PROJECT_ROOT / "benchmark" / "results"

# NOTE: the one file the project keeps inside `workspace/`, so the empty folder stays in git.
KEEP_FILE = ".gitkeep"


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
