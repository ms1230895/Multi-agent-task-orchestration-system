"""Configuration: the only place that reads `.env` and environment variables.

Architecture layer: cross-cutting config, plus layer 1 (the LLM factory `get_llm`).
Every other module imports its limits, flags and model name from here, so a setting
is defined once and validated once.
"""

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_ollama import ChatOllama
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# WHY: tools and tests need one agreed location for the sandbox folder (Hard rule 4).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = PROJECT_ROOT / "workspace"

# WHY: error messages need the allowed range, and Pydantic's error does not carry both
# ends. NOTE: keep this table equal to the `ge`/`le` values on `Settings`; a test checks it.
# Upper bounds are part of Hard rule 5: a typo like MAX_RETRIES=200 must be refused,
# not run for hours.
_BOUNDS: dict[str, tuple[float, float]] = {
    "ollama_temperature": (0.0, 2.0),
    "ollama_timeout_s": (1, 600),
    "max_retries": (0, 5),
    "max_replans": (0, 3),
    "max_tool_iterations": (1, 10),
    "tool_retry_attempts": (0, 5),
}

_TRUE_WORDS = {"true", "1", "yes"}
_FALSE_WORDS = {"false", "0", "no"}


class ConfigError(Exception):
    """Raised when a setting is missing or invalid.

    The message names the variable and the rule, never the value (Hard rule 7).
    """


class Settings(BaseModel):
    """All typed settings of the project, built once from the environment.

    Frozen so no module can change a limit or flag in the middle of a run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    # WHY: no default for the model name (Hard rule 2); a missing name must stop start-up.
    ollama_model: str = Field(min_length=1)
    ollama_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    ollama_timeout_s: int = Field(default=120, ge=1, le=600)
    enable_validation: bool = True
    enable_retry: bool = True
    max_retries: int = Field(default=2, ge=0, le=5)
    max_replans: int = Field(default=1, ge=0, le=3)
    max_tool_iterations: int = Field(default=5, ge=1, le=10)
    tool_retry_attempts: int = Field(default=2, ge=0, le=5)

    @field_validator("enable_validation", "enable_retry", mode="before")
    @classmethod
    def _parse_bool(cls, value: Any) -> Any:
        """Accept only true/false/1/0/yes/no (any case) for flag variables.

        WHY: Pydantic's default also accepts words like "on" and "t". A strict list means
        a typo such as "maybe" is an error instead of silently switching an ablation flag.
        """
        if isinstance(value, bool):
            return value
        word = str(value).strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
        raise ValueError("not a boolean")


def _describe_error(error: Mapping[str, Any]) -> str:
    """Turn one Pydantic error into a short message that never contains the input value.

    WHY: Pydantic's own text includes `input_value=...`, which could leak a secret
    (Hard rule 7). We build the message from the field name and the error type only.
    """
    field = str(error["loc"][0])
    env_name = field.upper()
    kind = error["type"]
    if kind in ("missing", "string_too_short"):
        return f"{env_name} is required"
    if field in ("enable_validation", "enable_retry"):
        return f"{env_name} must be true or false"
    if kind in ("int_parsing", "int_from_float", "int_type"):
        return f"{env_name} must be a whole number"
    if kind in ("float_parsing", "float_type"):
        return f"{env_name} must be a number"
    if field in _BOUNDS:
        low, high = _BOUNDS[field]
        return f"{env_name} must be between {low:g} and {high:g}"
    return f"{env_name} is invalid"


def load_settings(env: Mapping[str, str]) -> Settings:
    """Build `Settings` from a mapping of environment variables.

    Args:
        env: variable name to string value, for example `os.environ` or a test dict.

    Returns:
        A validated, frozen `Settings`.

    Raises:
        ConfigError: if `OLLAMA_MODEL` is missing or any value is invalid.

    Why it takes a mapping: tests pass a plain dict and never touch the real `.env`.
    """
    # STEP 1: pick out only the variables we know, in their field names.
    # WHY: an empty value (`MAX_RETRIES=`) means "not set", so the default applies and
    # the model name (no default) is reported as required.
    raw: dict[str, str] = {}
    for field in Settings.model_fields:
        value = env.get(field.upper(), "").strip()
        if value:
            raw[field] = value

    # STEP 2: validate, and turn every problem into a value-free message.
    # WHY: `model_validate` takes raw strings and converts them; calling `Settings(**raw)`
    # would tell the type checker that str is passed where int or bool is expected.
    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        problems = [_describe_error(error) for error in exc.errors()]
        raise ConfigError("Invalid configuration: " + "; ".join(problems)) from None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load `.env` and the process environment once, and return the cached settings.

    WHY: one load means every module sees the same limits and flags during a run.
    Tests that need different settings call `load_settings` or `get_settings.cache_clear()`.
    """
    load_dotenv(PROJECT_ROOT / ".env")
    return load_settings(os.environ)


def get_llm(settings: Settings | None = None) -> ChatOllama:
    """Build the `ChatOllama` client for the configured model.

    Args:
        settings: use these settings; default is `get_settings()`.

    Returns:
        A client. Creating it makes no network call.

    WHY: the model name and temperature come only from config (Hard rule 2), and
    temperature defaults to 0 so benchmark runs are as repeatable as possible.
    """
    settings = settings or get_settings()
    return ChatOllama(
        model=settings.ollama_model,
        temperature=settings.ollama_temperature,
        # WHY: a hard request timeout means a hung model call cannot hang the run (Hard rule 5).
        client_kwargs={"timeout": settings.ollama_timeout_s},
    )
