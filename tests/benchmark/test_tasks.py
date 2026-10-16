"""Tests for `benchmark/tasks.py` and the real `benchmark/tasks.json` (AC1-AC6).

The real file is loaded read-only. Invalid task files are small JSON files in `tmp_path`.
No LLM, no network (Hard rule 8).
"""

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from benchmark.checkers import run_checks
from benchmark.tasks import TASKS_PATH, BenchmarkTask, TasksFileError, load_tasks
from tests.benchmark.expected_tasks import EXPECTED_TASKS
from tests.benchmark.samples import SAMPLES

ID_PATTERN = re.compile(r"^(calc|search|file|multi)-\d{2}$")
PREFIX = {"calc": "calc", "search": "search", "file": "file", "multi-tool": "multi"}

VALID_TASK: dict[str, Any] = {
    "id": "calc-01",
    "category": "calc",
    "task": "Add the numbers together.",
    "checks": [{"type": "number", "value": 5}],
}


def _write(tmp_path: Path, tasks: Any) -> Path:
    """Write a tasks file and return its path."""
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(tasks), encoding="utf-8")
    return path


def _task(**overrides: Any) -> dict[str, Any]:
    """Return a valid task dict with fields replaced."""
    task = copy.deepcopy(VALID_TASK)
    task.update(overrides)
    return task


@pytest.fixture(scope="module")
def real_tasks() -> list[BenchmarkTask]:
    """Load the real task file once."""
    return load_tasks(TASKS_PATH)


# --- AC1 ---------------------------------------------------------------------------------


def test_load_tasks_real_file_returns_30_tasks_with_category_counts(
    real_tasks: list[BenchmarkTask],
) -> None:
    """AC1: 30 tasks, unique ids, 8 calc, 7 search, 7 file, 8 multi-tool."""
    # WHY: the benchmark size is fixed before any measurement.
    assert len(real_tasks) == 30
    assert len({t.id for t in real_tasks}) == 30
    counts = {c: sum(1 for t in real_tasks if t.category == c) for c in PREFIX}
    assert counts == {"calc": 8, "search": 7, "file": 7, "multi-tool": 8}


# --- AC2 ---------------------------------------------------------------------------------


def test_real_tasks_have_valid_ids_and_one_to_three_checks(
    real_tasks: list[BenchmarkTask],
) -> None:
    """AC2: id pattern, prefix matches category, 1 to 3 checks."""
    # WHY: an id must tell the truth about its task.
    for task in real_tasks:
        assert ID_PATTERN.match(task.id)
        assert task.id.startswith(PREFIX[task.category] + "-")
        assert 1 <= len(task.checks) <= 3


def test_real_tasks_equal_the_spec_table_in_order(real_tasks: list[BenchmarkTask]) -> None:
    """AC2: ids, texts, setup files and checks equal the spec table (own copy)."""
    # WHY: Hard rule 6, expected answers must not be edited to make a task pass.
    assert len(EXPECTED_TASKS) == 30
    assert [t.id for t in real_tasks] == [e["id"] for e in EXPECTED_TASKS]
    for task, expected in zip(real_tasks, EXPECTED_TASKS, strict=True):
        assert task.category == expected["category"], task.id
        assert task.task == expected["task"], task.id
        assert task.setup_files == expected["setup_files"], task.id
        assert [c.model_dump() for c in task.checks] == expected["checks"], task.id


# --- AC3 ---------------------------------------------------------------------------------


def test_load_tasks_valid_minimal_file_loads(tmp_path: Path) -> None:
    """AC3 control: the base task used by the error tests is valid."""
    # WHY: otherwise every error test below could pass for the wrong reason.
    tasks = load_tasks(_write(tmp_path, [_task()]))
    assert [t.id for t in tasks] == ["calc-01"]


def test_load_tasks_invalid_json_raises(tmp_path: Path) -> None:
    """AC3: invalid JSON raises TasksFileError."""
    # WHY: a broken file must stop before any LLM call.
    path = tmp_path / "tasks.json"
    path.write_text("[{not json", encoding="utf-8")
    with pytest.raises(TasksFileError):
        load_tasks(path)


def test_load_tasks_missing_file_raises(tmp_path: Path) -> None:
    """AC3: a missing file raises TasksFileError, not OSError."""
    # WHY: the CLI turns TasksFileError into exit 2.
    with pytest.raises(TasksFileError):
        load_tasks(tmp_path / "nope.json")


@pytest.mark.parametrize("content", [{}, [], "text"])
def test_load_tasks_not_a_non_empty_list_raises(tmp_path: Path, content: Any) -> None:
    """AC3: the file must be a non-empty JSON list."""
    # WHY: an empty benchmark would pass trivially.
    with pytest.raises(TasksFileError):
        load_tasks(_write(tmp_path, content))


@pytest.mark.parametrize(
    "bad",
    [
        _task(surprise=1),
        _task(checks=[{"type": "fuzzy", "value": 5}]),
        _task(id="search-01"),
        _task(checks=[{"type": "regex", "pattern": "("}]),
        _task(checks=[{"type": "contains"}]),
        _task(checks=[{"type": "number", "value": v} for v in (5, 6, 7, 8)]),
    ],
    ids=[
        "unknown_field",
        "unknown_check_type",
        "prefix_mismatch",
        "bad_regex",
        "empty_contains",
        "four_checks",
    ],
)
def test_load_tasks_invalid_task_raises_naming_the_task_id(tmp_path: Path, bad: dict) -> None:
    """AC3: each schema problem raises TasksFileError and the message names the task id."""
    # WHY: the message is how Gaurav finds the broken entry.
    with pytest.raises(TasksFileError, match=re.escape(bad["id"])):
        load_tasks(_write(tmp_path, [bad]))


def test_load_tasks_duplicate_id_raises_naming_the_id(tmp_path: Path) -> None:
    """AC3: a duplicate id raises and names the id."""
    # WHY: results are stored by id, two tasks with one id would overwrite each other.
    with pytest.raises(TasksFileError, match="calc-01"):
        load_tasks(_write(tmp_path, [_task(), _task()]))


def test_load_tasks_zero_checks_raises(tmp_path: Path) -> None:
    """AC3: a task needs at least one check."""
    # WHY: a task without checks would always pass.
    with pytest.raises(TasksFileError, match="calc-01"):
        load_tasks(_write(tmp_path, [_task(checks=[])]))


def test_load_tasks_empty_task_text_raises(tmp_path: Path) -> None:
    """AC3: an empty task text raises."""
    # WHY: run_task refuses an empty task, so the file must too.
    with pytest.raises(TasksFileError, match="calc-01"):
        load_tasks(_write(tmp_path, [_task(task="")]))


def test_load_tasks_error_message_does_not_echo_the_input_value(tmp_path: Path) -> None:
    """AC3: the message holds location and reason, not the whole input."""
    # WHY: keeps messages short and avoids echoing large inputs.
    secret = "SECRET-PATTERN-VALUE("
    with pytest.raises(TasksFileError) as info:
        load_tasks(_write(tmp_path, [_task(checks=[{"type": "regex", "pattern": secret}])]))
    assert "SECRET-PATTERN-VALUE" not in str(info.value)


# --- AC4 ---------------------------------------------------------------------------------

UNSAFE_PATHS = ["", "/etc/x", "a/../../x", "a\\b", "x" * 256]


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_load_tasks_unsafe_setup_path_raises(tmp_path: Path, path: str) -> None:
    """AC4: an empty, absolute, `..`, backslash or over-long setup path is refused."""
    # WHY: setup files are written to these paths, they must stay inside workspace/.
    with pytest.raises(TasksFileError, match="calc-01"):
        load_tasks(_write(tmp_path, [_task(setup_files={path: "x"})]))


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_load_tasks_unsafe_check_file_raises(tmp_path: Path, path: str) -> None:
    """AC4: the same rules apply to a check's `file`."""
    # WHY: the checkers read from these paths.
    check = {"type": "number", "value": 5, "file": path}
    with pytest.raises(TasksFileError, match="calc-01"):
        load_tasks(_write(tmp_path, [_task(checks=[check])]))


def test_load_tasks_nested_relative_paths_are_allowed(tmp_path: Path) -> None:
    """AC4 control: `reports/today.txt` is a valid path."""
    # WHY: file-07 needs a nested path, the validation must not be too strict.
    check = {"type": "number", "value": 5, "file": "reports/today.txt"}
    tasks = load_tasks(_write(tmp_path, [_task(setup_files={"a/b.txt": "x"}, checks=[check])]))
    assert tasks[0].setup_files == {"a/b.txt": "x"}


def test_load_tasks_over_long_setup_content_raises(tmp_path: Path) -> None:
    """AC4 related: a setup file longer than 10 000 chars is refused."""
    # WHY: keeps the task file small and the workspace writes bounded.
    with pytest.raises(TasksFileError, match="calc-01"):
        load_tasks(_write(tmp_path, [_task(setup_files={"a.txt": "x" * 10_001})]))


# --- AC5 ---------------------------------------------------------------------------------


def test_load_tasks_number_value_in_task_text_raises(tmp_path: Path) -> None:
    """AC5: task `What is 2 + 40?` with value 2 is refused."""
    # WHY: otherwise an answer that only repeats the task would pass.
    bad = _task(task="What is 2 + 40?", checks=[{"type": "number", "value": 2}])
    with pytest.raises(TasksFileError, match="calc-01"):
        load_tasks(_write(tmp_path, [bad]))


def test_load_tasks_number_value_within_tolerance_of_task_number_raises(tmp_path: Path) -> None:
    """AC5: the tolerance counts, 40.1 +- 0.2 covers the 40 in the text."""
    # WHY: the rule uses the same tolerance as the checker.
    check = {"type": "number", "value": 40.1, "tolerance": 0.2}
    with pytest.raises(TasksFileError):
        load_tasks(_write(tmp_path, [_task(task="What is 2 + 40?", checks=[check])]))


def test_load_tasks_number_value_not_in_task_text_loads(tmp_path: Path) -> None:
    """AC5 control: value 42 is not in `What is 2 + 40?`."""
    # WHY: the rule must only reject a real give-away.
    bad = _task(task="What is 2 + 40?", checks=[{"type": "number", "value": 42}])
    assert len(load_tasks(_write(tmp_path, [bad]))) == 1


# --- AC6 ---------------------------------------------------------------------------------


def _write_files(workspace: Path, files: dict[str, str]) -> None:
    """Write the files an agent would have produced into a workspace."""
    for name, content in files.items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def test_samples_cover_every_real_task(real_tasks: list[BenchmarkTask]) -> None:
    """AC6: the sample table has an entry for each of the 30 tasks, and nothing else."""
    # WHY: a task without a sample would escape the check below.
    assert set(SAMPLES) == {t.id for t in real_tasks}


@pytest.mark.parametrize("task_id", list(SAMPLES))
def test_real_task_good_sample_passes_every_check(
    task_id: str, real_tasks: list[BenchmarkTask], tmp_path: Path
) -> None:
    """AC6: a hand-written correct answer and files pass all checks of the task."""
    # WHY: proves the checks accept the right result in the shapes a model writes it.
    task = next(t for t in real_tasks if t.id == task_id)
    (answer, files), _bad = SAMPLES[task_id]
    _write_files(tmp_path, files)
    results = run_checks(task, answer, tmp_path)
    assert all(r.passed for r in results), [r.detail for r in results]


@pytest.mark.parametrize("task_id", list(SAMPLES))
def test_real_task_bad_sample_fails_at_least_one_check(
    task_id: str, real_tasks: list[BenchmarkTask], tmp_path: Path
) -> None:
    """AC6: a hand-written wrong answer fails at least one check."""
    # WHY: proves the checks cannot be passed by a wrong result.
    task = next(t for t in real_tasks if t.id == task_id)
    _good, (answer, files) = SAMPLES[task_id]
    _write_files(tmp_path, files)
    results = run_checks(task, answer, tmp_path)
    assert any(not r.passed for r in results)


def test_real_task_empty_answer_and_empty_workspace_fails_every_task(
    real_tasks: list[BenchmarkTask], tmp_path: Path
) -> None:
    """AC6 extra: doing nothing never passes a task."""
    # WHY: a run that wrote nothing and said nothing must not count as success.
    for task in real_tasks:
        assert any(not r.passed for r in run_checks(task, "", tmp_path)), task.id
