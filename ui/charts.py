"""Plotly charts of the benchmark results.

Architecture layer: 9 (interface). No Streamlit import. Every function takes numbers the
evaluation code already computed (`pass_metrics`, `compare_groups`) and only draws them, so a
test can check each bar without a browser. A function returns None when there is nothing to
draw, and the page then says so.
"""

from typing import Any

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from evaluation.passes import PassData, pass_metrics
from evaluation.report import CATEGORY_ORDER
from src.console import HTML_COLOURS, SUCCESS

# WHY: "gate on" is the good side of the ablation, so it uses the success colour. "Off" is a
# neutral grey, not a role colour.
ON_COLOUR = HTML_COLOURS[SUCCESS]
OFF_COLOUR = "#9ca3af"
BAR_COLOUR = "#3b82f6"


def _percent(successes: int, tasks: int) -> float | None:
    """successes / tasks as a percentage, or None when there are no tasks."""
    return successes / tasks * 100 if tasks else None


def _error_bars(
    values: list[float | None], intervals: list[tuple[float, float] | None]
) -> dict[str, Any]:
    """Plotly asymmetric error bars (in percentage points) from Wilson intervals.

    WHY: with 30 tasks one task is 3.3 points. The bars show how far apart two rates must be
    before the difference means anything.
    """
    up, down = [], []
    for value, interval in zip(values, intervals, strict=True):
        if value is None or interval is None:
            up.append(0.0)
            down.append(0.0)
        else:
            up.append(max(0.0, interval[1] * 100 - value))
            down.append(max(0.0, value - interval[0] * 100))
    return {"type": "data", "symmetric": False, "array": up, "arrayminus": down}


def success_chart(passes: list[PassData]) -> go.Figure | None:
    """One bar per complete pass: the success rate in %, with the 95% Wilson interval.

    Args:
        passes: passes read with `load_pass`.

    Returns:
        A figure, or None when no pass is complete.

    WHY: an incomplete pass has a biased sample (the tasks run first are not random), so it is
    never drawn; the same rule as the report.
    """
    ids, rates, intervals, labels = [], [], [], []
    for data in passes:
        if not data.complete:
            continue
        metrics = pass_metrics(data)
        if metrics["success_rate"] is None:
            continue
        ids.append(data.pass_id)
        rates.append(metrics["success_rate"] * 100)
        intervals.append(metrics["success_ci"])
        labels.append(f"{metrics['successes']}/{metrics['tasks_done']} ({metrics['model']})")
    if not ids:
        return None
    figure = go.Figure(
        go.Bar(
            x=ids,
            y=rates,
            error_y=_error_bars(rates, intervals),
            text=[f"{rate:.1f}%" for rate in rates],
            hovertext=labels,
            marker_color=BAR_COLOUR,
        )
    )
    figure.update_layout(
        title="Task success rate per pass (95% Wilson interval)",
        yaxis={"title": "success (%)", "range": [0, 105]},
        xaxis={"title": "pass"},
    )
    return figure


def ablation_chart(rows: list[dict[str, Any]]) -> go.Figure | None:
    """Gate off against gate on: success rate and failed-tool-call rate, side by side.

    Args:
        rows: the comparison rows of `compare_groups`. Only rows of kind `ablation` are used.

    Returns:
        A figure, or None when there is no ablation row.
    """
    ablations = [row for row in rows if row.get("kind") == "ablation"]
    if not ablations:
        return None
    labels = [f"{row['off']} vs {row['on']}" for row in ablations]
    figure = make_subplots(
        rows=1, cols=2, subplot_titles=("Task success rate (%)", "Failed tool calls (%)")
    )
    for side, colour in (("off", OFF_COLOUR), ("on", ON_COLOUR)):
        success = [_percent(row[f"success_{side}"], row[f"tasks_{side}"]) for row in ablations]
        figure.add_trace(
            go.Bar(
                x=labels,
                y=success,
                name=f"gate {side}",
                marker_color=colour,
                legendgroup=side,
                error_y=_error_bars(success, [row[f"success_ci_{side}"] for row in ablations]),
            ),
            row=1,
            col=1,
        )
        failed = [
            None if row[f"failed_rate_{side}"] is None else row[f"failed_rate_{side}"] * 100
            for row in ablations
        ]
        figure.add_trace(
            go.Bar(
                x=labels,
                y=failed,
                name=f"gate {side}",
                marker_color=colour,
                legendgroup=side,
                showlegend=False,
            ),
            row=1,
            col=2,
        )
    figure.update_layout(title="Ablation: validation and retry off vs on", barmode="group")
    return figure


def model_chart(rows: list[dict[str, Any]]) -> go.Figure | None:
    """Success rate per category (and overall) for each model.

    Args:
        rows: the comparison rows of `compare_groups`. Only rows of kind `model` are used.

    Returns:
        A figure, or None when there is no model row.
    """
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") == "model":
            for group in row["groups"]:
                groups.setdefault(group["label"], group)
    if not groups:
        return None
    categories = ["all", *CATEGORY_ORDER]
    figure = go.Figure()
    for label, group in groups.items():
        rates: list[float | None] = [_percent(group["successes"], group["tasks"])]
        for category in CATEGORY_ORDER:
            entry = group["by_category"].get(category)
            rates.append(_percent(entry["successes"], entry["tasks"]) if entry else None)
        figure.add_trace(go.Bar(x=categories, y=rates, name=f"{group['model']} ({label})"))
    figure.update_layout(
        title="Model comparison: success rate (%)",
        barmode="group",
        yaxis={"range": [0, 105]},
    )
    return figure
