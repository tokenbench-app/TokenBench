"""Environment setup shared by the sweep driver and the chain grader.

The sweep must resolve the real `claude` binary, so session-scoped variables of
the launching shell are dropped before the harness is imported and PATH is
rebuilt from the Windows registry (Machine + User) plus the toolchains the hidden
tests need (Go, CMake, a MinGW toolchain, Rust). Set TB_REPO, TB_SWEEP_DIR and
TB_TOOLCHAIN (os.pathsep-separated extra PATH entries) for your machine. When
TB_SWEEP_DIR is not set, a directory under the system temp folder is used.
"""
import os, sys, tempfile, winreg

TB = os.environ.get("TB_REPO", os.getcwd())
TMPDIR = tempfile.gettempdir()
SWEEP = os.environ.get("TB_SWEEP_DIR", os.path.join(TMPDIR, "tokenbench-sweep"))
TOOLCHAIN = [p for p in os.environ.get("TB_TOOLCHAIN", "").split(os.pathsep) if p]
DROP_PREFIXES = ("CLAUDE_CODE_", "TOKENBENCH_")
DROP_NAMES = {"CLAUDECODE", "CLAUDE_PROJECT_DIR", "AI_AGENT", "ANTHROPIC_BASE_URL", "PYTHONPATH"}


def _reg_path(root, sub):
    try:
        with winreg.OpenKey(root, sub) as k:
            v, _ = winreg.QueryValueEx(k, "Path")
            return os.path.expandvars(v)
    except OSError:
        return ""


def clean_path():
    machine = _reg_path(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    user = _reg_path(winreg.HKEY_CURRENT_USER, "Environment")
    parts = TOOLCHAIN + machine.split(os.pathsep) + user.split(os.pathsep)
    seen, out = set(), []
    for p in parts:
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return os.pathsep.join(out)


def setup():
    for k in list(os.environ):
        ku = k.upper()
        if ku.startswith(DROP_PREFIXES) or ku in DROP_NAMES:
            del os.environ[k]
    os.environ["PATH"] = clean_path()
    # keep the measured client fixed for the whole sweep
    os.environ["DISABLE_AUTOUPDATER"] = "1"
    os.environ.setdefault("TEMP", TMPDIR); os.environ.setdefault("TMP", TMPDIR)
    os.chdir(TB)
    if TB not in sys.path:
        sys.path.insert(0, TB)
