"""File tools: read and write text files, only inside `workspace/`.

Architecture layer: 2 (tools).

WARNING: the LLM chooses the path, so it is untrusted input (Hard rule 4). Every path goes
through `resolve_in_workspace`, which rejects `..`, absolute paths and symlinks that lead
outside the sandbox.
"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src import config
from src.tools.base import ToolResult, failure, success

MAX_FILE_CHARS = 100_000
MAX_PATH_CHARS = 255


class ReadFileArgs(BaseModel):
    """Input schema for `read_file`."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)


class WriteFileArgs(BaseModel):
    """Input schema for `write_file`."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=MAX_PATH_CHARS)
    content: str = Field(max_length=MAX_FILE_CHARS)


class WorkspaceError(Exception):
    """Internal: the path is not allowed. The message is safe to show the LLM."""


def resolve_in_workspace(path: str) -> Path:
    """Turn a user-given relative path into a real path that is inside `workspace/`.

    Args:
        path: a path relative to the workspace folder, such as `"notes/a.txt"`.

    Returns:
        The resolved absolute path.

    Raises:
        WorkspaceError: if the path is empty, absolute, or leads outside the workspace.
    """
    if not isinstance(path, str) or not path.strip():
        raise WorkspaceError("path must be a non-empty string")
    if "\x00" in path:
        raise WorkspaceError("path contains an invalid character")
    if Path(path).is_absolute():
        raise WorkspaceError("absolute paths are not allowed; use a path inside the workspace")

    # WHY: read the workspace location at call time (not import time) so tests can point
    # it at a temporary folder.
    root = config.WORKSPACE_DIR.resolve()

    # WHY: `resolve()` follows `..` and symlinks, so checking the resolved path catches
    # every way of escaping, including a symlink inside the workspace that points out.
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise WorkspaceError("path escapes the workspace")
    return target


def read_file(path: str) -> ToolResult:
    """Read a UTF-8 text file from the workspace.

    Args:
        path: relative path inside `workspace/`.

    Returns:
        `ok=True` with the file text, or `ok=False` with a readable error.
        Never raises (Hard rule 1).
    """
    try:
        target = resolve_in_workspace(path)
        if not target.is_file():
            return failure("file not found (or not a regular file)")
        # WHY: size cap so one huge file cannot fill the LLM's context.
        if target.stat().st_size > MAX_FILE_CHARS:
            return failure(f"file too large (limit {MAX_FILE_CHARS} bytes)")
        try:
            return success(target.read_bytes().decode("utf-8"))
        except UnicodeDecodeError:
            return failure("file is not UTF-8 text")
    except WorkspaceError as exc:
        return failure(str(exc))
    except OSError as exc:
        # WHY: the class name only; OS messages contain full local paths.
        return failure(f"could not read file: {type(exc).__name__}")
    except Exception as exc:  # WHY: last safety net, so no input can make a tool raise
        return failure(f"{type(exc).__name__}: {exc}")


def write_file(path: str, content: str) -> ToolResult:
    """Write a UTF-8 text file in the workspace, creating folders and overwriting.

    Args:
        path: relative path inside `workspace/`.
        content: the text to write.

    Returns:
        `ok=True` with a short confirmation, or `ok=False` with a readable error.
        Never raises (Hard rule 1).
    """
    try:
        if not isinstance(content, str):
            return failure("content must be a string")
        if len(content) > MAX_FILE_CHARS:
            return failure(f"content too large (limit {MAX_FILE_CHARS} characters)")
        target = resolve_in_workspace(path)
        if target == config.WORKSPACE_DIR.resolve() or target.is_dir():
            return failure("path is a folder, not a file")
        # WHY: encode before opening the file, so text that cannot be encoded fails
        # without first emptying an existing file.
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError:
            return failure("content cannot be encoded as UTF-8")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return success(f"wrote {len(content)} chars to {path}")
    except WorkspaceError as exc:
        return failure(str(exc))
    except OSError as exc:
        return failure(f"could not write file: {type(exc).__name__}")
    except Exception as exc:  # WHY: last safety net, so no input can make a tool raise
        return failure(f"{type(exc).__name__}: {exc}")
