---
description: Write and run tests for a feature using the test-writer and test-runner subagents. Use after implementing a feature or roadmap step.
argument-hint: <feature-name>
---

# Test a feature: $ARGUMENTS

1. Find the feature's spec `docs/specs/<feature>.md` and its source files (from `docs/plans/<feature>-plan.md`, or by searching `src/`). If the spec is missing, say so and ask whether to continue without it.
2. Run `pytest --collect-only -q` and read the total on the last line. Budget left = 100 - (total - 837) (hard rule 11; 837 is the frozen baseline). Delegate to the **test-writer** subagent. Give it the spec path, the acceptance criteria, the source file paths, the existing test file for this feature, the current total, and the budget left. It writes only in `tests/`.
3. When it finishes, delegate to the **test-runner** subagent with the new test file paths. Run it after the writer, never in parallel: the runner needs the tests to exist.
4. Report to Gaurav:
   - **N passed, M failed** for this feature's tests, and for the full suite
   - the new collected total, and the budget left (100 - (total - 837))
   - which acceptance criteria are covered and which are not
   - for each failure: the test, the key error, and whether the bug is more likely in the code or in the test
5. Do not fix failures automatically. Explain each one and ask Gaurav how to proceed.