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

### D30. The critic sees the tool calls (`last_tools`)
Added after the first real runs. The executor writes a short log of its tool calls to the new state field `last_tools`, and the critic gets it in a `<tools>` data block. **Why:** in runs R2 to R5 the critic saw only the executor's text and rejected correct work ("does not show the tool being invoked"). R3 used 3 retries and a replan for a one-line file write. It also "corrected" a right calculator result with wrong arithmetic (R5). Wasted retries cost quota and would lower the benchmark success rate for a reason unrelated to the agent's skill. **Decided with Gaurav (2026-10-16) to fix it in this branch**, before the benchmark, so the baseline passes are not measured against a blind critic. **Limits:** 150 chars per part, 1500 for the log (guesses; check the traces). Hardened after the code review (D31): one call is always one line, and a long log keeps the newest calls. **Risk:** the critic now trusts the log, so a tool that returns a wrong value is believed; the log does not prove the value is right.

### D31. Code-review fixes (session 5)
`/code-review` found 0 Critical and 8 Warnings. All Warnings were fixed, with tests (suite grew from 487 to 510 tests).
- **Tool log hardened.** Each part of a log line is collapsed to single-spaced text, so a tool result with newlines (or U+2028) cannot forge a log line the critic would trust. `ok: None` is now `ok:`. A log over 1500 chars drops its oldest whole lines (`(earlier calls omitted)`), so the newest call stays visible; cutting the end would have hidden the last `write_file`, the exact case D30 was meant to fix. The critic prompt no longer calls the log "evidence": it shows which tools were called, and the text after `->` is data that may be wrong.
- **Trace format.** Lines are written with `ensure_ascii=True`, and `read_trace` splits on `"\n"` only. **Why:** U+2028 in web text made `splitlines()` cut a record in half, and a lone surrogate made the write fail and could lose the `run_end`.
- **Terminal.** `sanitize()` in `src/console.py` replaces control characters (ESC, DEL, C1) with `?` and indents continuation lines; used for every event, the final answer and the config error. `emoji=False` is set too. `print_record` now takes its colours from `EVENT_COLOURS`, and a missing or odd `step` no longer raises.
- **`initial_state` refuses a non-string task** with `ValueError`, so a bad benchmark entry is one failed run and not a crash.
- **New tests:** a replan path end to end, the retry counters by kind, and one regression test per fix above.

**Deferred (not fixed, decide later):** trim nested argument values in `_loggable_args`; log an allowlist of settings instead of `model_dump()` in `run_end.config`; redact local paths and `input_value=` from exception text; check `run_id` against a pattern in `TraceLogger`; a wall-clock cap on a whole run (already the case before this branch); `read_trace` skipping a half-written last line; reporting `write_errors` in `run_end` or the CLI; event-name constants so a typo is not swallowed by `emit_safely`; `ensure_ascii` and shared constants in the executor log; tidy the failed-state code and the `on_record` try/except in `run.py`; share the test helpers `_collector` and `_broken_callback` in `tests/fakes.py` and merge the overlapping executor AC20/AC43 tests; Ctrl-C leaves a trace with no `run_end` (by design, documented). **Not changed:** `data_block` still neutralises only its own closing tag, not the other block tags.


## Session 6: benchmark (2026-10-17)

### D32. Each task has a list of `checks`, not one `checker` + `expected`
A task holds 1 to 3 checks (`number`, `contains`, `regex`, `exact`) and passes only if all pass. Every check can read the final answer or a file in `workspace/` through its `file` field, so there is no separate `file_content` checker. **Why:** a multi-tool task often needs two facts checked (for example "the file exists and holds the right number"); a list covers that without a special checker per task. This changes architecture section 2.7, which is updated.

### D33. Success = the run is `done` AND every check passes
`checks_passed` is stored next to `success`. **Why:** a `failed` run carries "Task failed: ..." plus a partial result; the agent gave up, and counting it would make the agent look better than it is. Storing both keeps the other reading possible. Decided before any measurement so the goalposts cannot move.

### D34. The code fingerprint, not the git commit, blocks a resume
The manifest holds a SHA-256 over `src/**/*.py`, `benchmark/*.py` and `benchmark/tasks.json`; resume refuses if it differs. The git commit and a dirty flag are information only. **Why:** session 7 fixes code between passes, and results from before and after a fix must never be mixed. The commit alone would block a docs-only commit and miss uncommitted code changes. **Cost:** any edit in these files, even a comment, means a new pass id.

### D35. Connection errors, timeouts and every Ollama `ResponseError` pause the pass
`INFRA_ERROR_TYPES` in `benchmark/runner.py`. The run goes to `paused.jsonl`, no result is written, and resume runs the task again. **Why:** the agents cannot cause these errors, so they must not count as agent failures (D12, D24). The Ollama client turns `httpx.ConnectError` into `ConnectionError`, raises `ResponseError` for server errors, and lets httpx timeouts pass. **Risk:** a guess from reading the client code, like D6. A non-quota server error would also pause. Check the real text of the first usage cap in `paused.jsonl` and adjust the list.

### D36. `MAX_REPLANS=1` in every pass (settles first-run problem P3)
Gaurav's `.env` had 2; the code default and D4 say 1. The manifest records the value and resume refuses a change, so it is fixed before the first real pass. **Why:** fewer LLM calls per task under the Ollama usage caps, and one value for all passes.

### D37. `benchmark/results/` is committed to git
Manifests and results are small and are the evidence behind the CV numbers. Traces in `logs/` stay ignored. **Risk:** final answers can hold public web text; no secrets are written (the manifest config has the same keys as `run_end.config`, which a test fixes).

### D38. `benchmark/text.py` holds the number and text helpers
`extract_numbers`, `normalize_words`, `contains_phrase` and `normalize_lines` live there, and `checkers.py` re-exports `extract_numbers`. **Why:** `tasks.py` needs `extract_numbers` (D39) and `checkers.py` needs the check models from `tasks.py`; with both helpers in `checkers.py` the two modules would import each other.

### D39. A `number` check's value may not appear in the task text
`load_tasks` refuses such a task. **Why:** the number check passes when ANY number in the answer matches; if the expected number were in the task, an answer that only repeats the task would pass without doing any work. **Known weak spots (accepted, listed for session 7):** a long answer listing many numbers could match by chance; the model knows most search facts without searching (`tools_used` is stored, success does not require a tool); the final answer is the last approved step's result, so a plan whose last step only says "file written" fails an answer check.

### D40. Code-review fixes (session 6)
`/code-review` found 0 Critical and 6 Warnings. All Warnings were fixed, with 22 new tests (benchmark tests 305 to 327).
- **Symlinked workspace.** `reset_workspace` raises and `run_pass` refuses (`PassRefused`) when `workspace/` itself is a symlink. **Why:** entries inside were already handled as links, but `iterdir()` follows a linked folder and would empty the folder it points to (verified).
- **Manifest is untrusted data.** `_read_manifest` checks the types of `model`, `code_fingerprint`, `config` and `task_ids` (a list of strings) and catches `RecursionError` from deeply nested JSON; `_read_results` also catches it. **Why:** a manifest can come from git, and a wrong type gave a `TypeError` traceback instead of a refusal.
- **Redaction.** Saved `error` and `final_answer` in result lines, and `error` in `paused.jsonl`, have the project folder replaced by `<project>`, the home folder by `~`, and URL query strings by `?<redacted>`. **Why:** results are committed (D37) and exception text is not under our control.
- **Pause bound.** After 3 pauses for the same task and error type (`MAX_TASK_PAUSES`), the run is recorded as a failed result with its error kept. **Why:** a deterministic server error (D35 risk) would otherwise pause every resume and spend quota forever. The pause message shows the count.
- **Comment tag** `# RISK:` changed to `# WARNING:` (colour-coding rule); a test now checks all `benchmark/*.py` for tags outside the allowed five.
- **Docs.** The spec, plan and progress now name `smoke-2` (the committed smoke pass); the untracked `smoke-1` (recorded with `MAX_REPLANS=2`) was deleted.

**Deferred (not fixed, decide later):** `extract_numbers` reads `.5` as 5, `-.5` and the Unicode minus `−5` as +5 (no current task is affected); setup paths `.`, `dir/`, `.gitkeep` and file/folder clashes pass validation; `OSError` in `main` gives a traceback (exit 1) and not exit 2; invisible characters in `text.py` and one test string; one shared `number_matches` helper (the tolerance formula is written twice); build `config` once and split `run_pass` (134 lines); `code_fingerprint` ignores a custom `tasks_path`; missing `# WHY:` comments and one docstring in `runner.py`, `CATEGORY_ORDER` placement; spec wording for the pause and Ctrl-C messages and the `summarize` keys; a pass lock against two runners; `-c core.fsmonitor=false` for `git status`; an allowlist for `config` in the manifest; tests for `KeyboardInterrupt` or a plain exception raised by `run_fn`, a manifest without `results.jsonl`, and a weak "model" refusal assertion.


## Session 7: evaluation (2026-10-17)

### D41. The evaluation tools live in `evaluation/`, not in `benchmark/`
`evaluation/passes.py`, `report.py` and `analyze.py` are read-only tools over `benchmark/results/` and `logs/`. **Why:** the code fingerprint (D34) covers `benchmark/*.py`, so a tool in `benchmark/` would change it on every edit. The baseline passes could then not be resumed after a pause, and `main-off-1` and `main-on-1` could end with different fingerprints although the agent code is the same. In `evaluation/` the tools can be built and fixed while the baseline passes run. Architecture gets a layer 8 row.

### D42. `evaluation/` imports three private names from `benchmark/runner.py`
`_read_manifest`, `_read_results` and `_redact`. **Why:** results must be read and redacted exactly as the runner wrote them, and a copy could drift. Making them public would edit `runner.py` and change the fingerprint before the baseline passes. **Deferred:** make them public in a later session. A test imports them, so a rename fails loudly.

### D43. Comparison rules: pooled sums, one difference, Wilson intervals
Complete passes with the same model, config, fingerprint and task ids are pooled by summing counts and recomputing rates (never averaging). Two groups are compared only when they differ in exactly one thing: the two gate flags (ablation), the model, or the code fingerprint (before/after a fix). Every other pair with the same tasks and model is listed under "not compared". Success rates always carry a 95% Wilson interval. **Why:** with 30 tasks one task is 3.3 points, so a difference between two passes is meaningless without an interval, and a pair that differs in two things measures neither. A negative reduction (the gate added failed calls) is shown, not hidden, and a reduction is `n/a` when the off pass has no failed calls.

### D44. The critic sees the approved earlier results (evaluation fix 1)
`run_critic` adds the earlier step results as a `<results>` data block (cut to 2000 chars, only from the second step on), and its prompt says a step may use earlier results without a tool and must not be retried only because no tool was called. **Cause and evidence:** first-run problem P2. The planner splits "read the file" and "pick a line" into two steps; the second needs no tool, and the critic, seeing neither the earlier result nor a tool call, rejected it. In the baseline passes there were 6, 8 and 13 such rejections, `file-02` and `file-03` cost 26 of the 37 step retries, and 3 of the 4 non-successes ended as "gave up" holding the right answer (runs `20261016-200108-4f4438`, `20261016-200729-0942a4`, `20261016-201351-47d5bf`). **Expected effect:** fewer step retries and fewer "gave up with the right answer" outcomes. **Risk:** a looser critic may approve a wrong output; the `done_but_wrong` count in the after-fix passes shows it. **Alternative not taken:** change the planner to avoid trivial splits. It changes every plan, and the reject point is the critic.

### D45. The tool log keeps line breaks as backslash-n (evaluation fix 2)
`_log_line` replaces `\r\n`, `\r` and `\n` in a tool result with the two characters backslash-n before the collapse of other whitespace; the critic prompt explains the mark. **Cause and evidence:** D31 collapsed all whitespace so a result cannot forge a log line, but it also hid the line structure. The critic read `pen book lamp cup key map box` and said "only 1 non-empty line, your answer of 7 is incorrect" (2 of 3 `file-03` runs: `20261016-200131-226caa`, `20261016-200729-0942a4`). **Expected effect:** the critic can check line counts and blank lines. **Safety kept:** one call is still exactly one line; tab, U+2028 and the like still collapse to a space. **Limit unchanged:** each part is still cut to 150 chars, so the end of a long multi-line result stays hidden.

### Not fixed: the planner adds an unrequested step (`search-03`)
One occurrence in 90 runs (`20261016-195950-972ae4`, `main-off-1`); the same task passed in the other two passes. Too little evidence to change the planner. Check again in the post-fix passes.

### D46. Code-review fixes (session 7)
`/code-review` found 0 Critical, 6 Warnings and 10 Suggestions (plus test gaps). Gaurav chose to fix Warnings 1 to 5, the test gaps, and Suggestions S1, S4 and S5. Suite 844 → 870 tests (33 of the 100-test budget used in this session).
- **Untrusted text in committed Markdown (Warning 1).** One `md_safe(value, limit)` in `evaluation/passes.py` is used for every task id, run id, tool name, error kind, category, model name, config key and digest field. It removes control characters, replaces backticks, escapes `|`, `<` and `[`, redacts, and cuts. **Why:** a hostile `\n# heading`, `<!-- -->` or forged end marker must not break a table, hide text, or make `write_block` refuse forever.
- **`trace_coverage` run ids (Warning 2).** `load_run_trace` and the run-id pattern moved to `passes.py` and both tools use them. A direct test was added.
- **Output paths (Warning 3).** `--out` and `--write` only write `.md` files inside `docs/` (`OUTPUT_ROOT`, symlinks resolved), the file is read and written byte-exact and replaced atomically, and an unreadable file is a refusal (exit 2), not a traceback.
- **Redaction (Warning 4).** Extra patterns for `sk-` / `ghp_` keys, `Bearer` tokens, long hex strings, `user:password@` URLs and other users' `/Users/<name>` and `/home/<name>` folders. `benchmark/runner.py` is not edited (fingerprint). A side effect: any run of 32+ hex digits in a digest is shown as `<secret>`.
- **Critic `<results>` cut (Warning 5).** Each earlier result gets its own share of the 2000-character budget, so the newest result is no longer pushed out by a long first one. This is part of Fix 1 (D44) and is in the measured code.
- **S1** `fullmatch` for pass and run ids. **S4** the critic's "no tool needed" rule now covers only steps that reuse earlier results; a step that asks for new data still needs a successful call in the log. **S5** a real backslash in a tool result is doubled in the log, so it cannot be read as a line break (D45).

**Deferred (not fixed, decide later):** Warning 6 (`analyze_pass` is about 90 lines: split it); S2 (size and depth limits on result and trace files, caps on digest lists); S3 (`data_block` neutralises only its own tag; wrap each earlier result separately); S6 (import `CATEGORY_ORDER` from the runner, move the markers to the top of `report.py`, merge the repeated category helper); S7 (rename the "passes" and "count" columns); S8 (a "too few failed calls" note when counts are below about 5); S9 (the digest prints a 0-based step); S10 (guard `wilson_interval` against `successes > total` and a non-finite duration).


## Session 8: streamlit-ui (2026-10-17)

### D47. Live runs happen in a background thread
`ui/live.py` `RunManager` runs `run_task` in a daemon thread, one run at a time per server process (`st.cache_resource`); the page polls `snapshot()` once a second with `st.fragment(run_every=1)`. **Why:** Streamlit runs the page script again on every click and stops the old run with an exception that `run_task` does not catch, so a run inside the page script would end halfway with no `run_end`. In its own thread the run goes on. The thread only appends records under a lock and never calls Streamlit. **Cost:** a run cannot be stopped from the UI; it ends through the hard limits (Hard rule 5). A hot reload during development can create a second manager, so restart the app before a real run.

### D48. UI settings stay out of `Settings`
`UiSettings` (`allow_live_runs`, `model_choices`) is a separate model in `src/config.py`. **Why:** `Settings.model_dump()` is `run_end.config` and the pass manifest's `config`. A new field there would make new passes look different from the baseline passes, and the report would refuse to compare them (D43). `Settings` still has exactly 9 fields.

### D49. Replay-only is the default
`UI_ALLOW_LIVE_RUNS` defaults to `false`; in that mode the Run page is not in the page list and its module is not imported. **Why:** a copy of the app hosted somewhere can then never spend Ollama quota or run tools. The Run page checks the mode again itself (defence in depth). **Open for session 9:** `logs/` is gitignored, so a hosted copy would show the dashboard but no traces; decide which sample traces to ship.

### D50. The dashboard hides small passes by default
The pass selector selects only the passes with the largest task list, so a 3-task smoke pass (`smoke-2`, 100%) does not sit beside the 30-task passes. It is one click away. `success_chart` still draws every complete pass it is given (AC14).

### Notes from the build
- `pages_for(live)` returns plain `PageSpec` records, not `st.Page`: `st.Page.title` fails outside a running Streamlit app, and a test needs to read the page list.
- The dashboard imports the private `_reduction_text` from `evaluation/report.py` so the page and `docs/results.md` use the same wording (as in D42). A rename fails the dashboard loudly.
- UI runs write normal traces to `logs/`, so the traced-run count includes them (107 after the first two UI runs).
