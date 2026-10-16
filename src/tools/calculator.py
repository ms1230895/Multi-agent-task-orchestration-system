"""Calculator tool: safe arithmetic by walking the `ast` tree.

Architecture layer: 2 (tools).

WARNING: never pass the expression to Python's built-in code runners (Hard rule 3). The
LLM writes the expression, so it is untrusted input. We parse it into a tree and
evaluate only node types from an allow-list; everything else is rejected.
"""

import ast
import math
import operator
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.tools.base import ToolResult, failure, success

MAX_EXPRESSION_CHARS = 200
MAX_EXPONENT = 100
# WHY: Python ints grow without limit; a tiny expression could otherwise build a number
# with millions of digits and freeze the run (Hard rule 5).
MAX_INT_BITS = 100_000

_BINARY_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}
_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "floor": math.floor,
    "ceil": math.ceil,
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
}


class CalculatorArgs(BaseModel):
    """Input schema: one expression string."""

    model_config = ConfigDict(extra="forbid")

    expression: str = Field(min_length=1, max_length=MAX_EXPRESSION_CHARS)


class _CalcError(Exception):
    """Internal: a readable reason an expression was rejected. Never leaves this file."""


def _check_number(value: Any) -> Any:
    """Reject results that are not a usable real number.

    Args:
        value: the result of one operation.

    Returns:
        The same value, if it is fine.

    WHY: Python returns a complex number for (-8) ** 0.5 and inf for some overflows.
    Catching them after every step stops them from spreading into later steps.
    """
    if isinstance(value, complex):
        raise _CalcError("result is not a real number")
    if isinstance(value, float) and not math.isfinite(value):
        raise _CalcError("result is too large or undefined")
    if isinstance(value, int) and value.bit_length() > MAX_INT_BITS:
        raise _CalcError("result too large")
    return value


def _evaluate(node: ast.AST) -> Any:
    """Evaluate one node of the parsed expression, allowing only safe node types.

    Args:
        node: a node from `ast.parse`.

    Returns:
        An int or float.

    Raises:
        _CalcError: for any node type that is not on the allow-list.
    """
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)

    if isinstance(node, ast.Constant):
        # WHY: `type(...) in` rather than `isinstance` so True/False (a bool is an int
        # in Python), strings, bytes and complex numbers are all rejected.
        if type(node.value) in (int, float):
            return _check_number(node.value)
        raise _CalcError("not allowed: Constant")

    if isinstance(node, ast.BinOp):
        binary = _BINARY_OPS.get(type(node.op))
        if binary is None:
            raise _CalcError(f"not allowed: {type(node.op).__name__}")
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            # WHY: check the size before computing; `9 ** 9 ** 9` would otherwise run first.
            if abs(right) > MAX_EXPONENT:
                raise _CalcError("exponent too large")
            if isinstance(left, int) and isinstance(right, int) and right > 0:
                if left.bit_length() * right > MAX_INT_BITS:
                    raise _CalcError("result too large")
        return _check_number(binary(left, right))

    if isinstance(node, ast.UnaryOp):
        unary = _UNARY_OPS.get(type(node.op))
        if unary is None:
            raise _CalcError(f"not allowed: {type(node.op).__name__}")
        return _check_number(unary(_evaluate(node.operand)))

    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        raise _CalcError(f"unknown name: {node.id}")

    if isinstance(node, ast.Call):
        # WHY: only a plain function name from the allow-list; no attribute access such
        # as `().__class__`, and no keyword arguments, so nothing unexpected is reachable.
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise _CalcError("not allowed: function call")
        if node.keywords:
            raise _CalcError("keyword arguments are not allowed")
        args = [_evaluate(arg) for arg in node.args]
        return _check_number(_FUNCTIONS[node.func.id](*args))

    raise _CalcError(f"not allowed: {type(node).__name__}")


def calculator(expression: str) -> ToolResult:
    """Evaluate an arithmetic expression safely.

    Args:
        expression: for example `"(2 + 3) * 4"` or `"sqrt(16)"`.

    Returns:
        `ok=True` with the result as text, or `ok=False` with a readable error.
        Never raises (Hard rule 1).
    """
    try:
        # WHY: with validation switched off, the gate passes raw arguments through.
        if not isinstance(expression, str):
            return failure("expression must be a string")
        if not 0 < len(expression) <= MAX_EXPRESSION_CHARS:
            return failure(f"expression must be 1 to {MAX_EXPRESSION_CHARS} characters")

        try:
            tree = ast.parse(expression.strip(), mode="eval")
        except (SyntaxError, ValueError):
            return failure("invalid expression")

        return success(str(_evaluate(tree)))
    except _CalcError as exc:
        return failure(str(exc))
    except ZeroDivisionError:
        return failure("division by zero")
    except OverflowError:
        return failure("result too large")
    except ValueError as exc:
        return failure(f"math error: {exc}")
    except TypeError:
        return failure("invalid arguments for function")
    except Exception as exc:  # WHY: last safety net, so no input can make a tool raise
        return failure(f"{type(exc).__name__}: {exc}")
