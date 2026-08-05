#!/usr/bin/env python3
"""Quarki VM stack watchdog — systemd edition of the Mac stack_watchdog.

Every minute (quarki-watchdog.timer): service liveness (restart dead units),
tick freshness (restart stale scanner), disk space, live-account drawdown vs
the $5 review trigger. Alerts via Pushover (creds ~/ibkr_bot/pushover.env),
deduped with a per-alert cooldown. Never deletes anything.
"""
import json
import os
import subprocess
import time
import urllib.request
import urllib.parse
from pathlib import Path

HOME = Path.home()
STATE_FILE = HOME / "trading-data" / "vm_watchdog_state.json"
LOG = HOME / "trading-data" / "vm_watchdog.log"
NTFY_ENV = HOME / "ntfy.env"
PEAK_FILE = HOME / "trading-data" / "portfolio_peaks" / "alpaca_live.json"

ALERT_COOLDOWN_S = 6 * 3600
# Self-heal escalation: a heal is "not sticking" if the same key needs
# healing this many times inside the window — that's when Robert gets paged.
HEAL_ESCALATE_N = 3
HEAL_WINDOW_S = 45 * 60
# D6 (revised 2026-08-04, user call): thresholds scale with contributed
# capital (sum of deposits - withdrawals) so a deposit can't turn ordinary
# noise into forced reviews. Dollar floors preserve the original behavior
# on a small book.
DD_TRIGGER_PCT = 0.03
DD_AMBER_PCT = 0.02
DD_FLOOR_TRIGGER_USD = 5.0
DD_FLOOR_AMBER_USD = 4.0


def dd_thresholds() -> tuple[float, float]:
    """(amber, trigger) in dollars: 2%/3% of contributed capital, floored."""
    contributed = 0.0
    try:
        rows = json.load(urllib.request.urlopen(
            "http://localhost:8080/api/transfers/alpaca_live", timeout=10))
        contributed = sum(float(r.get("amount", 0) or 0) for r in rows)
    except Exception:
        pass
    amber = max(DD_FLOOR_AMBER_USD, contributed * DD_AMBER_PCT)
    trigger = max(DD_FLOOR_TRIGGER_USD, contributed * DD_TRIGGER_PCT)
    return amber, trigger
MIN_FREE_GB = 5.0  # VM has 40G free; alert early

UNITS = ["quarki-scanner.service", "quarki-dashboard.service", "quarki-qc.service"]
TICKS = {  # freshness file -> (max stale s, unit to restart)
    str(HOME / "scanner/state/tick_combined_mirror.txt"): (30 * 60, "quarki-scanner.service"),
    str(HOME / "scanner/state/tick_watchdog.txt"): (15 * 60, "quarki-scanner.service"),
}


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG.open("a") as f:
        f.write(f"[{ts}] {msg}\n")


def state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save(st: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, indent=2))


import sys
sys.path.insert(0, str(HOME / "bin"))
from quarki_notify import notify  # tiered ntfy (2026-08-02): see its docstring


def alert(level: str, title: str, msg: str, key: str, st: dict) -> None:
    """Cooldown-deduped tiered notify. Self-healed events go out as
    'routine' (P2, silent); only heal failures / escalations page Robert."""
    now = time.time()
    if now - st.get(f"alert_{key}", 0) < ALERT_COOLDOWN_S:
        log(f"alert '{key}' suppressed (cooldown): {msg}")
        return
    st[f"alert_{key}"] = now
    sent = notify(level, f"[VM] {title}", msg)
    log(f"ntfy {level} {'sent' if sent else 'FAILED'}: {title}: {msg}")


def heal_event(key: str, st: dict) -> int:
    """Record a self-heal for `key`; return how many healed in the window."""
    now = time.time()
    hist = [t for t in st.get(f"heals_{key}", []) if now - t < HEAL_WINDOW_S]
    hist.append(now)
    st[f"heals_{key}"] = hist
    return len(hist)


def restart(unit: str) -> None:
    subprocess.run(["sudo", "systemctl", "restart", unit], timeout=60)
    log(f"restarted {unit}")


def main() -> None:
    st = state()
    now = time.time()

    # Service liveness
    for unit in UNITS:
        r = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
        if r.stdout.strip() != "active":
            restart(unit)
            n = heal_event(f"down_{unit}", st)
            if n >= HEAL_ESCALATE_N:
                alert("critical", "Self-heal NOT sticking",
                      f"{unit} down {n}x in {HEAL_WINDOW_S//60}min despite restarts — needs a human.",
                      f"esc_down_{unit}", st)
            else:
                alert("routine", "Self-healed",
                      f"{unit} was {r.stdout.strip()} — restarted ({n}/{HEAL_ESCALATE_N} in window).",
                      f"down_{unit}", st)

    # Tick freshness
    for path, (max_stale, unit) in TICKS.items():
        p = Path(path)
        stale = (not p.exists()) or (now - p.stat().st_mtime > max_stale)
        if stale:
            age = int((now - p.stat().st_mtime) / 60) if p.exists() else -1
            restart(unit)
            n = heal_event(f"stale_{p.name}", st)
            if n >= HEAL_ESCALATE_N:
                alert("critical", "Self-heal NOT sticking",
                      f"{p.name} stale {n}x in {HEAL_WINDOW_S//60}min despite restarts — needs a human.",
                      f"esc_stale_{p.name}", st)
            else:
                alert("routine", "Self-healed",
                      f"{p.name} stale ({age} min) — {unit} restarted ({n}/{HEAL_ESCALATE_N} in window).",
                      f"stale_{p.name}", st)

    # Disk
    s = os.statvfs("/")
    free_gb = s.f_bavail * s.f_frsize / 1e9
    log(f"disk free: {free_gb:.2f} GB")
    if free_gb < MIN_FREE_GB:
        alert("action", "Disk low", f"VM disk low: {free_gb:.1f} GB free.", "disk", st)

    # Live drawdown vs $5 trigger
    key, sec = os.environ.get("ALPACA_LIVE_KEY"), os.environ.get("ALPACA_LIVE_SECRET")
    if key and sec:
        try:
            req = urllib.request.Request(
                "https://api.alpaca.markets/v2/account",
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec})
            acct = json.load(urllib.request.urlopen(req, timeout=10))
            equity = float(acct.get("equity", 0))
            peak = equity
            try:
                peak = max(float(json.loads(PEAK_FILE.read_text()).get("peak", equity)), equity)
            except Exception:
                pass
            dd = peak - equity
            amber_usd, trigger_usd = dd_thresholds()
            log(f"live equity ${equity:.2f}, peak ${peak:.2f}, DD ${dd:.2f} "
                f"(amber ${amber_usd:.2f} / trigger ${trigger_usd:.2f})")
            if dd >= trigger_usd:
                alert("critical", "LIVE DRAWDOWN",
                      f"${dd:.2f} >= ${trigger_usd:.2f} review trigger (D6: 3% of "
                      f"contributed capital). Equity ${equity:.2f}.", "dd", st)
            elif dd >= amber_usd:
                alert("action", "Drawdown amber",
                      f"${dd:.2f} of ${trigger_usd:.2f} D6 trigger (3% of contributed "
                      f"capital). Equity ${equity:.2f}.", "dd_amber", st)
        except Exception as e:
            log(f"alpaca check failed: {e!r}")

    save(st)


if __name__ == "__main__":
    main()
