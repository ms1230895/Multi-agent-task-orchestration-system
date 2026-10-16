"""Tool definitions: the list of tools the LLM is told about, built from the registry.

Architecture layer: 2 (tools). The executor passes `tool_definitions()` to
`llm.bind_tools(...)`, so the LLM sees the same names and argument schemas that the
validation gate checks.
"""

from typing import Any

from src.tools import TOOLS

# WHY: the LLM chooses a tool by reading its description, so each one says what the tool does
# and what its arguments mean. Keep them short; they are sent with every executor call.
TOOL_DESCRIPTIONS: dict[str, str] = {
    "calculator": (
        "Evaluate an arithmetic expression. Supports + - * / // % **, parentheses, "
        "pi, e, tau, and sqrt, log, log10, log2, exp, sin, cos, tan, asin, acos, atan, "
        "floor, ceil, abs, round, min, max."
    ),
    "read_file": "Read a UTF-8 text file. The path is relative to the workspace folder.",
    "write_file": (
        "Write a UTF-8 text file, creating folders and overwriting an existing file. "
        "The path is relative to the workspace folder."
    ),
    "web_search": (
        "Search the web. Returns up to max_results results, each with title, URL and snippet."
    ),
}


def tool_definitions() -> list[dict[str, Any]]:
    """Build the tool list for `llm.bind_tools(...)`, one entry per registered tool.

    Returns:
        OpenAI-style function definitions with the registry name, a description, and the
        JSON schema of the tool's Pydantic argument model.

    WHY: passing the Pydantic classes to `bind_tools` would name the tools after the classes
    (`CalculatorArgs`), and the gate would reject every call as an unknown tool. The same list
    is used with `ENABLE_VALIDATION` on and off, so the ablation changes only the gate.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": TOOL_DESCRIPTIONS[name],
                "parameters": TOOLS[name][0].model_json_schema(),
            },
        }
        for name in sorted(TOOLS)
    ]
