"""Tests for `ui/data.py` (spec streamlit-ui, AC12 and AC13): trace list and pass list.

Everything is built in `tmp_path`; the real `logs/` and `benchmark/results/` are never read.
"""

import json
from pathlib import Path
from typing import Any

from tests.evaluation.helpers import make_data, write_pass
from ui.data import list_passes, list_traces


def _write_trace(logs: Path, name: str, records: list[dict[str, Any]]) -> None:
    """Write `logs/<name>` with one JSON record per line."""
    logs.mkdir(parents=True, exist_ok=True)
    (logs / name).write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_list_traces_mixed_folder_gives_four_rows_newest_first_ac12(tmp_path: Path) -> None:
    """AC12: done, failed, incomplete and unreadable rows; odd file names skipped; no raise."""
    # WHY: one broken or half-written trace must not hide the others or stop the page.
    logs = tmp_path / "logs"
    run_end = {"task": "t1", "counters": {"tool_calls": 2, "bad": "x", "flag": True}}
    _write_trace(
        logs,
        "20261017-100000-aaaaaa.jsonl",
        [
            {"event": "plan_created", "model": "m", "data": {}},
            {"event": "run_end", "data": {**run_end, "status": "done", "duration_ms": 1234}},
        ],
    )
    _write_trace(
        logs,
        "20261017-090000-bbbbbb.jsonl",
        [{"event": "run_end", "model": "m", "data": {"status": "failed", "task": "t2"}}],
    )
    _write_trace(
        logs, "20261017-080000-cccccc.jsonl", [{"event": "plan_created", "model": "m", "data": {}}]
    )
    (logs / "20261017-070000-dddddd.jsonl").write_text("{not json\n", encoding="utf-8")
    # NOTE: these names are not valid run ids (a dot or a space), so they must be skipped.
    _write_trace(logs, "bad.name.jsonl", [{"event": "run_end", "data": {"status": "done"}}])
    _write_trace(logs, "not a run.jsonl", [{"event": "run_end", "data": {"status": "done"}}])
    (logs / "notes.txt").write_text("hello", encoding="utf-8")

    rows = list_traces(logs)

    assert [row.run_id for row in rows] == [
        "20261017-100000-aaaaaa",
        "20261017-090000-bbbbbb",
        "20261017-080000-cccccc",
        "20261017-070000-dddddd",
    ]
    assert [row.status for row in rows] == ["done", "failed", "incomplete", "unreadable"]
    assert (rows[0].model, rows[0].task, rows[0].duration_ms) == ("m", "t1", 1234)
    assert rows[0].counters == {"tool_calls": 2}
    assert list_traces(tmp_path / "missing") == []


def test_list_passes_valid_and_broken_folder_returns_pass_and_error_ac13(tmp_path: Path) -> None:
    """AC13: a broken pass is listed with its error and does not hide the valid one."""
    # WHY: a half-written pass folder must not make the whole dashboard fail.
    results = tmp_path / "results"
    write_pass(results, make_data("good-1", n_tasks=2))
    broken = results / "broken-1"
    broken.mkdir()
    (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    passes, errors = list_passes(results)

    assert [data.pass_id for data in passes] == ["good-1"]
    assert len(errors) == 1
    assert errors[0][0] == "broken-1"
    assert errors[0][1] != ""
    assert list_passes(tmp_path / "missing") == ([], [])
