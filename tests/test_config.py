"""Unit tests for `src/config.py` (spec foundation-tools-validation, AC1-AC6).

No test reads `.env`, calls `get_settings()` unpatched, or touches the network.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src import config
from src.config import ConfigError, Settings, get_llm, load_settings

SRC_DIR = Path(__file__).resolve().parents[1] / "src"


def test_load_settings_only_model_given_returns_all_defaults() -> None:
    """AC1: every default from the spec table is applied."""
    # WHY: defaults are the hard limits of Hard rule 5; a wrong default changes every run.
    s = load_settings({"OLLAMA_MODEL": "m"})
    assert s.ollama_model == "m"
    assert s.ollama_temperature == 0.0
    assert s.ollama_timeout_s == 120
    assert s.enable_validation is True
    assert s.enable_retry is True
    assert s.max_retries == 2
    assert s.max_replans == 1
    assert s.max_tool_iterations == 5
    assert s.tool_retry_attempts == 2


def test_load_settings_values_given_converts_strings_to_types() -> None:
    """AC1: string env values are converted to int, float and bool."""
    # WHY: os.environ holds only strings; the types must be right for the limits to work.
    s = load_settings(
        {
            "OLLAMA_MODEL": "m",
            "OLLAMA_TEMPERATURE": "0.5",
            "OLLAMA_TIMEOUT_S": "30",
            "MAX_RETRIES": "4",
            "MAX_REPLANS": "3",
            "MAX_TOOL_ITERATIONS": "10",
            "TOOL_RETRY_ATTEMPTS": "0",
        }
    )
    assert (s.ollama_temperature, s.ollama_timeout_s) == (0.5, 30)
    assert (s.max_retries, s.max_replans, s.max_tool_iterations) == (4, 3, 10)
    assert s.tool_retry_attempts == 0


def test_load_settings_empty_value_uses_default() -> None:
    """AC1: an empty variable means 'not set', so the default applies."""
    # WHY: `MAX_RETRIES=` in .env must not crash start-up.
    s = load_settings({"OLLAMA_MODEL": "m", "MAX_RETRIES": "  "})
    assert s.max_retries == 2


def test_settings_assign_field_after_creation_raises() -> None:
    """AC1: Settings is frozen."""
    # WHY: no module may change a limit or flag in the middle of a run.
    s = load_settings({"OLLAMA_MODEL": "m"})
    with pytest.raises(ValidationError):
        s.max_retries = 5  # type: ignore[misc]


def test_load_settings_empty_mapping_raises_config_error_naming_model() -> None:
    """AC2: a missing model name stops start-up with a clear message."""
    # WHY: Hard rule 2, the model name has no default and must be required.
    with pytest.raises(ConfigError, match="OLLAMA_MODEL"):
        load_settings({})


def test_load_settings_blank_model_raises_config_error() -> None:
    """AC2: a whitespace-only model name counts as missing."""
    # WHY: a blank name would reach ChatOllama and fail late, with a worse message.
    with pytest.raises(ConfigError, match="OLLAMA_MODEL is required"):
        load_settings({"OLLAMA_MODEL": "   "})


@pytest.mark.parametrize(
    ("var", "bad", "expected"),
    [
        ("MAX_RETRIES", "99", "MAX_RETRIES must be between 0 and 5"),
        ("MAX_RETRIES", "-1", "MAX_RETRIES must be between 0 and 5"),
        ("MAX_RETRIES", "abc", "MAX_RETRIES must be a whole number"),
        ("MAX_REPLANS", "77", "MAX_REPLANS must be between 0 and 3"),
        ("MAX_TOOL_ITERATIONS", "0", "MAX_TOOL_ITERATIONS must be between 1 and 10"),
        ("TOOL_RETRY_ATTEMPTS", "88", "TOOL_RETRY_ATTEMPTS must be between 0 and 5"),
        ("OLLAMA_TIMEOUT_S", "9999", "OLLAMA_TIMEOUT_S must be between 1 and 600"),
        ("OLLAMA_TEMPERATURE", "5", "OLLAMA_TEMPERATURE must be between 0 and 2"),
        ("OLLAMA_TEMPERATURE", "hot", "OLLAMA_TEMPERATURE must be a number"),
    ],
)
def test_load_settings_bad_limit_raises_config_error_without_value(
    var: str, bad: str, expected: str
) -> None:
    """AC3: bad limits name the variable and rule, never the bad value."""
    # WHY: Hard rule 5 (a typo must be refused) and Hard rule 7 (no value in messages).
    with pytest.raises(ConfigError) as info:
        load_settings({"OLLAMA_MODEL": "m", var: bad})
    message = str(info.value)
    assert expected in message
    assert bad not in message.replace(expected, "")


@pytest.mark.parametrize("word", ["true", "TRUE", "True", "1", "yes", "YES", "Yes"])
def test_load_settings_enable_retry_true_words_parse_to_true(word: str) -> None:
    """AC4: true words in any case give True."""
    # WHY: ablation flag must be switchable reliably.
    assert load_settings({"OLLAMA_MODEL": "m", "ENABLE_RETRY": word}).enable_retry is True


@pytest.mark.parametrize("word", ["false", "FALSE", "False", "0", "no", "NO", "No"])
def test_load_settings_enable_retry_false_words_parse_to_false(word: str) -> None:
    """AC4: false words in any case give False."""
    # WHY: the 'off' side of the ablation must really switch the feature off.
    assert load_settings({"OLLAMA_MODEL": "m", "ENABLE_RETRY": word}).enable_retry is False


@pytest.mark.parametrize("var", ["ENABLE_RETRY", "ENABLE_VALIDATION"])
@pytest.mark.parametrize("word", ["maybe", "enabled", "2"])
def test_load_settings_flag_unknown_word_raises_config_error(var: str, word: str) -> None:
    """AC4: any other word is an error naming the variable, not echoing the value."""
    # WHY: a typo must never silently flip an ablation flag.
    with pytest.raises(ConfigError) as info:
        load_settings({"OLLAMA_MODEL": "m", var: word})
    assert var in str(info.value)
    assert word not in str(info.value).replace(var, "")


def test_get_llm_given_settings_builds_chatollama_with_model_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC5: get_llm passes the configured model name to ChatOllama."""
    # WHY: Hard rule 2, the model comes only from config; no network may be touched.
    fake_cls = MagicMock(name="ChatOllama")
    monkeypatch.setattr(config, "ChatOllama", fake_cls)
    settings = load_settings({"OLLAMA_MODEL": "some-model", "OLLAMA_TIMEOUT_S": "45"})

    client = get_llm(settings)

    fake_cls.assert_called_once()
    kwargs = fake_cls.call_args.kwargs
    assert kwargs["model"] == settings.ollama_model == "some-model"
    assert kwargs["temperature"] == 0.0
    assert kwargs["client_kwargs"] == {"timeout": 45}
    assert client is fake_cls.return_value


def test_get_llm_no_settings_uses_get_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC5: without an argument, get_llm falls back to get_settings()."""
    # WHY: the default path must work, and must not read the real .env in a test.
    fake_cls = MagicMock(name="ChatOllama")
    monkeypatch.setattr(config, "ChatOllama", fake_cls)
    monkeypatch.setattr(config, "get_settings", lambda: load_settings({"OLLAMA_MODEL": "dflt"}))

    get_llm()

    assert fake_cls.call_args.kwargs["model"] == "dflt"


def test_get_settings_called_twice_loads_once_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1/AC2: get_settings reads dotenv + environ once and caches the result."""
    # WHY: one load means all modules see the same limits; dotenv is patched so the real
    # .env is never read.
    calls: list[object] = []
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: calls.append(a))
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    config.get_settings.cache_clear()
    try:
        first = config.get_settings()
        second = config.get_settings()
    finally:
        config.get_settings.cache_clear()
    assert first is second
    assert first.ollama_model == "env-model"
    assert len(calls) == 1


def test_get_settings_model_missing_raises_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC2: with no OLLAMA_MODEL in the environment, get_settings raises ConfigError."""
    # WHY: a missing .env must give a clear start-up error (spec section 7).
    monkeypatch.setattr(config, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    config.get_settings.cache_clear()
    try:
        with pytest.raises(ConfigError, match="OLLAMA_MODEL"):
            config.get_settings()
    finally:
        config.get_settings.cache_clear()


def test_src_files_contain_no_hardcoded_model_names() -> None:
    """AC6: no model name appears anywhere under src/."""
    # WHY: Hard rule 2, the model name is read only from .env through config.
    offenders = [
        f"{path.relative_to(SRC_DIR)}: {name}"
        for path in SRC_DIR.rglob("*.py")
        for name in ("gpt-oss", "gemma")
        if name in path.read_text(encoding="utf-8").lower()
    ]
    assert offenders == []


def test_bounds_table_matches_settings_field_bounds() -> None:
    """Extra: _BOUNDS equals the ge/le values declared on Settings."""
    # WHY: error messages use _BOUNDS; if it drifts from the fields, users see wrong ranges.
    declared: dict[str, tuple[float, float]] = {}
    for name, field in Settings.model_fields.items():
        ge = next((m.ge for m in field.metadata if hasattr(m, "ge")), None)
        le = next((m.le for m in field.metadata if hasattr(m, "le")), None)
        if ge is not None and le is not None:
            declared[name] = (ge, le)
    assert declared == config._BOUNDS
