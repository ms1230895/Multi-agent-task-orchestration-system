# Spec: Streamlit UI

**Status:** Draft
**Branch:** feature/streamlit-ui
**Date:** 2026-10-17

## 1. Goal
A Streamlit app with three pages: (1) **Run a task** live in the browser, with a model selector and validation/retry toggles, showing a colour-coded event timeline as events happen; (2) **Trace explorer** to replay any run from `logs/`; (3) **Benchmark dashboard** with Plotly charts of the real pass results. A **replay-only mode** shows only pages 2 and 3 and can never call an LLM, so it is safe to host publicly.

## 2. Why
Today the system can only be watched in a terminal, and the results live in Markdown tables. A UI makes the project easy to demo (CV project) and makes traces easier to read than raw JSONL.

Architecture layer: a new top layer, **9 Interface** (`ui/`). It only displays what lower layers produce: live records from `run_task` (layer 6), trace files from the trace logger, and pass numbers from the evaluation tools (layer 8). It contains **no agent logic** (roadmap 8). Like `evaluation/`, `ui/` is outside the code fingerprint (`src/**/*.py`, `benchmark/*.py`, `benchmark/tasks.json`), so UI work never blocks resuming a pass.

Two small changes in `src/` are needed (config and console, section 5.1). They change the code fingerprint, so any future benchmark pass needs a new pass id. That is already true after the session 7 fixes (D44 to D46), so no extra cost.

## 3. Scope
**In scope:**
- `ui/` package: app entry, three pages, and four plain-Python helper modules (render, live, data, charts) that can be tested without a browser.
- `src/config.py`: UI settings (`UI_ALLOW_LIVE_RUNS`, model choices from `OLLAMA_MODEL` and `OLLAMA_MODEL_ALT`), kept **separate** from `Settings`.
- `src/console.py`: one shared formatter used by both the terminal and the UI, plus one HTML colour per role colour.
- Live runs in a background thread, one run at a time per server process.
- Replay-only mode (the default unless live runs are switched on).
- `streamlit` and `plotly` added to `requirements.txt` (and pinned in `requirements-lock.txt`), `.streamlit/config.toml` (usage statistics off), `UI_ALLOW_LIVE_RUNS=false` in `.env.example`.
- Docs: architecture (layer 9 and a short UI section), CLAUDE.md (command, layout), `docs/project-memory.md` (command).

**Out of scope:**
- Deploying the app anywhere, and deciding which traces a public copy would ship (`logs/` is gitignored). Open question 2.
- Login or any access control. Replay-only mode is the safety mechanism.
- Stopping a run that has started. A run always ends through the hard limits (Hard rule 5).
- Changing limits (`MAX_RETRIES` etc.) from the UI. Only the model and the two gate flags can be changed; every other setting comes from `.env`.
- Starting benchmark passes from the UI. The dashboard only reads `benchmark/results/`.
- Several live runs at the same time.
- Any change to agents, graph, tools, validation, benchmark or evaluation code.
- README screenshots (session 9).

## 4. Inputs and outputs

**Command:** `python -m streamlit run ui/app.py` (from the project root). `python -m` puts the project root on `sys.path`, so `from src...` imports work; plain `streamlit run` would only add `ui/`.

**New environment variables** (read only by `src/config.py`, never stored in `Settings`):

| Variable | Default | Meaning |
|---|---|---|
| `UI_ALLOW_LIVE_RUNS` | `false` | `true` shows the Run page. Same strict words as the other flags (`true/false/1/0/yes/no`). |
| `OLLAMA_MODEL_ALT` | (none) | Second model for the selector. Already in `.env.example`; until now nothing read it. |

**Run page inputs:** task text (`str`, 1 to `MAX_TASK_CHARS` = 2000 characters), model (`str`, one of `model_choices`), `enable_validation` (`bool`), `enable_retry` (`bool`). Defaults: the `.env` values.
**Run page output:** the live timeline (one coloured line group per trace record), then the final answer (green when done, red when failed), status, run id, trace path and counters.

**Trace explorer input:** a run id picked from a list (filter by model and status). **Output:** the run summary from `run_end` (task, status, model, duration, counters, gate flags), the timeline of all events, a slider to step through events one by one, and the raw records in a collapsed JSON view.

**Dashboard input:** every pass folder in `benchmark/results/`. **Output:** a passes table, three Plotly charts (section 5.6), and the number of traced runs (`run_end` events in `logs/`).

## 5. Design

### 5.1 Changes in `src/`

**`src/config.py`**
```python
class UiSettings(BaseModel):          # frozen, extra="forbid"
    allow_live_runs: bool = False
    model_choices: tuple[str, ...] = ()

def load_ui_settings(env: Mapping[str, str]) -> UiSettings
def get_ui_settings() -> UiSettings   # lru_cache, loads .env like get_settings
```
- `model_choices` = `OLLAMA_MODEL`, then `OLLAMA_MODEL_ALT` if it is set, non-empty and different. Empty tuple when `OLLAMA_MODEL` is missing (the UI then runs replay-only).
- An invalid `UI_ALLOW_LIVE_RUNS` raises `ConfigError("Invalid configuration: UI_ALLOW_LIVE_RUNS must be true or false")`, never showing the value (Hard rule 7).
- **Why separate from `Settings`:** `Settings.model_dump()` is `run_end.config` and the pass manifest's `config`. Adding a field there would make every new pass look like a config change against the baseline passes, and the report would refuse to compare them (D43). A test already fixes the exact `run_end.config` keys.

**`src/console.py`**
- `format_record(record) -> list[tuple[str, str]]`: the text lines and rich colour of one trace record, exactly what `print_record` prints today (same wording, same colour rules, `sanitize` applied). `print_record` becomes a loop over `format_record` that calls `_say`. The existing `print_record` tests must pass unchanged.
- `HTML_COLOURS: dict[str, str]`: one hex colour per role constant (`PLANNER`, `EXECUTOR`, `CRITIC`, `TOOL`, `RETRY`, `SUCCESS`, `ERROR`), chosen to read well on both light and dark backgrounds. The UI never writes a colour itself (colour-coding rule).

### 5.2 `ui/` layout
```
ui/
  __init__.py
  app.py              # entry: reads UiSettings, picks the pages, st.navigation
  render.py           # record -> safe HTML (no Streamlit import)
  live.py             # RunManager, live_settings (no Streamlit import)
  data.py             # traces and passes from disk (no Streamlit import)
  charts.py           # Plotly figures from metrics (no Streamlit import)
  pages/
    live_run.py       # Run a task
    trace_explorer.py # Replay a trace
    dashboard.py      # Benchmark dashboard
.streamlit/config.toml  # browser.gatherUsageStats = false
```
The four helper modules hold all logic and are unit-tested. The page files only lay out widgets and call the helpers.

### 5.3 `ui/app.py`: mode
1. Read `get_ui_settings()`. On `ConfigError`, use replay-only and show the message (no values).
2. Live mode only when `allow_live_runs` is true **and** `get_settings()` loads. Otherwise replay-only, with a one-line note saying why ("live runs are off: set `UI_ALLOW_LIVE_RUNS=true`", or the `ConfigError` text).
3. `st.navigation` gets `[live_run, trace_explorer, dashboard]` in live mode and `[trace_explorer, dashboard]` in replay-only mode. A page that is not in the list cannot be opened, so replay-only mode has no code path to `run_task`.
4. Defence in depth: `live_run.py` checks the mode again before starting a run.

### 5.4 `ui/render.py`: the timeline
- `record_html(record: dict) -> str`: calls `format_record`, then builds one `<div>` per line with `style="color:<HTML_COLOURS[colour]>; white-space:pre-wrap"`. **Every text part is passed through `html.escape`** before it goes into the HTML. Returns valid HTML for any input; never raises.
- `timeline_html(records: list[dict]) -> str`: the records in order, joined.
- The pages render it with `st.html`, which does not process Markdown. **Why:** trace text comes from the LLM, web pages and files. With `st.markdown(..., unsafe_allow_html=True)`, a result like `![x](http://tracker)` would load an outside image, and `<script>` would at best be stripped. Escaping plus `st.html` keeps every trace value plain text.

### 5.5 `ui/live.py`: running a task without blocking the page
```python
@dataclass(frozen=True)
class RunSnapshot:
    records: list[dict]        # copy, in arrival order
    running: bool
    state: AgentState | None   # final state when finished
    error: str                 # "" or "<Type>: <message>" if run_fn itself raised

class RunManager:
    def __init__(self, run_fn: Callable[..., AgentState] = run_task,
                 start_thread: Callable[[Callable[[], None]], None] = <starts a daemon Thread>)
    def start(self, task: str, settings: Settings) -> bool   # False if a run is active
    def snapshot(self) -> RunSnapshot

def live_settings(base: Settings, ui: UiSettings, *, model: str,
                  enable_validation: bool, enable_retry: bool) -> Settings
```
- **Why a thread:** Streamlit re-runs the page script when the user clicks anything, and that stops the running script with an exception that is not an `Exception` (so `run_task` does not catch it). A run inside the page script would then stop halfway with no `run_end`. In a background thread the run continues whatever the user clicks; the page polls `snapshot()` once a second with `st.fragment(run_every=1)` and shows new records.
- `on_record` only appends to a list under a `threading.Lock`. It never calls Streamlit (Streamlit calls are not allowed from other threads).
- One `RunManager` per server process (`st.cache_resource`). `start` takes the run lock without waiting; if a run is active it returns `False` and the page says "a run is already in progress". The lock is released in `finally`, so a crash in `run_fn` (even a `ConfigError`) cannot block later runs.
- `start_thread` is injectable so tests run the job synchronously, with no real thread.
- `live_settings` builds the run's settings with `Settings.model_validate({**base.model_dump(), "ollama_model": model, "enable_validation": ..., "enable_retry": ...})`, so every other limit stays the `.env` value. A `model` not in `ui.model_choices` raises `ValueError` (the browser can never pick a model name that is not in `.env`, Hard rule 2).
- The run's `run_end.config` records the chosen model and flags, so a UI run with the gate off is clearly marked in its trace.

### 5.6 `ui/data.py` and `ui/charts.py`: reading and charting
```python
@dataclass(frozen=True)
class TraceSummary:
    run_id: str; model: str; task: str; status: str   # "done" | "failed" | "incomplete" | "unreadable"
    duration_ms: int | None; counters: dict[str, int]

def list_traces(logs_dir: Path = LOGS_DIR) -> list[TraceSummary]      # newest first
def load_trace(run_id: str, logs_dir: Path = LOGS_DIR) -> list[dict] | None   # evaluation.passes.load_run_trace
def list_passes(results_root: Path = RESULTS_DIR) -> tuple[list[PassData], list[tuple[str, str]]]
```
- `list_traces` reads every `*.jsonl` whose stem matches `RUN_ID_PATTERN` (from `evaluation/passes.py`); other files are skipped. A file over 5 MB, or one that cannot be read or parsed, is listed as `unreadable`. A trace with no `run_end` (Ctrl-C, server stopped) is `incomplete`. Newest first = run id descending (the id starts with the UTC time).
- `list_passes` loads every folder name matching `PASS_ID_PATTERN` with `load_pass`. A broken pass becomes `(pass_id, error)` and is listed on the page, never a crash.
- Reused, not copied: `load_pass`, `pass_metrics`, `group_passes`, `load_run_trace` from `evaluation/passes.py`; `compare_groups`, `count_traces` from `evaluation/report.py`. So the dashboard shows exactly the numbers in `docs/results.md`.

`ui/charts.py` (return `plotly.graph_objects.Figure`, or `None` when there is nothing to draw):
1. `success_chart(passes)`: one bar per **complete** pass, success rate in %, error bars from the 95% Wilson interval. Caption on the page: "one task = 3.3 points".
2. `ablation_chart(rows)`: from the `ablation` rows of `compare_groups`: failed-tool-call rate and success rate, gate off vs on, side by side. The page prints the relative reduction with the report's wording (`n/a` when the off rate is 0, a negative value as an increase).
3. `model_chart(rows)`: from the `model` rows: success per category for each model.

Incomplete passes (for example `smoke-2`) appear in the table with "incomplete" and in no chart, the same rule as the report.

## 6. Acceptance criteria

Config and console
- [ ] AC1: `load_ui_settings({})` gives `allow_live_runs=False`; `UI_ALLOW_LIVE_RUNS=true` gives `True`; `UI_ALLOW_LIVE_RUNS=maybe` raises `ConfigError` whose message names the variable and does not contain `maybe`.
- [ ] AC2: `model_choices` is `(main, alt)` when both are set; `(main,)` when the alt is missing, empty, or equal to the main; `()` when `OLLAMA_MODEL` is missing.
- [ ] AC3: `Settings` has no new field: the existing `run_end.config` key test passes unchanged.
- [ ] AC4: for a sample of every event type (including a failed `tool_call` and a failed `run_end`), `print_record` prints exactly the lines of `format_record`, and every colour `format_record` returns has an entry in `HTML_COLOURS`. The existing `print_record` tests pass unchanged.

Render
- [ ] AC5: `record_html` colours each line with the `HTML_COLOURS` hex of its role: plan magenta, tool call blue (red when `ok` is false), executor output cyan and critic yellow, retry and replan dark_orange, `run_end` green when done and red otherwise.
- [ ] AC6: untrusted text is inert: for a record whose fields contain `<script>alert(1)</script>`, `<img src=x onerror=alert(1)>` and `![x](http://example.com/t.png)`, the HTML contains `&lt;script&gt;` and `&lt;img`, and no `<script` or `<img` tag; control characters are replaced.
- [ ] AC7: `record_html` returns a string and does not raise for a record with no `data`, `data` that is not a dict, or an unknown event.

Live runs
- [ ] AC8: with a fake `run_fn` that sends 3 records and returns a done state, and a synchronous `start_thread`, `start` returns `True` and `snapshot()` shows the 3 records in order, `running=False` and the final state.
- [ ] AC9: while a run is active, a second `start` returns `False` and `run_fn` is not called again.
- [ ] AC10: if `run_fn` raises (`ConfigError` or `RuntimeError`), the snapshot is finished with `error` starting with the exception type, and the next `start` returns `True`.
- [ ] AC11: `live_settings` changes only `ollama_model`, `enable_validation` and `enable_retry` (every other field equals `base`); a model not in `ui.model_choices` raises `ValueError`.

Data and charts
- [ ] AC12: `list_traces` on a temporary folder with a done trace, a failed trace, a trace with no `run_end`, an invalid-JSON file and a file named `notes.jsonl` (not a run id) returns 4 rows, newest first, with statuses `done`, `failed`, `incomplete`, `unreadable`; it never raises.
- [ ] AC13: `list_passes` on a temporary results folder with one valid pass and one pass with a broken `manifest.json` returns the valid pass and one `(pass_id, error)` entry.
- [ ] AC14: `success_chart` has one bar per complete pass, whose height equals `pass_metrics(...)["success_rate"] * 100` and whose error bar ends equal the Wilson interval × 100; an incomplete pass is not in it. With no complete pass it returns `None`.
- [ ] AC15: `ablation_chart` and `model_chart` return `None` when `compare_groups` gives no row of their kind, and otherwise plot the rates from those rows.

App and boundaries
- [ ] AC16: replay-only mode (`allow_live_runs=False`, or `get_settings` raises `ConfigError`): the app's page list has only the trace explorer and the dashboard, and a patched `run_task` that fails the test if called is never called.
- [ ] AC17: no file in `ui/` imports `src.agents`, `src.graph`, `src.tools` or `src.validation`, and no file in `ui/` contains either model name (Hard rule 2).

## 7. Edge cases and errors
- **No `logs/` folder or no traces:** the explorer says "no traces yet". **No passes:** the dashboard says "no benchmark passes yet".
- **Broken trace or pass file:** listed as unreadable or broken, with the reason; the other runs and passes still show.
- **Very large trace file (> 5 MB):** listed as unreadable, not loaded (keeps a public host from reading huge files into memory).
- **Empty task:** the Run button is disabled. **Too long:** the text box stops at 2000 characters (`max_chars`); `run_task` would refuse it anyway.
- **Ollama down, usage cap, timeout:** `run_task` returns a failed state with `error="<Type>: ..."`; the page shows it in red. Not a crash.
- **`OLLAMA_MODEL` missing while live runs are allowed:** replay-only with the `ConfigError` message.
- **User clicks around or leaves the page during a run:** the run continues in its thread; going back to the Run page shows its progress or result.
- **Two browser tabs:** one run per server process; the second tab sees "a run is already in progress" and can watch it.
- **Server stopped during a run:** the run ends without `run_end`; the explorer lists that trace as `incomplete`.
- **A benchmark pass running in a terminal at the same time:** both use `workspace/`, and the runner empties it before each task. Not enforced in code; the Run page shows a one-line warning not to run both at once.
- **UI runs count as traced runs:** they write normal traces to `logs/`, so `count_traces` includes them. They are real runs with full traces, so this is honest; the dashboard says the count covers all runs in `logs/`.
- **Secrets (Hard rule 7):** the UI shows only model names and the settings already in `run_end.config`; never other `.env` values. Error text from config never contains values.
- **Streamlit usage statistics:** turned off in `.streamlit/config.toml`, so task text and page use are not sent anywhere.

## 8. Test plan
All tests are unit tests: no LLM, no internet, no real `logs/` or `benchmark/results/` (Hard rule 8; temporary folders and fake `run_fn`). **Budget:** 870 tests now, so 33 of the 100 are used; this feature aims for **about 20 tests, at most 25**.

| File | Tests | Covers |
|---|---|---|
| `tests/test_config.py` (added) | 2 | AC1, AC2 (parametrize kept small) |
| `tests/test_console.py` (added) | 1 | AC4 |
| `tests/ui/test_render.py` | 3 | AC5, AC6, AC7 |
| `tests/ui/test_live.py` | 4 | AC8, AC9, AC10, AC11 |
| `tests/ui/test_data.py` | 2 | AC12, AC13 |
| `tests/ui/test_charts.py` | 2 | AC14, AC15 |
| `tests/ui/test_app.py` | 2 | AC16 (Streamlit `AppTest`, headless), AC17 (source scan) |

AC3 is covered by the existing `run_end.config` test. If `AppTest` cannot drive `st.navigation` in the installed Streamlit version, AC16 tests a small `pages_for(mode)` function in `app.py` instead; the plan decides.

## 9. Open questions
1. **Live runs off by default?** Recommended: yes. A copy of the app that is hosted somewhere without `UI_ALLOW_LIVE_RUNS=true` can never spend Ollama quota or run tools. Cost: Gaurav adds `UI_ALLOW_LIVE_RUNS=true` to his own `.env` once (Claude does not edit `.env`, Hard rule 7).
2. **Traces for a public copy:** `logs/` is gitignored, so a hosted replay-only copy would show the dashboard (results are committed) but no traces. Proposed: decide in session 9 (for example a small, checked set of sample traces). Not part of this session.
3. **Background thread:** chosen over running inside the page script (section 5.5) because a click would otherwise stop the run without a `run_end`. To be logged as a decision (D47), together with "UI settings stay out of `Settings`" (D48).
