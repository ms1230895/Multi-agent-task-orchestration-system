---
paths:
  - "**/*.py"
  - "**/*.ipynb"
---

# Explain the why in code

Gaurav is learning from this codebase. Every piece of code must say why it exists, not only what it does.

## Python files (.py)
1. Module docstring at the top: what the file is for and which architecture layer it belongs to.
2. Docstring on every function and class: what it does, its arguments, what it returns, and why it exists.
3. A `# WHY:` comment above every logical block (a loop, a branch, a try/except, a non-obvious line) giving the reason, not restating the code.
   - Bad: `# loop over the tools`
   - Good: `# WHY: validate every tool call before running it, so a bad argument becomes an error message the LLM can fix instead of a crash`
4. Use only the comment tags defined in colour-coding.md.
5. Do not comment obvious lines (imports, simple assignments). Comments explain decisions, not syntax.

## Notebooks (.ipynb)
- Put a markdown cell before every code cell: what the cell does, why, and what output to expect.
- Number the steps in those markdown cells (Step 1, Step 2, ...).

## When changing existing code
- Update every comment and docstring your change makes wrong. A stale comment is worse than no comment.