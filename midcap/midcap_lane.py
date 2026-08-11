#!/usr/bin/env python3
"""Midcap RSI Lane — PAPER lane testing Arthur's advice (2026-08-11).

The advice, verbatim from the group chat: large caps are too noisy for the
algo, "mid to small caps follow the algo 'rules' a lot better", and RSI
belongs on the daily chart when position trading. This lane tests exactly
that claim the stack's way: same discipline as every other lane, a clean
D7-scoreable closed-trade ledger, zero real-money risk (D7: paper proves).

Rules:
  - Universe: S&P 400 MidCap constituents (the canonical mid-cap set),
    refreshed weekly from Wikipedia, cached at ~/midcap/cache/sp400.json.
  - Signal: daily RSI(14) < 30 (Wilder) on Alpaca IEX daily bars, ranked
    deepest-oversold first. Liquidity floor: price > $5 and 20d average
    dollar volume > $5M. Position-trading timeframe = daily, per the advice.
  - Up to 5 open positions, $15 notional each, 5-trading-day cooldown per
    symbol after a close.
  - Exit on RSI > 55 (reverted), stop -8%, take +12%, or max hold 15
    trading days.
  - PDT guard: a leg entered today is never sold today.
  - Spend capped at min(buying_power, cash) — never borrow.
  - PAPER account orders only — this script must never touch the live base.

Usage:
    python3 midcap_lane.py --leg daily       # 15:45 ET weekdays: exits, then entries
    python3 midcap_lane.py --leg universe    # weekly: refresh S&P 400 cache
    python3 midcap_lane.py --leg daily --dry # data-pipeline smoke test, no orders
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

MIDCAP_DIR = Path.home() / "midcap"
CACHE = MIDCAP_DIR / "cache" / "sp400.json"

STATEDIR = Path.home() / "scanner" / "state"
LEDGER = STATEDIR / "midcap_ledger.jsonl"
STATE = STATEDIR / "midcap_state.json"

PAPER_BASE = "https://paper-api.alpaca.markets"   # PAPER only — never live
DATA_BASE = "https://data.alpaca.markets"
KEY = os.environ.get("ALPACA_PAPER_KEY", "")
SECRET = os.environ.get("ALPACA_PAPER_SECRET", "")

SLOTS = 5
PICK_USD = 15.0
RSI_ENTRY = 30.0
RSI_EXIT = 55.0
STOP_PCT = -8.0
TAKE_PCT = 12.0
MAX_HOLD_DAYS = 15        # trading days
COOLDOWN_DAYS = 5         # trading days before the same symbol can re-enter
MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 5_000_000.0
ET = ZoneInfo("America/New_York")


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
    st.setdefault("cooldown", {})
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


def _trading_days_between(a: str, b: str) -> int:
    da, db = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    n, d = 0, da
    while d < db:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


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


# ── Universe ───────────────────────────────────────────────────────────

WIKI_SP400 = "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies"


def refresh_universe() -> list[str]:
    """Fetch S&P 400 constituents from Wikipedia; cache with fallback."""
    syms: list[str] = []
    try:
        html = requests.get(WIKI_SP400, timeout=30,
                            headers={"User-Agent": "quarki-midcap-lane/1.0"}).text
        # tickers sit inside exchange-quote links: nyse.com/quote/XNYS:AA,
        # nasdaq.com/market-activity/stocks/xyz, etc. The MIC-prefixed form
        # covers the whole constituents table regardless of exchange.
        for m in re.finditer(r'/quote/[A-Z]+:([A-Z][A-Z.\-]{0,5})"', html):
            syms.append(m.group(1))
        for m in re.finditer(r'market-activity/stocks/([A-Za-z.\-]{1,6})"', html):
            syms.append(m.group(1).upper())
        syms = sorted(set(syms))
    except Exception as e:
        print(f"universe fetch failed: {e}", file=sys.stderr)
    if len(syms) < 300:  # sanity: the page should yield ~400 tickers
        try:
            cached = json.loads(CACHE.read_text())
            print(f"using cached universe ({len(cached['syms'])} syms, {cached['date']})")
            return cached["syms"]
        except Exception:
            print("no usable universe (fetch thin, no cache)", file=sys.stderr)
            return syms
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"date": dt.date.today().isoformat(), "syms": syms}))
    print(f"universe refreshed: {len(syms)} S&P 400 symbols")
    return syms


def universe() -> list[str]:
    try:
        cached = json.loads(CACHE.read_text())
        # stale cache (>10 days) refreshes inline rather than trading blind
        if (dt.date.today() - dt.date.fromisoformat(cached["date"])).days <= 10:
            return cached["syms"]
    except Exception:
        pass
    return refresh_universe()


# ── Bars + RSI ─────────────────────────────────────────────────────────

def _daily_bars(syms: list[str], days: int = 40) -> dict[str, list[dict]]:
    """Multi-symbol daily bars, batched; returns sym -> bars (oldest first)."""
    out: dict[str, list[dict]] = {}
    start = (dt.date.today() - dt.timedelta(days=days * 2)).isoformat()
    for i in range(0, len(syms), 100):
        batch = syms[i:i + 100]
        page = None
        while True:
            params = {"symbols": ",".join(batch), "timeframe": "1Day",
                      "start": start, "limit": 10000, "feed": "iex",
                      "adjustment": "split"}
            if page:
                params["page_token"] = page
            try:
                j = _get(f"{DATA_BASE}/v2/stocks/bars", **params)
            except Exception as e:
                print(f"bars batch failed: {e}", file=sys.stderr)
                break
            for s, bars in (j.get("bars") or {}).items():
                out.setdefault(s, []).extend(bars)
            page = j.get("next_page_token")
            if not page:
                break
    return out


def _rsi14(closes: list[float]) -> float | None:
    """Wilder RSI(14) on the full close series."""
    n = 14
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes, closes[1:]):
        d = b - a
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - 100.0 / (1.0 + rs)


def scan(syms: list[str]) -> tuple[list[dict], dict[str, float]]:
    """Returns (oversold candidates sorted deepest-first, rsi-by-symbol)."""
    bars = _daily_bars(syms)
    cands, rsis = [], {}
    for s, bs in bars.items():
        closes = [float(b["c"]) for b in bs]
        if len(closes) < 21:
            continue
        px = closes[-1]
        rsi = _rsi14(closes)
        if rsi is None:
            continue
        rsis[s] = rsi
        if px < MIN_PRICE:
            continue
        dvol = sum(float(b["c"]) * float(b["v"]) for b in bs[-20:]) / min(20, len(bs))
        if dvol < MIN_DOLLAR_VOL:
            continue
        if rsi < RSI_ENTRY:
            cands.append({"sym": s, "rsi": round(rsi, 2), "px": px,
                          "dvol": round(dvol)})
    cands.sort(key=lambda c: c["rsi"])
    return cands, rsis


# ── Legs ───────────────────────────────────────────────────────────────

def check_exits(st: dict, rsis: dict[str, float], today: str, dry: bool) -> None:
    for sym in list(st["open_legs"].keys()):
        leg = st["open_legs"][sym]
        if leg["entry_date"] == today:
            continue  # PDT guard
        px = _latest_px(sym)
        if px is None:
            continue
        chg = (px / leg["entry_px"] - 1) * 100
        held = _trading_days_between(leg["entry_date"], today)
        rsi = rsis.get(sym)
        reason = None
        if chg <= STOP_PCT:
            reason = "stop"
        elif chg >= TAKE_PCT:
            reason = "take"
        elif rsi is not None and rsi >= RSI_EXIT:
            reason = "rsi_reverted"
        elif held >= MAX_HOLD_DAYS:
            reason = "max_hold"
        if not reason:
            continue
        if dry:
            print(f"DRY exit: {sym} {chg:+.1f}% ({reason}, {held}d)")
            continue
        o = _order(sym, "sell", qty=leg["qty"])
        if not o:
            continue
        fill = _filled(o["id"])
        exit_px = float(fill["filled_avg_price"]) if fill else px
        realized_pct = (exit_px / leg["entry_px"] - 1) * 100
        realized_usd = (exit_px - leg["entry_px"]) * leg["qty"]
        _append_ledger({
            "sym": sym,
            "theme": "midcap_rsi",
            "score": leg.get("score", 0.0),
            "entry_date": leg["entry_date"],
            "exit_date": today,
            "entry_px": leg["entry_px"],
            "exit_px": round(exit_px, 4),
            "pl_pct": round(realized_pct, 3),
            "pl_usd": round(realized_usd, 4),
            "reason": reason,
            "hold_days": held,
            "cat_detail": f"rsi14 daily; exit_rsi={rsi if rsi is None else round(rsi, 1)}",
        })
        st["cooldown"][sym] = today
        del st["open_legs"][sym]
        _save_state(st)
        print(f"midcap_lane exit: {sym} {realized_pct:+.1f}% "
              f"(${realized_usd:+.2f}, {reason}, {held}d)")


def enter_picks(st: dict, cands: list[dict], today: str, dry: bool) -> None:
    open_n = len(st["open_legs"])
    for c in cands:
        if open_n >= SLOTS:
            break
        sym = c["sym"]
        if sym in st["open_legs"]:
            continue
        cd = st["cooldown"].get(sym)
        if cd and _trading_days_between(cd, today) < COOLDOWN_DAYS:
            continue
        if dry:
            print(f"DRY entry: {sym} rsi={c['rsi']} px={c['px']:.2f} dvol=${c['dvol']:,}")
            open_n += 1
            continue
        if _spendable_cash() < PICK_USD:
            print("paper cash exhausted — skipping remaining entries")
            break
        o = _order(sym, "buy", notional=PICK_USD)
        if not o:
            continue
        fill = _filled(o["id"])
        if not fill:
            continue
        st["open_legs"][sym] = {
            "entry_date": today,
            "entry_px": float(fill["filled_avg_price"]),
            "qty": float(fill["filled_qty"]),
            "score": c["rsi"],   # entry RSI doubles as the conviction score
        }
        _save_state(st)
        open_n += 1
        print(f"midcap_lane entry: {sym} rsi={c['rsi']} @ {fill['filled_avg_price']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leg", choices=["daily", "universe"], required=True)
    ap.add_argument("--dry", action="store_true",
                    help="no orders, no clock gate — data-pipeline smoke test")
    args = ap.parse_args()

    if args.leg == "universe":
        refresh_universe()
        return

    if not args.dry and not _market_open():
        print("market closed — skipping")
        return
    if not args.dry and (not KEY or not SECRET):
        _notify("p4", "midcap lane: missing paper keys",
                "ALPACA_PAPER_KEY/SECRET not in env — the midcap RSI paper "
                "lane cannot trade. Check ~/quarki.env sourcing in the "
                "launchd plist.")
        sys.exit(1)

    today = dt.datetime.now(ET).date().isoformat()
    st = _load_state()
    syms = universe()
    if not syms:
        _notify("p4", "midcap lane: no universe",
                "S&P 400 fetch failed and no cache exists — lane skipped today.")
        sys.exit(1)
    cands, rsis = scan(syms)
    print(f"universe={len(syms)} scanned={len(rsis)} oversold={len(cands)} "
          f"open={len(st['open_legs'])}")
    check_exits(st, rsis, today, args.dry)
    enter_picks(st, cands, today, args.dry)


if __name__ == "__main__":
    main()
