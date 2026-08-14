#!/usr/bin/env python3
"""One-shot check, Monday 2026-08-17 ~07:50 MT (20 min after the open):
did the D43-deferred rebalance buys (TEVA/INTC/...) start on the fresh
daily budget after Friday's JPM trim? Pages (P4 action) only if something
is wrong; quiet P2 confirmation if all is well. Self-removes afterward.
Context: trading-stack/CONTEXT.md 2026-08-14 entries (D43 partial-fill +
exempt-flow fixes)."""
import datetime
import json
import os
import subprocess
import time

STATE = "/Users/quarki3/scanner/state/combined_mirror_state.json"
NOTIFY = "/Users/quarki3/bin/quarki_notify.py"
PLIST = ("/Users/quarki3/Library/LaunchAgents/"
         "com.quarki3.monday-rebalance-check.plist")
LABEL = "gui/501/com.quarki3.monday-rebalance-check"


def notify(level: str, title: str, msg: str) -> None:
    subprocess.run([NOTIFY, level, title, msg], timeout=30)


def check() -> None:
    st = json.load(open(STATE))
    tb = st.get("turnover") or {}
    age_min = (time.time() - float(st.get("ts") or 0)) / 60
    spent = float(tb.get("spent") or 0)
    if age_min > 15:
        notify("action", "Rebalance check: scanner stale",
               f"combined_mirror state is {age_min:.0f} minutes old at "
               f"Monday open, so the deferred INTC/TEVA buys are not "
               f"happening. Restart it with: launchctl kickstart -k "
               f"gui/501/com.quarki3.scanner")
    elif tb.get("day") == time.strftime("%Y-%m-%d") and spent > 5:
        acts = ", ".join(
            f"{a.get('sym')} {a.get('side')} ${abs(a.get('delta_usd') or 0):.0f}"
            for a in (st.get("last_actions") or [])[:4]) or "orders submitted"
        notify("routine", "Rebalance running at Monday open",
               f"The turnover budget reset itself as designed and "
               f"${spent:.0f} of the ~$90 daily budget is already deployed "
               f"({acts}). Nothing for you to do.")
    else:
        sbp = st.get("settled_bp")
        notify("action", "Rebalance check: buys have not started",
               f"20 minutes after the Monday open, discretionary turnover "
               f"spend is ${spent:.2f} — the deferred buys have not fired. "
               f"Settled buying power reads {sbp}. If that is near zero, "
               f"Friday's $89 JPM sale proceeds have not settled yet; "
               f"check again mid-morning before doing anything.")


if __name__ == "__main__":
    if datetime.date.today().isoformat() == "2026-08-17":
        try:
            check()
        except Exception as e:  # never die silently
            try:
                notify("action", "Rebalance check failed",
                       f"The Monday-open check script crashed ({e}). "
                       f"Glance at the Book tab to confirm buys are flowing.")
            except Exception:
                pass
    # one-shot: remove the plist, then unload ourselves (kills this process)
    try:
        os.remove(PLIST)
    except FileNotFoundError:
        pass
    subprocess.Popen(["launchctl", "bootout", LABEL])
