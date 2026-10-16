# Implementation plan: Benchmark

**Spec:** docs/specs/benchmark.md
**Branch:** feature/benchmark
**Status:** Waiting for approval

## Progress
- [ ] Step 1: Package and text helpers (`benchmark/text.py`)
- [ ] Step 2: Task schema and `load_tasks` (`benchmark/tasks.py`)
- [ ] Step 3: Checkers (`benchmark/checkers.py`)
- [ ] Step 4: The 30 tasks (`benchmark/tasks.json`)
- [ ] Step 5: Workspace reset and setup (`benchmark/runner.py`, part 1)
- [ ] Step 6: Pass setup: pass id, fingerprint, manifest, refusal (runner, part 2)
- [ ] Step 7: The task loop: results, pause, crashes (runner, part 3)
- [ ] Step 8: Summary and CLI (runner, part 4)
- [ ] Step 9: Docs sync
- [ ] Step 10: Real smoke pass `smoke-1`

## Overview
Build bottom-up, pure code first. First the text helpers (number extraction and normalising), which both the task validation and the checkers need. Then the task schema, the checkers, and the real task file, checked against hand-written answers. Then the runner in four parts: workspace, pass setup, task loop, summary and CLI. Each part is checked with a short `python -` script using a fake `run_fn`, `tmp_path`-style temp folders and `load_settings({...})` with a dummy model (no LLM, no network), plus `ruff check`. The full test suite is written afterwards with `/test`. The real smoke pass comes last.

**One change from the spec's file list:** `extract_numbers` and the two normalisers live in a small extra file, `benchmark/text.py`, and `benchmark/checkers.py` re-exports `extract_numbers`. **Why:** `tasks.py` needs `extract_numbers` for rule 5.1.6, and `checkers.py` needs the check models from `tasks.py`. With both in `checkers.py` the two modules would import each other. The spec's signature `checkers.extract_numbers` still works.

The spec's open questions are settled with the spec's recommendations (running this skill approved the spec): success = `done` and all checks pass; `MAX_REPLANS=1` (Gaurav sets `.env` in step 10); `benchmark/results/` is committed; a `checks` list per task; every `ResponseError` pauses. All are recorded as decisions in step 9.

Checked while planning: the Ollama client turns `httpx.ConnectError` into `ConnectionError` and raises `ollama.ResponseError` for server errors (`.venv/.../ollama/_client.py`, `_types.py`); httpx timeouts (`ReadTimeout` and others) pass through. That matches the spec's infrastructure list in 5.5.

## Steps

### Step 1: Package and text helpers (`benchmark/text.py`)
- **Files:** `benchmark/__init__.py` (new, docstring only), `benchmark/text.py` (new)
- **What:**
  - `extract_numbers(text) -> list[float]`: one compiled regex for: optional sign (`-` only when not after a letter or digit, via a lookbehind), an integer part that is either `\d{1,3}` followed by groups of `[,   ]\d{3}` or plain `\d+`, an optional `.\d+`, an optional exponent `[eE][+-]?\d+`; separators removed before `float()`.
  - `normalize_words(text) -> str`: NFKD, drop combining marks, `casefold()`, collapse whitespace.
  - `contains_phrase(text, phrase) -> bool`: both normalised, `re.search(r"(?<![^\W_])" + re.escape(phrase) + r"(?![^\W_])", text)` (no letter or digit directly around it).
  - `normalize_lines(text) -> str`: `\r\n`/`\r` → `\n`, strip trailing spaces per line, strip leading and trailing blank lines.
- **Why:** both the task-file rule 5.1.6 and the checkers use these; first-run problem P5 (thousands separators) is solved here.
- **Check:** script with every example of AC7 and AC9's normalising cases (`García Márquez`, `Mozart`/`art`) and AC11's `exact` example. `ruff check benchmark`.
- **Done when:** every example gives the spec's result.
- **Covers:** AC7 (and the helper part of AC9, AC11)

### Step 2: Task schema and `load_tasks` (`benchmark/tasks.py`)
- **Files:** `benchmark/tasks.py` (new)
- **What:**
  - `TASKS_PATH`, `MAX_CHECKS = 3`, `MAX_SETUP_FILE_CHARS = 10_000`, `MAX_PATH_CHARS = 255`, `TasksFileError`.
  - Pydantic models `NumberCheck`, `ContainsCheck`, `RegexCheck`, `ExactCheck` (all `extra="forbid"`, `frozen=True`), the discriminated union `Check`, and `BenchmarkTask` (id pattern, category, task length 1 to `MAX_TASK_CHARS` from `src/state.py`, `setup_files`, 1 to 3 checks).
  - Validators: `tolerance >= 0`; `contains` needs one non-empty list; regex must compile; a shared `_check_relative_path` (empty, absolute, `..` part, backslash, `\x00`, length); id prefix matches category (`multi` for `multi-tool`); a `number` value not in the task text (`extract_numbers(task)` within tolerance).
  - `load_tasks(path)`: read, `json.loads`, validate each task, then check unique ids. Every problem becomes `TasksFileError("<task id or index>: <problem>")`, built from the Pydantic error's location and type, not its full text.
- **Why:** a mistake in the task file must stop the benchmark before any LLM call, and paths must stay inside `workspace/` (Hard rule 4).
- **Check:** script that writes small task files to a temp folder: one valid, then one per AC3/AC4/AC5 problem, each raising `TasksFileError` with the id in the message. `ruff check`.
- **Done when:** all invalid cases raise with a clear message and the valid one loads.
- **Covers:** AC3, AC4, AC5

### Step 3: Checkers (`benchmark/checkers.py`)
- **Files:** `benchmark/checkers.py` (new)
- **What:**
  - `CheckResult` (Pydantic: `type`, `source`, `passed`, `detail`), `MAX_DETAIL_CHARS = 200`, `MAX_SNIPPET_CHARS = 100`, `MAX_CHECK_FILE_BYTES = 100_000`.
  - `_read_source(check, answer, workspace) -> (text | None, source, error_detail)`: `file=None` → the answer; otherwise resolve `workspace / file`, refuse if not inside `workspace.resolve()` (symlink out), missing → `file not found: <file>`, too large → `file too large`, `OSError`/`UnicodeDecodeError` → `file unreadable`.
  - One function per type (`_check_number`, `_check_contains`, `_check_regex`, `_check_exact`), dispatched by `check.type`; `run_check` and `run_checks` (all checks, in order). Details cut with `trim` from `src/logger.py`.
  - Re-export `extract_numbers`.
- **Why:** the decision pass/fail is code only (Hard rule 6) and never raises, so one bad file never stops a pass.
- **Check:** script in a temp workspace covering AC8–AC14 (including a symlink pointing outside, and a 100 001-byte file). `ruff check`.
- **Done when:** every case matches the spec's table and nothing raises.
- **Covers:** AC8, AC9, AC10, AC11, AC12, AC13, AC14

### Step 4: The 30 tasks (`benchmark/tasks.json`)
- **Files:** `benchmark/tasks.json` (new)
- **What:** the 30 tasks of spec section 5.3, word for word, in table order (calc, search, file, multi-tool), with their setup files and checks.
- **Why:** the fixed benchmark; expected answers are set before any measurement (Hard rule 6).
- **Check:** script: `load_tasks()` gives 30 tasks, 8/7/7/8 per category; then for every task, run its checks on a hand-written correct answer (and files written to a temp workspace) and on a wrong one. These pairs become the AC6 test data in `/test`. Also recompute the calc and multi-tool numbers with Python.
- **Done when:** the file loads, every correct answer passes, every wrong answer fails.
- **Covers:** AC1, AC2, AC6

### Step 5: Workspace reset and setup (`benchmark/runner.py`, part 1)
- **Files:** `benchmark/runner.py` (new)
- **What:** module docstring, `RESULTS_DIR = PROJECT_ROOT / "benchmark" / "results"`, `reset_workspace(workspace)` (create if missing; for each entry except `.gitkeep`: symlink or file → `unlink`, folder → `shutil.rmtree`), `prepare_workspace(task, workspace)` (reset, then write setup files with parent folders, UTF-8). A `# WARNING:` comment: this deletes everything in `workspace/`.
- **Why:** tasks must not affect each other (architecture 2.7); file tasks need their input files (P4).
- **Check:** script in a temp folder: nested files and folders removed, `.gitkeep` kept, symlinks to an outside file and folder removed with their targets intact, setup files present. `ruff check`.
- **Done when:** all of AC15–AC17 hold in the script.
- **Covers:** AC15, AC16, AC17

### Step 6: Pass setup: pass id, fingerprint, manifest, refusal (runner, part 2)
- **Files:** `benchmark/runner.py`
- **What:**
  - `PASS_ID_PATTERN`, `PassRefused`, `_check_pass_id`.
  - `code_fingerprint(root)`: sorted relative paths of `src/**/*.py`, `benchmark/*.py`, `benchmark/tasks.json`; hash path and bytes of each into one SHA-256; `"sha256:<hex>"`.
  - `_git_info(root) -> (commit, dirty)` with `subprocess.run(["git", ...], timeout=10)`, `"unknown"` / `False` on any failure.
  - `_build_manifest(...)` (keys of spec section 4, `config = settings.model_dump() | {"max_plan_steps": MAX_PLAN_STEPS}`, the same shape as `run_end.config`), `_write_manifest`, `_read_manifest`.
  - `_compare_manifest(manifest, current) -> list[str]`: names of the differing fields (`model`, `config.<key>`, `code_fingerprint`, `task_ids`), never their values.
  - `_read_results(path)`: one dict per line; a line that is not valid JSON raises `PassRefused("results.jsonl line N is broken ...")`.
  - `_select_tasks(tasks, task_ids)`: file order; an unknown id raises `PassRefused`.
- **Why:** one pass must never mix models, settings, tasks or code (architecture 5); the pass id becomes a folder name, so it is checked first.
- **Check:** script: manifest written and read back; every mismatch named; bad ids and unknown `--tasks` refused with no folder created; fingerprint stable, changed by a `.py` or `tasks.json` edit, unchanged by a `docs/` edit (on a copy in a temp folder). `ruff check`.
- **Done when:** all cases behave as AC22, AC28, AC29, AC30 say.
- **Covers:** AC18 (manifest part), AC22, AC28, AC29, AC30

### Step 7: The task loop: results, pause, crashes (runner, part 3)
- **Files:** `benchmark/runner.py`
- **What:**
  - `INFRA_ERROR_TYPES` (the spec 5.5 list) and `is_infra_error(error)` (type = text before the first `:`).
  - `_RunCollector`: an `on_record` callback that keeps the `run_end` record and the tool names from `tool_call` records (unique, first-use order); calls `print_record` when `verbose`.
  - `_result_line(task, state, run_end, tools_used, checks)`: keys of spec section 4; `success = status == "done" and all checks passed`; `final_answer` cut to 500.
  - `run_pass(...)` (spec 5.5 steps 1 to 7): new pass or resume, skip finished ids, for each task `prepare_workspace` → `run_fn(task.task, settings=settings, on_record=collector)` → pause on infra error (append `paused.jsonl`, return `"paused"`) → stop with `PassRefused` if the `run_end` model differs → checks → append and flush the result line → one progress line. Prints the workspace warning once at the start, and a warning when `git_dirty`.
  - `PassOutcome = Literal["complete", "paused"]`.
- **Why:** this is the measuring loop; the pause rule keeps a usage cap from counting as an agent failure (D12, D24).
- **Check:** script with a fake `run_fn` (scripted states, sends `tool_call` and `run_end` records): new 3-task pass; resume with 2 of 3 done; complete pass runs nothing; pause on task 2 then resume; a `KeyError` crash recorded and the pass continues; wrong `run_end` model stops; `failed` state with right answer gives `success=False, checks_passed=True`; the fake sees only the current task's setup files. `ruff check`.
- **Done when:** every case matches its AC.
- **Covers:** AC18, AC19, AC20, AC21, AC23, AC24, AC25, AC26, AC27

### Step 8: Summary and CLI (runner, part 4)
- **Files:** `benchmark/runner.py`
- **What:**
  - `summarize(results, *, planned=None, infra_pauses=0)`: the fields of spec 5.6.
  - `_print_summary(...)` with `rich`, using only the role colours from `src/console.py`; all text through `escape(sanitize(...))`.
  - `main(argv)`: `argparse` with `--pass` (required), `--tasks` (comma list), `--verbose`, `--summary`. Exit codes `0` / `2` / `3` / `130`: `ConfigError`, `TasksFileError` and `PassRefused` print a red message without a traceback; `KeyboardInterrupt` prints "interrupted, resume later". `--summary` on a missing pass returns 2.
  - `if __name__ == "__main__": raise SystemExit(main())`.
- **Why:** the summary holds the exact metric definitions of architecture 5, and `--summary` gives the numbers without spending quota.
- **Check:** script: `summarize` on the AC31 data; `main` with `run_pass` patched for complete, paused, refused, `ConfigError`; `--summary` on a temp pass. `grep -n "gpt-oss\|gemma\|environ\|getenv\|load_dotenv\|eval(\|exec(\|langchain_ollama" benchmark/*.py` finds nothing. `ruff check .` and IDE diagnostics for every `benchmark/*.py`.
- **Done when:** exit codes and summary numbers match, and the grep is empty.
- **Covers:** AC31, AC32, AC33, AC34

### Step 9: Docs sync
- **Files:** `docs/specs/architecture.md`, `docs/plans/decisions.md`, `CLAUDE.md`, `docs/project-memory.md`
- **What:**
  - `architecture.md` 2.7: the `checks` list with `file` on every check (4 types: `number`, `contains`, `regex`, `exact`), `setup_files`, `benchmark/tasks.py` and `benchmark/text.py`. Section 5: the success rule (`done` and all checks), `paused.jsonl`, the code fingerprint, the exact refusal fields, exit codes.
  - `decisions.md`, new "Session 6: benchmark": D32 a `checks` list per task; D33 success = `done` and all checks pass (`checks_passed` stored too); D34 the code fingerprint instead of the git commit; D35 every `ResponseError` and the connection/timeout types pause (a guess, check on the first real cap); D36 `MAX_REPLANS=1` in every pass (settles P3); D37 `benchmark/results/` is committed; D38 `text.py` split to avoid an import cycle; D39 a number check's value may not appear in the task text.
  - CLAUDE.md "Commands": replace "The benchmark command is added when it exists." with the two runner commands.
  - `docs/project-memory.md` "Terminal commands": the two runner commands, one line each.
- **Why:** design choices go to `decisions.md`, and the architecture must match the code (CLAUDE.md, workflow step 6).
- **Check:** read the changed sections once; `grep -n "benchmark.runner" CLAUDE.md docs/project-memory.md` finds both.
- **Done when:** the docs describe what was built.
- **Covers:** AC39

### Step 10: Real smoke pass `smoke-1`
- **Files:** `benchmark/results/smoke-1/` (new, committed), `docs/plans/progress.md`
- **What:**
  1. Gaurav sets `MAX_REPLANS=1` in `.env` and checks `OLLAMA_MODEL=gpt-oss:120b-cloud` (Claude never reads `.env`, Hard rule 7).
  2. Gaurav runs `python -m benchmark.runner --pass smoke-1 --tasks calc-01,search-01,file-02`.
  3. Claude checks the files by code: manifest model, 3 result lines, each `run_id`'s trace (model on every line, one `run_end` as the last line).
  4. Gaurav runs the same command again: it must say all tasks are done, add no line, and create no trace (compare the `logs/` file count before and after).
  5. Results (pass/fail per task, anything odd) are noted in `progress.md`; a failure is written down honestly, not fixed by editing expected answers (Hard rule 6). Problems go to session 7 unless they are runner bugs.
- **Why:** proves the runner works with the real model before session 7 spends quota on full passes.
- **Check:** the code checks in 3 and 4.
- **Done when:** AC37 and AC38 hold.
- **Covers:** AC37, AC38

## Risks
- **The real usage-cap error looks different from the guess** (D35). Fallback: it lands in a result line with its `error` instead of `paused.jsonl`; the summary's `crashed` count shows it, we add the type to `INFRA_ERROR_TYPES`, delete those lines by hand, and resume. Not testable before the first real cap.
- **`reset_workspace` deletes files Gaurav keeps in `workspace/`.** Fallback: none needed for the code (it is the sandbox and gitignored), but the runner prints a warning, and the plan says so here. Tests only ever reset `tmp_path` folders.
- **The fingerprint makes resumes strict.** Any fix in `src/` or `benchmark/` after a pass starts means a new pass id. This is intended (results before and after a fix are never mixed), but a typo fix in a comment also counts. Accepted.
- **A task checks badly against real model answers** (for example the model writes "1 989" or adds quotes in a file). Fallback: the smoke pass and session 7 traces show it; the checker or task wording may be improved in a new pass, never the expected answer.
- **Import cycle** between `tasks.py` and `checkers.py`. Avoided by `text.py` (step 1).
- **Quota:** the smoke pass is 3 tasks (about 10 to 30 LLM calls). The full passes wait for session 7.

## Definition of done
- [ ] Every acceptance criterion in the spec passes as a test (/test)
- [ ] /code-review has no Critical findings
- [ ] Diagnostics: 0 errors
- [ ] progress.md and project-memory.md updated
- [ ] Ready for /git-feature
