"""Unit tests for `src/console.py` (spec foundation-tools-validation, AC7)."""

from rich.console import Console

from src import console as console_module


def test_console_constants_match_colour_coding_rule() -> None:
    """AC7: the 7 role colours equal the colour-coding rule."""
    # WHY: one fixed colour per role so output is readable and consistent.
    assert console_module.PLANNER == "magenta"
    assert console_module.EXECUTOR == "cyan"
    assert console_module.CRITIC == "yellow"
    assert console_module.TOOL == "blue"
    assert console_module.RETRY == "dark_orange"
    assert console_module.SUCCESS == "green"
    assert console_module.ERROR == "red"


def test_console_shared_instance_is_rich_console() -> None:
    """AC7: a shared rich Console exists."""
    # WHY: all output must go through one stream and settings.
    assert isinstance(console_module.console, Console)
