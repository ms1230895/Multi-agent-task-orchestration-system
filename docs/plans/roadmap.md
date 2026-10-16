# Roadmap

Nine sessions, built in this order. Each session is one feature branch (`feature/<session-name>`) and follows the workflow in CLAUDE.md (spec → plan → implement → test → review → merge). Do not start a session before the ones it depends on are merged into `main`.

Architecture reference: `docs/specs/architecture.md`.

---

## 1. architecture-and-roadmap
- **Goal:** Write down what we build and in what order, before writing any code.
- **Depends on:** Setup phase (CLAUDE.md, rules, hooks, `.vscode/`).
- **Done when:**
  - `docs/specs/architecture.md` and this roadmap exist and Gaurav has approved them.
  - Open questions in the architecture are answered or logged in `docs/plans/decisions.md`.
  - Merged into `main`.

## 2. foundation-tools-validation
- **Goal:** Build the bottom layers: config, console, the 4 tools, the registry, and the validation gate with retry.
- **Depends on:** 1.
- **Builds:** `src/config.py` (`.env` loading, Pydantic settings, `get_llm()`), `src/console.py` (rich colours), `src/tools/` (web_search, calculator, read_file, write_file, registry), `src/validation.py` (`call_tool`: exists → validate → run → retry).
- **Done when:**
  - Every tool returns `{ok, output, error}` and never raises, including for bad input.
  - Calculator uses `ast` only; tests show `eval`-style attacks (e.g. `__import__`) are rejected.
  - File tools reject `..`, absolute paths, and symlinks that leave `workspace/`.
  - `call_tool` handles unknown tool, invalid args, exceptions, and transient retries; `ENABLE_VALIDATION` and `ENABLE_RETRY` switch their parts off.
  - Config refuses to start without `OLLAMA_MODEL`; model name appears nowhere in code.
  - `pytest -q` passes with no network or LLM calls (web search mocked); `ruff check .` is clean.

## 3. graph-planner-critic
- **Goal:** Build the LangGraph skeleton with the router and its hard limits, plus the planner and critic agents.
- **Depends on:** 2 (config, `get_llm()`).
- **Builds:** `src/state.py` (`AgentState`), `src/agents/planner.py`, `src/agents/critic.py`, `src/graph.py` (nodes, `advance`, router, finalize, `recursion_limit`). The executor is a stub that returns a fixed output.
- **Done when:**
  - Planner and critic outputs are validated by Pydantic (`Plan`, `Verdict`), with one re-ask on invalid JSON.
  - Router unit tests cover all 6 rules: next step, done, retry, replan, failed after limits, and planner failure.
  - A test with a fake "always retry" critic proves the run ends as `failed` within the limits and never hits `recursion_limit`.
  - The graph runs end-to-end with mocked LLMs and the stub executor.
  - `pytest -q` passes, no real LLM calls.

## 4. executor
- **Goal:** Build the executor with its inner tool loop and connect it into the graph in place of the stub.
- **Depends on:** 2 (tools, `call_tool`), 3 (graph, state).
- **Builds:** `src/agents/executor.py` (bind tools, loop: LLM → tool calls → `call_tool` → `ToolMessage`, max `MAX_TOOL_ITERATIONS`).
- **Done when:**
  - Every tool call goes through `call_tool`; nothing calls a tool function directly.
  - Tests with a fake LLM cover: no tool needed, one tool call, a failed call that the LLM then corrects, and hitting the iteration limit.
  - The executor uses critic feedback on a retry.
  - The full graph runs with mocked LLMs, executor included.
  - `pytest -q` passes.

## 5. tracing-and-first-runs
- **Goal:** Log every run as a JSONL trace, add the `run_task` entry point, and do the first real end-to-end runs.
- **Depends on:** 3, 4.
- **Builds:** `src/logger.py` (7 event types, `model` on every line), `src/run.py` (`run_task`, CLI `python -m src.run "<task>"`).
- **Done when:**
  - All 7 event types are written by the right layer; each trace line includes `model`; `run_end` includes config and counters.
  - `run_task` never raises; a crash becomes a failed state plus a `run_end` event.
  - At least 5 real runs with `gpt-oss:120b-cloud` (one per tool, plus one multi-tool) finish without hanging, and their traces are readable.
  - Problems found in these runs are written down (fixed now if small, or listed for session 7).
  - Run command added to CLAUDE.md and `docs/project-memory.md`.
- **Note:** design `run_task` so it streams trace events as they happen (a generator or callback), so a UI can show live progress using the same events the logger writes. No agent logic in the UI.

## 6. benchmark
- **Goal:** Create the 30-task benchmark, its code checkers, and a runner that can pause and resume.
- **Depends on:** 5 (`run_task`, traces).
- **Builds:** `benchmark/tasks.json`, `benchmark/checkers.py`, `benchmark/runner.py` (pass folders, manifest, resume, config-mismatch refusal, infrastructure-error pause).
- **Done when:**
  - 30 tasks across calc, search, file, and multi-tool, each with a code checker and a fixed expected answer (search facts that do not change).
  - Every checker has unit tests for a passing and a failing answer.
  - The runner refuses to resume a pass whose model or flags differ from the manifest, skips finished tasks, and pauses (without recording a failure) on usage cap / connection errors. Tested with mocks.
  - One small real smoke pass (about 3 tasks) completes and writes results.
  - Benchmark command added to CLAUDE.md and `docs/project-memory.md`.

## 7. evaluation
- **Goal:** Measure the real numbers: off vs on ablation, 100+ traced runs, a second-model comparison, and fixes for the top failures.
- **Depends on:** 6.
- **Done when:**
  - Full passes finished: main model off, main model on, second model on (second model off if quota allows), each with its manifest.
  - Total `run_end` traces ≥ 100 (repeat passes if needed).
  - Success rate and failed-tool-call rate are reported per pass, with the real numbers, even if they miss the targets.
  - The top 3 failure causes are found from the traces. Fixes go in as separate small changes, and a fresh pass is run after the fixes (results from before and after are never mixed).
  - A results table and short analysis are saved in `docs/results.md`.

## 8. streamlit-ui
- **Goal:** A Streamlit app with three pages: (1) Run a task live, with model selector, validation/retry toggles, and a colour-coded event timeline using the same role colours as the terminal; (2) Trace explorer to replay any run from `logs/`; (3) Benchmark dashboard with Plotly charts for success rate, ablation (off vs on), and model comparison.
- **Depends on:** 5 (tracing-and-first-runs: the event stream), 6 and 7 (benchmark and evaluation: results to chart).
- **Done when:**
  - A task runs live in the browser with events appearing as they happen.
  - Any past trace can be replayed.
  - The dashboard shows real evaluation numbers.
  - A replay-only mode exists that makes no LLM calls (safe for public hosting).
- **Note:** the UI only displays events and results. All agent logic stays in `src/`. Add `streamlit` and `plotly` to `requirements.txt` in this session, not before.

## 9. readme-and-writeup
- **Goal:** README with architecture diagram, setup steps, UI screenshots, real benchmark numbers, and honest limitations; plus CV bullet points based on measured results.
- **Depends on:** all previous sessions (1 to 8).
- **Done when:**
  - A new person can clone, install, and run a task by following the README alone.
