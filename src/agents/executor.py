"""Executor stub: returns a fixed output so the graph can run end to end without tools.

Architecture layer: 4 (agents).
"""

from typing import Any

from src.state import AgentState


# TODO: session 4 replaces this stub with the real executor (LLM + tool loop through `call_tool`).
def stub_executor(state: AgentState) -> dict[str, Any]:
    """Return a fixed output for the current step.

    Args:
        state: the current run state. Only `plan` and `current_step` are read.

    Returns:
        `last_output`, the only field the executor changes.

    WHY: this lets us test every route of the graph (next step, retry, replan, failed) with
    no LLM and no tools in the executor, before the real executor makes runs slow and costly.
    """
    number = state["current_step"] + 1
    step = state["plan"][state["current_step"]]
    return {"last_output": f"stub output for step {number}: {step}"}
