# Progress

## Status
- **Phase:** Build (roadmap session 6 of 9 next)
- **Last completed:** Session 5, tracing-and-first-runs: done (510 tests pass; code review found 0 Critical, 8 Warnings fixed, Suggestions deferred in D31; `docs/plans/tracing-and-first-runs-plan.md`, `docs/plans/first-runs.md`, decisions D23-D31)
- **Next:** Session 6, benchmark (spec: opus, high; implementation: sonnet, medium)
- **Session 5, tracing-and-first-runs:** Merged into main
- **Session 6, benchmark:** Plan approved, implementing (`docs/specs/benchmark.md`, `docs/plans/benchmark-plan.md`)

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