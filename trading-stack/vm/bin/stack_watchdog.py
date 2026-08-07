#!/usr/bin/env python3.10
"""stack_watchdog.py — external supervisor for the trading stack.

Runs every 5 min via launchd (com.quarki3.stack-watchdog). Three jobs:
  1. LIVENESS  — required launchd jobs loaded + key state files fresh.
                 Self-heals with `launchctl kickstart -k` (never kill -9).
  2. DISK      — alert when the data volume drops under 1 GB free
                 (the 2026-07 stack-wide silent death was a full disk).
  3. LIVE P&L  — Alpaca live equity vs persisted peak; Pushover on the
                 $5-drawdown review trigger (user rule).

Alerts go to ntfy.sh (topic from ~/ntfy.env), deduped to one
per key per 6h via state in ~/trading-data/watchdog_state.json.
Log: ~/trading-data/watchdog.log (self-truncates at 1 MB).
"""
from __future__ import annotations

import json
import pathlib
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
STATE_FILE = HOME / "trading-data" / "watchdog_state.json"
LOG_FILE = HOME / "trading-data" / "watchdog.log"
NTFY_ENV = HOME / "ntfy.env"
PEAK_FILE = HOME / "trading-data" / "portfolio_peaks" / "alpaca_live.json"

ALERT_COOLDOWN_S = 6 * 3600
# D6 rev 2026-08-04: triggers scale with contributed capital (Σ deposits −
# withdrawals). Review = max($5, 3%), amber = max($4, 2%). At $500 contributed
# that's $15 / $10 — an ordinary INTC-driven ±$12 1σ day no longer pages.
DD_REVIEW_FLOOR_USD = 5.0
DD_REVIEW_PCT = 0.03
DD_AMBER_FLOOR_USD = 4.0
DD_AMBER_PCT = 0.02
MIN_FREE_GB = 3.0   # raised from 1.0 after the 2026-07-27 zero-free incident

# launchd label -> (freshness file, max staleness seconds, kickstart on stale?)
# staleness windows are generous vs tick cadence to avoid flapping.
JOBS = {
    "com.quarki3.scanner": ("/Users/quarki3/scanner/state/orchestrator.log", 15 * 60, True),
    "com.quarki3.ng-dashboard": (None, None, False),     # ng_dashboard; NEVER SIGKILL
    # (legacy com.quarki3.dashboard removed 2026-07-27 — it duplicated
    # ng-dashboard without live keys and kept winning the :8080 port race)
    "com.trading.qc": ("/tmp/qc_portfolio.log", 6 * 3600, True),
    "com.quarki3.swarm": ("/tmp/swarm_orchestrator.log", 30 * 60, True),
}
# combined_mirror manages the live $50 — watch its tick file specifically.
COMBINED_TICK = Path("/Users/quarki3/scanner/state/tick_combined_mirror.txt")
COMBINED_MAX_STALE_S = 30 * 60


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 1_000_000:
            LOG_FILE.write_text(line)
        else:
            with LOG_FILE.open("a") as f:
                f.write(line)
    except OSError:
        pass
    print(line, end="")


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(s: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(s, indent=1))
    except OSError:
        pass


def pushover(title: str, msg: str, key: str, state: dict,
             level: str = "P4") -> None:
    """Send a deduped alert via quarki_notify (raw ntfy only as fallback).
    `key` identifies the alert class."""
    now = time.time()
    last = state.setdefault("last_alerts", {}).get(key, 0)
    if now - last < ALERT_COOLDOWN_S:
        log(f"alert '{key}' suppressed (cooldown): {msg}")
        return
    notifier = HOME / "bin" / "quarki_notify.py"
    if notifier.exists():
        try:
            subprocess.run(["python3", str(notifier), level, title, msg],
                           timeout=20, check=True)
            state["last_alerts"][key] = now
            log(f"ALERT sent via quarki_notify [{key}/{level}]: {msg}")
            return
        except Exception as e:
            log(f"quarki_notify failed ({e!r}) — falling back to raw ntfy")
    creds = {}
    try:
        for ln in NTFY_ENV.read_text().splitlines():
            if "=" in ln:
                k, v = ln.split("=", 1)
                creds[k.strip()] = v.strip()
    except OSError:
        log("ntfy.env unreadable — alert not sent")
        return
    topic = creds.get("NTFY_TOPIC", "")
    if not topic:
        log("NTFY_TOPIC missing — alert not sent")
        return
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}", data=msg.encode(),
            headers={"Title": title.encode("ascii", "replace").decode(),
                     "Priority": "4"})
        urllib.request.urlopen(req, timeout=10)
        state["last_alerts"][key] = now
        log(f"ALERT sent [{key}]: {msg}")
    except Exception as e:
        log(f"ntfy send failed: {e!r}")


def loaded_jobs() -> set[str]:
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=15).stdout
        return {ln.split()[-1] for ln in out.splitlines() if ln.strip()}
    except Exception:
        return set()


def kickstart(label: str) -> None:
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/501/{label}"],
                   capture_output=True, timeout=20)
    log(f"kickstarted {label}")


def check_liveness(state: dict) -> None:
    live = loaded_jobs()
    now = time.time()
    for label, (fresh_file, max_stale, heal) in JOBS.items():
        if label not in live:
            plist = HOME / "Library" / "LaunchAgents" / f"{label}.plist"
            if plist.exists():
                subprocess.run(["launchctl", "bootstrap", "gui/501", str(plist)],
                               capture_output=True, timeout=20)
                log(f"re-bootstrapped missing job {label}")
                pushover("Stack watchdog", f"{label} was unloaded — re-bootstrapped it.",
                         f"unloaded:{label}", state)
            continue
        if fresh_file and max_stale:
            p = Path(fresh_file)
            stale = (not p.exists()) or (now - p.stat().st_mtime > max_stale)
            if stale:
                age = int((now - p.stat().st_mtime) / 60) if p.exists() else -1
                if heal:
                    kickstart(label)
                pushover("Stack watchdog",
                         f"{label} stale ({age} min since last write) — kickstarted.",
                         f"stale:{label}", state)
    # the one file that matters most: combined_mirror (live $50)
    if COMBINED_TICK.exists():
        age = now - COMBINED_TICK.stat().st_mtime
        if age > COMBINED_MAX_STALE_S:
            kickstart("com.quarki3.scanner")
            pushover("Stack watchdog",
                     f"combined_mirror (LIVE $) hasn't ticked in {int(age/60)} min — scanner kickstarted.",
                     "stale:combined_mirror", state)


def check_disk(state: dict) -> None:
    free_gb = shutil.disk_usage("/System/Volumes/Data").free / 1e9
    if free_gb < MIN_FREE_GB:
        pushover("Stack watchdog",
                 f"Disk nearly full: {free_gb:.2f} GB free. Stack writes will start failing.",
                 "disk", state)
    log(f"disk free: {free_gb:.2f} GB")


def check_live_drawdown(state: dict) -> None:
    key = os.environ.get("ALPACA_LIVE_KEY", "")
    sec = os.environ.get("ALPACA_LIVE_SECRET", "")
    if not key:
        # launchd wrapper sources ~/.zshenv; if missing, say so once
        pushover("Stack watchdog", "ALPACA_LIVE_KEY not in env — can't monitor live P&L.",
                 "no_creds", state)
        return
    req = urllib.request.Request(
        "https://api.alpaca.markets/v2/account",
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
    try:
        acct = json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception as e:
        log(f"alpaca account fetch failed: {e!r}")
        return
    equity = float(acct.get("equity", 0))
    try:
        peak = float(json.loads(PEAK_FILE.read_text()).get("peak", equity))
    except Exception:
        peak = equity
    dd = peak - equity

    # Contributed capital from Alpaca transfer activities; cache the last
    # good value so an API hiccup degrades to yesterday's thresholds, and a
    # cold start degrades to the conservative $5/$4 floors.
    contributed = float(state.get("contributed_usd", 0.0))
    try:
        req = urllib.request.Request(
            "https://api.alpaca.markets/v2/account/activities/TRANS?page_size=100",
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
        acts = json.loads(urllib.request.urlopen(req, timeout=10).read())
        contributed = sum(float(a.get("net_amount", 0)) for a in acts)
        state["contributed_usd"] = contributed
    except Exception as e:
        log(f"transfers fetch failed ({e!r}) — using cached contributed "
            f"${contributed:.2f}")
    review_at = max(DD_REVIEW_FLOOR_USD, DD_REVIEW_PCT * contributed)
    amber_at = max(DD_AMBER_FLOOR_USD, DD_AMBER_PCT * contributed)

    log(f"live equity ${equity:.2f}, peak ${peak:.2f}, DD ${dd:.2f} "
        f"(amber ${amber_at:.2f}, review ${review_at:.2f} "
        f"@ ${contributed:.2f} contributed)")
    if dd >= review_at:
        pushover("Live drawdown — review required",
                 f"The live Alpaca account is ${dd:.2f} below its peak "
                 f"(${equity:.2f} vs ${peak:.2f}), past your review trigger "
                 f"of ${review_at:.2f} (3% of ${contributed:.0f} contributed). "
                 "Per D6 this needs a review/assess/repair pass now — "
                 "not hold-and-wait.",
                 "live_dd", state, level="P5")
    elif dd >= amber_at:
        pushover("Live drawdown — amber warning",
                 f"The live Alpaca account is ${dd:.2f} below its peak "
                 f"(${equity:.2f} vs ${peak:.2f}), past the amber line of "
                 f"${amber_at:.2f} (2% of ${contributed:.0f} contributed) but "
                 f"below the ${review_at:.2f} review trigger. No action "
                 "required yet; a review fires if it deepens.",
                 "live_dd_amber", state, level="P4")


def _deadman_ping() -> None:
    """External dead-man's switch (healthchecks.io): tell the outside world
    this host's watchdog ran. If pings stop, healthchecks alerts Robert's
    phone independently of this host — the pager survives a hard crash.
    URL comes from ~/healthchecks.env (secrets bundle, never in git)."""
    import subprocess
    try:
        env = dict(l.strip().split("=", 1)
                   for l in (pathlib.Path.home() / "healthchecks.env").read_text().splitlines()
                   if "=" in l)
        url = env.get("HC_PING_ORACLE_VM_STACK") or env.get("HC_PING_HOST")
        if url:
            subprocess.run(["curl", "-fsS", "-m", "10", "-o", "/dev/null", url],
                           timeout=20, check=False)
    except Exception:
        pass


def main() -> None:
    state = _load_state()
    check_liveness(state)
    check_disk(state)
    check_live_drawdown(state)
    _deadman_ping()
    _save_state(state)


if __name__ == "__main__":
    main()
