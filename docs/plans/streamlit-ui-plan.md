# Implementation plan: Streamlit UI

**Spec:** docs/specs/streamlit-ui.md
**Branch:** feature/streamlit-ui
**Status:** Approved

## Progress
- [x] Step 1: dependencies, Streamlit config, and a feasibility check
- [x] Step 2: `UiSettings` in `src/config.py`
- [x] Step 3: `format_record` and `HTML_COLOURS` in `src/console.py`
- [x] Step 4: `ui/render.py` (safe HTML timeline)
- [x] Step 5: `ui/live.py` (`RunManager`, `live_settings`)
- [x] Step 6: `ui/data.py` (traces and passes from disk)
- [x] Step 7: `ui/charts.py` (three Plotly figures)
- [ ] Step 8: `ui/app.py`, trace explorer and dashboard pages (replay-only works)
- [ ] Step 9: the Run page (live mode)
- [ ] Step 10: docs and decisions

## Overview
Build from the bottom up: first the two small `src/` changes, then the four plain-Python helper modules in `ui/` (no Streamlit import, so each one can be checked in the terminal), then the thin Streamlit pages on top. Replay-only mode works first (Step 8), so the safe mode exists before any code path can start a live run (Step 9).

## Decisions taken from the spec's open questions
Running `/create-plan` approved the spec, so the plan uses its recommended answers. Tell me if one is wrong:
1. Live runs are **off by default** (`UI_ALLOW_LIVE_RUNS=false`). Before Step 9 is checked, Gaurav adds `UI_ALLOW_LIVE_RUNS=true` to his own `.env`. Claude does not edit `.env` (Hard rule 7).
2. Traces for a public copy: **decided in session 9**, not now.
3. Live runs in a **background thread** (D47); UI settings **stay out of `Settings`** (D48).

## Test budget (Hard rule 11)
870 tests now (33 of 100 used). This feature: **about 20 tests, at most 25**, so the suite ends at no more than 895. Tests are written in `/test`, not during `/implement`. Each step is checked with a terminal command, a scratch script in the scratchpad, or diagnostics.

## Note on the code fingerprint
Steps 2 and 3 change `src/`, so the code fingerprint changes. No pass is running, and every future pass needs a new pass id anyway after the session 7 fixes. Nothing in `ui/` is in the fingerprint.

## Steps

### Step 1: dependencies, Streamlit config, and a feasibility check
- **Files:** `requirements.txt`, `requirements-lock.txt`, `.streamlit/config.toml` (new), `.env.example`
- **What:** `pip install streamlit plotly` in `.venv` (Gaurav approves the install). Add `streamlit` and `plotly` to the Runtime part of `requirements.txt`. Add the new pinned lines to `requirements-lock.txt` (only the new packages and their new dependencies; check the diff so no unrelated package changes). `.streamlit/config.toml`: `[browser] gatherUsageStats = false`. `.env.example`: `UI_ALLOW_LIVE_RUNS=false` with a one-line comment.
- **Why:** the roadmap adds these packages in this session and not before. The feasibility check comes first because three later steps depend on what the installed Streamlit version supports.
- **Check:** a scratch script (in the scratchpad, not the repo) confirms: `st.html`, `st.fragment(run_every=...)` and `st.navigation` exist; `streamlit.testing.v1.AppTest` can run a two-page `st.navigation` app headless. Record the Streamlit and Plotly versions in the step's report.
- **Done when:** `python -c "import streamlit, plotly"` works, `pytest -q` still gives 870 passed, and we know whether AC16 can use `AppTest` on the real app or must test a `pages_for(mode)` function (spec section 8).
- **Covers:** setup for all ACs

### Step 2: `UiSettings` in `src/config.py`
- **Files:** `src/config.py`
- **What:** `UiSettings` (frozen, `extra="forbid"`: `allow_live_runs: bool = False`, `model_choices: tuple[str, ...] = ()`), `load_ui_settings(env)` (strict true/false words, the same as the gate flags; value-free `ConfigError`; model choices from `OLLAMA_MODEL` then `OLLAMA_MODEL_ALT`, deduplicated, empty when the main is missing), `get_ui_settings()` (`lru_cache`, loads `.env` like `get_settings`). `Settings` is not touched.
- **Why:** only `config.py` reads the environment (architecture 2). Keeping these out of `Settings` keeps `run_end.config` and the manifest shape the same, so new passes stay comparable with the baseline (D43).
- **Check:** a scratch script calls `load_ui_settings` with a few dicts (empty, `true`, `maybe`, alt equal to main) and prints the results; diagnostics 0 errors; `pytest tests/test_config.py tests/test_run.py -q` passes.
- **Done when:** the printed results match AC1 and AC2, and `ConfigError` for `maybe` does not contain `maybe`.
- **Covers:** AC1, AC2, AC3. Note for `/test`: the existing `run_end.config` test compares with `Settings.model_fields`, so it would **not** notice a new field. The AC2 test should also assert the exact set of 9 `Settings` field names, which makes AC3 real without a new test.

### Step 3: `format_record` and `HTML_COLOURS` in `src/console.py`
- **Files:** `src/console.py`
- **What:** move the per-event text and colour rules from `print_record` into `format_record(record) -> list[tuple[str, str]]` (text with `sanitize` applied, rich colour). `print_record` becomes a loop that calls `_say` for each line. Add `HTML_COLOURS`: one hex per role constant, readable on light and dark backgrounds.
- **Why:** the terminal and the browser must show the same text in the same colours, so the rules live in one function, not two copies.
- **Check:** `pytest tests/test_console.py tests/test_run.py -q` passes **unchanged** (this proves the terminal output did not change); a real `python -m src.run` is not needed. Diagnostics 0 errors.
- **Done when:** every existing console test passes without edits, and every colour in `EVENT_COLOURS` plus `EXECUTOR` and `ERROR` has a hex.
- **Covers:** AC4

### Step 4: `ui/render.py` (safe HTML timeline)
- **Files:** `ui/__init__.py` (new), `ui/render.py` (new)
- **What:** `record_html(record) -> str` (one `<div>` per `format_record` line, colour from `HTML_COLOURS`, `white-space: pre-wrap`, every text part through `html.escape`), `timeline_html(records) -> str`. No Streamlit import.
- **Why:** trace text is untrusted (LLM, web, files). Escaping here, plus `st.html` on the page (no Markdown), keeps every value plain text.
- **Check:** a scratch script prints `record_html` for a record holding `<script>`, `<img onerror>` and `![x](http://...)`, and for `{}` and an unknown event.
- **Done when:** the output shows `&lt;script&gt;` and `&lt;img`, has no raw `<script` or `<img`, and odd records give HTML without an exception.
- **Covers:** AC5, AC6, AC7

### Step 5: `ui/live.py` (`RunManager`, `live_settings`)
- **Files:** `ui/live.py` (new)
- **What:** `RunSnapshot`; `RunManager(run_fn=run_task, start_thread=<daemon thread>)` with `start(task, settings) -> bool` (non-blocking lock; `False` if busy), a `threading.Lock` around the record list, `on_record` that only appends, a `finally` that releases the run lock, and `error="<Type>: <message>"` if `run_fn` raises; `snapshot()` returns a copy. `live_settings(base, ui, *, model, enable_validation, enable_retry)` through `Settings.model_validate`, `ValueError` for a model not in `ui.model_choices`. No Streamlit import.
- **Why:** a click in Streamlit stops the page script with an exception `run_task` does not catch, which would cut a run in half without a `run_end`. A thread keeps the run going (D47).
- **Check:** a scratch script with a fake `run_fn` (sends 3 records, sleeps briefly, returns a state) and the real thread: `start` twice quickly gives `True` then `False`; after the run, `snapshot()` shows 3 records and `running=False`; a raising `run_fn` leaves the manager able to start again. No LLM call.
- **Done when:** all three behaviours print as expected and diagnostics show 0 errors.
- **Covers:** AC8, AC9, AC10, AC11

### Step 6: `ui/data.py` (traces and passes from disk)
- **Files:** `ui/data.py` (new)
- **What:** `TraceSummary`; `list_traces(logs_dir)` (stems matching `RUN_ID_PATTERN` only, newest first, `unreadable` for files over 5 MB or broken, `incomplete` without `run_end`, never raises); `load_trace(run_id, logs_dir)` (through `load_run_trace`); `list_passes(results_root)` (folders matching `PASS_ID_PATTERN`, `load_pass`, broken ones as `(pass_id, error)`). No Streamlit import.
- **Why:** both replay pages read the disk through one tested place, reusing the evaluation code so the numbers match `docs/results.md`.
- **Check:** a scratch script runs `list_traces()` and `list_passes()` on the real `logs/` and `benchmark/results/` (read-only) and prints counts: about 105 traces, passes `main-off-1`, `main-on-1`, `second-on-1`, `smoke-2` (or `smoke-2` listed as broken, with its reason).
- **Done when:** the counts match `ls logs | wc -l` and the results folder, and nothing raises.
- **Covers:** AC12, AC13

### Step 7: `ui/charts.py` (three Plotly figures)
- **Files:** `ui/charts.py` (new)
- **What:** `success_chart(passes)` (complete passes only, % with Wilson error bars), `ablation_chart(rows)` (failed-call rate and success, off vs on), `model_chart(rows)` (success per category per model). Each returns `None` when there is nothing to draw. No Streamlit import.
- **Why:** the charts are pure functions of the evaluation numbers, so a test can check every bar against `pass_metrics` without a browser.
- **Check:** a scratch script builds the three figures from the real passes, prints each bar's value (expected 93.3, 96.7, 96.7 for success), and writes them with `fig.write_html` into the scratchpad for a quick look.
- **Done when:** the printed values equal the numbers in `docs/results.md`, and `smoke-2` is not in the success chart.
- **Covers:** AC14, AC15

### Step 8: `ui/app.py`, trace explorer and dashboard pages (replay-only works)
- **Files:** `ui/app.py` (new), `ui/pages/__init__.py` if needed, `ui/pages/trace_explorer.py` (new), `ui/pages/dashboard.py` (new)
- **What:** `app.py` reads `get_ui_settings()` and `get_settings()`, picks the mode (spec 5.3) with a small `pages_for(live: bool)` function, shows the reason when live runs are off, and calls `st.navigation`. Trace explorer: filters (model, status), run picker, `run_end` summary, step slider, `st.html(timeline_html(...))`, raw records in a collapsed `st.json`. Dashboard: passes table (incomplete and broken passes marked), the three charts or "no pair yet", the relative-reduction text in the report's wording, and the traced-run count ("all runs in `logs/`"). Disk reads cached with `st.cache_data(ttl=...)`. Neither page imports `src.run`.
- **Why:** replay-only mode is the safe default and needs no LLM, so it is built and checked first.
- **Check:** with `UI_ALLOW_LIVE_RUNS` unset, Gaurav runs `python -m streamlit run ui/app.py` and opens the browser: only two pages, a past trace replays in role colours, the dashboard shows 93.3 / 96.7 / 96.7. Ruff clean, diagnostics 0 errors.
- **Done when:** Gaurav confirms both pages work in the browser, and the navigation shows no Run page.
- **Covers:** AC16 (replay side), AC17 (no forbidden imports in these files)

### Step 9: the Run page (live mode)
- **Files:** `ui/pages/live_run.py` (new), `ui/app.py` (add the page in live mode)
- **What:** task box (`max_chars=2000`, Run disabled when empty), model select from `model_choices`, two toggles defaulting to the `.env` values, the other limits shown read-only, a one-line warning not to run a benchmark pass at the same time, a cost note (one run uses Ollama quota). One `RunManager` per process through `st.cache_resource`. The page re-checks the mode before `start`. A `st.fragment(run_every=1)` polls `snapshot()` and draws the timeline; when the run ends, the page shows the final answer (green or red), run id, trace path and counters. "A run is already in progress" when `start` returns `False`.
- **Why:** the roadmap's main goal: a task running live in the browser.
- **Check:** Gaurav adds `UI_ALLOW_LIVE_RUNS=true` to `.env`, restarts the app, and runs one short real task (for example a calculator task; it uses one run of Ollama quota). Events must appear while the run is going, clicking another page and back must not stop it, and the trace must appear in the explorer with a `run_end`.
- **Done when:** that real run finishes with `run_end`, the live events match the trace file, and diagnostics show 0 errors.
- **Covers:** AC16 (live side), AC17, roadmap "a task runs live in the browser"

### Step 10: docs and decisions
- **Files:** `docs/specs/architecture.md` (layer 9 in the table, a short section 2.8 on the UI, `ui/` outside the fingerprint), `CLAUDE.md` (command `python -m streamlit run ui/app.py`, `ui/` in the layout), `docs/project-memory.md` (the command), `docs/plans/decisions.md` (D47 background thread, D48 UI settings outside `Settings`, and the replay-only default), `docs/plans/progress.md`
- **What:** write down what was built and the two design choices.
- **Why:** the next session (README) and future readers need the command and the reasons.
- **Check:** read the changed sections once; the command in CLAUDE.md is the one used in Steps 8 and 9.
- **Done when:** all five files are updated and committed.
- **Covers:** none directly (documentation)

## Risks
- **Streamlit API differences** (`st.html`, `st.fragment`, `st.navigation`, `AppTest` with navigation). Fallback: Step 1 checks them before any UI code. If `AppTest` cannot drive navigation, AC16 tests `pages_for(live)` instead. If `st.fragment(run_every=...)` is missing, the page polls with a short `time.sleep(1)` and `st.rerun()` while the run is active.
- **Threads and Streamlit.** Calling `st.*` from the run thread fails. Fallback by design: the thread only appends to a list under a lock; all drawing happens in the page script.
- **Hot reload during development** can create a second `RunManager` while an old run thread is still going, so two runs could overlap for a moment. Only during development; restart the app before a real run.
- **`st.html` sanitising.** If it strips the `style` attribute, the colours disappear (text stays safe). Fallback: a small fixed set of CSS classes in one `<style>` block built from `HTML_COLOURS`.
- **Install changes the lock file more than expected.** Fallback: add only the new pins by hand and check `git diff requirements-lock.txt` before committing.
- **Quota:** only Step 9 makes real LLM calls (one short run). Every test mocks `run_task`.

## Definition of done
- [ ] Every acceptance criterion in the spec passes as a test (/test), within the test budget (at most 25 new tests)
- [ ] /code-review has no Critical findings
- [ ] Diagnostics: 0 errors
- [ ] progress.md and project-memory.md updated
- [ ] Ready for /git-feature
