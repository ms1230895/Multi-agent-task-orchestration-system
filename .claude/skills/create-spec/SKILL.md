---
name: create-spec
description: Start a new feature. Creates a git feature branch from main and writes the feature spec in docs/specs/. Stops for Gaurav's review; the plan comes later with /create-plan.
argument-hint: <feature-name> <short description>
disable-model-invocation: true
model: opus
effort: high
allowed-tools: Bash(git status --short) Bash(git branch --show-current)
---

# Create a feature spec

Feature request: $ARGUMENTS

## Current git state
- Branch: !`git branch --show-current`
- Uncommitted changes:
!`git status --short`

## Step 1: Branch
1. Make a slug from the feature name: lowercase, words joined with hyphens ("Calculator Tool" → `calculator-tool`).
2. If there are uncommitted changes above, stop. List them and ask Gaurav whether to commit them first. Never carry unrelated changes into the new branch.
3. If the current branch is not `main`, ask before switching.
4. Run `git switch main`, then `git switch -c feature/<slug>`. If that branch already exists, stop and ask.

## Step 2: Spec
1. Read `docs/specs/architecture.md` if it exists, plus any spec this feature depends on.
2. Ask up to 3 clarifying questions only if the request is genuinely ambiguous. Otherwise continue.
3. Write `docs/specs/<slug>.md` using spec-template.md from this skill folder, with Status: Draft. Fill every section; write "None" rather than deleting one.
4. Every acceptance criterion must be checkable by code, because each one becomes a test.

## Step 3: Hand over to Gaurav
1. Commit: `git add docs/specs/<slug>.md` then `git commit -m "docs: spec for <slug>"`.
2. Explain the spec in simple steps.
3. End with exactly: "**Spec ready:** `docs/specs/<slug>.md`. Review it and ask me for any changes. When you are happy with it, run `/create-plan <slug>`."
4. If Gaurav asks for changes, edit the spec, commit with `docs: update spec for <slug>`, and repeat the hand-over line.
5. Save Gaurav's prompt to `docs/project-memory.md` (project-memory rule).

Do not write a plan or any code in this skill.