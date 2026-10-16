2) docs/plans/roadmap.md — exactly 8 sessions, in build order:
1. architecture-and-roadmap
2. foundation-tools-validation (config, .env loading, console colours, 4 tools, registry, validation gate + retry)
3. graph-planner-critic (AgentState, LangGraph wiring, router with limits, planner, critic)
4. executor (inner tool loop, connected into the graph)
5. tracing-and-first-runs (JSONL traces, run_task entry point, first end-to-end runs)
6. benchmark (30 tasks, code checkers, runner)
7. evaluation (ablation off vs on, 100+ runs, model comparison, fix top failures)
8. readme-and-writeup
For each: goal in one line, what it depends on, and "done when".