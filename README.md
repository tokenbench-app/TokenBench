# TokenBench

TokenBench measures what developer add-ons for AI coding agents do to the cost and
the outcome of real work. Each tool is run against the same tasks as an unmodified
baseline, with the same model and the same agent, and every run is billed by the
provider and graded by hidden tests. This repository contains the harness code,
the task definitions, the grading code, the per-run data and the scripts that turn
that data into the published tables for one sweep: Claude Sonnet 5.5 (high effort)
in Claude Code 2.1.293, five conditions, 710 runs.

## What was measured

| Condition | Version tested | How it was applied |
|---|---|---|
| Baseline | none | Claude Code with no tool added |
| caveman | upstream commit `1f951fb` (plugin version 4.0.1) | Claude Code plugin, `CAVEMAN_DEFAULT_MODE=caveman` |
| graphify | 0.9.95 (PyPI `graphifyy`) | knowledge graph built into the workspace before the run; the prompt tells the agent how to query it |
| ponytail | 5.3.2 (tag `v5.3.2`, commit `16352ac`) | Claude Code plugin, default level |
| rtk | 1.0.1 | `PreToolUse` hook on the Bash tool (`rtk hook claude`) |

Setup shared by all five conditions: model `claude-sonnet-5-5`, `--effort high`,
Claude Code 2.1.293 (auto-update disabled; every one of the 710 runs recorded this
client version), `--print --output-format json`, `--dangerously-skip-permissions`,
WebSearch and WebFetch disallowed, no user-level settings, skills, slash commands or
custom sub-agents, strict MCP configuration. Four runs executed concurrently on one
Windows 10 workstation. The base command line for each condition is in [`harness/agents/`](harness/agents/).

## The three task sets

| Set | Units | Runs per condition | What it is for |
|---|---:|---:|---|
| Short tasks | 55 tasks | 110 (x2 trials) | Very short tasks, close to simple questions for a modern coding agent. The median run takes about 36 seconds. Useful as a sanity check, but low statistical power: most differences between tools are within noise. Two are not: caveman cost 7.1% more than baseline and ponytail cost 9.0% less, and the 95% intervals for both exclude 0%. |
| SWE-bench Pro chains | 13 chains | 26 (x2 trials) | Several dependent SWE-bench Pro issues from one repository, given to the agent together as one long task. Longer and closer to real work than the short tasks. |
| Custom long tasks | 3 tasks | 6 (x2 trials) | Tasks we wrote to mirror heavy real development work (a Go billing back end, a C++ log tool, a PHP payment portal). They are a very hard challenge, written to reflect serious development work and to test whether the tools actually work. With only 3 tasks and 6 runs per condition the intervals are wide (about 16 to 76 percentage points) and coarse. |

Each condition therefore has 110 + 26 + 6 = 142 runs, and the sweep has 710 runs in
total. Task definitions are in [`tasks/`](tasks/) (see [`tasks/README.md`](tasks/README.md)
for what is and is not included).

## How the numbers are defined

* **Success.** Short and custom tasks: a run succeeds when at least 90% of the
  hidden test cases pass, the run's quality score is at least 80, and no forbidden
  path was modified. SWE-bench Pro chains: a run succeeds only when every
  fail-to-pass and every pass-to-pass test of every task in the chain passes.
* **Cost.** The cost the provider reports for the run (`total_cost_usd` in Claude
  Code's JSON result). It covers the main thread and any sub-agents. It is never
  estimated. Anything a tool causes the model to do, such as extra turns or
  sub-agent work, is part of that tool's cost.
* **Cost per task** (per-set tables only). For each task the two trials are
  averaged, then the average is taken over the tasks of the set.
* **Saving vs baseline** (per-set tables). `-(tool cost / baseline cost - 1)`, using
  the summed per-task costs of the same set. Positive means cheaper than baseline,
  negative means more expensive. The baseline runs come from the same sweep.
* **95% interval** (per-set tables). Bootstrap over tasks: resample the tasks with
  replacement 2000 times, recompute the cost ratio each time, and take the 2.5th and
  97.5th percentiles.
* **All tasks: cost change vs baseline, sets weighted equally.** The three sets
  measure different kinds of work, so the "All tasks" view counts each set once. The
  figure is the mean of the three per-set cost changes (short, SWE-bench Pro chains,
  custom long), each set's change being the pooled change within that set (summed
  per-task cost of the tool against summed per-task cost of baseline). It is shown
  as a cost change: negative means cheaper than baseline, positive means more
  expensive. Counting tasks would let the 55 short tasks dominate (77% of the 71
  tasks), and pooling dollars would let the 3 custom tasks dominate (54% of baseline
  spend), so neither is used. The Short, Chains and Custom columns show the three
  set changes that go into the mean.
* **All tasks: 95% range.** Bootstrap with 4000 resamples (`random.seed(7)`),
  resampling tasks within each set, recomputing the mean of the three set changes
  each time, and taking the 2.5th and 97.5th percentiles. If the range includes 0%,
  the difference between the tool and baseline could be noise: the sweep does not
  show a cost difference. The custom set has only 3 tasks, so there are few distinct
  resamples of it and its part of the range is coarse.
* **Quality.** Mean run score on a 0-100 scale. For short and custom tasks this is
  `0.85 x hidden-test pass rate + 0.10 x visible-test pass rate + 0.05 x artifact
  integrity` (reorganisation tasks use a structure score instead; see
  [`METHODOLOGY.md`](METHODOLOGY.md)). For chains it is 100 for a passing run and 0
  otherwise, so chain quality equals the chain pass rate.
* **Value.** In each set, quality divided by cost per task: roughly, quality points
  bought per dollar. Higher is better. In the "All tasks" view the value index is
  100 x the mean over the three sets of (tool value / baseline value), so baseline is
  100 and a tool above 100 bought more quality per dollar than baseline on average
  across the sets. Each set's value is computed from the unrounded run data (mean run
  quality divided by mean per-task cost) and the index is rounded to an integer only
  at the end, so it is not recomputed from the rounded per-set values in the tables.
* **Pass rate and quality (All tasks).** The mean of the three per-set values, so
  each set counts equally. The "All tasks" view shows no dollar amounts and no cost
  per task; dollar amounts are in each set's table.

Full detail, including the exact bootstrap procedure and the retry policy, is in
[`METHODOLOGY.md`](METHODOLOGY.md). Every number below can be recomputed from
[`results/runs.csv`](results/runs.csv):

```
python analysis/build_results.py --check
```

The script uses only the Python standard library and compares its output with the
published `results/results_*.json` files.

## Results

In the per-set tables, savings are relative to the baseline of the same set, and a
negative saving means the tool cost more than baseline. Pass rate is passed runs out
of graded runs. The "All tasks" table uses a different convention: it reports cost
change (negative = cheaper), so its Short, Chains and Custom columns are the
per-set savings below with the sign reversed.

### All tasks (each of the three sets weighted equally; 71 tasks, 142 runs per condition)

| Tool | Cost change vs baseline, sets weighted equally (95% range) | Short | Chains | Custom | Mean pass rate | Value index (baseline = 100) |
|---|---|---:|---:|---:|---:|---:|
| Baseline | reference | - | - | - | 81.1% | 100 |
| graphify 0.9.95 | -0.4% (-7.3% to +6.9%) | -0.7% | -11.4% | +10.9% | 74.6% | 99 |
| ponytail 5.3.2 | -18.3% (-21.8% to -14.3%) | -9.0% | -29.6% | -16.1% | 77.6% | 116 |
| caveman 1f951fb | +7.7% (-4.4% to +23.1%) | +7.1% | -9.3% | +25.5% | 80.1% | 93 |
| rtk 1.0.1 | +4.2% (-5.1% to +15.3%) | +0.5% | -0.3% | +12.3% | 74.9% | 94 |

Figures may not sum because of rounding. The mean pass rate is the mean of the three
per-set pass rates. It has no interval, and the 6 custom runs per condition make up a
third of it: one custom run moves it by about 5.6 points. The "All tasks" view has no
dollar amounts; they are in the three set tables below.

### Short tasks (55 tasks, 110 runs per condition)

| Tool | Cost/task | Saving vs baseline (95% interval) | Pass rate | Quality | Value |
|---|---:|---|---:|---:|---:|
| Baseline | $0.141 | reference | 97/110 (88.2%) | 96.6 | 686 |
| graphify 0.9.95 | $0.140 | +0.7% (-4.4% to +5.9%) | 98/110 (89.1%) | 95.6 | 684 |
| ponytail 5.3.2 | $0.128 | +9.0% (+4.4% to +13.4%) | 98/110 (89.1%) | 96.1 | 750 |
| caveman 1f951fb | $0.151 | -7.1% (-12.0% to -2.9%) | 98/110 (89.1%) | 96.8 | 642 |
| rtk 1.0.1 | $0.141 | -0.5% (-4.6% to +3.9%) | 99/110 (90.0%) | 96.2 | 680 |

### SWE-bench Pro chains (13 chains, 26 runs per condition)

| Tool | Cost/task | Saving vs baseline (95% interval) | Pass rate | Quality | Value |
|---|---:|---|---:|---:|---:|
| Baseline | $1.724 | reference | 23/26 (88.5%) | 88.5 | 51 |
| graphify 0.9.95 | $1.528 | +11.4% (+6.6% to +16.6%) | 22/26 (84.6%) | 84.6 | 55 |
| ponytail 5.3.2 | $1.213 | +29.6% (+21.2% to +37.1%) | 20/26 (76.9%) | 76.9 | 63 |
| caveman 1f951fb | $1.563 | +9.3% (-2.9% to +20.1%) | 22/26 (84.6%) | 84.6 | 54 |
| rtk 1.0.1 | $1.719 | +0.3% (-7.2% to +8.2%) | 22/26 (84.6%) | 84.6 | 49 |

### Custom long tasks (3 tasks, 6 runs per condition)

| Tool | Cost/task | Saving vs baseline (95% interval) | Pass rate | Quality | Value |
|---|---:|---|---:|---:|---:|
| Baseline | $11.802 | reference | 4/6 (66.7%) | 94.1 | 8 |
| graphify 0.9.95 | $13.088 | -10.9% (-34.2% to +8.2%) | 3/6 (50.0%) | 92.0 | 7 |
| ponytail 5.3.2 | $9.897 | +16.1% (+6.7% to +22.9%) | 4/6 (66.7%) | 91.3 | 9 |
| caveman 1f951fb | $14.807 | -25.5% (-71.8% to +3.7%) | 4/6 (66.7%) | 93.7 | 6 |
| rtk 1.0.1 | $13.251 | -12.3% (-49.1% to +15.8%) | 3/6 (50.0%) | 92.2 | 7 |

### Reading the tables

* A 95% interval or range is the span of cost changes that the bootstrap resampling
  of tasks produced for the middle 95% of resamples. If it contains 0%, the
  difference between the tool and baseline could be noise, and the sweep does not
  show a cost difference. This applies to graphify, caveman and rtk in the custom
  set, to caveman and rtk in the chains, and to graphify and rtk in the short
  tasks. In the "All tasks" view, only ponytail's range excludes 0.
* In the "All tasks" view each set counts equally, so the 3 custom tasks carry the
  same weight as the 55 short tasks. The custom set's bootstrap range is coarse
  because 3 tasks give few distinct resamples.
* The tables show cost and outcome, not the reason for either. The benchmark did
  not measure why a tool's cost differs from baseline.
* Pass-rate differences are small in absolute terms (for example 23/26 vs 20/26 in
  the chains) and no significance test was run on them. See the limitations below.

## Limitations

* **One model and one agent.** All results are for Claude Sonnet 5.5 (high effort) in
  Claude Code 2.1.293. They say nothing about other models, other agents or other
  versions of these tools.
* **Small custom set.** The custom set is 3 tasks, 6 runs per condition. Its
  intervals are wide (about 16 to 76 percentage points) and individual results move a
  lot with a single run. The 13-chain set is also small.
* **Short tasks have low power.** They are cheap for every condition, and savings
  of about 5% or less are within noise.
* **Two trials per task.** The bootstrap resamples tasks, so the intervals reflect
  variation between tasks. Run-to-run variation inside a task is only partly
  captured by averaging two trials.
* **Baseline from the same sweep.** Savings are measured against baseline runs made
  during the same sweep (October 2026). Provider pricing, model behaviour and
  tool versions may change; a later baseline may differ.
* **Cost, not wall-clock time.** Only provider-reported cost and pass/fail grading
  are analysed here. A tool that is cheaper per run is not necessarily faster.
* **Strict chain grading.** A chain run passes only if every test passes. A failed
  chain run in this sweep missed between 1 and 4 tests (at most about 1.2% of the
  chain's tests). The grader also records a looser flag (at least 90% of tests
  passing); under that flag all 130 chain runs pass. The published pass rates use
  the strict rule. Details are in [`METHODOLOGY.md`](METHODOLOGY.md).
* **Hidden tests and fixture repositories are not published.** See
  [`tasks/README.md`](tasks/README.md). The repository therefore documents the
  grading but cannot, by itself, re-run the short and custom tasks.
* **Not a recommendation.** Cost savings and quality are reported separately because
  they can trade off; which matters more depends on the use.

## Repository layout

```
README.md             this file
METHODOLOGY.md        detailed definitions, retry policy, grading, known caveats
results/              published numbers: results_{all,short,pro,custom}.json,
                      per-run runs.csv (710 rows), derived summary.json and tables.md
analysis/             extract_runs.py (raw sweep -> runs.csv), build_results.py (runs.csv -> tables)
tasks/                task definitions (see tasks/README.md)
harness/              reference copy of the sweep driver, agent configurations and scoring code
```

## Reproducing the tables

```
python analysis/build_results.py --check     # recompute everything from results/runs.csv
python analysis/extract_runs.py --sweep <raw sweep dir> --out results/runs.csv   # needs the raw sweep files
```

Python 3.11 or newer; no third-party packages are needed for the analysis scripts.

## License

* Code in this repository (`analysis/`, `harness/` and the scripts under `tasks/`
  written for TokenBench) is released under the MIT License: see [`LICENSE`](LICENSE).
* The contents of `results/` and the TokenBench-written task text in
  `tasks/short_tasks.jsonl` and `tasks/custom_tasks.jsonl` are released under
  Creative Commons Attribution 4.0 International: see [`LICENSE-DATA.md`](LICENSE-DATA.md).
* Third-party material, including everything under `tasks/swe_bench_pro_chains/`
  (`chain.json`, `validation.json`, `grade.py`, `container/`), which derives from
  SWE-bench Pro, keeps its upstream terms and is not covered by the licences above: see
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
