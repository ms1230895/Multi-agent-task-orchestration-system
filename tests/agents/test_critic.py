"""Tests for `src/agents/critic.py`: Verdict and `run_critic` with a fake LLM (AC7, AC14-AC18)."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from src.agents.critic import Verdict, run_critic
from src.state import AgentState
from tests.fakes import RecordingFakeLLM, make_state, prompt_text

APPROVE = json.dumps({"verdict": "approve", "feedback": "looks good"})
INVALID = '{"verdict": "maybe"}'


def _state(**overrides: Any) -> AgentState:
    """Build a state with a 2-step plan and an executor output waiting for the critic."""
    base: dict[str, Any] = {
        "plan": ["step one", "step two"],
        "current_step": 0,
        "last_output": "the output",
    }
    base.update(overrides)
    return make_state(**base)


# ---- AC7: Verdict schema ----


@pytest.mark.parametrize("raw", ["approve", "retry"])
def test_ac7_verdict_accepts_both_values(raw: str) -> None:
    assert Verdict(verdict=raw).verdict == raw  # type: ignore[arg-type]


def test_ac7_verdict_normalises_case_and_spaces() -> None:
    # WHY: models vary in case and spacing; this must not cost a re-ask.
    assert Verdict(verdict=" Approve ").verdict == "approve"  # type: ignore[arg-type]


def test_ac7_verdict_rejects_unknown_value() -> None:
    # WHY: only "approve" may move a run forward; anything else must be an error.
    with pytest.raises(ValidationError):
        Verdict(verdict="maybe")  # type: ignore[arg-type]


def test_ac7_verdict_missing_feedback_defaults_to_empty() -> None:
    assert Verdict(verdict="retry").feedback == ""


def test_ac7_verdict_cuts_long_feedback_to_1000_chars() -> None:
    assert len(Verdict(verdict="retry", feedback="f" * 1500).feedback) == 1000


# ---- AC14-AC18: run_critic ----


def test_ac14_run_critic_valid_approve_returns_verdict_and_feedback() -> None:
    llm = RecordingFakeLLM(critic_replies=[APPROVE])
    result = run_critic(_state(), llm=llm)
    assert result == {"verdict": "approve", "feedback": "looks good"}
    assert len(llm.calls) == 1


def test_ac15_run_critic_invalid_then_valid_reasks_with_error_text() -> None:
    llm = RecordingFakeLLM(critic_replies=[INVALID, APPROVE])
    result = run_critic(_state(), llm=llm)
    assert result["verdict"] == "approve"
    assert len(llm.calls) == 2
    second = prompt_text(llm.calls[1])
    # WHY: the re-ask must carry the validation error so the model can fix it.
    assert "Your reply was not valid" in second
    assert "verdict:" in second


def test_ac16_run_critic_two_invalid_replies_return_retry() -> None:
    # WHY: never approve work the critic could not judge.
    llm = RecordingFakeLLM(critic_replies=[INVALID, "garbage"])
    result = run_critic(_state(), llm=llm)
    assert result == {"verdict": "retry", "feedback": "critic output invalid"}
    assert len(llm.calls) == 2


def test_ac17_run_critic_prompt_has_task_step_and_output() -> None:
    llm = RecordingFakeLLM(critic_replies=[APPROVE])
    run_critic(_state(task="my task", current_step=1, last_output="OUT-123"), llm=llm)
    text = prompt_text(llm.calls[0])
    assert "my task" in text
    assert "step two" in text
    assert "step 2 of 2" in text
    assert "<output>" in text and "</output>" in text
    assert "OUT-123" in text


def test_ac17_run_critic_cuts_5000_char_output_to_4000() -> None:
    # WHY: long tool output must not blow up the critic prompt.
    llm = RecordingFakeLLM(critic_replies=[APPROVE])
    run_critic(_state(last_output="z" * 5000), llm=llm)
    text = prompt_text(llm.calls[0])
    assert "z" * 4000 in text
    assert "z" * 4001 not in text


def test_ac18_run_critic_connection_error_is_raised() -> None:
    # WHY: infrastructure errors must not turn into a "retry" verdict.
    llm = RecordingFakeLLM(error=ConnectionError("down"))
    with pytest.raises(ConnectionError):
        run_critic(_state(), llm=llm)
