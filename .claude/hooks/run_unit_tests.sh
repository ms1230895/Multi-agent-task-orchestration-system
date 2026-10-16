#!/bin/bash
# Stop hook: run the fast unit tests every time Claude finishes a turn.
# If tests fail, Claude is told and must report it to Gaurav.

INPUT=$(cat)

# WHY: if this hook already stopped Claude once in this turn, let it finish.
# Without this check, failing tests could keep Claude in an endless loop.
ACTIVE=$(echo "$INPUT" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("stop_hook_active", False))')
[ "$ACTIVE" = "True" ] && exit 0

cd "$CLAUDE_PROJECT_DIR" || exit 0

# WHY: nothing to run until the tests/ folder and the project's Python exist
[ -d tests ] || exit 0
PY="$CLAUDE_PROJECT_DIR/.venv/bin/python"
[ -x "$PY" ] || exit 0

OUTPUT=$("$PY" -m pytest -q -x --no-header 2>&1)
CODE=$?

# NOTE: pytest exit code 0 = all passed, 5 = no tests collected yet. Both are fine here.
if [ $CODE -eq 0 ] || [ $CODE -eq 5 ]; then
  exit 0
fi

# WHY: exit code 2 tells Claude Code to show this message to Claude and keep it working
echo "Unit tests are failing. Tell Gaurav which tests fail and why. Do not fix them without asking." >&2
echo "$OUTPUT" | tail -n 20 >&2
exit 2