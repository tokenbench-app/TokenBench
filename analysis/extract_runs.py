"""Flatten the raw sweep result files into one per-run CSV (results/runs.csv).

The sweep driver wrote three JSONL files (one row per finished run) plus one
grade file for the SWE-bench Pro chains. This script joins them and keeps only
the five published arms. It needs the raw sweep directory, which is not part
of this repository, so most readers will not run it; the CSV it produced is
committed as results/runs.csv and is the input of build_results.py.

Usage:
    python analysis/extract_runs.py --sweep <sweep dir> --out results/runs.csv

Expected files inside the sweep directory:
    b1_results.jsonl        55 short tasks
    pro/pro_results.jsonl   13 SWE-bench Pro chains (agent runs)
    pro/pro_grades.jsonl    grader output for the chains (may hold retries)
    b3_results.jsonl        3 custom long tasks
"""
from __future__ import annotations

import argparse
import csv
import json
import os

ARMS = ("baseline", "graphify", "ponytail", "caveman", "rtk")
COLUMNS = ["set", "task", "repo", "arm", "trial", "run_id", "passed", "quality",
           "cost_usd", "wall_s", "total_tokens", "needs_cleanup", "graded",
           "tests_failed", "tests_total", "pooled90_flag"]


def load(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def arm_of(row):
    arm = row["arm"]
    return "baseline" if arm in ("base", "baseline") else arm


def harness_rows(rows, set_name):
    out = []
    for r in rows:
        if arm_of(r) not in ARMS:
            continue
        out.append({
            "set": set_name, "task": r["task_id"], "repo": r["repo_id"], "arm": arm_of(r),
            "trial": r["trial"], "run_id": r["run_id"], "passed": int(bool(r["success"])),
            "quality": r["quality"], "cost_usd": r["cost_usd"], "wall_s": r["wall_s"],
            "total_tokens": r.get("all_thread_tokens") or 0,
            "needs_cleanup": int(bool(r.get("needs_cleanup"))), "graded": 1,
            "tests_failed": "", "tests_total": "", "pooled90_flag": "",
        })
    return out


def chain_rows(results, grades):
    # A chain can have several grader rows (e.g. an infrastructure error and then
    # a successful regrade). The last row that actually graded the chain wins.
    graded = {}
    for g in grades:
        if g.get("f2p_total") is not None:
            graded[(g["chain"], g["arm"], g["trial"])] = g
    out = []
    for r in results:
        if arm_of(r) not in ARMS:
            continue
        g = graded.get((r["chain"], r["arm"], r["trial"])) or {}
        passed = bool(g.get("all_pass"))
        tokens = ((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
                  + (r.get("cache_read") or 0) + (r.get("cache_create") or 0))
        out.append({
            "set": "pro", "task": r["chain"], "repo": r["chain"].rsplit("_", 1)[0],
            "arm": arm_of(r), "trial": r["trial"], "run_id": r["label"],
            "passed": int(passed), "quality": 100.0 if passed else 0.0,
            "cost_usd": r["cost_usd"], "wall_s": r["seconds"], "total_tokens": tokens,
            "needs_cleanup": 0, "graded": int(bool(g)),
            # chains only: failing tests among all fail-to-pass + pass-to-pass tests of the chain,
            # and the grader's looser flag (>= 90% of each pooled) that the published results do NOT use
            "tests_failed": (g["f2p_total"] - g["f2p_pass"] + g["p2p_total"] - g["p2p_pass"]) if g else "",
            "tests_total": (g["f2p_total"] + g["p2p_total"]) if g else "",
            "pooled90_flag": int(bool(g.get("ok"))) if g else "",
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", required=True)
    ap.add_argument("--out", default=os.path.join("results", "runs.csv"))
    a = ap.parse_args()
    s = a.sweep
    rows = (harness_rows(load(os.path.join(s, "b1_results.jsonl")), "short")
            + chain_rows(load(os.path.join(s, "pro", "pro_results.jsonl")),
                         load(os.path.join(s, "pro", "pro_grades.jsonl")))
            + harness_rows(load(os.path.join(s, "b3_results.jsonl")), "custom"))
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: (repr(r[k]) if isinstance(r[k], float) else r[k]) for k in COLUMNS})
    print(f"wrote {len(rows)} rows to {a.out}")


if __name__ == "__main__":
    main()
