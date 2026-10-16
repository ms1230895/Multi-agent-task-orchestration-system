"""Terminal colours for the orchestrator.

Architecture layer: cross-cutting console. Display only; the JSONL trace is the
source of truth, never what is printed here.

WHY: one fixed colour per role, defined once, so a reader can tell the planner from the
executor at a glance and no other module hard-codes a colour (colour-coding rule).
"""

from rich.console import Console

PLANNER = "magenta"
EXECUTOR = "cyan"
CRITIC = "yellow"
TOOL = "blue"
RETRY = "dark_orange"  # also used for replans
SUCCESS = "green"
ERROR = "red"

# WHY: one shared Console, so all output goes through the same stream and settings.
console = Console()
