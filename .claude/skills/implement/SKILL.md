---
name: implement
description: Implement a feature from its approved plan, one step at a time, with Gaurav's approval before each step.
argument-hint: <feature-slug>
disable-model-invocation: true
allowed-tools: Bash(git status --short) Bash(git branch --show-current)
---

# Implement: $ARGUMENTS

## Current git state
- Branch: !`git branch --show-current`
- Uncommitted changes:
!`git status --short`

## Before starting
1. The current branch must be `feature/$ARGUMENTS`, and `docs/plans/$ARGUMENTS-plan.md` must exist with Status: Approved. Otherwise stop and explain.
2. Read the spec and the plan. Find the first unticked step in the Progress list. If some steps are already ticked, say so and continue from there.
3. Check the model against the model-selection rule (implementation: sonnet, medium).

## For each step
1. **Explain** in simple language: what this step builds, why, and which files it touches. Wait for Gaurav to say "go".
2. **Build** it in small, reviewable edits (vscode-review rule), following the code-explanations and colour-coding rules.
3. **Check** it: run the check written in the plan, and make sure VS Code diagnostics show zero errors.
4. **Record** it: tick the step in the plan's Progress list, then commit with `feat($ARGUMENTS): step N - <name>`.
5. **Report** in three lines: what was done, the check result, the next step. Ask whether to continue.

If Gaurav says "continue with all remaining steps", keep going without waiting, but still stop immediately on any failed check or error.

## Rules
- Follow the plan. If it turns out to be wrong or incomplete, stop and propose a change to the plan. Never silently do something different.
- Do not write the full test suite here; that is what `/test` is for. Small checks per step are fine.

## When every step is ticked
1. Set the plan Status to Implemented, and update progress.md to "Implemented, next: /test".
2. Commit both.
3. Give the feature summary (feature-summary rule).
4. Suggest the next commands: `/test $ARGUMENTS`, then `/code-review`.