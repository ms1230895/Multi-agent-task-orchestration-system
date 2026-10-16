---
paths:
  - "**/*.py"
  - "**/*.ipynb"
---

# Colour coding and zero red lines

Gaurav reads code in VS Code with Pylance, Ruff, Error Lens and Better Comments. The colours carry meaning:
- red line or red squiggle = error, must be fixed
- yellow squiggle = warning
- faded text = unused import or variable
- different colours for modules, functions, classes and parameters (semantic highlighting)

## Write code the highlighting can understand
- Type hints on every function parameter and return value, so Pylance colours and checks them correctly.
- Import only what you use, and remove unused imports.
- Only import packages installed in `.venv`. If one is missing, say so and give the `pip install` command. Never leave an unresolved import.

## Zero red lines before you finish
After editing a Python file:
1. Check VS Code's diagnostics for that file with the IDE diagnostics tool.
2. Fix every error. Fix warnings too, unless there is a reason not to; if you leave one, say why in one line.
3. In the feature summary, report: **Diagnostics: N errors, M warnings**.
If VS Code is not connected and diagnostics are unavailable, say so and run `ruff check <file>` instead.

## Comment tags
Use exactly these tags. Better Comments colours them.

| Tag | Colour | Use for |
|---|---|---|
| `# WHY:` | green | the reason a block exists |
| `# NOTE:` | blue | useful context or background |
| `# STEP 1:` | purple | numbered stages inside a long function |
| `# TODO:` | orange | known unfinished work, saying what is missing |
| `# WARNING:` | red | danger: security, data loss, costly LLM calls, hard limits |

Do not invent other tags.

## Terminal output of the orchestrator
When code prints to the terminal, use the `rich` library with one fixed colour per role: planner magenta, executor cyan, critic yellow, tool call blue, retry or replan dark_orange, success green, error red. Define these once as constants in a single module (for example `src/console.py`) and import them; never hard-code colours elsewhere.