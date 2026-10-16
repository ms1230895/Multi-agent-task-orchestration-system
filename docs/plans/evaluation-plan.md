# Implementation plan: Evaluation

**Spec:** docs/specs/evaluation.md
**Branch:** feature/evaluation
**Status:** Approved

## Progress
Part A: build the tools, run the baseline, choose the fixes
- [x] Step 1: `evaluation/passes.py`
- [x] Step 2: comparisons and trace count in `evaluation/report.py`
- [ ] Step 3: results block, `write_block`, report CLI
- [ ] Step 4: `evaluation/analyze.py`, part 1 (classes, failed calls, repeats)
- [ ] Step 5: `evaluation/analyze.py`, part 2 (rejections, limits, digests, CLI)
- [ ] Step 6: docs for the tools (architecture, CLAUDE.md, project-memory)
- [ ] Step 7: baseline passes (Gaurav runs them)
- [ ] Step 8: baseline analysis and the failure causes
- [ ] Step 9: spec addendum, section 10 "Chosen fixes" (Gaurav approves)
- [ ] Step 10: the fixes (up to 3, one commit each)

Part B: runs after `/test` and `/code-review` (the measured code must be the merged code)
- [ ] Step 11: post-fix passes
- [ ] Step 12: `docs/results.md` and final numbers

## Overview
First build two read-only tools in a new `evaluation/` folder (outside the code fingerprint, so the baseline passes can run while we build). Then run the baseline passes, use `analyze` on their traces to name the top 3 failure causes, get Gaurav's approval of the fixes in a spec addendum, make the fixes in `src/`, and after test and review run fresh passes. The numbers in `docs/results.md` come from the report tool, never from hand-copying.

## Order of work (read this first)
1. `/implement evaluation` builds Steps 1 to 6 and stops at Step 7, which needs Gaurav (it runs the real passes). Steps 1 to 6 do not change `src/` or `benchmark/`, so the code fingerprint stays the same and the baseline passes are valid.
2. Steps 8 to 10 follow once the baseline passes are done. Step 9 is a hard stop: no fix is written before Gaurav approves the addendum.
3. After Step 10: `/test evaluation`, then `/code-review`, fix the findings, then Steps 11 and 12. The post-fix passes must run on the final code.

## Test budget (hard rule 11)
CLAUDE.md on `main` caps tests added after the 837-test baseline at 100. The spec has 23 test criteria (AC1 to AC23). `/test` must merge them into about **18 tests** (parametrize only where cases differ in behaviour), and each fix gets 1 to 2 tests, so the whole session stays near **24 of 100**. `main` has the rule; this branch does not yet. Before `/test`: `git merge main` into this branch (ask Gaurav first).

## Steps

### Step 1: `evaluation/passes.py`
- **Files:** `evaluation/__init__.py` (new, empty), `evaluation/passes.py` (new)
- **What:** `ReportError`; `PassData` and `load_pass` (reads manifest, results, pause count through the runner's `_read_manifest` / `_read_results`, turns `PassRefused` into `ReportError`); `wilson_interval`; `pass_metrics` (the runner's `summarize` plus the extra sums, median seconds, short fingerprint); `group_key`, `PassGroup`, `group_passes` (complete passes only, counts summed, rates recomputed).
- **Why:** every later piece reads passes through this one place, using the session 6 metric definitions.
- **Check:** a small script in the terminal builds a fake pass folder in `/tmp`-like scratch space and prints `pass_metrics`; `ruff check evaluation` clean; diagnostics 0 errors.
- **Done when:** `load_pass("smoke-2")` on the real committed pass returns 3 results and a metrics dict with no error.
- **Covers:** AC1, AC2, AC3, AC4

### Step 2: comparisons and trace count in `evaluation/report.py`
- **Files:** `evaluation/report.py` (new)
- **What:** `compare_groups` (kinds `ablation`, `model`, `fix`, plus "not compared" reasons; relative reduction `None` when the off rate is 0 or missing); `count_traces` (counts `run_end` events, skips unreadable files).
- **Why:** the gate-off vs gate-on comparison is the main CV number, so the rule for what may be compared is written as code, not by eye.
- **Check:** a scratch script with four scripted groups prints the expected rows and the "not compared" lines; `count_traces(Path("logs"))` returns a number.
- **Done when:** off 10/100 vs on 8/100 gives a reduction of 0.2, and a pair differing in two things gives no row and a reason.
- **Covers:** AC5, AC6, AC7, AC8

### Step 3: results block, `write_block`, report CLI
- **Files:** `evaluation/report.py`
- **What:** `render_markdown` (passes table, pooled groups, the three comparison tables, not-compared lines, trace count, the 3.3-points note; one decimal, intervals, no time stamps); `write_block` (markers; refuse on a broken marker set); `main` with `--passes` and `--write`, exit codes 0 and 2.
- **Why:** `docs/results.md` must be regenerable with identical output, so a number can never be hand-copied wrongly.
- **Check:** `python -m evaluation.report --passes smoke-2` prints a block; run it twice and compare the text; a `--write` to a scratch file with markers replaces only the middle.
- **Done when:** the output is identical on two runs, and a file with one marker is refused with exit 2 and left unchanged.
- **Covers:** AC9, AC10, AC11

### Step 4: `evaluation/analyze.py`, part 1
- **Files:** `evaluation/analyze.py` (new)
- **What:** `classify` (8 classes, first match wins), `error_kind`, failed tool calls grouped by tool and kind, repeated failing calls (P4), `missing_traces`, the skeleton of `analyze_pass`.
- **Why:** this turns 30 traces into counts we can rank, so the top causes come from evidence.
- **Check:** scratch traces in a scratch folder give the expected class and the grouped counts; `smoke-2` runs through `analyze_pass` without error.
- **Done when:** `analyze_pass` on `smoke-2` returns outcomes, failed calls and no exception.
- **Covers:** AC12, AC13, AC14, AC18 (missing traces)

### Step 5: `evaluation/analyze.py`, part 2
- **Files:** `evaluation/analyze.py`
- **What:** rejected attempts without a tool call (P2), iteration-limit hits, tool-not-used, plan steps and step retries per category, digests (redacted, one line, no backticks, capped), `render_analysis`, CLI `--pass` and `--out`.
- **Why:** these are the mechanical traces of the known problems P2 and P4 and of the D39 weak spots, and the digests are what Gaurav and Claude read to name causes.
- **Check:** `python -m evaluation.analyze --pass smoke-2` prints Markdown; the file-02 digest shows its retries; a digest line has no newline and no backtick.
- **Done when:** the `smoke-2` output lists the file-02 step retries and a digest per non-success result (none for a clean pass is fine).
- **Covers:** AC15, AC16, AC17, AC19, AC20

### Step 6: docs for the tools
- **Files:** `docs/specs/architecture.md`, `CLAUDE.md`, `docs/project-memory.md`, `docs/plans/decisions.md`
- **What:** architecture layer table and section 5 (the tools, pass naming, the comparison rules); CLAUDE.md commands and layout (`evaluation/`); project-memory commands for `evaluation.report` and `evaluation.analyze`; decision entries for the `evaluation/` folder (why outside the fingerprint) and the private-helper imports.
- **Why:** the commands must be findable, and the architecture must match the code.
- **Check:** `grep` finds both commands in CLAUDE.md and project-memory.
- **Done when:** the three files mention both commands and `evaluation/`.
- **Covers:** AC33

### Step 7: baseline passes (Gaurav runs them)
- **Files:** `benchmark/results/main-off-1/`, `main-on-1/`, `second-on-1/` (written by the runner)
- **What:** Gaurav sets `.env` (`MAX_REPLANS=1`; `main` = `gpt-oss:120b-cloud`, `second` = `gemma4:31b-cloud`; off = both flags `false`) and runs `python -m benchmark.runner --pass <id>` for the three passes, resuming after any pause with the same `.env`. Commit each finished pass: `chore(results): pass <id>`.
- **Why:** these are the before-fix numbers; `src/` and `benchmark/` must not change while they run.
- **Check:** `python -m benchmark.runner --pass <id> --summary` shows 30/30; a short script checks that the three manifests share one fingerprint and differ only in model and flags.
- **Done when:** three complete passes are committed.
- **Covers:** AC29

### Step 8: baseline analysis and the failure causes
- **Files:** `docs/analysis/main-on-1.md`, `docs/analysis/main-off-1.md`, `docs/analysis/second-on-1.md` (new), `docs/results.md` (new, draft)
- **What:** run `evaluation.analyze` on each baseline pass and save the output; run `evaluation.report --write docs/results.md`; with Gaurav read the counts and digests and write "Failure analysis (before fixes)": 1 to 3 causes, each with a count and run ids.
- **Why:** the traces, not guesses, decide what to fix.
- **Check:** every run id in the analysis appears in a committed `results.jsonl` (short script).
- **Done when:** `docs/results.md` has the failure-analysis section and the analysis files are committed.
- **Covers:** AC27

### Step 9: spec addendum "Chosen fixes" (stop for Gaurav)
- **Files:** `docs/specs/evaluation.md` (new section 10)
- **What:** one subsection per fix: cause, evidence, the change, its acceptance criteria. Commit `docs: update spec for evaluation`.
- **Why:** the fixes could not be known before the traces; they still pass through spec approval.
- **Check:** each fix has at least one criterion checkable by code.
- **Done when:** Gaurav says the addendum is approved. No fix is written before that.
- **Covers:** AC27 (approval path)

### Step 10: the fixes
- **Files:** `src/` (decided in Step 9), `tests/` (1 to 2 tests per fix), `docs/plans/decisions.md`
- **What:** one commit per fix (`fix(<area>): ...`), touching only `src/` and `tests/`, plus a decision entry per fix with cause, evidence and expected effect.
- **Why:** separate small commits let the before/after rows show what each fix did.
- **Check:** `pytest -q` passes after each commit; `ruff check .` clean.
- **Done when:** every approved fix is committed with its test and decision entry.
- **Covers:** AC28, and the per-fix criteria of section 10

### Step 11: post-fix passes (after `/test` and `/code-review`)
- **Files:** `benchmark/results/main-on-2/`, `main-off-2/`, `second-on-2/` (and `second-off-2`, `main-on-3` if quota allows)
- **What:** Gaurav runs the passes with the same `.env` as the baseline on the final merged code; commit each finished pass.
- **Why:** the measured code must be the code that is merged; before and after are never mixed.
- **Check:** all three manifests share a fingerprint different from the baseline one, equal to `code_fingerprint()` of the last commit; `max_replans == 1`.
- **Done when:** the post-fix passes are complete and committed.
- **Covers:** AC30

### Step 12: `docs/results.md` and final numbers
- **Files:** `docs/results.md`, `docs/plans/progress.md`, `docs/project-memory.md`
- **What:** `python -m evaluation.report --passes <all passes> --write docs/results.md`; write the setup, headline numbers (post-fix, next to the targets, with intervals), the fixes with before/after rows, and the limitations; check the trace count and coverage.
- **Why:** this is the deliverable: honest numbers, even below the targets.
- **Check:** run the report a second time and `git diff docs/results.md` is empty; `count_traces()` is at least 100 and every result has a trace with one `run_end`.
- **Done when:** the spec's AC31 and AC32 hold, and progress.md and project-memory.md are updated.
- **Covers:** AC31, AC32

## Risks
- **Quota.** Six or seven passes of 30 tasks may not fit one weekly cap. Mitigation: the order in the spec puts the most important passes first, the runner resumes after pauses, and `second-off-2` and `main-on-3` are optional. If the cap hits, the results say which passes are missing.
- **Fewer than 100 traces.** The baseline plus post-fix passes give about 180 runs, but a deleted `logs/` would lower the count. Do not delete `logs/`.
- **Pass differences are noise.** One task is 3.3 points and a cloud model at temperature 0 is not exactly repeatable. Mitigation: intervals in every table, a repeat pass if quota allows, and the limitations section saying so.
- **No clear failure cause.** If the traces show no cause above noise, we report that and fix nothing; we do not invent a fix.
- **Fingerprint drift.** Any edit in `src/` or `benchmark/` between two baseline passes breaks the comparison. Steps 1 to 6 only touch `evaluation/`, `docs/` and `CLAUDE.md`; Step 10 starts only after all baseline passes are done.
- **Private helper imports.** `evaluation/` imports three private names from `benchmark/runner.py`. If the runner renames them later the import fails loudly; a test imports them.

## Definition of done
- [ ] Every acceptance criterion in the spec passes as a test (/test), merged into about 18 tests
- [ ] /code-review has no Critical findings
- [ ] Diagnostics: 0 errors
- [ ] progress.md and project-memory.md updated
- [ ] Ready for /git-feature
