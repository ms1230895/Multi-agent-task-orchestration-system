#!/bin/bash
# PostToolUse hook: format and lint a Python file right after Claude edits it.

# WHY: Claude Code sends details of the edit as JSON on stdin; we only need the file path
FILE=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')

# WHY: only Python files are formatted; edits to markdown or JSON are left alone
[[ "$FILE" == *.py ]] || exit 0
[ -f "$FILE" ] || exit 0

# WHY: prefer the project's own ruff in .venv so the version matches the project
RUFF="$CLAUDE_PROJECT_DIR/.venv/bin/ruff"
[ -x "$RUFF" ] || RUFF="ruff"

"$RUFF" format --quiet "$FILE"
"$RUFF" check --fix --quiet "$FILE"

# NOTE: always exit 0. Formatting problems must never block Claude's edit.
exit 0