"""The validation gate: every tool call from the executor goes through `call_tool`.

Architecture layer: 3 (reliability). Steps, in order: the tool exists, the arguments are
valid, the tool runs inside try/except, and temporary failures are retried.

The two ablation flags live here: `ENABLE_VALIDATION` switches off step 2 and
`ENABLE_RETRY` switches off the retries. The other kind of retry, where the LLM fixes its
own bad call after reading the error, belongs to the executor (session 4), not here.
"""

import time
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import ValidationError

from src.config import Settings, get_settings
from src.tools import TOOLS
from src.tools.base import TRANSIENT_PREFIX, ToolResult, failure, is_transient

MAX_LOGGED_CHARS = 500
MAX_LOGGED_ARG_CHARS = 200
MAX_BACKOFF_S = 4

EventCallback = Callable[[str, dict[str, Any]], None]


def _trim(text: str, limit: int) -> str:
    """Cut text to `limit` characters, ending with `…` when something was removed."""
    return text if len(text) <= limit else text[:limit] + "…"


def _loggable_args(args: Any) -> Any:
    """Copy the arguments for an event, cutting long strings.

    WHY: `write_file` content can be 100 000 characters; a trace line should stay small.
    """
    if not isinstance(args, Mapping):
        return _trim(repr(args), MAX_LOGGED_ARG_CHARS)
    return {
        key: _trim(value, MAX_LOGGED_ARG_CHARS) if isinstance(value, str) else value
        for key, value in args.items()
    }


def _emit(on_event: EventCallback | None, event: str, data: dict[str, Any]) -> None:
    """Send an event to the optional callback, never letting it break the tool call.

    WHY: logging is a side job. If the logger fails, the tool result must still reach the
    executor, so a callback error is ignored.
    """
    if on_event is None:
        return
    try:
        on_event(event, data)
    except Exception:  # noqa: BLE001 - see WHY above
        pass


def _format_validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    """List what is wrong as `{field, problem}` pairs, without echoing the input values.

    WHY: the LLM reads this text to fix its next call, so each entry names the field.
    """
    return [
        {
            "field": ".".join(str(part) for part in error["loc"]) or "(arguments)",
            "problem": error["msg"],
        }
        for error in exc.errors()
    ]


def _run_once(function: Callable[..., ToolResult], kwargs: Any) -> ToolResult:
    """Run the tool function once; any exception becomes a failed result.

    Args:
        function: the tool function from the registry.
        kwargs: the keyword arguments (a mapping; anything else fails inside the call).

    Returns:
        A `ToolResult`. `TimeoutError` and `ConnectionError` are marked transient.
    """
    try:
        raw: Any = function(**kwargs)
    except (TimeoutError, ConnectionError) as exc:
        return failure(f"{TRANSIENT_PREFIX}{type(exc).__name__}: {exc}")
    except Exception as exc:  # WHY: a tool must never crash the run (Hard rule 1)
        return failure(f"{type(exc).__name__}: {exc}")

    # WHY: a buggy tool could return the wrong shape; the layers above rely on the format.
    if (
        isinstance(raw, dict)
        and set(raw) == {"ok", "output", "error"}
        and isinstance(raw["ok"], bool)
    ):
        return {"ok": raw["ok"], "output": raw["output"], "error": raw["error"]}
    return failure("tool returned an invalid result")


def call_tool(
    name: str,
    args: dict[str, Any],
    *,
    settings: Settings | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_event: EventCallback | None = None,
) -> ToolResult:
    """Run one tool call through the gate.

    Args:
        name: the tool name, which must be a key in `TOOLS`.
        args: the arguments the LLM chose.
        settings: use these settings; default is `get_settings()`.
        sleep: function used to wait between retries. Tests pass a fake so nothing waits.
        on_event: optional callback `(event_name, data)`; session 5 connects the logger.

    Returns:
        `{ok, output, error}`. Never raises because of the tool or its arguments.
    """
    settings = settings or get_settings()
    started = time.perf_counter()
    attempts = 0

    def finish(result: ToolResult) -> ToolResult:
        """Emit the one `tool_call` event for this call and return the result."""
        data: dict[str, Any] = {
            "tool": name,
            "args": _loggable_args(args),
            "ok": result["ok"],
            "attempts": attempts,
            "duration_ms": int((time.perf_counter() - started) * 1000),
        }
        if result["ok"]:
            data["output"] = _trim(result["output"] or "", MAX_LOGGED_CHARS)
        else:
            data["error"] = _trim(result["error"] or "", MAX_LOGGED_CHARS)
        _emit(on_event, "tool_call", data)
        return result

    # STEP 1: does the tool exist?
    entry = TOOLS.get(name)
    if entry is None:
        available = ", ".join(sorted(TOOLS))
        return finish(failure(f"unknown tool: {name}. Available: {available}"))
    schema, function = entry

    # STEP 2: are the arguments valid? (only when ENABLE_VALIDATION is on)
    # WHY: a bad argument becomes a clear message the LLM can fix, and the tool is never
    # called with it. When the flag is off (the ablation baseline) raw arguments go straight
    # to the tool, and any problem is caught in step 3.
    kwargs: Any = args
    if settings.enable_validation:
        try:
            kwargs = schema.model_validate(args).model_dump()
        except ValidationError as exc:
            errors = _format_validation_errors(exc)
            _emit(
                on_event,
                "validation_error",
                {"tool": name, "args": _loggable_args(args), "errors": errors},
            )
            details = "; ".join(f"{e['field']}: {e['problem']}" for e in errors)
            return finish(failure(f"invalid arguments for {name}: {details}"))

    # STEP 3 and 4: run the tool, and retry temporary failures (only when ENABLE_RETRY is on).
    # WARNING: the retry count is a hard limit (Hard rule 5); the loop cannot run forever.
    extra_attempts = settings.tool_retry_attempts if settings.enable_retry else 0
    while True:
        attempts += 1
        result = _run_once(function, kwargs)
        # WHY: only temporary problems are retried; the same bad call would fail again.
        if result["ok"] or not is_transient(result) or attempts > extra_attempts:
            break
        retry_number = attempts  # 1 for the first extra attempt, 2 for the second, ...
        _emit(
            on_event,
            "retry",
            {
                "kind": "tool",
                "tool": name,
                "attempt": retry_number,
                "reason": _trim(result["error"] or "", MAX_LOGGED_CHARS),
            },
        )
        # WHY: waiting 1 s, 2 s, 4 s (at most) gives a rate limit or timeout time to clear.
        sleep(min(2 ** (retry_number - 1), MAX_BACKOFF_S))

    # STEP 5: log and return.
    return finish(result)
