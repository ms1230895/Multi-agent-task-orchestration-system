"""Tests for `tests/fakes.py`: the fake LLM must fail loudly on an unknown role (AC28)."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from tests.fakes import RecordingFakeLLM


def test_ac28_fake_llm_unknown_role_in_system_prompt_raises() -> None:
    # WHY: a changed prompt must fail the test, not silently get the wrong scripted reply.
    llm = RecordingFakeLLM(planner_replies=["x"])
    with pytest.raises(ValueError, match="unknown role"):
        llm.invoke([SystemMessage("You are the janitor"), HumanMessage("hi")])


@pytest.mark.parametrize(
    ("marker", "role"),
    [("planner", "planner"), ("critic", "critic"), ("executor", "executor")],
)
def test_ac28_fake_llm_known_role_markers_are_recognised(marker: str, role: str) -> None:
    llm = RecordingFakeLLM(
        planner_replies=["p"], critic_replies=["c"], executor_replies=[AIMessage("e")]
    )
    llm.invoke([SystemMessage(f"You are the {marker} of a system"), HumanMessage("hi")])
    assert llm.roles == [role]
