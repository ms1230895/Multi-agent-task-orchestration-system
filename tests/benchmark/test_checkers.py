"""Tests for `benchmark/checkers.py`: the 4 check types and `run_checks` (AC7-AC14).

Files for file checks live in pytest's `tmp_path`. No LLM, no network (Hard rule 8).
"""

import os
from pathlib import Path

import pytest

import benchmark.text as text_module
from benchmark.checkers import (
    MAX_CHECK_FILE_BYTES,
    CheckResult,
    extract_numbers,
    run_check,
    run_checks,
)
from benchmark.tasks import BenchmarkTask, ContainsCheck, ExactCheck, NumberCheck, RegexCheck


def _number(value: float, tolerance: float = 0.0, file: str | None = None) -> NumberCheck:
    """Build a number check."""
    return NumberCheck(type="number", value=value, tolerance=tolerance, file=file)


def _contains(
    all_: list[str] | None = None,
    any_: list[str] | None = None,
    none: list[str] | None = None,
    file: str | None = None,
) -> ContainsCheck:
    """Build a contains check."""
    return ContainsCheck(
        type="contains", all=all_ or [], any=any_ or [], none=none or [], file=file
    )


def _task(*checks: object) -> BenchmarkTask:
    """Build a task around the given checks."""
    return BenchmarkTask.model_validate(
        {
            "id": "file-01",
            "category": "file",
            "task": "Do the thing.",
            "checks": [c.model_dump() for c in checks],  # type: ignore[attr-defined]
        }
    )


# --- AC7 ---------------------------------------------------------------------------------


def test_checkers_reexports_extract_numbers_from_text_module() -> None:
    """AC7: `checkers.extract_numbers` is the same function as in `text.py`."""
    # WHY: the spec names checkers.extract_numbers, tasks.py uses the text module copy.
    assert extract_numbers is text_module.extract_numbers


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("29,031.69 ft", [29031.69]),
        ("1,048,576", [1048576]),
        ("1.798754748e10", [17987547480]),
        ("2026-10-16", [2026, 10, 16]),
        ("-5 degrees", [-5]),
        ("1,2,3", [1, 2, 3]),
        ("no numbers", []),
    ],
)
def test_extract_numbers_spec_examples_give_expected_values(
    text: str, expected: list[float]
) -> None:
    """AC7: the examples of the spec, through the checkers module."""
    # WHY: the number check depends on exactly these readings.
    assert extract_numbers(text) == pytest.approx(expected)


def test_extract_numbers_narrow_no_break_space_joins_groups_into_8848000() -> None:
    """AC7: groups joined by U+202F read as the single number 8848000."""
    assert extract_numbers("8\u202f848\u202f000") == [8848000]


def test_extract_numbers_spec_example_contains_848000() -> None:
    """AC7: the spec example `8 848<U+202F>000` contains 848000."""
    # WHY: here only the second separator is U+202F; the first is a normal space, which is not
    # a thousands separator, so "8" is its own number and "848<U+202F>000" is 848000.
    assert 848000 in extract_numbers("8 848\u202f000")


def test_number_check_thousands_separator_answer_passes(tmp_path: Path) -> None:
    """AC8: `The answer is 1,415.` passes value 1415."""
    # WHY: first-run problem P5.
    assert run_check(_number(1415), "The answer is 1,415.", tmp_path).passed


def test_number_check_within_tolerance_passes(tmp_path: Path) -> None:
    """AC8: 7401.2214 passes value 7401.22 with tolerance 0.01."""
    # WHY: rounding differences must not fail a correct answer.
    assert run_check(_number(7401.22, 0.01), "7401.2214", tmp_path).passed


def test_number_check_outside_tolerance_fails(tmp_path: Path) -> None:
    """AC8: 7401.24 fails the same check."""
    # WHY: the tolerance must be a real limit.
    assert not run_check(_number(7401.22, 0.01), "7401.24", tmp_path).passed


def test_number_check_no_number_fails_and_detail_says_none(tmp_path: Path) -> None:
    """AC8: `no idea` fails."""
    # WHY: a text answer cannot satisfy a number check.
    result = run_check(_number(1415), "no idea", tmp_path)
    assert not result.passed
    assert "none" in result.detail


def test_number_check_failure_detail_lists_found_numbers(tmp_path: Path) -> None:
    """AC8: the failure detail lists the numbers found (first 5 only)."""
    # WHY: Gaurav reads this detail to see what the agent answered.
    result = run_check(_number(99), "1 2 3 4 5 6 7", tmp_path)
    assert not result.passed
    assert "found: 1, 2, 3, 4, 5" in result.detail
    assert "6" not in result.detail.split("found:")[1]


def test_number_check_matches_any_number_in_the_text(tmp_path: Path) -> None:
    """AC8: it passes when any one number matches."""
    # WHY: documents the accepted weak spot of the spec.
    assert run_check(_number(84), "sqrt of 7056 is 84, not 83", tmp_path).passed


def test_number_check_negative_value_passes(tmp_path: Path) -> None:
    """AC8: a negative expected value matches `-5`."""
    # WHY: the sign handling must reach the checker.
    assert run_check(_number(-5), "It is -5 degrees", tmp_path).passed


# --- AC9 ---------------------------------------------------------------------------------


def test_contains_check_is_case_and_accent_insensitive(tmp_path: Path) -> None:
    """AC9: `Gabriel García Márquez` passes all ["Marquez"]."""
    # WHY: the model writes the author's name with accents.
    assert run_check(_contains(["Marquez"]), "Gabriel García Márquez", tmp_path).passed
    assert run_check(_contains(["MARQUEZ"]), "gabriel garcia marquez", tmp_path).passed


def test_contains_check_matches_whole_words_only(tmp_path: Path) -> None:
    """AC9: `Mozart` fails all ["art"]."""
    # WHY: a keyword inside a longer word is not a match.
    result = run_check(_contains(["art"]), "Mozart", tmp_path)
    assert not result.passed
    assert "art" in result.detail


def test_contains_check_all_keywords_must_be_present(tmp_path: Path) -> None:
    """AC9: a missing `all` keyword fails and is named."""
    # WHY: both Neil and Armstrong are required.
    result = run_check(_contains(["Neil", "Armstrong"]), "Neil only", tmp_path)
    assert not result.passed
    assert "Armstrong" in result.detail


def test_contains_check_any_passes_when_one_keyword_present(tmp_path: Path) -> None:
    """AC9: `any` passes with one keyword, fails with none."""
    # WHY: `any` is an OR list.
    assert run_check(_contains(any_=["red", "blue"]), "It is blue.", tmp_path).passed
    assert not run_check(_contains(any_=["red", "blue"]), "It is green.", tmp_path).passed


def test_contains_check_none_keyword_present_fails(tmp_path: Path) -> None:
    """AC9: a forbidden keyword fails the check."""
    # WHY: `none` guards against hedged or wrong answers.
    check = _contains(["Tokyo"], none=["Kyoto"])
    assert run_check(check, "Tokyo", tmp_path).passed
    result = run_check(check, "Tokyo, not Kyoto", tmp_path)
    assert not result.passed
    assert "Kyoto" in result.detail


# --- AC10 --------------------------------------------------------------------------------


def test_regex_check_matches_case_insensitively(tmp_path: Path) -> None:
    """AC10: the pattern matches ignoring case."""
    # WHY: spec: re.IGNORECASE.
    assert run_check(
        RegexCheck(type="regex", pattern=r"^port\s*=\s*\d+"), "PORT = 80", tmp_path
    ).passed


def test_regex_check_uses_multiline_anchors(tmp_path: Path) -> None:
    """AC10: `^` matches at the start of any line."""
    # WHY: spec: re.MULTILINE.
    check = RegexCheck(type="regex", pattern=r"^beta$")
    assert run_check(check, "alpha\nbeta\ngamma", tmp_path).passed


def test_regex_check_no_match_fails_with_detail(tmp_path: Path) -> None:
    """AC10: no match fails with `pattern not found`."""
    # WHY: the detail is what the result line shows.
    result = run_check(RegexCheck(type="regex", pattern="zzz"), "abc", tmp_path)
    assert not result.passed
    assert result.detail == "pattern not found"


# --- AC11 --------------------------------------------------------------------------------


def test_exact_check_ignores_line_endings_and_blank_edges(tmp_path: Path) -> None:
    """AC11: `eggs\\r\\nmilk  \\nbread\\n\\n` equals `eggs\\nmilk\\nbread`."""
    # WHY: a file ending with a newline holds the same text.
    check = ExactCheck(type="exact", text="eggs\nmilk\nbread")
    assert run_check(check, "eggs\r\nmilk  \nbread\n\n", tmp_path).passed


def test_exact_check_added_quotes_fail(tmp_path: Path) -> None:
    """AC11: `'benchmark ok'` does not equal `benchmark ok`."""
    # WHY: added quotes are a real difference (file-01).
    check = ExactCheck(type="exact", text="benchmark ok")
    result = run_check(check, "'benchmark ok'", tmp_path)
    assert not result.passed
    assert "expected" in result.detail


def test_exact_check_is_case_sensitive(tmp_path: Path) -> None:
    """AC11: `Benchmark ok` does not equal `benchmark ok`."""
    # WHY: file-04 requires uppercase.
    assert not run_check(
        ExactCheck(type="exact", text="benchmark ok"), "Benchmark ok", tmp_path
    ).passed


def test_exact_check_detail_snippet_is_short(tmp_path: Path) -> None:
    """AC11/AC14: the detail holds at most a short snippet of a long text."""
    # WHY: result lines must stay small.
    result = run_check(ExactCheck(type="exact", text="a"), "b" * 5000, tmp_path)
    assert not result.passed
    assert len(result.detail) <= 200
    assert "b" * 101 not in result.detail


# --- AC12 --------------------------------------------------------------------------------


def test_file_check_reads_the_workspace_file(tmp_path: Path) -> None:
    """AC12: a check with `file` reads `workspace/<file>` and ignores the answer."""
    # WHY: file tasks are judged by what they wrote (first-run problem P4).
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "n.txt").write_text("the value is 111", encoding="utf-8")
    result = run_check(_number(111, file="sub/n.txt"), "answer without numbers", tmp_path)
    assert result.passed
    assert result.source == "file:sub/n.txt"


def test_file_check_missing_file_fails_without_raising(tmp_path: Path) -> None:
    """AC12: a missing file fails with detail `file not found`."""
    # WHY: a task that never wrote its file is a normal failure, not a crash.
    result = run_check(_number(1, file="nope.txt"), "1", tmp_path)
    assert not result.passed
    assert result.detail.startswith("file not found")


def test_file_check_folder_instead_of_file_fails_not_found(tmp_path: Path) -> None:
    """AC12: a folder with the file's name is not a file."""
    # WHY: reading a directory must not raise.
    (tmp_path / "dir.txt").mkdir()
    result = run_check(_number(1, file="dir.txt"), "1", tmp_path)
    assert result.detail.startswith("file not found")


def test_file_check_file_over_100000_bytes_fails_too_large(tmp_path: Path) -> None:
    """AC12: a file over 100 000 bytes fails with `file too large`."""
    # WHY: the same limit as the file tools, and no huge read.
    (tmp_path / "big.txt").write_bytes(b"1" * (MAX_CHECK_FILE_BYTES + 1))
    result = run_check(_number(1, file="big.txt"), "", tmp_path)
    assert not result.passed
    assert result.detail.startswith("file too large")


def test_file_check_file_of_exactly_100000_bytes_is_read(tmp_path: Path) -> None:
    """AC12: the limit itself is allowed."""
    # WHY: boundary of the size rule.
    (tmp_path / "edge.txt").write_bytes(b"a" * MAX_CHECK_FILE_BYTES)
    check = RegexCheck(type="regex", pattern="^a+$", file="edge.txt")
    assert run_check(check, "", tmp_path).passed


def test_file_check_invalid_utf8_fails_unreadable(tmp_path: Path) -> None:
    """AC12: a non-UTF-8 file fails with `file unreadable` and does not raise."""
    # WHY: the spec says an unreadable file fails, never raises.
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00\x80")
    result = run_check(_number(1, file="bin.dat"), "", tmp_path)
    assert not result.passed
    assert result.detail.startswith("file unreadable")


# --- AC13 --------------------------------------------------------------------------------


def test_file_check_symlink_to_file_outside_workspace_fails(tmp_path: Path) -> None:
    """AC13: a link inside the workspace that points outside is never read."""
    # WHY: Hard rule 4, the checker must not read outside the workspace.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("42", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    os.symlink(outside / "secret.txt", workspace / "link.txt")
    result = run_check(_number(42, file="link.txt"), "42", workspace)
    assert not result.passed
    assert result.detail.startswith("file not allowed")


def test_file_check_file_inside_symlinked_folder_outside_workspace_fails(tmp_path: Path) -> None:
    """AC13: a link to an outside folder cannot be used to reach files in it."""
    # WHY: folder links are the other way to escape.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("42", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    os.symlink(outside, workspace / "dirlink", target_is_directory=True)
    result = run_check(_number(42, file="dirlink/secret.txt"), "42", workspace)
    assert not result.passed


def test_file_check_symlink_to_file_inside_workspace_is_allowed(tmp_path: Path) -> None:
    """AC13 control: a link that stays inside the workspace is fine."""
    # WHY: only escapes are refused.
    (tmp_path / "real.txt").write_text("42", encoding="utf-8")
    os.symlink(tmp_path / "real.txt", tmp_path / "alias.txt")
    assert run_check(_number(42, file="alias.txt"), "", tmp_path).passed


# --- AC14 --------------------------------------------------------------------------------


def test_run_checks_returns_one_result_per_check_in_order_after_a_failure(tmp_path: Path) -> None:
    """AC14: all checks run, in order, even after a failed one."""
    # WHY: the result line must show every check.
    (tmp_path / "out.txt").write_text("hello", encoding="utf-8")
    task = _task(
        _number(1),
        _contains(["hello"], file="out.txt"),
        ExactCheck(type="exact", text="nope", file="out.txt"),
    )
    results = run_checks(task, "no numbers here", tmp_path)
    assert [r.type for r in results] == ["number", "contains", "exact"]
    assert [r.passed for r in results] == [False, True, False]
    assert [r.source for r in results] == ["answer", "file:out.txt", "file:out.txt"]
    assert all(isinstance(r, CheckResult) for r in results)


def test_run_checks_detail_is_at_most_200_chars(tmp_path: Path) -> None:
    """AC14: a very long keyword or path gives a detail of at most 200 chars."""
    # WHY: result lines must stay small.
    results = run_checks(_task(_contains(["k" * 600])), "text", tmp_path)
    assert not results[0].passed
    assert len(results[0].detail) <= 200
    missing = run_check(_number(1, file="f" * 250), "", tmp_path)
    assert len(missing.detail) <= 200


def test_run_check_is_deterministic(tmp_path: Path) -> None:
    """AC14: the same inputs give the same result."""
    # WHY: Hard rule 6, checkers are code, not a judge.
    check = _number(5)
    assert run_check(check, "x 4", tmp_path) == run_check(check, "x 4", tmp_path)
