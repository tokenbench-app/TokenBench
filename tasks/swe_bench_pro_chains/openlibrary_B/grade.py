"""Grade a workspace for this SWE-bench Pro chain task (Docker required).

usage:
  python grade.py <workspace_dir>            # diff the workspace against its root commit (the base) and grade it
  python grade.py --patch <file.patch>       # grade a patch file (e.g. gold.patch); use --patch "" for the bare base
options: --keep (keep container), --raw-out <dir> (copy container outputs), --runs N (repeat, for flake checks)
Prints one JSON object: f2p_pass/f2p_total/p2p_pass/p2p_total/ok plus per-task detail.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHAIN = json.loads((HERE / "chain.json").read_text(encoding="utf-8"))


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)


def workspace_patch(ws):
    ws = Path(ws)
    root = sh(["git", "-C", str(ws), "rev-list", "--max-parents=0", "HEAD"], text=True).stdout.split()
    if not root:
        sys.exit("workspace has no git root commit (expected the base snapshot committed as the first commit)")
    sh(["git", "-C", str(ws), "add", "-A"])
    return sh(["git", "-C", str(ws), "-c", "core.autocrlf=false", "diff", "--cached", "--binary", root[-1]]).stdout


def run_container(patch_bytes, keep=False, raw_out=None, skip_testpatch=False):
    tmp = Path(tempfile.mkdtemp(prefix="chaingrade_"))
    shutil.copytree(HERE / "container", tmp / "chain")
    (tmp / "chain" / "model.patch").write_bytes(patch_bytes or b"")
    if skip_testpatch:  # used only by validation (pre-existing tests on the bare base -> derived pass_to_pass)
        (tmp / "chain" / "skip_testpatch").write_bytes(b"")
    name = f"tbchain_{CHAIN['chain_id']}_{uuid.uuid4().hex[:8]}"
    img = CHAIN["docker_image"]
    if sh(["docker", "image", "inspect", img]).returncode:
        p = sh(["docker", "pull", "-q", img])
        if p.returncode:
            sys.exit(f"docker pull failed: {p.stderr[:300]}")
    p = sh(["docker", "run", "-d", "--name", name, "--entrypoint", "sleep", img, "infinity"], text=True)
    if p.returncode:
        sys.exit(f"docker run failed: {p.stderr[:300]}")
    try:
        sh(["docker", "cp", str(tmp / "chain"), f"{name}:/chain"])
        # docker cp from Windows keeps CRLF-free bytes; make scripts executable
        sh(["docker", "exec", name, "bash", "-lc", "chmod -R +x /chain && chown -R root /chain"])
        r = sh(["docker", "exec", name, "bash", "/chain/run_chain.sh"], timeout=7200)
        out = tmp / "out"
        sh(["docker", "cp", f"{name}:/chain/out", str(out)])
        if raw_out:
            shutil.copytree(out, raw_out, dirs_exist_ok=True)
        return out, r.returncode
    finally:
        if not keep:
            sh(["docker", "rm", "-f", name])


def status_map(out, k):
    f = out / f"{k}.json"
    if not f.exists():
        return {}
    try:
        tests = json.loads(f.read_text(encoding="utf-8", errors="replace")).get("tests", [])
    except Exception:
        return {}
    m = {}
    for t in tests:
        n, s = t.get("name", ""), t.get("status")
        if m.get(n) != "PASSED":  # duplicate names: count a test passed if any instance passed
            m[n] = s
    return m


def score(out, rc):
    apply = (out / "apply.txt").read_text().split() if (out / "apply.txt").exists() else ["missing"]
    tree = (out / "tree.txt").read_text().strip() if (out / "tree.txt").exists() else ""
    res = dict(chain_id=CHAIN["chain_id"], apply=apply, base_tree_match=(tree == CHAIN["base_tree"]), tasks=[])
    F = Fp = P = Pp = 0
    resolved = 0
    for t in CHAIN["tasks"]:
        m = status_map(out, t["k"])
        f2p = t["fail_to_pass"]; p2p = list(dict.fromkeys(t["pass_to_pass"] + t.get("p2p_derived", [])))
        fp = sum(m.get(x) == "PASSED" for x in f2p)
        pp = sum(m.get(x) == "PASSED" for x in p2p)
        ds_ok = all(m.get(x) == "PASSED" for x in f2p + t["pass_to_pass"])
        resolved += ds_ok
        res["tasks"].append(dict(k=t["k"], instance_id=t["instance_id"], f2p_pass=fp, f2p_total=len(f2p), p2p_pass=pp,
                                 p2p_total=len(p2p), resolved=ds_ok,
                                 f2p_failed=[x for x in f2p if m.get(x) != "PASSED"][:30],
                                 p2p_failed=[x for x in p2p if m.get(x) != "PASSED"][:30]))
        F += len(f2p); Fp += fp; P += len(p2p); Pp += pp
    ff = Fp / F if F else 1.0
    pf = Pp / P if P else 1.0
    res.update(f2p_pass=Fp, f2p_total=F, p2p_pass=Pp, p2p_total=P, f2p_frac=round(ff, 4), p2p_frac=round(pf, 4),
               tasks_resolved=resolved, n_tasks=len(CHAIN["tasks"]),
               ok=bool(ff >= 0.9 and pf >= 0.9 and "testpatch_failed" not in apply and "failed" not in apply),
               all_pass=bool(Fp == F and Pp == P))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", nargs="?")
    ap.add_argument("--patch")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--raw-out")
    a = ap.parse_args()
    if a.patch is not None:
        pb = Path(a.patch).read_bytes() if a.patch else b""
    elif a.workspace:
        pb = workspace_patch(a.workspace)
    else:
        ap.error("workspace or --patch required")
    out, rc = run_container(pb, keep=a.keep, raw_out=a.raw_out)
    print(json.dumps(score(out, rc)))


if __name__ == "__main__":
    main()
