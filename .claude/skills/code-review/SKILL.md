---
name: code-review
description: Review code changes with two subagents in parallel, security-reviewer and quality-reviewer, and merge their findings into one report. Use after a feature is implemented and tested, before /git-feature.
argument-hint: "[optional file or folder to focus on]"
allowed-tools: Bash(git branch --show-current) Bash(git diff --stat main...HEAD)
---

# Code review

## What changed
- Branch: !`git branch --show-current`
- Files changed compared with main:
!`git diff --stat main...HEAD`

Focus (optional): $ARGUMENTS

## Steps
1. Decide the review scope: the committed changes above plus any uncommitted changes. If a focus was given, review only that.
2. Launch the **security-reviewer** and **quality-reviewer** subagents in the same message, so they run in parallel. Give each the changed files, the spec path in `docs/specs/` if there is one, and the focus.
3. Wait for both reports. Do not summarise until both have finished.
4. Merge the findings into one table, sorted by severity, removing duplicates found by both:

| Severity | Area | File:line | Problem | Suggested fix |
|---|---|---|---|---|

   Severity: **Critical** (must fix before merging), **Warning** (should fix), **Suggestion**. Area: Security or Quality.
5. End with a bold verdict: **Ready to merge** (no Critical findings) or **Not ready: N critical issues**.
6. Do not change any code. Ask Gaurav which findings to fix.