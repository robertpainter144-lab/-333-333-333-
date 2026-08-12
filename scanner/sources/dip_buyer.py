#!/opt/homebrew/bin/python3.10
"""Index Dip-Buyer benchmark — Robert's simplified "buy the dip" idea, run
forward as a fully virtual, zero-broker paper ledger (D4-clean: display-only,
STRUCTURALLY isolated from real money, same doctrine as every other catalog
row).

BUILT 2026-08-12. PRIOR ART: dip-buy variants were backtested 2026-08-03
(see trading-stack/CONTEXT.md and project_dip_buy.md) and LOST to
regime-only on out-of-sample data — the SPY Overlay (regime-based) is the
surviving LIVE form of that idea. This file is NOT a revival of live
trading; it is a live-forward benchmark that keeps watching Robert's
original simplified thesis ("buy more on every double-digit dip") so the
catalog scoreboard can show, honestly and continuously, whether it would
have caught up.

THE RULE (deliberately simple, matching the spec verbatim)
------------------------------------------------------------
  - Universe: SPY + QQQ, 50/50 attention split.
  - $10,000 virtual ledger, struck 2026-08-12.
  - Inception: buy 25% of starting cash into SPY and 25% into QQQ at the
    inception close (50% invested, 50% cash reserve for dips).
  - Track each symbol's rolling all-time-high of daily closes.
  - Whenever a symbol's close is >=10% below its own rolling peak AND the
    ledger still has uninvested cash, deploy 25% of the REMAINING cash into
    that symbol at that close. Max one tranche per symbol per 5 trading
    days (so a single multi-day crash doesn't blow the whole reserve on
    day one).
  - NO SELLING, EVER — that is the point of this benchmark: buy-and-hold
    plus mechanical dip-adds, nothing else.

DATA: Alpaca market DATA API only (GET .../v2/stocks/{sym}/bars,
feed=iex), auth via APCA-API-KEY-ID / APCA-API-SECRET-KEY headers read from
env ALPACA_PAPER_KEY / ALPACA_PAPER_SECRET (same paper-data keys the other
virtual lanes use). This file never calls, and must never call, any
trading/order endpoint — there is no broker account attached to this
ledger at all, paper or otherwise.

STATE: state/dip_buyer_state.json is the ledger of record (cash, holdings,
rolling peaks, trading-day counter, tranche cooldowns, NAV curve). Atomic
writes only (tmp sibling + os.replace). Every dip-buy (and the one-time
inception buy) also gets an append-only line in state/dip_buyer_ledger.jsonl
for audit trail, mirroring the sub5_watch.py convention.

CATALOG WIRING: this module owns one key in the SHARED
state/autopilot_catalog_curves.json store — CATALOG_KEY below — written in
the exact {"curve": {date: nav}, "shares": {...}, "weights_hash": ...,
"updated_ts": ...} shape autopilot_catalog.py already uses, so the existing
owner_catalog.js card renderer draws it with zero frontend changes. NAV is
expressed on the same base-100 convention as every other pilot (nav =
100 * ledger_value / starting_cash) so "since inception" math in the card
renderer (which assumes base 100) comes out correct. A matching metadata-
only pilot entry (CATALOG_KEY, all "_"-prefixed keys, no numeric weights)
lives in state/autopilot_catalog.json's "pilots" dict — that's what makes
edge_ledger.py's _autopilot_catalog_rows() auto-generate the
"autopilot_cat:<CATALOG_KEY>" row that /api/catalog serves, with zero
changes needed to edge_ledger.py, api.py, or owner_catalog.js. Because that
pilot entry has no positive numeric weight keys, autopilot_catalog.py's own
generic _holdings()-based marking loop skips it (h == {} is falsy), so the
two writers never collide over the same curve entry.

DEPENDENCIES: stdlib + requests only (already a dependency of every sibling
in this directory). Fail-soft throughout: any network or parse error is
caught, logged, and skipped — a bad tick must never crash the scanner's
worker thread or corrupt existing state.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR  # noqa: E402

LANE = "dip_buyer"
SYMBOLS = ["SPY", "QQQ"]
CATALOG_KEY = "DipBuyer_SPYQQQ"  # must match the pilot key in autopilot_catalog.json

STARTING_CASH = 10000.0
INITIAL_DEPLOY_FRACTION = 0.25   # 25% each of SPY/QQQ at inception -> 50% invested
DIP_THRESHOLD = 0.10             # buy the dip at >=10% below rolling all-time-high
TRANCHE_FRACTION = 0.25          # 25% of REMAINING cash per dip tranche
TRANCHE_COOLDOWN_TRADING_DAYS = 5
HISTORICAL_LOOKBACK_YEARS = 15   # depth used to seed each symbol's rolling peak

STATE_PATH = STATEDIR / "dip_buyer_state.json"
LEDGER_PATH = STATEDIR / "dip_buyer_ledger.jsonl"
CURVES_PATH = STATEDIR / "autopilot_catalog_curves.json"

DATA_BASE = "https://data.alpaca.markets"
REQUEST_TIMEOUT = 15


def _headers() -> dict[str, str] | None:
    key = os.environ.get("ALPACA_PAPER_KEY")
    secret = os.environ.get("ALPACA_PAPER_SECRET")
    if not key or not secret:
        return None
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _latest_daily_bar(sym: str, headers: dict) -> tuple[str, float] | None:
    """Most recent daily close: (date, close), or None on any failure."""
    try:
        r = requests.get(
            f"{DATA_BASE}/v2/stocks/{sym}/bars",
            params={"timeframe": "1Day", "limit": 1, "feed": "iex"},
            headers=headers, timeout=REQUEST_TIMEOUT,
        )
        if not r.ok:
            print(f"[dip_buyer] latest bar fetch failed for {sym}: HTTP {r.status_code}")
            return None
        bars = (r.json() or {}).get("bars") or []
        if not bars:
            return None
        b = bars[-1]
        date_str = str(b.get("t", ""))[:10]
        close = b.get("c")
        if not date_str or close is None:
            return None
        return date_str, float(close)
    except Exception as e:
        print(f"[dip_buyer] latest bar fetch exception for {sym}: {e}")
        return None


def _historical_peak(sym: str, headers: dict, max_pages: int = 5) -> float | None:
    """Rolling all-time-high seed: max daily close over a deep lookback window.
    Bounded to max_pages requests (10k bars/page) so one tick can't run away."""
    start = (dt.date.today() - dt.timedelta(days=365 * HISTORICAL_LOOKBACK_YEARS)).isoformat()
    peak = None
    page_token = None
    for _ in range(max_pages):
        params = {"timeframe": "1Day", "start": start, "limit": 10000, "feed": "iex"}
        if page_token:
            params["page_token"] = page_token
        try:
            r = requests.get(f"{DATA_BASE}/v2/stocks/{sym}/bars", params=params,
                              headers=headers, timeout=REQUEST_TIMEOUT)
            if not r.ok:
                print(f"[dip_buyer] historical peak fetch failed for {sym}: HTTP {r.status_code}")
                break
            data = r.json() or {}
            for b in (data.get("bars") or []):
                c = b.get("c")
                if c is not None:
                    peak = c if peak is None else max(peak, c)
            page_token = data.get("next_page_token")
            if not page_token:
                break
        except Exception as e:
            print(f"[dip_buyer] historical peak fetch exception for {sym}: {e}")
            break
    return float(peak) if peak is not None else None


def _new_state() -> dict:
    return {
        "inception_done": False,
        "inception_date": None,
        "cash": STARTING_CASH,
        "holdings": {},                 # sym -> {"qty": float, "cost_basis_usd": float}
        "peak_close": {},               # sym -> rolling all-time-high close
        "last_close": {},               # sym -> most recent known close (carry-forward)
        "trading_day_index": 0,         # bumped once per NEW distinct bar date seen
        "last_tranche_trading_day": {}, # sym -> trading_day_index at last dip buy
        "tranche_count": {},            # sym -> number of dip tranches bought
        "curve": {},                    # date -> NAV, base 100 (matches sibling pilots)
        "updated_ts": None,
    }


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True))
    os.replace(tmp, path)


def _append_ledger(row: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = dict(row)
    row.setdefault("ts", int(time.time()))
    with LEDGER_PATH.open("a") as f:
        f.write(json.dumps(row) + "\n")


def _maybe_dip_buy(sym: str, state: dict, bar_date: str) -> None:
    if state["cash"] <= 1.0:
        return
    peak = state["peak_close"].get(sym)
    close = state["last_close"].get(sym)
    if not peak or not close or peak <= 0:
        return
    if close > peak * (1 - DIP_THRESHOLD):
        return  # not dipped >=10% off the rolling all-time-high
    last_day = state["last_tranche_trading_day"].get(sym)
    if last_day is not None and (state["trading_day_index"] - last_day) < TRANCHE_COOLDOWN_TRADING_DAYS:
        return  # cooldown: one tranche per symbol per 5 trading days max

    tranche_usd = round(TRANCHE_FRACTION * state["cash"], 2)
    if tranche_usd <= 0:
        return
    qty = tranche_usd / close
    h = state["holdings"].setdefault(sym, {"qty": 0.0, "cost_basis_usd": 0.0})
    h["qty"] = round(h["qty"] + qty, 6)
    h["cost_basis_usd"] = round(h["cost_basis_usd"] + tranche_usd, 2)
    state["cash"] = round(state["cash"] - tranche_usd, 2)
    state["last_tranche_trading_day"][sym] = state["trading_day_index"]
    state["tranche_count"][sym] = state["tranche_count"].get(sym, 0) + 1

    dip_pct = round((1 - close / peak) * 100, 2)
    _append_ledger({
        "event": "dip_buy", "date": bar_date, "sym": sym, "close": close,
        "peak": peak, "dip_pct": dip_pct, "tranche_usd": tranche_usd,
        "qty": qty, "tranche_n": state["tranche_count"][sym],
        "cash_after": state["cash"],
    })
    print(f"[dip_buyer] DIP BUY {sym} @ {close:.2f} ({dip_pct:.1f}% off peak {peak:.2f}) "
          f"— deployed ${tranche_usd:.2f} (tranche #{state['tranche_count'][sym]}), "
          f"cash now ${state['cash']:.2f}")


def _mirror_curve_to_shared_store(state: dict) -> None:
    """Write this pilot's curve into the SAME shared file/schema
    autopilot_catalog.py uses, so the existing card renderer draws it with
    zero frontend changes. Read-modify-write on the whole file — same
    pattern autopilot_catalog.py already uses for this file; there is no
    locking today for concurrent writers, so this mirrors an already-
    accepted risk (self-heals next tick) rather than introducing a new one."""
    curves = {}
    if CURVES_PATH.exists():
        try:
            curves = json.loads(CURVES_PATH.read_text())
        except Exception as e:
            print(f"[dip_buyer] could not read shared curves file ({e}) — skipping curve mirror this tick")
            return
    shares = {sym: h["qty"] for sym, h in state["holdings"].items()}
    curves[CATALOG_KEY] = {
        "curve": dict(state["curve"]),
        "shares": shares,
        "weights_hash": hashlib.sha256(
            json.dumps({"cash": state["cash"], "shares": shares}, sort_keys=True).encode()
        ).hexdigest()[:16],
        "updated_ts": int(time.time()),
        "inception": state.get("inception_date"),
    }
    _atomic_write_json(CURVES_PATH, curves)


def _run() -> None:
    headers = _headers()
    if headers is None:
        log_tick(LANE, "ALPACA_PAPER_KEY/SECRET not set — idle")
        return

    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text())
        except Exception as e:
            # Refuse to silently reinitialize a possibly-corrupt ledger —
            # log loudly and skip rather than risk wiping accumulated state.
            log_tick(LANE, f"state file unreadable ({e}) — refusing to touch it, skipping this run")
            return
    else:
        state = _new_state()

    # Seed each symbol's rolling all-time-high once (subsequent ticks just
    # extend it with max(peak, new close) — cheap, no repeat deep fetch).
    for sym in SYMBOLS:
        if sym not in state["peak_close"]:
            peak = _historical_peak(sym, headers)
            if peak is not None:
                state["peak_close"][sym] = peak
                print(f"[dip_buyer] seeded {sym} rolling peak close = {peak:.2f} "
                      f"(lookback {HISTORICAL_LOOKBACK_YEARS}y)")
            else:
                print(f"[dip_buyer] could not seed peak for {sym} this tick — will retry")

    today: dict[str, tuple[str, float]] = {}
    for sym in SYMBOLS:
        bar = _latest_daily_bar(sym, headers)
        if bar is not None:
            today[sym] = bar
        else:
            print(f"[dip_buyer] no fresh price for {sym} this tick — carrying forward last known close")

    if not today and not state["last_close"]:
        log_tick(LANE, "no prices available yet — idle")
        return

    bar_date = max((d for d, _ in today.values()), default=None)
    is_new_trading_day = bar_date is not None and bar_date not in state["curve"]

    for sym, (d, c) in today.items():
        state["last_close"][sym] = c
        state["peak_close"][sym] = max(state["peak_close"].get(sym, c), c)

    # One-time inception buy: as soon as we have a close for every symbol.
    if not state["inception_done"] and all(s in state["last_close"] for s in SYMBOLS):
        for sym in SYMBOLS:
            c = state["last_close"][sym]
            tranche_usd = round(INITIAL_DEPLOY_FRACTION * STARTING_CASH, 2)
            qty = tranche_usd / c
            state["holdings"][sym] = {"qty": qty, "cost_basis_usd": tranche_usd}
            state["cash"] = round(state["cash"] - tranche_usd, 2)
            _append_ledger({"event": "inception_buy", "date": bar_date, "sym": sym,
                            "close": c, "tranche_usd": tranche_usd, "qty": qty})
        state["inception_done"] = True
        state["inception_date"] = bar_date
        print(f"[dip_buyer] inception buy complete on {bar_date}: "
              f"{INITIAL_DEPLOY_FRACTION*100:.0f}% each of {SYMBOLS}, "
              f"${state['cash']:.2f} cash reserve left for dips")

    # Trading-day bump + dip-buy pass — evaluated once per NEW trading day,
    # never re-run on same-day repeat ticks within the 6h cadence.
    if is_new_trading_day:
        state["trading_day_index"] += 1
        if state["inception_done"]:
            for sym in SYMBOLS:
                _maybe_dip_buy(sym, state, bar_date)

    # Mark NAV (base 100, matches every other catalog pilot's convention).
    if bar_date is not None and state["inception_done"]:
        ledger_value = state["cash"] + sum(
            h["qty"] * state["last_close"].get(sym, 0.0)
            for sym, h in state["holdings"].items()
        )
        state["curve"][bar_date] = round(100.0 * ledger_value / STARTING_CASH, 4)

    state["updated_ts"] = int(time.time())
    _atomic_write_json(STATE_PATH, state)
    _mirror_curve_to_shared_store(state)

    holdings_desc = ", ".join(
        f"{s}:{state['holdings'][s]['qty']:.3f}sh" for s in state["holdings"]
    ) or "none yet"
    nav_now = state["curve"].get(bar_date, "—")
    log_tick(LANE, f"cash=${state['cash']:.2f} holdings=[{holdings_desc}] "
                   f"trading_day={state['trading_day_index']} nav={nav_now}")


def tick() -> None:
    try:
        _run()
    except Exception as e:
        # Fail-soft, always — a bad tick must never break the scanner's
        # worker thread or the launchd chain that calls this directly.
        print(f"[dip_buyer] fail-soft: unhandled error: {e}")
        log_tick(LANE, f"error: {e!r}")


if __name__ == "__main__":
    tick()
