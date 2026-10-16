"""Entry point: `run_task` runs one task and always returns a state.

Architecture layer: 6 (entry point). It builds the graph, connects the trace logger to every
layer, runs the graph, and writes exactly one `run_end` event per run, also when the run
crashes. The benchmark and the UI call `run_task`; the CLI at the bottom of this file calls
it too.

Terminal use: `python -m src.run "your task here"`.
"""

import argparse
import time
from pathlib import Path
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from rich.markup import escape

from src.config import LOGS_DIR, MAX_PLAN_STEPS, ConfigError, Settings, get_settings
from src.console import ERROR, SUCCESS, console, print_record, sanitize
from src.graph import build_graph, recursion_limit
from src.logger import RecordCallback, TraceLogger, new_run_id, trace_path, trim
from src.state import MAX_TASK_CHARS, AgentState, initial_state

# WHY: an exception message can be long (a whole HTTP body); the trace and `error` stay small.
MAX_ERROR_CHARS = 300

COUNTER_KEYS = (
    "tool_calls",
    "failed_tool_calls",
    "validation_errors",
    "tool_retries",
    "step_retries",
    "critic_verdicts",
)


def _error_text(exc: BaseException) -> str:
    """Return `"<ExceptionType>: <message>"`, cut to `MAX_ERROR_CHARS`.

    WHY: the type at the start is how the benchmark tells a usage cap or connection error
    from an agent failure.
    """
    return trim(f"{type(exc).__name__}: {exc}", MAX_ERROR_CHARS)


def _count_event(counters: dict[str, int], event: str, data: dict[str, Any]) -> None:
    """Update the run's event counters from one event.

    WHY: the counters come from the events, not from the state, because `retries` in the
    state is reset on every step and would undercount.
    """
    if event == "tool_call":
        counters["tool_calls"] += 1
        if not data.get("ok"):
            counters["failed_tool_calls"] += 1
    elif event == "validation_error":
        counters["validation_errors"] += 1
    elif event == "retry":
        key = "step_retries" if data.get("kind") == "step" else "tool_retries"
        counters[key] += 1
    elif event == "critic_verdict":
        counters["critic_verdicts"] += 1


def _failed_state(state: AgentState, error: str) -> AgentState:
    """Return a copy of the state marked as failed because of an exception or bad input."""
    failed = AgentState(**state)  # type: ignore[typeddict-item]
    failed["status"] = "failed"
    failed["error"] = error
    failed["final_answer"] = f"Task failed: unexpected error: {error}"
    return failed


def run_task(
    task: str,
    *,
    on_record: RecordCallback | None = None,
    llm: BaseChatModel | None = None,
    settings: Settings | None = None,
    logs_dir: Path | None = None,
) -> AgentState:
    """Run one task through the graph and return the final state.

    Args:
        task: the user's task in plain language.
        on_record: optional callback that receives every trace record as it is written, so a
            terminal or a UI can show live progress. An exception in it is ignored.
        llm: the chat model for all three agents. Default: built from the settings.
        settings: limits and flags. Default: `get_settings()`.
        logs_dir: folder for the trace file. Default: `logs/`.

    Returns:
        The final state. A crash gives `status="failed"` and `error="<Type>: <message>"`.

    Raises:
        ConfigError: if the settings are missing or invalid. There is no model to put in a
            trace and nothing ran, so this is the one exception `run_task` lets through.

    WHY: it never raises for anything else (LLM errors, usage caps, bugs), because the
    benchmark needs a result and a `run_end` for every run it starts.
    """
    # STEP 1: settings first. A ConfigError here means no run exists yet.
    settings = settings or get_settings()

    # STEP 2: open the trace and build the run's event handler.
    run_id = new_run_id()
    logger = TraceLogger(run_id, settings.ollama_model, logs_dir=logs_dir or LOGS_DIR)
    counters: dict[str, int] = dict.fromkeys(COUNTER_KEYS, 0)
    started = time.perf_counter()

    def emit(event: str, data: dict[str, Any]) -> None:
        """Count the event, write it to the trace, and pass the record to the caller."""
        _count_event(counters, event, data)
        record = logger.log(event, data)
        if on_record is None:
            return
        # WHY: a broken display must never stop a run or change its trace.
        try:
            on_record(record)
        except Exception:  # noqa: BLE001 - see WHY above
            pass

    # STEP 3: run. The state after the last graph step is kept, so a crash in the middle
    # still reports the plan and the partial results.
    try:
        state = initial_state(task, run_id)
    except ValueError as exc:
        # WHY: refuse a bad task before any LLM call is spent on it; the run still gets a
        # trace with its `run_end`.
        placeholder = initial_state("(invalid task)", run_id)
        placeholder["task"] = str(task)[:MAX_TASK_CHARS]
        state = _failed_state(placeholder, _error_text(exc))
        state["final_answer"] = f"Task failed: {exc}"
    else:
        try:
            graph = build_graph(llm=llm, settings=settings, on_event=emit)
            # WHY: `stream` and not `invoke`: if the run crashes, the last yielded state is
            # still available.
            for latest in graph.stream(
                state,
                config={"recursion_limit": recursion_limit(settings)},
                stream_mode="values",
            ):
                state = cast(AgentState, latest)
        except Exception as exc:  # noqa: BLE001 - every crash becomes a failed state
            state = _failed_state(state, _error_text(exc))

    # STEP 4: the one `run_end`.
    # WHY: written here and not in `finalize`, so a crashed run (where finalize never ran)
    # gets one too, and every run has exactly one.
    emit(
        "run_end",
        {
            "task": state["task"][:MAX_TASK_CHARS],
            "status": state["status"],
            "final_answer": state["final_answer"],
            "error": state["error"],
            "counters": {
                "plan_steps": len(state["plan"]),
                "steps_done": len(state["step_results"]),
                "replans": state["replans"],
                **counters,
            },
            "config": {**settings.model_dump(), "max_plan_steps": MAX_PLAN_STEPS},
            "duration_ms": int((time.perf_counter() - started) * 1000),
        },
    )
    return state


def main(argv: list[str] | None = None) -> int:
    """Command line entry: run one task and show the events live.

    Args:
        argv: the arguments after the program name. Default: `sys.argv[1:]`.

    Returns:
        The exit code: 0 when the run is done, 1 when it failed, 2 on a configuration error.
        A missing task makes `argparse` exit with 2 as well.
    """
    parser = argparse.ArgumentParser(
        prog="python -m src.run", description="Run one task with the multi-agent orchestrator."
    )
    parser.add_argument("task", help="the task, in plain language (put it in quotes)")
    args = parser.parse_args(argv)

    # WHY: `print_record` is the live display; it gets the same records the logger writes.
    try:
        state = run_task(args.task, on_record=print_record)
    except ConfigError as exc:
        # WHY: a setup mistake needs a short message, not a traceback.
        console.print(escape(sanitize(str(exc))), style=ERROR, highlight=False, emoji=False)
        return 2

    done = state["status"] == "done"
    console.print("")
    # WHY: the final answer is LLM text (and can echo web text), so it is sanitized like the
    # live events.
    console.print(
        escape(sanitize(f"Final answer: {state['final_answer']}")),
        style=SUCCESS if done else ERROR,
        highlight=False,
        emoji=False,
    )
    console.print(f"Trace: {escape(str(trace_path(state['run_id'])))}", highlight=False)
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
