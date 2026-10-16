"""Unit tests for `src/tools/files.py` (AC14-AC18).

Every test uses a tmp_path workspace; the real `workspace/` is never touched.
"""

import os
from pathlib import Path

import pytest

from src import config
from src.tools.files import read_file, write_file


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary workspace folder that config.WORKSPACE_DIR points at."""
    # WHY: Hard rule 4 tests need a safe sandbox; files.py reads WORKSPACE_DIR at call time.
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    return ws


def test_write_then_read_roundtrip_in_nested_folder(workspace: Path) -> None:
    """AC14: write_file creates folders and file; read_file returns the text."""
    # WHY: the core promise of the file tools.
    written = write_file("a/b.txt", "hi")
    assert written["ok"] is True
    assert written["output"] == "wrote 2 chars to a/b.txt"
    assert (workspace / "a" / "b.txt").read_text(encoding="utf-8") == "hi"
    assert read_file("a/b.txt") == {"ok": True, "output": "hi", "error": None}


def test_write_file_existing_file_is_overwritten(workspace: Path) -> None:
    """AC14: writing twice replaces the content."""
    # WHY: spec says overwrite.
    write_file("a.txt", "one")
    write_file("a.txt", "two")
    assert read_file("a.txt")["output"] == "two"


def test_write_file_unicode_roundtrip(workspace: Path) -> None:
    """AC14: UTF-8 text survives a write and read."""
    # WHY: files are specified as UTF-8.
    write_file("u.txt", "héllo ✓")
    assert read_file("u.txt")["output"] == "héllo ✓"


def test_write_file_workspace_folder_missing_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC14: a missing workspace folder is created by write_file (spec section 7)."""
    # WHY: a fresh clone may have no workspace folder.
    ws = tmp_path / "later"
    monkeypatch.setattr(config, "WORKSPACE_DIR", ws)
    assert write_file("a.txt", "x")["ok"] is True
    assert (ws / "a.txt").exists()


def test_read_file_workspace_folder_missing_returns_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC17: read_file with no workspace folder returns ok False (spec section 7)."""
    # WHY: must not raise.
    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path / "nope")
    result = read_file("a.txt")
    assert result["ok"] is False
    assert "not found" in (result["error"] or "")


@pytest.mark.parametrize("bad_path", ["../x.txt", "a/../../x.txt"])
def test_both_tools_parent_traversal_rejected_and_nothing_created(
    workspace: Path, bad_path: str
) -> None:
    """AC15: `..` paths escaping the workspace are rejected by both tools."""
    # WHY: Hard rule 4.
    outside = workspace.parent / "x.txt"
    outside.write_text("secret", encoding="utf-8")
    outside.unlink()

    assert write_file(bad_path, "pwned")["ok"] is False
    assert not outside.exists()

    outside.write_text("secret", encoding="utf-8")
    result = read_file(bad_path)
    assert result["ok"] is False
    assert result["output"] is None


def test_both_tools_absolute_path_rejected_and_nothing_created(
    workspace: Path, tmp_path: Path
) -> None:
    """AC15: absolute paths are rejected by both tools."""
    # WHY: Hard rule 4; an absolute path ignores the workspace root.
    target = tmp_path / "abs.txt"
    assert write_file(str(target), "pwned")["ok"] is False
    assert not target.exists()
    assert read_file("/etc/passwd")["ok"] is False


def test_both_tools_null_byte_path_rejected(workspace: Path) -> None:
    """AC15: a path with a null byte returns ok False and does not raise."""
    # WHY: null bytes make the OS raise ValueError; tools must not raise.
    assert read_file("a\x00b")["ok"] is False
    assert write_file("a\x00b", "x")["ok"] is False


def test_symlink_to_outside_file_rejected_by_both_tools(workspace: Path, tmp_path: Path) -> None:
    """AC16: a symlink inside the workspace that leads outside is rejected."""
    # WHY: resolve() must catch sandbox escape through links.
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    link = workspace / "link.txt"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks cannot be created on this system")

    read_result = read_file("link.txt")
    assert read_result["ok"] is False
    assert "top secret" not in str(read_result)

    assert write_file("link.txt", "overwritten")["ok"] is False
    assert secret.read_text(encoding="utf-8") == "top secret"


def test_symlink_to_outside_folder_rejected_for_write(workspace: Path, tmp_path: Path) -> None:
    """AC16: writing through a symlinked folder that leads outside is rejected."""
    # WHY: a new file below a link must not land outside the sandbox.
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    try:
        os.symlink(outside_dir, workspace / "dirlink", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks cannot be created on this system")

    assert write_file("dirlink/new.txt", "x")["ok"] is False
    assert not (outside_dir / "new.txt").exists()


def test_read_file_missing_file_returns_error(workspace: Path) -> None:
    """AC17: a missing file gives ok False."""
    # WHY: Hard rule 1.
    result = read_file("nope.txt")
    assert result["ok"] is False
    assert result["error"]


def test_read_file_folder_returns_error(workspace: Path) -> None:
    """AC17: a folder is not a regular file."""
    # WHY: reading a directory raises in plain Python; tool must return a result.
    (workspace / "d").mkdir()
    assert read_file("d")["ok"] is False


def test_read_file_over_100000_bytes_returns_error(workspace: Path) -> None:
    """AC17: a file larger than 100 000 bytes is refused."""
    # WHY: keeps the LLM context small.
    (workspace / "big.txt").write_bytes(b"x" * 100_001)
    result = read_file("big.txt")
    assert result["ok"] is False
    assert "too large" in (result["error"] or "")


def test_read_file_exactly_100000_bytes_is_allowed(workspace: Path) -> None:
    """AC17: the size limit is inclusive."""
    # WHY: boundary of 'size <= 100 000'.
    (workspace / "edge.txt").write_bytes(b"x" * 100_000)
    assert read_file("edge.txt")["ok"] is True


def test_read_file_non_utf8_returns_error(workspace: Path) -> None:
    """AC17: a binary, non-UTF-8 file gives ok False."""
    # WHY: UnicodeDecodeError must not escape.
    (workspace / "bin.dat").write_bytes(b"\xff\xfe\x00\x80")
    result = read_file("bin.dat")
    assert result["ok"] is False
    assert "UTF-8" in (result["error"] or "")


@pytest.mark.parametrize("path", [".", ""])
def test_both_tools_dot_or_empty_path_fail(workspace: Path, path: str) -> None:
    """AC17/spec section 7: '.' and '' are failures for both tools."""
    # WHY: '.' resolves to the workspace itself, which is a folder.
    assert read_file(path)["ok"] is False
    assert write_file(path, "x")["ok"] is False


def test_write_file_to_existing_folder_path_fails(workspace: Path) -> None:
    """AC17: writing onto an existing folder returns ok False."""
    # WHY: write_bytes on a directory raises; must be a result.
    (workspace / "d").mkdir()
    assert write_file("d", "x")["ok"] is False


def test_write_file_content_over_100000_chars_fails_and_writes_nothing(workspace: Path) -> None:
    """AC18: oversize content is refused and no file appears."""
    # WHY: size limit protects disk and context.
    result = write_file("big.txt", "x" * 100_001)
    assert result["ok"] is False
    assert not (workspace / "big.txt").exists()


def test_write_file_content_of_100000_chars_is_allowed(workspace: Path) -> None:
    """AC18: the limit is inclusive."""
    # WHY: boundary of 'max 100 000 chars'.
    assert write_file("edge.txt", "x" * 100_000)["ok"] is True


def test_write_file_oversize_content_does_not_overwrite_existing(workspace: Path) -> None:
    """AC18: a refused write leaves the old file intact."""
    # WHY: a failed write must not destroy data.
    write_file("keep.txt", "old")
    assert write_file("keep.txt", "x" * 100_001)["ok"] is False
    assert read_file("keep.txt")["output"] == "old"


def test_write_file_unencodable_text_fails_without_emptying_file(workspace: Path) -> None:
    """AC18: text that cannot be UTF-8 encoded (lone surrogate) fails cleanly."""
    # WHY: the tool must not raise, and must not truncate an existing file first.
    write_file("keep.txt", "old")
    result = write_file("keep.txt", "bad \ud800 text")
    assert result["ok"] is False
    assert read_file("keep.txt")["output"] == "old"
