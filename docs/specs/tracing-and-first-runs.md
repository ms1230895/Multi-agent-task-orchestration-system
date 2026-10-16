# Spec: Tracing and first runs

**Status:** Approved
**Branch:** feature/tracing-and-first-runs
**Date:** 2026-10-16

## 1. Goal
Write every run as a JSONL trace in `logs/<run_id>.jsonl`, with the 7 event types from the architecture and `model` on every line. Add the entry point `run_task(task)`, which never raises, and the CLI `python -m src.run "<task>"`, which shows the events live. Then do the first 5 real runs with `gpt-oss:120b-cloud` and write down what went wrong.

## 2. Why
Until now the system only ran with fake LLMs, and nothing was recorded. Every CV number depends on traces: the failed-tool-call rate is counted from `tool_call` events, the trace count from `run_end` events, and session 7 finds failure causes by reading traces. The benchmark (session 6) also needs one function, `run_task`, that runs a task and always returns a result.

The first real runs test guesses made in earlier sessions with real model behaviour: the rate-limit hints in `web_search` (D6) and the executor limits (D18).

Architecture layers (`docs/specs/architecture.md` section 2): the cross-cutting trace logger (`src/logger.py`), layer 6 (entry point, `src/run.py`), and small changes in layers 3 to 5 so they emit events.

## 3. Scope
**In scope:**
- `src/logger.py` (new): `TraceLogger`, `new_run_id()`, `read_trace()`, the event names, and the shared helpers `EventCallback`, `emit_safely()` and `trim()` (the last two moved from `src/validation.py`).
- `src/validation.py`: import `EventCallback`, `emit_safely` and `trim` from `src/logger.py` instead of defining them. Behaviour unchanged.
- `src/agents/planner.py`, `src/agents/critic.py`: an optional `on_event` argument; emit `plan_created`, `replan` and `critic_verdict`.
- `src/agents/executor.py`: an optional `on_event` argument, passed on to every `call_tool`.
- `src/graph.py`: `advance` emits `retry` (kind `step`); `build_graph` takes `on_event` and gives it to every node.
- `src/state.py`: new field `error: str`.
- `src/config.py`: new constant `LOGS_DIR`.
- `src/run.py` (new): `run_task(...)` and the CLI `main(...)`.
- `src/console.py`: `EVENT_COLOURS` and `print_record(record)`, used by the CLI.
- 5 real runs with `gpt-oss:120b-cloud`, results in `docs/plans/first-runs.md` (new).
- Docs: `docs/specs/architecture.md` (who writes `run_end`, the `error` field, event fields, open question 3 closed), `docs/plans/decisions.md`, CLAUDE.md (run command, `AgentState` field list), `docs/project-memory.md` (run command).
- Unit tests, fully mocked.

**Out of scope:**
- The benchmark, its runner, and pausing on usage caps (session 6). This session only keeps the error type, so session 6 can tell infrastructure errors from agent failures.
- Large fixes found in the first runs: they are listed for session 7, not fixed here.
- New event types beyond the 7 (for example a `run_start` event, or logging every LLM reply).
- The Streamlit UI (session 8). `run_task` only offers the callback the UI will use.
- The deferred review items from sessions 2 to 4 (D8, D22), except the shared `trim` helper, which this spec needs.
- Any real LLM or internet call in unit tests.

## 4. Inputs and outputs

```python
# src/logger.py
EVENT_TYPES: frozenset[str]   # the 7 event names
EventCallback = Callable[[str, dict[str, Any]], None]   # (event, data), used inside the run
RecordCallback = Callable[[dict[str, Any]], None]       # (record), used by the caller

def new_run_id() -> str
def trace_path(run_id: str, logs_dir: Path = LOGS_DIR) -> Path
def trim(text: str, limit: int) -> str
def emit_safely(on_event: EventCallback | None, event: str, data: dict[str, Any]) -> None
def read_trace(path: Path) -> list[dict[str, Any]]

class TraceLogger:
    def __init__(self, run_id: str, model: str, *, logs_dir: Path = LOGS_DIR,
                 now: Callable[[], datetime] = <UTC now>) -> None
    def log(self, event: str, data: dict[str, Any]) -> dict[str, Any]   # returns the record
    write_errors: int

# src/run.py
def run_task(
    task: str,
    *,
    on_record: RecordCallback | None = None,
    llm: BaseChatModel | None = None,
    settings: Settings | None = None,
    logs_dir: Path | None = None,
) -> AgentState
def main(argv: list[str] | None = None) -> int     # exit code

# src/console.py
EVENT_COLOURS: dict[str, str]
def print_record(record: dict[str, Any], *, out: Console = console) -> None
```

**A trace record** (one line of the file, and what `on_record` receives):
```json
{"ts": "2026-10-16T12:00:00.123Z", "run_id": "20261016-120000-a1b2c3", "model": "gpt-oss:120b-cloud", "event": "tool_call", "data": {}}
```

**New signatures** (all new arguments are keyword-only and default to `None`, so existing calls keep working):
- `run_planner(state, *, llm, on_event=None)`
- `run_critic(state, *, llm, on_event=None)`
- `run_executor(state, *, llm, settings, on_event=None)`
- `advance(state, *, settings, on_event=None)`
- `build_graph(*, llm=None, settings=None, executor=None, on_event=None)`

**State:** new field `error: str`. It is `""` unless `run_task` caught an exception or refused the task; then it holds `"<ExceptionType>: <message>"`.

## 5. Design

### 5.1 `src/logger.py`
- `EVENT_TYPES = {"plan_created", "tool_call", "validation_error", "retry", "critic_verdict", "replan", "run_end"}`.
- `MAX_TRACE_TEXT_CHARS = 500`: the cut for long text fields in events (outputs, feedback, reasons).
- `new_run_id()`: `"YYYYMMDD-HHMMSS-<6 hex chars>"`, in UTC, for example `20261016-120000-a1b2c3`. **Why:** files sort by time in `logs/`, the random part prevents two runs in the same second from sharing a file, and the id has no characters that are unsafe in a file name. `run_task` always makes the id itself; callers cannot pass one, so no path can be smuggled in.
- `trace_path(run_id, logs_dir)`: `logs_dir / f"{run_id}.jsonl"`.
- `TraceLogger.log(event, data)`:
  1. If `event` is not in `EVENT_TYPES`, raise `ValueError`. **Why:** event names are chosen by our code, never by the LLM, so an unknown name is a bug a test should catch.
  2. Build the record `{ts, run_id, model, event, data}`. `ts` is UTC ISO 8601 with milliseconds and a `Z`.
  3. Create `logs_dir` if needed, open the file in append mode, write `json.dumps(record, ensure_ascii=False, default=str)` plus a newline, close. **Why:** one open-write-close per line means a crash in the middle of a run keeps every line written so far, and no file handle is left open. `default=str` means a strange value in the tool arguments (the LLM chooses them) can never break logging.
  4. If writing fails with `OSError`, add 1 to `write_errors` and continue. Never raise for a write problem: the run matters more than one lost line.
  5. Return the record.
- `read_trace(path)`: read the file and return one dict per non-empty line, in order. Used by the real-run checks now, and later by the benchmark and the trace explorer.
- `trim` and `emit_safely` are moved from `src/validation.py` (`_trim`, `_emit`) without changing what they do. `emit_safely` ignores any exception from the callback (D5).

### 5.2 Who emits which event

| Event | Emitted by | `data` |
|---|---|---|
| `plan_created` | planner, first plan (`replans == 0`), only when the plan is valid | `steps` |
| `replan` | planner, new plan after a replan (`replans > 0`), only when valid | `replans`, `steps`, `reason` (the critic's last feedback, trimmed) |
| `tool_call` | `call_tool` (already built) | `tool`, `args`, `ok`, `output` or `error`, `attempts`, `duration_ms` |
| `validation_error` | `call_tool` (already built) | `tool`, `args`, `errors` |
| `retry` | `call_tool` for kind `tool` (already built); `advance` for kind `step` | `kind`, `step` (kind `step` only), `tool` (kind `tool` only), `attempt`, `reason` |
| `critic_verdict` | critic, every verdict, including the fallback after invalid JSON | `step`, `verdict`, `feedback`, `output` |
| `run_end` | `run_task`, exactly once per run | `task`, `status`, `final_answer`, `error`, `counters`, `config`, `duration_ms` |

Details:
- `step` is the 0-based index, the same as `current_step` in the state, so a trace and a state can be compared without conversion.
- `critic_verdict.output` is the executor output being judged, cut to `MAX_TRACE_TEXT_CHARS`. **Why:** the executor has no event of its own, and without this field a trace would never show what the executor answered. It is the most useful line when we debug a failed step in session 7.
- `retry` (kind `step`) is emitted only when `advance` chooses `retry`. `attempt` is the new `retries` value (1 for the first retry), `reason` is the critic's feedback, trimmed.
- The planner emits nothing when it fails; the `run_end` line then has `status="failed"` and the planner failure message.
- The executor emits nothing itself. It passes `on_event` to every `call_tool`, which already emits `tool_call`, `validation_error` and `retry`.

**`run_end` written by `run_task`, not by `finalize`.** The architecture says finalize logs `run_end`. This spec moves it to `run_task`. **Why:** `run_end` must also be written when the graph crashes, and then finalize never runs. With one place, every run gets exactly one `run_end`, and `run_task` already has the settings and counters that `run_end` needs. `architecture.md` is updated.

**`run_end` fields:**
- `task`: the task text, cut to `MAX_TASK_CHARS`. **Why:** no other event records the task, and a trace must make sense on its own.
- `final_answer`: as in the state (at most about 4000 chars, set by the executor's cap).
- `error`: `""`, or `"<ExceptionType>: <message>"` with the message cut to 300 chars.
- `counters`: `plan_steps` (len of the last plan), `steps_done` (len of `step_results`), `replans`, `tool_calls`, `failed_tool_calls` (`tool_call` with `ok` false), `validation_errors`, `tool_retries` (`retry` kind `tool`), `step_retries` (`retry` kind `step`), `critic_verdicts`. The event counts are taken from the events of this run, not from the state, because `retries` in the state is reset on every step.
- `config`: `settings.model_dump()` plus `max_plan_steps`. A test checks the exact set of keys, so a future secret setting cannot enter the trace without someone changing the test on purpose (Hard rule 7).
- `duration_ms`: wall time of the run.

### 5.3 Event wiring in the graph
- `build_graph(..., on_event)` passes `on_event` with `partial` to the planner, critic, `advance` and the default executor (`partial(run_executor, llm=llm, settings=settings, on_event=on_event)`). An executor passed in by a test is used as it is.
- Every agent emits with `emit_safely`, so a broken callback never changes the state a node returns.
- With `on_event=None` everything behaves exactly as before, so the session 2 to 4 tests keep passing unchanged.
- **Why a callback and not LangGraph's own streaming:** the callback is plain Python that a learner can follow line by line, it works the same in `call_tool` (which is not a graph node), and tests can collect events in a list.

### 5.4 `src/run.py`: `run_task`
1. `settings = settings or get_settings()`. A `ConfigError` is raised here, before a run exists (see 7).
2. `run_id = new_run_id()`; `logger = TraceLogger(run_id, settings.ollama_model, logs_dir=logs_dir or LOGS_DIR)`.
3. Build the run's `emit(event, data)`: update the counters, `record = logger.log(event, data)`, then `emit_safely`-style call to `on_record(record)` if given (an exception from `on_record` is ignored).
4. `state = initial_state(task, run_id)`. If it raises `ValueError` (empty or too long task), make a failed state (`status="failed"`, `final_answer="Task failed: <reason>"`, `error="ValueError: <reason>"`) without calling the LLM, and go to step 7.
5. `graph = build_graph(llm=llm, settings=settings, on_event=emit)`. Run it with `graph.stream(state, config={"recursion_limit": recursion_limit(settings)}, stream_mode="values")`, keeping the last state it yields. **Why `stream` and not `invoke`:** if the run crashes in the middle, we still have the last good state (plan, counters, partial results) for the trace.
6. If any `Exception` is raised in step 5 (LLM connection error, usage cap, timeout, `GraphRecursionError`, a bug): take the last good state, set `status="failed"`, `error="<Type>: <message>"` (message cut to 300 chars), and `final_answer="Task failed: unexpected error: <error>"`.
7. Emit `run_end` (5.2) and return the state.

`run_task` never raises, except `ConfigError` in step 1. `KeyboardInterrupt` is not an `Exception` and is not caught: Ctrl-C stops the run, and the trace has no `run_end`, so the interrupted run is not counted as one of the 100+ runs.

**Why the `error` field:** session 6 must pause the benchmark on a usage cap or connection error instead of counting it as an agent failure (architecture section 5). Since `run_task` catches every crash, the caller needs a way to see what kind of crash it was. The error type at the start of `error` gives it.

### 5.5 `src/run.py`: the CLI
- `python -m src.run "your task"`: `main(argv)` parses one positional argument `task` with `argparse`.
- It calls `run_task(task, on_record=print_record)`, so each event is printed as it happens.
- At the end it prints the final answer (green when done, red when failed) and the trace file path.
- Exit codes: `0` done, `1` failed, `2` configuration error (the `ConfigError` message is printed in red, without a traceback). `argparse` also exits with `2` when the task is missing.
- `if __name__ == "__main__": raise SystemExit(main())`.

### 5.6 `src/console.py`
- `EVENT_COLOURS`: `plan_created` → `PLANNER`, `replan` → `RETRY`, `tool_call` → `TOOL`, `validation_error` → `ERROR`, `retry` → `RETRY`, `critic_verdict` → `CRITIC`, `run_end` → `SUCCESS`. Only the existing role constants are used (colour-coding rule).
- `print_record(record, out=console)`: one short line or block per event, for example:
  - `plan_created`: "Plan:" and the numbered steps.
  - `tool_call`: `tool(args) → output`, in `ERROR` when `ok` is false.
  - `critic_verdict`: the executor output in `EXECUTOR`, then the verdict and feedback in `CRITIC`.
  - `run_end`: status, in `SUCCESS` or `ERROR`, and the counters.
- **All text from the record is escaped** (`rich.markup.escape`) before printing. **Why:** LLM and web text can contain rich markup like `[red]` or `[link=...]`, which would otherwise change the terminal output.

### 5.7 The first real runs
Gaurav sets `OLLAMA_MODEL=gpt-oss:120b-cloud` in `.env` (Claude never reads it, Hard rule 7) and creates `workspace/notes.txt` with three lines: `alpha`, `beta`, `gamma`. Then he runs:

| # | Tool | Task | A good answer |
|---|---|---|---|
| R1 | calculator | `What is (17 * 23) + 2 ** 10? Use the calculator.` | 1415 |
| R2 | web_search | `Search the web: in which year was the Eiffel Tower completed?` | 1889 |
| R3 | write_file | `Write the text 'hello from the agent' to a file called hello.txt.` | `workspace/hello.txt` holds the text |
| R4 | read_file | `Read the file notes.txt and tell me what its second line says.` | beta |
| R5 | multi-tool | `Search the web for the height of Mount Everest in metres, convert it to feet with the calculator (1 m = 3.28084 ft), and write the result to everest.txt.` | about 29 032 ft in `workspace/everest.txt` |

For each run, `docs/plans/first-runs.md` records: run id, status, whether the answer was right (judged by Gaurav by eye; this is not the benchmark), duration, counters, and every problem seen in the trace. Each problem is marked "fixed now" (small, fixed in this branch with a test) or "session 7". The file also says whether the D6 rate-limit hints and the D18 limits looked right.

A failed or wrong answer is fine and is written down honestly. The goal of these runs is that they finish and their traces are readable, not that they succeed.

## 6. Acceptance criteria

**Logger** (`tests/test_logger.py`, logs in `tmp_path`)
- [ ] AC1: `new_run_id()` matches `^\d{8}-\d{6}-[0-9a-f]{6}$`, and two calls return different ids.
- [ ] AC2: With a fixed clock, `log("tool_call", {"a": 1})` writes one line to `logs_dir/<run_id>.jsonl` that parses to exactly `{"ts": "<fixed time>Z", "run_id", "model", "event": "tool_call", "data": {"a": 1}}`, and returns that same dict.
- [ ] AC3: Three `log` calls give three lines in call order; a missing `logs_dir` is created.
- [ ] AC4: Data holding a value JSON cannot encode (a `set`, an object) is written with `str()`, and `log` does not raise.
- [ ] AC5: `log("made_up", {})` raises `ValueError`; `EVENT_TYPES` has exactly the 7 names.
- [ ] AC6: When the file cannot be written (`logs_dir` is an existing file), `log` does not raise, `write_errors == 1`, and the record is still returned.
- [ ] AC7: `read_trace` returns the records in order and skips empty lines.
- [ ] AC8: `emit_safely(None, ...)` does nothing; with a callback that raises, it does not raise.
- [ ] AC9: `trim("x" * 600, 500)` has length 500 and ends with `…`; text within the limit is unchanged.

**Events from each layer** (fake LLM; events collected in a list)
- [ ] AC10: The planner's first valid plan emits exactly one `plan_created` with `steps`; a planner that fails (invalid twice) emits nothing.
- [ ] AC11: A valid replan (`replans=1`) emits exactly one `replan` with `replans == 1`, the new `steps` and `reason` equal to the trimmed old feedback, and no `plan_created`.
- [ ] AC12: The critic emits exactly one `critic_verdict` with `step == current_step`, `verdict`, `feedback`, and `output == trim(last_output, 500)`; after two invalid replies it emits one with `verdict == "retry"` and `feedback == INVALID_FEEDBACK`.
- [ ] AC13: `advance` emits one `retry` with `kind == "step"`, `step`, `attempt == retries + 1` and the trimmed feedback as `reason` when the route is `retry`, and no event for `next_step`, `done`, `replan` or `failed`.
- [ ] AC14: The executor with the real `call_tool` and calculator (no network) emits one `tool_call` with `tool == "calculator"`, `ok` true and `output == "1024"` for `{"expression": "2 ** 10"}`.
- [ ] AC15: With `enable_validation=True`, a calculator call with `{"expr": "1"}` emits `validation_error` and then `tool_call` with `ok` false, in that order.
- [ ] AC16: A callback that raises does not change the dict returned by the planner, critic, `advance` or executor.
- [ ] AC17: With `on_event=None` (the default) the session 2 to 4 tests pass unchanged.
- [ ] AC18: Whole graph via `build_graph(llm=fake, settings=s, on_event=collect)`: a 1-step plan, one calculator call, a final text, a `retry` verdict, a second final text, then `approve` gives the event names `plan_created, tool_call, critic_verdict, retry, critic_verdict`, in that order.

**`run_task`** (`tests/test_run.py`, fake LLM, logs in `tmp_path`)
- [ ] AC19: A successful run returns a state with `status == "done"`, `error == ""`, and the `run_id`; `logs_dir/<run_id>.jsonl` exists; every line has `model == settings.ollama_model` and `run_id == state["run_id"]`; the last line is the only `run_end`.
- [ ] AC20: `run_end.data` has exactly the keys `task`, `status`, `final_answer`, `error`, `counters`, `config`, `duration_ms`; `config` has exactly the `Settings` field names plus `max_plan_steps`.
- [ ] AC21: For a scripted run with 2 tool calls (1 failed by a validation error) and 1 step retry, `counters` equals the expected dict for all 9 counter keys.
- [ ] AC22: A fake LLM that raises `ConnectionError("down")` at the critic: `run_task` does not raise, `status == "failed"`, `error == "ConnectionError: down"`, `final_answer` starts with `"Task failed: unexpected error"`, `plan_steps` in `run_end` is the plan length, and the trace ends with one `run_end` carrying the same `error`.
- [ ] AC23: With `recursion_limit` patched to return 2, the `GraphRecursionError` becomes a failed state whose `error` starts with `"GraphRecursionError"`, plus a `run_end`.
- [ ] AC24: `run_task("   ")` does not raise, makes no LLM call, returns `status == "failed"` with `error` starting with `"ValueError"`, and writes a trace with exactly one line, `run_end`.
- [ ] AC25: `on_record` receives every record, in the same order and equal to the lines in the file; an `on_record` that raises does not stop the run, which still ends `done` with a complete trace.
- [ ] AC26: Every path (done, failed by limits, crash, invalid task) writes exactly one `run_end`.
- [ ] AC27: With `settings=None` and `get_settings` patched to raise `ConfigError`, `run_task` raises `ConfigError` and no trace file is created.
- [ ] AC28: `initial_state(...)` has `error == ""`, and the state from `run_task` has every `AgentState` key.

**CLI and console** (`tests/test_run.py`, `tests/test_console.py`)
- [ ] AC29: `main(["2+2"])` with `run_task` patched returns `0` for a done state and `1` for a failed one, and prints the final answer and the trace path.
- [ ] AC30: `main(["2+2"])` when `run_task` raises `ConfigError` returns `2` and prints the message, no traceback.
- [ ] AC31: `EVENT_COLOURS` has exactly the keys of `EVENT_TYPES`, and every value is one of the role constants in `src/console.py`.
- [ ] AC32: `print_record` prints a record of each of the 7 event types to a recording `Console` without raising.
- [ ] AC33: A record whose text contains `[red]x[/red]` is printed literally (the recorded text contains `[red]x[/red]`).

**Source rules**
- [ ] AC34: `src/logger.py` and `src/run.py` contain no model name (`gpt-oss`, `gemma`) and do not read the environment (`os.environ`, `getenv`, `load_dotenv`) (test reads the source).
- [ ] AC35: `src/validation.py` no longer defines `_trim` or `_emit`, and all session 2 validation tests pass.

**Project checks**
- [ ] AC36: `pytest -q` passes with no network access and no LLM calls.
- [ ] AC37: `ruff check .` reports no errors.

**Real runs** (not unit tests; checked by code over the trace files)
- [ ] AC38: `docs/plans/first-runs.md` lists 5 run ids, one per task R1 to R5. For each, `read_trace(trace_path(run_id))` shows `model == "gpt-oss:120b-cloud"` on every line and exactly one `run_end`, as the last line.
- [ ] AC39: Every one of those runs has `run_end.data.duration_ms` below 15 minutes (no run hung) and an `error` that does not start with `"GraphRecursionError"`.
- [ ] AC40: `docs/plans/first-runs.md` has a problems list where each problem is marked "fixed now" or "session 7".
- [ ] AC41: CLAUDE.md "Commands" and `docs/project-memory.md` contain `python -m src.run "<task>"`.

## 7. Edge cases and errors
- **Missing or invalid `.env` setting:** `run_task` raises `ConfigError` before any run starts. There is no model to put in a trace, and nothing ran. The CLI turns it into exit code 2 and a red message.
- **Empty or too long task:** a failed state and a one-line trace (`run_end`), no LLM call (AC24).
- **LLM connection error, usage cap, timeout:** caught by `run_task`, kept as `error` with its type, run ends as failed with a `run_end` (AC22). Session 6 uses the type to pause the benchmark.
- **`GraphRecursionError`:** caught the same way (AC23). It should never happen (the router's limits come first); if it does, it is a bug to fix.
- **Ctrl-C:** not caught; the run stops and its trace has no `run_end`, so it is not counted.
- **The logger cannot write** (disk full, no permission): the run continues, lines are lost, `write_errors` counts them (AC6). The CLI still shows events, because `on_record` gets the record either way.
- **`on_record` or `on_event` raises:** ignored; the run and the trace are not affected (AC16, AC25).
- **Strange tool arguments** (the LLM chooses them, any type): written with `default=str` (AC4); long strings are already trimmed by `call_tool`.
- **Rich markup in LLM or web text:** escaped before printing (AC33).
- **Secrets:** the trace holds the task, tool arguments, trimmed tool output, and the config without secrets (AC20). Web snippets and file contents are written to `logs/`, which is in `.gitignore`. Never paste a trace with private data into the repo.
- **Two runs in the same second:** the 6 random hex characters make a shared file name very unlikely (1 in 16 million).
- **Real run uses the wrong model:** visible on every line of the trace; AC38 checks it.
- **A real run fails or answers wrong:** expected and fine; it is written down in `first-runs.md`, not hidden.

## 8. Test plan
All unit tests: no LLM, no internet (Hard rule 8). LLMs are `RecordingFakeLLM` from `tests/fakes.py`; traces go to pytest's `tmp_path`; the clock is a fixed function.

| Test file | Covers |
|---|---|
| `tests/test_logger.py` (new) | AC1–AC9 |
| `tests/agents/test_planner.py` (updated) | AC10, AC11, AC16 |
| `tests/agents/test_critic.py` (updated) | AC12, AC16 |
| `tests/agents/test_executor.py` (updated) | AC14, AC15, AC16 |
| `tests/test_graph.py` (updated) | AC13, AC16, AC18 |
| `tests/test_state.py` (updated) | AC28 (initial state) |
| `tests/test_run.py` (new) | AC19–AC30, AC34 |
| `tests/test_console.py` (updated) | AC31–AC33 |
| `tests/test_validation.py` (unchanged, must pass) | AC17, AC35 |

AC36 and AC37: run `pytest -q` and `ruff check .`. AC38 to AC41 are checked after the real runs, with a short `read_trace` check in the terminal and by reading the docs.

## 9. Open questions
1. **`run_end` from `run_task` instead of `finalize`.** This changes one line of the architecture, so a crash still gets a `run_end` and there is exactly one per run. OK?
2. **New `AgentState` field `error`.** Needed so the benchmark can tell a usage cap from an agent failure after `run_task` catches it. The alternative is returning a separate result object next to the state, which changes the `run_task` signature from the architecture. Add the field?
3. **`ConfigError` is the one exception `run_task` raises.** Without a model there is nothing to trace. The alternative is to return a failed state with no trace file. Raise it?
4. **Live events by callback (`on_record`), not a generator.** A callback is simpler and works in `call_tool`, which is not a graph node; Streamlit can update the page from a callback. A generator would need a thread or LangGraph's streaming. Callback OK?
