"""Tests for `src/run.py`: `run_task`, the CLI `main`, and the source rules (AC19-AC30, AC34).

The LLM is `RecordingFakeLLM`, settings come from `load_settings` with a dict, and every trace
goes to pytest's `tmp_path`. The only tool used is the real calculator, which needs no network.
No real LLM, no internet, nothing written to the real `logs/` or `workspace/` (Hard rule 8).
"""

import io
import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from rich.console import Console

import src.logger as logger_module
import src.run as run_module
from src.config import MAX_PLAN_STEPS, ConfigError, Settings, load_settings
from src.logger import EVENT_TYPES, read_trace, trace_path
from src.run import COUNTER_KEYS, main, run_task
from src.state import AgentState, initial_state
from tests.fakes import RecordingFakeLLM, make_state

SETTINGS = load_settings({"OLLAMA_MODEL": "fake-model"})
NO_RETRY_SETTINGS = load_settings(
    {"OLLAMA_MODEL": "fake-model", "MAX_RETRIES": "0", "MAX_REPLANS": "0"}
)

COUNTER_NAMES = {
    "plan_steps",
    "steps_done",
    "replans",
    "tool_calls",
    "failed_tool_calls",
    "validation_errors",
    "tool_retries",
    "step_retries",
    "critic_verdicts",
}


def _plan(*steps: str) -> str:
    """Build a planner reply."""
    return json.dumps({"steps": list(steps)})


def _verdict(verdict: str, feedback: str = "") -> str:
    """Build a critic reply."""
    return json.dumps({"verdict": verdict, "feedback": feedback})


def _calc_reply(args: dict[str, Any], call_id: str = "c1") -> AIMessage:
    """Build an executor reply that asks for one calculator call."""
    call = {"name": "calculator", "args": args, "id": call_id, "type": "tool_call"}
    return AIMessage(content="", tool_calls=[call])


def _happy_llm() -> RecordingFakeLLM:
    """A fake for a 1-step run that is approved at once, with no tool call."""
    return RecordingFakeLLM(
        planner_replies=[_plan("answer it")],
        critic_replies=[_verdict("approve", "ok")],
        executor_replies=[AIMessage("42")],
    )


def _tools_and_retry_llm() -> RecordingFakeLLM:
    """A fake for a run with 2 calculator calls (the first invalid) and 1 step retry."""
    return RecordingFakeLLM(
        planner_replies=[_plan("compute 1+1")],
        critic_replies=[_verdict("retry", "say it again"), _verdict("approve", "ok")],
        executor_replies=[
            _calc_reply({"expr": "1"}, "c1"),
            _calc_reply({"expression": "1+1"}, "c2"),
            AIMessage("2"),
            AIMessage("The answer is 2"),
        ],
    )


def _run(
    tmp_path: Path,
    llm: RecordingFakeLLM,
    task: str = "do the task",
    settings: Settings = SETTINGS,
    **kwargs: Any,
) -> AgentState:
    """Run `run_task` with a fake LLM and the trace in `tmp_path`."""
    return run_task(task, llm=llm, settings=settings, logs_dir=tmp_path, **kwargs)


def _trace(tmp_path: Path, state: AgentState) -> list[dict[str, Any]]:
    """Read the trace file of a finished run."""
    return read_trace(trace_path(state["run_id"], tmp_path))


def _run_end(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the data of the only `run_end` record."""
    ends = [r for r in records if r["event"] == "run_end"]
    assert len(ends) == 1
    return ends[0]["data"]


# ---- AC19: a successful run ----


def test_ac19_run_task_success_returns_done_state_with_no_error(tmp_path: Path) -> None:
    state = _run(tmp_path, _happy_llm())
    # WHY: the benchmark reads status and error from this state.
    assert state["status"] == "done"
    assert state["error"] == ""
    assert state["final_answer"] == "42"
    assert state["run_id"]


def test_ac19_run_task_success_trace_lines_carry_model_and_run_id(tmp_path: Path) -> None:
    state = _run(tmp_path, _happy_llm())
    path = trace_path(state["run_id"], tmp_path)
    records = read_trace(path)
    # WHY: a model name on every line is how traces of different models are told apart.
    assert path.is_file()
    assert records
    assert all(r["model"] == SETTINGS.ollama_model for r in records)
    assert all(r["run_id"] == state["run_id"] for r in records)


def test_ac19_run_task_success_last_line_is_the_only_run_end(tmp_path: Path) -> None:
    state = _run(tmp_path, _happy_llm())
    records = _trace(tmp_path, state)
    # WHY: the trace count for the CV is the count of run_end lines, so there must be one.
    assert records[-1]["event"] == "run_end"
    assert [r["event"] for r in records].count("run_end") == 1
    assert [r["event"] for r in records][:-1] == ["plan_created", "critic_verdict"]


def test_ac19_run_task_only_writes_the_trace_file_into_logs_dir(tmp_path: Path) -> None:
    state = _run(tmp_path, _happy_llm())
    # WHY: nothing but the one trace file may appear in the logs folder.
    assert [p.name for p in tmp_path.iterdir()] == [f"{state['run_id']}.jsonl"]


# ---- AC20: run_end shape ----


def test_ac20_run_end_data_has_exactly_the_documented_keys(tmp_path: Path) -> None:
    state = _run(tmp_path, _happy_llm())
    data = _run_end(_trace(tmp_path, state))
    # WHY: the benchmark and the trace explorer read these keys.
    assert set(data) == {
        "task",
        "status",
        "final_answer",
        "error",
        "counters",
        "config",
        "duration_ms",
    }
    assert data["task"] == "do the task"
    assert data["status"] == "done"
    assert data["final_answer"] == "42"
    assert data["error"] == ""
    assert isinstance(data["duration_ms"], int)
    assert data["duration_ms"] >= 0


def test_ac20_run_end_config_has_exactly_the_settings_fields_plus_max_plan_steps(
    tmp_path: Path,
) -> None:
    state = _run(tmp_path, _happy_llm())
    config = _run_end(_trace(tmp_path, state))["config"]
    # WHY: a future secret setting must not enter the trace unless someone changes this test.
    assert set(config) == set(Settings.model_fields) | {"max_plan_steps"}
    assert config["max_plan_steps"] == MAX_PLAN_STEPS
    assert config["ollama_model"] == "fake-model"


def test_ac20_run_end_config_matches_the_settings_values(tmp_path: Path) -> None:
    settings = load_settings({"OLLAMA_MODEL": "fake-model", "MAX_RETRIES": "0", "MAX_REPLANS": "0"})
    state = _run(tmp_path, _happy_llm(), settings=settings)
    config = _run_end(_trace(tmp_path, state))["config"]
    # WHY: the ablation passes are told apart by the flags and limits written here.
    assert config["max_retries"] == 0
    assert config["enable_validation"] is True


# ---- AC21: counters ----


def test_ac21_run_end_counters_match_a_scripted_run(tmp_path: Path) -> None:
    state = _run(tmp_path, _tools_and_retry_llm())
    counters = _run_end(_trace(tmp_path, state))["counters"]
    # WHY: the failed-tool-call rate (a CV number) is computed from these counters.
    assert set(counters) == COUNTER_NAMES
    assert counters == {
        "plan_steps": 1,
        "steps_done": 1,
        "replans": 0,
        "tool_calls": 2,
        "failed_tool_calls": 1,
        "validation_errors": 1,
        "tool_retries": 0,
        "step_retries": 1,
        "critic_verdicts": 2,
    }
    assert state["status"] == "done"


def test_ac21_counter_keys_constant_lists_the_six_event_counters() -> None:
    # WHY: the six event counters plus three state counters make the nine documented keys.
    assert set(COUNTER_KEYS) | {"plan_steps", "steps_done", "replans"} == COUNTER_NAMES


# ---- AC22: a crash in the middle ----


def test_ac22_run_task_connection_error_at_critic_becomes_failed_state(tmp_path: Path) -> None:
    llm = RecordingFakeLLM(
        planner_replies=[_plan("one", "two")],
        executor_replies=[AIMessage("x")],
        role_errors={"critic": ConnectionError("down")},
    )
    state = _run(tmp_path, llm)
    # WHY: the benchmark needs a result for every run, and the error type to tell a usage cap
    # or connection error from an agent failure.
    assert state["status"] == "failed"
    assert state["error"] == "ConnectionError: down"
    assert state["final_answer"].startswith("Task failed: unexpected error")


def test_ac22_run_task_crash_keeps_the_plan_in_run_end_and_ends_the_trace(tmp_path: Path) -> None:
    llm = RecordingFakeLLM(
        planner_replies=[_plan("one", "two")],
        executor_replies=[AIMessage("x")],
        role_errors={"critic": ConnectionError("down")},
    )
    state = _run(tmp_path, llm)
    records = _trace(tmp_path, state)
    data = _run_end(records)
    # WHY: `stream` keeps the last good state, so a crash still reports how far the run got.
    assert records[-1]["event"] == "run_end"
    assert data["counters"]["plan_steps"] == 2
    assert data["error"] == "ConnectionError: down"
    assert data["status"] == "failed"
    assert state["plan"] == ["one", "two"]


def test_ac22_run_task_long_error_message_is_cut_to_300_chars(tmp_path: Path) -> None:
    llm = RecordingFakeLLM(
        planner_replies=[_plan("one")],
        executor_replies=[AIMessage("x")],
        role_errors={"critic": RuntimeError("m" * 1000)},
    )
    state = _run(tmp_path, llm)
    # WHY: an HTTP body in an exception message must not fill the trace.
    assert state["error"].startswith("RuntimeError: ")
    assert len(state["error"]) == 300


def test_ac22_run_task_planner_connection_error_is_caught_too(tmp_path: Path) -> None:
    llm = RecordingFakeLLM(error=ConnectionError("down"))
    state = _run(tmp_path, llm)
    # WHY: a crash before any plan exists must still give a state and a run_end.
    assert state["status"] == "failed"
    assert state["error"] == "ConnectionError: down"
    assert _run_end(_trace(tmp_path, state))["counters"]["plan_steps"] == 0


# ---- AC23: recursion limit ----


def test_ac23_run_task_graph_recursion_error_becomes_failed_state_with_run_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run_module, "recursion_limit", lambda settings: 2)
    state = _run(tmp_path, _happy_llm())
    # WHY: Hard rule 5 safety net; if it ever fires, the run must still end cleanly.
    assert state["status"] == "failed"
    assert state["error"].startswith("GraphRecursionError")
    assert _run_end(_trace(tmp_path, state))["error"].startswith("GraphRecursionError")


# ---- AC24: invalid task ----


@pytest.mark.parametrize("task", ["   ", "", "x" * 2001])
def test_ac24_run_task_invalid_task_fails_without_calling_the_llm(
    tmp_path: Path, task: str
) -> None:
    llm = _happy_llm()
    state = _run(tmp_path, llm, task=task)
    # WHY: a bad task must not spend any LLM quota.
    assert llm.calls == []
    assert state["status"] == "failed"
    assert state["error"].startswith("ValueError")
    assert state["final_answer"].startswith("Task failed:")


def test_ac24_run_task_invalid_task_trace_is_one_run_end_line(tmp_path: Path) -> None:
    state = _run(tmp_path, _happy_llm(), task="   ")
    records = _trace(tmp_path, state)
    # WHY: even a refused task is one run with exactly one run_end.
    assert [r["event"] for r in records] == ["run_end"]
    assert records[0]["data"]["status"] == "failed"
    assert records[0]["data"]["error"].startswith("ValueError")


# ---- AC25: on_record ----


def test_ac25_on_record_receives_every_record_in_file_order(tmp_path: Path) -> None:
    received: list[dict[str, Any]] = []
    state = _run(tmp_path, _tools_and_retry_llm(), on_record=received.append)
    # WHY: the live display must show exactly what the trace holds.
    assert received == _trace(tmp_path, state)
    assert received[-1]["event"] == "run_end"


def test_ac25_raising_on_record_does_not_stop_the_run_or_change_the_trace(tmp_path: Path) -> None:
    def broken(record: dict[str, Any]) -> None:
        raise RuntimeError("display crashed")

    clean_dir = tmp_path / "clean"
    broken_dir = tmp_path / "broken"
    clean = _run(clean_dir, _tools_and_retry_llm())
    state = _run(broken_dir, _tools_and_retry_llm(), on_record=broken)
    # WHY: a broken display must never stop a run or lose lines from its trace.
    assert state["status"] == "done"
    assert state["error"] == ""
    broken_events = [r["event"] for r in _trace(broken_dir, state)]
    assert broken_events == [r["event"] for r in _trace(clean_dir, clean)]
    assert broken_events[-1] == "run_end"


# ---- AC26: exactly one run_end on every path ----


def _llm_always_retry() -> RecordingFakeLLM:
    return RecordingFakeLLM(
        planner_replies=[_plan("only")],
        critic_replies=[_verdict("retry", "no")],
        executor_replies=[AIMessage("x")],
    )


def _llm_crash() -> RecordingFakeLLM:
    return RecordingFakeLLM(
        planner_replies=[_plan("only")],
        executor_replies=[AIMessage("x")],
        role_errors={"critic": ConnectionError("down")},
    )


@pytest.mark.parametrize(
    ("make_llm", "task", "status"),
    [
        (_happy_llm, "do the task", "done"),
        (_llm_always_retry, "do the task", "failed"),
        (_llm_crash, "do the task", "failed"),
        (_happy_llm, "   ", "failed"),
    ],
    ids=["done", "limits_used_up", "crash", "invalid_task"],
)
def test_ac26_every_path_writes_exactly_one_run_end(
    tmp_path: Path, make_llm: Any, task: str, status: str
) -> None:
    state = _run(tmp_path, make_llm(), task=task, settings=NO_RETRY_SETTINGS)
    events = [r["event"] for r in _trace(tmp_path, state)]
    # WHY: the "100+ traces" count is the number of run_end lines, so each run adds one.
    assert state["status"] == status
    assert events.count("run_end") == 1
    assert events[-1] == "run_end"
    assert set(events) <= EVENT_TYPES


def test_ac26_planner_failure_writes_exactly_one_run_end(tmp_path: Path) -> None:
    llm = RecordingFakeLLM(planner_replies=["not json", "still not json"])
    state = _run(tmp_path, llm)
    events = [r["event"] for r in _trace(tmp_path, state)]
    # WHY: a planner that gives up emits no plan event, so run_end is the whole trace.
    assert events == ["run_end"]
    assert state["final_answer"] == "Task failed: the planner could not make a valid plan."


# ---- AC27: ConfigError ----


def test_ac27_run_task_config_error_is_raised_and_no_trace_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_settings() -> Settings:
        raise ConfigError("OLLAMA_MODEL is required")

    monkeypatch.setattr(run_module, "get_settings", broken_settings)
    logs = tmp_path / "logs"
    llm = _happy_llm()
    # WHY: with no model there is nothing to trace and nothing ran, so this is the one raise.
    with pytest.raises(ConfigError):
        run_task("do the task", llm=llm, logs_dir=logs)
    assert not logs.exists()
    assert llm.calls == []


# ---- AC28: the state ----


def test_ac28_initial_state_has_empty_error() -> None:
    # WHY: "" must mean "no error", so callers can test `if state["error"]`.
    assert initial_state("t", "r1")["error"] == ""


def test_ac28_run_task_state_has_every_agent_state_key(tmp_path: Path) -> None:
    for llm in (_happy_llm(), _llm_crash()):
        state = _run(tmp_path, llm)
        # WHY: callers index the state by key; a missing key would crash the benchmark.
        assert set(state) == set(AgentState.__annotations__)


# ---- AC29, AC30: the CLI ----


def _cli_console(monkeypatch: pytest.MonkeyPatch) -> Console:
    """Replace the CLI's console with a recording one, and return it."""
    recording = Console(record=True, file=io.StringIO(), width=1000)
    monkeypatch.setattr(run_module, "console", recording)
    return recording


def _patch_run_task(
    monkeypatch: pytest.MonkeyPatch, state: AgentState
) -> list[tuple[str, dict[str, Any]]]:
    """Make `run_task` return `state` and record how it was called."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run_task(task: str, **kwargs: Any) -> AgentState:
        calls.append((task, kwargs))
        return state

    monkeypatch.setattr(run_module, "run_task", fake_run_task)
    return calls


def test_ac29_main_done_state_returns_0_and_prints_answer_and_trace_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = _cli_console(monkeypatch)
    state = make_state(status="done", final_answer="four", run_id="rid-1")
    _patch_run_task(monkeypatch, state)
    code = main(["2+2"])
    text = recording.export_text()
    # WHY: shell scripts use the exit code; the user needs the answer and the trace location.
    assert code == 0
    assert "four" in text
    assert str(trace_path("rid-1")) in text


def test_ac29_main_failed_state_returns_1_and_prints_the_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = _cli_console(monkeypatch)
    state = make_state(status="failed", final_answer="Task failed: nope", run_id="rid-2")
    _patch_run_task(monkeypatch, state)
    code = main(["2+2"])
    text = recording.export_text()
    # WHY: a failed run must be visible in the exit code.
    assert code == 1
    assert "Task failed: nope" in text
    assert str(trace_path("rid-2")) in text


def test_ac29_main_passes_the_task_and_print_record_to_run_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _cli_console(monkeypatch)
    calls = _patch_run_task(monkeypatch, make_state(status="done", final_answer="x"))
    main(["what is 2+2"])
    # WHY: the live display only works if run_task gets the console printer as on_record.
    assert len(calls) == 1
    assert calls[0][0] == "what is 2+2"
    assert calls[0][1]["on_record"] is run_module.print_record


def test_ac29_main_prints_markup_in_the_answer_literally(monkeypatch: pytest.MonkeyPatch) -> None:
    recording = _cli_console(monkeypatch)
    _patch_run_task(monkeypatch, make_state(status="done", final_answer="[red]x[/red]"))
    main(["t"])
    # WHY: the final answer comes from the LLM, so rich markup in it must stay literal.
    assert "[red]x[/red]" in recording.export_text()


def test_ac30_main_config_error_returns_2_and_prints_message_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recording = _cli_console(monkeypatch)

    def raising_run_task(task: str, **kwargs: Any) -> AgentState:
        raise ConfigError("OLLAMA_MODEL is required")

    monkeypatch.setattr(run_module, "run_task", raising_run_task)
    code = main(["2+2"])
    text = recording.export_text()
    # WHY: a setup mistake needs a short message, not a traceback.
    assert code == 2
    assert "OLLAMA_MODEL is required" in text
    assert "Traceback" not in text


def test_ac30_main_without_a_task_exits_with_code_2() -> None:
    # WHY: argparse owns the missing-task case; the spec promises exit code 2 for it.
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2


# ---- AC34: source rules ----


@pytest.mark.parametrize("module", [logger_module, run_module], ids=["logger", "run"])
def test_ac34_source_has_no_model_name_and_does_not_read_the_environment(module: Any) -> None:
    source = Path(module.__file__).read_text(encoding="utf-8").lower()
    # WHY: Hard rule 2 (model name only from config) and "config is the only place that reads .env".
    for forbidden in ("gpt-oss", "gemma", "os.environ", "getenv", "load_dotenv"):
        assert forbidden not in source, forbidden


# ---- Review fixes (session 5 code review) ----


@pytest.mark.parametrize("task", [None, 123, ["a task"]])
def test_review_run_task_non_string_task_fails_without_raising(tmp_path: Path, task: Any) -> None:
    llm = _happy_llm()
    state = _run(tmp_path, llm, task=task)
    records = _trace(tmp_path, state)
    # WHY: a bad entry in a benchmark file must give one failed run, not stop the whole loop.
    assert llm.calls == []
    assert state["status"] == "failed"
    assert state["error"].startswith("ValueError")
    assert [r["event"] for r in records] == ["run_end"]


def test_review_replan_path_end_to_end_emits_replan_with_the_critic_feedback(
    tmp_path: Path,
) -> None:
    settings = load_settings({"OLLAMA_MODEL": "fake-model", "MAX_RETRIES": "0", "MAX_REPLANS": "1"})
    llm = RecordingFakeLLM(
        planner_replies=[_plan("first plan"), _plan("second plan")],
        critic_replies=[_verdict("retry", "fix it"), _verdict("approve", "ok")],
        executor_replies=[AIMessage("first try"), AIMessage("second try")],
    )
    state = _run(tmp_path, llm, settings=settings)
    records = _trace(tmp_path, state)
    events = [r["event"] for r in records]
    # WHY: this is the real interplay: `advance` keeps the feedback, the planner reads it as
    # the replan reason, and `replans > 0` picks the `replan` event instead of `plan_created`.
    assert events == ["plan_created", "critic_verdict", "replan", "critic_verdict", "run_end"]
    replan = next(r for r in records if r["event"] == "replan")["data"]
    assert replan["reason"] == "fix it"
    assert replan["replans"] == 1
    assert replan["steps"] == ["second plan"]
    assert state["status"] == "done"
    assert _run_end(records)["counters"]["replans"] == 1


@pytest.mark.parametrize(
    ("data", "key"),
    [
        ({"kind": "tool"}, "tool_retries"),
        ({"kind": "step"}, "step_retries"),
    ],
)
def test_review_count_event_splits_retries_by_kind(data: dict[str, Any], key: str) -> None:
    counters: dict[str, int] = dict.fromkeys(COUNTER_KEYS, 0)
    run_module._count_event(counters, "retry", data)
    # WHY: the two kinds of retry are different metrics; mixing them would hide the gate's work.
    assert counters[key] == 1
    assert sum(counters.values()) == 1


def test_review_main_sanitizes_the_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    state = initial_state("t", "20261016-000000-aaaaaa")
    state.update(status="done", final_answer="ok\x1b[2J\nCritic: approve")
    buffer = io.StringIO()
    monkeypatch.setattr(run_module, "console", Console(file=buffer, width=200))
    monkeypatch.setattr(run_module, "run_task", lambda task, on_record=None: state)
    assert main(["task"]) == 0
    printed = buffer.getvalue()
    # WHY: LLM and web text must not be able to send terminal commands or fake a line.
    assert "\x1b" not in printed
    assert "\n  Critic: approve" in printed
