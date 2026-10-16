"""Run a task in a background thread so the Streamlit page can show it live.

Architecture layer: 9 (interface). No Streamlit import: the thread must never call Streamlit
(Streamlit calls from another thread fail), so it only collects records. The page reads them
with `RunManager.snapshot()` and does all the drawing itself.

WHY a thread (D47): Streamlit runs the page script again on every click, and that stops the old
script run with an exception that `run_task` does not catch. A run inside the page script would
stop halfway and have no `run_end`. In its own thread the run goes on whatever the user clicks.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from src.config import Settings, UiSettings
from src.logger import trim
from src.run import run_task
from src.state import AgentState

# WHY: an exception message can be long; the page and the snapshot stay small.
MAX_ERROR_CHARS = 300


@dataclass(frozen=True)
class RunSnapshot:
    """What the page needs to draw one frame of a run.

    Attributes:
        records: the trace records received so far, in arrival order (a copy).
        running: True while a run is active.
        state: the final state when the run has finished, otherwise None.
        error: `""`, or `"<Type>: <message>"` when `run_fn` itself raised (for example a
            `ConfigError`). A crash inside a run is not an error here: `run_task` turns it
            into a failed state.
    """

    records: list[dict[str, Any]]
    running: bool
    state: AgentState | None
    error: str


def _start_daemon_thread(job: Callable[[], None]) -> None:
    """Start `job` in a daemon thread.

    WHY daemon: when the server stops, a leftover run must not keep the process alive.
    """
    threading.Thread(target=job, daemon=True).start()


class RunManager:
    """Starts at most one run at a time and keeps its records for the page to read.

    One manager per server process (the page creates it with `st.cache_resource`).
    """

    def __init__(
        self,
        run_fn: Callable[..., AgentState] = run_task,
        start_thread: Callable[[Callable[[], None]], None] = _start_daemon_thread,
    ) -> None:
        """Prepare a manager.

        Args:
            run_fn: called as `run_fn(task, on_record=..., settings=...)`. Default: `run_task`.
            start_thread: runs a job. Tests pass a function that runs it at once, so they need
                no real thread.
        """
        self._run_fn = run_fn
        self._start_thread = start_thread
        # WHY two locks: `_run_lock` means "a run is active" and is only tried, never waited
        # for. `_data_lock` is held for a moment while the fields below are read or changed.
        self._run_lock = threading.Lock()
        self._data_lock = threading.Lock()
        self._records: list[dict[str, Any]] = []
        self._running = False
        self._state: AgentState | None = None
        self._error = ""

    def start(self, task: str, settings: Settings) -> bool:
        """Start a run in the background.

        Args:
            task: the user's task.
            settings: the limits, flags and model for this run.

        Returns:
            True when the run started; False when another run is active (nothing is changed).
        """
        # WHY: never wait. A second click, or a second browser tab, gets an answer at once.
        if not self._run_lock.acquire(blocking=False):
            return False
        with self._data_lock:
            self._records = []
            self._state = None
            self._error = ""
            self._running = True

        def job() -> None:
            try:
                state = self._run_fn(task, on_record=self._add_record, settings=settings)
                with self._data_lock:
                    self._state = state
            except Exception as exc:  # noqa: BLE001 - a thread must never die without a trace
                with self._data_lock:
                    self._error = trim(f"{type(exc).__name__}: {exc}", MAX_ERROR_CHARS)
            finally:
                # WHY: always release, so a crash in `run_fn` cannot block every later run.
                with self._data_lock:
                    self._running = False
                self._run_lock.release()

        try:
            self._start_thread(job)
        except Exception as exc:  # noqa: BLE001 - the thread could not start
            with self._data_lock:
                self._error = trim(f"{type(exc).__name__}: {exc}", MAX_ERROR_CHARS)
                self._running = False
            self._run_lock.release()
            return False
        return True

    def _add_record(self, record: dict[str, Any]) -> None:
        """Store one record from the run. This is `on_record`, called from the run thread."""
        with self._data_lock:
            self._records.append(record)

    def snapshot(self) -> RunSnapshot:
        """Return the current state of the run as an immutable copy."""
        with self._data_lock:
            return RunSnapshot(
                records=list(self._records),
                running=self._running,
                state=self._state,
                error=self._error,
            )


def live_settings(
    base: Settings,
    ui: UiSettings,
    *,
    model: str,
    enable_validation: bool,
    enable_retry: bool,
) -> Settings:
    """Build the settings of one UI run: the `.env` settings with three values replaced.

    Args:
        base: the settings from `.env`.
        ui: the UI settings; `ui.model_choices` lists the models the page may use.
        model: the model chosen on the page.
        enable_validation: the validation gate flag chosen on the page.
        enable_retry: the retry flag chosen on the page.

    Returns:
        A new `Settings`; every other limit equals `base`.

    Raises:
        ValueError: if `model` is not one of `ui.model_choices`.

    WHY: the browser must never pick a model name that is not in `.env` (Hard rule 2). The run's
    `run_end.config` records the chosen model and flags, so a UI run with the gate off is clearly
    marked in its trace.
    """
    if model not in ui.model_choices:
        raise ValueError("model is not one of the configured choices")
    return Settings.model_validate(
        {
            **base.model_dump(),
            "ollama_model": model,
            "enable_validation": enable_validation,
            "enable_retry": enable_retry,
        }
    )
