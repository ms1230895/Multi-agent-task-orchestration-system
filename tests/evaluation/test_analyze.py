"""Tests for `evaluation/analyze.py`: outcome classes, failed calls, trace counts, digests, CLI.

Covers AC12 to AC20 of `docs/specs/evaluation.md`. Results and traces are scripted in
`tmp_path`. `main()` reads the real results and logs folders by default, so the CLI test wraps
`load_pass` and `analyze_pass` to point at `tmp_path`. No LLM and no network.
"""

import dataclasses
from pathlib import Path
from typing import Any

import pytest

import evaluation.passes as passes_module
from benchmark.runner import PROJECT_ROOT
from evaluation import analyze
from evaluation.analyze import analyze_pass, classify, error_kind
from tests.evaluation.helpers import make_data, pass_of, write_pass, write_trace

LIMIT_TEXT = "Step not completed: tool iteration limit reached."
PLAN: list[dict[str, Any]] = [{"event": "plan_created", "data": {"steps": ["do it"]}}]


def _call(tool: str, args: dict[str, Any], *, ok: bool = False, error: str = "boom") -> Any:
    """A `tool_call` trace event as an `(event, data)` pair."""
    return ("tool_call", {"tool": tool, "args": args, "ok": ok, "error": error})


def _verdict(verdict: str, output: str = "", feedback: str = "") -> Any:
    """A `critic_verdict` trace event as an `(event, data)` pair."""
    return (
        "critic_verdict",
        {"step": 1, "verdict": verdict, "output": output, "feedback": feedback},
    )


def _result(task_id: str, run_id: str, **extra: Any) -> dict[str, Any]:
    """A failed result line; `extra` overrides any field."""
    base: dict[str, Any] = {
        "task_id": task_id,
        "run_id": run_id,
        "category": "file",
        "success": False,
        "status": "failed",
        "checks_passed": False,
    }
    return {**base, **extra}


def test_classify_gives_each_of_the_eight_classes_in_rule_order_ac12() -> None:
    """AC12: each class has a scripted result, and earlier rules win over later ones."""
    # WHY: the class says how a task failed; "gave up with the right answer" and "wrong answer"
    # need different fixes, so a wrong rule order would send the fix to the wrong place.
    cases: list[tuple[str, dict[str, Any], list[dict[str, Any]] | None]] = [
        ("success", {"success": True, "error": "Boom", "status": "failed"}, None),
        ("infra", {"success": False, "error": "ConnectionError: down"}, None),
        ("crash", {"success": False, "error": "ValueError: x"}, None),
        ("wrong_answer", {"success": False, "status": "done"}, None),
        ("planner_failed", {"success": False, "status": "failed", "checks_passed": True}, []),
        (
            "gave_up_right_answer",
            {"success": False, "status": "failed", "checks_passed": True},
            PLAN,
        ),
        ("gave_up", {"success": False, "status": "failed", "checks_passed": False}, PLAN),
        ("gave_up", {"success": False, "status": "failed"}, None),
        ("other", {"success": False, "status": "weird"}, None),
    ]
    for expected, result, trace in cases:
        assert classify(result, trace) == expected, (expected, result)


def test_error_kind_labels_and_failed_calls_group_by_tool_and_kind_ac13(tmp_path: Path) -> None:
    """AC13: labels are stable; failed calls are grouped by tool and kind, highest count first."""
    # WHY: equal failures must land in one row, or the top failure cause is split and hidden.
    assert error_kind("file not found: notes2.txt") == "file not found"
    assert error_kind("transient: timed out after 3 s") == "transient"
    assert error_kind("no such file 12") == "no such file ##"
    assert len(error_kind("x" * 200)) <= 60

    logs = tmp_path / "logs"
    write_trace(logs, "r1", [
        _call("calculator", {"e": "1/0"}, error="Division by zero: 1/0"),
        _call("read_file", {"path": "a"}, error="file not found: a.txt"),
        _call("read_file", {"path": "b"}, error="File not found: b.txt"),
        _call("read_file", {"path": "c"}, ok=True, error=""),
    ])  # fmt: skip
    write_trace(logs, "r2", [_call("read_file", {"path": "c"}, error="file not found: c.txt")])
    data = pass_of([_result("t1", "r1", success=True), _result("t2", "r2", success=True)])

    failed = analyze_pass(data, logs)["failed_tool_calls"]

    assert [(f["tool"], f["kind"], f["count"]) for f in failed] == [
        ("read_file", "file not found", 3),
        ("calculator", "division by zero", 1),
    ]
    assert failed[0]["example_run"] == "r1"


def test_repeated_failing_calls_count_same_args_in_any_key_order_ac14(tmp_path: Path) -> None:
    """AC14: a failed call equal to an earlier failed one repeats; a later success does not."""
    # WHY: first-run problem P4 (the same failing call again and again) is only visible if
    # equal arguments in another key order count as equal and a success is never a repeat.
    logs = tmp_path / "logs"
    write_trace(logs, "ra", [
        *[_call("read_file", {"path": "a"}) for _ in range(3)],
        _call("read_file", {"path": "b"}),
    ])  # fmt: skip
    write_trace(
        logs, "rb", [_call("calculator", {"x": 1, "y": 2}), _call("calculator", {"y": 2, "x": 1})]
    )
    write_trace(
        logs, "rc", [_call("read_file", {"path": "a"}), _call("read_file", {"path": "a"}, ok=True)]
    )
    data = pass_of(
        [_result(t, r, success=True) for t, r in (("t1", "ra"), ("t2", "rb"), ("t3", "rc"))]
    )

    assert analyze_pass(data, logs)["repeated_failing_calls"] == {"runs": 2, "repeats": 3}


def test_rejections_without_tool_and_iteration_limit_hits_are_counted_ac15_ac16(
    tmp_path: Path,
) -> None:
    """AC15 and AC16: rejections without a tool call; only outputs that start with the text."""
    # WHY: these two counts point at the P2 cause and the D18 limit; a loose match would
    # send the reader to the wrong traces.
    logs = tmp_path / "logs"
    write_trace(logs, "r1", [
        ("plan_created", {"steps": ["a"]}),
        _verdict("retry"),
        _call("read_file", {"path": "a"}, ok=True),
        _verdict("approve"),
        _verdict("retry"),
    ])  # fmt: skip
    write_trace(logs, "r2", [
        ("plan_created", {"steps": ["a"]}),
        _call("read_file", {"path": "a"}, ok=True),
        _verdict("retry", output=LIMIT_TEXT + " more"),
        _verdict("approve", output="Step not completed: something else"),
        _verdict("approve", output="note: " + LIMIT_TEXT),
    ])  # fmt: skip
    data = pass_of([_result("t1", "r1", category="file"), _result("t2", "r2", category="calc")])

    analysis = analyze_pass(data, logs)

    assert analysis["rejected_without_tool"] == {"total": 2, "by_category": {"file": 2}}
    assert analysis["iteration_limit_hits"] == 1


def test_tool_not_used_lists_only_tasks_missing_their_expected_tool_ac17(tmp_path: Path) -> None:
    """AC17: four categories each list one task without its tool; the satisfied ones are absent."""

    # WHY: D39 says a model can answer without its tool; this list shows how often, for
    # information only, and must not flag tasks that did use their tools.
    def ok(task_id: str, category: str, tools: list[str]) -> dict[str, Any]:
        return _result(task_id, "x1", category=category, success=True, tools_used=tools)

    data = pass_of([
        ok("s-bad", "search", []),
        ok("s-good", "search", ["web_search"]),
        ok("c-bad", "calc", ["web_search"]),
        ok("c-good", "calc", ["calculator"]),
        ok("f-bad", "file", ["web_search"]),
        ok("f-good", "file", ["write_file"]),
        ok("m-bad", "multi-tool", ["calculator"]),
        ok("m-good", "multi-tool", ["calculator", "write_file"]),
    ])  # fmt: skip

    unused = analyze_pass(data, tmp_path / "logs")["tool_not_used"]

    assert [(u["task_id"], u["expected"]) for u in unused] == [
        ("s-bad", "web_search"),
        ("c-bad", "calculator"),
        ("f-bad", "read_file or write_file"),
        ("m-bad", "at least 2 different tools"),
    ]


def test_analyze_main_lists_missing_trace_writes_out_file_and_refuses_bad_pass_ac18_ac20(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC18 and AC20: a missing trace is listed and main returns 0; --out matches the output."""
    # WHY: a deleted `logs/` must not stop the analysis, and the saved file must be exactly
    # what was printed; a bad pass must exit 2 without writing anything.
    root, logs = tmp_path / "results", tmp_path / "logs"
    monkeypatch.setattr(passes_module, "OUTPUT_ROOT", tmp_path)
    write_trace(logs, "run-ok", [("plan_created", {"steps": ["a"]})])
    results = [_result("t1", "gone"), _result("t2", "run-ok", success=True, status="done")]
    data = dataclasses.replace(make_data("p1", n_tasks=2), results=results)
    write_pass(root, data)

    analysis = analyze_pass(data, logs)
    assert analysis["missing_traces"] == ["gone"]
    assert analysis["outcomes"]["gave_up"] == 1
    assert [d["task_id"] for d in analysis["digests"]] == ["t1"]

    real_load, real_analyze = analyze.load_pass, analyze.analyze_pass
    monkeypatch.setattr(analyze, "load_pass", lambda pass_id: real_load(pass_id, results_root=root))
    monkeypatch.setattr(analyze, "analyze_pass", lambda pass_data: real_analyze(pass_data, logs))
    out = tmp_path / "docs" / "analysis" / "p1.md"

    assert analyze.main(["--pass", "p1", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert out.read_text(encoding="utf-8").strip() == printed.strip()
    assert "gone" in printed

    refused_out = tmp_path / "refused.md"
    for bad in ("../x", "zz"):
        assert analyze.main(["--pass", bad, "--out", str(refused_out)]) == 2
    assert not refused_out.exists()


def test_digests_are_one_per_failure_single_line_redacted_and_capped_ac19(tmp_path: Path) -> None:
    """AC19: one digest per non-success in task order; no newline or backtick; caps hold."""
    # WHY: digests go into a committed Markdown file and hold untrusted web and model text;
    # they must not break the Markdown, leak the local path, or grow without limit.
    # NOTE: the filler letters are not hex digits; a 32+ character hex run is redacted as a secret.
    logs = tmp_path / "logs"
    write_trace(logs, "run1", [
        ("plan_created", {"steps": ["step `one`\nline"]}),
        _verdict("retry", feedback="`bad`\n" + "Q" * 500),
        *[
            _call("read_file", {"path": "P" * 300 + str(i)}, error="X" * 300 + "\nnew")
            for i in range(12)
        ],
    ])  # fmt: skip
    hostile = f"{PROJECT_ROOT}/private `a`\n" + "Z" * 400
    data = pass_of([
        _result("t0", "ra", success=True),
        _result("t1", "run1", final_answer=hostile),
        _result("t2", "rb", final_answer="short"),
        _result("t3", "rc", success=True),
    ])  # fmt: skip

    digests = analyze_pass(data, logs)["digests"]

    assert [d["task_id"] for d in digests] == ["t1", "t2"]
    first = digests[0]
    texts = [first["final_answer"], *first["plan"], *first["critic"], *first["failed_calls"]]
    for text in texts:
        assert "\n" not in text and "`" not in text
        assert str(PROJECT_ROOT) not in text
    assert first["final_answer"].startswith("<project>/private 'a'")
    # WHY: `==`, not `<=`: a cut to 50 characters would also pass a "no more than" check.
    assert len(first["final_answer"]) == 300
    assert len(first["critic"][0]) == len("step 1 retry: ") + 200
    assert len(first["failed_calls"]) == 10
    for call in first["failed_calls"]:
        assert len(call.split(" -> ", 1)[1]) == 150
        assert len(call) <= len("read_file() -> ") + 150 + 150


def test_untrusted_fields_cannot_break_the_markdown_or_leak_paths_and_secrets(
    tmp_path: Path,
) -> None:
    """Review finding 1 and 4: hostile ids, names and error text are made safe everywhere."""
    # WHY: results and traces hold model output and web text and the analysis is committed.
    # A forged heading, a pipe, an HTML comment or a key must not reach the file.
    logs = tmp_path / "logs"
    secret = "sk-abcdefghijklmnopqrstuv"
    write_trace(logs, "run1", [
        ("plan_created", {"steps": ["a"]}),
        _call(
            "evil|tool\n# forged heading",
            {},
            error="/Users/someone/x " + secret + " | <!--x-->",
        ),
    ])  # fmt: skip
    data = pass_of(
        [
            _result("t|1 <!-- c -->", "run1", category="f|ile"),
            _result("t2", "../../escape"),
        ]
    )
    analysis = analyze_pass(data, logs)
    text = analyze.render_analysis("p1", analysis)
    assert analysis["missing_traces"] == ["../../escape"]
    assert "\n# forged" not in text and "<!--" not in text and secret not in text
    assert "someone" not in text
    for line in text.splitlines():
        assert not line.startswith("# forged")
    assert "evil\\|tool" in text and "t\\|1" in text
