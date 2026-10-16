# Multi-Agent Task Orchestrator

## What this project is
A LangGraph system where three LLM agents (Planner, Executor, Critic) complete multi-step tasks on their own using tools: web search, calculator, file read/write. Built from scratch by Gaurav, as a CV project and a learning project.

The CV numbers are targets to measure honestly, not facts:
- task success rate on a self-made 30-task benchmark (target ~65%)
- reduction in failed tool calls from retry + validation (target ~20%), measured by running the benchmark with these features off and on
- reasoning traces logged across 100+ runs
Report whatever numbers we actually get.

## Two kinds of agents: never mix them up
- **Product agents:** Planner, Executor, Critic in `src/agents/`. Python code, powered by Ollama.
- **Claude Code subagents:** in `.claude/agents/`. Helpers that build the project. Never part of the product.

## Stack
- Python 3.13, virtual environment `.venv`
- LangGraph (orchestration), langchain-core, langchain-ollama (`ChatOllama`)
- Ollama cloud models (free plan, has 5-hour and weekly usage caps): main `gpt-oss:120b-cloud`, second `gemma4:31b-cloud` (both confirmed to do tool calling). The model name is always read from `.env`. Never mix models within one benchmark pass.
- Pydantic (validation), ddgs (web search), rich (terminal colours), pytest, ruff
- Editor: VS Code with Pylance, Ruff, Error Lens, Better Comments (settings in `.vscode/`)

## Commands
- Activate environment: `source .venv/bin/activate`
- Run tests: `pytest -q`
- Lint and format: `ruff check . --fix && ruff format .`
- List Ollama models: `ollama list`
- Run one task: `python -m src.run "your task here"` (prints the events live; the trace goes to `logs/<run_id>.jsonl`)
- Run or resume a benchmark pass: `python -m benchmark.runner --pass <pass_id>` (results go to `benchmark/results/<pass_id>/`; add `--tasks calc-01,file-02` for a small pass, `--verbose` for live events)
- Show a pass summary without running anything: `python -m benchmark.runner --pass <pass_id> --summary`

## Architecture in brief
Graph: START → planner → executor → critic → advance → router. If the planner fails, planner → finalize.
Router: next step or retry → executor | replan → planner | done or limits used up → finalize → END.
- Shared state: one TypedDict, `AgentState` (task, run_id, plan, current_step, step_results, last_output, last_tools, verdict, feedback, retries, replans, final_answer, status, route, error).
- The router is plain Python with hard limits (MAX_RETRIES, MAX_REPLANS). The critic gives a verdict; code decides where to go.
- Every tool call passes one validation gate: tool exists → Pydantic arguments valid → run inside try/except → return `{ok, output, error}`.
Full detail: `docs/specs/architecture.md`. Read it before changing the structure.

## Project layout
- `src/`: config.py, state.py, tools/, validation.py, agents/, graph.py, run.py, logger.py, console.py
- `benchmark/`: tasks.json, tasks.py, text.py, checkers.py, runner.py, results/
- `logs/` (run traces), `workspace/` (sandbox for file tools), `tests/` (mirrors `src/`)
- `docs/specs/` (what we build), `docs/plans/` (plans and progress), `docs/project-memory.md`
- `.claude/`: rules/, skills/, agents/, hooks/

## Hard rules
1. Tools never raise exceptions. They always return `{ok, output, error}`.
2. Never hard-code the model name. Read it from `.env` through `src/config.py`.
3. Calculator: parse with `ast`. Never use `eval` or `exec`.
4. File tools work only inside `workspace/` and reject any path that escapes it.
5. Every loop has a hard limit (retries, replans, tool iterations, LangGraph `recursion_limit`). No run may hang.
6. Benchmark checkers are code, never an LLM judge. Never edit expected answers to make a task pass.
7. Never read, print, or commit `.env` or any secret.
8. Unit tests never call an LLM or the internet. Mock them.
9. All feature work happens on a `feature/<slug>` branch. Never commit feature code directly to `main`; it reaches main only through `/git-feature`.
10. Ask before any `git push`. Never force-push. Never use `git branch -D`.

## Working with Gaurav
- Gaurav is learning. Before writing code, explain in simple steps what you will build and why, then wait for his go-ahead.
- Build one layer at a time. Do not jump ahead in the roadmap.
- If something is unclear, ask instead of guessing.
- Be honest about weak results, failed tests, and uncertainty.
- `.claude/rules/` covers VS Code review, code explanations, colour coding, feature summaries, project memory, model selection, and session start.

## Workflow for every feature
Each step ends with a stop where Gaurav decides.
1. `/create-spec <feature> <description>`: feature branch + spec. Gaurav reviews.
2. `/create-plan <feature>`: plan, then "Shall we implement it like this?"
3. Switch to sonnet, medium. `/implement <feature>`: one step at a time, approval before each.
4. `/test <feature>`: test-writer, then test-runner.
5. `/code-review`: security-reviewer and quality-reviewer in parallel. Fix Critical findings.
6. Update `docs/plans/decisions.md` if a design choice was made, and `docs/project-memory.md`.
7. `/git-feature`: push, merge into main, delete the branch.

## Claude Code setup
| Skill | Does | Who runs it |
|---|---|---|
| `/create-spec` | branch + spec | Gaurav only |
| `/create-plan` | implementation plan + approval question | Gaurav only |
| `/implement` | builds the plan step by step | Gaurav only |
| `/test` | test-writer → test-runner | Gaurav or Claude |
| `/code-review` | security + quality reviewers in parallel | Gaurav or Claude |
| `/git-feature` | push, merge into main, delete branch | Gaurav only |

| Subagent | Model, effort | Job |
|---|---|---|
| test-writer | sonnet, medium | writes pytest tests from acceptance criteria |
| test-runner | haiku, low | runs pytest, read-only |
| security-reviewer | sonnet, high | security review, read-only |
| quality-reviewer | sonnet, high | quality review, read-only |

## Docs
- Specs: `docs/specs/`
- Plans: `docs/plans/<feature>-plan.md`, `docs/plans/roadmap.md`, `docs/plans/decisions.md`
- Commands and prompts: `docs/project-memory.md`
- Current progress (loaded automatically):
@docs/plans/progress.md