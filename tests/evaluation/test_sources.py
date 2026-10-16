"""Source-rule tests for `evaluation/*.py`: read-only, no secrets, tags, fingerprint.

Covers AC21 to AC23 of `docs/specs/evaluation.md`. These tests read the source files and a
small project copy in `tmp_path`. Nothing is run and no LLM is called.
"""

import ast
import re
from pathlib import Path

from benchmark.runner import PROJECT_ROOT, code_fingerprint

EVALUATION_DIR = PROJECT_ROOT / "evaluation"
FORBIDDEN_MODULES = ("langchain_ollama", "src.run", "src.graph", "src.agents")
FORBIDDEN_NAMES = {"environ", "getenv", "load_dotenv", "eval", "exec", "run_pass"}


def _sources() -> list[Path]:
    """Every Python file of the `evaluation/` package."""
    return sorted(EVALUATION_DIR.glob("*.py"))


def test_evaluation_sources_are_read_only_and_runner_helpers_still_import_ac21() -> None:
    """AC21: no model name, environment, eval/exec, LLM or run import, run_pass; helpers exist."""
    # WHY: the tools only read finished passes. If one could read `.env`, build an LLM or
    # start a run, a report would spend quota or touch secrets (hard rules 2, 3, 7).
    assert _sources(), "evaluation/ has no Python files"
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        assert "gpt-oss" not in text.lower() and "gemma" not in text.lower(), path.name
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""] + [f"{node.module}.{a.name}" for a in node.names]
            else:
                imported = []
            for name in imported:
                assert not name.startswith(FORBIDDEN_MODULES), (path.name, name)
            used = node.id if isinstance(node, ast.Name) else getattr(node, "attr", None)
            assert used not in FORBIDDEN_NAMES, (path.name, used)

    # WHY: `evaluation/passes.py` imports these three private names on purpose (spec 5.1);
    # a rename in the runner must fail here, loudly, and not at the first report.
    from benchmark.runner import _read_manifest, _read_results, _redact

    assert all(callable(item) for item in (_read_manifest, _read_results, _redact))


def test_evaluation_sources_use_only_the_allowed_comment_tags_ac22() -> None:
    """AC22: only WHY, NOTE, STEP n, TODO and WARNING appear as comment tags."""
    # WHY: Better Comments colours exactly these five tags (colour-coding rule); an invented
    # tag such as `# RISK:` would be uncoloured and confusing.
    allowed = {"WHY", "NOTE", "STEP", "TODO", "WARNING"}
    for path in _sources():
        tags = set(re.findall(r"#\s([A-Z]{3,})(?: \d+)?:", path.read_text(encoding="utf-8")))
        assert tags <= allowed, (path.name, tags - allowed)


def test_code_fingerprint_ignores_changes_under_evaluation_ac23(tmp_path: Path) -> None:
    """AC23: editing or adding a file under `evaluation/` leaves the fingerprint unchanged."""
    # WHY: the tools are built while the baseline passes run; if they changed the fingerprint,
    # a paused pass could not resume and two passes could not be compared.
    for folder in ("src", "benchmark", "evaluation"):
        (tmp_path / folder).mkdir()
    (tmp_path / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (tmp_path / "benchmark" / "b.py").write_text("B = 2\n", encoding="utf-8")
    (tmp_path / "benchmark" / "tasks.json").write_text("[]\n", encoding="utf-8")
    tool = tmp_path / "evaluation" / "report.py"
    tool.write_text("X = 1\n", encoding="utf-8")
    before = code_fingerprint(tmp_path)

    tool.write_text("X = 2  # edited\n", encoding="utf-8")
    (tmp_path / "evaluation" / "new_tool.py").write_text("Y = 3\n", encoding="utf-8")

    assert code_fingerprint(tmp_path) == before
