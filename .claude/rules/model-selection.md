# Model and effort per task

Use the right model and effort for each kind of task. Aliases: `opus` = Opus 5.5, `sonnet` = Sonnet 5.5, `haiku` = Haiku 5.5.

| Task | Model | Effort |
|---|---|---|
| Writing or changing specs, architecture, roadmap planning | opus | high |
| Designing benchmark tasks and checkers | opus | high |
| Debugging a failure not solved on the first attempt; analysing agent traces | opus | high |
| Implementing a roadmap step or feature | sonnet | medium |
| Writing tests | sonnet | medium |
| Code review | sonnet | high |
| Refactoring | sonnet | medium |
| Explaining a concept to Gaurav | sonnet | medium |
| Small edits: typos, renames, formatting, updating progress or project-memory | haiku | low |

## How to apply this
You cannot switch your own model or effort, so:
1. At the start of each session, and whenever the type of task changes, compare the current model and effort with this table.
2. If they do not match, tell Gaurav in one line before starting: "This is <task>: switch to <model>, effort <level>, with /model." Then wait for him.
3. Skills and subagents in .claude/ set their own `model` and `effort` in frontmatter. When one runs, that setting applies, so do not ask for a switch for it.
4. If Gaurav chooses to stay on the current model, continue without asking again for the same task.