"""The LangGraph graph: router rules with hard limits, the `advance` and `finalize` nodes.

Architecture layer: 5 (orchestration). The critic gives a verdict; the plain Python in this
file decides where the run goes next. Every loop here has a hard limit (Hard rule 5).
"""

from collections.abc import Callable
from functools import partial
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.critic import run_critic
from src.agents.executor import run_executor
from src.agents.planner import run_planner
from src.config import MAX_PLAN_STEPS, Settings, get_llm, get_settings
from src.logger import MAX_TRACE_TEXT_CHARS, EventCallback, emit_safely, trim
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


def advance(
    state: AgentState, *, settings: Settings, on_event: EventCallback | None = None
) -> dict[str, Any]:
    """Apply the router's decision: move the counters and store the route.

    Args:
        state: the state after the critic.
        settings: the limits (`max_retries`, `max_replans`).
        on_event: optional callback `(event, data)`. Only the `retry` route emits an event
            (`retry` with kind `step`); the other routes are visible in the next events.

    Returns:
        Only the changed fields, always including `route`. The input state is not changed.

    WHY: a conditional edge function should only choose a node. Doing the counter updates in
    a node keeps every state change visible to the trace.
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
        # WHY: the trace must show that a step was sent back, and why. `attempt` is the new
        # `retries` value, so the first retry is 1.
        emit_safely(
            on_event,
            "retry",
            {
                "kind": "step",
                "step": state["current_step"],
                "attempt": state["retries"] + 1,
                "reason": trim(state["feedback"], MAX_TRACE_TEXT_CHARS),
            },
        )
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


def build_graph(
    *,
    llm: BaseChatModel | None = None,
    settings: Settings | None = None,
    executor: Callable[[AgentState], dict[str, Any]] | None = None,
    on_event: EventCallback | None = None,
) -> CompiledStateGraph:
    """Build and compile the graph. Building makes no LLM call.

    Args:
        llm: the chat model for planner and critic. Default: `get_llm(settings)`.
        settings: limits and flags. Default: `get_settings()`.
        executor: the executor node. Default: `run_executor` with the shared `llm`.
        on_event: optional callback `(event, data)` given to the planner, critic, `advance`
            and the default executor. A passed-in `executor` is used as it is.

    Returns:
        A compiled graph. Invoke it with
        `config={"recursion_limit": recursion_limit(settings)}`.

    WHY: planner and critic share one `llm`, so one run always uses one model.
    """
    settings = settings or get_settings()
    llm = llm or get_llm(settings)
    # WHY: `is not None`, not `or`, so a callable executor is never replaced by truthiness.
    if executor is None:
        executor = partial(run_executor, llm=llm, settings=settings, on_event=on_event)

    graph = StateGraph(AgentState)

    # WHY: `partial` binds the llm and settings now, so each node is a plain `state -> dict`.
    graph.add_node("planner", partial(run_planner, llm=llm, on_event=on_event))
    graph.add_node("executor", executor)
    graph.add_node("critic", partial(run_critic, llm=llm, on_event=on_event))
    graph.add_node("advance", partial(advance, settings=settings, on_event=on_event))
    graph.add_node("finalize", finalize)

    graph.add_edge(START, "planner")
    # WHY: explicit path maps make the possible routes visible, and let LangGraph draw and
    # check the graph.
    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {"executor": "executor", "finalize": "finalize"},
    )
    graph.add_edge("executor", "critic")
    graph.add_edge("critic", "advance")
    graph.add_conditional_edges(
        "advance",
        route_after_advance,
        {"executor": "executor", "planner": "planner", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)

    return graph.compile()
