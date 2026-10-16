"""Page: run one task live and watch the events appear.

Architecture layer: 9 (interface). This is the only page that can start a real run, so it is
only added to the app in live mode, and it checks the mode again itself. It holds no agent
logic: `RunManager` calls `run_task`, and this page only draws the records it collects.
"""

import streamlit as st

from src.config import ConfigError, get_settings, get_ui_settings
from src.console import ERROR, SUCCESS, sanitize
from src.state import MAX_TASK_CHARS
from ui.live import RunManager, RunSnapshot, live_settings
from ui.render import line_html, timeline_html


@st.cache_resource
def _manager() -> RunManager:
    """The one run manager of this server process.

    WHY: `st.cache_resource` is shared by every browser tab, so there is one run at a time per
    server, and a second tab can watch the run of the first.
    """
    return RunManager()


def _draw(manager: RunManager, polling: bool) -> None:
    """Draw the timeline and, when the run is over, the result.

    Args:
        manager: the run manager.
        polling: True when this fragment re-runs every second.

    WHY: this runs as a fragment, so only this part of the page is redrawn each second and the
    form above keeps its state.
    """
    snapshot = manager.snapshot()
    if snapshot.running:
        st.caption(f"Running... {len(snapshot.records)} events so far")
    elif polling:
        # WHY: the run just ended. A full re-run makes the page stop polling and re-enables
        # the Run button.
        st.rerun()
    _draw_result(snapshot)


def _draw_result(snapshot: RunSnapshot) -> None:
    """Draw the events, then the final answer (green when done, red when failed)."""
    if snapshot.records:
        st.html(timeline_html(snapshot.records))
    elif not snapshot.running and snapshot.state is None and not snapshot.error:
        st.caption("No run yet.")
        return

    # WHY: the answer and the error are LLM or exception text, so they are escaped and shown
    # with `st.html`, never as Markdown.
    if snapshot.error:
        st.html(line_html(sanitize(f"Run could not start: {snapshot.error}"), ERROR))
    state = snapshot.state
    if state is not None:
        done = state["status"] == "done"
        st.html(
            line_html(
                sanitize(f"Final answer: {state['final_answer']}"), SUCCESS if done else ERROR
            )
        )
        st.text(f"Run id: {state['run_id']}\nTrace: logs/{state['run_id']}.jsonl")
        with st.expander("Counters"):
            st.json(
                next(
                    (
                        r["data"].get("counters", {})
                        for r in reversed(snapshot.records)
                        if r.get("event") == "run_end" and isinstance(r.get("data"), dict)
                    ),
                    {},
                )
            )


def render() -> None:
    """Draw the Run page."""
    st.title("Run a task")

    # STEP 1: check the mode again.
    # WHY: defence in depth. The app only lists this page in live mode, but a run costs quota,
    # so the page does not trust that alone.
    try:
        ui_settings = get_ui_settings()
        base = get_settings()
    except ConfigError as error:
        st.error(str(error))
        return
    if not ui_settings.allow_live_runs or not ui_settings.model_choices:
        st.error("Live runs are off. Set UI_ALLOW_LIVE_RUNS=true in .env.")
        return

    # STEP 2: the form.
    manager = _manager()
    busy = manager.snapshot().running
    st.warning(
        "A run calls the Ollama model and uses your quota. Do not run a benchmark pass at the "
        "same time: both use the `workspace/` folder."
    )
    task = st.text_area("Task", max_chars=MAX_TASK_CHARS, height=120, disabled=busy)
    models = list(ui_settings.model_choices)
    model = st.selectbox(
        "Model",
        models,
        index=models.index(base.ollama_model) if base.ollama_model in models else 0,
        disabled=busy,
    )
    left, right = st.columns(2)
    validation = left.checkbox(
        "Validate tool arguments", value=base.enable_validation, disabled=busy
    )
    retry = right.checkbox("Retry transient tool errors", value=base.enable_retry, disabled=busy)
    st.caption(
        f"Other limits come from .env: retries {base.max_retries}, replans {base.max_replans}, "
        f"tool iterations {base.max_tool_iterations}."
    )

    # STEP 3: start a run.
    if st.button("Run", type="primary", disabled=busy or not task.strip()):
        try:
            settings = live_settings(
                base,
                ui_settings,
                model=str(model),
                enable_validation=validation,
                enable_retry=retry,
            )
        except ValueError as error:
            st.error(str(error))
        else:
            if not manager.start(task.strip(), settings):
                st.warning("A run is already in progress.")

    # STEP 4: the live timeline.
    # WHY: take the snapshot after the click, so a run that has just started already polls.
    polling = manager.snapshot().running
    st.fragment(_draw, run_every=1 if polling else None)(manager, polling)
