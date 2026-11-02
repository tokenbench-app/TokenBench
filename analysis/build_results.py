"""Recompute every published number from results/runs.csv.

    python analysis/build_results.py                 # writes results/summary.json and results/tables.md
    python analysis/build_results.py --check         # also compares against results/results_*.json

Definitions (identical to the generator that produced the published JSON files):

  unit            one task (short and custom sets) or one chain (SWE-bench Pro set)
  cost per unit   mean provider cost over the 2 trials of that unit for one tool
  cost per task   mean of the per-unit costs over all units of the set
  saving          -(sum of the tool's per-unit costs / sum of baseline per-unit costs - 1) * 100
  95% interval    bootstrap over units: resample the units with replacement 2000 times,
                  recompute the cost ratio on each resample, take the 2.5th and 97.5th
                  percentile (index int(0.025*n) and int(0.975*n) of the sorted values),
                  random.seed(7). The interval is reported as a change in cost (negative =
                  cheaper); the saving interval is its negation.
  pass rate       runs that passed / graded runs (hidden tests pass; all chain tests for chains)
  quality         mean over runs of the run score (harness score 0-100 for short and custom
                  tasks; 100 for a passing chain run and 0 otherwise)
  value           quality / cost per task

The "All tasks" view is different: it gives each of the three sets equal weight.

  cost change (All)   mean of the three per-set cost changes (short, SWE-bench Pro chains, custom
                      long), each set's change being the pooled change within that set:
                      (sum of tool per-unit costs / sum of baseline per-unit costs - 1) * 100
  95% range (All)     bootstrap, 4000 resamples, random.seed(7) set once before the loop. In each
                      resample, for each set in the order short, pro, custom, draw one
                      random.choice per unit of that set (resampling units within each set), then
                      recompute the mean of the three set changes. Sort the values and take
                      indices int(0.025*4000) and int(0.975*4000). Units are taken in order of
                      first occurrence of (set, task) in runs.csv.
  pass rate, quality  mean of the three per-set values (the published, rounded per-set values)
  value index (All)   per set, value = mean run quality / (sum of per-task mean costs / task count),
                      both unrounded, taken straight from runs.csv; index = 100 x mean over the
                      three sets of (tool value / baseline value), rounded to an integer at the
                      end only (baseline = 100). The rounded per-set values shown in the per-set
                      tables are not used.
  dollar amounts      none: the All view has no dollar amounts or per-task cost
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import random

ARMS = ["baseline", "graphify", "ponytail", "caveman", "rtk"]
SETS = ["short", "pro", "custom"]
LABEL = {"baseline": "Baseline", "graphify": "graphify 0.9.95", "ponytail": "ponytail 5.3.2",
         "caveman": "caveman 1f951fb", "rtk": "rtk 1.0.1"}


def read_runs(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    for r in rows:
        r["trial"] = int(r["trial"])
        r["passed"] = bool(int(r["passed"]))
        r["graded"] = bool(int(r["graded"]))
        r["needs_cleanup"] = bool(int(r["needs_cleanup"]))
        for k in ("quality", "cost_usd", "wall_s", "total_tokens"):
            r[k] = float(r[k])
    return rows


def unit_order(rows):
    """Short tasks sorted by (repo, task); chains and custom tasks sorted by name."""
    short = sorted({(r["repo"], r["task"]) for r in rows if r["set"] == "short"})
    return {"short": [t for _, t in short],
            "pro": sorted({r["task"] for r in rows if r["set"] == "pro"}),
            "custom": sorted({r["task"] for r in rows if r["set"] == "custom"})}


def bootstrap(per_unit, arm, n=2000):
    tasks = [t for t in per_unit if "baseline" in per_unit[t] and arm in per_unit[t]]
    random.seed(7)
    out = []
    for _ in range(n):
        s = [random.choice(tasks) for _ in tasks]
        out.append((sum(per_unit[t][arm] for t in s) / sum(per_unit[t]["baseline"] for t in s) - 1) * 100)
    out.sort()
    b = sum(per_unit[t]["baseline"] for t in tasks)
    a = sum(per_unit[t][arm] for t in tasks)
    return round((a / b - 1) * 100, 1), round(out[int(.025 * n)], 1), round(out[int(.975 * n)], 1)


def summarise(rows, units):
    by = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        by[r["arm"]][r["task"]].append(r)

    def mean(a, u, key):
        v = by[a][u]
        return sum(x[key] for x in v) / len(v) if v else None

    per_unit = {u: {a: mean(a, u, "cost_usd") for a in ARMS if by[a][u]} for u in units}
    out = {}
    for a in ARMS:
        v = [x for u in units for x in by[a][u]]
        have = [u for u in units if by[a][u]]
        tc = sum(mean(a, u, "cost_usd") for u in have)
        graded = [x for x in v if x["graded"]]
        succ = sum(1 for x in graded if x["passed"])
        q = round(sum(x["quality"] for x in v) / len(v), 2)
        cpt = round(tc / len(have), 4)
        rec = {"tool": a, "runs": len(v), "units": len(have), "cost_per_task": cpt,
               "cost_total": round(tc, 4), "passed": succ, "graded_runs": len(graded),
               "pass_rate": round(100 * succ / len(graded), 1), "quality": q,
               "value": round(q / (tc / len(have)), 0),
               "wall_per_run_s": round(sum(x["wall_s"] for x in v) / len(v)),
               "needs_cleanup_runs": sum(1 for x in v if x["needs_cleanup"])}
        if a == "baseline":
            rec["savings_pct"] = 0.0
        else:
            chg, lo, hi = bootstrap(per_unit, a)
            rec.update({"savings_pct": round(-chg, 1), "change_ci_low": lo, "change_ci_high": hi})
        out[a] = rec
    return out


ALL_B = 4000


def summarise_all(rows, set_summaries):
    """Equal-weight-per-set view. set_summaries: output of summarise() for short, pro, custom."""
    cost = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        cost[(r["set"], r["task"])][r["arm"]].append(r["cost_usd"])
    unit_cost = {k: {a: sum(v) / len(v) for a, v in d.items()} for k, d in cost.items()}  # first-occurrence order
    by_set = {s: [k for k in unit_cost if k[0] == s] for s in SETS}

    def pooled(keys, a):
        return 100 * (sum(unit_cost[k][a] for k in keys) / sum(unit_cost[k]["baseline"] for k in keys) - 1)

    def set_value(s, a):
        """Unrounded value of one set: mean run quality / mean per-task cost."""
        rr = [r for r in rows if r["set"] == s and r["arm"] == a]
        q = sum(r["quality"] for r in rr) / len(rr)
        keys = by_set[s]
        return q / (sum(unit_cost[k][a] for k in keys) / len(keys))

    def equal_weight(keys_by_set, a):
        return sum(pooled(keys_by_set[s], a) for s in SETS) / len(SETS)

    tools = [a for a in ARMS if a != "baseline"]
    random.seed(7)
    boot = {a: [] for a in tools}
    for _ in range(ALL_B):
        ks = {s: [random.choice(by_set[s]) for _ in by_set[s]] for s in SETS}
        for a in tools:
            boot[a].append(equal_weight(ks, a))
    out = {}
    for a in ARMS:
        per = [set_summaries[s][a] for s in SETS]
        base = [set_summaries[s]["baseline"] for s in SETS]
        rec = {"tool": a, "runs": sum(p["runs"] for p in per), "units": sum(p["units"] for p in per),
               "passed": sum(p["passed"] for p in per), "graded_runs": sum(p["graded_runs"] for p in per),
               "pass_rate": round(sum(p["pass_rate"] for p in per) / 3, 1),
               "quality": round(sum(p["quality"] for p in per) / 3, 2),
               "value": round(100 * sum(set_value(s, a) / set_value(s, "baseline") for s in SETS) / 3, 0),
               "set_savings": {s: (round(-pooled(by_set[s], a), 1) if a != "baseline" else 0.0) for s in SETS},
               "set_pass_rate": {s: set_summaries[s][a]["pass_rate"] for s in SETS}}
        if a == "baseline":
            rec["savings_pct"] = 0.0
        else:
            b = sorted(boot[a])
            rec.update({"savings_pct": round(-equal_weight(by_set, a), 1),
                        "change_ci_low": round(b[int(.025 * ALL_B)], 1),
                        "change_ci_high": round(b[int(.975 * ALL_B)], 1)})
        out[a] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=os.path.join("results", "runs.csv"))
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    rows = read_runs(a.runs)
    order = unit_order(rows)
    views = {s: [r for r in rows if r["set"] == s] for s in SETS}
    views["all"] = rows
    order["all"] = order["short"] + order["pro"] + order["custom"]
    assert len(order["all"]) == len(set(order["all"]))
    summary = {k: summarise(views[k], order[k]) for k in SETS}
    summary["all"] = summarise_all(rows, summary)
    summary = {k: summary[k] for k in ("all", "short", "pro", "custom")}
    json.dump(summary, open(os.path.join(a.outdir, "summary.json"), "w"), indent=1)
    lines = []
    names = {"all": "All tasks", "short": "Short tasks", "pro": "SWE-bench Pro chains", "custom": "Custom long tasks"}
    for k in ("all", "short", "pro", "custom"):
        if k == "all":
            lines += [f"### {names[k]} ({len(order[k])} units, {summary[k]['baseline']['runs']} runs per tool, "
                      "each set weighted equally)", "",
                      "Cost change vs baseline: negative = cheaper. Set columns show each set's own pooled cost change.", "",
                      "| Tool | Cost change vs baseline, sets weighted equally (95% range) | Short | Chains | Custom "
                      "| Mean pass rate | Value index (baseline = 100) |",
                      "|---|---|---:|---:|---:|---:|---:|"]
            for t in ARMS:
                r = summary[k][t]
                if t == "baseline":
                    chg, ss = "reference", ["-"] * 3
                else:
                    chg = (f"{-r['savings_pct']:+.1f}% ({r['change_ci_low']:+.1f}% to {r['change_ci_high']:+.1f}%)")
                    ss = [f"{-r['set_savings'][s]:+.1f}%" for s in SETS]
                lines.append(f"| {LABEL[t]} | {chg} | {ss[0]} | {ss[1]} | {ss[2]} | "
                             f"{r['pass_rate']:.1f}% | {r['value']:.0f} |")
            lines.append("")
            continue
        lines += [f"### {names[k]} ({len(order[k])} units, {summary[k]['baseline']['runs']} runs per tool)", "",
                  "| Tool | Cost/task | Saving vs baseline (95% interval) | Pass rate | Quality | Value |",
                  "|---|---:|---|---:|---:|---:|"]
        for t in ARMS:
            r = summary[k][t]
            sav = "reference" if t == "baseline" else \
                f"{r['savings_pct']:+.1f}% ({-r['change_ci_high']:+.1f}% to {-r['change_ci_low']:+.1f}%)"
            lines.append(f"| {LABEL[t]} | ${r['cost_per_task']:.3f} | {sav} | "
                         f"{r['passed']}/{r['graded_runs']} ({r['pass_rate']:.1f}%) | {r['quality']:.1f} | {r['value']:.0f} |")
        lines.append("")
    open(os.path.join(a.outdir, "tables.md"), "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))
    if a.check:
        bad = 0
        for k in ("all", "short", "pro", "custom"):
            pub = json.load(open(os.path.join(a.outdir, f"results_{k}.json")))
            cost = {r["condition"]: r for r in pub["leaderboards"]["cost"]}
            qual = {r["condition"]: r for r in pub["leaderboards"]["quality"]}
            for t in ARMS:
                cid = "__baseline__" if t == "baseline" else "sonnet_" + t
                mine, c, q = summary[k][t], cost[cid], qual[cid]
                pairs = [("runs", c["runs"]), ("passed", c["successes"]), ("graded_runs", c["graded_runs"]),
                         ("savings_pct", c["savings_pct"]), ("quality", q["quality_score"]),
                         ("value", q["value_per_usd"]), ("pass_rate", q["success_rate"])]
                if k == "all":
                    for s in SETS:
                        pairs.append((f"set_savings.{s}", c["set_savings"][s]))
                        pairs.append((f"set_success.{s}", q["set_success"][s]))
                    for f in ("cost_per_task", "cost_total"):
                        if c[f] is not None:
                            bad += 1
                            print("MISMATCH", k, t, f, "All view must carry no dollar amounts", c[f])
                else:
                    pairs += [("cost_per_task", c["cost_per_task"]), ("cost_total", c["cost_total"])]
                if t != "baseline":
                    pairs += [("change_ci_low", c["change_ci_low"]), ("change_ci_high", c["change_ci_high"])]
                for key, want in pairs:
                    if key.startswith("set_savings."):
                        got = mine["set_savings"][key.split(".")[1]]
                    elif key.startswith("set_success."):
                        got = mine["set_pass_rate"][key.split(".")[1]]
                    else:
                        got = mine[key]
                    if abs(got - want) > 1e-9:
                        bad += 1
                        print("MISMATCH", k, t, key, got, want)
        print("check:", "all published numbers reproduced" if not bad else f"{bad} mismatches")
        raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
