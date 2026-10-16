"""Shared test helpers: a recording fake chat model and small state builders.

No real LLM and no network (Hard rule 8). Used by the agent and graph tests.
"""

from collections.abc import Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from src.state import AgentState, initial_state


class RecordingFakeLLM(BaseChatModel):
    """Fake chat model that records every messages list and returns scripted replies.

    The role is found from a marker in the system prompt ("You are the planner", "You are the
    critic", "You are the executor"); an unknown role raises, so a changed prompt cannot
    silently get the wrong replies. When a script runs out, its last reply repeats (useful for
    an always-retry critic). If `error` is set, every call raises it.
    """

    planner_replies: list[str] = Field(default_factory=list)
    critic_replies: list[str] = Field(default_factory=list)
    # WHY: executor replies are messages, so they can carry `tool_calls` and `invalid_tool_calls`.
    executor_replies: list[AIMessage] = Field(default_factory=list)
    bound_tools: list[Any] = Field(default_factory=list)
    error: Exception | None = None
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recording-fake"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "RecordingFakeLLM":  # type: ignore[override]
        """Record the tool list and return the fake itself, so `invoke` still records calls."""
        self.bound_tools.append(list(tools))
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        system = str(messages[0].content)
        if "You are the planner" in system:
            role = "planner"
        elif "You are the critic" in system:
            role = "critic"
        elif "You are the executor" in system:
            role = "executor"
        else:
            raise ValueError("RecordingFakeLLM: unknown role in the system prompt")
        self.roles.append(role)
        index = sum(1 for r in self.roles if r == role) - 1
        if role == "executor":
            reply = self.executor_replies[min(index, len(self.executor_replies) - 1)]
            return ChatResult(generations=[ChatGeneration(message=reply)])
        script = self.planner_replies if role == "planner" else self.critic_replies
        text = script[min(index, len(script) - 1)]
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def count(self, role: str) -> int:
        """Return how many calls had this role."""
        return sum(1 for r in self.roles if r == role)


def make_state(**overrides: Any) -> AgentState:
    """Return an initial state with the given fields overridden."""
    state = initial_state("do the task", "run-1")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def prompt_text(messages: list[BaseMessage]) -> str:
    """Join the text of all messages, for substring checks."""
    return "\n".join(str(m.content) for m in messages)


def stub_executor(state: AgentState) -> dict[str, Any]:
    """Return a fixed output for the current step, with no LLM and no tools.

    WHY: graph tests need an executor whose output is predictable, to check which step ran.
    It was the default executor in session 3; the real executor replaced it in session 4.
    """
    number = state["current_step"] + 1
    step = state["plan"][state["current_step"]]
    return {"last_output": f"stub output for step {number}: {step}"}
