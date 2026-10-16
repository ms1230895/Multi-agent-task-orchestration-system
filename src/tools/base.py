"""Shared result type and helpers for every tool.

Architecture layer: 2 (tools). All four tools and the validation gate use this one
return format, so the layers above never have to guess what a tool returns.
"""

from typing import TypedDict

# WHY: a tool marks a failure as worth retrying by starting its error with this prefix.
# That keeps the return format exactly {ok, output, error} (Hard rule 1).
TRANSIENT_PREFIX = "transient: "


class ToolResult(TypedDict):
    """What every tool returns. Tools never raise (Hard rule 1)."""

    ok: bool
    output: str | None  # set when ok is True
    error: str | None  # set when ok is False


def success(output: str) -> ToolResult:
    """Build a successful result.

    Args:
        output: the text the tool produced.

    Returns:
        `{"ok": True, "output": output, "error": None}`.
    """
    return {"ok": True, "output": output, "error": None}


def failure(error: str) -> ToolResult:
    """Build a failed result.

    Args:
        error: a short readable reason; the LLM may read it and fix its call.

    Returns:
        `{"ok": False, "output": None, "error": error}`.
    """
    return {"ok": False, "output": None, "error": error}


def is_transient(result: ToolResult) -> bool:
    """Say whether a failed result is worth retrying.

    Args:
        result: a tool result.

    Returns:
        True only for a failure whose error starts with `TRANSIENT_PREFIX`.

    WHY: the gate retries only temporary problems such as a timeout. Retrying a bad
    argument or a blocked path would fail the same way again.
    """
    error = result["error"]
    return not result["ok"] and error is not None and error.startswith(TRANSIENT_PREFIX)
