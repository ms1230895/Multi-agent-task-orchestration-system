"""Unit tests for `src/tools/calculator.py` (AC10-AC13)."""

import ast
import time
from pathlib import Path

import pytest

from src.tools import calculator as calculator_module
from src.tools.calculator import calculator


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3 * 4", "14"),
        ("(2 + 3) * 4", "20"),
        ("-5 + 2", "-3"),
        ("2 ** 10", "1024"),
        ("7 // 2", "3"),
        ("7 % 3", "1"),
        ("sqrt(16)", "4.0"),
        ("round(pi, 2)", "3.14"),
    ],
)
def test_calculator_valid_expression_returns_expected_text(expression: str, expected: str) -> None:
    """AC10: the spec's example expressions give the exact expected string."""
    # WHY: the executor reads this text as the answer.
    result = calculator(expression)
    assert result == {"ok": True, "output": expected, "error": None}


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("+4", "4"), ("10 / 4", "2.5"), ("max(1, 5, 3)", "5"), ("abs(-3)", "3"), ("floor(2.7)", "2")],
)
def test_calculator_other_allowed_features_work(expression: str, expected: str) -> None:
    """AC10: unary plus, division and whitelisted functions work."""
    # WHY: the whitelist must be usable, not only safe.
    assert calculator(expression)["output"] == expected


def test_calculator_surrounding_whitespace_is_ignored() -> None:
    """AC10: leading and trailing spaces do not cause a syntax error."""
    # WHY: LLMs often add whitespace.
    assert calculator("  1 + 1  ")["output"] == "2"


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os')",
        "().__class__",
        "open('x')",
        "x",
        "'a' * 3",
        "[1, 2]",
        "lambda: 1",
        "round(2.5, ndigits=1)",
        "True + 1",
        "(1).real",
        "[i for i in range(3)]",
        "1j",
    ],
)
def test_calculator_disallowed_expression_is_rejected(expression: str) -> None:
    """AC11: injection attempts and non-whitelisted syntax return ok False."""
    # WHY: Hard rule 3, the expression is untrusted LLM output.
    result = calculator(expression)
    assert result["ok"] is False
    assert result["output"] is None
    assert result["error"]


def test_calculator_import_attempt_names_not_allowed_reason() -> None:
    """AC11: the error for a blocked call says it is not allowed."""
    # WHY: the LLM reads the error to fix its next call.
    assert "not allowed" in (calculator("__import__('os')")["error"] or "")


def test_calculator_division_by_zero_returns_readable_error() -> None:
    """AC12: 1 / 0 gives 'division by zero'."""
    # WHY: a math error must be a message, not an exception.
    result = calculator("1 / 0")
    assert result["ok"] is False
    assert "division by zero" in (result["error"] or "")


@pytest.mark.parametrize("expression", ["9 ** 999", "9 ** 9 ** 9", "2 ** -101"])
def test_calculator_exponent_over_limit_is_rejected(expression: str) -> None:
    """AC12: abs(exponent) above 100 is rejected before computing."""
    # WHY: Hard rule 5, a tiny expression must not freeze the run.
    result = calculator(expression)
    assert result["ok"] is False
    assert "exponent" in (result["error"] or "")


def test_calculator_exponent_at_limit_is_allowed() -> None:
    """AC12: exponent of exactly 100 still works."""
    # WHY: the limit is 'abs(exp) <= 100', so the boundary must pass.
    assert calculator("2 ** 100") == {"ok": True, "output": str(2**100), "error": None}


def test_calculator_sqrt_of_negative_returns_math_error() -> None:
    """AC12: sqrt(-1) is ok False with a math error."""
    # WHY: math.sqrt raises ValueError; it must be converted.
    result = calculator("sqrt(-1)")
    assert result["ok"] is False
    assert "math error" in (result["error"] or "")


def test_calculator_expression_over_200_chars_is_rejected() -> None:
    """AC12: an expression of 201 characters is rejected."""
    # WHY: length cap from the spec limits abuse.
    result = calculator("1" * 201)
    assert result["ok"] is False


def test_calculator_expression_of_200_chars_is_accepted() -> None:
    """AC12: exactly 200 characters is allowed (boundary)."""
    # WHY: the limit is 'at most 200'.
    assert calculator("1" * 200)["ok"] is True


@pytest.mark.parametrize("expression", ["1e308 * 10", "(-8) ** 0.5", "2.0 ** 100 * 1e300"])
def test_calculator_overflow_or_complex_result_is_rejected(expression: str) -> None:
    """AC12: inf, nan and complex results are errors (spec section 7)."""
    # WHY: such values would poison later steps and the LLM's context.
    assert calculator(expression)["ok"] is False


@pytest.mark.parametrize("expression", ["", "1 +", "2 2", "((("])
def test_calculator_invalid_syntax_returns_invalid_expression(expression: str) -> None:
    """AC12: malformed input returns ok False, never raises."""
    # WHY: Hard rule 1.
    result = calculator(expression)
    assert result["ok"] is False
    assert result["error"]


def test_calculator_source_has_no_eval_or_exec_calls() -> None:
    """AC13: calculator.py never calls eval( or exec(."""
    # WHY: Hard rule 3. Uses the syntax tree so docstrings and names like _evaluate do not
    # cause false alarms.
    source_path = Path(calculator_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert called.isdisjoint({"eval", "exec", "compile"})


def test_calculator_round_with_huge_negative_digits_is_rejected_fast() -> None:
    """Hard rule 5: `round(1, -100000000)` is refused at once instead of hanging."""
    # WHY: Python builds 10 ** 100000000 inside round; this took over 20 s before the guard.
    started = time.perf_counter()
    result = calculator("round(1, -100000000)")
    assert time.perf_counter() - started < 1.0
    assert result == {"ok": False, "output": None, "error": "round digits out of range"}


@pytest.mark.parametrize("expression", ["round(1, 101)", "round(1, -101)", "round(1.5, 1000000)"])
def test_calculator_round_digits_beyond_limit_are_rejected(expression: str) -> None:
    """Round digits outside -100..100 are rejected, in both directions."""
    # WHY: the guard must cover positive and negative digits.
    result = calculator(expression)
    assert result["ok"] is False
    assert result["error"] == "round digits out of range"


@pytest.mark.parametrize(
    ("expression", "expected"),
    [("round(1234, -2)", "1200"), ("round(2.567, 2)", "2.57"), ("round(5.5)", "6")],
)
def test_calculator_round_with_normal_digits_still_works(expression: str, expected: str) -> None:
    """Normal use of round is unchanged by the guard."""
    # WHY: the fix must not break the common case.
    assert calculator(expression) == {"ok": True, "output": expected, "error": None}


def test_calculator_huge_integer_result_is_rejected_as_too_large() -> None:
    """Results over MAX_INT_BITS are refused with `result too large`."""
    # WHY: covers the bit-size guard; ((2**100)**11)**100 would be about 111 000 bits.
    result = calculator("((2**100)**11)**100")
    assert result == {"ok": False, "output": None, "error": "result too large"}


def test_calculator_largest_allowed_integer_can_be_shown_as_text() -> None:
    """An integer just under the bit limit converts to text without a Python int-limit error."""
    # WHY: MAX_INT_BITS must stay below Python's own 4300-digit limit for int-to-text.
    # (2**100)**100 is 2**10000: about 3000 digits, inside both limits.
    result = calculator("(2**100)**100")
    assert result["ok"] is True
    assert result["output"] == str(2**10000)
    assert calculator_module.MAX_INT_BITS < 4300 * 3.32
