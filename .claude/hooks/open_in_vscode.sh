#!/bin/bash
# PostToolUse hook: open every edited file in the current VS Code window,
# so Gaurav sees changes as they happen, even in Auto mode.

FILE=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')
[ -f "$FILE" ] || exit 0

# WHY: -r reuses the current window instead of opening a new one.
# An already-open file just switches to its tab, so no duplicate tabs.
command -v code >/dev/null 2>&1 && code -r "$FILE"

exit 0