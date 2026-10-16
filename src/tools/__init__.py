"""Tool registry: maps each tool name to its input schema and its function.

Architecture layer: 2 (tools). The validation gate (`src/validation.py`) looks tools up
here by name; nothing else should call a tool function directly.
"""

from collections.abc import Callable

from pydantic import BaseModel

# NOTE: import the modules, not the functions. Each module has a function with the same
# name (calculator.calculator, web_search.web_search); importing the function would hide
# the module, and `src.tools.web_search.DDGS` could no longer be patched in tests.
from src.tools import calculator, files, web_search
from src.tools.base import ToolResult

# WHY: one dict is the single source of truth for which tools exist. The gate checks a
# name against it, validates arguments with the schema, then calls the function.
TOOLS: dict[str, tuple[type[BaseModel], Callable[..., ToolResult]]] = {
    "calculator": (calculator.CalculatorArgs, calculator.calculator),
    "read_file": (files.ReadFileArgs, files.read_file),
    "write_file": (files.WriteFileArgs, files.write_file),
    "web_search": (web_search.WebSearchArgs, web_search.web_search),
}

__all__ = ["TOOLS", "ToolResult"]
