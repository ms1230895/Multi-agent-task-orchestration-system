"""Checkers: plain code that decides whether an answer or a file is right.

Architecture layer: 7 (Benchmark). Never an LLM judge (Hard rule 6): the same inputs always
give the same result. No check raises, so one bad file can never stop a pass.
"""

import math
import re
from pathlib import Path

from pydantic import BaseModel

from benchmark.tasks import (
    NUMBER_REL_TOLERANCE,
    BenchmarkTask,
    Check,
    ContainsCheck,
    ExactCheck,
    NumberCheck,
    RegexCheck,
)
from benchmark.text import contains_phrase, extract_numbers, normalize_lines
from src.logger import trim

# WHY: re-exported because the spec names `checkers.extract_numbers`; it lives in `text.py`
# so `tasks.py` can use it without importing this file.
__all__ = ["CheckResult", "extract_numbers", "run_check", "run_checks"]

MAX_DETAIL_CHARS = 200
MAX_SNIPPET_CHARS = 100
MAX_NUMBERS_SHOWN = 5
# WHY: the same size limit as the file tools, so any file the agent could write can be read.
MAX_CHECK_FILE_BYTES = 100_000


class CheckResult(BaseModel):
    """The outcome of one check, as stored in a result line."""

    type: str
    source: str  # "answer" or "file:<path>"
    passed: bool
    detail: str


def _snippet(text: str, limit: int = MAX_SNIPPET_CHARS) -> str:
    """Return a one-line, short piece of the text for a detail message."""
    return trim(" ".join(text.split()), limit)


def _format_number(value: float) -> str:
    """Write a number without a pointless `.0` or an exponent for whole numbers."""
    if math.isfinite(value) and value == int(value):
        return str(int(value))
    return repr(value)


def _read_source(check: Check, answer: str, workspace: Path) -> tuple[str | None, str, str]:
    """Get the text a check looks at.

    Returns:
        `(text, source, error_detail)`. `text` is None when a file cannot be used, and then
        `error_detail` says why. Never raises.
    """
    if check.file is None:
        return answer, "answer", ""

    source = f"file:{check.file}"
    root = workspace.resolve()
    # WHY: `resolve()` follows symlinks, so a link inside the workspace that points outside
    # is caught here and never read (Hard rule 4).
    try:
        target = (root / check.file).resolve()
    except (OSError, RuntimeError, ValueError):
        return None, source, f"file unreadable: {check.file}"
    if not target.is_relative_to(root):
        return None, source, f"file not allowed: {check.file}"
    if not target.is_file():
        return None, source, f"file not found: {check.file}"
    try:
        # WHY: read at most one byte more than the limit, so a huge file is never loaded
        # whole and the size test cannot be fooled by a file that grows after a `stat`.
        with target.open("rb") as handle:
            data = handle.read(MAX_CHECK_FILE_BYTES + 1)
    except OSError:
        return None, source, f"file unreadable: {check.file}"
    if len(data) > MAX_CHECK_FILE_BYTES:
        return None, source, f"file too large: {check.file}"
    try:
        return data.decode("utf-8"), source, ""
    except UnicodeDecodeError:
        return None, source, f"file unreadable: {check.file}"


def _check_number(check: NumberCheck, text: str) -> tuple[bool, str]:
    """Pass when any number in the text is within the tolerance of the expected value."""
    found = extract_numbers(text)
    slack = check.tolerance + NUMBER_REL_TOLERANCE * max(1.0, abs(check.value))
    for number in found:
        if abs(number - check.value) <= slack:
            return True, f"found {_format_number(number)}"
    shown = ", ".join(_format_number(n) for n in found[:MAX_NUMBERS_SHOWN]) or "none"
    return False, (
        f"no number within {_format_number(check.tolerance)} of "
        f"{_format_number(check.value)}; found: {shown}"
    )


def _check_contains(check: ContainsCheck, text: str) -> tuple[bool, str]:
    """Pass when all `all` keywords, one `any` keyword and no `none` keyword are present."""
    for keyword in check.all:
        if not contains_phrase(text, keyword):
            return False, f"missing keyword: {keyword}"
    if check.any and not any(contains_phrase(text, keyword) for keyword in check.any):
        return False, "none of the any keywords found"
    for keyword in check.none:
        if contains_phrase(text, keyword):
            return False, f"forbidden keyword present: {keyword}"
    return True, "keywords ok"


def _check_regex(check: RegexCheck, text: str) -> tuple[bool, str]:
    """Pass when the pattern is found, ignoring case, with `^` and `$` per line."""
    if re.search(check.pattern, text, re.IGNORECASE | re.MULTILINE):
        return True, "pattern found"
    return False, "pattern not found"


def _check_exact(check: ExactCheck, text: str) -> tuple[bool, str]:
    """Pass when the text equals the expected text after line-ending normalising."""
    expected = normalize_lines(check.text)
    actual = normalize_lines(text)
    if actual == expected:
        return True, "text matches"
    return False, f"expected {trim(expected, 60)!r}, got {_snippet(actual)!r}"


def run_check(check: Check, answer: str, workspace: Path) -> CheckResult:
    """Run one check.

    Args:
        check: the check from the task.
        answer: the run's final answer.
        workspace: the folder that file checks read from.

    Returns:
        A `CheckResult`. Never raises: a missing file or an odd input is a failed check.
    """
    text, source, error_detail = _read_source(check, answer, workspace)
    if text is None:
        return CheckResult(
            type=check.type,
            source=source,
            passed=False,
            detail=trim(error_detail, MAX_DETAIL_CHARS),
        )
    try:
        if isinstance(check, NumberCheck):
            passed, detail = _check_number(check, text)
        elif isinstance(check, ContainsCheck):
            passed, detail = _check_contains(check, text)
        elif isinstance(check, RegexCheck):
            passed, detail = _check_regex(check, text)
        else:
            passed, detail = _check_exact(check, text)
    except Exception as exc:  # noqa: BLE001 - a checker bug must be one failed check, not a crash
        # WHY: a crash here would stop the whole pass after its LLM calls were already paid.
        passed, detail = False, f"check error: {type(exc).__name__}"
    return CheckResult(
        type=check.type, source=source, passed=passed, detail=trim(detail, MAX_DETAIL_CHARS)
    )


def run_checks(task: BenchmarkTask, answer: str, workspace: Path) -> list[CheckResult]:
    """Run every check of a task, in order.

    WHY: it does not stop at the first failure, so the result line shows all of them.
    """
    return [run_check(check, answer, workspace) for check in task.checks]
