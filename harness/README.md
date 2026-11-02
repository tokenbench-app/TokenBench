# Harness (reference copy)

**This directory is a reference copy for auditing. It imports modules that are not
included and it is not runnable on its own.** It contains the parts of the harness
that define the measurement, for reading and checking; the full harness has run
orchestration, a database and reporting code that are not needed to understand the
numbers and are not included.

| Path | Content |
|---|---|
| `sweep/driver.py` | the sweep driver: builds the run queue (task shuffle seed, condition rotation, two trials), launches the agent for each run, classifies each run as valid, transient, invalid or timed out, applies the retry rules, and appends accepted runs to `b1_results.jsonl` (short), `b3_results.jsonl` (custom) and `pro_results.jsonl` (chains) |
| `sweep/grade_sweep.py` | grades chain patches one at a time in the docker verifier and writes `pro_grades.jsonl` |
| `sweep/sweep_env.py` | environment setup used by both scripts |
| `agents/*.json` | the base Claude Code command line and environment of each condition (flags added by modules that are not included, such as `--strict-mcp-config`, are not in these files); `rtk_settings.json` registers the rtk hook |
| `tokenbench/scoring/` | scoring: quality formula (`quality.py`), success thresholds (`scorer.py`), test-output parsing, structure scoring for reorganisation tasks |
| `tokenbench/telemetry/provider_usage.py` | reads the provider-reported cost and token usage from Claude Code's JSON result |
| `tokenbench/runners/prompt_builder.py` | the agent prompt template for the short and custom tasks |
| `tokenbench/runners/graphify_build.py` | the graphify graph build |
| `tokenbench/manifests/schema.py` | the task manifest schema |

Machine-specific values are placeholders or read from environment variables:
`<PLUGIN_CACHE>` in the agent files is the directory where the ponytail and caveman
plugins were unpacked, `<REPO>` is the repository root, and the driver reads
`TB_REPO`, `TB_SWEEP_DIR`, `TB_ISO_ROOT`, `TB_PRO_REPOS`, `TB_SUITE` and
`TB_TOOLCHAIN`. Where a directory variable is not set, a directory under the system
temp folder is used.
The sweep was run on Windows 10 and the driver uses the Windows registry to build a
clean `PATH`.

The sweep driver imports modules of the full harness (`tokenbench.cli`,
`tokenbench.runners.cli_agent`) that are not part of this copy, so it cannot be run
from this directory alone.

## Block labels in `sweep/driver.py`

The driver uses internal labels that predate the published set names:

| Label | Block | What it covers | Files |
|---|---|---|---|
| `b1` (`block=1`) | short block | the 55 short tasks | `b1_results.jsonl`, `b1_attempts.jsonl` |
| `b2` (`block=2`) | chain block | the 13 SWE-bench Pro chains | `pro_results.jsonl`, `pro_attempts.jsonl` |
| `b3` (`block=3`) | custom block | the 3 custom long tasks | `b3_results.jsonl` (attempts of this block are written to `b1_attempts.jsonl` with `block` 3) |

Short and custom runs share the same code path (`run_b1`, `b1_classify`), and the
custom block differs by its 4-hour run limit. Chain runs use `run_b2` and a 90-minute
limit. Time limits are described in section 6 of [`../METHODOLOGY.md`](../METHODOLOGY.md).

## Re-running

Every agent configuration in `agents/` runs Claude Code with
`--dangerously-skip-permissions`, so the agent can read, write and delete files and
run commands without asking. Runs therefore belong in a disposable sandbox or
virtual machine, not on a machine that holds anything you want to keep.
