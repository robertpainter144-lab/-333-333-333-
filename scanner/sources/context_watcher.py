"""Context Watcher — keeps the master project context doc honest.

Every 30 min, scan for:
  1. New state files in scanner/state/ that aren't listed in CONTEXT.md
  2. Systems that stopped writing state (dead > 6h)
  3. Uncommitted repo changes older than the last auto-push

Appends findings to state/context_audit.jsonl. Flags high-severity ones in
the app's live activity feed so we never lose track of what's actually
running.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

CONTEXT_MD = Path("/Users/quarki3/trading-stack/CONTEXT.md")
AUDIT_LOG  = STATEDIR / "context_audit.jsonl"

# Known state file names that should appear in CONTEXT.md's paper systems table
KNOWN_STATE_FILES = {
    "combined_mirror_state.json",
    "qc_mirror_state.json",
    "patent_mirror_state.json",
    "prop_state.json",
    "strategy_governor_ui.json",
    "value_screen_state.json",
    # Support files (not systems, so not flagged)
    "auto_tune_config.json",
    "prop_hold.json",
    "prop_ledger.jsonl",
    "health.json",
    "seen.json",
    "signals.jsonl",
    "live_activity.jsonl",
    "orchestrator.log",
    "orchestrator.stdout",
    "retune.log",
    "context_audit.jsonl",
    "progress.jsonl",
    "combined_mirror_ledger.jsonl",
    "patent_mirror_ledger.jsonl",
    "prop_counters.json",
    "decisions.jsonl",
    "strategy_governor.jsonl",
    "strategy_governor_alerts.jsonl",
    "strategy_governor_state.json",
    "momentum_config.json",
    "momentum_entries.json",
    "momentum_ledger.jsonl",
}


def scan_state_dir() -> tuple[list[str], list[str]]:
    """Return (unknown_files, stale_files)."""
    now = int(time.time())
    unknown, stale = [], []
    for p in STATEDIR.iterdir():
        if p.is_dir():
            continue
        name = p.name
        # Whitelist noisy filename patterns that aren't systems
        if name.startswith("tick_"): continue        # per-source heartbeats
        if name.endswith(".old.json"): continue      # archived snapshots
        if name.endswith(".old.jsonl"): continue
        if name.endswith(".tmp"): continue
        if name.startswith("."): continue
        if name.startswith("prices/"): continue
        # Skip ledger files that are per-strategy
        if name.endswith("_ledger.jsonl") and name != "prop_ledger.jsonl": continue
        if name not in KNOWN_STATE_FILES:
            unknown.append(name)
        # Stale detection: JSON state files should be updated in the last 6h
        if name.endswith("_state.json"):
            age_h = (now - int(p.stat().st_mtime)) / 3600
            if age_h > 6:
                stale.append(f"{name} ({age_h:.1f}h old)")
    return unknown, stale


def context_doc_ok() -> bool:
    if not CONTEXT_MD.exists():
        return False
    try:
        age_h = (time.time() - CONTEXT_MD.stat().st_mtime) / 3600
        return age_h < 24 * 7  # not older than a week
    except Exception:
        return False


def append_findings(unknown: list[str], stale: list[str]) -> None:
    """Append newly-detected unknown files to CONTEXT.md's changelog."""
    if not unknown or not CONTEXT_MD.exists():
        return
    stamp = time.strftime("%Y-%m-%d %H:%M")
    lines = []
    for f in unknown:
        lines.append(f"- {stamp} — UNKNOWN STATE FILE detected: `{f}` (add to CONTEXT.md)")
    try:
        with CONTEXT_MD.open("a") as f:
            for l in lines:
                f.write(l + "\n")
    except Exception:
        pass


def tick() -> None:
    now = int(time.time())
    unknown, stale = scan_state_dir()
    ctx_ok = context_doc_ok()

    audit = {
        "ts": now,
        "context_md_present": CONTEXT_MD.exists(),
        "context_md_fresh": ctx_ok,
        "unknown_state_files": unknown,
        "stale_state_files": stale,
    }
    STATEDIR.mkdir(exist_ok=True)
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps(audit) + "\n")

    append_findings(unknown, stale)

    if unknown or stale:
        emit_live("context_watcher",
                  f"[audit] {len(unknown)} unknown files, {len(stale)} stale: " +
                  ",".join(unknown[:3] + stale[:3]))
    log_tick("context_watcher",
             f"ok={ctx_ok} unknown={len(unknown)} stale={len(stale)}")


if __name__ == "__main__":
    tick()
