"""Shared state of one run: the `AgentState` dict that every graph node reads and writes.

Architecture layer: 5 (orchestration). Each node returns only the fields it changes, and
LangGraph overwrites those fields. There are no reducers, so every change is easy to follow.
"""

from typing import Literal, TypedDict

# WHY: the task goes into every prompt, so an empty or huge task is a caller bug that should
# fail before any LLM call is spent on it.
MAX_TASK_CHARS = 2000

Status = Literal["running", "done", "failed"]
VerdictValue = Literal["approve", "retry"]
Route = Literal["next_step", "done", "retry", "replan", "failed"]


class AgentState(TypedDict):
    """All data of one run. Field meanings match `docs/specs/architecture.md` section 2.5."""

    task: str  # the user's task, never changed
    run_id: str  # unique id, later the trace file name
    plan: list[str]  # current plan steps
    current_step: int  # index into plan, 0-based
    step_results: list[str]  # approved output of each finished step
    last_output: str  # executor output for the current step, waiting for the critic
    # WHY: the critic sees only text, so without this it rejects correct work ("no tool call
    # shown") and burns retries and replans. Real runs R2-R5 showed it.
    last_tools: str  # log of the tool calls behind `last_output`, written by the executor
    verdict: str  # "approve", "retry", or "" before the first verdict
    feedback: str  # critic's explanation, passed to the executor or the planner
    retries: int  # retries used on the current step
    replans: int  # replans used in the whole run
    final_answer: str  # set by finalize
    status: str  # a Status value
    # WHY: `advance` stores its decision here, so the edge function only reads it. After
    # `advance` moves the counters, the decision cannot be worked out again from the state.
    route: str  # a Route value, "" before the first decision
    # WHY: `run_task` catches every crash. The benchmark must still tell a usage cap or
    # connection error from an agent failure, so the exception type and message stay here.
    error: str  # "" or "<ExceptionType>: <message>", set only by `run_task`


def initial_state(task: str, run_id: str) -> AgentState:
    """Build the starting state of a run.

    Args:
        task: the user's task in plain language. Leading and trailing spaces are removed.
        run_id: unique id of this run.

    Returns:
        A state with every field set, so no node has to guess a missing key.

    Raises:
        ValueError: if the task is empty or longer than `MAX_TASK_CHARS`.
    """
    # WHY: refuse a bad task here, before any prompt is built or any LLM call is made.
    task = task.strip()
    if not task:
        raise ValueError("task must not be empty")
    if len(task) > MAX_TASK_CHARS:
        raise ValueError(f"task must be at most {MAX_TASK_CHARS} characters")

    return AgentState(
        task=task,
        run_id=run_id,
        plan=[],
        current_step=0,
        step_results=[],
        last_output="",
        last_tools="",
        verdict="",
        feedback="",
        retries=0,
        replans=0,
        final_answer="",
        status="running",
        route="",
        error="",
    )
