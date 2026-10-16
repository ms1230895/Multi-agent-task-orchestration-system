# Progress

## Status
- **Phase:** Setup (Claude Code configuration)
- **Last completed:** CLAUDE.md
- **Next:** write specs and roadmap (planning session: opus, high effort)

## Done
- [x] Project folder and `.claude/` skeleton
- [x] 7 rules in `.claude/rules/`
- [x] `.vscode/` settings and extensions
- [x] `git init`
- [x] `.gitignore`
- [x] `CLAUDE.md`

## To do (setup)
- [ ] Specs in `docs/specs/` and `docs/plans/roadmap.md`
- [ ] `.claude/settings.json` (permissions, default Manual mode, hooks)
- [ ] Hook scripts (format, open edited file, unit tests on stop)
- [ ] Subagents: code-reviewer, test-writer
- [ ] Skills: implement-step, update-progress
- [ ] GitHub repo and issues

## Open questions
- Which Ollama model supports tool calling? (`test_toolcall.py` not run yet)
- Were the Python libraries installed in this project's `.venv`?
- GitHub MCP scope (`claude mcp list`)