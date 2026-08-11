#!/usr/bin/env python3
"""Earnvol Lane — PAPER day-volatility lane (Arthur's advice #3, 2026-08-11).

"Concentrate on those that are most likely to move today (if you're doing
volatility trading) — earnings or special situation." This lane tests the
tradeable version of that: post-earnings gap CONTINUATION, intraday only.

Mechanics:
  - Reporters whose reaction window is TODAY: today's pre-market reporters
    plus yesterday's after-hours reporters (Nasdaq public earnings calendar).
  - Entry 09:35 ET: among those, gap vs yesterday's close >= +2.5%, price >
    $5, yesterday dollar volume > $5M — buy the top 3 gappers, $15 notional
    each, on the Alpaca PAPER account.
  - Exit 15:50 ET the SAME day: everything closes. No overnight holds, ever —
    the bet is only "earnings movers keep moving intraday".
  - Ledger: one row per round trip (same shape as moonshot/midcap lanes) →
    state/earnvol_ledger.jsonl, D7-scoreable, zero live weight until proven.

Usage:
    python3 earnvol_lane.py --leg entry          # 09:35 ET weekdays
    python3 earnvol_lane.py --leg exit           # 15:50 ET weekdays
    python3 earnvol_lane.py --leg entry --dry    # pipeline test, no orders
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

STATEDIR = Path.home() / "scanner" / "state"
LEDGER = STATEDIR / "earnvol_ledger.jsonl"
STATE = STATEDIR / "earnvol_state.json"

PAPER_BASE = "https://paper-api.alpaca.markets"   # PAPER only — never live
DATA_BASE = "https://data.alpaca.markets"
KEY = os.environ.get("ALPACA_PAPER_KEY", "")
SECRET = os.environ.get("ALPACA_PAPER_SECRET", "")

SLOTS = 3
PICK_USD = 15.0
MIN_GAP_PCT = 2.5
MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 5_000_000.0
ET = ZoneInfo("America/New_York")

NASDAQ_CAL = "https://api.nasdaq.com/api/calendar/earnings"
NASDAQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


def _h() -> dict:
    return {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET,
            "Content-Type": "application/json"}


def _notify(level: str, title: str, msg: str) -> None:
    try:
        subprocess.run(
            ["/opt/homebrew/bin/python3.10", str(Path.home() / "bin" / "quarki_notify.py"),
             level, title, msg],
            timeout=15, check=False,
        )
    except Exception:
        pass


def _get(url: str, **params):
    r = requests.get(url, headers=_h(), params=params or None, timeout=20)
    r.raise_for_status()
    return r.json()


def _latest_px(sym: str) -> float | None:
    try:
        j = _get(f"{DATA_BASE}/v2/stocks/{sym}/trades/latest", feed="iex")
        return float(j["trade"]["p"])
    except Exception:
        return None


def _load_state() -> dict:
    try:
        st = json.loads(STATE.read_text())
    except Exception:
        st = {}
    st.setdefault("open_legs", {})
    return st


def _save_state(st: dict) -> None:
    STATEDIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(STATE)


def _append_ledger(row: dict) -> None:
    STATEDIR.mkdir(parents=True, exist_ok=True)
    row = {"ts": time.time(), **row}
    with LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")


def _market_open() -> bool:
    try:
        return bool(_get(f"{PAPER_BASE}/v2/clock").get("is_open"))
    except Exception:
        return False


def _spendable_cash() -> float:
    try:
        acc = _get(f"{PAPER_BASE}/v2/account")
        return min(float(acc.get("buying_power", 0) or 0),
                   float(acc.get("cash", 0) or 0))
    except Exception:
        return 0.0


def _order(sym: str, side: str, notional: float | None = None,
           qty: float | None = None) -> dict | None:
    body = {"symbol": sym, "side": side, "type": "market",
            "time_in_force": "day"}
    if notional is not None:
        body["notional"] = f"{notional:.2f}"
    else:
        body["qty"] = f"{qty:.9f}"
    try:
        r = requests.post(f"{PAPER_BASE}/v2/orders", headers=_h(),
                          json=body, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"order failed {side} {sym}: {e}", file=sys.stderr)
        return None


def _filled(order_id: str) -> dict | None:
    for _ in range(10):
        try:
            o = _get(f"{PAPER_BASE}/v2/orders/{order_id}")
            if o.get("status") == "filled":
                return o
        except Exception:
            pass
        time.sleep(2)
    return None


# ── Reporters whose reaction window is today ───────────────────────────

def _calendar(date: str) -> list[dict]:
    try:
        r = requests.get(NASDAQ_CAL, params={"date": date},
                         headers=NASDAQ_HEADERS, timeout=25)
        r.raise_for_status()
        return ((r.json().get("data") or {}).get("rows")) or []
    except Exception as e:
        print(f"nasdaq calendar {date} failed: {e}", file=sys.stderr)
        return []


def todays_reporters() -> list[str]:
    today = dt.datetime.now(ET).date()
    prev = today - dt.timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= dt.timedelta(days=1)
    syms: set[str] = set()
    for row in _calendar(today.isoformat()):
        if "pre-market" in str(row.get("time", "")):
            syms.add(str(row.get("symbol", "")).upper())
    for row in _calendar(prev.isoformat()):
        if "after-hours" in str(row.get("time", "")):
            syms.add(str(row.get("symbol", "")).upper())
    return sorted(s for s in syms if s.isalpha() and 1 <= len(s) <= 5)


def gappers(syms: list[str]) -> list[dict]:
    """Yesterday-close→now gap for each reporter; top gappers first."""
    out = []
    start = (dt.date.today() - dt.timedelta(days=10)).isoformat()
    for i in range(0, len(syms), 100):
        batch = syms[i:i + 100]
        try:
            j = _get(f"{DATA_BASE}/v2/stocks/bars",
                     symbols=",".join(batch), timeframe="1Day", start=start,
                     limit=10000, feed="iex", adjustment="split")
        except Exception as e:
            print(f"bars failed: {e}", file=sys.stderr)
            continue
        for s, bars in (j.get("bars") or {}).items():
            if len(bars) < 2:
                continue
            prev_bar = bars[-1]
            # if today's bar already exists (post-open), the previous close
            # is bars[-2]; pre-open it's bars[-1]
            today_iso = dt.datetime.now(ET).date().isoformat()
            if str(prev_bar.get("t", "")).startswith(today_iso) and len(bars) >= 2:
                prev_bar = bars[-2]
            prev_close = float(prev_bar["c"])
            dvol = float(prev_bar["c"]) * float(prev_bar["v"])
            px = _latest_px(s)
            if not px or px < MIN_PRICE or prev_close <= 0:
                continue
            if dvol < MIN_DOLLAR_VOL:
                continue
            gap = (px / prev_close - 1) * 100
            if gap >= MIN_GAP_PCT:
                out.append({"sym": s, "gap": round(gap, 2), "px": px,
                            "dvol": round(dvol)})
    out.sort(key=lambda g: -g["gap"])
    return out


# ── Legs ───────────────────────────────────────────────────────────────

def leg_entry(dry: bool) -> None:
    today = dt.datetime.now(ET).date().isoformat()
    st = _load_state()
    if st["open_legs"]:
        print("legs already open — exit leg must run first")
        return
    reporters = todays_reporters()
    print(f"reporters today: {len(reporters)}")
    if not reporters:
        return
    cands = gappers(reporters)
    print(f"qualified gappers: {len(cands)} -> {[c['sym'] for c in cands[:6]]}")
    for c in cands[:SLOTS]:
        if dry:
            print(f"DRY entry: {c['sym']} gap +{c['gap']}% px={c['px']:.2f}")
            continue
        if _spendable_cash() < PICK_USD:
            print("paper cash exhausted")
            break
        o = _order(c["sym"], "buy", notional=PICK_USD)
        if not o:
            continue
        fill = _filled(o["id"])
        if not fill:
            continue
        st["open_legs"][c["sym"]] = {
            "entry_date": today,
            "entry_px": float(fill["filled_avg_price"]),
            "qty": float(fill["filled_qty"]),
            "score": c["gap"],
        }
        _save_state(st)
        print(f"earnvol entry: {c['sym']} gap +{c['gap']}% @ {fill['filled_avg_price']}")


def leg_exit(dry: bool) -> None:
    today = dt.datetime.now(ET).date().isoformat()
    st = _load_state()
    for sym in list(st["open_legs"].keys()):
        leg = st["open_legs"][sym]
        px = _latest_px(sym)
        if dry:
            print(f"DRY exit: {sym} @ {px}")
            continue
        o = _order(sym, "sell", qty=leg["qty"])
        if not o:
            continue
        fill = _filled(o["id"])
        exit_px = float(fill["filled_avg_price"]) if fill else (px or leg["entry_px"])
        realized_pct = (exit_px / leg["entry_px"] - 1) * 100
        realized_usd = (exit_px - leg["entry_px"]) * leg["qty"]
        _append_ledger({
            "sym": sym,
            "theme": "earnvol",
            "score": leg.get("score", 0.0),
            "entry_date": leg["entry_date"],
            "exit_date": today,
            "entry_px": leg["entry_px"],
            "exit_px": round(exit_px, 4),
            "pl_pct": round(realized_pct, 3),
            "pl_usd": round(realized_usd, 4),
            "reason": "eod_close",
            "hold_days": 0,
            "cat_detail": f"earnings gap continuation; entry_gap={leg.get('score')}%",
        })
        del st["open_legs"][sym]
        _save_state(st)
        print(f"earnvol exit: {sym} {realized_pct:+.1f}% (${realized_usd:+.2f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", choices=["entry", "exit"], required=True)
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    if not args.dry and not _market_open():
        print("market closed — skipping")
        return
    if not args.dry and (not KEY or not SECRET):
        _notify("p4", "earnvol lane: missing paper keys",
                "ALPACA_PAPER_KEY/SECRET not in env — the earnings day-vol "
                "paper lane cannot trade.")
        sys.exit(1)
    if args.leg == "entry":
        leg_entry(args.dry)
    else:
        leg_exit(args.dry)


if __name__ == "__main__":
    main()
