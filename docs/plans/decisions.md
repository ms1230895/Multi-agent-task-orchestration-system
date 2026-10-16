# Decisions

Design choices made while building, with the reason. Newest at the bottom. If a decision changes `docs/specs/architecture.md`, that file is updated too.

## Session 2: foundation-tools-validation (2026-10-16)

### D1. Gate retries use `TOOL_RETRY_ATTEMPTS`, not `MAX_RETRIES`
`MAX_RETRIES` is the critic's per-step retry limit. The gate's retries of transient tool errors have their own counter, `TOOL_RETRY_ATTEMPTS` (default 2). **Why:** the ablation switches `ENABLE_RETRY` and `ENABLE_VALIDATION` must change only the gate. With one shared number, switching the gate off would also change how often the critic can retry a step. This also settles architecture open question 2: `ENABLE_RETRY` controls gate-level retries only, and critic-driven retries stay on in both ablation passes.

### D2. `web_search` returns 3 results by default
The default `max_results` is 3 (range 1 to 10), not 5. **Why:** fewer results keep the LLM context small, and the top few are usually enough for the benchmark's fact questions.

### D3. A transient failure is marked by an error prefix
A tool marks a failure as worth retrying by starting its error with `"transient: "` (`TRANSIENT_PREFIX` in `src/tools/base.py`). **Why:** it keeps the return format exactly `{ok, output, error}` (Hard rule 1). A fourth key `retryable` would be clearer in code but would change the rule everything else relies on. Revisit if more tools need richer error information.

### D4. `MAX_REPLANS` defaults to 1
`MAX_REPLANS=1`, as in the architecture; `.env.example` said 2 and was changed to match. **Why:** a replan costs a whole new plan and its steps. One replan is enough to test the idea, and it keeps the run count per benchmark task low under the Ollama usage caps.

### D5. `call_tool` reports events through an `on_event` callback
The gate sends `validation_error`, `retry` and `tool_call` events to an optional callback, and does not know about the logger. **Why:** the trace logger arrives in session 5. With a callback, session 5 connects it without changing `call_tool`, and tests can collect events in a list.

### D6. `web_search` also detects rate limits from the message text
In `ddgs` 9.16 only timeouts raise `TimeoutException`. `RatelimitException` exists but is never raised; a rate limit arrives as a plain `DDGSException` with the engine's message, and an empty search raises `DDGSException("No results found.")`. So `web_search` treats that exact message as zero results (success), and marks a plain `DDGSException` as transient when its text contains a rate-limit or timeout hint (`ratelimit`, `rate limit`, `429`, `timed out`, `timeout`, `connection`). **Why:** relying on the exception type alone would make the retry feature never trigger for real rate limits. **Risk:** the hint list is a guess based on reading the source, not on observed failures; check the real error texts in the traces from session 5 and adjust.

### D7. Config limits have upper bounds
Each limit has a range (for example `MAX_RETRIES` 0 to 5, `OLLAMA_TIMEOUT_S` 1 to 600), and a value outside it stops start-up with a `ConfigError`. **Why:** Hard rule 5 (no run may hang). A typo like `MAX_RETRIES=200` should be refused, not run for hours. The error text names the variable but never shows the value (Hard rule 7).

### D8. Code-review fixes (session 2)
The `/code-review` found one verified Critical issue and four Warnings. All were fixed, with tests (suite grew from 197 to 227 tests).
- **`round()` hang (Critical).** `round(1, -N)` makes Python compute `10 ** N`; N = 10 million took 8 s and N = 100 million did not finish in 20 s. The calculator now rejects `round` digits outside -100 to 100 (`MAX_ROUND_DIGITS`). **Why:** Hard rule 5.
- **Non-string tool name.** `call_tool` returns `tool name must be a string` instead of raising `TypeError` on an unhashable name. The name is also cut to 100 characters wherever it is shown or logged. **Why:** Hard rule 1, and the name is LLM-controlled.
- **`web_search` checks its own input** (query length, `max_results` 1 to 10, no bool), like the other tools. **Why:** with `ENABLE_VALIDATION=false`, raw arguments reach the tool, and the ablation baseline must not send bad calls to the network.
- **`_trim` returns exactly the limit** (500 in total, not 501), as the spec says.
- **Read and write share one unit.** `write_file` now also limits the encoded size to 100 000 bytes, so any file it writes can be read back.
- **Safer error text.** The catch-all in the file tools returns only `unexpected error: <ClassName>`, and `web_search` replaces URLs with `<url>` and cuts the message to 200 characters. **Why:** messages go to the LLM and into traces and could hold local paths or proxy passwords.
- **`MAX_INT_BITS` is 14 000** (was 100 000), because Python cannot turn an int of more than 4300 digits into text anyway.

**Deferred (not fixed, decide later):** a symlink swap between the path check and the file use (`O_NOFOLLOW`); marking tool output as untrusted data (belongs in the session 4 executor spec); trimming nested values in `_loggable_args`; one shared `_trim` helper; a structural `for` loop in the retry; more `# WHY:` comments; spec drift (transient hints, the `tool` key in `retry` events, calculator error texts, `load_dotenv(path)`, unticked AC boxes); small items (`.env.example` newline, magic numbers, stronger wrong-shape and config-environment tests).

## Session 3: graph-planner-critic (2026-10-16)

### D9. `AgentState` has a `route` field
`advance` calls `decide()`, moves the counters, and stores its decision in `state["route"]`. `route_after_advance` only reads it. **Why:** after the counters move (for example `current_step += 1`), the decision cannot be worked out again from the new state. The alternative, a router that decides before `advance` runs, would force `advance` to leave the counters alone and break the rule that every state change happens inside a node.

### D10. A conditional edge after the planner
If the planner gives up (`status="failed"`, empty plan), the run goes planner → finalize. **Why:** the executor would otherwise index into an empty plan.

### D11. The planner clears `step_results`, not `advance`
On a replan, `advance` only increments `replans`. The planner reads the old plan, step results and feedback, then returns a new plan with `current_step`, `retries`, `step_results`, `verdict` and `feedback` reset. **Why:** if `advance` cleared `step_results`, the planner could not show the old results in its prompt, and the architecture says it should.

### D12. Agents do not catch LLM errors
Connection errors, usage caps and timeouts raised by `llm.invoke` pass through the planner and critic to the caller. **Why:** the benchmark must pause on infrastructure errors and not count them as agent failures (architecture section 5). Hard rule 1 (never raise) covers tools, not agents. `run_task` (session 5) catches crashes.

### D13. The critic has only `approve` and `retry`
No third `"replan"` verdict. A replan happens only after the step's retries are used up. **Why:** one fewer route to build and test. **Revisit:** after the session 7 traces show whether retries are often wasted.

### D14. `MAX_PLAN_STEPS` is a constant, not an `.env` setting
`MAX_PLAN_STEPS = 6` lives in `src/config.py`. **Why:** it is a fixed design limit used by `Plan` and `recursion_limit`, and it must be the same in every benchmark pass.

### D15. No event emission yet
The planner, critic and `advance` have no `on_event` callback. **Why:** the logger arrives in session 5, which adds event emission and the `run_task` entry point together.

## Session 4: executor (2026-10-16)

### D16. Tool definitions are built from the registry
`tool_definitions()` in `src/tools/definitions.py` returns one OpenAI-style function dict per registered tool, with the registry name, a short description and the Pydantic JSON schema. **Why:** `bind_tools` with the Pydantic classes would name the tools `CalculatorArgs` and so on, and the gate would reject every call as unknown. The same list is used in both ablation passes, so only the gate differs.

### D17. Invalid tool calls also go through the gate
A reply's `invalid_tool_calls` (arguments that were not valid JSON) are passed to `call_tool` with the raw argument string. **Why:** every call the LLM makes should count in the failed-tool-call metric the same way in both passes, and the LLM gets the error text to fix its next call.

### D18. Executor limits
At most 5 tool calls run per reply (the rest are answered with an error), tool results are cut to 8000 chars, and `last_output` to 4000 chars (equal to the critic's view). **Why:** one reply must not start dozens of searches, and prompts must stay small. **Risk:** these are guesses; check the first real traces (session 5) for tasks that fail because of them.

### D19. The executor sees its previous output on a retry
With the critic's feedback it also gets its rejected output (cut to 1000 chars). **Why:** it can fix the attempt instead of starting over.

### D20. Untrusted text goes through `data_block`
`data_block(tag, text, limit)` cuts the text, neutralises any closing tag for `tag` inside it, and wraps it in markers. Used for the critic's `<output>`, the planner's replan `<results>` and `<feedback>`, and the executor's context. The system prompts say marked text is data. **Why:** this fixes the two security Warnings from the session 3 review (tool output could close the critic's `<output>` early and force an approval). It lowers prompt-injection risk; it does not remove it.

### D21. `stub_executor` moved to `tests/fakes.py`
It is gone from `src/`; the graph tests pass it explicitly. `RecordingFakeLLM` finds its role by "You are the planner / critic / executor" in the system prompt and raises on an unknown role (review Suggestion S7).

### D22. Deferred from the session 3 code review
Not fixed in session 4:
- **Warnings:** one shared `ask_validated` helper for planner and critic (duplicated re-ask flow); a whole-graph test that the state is clean after a replan (AC38); two outdated lines in `architecture.md` (the planner failure "router then finalizes", and finalize "logs `run_end`", which is session 5); progress and AC checkboxes.
- **Suggestions:** cap `parse_json_model` input and make the fence regex linear; cap `last_output` stored in `step_results` (the executor now caps new outputs at 4000); `recursion_limit` enforced in `build_graph`; misplaced `# WHY:` in `finalize`; unused `Status` and `VerdictValue` aliases; weak AC26 assertion; `or` defaults in `build_graph` (the executor default is now `is not None`); a test for the brace limitation in `parse_json_model`; missing docstrings and `# WHY:` in tests.

## Session 5: tracing-and-first-runs (2026-10-16)

### D23. `run_task` writes `run_end`, not `finalize`
`finalize` writes no event. `run_task` writes the one `run_end` after the graph ends or crashes. **Why:** a crash means `finalize` never runs, and every run must have exactly one `run_end` (it is what we count as a traced run). It also lets `run_end` hold the settings, counters and duration, which `run_task` already has. This changes one line of the architecture, which is updated.

### D24. `AgentState` has an `error` field
`""` normally; `"<ExceptionType>: <message>"` (message cut to 300 chars) when `run_task` caught a crash or refused the task. **Why:** `run_task` never raises, so the benchmark needs another way to see that a run ended because of a usage cap or connection error and must be paused, not counted as an agent failure (D12). The alternative, a separate result object, would change the `run_task` signature.

### D25. `ConfigError` is the one exception `run_task` raises
**Why:** without valid settings there is no model name to put in a trace and nothing ran. The CLI turns it into exit code 2 and a short message.

### D26. Live events by callback, not a generator
`run_task(on_record=...)` is called with every trace record. **Why:** it works inside `call_tool`, which is not a graph node, a learner can follow it line by line, and Streamlit can update a page from a callback. A generator would need a thread or LangGraph streaming.

### D27. `critic_verdict` carries the executor output
The event has an `output` field (cut to 500 chars). **Why:** the executor has no event of its own, and without this a trace never shows what the executor answered, which is the first thing needed to debug a failed step in session 7.

### D28. `trim` and `emit_safely` live in `src/logger.py`
Moved from `src/validation.py` (`_trim`, `_emit`), behaviour unchanged. **Why:** the planner, critic, `advance` and the gate all need them, and one copy keeps the safe-callback rule (D5) in one place. Partly settles the "one shared `_trim` helper" item deferred in D8.

### D29. The test spy for `call_tool` accepts `on_event`
`CallToolSpy` in `tests/agents/test_executor.py` got an `on_event` keyword. **Why:** the executor now passes `on_event` to every `call_tool`, as the real gate accepts it. Spec AC17 said the old tests should pass unchanged; this is the one test helper that had to change, and no assertion changed.

