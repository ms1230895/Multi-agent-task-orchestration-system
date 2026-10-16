"""Tests for `src/state.py`: starting state, task validation, field list (AC1-AC3)."""

import pytest

from src.state import MAX_TASK_CHARS, AgentState, initial_state

EXPECTED_FIELDS = {
    "task",
    "run_id",
    "plan",
    "current_step",
    "step_results",
    "last_output",
    "verdict",
    "feedback",
    "retries",
    "replans",
    "final_answer",
    "status",
    "route",
    "error",
}


def test_ac1_initial_state_returns_all_fields_with_start_values() -> None:
    # WHY: a missing key would crash a node later; wrong start values break the router.
    state = initial_state("t", "r1")
    assert state == {
        "task": "t",
        "run_id": "r1",
        "plan": [],
        "current_step": 0,
        "step_results": [],
        "last_output": "",
        "verdict": "",
        "feedback": "",
        "retries": 0,
        "replans": 0,
        "final_answer": "",
        "status": "running",
        "route": "",
        "error": "",
    }
    assert len(state) == 14


@pytest.mark.parametrize("task", ["", "   ", "x" * (MAX_TASK_CHARS + 1)])
def test_ac2_initial_state_bad_task_raises_value_error(task: str) -> None:
    # WHY: a bad task must fail before any LLM call is spent.
    with pytest.raises(ValueError):
        initial_state(task, "r1")


def test_ac2_initial_state_strips_task() -> None:
    assert initial_state("  t  ", "r1")["task"] == "t"


def test_ac2_initial_state_accepts_task_at_max_length() -> None:
    # WHY: protects the boundary (2000 is allowed, 2001 is not).
    assert len(initial_state("x" * MAX_TASK_CHARS, "r1")["task"]) == MAX_TASK_CHARS


def test_ac3_agent_state_annotations_match_spec_fields() -> None:
    # WHY: the field list is documented in the spec and CLAUDE.md; drift must be noticed.
    assert set(AgentState.__annotations__) == EXPECTED_FIELDS
    assert len(AgentState.__annotations__) == 14
