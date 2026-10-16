# Project memory: Multi-Agent Orchestrator

My commands and prompts for this project. Claude Code appends to this file; I can edit it anytime.

## Terminal commands

### Setup
```bash
source .venv/bin/activate
```

### Run a task
```bash
python -m src.run "your task here"
```
Runs one task with the Planner, Executor and Critic. Shows the events live in colour and writes the trace to `logs/<run_id>.jsonl`. Needs `OLLAMA_MODEL` in `.env`. Exit code 0 done, 1 failed, 2 configuration error.

### Run a benchmark pass
```bash
python -m benchmark.runner --pass <pass_id>
python -m benchmark.runner --pass smoke-1 --tasks calc-01,search-01,file-02
```
Runs the 30 tasks (or the listed ones) as one pass and saves `manifest.json`, `results.jsonl` and `paused.jsonl` in `benchmark/results/<pass_id>/`. The same command resumes a pass; it refuses if the model, settings, tasks or code changed (start a new pass id then). Exit code 0 complete, 2 refused, 3 paused (usage cap or connection error; resume later), 130 Ctrl-C. Empties `workspace/` before every task.

### Show a pass summary
```bash
python -m benchmark.runner --pass <pass_id> --summary
```
Prints success rate, failed-tool-call rate and counts for an existing pass. Makes no LLM calls.

## Claude Code commands
<!-- slash commands and subagents, with one line each on what they do -->

## Reusable prompts
<!-- prompts worth using again, with when to use them and model + effort -->

## Prompt history
<!-- dated record of prompts that started or finished a feature -->

### 2026-10-16: Spec for session 2, foundation-tools-validation
```
/create-spec foundation-tools-validation
```
Background description given just before it: "Build the foundation layer from docs/specs/architecture.md: (1) src/config.py loading .env (model names, MAX_RETRIES, MAX_REPLANS, ENABLE_VALIDATION, ENABLE_RETRY) with safe defaults; (2) src/console.py with the rich colour constants from the colour-coding rule; (3) four tools — calculator (ast only), read_file and write_file (workspace/ only), web_search (ddgs, top 3 trimmed results) — each with a Pydantic input schema and returning {ok, output, error}; (4) a tool registry mapping name to (schema, function); (5) a validation gate that checks the tool exists, validates arguments, runs the tool in try/except, and retries transient tool failures such as a search timeout up to MAX_RETRIES when ENABLE_RETRY is true. The retry where the LLM fixes its own bad tool call belongs to the executor in session 4, not here."
Use it to start a roadmap session's spec. Type the slash command directly; don't paste it, or the skill won't run. Model: opus, effort high (set by the skill).

### 2026-10-16: Spec for session 3, graph-planner-critic
```
/create-spec graph-planner-critic
```
No extra description: the spec was built from the roadmap entry for session 3 and `docs/specs/architecture.md`. Use this short form when the roadmap already describes the session well. Model: opus, effort high.
### 2026-10-16: Spec for session 4, executor
```
/create-spec executor
```
No extra description: built from the roadmap entry for session 4, `docs/specs/architecture.md`, and the session 3 code-review findings. Use this short form when the roadmap already describes the session well. Model: opus, effort high.

### 2026-10-16: Spec for session 5, tracing-and-first-runs
```
/create-spec tracing-and-first-runs
```
No extra description: built from the roadmap entry for session 5, `docs/specs/architecture.md`, and decisions D5, D6, D12, D15 and D18. Use this short form when the roadmap already describes the session well. Model: opus, effort high.

### 2026-10-16: Implement session 5 in one go
```
/implement
do all the step at once don't ask again and again also remember this that while implementing just ask once at the starting saying it will include these many steps and if user says go then implement all at once without asking again and again
```
Use it to build a whole plan after one go-ahead. Claude stops only on a failed check or at a step that needs you (real LLM runs, `.env`). Model: sonnet, effort medium.

### 2026-10-16: Fix review findings, then merge
```
fix it and then merge
```
Said after `/code-review` listed findings and asked which to fix. Claude fixed all Warnings and deferred the Suggestions (D31); the merge itself is done by Gaurav with `/git-feature`. Model: sonnet, effort medium.

### 2026-10-16: Spec for session 6, benchmark
```
/create-spec benchmark
```
No extra description: built from the roadmap entry for session 6, `docs/specs/architecture.md` (sections 2.7 and 5), decisions D12 and D24, and first-run problems P3 to P5. Use this short form when the roadmap already describes the session well. Model: opus, effort high.

### 2026-10-17: Plan and implement session 6, benchmark
```
/create-plan benchmark
/implement
go
```
`/implement` was run without a feature name and Claude asked once, listing the 9 steps it would build in one go; "go" started them. The real smoke pass (step 10) needs you: set `.env`, then run `python -m benchmark.runner --pass <pass_id> --tasks calc-01,search-01,file-02` twice. Model: sonnet, effort medium.
