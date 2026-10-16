# Spec: Benchmark

**Status:** Approved
**Branch:** feature/benchmark
**Date:** 2026-10-16

## 1. Goal
Create a fixed 30-task benchmark (`benchmark/tasks.json`), code checkers that decide pass or fail without any LLM (`benchmark/checkers.py`), and a runner (`benchmark/runner.py`) that runs a whole pass through `run_task`, saves one result per task, refuses to mix models or settings, and pauses cleanly on a usage cap or connection error so the pass can be resumed later. Finish with one small real smoke pass of 3 tasks.

## 2. Why
Every CV number comes from here: task success rate per pass, failed-tool-call rate off vs on, and part of the 100+ traced runs. Session 7 runs the full passes; this session builds the measuring tool and proves it works on a small pass.

The numbers are only honest if:
- the expected answers are fixed before any measurement (Hard rule 6),
- the checker is code, so the same answer always gets the same result,
- one pass never mixes models, settings, tasks, or code versions,
- an Ollama usage cap is never counted as an agent failure (architecture section 5),
- tasks cannot affect each other (the workspace is reset before each task).

Architecture layer: 7 (Benchmark, `benchmark/`), on top of layer 6 (`run_task`). Nothing in `src/` changes.

## 3. Scope
**In scope:**
- `benchmark/__init__.py` (new, empty package marker).
- `benchmark/tasks.py` (new): Pydantic schema for a task and its checks, and `load_tasks()`, which validates the whole file.
- `benchmark/tasks.json` (new): the 30 tasks of section 5.3, with fixed expected answers.
- `benchmark/checkers.py` (new): number extraction, the 4 check types, and `run_checks()`.
- `benchmark/runner.py` (new): workspace reset, pass folders, manifest, resume, mismatch refusal, infrastructure-error pause, results, summary, and the CLI.
- Fixes for first-run problems P4 (file tasks create their input files before the task) and P5 (numbers with thousands separators are accepted).
- One real smoke pass (`smoke-2`, 3 tasks) with `gpt-oss:120b-cloud`.
- Docs: `docs/specs/architecture.md` (section 2.7 and 5: the `checks` list, the success rule, `paused.jsonl`, the code fingerprint), `docs/plans/decisions.md`, CLAUDE.md (benchmark command), `docs/project-memory.md` (benchmark command).
- Unit tests, fully mocked.

**Out of scope:**
- Full passes (main off, main on, second model), the 100+ runs, and the results analysis: session 7.
- Fixing first-run problems P2, P4 (stopping repeated identical failing calls), P6: session 7.
- Any LLM judge, ever (Hard rule 6).
- Running tasks in parallel, or locking a pass against two runners at once.
- Comparing passes with each other (session 7 and the UI dashboard in session 8). This session only summarises one pass.
- Saving a copy of the workspace after each task (the check details hold a short snippet instead).
- Any change in `src/`.

## 4. Inputs and outputs

```python
# benchmark/tasks.py
Category = Literal["calc", "search", "file", "multi-tool"]

class NumberCheck(BaseModel):    # type="number"
    type: Literal["number"]; file: str | None = None
    value: float; tolerance: float = 0.0          # tolerance >= 0
class ContainsCheck(BaseModel):  # type="contains"
    type: Literal["contains"]; file: str | None = None
    all: list[str] = []; any: list[str] = []; none: list[str] = []   # at least one non-empty
class RegexCheck(BaseModel):     # type="regex"
    type: Literal["regex"]; file: str | None = None
    pattern: str                                   # must compile
class ExactCheck(BaseModel):     # type="exact"
    type: Literal["exact"]; file: str | None = None
    text: str

Check = Annotated[NumberCheck | ContainsCheck | RegexCheck | ExactCheck, Field(discriminator="type")]

class BenchmarkTask(BaseModel):
    id: str                        # ^(calc|search|file|multi)-\d{2}$
    category: Category
    task: str                      # 1 to MAX_TASK_CHARS characters
    setup_files: dict[str, str] = {}   # path in workspace -> content, written before the task
    checks: list[Check]            # 1 to 3; the task passes only if every check passes

def load_tasks(path: Path = TASKS_PATH) -> list[BenchmarkTask]   # raises TasksFileError

# benchmark/checkers.py
class CheckResult(BaseModel):
    type: str; source: str         # "answer" or "file:<path>"
    passed: bool; detail: str      # detail cut to 200 chars

def extract_numbers(text: str) -> list[float]
def run_check(check: Check, answer: str, workspace: Path) -> CheckResult
def run_checks(task: BenchmarkTask, answer: str, workspace: Path) -> list[CheckResult]

# benchmark/runner.py
RunFn = Callable[..., AgentState]   # same signature as run_task

def reset_workspace(workspace: Path) -> None
def prepare_workspace(task: BenchmarkTask, workspace: Path) -> None
def code_fingerprint(root: Path = PROJECT_ROOT) -> str
def is_infra_error(error: str) -> bool
def run_pass(
    pass_id: str,
    *,
    task_ids: list[str] | None = None,
    settings: Settings | None = None,
    run_fn: RunFn = run_task,
    tasks_path: Path = TASKS_PATH,
    results_root: Path = RESULTS_DIR,
    workspace: Path = WORKSPACE_DIR,
    root: Path = PROJECT_ROOT,
    verbose: bool = False,
) -> PassOutcome                     # "complete" | "paused"; raises PassRefused
def summarize(results: list[dict[str, Any]]) -> dict[str, Any]
def main(argv: list[str] | None = None) -> int     # exit code
```

**CLI:**
```
python -m benchmark.runner --pass <pass_id> [--tasks calc-01,search-01] [--verbose]
python -m benchmark.runner --pass <pass_id> --summary
```
Exit codes: `0` pass complete (or summary printed), `2` refused (bad pass id, unknown task id, invalid tasks file, settings or code differ from the manifest, `ConfigError`), `3` paused (infrastructure error), `130` Ctrl-C.

**Pass folder** `benchmark/results/<pass_id>/`:

`manifest.json`, written once when the pass starts:
```json
{
  "schema_version": 1,
  "pass_id": "main-on-1",
  "model": "gpt-oss:120b-cloud",
  "config": {"ollama_model": "...", "enable_validation": true, "...": "...", "max_plan_steps": 6},
  "code_fingerprint": "sha256:…",
  "task_ids": ["calc-01", "..."],
  "git_commit": "e8125a3…",
  "git_dirty": false,
  "started_at": "2026-10-16T12:00:00.000Z"
}
```

`results.jsonl`, one line per finished task:
```json
{"task_id": "calc-01", "category": "calc", "run_id": "20261016-...", "model": "gpt-oss:120b-cloud",
 "success": true, "status": "done", "checks_passed": true,
 "checks": [{"type": "number", "source": "answer", "passed": true, "detail": "found 11626"}],
 "error": "", "final_answer": "…(cut to 500)", "tools_used": ["calculator"],
 "counters": {"plan_steps": 1, "...": 0}, "duration_ms": 8600, "finished_at": "…Z"}
```

`paused.jsonl`, one line per run that ended in an infrastructure error (no result written):
```json
{"task_id": "search-03", "run_id": "…", "error": "ResponseError: …", "at": "…Z"}
```

## 5. Design

### 5.1 `benchmark/tasks.py`: the task file and its validation
- `TASKS_PATH = PROJECT_ROOT / "benchmark" / "tasks.json"`. The file is a JSON list of task objects.
- **Why a list of `checks` instead of one `checker` + `expected`** (architecture 2.7): a multi-tool task often needs two facts checked, for example "the file exists and holds the right number". A list of small checks covers that without a special checker per task. The architecture names `file_content` as a checker; here every check type can read a file instead, through its `file` field (`None` = check the final answer). `architecture.md` is updated.
- `load_tasks()` reads the file and validates it with Pydantic. It raises `TasksFileError` (a message naming the task id and the problem) when:
  1. the JSON is invalid or a task does not match the schema (unknown field, unknown check type, wrong type);
  2. two tasks share an `id`, or the id prefix does not match the category (`multi-` for `multi-tool`);
  3. a `regex` pattern does not compile;
  4. a path in `setup_files` or a check's `file` is empty, absolute, contains a `..` part or a backslash, or is longer than 255 chars. **Why:** the runner writes and reads these paths, and they must stay inside `workspace/` (Hard rule 4), just like the file tools;
  5. a `contains` check has all three lists empty;
  6. **a `number` check's `value` appears in the task text** (any number extracted from the task text within the check's tolerance). **Why:** the number checker passes when any number in the answer matches; if the expected number were in the task, an answer that only repeats the task would pass.
- `MAX_CHECKS = 3`, `MAX_SETUP_FILE_CHARS = 10_000`.

### 5.2 `benchmark/checkers.py`: the checks
All checks are plain functions with no network, no LLM, and no state. The same inputs always give the same result.

**Source of the text.** `file=None`: the run's `final_answer`. Otherwise the file `workspace/<file>`, read as UTF-8 (at most 100 000 bytes, the file tools' limit). A missing, too large, or unreadable file makes the check fail with detail `file not found: <file>` (or `file too large` / `file unreadable`); it never raises. The resolved path must stay inside the workspace, otherwise the check fails.

**`extract_numbers(text)`** finds every number in the text, in order:
- integers and decimals: `42`, `3.5`, `0.25`;
- thousands separators in groups of exactly 3 digits, with `,`, no-break space (U+00A0), narrow no-break space (U+202F) or thin space (U+2009): `29,031.69` → 29031.69, `1,048,576` → 1048576 (first-run problem P5);
- scientific notation: `1.798754748e10` → 17987547480;
- a leading `-` counts as a sign only when it does not follow a letter or digit: `-5 degrees` → -5, but `2026-10-16` → 2026, 10, 16;
- `1,2,3` (groups not of 3 digits) → 1, 2, 3.
- Not handled (documented limits): decimal commas (`8,45`), the Unicode minus `−`, `×10^`, numbers written in words.

**The 4 check types:**

| Type | Passes when | Detail on failure |
|---|---|---|
| `number` | at least one number from `extract_numbers(text)` satisfies `abs(n - value) <= tolerance + 1e-9 * max(1, abs(value))` | `no number within <tol> of <value>; found: <first 5 numbers>` |
| `contains` | after normalising text and keywords, every `all` keyword is present, at least one `any` keyword is present (if `any` is not empty), and no `none` keyword is present | the first missing or forbidden keyword |
| `regex` | `re.search(pattern, text, re.IGNORECASE \| re.MULTILINE)` finds a match | `pattern not found` |
| `exact` | the normalised text equals the normalised `text` (case-sensitive) | `expected <text>, got <snippet>` |

- **Normalising for `contains`:** case-folded, accents removed (Unicode NFKD, combining marks dropped, so "García Márquez" matches "Garcia Marquez"), runs of whitespace collapsed to one space. A keyword matches only as a whole word or phrase: no letter or digit directly before or after it. **Why:** "art" must not match "Mozart".
- **Normalising for `exact`:** `\r\n` and `\r` become `\n`, trailing spaces on each line are removed, and leading and trailing blank lines are removed. **Why:** a file that ends with a newline holds the same text; anything else (quotes added, capitals changed) is a real difference.
- `detail` is cut to 200 chars and never contains more than a 100-char snippet of the text.
- `run_checks` runs every check of the task (it does not stop at the first failure, so the result line shows all of them).

**Success rule** (defined now, before measuring): a task is a success when **the run's status is `done` and every check passes**. `checks_passed` is stored too, so a run that failed but still holds the right answer is visible. **Why:** a `failed` run's final answer is "Task failed: ..." plus a partial result; the agent itself gave up, so counting it would make the agent look better than it is.

### 5.3 The 30 tasks
8 calc, 7 search, 7 file, 8 multi-tool. Search tasks use facts that do not change. Every expected value below was worked out by hand when writing this spec; tests check it again (section 8).

**calc** (the calculator should be used; nothing is required)

| id | Task | Checks |
|---|---|---|
| calc-01 | Use the calculator: what is 347 * 29 + 1563? | number 11626 |
| calc-02 | What is the square root of 7056? Use the calculator. | number 84 |
| calc-03 | Use the calculator to compute (2 ** 16 - 1) / 5. | number 13107 |
| calc-04 | An investment of 5000 grows by 4% per year, compounded yearly. What is it worth after 10 years? Use the calculator and round to 2 decimal places. | number 7401.22, tolerance 0.01 |
| calc-05 | What is 15% of 2480? Use the calculator. | number 372 |
| calc-06 | Use the calculator: what is 1234567 modulo 97? | number 48 |
| calc-07 | Use the calculator: what is the natural logarithm of 1000, rounded to 4 decimal places? | number 6.9078, tolerance 0.0001 |
| calc-08 | A rectangle is 12.5 m long and 7.2 m wide. Use the calculator to find its area in square metres. | number 90 |

**search**

| id | Task | Checks |
|---|---|---|
| search-01 | Search the web: in what year did the Berlin Wall fall? | number 1989 |
| search-02 | Search the web: what is the atomic number of gold? | number 79 |
| search-03 | Search the web: who painted the ceiling of the Sistine Chapel? | contains all ["Michelangelo"] |
| search-04 | Search the web: what is the capital city of Australia? | contains all ["Canberra"] |
| search-05 | Search the web: in what year was version 0.9.0 of the Python programming language first released? | number 1991 |
| search-06 | Search the web: what is the speed of light in a vacuum, in metres per second? | number 299792458 |
| search-07 | Search the web: who wrote the novel One Hundred Years of Solitude? | contains all ["Marquez"] |

**file** (`setup_files` are written into the empty workspace before the run; first-run problem P4)

| id | Setup files | Task | Checks |
|---|---|---|---|
| file-01 | none | Write the text 'benchmark ok' to a file called status.txt. | exact `benchmark ok` in `status.txt` |
| file-02 | `notes.txt`: `copper\nviolet\nharbor\n` | Read the file notes.txt and tell me what its third line says. | contains all ["harbor"] |
| file-03 | `items.txt`: `pen\nbook\nlamp\n\ncup\nkey\nmap\nbox\n` | How many non-empty lines does the file items.txt have? | number 7 |
| file-04 | `name.txt`: `ada lovelace\n` | Read name.txt and write its contents in uppercase to a file called upper.txt. | exact `ADA LOVELACE` in `upper.txt` |
| file-05 | none | Create a file called shopping.txt with three lines: eggs, milk, bread (one item per line, in this order, nothing else). | exact `eggs\nmilk\nbread` in `shopping.txt` |
| file-06 | `config.ini`: `[server]\nport = 8443\nhost = example.local\n` | Read config.ini and tell me which port the server uses. | number 8443 |
| file-07 | none | Write the text 'nested ok' to the file reports/today.txt. | exact `nested ok` in `reports/today.txt` |

**multi-tool**

| id | Setup files | Task | Checks |
|---|---|---|---|
| multi-01 | none | Search the web for the year the Eiffel Tower was completed, use the calculator to find how many years passed between then and 2000, and write only that number to eiffel.txt. | number 111 in `eiffel.txt` |
| multi-02 | `scores.txt`: `12\n45\n33\n8\n27\n` | Read scores.txt, use the calculator to find the average of the numbers in it, and tell me the result. | number 25 |
| multi-03 | none | Use the calculator to compute 2 ** 20 and write the result to power.txt. | number 1048576 in `power.txt` |
| multi-04 | none | Search the web for the atomic number of carbon and the atomic number of oxygen, then use the calculator to multiply the two numbers. | number 48 |
| multi-05 | `prices.txt`: `apple 1.20\nbread 2.50\ncheese 4.75\n` | Read prices.txt, use the calculator to add up the prices, and write the total to total.txt. | number 8.45, tolerance 0.001, in `total.txt` |
| multi-06 | none | Search the web for the name of the first person to walk on the Moon, and write only their full name to moon.txt. | contains all ["Neil", "Armstrong"] in `moon.txt` |
| multi-07 | none | Search the web for the speed of light in metres per second, use the calculator to find how many metres light travels in 60 seconds, and write the result to light.txt. | number 17987547480 in `light.txt` |
| multi-08 | `country.txt`: `Japan\n` | Read country.txt, search the web for the capital city of the country named in it, and write the capital to capital.txt. | contains all ["Tokyo"] in `capital.txt` |

Known weak spots, accepted on purpose and listed for session 7: the model knows most search facts without searching (so `tools_used` is recorded, but success does not require a tool); a `number` check passes if *any* number matches, so a long answer listing many numbers could pass by chance (rule 5.1.6 removes the easiest case); the final answer is the last approved step's result, so a plan whose last step only says "file written" fails an answer check.

### 5.4 `benchmark/runner.py`: the workspace
- `reset_workspace(workspace)`: deletes every entry inside `workspace/` except `.gitkeep`. A symlink (to a file or a folder) is removed with `unlink` and its target is never touched; a real folder is removed with `shutil.rmtree`; a file with `unlink`. Creates the folder if it is missing.
- `prepare_workspace(task, workspace)`: `reset_workspace`, then writes each `setup_files` entry (UTF-8, parent folders created).
- Called before every task. The checks run right after the task, before the next reset.
- **Why:** a file left by one task (for example `total.txt`) must never make another task pass or fail (architecture 2.7).
- **WARNING:** this deletes everything in `workspace/`, including files Gaurav put there by hand (for example the `notes.txt` from session 5). The runner prints one line saying so when a pass starts.

### 5.5 `benchmark/runner.py`: a pass
1. **Check the pass id**: `^[a-z0-9][a-z0-9-]{0,39}$`. **Why:** it becomes a folder name; this blocks `..`, `/` and other surprises. Otherwise refuse (exit 2).
2. **Load** settings (`get_settings()` unless given; a `ConfigError` exits 2) and tasks (`load_tasks()`; a `TasksFileError` exits 2). Compute `code_fingerprint()`.
3. **New pass** (no folder yet): choose the tasks (all 30, or the `--tasks` list in file order; an unknown id refuses), write `manifest.json`, create an empty `results.jsonl`.
4. **Existing pass** (resume): read the manifest and **refuse** (exit 2, naming every field that differs, never printing secret values) if any of these differ from now: `model`, `config` (every key), `code_fingerprint`, or `task_ids` (when `--tasks` is given and differs; without `--tasks` the manifest's list is used). `git_commit` and `git_dirty` are information only.
5. **Skip** every task id already in `results.jsonl`.
6. **For each remaining task**, in file order:
   1. `prepare_workspace(task, workspace)`.
   2. `state = run_fn(task.task, settings=settings, on_record=collect)`. `collect` keeps the `run_end` record and the names of the tools in `tool_call` records; with `--verbose` it also calls `print_record`.
   3. If `is_infra_error(state["error"])`: append a line to `paused.jsonl` (error text redacted, see D40), print `paused at <id> (pause N of 3 for this task): <error>. Resume later with: python -m benchmark.runner --pass <pass_id>`, and stop with outcome `paused` (exit 3). No result is written, so the task runs again on resume. **Exception (added after the code review):** on the third pause for the same task and error type (`MAX_TASK_PAUSES`), the run is recorded as a normal failed result with its `error` kept, so a deterministic server error cannot block a pass forever.
   4. If the `run_end` model differs from the manifest model: stop with an error (exit 2). **Why:** this should never happen; if it does, the pass would be mixed.
   5. Otherwise run the checks, build the result line (4, section 4), append it to `results.jsonl` and flush. Print one line: `[3/30] calc-03 PASS 8.6 s` (or `FAIL`, with the first failed check's detail).
7. When no task is left: print the summary, outcome `complete` (exit 0).

**Results are written one line per task, appended and flushed.** **Why:** a crash or Ctrl-C keeps every finished task, and resume continues from there.

**`code_fingerprint(root)`**: SHA-256 over the sorted relative paths and contents of `src/**/*.py`, `benchmark/*.py` and `benchmark/tasks.json`, written `sha256:<hex>`. **Why:** session 7 fixes code between passes, and results from before and after a fix must never be mixed (roadmap 7). The git commit alone is not enough: a docs-only commit would block a resume, and uncommitted code changes would not be seen.

**`is_infra_error(error)`**: `True` when the type before the first `:` is one of:
- Python: `ConnectionError`, `ConnectionRefusedError`, `ConnectionResetError`, `ConnectionAbortedError`, `TimeoutError`;
- httpx (used by the Ollama client): `ConnectError`, `ConnectTimeout`, `ReadTimeout`, `WriteTimeout`, `PoolTimeout`, `ReadError`, `WriteError`, `RemoteProtocolError`, `NetworkError`;
- Ollama: `ResponseError` (every one: usage cap, rate limit, not signed in, model not found, server error). **Why every one:** the agents never cause a `ResponseError`; it is always the server refusing or failing. **Risk:** a guess from reading the client code, like D6. The first real usage cap will show the real text; it goes into `paused.jsonl`, and the list is adjusted if needed.

Anything else (`""`, `GraphRecursionError`, `ValueError`, a bug) is **not** infrastructure: the task gets a normal result line with `success=false` and the `error` kept, so it is visible in the results.

### 5.6 `summarize(results)` and `--summary`
`--summary` reads `results.jsonl` and prints the summary without running anything (no LLM calls). The same summary is printed when a pass completes.
- `tasks`: results / tasks in the manifest; `successes`; `success_rate` = successes ÷ results.
- `by_category`: successes and results per category.
- `tool_calls`, `failed_tool_calls`, `failed_tool_call_rate` = failed ÷ calls (`None` when there were no calls), summed from the counters.
- `infra_pauses`: lines in `paused.jsonl`.
- `crashed`: results with a non-empty `error`.
These are the exact metric definitions of architecture section 5; session 7 compares passes using them.

### 5.7 The smoke pass
Gaurav sets `.env` (Claude never reads it, Hard rule 7) and runs:
```
python -m benchmark.runner --pass smoke-2 --tasks calc-01,search-01,file-02
```
Done when it completes with 3 result lines, a manifest with `model == "gpt-oss:120b-cloud"`, and 3 traces in `logs/`. A task that fails is fine and is written down. Then Gaurav runs it once more, to see that a finished pass skips all 3 tasks and makes no LLM call.

## 6. Acceptance criteria

**Task file** (`tests/benchmark/test_tasks.py`)
- [x] AC1: `load_tasks()` on the real `benchmark/tasks.json` returns 30 tasks with unique ids: 8 `calc`, 7 `search`, 7 `file`, 8 `multi-tool`.
- [x] AC2: Every real task has 1 to 3 checks, every id matches `^(calc|search|file|multi)-\d{2}$` with the prefix matching its category, and the ids, task texts, setup files and checks equal the table in section 5.3 (the test holds its own copy of the expected values).
- [x] AC3: `load_tasks` raises `TasksFileError` (message names the task id) for each of: invalid JSON, an unknown field, an unknown check type, a duplicate id, a prefix that does not match the category, a regex that does not compile, a `contains` check with all lists empty, more than 3 checks.
- [x] AC4: `load_tasks` raises `TasksFileError` for a setup or check path that is empty, absolute (`/etc/x`), has a `..` part (`a/../../x`), or has a backslash.
- [x] AC5: `load_tasks` raises `TasksFileError` for a `number` check whose value appears in the task text (task `What is 2 + 40?`, value `2`).
- [x] AC6: For every one of the 30 real tasks, a hand-written correct answer and files (held in the test) pass all checks, and a hand-written wrong answer fails at least one.

**Checkers** (`tests/benchmark/test_checkers.py`, files in `tmp_path`)
- [x] AC7: `extract_numbers` gives: `"29,031.69 ft"` → `[29031.69]`; `"1,048,576"` → `[1048576]`; `"1.798754748e10"` → `[17987547480]`; `"2026-10-16"` → `[2026, 10, 16]`; `"-5 degrees"` → `[-5]`; `"1,2,3"` → `[1, 2, 3]`; `"8 848 000"` with U+202F → contains `848000`; `"no numbers"` → `[]`.
- [x] AC8: `number` passes for `"The answer is 1,415."` with value 1415, passes for `7401.2214` with value 7401.22 and tolerance 0.01, fails for `7401.24` with the same check, and fails for `"no idea"`; the failure detail lists the numbers found.
- [x] AC9: `contains` is case-insensitive and accent-insensitive (`"Gabriel García Márquez"` passes all ["Marquez"]), matches whole words only (`"Mozart"` fails all ["art"]), passes `any` when one keyword is present, and fails when a `none` keyword is present.
- [x] AC10: `regex` passes when the pattern matches (case-insensitive) and fails otherwise.
- [x] AC11: `exact` passes for `"eggs\r\nmilk  \nbread\n\n"` against `"eggs\nmilk\nbread"`, and fails for `"'benchmark ok'"` against `"benchmark ok"` and for `"Benchmark ok"` (case-sensitive).
- [x] AC12: A check with `file` reads `workspace/<file>`; a missing file fails with detail starting `file not found`, a file over 100 000 bytes fails with `file too large`, and neither raises.
- [x] AC13: A symlink inside the workspace that points outside it fails the check (it is never read).
- [x] AC14: `run_checks` returns one `CheckResult` per check, in order, even after a failed one; `source` is `"answer"` or `"file:<path>"`; every `detail` is at most 200 chars.

**Workspace** (`tests/benchmark/test_runner.py`, `tmp_path` workspace)
- [x] AC15: `reset_workspace` removes files and nested folders, keeps `.gitkeep`, and creates a missing workspace folder.
- [x] AC16: `reset_workspace` removes a symlink to a file and a symlink to a folder that lie outside the workspace without touching their targets (target file and folder contents still exist).
- [x] AC17: `prepare_workspace` leaves exactly the task's setup files (with parent folders) and `.gitkeep`, with the exact contents.

**Runner** (fake `run_fn` that returns a scripted state and sends a `run_end` record; no LLM, no network)
- [x] AC18: A new pass over 3 tasks writes `manifest.json` with exactly the keys of section 4, `model == settings.ollama_model`, `config` equal to the `run_end` config shape (settings plus `max_plan_steps`), and `task_ids` equal to the 3 ids in file order; `results.jsonl` gets 3 lines with exactly the keys of section 4; outcome `complete`.
- [x] AC19: `success` is `True` only when `status == "done"` and every check passes; a `failed` state whose answer passes the checks gives `success=False` and `checks_passed=True`.
- [x] AC20: `prepare_workspace` runs before each task: the fake `run_fn` sees the task's setup files and no file from the previous task.
- [x] AC21: Resuming a pass with 2 of 3 results calls `run_fn` exactly once, for the missing task; resuming a complete pass calls it zero times.
- [x] AC22: Resume refuses (raises `PassRefused`, exit 2, no `run_fn` call, files unchanged) when the model differs, when any config value differs (`enable_validation`, `max_replans`), when `code_fingerprint` differs, and when `--tasks` differs from the manifest; the message names the differing field.
- [x] AC23: A state with `error = "ResponseError: you have reached your weekly usage limit"` on task 2 of 3: outcome `paused`, exit 3, `results.jsonl` has 1 line, `paused.jsonl` has 1 line with that task id and error, and task 3 is not run. A resume afterwards runs tasks 2 and 3.
- [x] AC24: `is_infra_error` is `True` for `"ConnectionError: down"`, `"ConnectError: x"`, `"ReadTimeout: x"`, `"ResponseError: x"`, `"TimeoutError: x"`, and `False` for `""`, `"GraphRecursionError: x"`, `"ValueError: task is empty"`, `"KeyError: 'plan'"`.
- [x] AC25: A non-infrastructure crash (`error = "KeyError: 'plan'"`) writes a result line with `success=False` and that `error`, and the pass continues to the next task.
- [x] AC26: A `run_end` record whose `model` differs from the manifest stops the pass with exit 2 and writes no result line for that task.
- [x] AC27: `tools_used` lists the tool names from the run's `tool_call` records, each once, in first-use order; `counters` equals the `run_end` counters; `final_answer` is cut to 500 chars.
- [x] AC28: Pass ids `"../x"`, `"a/b"`, `"Main"`, `""`, and a 41-char id are refused with exit 2 and no folder is created; `"main-on-1"` is accepted.
- [x] AC29: An unknown id in `--tasks` refuses with exit 2 and creates no pass folder.
- [x] AC30: `code_fingerprint` is the same for the same files, changes when a `.py` file under `src/` or `benchmark/` or `tasks.json` changes, and does not change when a file under `docs/` changes (tested on a copy in `tmp_path`).

**Summary and CLI**
- [x] AC31: `summarize` on 4 scripted results (2 successes, 3 calc + 1 file, 10 tool calls of which 3 failed, 1 crash) gives `success_rate == 0.5`, the right `by_category`, `failed_tool_call_rate == 0.3`, `crashed == 1`; with 0 tool calls the rate is `None`.
- [x] AC32: `main(["--pass", "p1", "--summary"])` on an existing pass prints the summary, returns 0, and never calls `run_fn`; on a missing pass it returns 2.
- [x] AC33: `main` returns 0 for a complete pass, 3 for a paused one, 2 for a `ConfigError` (message printed, no traceback).

**Source rules**
- [x] AC34: `benchmark/*.py` contains no model name (`gpt-oss`, `gemma`), does not read the environment (`os.environ`, `getenv`, `load_dotenv`), does not use `eval(`/`exec(`, and imports nothing from `langchain_ollama` (test reads the source). **Why:** the checkers must be code only (Hard rule 6), and the model comes only from config (Hard rule 2).

**Project checks**
- [x] AC35: `pytest -q` passes with no network and no LLM calls; the session 2 to 5 tests pass unchanged.
- [x] AC36: `ruff check .` reports no errors.

**Real smoke pass** (not a unit test; checked by code over the files)
- [x] AC37: `benchmark/results/smoke-2/` has a manifest with `model == "gpt-oss:120b-cloud"` and 3 result lines; each `run_id` has a trace in `logs/` whose every line has that model and whose last line is the only `run_end`.
- [x] AC38: A second run of the same command prints that all tasks are done, adds no result line and creates no new trace.
- [x] AC39: CLAUDE.md "Commands" and `docs/project-memory.md` contain `python -m benchmark.runner --pass <pass_id>`.

## 7. Edge cases and errors
- **Usage cap, rate limit, Ollama down, network down:** `run_task` catches it (session 5); the runner sees an infrastructure error type, writes `paused.jsonl`, writes no result, exits 3. Resume runs the task again (AC23).
- **Ctrl-C during a task:** `KeyboardInterrupt` passes through `run_task`; the runner prints "interrupted, resume later" and exits 130. No result is written for that task; its trace has no `run_end`, so it is not counted.
- **A crash in our own code** (`KeyError`, `GraphRecursionError`): recorded as a failed task with its `error`, not a pause (AC25). If every task crashes the same way, the summary's `crashed` count shows it.
- **`.env` changed between sessions** (model, a flag, `MAX_REPLANS`): resume refuses and names the field (AC22). Start a new pass id instead.
- **Code or tasks changed after a pass started:** the fingerprint differs; resume refuses (AC22, AC30).
- **Uncommitted code at pass start:** allowed, but `git_dirty=true` goes in the manifest and a warning is printed. `git` missing or not a repo: `git_commit = "unknown"`.
- **A half-written last line in `results.jsonl`** (power cut during the write): the runner refuses (exit 2) and names the line number, instead of silently dropping or re-running it. Gaurav deletes the broken line by hand and resumes.
- **Two runners on the same pass at the same time:** not prevented (out of scope); the results would be mixed. Run one at a time.
- **The agent writes outside `workspace/` or creates a symlink:** the file tools block it (Hard rule 4); the checker also refuses to read a path that resolves outside (AC13).
- **The answer holds markdown** (`**1415**`): number extraction ignores the stars; `contains` ignores punctuation around words.
- **Number written with a decimal comma or in words** (`8,45`, "eighty-four"): fails. Accepted as a real failure; the task texts are in English and the model writes English numbers.
- **A search fact the web gets wrong:** the expected answers are standard, stable facts. If a real pass shows a source disagreeing, it goes in the session 7 analysis; the expected answer is never edited to make a task pass (Hard rule 6).
- **Secrets:** the manifest holds `settings.model_dump()`, the same keys as `run_end.config`, which a session 5 test fixes so no secret can enter. `.env` is never read by the benchmark code.

## 8. Test plan
All unit tests: no LLM, no internet (Hard rule 8). The runner gets a fake `run_fn` that returns a scripted `AgentState` and sends a scripted `run_end` (and `tool_call`) record to `on_record`. Workspaces, results folders and fingerprint roots live in `tmp_path`. Settings come from `load_settings({...})` with a dummy model name.

| Test file | Covers |
|---|---|
| `tests/benchmark/__init__.py` (new) | package marker |
| `tests/benchmark/test_tasks.py` (new) | AC1–AC6 (real `tasks.json`, plus small invalid files in `tmp_path`) |
| `tests/benchmark/test_checkers.py` (new) | AC7–AC14 |
| `tests/benchmark/test_runner.py` (new) | AC15–AC33, AC34 (source checks) |

AC35 and AC36: run `pytest -q` and `ruff check .`. AC37 to AC39 are checked after the smoke pass with a short check in the terminal and by reading the docs.

## 9. Open questions
Settled with the recommendations below when the spec was approved (`/create-plan`, 2026-10-16); recorded as decisions in the plan's docs step. Question 2 needs Gaurav to set `.env` before the smoke pass.

1. **Success needs `status == "done"` and all checks passing** (section 5.2). The alternative is "checks pass" only, which would count a run the agent gave up on. Both are stored, so either can be reported, but the headline number must be chosen now. Use "done and checks pass"?
2. **`MAX_REPLANS` (first-run problem P3).** Your `.env` has 2; the code default and D4 say 1. Every pass records it in the manifest and resume refuses a change, so the value must be fixed before the first real pass. Recommendation: set `MAX_REPLANS=1` in `.env` before the smoke pass (fewer LLM calls per task under the usage caps). Which value?
3. **Commit `benchmark/results/` to git?** Recommendation: yes. Manifests and results are small and are the evidence behind the CV numbers; traces in `logs/` stay ignored. The risk is that final answers hold web text, which is public anyway.
4. **A list of `checks` per task instead of one `checker` + `expected`**, and `file` on every check instead of a separate `file_content` checker (section 5.1). This changes architecture section 2.7. OK?
5. **Every Ollama `ResponseError` pauses the pass** (section 5.5). Simple and safe for the metrics, but a non-quota server error would also pause instead of being counted. OK for now, and adjust after the first real cap?
