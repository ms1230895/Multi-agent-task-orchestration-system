# Spec: Foundation, tools, and validation gate

**Status:** Approved
**Branch:** feature/foundation-tools-validation
**Date:** 2026-10-16

## 1. Goal
Build the bottom layers of the system: typed config loaded from `.env`, the `rich` colour constants, the four tools with Pydantic input schemas, the tool registry, and the validation gate `call_tool` that checks, runs, and retries every tool call.

## 2. Why
Every layer above (agents, graph, benchmark) calls tools and reads config. If tools can raise, or bad arguments reach a tool unchecked, a run can crash or hang. This feature makes tool calls safe and predictable, and it builds the two ablation switches (`ENABLE_VALIDATION`, `ENABLE_RETRY`) that the "failed tool calls" metric depends on.

Architecture layers (`docs/specs/architecture.md` section 2): layer 1 (LLM factory in `src/config.py`), layer 2 (tools), layer 3 (reliability / validation gate), plus the cross-cutting config and console pieces.

## 3. Scope
**In scope:**
- `src/config.py`: load `.env`, validate settings with Pydantic, safe defaults for limits and flags, `get_settings()`, `get_llm()`.
- `src/console.py`: one `rich` colour constant per role, plus a shared `Console`.
- `src/tools/`: `calculator`, `read_file`, `write_file`, `web_search`, each with a Pydantic input schema and returning `{ok, output, error}`.
- `src/tools/__init__.py`: registry `TOOLS` mapping tool name to `(schema, function)`.
- `src/validation.py`: `call_tool(name, args)`: tool exists → arguments valid → run in `try/except` → retry transient failures.
- `.env.example`: add the new variables with their defaults (no real values, no secrets).
- `workspace/` folder kept in git with a `.gitkeep` (contents ignored).
- Unit tests in `tests/` for all of the above, fully mocked.

**Out of scope:**
- The retry where the LLM fixes its own bad tool call. That is the executor's inner loop (session 4).
- LangChain tool definitions for `llm.bind_tools(...)` (session 4).
- The trace logger `src/logger.py` (session 5). This feature only provides an `on_event` hook that session 5 connects to the logger.
- `AgentState`, agents, graph, router (sessions 3 and 4).
- Any real LLM or internet call.

## 4. Inputs and outputs

**Tool result** (every tool, and `call_tool`):
```python
class ToolResult(TypedDict):
    ok: bool
    output: str | None   # set when ok is True
    error: str | None    # set when ok is False
```

**Tools:**

| Tool | Input schema (Pydantic) | Output on success |
|---|---|---|
| `calculator` | `expression: str` (1–200 chars) | the result as a string, e.g. `"42"`, `"1.4142135623730951"` |
| `read_file` | `path: str` (1–255 chars, relative to `workspace/`) | the file's text |
| `write_file` | `path: str` (1–255 chars), `content: str` (max 100 000 chars) | `"wrote <N> chars to <path>"` |
| `web_search` | `query: str` (1–300 chars), `max_results: int = 3` (1–10) | numbered results: title, URL, trimmed snippet |

All schemas use `extra="forbid"`, so an unknown argument name is a validation error.

**Gate:**
```python
def call_tool(
    name: str,
    args: dict[str, Any],
    *,
    settings: Settings | None = None,           # default: get_settings()
    sleep: Callable[[float], None] = time.sleep,  # tests pass a fake
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> ToolResult
```

**Config:** `get_settings() -> Settings` (cached), `load_settings(env: Mapping[str, str]) -> Settings`, `get_llm(settings: Settings | None = None) -> ChatOllama`.

## 5. Design

### 5.1 `src/config.py`
- Uses `python-dotenv` (installed) plus a Pydantic `BaseModel`. No new dependency (`pydantic-settings` is not installed and not needed).
- `Settings` (frozen Pydantic model):

| Field | Env variable | Default | Allowed |
|---|---|---|---|
| `ollama_model` | `OLLAMA_MODEL` | **required** | non-empty string |
| `ollama_temperature` | `OLLAMA_TEMPERATURE` | `0.0` | 0.0–2.0 |
| `ollama_timeout_s` | `OLLAMA_TIMEOUT_S` | `120` | 1–600 |
| `enable_validation` | `ENABLE_VALIDATION` | `True` | bool |
| `enable_retry` | `ENABLE_RETRY` | `True` | bool |
| `max_retries` | `MAX_RETRIES` | `2` | 0–5 |
| `max_replans` | `MAX_REPLANS` | `1` | 0–3 |
| `max_tool_iterations` | `MAX_TOOL_ITERATIONS` | `5` | 1–10 |
| `tool_retry_attempts` | `TOOL_RETRY_ATTEMPTS` | `2` | 0–5 |

- Booleans accept `true/false/1/0/yes/no` (any case). Anything else is an error.
- `load_settings(env)` builds `Settings` from a mapping. On a missing or invalid value it raises `ConfigError` with a message that names the variable and the rule (for example `"OLLAMA_MODEL is required"`, `"MAX_RETRIES must be between 0 and 5"`). The message never contains the value itself (Hard rule 7).
- `get_settings()` calls `load_dotenv()` once, then `load_settings(os.environ)`, and caches the result (`functools.lru_cache`). Nothing else in the project reads `.env` or `os.environ`.
- `get_llm(settings)` returns `ChatOllama(model=settings.ollama_model, temperature=settings.ollama_temperature, ...)` with the timeout. It only builds the client; it makes no call.
- Path constants: `PROJECT_ROOT`, `WORKSPACE_DIR = PROJECT_ROOT / "workspace"`.
- The upper bounds on the limits are part of Hard rule 5: a typo like `MAX_RETRIES=200` is refused instead of running for hours.

### 5.2 `src/console.py`
Constants from the colour-coding rule, defined once:
```python
PLANNER = "magenta"; EXECUTOR = "cyan"; CRITIC = "yellow"; TOOL = "blue"
RETRY = "dark_orange"; SUCCESS = "green"; ERROR = "red"
console = Console()
```
Display only. No other module hard-codes a colour.

### 5.3 `src/tools/`
- `base.py`: `ToolResult`, helpers `success(output) -> ToolResult` and `failure(error) -> ToolResult`, and `TRANSIENT_PREFIX = "transient: "`. A tool marks a failure as worth retrying by starting its error with this prefix. This keeps the return format exactly `{ok, output, error}`.
- Every tool function takes keyword arguments that match its schema (e.g. `calculator(expression: str)`), wraps its whole body in `try/except Exception`, and never raises (Hard rule 1). The gate's own `try/except` is a second safety net.

**`calculator.py`** (Hard rule 3: `ast` only, never `eval`/`exec`)
- Parse with `ast.parse(expression, mode="eval")` and walk the tree. Allowed nodes only:
  - numbers (`int`, `float`; not `bool`, `str`, `complex`)
  - `+ - * / // % **`, unary `-` and `+`, parentheses
  - names `pi`, `e`, `tau`
  - calls to whitelisted functions by plain name, positional arguments only: `sqrt, log, log10, log2, exp, sin, cos, tan, asin, acos, atan, floor, ceil, abs, round, min, max`
- Anything else (attributes, subscripts, other names, lambdas, comprehensions, strings, keyword arguments) → `failure("not allowed: <NodeType>")`.
- Limits: expression ≤ 200 chars; exponent `abs(exp) ≤ 100`; a result that is `inf` or `nan` is an error.
- Readable errors: `invalid expression`, `division by zero`, `math error: <msg>`, `result too large`.

**`files.py`** (`read_file`, `write_file`; Hard rule 4)
- `resolve_in_workspace(path) -> Path`:
  1. reject an empty path, a path containing a null byte, or an absolute path;
  2. `target = (WORKSPACE_DIR / path).resolve()`;
  3. reject unless `target.is_relative_to(WORKSPACE_DIR.resolve())`.
  `resolve()` follows symlinks, so a symlink that leads outside `workspace/` is rejected.
- `WORKSPACE_DIR` is read from `src.config` at call time, so tests can point it at a `tmp_path`.
- `read_file`: must be an existing regular file, size ≤ 100 000 bytes, UTF-8 text.
- `write_file`: content ≤ 100 000 chars, creates parent folders, overwrites an existing file, writes UTF-8.

**`web_search.py`**
- `DDGS(timeout=10).text(query, max_results=max_results)`.
- Output: one block per result, `"<n>. <title>\n   <url>\n   <snippet>"`. Each snippet is trimmed to 300 chars (adding `…`), and the whole output to 2000 chars.
- No results → `success("No results found.")`. An empty search is not a tool failure.
- `RatelimitException`, `TimeoutException`, `TimeoutError`, `ConnectionError` → `failure(TRANSIENT_PREFIX + "<Type>: <msg>")`.
- Any other exception → `failure("<Type>: <msg>")` (not transient).

**`__init__.py`**: the registry.
```python
TOOLS: dict[str, tuple[type[BaseModel], Callable[..., ToolResult]]] = {
    "calculator": (CalculatorArgs, calculator),
    "read_file": (ReadFileArgs, read_file),
    "write_file": (WriteFileArgs, write_file),
    "web_search": (WebSearchArgs, web_search),
}
```

### 5.4 `src/validation.py`: `call_tool`
Steps, in this order:
1. **Tool exists?** If `name` is not in `TOOLS` → `failure("unknown tool: <name>. Available: calculator, read_file, web_search, write_file")`. Never retried.
2. **Arguments valid?** Only when `enable_validation` is on. `schema.model_validate(args)`. On failure → `failure("invalid arguments for <name>: <field>: <problem>; ...")`, emit `validation_error` (`tool`, `args`, `errors`). Never retried: the same bad arguments would fail again. On success the tool is called with `**model.model_dump()`.
   When validation is off, the raw `args` go straight to the function (`fn(**args)`). Bad arguments then fail inside the call and are caught by step 3. This is the "off" side of the ablation.
3. **Run** inside `try/except Exception`. An exception becomes `failure("<Type>: <msg>")`; `TimeoutError` and `ConnectionError` get the `TRANSIENT_PREFIX`. A return value that is not a valid `ToolResult` becomes `failure("tool returned an invalid result")`.
4. **Retry** only when `enable_retry` is on and the error starts with `TRANSIENT_PREFIX`. Up to `tool_retry_attempts` extra attempts. Before extra attempt `k` (1, 2, …) wait `min(2 ** (k - 1), 4)` seconds through `sleep` (1 s, 2 s, then 4 s). Emit `retry` (`kind="tool"`, `attempt`, `reason`) before each one. Stop as soon as an attempt succeeds or gives a non-transient error.
5. Emit `tool_call` (`tool`, `args`, `ok`, `output` or `error` trimmed to 500 chars, `attempts`, `duration_ms`) and return the last result.

- `on_event` is optional. If it raises, `call_tool` ignores that error and still returns the tool result.
- The retry counter here is `TOOL_RETRY_ATTEMPTS`, not `MAX_RETRIES`. `MAX_RETRIES` is the critic's per-step retry limit (architecture section 3). Keeping them separate means the ablation flag only changes the gate. See open question 1.

## 6. Acceptance criteria

**Config**
- [ ] AC1: `load_settings({"OLLAMA_MODEL": "m"})` returns all defaults from the table in 5.1.
- [ ] AC2: `load_settings({})` raises `ConfigError` whose message contains `OLLAMA_MODEL`.
- [ ] AC3: An out-of-range or non-numeric limit (`MAX_RETRIES=99`, `MAX_RETRIES=abc`) raises `ConfigError` naming the variable, and the message does not contain the bad value.
- [ ] AC4: `ENABLE_RETRY` parses `true/false/1/0/yes/no` in any case; `ENABLE_RETRY=maybe` raises `ConfigError`.
- [ ] AC5: `get_llm(settings)` builds a `ChatOllama` whose `model` equals `settings.ollama_model` (checked with a mocked `ChatOllama`, no network).
- [ ] AC6: No model name (`gpt-oss`, `gemma`) appears in any file under `src/` (test greps the source).

**Console**
- [ ] AC7: The 7 constants in `src/console.py` equal the colours in the colour-coding rule.

**Tools: general**
- [ ] AC8: For every tool, a call with bad input (wrong type, empty string, oversized value) returns a dict with exactly the keys `ok`, `output`, `error`, with `ok` False, and does not raise.
- [ ] AC9: The registry has exactly the 4 tool names, and each entry is a (Pydantic model class, callable) pair.

**Calculator**
- [ ] AC10: `2 + 3 * 4` → `"14"`; `(2 + 3) * 4` → `"20"`; `-5 + 2` → `"-3"`; `2 ** 10` → `"1024"`; `7 // 2` → `"3"`; `7 % 3` → `"1"`; `sqrt(16)` → `"4.0"`; `round(pi, 2)` → `"3.14"`.
- [ ] AC11: These are rejected with `ok` False: `__import__('os')`, `().__class__`, `open('x')`, `x`, `'a' * 3`, `[1, 2]`, `lambda: 1`, `round(2.5, ndigits=1)`.
- [ ] AC12: `1 / 0` → `ok` False with `division by zero`; `9 ** 999` → `ok` False (exponent limit); `sqrt(-1)` → `ok` False; an expression longer than 200 chars → `ok` False.
- [ ] AC13: `src/tools/calculator.py` contains no call to `eval(` or `exec(` (test reads the source).

**File tools** (tests use a `tmp_path` workspace)
- [ ] AC14: `write_file("a/b.txt", "hi")` creates the file inside the workspace; `read_file("a/b.txt")` returns `"hi"`.
- [ ] AC15: `../x.txt`, `a/../../x.txt`, and an absolute path (e.g. `/etc/passwd`) are rejected by both tools, and no file is created outside the workspace.
- [ ] AC16: A symlink inside the workspace that points outside it is rejected by both tools (skipped if the OS cannot create symlinks).
- [ ] AC17: `read_file` on a missing file, a folder, a file over 100 000 bytes, or a non-UTF-8 file returns `ok` False.
- [ ] AC18: `write_file` with content over 100 000 chars returns `ok` False and writes nothing.

**Web search** (`DDGS` mocked)
- [ ] AC19: With 3 mocked results, the output contains each title and URL, each snippet is at most 300 chars plus `…`, and the whole output is at most 2000 chars.
- [ ] AC20: `max_results` defaults to 3 and is passed to `DDGS().text`; `max_results=0` and `max_results=11` fail validation.
- [ ] AC21: A mocked `RatelimitException` or `TimeoutException` gives `ok` False with an error starting with `TRANSIENT_PREFIX`; a mocked `ValueError` gives an error without it.
- [ ] AC22: Zero mocked results give `ok` True and `"No results found."`.

**Validation gate**
- [ ] AC23: `call_tool("nope", {})` → `ok` False, the error names `nope` and lists the 4 available tools.
- [ ] AC24: With validation on, `call_tool("calculator", {"expr": "1+1"})` and `call_tool("calculator", {"expression": 5})` return `ok` False with an error that names the field, and the tool function is never called.
- [ ] AC25: With validation on, a `validation_error` event is emitted for invalid arguments.
- [ ] AC26: With validation off, invalid arguments reach the tool function (spy), and the result is still `ok` False without raising.
- [ ] AC27: A tool that raises `RuntimeError("boom")` gives `{"ok": False, "output": None, "error": "RuntimeError: boom"}`.
- [ ] AC28: With retry on and `tool_retry_attempts=2`, a tool that fails transiently twice then succeeds returns `ok` True, is called 3 times, `sleep` gets `[1, 2]`, and 2 `retry` events are emitted.
- [ ] AC29: A tool that always fails transiently is called exactly `1 + tool_retry_attempts` times, then `ok` False is returned.
- [ ] AC30: With retry off, a transient failure is called exactly once and `sleep` is never called.
- [ ] AC31: A non-transient failure and a validation error are never retried (one call or zero calls, no `sleep`).
- [ ] AC32: Every `call_tool` emits exactly one `tool_call` event with the keys `tool`, `args`, `ok`, `attempts`, `duration_ms`, and one of `output`/`error`.
- [ ] AC33: An `on_event` callback that raises does not change the result `call_tool` returns.

**Project checks**
- [ ] AC34: `pytest -q` passes with no network access and no LLM calls.
- [ ] AC35: `ruff check .` reports no errors.

## 7. Edge cases and errors
- **`.env` missing:** `get_settings()` raises `ConfigError("OLLAMA_MODEL is required ...")`. Defaults still cover everything else.
- **`.env.example` says `MAX_REPLANS=2`**, while the architecture default is 1. The code default follows the architecture; `.env.example` is updated to match (see open question 4).
- **Args are not a dict** (e.g. the LLM sends a string): with validation on, a validation error; with validation off, the `TypeError` from `fn(**args)` is caught in step 3.
- **Calculator huge numbers:** the exponent limit stops `9 ** 9 ** 9` (the inner `9 ** 9` is already over 100). Float overflow → `result too large`.
- **Calculator `True + 1`:** `bool` constants are rejected.
- **File path `.` or `""`:** `.` resolves to the workspace folder itself, so `read_file` fails ("not a file") and `write_file` fails; `""` fails validation.
- **Workspace folder missing:** `write_file` creates it; `read_file` returns "file not found".
- **ddgs raises `DDGSException("No results found")`** on empty searches in some versions. Treat that message as zero results, not as an error (check the real behaviour during implementation).
- **Real backoff in tests:** never. Tests always pass a fake `sleep`.
- **Secrets:** no error message or event ever includes a `.env` value.

## 8. Test plan
All tests are unit tests: no LLM, no internet (Hard rule 8). Files mirror `src/`.

| Test file | Covers |
|---|---|
| `tests/test_config.py` | AC1–AC6: defaults, required model, bounds, bool parsing, secret-free messages, `get_llm` with `ChatOllama` patched, no model names in `src/` |
| `tests/test_console.py` | AC7 |
| `tests/tools/test_registry.py` | AC8 (parametrised over all 4 tools), AC9 |
| `tests/tools/test_calculator.py` | AC10–AC13: correct results, injection attempts, limits, no `eval`/`exec` in source |
| `tests/tools/test_files.py` | AC14–AC18 using `tmp_path` and `monkeypatch` on `WORKSPACE_DIR`; symlink test with `pytest.skip` fallback |
| `tests/tools/test_web_search.py` | AC19–AC22 with `DDGS` monkeypatched to a fake class |
| `tests/test_validation.py` | AC23–AC33 with fake tools registered through `monkeypatch` on `TOOLS`, a fake `sleep` that records delays, and a list-collecting `on_event` |

AC34 and AC35 are checked by running `pytest -q` and `ruff check .`.

## 9. Open questions
1. **Which counter limits gate retries?** Your request says "up to MAX_RETRIES". The architecture uses a separate `TOOL_RETRY_ATTEMPTS` (default 2), because `MAX_RETRIES` is the critic's per-step limit. This spec follows the architecture. Change it if you want one shared number.
2. **`web_search` default:** your request says top 3 results; the architecture says `max_results` default 5. This spec uses default 3 (range 1–10) and will update the architecture table.
3. **How a tool marks a failure as transient:** this spec uses an error prefix (`"transient: "`), which keeps the return format exactly `{ok, output, error}`. The alternative is a fourth key, `retryable: bool`, which is clearer in code but changes the format in Hard rule 1.
4. **`MAX_REPLANS` default:** architecture 1, `.env.example` 2. Which one do you want?
5. **`on_event` hook:** added now, so session 5 can connect the logger without changing `call_tool`. Fine, or would you rather leave event emission out until session 5?
