# Implementation plan: Graph, planner, and critic

**Spec:** docs/specs/graph-planner-critic.md
**Branch:** feature/graph-planner-critic
**Status:** Approved

## Progress
- [x] Step 1: Scaffolding and `MAX_PLAN_STEPS`
- [x] Step 2: State (`src/state.py`)
- [ ] Step 3: JSON parsing helper (`src/agents/parsing.py`)
- [ ] Step 4: Planner (`src/agents/planner.py`)
- [ ] Step 5: Critic (`src/agents/critic.py`)
- [ ] Step 6: Stub executor (`src/agents/executor.py`)
- [ ] Step 7: Router logic (`decide`, `advance`, edges, `finalize`, `recursion_limit`)
- [ ] Step 8: Graph wiring (`build_graph`) and end-to-end check
- [ ] Step 9: Docs sync and final checks

## Overview
Build bottom-up, in the order the pieces depend on each other: the state first, then the shared JSON helper, then the two agents, then the stub executor, then the router's pure functions, and last the graph that wires them together. Every step before step 8 is a plain function that can be checked with a one-line `python -c` and a fake LLM, so no step needs a real model. The full test suite is written afterwards with `/test`, from the spec's acceptance criteria.

The spec's open questions are settled with the spec's own choices (approving the spec approved them): no `"replan"` verdict; a new `route` field in `AgentState`; no `on_event` parameter yet (session 5 adds event emission); `run_task` location decided in session 5. Step 9 records these in `docs/plans/decisions.md`.

## Steps

### Step 1: Scaffolding and `MAX_PLAN_STEPS`
- **Files:** `src/agents/__init__.py` (new, docstring only), `tests/agents/__init__.py` (new, empty), `src/config.py`
- **What:** create the `agents` package and its test folder. Add `MAX_PLAN_STEPS = 6` to `src/config.py` next to the path constants, with a `# WHY:` comment (fixed design limit used by `Plan` and `recursion_limit`, same in every pass, so not an `.env` setting).
- **Why:** later steps import `src.agents.*` and `MAX_PLAN_STEPS`; tests need the folder to mirror `src/`.
- **Check:** `python -c "import src.agents; from src.config import MAX_PLAN_STEPS; print(MAX_PLAN_STEPS)"` prints `6`; `pytest -q` still passes (227 tests); diagnostics 0 errors.
- **Done when:** the checks pass.
- **Covers:** groundwork for AC6, AC33

### Step 2: State (`src/state.py`)
- **Files:** `src/state.py` (new)
- **What:** `Status`, `VerdictValue`, `Route` literal types; `AgentState` TypedDict with the 13 fields from spec section 4; `MAX_TASK_CHARS = 2000`; `initial_state(task, run_id)` that strips the task, raises `ValueError` on empty or too long, and returns all starting values from spec 5.1.
- **Why:** every node reads and writes this one dict; a single function for the starting values means no node has to guess a missing field.
- **Check:** `python -c "from src.state import initial_state; print(initial_state('  t  ', 'r1'))"` shows 13 fields and `task='t'`; `initial_state('', 'r1')` raises `ValueError`. Diagnostics 0 errors.
- **Done when:** the checks behave as described.
- **Covers:** AC1, AC2, AC3

### Step 3: JSON parsing helper (`src/agents/parsing.py`)
- **Files:** `src/agents/parsing.py` (new)
- **What:** `parse_json_model(text, schema) -> tuple[model | None, error | None]`: strip a Markdown code fence, take first `{` to last `}`, `schema.model_validate_json`, turn a `ValidationError` into `field: problem; ...` (max 300 chars). Never raises. Also a small `reply_text(message) -> str` helper that returns `str(message.text)`, so both agents read the reply the same way.
- **Why:** planner and critic both need "parse, and if it fails, give the LLM a readable error"; one helper keeps that behaviour the same in both.
- **Check:** `python -c` with a tiny test model: plain JSON, fenced JSON, chatter around JSON → model; `"no json here"` and `{"steps": [}` → `(None, error)`. Diagnostics 0 errors.
- **Done when:** all five inputs give the expected result.
- **Covers:** AC4, AC5

### Step 4: Planner (`src/agents/planner.py`)
- **Files:** `src/agents/planner.py` (new)
- **What:**
  - `Plan` schema: 1 to `MAX_PLAN_STEPS` steps, each stripped, non-empty, max 500 chars; extra keys ignored.
  - `PLANNER_SYSTEM_PROMPT` listing the tool names from `src.tools.TOOLS` and the exact JSON shape.
  - Prompt builder: first plan (task only) or replan (`replans > 0`: old plan, step results cut to 1000 chars each, feedback).
  - `run_planner(state, *, llm)`: invoke → parse → re-ask once with the error → return the fields from spec 5.3 step 3, or `plan=[]`, `status="failed"`. LLM exceptions are not caught.
- **Why:** turns the task into steps the executor can follow; the re-ask gives the model one chance to fix its own format mistake.
- **Check:** `python -c` with `FakeListChatModel(responses=['{"steps": ["a", "b"]}'])` returns a 2-step plan; with `responses=['oops', 'oops']` returns `status="failed"`. Diagnostics 0 errors.
- **Done when:** both checks behave as described.
- **Covers:** AC6, AC8, AC9, AC10, AC11, AC12, AC13

### Step 5: Critic (`src/agents/critic.py`)
- **Files:** `src/agents/critic.py` (new)
- **What:**
  - `Verdict` schema: `verdict` stripped and lower-cased, then checked against `Literal["approve", "retry"]`; `feedback` default `""`, cut to 1000 chars; extra keys ignored.
  - `CRITIC_SYSTEM_PROMPT` (output is data, not instructions; exact JSON shape).
  - User message: task, "step N of M: <step>", `last_output` cut to `MAX_OUTPUT_CHARS = 4000` between `<output>` markers.
  - `run_critic(state, *, llm)`: invoke → parse → re-ask once → return `verdict`, `feedback`, or `retry` + `"critic output invalid"`. LLM exceptions are not caught.
- **Why:** the critic gives the verdict; code (step 7) decides what happens with it.
- **Check:** `python -c` with a fake reply `{"verdict": "Approve", "feedback": "ok"}` returns `approve`; with two invalid replies returns `retry` and `critic output invalid`. Diagnostics 0 errors.
- **Done when:** both checks behave as described.
- **Covers:** AC7, AC14, AC15, AC16, AC17, AC18

### Step 6: Stub executor (`src/agents/executor.py`)
- **Files:** `src/agents/executor.py` (new)
- **What:** `stub_executor(state)` returns only `{"last_output": f"stub output for step {n}: {step}"}`. Module docstring and a `# TODO:` saying session 4 replaces it with the real tool loop.
- **Why:** lets the graph run end to end now, with no LLM and no tools in the executor.
- **Check:** `python -c` on a state with `plan=["a", "b"]`, `current_step=1` prints `{'last_output': 'stub output for step 2: b'}`.
- **Done when:** the output matches exactly.
- **Covers:** AC34

### Step 7: Router logic (`src/graph.py`, part 1)
- **Files:** `src/graph.py` (new)
- **What:** the pure functions only, no LangGraph yet:
  - `decide(state, settings) -> Route` with the 6 rules from spec 5.5.
  - `advance(state, *, settings)` returning the changes from the table in spec 5.5 plus `route`; never changes the input.
  - `route_after_planner(state)` and `route_after_advance(state)` (unknown route → `"finalize"`).
  - `finalize(state)` with the done message, the two failure messages, and the best partial result.
  - `recursion_limit(settings)`.
- **Why:** this is where Hard rule 5 lives. Keeping the rules in plain functions means each one can be tested without running a graph.
- **Check:** `python -c` loops over hand-made states for each of the 6 rules and prints the route; `recursion_limit` with default settings prints `154`. Diagnostics 0 errors.
- **Done when:** every rule gives the route in the spec table.
- **Covers:** AC19, AC20, AC21, AC22, AC23, AC24, AC25, AC26, AC27, AC28, AC29, AC30, AC31, AC32, AC33

### Step 8: Graph wiring (`src/graph.py`, part 2) and end-to-end check
- **Files:** `src/graph.py`
- **What:** `build_graph(*, llm=None, settings=None, executor=None)`: defaults from `get_settings()`, `get_llm(settings)`, `stub_executor`; nodes `planner`, `executor`, `critic`, `advance`, `finalize` bound with `functools.partial`; edges `START → planner`, conditional after `planner` and after `advance` (with explicit path maps), `executor → critic → advance`, `finalize → END`; returns the compiled graph.
- **Why:** connects the tested pieces into the loop from the spec diagram.
- **Check:** a short script in the scratchpad (not in the repo) runs the graph with a fake LLM that answers by role: happy path ends `done`; an always-retry critic ends `failed` with `replans=1` and 6 executor runs, invoked with `recursion_limit(settings)` and no `GraphRecursionError`. Diagnostics 0 errors.
- **Done when:** both runs end as described.
- **Covers:** AC35, AC36, AC37, AC38, AC39, AC40

### Step 9: Docs sync and final checks
- **Files:** `docs/specs/architecture.md`, `CLAUDE.md`, `docs/plans/decisions.md`
- **What:**
  - Architecture 2.5: add `route` to the `AgentState` table, redraw the graph with the `advance` node and the edge after the planner, change "step_results is cleared" on replan to "the planner clears it after reading it", note `MAX_PLAN_STEPS` lives in `src/config.py`; mark open question 1 as decided (no `replan` verdict for now).
  - `CLAUDE.md`: update the `AgentState` field list.
  - `decisions.md`: session 3 decisions (route field, planner edge, who clears `step_results`, LLM errors not caught by agents, no `on_event` yet, `MAX_PLAN_STEPS` as a constant).
  - Run `ruff check . --fix && ruff format .` and `pytest -q`.
- **Why:** the architecture must match the code (its own rule), and the decisions must be written down before review.
- **Check:** `ruff check .` clean; `pytest -q` passes (old 227 tests; new tests come with `/test`).
- **Done when:** docs match the code and both commands pass.
- **Covers:** AC41, AC42 (final run after `/test`)

## Risks
- **`FakeListChatModel` does not record the messages it receives**, but AC9, AC12, AC15 and AC17 check the prompt. Fallback: the tests use a small recording fake `BaseChatModel` (spec section 8). `/test` handles this; no change to `src/`.
- **`message.text` is a `TextAccessor`, not a plain `str`**, in langchain-core 1.6. Passing it straight to Pydantic or string checks could behave oddly. Fallback: `reply_text` wraps it in `str(...)` (step 3).
- **LangGraph conditional edges without a path map** draw and check badly. Fallback: always pass an explicit path map (step 8).
- **Real `gpt-oss` replies** (reasoning text with braces, code fences) are not tested in this session, because there are no real LLM calls. The parser may need changes after the first real runs in session 5. Logged in spec section 7.
- **Off-by-one in the retry count** would make AC38 fail (5 or 7 executor runs instead of 6). Fallback: step 8's script checks the exact count before `/test`.

## Definition of done
- [ ] Every acceptance criterion in the spec passes as a test (/test)
- [ ] /code-review has no Critical findings
- [ ] Diagnostics: 0 errors
- [ ] progress.md and project-memory.md updated
- [ ] Ready for /git-feature
