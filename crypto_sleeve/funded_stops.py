#!/usr/bin/env python3
"""Funded stop-watcher (D62.2, 2026-08-25). Kraken Funded has NO stop-loss
orders — every stop is mental. This closes that gap as far as physics
allows: every minute, price-check Robert's hand-logged Funded positions
(funded.json) against their stop/take levels and PAGE him (critical for
stops — bypasses mute and DND; action for takes) the moment one crosses.
He still pushes the button; this makes sure he knows within a minute.
Read-only against Kraken public tickers; never trades; repeat-suppressed
(one page per breach + re-remind every 30 min while still breached)."""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FUNDED_F = ROOT / "state" / "funded.json"
STATE_F = ROOT / "state" / "funded_stops_state.json"
NOTIFIER = Path.home() / "bin" / "quarki_notify.py"
DEFAULT_STOP_PCT = -3.0     # positions without an explicit 'stop' level
DEFAULT_TAKE_PCT = 30.0     # advisory only; loud nudge, not critical
REMIND_S = 1800

PAIR = {"SOL/USD": "SOLUSD", "BTC/USD": "XBTUSD", "ETH/USD": "ETHUSD",
        "XRP/USD": "XRPUSD", "CRV/USD": "CRVUSD", "DOGE/USD": "XDGUSD",
        "ZEC/USD": "XZECZUSD"}


def px_of(sym):
    code = PAIR.get(sym) or sym.replace("/", "")
    with urllib.request.urlopen(
            f"https://api.kraken.com/0/public/Ticker?pair={code}",
            timeout=8) as r:
        return float(next(iter(json.load(r)["result"].values()))["c"][0])


def notify(level, title, msg):
    subprocess.run(["python3", str(NOTIFIER), level, title, msg],
                   timeout=30, check=False)


def main():
    try:
        d = json.loads(FUNDED_F.read_text())
    except Exception:
        return
    try:
        st = json.loads(STATE_F.read_text())
    except Exception:
        st = {}
    now = time.time()
    for p in d.get("positions") or []:
        sym = p.get("sym")
        entry = p.get("entry")
        if not sym or not entry:
            continue
        try:
            px = px_of(sym)
        except Exception:
            continue
        chg = (px / float(entry) - 1) * 100
        stop_px = p.get("stop")
        stopped = (px <= float(stop_px)) if stop_px else (chg <= DEFAULT_STOP_PCT)
        take_px = p.get("take")
        taken = (px >= float(take_px)) if take_px else (chg >= DEFAULT_TAKE_PCT)
        short = sym.replace("/USD", "")
        k = f"stop:{short}"
        if stopped:
            if now - st.get(k, 0) > REMIND_S:
                lvl_txt = (f"below its stop {stop_px}" if stop_px
                           else f"{chg:+.1f}% vs entry (default -3% line)")
                notify("critical", f"Funded STOP hit: {short}",
                       f"{short} is at {px} — {lvl_txt}. Funded has no stop "
                       "orders, so nothing sells until YOU do: open the "
                       f"Kraken app and close the {short} position (about "
                       f"${float(p.get('size_usd') or 0) * (1 + chg / 100):.0f} now). "
                       "Then tell Claude so the ledger updates.")
                st[k] = now
        else:
            st.pop(k, None)
        kt = f"take:{short}"
        if taken:
            if now - st.get(kt, 0) > REMIND_S:
                notify("action", f"Funded take-profit: {short}",
                       f"{short} is at {px} ({chg:+.1f}% vs your entry) — "
                       "at/above the take level. Consider banking it in the "
                       "Kraken app; the challenge rewards locked gains.")
                st[kt] = now
        else:
            st.pop(kt, None)
    # derived equity point (2026-08-25, Robert: "yellow line stopped
    # producing"): cash_usd is hand-logged, position qtys are hand-logged,
    # prices are public — so the Funded line can extend itself every ~5min
    # between Robert's reports. Marked nowhere as exchange-truth: it IS
    # the same arithmetic Kraken's app does, minus their fees display.
    try:
        if d.get('cash_usd') is not None and (d.get('positions') or []):
            _bal = float(d['cash_usd'])
            _ok = True
            for _p in d['positions']:
                _q = _p.get('qty')
                if not _q:
                    _ok = False
                    break
                _bal += float(_q) * px_of(_p['sym'])
            _eq = d.get('equity') or []
            if _ok and (not _eq or now - _eq[-1]['ts'] >= 300):
                _eq.append({'ts': int(now), 'bal': round(_bal, 2)})
                d['equity'] = _eq[-2000:]
                d['balance'] = round(_bal, 2)
                _t2 = FUNDED_F.with_suffix('.tmp')
                _t2.write_text(json.dumps(d))
                _t2.replace(FUNDED_F)
    except Exception:
        pass
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(STATE_F)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR {e}", flush=True)
