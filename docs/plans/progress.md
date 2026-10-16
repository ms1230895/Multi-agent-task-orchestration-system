# Progress

## Status
- **Phase:** Build (roadmap session 2 of 8)
- **Last completed:** Session 1, architecture-and-roadmap (`docs/specs/architecture.md`, `docs/plans/roadmap.md`)
- **Next:** Session 2, foundation-tools-validation (implementation: sonnet, medium effort)
- **Current feature:** foundation-tools-validation: Implemented, next: /test (`docs/plans/foundation-tools-validation-plan.md`)

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
- Architecture open questions (see `docs/specs/architecture.md` section 6): critic `replan` verdict, scope of `ENABLE_RETRY`, `src/run.py` vs `src/graph.py`. Also update the `AgentState` field list in CLAUDE.md.