"""Tests for `benchmark/runner.py`: workspace, passes, resume, pause, summary, CLI.

Covers AC15 to AC34 and AC39.

The runner gets a fake `run_fn` that returns a scripted `AgentState` and sends scripted
`tool_call` and `run_end` records to `on_record`. Workspaces, result folders and the project
used for the code fingerprint all live in pytest's `tmp_path`. No LLM, no network, and nothing
is written to the real `workspace/`, `logs/` or `benchmark/results/` (Hard rule 8).
"""

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

import benchmark.runner as runner
from benchmark.checkers import CheckResult
from benchmark.runner import (
    INFRA_ERROR_TYPES,
    PassRefused,
    _compare_manifest,
    _read_results,
    _RunCollector,
    _select_tasks,
    code_fingerprint,
    is_infra_error,
    main,
    prepare_workspace,
    reset_workspace,
    run_pass,
    summarize,
)
from benchmark.tasks import BenchmarkTask, TasksFileError, load_tasks
from src.config import MAX_PLAN_STEPS, PROJECT_ROOT, ConfigError, Settings, load_settings
from src.state import AgentState, initial_state

MODEL = "dummy-model"
SETTINGS = load_settings({"OLLAMA_MODEL": MODEL})

MANIFEST_KEYS = {
    "schema_version",
    "pass_id",
    "model",
    "config",
    "code_fingerprint",
    "task_ids",
    "git_commit",
    "git_dirty",
    "started_at",
}
RESULT_KEYS = {
    "task_id",
    "category",
    "run_id",
    "model",
    "success",
    "status",
    "checks_passed",
    "checks",
    "error",
    "final_answer",
    "tools_used",
    "counters",
    "duration_ms",
    "finished_at",
}
DEFAULT_COUNTERS = {"plan_steps": 1, "tool_calls": 1, "failed_tool_calls": 0}
USAGE_CAP = "ResponseError: you have reached your weekly usage limit"

# NOTE: a small 3-task file keeps every runner test fast. Task texts hold no digits, so the
# `number` check cannot be given away by the task text.
SMALL_TASKS: list[dict[str, Any]] = [
    {
        "id": "calc-01",
        "category": "calc",
        "task": "Compute the sum of seven and eight.",
        "checks": [{"type": "number", "value": 15}],
    },
    {
        "id": "file-01",
        "category": "file",
        "task": "Read a.txt and report its content.",
        "setup_files": {"a.txt": "alpha"},
        "checks": [{"type": "contains", "all": ["alpha"]}],
    },
    {
        "id": "file-02",
        "category": "file",
        "task": "Read b.txt and report its content.",
        "setup_files": {"b.txt": "beta"},
        "checks": [{"type": "contains", "all": ["beta"]}],
    },
]
GOOD_ANSWERS = {"calc-01": "The sum is 15.", "file-01": "alpha", "file-02": "beta"}
IDS = ["calc-01", "file-01", "file-02"]

_REAL_GIT_INFO = runner._git_info


class FakeRun:
    """A scripted `run_fn`: looks the task up by its text and returns a scripted state.

    `script` maps a task id to overrides: `status`, `answer`, `error`, `model`, `tools`,
    `counters`, `run_end` (False = send none) and `write` (files put into the workspace).
    """

    def __init__(
        self, tasks_path: Path, workspace: Path, script: dict[str, dict[str, Any]] | None = None
    ) -> None:
        self._by_text = {task.task: task for task in load_tasks(tasks_path)}
        self._workspace = workspace
        self.script = script or {}
        self.calls: list[str] = []
        self.listings: dict[str, list[str]] = {}

    def __call__(self, task_text: str, *, settings: Settings, on_record: Any = None) -> AgentState:
        """Behave like `run_task`: send records, return the final state."""
        task = self._by_text[task_text]
        plan = self.script.get(task.id, {})
        self.calls.append(task.id)
        # WHY: proves what the workspace held when the task started (AC20).
        self.listings[task.id] = sorted(p.name for p in self._workspace.iterdir())
        for name, content in plan.get("write", {}).items():
            (self._workspace / name).write_text(content, encoding="utf-8")
        state = initial_state(task_text, f"run-{task.id}")
        state["status"] = plan.get("status", "done")
        state["final_answer"] = plan.get("answer", GOOD_ANSWERS[task.id])
        state["error"] = plan.get("error", "")
        model = plan.get("model", settings.ollama_model)
        for tool in plan.get("tools", ["calculator"]):
            on_record({"event": "tool_call", "model": model, "data": {"tool": tool}})
        if plan.get("run_end", True):
            counters = plan.get("counters", dict(DEFAULT_COUNTERS))
            on_record(
                {
                    "event": "run_end",
                    "model": model,
                    "data": {"counters": counters, "duration_ms": 1500},
                }
            )
        return state


@dataclass
class Env:
    """The folders one runner test works in, all under `tmp_path`."""

    tasks_path: Path
    results_root: Path
    workspace: Path
    root: Path

    def fake(self, script: dict[str, dict[str, Any]] | None = None) -> FakeRun:
        """Build a fake run function for this environment."""
        return FakeRun(self.tasks_path, self.workspace, script)

    def run(
        self,
        pass_id: str = "p1",
        *,
        fake: FakeRun | None = None,
        settings: Settings = SETTINGS,
        task_ids: list[str] | None = None,
        verbose: bool = False,
    ) -> str:
        """Call `run_pass` with this environment's folders."""
        return run_pass(
            pass_id,
            task_ids=task_ids,
            settings=settings,
            run_fn=fake or self.fake(),
            tasks_path=self.tasks_path,
            results_root=self.results_root,
            workspace=self.workspace,
            root=self.root,
            verbose=verbose,
        )

    def pass_dir(self, pass_id: str = "p1") -> Path:
        """Return the folder of a pass."""
        return self.results_root / pass_id

    def results(self, pass_id: str = "p1") -> list[dict[str, Any]]:
        """Read the result lines of a pass."""
        return _jsonl(self.pass_dir(pass_id) / "results.jsonl")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSON-lines file; a missing file means no lines."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line]


def _snapshot(folder: Path) -> dict[str, bytes | None]:
    """Map every path under a folder to its bytes (None for folders), to prove no change."""
    return {
        str(p.relative_to(folder)): (p.read_bytes() if p.is_file() else None)
        for p in sorted(folder.rglob("*"))
    }


def _make_project(root: Path) -> None:
    """Create a tiny project for the code fingerprint."""
    for folder in ("src", "benchmark", "docs"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "benchmark" / "b.py").write_text("B = 2\n", encoding="utf-8")
    (root / "benchmark" / "tasks.json").write_text("[]\n", encoding="utf-8")
    (root / "docs" / "x.md").write_text("docs\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _fixed_git_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the git call in the runner so manifests do not depend on the machine."""
    monkeypatch.setattr(runner, "_git_info", lambda root: ("abc123", False))


@pytest.fixture
def env(tmp_path: Path) -> Env:
    """Build the folders, the 3-task file and the tiny project for one test."""
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(json.dumps(SMALL_TASKS), encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".gitkeep").write_text("", encoding="utf-8")
    root = tmp_path / "project"
    _make_project(root)
    return Env(tasks_path, tmp_path / "results", workspace, root)


# --- AC15: reset_workspace ---------------------------------------------------------------


def test_reset_workspace_removes_files_and_folders_but_keeps_gitkeep(tmp_path: Path) -> None:
    """AC15: files and nested folders go, `.gitkeep` stays."""
    # WHY: the empty folder must stay in git, but no task may leave anything behind.
    ws = tmp_path / "ws"
    (ws / "deep" / "er").mkdir(parents=True)
    (ws / "deep" / "er" / "f.txt").write_text("x", encoding="utf-8")
    (ws / "a.txt").write_text("x", encoding="utf-8")
    (ws / ".gitkeep").write_text("", encoding="utf-8")
    reset_workspace(ws)
    assert [p.name for p in ws.iterdir()] == [".gitkeep"]


def test_reset_workspace_missing_folder_is_created(tmp_path: Path) -> None:
    """AC15: a missing workspace folder is created, with its parents."""
    # WHY: a fresh checkout may have no workspace yet.
    ws = tmp_path / "a" / "b" / "ws"
    reset_workspace(ws)
    assert ws.is_dir()
    assert list(ws.iterdir()) == []


# --- AC16: symlinks ----------------------------------------------------------------------


def test_reset_workspace_symlinks_removed_and_targets_untouched(tmp_path: Path) -> None:
    """AC16: links to a file and a folder outside are removed, their targets stay."""
    # WHY: Hard rule 4, the reset must never delete anything outside the workspace.
    outside = tmp_path / "outside"
    (outside / "folder").mkdir(parents=True)
    (outside / "folder" / "keep.txt").write_text("keep", encoding="utf-8")
    (outside / "file.txt").write_text("keep", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    os.symlink(outside / "file.txt", ws / "filelink")
    os.symlink(outside / "folder", ws / "folderlink", target_is_directory=True)
    reset_workspace(ws)
    assert list(ws.iterdir()) == []
    assert (outside / "file.txt").read_text(encoding="utf-8") == "keep"
    assert (outside / "folder" / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_reset_workspace_broken_symlink_is_removed(tmp_path: Path) -> None:
    """AC16 extra: a link to nothing is removed too."""
    # WHY: `is_file()` is False for a broken link, it must still not reach `rmtree`.
    ws = tmp_path / "ws"
    ws.mkdir()
    os.symlink(tmp_path / "missing", ws / "dead")
    reset_workspace(ws)
    assert list(ws.iterdir()) == []


# --- AC17: prepare_workspace -------------------------------------------------------------


def _task_with_files(files: dict[str, str]) -> BenchmarkTask:
    """Build a task that has the given setup files."""
    return BenchmarkTask.model_validate(
        {
            "id": "file-01",
            "category": "file",
            "task": "Do it.",
            "setup_files": files,
            "checks": [{"type": "contains", "all": ["x"]}],
        }
    )


def test_prepare_workspace_leaves_exactly_the_setup_files_and_gitkeep(tmp_path: Path) -> None:
    """AC17: only setup files (with parents) and `.gitkeep`, with exact contents."""
    # WHY: a stale file from the previous task must never be visible to the next one.
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".gitkeep").write_text("", encoding="utf-8")
    (ws / "stale.txt").write_text("old", encoding="utf-8")
    files = {"a.txt": "line1\nline2\n", "reports/today.txt": "nested ok", "u.txt": "café ✓"}
    prepare_workspace(_task_with_files(files), ws)
    found = sorted(p.relative_to(ws).as_posix() for p in ws.rglob("*") if p.is_file())
    assert found == sorted([".gitkeep", *files])
    for name, content in files.items():
        assert (ws / name).read_text(encoding="utf-8") == content


def test_prepare_workspace_without_setup_files_leaves_empty_workspace(tmp_path: Path) -> None:
    """AC17: a task without setup files gets an empty workspace."""
    # WHY: file-01 must start with nothing in the folder.
    ws = tmp_path / "ws"
    (ws).mkdir()
    (ws / "old.txt").write_text("old", encoding="utf-8")
    prepare_workspace(_task_with_files({}), ws)
    assert list(ws.iterdir()) == []


def test_prepare_workspace_path_leaving_workspace_raises_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """AC17: the second guard refuses a setup path that escapes (task built unvalidated)."""
    # WHY: load_tasks already refuses these paths, this guard covers a bug in that layer.
    ws = tmp_path / "ws"
    ws.mkdir()
    task = BenchmarkTask.model_construct(
        id="file-01", category="file", task="x", setup_files={"../escape.txt": "bad"}, checks=[]
    )
    with pytest.raises(ValueError, match="leaves the workspace"):
        prepare_workspace(task, ws)
    assert not (tmp_path / "escape.txt").exists()


# --- AC18: a new pass --------------------------------------------------------------------


def test_new_pass_writes_manifest_and_one_result_line_per_task(env: Env) -> None:
    """AC18: manifest keys, model, config, task ids; 3 result lines; outcome complete."""
    # WHY: the manifest is the record of what a pass was started with.
    assert env.run() == "complete"
    manifest = json.loads((env.pass_dir() / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == MANIFEST_KEYS
    assert manifest["schema_version"] == 1
    assert manifest["pass_id"] == "p1"
    assert manifest["model"] == SETTINGS.ollama_model
    # WHY: same shape as `run_end.config`: settings plus max_plan_steps, nothing secret.
    assert manifest["config"] == {**SETTINGS.model_dump(), "max_plan_steps": MAX_PLAN_STEPS}
    assert manifest["task_ids"] == IDS
    assert manifest["git_commit"] == "abc123"
    assert manifest["git_dirty"] is False
    assert manifest["code_fingerprint"] == code_fingerprint(env.root)
    results = env.results()
    assert [r["task_id"] for r in results] == IDS
    assert all(set(r) == RESULT_KEYS for r in results)


def test_new_pass_result_line_fields_hold_the_run_data(env: Env) -> None:
    """AC18: a result line carries the run id, model, status, checks and duration."""
    # WHY: this line is the evidence behind the CV numbers.
    env.run()
    first = env.results()[0]
    assert first["run_id"] == "run-calc-01"
    assert first["model"] == MODEL
    assert first["category"] == "calc"
    assert first["status"] == "done"
    assert first["error"] == ""
    assert first["duration_ms"] == 1500
    assert first["success"] is True
    assert first["checks_passed"] is True
    assert set(first["checks"][0]) == {"type", "source", "passed", "detail"}
    assert first["finished_at"].endswith("Z")


def test_new_pass_with_task_ids_runs_only_those_in_file_order(env: Env) -> None:
    """AC18: `--tasks` picks tasks, stored in file order whatever the order given."""
    # WHY: the manifest's task list must not depend on how the user typed it.
    fake = env.fake()
    assert env.run(fake=fake, task_ids=["file-02", "calc-01"]) == "complete"
    manifest = json.loads((env.pass_dir() / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["task_ids"] == ["calc-01", "file-02"]
    assert fake.calls == ["calc-01", "file-02"]


def test_new_pass_dirty_git_is_recorded_and_warned(
    env: Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC18/spec 7: uncommitted code sets `git_dirty` and prints a warning, but runs."""
    # WHY: allowed, but the reader of the manifest must be able to see it.
    monkeypatch.setattr(runner, "_git_info", lambda root: ("def456", True))
    assert env.run() == "complete"
    manifest = json.loads((env.pass_dir() / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["git_dirty"] is True
    assert manifest["git_commit"] == "def456"
    assert "git_dirty" in capsys.readouterr().out


def test_new_pass_progress_line_shows_position_id_verdict_and_seconds(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec 5.5: one line per task, `[1/3] calc-01 PASS 1.5 s`."""
    # WHY: Gaurav watches a long pass through these lines.
    env.run(fake=env.fake({"file-01": {"answer": "wrong"}}))
    out = capsys.readouterr().out
    assert "[1/3] calc-01 PASS 1.5 s" in out
    assert "[2/3] file-01 FAIL" in out
    assert "Summary of pass p1" in out


def test_result_lines_are_ascii_so_unicode_line_separators_cannot_split_them(env: Env) -> None:
    """Spec 5.5 / D31: web text with U+2028 must not break the one-line-per-task format."""
    # WHY: a split line would look like a half-written line and block the resume. The separator
    # is written as an escape: a literal U+2028 is invisible and editors offer to strip it.
    answer = "15 García" + "\u2028" + "tail"
    env.run(fake=env.fake({"calc-01": {"answer": answer}}))
    raw = (env.pass_dir() / "results.jsonl").read_text(encoding="utf-8")
    assert "\u2028" not in raw
    assert len([line for line in raw.split("\n") if line]) == 3
    assert env.results()[0]["final_answer"] == answer


# --- AC19: success rule ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "answer", "success", "checks_passed"),
    [
        ("done", "The sum is 15.", True, True),
        ("failed", "Task failed: partial. 15", False, True),
        ("done", "The sum is 16.", False, False),
        ("failed", "no idea", False, False),
    ],
)
def test_success_needs_done_status_and_every_check_passing(
    env: Env, status: str, answer: str, success: bool, checks_passed: bool
) -> None:
    """AC19: success only when status is done and all checks pass."""
    # WHY: a run the agent gave up on must not count even if its text holds the answer.
    env.run(fake=env.fake({"calc-01": {"status": status, "answer": answer}}), task_ids=["calc-01"])
    line = env.results()[0]
    assert line["success"] is success
    assert line["checks_passed"] is checks_passed
    assert line["status"] == status


def test_success_needs_all_checks_when_a_task_has_two() -> None:
    """AC19: with two checks, one failing check fails the task (via `_result_line`)."""
    # WHY: the task passes only if every check passes.
    task = _task_with_files({})
    state = initial_state("Do it.", "r1")
    state["status"] = "done"
    checks = [
        CheckResult(type="a", source="answer", passed=True, detail=""),
        CheckResult(type="b", source="answer", passed=False, detail="x"),
    ]
    line = runner._result_line(task, state, {"model": MODEL, "data": {}}, [], checks)
    assert line["success"] is False
    assert line["checks_passed"] is False


# --- AC20: workspace is prepared before each task ----------------------------------------


def test_workspace_is_prepared_before_each_task(env: Env) -> None:
    """AC20: each task sees its own setup files and nothing from the previous task."""
    # WHY: a file left by one task must never make another task pass or fail.
    (env.workspace / "stale.txt").write_text("old", encoding="utf-8")
    fake = env.fake({"calc-01": {"write": {"leftover.txt": "from calc-01"}}})
    env.run(fake=fake)
    assert fake.listings["calc-01"] == [".gitkeep"]
    assert fake.listings["file-01"] == [".gitkeep", "a.txt"]
    assert fake.listings["file-02"] == [".gitkeep", "b.txt"]


def test_checks_see_the_files_the_task_wrote(env: Env) -> None:
    """AC20/spec 5.4: checks run right after the task, before the next reset."""
    # WHY: file tasks are judged by what they wrote into the workspace.
    tasks = json.loads(env.tasks_path.read_text(encoding="utf-8"))
    tasks[0]["checks"] = [{"type": "exact", "text": "hello", "file": "out.txt"}]
    env.tasks_path.write_text(json.dumps(tasks), encoding="utf-8")
    fake = env.fake({"calc-01": {"write": {"out.txt": "hello\n"}}})
    env.run(fake=fake, task_ids=["calc-01"])
    line = env.results()[0]
    assert line["success"] is True
    assert line["checks"][0]["source"] == "file:out.txt"


# --- AC21: resume ------------------------------------------------------------------------


def test_resume_with_two_of_three_results_runs_only_the_missing_task(env: Env) -> None:
    """AC21: only the missing task runs."""
    # WHY: resume must not pay for tasks that are already measured.
    env.run()
    results_path = env.pass_dir() / "results.jsonl"
    lines = results_path.read_text(encoding="utf-8").split("\n")
    results_path.write_text("\n".join(lines[:2]) + "\n", encoding="utf-8")
    fake = env.fake()
    assert env.run(fake=fake) == "complete"
    assert fake.calls == ["file-02"]
    assert [r["task_id"] for r in env.results()] == IDS


def test_resume_of_a_complete_pass_makes_no_run_call(env: Env) -> None:
    """AC21: a complete pass resumes with zero run_fn calls and no new line."""
    # WHY: spec 5.7, the second smoke run must make no LLM call.
    env.run()
    before = _snapshot(env.pass_dir())
    fake = env.fake()
    assert env.run(fake=fake) == "complete"
    assert fake.calls == []
    assert _snapshot(env.pass_dir()) == before


def test_resume_with_same_explicit_task_ids_is_accepted(env: Env) -> None:
    """AC21/AC22: `--tasks` equal to the manifest's list is not a difference."""
    # WHY: only a real difference may block a resume.
    env.run(task_ids=["calc-01", "file-01"])
    fake = env.fake()
    assert env.run(fake=fake, task_ids=["file-01", "calc-01"]) == "complete"
    assert fake.calls == []


def test_resume_with_a_half_written_results_line_is_refused_naming_the_line(env: Env) -> None:
    """Spec 7: a broken last line refuses the resume and names the line number."""
    # WHY: silently dropping it would re-run a paid task; keeping it would hide a gap.
    env.run(task_ids=["calc-01"])
    results_path = env.pass_dir() / "results.jsonl"
    with results_path.open("a", encoding="utf-8") as handle:
        handle.write('{"task_id": "file-01", "succ')
    fake = env.fake()
    with pytest.raises(PassRefused, match="line 2"):
        env.run(fake=fake, task_ids=["calc-01"])
    assert fake.calls == []


def test_read_results_missing_file_means_no_results(tmp_path: Path) -> None:
    """Spec 5.5: no `results.jsonl` yet means an empty list."""
    # WHY: a fresh pass has no results.
    assert _read_results(tmp_path / "results.jsonl") == []


@pytest.mark.parametrize("line", ["[1, 2]", '{"no_task_id": 1}', '{"task_id": 5}', "not json"])
def test_read_results_line_that_is_not_a_result_object_is_refused(
    tmp_path: Path, line: str
) -> None:
    """Spec 7: anything that is not an object with a string task_id is refused."""
    # WHY: a wrong line would hide a task or crash later.
    path = tmp_path / "results.jsonl"
    path.write_text(f'{{"task_id": "calc-01"}}\n{line}\n', encoding="utf-8")
    with pytest.raises(PassRefused, match="line 2"):
        _read_results(path)


def test_existing_pass_folder_without_manifest_is_refused(env: Env) -> None:
    """Spec 5.5: a non-empty folder with no manifest is not a pass that can be resumed."""
    # WHY: a half-created pass must not be silently treated as new.
    env.pass_dir().mkdir(parents=True)
    (env.pass_dir() / "results.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(PassRefused, match="manifest"):
        env.run()


def test_existing_empty_pass_folder_is_treated_as_a_new_pass(env: Env) -> None:
    """Spec 5.5: an empty folder (for example a failed first start) starts fresh."""
    # WHY: an empty folder holds no record that could be mixed.
    env.pass_dir().mkdir(parents=True)
    assert env.run() == "complete"
    assert len(env.results()) == 3


# --- AC22: refusals on resume ------------------------------------------------------------


def _refusal_cases() -> list[Any]:
    """Return (id, settings, task_ids, expected field in the message) for each difference."""
    other_model = load_settings({"OLLAMA_MODEL": "other-model"})
    no_validation = load_settings({"OLLAMA_MODEL": MODEL, "ENABLE_VALIDATION": "false"})
    no_replans = load_settings({"OLLAMA_MODEL": MODEL, "MAX_REPLANS": "0"})
    return [
        pytest.param(other_model, None, "model", id="model"),
        pytest.param(no_validation, None, "config.enable_validation", id="enable_validation"),
        pytest.param(no_replans, None, "config.max_replans", id="max_replans"),
        pytest.param(SETTINGS, ["calc-01", "file-01"], "task_ids", id="task_ids"),
    ]


@pytest.mark.parametrize(("settings", "task_ids", "field"), _refusal_cases())
def test_resume_refuses_when_settings_or_tasks_differ_and_changes_nothing(
    env: Env, settings: Settings, task_ids: list[str] | None, field: str
) -> None:
    """AC22: a differing model, config value or task list refuses, names the field."""
    # WHY: one pass must never mix models, settings or tasks.
    env.run()
    before = _snapshot(env.pass_dir())
    fake = env.fake()
    with pytest.raises(PassRefused) as info:
        env.run(fake=fake, settings=settings, task_ids=task_ids)
    assert field in str(info.value)
    # WHY: Hard rule 7, values are never printed, only field names.
    assert "other-model" not in str(info.value)
    assert fake.calls == []
    assert _snapshot(env.pass_dir()) == before


def test_resume_refuses_when_code_fingerprint_differs(env: Env) -> None:
    """AC22: changing one byte of code refuses the resume and names `code_fingerprint`."""
    # WHY: results from before and after a code fix must never be mixed.
    env.run()
    before = _snapshot(env.pass_dir())
    (env.root / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
    fake = env.fake()
    with pytest.raises(PassRefused, match="code_fingerprint"):
        env.run(fake=fake)
    assert fake.calls == []
    assert _snapshot(env.pass_dir()) == before


def test_compare_manifest_equal_inputs_report_no_difference() -> None:
    """AC22: identical manifest and current run give an empty list."""
    # WHY: resume must be allowed when nothing changed.
    manifest = {"model": "m", "config": {"a": 1}, "code_fingerprint": "f", "task_ids": ["x"]}
    assert _compare_manifest(manifest, dict(manifest)) == []


def test_compare_manifest_names_every_differing_field_without_values() -> None:
    """AC22: all differences are listed by name, never by value."""
    # WHY: Hard rule 7, a secret in a config value must not reach a message.
    manifest = {
        "model": "old-model",
        "config": {"a": 1, "gone": "SECRET-OLD"},
        "code_fingerprint": "f1",
        "task_ids": ["x"],
    }
    current = {
        "model": "new-model",
        "config": {"a": 2, "added": "SECRET-NEW"},
        "code_fingerprint": "f2",
        "task_ids": ["y"],
    }
    differing = _compare_manifest(manifest, current)
    assert differing == [
        "model",
        "config.a",
        "config.added",
        "config.gone",
        "code_fingerprint",
        "task_ids",
    ]
    assert not any("SECRET" in name for name in differing)


# --- AC23: pause on infrastructure error -------------------------------------------------


def test_usage_cap_pauses_the_pass_and_writes_no_result_for_that_task(env: Env) -> None:
    """AC23: outcome paused, 1 result, 1 paused line, task 3 not run."""
    # WHY: a usage cap says nothing about the agent, so it must not count as a failure.
    fake = env.fake({"file-01": {"status": "failed", "error": USAGE_CAP, "run_end": False}})
    assert env.run(fake=fake) == "paused"
    assert fake.calls == ["calc-01", "file-01"]
    assert [r["task_id"] for r in env.results()] == ["calc-01"]
    paused = _jsonl(env.pass_dir() / "paused.jsonl")
    assert len(paused) == 1
    assert paused[0]["task_id"] == "file-01"
    assert paused[0]["error"] == USAGE_CAP
    assert paused[0]["run_id"] == "run-file-01"
    assert paused[0]["at"].endswith("Z")


def test_resume_after_a_pause_runs_the_paused_task_and_the_rest(env: Env) -> None:
    """AC23: a resume afterwards runs tasks 2 and 3 and completes."""
    # WHY: nothing was lost by the pause.
    env.run(fake=env.fake({"file-01": {"status": "failed", "error": USAGE_CAP}}))
    fake = env.fake()
    assert env.run(fake=fake) == "complete"
    assert fake.calls == ["file-01", "file-02"]
    assert [r["task_id"] for r in env.results()] == IDS
    assert len(_jsonl(env.pass_dir() / "paused.jsonl")) == 1


def test_paused_pass_prints_how_to_resume(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    """Spec 5.5: the pause message tells how to resume."""
    # WHY: Gaurav needs the exact command after a usage cap.
    env.run(fake=env.fake({"calc-01": {"status": "failed", "error": USAGE_CAP}}))
    out = " ".join(capsys.readouterr().out.split())
    assert "paused at calc-01" in out
    assert "python -m benchmark.runner --pass p1" in out


# --- AC24: is_infra_error ----------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        "ConnectionError: down",
        "ConnectError: x",
        "ReadTimeout: x",
        "ResponseError: x",
        "TimeoutError: x",
        "ResponseError",
        "  ResponseError : spaced",
    ],
)
def test_is_infra_error_true_for_infrastructure_types(error: str) -> None:
    """AC24: infrastructure exception types are recognised."""
    # WHY: these pause the pass instead of counting a failure.
    assert is_infra_error(error) is True


@pytest.mark.parametrize(
    "error",
    [
        "",
        "GraphRecursionError: x",
        "ValueError: task is empty",
        "KeyError: 'plan'",
        "ValueError: ConnectionError: wrapped",
    ],
)
def test_is_infra_error_false_for_everything_else(error: str) -> None:
    """AC24: empty text, agent errors and bugs are not infrastructure."""
    # WHY: a bug counted as an outage would hide it from the results.
    assert is_infra_error(error) is False


@pytest.mark.parametrize("name", sorted(INFRA_ERROR_TYPES))
def test_is_infra_error_true_for_every_listed_type(name: str) -> None:
    """AC24: each type in the spec list is recognised."""
    # WHY: pins the list from spec section 5.5.
    assert is_infra_error(f"{name}: boom") is True


def test_infra_error_types_match_the_spec_list() -> None:
    """AC24: the set equals the 15 names of spec section 5.5."""
    # WHY: a silent edit of the list would change what counts as an agent failure.
    assert INFRA_ERROR_TYPES == {
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


# --- AC25: crashes -----------------------------------------------------------------------


def test_non_infrastructure_crash_is_a_failed_result_and_the_pass_continues(env: Env) -> None:
    """AC25: `KeyError: 'plan'` gives success=False with the error, and the next task runs."""
    # WHY: our own bugs must show up in the results, not stop the pass.
    fake = env.fake({"file-01": {"status": "failed", "error": "KeyError: 'plan'", "answer": ""}})
    assert env.run(fake=fake) == "complete"
    assert fake.calls == IDS
    line = env.results()[1]
    assert line["success"] is False
    assert line["error"] == "KeyError: 'plan'"
    assert not (env.pass_dir() / "paused.jsonl").exists()


# --- AC26: model mismatch ----------------------------------------------------------------


def test_run_end_with_another_model_stops_the_pass_without_a_result(env: Env) -> None:
    """AC26: a different model in `run_end` refuses, and that task has no result line."""
    # WHY: this should never happen, but if it does the pass would be mixed.
    fake = env.fake({"file-01": {"model": "other-model"}})
    with pytest.raises(PassRefused, match="different model"):
        env.run(fake=fake)
    assert [r["task_id"] for r in env.results()] == ["calc-01"]
    assert fake.calls == ["calc-01", "file-01"]


def test_run_without_run_end_stops_the_pass_without_a_result(env: Env) -> None:
    """AC26 extra: no `run_end` record means the model is unknown, so the pass stops."""
    # WHY: a result without model and counters would be unreliable evidence.
    with pytest.raises(PassRefused, match="run_end"):
        env.run(fake=env.fake({"calc-01": {"run_end": False}}))
    assert env.results() == []


# --- AC27: tools, counters, answer -------------------------------------------------------


def test_tools_used_lists_each_tool_once_in_first_use_order(env: Env) -> None:
    """AC27: tool names from `tool_call` records, once each, first-use order."""
    # WHY: `tools_used` shows whether a search task really used a tool.
    tools = ["web_search", "calculator", "web_search", "write_file", "calculator"]
    env.run(fake=env.fake({"calc-01": {"tools": tools}}), task_ids=["calc-01"])
    assert env.results()[0]["tools_used"] == ["web_search", "calculator", "write_file"]


def test_counters_equal_the_run_end_counters(env: Env) -> None:
    """AC27: `counters` is the `run_end` counters."""
    # WHY: the failed-tool-call rate is computed from them.
    counters = {"plan_steps": 3, "tool_calls": 7, "failed_tool_calls": 2}
    env.run(fake=env.fake({"calc-01": {"counters": counters}}), task_ids=["calc-01"])
    assert env.results()[0]["counters"] == counters


def test_final_answer_is_cut_to_500_chars(env: Env) -> None:
    """AC27: a long answer is cut to 500 characters."""
    # WHY: the full text is in the trace; result lines stay small.
    answer = "15 " + "y" * 900
    env.run(fake=env.fake({"calc-01": {"answer": answer}}), task_ids=["calc-01"])
    stored = env.results()[0]["final_answer"]
    assert len(stored) == 500
    assert stored.startswith("15 yyy")


def test_verbose_shows_every_record_and_default_shows_none(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 5.5: `--verbose` sends each record to `print_record`; otherwise none."""
    # WHY: live events are opt-in.
    shown: list[dict[str, Any]] = []
    monkeypatch.setattr(runner, "print_record", shown.append)
    env.run(task_ids=["calc-01"])
    assert shown == []
    env.run("p2", task_ids=["calc-01"], verbose=True)
    assert [r["event"] for r in shown] == ["tool_call", "run_end"]


def test_collector_keeps_run_end_and_ignores_odd_tool_records() -> None:
    """AC27: the collector tolerates records without a usable tool name."""
    # WHY: a malformed record must not crash a paid pass.
    collector = _RunCollector(verbose=False)
    collector({"event": "tool_call", "data": {"tool": 5}})
    collector({"event": "tool_call", "data": "oops"})
    collector({"event": "tool_call"})
    collector({"event": "tool_call", "data": {"tool": "x" * 300}})
    collector({"event": "run_end", "model": "m", "data": {}})
    assert len(collector.tools) == 1
    assert len(collector.tools[0]) == 100
    assert collector.run_end is not None
    assert collector.run_end["model"] == "m"


# --- AC28/AC29: pass id and task ids -----------------------------------------------------


@pytest.mark.parametrize("pass_id", ["../x", "a/b", "Main", "", "a" * 41, "-x", "a b", "a.b"])
def test_bad_pass_id_is_refused_and_creates_nothing(env: Env, tmp_path: Path, pass_id: str) -> None:
    """AC28: unsafe pass ids are refused before anything is created."""
    # WHY: the pass id becomes a folder name; this blocks `..` and `/`.
    before = _snapshot(tmp_path)
    fake = env.fake()
    with pytest.raises(PassRefused, match="invalid pass id"):
        env.run(pass_id, fake=fake)
    assert fake.calls == []
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("pass_id", ["main-on-1", "a", "0x", "a" * 40])
def test_good_pass_id_is_accepted(env: Env, pass_id: str) -> None:
    """AC28: `main-on-1` and the limits of the pattern are accepted."""
    # WHY: the rule must not refuse real pass names.
    assert env.run(pass_id, task_ids=["calc-01"]) == "complete"
    assert (env.results_root / pass_id / "manifest.json").is_file()


def test_unknown_task_id_is_refused_and_creates_no_pass_folder(env: Env, tmp_path: Path) -> None:
    """AC29: an unknown id in `--tasks` refuses and creates no folder."""
    # WHY: a typo must not leave a half-made pass behind.
    before = _snapshot(tmp_path)
    fake = env.fake()
    with pytest.raises(PassRefused, match="unknown task id"):
        env.run(fake=fake, task_ids=["calc-01", "nope-99"])
    assert fake.calls == []
    assert _snapshot(tmp_path) == before
    assert not env.pass_dir().exists()


def test_select_tasks_cuts_an_over_long_unknown_id_in_the_message() -> None:
    """AC29: the message shows at most 40 characters of the bad id."""
    # WHY: a pasted blob must not flood the terminal.
    tasks = load_tasks(PROJECT_ROOT / "benchmark" / "tasks.json")
    with pytest.raises(PassRefused) as info:
        _select_tasks(tasks, ["x" * 100])
    assert "x" * 41 not in str(info.value)


def test_select_tasks_none_returns_all_tasks_in_file_order() -> None:
    """AC29: `None` selects everything, in file order."""
    # WHY: a full pass uses all 30 tasks.
    tasks = load_tasks(PROJECT_ROOT / "benchmark" / "tasks.json")
    assert _select_tasks(tasks, None) == tasks
    picked = _select_tasks(tasks, ["file-01", "calc-01"])
    assert [t.id for t in picked] == ["calc-01", "file-01"]


def test_invalid_tasks_file_raises_before_any_folder_is_created(env: Env) -> None:
    """Spec 5.5 step 2: a bad task file raises TasksFileError and creates nothing."""
    # WHY: no LLM call may be spent on a broken benchmark.
    env.tasks_path.write_text("[{", encoding="utf-8")
    with pytest.raises(TasksFileError):
        env.run()
    assert not env.results_root.exists()


# --- AC30: code_fingerprint --------------------------------------------------------------


def test_code_fingerprint_is_stable_and_has_sha256_prefix(tmp_path: Path) -> None:
    """AC30: same files, same fingerprint."""
    # WHY: a resume must be allowed when nothing changed.
    _make_project(tmp_path)
    first = code_fingerprint(tmp_path)
    assert first.startswith("sha256:")
    assert len(first) == len("sha256:") + 64
    assert code_fingerprint(tmp_path) == first


def test_code_fingerprint_same_for_an_identical_copy_in_another_folder(tmp_path: Path) -> None:
    """AC30: only relative paths and contents count, not the folder."""
    # WHY: moving the project must not block a resume.
    _make_project(tmp_path / "one")
    _make_project(tmp_path / "two")
    assert code_fingerprint(tmp_path / "one") == code_fingerprint(tmp_path / "two")


@pytest.mark.parametrize(
    "relative", ["src/a.py", "benchmark/b.py", "benchmark/tasks.json"], ids=["src", "bench", "json"]
)
def test_code_fingerprint_changes_when_code_or_tasks_change(tmp_path: Path, relative: str) -> None:
    """AC30: a changed `.py` under src/ or benchmark/, or tasks.json, changes it."""
    # WHY: results from before and after a code fix must never be mixed.
    _make_project(tmp_path)
    before = code_fingerprint(tmp_path)
    target = tmp_path / relative
    target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert code_fingerprint(tmp_path) != before


def test_code_fingerprint_changes_when_a_python_file_is_added_or_renamed(tmp_path: Path) -> None:
    """AC30: file names count, not only contents."""
    # WHY: moving code between files is a code change.
    _make_project(tmp_path)
    before = code_fingerprint(tmp_path)
    (tmp_path / "src" / "a.py").rename(tmp_path / "src" / "c.py")
    renamed = code_fingerprint(tmp_path)
    assert renamed != before
    (tmp_path / "src" / "sub").mkdir()
    (tmp_path / "src" / "sub" / "d.py").write_text("D = 4\n", encoding="utf-8")
    assert code_fingerprint(tmp_path) != renamed


@pytest.mark.parametrize(
    "relative",
    [
        "docs/x.md",
        "docs/new.md",
        "src/notes.txt",
        "benchmark/results/p1/results.jsonl",
        "benchmark/sub/deep.py",
        "tests/test_x.py",
    ],
)
def test_code_fingerprint_ignores_files_outside_the_code_set(tmp_path: Path, relative: str) -> None:
    """AC30: docs, results, non-Python files in src/, nested benchmark files do not count."""
    # WHY: a docs-only commit or a new result line must not block a resume.
    _make_project(tmp_path)
    before = code_fingerprint(tmp_path)
    target = tmp_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed content", encoding="utf-8")
    assert code_fingerprint(tmp_path) == before


def test_code_fingerprint_of_an_empty_folder_still_returns_a_hash(tmp_path: Path) -> None:
    """AC30 extra: missing src/ and benchmark/ folders do not raise."""
    # WHY: robust for a minimal root.
    assert code_fingerprint(tmp_path).startswith("sha256:")


# --- _git_info ---------------------------------------------------------------------------


def test_git_info_unusable_git_gives_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 7: git missing gives `("unknown", False)`."""

    # WHY: a pass must still start without git.
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("git")

    monkeypatch.setattr(runner.subprocess, "run", boom)
    assert _REAL_GIT_INFO(tmp_path) == ("unknown", False)


def test_git_info_not_a_repo_gives_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec 7: a failing git command (not a repo) gives `("unknown", False)`."""
    # WHY: a pass must still start outside a git repository.
    failed = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="no repo")
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: failed)
    assert _REAL_GIT_INFO(tmp_path) == ("unknown", False)


def test_git_info_reads_commit_and_dirty_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec 7: the commit and the dirty flag come from git output."""

    # WHY: they go in the manifest as information.
    def fake_git(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        out = "deadbeef\n" if command[1] == "rev-parse" else " M src/a.py\n"
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_git)
    assert _REAL_GIT_INFO(tmp_path) == ("deadbeef", True)


# --- AC31: summarize ---------------------------------------------------------------------


def _scripted_results() -> list[dict[str, Any]]:
    """4 results: 2 successes, 3 calc + 1 file, 10 tool calls (3 failed), 1 crash."""
    return [
        {"category": "calc", "success": True, "error": "", "counters": _calls(4, 1)},
        {"category": "calc", "success": True, "error": "", "counters": _calls(3, 0)},
        {
            "category": "calc",
            "success": False,
            "error": "KeyError: 'plan'",
            "counters": _calls(2, 1),
        },
        {"category": "file", "success": False, "error": "", "counters": _calls(1, 1)},
    ]


def _calls(total: int, failed: int) -> dict[str, int]:
    """Build run counters."""
    return {"tool_calls": total, "failed_tool_calls": failed}


def test_summarize_four_scripted_results_gives_the_spec_numbers() -> None:
    """AC31: success rate, categories, failed-call rate and crashes."""
    # WHY: these are the exact metric definitions of architecture section 5.
    summary = summarize(_scripted_results(), planned=30, infra_pauses=2)
    assert summary["tasks_done"] == 4
    assert summary["tasks_planned"] == 30
    assert summary["successes"] == 2
    assert summary["success_rate"] == 0.5
    assert summary["by_category"] == {
        "calc": {"successes": 2, "tasks": 3},
        "file": {"successes": 0, "tasks": 1},
    }
    assert summary["tool_calls"] == 10
    assert summary["failed_tool_calls"] == 3
    assert summary["failed_tool_call_rate"] == pytest.approx(0.3)
    assert summary["infra_pauses"] == 2
    assert summary["crashed"] == 1


def test_summarize_no_tool_calls_gives_none_rate() -> None:
    """AC31: with 0 tool calls the failed-call rate is None, not a division error."""
    # WHY: "no data" must not look like "0 % failed".
    results = [{"category": "calc", "success": True, "error": "", "counters": _calls(0, 0)}]
    assert summarize(results)["failed_tool_call_rate"] is None


def test_summarize_no_results_gives_none_success_rate() -> None:
    """AC31: an empty pass has no success rate."""
    # WHY: avoids a division by zero in `--summary` of a fresh pass.
    summary = summarize([])
    assert summary["success_rate"] is None
    assert summary["tasks_done"] == 0
    assert summary["by_category"] == {}


def test_summarize_ignores_counters_that_are_not_plain_ints() -> None:
    """AC31 extra: a bad counter value counts as 0 and never raises."""
    # WHY: one odd result line must not stop a summary.
    results = [
        {"category": "calc", "success": True, "counters": {"tool_calls": "5"}},
        {"category": "calc", "success": True, "counters": {"tool_calls": True}},
        {"category": "calc", "success": True, "counters": "oops"},
    ]
    assert summarize(results)["tool_calls"] == 0


def test_summarize_counts_only_real_true_as_success() -> None:
    """AC31 extra: a truthy non-bool does not count as a success."""
    # WHY: `success` must be exactly True.
    assert summarize([{"category": "calc", "success": 1}])["successes"] == 0


def test_completed_pass_summary_matches_its_result_lines(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC31: the summary printed at the end of a pass uses the same numbers."""
    # WHY: the printed summary is what Gaurav copies into his notes.
    env.run(fake=env.fake({"file-02": {"answer": "wrong"}}))
    out = capsys.readouterr().out
    assert "success: 2/3 (66.7%)" in out
    assert "calc: 1/1" in out
    assert "file: 1/2" in out
    assert "tool calls: 3, failed: 0 (0.0%)" in out


# --- AC32/AC33: CLI ----------------------------------------------------------------------


def _build_pass(results_root: Path, pass_id: str = "p1") -> Path:
    """Write a finished pass folder by hand: manifest, 2 of 3 results, 1 pause."""
    folder = results_root / pass_id
    folder.mkdir(parents=True)
    manifest = {
        "model": MODEL,
        "config": {},
        "code_fingerprint": "sha256:x",
        "task_ids": IDS,
    }
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    lines = [
        {"task_id": "calc-01", "category": "calc", "success": True, "counters": _calls(2, 1)},
        {"task_id": "file-01", "category": "file", "success": False, "counters": _calls(0, 0)},
    ]
    (folder / "results.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )
    (folder / "paused.jsonl").write_text('{"task_id": "file-02"}\n', encoding="utf-8")
    return folder


def test_main_summary_prints_the_summary_returns_zero_and_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC32: `--summary` on an existing pass prints, returns 0, makes no run."""
    # WHY: the summary must cost no LLM call.
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
    _build_pass(tmp_path / "results")
    run_mock = MagicMock()
    monkeypatch.setattr(runner, "run_pass", run_mock)
    assert main(["--pass", "p1", "--summary"]) == 0
    run_mock.assert_not_called()
    out = capsys.readouterr().out
    assert "Summary of pass p1" in out
    assert "tasks done: 2/3" in out
    assert "success: 1/2 (50.0%)" in out
    assert "paused by infrastructure errors: 1" in out


def test_main_summary_on_a_missing_pass_returns_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC32: a missing pass returns 2 and says so."""
    # WHY: a typo in the pass id must be a clear refusal.
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
    run_mock = MagicMock()
    monkeypatch.setattr(runner, "run_pass", run_mock)
    assert main(["--pass", "nope", "--summary"]) == 2
    run_mock.assert_not_called()
    assert "no such pass" in capsys.readouterr().out


def test_main_summary_with_unsafe_pass_id_returns_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC32/AC28: `--summary` also checks the pass id."""
    # WHY: `--pass ../x --summary` must not read outside the results folder.
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
    assert main(["--pass", "../x", "--summary"]) == 2


def test_main_summary_with_a_broken_manifest_returns_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC32 extra: an unreadable manifest is a refusal, not a traceback."""
    # WHY: the CLI must always give a short message.
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
    folder = tmp_path / "results" / "p1"
    folder.mkdir(parents=True)
    (folder / "manifest.json").write_text("{not json", encoding="utf-8")
    assert main(["--pass", "p1", "--summary"]) == 2


@pytest.mark.parametrize(("outcome", "code"), [("complete", 0), ("paused", 3)])
def test_main_returns_zero_for_complete_and_three_for_paused(
    monkeypatch: pytest.MonkeyPatch, outcome: str, code: int
) -> None:
    """AC33: exit code 0 for a complete pass, 3 for a paused one."""
    # WHY: scripts and Gaurav rely on these codes.
    monkeypatch.setattr(runner, "run_pass", MagicMock(return_value=outcome))
    assert main(["--pass", "p1"]) == code


@pytest.mark.parametrize(
    "error",
    [ConfigError("OLLAMA_MODEL is required"), PassRefused("nope"), TasksFileError("bad file")],
)
def test_main_returns_two_for_refusals_with_a_short_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: Exception
) -> None:
    """AC33: ConfigError, PassRefused and TasksFileError give exit 2 and no traceback."""
    # WHY: a setup mistake needs a short message, not a stack trace.
    monkeypatch.setattr(runner, "run_pass", MagicMock(side_effect=error))
    assert main(["--pass", "p1"]) == 2
    out = capsys.readouterr().out
    assert "refused" in out
    assert str(error) in out
    assert "Traceback" not in out


def test_main_with_real_run_pass_and_missing_config_returns_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC33: a ConfigError from `get_settings` reaches exit 2 and creates no folder."""

    # WHY: the real wiring, without reading the real `.env`.
    def broken() -> Settings:
        raise ConfigError("OLLAMA_MODEL is required")

    monkeypatch.setattr(runner, "get_settings", broken)
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
    assert main(["--pass", "p1"]) == 2
    assert not (tmp_path / "results").exists()


def test_main_keyboard_interrupt_returns_130_and_says_how_to_resume(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Spec 7: Ctrl-C returns 130 and prints the resume command."""
    # WHY: finished tasks are on disk, the user needs the next step.
    monkeypatch.setattr(runner, "run_pass", MagicMock(side_effect=KeyboardInterrupt))
    assert main(["--pass", "p1"]) == 130
    out = " ".join(capsys.readouterr().out.split())
    assert "interrupted" in out
    assert "python -m benchmark.runner --pass p1" in out


def test_main_passes_task_ids_and_verbose_to_run_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC33: `--tasks a, b --verbose` reach run_pass as a clean list and a flag."""
    # WHY: the CLI is only a thin shell around run_pass.
    run_mock = MagicMock(return_value="complete")
    monkeypatch.setattr(runner, "run_pass", run_mock)
    assert main(["--pass", "p1", "--tasks", "calc-01, file-02,", "--verbose"]) == 0
    args, kwargs = run_mock.call_args
    assert args == ("p1",)
    assert kwargs["task_ids"] == ["calc-01", "file-02"]
    assert kwargs["verbose"] is True


def test_main_without_task_option_passes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC33: no `--tasks` means all tasks (None)."""
    # WHY: None selects the full benchmark.
    run_mock = MagicMock(return_value="complete")
    monkeypatch.setattr(runner, "run_pass", run_mock)
    main(["--pass", "p1"])
    assert run_mock.call_args.kwargs["task_ids"] is None
    assert run_mock.call_args.kwargs["verbose"] is False


@pytest.mark.parametrize("argv", [[], ["--pass", "p1", "--tasks", " , "]])
def test_main_bad_arguments_exit_with_argparse_code_two(argv: list[str]) -> None:
    """AC33 extra: a missing `--pass` or an empty `--tasks` list is a usage error."""
    # WHY: no pass may start with unusable arguments.
    with pytest.raises(SystemExit) as info:
        main(argv)
    assert info.value.code == 2


# --- AC34: source rules ------------------------------------------------------------------


def _benchmark_sources() -> dict[str, str]:
    """Read the text of every `benchmark/*.py` file."""
    folder = PROJECT_ROOT / "benchmark"
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.py"))}


def test_benchmark_package_has_the_expected_modules() -> None:
    """AC34 control: the source scan really sees the four modules."""
    # WHY: an empty scan would make the rules below pass for nothing.
    assert {"__init__.py", "text.py", "tasks.py", "checkers.py", "runner.py"} <= set(
        _benchmark_sources()
    )


@pytest.mark.parametrize(
    "forbidden",
    [
        "gpt-oss",
        "gemma",
        "os.environ",
        "getenv",
        "load_dotenv",
        "langchain_ollama",
    ],
)
def test_benchmark_sources_contain_no_model_name_env_read_or_ollama_import(
    forbidden: str,
) -> None:
    """AC34: no model name, no environment read, no `langchain_ollama`."""
    # WHY: Hard rule 2 (model only from config) and rule 6 (checkers are code only).
    offenders = [name for name, text in _benchmark_sources().items() if forbidden in text]
    assert offenders == []


@pytest.mark.parametrize("function", ["eval", "exec"])
def test_benchmark_sources_never_call_eval_or_exec(function: str) -> None:
    """AC34: no `eval(` or `exec(` call."""
    # WHY: Hard rule 3 spirit, and nothing in the benchmark may run text as code.
    pattern = re.compile(rf"(?<![\w.]){function}\(")
    offenders = [name for name, text in _benchmark_sources().items() if pattern.search(text)]
    assert offenders == []


# --- AC39: docs --------------------------------------------------------------------------


@pytest.mark.parametrize("doc", ["CLAUDE.md", "docs/project-memory.md"])
def test_docs_contain_the_benchmark_command(doc: str) -> None:
    """AC39: CLAUDE.md and project-memory hold `python -m benchmark.runner --pass <pass_id>`."""
    # WHY: Gaurav looks the command up there.
    text = (PROJECT_ROOT / doc).read_text(encoding="utf-8")
    assert "python -m benchmark.runner --pass <pass_id>" in text


# --- Code-review fixes: symlinked workspace, manifest types, redaction, repeated pauses ---


def test_reset_workspace_refuses_a_workspace_that_is_a_symlink(tmp_path: Path) -> None:
    """Review W1: a linked sandbox is refused and the folder it points to is untouched."""
    # WHY: `iterdir()` follows a symlinked folder, so without the guard this would empty the
    # victim folder.
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("precious", encoding="utf-8")
    link = tmp_path / "workspace"
    os.symlink(victim, link)
    with pytest.raises(ValueError, match="symlink"):
        reset_workspace(link)
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "precious"


def test_run_pass_refuses_a_symlinked_workspace_before_creating_anything(
    env: Env, tmp_path: Path
) -> None:
    """Review W1: the pass is refused up front, no folder is made, nothing is deleted."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep.txt").write_text("precious", encoding="utf-8")
    env.workspace.rename(tmp_path / "old-workspace")
    os.symlink(victim, env.workspace)
    fake = env.fake()
    with pytest.raises(PassRefused, match="symlink"):
        env.run(fake=fake)
    assert fake.calls == []
    assert not env.pass_dir().exists()
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "precious"


GOOD_MANIFEST: dict[str, Any] = {
    "model": "m",
    "config": {},
    "code_fingerprint": "sha256:x",
    "task_ids": ["calc-01"],
}


@pytest.mark.parametrize(
    "broken",
    [
        {**GOOD_MANIFEST, "task_ids": [["x"]]},
        {**GOOD_MANIFEST, "task_ids": [1]},
        {**GOOD_MANIFEST, "task_ids": 5},
        {**GOOD_MANIFEST, "model": 7},
        {**GOOD_MANIFEST, "config": []},
        {**GOOD_MANIFEST, "code_fingerprint": None},
        {k: v for k, v in GOOD_MANIFEST.items() if k != "task_ids"},
        ["not", "a", "dict"],
    ],
)
def test_read_manifest_wrong_types_are_refused_not_crashes(tmp_path: Path, broken: Any) -> None:
    """Review W2: a manifest with wrong types gives PassRefused, never a TypeError."""
    # WHY: a manifest can come from git, so it is untrusted data.
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(PassRefused):
        runner._read_manifest(path)


def test_read_manifest_good_manifest_is_returned(tmp_path: Path) -> None:
    """Review W2: the type checks do not refuse a valid manifest."""
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(GOOD_MANIFEST), encoding="utf-8")
    assert runner._read_manifest(path) == GOOD_MANIFEST


def test_read_manifest_deeply_nested_json_is_refused(tmp_path: Path) -> None:
    """Review W2: `RecursionError` from `json.loads` becomes a refusal."""
    path = tmp_path / "manifest.json"
    path.write_text("[" * 200_000, encoding="utf-8")
    with pytest.raises(PassRefused):
        runner._read_manifest(path)


def test_read_results_deeply_nested_line_is_refused(tmp_path: Path) -> None:
    """Review W2: a deeply nested results line is a refusal naming the line."""
    path = tmp_path / "results.jsonl"
    path.write_text("[" * 200_000 + "\n", encoding="utf-8")
    with pytest.raises(PassRefused, match="line 1"):
        _read_results(path)


def test_resume_with_a_crafted_manifest_is_refused_not_a_traceback(env: Env) -> None:
    """Review W2: a resume of a pass whose manifest has bad task ids is a clean refusal."""
    env.run()
    manifest_path = env.pass_dir() / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["task_ids"] = [["calc-01"]]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    fake = env.fake()
    with pytest.raises(PassRefused):
        env.run(fake=fake)
    assert fake.calls == []


def test_redact_removes_project_folder_home_folder_and_url_queries() -> None:
    """Review W3: local paths and URL query strings do not survive."""
    home = str(Path.home())
    text = (
        f"failed in {PROJECT_ROOT}/src/x.py and {home}/Documents/y.txt, "
        "see https://example.com/api/v1?token=SECRET123&b=2 and https://example.com/plain"
    )
    cleaned = runner._redact(text)
    assert str(PROJECT_ROOT) not in cleaned
    assert "SECRET123" not in cleaned
    assert "<project>/src/x.py" in cleaned
    assert "~/Documents/y.txt" in cleaned
    assert "https://example.com/api/v1?<redacted>" in cleaned
    # WHY: a URL without a query is useful for debugging and is kept.
    assert "https://example.com/plain" in cleaned


def test_redact_leaves_plain_text_unchanged() -> None:
    """Review W3: text without paths or queries is returned as it is."""
    assert runner._redact("KeyError: 'plan'") == "KeyError: 'plan'"


def test_result_line_and_paused_line_are_redacted(env: Env) -> None:
    """Review W3: the saved error, final answer and paused error hold no path or token."""
    leak = f"at {PROJECT_ROOT}/a.py via https://h.example/p?key=SECRET123"
    env.run(
        fake=env.fake(
            {
                "calc-01": {"status": "failed", "error": f"KeyError: {leak}", "answer": leak},
                "file-01": {"status": "failed", "error": f"ResponseError: {leak}"},
            }
        )
    )
    saved = (env.pass_dir() / "results.jsonl").read_text(encoding="utf-8")
    paused = (env.pass_dir() / "paused.jsonl").read_text(encoding="utf-8")
    for text in (saved, paused):
        assert "SECRET123" not in text
        assert str(PROJECT_ROOT) not in text
    assert "<project>" in saved
    assert "<project>" in paused


def test_same_task_paused_three_times_is_recorded_as_a_failed_task(env: Env) -> None:
    """Review W6: the third pause for one task and error type becomes a failed result."""
    # WHY: a deterministic server error must not block the pass and burn quota forever.
    cap = {"calc-01": {"status": "failed", "error": USAGE_CAP}}
    assert runner.MAX_TASK_PAUSES == 3
    assert env.run(fake=env.fake(cap)) == "paused"
    assert env.run(fake=env.fake(cap)) == "paused"
    assert env.results() == []
    assert env.run(fake=env.fake(cap)) == "complete"
    results = env.results()
    assert [r["task_id"] for r in results] == IDS
    assert results[0]["success"] is False
    assert results[0]["error"] == USAGE_CAP
    assert len(_jsonl(env.pass_dir() / "paused.jsonl")) == 3


def test_pause_count_is_per_task_and_error_type(env: Env) -> None:
    """Review W6: a different error type starts a new count, so the pass pauses again."""
    cap = {"calc-01": {"status": "failed", "error": USAGE_CAP}}
    down = {"calc-01": {"status": "failed", "error": "ConnectionError: down"}}
    assert env.run(fake=env.fake(cap)) == "paused"
    assert env.run(fake=env.fake(cap)) == "paused"
    assert env.run(fake=env.fake(down)) == "paused"
    assert env.results() == []


def test_pause_message_shows_the_pause_count(env: Env, capsys: pytest.CaptureFixture[str]) -> None:
    """Review W6: the terminal shows how many times this task has paused the pass."""
    env.run(fake=env.fake({"calc-01": {"status": "failed", "error": USAGE_CAP}}))
    out = " ".join(capsys.readouterr().out.split())
    assert "pause 1 of 3" in out


def test_count_pauses_skips_broken_lines_and_other_tasks(tmp_path: Path) -> None:
    """Review W6: only matching, readable lines are counted."""
    path = tmp_path / "paused.jsonl"
    lines = [
        json.dumps({"task_id": "calc-01", "error": "ResponseError: a"}),
        "not json",
        json.dumps({"task_id": "calc-01", "error": "ConnectionError: b"}),
        json.dumps({"task_id": "file-01", "error": "ResponseError: c"}),
        json.dumps(["not", "a", "dict"]),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert runner._count_pauses(path, "calc-01", "ResponseError") == 1
    assert runner._count_pauses(path, "calc-01", "ConnectionError") == 1
    assert runner._count_pauses(path, "none-00", "ResponseError") == 0
    assert runner._count_pauses(tmp_path / "missing.jsonl", "calc-01", "ResponseError") == 0


def test_benchmark_source_uses_only_the_allowed_comment_tags() -> None:
    """Review W4: no invented tags such as `# RISK:` (colour-coding rule)."""
    allowed = {"WHY", "NOTE", "STEP", "TODO", "WARNING"}
    for path in sorted((PROJECT_ROOT / "benchmark").glob("*.py")):
        tags = set(re.findall(r"#\s([A-Z]{3,})(?: \d+)?:", path.read_text(encoding="utf-8")))
        assert tags <= allowed, (path.name, tags - allowed)
