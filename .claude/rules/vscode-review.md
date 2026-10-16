# VS Code review workflow

Gaurav reviews every change inside VS Code. He must always be able to see what you are changing, in the file itself.

## Before editing
- This project runs Claude Code connected to VS Code (chat panel, or CLI in the integrated terminal). If the CLI is running in an external terminal, ask Gaurav to run `/ide` before you edit anything.
- Before a set of edits, list the files you will touch and what changes in each, one line per file.

## While editing
- Edit in small, logical pieces, one file at a time, so each diff is easy to review. Never mix unrelated changes in one edit.
- In Manual mode, each edit opens as a diff for approval. Wait for the decision. If Gaurav rejects a change or edits your proposal, treat his version as correct and do not re-apply your original.
- In Auto or Edit automatically mode, edits are not shown for approval. A hook in settings.json opens each edited file in VS Code so the change is visible. After each edit, also state in one line: file, what changed, why.
- Never change the permission mode yourself and never suggest switching to Auto to go faster.

## After editing
- Tell Gaurav where to see the full diff: the Source Control panel in VS Code, or `git diff <file>`.