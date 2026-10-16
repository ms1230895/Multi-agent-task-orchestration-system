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
