# Methodology

This document describes how the published TokenBench numbers were produced. It
covers one sweep: Claude Sonnet 5.5 (high effort) in Claude Code 2.1.293, five
conditions, three task sets, two trials per task, 710 runs. The sweep ran between
24 and 29 October 2026.

## 1. Design

For every task and trial, each of the five conditions is run once. The conditions
differ only in the tool added to the agent; the model, the effort level, the agent
binary, the permission flags and the task prompt are identical (graphify adds a
short paragraph to the prompt, see section 3). Savings are always computed against
the baseline runs of the same sweep.

Scheduling:

* The 55 short tasks are shuffled with a fixed seed (55055). For task *i* the five
  conditions are rotated by *i* positions; trial 2 runs the conditions in the
  reverse order of trial 1. This spreads time-of-day and provider-load effects
  across conditions.
* One driver process keeps four runs in flight at a time across all three sets.
* Each run gets its own isolated workspace copy. The environment passed to the agent
  is an allowlist built by the harness; session variables of the launching shell
  are removed.

## 2. Agent configuration

All five conditions use the same base command (see [`harness/agents/`](harness/agents/)):

```
claude --print --output-format json --dangerously-skip-permissions
       --disallowedTools WebSearch WebFetch
       --model claude-sonnet-5-5 --effort high
       --setting-sources project,local --disable-slash-commands
```

plus `--strict-mcp-config`, which the harness adds (the module that adds it is not
included, so this flag is not visible in `harness/agents/` and cannot be checked from
this repository). The tool-specific parts are:

| Condition | Version | Added to the base command |
|---|---|---|
| Baseline | none | nothing |
| caveman | upstream commit `1f951fb4ad40` (plugin.json version 4.0.1) | `--plugin-dir <plugin cache>`, environment `CAVEMAN_DEFAULT_MODE=caveman` |
| graphify | `graphifyy` 0.9.95 | harness builds `graphify-out/graph.json` in the workspace before the run, and appends a "Knowledge graph" paragraph to the prompt |
| ponytail | 5.3.2 (tag `v5.3.2`, commit `16352ac229eb08a9598dcae48fe9f2b342567d86`) | `--plugin-dir <plugin cache>`, default level |
| rtk | 1.0.1 (GitHub release v1.0.1, Windows x86_64 build) | `--settings rtk_settings.json`, which registers `rtk hook claude` as a `PreToolUse` hook on the Bash tool |

Notes on how each tool was set up:

* **caveman.** In the tested upstream commit a headless `claude -p` session starts
  with caveman off unless `CAVEMAN_DEFAULT_MODE` is set, so the agent
  configuration sets `CAVEMAN_DEFAULT_MODE=caveman`, the variable upstream
  documents for this case.
* **graphify.** The graph is built by the harness with graphify's AST extraction, so
  building it uses no model tokens and its cost is not part of the run cost. The
  agent is told it can query the graph with `python -m graphify query`, `explain`
  and `path`. For the chains, the same block is appended to the chain prompt.
* **ponytail and caveman activity check.** For these two plugins the driver reads
  the session transcript and checks that the plugin's hook text is present. A run
  in which it is absent is discarded and re-run (see section 6). This only
  verifies that the treatment was applied; it does not look at results.
* **rtk.** The hook rewrites Bash commands so they go through `rtk`. It was
  checked once that `git status` becomes `rtk git status`.

## 3. Task sets

### Short tasks (55 tasks)

Defined in [`tasks/short_tasks.jsonl`](tasks/short_tasks.jsonl). The tasks come from
eight repositories: six purpose-built applications (Go, C++, PHP and others) and two
open-source projects, SharpTS (MIT, a TypeScript interpreter in C#) and mvm
(BSD-3-Clause, a Go interpreter). Categories: bug fixes, staged implementations,
reorganisations, contract tasks and implementations. Each task has visible tests
that the agent can run and hidden tests that are run after the agent finishes.
The agent prompt contains the task text, the workspace path, the list of forbidden
paths, the visible test command, and a fixed set of rules (fix only the described
task, do not edit tests, do not install dependencies unless necessary, run the
visible tests, stop when done). Hidden test commands never appear in the prompt.
The prompt template is [`harness/tokenbench/runners/prompt_builder.py`](harness/tokenbench/runners/prompt_builder.py).

### SWE-bench Pro chains (13 chains)

A chain is a set of 4 to 9 dependent SWE-bench Pro issues from one repository, whose
reference patches apply in sequence to one common base commit. The agent receives
all issues of the chain in one prompt and has to resolve all of them in a single
session. The 13 chains cover ansible (3 chains), flipt, navidrome, nodebb, openlibrary
(2), qutebrowser (2), teleport, vuls and webclients. The chain definitions
(instance ids, base commit, docker image, test lists) are in
[`tasks/swe_bench_pro_chains/`](tasks/swe_bench_pro_chains/). After the run, the
agent's workspace diff is applied to the base commit inside the SWE-bench Pro docker
image, the hidden tests are run, and `grade.py` counts passing fail-to-pass and
pass-to-pass tests.

### Custom long tasks (3 tasks)

Defined in [`tasks/custom_tasks.jsonl`](tasks/custom_tasks.jsonl):

* `bizflow_long_ar_001` (Go): re-implement a lost accounts-receivable extension so
  that the HTTP API and command line match a compiled reference.
* `logforge_long_query_001` (C++): re-implement a lost `query` mode so that stdout
  and exit status match a reference build byte for byte.
* `paygate_long_settlement_001` (PHP): add settlement, disputes and reconciliation
  to a payment portal while keeping its documented invariants.

These tasks were written to be long and to leave little room for guessing: behaviour
is checked by hidden tests, and for two of the tasks the reference behaviour is only
available through compiled binaries. Run time limits are described in section 6.

## 4. Grading and success

### Short and custom tasks

After the agent finishes, visible and hidden tests are run and the run is scored
(`harness/tokenbench/scoring/`):

```
quality = 0.85 x hidden test-case pass rate
        + 0.10 x visible test-case pass rate        (full credit if the task has no visible tests)
        + 0.05 x artifact integrity score           (100, minus penalties)
```

all on a 0-100 scale. Artifact integrity starts at 100; deleting files outside the
scored paths costs 20, modifying ignored fixture files costs 10, and an empty patch
with failing hidden tests costs 10. Modifying a forbidden path sets quality to 0.
Reorganisation tasks use `0.70 x hidden behaviour + 0.25 x structure-contract score
+ 0.05 x artifact integrity` instead.

A run **succeeds** when quality >= 80, the hidden test-case pass rate is >= 0.90,
and no forbidden path was modified. The 0.90 threshold lets a run miss one edge case
on a task with about ten hidden cases but not two.

The harness also computes an efficiency score from wall time, log size and file
churn. It is not used in any published number.

### SWE-bench Pro chains

`grade.py` runs the tests of every task in the chain in the docker image and reports
the number of passing fail-to-pass (F2P) and pass-to-pass (P2P) tests, pooled over the
chain. The published pass rule is `all_pass`: every F2P and every P2P test of every
task passes. Chains have no partial credit in the published tables: quality is 100
for a passing run and 0 otherwise.

`grade.py` also writes a looser flag, `ok`, which is true when at least 90% of the
pooled F2P tests and at least 90% of the pooled P2P tests pass. The published results
do not use it; it is kept in `results/runs.csv` as `pooled90_flag`. Over the 130
chain runs, `all_pass` is true for 23 (baseline), 22 (graphify), 20 (ponytail), 22
(caveman) and 22 (rtk) of 26 runs, while `ok` is true for all 130. A failed run
missed 1 to 4 tests in total, between 0.0% and 1.2% of the chain's tests. The strict
rule was chosen for publication because a missed test is a missed behaviour. The chain
definition files originally recorded the looser flag as the success rule; both flags
are published for every run (`all_pass` and `pooled90_flag` in `results/runs.csv`), so
either rule can be applied. Because a chain contains many tests, a single missed test
is enough to fail a run, so chain pass rates are sensitive to a handful of tests.

## 5. Cost, savings and intervals

**Cost of a run.** The provider-reported `total_cost_usd` from Claude Code's JSON
result, which bills the main thread and every sub-agent. No cost is estimated. The
cost of building the graphify graph is not billed because it uses no model calls.
Failed attempts that were discarded and re-run (section 6) are not included in the
published cost of a condition.

**Cost per task (per-set views).** For a set with units *u* (tasks or chains) and conditions *c*, let
`cost(c, u)` be the mean cost over the two trials of unit *u*. Cost per task is the
mean of `cost(c, u)` over all units of the set. The cost total is the sum over units.

**Saving (per-set views).**

```
saving(c) = -( sum_u cost(c, u) / sum_u cost(baseline, u) - 1 ) x 100
```

Positive = cheaper than baseline.

**95% interval (per-set views).** Bootstrap over units: draw as many units as the set has, with
replacement, 2000 times (`random.seed(7)`); on each draw compute
`(sum cost(c) / sum cost(baseline) - 1) x 100` over the drawn units; sort the 2000
values and take the values at indices `int(0.025 x 2000)` and `int(0.975 x 2000)`.
The interval shown in the tables is the negation of that range, so it is on the same
scale as the saving. Per-unit trial averages are the resampled unit, so the interval
reflects the variation between tasks. The custom set has only 3 tasks, so only a few
distinct resamples exist and its interval bounds are coarse: they take a small number
of discrete values and should be read as a rough range, not a precise one. Only the
cost ratio is bootstrapped; quality and pass rate have no interval in these tables.

**All tasks: each set weighted equally.** The three sets measure different kinds of
work. Counting tasks would let the 55 short tasks dominate (77% of the 71 tasks), and
pooling dollars would let the 3 custom tasks dominate (54% of baseline spend), so the
"All tasks" view counts each set once.

* *Cost change vs baseline* is the mean of the three per-set cost changes (short,
  SWE-bench Pro chains, custom long). Each set's change is the pooled change within
  that set, `(sum_u cost(c, u) / sum_u cost(baseline, u) - 1) x 100` over the units
  of that set. A negative value is cheaper than baseline. The published payload also
  stores it as a saving (`savings_pct`, the negation) and stores the three set
  savings in `set_savings`.
* *95% range* is a stratified bootstrap with 4000 resamples and `random.seed(7)`, set
  once before the loop. In each resample, for each set in the order short, chains,
  custom, one unit is drawn at random with replacement for every unit in that set
  (so each set keeps its own number of units), and the mean of the three set changes
  is recomputed. The 4000 values are sorted and the values at indices
  `int(0.025 x 4000)` and `int(0.975 x 4000)` are the bounds. Units are taken in the
  order in which each (set, task) first occurs in `results/runs.csv`. If the range
  includes 0%, the difference between the tool and baseline could be noise. The
  custom set has 3 tasks, so it has few distinct resamples and its contribution to
  the range is coarse.
* *Pass rate* and *quality* are the means of the three per-set values (the per-set
  values as published, that is, rounded as in the set tables).
* *Value index* is `100 x` the mean over the three sets of `(tool value / baseline
  value)`, so baseline is 100. Each set's value is computed from `results/runs.csv`
  without rounding: the mean run quality of the set divided by the mean per-task cost
  of the set (the sum of per-task mean costs divided by the number of tasks). The
  index is rounded to an integer once, at the end. The rounded per-set values shown in
  the set tables are not used.
* The "All tasks" view carries no dollar amounts and no cost per task: the payload
  fields for them are null. Dollar amounts are in each set's table.

**Pass rate.** Successful runs divided by graded runs (all 710 runs were graded).
The "All tasks" figure is instead the mean of the three set pass rates (above); the
published payload keeps the total number of successes as `successes`.

**Quality.** Mean of the per-run score over the runs of the set (short and custom:
the quality score of section 4; chains: 100 or 0).

**Value (per set).** `quality / cost per task`, rounded to an integer in the tables
("quality per dollar"; the "All tasks" index uses the unrounded values). The
quantity has no absolute meaning; it is only comparable
between conditions within one set, and it is dominated by cost in sets where quality
is similar across tools. The "All tasks" value index is defined above.

All of the above is implemented in [`analysis/build_results.py`](analysis/build_results.py),
which reproduces every figure in `results/results_{all,short,pro,custom}.json`:
for the three set views cost per task, cost total, run counts, passes, saving,
interval bounds, quality, value and pass rate; for the "All tasks" view run counts,
successes, cost change, range bounds, per-set savings, mean pass rate and quality and
the value index.

## 6. Run validity and retries

A run was kept as a result only if the harness classified it as valid. Runs were
discarded and run again when:

* the provider returned a transient error (rate or usage limit, overload, API or
  network error); the driver waited and retried, up to 9 attempts for a cell;
* the harness itself reported an invalid run (for example a process-tree or
  provenance failure), or raised an exception; up to 3 attempts;
* for ponytail and caveman, the plugin's hook text was not found in the transcript
  (see section 2); up to 3 attempts for the short and custom tasks, 2 for chains.
  The sweep's anomaly log has no entry of this kind, so the check never discarded a
  run.

The harness records the client version of every run. All 710 published runs report
Claude Code 2.1.293. The per-run records are not included, so this cannot be checked
from this repository (see "What cannot be checked from this repository" below).

**Time limits.** From `harness/sweep/driver.py` and the agent configurations:

* Short tasks: every agent configuration in `harness/agents/` has
  `default_timeout_seconds` 900. The task files carry `allowed_runtime_seconds`
  of 600 (34 tasks), 900 (17 tasks) or 1200 (4 tasks). Which of the two values the
  runner enforced is decided in a runner module that is not included, so it cannot be
  stated from this repository. The longest accepted
  short run took 737 s, and no accepted short run exceeded its task's allowance.
* SWE-bench Pro chains: the driver waits at most 5400 s (90 minutes) for the agent
  (`PRO_TIMEOUT`). The longest chain run took 1187 s.
* Custom long tasks: the task files in `tasks/custom_tasks.jsonl` carry
  `allowed_runtime_seconds` 3600 for all three tasks, but the sweep applied 14400 s
  (4 hours). The driver sets both the manifest allowance and the runner's default
  timeout to 14400 s in memory for this block (`B3_TIMEOUT`), without changing the
  task files. The longest custom run took 5733 s.

`results/runs.csv` has no timeout column. No accepted run reached a limit, judging by
wall time against the limits above and by the attempt log.

Two baseline, one graphify, one ponytail, one caveman and two rtk runs, all in the
custom set, were flagged `needs_cleanup` (the run finished normally but left a
background process running afterwards); they are included.

Every attempt, accepted or not, is recorded in the driver's attempt log. The same
rules applied to all conditions, except that the plugin-activity check applies only to
ponytail and caveman. The log reconciles with the published runs as follows.

* Short tasks: 555 attempts for 550 accepted runs. Five attempts were discarded and
  the cell was run again: three failed harness hygiene checks (a leftover process, an
  unowned writer in the workspace, a locked workspace path), one hit an internal
  harness error, and one timed out; the re-run of the timed-out cell finished
  normally. Cost of the discarded attempts: $0.52.
* SWE-bench Pro chains: 130 attempts for 130 accepted runs. None was discarded.
* Custom long tasks: 78 attempts for 30 accepted runs. 23 attempts stopped before the
  agent started, because the harness's provenance check refused to run after local
  source changes (no cost; the pins were refreshed and the cells run again). 4 ended in
  transient provider errors (no cost). 21 finished normally (none ended in an agent
  error or a timeout) but were set aside by the process-census check, which flags a
  run when a process the agent started, such as a background shell, a wait loop or a
  monitor, is still alive after the run ends. The early rule treated these as invalid
  and re-ran the cell; this wasted $284.84, against $377.07 for the 30 accepted custom
  runs, and was a harness-side decision, not a result of the agent's work, which had
  finished. From 29 October the harness kept such runs and flagged them
  `needs_cleanup` instead. Seven accepted custom runs carry that flag
  (`needs_cleanup` in `results/runs.csv`).

The 21 runs set aside by the process-census check, by condition (cost from the
attempt log's `cost` field, which equals `stdout_cost` for all 21):

| Condition | Runs set aside | Cost (USD) |
|---|---:|---:|
| baseline | 1 | 21.37 |
| graphify | 5 | 63.13 |
| ponytail | 8 | 103.84 |
| caveman | 3 | 45.21 |
| rtk | 4 | 51.29 |
| Total | 21 | 284.84 |

The attempt log does not record whether these runs passed or what quality they
scored: its fields are the run id, condition, task, trial, verdict, wall time, cost,
exit code, agent error and timeout flags, and the harness's invalid reason. Their
outcome is therefore not known from the log. The attempt log itself is not included in
this repository, so this table cannot be recomputed from it here.

Two safeguards exist in the driver for edge cases: a timed-out run is accepted if it
times out again on retry, and a ponytail or caveman run whose plugin text is absent is
accepted after the final attempt. Neither case occurred in this sweep: no run in the
published set timed out, and the plugin-activity check never discarded or flagged a
run. The cost of discarded attempts is not included in the tables.

Chains were graded after the sweep, in the docker verifier. While grading, the
verifier script of one chain (`navidrome_A`) was found to contain a carriage-return
handling error, which made all nine of its fail-to-pass tests count as failing. The
script was corrected on 2 November 2026 and the six navidrome_A runs that had been
graded with the faulty script were graded again. All ten navidrome_A runs (two per
condition) pass; that chain has no pass-to-pass tests. No other grade was changed.
The correction is to the verifier, not to any run's output, and every navidrome_A run
was graded by the same corrected script. Before the correction the chain pass counts
were baseline 21, graphify 20, ponytail 20, caveman 20 and rtk 22 of 26; with it they
are 23, 22, 20, 22 and 22.

### Tool activity in the transcripts

The harness checks that the ponytail and caveman plugin text reached the model (section
2). For rtk and graphify, activity was counted from the Claude Code session transcripts
after the sweep (the transcripts themselves are not part of this repository):

| Condition | Measure | Short (110 runs) | Chains (26 runs) | Custom (6 runs) |
|---|---|---:|---:|---:|
| rtk | runs with at least one Bash command rewritten by the rtk hook | 73 | 26 | 6 |
| rtk | Bash commands rewritten / Bash calls | 225 / 370 | 544 / 1129 | 101 / 939 |
| graphify | runs that queried the graph (`python -m graphify query`, `explain` or `path`) | 48 | 0 | 0 |

No hook rewrite appears in baseline or graphify runs. In graphify runs the prompt adds a
paragraph that names the graph and shows how to query it; where the agent did not query
it, it searched with its native tools (grep, glob, file reads) instead.

### What cannot be checked from this repository

Some statements in this document rest on files that are not part of this repository
(per-run records, session transcripts, the attempt and anomaly logs, and harness
modules that are not included). They are given as recorded by the sweep and cannot be
verified from `results/runs.csv` and the files in `harness/`:

* the client version of every run (all 710 report Claude Code 2.1.293);
* the transcript counts in the table above (rtk rewrites and graphify queries), and
  the statement that no hook rewrite appears in baseline or graphify runs;
* the attempt-log reconciliation in this section: attempt counts, discarded attempts,
  their costs and the table of runs set aside by the process-census check;
* the statements that the plugin-activity check never discarded a run and that the
  anomaly log has no entry of that kind;
* that no accepted run hit a runtime cap (wall times in `runs.csv` are consistent with
  it, but there is no timeout column), and which timeout the runner applied to short
  tasks;
* flags added by modules that are not included, such as `--strict-mcp-config`, and
  the exact final argument list of each run;
* the one-off check that the rtk hook rewrites `git status` to `rtk git status`.

What can be checked: every number in the tables and in `results/results_*.json`
(recomputed by `analysis/build_results.py --check` from `runs.csv`), the scoring
code, the agent base commands, the driver's retry and timeout logic as written, and
the task and chain definitions.

## 7. Data files

| File | Content |
|---|---|
| `results/runs.csv` | one row per run (710): set, task, repo, condition, trial, pass flag, quality, cost, wall time, tokens, chain test counts |
| `results/results_*.json` | the published payload for each view, as shown on the site |
| `results/summary.json`, `results/tables.md` | recomputed by `analysis/build_results.py` from `runs.csv` |

Column notes for `runs.csv`: `total_tokens` is the sum of input, output, cache-read
and cache-creation tokens recorded for the run (all threads, including sub-agents,
for the short and custom tasks; the figures in the run result for chains). Tokens
are not used in any table in this repository. `quality` for chains is 100 or 0. `tests_failed`,
`tests_total` and `pooled90_flag` are filled for chains only.

* `needs_cleanup`: 1 if the run finished normally but left a background process running afterwards, otherwise 0; flagged runs are included in all tables.
* `pooled90_flag`: chains only; 1 if at least 90% of the pooled fail-to-pass tests and at least 90% of the pooled pass-to-pass tests passed. It is the looser rule and is not used in any published table.

## 8. Known caveats

* Sonnet 5.5 at high effort is the only configuration measured.
* Two trials per task is the minimum for a rough estimate of run-to-run variation;
  intervals are wide for the 3-task and 13-chain sets. The interval ranges for the
  custom set are also coarse: with 3 tasks there are few distinct resamples.
* Hidden tests and the purpose-built fixture repositories are not distributed, and
  the SWE-bench Pro issue text and test patches are not redistributed here; they
  can be rebuilt from the instance ids in each `chain.json`. See
  [`tasks/README.md`](tasks/README.md).
* The harness code under `harness/` is a reference copy of the pieces that define
  the measurement. It is not a complete installable package.
* The analysis gives cost and pass/fail results. It does not explain why a tool's
  cost differs from baseline, and no explanation is claimed.
