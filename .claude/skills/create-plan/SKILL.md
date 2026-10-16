---
name: create-plan
description: Write the implementation plan for a feature from its spec, then ask Gaurav whether to implement it this way.
argument-hint: <feature-slug>
disable-model-invocation: true
model: opus
effort: high
allowed-tools: Bash(git status --short) Bash(git branch --show-current)
---

# Create the implementation plan: $ARGUMENTS

## Current git state
- Branch: !`git branch --show-current`
- Uncommitted changes:
!`git status --short`

## Step 1: Checks (stop at the first failure and explain)
1. The current branch must be `feature/$ARGUMENTS`. If not, stop and ask.
2. `docs/specs/$ARGUMENTS.md` must exist. If not, stop and suggest `/create-spec`.
3. Running this skill means Gaurav has approved the spec: change its Status to Approved.

## Step 2: Write the plan
1. Read the spec, the architecture section of CLAUDE.md, and any existing code the feature touches.
2. Write `docs/plans/$ARGUMENTS-plan.md` using plan-template.md from this skill folder.
3. Split the work into small steps in dependency order. Each step must be finishable and checkable in one sitting.
4. Every acceptance criterion in the spec must be covered by at least one step.
5. Add the feature to `docs/plans/progress.md` as "Plan ready, waiting for approval".
6. Commit: `git add docs/specs/$ARGUMENTS.md docs/plans/$ARGUMENTS-plan.md docs/plans/progress.md` then `git commit -m "docs: plan for $ARGUMENTS"`.

## Step 3: Ask before implementing
Show the plan as a short numbered list, one line per step, plus the biggest risk. Then ask exactly:

"**Plan is ready. Shall we implement it like this?** Reply *yes* to start, or tell me what to change."

- If Gaurav asks for changes: update the plan, commit with `docs: update plan for $ARGUMENTS`, and ask the same question again.
- If he says yes: update progress.md to "Plan approved", commit it, then tell him: "Switch to **sonnet, effort medium** with `/model`, then run `/implement $ARGUMENTS`."

Do not write any code in this skill.