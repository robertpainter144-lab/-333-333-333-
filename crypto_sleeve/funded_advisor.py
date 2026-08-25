#!/usr/bin/env python3
"""Funded assist advisor (D62, 2026-08-24). Advisory-ONLY pushes for
Robert's hand-traded Kraken Funded challenge: reads the sleeve's whale
gate (risk_on), the whole-exchange radar, and the hand-entered
funded.json ledger, and fires plain-English P4 tickets he can execute in
the Kraken app in seconds. NEVER trades, never touches the exchange —
signals to a human, D28 repeat-suppressed.

Sizing rationale (encoded, keep simple per D58): the challenge dies at
−5% of start. Ticket = 12% of current balance with a −3% stop → ~0.36%
of the account at risk per trade; even a long losing streak can't reach
the kill line quickly. Entries only suggested while risk_on ≥ 0.60."""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path.home() / "lab-stack" / "crypto_sleeve"
STATE_F = ROOT / "state" / "funded_advisor_state.json"
NOTIFIER = Path.home() / "bin" / "quarki_notify.py"

SIZE_PCT = 12.0          # of current Funded balance, per ticket
STOP_PCT = 3.0           # hard stop per ticket
MOVER_GAIN = 5.0         # radar mover threshold (24h %)
MOVER_DVOL_M = 30.0      # min $M 24h volume — no thin movers
MOVER_COOLDOWN_H = 6     # per-symbol re-alert suppression
RISK_HIGH = 0.75
RISK_LOW = 0.40
REMIND_H = 24            # D28: persisting condition re-reminder


def _load(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def notify(level, title, msg):
    subprocess.run(["python3", str(NOTIFIER), level, title, msg],
                   timeout=30, check=False)


def main():
    now = time.time()
    st = _load(STATE_F, {})
    whale = _load(ROOT / "state" / "whale_signals.json", {})
    radar = _load(ROOT / "state" / "radar.json", {})
    funded = _load(ROOT / "state" / "funded.json", {})

    bal = float(funded.get("balance") or funded.get("start_balance") or 0)
    if bal <= 0:
        return                       # no ledger yet — nothing to advise on
    size = round(bal * SIZE_PCT / 100)
    stop_usd = round(size * STOP_PCT / 100, 2)
    risk_on = whale.get("risk_on")

    # 1) challenge-line guards (from Robert's own reported balance)
    kill = float(funded.get("kill_floor") or 0)
    passt = float(funded.get("pass_target") or 0)
    if kill and bal - kill <= 15:
        if now - st.get("kill_alert_ts", 0) > 3600:
            notify("critical", "Funded: kill line close",
                   f"Your Funded balance ${bal:.2f} is within "
                   f"${bal - kill:.2f} of the -5% kill line (${kill:.0f}). "
                   "Flatten or defend by hand in the Kraken app NOW — "
                   "one more loss can end the challenge.")
            st["kill_alert_ts"] = now
    elif passt and passt - bal <= 10 and bal < passt:
        if now - st.get("pass_alert_ts", 0) > 3600 * 6:
            notify("action", "Funded: pass line close",
                   f"Funded balance ${bal:.2f} is within "
                   f"${passt - bal:.2f} of the +12% pass target "
                   f"(${passt:.0f}). Consider tightening stops to lock "
                   "the pass rather than chasing more.")
            st["pass_alert_ts"] = now

    # 2) whale-gate zone transitions (repeat-suppressed, 24h re-remind)
    if risk_on is not None:
        zone = ("high" if risk_on >= RISK_HIGH
                else "low" if risk_on <= RISK_LOW else "mid")
        prev = st.get("risk_zone")
        stale = now - st.get("risk_zone_ts", 0) > REMIND_H * 3600
        if zone != prev or (zone != "mid" and stale):
            if zone == "high":
                notify("action", "Funded assist: green window",
                       f"The whale gate is strong (risk_on {risk_on:.2f}). "
                       "Good window for a Funded long. Suggested ticket: "
                       f"pick a radar leader, size ~${size}, stop "
                       f"-{STOP_PCT:.0f}% (~${stop_usd} risk), take +6-8%. "
                       "You execute in the Kraken app; report it in the "
                       "Claude terminal so it gets logged.")
            elif zone == "low":
                notify("action", "Funded assist: stand aside",
                       f"The whale gate went weak (risk_on {risk_on:.2f}). "
                       "Skip new Funded entries; tighten stops on anything "
                       "open. Protecting the -5% line beats catching moves.")
            st["risk_zone"] = zone
            st["risk_zone_ts"] = now

    # 3) radar movers — concrete tickets, gated on a decent risk_on
    alerted = st.setdefault("movers", {})
    for r in radar.get("rows", []):
        sym = str(r.get("sym") or "")
        gain = float(r.get("gain") or 0)
        dvol = float(r.get("dvol_m") or 0)
        if gain < MOVER_GAIN or dvol < MOVER_DVOL_M:
            continue
        if risk_on is not None and risk_on < 0.60:
            continue
        if now - alerted.get(sym, 0) < MOVER_COOLDOWN_H * 3600:
            continue
        short = sym.replace("/USD", "")
        notify("action", f"Funded ticket: {short}",
               f"{short} is +{gain:.1f}% on ${dvol:.0f}M volume and the "
               f"whale gate agrees (risk_on {risk_on:.2f}). Suggested "
               f"Funded ticket: BUY ~${size} of {short}, hard stop "
               f"-{STOP_PCT:.0f}% (~${stop_usd} at risk), take +6-8% or "
               "trail by hand. Your call, your thumbs — report it in the "
               "Claude terminal after so it gets logged.")
        alerted[sym] = now

    # trim mover history so state can't grow forever
    st["movers"] = {k: v for k, v in alerted.items()
                    if now - v < 7 * 86400}
    st["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(STATE_F)


if __name__ == "__main__":
    main()
