# First real runs (session 5)

Model: `gpt-oss:120b-cloud` for every run. Date: 2026-10-16. Settings in the traces: `MAX_RETRIES=2`, `MAX_REPLANS=2` (see problem P5), `MAX_TOOL_ITERATIONS=5`, validation and retry on.

"Answer right?" was judged by eye by Gaurav and Claude. It is **not** the benchmark.

## The five official runs

These were run **after** the fix D30 (the critic now sees the tool calls). R1 never needed the fix and is the first run.

| # | Task | Run id | Status | Answer right? | Time | Tool calls (failed) | Step retries | Replans |
|---|---|---|---|---|---|---|---|---|
| R1 | calculator | `20261016-161445-7039a8` | done | yes, 1415 | 8.6 s | 1 (0) | 0 | 0 |
| R2 | web_search | `20261016-162635-243393` | done | yes, 1889 | 9.0 s | 1 (0) | 0 | 0 |
| R3 | write_file | `20261016-162644-b5eabd` | done | yes, `workspace/hello.txt` holds exactly `hello from the agent` | 3.3 s | 1 (0) | 0 | 0 |
| R4 | read_file | `20261016-162620-d35420` | done | yes, `beta` | 13.8 s | 3 (0) | 2 | 0 |
| R5 | multi-tool | `20261016-162648-024c0e` | done | yes, 29,031.6938424 ft in `workspace/everest.txt` (8848.86 m x 3.28084) | 16.2 s | 3 (0) | 0 | 0 |

Checked by code over the five traces: `model == "gpt-oss:120b-cloud"` on every line, exactly one `run_end` and it is the last line, every run under 15 minutes, no `GraphRecursionError` (AC38, AC39). No run hung.

## Before and after the critic fix

The same tasks, first with a critic that saw only the executor's text, then after D30.

| Task | Before: tool calls / step retries / replans / time | After: tool calls / step retries / replans / time |
|---|---|---|
| R2 web_search | 2 / 1 / 0 / 19.9 s (`20261016-161509-efd3b2`) | 1 / 0 / 0 / 9.0 s |
| R3 write_file | 4 / 2 / **1** / 26.1 s (`20261016-161545-7b6d52`) | 1 / 0 / 0 / 3.3 s |
| R5 multi-tool | 6 / 2 / 0 / 21.2 s (`20261016-161837-6eefa3`) | 3 / 0 / 0 / 16.2 s |

This is one run per cell, with temperature 0 but a cloud model, so it is a sign and **not a measured rate**. It matches the cause found in the "before" traces: the critic said "does not show the tool being invoked" about correct work.

## Run that did not count: R4 without the file

`20261016-161632-ceae7b`: `workspace/notes.txt` did not exist (setup mistake). Status failed after 93.9 s, with **45 tool calls (42 failed)**, 6 step retries, 2 replans, 9 verdicts. This was not a valid `read_file` test, but it shows the hard limits work: the run ended, nothing hung.

## Problems found

| # | Problem | Seen in | Status |
|---|---|---|---|
| P1 | The critic saw only the executor's text, so it rejected correct work ("no tool call shown") and in R5 "corrected" a right calculator result with its own wrong arithmetic. Cost: wasted retries, a replan, and quota. | R2, R3, R4, R5 (before) | **Fixed now** (D30, step 11). Tested with fakes and confirmed in the 3 reruns above. The tests for it come with `/test`. |
| P2 | The critic asks for a tool call on a step that only needs earlier results. R4 step 2 ("extract the second line") got "You did not read the file; no tool was used", costing one retry. The plan also splits "read" and "extract" into two steps, and the executor answered `beta` in step 1 (doing step 2's work), which the critic rejected as "not the full contents" (one more retry). | R4 (after) | **Session 7.** Candidate fixes: tell the planner to avoid splitting trivial steps; tell the critic that a step may use earlier results without a tool. |
| P3 | `MAX_REPLANS` is 2 in Gaurav's `.env`, but the code default and `.env.example` are 1 (D4). | all traces (`run_end.config`) | **Decision needed before the benchmark** (session 6). It must be the same in every pass; the manifest records it. |
| P4 | A missing file produced 45 tool calls, 42 failed: the executor guessed paths and the same failing call repeated. One such task can swamp the failed-tool-call rate of a pass. | invalid R4 | **Session 7.** Benchmark file tasks must create their input file in `workspace/` before the task (the runner resets `workspace/`). Also consider stopping repeated identical failing calls. |
| P5 | The model writes numbers like `29,031.6938424` (with a thousands separator) in files. | R5 | **Session 6.** The `number` and `file_content` checkers must accept commas, or the task text must say "no thousands separators". |
| P6 | The model used its own citation marker `【1†L1-L3】` in a final answer. | R2 (before) | **Session 7**, only if it breaks a checker. Not seen after the fix. |

No problem was left unmarked. Nothing else needed a code change in this session.

## Did the earlier guesses hold?

- **D6 (rate-limit hints in `web_search`):** **not tested.** The 9 traces hold no transient error and no tool call with more than 1 attempt. `ddgs` did not rate limit in about 6 searches. The hints stay a guess.
- **D18 (executor limits: 5 calls per reply, 8000-char tool results, 4000-char outputs):** **looked fine for these simple tasks**, but they were not stressed. Only the iteration limit (5 LLM calls per step) was hit, and only in the missing-file run. Re-check on the benchmark traces.
- **D30 limits (150 chars per log part, 1500 for the log):** enough for these runs; the critic approved with them.

## Readability of the traces

The traces are readable: each run is 4 to 15 lines (64 for the failed one), with the plan, the tool calls with arguments and results, the critic's verdict with the executor output, and the `run_end` with counters and config. The live terminal output shows the same events.
