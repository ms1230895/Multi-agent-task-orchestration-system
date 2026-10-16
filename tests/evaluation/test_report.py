"""Tests for `evaluation/report.py`: comparisons, trace count, Markdown block, `--write`, CLI.

Covers AC5 to AC11 of `docs/specs/evaluation.md`. Passes are built in memory or written to
`tmp_path`; dummy model names only. `main()` reads the real results folder by default, so the
tests wrap `load_pass` and `render_markdown` to point at `tmp_path`, and patch the runner and
the LLM factory to raise, which proves nothing is run.
"""

import dataclasses
from pathlib import Path
from typing import Any, NoReturn

import pytest

import benchmark.runner
import evaluation.passes as passes_module
import src.config
from evaluation import report
from evaluation.passes import PassData, ReportError, group_passes
from evaluation.report import (
    END_MARKER,
    START_MARKER,
    compare_groups,
    count_traces,
    render_markdown,
    write_block,
)
from tests.evaluation.helpers import make_data, write_pass, write_trace

EARLY = "2026-10-17T10:00:00.000Z"
LATE = "2026-10-17T11:00:00.000Z"
OTHER_FINGERPRINT = "sha256:" + "b" * 64


def _rows_of(*passes: PassData) -> tuple[list[dict[str, Any]], list[str]]:
    """Group the passes and compare the groups."""
    return compare_groups(group_passes(list(passes)))


def test_ablation_row_reduction_cases_ac5(tmp_path: Path) -> None:
    """AC5: one ablation row; reduction 0.2, None (n/a) for 0 off failures, negative shown."""
    # WHY: the CV number "failed calls reduced by X%" comes from this row; an increase must
    # never be hidden and a zero baseline must never claim a reduction.
    no_logs = tmp_path / "no-logs"
    cases = [
        (10, 8, 0.2, "20.0%"),
        (0, 0, None, "+0.0 | n/a |"),
        (8, 12, -0.5, "the gate added failed calls"),
    ]
    for off_failed, on_failed, reduction, text_part in cases:
        off = make_data(
            "off-1",
            validation=False,
            retry=False,
            tool_calls=100,
            failed=off_failed,
            started_at=EARLY,
        )
        on = make_data("on-1", tool_calls=100, failed=on_failed, started_at=LATE)
        rows, not_compared = _rows_of(off, on)
        assert [r["kind"] for r in rows] == ["ablation"]
        assert not_compared == []
        assert rows[0]["off"] == "off-1"
        if reduction is None:
            assert rows[0]["relative_reduction"] is None
        else:
            assert rows[0]["relative_reduction"] == pytest.approx(reduction)
        if text_part is not None:
            assert text_part in render_markdown([off, on], logs_dir=no_logs)


def test_no_ablation_row_when_more_than_the_flags_differ_ac6() -> None:
    """AC6: other differences give no ablation row, and a "not compared" line names them."""
    # WHY: a missing ablation row must never be silent; it usually means `.env` changed by
    # mistake between the off pass and the on pass.
    off = make_data("off-1", validation=False, retry=False, started_at=EARLY)
    cases = [
        (make_data("on-1", fingerprint=OTHER_FINGERPRINT, started_at=LATE), "code_fingerprint"),
        (make_data("on-1", extra_config={"max_replans": 2}, started_at=LATE), "config.max_replans"),
        (
            make_data("on-1", validation=True, retry=False, started_at=LATE),
            "config.enable_validation",
        ),
    ]
    for on, expected in cases:
        rows, not_compared = _rows_of(off, on)
        assert [r for r in rows if r["kind"] == "ablation"] == []
        assert len(not_compared) == 1
        assert expected in not_compared[0]
        assert "off-1" in not_compared[0] and "on-1" in not_compared[0]
    # A different model (plus the flags) is never an ablation row either.
    other_model = make_data("on-1", model="other-model", started_at=LATE)
    assert [r for r in _rows_of(off, other_model)[0] if r["kind"] == "ablation"] == []
    # Spec 5.3: a pair with a different model gets no "not compared" line either.
    assert _rows_of(off, other_model)[1] == []


def test_model_row_and_fix_row_with_earlier_group_as_before_ac7() -> None:
    """AC7: model-only difference gives a model row; fingerprint-only gives a fix row."""
    # WHY: "before" must be the earlier group even if the list arrives in another order,
    # or a fix would look like a regression.
    first = make_data("m1-a", model="m1", started_at=EARLY)
    second = make_data("m2-a", model="m2", started_at=LATE)
    fixed = make_data(
        "m1-b", model="m1", fingerprint=OTHER_FINGERPRINT, started_at="2026-10-17T12:00:00.000Z"
    )
    groups = group_passes([first, second, fixed])

    rows, _ = compare_groups(list(reversed(groups)))

    assert sorted(r["kind"] for r in rows) == ["fix", "model"]
    fix = next(r for r in rows if r["kind"] == "fix")
    assert [(s["side"], s["label"]) for s in fix["sides"]] == [
        ("before", "m1-a"),
        ("after", "m1-b"),
    ]
    model = next(r for r in rows if r["kind"] == "model")
    assert {g["model"] for g in model["groups"]} == {"m1", "m2"}


def test_count_traces_counts_only_run_end_in_jsonl_files_ac8(tmp_path: Path) -> None:
    """AC8: two finished traces count; an unfinished trace and a non-.jsonl file do not."""
    # WHY: "traced runs" is a CV number; an unfinished run or a stray file must not inflate it.
    logs = tmp_path / "logs"
    write_trace(logs, "run-a", [("run_start", {}), ("run_end", {})])
    write_trace(logs, "run-b", [("run_start", {}), ("run_end", {})])
    write_trace(logs, "run-c", [("run_start", {})])
    (logs / "notes.txt").write_text('{"event": "run_end", "data": {}}\n', encoding="utf-8")
    assert count_traces(logs) == 2
    assert count_traces(tmp_path / "missing") == 0


def test_render_markdown_is_deterministic_with_one_decimal_and_intervals_ac9(
    tmp_path: Path,
) -> None:
    """AC9: same text twice; one-decimal rates; intervals; incomplete label; fixed note."""
    # WHY: `docs/results.md` is regenerated, so the block must not change between two runs on
    # the same files, and a rate must always travel with its interval.
    full = make_data("full-1", n_tasks=30, n_success=19, tool_calls=40, failed=4)
    partial = make_data("part-1", n_tasks=30, n_results=17, n_success=5)
    logs = tmp_path / "no-logs"

    text = render_markdown([full, partial], logs_dir=logs)

    assert render_markdown([full, partial], logs_dir=logs) == text
    assert "19/30 (63.3%) [" in text
    assert "5/17 (29.4%) [" in text
    assert "10.0%" in text
    assert "incomplete 17/30" in text
    assert (
        "30 tasks per pass: one task = 3.3 percentage points. Intervals are 95% Wilson intervals."
        in text
    )


def test_write_block_creates_replaces_between_markers_and_refuses_bad_files_ac10(
    tmp_path: Path,
) -> None:
    """AC10: create, replace only the block, refuse four broken marker sets unchanged."""
    # WHY: the hand-written analysis around the block must never be touched, and a broken
    # marker set means the file was edited by mistake.
    created = tmp_path / "sub" / "results.md"
    write_block(created, "BLOCK")
    assert (
        created.read_text(encoding="utf-8") == f"# Results\n\n{START_MARKER}\nBLOCK\n{END_MARKER}\n"
    )

    before, after = "intro text\n\n", "\n\noutro text\n"
    replaced = tmp_path / "doc.md"
    replaced.write_text(f"{before}{START_MARKER}\nOLD\n{END_MARKER}{after}", encoding="utf-8")
    write_block(replaced, "NEW")
    assert (
        replaced.read_text(encoding="utf-8") == f"{before}{START_MARKER}\nNEW\n{END_MARKER}{after}"
    )

    broken = {
        "no marker": "just text\n",
        "one marker": f"text\n{START_MARKER}\nold\n",
        "two starts": f"{START_MARKER}\na\n{START_MARKER}\nb\n{END_MARKER}\n",
        "end first": f"{END_MARKER}\nold\n{START_MARKER}\n",
    }
    for name, content in broken.items():
        path = tmp_path / f"{name.replace(' ', '-')}.md"
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ReportError):
            write_block(path, "NEW")
        assert path.read_text(encoding="utf-8") == content, name


def test_report_main_exit_codes_and_nothing_is_run_ac11(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC11: 0 and the block for good passes; 2 for bad ids or markerless files; no run starts."""
    # WHY: the report is read-only. If it ever called the runner or the LLM factory, a report
    # would spend quota; the patched functions raise so such a call fails the test.
    root = tmp_path / "results"
    monkeypatch.setattr(passes_module, "OUTPUT_ROOT", tmp_path)
    write_pass(root, make_data("a", validation=False, retry=False, started_at=EARLY))
    write_pass(root, make_data("b", started_at=LATE))
    real_load, real_render = report.load_pass, report.render_markdown
    monkeypatch.setattr(report, "load_pass", lambda pass_id: real_load(pass_id, results_root=root))
    monkeypatch.setattr(
        report, "render_markdown", lambda passes: real_render(passes, logs_dir=tmp_path / "logs")
    )

    def boom(*args: Any, **kwargs: Any) -> NoReturn:
        raise AssertionError("the report must never run a task or build an LLM")

    monkeypatch.setattr(benchmark.runner, "run_task", boom)
    monkeypatch.setattr(src.config, "get_llm", boom)

    assert report.main(["--passes", "a,b"]) == 0
    assert "### Passes" in capsys.readouterr().out
    for refused in ("../x", "zz", "a,a"):
        assert report.main(["--passes", refused]) == 2
    markerless = tmp_path / "doc.md"
    markerless.write_text("no markers here\n", encoding="utf-8")
    assert report.main(["--passes", "a,b", "--write", str(markerless)]) == 2
    assert markerless.read_text(encoding="utf-8") == "no markers here\n"
    target = tmp_path / "new" / "results.md"
    assert report.main(["--passes", "a,b", "--write", str(target)]) == 0
    assert START_MARKER in target.read_text(encoding="utf-8")


def test_write_block_keeps_exact_bytes_and_write_paths_are_limited_to_md_in_docs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review findings 3 and 2: CRLF text outside the markers survives; unsafe paths are refused."""
    # WHY: the hand-written text around the block must stay byte for byte (AC10), and a wrong
    # `--write CLAUDE.md` must never overwrite a project file.
    doc = tmp_path / "docs" / "results.md"
    doc.parent.mkdir()
    before, after = b"intro\r\n", b"\r\ntail\r\n"
    doc.write_bytes(before + START_MARKER.encode() + b"\r\nold\r\n" + END_MARKER.encode() + after)
    report.write_block(doc, "NEW")
    written = doc.read_bytes()
    assert written.startswith(before + START_MARKER.encode())
    assert written.endswith(END_MARKER.encode() + after)
    assert b"NEW" in written and b"old" not in written
    with pytest.raises(ReportError):
        report.write_block(tmp_path / "docs", "NEW")  # a folder is a clean refusal, not OSError

    monkeypatch.setattr(passes_module, "OUTPUT_ROOT", tmp_path / "docs")
    write_pass(tmp_path / "results", make_data("a"))
    real_load = report.load_pass
    monkeypatch.setattr(
        report, "load_pass", lambda pid: real_load(pid, results_root=tmp_path / "results")
    )
    monkeypatch.setattr(report, "render_markdown", lambda passes: "block")
    for outside in (tmp_path / "CLAUDE.md", tmp_path / "docs" / "notes.txt"):
        assert report.main(["--passes", "a", "--write", str(outside)]) == 2
        assert not outside.exists()


def test_trace_coverage_counts_only_complete_traces_and_refuses_odd_run_ids(
    tmp_path: Path,
) -> None:
    """Review finding 2: one `run_end`, last; bad ids and broken traces never count."""
    # WHY: "traces across 100+ runs" is only true if each result has one complete trace; and a
    # run id from a committed file must not make the tool read a file outside `logs/`.
    logs = tmp_path / "logs"
    end = ("run_end", {"status": "done"})
    plan = ("plan_created", {"steps": ["a"]})
    write_trace(logs, "good", [plan, end])
    write_trace(logs, "two-ends", [plan, end, end])
    write_trace(logs, "end-not-last", [end, plan])
    write_trace(logs, "empty", [])
    (tmp_path / "outside.jsonl").write_text('{"event": "run_end", "data": {}}\n', encoding="utf-8")
    ids = ["good", "two-ends", "end-not-last", "empty", "missing", "../outside"]
    data = make_data("p1", n_tasks=len(ids))
    data = dataclasses.replace(
        data, results=[{"task_id": f"t{i}", "run_id": r} for i, r in enumerate(ids)]
    )
    assert report.trace_coverage(data, logs) == (1, 6)
