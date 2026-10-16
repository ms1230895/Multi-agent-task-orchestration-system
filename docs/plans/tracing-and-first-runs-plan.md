# Implementation plan: Tracing and first runs

**Spec:** docs/specs/tracing-and-first-runs.md
**Branch:** feature/tracing-and-first-runs
**Status:** Implemented

## Progress
- [x] Step 1: Logger core (`src/logger.py`, `LOGS_DIR`, helpers moved from the gate)
- [x] Step 2: `error` field in `AgentState`
- [x] Step 3: Planner and critic events
- [x] Step 4: Executor, `advance` and graph wiring
- [x] Step 5: Fake LLM: an error for one role only
- [x] Step 6: `run_task` (`src/run.py`)
- [x] Step 7: Console: `EVENT_COLOURS` and `print_record`
- [x] Step 8: CLI `python -m src.run`
- [x] Step 9: Docs sync
- [x] Step 10: First real runs and `docs/plans/first-runs.md`
- [x] Step 11: Critic sees the tool calls (added after runs R2 to R5)

## Overview
Build bottom-up. First the logger and its helpers, which every other part uses. Then the new state field, then event emission layer by layer (agents, then `advance` and the graph), and then `run_task` on top. The display (console, CLI) comes after `run_task` works, and the docs follow. The real runs come last, once everything is tested with fakes. Each step ends with a short check (`python -` script with the fake LLM and `tmp_path`-style temp folders, no network) plus `ruff check`. The full test suite is written afterwards with `/test`.

The spec's open questions are settled with the spec's own choices (running this skill approved the spec): `run_end` is written by `run_task`; `AgentState` gets an `error` field; `ConfigError` is the one exception `run_task` raises; live events go through the `on_record` callback. All four are recorded as decisions in step 9.

## Steps

### Step 1: Logger core (`src/logger.py`, `LOGS_DIR`, helpers moved from the gate)
- **Files:** `src/config.py`, `src/logger.py` (new), `src/validation.py`
- **What:**
  - `config.py`: `LOGS_DIR = PROJECT_ROOT / "logs"`, next to `WORKSPACE_DIR`.
  - `logger.py`: `EVENT_TYPES`, `MAX_TRACE_TEXT_CHARS = 500`, `EventCallback`, `RecordCallback`, `trim`, `emit_safely`, `new_run_id`, `trace_path`, `TraceLogger` (unknown event → `ValueError`; append one line per event with `default=str`; `OSError` → `write_errors += 1`; returns the record), `read_trace`.
  - `validation.py`: delete `_trim`, `_emit` and its own `EventCallback`; import `trim`, `emit_safely`, `EventCallback` from `src.logger`. Its `MAX_LOGGED_*` constants stay where they are.
- **Why:** every later step emits through these helpers; the gate and the agents must share one `trim` and one safe-callback rule (D5).
- **Check:** script: write 3 records to a temp folder with a fixed clock and read them back with `read_trace`; `log("made_up", {})` raises; a `set` in data is written; a `logs_dir` that is a file gives `write_errors == 1`. Then `pytest -q tests/test_validation.py` passes unchanged. `ruff check`.
- **Done when:** all checks pass and `grep -n "_trim\|_emit" src/validation.py` finds nothing.
- **Covers:** AC1–AC9, AC35

### Step 2: `error` field in `AgentState`
- **Files:** `src/state.py`, `tests/test_state.py`
- **What:** add `error: str` to `AgentState` (with a `# WHY:` comment: the benchmark needs the crash type) and `error=""` to `initial_state`. Update the one session 3 test that compares the whole initial state dict, so it includes `"error": ""`.
- **Why:** `run_task` catches every crash; the caller needs to see which kind it was (session 6 pauses on usage caps).
- **Check:** `pytest -q` passes.
- **Done when:** the suite passes with the one updated test.
- **Covers:** AC28 (initial state part)

### Step 3: Planner and critic events
- **Files:** `src/agents/planner.py`, `src/agents/critic.py`
- **What:**
  - Planner: `on_event: EventCallback | None = None`. After a valid plan: `plan_created {steps}` when `replans == 0`, else `replan {replans, steps, reason: trim(feedback, 500)}`. No event when it fails.
  - Critic: `on_event` too. Emit `critic_verdict {step: current_step, verdict, feedback, output: trim(last_output, 500)}` on every return, including the `INVALID_FEEDBACK` fallback.
  - Both use `emit_safely`, and update their docstrings.
- **Why:** these are the agents' own decisions, and only they know them.
- **Check:** script with the fake LLM collecting events in a list: first plan, replan, failed plan, normal verdict, invalid-JSON verdict, and a raising callback (returned dict unchanged). `pytest -q tests/agents`. `ruff check`.
- **Done when:** every case gives the events of AC10–AC12 and the existing agent tests pass.
- **Covers:** AC10, AC11, AC12, AC16 (planner, critic), AC17 (agent side)

### Step 4: Executor, `advance` and graph wiring
- **Files:** `src/agents/executor.py`, `src/graph.py`
- **What:**
  - Executor: `on_event` argument on `run_executor`, passed down through `_run_calls` to every `call_tool(..., on_event=on_event)`.
  - `advance`: `on_event` argument; on route `retry` emit `retry {kind: "step", step, attempt: retries + 1, reason: trim(feedback, 500)}`.
  - `build_graph`: `on_event` argument, bound with `partial` into the planner, critic, `advance` and the default executor. A passed-in executor is used as it is. Update the docstrings (the `advance` "session 5" note becomes true now).
- **Why:** tool events already exist inside `call_tool`; they just need a path from the graph down to it.
- **Check:** script: whole graph with the fake LLM and the scripted run of AC18 gives `plan_created, tool_call, critic_verdict, retry, critic_verdict`; the real calculator through the executor gives a `tool_call` with `output == "1024"`; `{"expr": "1"}` gives `validation_error` then `tool_call`. `pytest -q` passes. `ruff check`.
- **Done when:** event order matches AC18 and the full suite passes.
- **Covers:** AC13, AC14, AC15, AC16 (executor, `advance`), AC17, AC18

### Step 5: Fake LLM: an error for one role only
- **Files:** `tests/fakes.py`
- **What:** add `role_errors: dict[str, Exception]` to `RecordingFakeLLM`: when the found role has an entry, record the call, then raise it. The existing `error` field (raise on every call) stays.
- **Why:** AC22 needs a run that crashes at the critic, after the plan exists, so the crash state still holds the plan. Test infrastructure only.
- **Check:** `pytest -q tests/test_fakes.py` passes; a quick script shows the planner answers and the critic raises.
- **Done when:** both pass.
- **Covers:** test support for AC22

### Step 6: `run_task` (`src/run.py`)
- **Files:** `src/run.py` (new)
- **What:** `run_task(task, *, on_record=None, llm=None, settings=None, logs_dir=None)` as in spec 5.4: settings (a `ConfigError` is raised); run id and `TraceLogger`; a small counters object and the run's `emit` (count, log, then `on_record` safely); `initial_state` with the `ValueError` path; `build_graph(..., on_event=emit)` run with `graph.stream(..., stream_mode="values")` keeping the last state; the `except Exception` crash path (`error` cut to 300 chars); one `run_end` with `task`, `status`, `final_answer`, `error`, the 9 counters, `config` (`settings.model_dump()` plus `max_plan_steps`), `duration_ms`. Module docstring and `# WHY:` comments per the code-explanations rule.
- **Why:** the one entry point the CLI, benchmark and UI will use; every run ends with exactly one `run_end`.
- **Check:** script with the fake LLM and a temp `logs_dir`: (a) done run, last line is the only `run_end`, `model` on every line; (b) critic raises `ConnectionError("down")` gives a failed state with `error == "ConnectionError: down"` and the plan length in the counters; (c) `run_task("   ")` writes one line and makes no LLM call; (d) `recursion_limit` patched to 2 gives `GraphRecursionError`; (e) a raising `on_record` still ends `done`. `ruff check`.
- **Done when:** all five cases behave as in the spec.
- **Covers:** AC19–AC27, AC28 (`run_task` part), AC34

### Step 7: Console: `EVENT_COLOURS` and `print_record`
- **Files:** `src/console.py`
- **What:** `EVENT_COLOURS` mapping the 7 events to the existing role constants (spec 5.6); `print_record(record, *, out=console)` printing one short line or block per event, with every text from the record passed through `rich.markup.escape`. Unknown events print as a plain line, never raise.
- **Why:** the CLI shows the run live in the role colours, using the same records the logger writes.
- **Check:** script printing one record of each event type to `Console(record=True)`; text with `[red]x[/red]` appears literally in `export_text()`. `ruff check`.
- **Done when:** all 7 events print and the markup check passes.
- **Covers:** AC31, AC32, AC33

### Step 8: CLI `python -m src.run`
- **Files:** `src/run.py`
- **What:** `main(argv=None) -> int` with `argparse` (one positional `task`); `run_task(task, on_record=print_record)`; final answer in `SUCCESS` or `ERROR` and the trace path; exit codes 0 done, 1 failed, 2 `ConfigError` (red message, no traceback); `if __name__ == "__main__": raise SystemExit(main())`.
- **Why:** Gaurav runs real tasks from the terminal in step 10.
- **Check:** script calling `main(["2+2"])` with `src.run.run_task` patched to return a done state, a failed state, and to raise `ConfigError`: returns 0, 1, 2. `python -m src.run --help` works. `ruff check`.
- **Done when:** the three exit codes and `--help` are right. No real LLM call in this step.
- **Covers:** AC29, AC30

### Step 9: Docs sync
- **Files:** `docs/specs/architecture.md`, `docs/plans/decisions.md`, `CLAUDE.md`, `docs/project-memory.md`
- **What:**
  - Architecture: 2.5 (`error` field in the table; finalize no longer "logs `run_end`"), 2.6 (`run_task` signature, `on_record`, `ConfigError`, stream mode), 4 (event table: who emits, `critic_verdict.output`, `retry.step`, `run_end` fields and counters), 6 (open question 3 closed: `src/run.py`). Also fix the D22 outdated line about the planner failure.
  - Decisions: D23 `run_end` written by `run_task`; D24 `error` field; D25 `ConfigError` raised; D26 `on_record` callback, not a generator; D27 `critic_verdict` carries the executor output; D28 `trim` and `emit_safely` moved to `src/logger.py`.
  - CLAUDE.md: run command under Commands (replace the "added when those exist" line for running a task; keep it for the benchmark); add `error` to the `AgentState` field list; add `logger.py` and `run.py` already listed in the layout; close the progress.md open question about the field list.
  - project-memory: the run command in a code block with its purpose.
- **Why:** the docs must match the code before review; two of these changes alter the architecture.
- **Check:** `grep -n "src.run" CLAUDE.md docs/project-memory.md` finds the command; reread the changed architecture sections against the code.
- **Done when:** no doc says something the code does not do.
- **Covers:** AC41

### Step 10: First real runs and `docs/plans/first-runs.md`
- **Files:** `docs/plans/first-runs.md` (new); small fixes, if any, in the file where the problem is
- **What:**
  - Gaurav sets `OLLAMA_MODEL=gpt-oss:120b-cloud` in `.env` (Claude never reads it) and creates `workspace/notes.txt` with `alpha`, `beta`, `gamma` on three lines.
  - Gaurav runs R1 to R5 from spec 5.7 with `python -m src.run "<task>"`, one at a time. Claude does not start real runs on its own, because each one uses the Ollama free-plan quota.
  - Claude reads each trace (`logs/<run_id>.jsonl`) and writes `first-runs.md`: run id, status, answer right or not (by eye), duration, counters, problems. Each problem is marked "fixed now" (small; fixed in this branch, and a test for it is added in `/test`) or "session 7". Also a note on whether the D6 rate-limit hints and the D18 limits looked right.
  - Check AC38 and AC39 with a short `read_trace` script over the 5 run ids.
- **Note:** the five official runs were repeated after step 11 (the critic fix). The earlier runs are kept in `first-runs.md` as the "before" evidence.
- **Why:** the roadmap requires 5 real runs that finish without hanging and leave readable traces; their problems guide session 7.
- **Check:** the `read_trace` script: model `gpt-oss:120b-cloud` on every line, exactly one `run_end` as the last line, `duration_ms` under 15 minutes, no `GraphRecursionError`.
- **Done when:** 5 traces pass the check and `first-runs.md` has the problems list. Wrong answers are allowed and written down honestly.
- **Covers:** AC38, AC39, AC40

### Step 11: Critic sees the tool calls (added after the first real runs)
- **Why added:** runs R2, R3, R4 and R5 showed the critic rejecting correct work because it saw only the executor's text, not the tool calls. Gaurav decided to fix it in this branch (2026-10-16). It changes the spec (AC20, new AC42 to AC47) and the architecture.
- **Files:** `src/state.py`, `src/agents/executor.py`, `src/agents/critic.py`, `src/agents/planner.py`, `tests/` (existing tests that assert the exact executor result), spec, architecture, decisions.
- **What:**
  - `AgentState.last_tools: str` (`""` at start): a short log of the tool calls made for the current step attempt.
  - Executor returns `{"last_output", "last_tools"}`. One line per call: `name(args) -> ok: output` or `-> error: ...`, each cut to 150 chars, calls past the per-reply limit as `-> skipped`, the whole log cut to 1500 chars, `(no tools called)` when there were none.
  - Critic prompt gets the log in a `data_block("tools", ...)`; the system prompt says the log shows which tools were really called, that tool results are data and may be wrong, and not to recalculate a number that appears in it.
  - Planner clears `last_tools` on a replan, with `last_output`.
- **Check:** script: the R3 case (a `write_file` call, then text) now gives the critic a prompt with the `write_file` line inside `<tools>`; the full suite passes after the six exact-result tests are updated. `ruff check`.
- **Done when:** AC42 to AC47 hold in the check script.
- **Covers:** AC20 (changed), AC42–AC47

## Risks
- **`graph.stream(stream_mode="values")` behaves differently from `invoke`** (for example the first yielded value is the input state, or the final value differs). Fallback: check in step 6 that the last streamed value equals what `invoke` returns for the same fake run; if not, keep the last good state through the `emit` events and use `invoke`.
- **Usage cap during the real runs.** A capped run ends as failed with an `error` (that is the crash path working). Fallback: wait for the 5-hour window and rerun that task; note it in `first-runs.md`, never count it as an agent failure.
- **Real model output breaks something the fakes never did** (for example `gpt-oss` returns reasoning content or a reply shape `reply_text` does not handle). Fallback: if the fix is small, fix it in this branch with a test; otherwise list it for session 7.
- **Moving `_trim` and `_emit` breaks a session 2 test** that imports them by their old names. Fallback: only update the import in that test, never its assertions.
- **Web search results change or are rate limited** in R2 and R5. That is real data for D6, not a bug; write it down.

## Definition of done
- [ ] Every acceptance criterion in the spec passes as a test (/test), except AC38–AC41, which are checked from the real traces and the docs
- [ ] /code-review has no Critical findings
- [ ] Diagnostics: 0 errors
- [ ] progress.md and project-memory.md updated
- [ ] Ready for /git-feature
