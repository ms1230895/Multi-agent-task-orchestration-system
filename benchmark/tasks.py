"""The task file: Pydantic models for a task and its checks, and `load_tasks`.

Architecture layer: 7 (Benchmark). A mistake in `tasks.json` must stop the benchmark before
any LLM call is spent, so the whole file is validated when it is loaded. The checks here are
plain code (Hard rule 6): no LLM ever decides what a task expects.
"""

import json
import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import ErrorDetails

from benchmark.text import extract_numbers, normalize_words
from src.config import PROJECT_ROOT
from src.state import MAX_TASK_CHARS

TASKS_PATH = PROJECT_ROOT / "benchmark" / "tasks.json"

MAX_CHECKS = 3
MAX_SETUP_FILE_CHARS = 10_000
# WHY: the same limit as the file tools, so a benchmark path is never one the tools refuse.
MAX_PATH_CHARS = 255
MAX_PATTERN_CHARS = 500
# WHY: a broken file can produce hundreds of problems; the first few are enough to fix it.
MAX_PROBLEMS_SHOWN = 3

Category = Literal["calc", "search", "file", "multi-tool"]

# WHY: ids are short (`multi-01`), the category name is not (`multi-tool`); the prefix must
# match so an id always tells the truth about its task.
_ID_PREFIX: dict[str, str] = {
    "calc": "calc",
    "search": "search",
    "file": "file",
    "multi-tool": "multi",
}

# WHY: the same tolerance rule as the `number` checker, so a value that would pass against
# the task text is caught here (`NUMBER_REL_TOLERANCE` is also used by benchmark/checkers.py).
NUMBER_REL_TOLERANCE = 1e-9


class TasksFileError(Exception):
    """Raised when `tasks.json` is missing, unreadable, or invalid.

    The message starts with the task id (or `task #N`), so the broken entry is easy to find.
    """


def _check_relative_path(path: str) -> str:
    """Return the path if it is a safe relative path inside `workspace/`, else raise.

    WHY: the runner writes setup files to these paths and the checkers read from them, so a
    path must never leave `workspace/` (Hard rule 4), just like the file tools.
    """
    if not path.strip():
        raise ValueError("path is empty")
    if len(path) > MAX_PATH_CHARS:
        raise ValueError(f"path is longer than {MAX_PATH_CHARS} characters")
    if "\x00" in path or "\\" in path:
        raise ValueError("path contains a NUL character or a backslash")
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise ValueError("path must be relative")
    if ".." in pure.parts:
        raise ValueError("path must not contain '..'")
    return path


class _CheckBase(BaseModel):
    """Fields shared by every check: where the text to check comes from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # WHY: `None` means "check the final answer"; a path means "check that file in
    # `workspace/`" (first-run problem P4: file tasks are judged by what they wrote).
    file: str | None = None

    @field_validator("file")
    @classmethod
    def _file_is_safe(cls, value: str | None) -> str | None:
        """Reject a file path that could leave the workspace."""
        return None if value is None else _check_relative_path(value)


class NumberCheck(_CheckBase):
    """Passes when any number in the text is within `tolerance` of `value`."""

    type: Literal["number"]
    value: float = Field(allow_inf_nan=False)
    tolerance: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)


class ContainsCheck(_CheckBase):
    """Passes when every `all` keyword, one `any` keyword, and no `none` keyword is present."""

    type: Literal["contains"]
    all: list[str] = []
    any: list[str] = []
    none: list[str] = []

    @model_validator(mode="after")
    def _has_keywords(self) -> "ContainsCheck":
        """Require at least one keyword, and no keyword that is empty once normalised."""
        groups = (self.all, self.any, self.none)
        if not any(groups):
            raise ValueError("contains check needs at least one keyword in all, any or none")
        for group in groups:
            for keyword in group:
                if not normalize_words(keyword):
                    raise ValueError("a keyword is empty")
        return self


class RegexCheck(_CheckBase):
    """Passes when the pattern is found in the text (case-insensitive, multi-line)."""

    type: Literal["regex"]
    pattern: str = Field(min_length=1, max_length=MAX_PATTERN_CHARS)

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, value: str) -> str:
        """Reject a pattern that does not compile, so it fails at load time and not mid-pass."""
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"regex does not compile: {exc.msg}") from None
        return value


class ExactCheck(_CheckBase):
    """Passes when the text equals `text` after line-ending and blank-edge normalising."""

    type: Literal["exact"]
    text: str


# WHY: `type` selects the model, so an unknown check type is one clear error and not a pile
# of "does not match any option" messages.
Check = Annotated[
    NumberCheck | ContainsCheck | RegexCheck | ExactCheck,
    Field(discriminator="type"),
]


class BenchmarkTask(BaseModel):
    """One benchmark task: the text given to the agent, its input files, and its checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^(calc|search|file|multi)-\d{2}$")
    category: Category
    task: str = Field(min_length=1, max_length=MAX_TASK_CHARS)
    # WHY: file tasks must create their input files before the task (first-run problem P4);
    # a missing file made the executor guess paths and fail 42 tool calls.
    setup_files: dict[str, str] = {}
    # WHY: the task passes only if every check passes (spec 5.2).
    checks: list[Check] = Field(min_length=1, max_length=MAX_CHECKS)

    @field_validator("setup_files")
    @classmethod
    def _setup_files_are_safe(cls, value: dict[str, str]) -> dict[str, str]:
        """Reject unsafe setup paths and over-long contents."""
        for path, content in value.items():
            _check_relative_path(path)
            if len(content) > MAX_SETUP_FILE_CHARS:
                raise ValueError(f"setup file is longer than {MAX_SETUP_FILE_CHARS} characters")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "BenchmarkTask":
        """Check the id prefix, and that no expected number is given away by the task text."""
        if not self.id.startswith(_ID_PREFIX[self.category] + "-"):
            raise ValueError(f"id prefix does not match category '{self.category}'")

        # WHY: the `number` check passes when ANY number in the answer matches. If the
        # expected number were written in the task, an answer that only repeats the task
        # would pass without doing any work.
        given = extract_numbers(self.task)
        for check in self.checks:
            if not isinstance(check, NumberCheck):
                continue
            slack = check.tolerance + NUMBER_REL_TOLERANCE * max(1.0, abs(check.value))
            if any(abs(number - check.value) <= slack for number in given):
                raise ValueError("expected number appears in the task text")
        return self


def _describe(error: ErrorDetails) -> str:
    """Turn one Pydantic error into `location: message`, without the input value.

    WHY: Pydantic's own text can echo the whole input; the location and message are enough
    to find the problem.
    """
    location = ".".join(str(part) for part in error["loc"])
    message = str(error["msg"]).removeprefix("Value error, ")
    return f"{location}: {message}" if location else message


def load_tasks(path: Path = TASKS_PATH) -> list[BenchmarkTask]:
    """Read and validate the task file.

    Args:
        path: the JSON file. Default: `benchmark/tasks.json`.

    Returns:
        The tasks, in file order.

    Raises:
        TasksFileError: if the file cannot be read, is not a JSON list, holds a task that does
            not match the schema, or repeats an id. The message names the task.
    """
    # STEP 1: read and parse.
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise TasksFileError(f"cannot read tasks file: {type(exc).__name__}") from None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TasksFileError(f"tasks file is not valid JSON: {type(exc).__name__}") from None
    if not isinstance(raw, list) or not raw:
        raise TasksFileError("tasks file must be a non-empty JSON list")

    # STEP 2: validate task by task, so every message can name its task.
    tasks: list[BenchmarkTask] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        label = item["id"] if isinstance(item, dict) and isinstance(item.get("id"), str) else None
        label = label or f"task #{index}"
        try:
            task = BenchmarkTask.model_validate(item)
        except ValidationError as exc:
            problems = [_describe(error) for error in exc.errors()[:MAX_PROBLEMS_SHOWN]]
            raise TasksFileError(f"{label}: " + "; ".join(problems)) from None
        # WHY: results are stored by id, so two tasks with one id would overwrite each other.
        if task.id in seen:
            raise TasksFileError(f"{task.id}: duplicate id")
        seen.add(task.id)
        tasks.append(task)
    return tasks
