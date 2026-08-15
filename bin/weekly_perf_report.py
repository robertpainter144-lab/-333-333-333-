#!/usr/bin/env python3
"""D49 weekly funding-ladder reporter (Fri 14:06 MT via launchd
com.quarki3.weekly-perf). Computes the Alpaca live book's week-over-week
percentage NET OF INTRAWEEK TRANSFERS and pushes a P4 ntfy so Robert's
Monday deposit/withdrawal decision ($5 per whole 1%, his call) is one
glance. Read-only: per D49's guardrail this script informs Robert and
NOTHING in the trading loop may reference it or its state."""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

STATE = Path("/Users/quarki3/trading-data/weekly_perf_state.json")
RISK = Path("/Users/quarki3/scanner/state/risk_config.json")
NOTIFY = "/Users/quarki3/bin/quarki_notify.py"


def notify(level: str, title: str, msg: str) -> None:
    subprocess.run([NOTIFY, level, title, msg], timeout=30)


def main() -> None:
    eq = json.loads(urllib.request.urlopen(
        "http://localhost:8080/api/portfolio/alpaca_live",
        timeout=20).read())["equity"]
    contributed = float(json.loads(RISK.read_text())
                        ["breakeven_ratchet"]["contributed_usd"])
    try:
        prev = json.loads(STATE.read_text())["prev"]
    except Exception:
        prev = None

    if prev:
        flows = contributed - float(prev["contributed"])
        pct = (eq - float(prev["equity"]) - flows) / float(prev["equity"]) * 100
        amt = round(5 * abs(pct), 2)  # pro-rata (D49 rev 2026-08-14 eve)
        if amt < 0.01:
            move = "flat — nothing due"
        elif pct > 0:
            move = (f"up {pct:.2f}% — your rule says deposit ${amt:.2f} "
                    f"($5 × {abs(pct):.2f}) by Monday's open")
        else:
            move = (f"down {abs(pct):.2f}% — your rule says withdraw "
                    f"${amt:.2f} ($5 × {abs(pct):.2f}) by Monday's open")
        body = (f"Alpaca live closed the week at ${eq:.2f} "
                f"(last Friday ${float(prev['equity']):.2f}"
                + (f", net of ${flows:+.2f} deposits this week" if abs(flows) > 0.005 else "")
                + f"). Week: {pct:+.2f}% — {move}. Your decision either way; "
                f"tell Claude the amount when it lands so contributed_usd "
                f"stays true.")
        notify("action", "Weekly ladder: " + f"{pct:+.2f}%", body)

    STATE.write_text(json.dumps(
        {"prev": {"ts": int(time.time()), "equity": round(eq, 2),
                  "contributed": round(contributed, 2)}}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            notify("action", "Weekly ladder report failed",
                   f"The Friday-close report crashed ({e}) — the week's % "
                   f"was not computed. Check the dashboard is up and rerun "
                   f"~/bin/weekly_perf_report.py.")
        except Exception:
            pass
