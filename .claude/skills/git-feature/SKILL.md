---
description: Finish a feature. Pushes the current feature branch, merges it into main, pushes main, and deletes the branch locally and on GitHub.
disable-model-invocation: true
allowed-tools: Bash(git status --short) Bash(git branch --show-current) Bash(git remote -v)
---

# Ship a feature branch to main

## Current git state
- Branch: !`git branch --show-current`
- Remotes: !`git remote -v`
- Uncommitted changes:
!`git status --short`

## Step 1: Safety checks (stop at the first failure and explain it)
1. The current branch must start with `feature/`. If it is `main` or anything else, stop.
2. A remote named `origin` must exist. If not, stop and tell Gaurav the GitHub repo must be connected first.
3. If there are uncommitted changes, list them and ask Gaurav whether to commit them, suggesting a message. Never commit files he has not seen.
4. Run `pytest -q`. If any test fails, stop. If no tests exist, say so and ask whether to continue.
5. Make sure `docs/plans/progress.md` marks this feature as done. If not, update it and commit on this branch: `docs: mark <slug> done`.

## Step 2: Confirm once
Show Gaurav exactly what will happen:
- push `feature/<slug>` to origin
- merge it into `main` with a merge commit
- push `main`
- delete `feature/<slug>` locally and on GitHub
Wait for "yes". This is the only confirmation.

## Step 3: Ship (in order; stop on any error)
1. `git push -u origin <branch>`
2. `git switch main`
3. `git pull --ff-only origin main` (skip if main does not exist on origin yet)
4. `git merge --no-ff <branch> -m "Merge <branch>"`
   On a merge conflict: stop. Do not resolve it yourself. Show the conflicting files and explain the options.
5. `git push origin main`
6. `git branch -d <branch>` (lowercase -d: git refuses if the branch is not fully merged; never use -D)
7. `git push origin --delete <branch>`

## Step 4: Report
- The last commits on main: `git log --oneline -5`
- Confirmation that the branch is deleted locally and on GitHub
- The next step from progress.md