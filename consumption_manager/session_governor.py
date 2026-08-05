#!/usr/bin/env python3
"""D26 session governor — meta-manager over all live Claude sessions on this Mac.

Runs every 2 min under launchd. Reads the D25 consumption manager's
state.json (auto_tier) and enforces it at the process level, with one hard
invariant: Robert's ability to type prompts is NEVER blocked.

Process classes (from ps, every cycle):
  interactive — any claude process attached to a tty. PROTECTED: never
                stopped; if found stopped (by anything), SIGCONT + P4 alert.
  infra       — claude daemon, bg-spare pools, spare pty hosts. PROTECTED:
                never stopped; resumed if stopped (a frozen daemon degrades
                interactive sessions, seen 2026-08-02).
  governable  — headless/background sessions (tty ?? with --session-id or
                -p/--print). These are the only things this script pauses.

Tier policy (auto_tier from state.json; scan is re-run if state is stale):
  GREEN — resume every stopped governable session.
  AMBER — allow max_concurrent_heavy (state.json, default 2) governable
          session groups; oldest keep running (let work finish), excess
          paused with SIGSTOP.
  RED   — pause all governable session groups.

Pauses use SIGSTOP (resumable, no lost work) — never SIGKILL. A session
"group" is the root governable process plus its descendants.
"""
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE_PATH = BASE / "state.json"
GOV_STATE_PATH = BASE / "governor_state.json"
LOG_PATH = BASE / "governor.log"
STALE_S = 25 * 60  # rerun scan if state.json older than this


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def ntfy(level, msg):
    notifier = Path.home() / "bin" / "quarki_notify.py"
    if not notifier.exists():
        return
    try:
        subprocess.run([sys.executable, str(notifier), level,
                        "Claude session governor", msg],
                       capture_output=True, timeout=20)
    except Exception:
        pass


def load_tier():
    state = {}
    try:
        state = json.loads(STATE_PATH.read_text())
    except Exception:
        pass
    if time.time() - state.get("generated_epoch", 0) > STALE_S:
        subprocess.run([sys.executable, str(BASE / "consumption_manager.py"),
                        "scan"], capture_output=True, timeout=280)
        try:
            state = json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return state


def list_procs():
    out = subprocess.run(["ps", "ax", "-o", "pid=,ppid=,stat=,tty=,command="],
                         capture_output=True, text=True).stdout
    procs = {}
    for ln in out.splitlines():
        parts = ln.split(None, 4)
        if len(parts) < 5:
            continue
        pid, ppid, stat, tty, cmd = parts
        if "claude" not in cmd.lower() or "session_governor" in cmd:
            continue
        procs[int(pid)] = {"ppid": int(ppid), "stat": stat, "tty": tty,
                           "cmd": cmd}
    return procs


def classify(p):
    if p["tty"] != "??":
        return "interactive"
    cmd = p["cmd"]
    if (" daemon " in cmd or cmd.rstrip().endswith("daemon run")
            or "daemon run" in cmd or "bg-spare" in cmd
            or ("bg-pty-host" in cmd and "--session-id" not in cmd
                and " -p " not in cmd and "--print" not in cmd)):
        return "infra"
    if "--session-id" in cmd or " -p " in cmd or "--print" in cmd or "--resume" in cmd:
        return "governable"
    return "infra"  # unknown headless claude helper: err on the safe side


def descendants(procs, root):
    kids, stack = [], [root]
    while stack:
        cur = stack.pop()
        for pid, p in procs.items():
            if p["ppid"] == cur:
                kids.append(pid)
                stack.append(pid)
    return kids


def sig(pid, s):
    try:
        os.kill(pid, s)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def main():
    state = load_tier()
    tier = state.get("auto_tier", "GREEN")
    amber_cap = max(1, state.get("max_concurrent_heavy") or 2)

    procs = list_procs()
    resumed_protected, paused, resumed = [], [], []

    # Invariant first: protected classes are never left stopped.
    for pid, p in procs.items():
        if classify(p) in ("interactive", "infra") and p["stat"].startswith("T"):
            if sig(pid, signal.SIGCONT):
                resumed_protected.append((pid, classify(p)))

    # Governable session groups: roots are governable procs whose parent
    # is not itself governable.
    gov = {pid for pid, p in procs.items() if classify(p) == "governable"}
    roots = sorted(pid for pid in gov if procs[pid]["ppid"] not in gov)

    if tier == "RED":
        allowed = set()
    elif tier == "AMBER":
        allowed = set(roots[:amber_cap])  # oldest pids: let running work finish
    else:
        allowed = set(roots)

    for root in roots:
        group = [root] + [d for d in descendants(procs, root) if d in gov]
        stopped = procs[root]["stat"].startswith("T")
        if root in allowed and stopped:
            for pid in group:
                sig(pid, signal.SIGCONT)
            resumed.append(root)
        elif root not in allowed and not stopped:
            for pid in reversed(group):  # children first
                sig(pid, signal.SIGSTOP)
            paused.append(root)

    prev = {}
    try:
        prev = json.loads(GOV_STATE_PATH.read_text())
    except Exception:
        pass

    if resumed_protected:
        # Someone froze an interactive/infra process — that blocks Robert.
        ntfy("action", f"Found PROTECTED claude processes stopped and resumed "
                       f"them: {resumed_protected}. Interactive input is never "
                       f"to be blocked (D26). Run `reset` in affected "
                       f"terminals if output looks garbled.")
    if paused or resumed:
        ntfy("routine", f"Tier {tier}: paused headless sessions {paused or '[]'}, "
                        f"resumed {resumed or '[]'} "
                        f"(5h {state.get('five_hour', {}).get('pct', '?')}%, "
                        f"wk {state.get('weekly', {}).get('pct', '?')}%).")
    if paused or resumed or resumed_protected or prev.get("tier") != tier:
        log(f"tier={tier} roots={roots} paused={paused} resumed={resumed} "
            f"protected_resumed={resumed_protected}")

    GOV_STATE_PATH.write_text(json.dumps({
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tier": tier,
        "governable_roots": roots,
        "paused_now": sorted(r for r in roots
                             if r not in allowed) if roots else [],
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
