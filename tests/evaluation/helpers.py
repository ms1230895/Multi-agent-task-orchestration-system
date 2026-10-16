"""Small builders shared by the `evaluation/` tests: scripted passes and traces in `tmp_path`.

Not a test file (no `test_` prefix), so pytest does not collect it. Model names are dummies.
"""

import json
from pathlib import Path
from typing import Any

from evaluation.passes import PassData

DUMMY_FINGERPRINT = "sha256:" + "a" * 64


def make_data(
    pass_id: str,
    *,
    model: str = "dummy-model",
    fingerprint: str = DUMMY_FINGERPRINT,
    validation: bool = True,
    retry: bool = True,
    extra_config: dict[str, Any] | None = None,
    started_at: str = "2026-10-17T10:00:00.000Z",
    n_tasks: int = 1,
    n_results: int | None = None,
    n_success: int | None = None,
    tool_calls: int = 10,
    failed: int = 0,
    pauses: int = 0,
) -> PassData:
    """Build a `PassData` in memory. Tool-call counters sit on the first result.

    WHY: comparison tests need many passes that differ in one setting; writing a folder for
    each would hide what the test is about.
    """
    results_count = n_tasks if n_results is None else n_results
    successes = results_count if n_success is None else n_success
    config: dict[str, Any] = {
        "ollama_model": model,
        "enable_validation": validation,
        "enable_retry": retry,
        "max_replans": 1,
        **(extra_config or {}),
    }
    manifest = {
        "model": model,
        "config": config,
        "code_fingerprint": fingerprint,
        "task_ids": [f"t{i}" for i in range(n_tasks)],
        "started_at": started_at,
    }
    results = [
        {
            "task_id": f"t{i}",
            "category": "calc",
            "success": i < successes,
            "status": "done" if i < successes else "failed",
            "counters": {
                "tool_calls": tool_calls if i == 0 else 0,
                "failed_tool_calls": failed if i == 0 else 0,
            },
        }
        for i in range(results_count)
    ]
    return PassData(pass_id=pass_id, manifest=manifest, results=results, pauses=pauses)


def write_pass(root: Path, data: PassData) -> Path:
    """Write a `PassData` as a pass folder (manifest.json, results.jsonl, paused.jsonl)."""
    pass_dir = root / data.pass_id
    pass_dir.mkdir(parents=True, exist_ok=True)
    (pass_dir / "manifest.json").write_text(json.dumps(data.manifest), encoding="utf-8")
    lines = [json.dumps(record) for record in data.results]
    (pass_dir / "results.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if data.pauses:
        paused = "\n".join(json.dumps({"n": i}) for i in range(data.pauses)) + "\n"
        (pass_dir / "paused.jsonl").write_text(paused, encoding="utf-8")
    return pass_dir


def pass_of(results: list[dict[str, Any]], pass_id: str = "p1") -> PassData:
    """A `PassData` holding exactly these result lines (for the analysis tests)."""
    manifest = {"task_ids": [str(r.get("task_id")) for r in results]}
    return PassData(pass_id=pass_id, manifest=manifest, results=results, pauses=0)


def write_trace(logs_dir: Path, run_id: str, events: list[tuple[str, dict[str, Any]]]) -> None:
    """Write `logs_dir/<run_id>.jsonl` with one record per `(event, data)` pair."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"event": name, "data": data}) for name, data in events]
    (logs_dir / f"{run_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
