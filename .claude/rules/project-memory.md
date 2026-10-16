# Project memory file

`docs/project-memory.md` is Gaurav's record of the commands and prompts used in this project. It is separate from Claude Code's automatic memory.

## When to add an entry
- Gaurav uses a prompt that starts, finishes, or reworks a feature.
- A prompt worked well and is worth reusing (a review request, a debugging request, etc.).
- A new terminal command becomes part of the workflow (setup, test, run, benchmark, git).
- A new slash command (skill) or subagent is created.

## How to add an entry
- Append to the correct section. Never rewrite, reorder, or delete existing entries unless Gaurav asks.
- Prompts: copy Gaurav's prompt word for word, then add one line on when to use it and which model and effort suit it.
- Commands: the command in a code block, plus one line on its purpose.
- Date every prompt-history entry as YYYY-MM-DD.
- After updating, say in one line: "Saved to project-memory: <entry title>".
- Never store secrets, API keys, tokens, or anything from `.env` in this file.

## Using it
When Gaurav asks what the prompt or command for something was, answer from this file first.