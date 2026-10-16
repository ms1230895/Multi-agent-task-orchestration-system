"""Tests for `src/tools/definitions.py`: tool descriptions and `tool_definitions()` (AC1, AC2)."""

from src.tools import TOOLS
from src.tools.definitions import TOOL_DESCRIPTIONS, tool_definitions


def test_ac1_tool_definitions_returns_one_function_dict_per_tool_in_sorted_order() -> None:
    # WHY: the LLM must see exactly the registry names, or the gate rejects every call.
    definitions = tool_definitions()
    assert len(definitions) == 4
    assert [d["function"]["name"] for d in definitions] == sorted(TOOLS)
    for definition in definitions:
        assert definition["type"] == "function"


def test_ac1_tool_definitions_have_description_and_schema_from_registry() -> None:
    # WHY: parameters must be the same schema the gate validates with.
    for definition in tool_definitions():
        function = definition["function"]
        name = function["name"]
        assert function["description"].strip() != ""
        assert function["parameters"] == TOOLS[name][0].model_json_schema()


def test_ac2_tool_descriptions_keys_equal_registry_keys() -> None:
    # WHY: a missing description would raise KeyError; an extra one would be dead text.
    assert set(TOOL_DESCRIPTIONS) == set(TOOLS)
