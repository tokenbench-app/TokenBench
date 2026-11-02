"""Sweep driver: short block (55 short tasks via tokenbench execute_run), chain block
(SWE-bench Pro chains) and custom block (3 custom long tasks).

ONE process, ONE global pool of 4 slots across all blocks. Short-block runs go through
tokenbench.cli.execute_run -- the exact function `tokenbench run-suite` dispatches per
run -- because run-suite's --graphify is process-global (cannot interleave graphify with
the other arms) and strict measurement authority refuses a second concurrent harness
process. Chain-block runs launch the same agent configs (isolated argv + harness-built allowlist env) as the short block.

Resumable: finished cells are rebuilt from runs/ (short and custom blocks) and pro_results.jsonl (chain block).
Stop: create a STOP file in the sweep directory (in-flight runs finish) or write KILL into it (in-flight runs are killed).
"""
import json, os, random, re, shutil, subprocess, sys, tempfile, threading, time, traceback
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import sweep_env
sweep_env.setup()

import psutil
import tokenbench.cli as tbc
from tokenbench.agents.loader import load_agent
from tokenbench.runners.cli_agent import child_env, with_explicit_mcp_set
from tokenbench.runners.prompt_builder import _graphify_block
from tokenbench.runners.graphify_build import build_graph

TB = Path(sweep_env.TB)
SW = Path(sweep_env.SWEEP)
RUNS = TB / "runs"
TMPDIR = Path(tempfile.gettempdir())
ISO_ROOT = Path(os.environ.get("TB_ISO_ROOT", str(TMPDIR / "tokenbench-runs")))  # isolated per-run workspaces
DISCARD = SW / "discarded"
LOGF = SW / "logs" / "driver.log"
STOPF = SW / "STOP"
DONEF = SW / "DONE"
STATUSF = SW / "status.json"
B1_RES = SW / "b1_results.jsonl"
B1_ATT = SW / "b1_attempts.jsonl"
B3_RES = SW / "b3_results.jsonl"
B3_TASKS = {"bizflow_long_ar_001": "bizflow-go-sql", "logforge_long_query_001": "logforge-cpp", "paygate_long_settlement_001": "paygate-php-portal"}
B3_TIMEOUT = 14400  # custom long tasks: 4-hour runtime cap, applied in memory only
PRO = SW / "pro"
B2_RES = PRO / "pro_results.jsonl"
B2_ATT = PRO / "pro_attempts.jsonl"
ANOM = SW / "anomalies.jsonl"
INFLIGHT_PIDS = SW / "inflight_pro_pids.json"
PIDF = SW / "driver.pid"

CONCURRENCY = 4
SUITE = Path(os.environ.get("TB_SUITE", str(TB / "benchmark" / "suites" / "short_tasks.json")))
TRIALS = 2
SEED = 55055
ARMS = {  # arm -> (condition_id, agent_id, graphify)
    "baseline": ("sweep_base", "claude_code_sonnet55_iso", False),
    "graphify": ("sweep_graphify", "claude_code_sonnet55_iso", True),
    "ponytail": ("sweep_ponytail", "claude_code_sonnet55_ponytail532", False),
    "caveman": ("sweep_caveman", "claude_code_sonnet55_caveman1f951f", False),
    "rtk": ("sweep_rtk", "claude_code_sonnet55_rtk101", False),
}
COND2ARM = {v[0]: k for k, v in ARMS.items()}
ARM_ORDER = list(ARMS)
PRO_CHAINS_DIR = TB / "benchmark" / "pro_chains"
PRO_REPOS = Path(os.environ.get("TB_PRO_REPOS", str(TMPDIR / "tokenbench-pro-repos")))
PRO_TIMEOUT = 5400
PRO_PREFIX = "p55"  # Pro run label prefix
MIN_FREE_PAUSE = 4.0
MIN_FREE_TARGET = 6.0
G = ["git", "-c", "core.autocrlf=false", "-c", "user.name=bench", "-c", "user.email=bench@local"]
MODEL = "claude-sonnet-5-5"

LOCK = threading.RLock()
WS_LOCK = threading.Lock()
STATE = dict(started=time.time(), inflight={}, pause_until=0.0, pause_reason="", events=[], stopping=False,
             spent_all_usd=0.0, last_free_space=0.0, consecutive_transient=0)


# ----------------------------------------------------------------------------- utils
def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    with LOCK:
        LOGF.parent.mkdir(parents=True, exist_ok=True)
        with open(LOGF, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        STATE["events"] = (STATE["events"] + [line])[-25:]
    print(line, flush=True)


def jappend(path, row):
    with LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")


def jrows(path):
    if not path.exists():
        return []
    out = []
    for l in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(l))
        except Exception:
            pass
    return out


def anomaly(kind, **kw):
    jappend(ANOM, dict(t=time.strftime("%Y-%m-%d %H:%M:%S"), kind=kind, **kw))
    log(f"ANOMALY {kind} {kw}")


def free_gb():
    return shutil.disk_usage(SW.anchor or str(SW)).free / 1e9


def stop_state():
    if not STOPF.exists():
        return None
    try:
        txt = STOPF.read_text(encoding="utf-8", errors="replace")
    except Exception:
        txt = ""
    return "KILL" if "KILL" in txt.upper() else "STOP"


def rjson(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def last_json_line(text):
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {}


TRANSIENT_PAT = re.compile(r"usage limit|hit your limit|rate.?limit|limit reached|overloaded|\b529\b|\b500\b|api error|"
                           r"connection (error|reset|refused)|ECONNRESET|socket hang up|internal server error|credit balance|"
                           r"timed? ?out while|network", re.I)


def limit_wait(msg, attempt):
    m = re.search(r"\|(\d{10})", msg or "")
    if m:
        return max(60, int(m.group(1)) - time.time() + 90)
    if re.search(r"usage limit|hit your limit|limit reached", msg or "", re.I):
        return 1800
    return min(900, 120 * (attempt + 1))


def global_pause(seconds, reason):
    with LOCK:
        until = time.time() + seconds
        if until > STATE["pause_until"]:
            STATE["pause_until"] = until
            STATE["pause_reason"] = reason
    log(f"GLOBAL PAUSE {seconds:.0f}s: {reason}")


# --------------------------------------------------------------------- transcript stats
def transcript_stats(tp):
    """Fixed matcher: count Bash (and PowerShell) tool calls whose command contains `python -m graphify`;
    count Agent/Task tool calls (subagent spawns)."""
    st = dict(graphify_bash=0, graphify_ps=0, graphify_ok=0, subagent_calls=0, tool_calls=0, hook_cancelled=0,
              ponytail_active=False, caveman_active=False)
    if not tp or not os.path.exists(tp):
        return None
    pend = {}
    for line in open(tp, encoding="utf-8", errors="replace"):
        if '"hook_cancelled"' in line:
            st["hook_cancelled"] += 1
        if "PONYTAIL MODE ACTIVE" in line:
            st["ponytail_active"] = True
        if "CAVEMAN MODE ACTIVE" in line:
            st["caveman_active"] = True
        try:
            j = json.loads(line)
        except Exception:
            continue
        c = (j.get("message") or {}).get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                st["tool_calls"] += 1
                name = b.get("name")
                cmd = str((b.get("input") or {}).get("command", ""))
                if name in ("Agent", "Task"):
                    st["subagent_calls"] += 1
                if "python -m graphify" in cmd:
                    if name == "Bash":
                        st["graphify_bash"] += 1; pend[b.get("id")] = 1
                    elif name == "PowerShell":
                        st["graphify_ps"] += 1; pend[b.get("id")] = 1
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in pend:
                r = b.get("content")
                r = r if isinstance(r, str) else json.dumps(r)
                if not b.get("is_error") and r.strip() and not re.search(r"no module|not found|not recognized|traceback", r[:400], re.I):
                    st["graphify_ok"] += 1
    return st


def b3_items():
    items = []
    for i, (tid, repo) in enumerate(sorted(B3_TASKS.items(), key=lambda kv: kv[0] != "paygate_long_settlement_001")):
        rot = ARM_ORDER[i % 5:] + ARM_ORDER[:i % 5]
        for trial in range(TRIALS):
            order = rot if trial == 0 else list(reversed(rot))
            for arm in order:
                items.append(dict(block=3, arm=arm, task_rel=f"benchmark/manifests/{repo}/{tid}.json", trial=trial, attempt=0, not_before=0.0, repin=True))
    return items


# ------------------------------------------------------------------------------ short block
def b1_items():
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    tasks = list(suite["tasks"])
    assert len(tasks) == 55, len(tasks)
    rnd = random.Random(SEED)
    rnd.shuffle(tasks)
    items = []
    for i, rel in enumerate(tasks):
        rot = ARM_ORDER[i % 5:] + ARM_ORDER[:i % 5]
        for trial in range(TRIALS):
            order = rot if trial == 0 else list(reversed(rot))
            for arm in order:
                items.append(dict(block=1, arm=arm, task_rel=rel, trial=trial, attempt=0, not_before=0.0))
    return items


def b1_classify(run_dir):
    """-> (verdict, info). verdict in valid|transient|invalid|timeout|incomplete"""
    rd = Path(run_dir)
    s = rjson(rd / "score.json"); t = rjson(rd / "telemetry.json"); rs = rjson(rd / "run_state.json") or {}
    try:
        stdout = (rd / "logs" / "agent.stdout.log").read_text(encoding="utf-8", errors="replace")
    except Exception:
        stdout = ""
    j = last_json_line(stdout)
    info = dict(agent_is_error=j.get("is_error"), agent_result=str(j.get("result", ""))[:300], stdout_cost=j.get("total_cost_usd"))
    if not s or not t:
        return "incomplete", info
    pu = t.get("provider_usage") or {}
    if isinstance(pu, str):
        pu = {}
    cost = pu.get("cost_usd")
    if cost is None:
        cost = j.get("total_cost_usd")
        info["cost_source"] = "agent_stdout_fallback"
    info["cost"] = cost
    info["timed_out"] = bool((s.get("penalties") or {}).get("timed_out"))
    agent = rs.get("agent") or {}
    msg = info["agent_result"] + " " + str((rd / "logs" / "agent.stderr.log").read_text(encoding="utf-8", errors="replace")[-500:]
                                           if (rd / "logs" / "agent.stderr.log").exists() else "")
    info["msg"] = msg[:400]
    info["exit_code"] = agent.get("exit_code")
    if (j.get("is_error") or not stdout.strip()) and not info["timed_out"]:
        if TRANSIENT_PAT.search(msg) or not stdout.strip():
            return "transient", info
    if rs.get("harness_invalid"):
        info["harness_invalid_reason"] = rs.get("harness_invalid_reason")
        # policy: a finished run whose only fault is a leftover background process is flagged needs-cleanup, not invalid
        if (str(info["harness_invalid_reason"] or "").startswith("process census:") and not info["timed_out"]
                and not j.get("is_error") and stdout.strip() and cost is not None):
            info["needs_cleanup"] = True
            info["cleanup_reason"] = str(info["harness_invalid_reason"])[:300]
            return "valid_cleanup", info
        return "invalid", info
    if info["timed_out"]:
        return "timeout", info
    if cost is None:
        return "invalid", info
    return "valid", info


def b1_row(run_dir, arm, info):
    rd = Path(run_dir)
    s = rjson(rd / "score.json") or {}; t = rjson(rd / "telemetry.json") or {}; tu = rjson(rd / "tool_usage.json") or {}
    pv = rjson(rd / "provenance.json") or {}
    pu = t.get("provider_usage") or {}
    if isinstance(pu, str):
        pu = {}
    by = tu.get("by_tool") or {}
    ts = transcript_stats(tu.get("transcript"))
    return dict(block=1, arm=arm, condition_id=s.get("condition_id"), task_id=s.get("task_id"), repo_id=s.get("repo_id"),
                trial=s.get("trial_index"), run_id=rd.name, success=bool(s.get("success")), quality=s.get("quality_score"),
                final_score=s.get("final_score"), cost_usd=info.get("cost"), cost_source=info.get("cost_source", "telemetry"),
                timed_out=info.get("timed_out"), wall_s=(s.get("timing") or {}).get("agent_wall_time_seconds"),
                output_tokens=pu.get("output_tokens"), cache_read=pu.get("cache_read_tokens"), cache_write=pu.get("cache_write_tokens"),
                subagent_tokens=pu.get("subagent_tokens"), all_thread_tokens=pu.get("all_thread_total_tokens"),
                tool_calls=tu.get("total_tool_calls"), subagent_runs=(by.get("Agent", 0) + by.get("Task", 0)) > 0,
                needs_cleanup=bool(info.get("needs_cleanup")), cleanup_reason=info.get("cleanup_reason"),
                by_tool=by, transcript_stats=ts, client_version=pv.get("client_version"),
                client_sha=pv.get("client_binary_sha256"), pin_status=(pv.get("enforcement") or {}).get("pin_status"))


def discard_run(run_dir, why):
    rd = Path(run_dir)
    if not rd.exists():
        return
    dst = DISCARD / "b1" / rd.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(rd), str(dst))
        (dst / "DISCARD_REASON.txt").write_text(why, encoding="utf-8")
    except Exception as e:
        log(f"discard move failed {rd.name}: {e!r}")


def b1_scan_done():
    """(cond, task_id, trial) -> row, rebuilt from the run directories under runs/. Incomplete runs are discarded."""
    done = {}
    known = {r["run_id"]: r for r in jrows(B1_RES) + jrows(B3_RES)}
    for d in os.scandir(RUNS):
        if not d.is_dir() or not d.name.startswith("2026"):
            continue
        meta = (rjson(os.path.join(d.path, "score.json")) or rjson(os.path.join(d.path, "run_state.json"))
                or rjson(os.path.join(d.path, "provenance.json")) or {})
        cond = meta.get("condition_id")
        if cond not in COND2ARM:
            continue
        verdict, info = b1_classify(d.path)
        if verdict not in ("valid", "valid_cleanup") and not (verdict == "timeout" and (known.get(d.name) or {}).get("accepted_timeout")):
            if verdict == "incomplete":
                discard_run(d.path, "incomplete at driver start (driver died mid-run)")
                anomaly("b1_incomplete_discarded", run_id=d.name)
            elif d.name not in known:
                discard_run(d.path, f"{verdict} found at start: {info}")
            continue
        s = rjson(os.path.join(d.path, "score.json"))
        key = (cond, s["task_id"], s["trial_index"])
        if key in done:  # duplicate valid: keep first by run_id order, move extra out
            if d.name < done[key]["run_id"]:
                discard_run(RUNS / done[key]["run_id"], "duplicate valid cell")
            else:
                discard_run(d.path, "duplicate valid cell"); continue
        row = known.get(d.name) or b1_row(d.path, COND2ARM[cond], info)
        if d.name not in known:
            if s["task_id"] in B3_TASKS:
                row["block"] = 3
            jappend(B3_RES if s["task_id"] in B3_TASKS else B1_RES, row)
        done[key] = row
    return done


def run_b1(item):
    arm = item["arm"]; cond, agent_id, graphify = ARMS[arm]
    manifest = tbc.load_manifest(TB / item["task_rel"])
    errs = tbc.validate_manifest(manifest, TB)
    if errs:
        anomaly("b1_manifest_invalid", task=item["task_rel"], errors=errs); return "fatal"
    runner = tbc._make_runner("cli-agent", None, True, agent_id, TB)
    if item["block"] == 3:
        manifest.allowed_runtime_seconds = B3_TIMEOUT; runner.config.default_timeout_seconds = B3_TIMEOUT
    t0 = time.time()
    try:
        rep = tbc.execute_run(manifest, TB, runner, cond, item["trial"], False, isolated_runs_root=ISO_ROOT, quiet=True,
                              graphify=graphify, pin_provenance=item.get("repin") or None)
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        jappend(B1_ATT, dict(item, t=time.time(), verdict="exception", error=msg[:2000]))
        if "Provenance" in type(e).__name__ or "provenance" in msg[:300].lower():
            if re.search(r"client_(version|binary)", msg) and not item.get("repin"):
                anomaly("b1_client_changed_repin", cond=cond, task=manifest.task_id, msg=msg[:600])
                item["repin"] = True; return "retry"
            anomaly("b1_provenance_refused", cond=cond, task=manifest.task_id, msg=msg[:1200]); return "fatal"
        if "MeasurementAuthority" in type(e).__name__:
            anomaly("b1_authority_refused", msg=msg[:800]); global_pause(600, "authority refused"); return "retry"
        anomaly("b1_exception", cond=cond, task=manifest.task_id, msg=msg[:800], tb=traceback.format_exc()[-1500:])
        return "retry" if item["attempt"] < 2 else "fatal"
    rd = RUNS / rep["run_id"]
    verdict, info = b1_classify(rd)
    cost = info.get("cost") or info.get("stdout_cost") or 0
    with LOCK:
        STATE["spent_all_usd"] += float(cost or 0)
    jappend(B1_ATT, dict(item, t=time.time(), run_id=rd.name, verdict=verdict, wall=round(time.time() - t0), **{k: v for k, v in info.items() if k != "msg"}))
    if verdict in ("valid", "valid_cleanup") and arm in ("ponytail", "caveman"):
        tu = rjson(rd / "tool_usage.json") or {}
        ts = transcript_stats(tu.get("transcript")) or {}
        if not ts.get(f"{arm}_active"):
            if item["attempt"] < 2:
                anomaly("b1_treatment_not_active_retry", run_id=rd.name, task=manifest.task_id, arm=arm, hook_cancelled=ts.get("hook_cancelled"))
                discard_run(rd, f"{arm} hook text absent (hook_cancelled={ts.get('hook_cancelled')})")
                return "retry"
            anomaly("b1_treatment_not_active_accepted", run_id=rd.name, task=manifest.task_id, arm=arm)
    if verdict in ("valid", "valid_cleanup") or (verdict == "timeout" and item["attempt"] >= 1):
        row = b1_row(rd, arm, info)
        if item["block"] == 3:
            row["block"] = 3
        if verdict == "timeout":
            row["accepted_timeout"] = True; anomaly("b1_timeout_accepted", run_id=rd.name, task=manifest.task_id, arm=arm)
        jappend(B3_RES if item["block"] == 3 else B1_RES, row)
        with LOCK:
            STATE["consecutive_transient"] = 0
        gq = (row.get("transcript_stats") or {}).get("graphify_bash")
        log(f"B{item['block']} DONE {arm:8s} {manifest.task_id} t{item['trial']} ok={row['success']} q={row['quality']} ${row['cost_usd']} "
            f"{row['wall_s']}s sub={row['subagent_runs']} gq={gq} [{rd.name}]")
        return "done"
    discard_run(rd, f"{verdict}: {info}")
    if verdict == "transient":
        with LOCK:
            STATE["consecutive_transient"] += 1; n = STATE["consecutive_transient"]
        w = limit_wait(info.get("msg"), item["attempt"])
        if n >= 2 or re.search(r"limit", info.get("msg") or "", re.I):
            global_pause(w, f"API transient x{n}: {info.get('msg', '')[:160]}")
        item["not_before"] = time.time() + w
        log(f"B1 TRANSIENT {arm} {manifest.task_id} t{item['trial']}: {info.get('msg', '')[:200]} -> retry in {w:.0f}s")
        return "retry" if item["attempt"] < 8 else "fatal"
    anomaly(f"b1_{verdict}", run_id=rd.name, task=manifest.task_id, arm=arm, trial=item["trial"], info={k: v for k, v in info.items() if k != "msg"}, msg=info.get("msg", "")[:300])
    return "retry" if item["attempt"] < (1 if verdict == "timeout" else 2) else "fatal"


# ------------------------------------------------------------------------------ chain block
def pro_chains():
    out = []
    for d in sorted(PRO_CHAINS_DIR.iterdir()):
        if d.is_dir() and not d.name.startswith("_") and d.name != "webclients_B" and (d / "validation.json").exists():
            out.append(d.name)
    return out


def b2_items():
    items = []
    for i, c in enumerate(pro_chains()):
        rot = ARM_ORDER[i % 5:] + ARM_ORDER[:i % 5]
        for trial in range(TRIALS):
            order = rot if trial == 0 else list(reversed(rot))
            for arm in order:
                items.append(dict(block=2, arm=arm, chain=c, trial=trial, attempt=0, not_before=0.0))
    return items


def b2_done():
    return {(r["chain"], r["arm"], r["trial"]) for r in jrows(B2_RES)}


def make_ws(ws, chain):
    repo = PRO_REPOS / (chain["repo"].split("/")[1] + ".git")
    with WS_LOCK:
        if ws.exists():
            rmtree(ws)
        subprocess.run(["git", "--git-dir", str(repo), "worktree", "prune"], capture_output=True)
        subprocess.run(G + ["--git-dir", str(repo), "worktree", "add", "--detach", "-f", str(ws), chain["base_commit"]], check=True, capture_output=True)
        (ws / ".git").unlink()
        subprocess.run(["git", "--git-dir", str(repo), "worktree", "prune"], capture_output=True)
    subprocess.run(G + ["init", "-q"], cwd=ws, check=True)
    subprocess.run(G + ["add", "-A"], cwd=ws, capture_output=True)
    subprocess.run(G + ["commit", "-qm", "base"], cwd=ws, check=True, capture_output=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=ws)


def workspace_patch(ws):
    root = subprocess.run(["git", "-C", str(ws), "rev-list", "--max-parents=0", "HEAD"], capture_output=True, text=True).stdout.split()
    subprocess.run(["git", "-C", str(ws), "add", "-A"], capture_output=True)
    return subprocess.run(["git", "-C", str(ws), "-c", "core.autocrlf=false", "diff", "--cached", "--binary", root[-1]], capture_output=True).stdout


def rmtree(p):
    def onerr(func, path, exc):
        try:
            os.chmod(path, 0o666); func(path)
        except Exception:
            pass
    shutil.rmtree(p, onerror=onerr)
    if Path(p).exists():
        subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(p)], capture_output=True)


def kill_tree(pid):
    try:
        p = psutil.Process(pid)
        kids = p.children(recursive=True)
        for k in kids:
            try: k.kill()
            except Exception: pass
        p.kill()
    except Exception:
        pass


def _inflight_pids(update=None, remove=None):
    with LOCK:
        d = rjson(INFLIGHT_PIDS) or {}
        if update:
            d.update(update)
        if remove:
            d.pop(remove, None)
        INFLIGHT_PIDS.write_text(json.dumps(d), encoding="utf-8")
        return d


def find_transcript(sid):
    if not sid:
        return None
    for p in Path(os.environ["USERPROFILE"], ".claude", "projects").glob(f"*/{sid}.jsonl"):
        return p
    return None


def run_b2(item):
    c, arm, trial = item["chain"], item["arm"], item["trial"]
    cond, agent_id, graphify = ARMS[arm]
    label = f"{c}_{arm}_t{trial}"
    pkg = PRO_CHAINS_DIR / c
    chain = json.loads((pkg / "chain.json").read_text(encoding="utf-8"))
    cfg = load_agent(TB / "benchmark" / "agents", agent_id)
    argv = with_explicit_mcp_set(list(cfg.command_template))
    argv[0] = shutil.which(argv[0]) or argv[0]
    env = child_env(cfg)
    prompt = (pkg / "prompt.txt").read_text(encoding="utf-8")
    ws = PRO / "ws" / label
    runs = PRO / "runs"; runs.mkdir(parents=True, exist_ok=True)
    make_ws(ws, chain)
    gstats = gsec = None
    if graphify:
        t = time.time(); gstats = build_graph(ws); gsec = round(time.time() - t)
        (ws / ".git" / "info").mkdir(parents=True, exist_ok=True)
        with open(ws / ".git" / "info" / "exclude", "a", encoding="utf-8") as f:
            f.write("\ngraphify-out/\n")
        prompt = prompt.rstrip("\n") + _graphify_block(ws) + "\n"
        log(f"B2 {label} graph {gstats} in {gsec}s")
    outp = runs / f"{label}.attempt{item['attempt']}.stdout"; errp = runs / f"{label}.attempt{item['attempt']}.stderr"
    t = time.time(); rc = None
    with open(outp, "wb") as fo, open(errp, "wb") as fe:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=fo, stderr=fe, cwd=ws, env=env,
                             creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        _inflight_pids(update={label: [p.pid, psutil.Process(p.pid).create_time()]})
        with LOCK:
            STATE["inflight"][label]["pid"] = p.pid
        try:
            p.stdin.write(prompt.encode("utf-8")); p.stdin.close()
        except Exception:
            pass
        try:
            rc = p.wait(timeout=PRO_TIMEOUT)
        except subprocess.TimeoutExpired:
            kill_tree(p.pid); rc = "timeout"
        _inflight_pids(remove=label)
    dt = time.time() - t
    out = outp.read_text(encoding="utf-8", errors="replace"); err = errp.read_text(encoding="utf-8", errors="replace")
    j = last_json_line(out)
    msg = str(j.get("result", ""))[:400] + (err[-400:] if rc not in (0, "timeout") else "")
    with LOCK:
        STATE["spent_all_usd"] += float(j.get("total_cost_usd") or 0)
    transient = (rc not in (0, "timeout") or j.get("is_error")) and (TRANSIENT_PAT.search(msg) or (dt < 300 and not out.strip()))
    jappend(B2_ATT, dict(item, t=time.time(), seconds=round(dt), rc=rc, is_error=j.get("is_error"), cost=j.get("total_cost_usd"),
                         transient=bool(transient), msg=msg[:300]))
    if transient and not stop_state() == "KILL":
        rmtree(ws)
        w = limit_wait(msg, item["attempt"])
        with LOCK:
            STATE["consecutive_transient"] += 1; n = STATE["consecutive_transient"]
        if n >= 2 or re.search(r"limit", msg, re.I):
            global_pause(w, f"API transient x{n}: {msg[:160]}")
        item["not_before"] = time.time() + w
        log(f"B2 TRANSIENT {label} rc={rc} {dt:.0f}s: {msg[:200]!r} -> retry in {w:.0f}s")
        return "retry" if item["attempt"] < 8 else "fatal"
    with LOCK:
        STATE["consecutive_transient"] = 0
    if arm in ("ponytail", "caveman") and item["attempt"] < 1:
        tr0 = find_transcript(j.get("session_id"))
        ts0 = transcript_stats(str(tr0)) if tr0 else {}
        if not (ts0 or {}).get(f"{arm}_active"):
            anomaly("b2_treatment_not_active_retry", label=label, hook_cancelled=(ts0 or {}).get("hook_cancelled"))
            outp.replace(runs / f"{label}.attempt{item['attempt']}.inactive.json")
            rmtree(ws)
            return "retry"
    final_out = runs / f"{label}.json"; outp.replace(final_out)
    if err.strip():
        errp.replace(runs / f"{label}.stderr.txt")
    else:
        errp.unlink(missing_ok=True)
    tr = find_transcript(j.get("session_id"))
    trc = None
    if tr:
        trc = runs / f"{label}.transcript.jsonl"; shutil.copy2(tr, trc)
        sub_dir = tr.with_suffix("") / "subagents"  # subagent transcripts, if the client writes them
        if sub_dir.is_dir():
            shutil.copytree(sub_dir, runs / f"{label}.subagents", dirs_exist_ok=True)
    patch = workspace_patch(ws)
    (PRO / "patches").mkdir(exist_ok=True)
    (PRO / "patches" / f"{label}.patch").write_bytes(patch)
    files = re.findall(rb"^diff --git a/(\S+) ", patch, re.M)
    mu = (j.get("modelUsage") or {}).get(MODEL, {})
    ts = transcript_stats(str(trc)) if trc else None
    row = dict(block=2, chain=c, arm=arm, trial=trial, label=label, condition_id=cond, agent_id=agent_id, seconds=round(dt), rc=rc,
               timed_out=(rc == "timeout"), is_error=j.get("is_error"), cost_usd=j.get("total_cost_usd"), num_turns=j.get("num_turns"),
               duration_api_ms=j.get("duration_api_ms"), input_tokens=mu.get("inputTokens"), output_tokens=mu.get("outputTokens"),
               cache_read=mu.get("cacheReadInputTokens"), cache_create=mu.get("cacheCreationInputTokens"),
               model_usage={k: {kk: vv for kk, vv in v.items() if kk in ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens", "costUSD")}
                            for k, v in (j.get("modelUsage") or {}).items()},
               subagent_stats=j.get("subagent_stats"), transcript_stats=ts, patch_lines=patch.count(b"\n"), patch_files=len(files),
               graphify_out_in_patch=any(f.startswith(b"graphify-out") for f in files), graph_stats=gstats, graph_build_s=gsec,
               session_id=j.get("session_id"), transcript=bool(tr), attempts=item["attempt"] + 1, argv=argv[1:],
               client_version=CLIENT_VERSION, graded=False)
    jappend(B2_RES, row)
    log(f"B2 DONE {label} {round(dt)}s ${j.get('total_cost_usd')} turns={j.get('num_turns')} patch={row['patch_lines']} "
        f"gq={(ts or {}).get('graphify_bash')} sub={(ts or {}).get('subagent_calls')} rc={rc}")
    rmtree(ws)
    return "done"


# ------------------------------------------------------------------------------ status
def go_cache_gb():
    p = Path(os.environ["LOCALAPPDATA"]) / "go-build"
    try:
        return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e9, 2)
    except Exception:
        return None


GO_CACHE = {"gb": None, "t": 0}


def write_status(queue, final=None):
    b1 = list({(r["condition_id"], r["task_id"], r["trial"]): r for r in reversed(jrows(B1_RES))
               if (RUNS / r["run_id"]).exists()}.values())
    b2 = list({(r["chain"], r["arm"], r["trial"]): r for r in reversed(jrows(B2_RES))}.values())
    by1 = {a: dict(done=0, total=55 * TRIALS, ok=0, cost=0.0, wall=0.0) for a in ARM_ORDER}
    for r in b1:
        a = by1[r["arm"]]; a["done"] += 1; a["ok"] += bool(r["success"]); a["cost"] += float(r.get("cost_usd") or 0); a["wall"] += float(r.get("wall_s") or 0)
    nch = len(pro_chains())
    by2 = {a: dict(done=0, total=nch * TRIALS, cost=0.0, wall=0.0, timeouts=0) for a in ARM_ORDER}
    for r in b2:
        a = by2[r["arm"]]; a["done"] += 1; a["cost"] += float(r.get("cost_usd") or 0); a["wall"] += float(r.get("seconds") or 0); a["timeouts"] += bool(r.get("timed_out"))
    for d in list(by1.values()) + list(by2.values()):
        d["cost"] = round(d["cost"], 2); d["mean_cost"] = round(d["cost"] / d["done"], 3) if d["done"] else None
        d["mean_wall_s"] = round(d.pop("wall") / d["done"]) if d["done"] else None
    if time.time() - GO_CACHE["t"] > 1800:
        GO_CACHE.update(gb=go_cache_gb(), t=time.time())
    rem1 = sum(1 for q in queue if q["block"] == 1); rem2 = sum(1 for q in queue if q["block"] == 2)
    m1 = (sum(float(r.get("wall_s") or 0) for r in b1) / len(b1)) if b1 else 180
    m2 = (sum(float(r.get("seconds") or 0) for r in b2) / len(b2)) if b2 else 700
    with LOCK:
        infl = {k: dict(v, running_s=round(time.time() - v["start"])) for k, v in STATE["inflight"].items()}
        st = dict(updated=time.strftime("%Y-%m-%d %H:%M:%S"), state=final or ("stopping" if STATE["stopping"] else
                  ("paused" if STATE["pause_until"] > time.time() else "running")),
                  pause_reason=STATE["pause_reason"] if STATE["pause_until"] > time.time() else "",
                  pause_until=time.strftime("%H:%M:%S", time.localtime(STATE["pause_until"])) if STATE["pause_until"] > time.time() else "",
                  runtime_h=round((time.time() - STATE["started"]) / 3600, 2), driver_pid=os.getpid(),
                  block1=dict(done=len(b1), total=55 * TRIALS * 5, by_arm=by1, queued=rem1),
                  block2=dict(done=len(b2), total=nch * TRIALS * 5, chains=nch, by_arm=by2, queued=rem2,
                              graded=len({(g['label']) for g in jrows(PRO / 'pro_grades.jsonl') if g.get('f2p_total') is not None})),
                  block3=dict(done=len(jrows(B3_RES)), total=3 * TRIALS * 5, cost=round(sum(float(r.get("cost_usd") or 0) for r in jrows(B3_RES)), 2), timeout_s=B3_TIMEOUT),
                  cost_valid_usd=round(sum(float(r.get("cost_usd") or 0) for r in b1 + b2), 2),
                  cost_all_attempts_this_driver_usd=round(STATE["spent_all_usd"], 2),
                  inflight=infl, free_disk_gb=round(free_gb(), 2), go_build_cache_gb=GO_CACHE["gb"],
                  eta_hours=round((rem1 * m1 + rem2 * m2) / CONCURRENCY / 3600 + sum(1 for _ in infl) * 0, 1),
                  anomalies=len(jrows(ANOM)), client_version=CLIENT_VERSION, versions=VERSIONS, recent=STATE["events"][-12:])
    tmp = STATUSF.with_suffix(".tmp"); tmp.write_text(json.dumps(st, indent=1, default=str), encoding="utf-8"); tmp.replace(STATUSF)


def status_loop(queue):
    while True:
        try:
            write_status(queue)
        except Exception as e:
            log(f"status error {e!r}")
        time.sleep(60)


def free_space_loop():
    while True:
        try:
            if time.time() - STATE["last_free_space"] > 1800 or free_gb() < MIN_FREE_TARGET:
                STATE["last_free_space"] = time.time()
                p = subprocess.run([sys.executable, str(TB / "scripts" / "free_space.py")], capture_output=True, text=True, timeout=1800)
                log("free_space: " + p.stdout.strip().replace("\n", " | ")[-300:])
                for d in ISO_ROOT.glob("*"):  # leftovers of crashed runs (never an in-flight one)
                    with LOCK:
                        live = any(d.name == v.get("run_hint") for v in STATE["inflight"].values())
                    if not live and time.time() - d.stat().st_mtime > 3 * 3600:
                        rmtree(d); log(f"removed leftover isolated workspace {d.name}")
        except Exception as e:
            log(f"free_space error {e!r}")
        time.sleep(120)


# ------------------------------------------------------------------------------ main
def single_instance():
    d = rjson(PIDF)
    if d:
        try:
            p = psutil.Process(d["pid"])
            if abs(p.create_time() - d["ct"]) < 1 and "driver.py" in " ".join(p.cmdline()):
                return False
        except Exception:
            pass
    PIDF.write_text(json.dumps(dict(pid=os.getpid(), ct=psutil.Process().create_time())), encoding="utf-8")
    return True


def kill_orphan_pro():
    for label, (pid, ct) in (rjson(INFLIGHT_PIDS) or {}).items():
        try:
            p = psutil.Process(pid)
            if abs(p.create_time() - ct) < 1:
                kill_tree(pid); anomaly("b2_orphan_killed", label=label, pid=pid)
        except Exception:
            pass
    INFLIGHT_PIDS.write_text("{}", encoding="utf-8")
    for d in (PRO / "ws").glob("*"):
        rmtree(d)


CLIENT_VERSION = None
VERSIONS = {}


def main():
    global CLIENT_VERSION, VERSIONS
    for d in (SW / "logs", DISCARD, PRO / "runs", PRO / "patches", PRO / "ws", ISO_ROOT):
        d.mkdir(parents=True, exist_ok=True)
    if DONEF.exists():
        print("DONE marker present; nothing to do"); return
    if stop_state():
        print("STOP present; not starting"); return
    if not single_instance():
        print("driver already running"); return
    CLIENT_VERSION = subprocess.run([shutil.which("claude"), "--version"], capture_output=True, text=True, env=child_env(load_agent(TB / "benchmark" / "agents", "claude_code_sonnet55_iso"))).stdout.strip()
    VERSIONS = rjson(SW / "versions.json") or {}
    log(f"driver start pid={os.getpid()} client={CLIENT_VERSION} free={free_gb():.2f}GB PATH0={os.environ['PATH'][:80]}")
    kill_orphan_pro()
    auth = tbc.establish_startup_authority("strict")
    log(f"authority verdict={auth.get('verdict')}")
    done1 = b1_scan_done(); done2 = b2_done()
    queue = [x for x in b1_items() if (ARMS[x["arm"]][0], tbc.load_manifest(TB / x["task_rel"]).task_id, x["trial"]) not in done1]
    queue += [x for x in b2_items() if (x["chain"], x["arm"], x["trial"]) not in done2]
    done3 = {(k[1], k[2], k[0]) for k in done1 if k[1] in B3_TASKS}
    queue = [x for x in queue if not (x["block"] == 1 and tbc.load_manifest(TB / x["task_rel"]).task_id in B3_TASKS)]
    queue += [x for x in b3_items() if (ARMS[x["arm"]][0], tbc.load_manifest(TB / x["task_rel"]).task_id, x["trial"]) not in done1]
    log(f"queue: block1 {sum(q['block'] == 1 for q in queue)} todo ({len(done1)} done); block2 {sum(q['block'] == 2 for q in queue)} todo ({len(done2)} done)")
    threading.Thread(target=status_loop, args=(queue,), daemon=True).start()
    threading.Thread(target=free_space_loop, daemon=True).start()
    qlock = threading.Lock()
    fatal = []

    def next_item():
        while True:
            ss = stop_state()
            if ss:
                STATE["stopping"] = True
                if ss == "KILL":
                    log("KILL requested: killing in-flight and exiting")
                    for k in psutil.Process().children(recursive=True):
                        try: k.kill()
                        except Exception: pass
                    write_status(queue, final="killed"); os._exit(3)
                return None
            if time.time() < STATE["pause_until"]:
                time.sleep(15); continue
            fg = free_gb()
            if fg < MIN_FREE_PAUSE:
                with LOCK:
                    STATE["pause_reason"] = f"low disk {fg:.2f} GB"; STATE["pause_until"] = time.time() + 300
                STATE["last_free_space"] = 0
                log(f"LOW DISK {fg:.2f} GB: pausing launches"); time.sleep(60); continue
            with qlock:
                if not queue:
                    return None
                now = time.time()
                for i, q in enumerate(queue):
                    if q["not_before"] <= now:
                        # a later block starts only once earlier blocks have nothing launchable left
                        if q["block"] >= 2 and any(x["block"] < q["block"] for x in queue):
                            break
                        return queue.pop(i)
            time.sleep(10)

    def worker(wi):
        time.sleep(wi * 8)  # stagger the first batch
        while True:
            item = next_item()
            if item is None:
                return
            key = (f"b{item['block']}:{item['arm']}:{item['task_rel'].replace('benchmark/manifests/', '').replace('.json', '')}:t{item['trial']}" if item["block"] in (1, 3)
                   else f"{item['chain']}_{item['arm']}_t{item['trial']}")
            with LOCK:
                STATE["inflight"][key] = dict(start=time.time(), attempt=item["attempt"], block=item["block"], arm=item["arm"])
            try:
                res = run_b1(item) if item["block"] in (1, 3) else run_b2(item)
            except Exception as e:
                anomaly("worker_exception", key=key, err=repr(e), tb=traceback.format_exc()[-2000:])
                res = "retry" if item["attempt"] < 2 else "fatal"
                if item["block"] == 2:
                    _inflight_pids(remove=key)
            finally:
                with LOCK:
                    STATE["inflight"].pop(key, None)
            if res == "retry":
                item["attempt"] += 1
                with qlock:
                    queue.insert(0, item)
            elif res == "fatal":
                fatal.append(key); anomaly("cell_gave_up", key=key, attempts=item["attempt"] + 1)

    ths = [threading.Thread(target=worker, args=(i,)) for i in range(CONCURRENCY)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    if stop_state():
        log("STOP honoured: in-flight runs finished, exiting"); write_status(queue, final="stopped"); return
    for rec in tbc.report_stale_process_trees(force=True):
        anomaly("orphan_tree_at_end", label=rec.get("label"))
    if not queue:
        DONEF.write_text(time.strftime("%Y-%m-%d %H:%M:%S") + f" fatal={fatal}", encoding="utf-8")
        log(f"ALL BLOCKS DONE fatal={fatal}"); write_status(queue, final="done")


if __name__ == "__main__":
    main()
