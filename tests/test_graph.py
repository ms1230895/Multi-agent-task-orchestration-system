"""Tests for `src/graph.py`: router rules, advance, edges, finalize, recursion limit, whole graph.

AC19-AC33 are pure-function tests (no LLM). AC35-AC40 run the compiled graph with a recording
fake LLM and a spy executor. No real LLM, no network (Hard rule 8).
"""

import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage

import src.graph as graph_module
from src.config import Settings, load_settings
from src.graph import (
    advance,
    build_graph,
    decide,
    finalize,
    recursion_limit,
    route_after_advance,
    route_after_planner,
)
from src.logger import EventCallback, trim
from src.state import AgentState, initial_state
from tests.fakes import RecordingFakeLLM, make_state, prompt_text, stub_executor


def _settings(**env: str) -> Settings:
    """Settings from a dict, never from the real .env."""
    return load_settings({"OLLAMA_MODEL": "m", **env})


S = _settings(MAX_RETRIES="2", MAX_REPLANS="1")


def _plan_reply(*steps: str) -> str:
    return json.dumps({"steps": list(steps)})


def _verdict_reply(verdict: str, feedback: str = "") -> str:
    return json.dumps({"verdict": verdict, "feedback": feedback})


# ---- AC19-AC27: decide ----


def test_ac19_failed_status_routes_to_failed() -> None:
    # WHY: a failed run must never be revived, whatever the verdict says.
    for verdict in ("approve", "retry", ""):
        state = make_state(status="failed", verdict=verdict, plan=["a", "b"])
        assert decide(state, S) == "failed"


def test_ac20_approve_with_steps_left_routes_to_next_step() -> None:
    state = make_state(verdict="approve", plan=["a", "b"], current_step=0)
    assert decide(state, S) == "next_step"


@pytest.mark.parametrize(("plan", "step"), [(["a", "b"], 1), (["a"], 0)])
def test_ac21_approve_on_last_step_routes_to_done(plan: list[str], step: int) -> None:
    state = make_state(verdict="approve", plan=plan, current_step=step)
    assert decide(state, S) == "done"


@pytest.mark.parametrize("retries", [0, 1])
def test_ac22_retry_with_retries_left_routes_to_retry(retries: int) -> None:
    state = make_state(verdict="retry", plan=["a"], retries=retries)
    assert decide(state, S) == "retry"


def test_ac23_retries_used_up_with_replans_left_routes_to_replan() -> None:
    state = make_state(verdict="retry", plan=["a"], retries=2, replans=0)
    assert decide(state, S) == "replan"


def test_ac24_retries_and_replans_used_up_routes_to_failed() -> None:
    state = make_state(verdict="retry", plan=["a"], retries=2, replans=1)
    assert decide(state, S) == "failed"


def test_ac25_zero_max_retries_first_retry_routes_to_replan() -> None:
    s = _settings(MAX_RETRIES="0", MAX_REPLANS="1")
    assert decide(make_state(verdict="retry", plan=["a"]), s) == "replan"


def test_ac25_zero_limits_first_retry_routes_to_failed() -> None:
    s = _settings(MAX_RETRIES="0", MAX_REPLANS="0")
    assert decide(make_state(verdict="retry", plan=["a"]), s) == "failed"


@pytest.mark.parametrize("verdict", ["", "maybe"])
def test_ac26_unexpected_verdict_never_moves_forward(verdict: str) -> None:
    # WHY: only "approve" may count as success; anything else is a retry path.
    state = make_state(verdict=verdict, plan=["a", "b"], retries=0)
    assert decide(state, S) not in ("next_step", "done")


@pytest.mark.parametrize(
    "state",
    [
        make_state(verdict="approve", plan=["a", "b"], step_results=["x"]),
        make_state(verdict="retry", plan=["a"], retries=2, replans=0, step_results=["x"]),
        make_state(status="failed", plan=["a"]),
    ],
)
def test_ac27_decide_does_not_mutate_state(state: AgentState) -> None:
    before = copy.deepcopy(state)
    decide(state, S)
    assert state == before


# ---- AC28: advance ----


def _advance_state(**overrides: Any) -> AgentState:
    base: dict[str, Any] = {
        "plan": ["a", "b"],
        "current_step": 0,
        "step_results": ["r0"],
        "last_output": "out",
        "feedback": "fb",
        "retries": 1,
        "replans": 0,
        "verdict": "approve",
    }
    base.update(overrides)
    return make_state(**base)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        (
            {},
            {
                "route": "next_step",
                "step_results": ["r0", "out"],
                "current_step": 1,
                "retries": 0,
                "feedback": "",
            },
        ),
        (
            {"current_step": 1},
            {"route": "done", "step_results": ["r0", "out"], "status": "done"},
        ),
        ({"verdict": "retry"}, {"route": "retry", "retries": 2}),
        ({"verdict": "retry", "retries": 2}, {"route": "replan", "replans": 1}),
        (
            {"verdict": "retry", "retries": 2, "replans": 1},
            {"route": "failed", "status": "failed"},
        ),
    ],
    ids=["next_step", "done", "retry", "replan", "failed"],
)
def test_ac28_advance_returns_exact_changes_and_keeps_input(
    overrides: dict[str, Any], expected: dict[str, Any]
) -> None:
    # WHY: advance is the only place counters move; an extra or missing key breaks the run.
    state = _advance_state(**overrides)
    before = copy.deepcopy(state)
    assert advance(state, settings=S) == expected
    assert state == before


# ---- AC29-AC30: edge functions ----


@pytest.mark.parametrize(
    ("route", "target"),
    [
        ("next_step", "executor"),
        ("retry", "executor"),
        ("replan", "planner"),
        ("done", "finalize"),
        ("failed", "finalize"),
        ("bogus", "finalize"),
        ("", "finalize"),
    ],
)
def test_ac29_route_after_advance_maps_route_to_node(route: str, target: str) -> None:
    # WHY: an unknown route must end the run instead of looping.
    assert route_after_advance(make_state(route=route)) == target


@pytest.mark.parametrize(("status", "target"), [("failed", "finalize"), ("running", "executor")])
def test_ac30_route_after_planner_maps_status_to_node(status: str, target: str) -> None:
    assert route_after_planner(make_state(status=status)) == target


# ---- AC31-AC32: finalize ----


def test_ac31_finalize_done_uses_last_step_result() -> None:
    state = make_state(status="done", plan=["a", "b"], step_results=["r1", "r2"])
    assert finalize(state) == {"status": "done", "final_answer": "r2"}


def test_ac32_finalize_empty_plan_gives_planner_failure_message() -> None:
    result = finalize(make_state(status="failed", plan=[]))
    assert result == {
        "status": "failed",
        "final_answer": "Task failed: the planner could not make a valid plan.",
    }


def test_ac32_finalize_limits_used_up_adds_last_step_result() -> None:
    state = make_state(status="failed", plan=["a"], step_results=["r1", "r2"], last_output="lo")
    result = finalize(state)
    assert result["status"] == "failed"
    assert result["final_answer"] == (
        "Task failed: retry and replan limits used up. Best partial result: r2"
    )


def test_ac32_finalize_without_step_results_falls_back_to_last_output() -> None:
    state = make_state(status="failed", plan=["a"], last_output="lo")
    assert finalize(state)["final_answer"].endswith("Best partial result: lo")


def test_ac32_finalize_without_any_partial_adds_nothing() -> None:
    result = finalize(make_state(status="failed", plan=["a"]))
    assert result["final_answer"] == "Task failed: retry and replan limits used up."


def test_ac32_finalize_cuts_partial_result_to_1000_chars() -> None:
    state = make_state(status="failed", plan=["a"], last_output="p" * 1500)
    answer = finalize(state)["final_answer"]
    assert "p" * 1000 in answer
    assert "p" * 1001 not in answer


def test_ac32_finalize_running_status_becomes_failed() -> None:
    # WHY: "running" at finalize is a bug; ending as failed is the safe result.
    result = finalize(make_state(status="running", plan=["a"]))
    assert result["status"] == "failed"


# ---- AC33: recursion_limit ----


def test_ac33_recursion_limit_default_settings_is_154() -> None:
    assert recursion_limit(_settings()) == 154


def test_ac33_recursion_limit_max_settings_is_586() -> None:
    assert recursion_limit(_settings(MAX_RETRIES="5", MAX_REPLANS="3")) == 586


# ---- AC35-AC40: whole graph ----


class SpyExecutor:
    """Executor that records the state it receives, then behaves like the stub."""

    def __init__(self) -> None:
        self.states: list[AgentState] = []

    def __call__(self, state: AgentState) -> dict[str, Any]:
        self.states.append(copy.deepcopy(state))
        return stub_executor(state)


def _run(llm: RecordingFakeLLM, settings: Settings, spy: SpyExecutor | None = None) -> AgentState:
    # WHY: no spy means the plain stub, never the real executor, so these tests need no tools.
    graph = build_graph(llm=llm, settings=settings, executor=spy or stub_executor)
    return cast(
        AgentState,
        graph.invoke(
            initial_state("do the task", "run-1"),
            config={"recursion_limit": recursion_limit(settings)},
        ),
    )


def test_ac35_compiled_graph_has_exactly_the_five_nodes() -> None:
    graph = build_graph(llm=RecordingFakeLLM(), settings=S)
    names = set(graph.get_graph().nodes)
    assert names - {"__start__", "__end__"} == {
        "planner",
        "executor",
        "critic",
        "advance",
        "finalize",
    }


def test_ac36_happy_path_two_steps_ends_done() -> None:
    llm = RecordingFakeLLM(
        planner_replies=[_plan_reply("one", "two")],
        critic_replies=[_verdict_reply("approve", "ok")],
    )
    final = _run(llm, S)
    assert final["status"] == "done"
    assert len(final["step_results"]) == 2
    assert final["final_answer"] == "stub output for step 2: two"
    assert len(llm.calls) == 3


def test_ac37_retry_then_approve_runs_executor_twice_with_feedback() -> None:
    llm = RecordingFakeLLM(
        planner_replies=[_plan_reply("only")],
        critic_replies=[_verdict_reply("retry", "fix it"), _verdict_reply("approve")],
    )
    spy = SpyExecutor()
    final = _run(llm, S, spy)
    assert final["status"] == "done"
    assert len(spy.states) == 2
    # WHY: the executor must see the critic's advice and the retry count on the second try.
    assert spy.states[1]["feedback"] == "fix it"
    assert spy.states[1]["retries"] == 1


def test_ac38_always_retry_critic_fails_within_limits_without_recursion_error() -> None:
    # WHY: Hard rule 5. The router limits must stop a never-satisfied critic before LangGraph does.
    s = _settings(MAX_RETRIES="2", MAX_REPLANS="1")
    llm = RecordingFakeLLM(
        planner_replies=[_plan_reply("only")],
        critic_replies=[_verdict_reply("retry", "no")],
    )
    spy = SpyExecutor()
    final = _run(llm, s, spy)  # a GraphRecursionError here fails the test
    assert final["status"] == "failed"
    assert final["replans"] == 1
    assert llm.count("planner") == 2
    assert len(spy.states) == 6


def test_ac39_planner_two_invalid_replies_fails_without_executor_or_critic() -> None:
    llm = RecordingFakeLLM(planner_replies=["not json", "still not json"])
    spy = SpyExecutor()
    final = _run(llm, S, spy)
    assert final["status"] == "failed"
    assert final["final_answer"] == "Task failed: the planner could not make a valid plan."
    assert spy.states == []
    assert llm.count("critic") == 0


def test_ac40_build_graph_without_llm_uses_get_llm_and_makes_no_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = RecordingFakeLLM()
    seen: list[Settings] = []

    def fake_get_llm(settings: Settings | None = None) -> RecordingFakeLLM:
        assert settings is not None
        seen.append(settings)
        return fake

    monkeypatch.setattr(graph_module, "get_llm", fake_get_llm)
    build_graph(settings=S)
    assert seen == [S]
    assert fake.calls == []


# ---- AC26-AC27: the real executor is the default ----


def test_ac26_build_graph_without_executor_runs_real_executor_with_calculator() -> None:
    call = {"name": "calculator", "args": {"expression": "6*7"}, "id": "c1", "type": "tool_call"}
    llm = RecordingFakeLLM(
        planner_replies=[_plan_reply("compute 6*7")],
        critic_replies=[_verdict_reply("approve", "ok")],
        executor_replies=[AIMessage(content="", tool_calls=[call]), AIMessage("The answer is 42")],
    )
    # WHY: no `executor=` argument, so this protects the default wiring in build_graph.
    graph = build_graph(llm=llm, settings=S)
    final = graph.invoke(
        initial_state("do the task", "run-1"),
        config={"recursion_limit": recursion_limit(S)},
    )
    assert final["status"] == "done"
    assert final["final_answer"] == "The answer is 42"
    assert llm.count("executor") == 2


def test_ac27_stub_executor_no_longer_exists_in_src() -> None:
    # WHY: the stub must live only in tests, or it could ship as a fake agent.
    src_dir = Path(graph_module.__file__).parent
    offenders = [p.name for p in src_dir.rglob("*.py") if "stub_executor" in p.read_text("utf-8")]
    assert offenders == []
    assert not hasattr(graph_module, "stub_executor")


def test_ac27_stub_executor_from_fakes_returns_step_text() -> None:
    state = make_state(plan=["a", "b"], current_step=1)
    assert stub_executor(state) == {"last_output": "stub output for step 2: b"}


# ---- Session 5: events from advance (AC13, AC16) ----


def _collector() -> tuple[list[tuple[str, dict[str, Any]]], EventCallback]:
    """Return a list and a callback that appends every event to it."""
    events: list[tuple[str, dict[str, Any]]] = []

    def collect(event: str, data: dict[str, Any]) -> None:
        events.append((event, data))

    return events, collect


def _broken_callback(event: str, data: dict[str, Any]) -> None:
    """A callback that always raises."""
    raise RuntimeError("callback crashed")


def test_ac13_advance_retry_route_emits_one_step_retry_event() -> None:
    events, collect = _collector()
    state = _advance_state(verdict="retry", current_step=1, retries=1, feedback="f" * 700)
    advance(state, settings=S, on_event=collect)
    # WHY: the trace must show that a step was sent back, which step, and why.
    assert events == [
        (
            "retry",
            {"kind": "step", "step": 1, "attempt": 2, "reason": trim("f" * 700, 500)},
        )
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"current_step": 1},
        {"verdict": "retry", "retries": 2},
        {"verdict": "retry", "retries": 2, "replans": 1},
    ],
    ids=["next_step", "done", "replan", "failed"],
)
def test_ac13_advance_other_routes_emit_no_event(overrides: dict[str, Any]) -> None:
    events, collect = _collector()
    advance(_advance_state(**overrides), settings=S, on_event=collect)
    # WHY: only a retry is a new fact; the other routes show up in the next events.
    assert events == []


@pytest.mark.parametrize(
    "overrides",
    [{}, {"current_step": 1}, {"verdict": "retry"}, {"verdict": "retry", "retries": 2}],
    ids=["next_step", "done", "retry", "replan"],
)
def test_ac16_advance_raising_callback_does_not_change_the_result(
    overrides: dict[str, Any],
) -> None:
    plain = advance(_advance_state(**overrides), settings=S)
    broken = advance(_advance_state(**overrides), settings=S, on_event=_broken_callback)
    # WHY: a broken display must never change the route or the counters.
    assert broken == plain


# ---- Session 5: events through the whole graph (AC18, AC47) ----


def _calc_call(expression: str = "2 ** 10") -> AIMessage:
    """Build an executor reply that asks for one calculator call."""
    call = {
        "name": "calculator",
        "args": {"expression": expression},
        "id": "c1",
        "type": "tool_call",
    }
    return AIMessage(content="", tool_calls=[call])


def _scripted_llm() -> RecordingFakeLLM:
    """A 1-step run: calculator call, text, a retry verdict, text again, then approve."""
    return RecordingFakeLLM(
        planner_replies=[_plan_reply("compute 2 ** 10")],
        critic_replies=[_verdict_reply("retry", "say it again"), _verdict_reply("approve", "ok")],
        executor_replies=[_calc_call(), AIMessage("1024"), AIMessage("The answer is 1024")],
    )


def test_ac18_whole_graph_emits_events_in_order() -> None:
    events, collect = _collector()
    graph = build_graph(llm=_scripted_llm(), settings=S, on_event=collect)
    final = graph.invoke(
        initial_state("do the task", "run-1"),
        config={"recursion_limit": recursion_limit(S)},
    )
    # WHY: this is the order the trace reader and the console rely on.
    assert [name for name, _ in events] == [
        "plan_created",
        "tool_call",
        "critic_verdict",
        "retry",
        "critic_verdict",
    ]
    assert final["status"] == "done"


def test_ac18_whole_graph_with_broken_callback_gives_same_final_state() -> None:
    def run(on_event: EventCallback | None) -> AgentState:
        graph = build_graph(llm=_scripted_llm(), settings=S, on_event=on_event)
        return cast(
            AgentState,
            graph.invoke(
                initial_state("do the task", "run-1"),
                config={"recursion_limit": recursion_limit(S)},
            ),
        )

    # WHY: a broken display callback must not change a run, only hide its events.
    assert run(_broken_callback) == run(None)


def test_ac18_whole_graph_with_passed_in_executor_still_emits_planner_and_critic_events() -> None:
    events, collect = _collector()
    llm = RecordingFakeLLM(
        planner_replies=[_plan_reply("only")], critic_replies=[_verdict_reply("approve", "ok")]
    )
    graph = build_graph(llm=llm, settings=S, executor=stub_executor, on_event=collect)
    graph.invoke(
        initial_state("do the task", "run-1"),
        config={"recursion_limit": recursion_limit(S)},
    )
    # WHY: a test executor is used as it is, so it emits nothing, but the other nodes still do.
    assert [name for name, _ in events] == ["plan_created", "critic_verdict"]


def test_ac47_whole_graph_critic_prompt_contains_the_calculator_log_line() -> None:
    llm = _scripted_llm()
    graph = build_graph(llm=llm, settings=S)
    graph.invoke(
        initial_state("do the task", "run-1"),
        config={"recursion_limit": recursion_limit(S)},
    )
    critic_prompts = [
        prompt_text(call)
        for role, call in zip(llm.roles, llm.calls, strict=True)
        if role == "critic"
    ]
    # WHY: this is the fix for the critic rejecting correct work it could not see (runs R2-R5).
    assert 'calculator({"expression": "2 ** 10"}) -> ok: 1024' in critic_prompts[0]
