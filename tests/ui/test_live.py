"""Tests for `ui/live.py` (spec streamlit-ui, AC8 to AC11): RunManager and live_settings.

No real thread and no LLM: `start_thread` runs the job at once and `run_fn` is a fake.
"""

from collections.abc import Callable
from typing import Any

import pytest

from src.config import ConfigError, Settings, UiSettings, load_settings
from ui.live import RunManager, RunSnapshot, live_settings

SETTINGS = load_settings({"OLLAMA_MODEL": "m1"})


def _run_now(job: Callable[[], None]) -> None:
    """A `start_thread` stand-in: run the job in the calling thread."""
    job()


def test_run_manager_start_collects_records_and_final_state_ac8() -> None:
    """AC8: records arrive in order, running is True during the run and False after it."""
    # WHY: the page draws only from snapshots; wrong order, a stale running flag or a missing
    # state would show a wrong live view.
    seen: list[RunSnapshot] = []
    holder: list[RunManager] = []
    final_state: dict[str, Any] = {"status": "done", "final_answer": "42"}

    def fake(task: str, *, on_record: Callable[[dict[str, Any]], None], settings: Settings) -> Any:
        for number in range(3):
            on_record({"event": "retry", "n": number})
            seen.append(holder[0].snapshot())
        return final_state

    manager = RunManager(run_fn=fake, start_thread=_run_now)
    holder.append(manager)

    assert manager.start("task", SETTINGS) is True

    assert [len(snap.records) for snap in seen] == [1, 2, 3]
    assert all(snap.running and snap.state is None for snap in seen)
    done = manager.snapshot()
    assert [record["n"] for record in done.records] == [0, 1, 2]
    assert done.running is False
    assert done.state == final_state
    assert done.error == ""
    # NOTE: a snapshot holds a copy, so changing it cannot change the manager.
    done.records.append({"n": 99})
    assert len(manager.snapshot().records) == 3


def test_run_manager_second_start_while_active_returns_false_ac9() -> None:
    """AC9: a start during an active run is refused and changes nothing."""
    # WHY: two clicks or two browser tabs must never start two runs at once (quota, and the
    # records of one run would mix with the other).
    calls: list[str] = []
    nested: list[bool] = []
    holder: list[RunManager] = []

    def fake(task: str, *, on_record: Callable[[dict[str, Any]], None], settings: Settings) -> Any:
        calls.append(task)
        on_record({"n": 1})
        nested.append(holder[0].start("other", settings))
        on_record({"n": 2})
        return {"status": "done"}

    manager = RunManager(run_fn=fake, start_thread=_run_now)
    holder.append(manager)

    assert manager.start("first", SETTINGS) is True

    assert nested == [False]
    assert calls == ["first"]
    assert [record["n"] for record in manager.snapshot().records] == [1, 2]
    # WHY: the lock must be released at the end, so the next run can start.
    assert manager.start("third", SETTINGS) is True
    assert calls == ["first", "third"]


@pytest.mark.parametrize(
    ("error", "type_name"),
    [
        (ConfigError("Invalid configuration: OLLAMA_MODEL is required"), "ConfigError"),
        (RuntimeError("boom"), "RuntimeError"),
    ],
)
def test_run_manager_run_fn_raises_sets_error_and_allows_next_start_ac10(
    error: Exception, type_name: str
) -> None:
    # WHY: a crash or a config error in run_fn must reach the page as text and must not leave
    # the manager locked forever, which would block every later run.
    calls: list[int] = []

    def fake(task: str, *, on_record: Callable[[dict[str, Any]], None], settings: Settings) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise error
        return {"status": "done"}

    manager = RunManager(run_fn=fake, start_thread=_run_now)

    assert manager.start("task", SETTINGS) is True
    snap = manager.snapshot()
    assert snap.error.startswith(f"{type_name}: ")
    assert snap.running is False
    assert snap.state is None

    assert manager.start("task", SETTINGS) is True
    again = manager.snapshot()
    assert again.error == ""
    assert again.state == {"status": "done"}


def test_live_settings_changes_only_the_three_allowed_fields_ac11() -> None:
    """AC11: model, validation and retry may change; anything else equals base; bad model fails."""
    # WHY: the browser must never pick a model that is not in `.env` (Hard rule 2) nor change
    # a limit such as MAX_REPLANS.
    base = load_settings(
        {"OLLAMA_MODEL": "m1", "MAX_RETRIES": "3", "MAX_REPLANS": "2", "OLLAMA_TEMPERATURE": "0.5"}
    )
    ui = UiSettings(allow_live_runs=True, model_choices=("m1", "m2"))

    chosen = live_settings(base, ui, model="m2", enable_validation=False, enable_retry=False)

    assert chosen.ollama_model == "m2"
    assert chosen.enable_validation is False
    assert chosen.enable_retry is False
    changeable = {"ollama_model", "enable_validation", "enable_retry"}
    assert {k: v for k, v in chosen.model_dump().items() if k not in changeable} == {
        k: v for k, v in base.model_dump().items() if k not in changeable
    }
    assert base.ollama_model == "m1"
    with pytest.raises(ValueError):
        live_settings(base, ui, model="other", enable_validation=True, enable_retry=True)
