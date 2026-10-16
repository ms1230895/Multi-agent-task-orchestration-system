"""The LangGraph graph: router rules with hard limits, the `advance` and `finalize` nodes.

Architecture layer: 5 (orchestration). The critic gives a verdict; the plain Python in this
file decides where the run goes next. Every loop here has a hard limit (Hard rule 5).
"""

from typing import Any

from src.config import MAX_PLAN_STEPS, Settings
from src.state import AgentState, Route

# WHY: a failure message must stay short even when the last output is long.
MAX_PARTIAL_CHARS = 1000

PLANNER_FAILED_MESSAGE = "Task failed: the planner could not make a valid plan."
LIMITS_FAILED_MESSAGE = "Task failed: retry and replan limits used up."


def decide(state: AgentState, settings: Settings) -> Route:
    """Choose the next route from the state. Pure: it changes nothing and calls no LLM.

    Rules, checked in this order:
        1. status is "failed"                       -> failed
        2. approved and steps left                  -> next_step
        3. approved on the last step                -> done
        4. retries left                             -> retry
        5. replans left                             -> replan
        6. otherwise                                -> failed

    WHY: rules 4 to 6 cover every verdict that is not "approve", so an unexpected verdict
    is treated as a retry and can never count as success.
    """
    if state["status"] == "failed":
        return "failed"
    if state["verdict"] == "approve":
        if state["current_step"] < len(state["plan"]) - 1:
            return "next_step"
        return "done"
    if state["retries"] < settings.max_retries:
        return "retry"
    if state["replans"] < settings.max_replans:
        return "replan"
    return "failed"


def advance(state: AgentState, *, settings: Settings) -> dict[str, Any]:
    """Apply the router's decision: move the counters and store the route.

    Args:
        state: the state after the critic.
        settings: the limits (`max_retries`, `max_replans`).

    Returns:
        Only the changed fields, always including `route`. The input state is not changed.

    WHY: a conditional edge function should only choose a node. Doing the counter updates in
    a node keeps every state change visible to the trace (session 5).
    """
    route = decide(state, settings)

    if route == "next_step":
        # WHY: a new list, never `append`, so the caller's state is not changed in place.
        return {
            "route": route,
            "step_results": [*state["step_results"], state["last_output"]],
            "current_step": state["current_step"] + 1,
            "retries": 0,
            # WHY: an old "looks good" note must not read as advice for the next step.
            "feedback": "",
        }
    if route == "done":
        return {
            "route": route,
            "step_results": [*state["step_results"], state["last_output"]],
            "status": "done",
        }
    if route == "retry":
        # WHY: feedback is kept, so the executor sees what to fix.
        return {"route": route, "retries": state["retries"] + 1}
    if route == "replan":
        # WHY: plan, step results and feedback are kept; the planner reads them, then clears them.
        return {"route": route, "replans": state["replans"] + 1}
    return {"route": route, "status": "failed"}


def route_after_planner(state: AgentState) -> str:
    """Go to the executor, or straight to finalize when the planner gave up.

    WHY: with no plan, the executor would index into an empty list.
    """
    return "finalize" if state["status"] == "failed" else "executor"


def route_after_advance(state: AgentState) -> str:
    """Map the route that `advance` stored to the next node name.

    WHY: an unknown route goes to finalize, so a bug ends the run instead of looping.
    """
    route = state["route"]
    if route in ("next_step", "retry"):
        return "executor"
    if route == "replan":
        return "planner"
    return "finalize"


def _partial_result(state: AgentState) -> str:
    """Return the best partial result: the last approved step, else the last output."""
    text = state["step_results"][-1] if state["step_results"] else state["last_output"]
    return text[:MAX_PARTIAL_CHARS]


def finalize(state: AgentState) -> dict[str, Any]:
    """Write the final answer and make the status final.

    Returns:
        `status` and `final_answer`. A done run answers with the last step result. A failed
        run explains why, and adds the best partial result if there is one.
    """
    if state["status"] == "done":
        return {"status": "done", "final_answer": state["step_results"][-1]}

    # WHY: a status still "running" here would be a bug; ending as failed is the safe result.
    if not state["plan"]:
        return {"status": "failed", "final_answer": PLANNER_FAILED_MESSAGE}

    message = LIMITS_FAILED_MESSAGE
    partial = _partial_result(state)
    if partial:
        message += f" Best partial result: {partial}"
    return {"status": "failed", "final_answer": message}


def recursion_limit(settings: Settings) -> int:
    """Return the LangGraph `recursion_limit` computed from the other limits.

    WHY: it is a last safety net above the router's own limits. A run needs at most 3 graph
    steps (executor, critic, advance) per executor attempt, so the router always stops first.
    Hitting `GraphRecursionError` means a bug.
    """
    attempts = (settings.max_retries + 1) * (settings.max_replans + 1)
    return MAX_PLAN_STEPS * attempts * 4 + 10
