# Implementation plan: Foundation, tools, and validation gate

**Spec:** docs/specs/foundation-tools-validation.md
**Branch:** feature/foundation-tools-validation
**Status:** Approved

## Progress
- [x] Step 1: Project scaffolding
- [x] Step 2: Config (`src/config.py`)
- [x] Step 3: Console (`src/console.py`)
- [x] Step 4: Tool result base (`src/tools/base.py`)
- [x] Step 5: Calculator
- [x] Step 6: File tools
- [x] Step 7: Web search
- [x] Step 8: Tool registry
- [ ] Step 9: Validation gate (`src/validation.py`)
- [ ] Step 10: Docs sync and final checks

## Overview
Build bottom-up, following the layer order: scaffolding, then config and console (no dependencies), then the shared tool result type, then each tool on its own, then the registry that collects them, and finally the gate that uses the registry and config. Each step ends with a quick manual check (`python -c ...` and VS Code diagnostics). The full test suite is written afterwards with `/test`, from the spec's acceptance criteria.

The spec's open questions are settled with the spec's own choices (approving the spec approved them): gate retries use `TOOL_RETRY_ATTEMPTS`; `web_search` defaults to 3 results; transient failures are marked with the `"transient: "` prefix; `MAX_REPLANS` defaults to 1 (architecture), and `.env.example` is updated to match; `call_tool` has an `on_event` hook. Step 10 records these in `docs/plans/decisions.md`.

## Steps

### Step 1: Project scaffolding
- **Files:** `pyproject.toml` (new), `src/__init__.py`, `src/tools/__init__.py` (empty for now), `tests/__init__.py`, `tests/tools/__init__.py`, `workspace/.gitkeep`, `.gitignore`, `.env.example`
- **What:**
  - `pyproject.toml` with only tool settings: `[tool.pytest.ini_options] pythonpath = ["."]`, `testpaths = ["tests"]`; `[tool.ruff] target-version = "py313"`, line length 100, a small rule set (`E`, `F`, `I`, `B`, `UP`).
  - `.gitignore`: change `workspace/` to `workspace/*` plus `!workspace/.gitkeep`, so the folder exists in git but its contents do not.
  - `.env.example`: add `OLLAMA_TEMPERATURE`, `OLLAMA_TIMEOUT_S`, `MAX_TOOL_ITERATIONS`, `TOOL_RETRY_ATTEMPTS` with their defaults; set `MAX_REPLANS=1`. Placeholder values only. `.env` is never opened (Hard rule 7).
- **Why:** `from src.config import ...` must work inside pytest, and ruff needs a target version. Without `pythonpath`, pytest cannot import `src`.
- **Check:** `pytest -q` runs (0 tests collected, no import errors); `ruff check .` clean; `git status` shows `workspace/.gitkeep`.
- **Done when:** the three checks pass.
- **Covers:** groundwork for AC34, AC35

### Step 2: Config (`src/config.py`)
- **Files:** `src/config.py`
- **What:** `PROJECT_ROOT`, `WORKSPACE_DIR`, `ConfigError`, frozen `Settings` model with the 9 fields and bounds from spec 5.1, a bool parser (`true/false/1/0/yes/no`), `load_settings(env)`, cached `get_settings()` (`load_dotenv()` then `os.environ`), and `get_llm(settings)` returning `ChatOllama`. `ConfigError` messages are built from Pydantic error locations and types only, never from input values.
- **Why:** every later layer reads limits and flags from here; Hard rules 2, 5, 7.
- **Check:** `python -c "from src.config import load_settings; print(load_settings({'OLLAMA_MODEL': 'm'}))"` shows the defaults; `load_settings({})` and `load_settings({'OLLAMA_MODEL': 'm', 'MAX_RETRIES': '99'})` raise `ConfigError` without the value in the message. Diagnostics: 0 errors.
- **Done when:** the checks above behave as described.
- **Covers:** AC1, AC2, AC3, AC4, AC5, AC6

### Step 3: Console (`src/console.py`)
- **Files:** `src/console.py`
- **What:** the 7 colour constants and `console = Console()`.
- **Why:** one place for colours (colour-coding rule).
- **Check:** `python -c "from src.console import console, TOOL; console.print('tool', style=TOOL)"` prints in blue.
- **Done when:** import works, diagnostics clean.
- **Covers:** AC7

### Step 4: Tool result base (`src/tools/base.py`)
- **Files:** `src/tools/base.py`
- **What:** `ToolResult` TypedDict, `success(output)`, `failure(error)`, `TRANSIENT_PREFIX`, and `is_transient(result)`.
- **Why:** all four tools and the gate share one return format (Hard rule 1).
- **Check:** `python -c "from src.tools.base import success, failure; print(success('x'), failure('y'))"`.
- **Done when:** both helpers return exactly the 3 keys.
- **Covers:** groundwork for AC8, AC21, AC28–AC31

### Step 5: Calculator
- **Files:** `src/tools/calculator.py`
- **What:** `CalculatorArgs` schema; a recursive `ast` evaluator that allows only the nodes in spec 5.3; constants `pi, e, tau`; the function whitelist; the 200-char and exponent-100 limits; `inf`/`nan` rejection; readable error messages; the whole body inside `try/except Exception`.
- **Why:** the safe maths tool (Hard rule 3).
- **Check:** `python -c` with `2 + 3 * 4`, `sqrt(16)`, `__import__('os')`, `1/0`, `9 ** 999`: first two `ok` True, last three `ok` False. `grep -nE "eval\(|exec\(" src/tools/calculator.py` finds nothing (the `ast` mode string `"eval"` is not a call).
- **Done when:** the examples behave as in AC10–AC12.
- **Covers:** AC8 (calculator), AC10, AC11, AC12, AC13

### Step 6: File tools
- **Files:** `src/tools/files.py`
- **What:** `ReadFileArgs`, `WriteFileArgs`; `resolve_in_workspace(path)` (reject empty, null byte, absolute; `resolve()`; `is_relative_to` check), reading `config.WORKSPACE_DIR` at call time; `read_file` (exists, regular file, ≤ 100 000 bytes, UTF-8); `write_file` (≤ 100 000 chars, create parents, overwrite).
- **Why:** sandboxed file access (Hard rule 4).
- **Check:** in a temp folder (patch `WORKSPACE_DIR` in a `python -c` snippet): write then read `a/b.txt`; `../x.txt` and `/etc/passwd` rejected; a symlink to `/tmp` rejected.
- **Done when:** the check matches AC14–AC18 and nothing is written outside the temp workspace.
- **Covers:** AC8 (file tools), AC14, AC15, AC16, AC17, AC18

### Step 7: Web search
- **Files:** `src/tools/web_search.py`
- **What:** `WebSearchArgs` (`query` 1–300 chars, `max_results` 1–10, default 3); `DDGS(timeout=10).text(...)`; result formatting with 300-char snippets and a 2000-char total; transient classification of `RatelimitException`, `TimeoutException`, `TimeoutError`, `ConnectionError`. First read the installed `ddgs` source (no network) to see what `text()` does on zero results, and handle that case as `"No results found."`.
- **Why:** the only network tool, and the main source of transient errors.
- **Check:** `python -c` with `DDGS` replaced by a fake class giving 3 long results, then one raising `TimeoutException`. No real search is run.
- **Done when:** output is trimmed correctly, and the timeout gives an error starting with `"transient: "`.
- **Covers:** AC8 (web_search), AC19, AC20, AC21, AC22

### Step 8: Tool registry
- **Files:** `src/tools/__init__.py`
- **What:** `TOOLS` dict with the 4 `(schema, function)` pairs, and the public imports.
- **Why:** the gate (and later the executor) finds tools by name here.
- **Check:** `python -c "from src.tools import TOOLS; print(sorted(TOOLS))"`.
- **Done when:** exactly the 4 names print.
- **Covers:** AC9

### Step 9: Validation gate (`src/validation.py`)
- **Files:** `src/validation.py`
- **What:** `call_tool(name, args, *, settings=None, sleep=time.sleep, on_event=None)` with the 5 steps of spec 5.4: unknown tool, Pydantic validation (only when `enable_validation`, formatted field errors, `validation_error` event), run in `try/except` (exceptions to errors, `TimeoutError`/`ConnectionError` transient, invalid return value caught), retry loop (only when `enable_retry`, `tool_retry_attempts` extra attempts, waits `min(2 ** (k - 1), 4)`, `retry` events), and one `tool_call` event with trimmed output/error, attempts, and `duration_ms`. A small `_emit` helper wraps `on_event` in `try/except`.
- **Why:** the single point every tool call passes through; the ablation flags live here.
- **Check:** `python -c` with a fake tool that fails transiently twice and then succeeds, a fake `sleep` that records delays, and a list as `on_event`: result `ok` True, delays `[1, 2]`, events `retry, retry, tool_call`. Then `call_tool("nope", {})` returns the unknown-tool error.
- **Done when:** the check matches AC28, AC23.
- **Covers:** AC23, AC24, AC25, AC26, AC27, AC28, AC29, AC30, AC31, AC32, AC33

### Step 10: Docs sync and final checks
- **Files:** `docs/specs/architecture.md`, `docs/plans/decisions.md` (new), `docs/plans/progress.md`
- **What:** update the architecture: `web_search` default 3, the `OLLAMA_TIMEOUT_S` row in the config table, the transient prefix and `on_event` in section 2.3. Write `decisions.md` entries for the 5 settled open questions. Run `ruff check . --fix && ruff format .` and `pytest -q`.
- **Why:** the architecture must match the code (its own rule at the top), and decisions must be recorded (workflow step 6).
- **Check:** `ruff check .` clean; `pytest -q` passes (the real tests come from `/test`).
- **Done when:** docs match the code; 0 diagnostics errors in `src/`.
- **Covers:** AC34, AC35

## Risks
- **ddgs on empty results:** some versions raise `DDGSException("No results found")` instead of returning `[]`. Step 7 reads the installed source first. If it is unclear, handle both, and test both with a fake.
- **`ConfigError` leaking values:** Pydantic's default error text often includes the input (`input_value=...`). Fallback: build messages only from `loc` and `type`/`ctx` (bounds), never from `str(exc)`. AC3 tests this.
- **Path checks on macOS:** `/tmp` is a symlink to `/private/tmp`, and `tmp_path` lives under it. Always compare `resolve()`d paths on both sides, or real paths inside the workspace may be wrongly rejected.
- **Calculator resource limits:** the exponent cap blocks `9 ** 9 ** 9`, but chains of large multiplications could still build big integers. The 200-char limit bounds this; if a test shows it is slow, add a cap on integer size (e.g. 10 000 digits).
- **pytest picking up the real `.env`:** `get_settings()` reads `.env`. Tests must use `load_settings({...})` or clear the `get_settings` cache with `monkeypatch`, and never print settings.

## Definition of done
- [ ] Every acceptance criterion in the spec passes as a test (/test)
- [ ] /code-review has no Critical findings
- [ ] Diagnostics: 0 errors
- [ ] progress.md and project-memory.md updated
- [ ] Ready for /git-feature
