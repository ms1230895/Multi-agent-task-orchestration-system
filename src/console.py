"""Terminal colours for the orchestrator.

Architecture layer: cross-cutting console. Display only; the JSONL trace is the
source of truth, never what is printed here.

WHY: one fixed colour per role, defined once, so a reader can tell the planner from the
executor at a glance and no other module hard-codes a colour (colour-coding rule).
"""

from typing import Any

from rich.console import Console
from rich.markup import escape

PLANNER = "magenta"
EXECUTOR = "cyan"
CRITIC = "yellow"
TOOL = "blue"
RETRY = "dark_orange"  # also used for replans
SUCCESS = "green"
ERROR = "red"

# WHY: one shared Console, so all output goes through the same stream and settings.
console = Console()

# WHY: one colour per trace event, built only from the role constants above, so the live
# terminal output and a future UI can use the same colours for the same events.
EVENT_COLOURS: dict[str, str] = {
    "plan_created": PLANNER,
    "replan": RETRY,
    "tool_call": TOOL,
    "validation_error": ERROR,
    "retry": RETRY,
    "critic_verdict": CRITIC,
    "run_end": SUCCESS,
}


def _say(out: Console, text: str, colour: str) -> None:
    """Print text in a colour, showing rich markup in it literally.

    WHY: the text comes from the LLM, web pages and files. Without `escape`, a string like
    `[red]x[/red]` or `[link=...]` would change the terminal output. `highlight=False` keeps
    rich from re-colouring numbers and paths on top of the role colour.
    """
    out.print(escape(text), style=colour, highlight=False)


def print_record(record: dict[str, Any], *, out: Console = console) -> None:
    """Print one trace record as a short coloured block.

    Args:
        record: a trace record `{ts, run_id, model, event, data}`.
        out: the console to print to. Tests pass a recording console.

    WHY: the CLI shows the run live from the same records the logger writes, so what you
    see is what is in the trace. An unknown event prints as a plain line and never raises.
    """
    event = str(record.get("event", ""))
    data = record.get("data", {})
    if not isinstance(data, dict):
        data = {}

    if event == "plan_created":
        _say(out, "Plan:", PLANNER)
        for number, step in enumerate(data.get("steps", []), start=1):
            _say(out, f"  {number}. {step}", PLANNER)
    elif event == "replan":
        _say(out, f"Replan #{data.get('replans')}: {data.get('reason', '')}", RETRY)
        for number, step in enumerate(data.get("steps", []), start=1):
            _say(out, f"  {number}. {step}", RETRY)
    elif event == "tool_call":
        ok = bool(data.get("ok"))
        detail = data.get("output") if ok else data.get("error")
        _say(
            out,
            f"{data.get('tool')}({data.get('args')}) -> {detail}",
            TOOL if ok else ERROR,
        )
    elif event == "validation_error":
        _say(out, f"invalid arguments for {data.get('tool')}: {data.get('errors')}", ERROR)
    elif event == "retry":
        _say(out, f"retry ({data.get('kind')}) #{data.get('attempt')}: {data.get('reason')}", RETRY)
    elif event == "critic_verdict":
        _say(out, f"Step {data.get('step', 0) + 1} output: {data.get('output')}", EXECUTOR)
        _say(out, f"Critic: {data.get('verdict')}. {data.get('feedback')}", CRITIC)
    elif event == "run_end":
        done = data.get("status") == "done"
        _say(
            out,
            f"Run {data.get('status')}. Counters: {data.get('counters')}",
            SUCCESS if done else ERROR,
        )
    else:
        _say(out, f"{event}: {data}", EVENT_COLOURS.get(event, TOOL))
