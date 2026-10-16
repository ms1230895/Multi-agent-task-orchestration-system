"""Tests for `ui/charts.py` (spec streamlit-ui, AC14 and AC15): the numbers inside the figures.

Passes are built in memory with the evaluation test helpers. No browser, no Streamlit.
"""

from typing import Any

import pytest

from evaluation.passes import group_passes, pass_metrics, wilson_interval
from evaluation.report import compare_groups
from tests.evaluation.helpers import make_data
from ui.charts import ablation_chart, model_chart, success_chart


def test_success_chart_draws_complete_passes_with_wilson_error_bars_ac14() -> None:
    """AC14: bar = success rate in %, error bars = Wilson interval, incomplete pass absent."""
    # WHY: the chart must show exactly the numbers of the report; an incomplete pass has a
    # biased sample and must never be drawn.
    full = make_data("full-1", n_tasks=10, n_success=7)
    other = make_data("full-2", n_tasks=20, n_success=5)
    partial = make_data("partial-1", n_tasks=4, n_results=2)

    figure = success_chart([full, partial, other])

    assert figure is not None
    bar = figure.data[0]
    assert list(bar.x) == ["full-1", "full-2"]
    for index, data in enumerate((full, other)):
        metrics = pass_metrics(data)
        low, high = wilson_interval(metrics["successes"], metrics["tasks_done"]) or (0, 0)
        rate = metrics["success_rate"] * 100
        assert bar.y[index] == pytest.approx(rate)
        assert bar.error_y.array[index] == pytest.approx(high * 100 - rate)
        assert bar.error_y.arrayminus[index] == pytest.approx(rate - low * 100)
    assert bar.y[0] == pytest.approx(70.0)
    assert success_chart([partial]) is None
    assert success_chart([]) is None


def test_ablation_and_model_charts_plot_row_rates_and_return_none_without_rows_ac15() -> None:
    """AC15: each chart plots its own kind of row, and is None when there is none."""
    # WHY: the dashboard says "nothing to compare" only when the chart is None; a chart that
    # draws other kinds of rows, or wrong rates, would mislead the CV numbers.
    off = make_data(
        "off-1", validation=False, retry=False, n_tasks=10, n_success=5, tool_calls=10, failed=4
    )
    on = make_data("on-1", n_tasks=10, n_success=8, tool_calls=10, failed=1)
    ablation_rows, _ = compare_groups(group_passes([off, on]))
    first = make_data("a-1", model="model-a", n_tasks=10, n_success=6)
    second = make_data("b-1", model="model-b", n_tasks=10, n_success=9)
    model_rows, _ = compare_groups(group_passes([first, second]))

    ablation = ablation_chart(ablation_rows)
    models = model_chart(model_rows)

    assert ablation is not None
    traces: dict[tuple[str, int], Any] = {
        (trace.name, 1 if trace.xaxis in (None, "x") else 2): trace for trace in ablation.data
    }
    assert list(traces[("gate off", 1)].y) == pytest.approx([50.0])
    assert list(traces[("gate on", 1)].y) == pytest.approx([80.0])
    assert list(traces[("gate off", 2)].y) == pytest.approx([40.0])
    assert list(traces[("gate on", 2)].y) == pytest.approx([10.0])

    assert models is not None
    assert [trace.name for trace in models.data] == ["model-a (a-1)", "model-b (b-1)"]
    assert list(models.data[0].x) == ["all", "calc", "search", "file", "multi-tool"]
    assert models.data[0].y[:2] == pytest.approx((60.0, 60.0))
    assert models.data[0].y[2] is None
    assert models.data[1].y[0] == pytest.approx(90.0)

    other_rows: list[dict[str, Any]] = [{"kind": "fix"}]
    for rows in ([], other_rows, model_rows):
        assert ablation_chart(rows) is None
    for rows in ([], other_rows, ablation_rows):
        assert model_chart(rows) is None
