"""Tests for `evaluation/passes.py`: loading a pass, its metrics, the Wilson interval, grouping.

Covers AC1 to AC4 of `docs/specs/evaluation.md`. Pass folders live in `tmp_path`; manifests
use a dummy model name. Nothing here runs a task or calls an LLM.
"""

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from benchmark.runner import summarize
from evaluation.passes import (
    PassData,
    ReportError,
    group_passes,
    load_pass,
    pass_metrics,
    wilson_interval,
)
from tests.evaluation.helpers import make_data, write_pass


def test_load_pass_reads_pass_folder_and_refuses_bad_input_ac1(tmp_path: Path) -> None:
    """AC1: manifest, results, pauses and `complete` are read; bad input raises ReportError."""
    # WHY: every report reads passes through this one function; it must give the same data for
    # a good folder and refuse the same broken input every time.
    partial = make_data("p1", n_tasks=3, n_results=2, pauses=2)
    pass_dir = write_pass(tmp_path, partial)
    loaded = load_pass("p1", results_root=tmp_path)
    assert loaded.manifest["model"] == "dummy-model"
    assert [r["task_id"] for r in loaded.results] == ["t0", "t1"]
    assert loaded.pauses == 2
    assert loaded.complete is False

    write_pass(tmp_path, make_data("p2", n_tasks=3, n_results=3))
    assert load_pass("p2", results_root=tmp_path).complete is True

    for bad_id in ("../x", "Main"):
        with pytest.raises(ReportError, match="invalid pass id"):
            load_pass(bad_id, results_root=tmp_path)
    with pytest.raises(ReportError, match="no such pass"):
        load_pass("missing", results_root=tmp_path)

    results_file = pass_dir / "results.jsonl"
    results_file.write_text(results_file.read_text() + "{broken\n", encoding="utf-8")
    with pytest.raises(ReportError, match="line 3"):
        load_pass("p1", results_root=tmp_path)

    (pass_dir / "manifest.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ReportError, match="p1"):
        load_pass("p1", results_root=tmp_path)


def test_pass_metrics_matches_runner_summary_and_adds_sums_ac2() -> None:
    """AC2: headline numbers equal the runner's `summarize`; the extras are summed."""

    # WHY: the headline numbers must use the session 6 definitions exactly; only the extras
    # are new, and a missing counter must count as 0.
    def counters(calls: int, failed: int, val: int, tool: int, step: int, replans: int) -> Any:
        return {
            "tool_calls": calls,
            "failed_tool_calls": failed,
            "validation_errors": val,
            "tool_retries": tool,
            "step_retries": step,
            "replans": replans,
        }

    results: list[dict[str, Any]] = [
        {"task_id": "t0", "category": "calc", "success": True, "status": "done",
         "counters": counters(5, 1, 1, 2, 1, 0), "duration_ms": 1000},
        {"task_id": "t1", "category": "file", "success": False, "status": "failed",
         "error": "Boom", "counters": counters(3, 2, 2, 0, 3, 1), "duration_ms": 3000},
        {"task_id": "t2", "category": "calc", "success": True, "status": "done",
         "counters": counters(2, 0, 0, 0, 0, 0), "duration_ms": 2000},
        {"task_id": "t3", "category": "search", "success": False, "status": "failed",
         "counters": {}, "duration_ms": 4000},
    ]  # fmt: skip
    base = make_data("p1", n_tasks=4)
    data = dataclasses.replace(base, results=results)
    metrics = pass_metrics(data)
    expected = summarize(results, planned=4, infra_pauses=0)
    for key in ("success_rate", "by_category", "failed_tool_call_rate", "crashed"):
        assert metrics[key] == expected[key]
    assert metrics["validation_errors"] == 3
    assert metrics["tool_retries"] == 2
    assert metrics["step_retries"] == 4
    assert metrics["replans"] == 1
    assert metrics["runs_with_failed_call"] == 2
    assert metrics["median_duration_s"] == pytest.approx(2.5)
    assert metrics["fingerprint_short"] == "a" * 8


def test_wilson_interval_known_values_and_edges_ac3() -> None:
    """AC3: 15/30 matches the textbook interval; the edges stay inside 0..1; 0/0 is None."""
    # WHY: every success rate in the report is printed with this interval; a wrong formula
    # would make a small difference look meaningful.
    interval = wilson_interval(15, 30)
    assert interval is not None
    assert interval[0] == pytest.approx(0.3315, abs=0.001)
    assert interval[1] == pytest.approx(0.6685, abs=0.001)
    zero = wilson_interval(0, 30)
    full = wilson_interval(30, 30)
    assert zero is not None and zero[0] == 0
    assert full is not None and full[1] == 1
    assert wilson_interval(0, 0) is None


def test_group_passes_pools_sums_and_skips_incomplete_or_other_code_ac4() -> None:
    """AC4: repeats are pooled by sums; incomplete passes and other code are kept apart."""
    # WHY: averaging rates would weight a small pass like a big one, and pooling before and
    # after a fix would mix two different programs (roadmap 7).
    first = make_data(
        "a-1", n_tasks=2, n_success=1, tool_calls=2, failed=1, started_at="2026-10-17T10"
    )
    second = make_data(
        "a-2", n_tasks=2, n_success=0, tool_calls=8, failed=0, started_at="2026-10-17T11"
    )
    incomplete = make_data("a-3", n_tasks=2, n_results=1, started_at="2026-10-17T12")
    other_code = make_data(
        "b-1", n_tasks=2, fingerprint="sha256:" + "b" * 64, started_at="2026-10-17T13"
    )
    passes: list[PassData] = [second, other_code, incomplete, first]

    groups = group_passes(passes)

    assert [g.pass_ids for g in groups] == [["a-1", "a-2"], ["b-1"]]
    pooled = groups[0].metrics
    assert pooled["tool_calls"] == 10
    assert pooled["failed_tool_calls"] == 1
    assert pooled["successes"] == 1
    # WHY: the mean of the two rates would be 0.25; the pooled rate is 1/10.
    assert pooled["failed_tool_call_rate"] == pytest.approx(0.1)
    assert all("a-3" not in g.pass_ids for g in groups)
