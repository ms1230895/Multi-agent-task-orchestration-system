# Architecture

This document describes what we build and how the parts fit together. Read it before changing the structure of the system. Design choices made later go in `docs/plans/decisions.md`; if one of them changes this document, update this document too.

## 1. Overview

A user gives the system a task in plain language. Three LLM agents work on it:

- **Planner** splits the task into a short list of steps.
- **Executor** carries out one step at a time, calling tools when needed.
- **Critic** checks each step's result and says "approve" or "retry".

Plain Python code (the **router**) reads the critic's verdict and decides what happens next. The LLM never decides where the graph goes; code does, with hard limits so no run can loop forever.

## 2. Layers

The system has 7 layers, plus 2 cross-cutting pieces used by every layer. Each layer only depends on the layers below it.

| # | Layer | Lives in | Job |
|---|---|---|---|
| 7 | Benchmark | `benchmark/` | Runs 30 fixed tasks, checks answers with code, reports metrics |
| 6 | Entry point | `src/run.py` | `run_task(task)`: builds the graph, runs it, returns the final state |
| 5 | Orchestration | `src/graph.py`, `src/state.py` | LangGraph graph, `AgentState`, router with hard limits |
| 4 | Agents | `src/agents/` | Planner, Executor, Critic: prompts + output parsing |
| 3 | Reliability | `src/validation.py` | Validation gate + retry around every tool call |
| 2 | Tools | `src/tools/` | web_search, calculator, read_file, write_file + registry |
| 1 | LLM | `src/config.py` (factory) | `ChatOllama` client, model name from `.env` |

Cross-cutting:

- **Config** (`src/config.py`): loads `.env` once, exposes typed settings (model name, flags, limits). Nothing else reads `.env` or environment variables directly.
- **Trace logger** (`src/logger.py`): writes one JSONL file per run in `logs/`. Every layer from 3 up logs events through it.
- **Console** (`src/console.py`): `rich` colour output for humans. Display only; never the source of truth (the trace is).

### 2.1 LLM layer
- One factory function, `get_llm()`, returns a `ChatOllama` built from config: `OLLAMA_MODEL`, `temperature` (default 0 for repeatable runs), and a request timeout.
- Models (free plan, both confirmed to support tool calling):
  - main: `gpt-oss:120b-cloud`
  - second: `gemma4:31b-cloud`
- The model name is never written in code (Hard rule 2). One run and one benchmark pass always use one model.

### 2.2 Tools layer
Four tools. Each one is a plain Python function plus a Pydantic argument schema.

| Tool | Arguments | Behaviour |
|---|---|---|
| `web_search` | `query: str`, `max_results: int = 3` (1–10) | Uses `ddgs`. Returns title, URL, and a snippet per result. Snippets trimmed (about 300 chars each, about 2000 chars total) so the LLM context stays small. |
| `calculator` | `expression: str` | Parses with `ast` and evaluates only allowed nodes: numbers, `+ - * / // % **`, unary minus, parentheses, and a small whitelist of `math` functions. Never `eval`/`exec` (Hard rule 3). Limits on exponent size and expression length. |
| `read_file` | `path: str` | Reads a text file inside `workspace/`. Size cap. |
| `write_file` | `path: str`, `content: str` | Writes a text file inside `workspace/`, creating parent folders. Size cap. |

Path safety for file tools: resolve the path (`Path.resolve()`) and reject it unless it stays inside `workspace/`. This blocks `..`, absolute paths, and symlinks that lead outside (Hard rule 4).

**Return format.** Every tool returns a dict and never raises (Hard rule 1):
```python
{"ok": bool, "output": str | None, "error": str | None}
```

**Registry** (`src/tools/__init__.py`): `TOOLS: dict[str, tuple[type[BaseModel], Callable]]`, mapping a tool name to its schema and function. `src/tools/definitions.py` builds the tool list bound to the executor's LLM (`llm.bind_tools(tool_definitions())`) from the registry: one entry per tool with its registry name, a short description (`TOOL_DESCRIPTIONS`), and the JSON schema of its Pydantic model. Passing the Pydantic classes directly would name the tools after the classes, and the gate would reject every call. The same list is used with `ENABLE_VALIDATION` on and off.

### 2.3 Reliability layer: the validation gate
Every tool call from the executor goes through one function, `call_tool(name, args) -> {ok, output, error}`. The steps run in this order:

1. **Tool exists?** If the name is not in the registry, return `ok=False`, `error="unknown tool: <name>. Available: ..."`.
2. **Arguments valid?** (only when `ENABLE_VALIDATION=true`) Validate `args` against the tool's Pydantic schema. On failure, return `ok=False` with a short readable list of what is wrong (field, problem). Log a `validation_error` event.
3. **Run** the function inside `try/except Exception`. Any exception becomes `ok=False, error="<type>: <message>"`.
4. **Retry** (only when `ENABLE_RETRY=true`): if the failure is *transient* (network error, timeout, rate limit from `ddgs`), wait (backoff 1 s, then 2 s, then 4 s at most) and run again, up to `TOOL_RETRY_ATTEMPTS` extra attempts. Log a `retry` event per attempt. A tool marks a failure as transient by starting its error text with `"transient: "` (`TRANSIENT_PREFIX` in `src/tools/base.py`), which keeps the return format exactly `{ok, output, error}`. In `ddgs` 9.x only timeouts have their own exception; a rate limit arrives as a plain `DDGSException`, so `web_search` also looks for rate-limit and timeout words in the message. Validation errors are never retried by the gate: the same bad arguments would fail again. Instead, the error goes back to the LLM so it can fix them (see Executor).
5. Log a `tool_call` event with name, args, ok, a trimmed output or error, attempts, and duration. `call_tool` does not write the trace itself: it sends events to an optional `on_event(event, data)` callback, and `run_task` connects the trace logger there. A callback that raises never changes the tool result.

When `ENABLE_VALIDATION=false`, step 2 is skipped. The raw arguments go straight to the function, and bad arguments fail inside it (caught by step 3). This is the "off" side of the ablation.

### 2.4 Agents layer
Each agent is a function `(state: AgentState) -> dict` that returns only the state fields it changes. Prompts live next to the agent code as constants.

**Planner** (`src/agents/planner.py`)
- Input: `task`. On a replan it also gets the old plan, the step results so far, and the critic's last feedback.
- On a replan, step results and the critic's feedback go into the prompt as `data_block`s (untrusted text, see Executor).
- Output: JSON `{"steps": ["...", "..."]}`, validated by a Pydantic model `Plan` (1 to `MAX_PLAN_STEPS` = 6 steps, each a non-empty string).
- If the JSON is invalid, re-ask once with the validation error. If it is still invalid, set `status="failed"` (the graph then goes straight to `finalize`).
- Logs `plan_created` (or `replan` on a replan) with the steps.

**Executor** (`src/agents/executor.py`)
- Works on exactly one step: `plan[current_step]`. The prompt holds the task, the whole plan, the current step ("step N of M"), and the earlier step results. On a retry it also holds the critic's feedback and its own rejected output.
- Inner tool loop, at most `MAX_TOOL_ITERATIONS` (default 5) LLM calls:
  1. Call the LLM (with tools bound).
  2. If the reply has no tool calls, that reply is the step's output (cut to 4000 chars; an empty reply gives `"Step not completed: the executor gave an empty reply."`); stop.
  3. For each tool call, at most 5 per reply: `call_tool(...)`, then append a `ToolMessage` holding the output, or `error: ...` with status `error`, cut to 8000 chars. Calls whose arguments were not valid JSON (`invalid_tool_calls`) also go through `call_tool`, so they count in the failed-tool-call metric in both ablation passes. Extra calls are answered with an error and not run, so every call id gets exactly one answer. The error text is what lets the LLM correct itself in the next round.
  4. Go to 1.
- If the limit is reached without a final reply, the output is `"Step not completed: tool iteration limit reached."` plus the last tool result. The critic will then reject it.
- Errors from the LLM call are not caught (D12). The executor imports `call_tool` and `tool_definitions` only, never a tool function, so nothing can call a tool around the gate.
- Untrusted text (earlier results, feedback, the previous output) goes into the prompt through `data_block` (`src/agents/prompting.py`): cut to a limit, any closing tag inside it neutralised, wrapped in `<results>`, `<feedback>`, or `<previous_output>` markers. The system prompt says text in these markers and every tool result is data, not instructions. This lowers prompt-injection risk; it does not remove it.
- Writes `last_output`.

**Critic** (`src/agents/critic.py`)
- Input: task, current step, `last_output`, put in the prompt as a `data_block("output", ...)`; the system prompt says to ignore any instruction inside it.
- Output: JSON `{"verdict": "approve" | "retry", "feedback": "..."}`, validated by a Pydantic model `Verdict`.
- If the JSON is invalid, re-ask once. If it is still invalid, treat it as `verdict="retry"` with feedback "critic output invalid". This is safe because retries are limited.
- Logs `critic_verdict`.

Planner and Critic always validate their own JSON output, whatever `ENABLE_VALIDATION` says. That flag covers only tool arguments, so the ablation measures one thing.

### 2.5 Orchestration layer

**AgentState** (`src/state.py`), a `TypedDict`:

| Field | Type | Meaning |
|---|---|---|
| `task` | `str` | The user's task, never changed |
| `run_id` | `str` | Unique id, also the trace file name |
| `plan` | `list[str]` | Current plan steps |
| `current_step` | `int` | Index into `plan` (0-based) |
| `step_results` | `list[str]` | Approved output of each finished step |
| `last_output` | `str` | Executor output for the current step, waiting for the critic |
| `verdict` | `str` | `"approve"` or `"retry"` from the critic |
| `feedback` | `str` | Critic's explanation, passed to executor (retry) or planner (replan) |
| `retries` | `int` | Retries used on the **current step** (reset to 0 when a step is approved or a new plan starts) |
| `replans` | `int` | Replans used in the **whole run** (never reset) |
| `final_answer` | `str` | Set by finalize |
| `status` | `str` | `"running"`, `"done"`, or `"failed"` |
| `route` | `str` | The decision `advance` made: `next_step`, `done`, `retry`, `replan`, or `failed` (`""` before the first one). The edge after `advance` only reads it. |
| `error` | `str` | `""`, or `"<ExceptionType>: <message>"` when `run_task` caught a crash or refused the task. Only `run_task` sets it. The benchmark reads the type to tell a usage cap or connection error from an agent failure. |

**Graph**

```mermaid
flowchart TD
    START([START]) --> planner
    planner -- "status running" --> executor
    planner -- "status failed" --> finalize
    executor --> critic
    critic --> advance
    advance -- "route next_step or retry" --> executor
    advance -- "route replan" --> planner
    advance -- "route done or failed" --> finalize
    finalize --> END([END])
```

**Router rules** (`decide(state, settings) -> Route` in `src/graph.py`), plain Python, no LLM, no state change. Rules are checked in this order:

1. `status == "failed"` (e.g. the planner gave up) → `failed`
2. `verdict == "approve"` and `current_step < len(plan) - 1` → `next_step`
3. `verdict == "approve"` and this is the last step → `done`
4. `retries < MAX_RETRIES` → `retry` (same step, with feedback)
5. `replans < MAX_REPLANS` → `replan`
6. otherwise → `failed`

Rules 4 to 6 cover every verdict that is not `"approve"`, so an unexpected verdict counts as a retry and never as success.

A conditional edge function in LangGraph should only *choose* the next node. The counter updates (`current_step += 1`, `retries += 1`, `replans += 1`, saving the approved output into `step_results`) happen in a small `advance` node that runs right after the critic. `advance` calls `decide`, applies the changes, and stores the decision in `state["route"]`. The edge function `route_after_advance` then only reads `route` (`next_step` and `retry` → executor, `replan` → planner, anything else → finalize). The decision is stored because, once the counters have moved, it can no longer be worked out from the new state. This keeps every state change inside a node, where the trace can see it.

There is also a conditional edge after the planner: if the planner gave up (`status == "failed"`, empty plan), the run goes straight to `finalize`, so the executor never runs on an empty plan.

On a replan, `advance` only increments `replans`. The planner then reads the old plan, step results and feedback, and returns the new plan with `current_step = 0`, `retries = 0` and `step_results`, `verdict` and `feedback` cleared. The old results reach the planner through its prompt, so useful work is not lost.

**Finalize** builds `final_answer`: the last approved step result if status is done, or a short failure message with the best partial result if failed. It writes no trace event: `run_task` writes `run_end` (section 2.6), so a crashed run, where `finalize` never runs, still gets one.

**Hard limits** (Hard rule 5): `MAX_RETRIES` (default 2), `MAX_REPLANS` (default 1), `MAX_TOOL_ITERATIONS` (default 5), `MAX_PLAN_STEPS` (6, a constant in `src/config.py`, not an `.env` setting), and LangGraph `recursion_limit` (`recursion_limit(settings)` in `src/graph.py`). The recursion limit is computed from the other limits, roughly `MAX_PLAN_STEPS × (MAX_RETRIES + 1) × (MAX_REPLANS + 1) × 4 + 10`, so the router's limits always trigger first. If the recursion limit is ever hit, that is a bug.

### 2.6 Entry point
`src/run.py`:
- `run_task(task, *, on_record=None, llm=None, settings=None, logs_dir=None) -> AgentState`: reads the settings, makes the `run_id` (`new_run_id()`, always made inside `run_task`), opens the `TraceLogger`, builds the graph with an `on_event` handler, runs it with `graph.stream(..., stream_mode="values")` and `recursion_limit`, and returns the last state. Streaming keeps the last good state, so a crash still reports the plan and partial results.
- **Live events:** `on_record(record)` is called with every trace record as it is written, so a terminal or UI shows the same events the logger writes. An exception in it is ignored.
- **Never raises, with one exception.** A `ConfigError` (missing or invalid setting) is raised before a run exists: there is no model to put in a trace. Anything else (LLM connection error, usage cap, timeout, `GraphRecursionError`, a bug) becomes a failed state with `error="<Type>: <message>"` and a `run_end`. An empty or too long task does the same, without any LLM call. `KeyboardInterrupt` is not caught, so an interrupted run has no `run_end` and is not counted.
- **`run_end` is written here**, not in `finalize`, so every run has exactly one, also after a crash.
- CLI: `python -m src.run "your task here"` prints the events live with `rich` (`print_record` in `src/console.py`), then the final answer and the trace path. Exit code 0 done, 1 failed, 2 configuration error.

### 2.7 Benchmark
- `benchmark/tasks.json`: 30 tasks. Each has `id`, `category` (calc, search, file, multi-tool), `task`, `checker` (checker name), and `expected` (checker arguments).
- `benchmark/checkers.py`: plain code, never an LLM judge (Hard rule 6). Planned checkers: `number` (with tolerance), `contains` (all / any keywords, case-insensitive), `regex`, `file_content` (a file in `workspace/` holds the expected text). Search tasks use facts that do not change over time, so the checker stays valid.
- `benchmark/runner.py`: runs tasks through `run_task`, applies checkers, saves results (see section 5).
- `workspace/` is reset before each task so tasks cannot affect each other.

## 3. Configuration (`.env` through `src/config.py`)

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_MODEL` | (required) | Model name. No default in code. |
| `OLLAMA_TEMPERATURE` | `0` | Repeatability |
| `OLLAMA_TIMEOUT_S` | `120` | Request timeout for one LLM call, so a hung call cannot hang a run |
| `ENABLE_VALIDATION` | `true` | Pydantic gate on tool arguments (ablation flag) |
| `ENABLE_RETRY` | `true` | Gate retries on transient tool errors (ablation flag) |
| `MAX_RETRIES` | `2` | Critic-driven retries per step |
| `MAX_REPLANS` | `1` | Replans per run |
| `MAX_TOOL_ITERATIONS` | `5` | Executor inner loop rounds per step |
| `TOOL_RETRY_ATTEMPTS` | `2` | Extra attempts per transient tool failure |

`config.py` validates these with Pydantic at start-up and stops with a clear message if one is missing or invalid. It never prints the values of secrets (Hard rule 7).

Ablation: the "off" pass sets `ENABLE_VALIDATION=false` and `ENABLE_RETRY=false`. Every other setting (limits, model, temperature) stays identical between the off and on passes, so the only difference measured is the gate.

## 4. Traces

One file per run: `logs/<run_id>.jsonl`, one JSON object per line:
```json
{"ts": "2026-10-16T12:00:00Z", "run_id": "...", "model": "gpt-oss:120b-cloud", "event": "tool_call", "data": {...}}
```
Every line carries `model`, so a trace can never be mixed up between models. The `run_end` event also records the task, the config flags and limits used, and the counters. Inside a run, layers send `(event, data)` to an `on_event` callback; `run_task` adds `ts`, `run_id` and `model`, writes the line, and passes the record to `on_record`. Long text fields are cut to 500 characters.

| Event | Written by | `data` fields |
|---|---|---|
| `plan_created` | planner, first valid plan | `steps` |
| `tool_call` | `call_tool`, every gated tool call | `tool`, `args`, `ok`, `output`/`error` (trimmed), `attempts`, `duration_ms` |
| `validation_error` | `call_tool`, Pydantic rejects tool args | `tool`, `args`, `errors` |
| `retry` | `call_tool` (kind `tool`) or `advance` (kind `step`) | `kind`, `attempt`, `reason`; plus `tool` (kind `tool`) or `step` (kind `step`) |
| `critic_verdict` | critic, every verdict | `step` (0-based), `verdict`, `feedback`, `output` (the executor output judged, trimmed) |
| `replan` | planner, valid new plan | `replans`, `steps`, `reason` (the critic's last feedback) |
| `run_end` | `run_task`, once per run, also after a crash | `task`, `status`, `final_answer`, `error`, `counters`, `config`, `duration_ms` |

`run_end.counters`: `plan_steps`, `steps_done`, `replans`, `tool_calls`, `failed_tool_calls`, `validation_errors`, `tool_retries`, `step_retries`, `critic_verdicts`. The event counts come from the events, not from the state, because `retries` in the state resets on every step. `run_end.config` is the settings plus `max_plan_steps`; a test fixes its exact keys so a secret can never enter a trace (Hard rule 7).

Traces are what we count for "reasoning traces across 100+ runs" and what we read when we debug failures.

## 5. Evaluation design

### Metrics (defined before measuring, so we cannot move the goalposts)
- **Task success rate** = tasks whose checker passes ÷ tasks attempted, per pass.
- **Failed tool call rate** = `tool_call` events with `ok=false` (after all gate retries) ÷ all `tool_call` events, per pass. We report the relative reduction from the off pass to the on pass, and also the absolute numbers.
- **Trace count** = number of `run_end` events in `logs/`.

### Passes, pausing, and resuming
A **pass** is one model + one config running all 30 tasks. The Ollama free plan has 5-hour and weekly usage caps, so a pass may have to stop and continue later.

- Each pass has a folder `benchmark/results/<pass_id>/` containing:
  - `manifest.json`: model, all config flags and limits, git commit, start time. Written once when the pass starts.
  - `results.jsonl`: one line per finished task (`task_id`, `run_id`, `success`, `status`, checker detail, tool-call counts).
- `python -m benchmark.runner --pass <pass_id>` starts a new pass, or resumes one that exists:
  - On resume, the runner compares the current config (model, flags, limits) with the manifest. **If anything differs, it refuses to run.** This is how we never mix models or settings in one pass.
  - Tasks already in `results.jsonl` are skipped.
- **Infrastructure errors are not task failures.** If Ollama returns a usage cap / rate limit / connection error, the runner stops the pass cleanly, does not write a result for that task, and prints "paused, resume later". The task runs again on resume. This keeps a usage cap from looking like an agent failure.
- Planned passes: main model off, main model on, second model on (and second model off if quota allows). Repeated passes are allowed (e.g. `main-on-2`) to reach 100+ runs and to see run-to-run variance.

## 6. Open questions
1. ~~Critic `replan` verdict~~ Decided in session 3: only `approve` / `retry`. A replan happens only after the step's retries are used up. Revisit after the session 7 traces show whether retries are often wasted. See `docs/plans/decisions.md`.
2. ~~`ENABLE_RETRY` scope~~ Decided in session 2: `ENABLE_RETRY` controls gate-level retries of transient tool errors only (counted by `TOOL_RETRY_ATTEMPTS`). Critic-driven step retries (`MAX_RETRIES`) stay on in both ablation passes. See `docs/plans/decisions.md`.
3. ~~Entry point location~~ Decided in session 5: `run_task` lives in `src/run.py` (also the CLI), so `src/graph.py` stays free of logging and CLI code. See `docs/plans/decisions.md`.
