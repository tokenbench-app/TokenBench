"""Detached grader for the chain block (SWE-bench Pro chains) patches. Sequential; grades only when Docker responds.
Never restarts Docker. Waits while another grade.py is running so only one chain container runs at a time. Removes a chain's image after all
of that chain's sweep patches are graded, but only if this grader pulled it (snapshot of images taken first).
usage: python grade_sweep.py [--once]  -> <sweep dir>/pro/pro_grades.jsonl, log <sweep dir>/logs/grader.log"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
import psutil

SW = Path(os.environ.get("TB_SWEEP_DIR", os.path.join(tempfile.gettempdir(), "tokenbench-sweep"))); PRO = SW / "pro"
CH = Path(os.environ.get("TB_REPO", ".")) / "benchmark" / "pro_chains"  # chain definitions (tasks/swe_bench_pro_chains in this repository)
PY = sys.executable
GRADES = PRO / "pro_grades.jsonl"
MIN_FREE = 7.0


def log(m):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}"
    (SW / "logs").mkdir(parents=True, exist_ok=True)
    open(SW / "logs" / "grader.log", "a", encoding="utf-8").write(line + "\n"); print(line, flush=True)


def rows(f):
    out = []
    if f.exists():
        for l in f.read_text(encoding="utf-8", errors="replace").splitlines():
            try: out.append(json.loads(l))
            except Exception: pass
    return out


def docker_ok():
    try:
        return subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, timeout=25).returncode == 0
    except Exception:
        return False


def other_grader_busy():
    me = os.getpid()
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cl = " ".join(p.info["cmdline"] or [])
        except Exception:
            continue
        if p.info["pid"] != me and "grade.py" in cl and "pro_chains" in cl and str(PRO) not in cl:
            return True
    return False


def free_gb():
    import shutil
    return shutil.disk_usage(SW.anchor or str(SW)).free / 1e9


def pending():
    graded = {g["label"] for g in rows(GRADES) if g.get("f2p_total") is not None}
    fails = {}
    for g in rows(GRADES):
        if g.get("f2p_total") is None:
            fails[g["label"]] = fails.get(g["label"], 0) + 1
    res = rows(PRO / "pro_results.jsonl")
    todo = [r for r in res if r["label"] not in graded and fails.get(r["label"], 0) < 3
            and (PRO / "patches" / f"{r['label']}.patch").exists()]
    return sorted(todo, key=lambda r: (r["chain"], r["label"])), res, graded


def images():
    p = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], capture_output=True, text=True, timeout=120)
    return set(p.stdout.split()) if p.returncode == 0 else None


def grade(r):
    c, label = r["chain"], r["label"]
    raw = PRO / "grade_out" / label
    cmd = [PY, str(CH / c / "grade.py"), "--patch", str(PRO / "patches" / f"{label}.patch"), "--raw-out", str(raw)]
    t = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=7800)
        out, rc = p.stdout, p.returncode
        err = p.stderr
    except subprocess.TimeoutExpired:
        out, rc, err = "", "timeout", ""
    try:
        g = json.loads(out.strip().splitlines()[-1])
    except Exception:
        row = dict(label=label, chain=c, arm=r["arm"], trial=r["trial"], f2p_total=None, error=f"rc={rc} {(out + err)[-600:]}")
        open(GRADES, "a", encoding="utf-8").write(json.dumps(row) + "\n")
        log(f"[{label}] grade FAILED rc={rc}: {(out + err)[-300:]}"); return
    row = dict(label=label, chain=c, arm=r["arm"], trial=r["trial"], f2p=f"{g['f2p_pass']}/{g['f2p_total']}", p2p=f"{g['p2p_pass']}/{g['p2p_total']}",
               f2p_pass=g["f2p_pass"], f2p_total=g["f2p_total"], p2p_pass=g["p2p_pass"], p2p_total=g["p2p_total"],
               f2p_frac=g["f2p_frac"], p2p_frac=g["p2p_frac"], tasks_resolved=g["tasks_resolved"], n_tasks=g["n_tasks"],
               ok=g["ok"], all_pass=g["all_pass"], apply=g["apply"], base_tree_match=g.get("base_tree_match"), grade_s=round(time.time() - t),
               failed=[(x["k"], x["f2p_failed"][:8], x["p2p_failed"][:8]) for x in g["tasks"] if x["f2p_failed"] or x["p2p_failed"]])
    open(GRADES, "a", encoding="utf-8").write(json.dumps(row) + "\n")
    log(f"[{label}] graded f2p {row['f2p']} p2p {row['p2p']} resolved {row['tasks_resolved']}/{row['n_tasks']} ok={row['ok']} ({row['grade_s']}s)")


def main():
    once = "--once" in sys.argv
    snap = SW / "grader_images_before.json"
    while True:
        stop = (SW / "STOP").read_text(errors="replace") if (SW / "STOP").exists() else ""
        if "KILL" in stop.upper():
            log("STOP KILL present: grader exiting"); return
        todo, res, graded = pending()
        driver_done = (SW / "DONE").exists()
        if not todo and driver_done:
            log("all graded"); return
        if todo and docker_ok():
            if not snap.exists():
                im = images()
                if im is not None:
                    snap.write_text(json.dumps(sorted(im)), encoding="utf-8")
            before = set(json.loads(snap.read_text())) if snap.exists() else set()
            for r in todo:
                if (SW / "STOP").exists() and "KILL" in (SW / "STOP").read_text(errors="replace").upper():
                    return
                while other_grader_busy():
                    log("another grade.py is running; waiting"); time.sleep(120)
                if free_gb() < MIN_FREE:
                    log(f"disk free {free_gb():.1f} GB < {MIN_FREE}; deferring grading"); break
                if not docker_ok():
                    log("docker stopped responding"); break
                log(f"[{r['label']}] grading")
                grade(r)
                # chain fully graded (all 10 sweep runs) -> drop its image if this grader pulled it
                _, res2, graded2 = pending()
                labels = [x["label"] for x in res2 if x["chain"] == r["chain"]]
                img = json.loads((CH / r["chain"] / "chain.json").read_text(encoding="utf-8"))["docker_image"]
                if len(labels) >= 10 and all(l in graded2 for l in labels) and img not in before:
                    p = subprocess.run(["docker", "rmi", img], capture_output=True, text=True, timeout=600)
                    log(f"rmi {img}: rc={p.returncode}")
        elif todo:
            log(f"docker not responding; {len(todo)} pending")
        if once:
            return
        time.sleep(600)


if __name__ == "__main__":
    main()
