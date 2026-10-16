"""Tests for `ui/app.py` and the source rules of `ui/` (spec streamlit-ui, AC16 and AC17).

Pure Python: no Streamlit app is started, no page is drawn, and no run is ever started.
"""

import ast
import sys
from pathlib import Path
from typing import NoReturn

import pytest

import ui.app as app_module
from src.config import PROJECT_ROOT, ConfigError, UiSettings
from ui.app import DASHBOARD_TITLE, EXPLORER_TITLE, RUN_TITLE, pages_for, resolve_mode

UI_DIR = PROJECT_ROOT / "ui"
FORBIDDEN_MODULES = ("src.agents", "src.graph", "src.tools", "src.validation")


def _fail_if_called(*args: object, **kwargs: object) -> NoReturn:
    """A stand-in for `run_task` that fails the test when it runs."""
    raise AssertionError("run_task must not be called")


def test_pages_for_replay_only_has_no_run_page_and_never_runs_a_task_ac16(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC16: replay-only has two pages and no code path to the Run page or `run_task`."""
    # WHY: a hosted copy without the live flag must never spend quota or run tools.
    monkeypatch.setattr("src.run.run_task", _fail_if_called)
    monkeypatch.setattr("ui.live.run_task", _fail_if_called)
    monkeypatch.delitem(sys.modules, "ui.pages.live_run", raising=False)

    replay = pages_for(False)

    assert [page.title for page in replay] == [EXPLORER_TITLE, DASHBOARD_TITLE]
    assert "ui.pages.live_run" not in sys.modules
    live = pages_for(True)
    assert [page.title for page in live] == [RUN_TITLE, EXPLORER_TITLE, DASHBOARD_TITLE]
    # NOTE: the guard means importing `ui.app` draws nothing; `main()` runs only as `__main__`.
    tree = ast.parse(Path(app_module.__file__ or "").read_text(encoding="utf-8"))
    top_level_calls = [
        node.value.func.id
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]
    assert "main" not in top_level_calls


def test_resolve_mode_each_condition_gives_live_flag_and_note_ac16(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # WHY: live mode needs both the flag and valid main settings; any other state must fall
    # back to replay-only with a reason, never raise on the first page load.
    def ui_settings(allow: bool) -> UiSettings:
        return UiSettings(allow_live_runs=allow, model_choices=("m1",))

    def raise_config_error() -> NoReturn:
        raise ConfigError("Invalid configuration: OLLAMA_MODEL is required")

    monkeypatch.setattr(app_module, "get_ui_settings", lambda: ui_settings(False))
    live, note = resolve_mode()
    assert live is False
    assert "UI_ALLOW_LIVE_RUNS" in note

    monkeypatch.setattr(app_module, "get_ui_settings", lambda: ui_settings(True))
    monkeypatch.setattr(app_module, "get_settings", raise_config_error)
    assert resolve_mode() == (False, "Invalid configuration: OLLAMA_MODEL is required")

    monkeypatch.setattr(app_module, "get_settings", lambda: object())
    assert resolve_mode() == (True, "")

    monkeypatch.setattr(app_module, "get_ui_settings", raise_config_error)
    assert resolve_mode() == (False, "Invalid configuration: OLLAMA_MODEL is required")


def test_ui_sources_import_no_agent_graph_tool_or_validation_code_ac17() -> None:
    """AC17: the UI files import no agent internals and name no model."""
    # WHY: the UI may only start a run through `run_task`; importing agents, the graph or tools
    # would give the browser a second way to run them, and a model name would break Hard rule 2.
    sources = sorted([*UI_DIR.glob("*.py"), *(UI_DIR / "pages").glob("*.py")])
    assert len(sources) >= 8
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert "gpt-oss" not in text.lower() and "gemma" not in text.lower(), path.name
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
            else:
                imported = []
            for name in imported:
                assert not name.startswith(FORBIDDEN_MODULES), (path.name, name)
