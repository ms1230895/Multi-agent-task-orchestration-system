"""Tests for `src/agents/parsing.py`: JSON extraction and validation (AC4, AC5)."""

import pytest
from pydantic import BaseModel

from src.agents.parsing import MAX_ERROR_CHARS, parse_json_model


class Sample(BaseModel):
    """Tiny schema for parser tests."""

    steps: list[str]


@pytest.mark.parametrize(
    "text",
    [
        '{"steps": ["a", "b"]}',
        '```json\n{"steps": ["a", "b"]}\n```',
        '```\n{"steps": ["a", "b"]}\n```',
        'Sure, here it is: {"steps": ["a", "b"]} hope that helps',
    ],
    ids=["plain", "json_fence", "bare_fence", "chatter"],
)
def test_ac4_parse_json_model_valid_forms_return_model(text: str) -> None:
    # WHY: LLMs wrap JSON in fences or chatter; all of these must still parse.
    model, error = parse_json_model(text, Sample)
    assert error is None
    assert model is not None
    assert model.steps == ["a", "b"]


@pytest.mark.parametrize(
    "text",
    ["no json here", '{"steps": [}', '{"steps": "not a list"}', '{"other": 1}'],
    ids=["no_json", "broken_json", "wrong_type", "missing_field"],
)
def test_ac5_parse_json_model_bad_input_returns_error_not_raise(text: str) -> None:
    # WHY: the helper must never raise; the agent needs error text for its re-ask.
    model, error = parse_json_model(text, Sample)
    assert model is None
    assert error
    assert len(error) <= MAX_ERROR_CHARS


def test_ac5_parse_json_model_long_error_is_cut_to_300_chars() -> None:
    # WHY: the error goes back into a prompt, so it must stay short.
    text = '{"steps": [' + ",".join(["1"] * 200) + "]}"
    model, error = parse_json_model(text, Sample)
    assert model is None
    assert error is not None
    assert 0 < len(error) <= 300
