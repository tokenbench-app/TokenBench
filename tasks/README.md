# Task definitions

| Path | Content |
|---|---|
| `short_tasks.jsonl` | the 55 short tasks: id, repository, category, difficulty, issue title, the task statement given to the agent, skills tested, runtime allowance |
| `custom_tasks.jsonl` | the 3 custom long tasks, same fields plus the expected-behaviour list that is appended to the agent's task statement |
| `swe_bench_pro_chains/<chain>/` | the 13 SWE-bench Pro chains: `chain.json` (instance ids, base commit and tree, docker image, per-task fail-to-pass and pass-to-pass test lists), `grade.py` (grader), `container/` (verifier scripts that run inside the docker image), `validation.json` (reference-patch and bare-base results) |

## What is not included

* **Hidden tests and fixture repositories.** The short and custom tasks run against
  eight repositories (six purpose-built applications and two open-source projects:
  [SharpTS](https://github.com/nickna/SharpTS) at commit
  `33f266f97fcdf2f37f438f4eedaed3b4e7ce05d7` and
  [mvm](https://github.com/mvm-sh/mvm) at commit
  `f0d101dad05ec263ed1f195bee51ff2f4a7a7925`) and are graded by hidden tests. The
  repositories and hidden tests are not part of this repository, so these tasks
  cannot be re-run from it. They are kept out so that the hidden tests stay unseen. The scoring code that turns test results into a score is in
  [`../harness/tokenbench/scoring/`](../harness/tokenbench/scoring/).
* **SWE-bench Pro issue text and test patches.** The agent prompt of a chain is the
  problem statement, requirements and interface of each of its issues from
  [ScaleAI/SWE-bench_Pro](https://huggingface.co/datasets/ScaleAI/SWE-bench_Pro),
  introduced by "This repository has N separate open issues. Resolve all of them."
  and one `=== Issue k of N ===` section per issue. That text, and the hidden test patches (`test.patch`), derive from
  the upstream repositories and are not redistributed here. They can be rebuilt from
  the `instance_id` of each task in `chain.json`, which identifies the dataset row;
  `grade.py` expects the merged test patches as `container/test.patch`.
  The per-task `run_script.sh`, `parser.py` and `args.txt` files under `container/`
  come from the SWE-bench Pro harness (MIT).
* **The agent-side workspaces.** For a chain, the workspace is the upstream
  repository checked out at `base_commit`, with history replaced by a single commit
  (see `grade.py`, which diffs the final workspace against that commit).

## Chain success rule

`chain.json` contains a field `success_rule` for a looser flag (`ok`: at least 90% of
pooled fail-to-pass tests and of pooled pass-to-pass tests). The published results
use the stricter `all_pass` rule, recorded in `published_success_rule`. See
[`../METHODOLOGY.md`](../METHODOLOGY.md#4-grading-and-success).
