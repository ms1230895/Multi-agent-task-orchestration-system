"""Tests for `benchmark/text.py`: number extraction and the text normalisers (AC7, AC9, AC11).

Pure functions: no files, no network, no LLM (Hard rule 8).
"""

import pytest

from benchmark.text import contains_phrase, extract_numbers, normalize_lines, normalize_words


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
        ("", []),
    ],
)
def test_extract_numbers_spec_examples_give_expected_values(
    text: str, expected: list[float]
) -> None:
    """AC7: the examples from the spec."""
    # WHY: these are the exact cases (first-run problem P5 among them) the spec fixes.
    assert extract_numbers(text) == pytest.approx(expected)


@pytest.mark.parametrize("separator", ["\u00a0", "\u202f", "\u2009"])
def test_extract_numbers_space_separators_read_as_one_number(separator: str) -> None:
    """AC7: no-break, narrow no-break and thin spaces are thousands separators."""
    # WHY: the model sometimes writes "8 848 000" with a special space.
    text = f"8{separator}848{separator}000"
    assert extract_numbers(text) == [8848000]


def test_extract_numbers_plain_space_does_not_join_numbers() -> None:
    """AC7: a normal space is not a thousands separator."""
    # WHY: "12 345" in running text is two numbers, not twelve thousand.
    assert extract_numbers("12 345") == [12, 345]


def test_extract_numbers_decimal_comma_is_split_documented_limit() -> None:
    """AC7: decimal commas are a documented limit: `8,45` is read as two numbers."""
    # WHY: pins the documented limit so a change in behaviour is noticed.
    assert extract_numbers("8,45") == [8, 45]


def test_extract_numbers_four_digit_group_after_comma_is_not_thousands() -> None:
    """AC7: `1,0001` must not read as 1000 followed by 1."""
    # WHY: groups after a separator must be exactly 3 digits.
    assert extract_numbers("1,0001") == [1, 1]


def test_extract_numbers_markdown_stars_are_ignored() -> None:
    """AC7: `**1415**` still gives 1415 (spec section 7)."""
    # WHY: models bold their answers.
    assert extract_numbers("The answer is **1415**.") == [1415]


def test_extract_numbers_minus_after_letter_is_not_a_sign() -> None:
    """AC7: a minus right after a letter or digit is a dash, not a sign."""
    # WHY: "x-5" and "3-4" must not produce negative numbers.
    assert extract_numbers("x-5 and 3-4") == [5, 3, 4]


def test_extract_numbers_decimals_and_signed_exponent() -> None:
    """AC7: decimals and signed exponents parse."""
    # WHY: "0.25" must not lose the integer part, and "1.5E+3" is 1500.
    assert extract_numbers("0.25 and 1.5E+3") == pytest.approx([0.25, 1500])


def test_normalize_words_removes_accents_case_and_extra_whitespace() -> None:
    """AC9: normalisation folds case, drops accents and collapses whitespace."""
    # WHY: "García   Márquez" must match the keyword "Marquez".
    assert normalize_words("  García \n  MÁRQUEZ ") == "garcia marquez"


@pytest.mark.parametrize(
    ("text", "phrase", "expected"),
    [
        ("Gabriel García Márquez", "Marquez", True),
        ("Mozart", "art", False),
        ("The art of war", "art", True),
        ("Neil\nArmstrong", "neil armstrong", True),
        ("Born in 1979", "79", False),
        ("anything", "", False),
        ("Tokyo.", "Tokyo", True),
    ],
)
def test_contains_phrase_matches_whole_words_only(text: str, phrase: str, expected: bool) -> None:
    """AC9: whole-word, accent-insensitive matching."""
    # WHY: "art" must not match "Mozart", and punctuation around a word is fine.
    assert contains_phrase(text, phrase) is expected


def test_normalize_lines_unifies_endings_and_trims_edges() -> None:
    """AC11: CRLF, trailing spaces and blank edge lines are not real differences."""
    # WHY: a file ending with a newline holds the same text as one that does not.
    assert normalize_lines("\n\neggs\r\nmilk  \rbread\n\n") == "eggs\nmilk\nbread"


def test_normalize_lines_keeps_case_quotes_and_leading_spaces() -> None:
    """AC11: capitals, quotes and leading spaces are real differences."""
    # WHY: `exact` is case-sensitive and must see added quotes.
    assert normalize_lines("  'Benchmark ok'\n") == "  'Benchmark ok'"
