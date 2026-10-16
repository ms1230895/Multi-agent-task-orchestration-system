# Progress

## Status
- **Phase:** Build (roadmap session 7 of 9 next)
- **Last completed:** Session 6, benchmark: done, waiting for `/git-feature` (837 tests pass; see below). Before that, Session 5, tracing-and-first-runs: done (510 tests pass; code review found 0 Critical, 8 Warnings fixed, Suggestions deferred in D31; `docs/plans/tracing-and-first-runs-plan.md`, `docs/plans/first-runs.md`, decisions D23-D31)
- **Next:** Session 7, evaluation: done and ready for `/git-feature` (870 tests pass; baseline passes, analysis, fixes D44/D45, review fixes D46). The post-fix passes (plan Steps 11 and 12) were skipped by Gaurav's decision, so the fixes are not measured. Then Session 8, streamlit-ui (spec: opus, high).
- **Session 8, streamlit-ui:** Plan approved (`docs/specs/streamlit-ui.md` approved, `docs/plans/streamlit-ui-plan.md`, 10 steps, at most 25 new tests).
- **Follow-up fix `feature/fix-line-separator-test`:** done. A VS Code buffer stripped the literal U+2028 from a test; the test now writes it as the escape `\u2028`. Tip: close or revert stale editor tabs before a session.
- **Follow-up `feature/test-cap`:** done. Hard rule 11: the 837 existing tests are a frozen baseline, tests added after them are capped at 100 (budget = `pytest --collect-only -q` total minus 837). Test-writer, `/test`, test-runner and quality-reviewer updated. No tests deleted. Session 7, evaluation: spec is a Draft on `feature/evaluation` (`docs/specs/evaluation.md`), waiting for review, then `/create-plan evaluation`.
- **Session 5, tracing-and-first-runs:** Merged into main
- **Session 6, benchmark:** Implemented, tested (327 benchmark tests; 837 in the suite) and code-reviewed (0 Critical; the 6 Warnings fixed, Suggestions deferred in D40). Next: `/git-feature` (`docs/specs/benchmark.md`, `docs/plans/benchmark-plan.md`, decisions D32-D40). Smoke pass `smoke-2` (3 tasks, `gpt-oss:120b-cloud`, `MAX_REPLANS=1`): 3/3 success, resume skipped all 3. Finding for session 7: file-02 passed but cost 6 step retries and 1 replan, because the planner splits read/extract/return into 3 steps and the critic rejects steps without a tool call (first-run problem P2 again). Before any full pass: keep `MAX_REPLANS=1` in `.env`. Note: `smoke-2` cannot be resumed any more (the code fingerprint changed after the review fixes); start new pass ids.

## Done
- [x] Project folder and `.claude/` skeleton
- [x] 7 rules in `.claude/rules/`
- [x] `.vscode/` settings and extensions
- [x] `git init`
- [x] `.gitignore`
- [x] `CLAUDE.md`

## To do (setup)
- [x] Specs in `docs/specs/` and `docs/plans/roadmap.md`
- [x] `.claude/settings.json` (permissions, default Manual mode, hooks)
- [x] Hook scripts (format, open edited file, unit tests on stop)
- [ ] Subagents: code-reviewer, test-writer
- [ ] Skills: implement-step, update-progress
- [ ] GitHub repo and issues

## Open questions
- [x] Tool calling confirmed: gpt-oss:120b-cloud (main), gemma4:31b-cloud (second). deepseek not on free plan.
- Were the Python libraries installed in this project's `.venv`?
- GitHub MCP scope (`claude mcp list`)er 
- [x] Architecture open questions (section 6) all decided: critic verdicts (D13), `ENABLE_RETRY` scope (D1), `src/run.py` (D23-D26).