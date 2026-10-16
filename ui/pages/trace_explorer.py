"""Page: replay any past run from its trace file in `logs/`.

Architecture layer: 9 (interface). Read-only: it makes no LLM call and cannot start a run.
"""

from typing import Any

import streamlit as st

from src.console import sanitize
from src.logger import trim
from ui.data import TraceSummary, list_traces, load_trace
from ui.render import timeline_html

STATUS_ORDER = ("done", "failed", "incomplete", "unreadable")


@st.cache_data(ttl=10, show_spinner=False)
def _traces() -> list[TraceSummary]:
    """The trace list, cached for a few seconds so a click does not re-read 100 files."""
    return list_traces()


@st.cache_data(ttl=60, show_spinner=False)
def _trace(run_id: str) -> list[dict[str, Any]] | None:
    """One trace, cached by run id (a finished trace never changes)."""
    return load_trace(run_id)


def _label(summary: TraceSummary) -> str:
    """The text of one entry in the run picker."""
    task = trim(sanitize(summary.task).replace("\n", " "), 60)
    return f"{summary.run_id} · {summary.status} · {summary.model or '?'} · {task}"


def _run_end(records: list[dict[str, Any]]) -> dict[str, Any]:
    """The `data` of the last `run_end` record, or an empty dict."""
    for record in reversed(records):
        if record.get("event") == "run_end" and isinstance(record.get("data"), dict):
            return record["data"]
    return {}


def render() -> None:
    """Draw the trace explorer."""
    st.title("Trace explorer")
    summaries = _traces()
    if not summaries:
        st.info("No traces yet. Run a task, then come back.")
        return

    # STEP 1: filters.
    models = sorted({item.model for item in summaries if item.model})
    left, right = st.columns(2)
    chosen_models = left.multiselect("Model", models, default=models)
    chosen_status = right.multiselect("Status", STATUS_ORDER, default=list(STATUS_ORDER))
    shown = [
        item
        for item in summaries
        if item.status in chosen_status and (not item.model or item.model in chosen_models)
    ]
    st.caption(f"{len(shown)} of {len(summaries)} traces")
    if not shown:
        st.info("No trace matches these filters.")
        return

    # STEP 2: pick a run and load it.
    summary = st.selectbox("Run", shown, format_func=_label)
    if summary is None:
        return
    records = _trace(summary.run_id)
    if not records:
        st.error("This trace could not be read (missing, too big, or not valid).")
        return

    # STEP 3: the run summary, from `run_end`.
    # WHY: task, answer and config are shown with `st.text` and `st.json`, which do no Markdown
    # or HTML, because they come from the LLM, web pages and files.
    end = _run_end(records)
    raw_config = end.get("config")
    config: dict[str, Any] = raw_config if isinstance(raw_config, dict) else {}
    duration = end.get("duration_ms")
    columns = st.columns(4)
    columns[0].metric("Status", summary.status)
    columns[1].metric("Model", summary.model or "?")
    columns[2].metric(
        "Duration", f"{duration / 1000:.1f} s" if isinstance(duration, int | float) else "?"
    )
    columns[3].metric(
        "Validation / retry",
        f"{config.get('enable_validation', '?')} / {config.get('enable_retry', '?')}",
    )
    st.text(sanitize(f"Task: {end.get('task', summary.task)}"))
    if end:
        st.text(sanitize(f"Final answer: {end.get('final_answer', '')}"))
        with st.expander("Counters"):
            st.json(end.get("counters", {}))

    # STEP 4: the timeline, one event at a time if wanted.
    st.subheader("Timeline")
    count = len(records)
    upto = count
    if count > 1:
        upto = st.slider("Show events up to", 1, count, count)
    st.html(timeline_html(records[:upto]))
    with st.expander("Raw records"):
        st.json(records[:upto], expanded=False)
