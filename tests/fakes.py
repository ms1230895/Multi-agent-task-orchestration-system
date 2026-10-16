"""Shared test helpers: a recording fake chat model and small state builders.

No real LLM and no network (Hard rule 8). Used by the agent and graph tests.
"""

from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from src.state import AgentState, initial_state


class RecordingFakeLLM(BaseChatModel):
    """Fake chat model that records every messages list and returns scripted replies.

    The role is found from the system prompt text: "planner" -> planner replies, "critic" ->
    critic replies. When a script runs out, its last reply repeats (useful for an
    always-retry critic). If `error` is set, every call raises it.
    """

    planner_replies: list[str] = Field(default_factory=list)
    critic_replies: list[str] = Field(default_factory=list)
    error: Exception | None = None
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recording-fake"

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
        system = str(messages[0].content).lower()
        role = "planner" if "planner" in system else "critic"
        self.roles.append(role)
        script = self.planner_replies if role == "planner" else self.critic_replies
        index = sum(1 for r in self.roles if r == role) - 1
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
