"""Tests for `src/agents/executor.py`: the stub executor (AC34)."""

from src.agents.executor import stub_executor
from tests.fakes import make_state


def test_ac34_stub_executor_step_index_1_returns_fixed_output() -> None:
    # WHY: graph tests rely on this exact text to check which step ran.
    state = make_state(plan=["a", "b"], current_step=1)
    assert stub_executor(state) == {"last_output": "stub output for step 2: b"}
