"""Page: the benchmark results as tables and Plotly charts.

Architecture layer: 9 (interface). Read-only: the numbers come from the evaluation code, so
they are the same as in `docs/results.md`.
"""

import streamlit as st

# WHY: these two are the report's own wording for a reduction and for the "one task = 3.3
# points" note. Importing them (as `evaluation/` imports runner helpers, D42) keeps the page and
# `docs/results.md` from saying different things. A test imports them, so a rename fails loudly.
from evaluation.passes import PassData, group_passes, pass_metrics
from evaluation.report import POINTS_NOTE, _reduction_text, compare_groups, count_traces
from ui.charts import ablation_chart, model_chart, success_chart
from ui.data import list_passes


@st.cache_data(ttl=10, show_spinner=False)
def _passes() -> tuple[list[PassData], list[tuple[str, str]]]:
    """The passes on disk, cached for a few seconds."""
    return list_passes()


@st.cache_data(ttl=30, show_spinner=False)
def _traced_runs() -> int:
    """The number of `run_end` events in `logs/`."""
    return count_traces()


def _pass_row(data: PassData) -> dict[str, object]:
    """One table row for a pass."""
    metrics = pass_metrics(data)
    rate = metrics["success_rate"]
    failed_rate = metrics["failed_tool_call_rate"]
    failed = f"{metrics['failed_tool_calls']}/{metrics['tool_calls']}"
    return {
        "pass": data.pass_id,
        "model": metrics["model"],
        "validation": metrics["enable_validation"],
        "retry": metrics["enable_retry"],
        "tasks": f"{metrics['tasks_done']}/{metrics['tasks_planned']}",
        "complete": "yes" if metrics["complete"] else "incomplete",
        "success": "n/a" if rate is None else f"{metrics['successes']} ({rate * 100:.1f}%)",
        "failed calls": "n/a" if failed_rate is None else f"{failed} ({failed_rate * 100:.1f}%)",
        "step retries": metrics["step_retries"],
        "replans": metrics["replans"],
    }


def render() -> None:
    """Draw the dashboard."""
    st.title("Benchmark dashboard")
    passes, broken = _passes()
    for pass_id, error in broken:
        st.warning(f"Pass `{pass_id}` could not be read: {error}")
    if not passes:
        st.info("No benchmark passes yet. Run `python -m benchmark.runner --pass <pass_id>`.")
        return

    # STEP 1: which passes to show.
    # WHY: a small smoke pass (3 tasks) next to the 30-task passes would look like a 100%
    # result. By default only the passes with the largest task list are selected; the rest stay
    # one click away.
    full_size = max(len(data.manifest["task_ids"]) for data in passes)
    ids = [data.pass_id for data in passes]
    default = [d.pass_id for d in passes if len(d.manifest["task_ids"]) == full_size]
    chosen = st.multiselect("Passes", ids, default=default)
    selected = [data for data in passes if data.pass_id in chosen]
    if not selected:
        st.info("Choose at least one pass.")
        return
    st.table([_pass_row(data) for data in selected])
    st.caption(POINTS_NOTE)

    # STEP 2: success per pass.
    figure = success_chart(selected)
    if figure is None:
        st.info("No complete pass selected, so there is nothing to draw.")
    else:
        st.plotly_chart(figure)

    # STEP 3: comparisons. Only pairs that differ in exactly one thing are compared.
    rows, not_compared = compare_groups(group_passes(selected))
    st.subheader("Ablation: validation and retry off vs on")
    ablation = ablation_chart(rows)
    if ablation is None:
        st.info("Select one pass with the gate off and one with it on (same model, same code).")
    else:
        st.plotly_chart(ablation)
        for row in (r for r in rows if r["kind"] == "ablation"):
            st.write(
                f"`{row['off']}` vs `{row['on']}`: relative reduction of failed tool calls: "
                f"**{_reduction_text(row['relative_reduction'])}**"
            )

    st.subheader("Model comparison")
    models = model_chart(rows)
    if models is None:
        st.info("Select passes of two different models with the same settings.")
    else:
        st.plotly_chart(models)

    for line in not_compared:
        st.caption(f"Not compared: {line}")

    # STEP 4: the trace count.
    st.metric("Traced runs in logs/", _traced_runs())
    st.caption("Counts every `run_end` in `logs/`, including runs started from this app.")
