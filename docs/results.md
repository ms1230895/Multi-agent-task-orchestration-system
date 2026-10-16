# Results

**Status: baseline only (before any fix).** The generated block at the bottom is regenerated with `python -m evaluation.report`. Do not edit it by hand. Final headline numbers come from the post-fix passes and are added in Step 12.

## Setup (baseline)
- Models: main `gpt-oss:120b-cloud`, second `gemma4:31b-cloud`. Passes: `main-off-1` (gate off), `main-on-1` (gate on), `second-on-1` (gate on).
- Same config in all three except the model and the two gate flags: `MAX_REPLANS=1`, all other limits at their defaults. Same code fingerprint (`479ac5d5`) and the same 30 tasks. One pass each, so these are single measurements.
- Analysis files: `docs/analysis/main-off-1.md`, `docs/analysis/main-on-1.md`, `docs/analysis/second-on-1.md`.

## Baseline in one paragraph
Success was 28/30, 29/30 and 29/30 (93.3%, 96.7%, 96.7%), far above the ~65% target, and the three intervals overlap completely. The benchmark is easy for these models. The failed-tool-call metric has almost no signal: 1 failed call in 72 (gate off), 1 in 71 (gate on), 0 in 49 (second model). **The gate-off vs gate-on difference is 0.0 points, and the relative reduction is not meaningful** (target ~20%). With one failed call per pass, a reduction cannot be measured on this benchmark. The same bad `read_file` call failed in both passes; with the gate on it failed as a validation error, with the gate off as the tool's own input check.

## Failure analysis (before fixes)
Four tasks did not succeed in 90 runs. Three of them are the same problem (causes 1 and 2); one is a single planner mistake (cause 3). Step retries tell the same story: `file-02` and `file-03` cost 26 of the 37 step retries of all three passes (file-02: 4, 4, 4; file-03: 3, 8, 3).

1. **The critic rejects steps that need no tool (first-run problem P2).** The planner splits "read the file" and "pick the third line" into separate steps. On the second step the executor correctly reuses the earlier result and calls no tool, and the critic rejects it ("You did not read the file; no tool was used"). Evidence: 6, 8 and 13 critic rejections with no tool call in the attempt (off, on, second), mostly in the `file` category (all of them in two of the three passes). `file-02` ended as "gave up" with the **right answer** in `main-off-1` (run `20261016-200108-4f4438`) and `second-on-1` (run `20261016-201351-47d5bf`); `file-03` did the same in `main-on-1` (run `20261016-200729-0942a4`). **3 of the 4 non-successes**, and every one of them held the right answer.
2. **The critic cannot count lines because the tool log flattens newlines.** The log the critic sees (D30, D31) collapses every tool result to single-spaced text. The file `items.txt` has 7 non-empty lines, but the critic reads `pen book lamp cup key map box` and says "only 1 non-empty line, your answer of 7 is incorrect". Evidence: this feedback appears in 2 of the 3 `file-03` runs (`20261016-200131-226caa` and `20261016-200729-0942a4`). It cost 3 step retries in `main-off-1`, and 8 step retries plus a replan in `main-on-1`, where the task then failed.
3. **The planner adds a step the task did not ask for, and the final answer is only "file saved" (known weak spot D39).** In `search-03` (`main-off-1`, run `20261016-195950-972ae4`) the plan wrote the answer to a file, so the final answer was "The answer has been saved to sistine_ceiling_painter.txt" and the check for "Michelangelo" failed. **One occurrence in 90 runs** (the same task passed in the other two passes), so the evidence is thin. It is listed here for honesty; it is not a fix candidate unless the post-fix passes show it again.

Not a cause: no repeated failing calls (P4: 0), no crashes, no infrastructure pauses, one iteration-limit hit.

<!-- evaluation-report:start -->
### Passes

| pass | model | validation | retry | code | complete | success | calc | search | file | multi-tool | tool calls | failed calls | validation errors | tool retries | step retries | replans | crashed | pauses | median s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main-off-1 | gpt-oss:120b-cloud | False | False | 479ac5d5 | yes | 28/30 (93.3%) [78.7%–98.2%] | 8/8 | 6/7 | 6/7 | 8/8 | 72 | 1/72 (1.4%) | 0 | 0 | 12 | 1 | 0 | 0 | 8.9 |
| main-on-1 | gpt-oss:120b-cloud | True | True | 479ac5d5 | yes | 29/30 (96.7%) [83.3%–99.4%] | 8/8 | 7/7 | 6/7 | 8/8 | 71 | 1/71 (1.4%) | 1 | 0 | 15 | 1 | 0 | 0 | 7.6 |
| second-on-1 | gemma4:31b-cloud | True | True | 479ac5d5 | yes | 29/30 (96.7%) [83.3%–99.4%] | 8/8 | 7/7 | 6/7 | 8/8 | 49 | 0/49 (0.0%) | 0 | 0 | 10 | 3 | 0 | 0 | 6.5 |

### Pooled groups (repeat passes with the same model, config and code)

| passes | model | count | success | failed calls | step retries | replans |
|---|---|---|---|---|---|---|
| main-off-1 | gpt-oss:120b-cloud | 1 | 28/30 (93.3%) [78.7%–98.2%] | 1/72 (1.4%) | 12 | 1 |
| main-on-1 | gpt-oss:120b-cloud | 1 | 29/30 (96.7%) [83.3%–99.4%] | 1/71 (1.4%) | 15 | 1 |
| second-on-1 | gemma4:31b-cloud | 1 | 29/30 (96.7%) [83.3%–99.4%] | 0/49 (0.0%) | 10 | 3 |

### Ablation: gate off vs gate on

| off | on | failed calls off | failed calls on | difference (points) | relative reduction | success off | success on | validation errors (on) | tool retries (on) |
|---|---|---|---|---|---|---|---|---|---|
| main-off-1 | main-on-1 | 1/72 (1.4%) | 1/71 (1.4%) | +0.0 | -1.4% (the gate added failed calls) | 28/30 (93.3%) [78.7%–98.2%] | 29/30 (96.7%) [83.3%–99.4%] | 1 | 0 |

### Model comparison

| passes | model | success | failed calls | calc | search | file | multi-tool |
|---|---|---|---|---|---|---|---|
| main-on-1 | gpt-oss:120b-cloud | 29/30 (96.7%) [83.3%–99.4%] | 1/71 (1.4%) | 8/8 | 7/7 | 6/7 | 8/8 |
| second-on-1 | gemma4:31b-cloud | 29/30 (96.7%) [83.3%–99.4%] | 0/49 (0.0%) | 8/8 | 7/7 | 6/7 | 8/8 |

### Before vs after fixes

none

### Not compared

none

### Traced runs

- `run_end` events in `logs/`: **105**
- main-off-1: 30/30 results have a complete trace
- main-on-1: 30/30 results have a complete trace
- second-on-1: 30/30 results have a complete trace

30 tasks per pass: one task = 3.3 percentage points. Intervals are 95% Wilson intervals.
<!-- evaluation-report:end -->
