"""Terminal colours for the orchestrator.

Architecture layer: cross-cutting console. Display only; the JSONL trace is the
source of truth, never what is printed here.

WHY: one fixed colour per role, defined once, so a reader can tell the planner from the
executor at a glance and no other module hard-codes a colour (colour-coding rule).
"""

import re
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


# WHY: C0 and C1 control characters, including ESC (0x1b) and DEL, except tab (0x09) and
# newline (0x0a). A terminal reads ESC sequences as commands (clear the screen, set the title,
# hide text).
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def sanitize(text: str) -> str:
    """Make untrusted text safe to print: no control characters, no fake top-level lines.

    WHY: LLM output, web pages and files can hold terminal escape codes, and a newline could
    start a line that looks like a real `Critic: approve` event. Control characters become
    `?`, and every line after the first is indented by two spaces.
    """
    return _CONTROL_CHARS.sub("?", text).replace("\n", "\n  ")


def _say(out: Console, text: str, colour: str) -> None:
    """Print text in a colour, showing rich markup and control characters harmlessly.

    WHY: the text comes from the LLM, web pages and files. Without `escape`, a string like
    `[red]x[/red]` or `[link=...]` would change the terminal output. `highlight=False` keeps
    rich from re-colouring numbers and paths on top of the role colour, and `emoji=False`
    stops `:name:` text from being rewritten.
    """
    out.print(escape(sanitize(text)), style=colour, highlight=False, emoji=False)


def print_record(record: dict[str, Any], *, out: Console = console) -> None:
    """Print one trace record as a short coloured block.

    Args:
        record: a trace record `{ts, run_id, model, event, data}`.
        out: the console to print to. Tests pass a recording console.

    WHY: the CLI shows the run live from the same records the logger writes, so what you
    see is what is in the trace. Odd or unknown records print as plain lines and never raise.
    """
    event = str(record.get("event", ""))
    data = record.get("data", {})
    if not isinstance(data, dict):
        data = {}
    # WHY: the colour of each event comes from `EVENT_COLOURS`, so the table is the one place
    # to change it. Failures and the critic's executor-output line override it below.
    colour = EVENT_COLOURS.get(event, TOOL)

    if event == "plan_created":
        _say(out, "Plan:", colour)
        for number, step in enumerate(data.get("steps", []), start=1):
            _say(out, f"  {number}. {step}", colour)
    elif event == "replan":
        _say(out, f"Replan #{data.get('replans')}: {data.get('reason', '')}", colour)
        for number, step in enumerate(data.get("steps", []), start=1):
            _say(out, f"  {number}. {step}", colour)
    elif event == "tool_call":
        ok = bool(data.get("ok"))
        detail = data.get("output") if ok else data.get("error")
        _say(out, f"{data.get('tool')}({data.get('args')}) -> {detail}", colour if ok else ERROR)
    elif event == "validation_error":
        _say(out, f"invalid arguments for {data.get('tool')}: {data.get('errors')}", colour)
    elif event == "retry":
        _say(
            out, f"retry ({data.get('kind')}) #{data.get('attempt')}: {data.get('reason')}", colour
        )
    elif event == "critic_verdict":
        step = data.get("step")
        number = step + 1 if isinstance(step, int) else "?"
        _say(out, f"Step {number} output: {data.get('output')}", EXECUTOR)
        _say(out, f"Critic: {data.get('verdict')}. {data.get('feedback')}", colour)
    elif event == "run_end":
        done = data.get("status") == "done"
        _say(
            out,
            f"Run {data.get('status')}. Counters: {data.get('counters')}",
            colour if done else ERROR,
        )
    else:
        _say(out, f"{event}: {data}", colour)
