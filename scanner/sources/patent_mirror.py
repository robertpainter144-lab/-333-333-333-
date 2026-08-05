"""Patent Cliff → Alpaca Paper Mirror.

Reads the latest portfolio_picks_*.csv from ~/patent_cliff/output/,
takes the top-3 picks flagged us_listed_alpaca_tradable=True, and buys $2
of each on Alpaca paper. Tracks P/L over time via state/patent_mirror_state.json.

Exits are rule-based: +15% target, -10% stop, or 60-trading-day time exit,
checked every tick. Closes append to state/patent_closed_trades.csv so the
promotion gate (edge_ledger, n>=30 closed trades) has something to count —
a mirror that only ever buys can never prove or disprove itself.

Every 30 min. Only adds new picks — doesn't churn.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

PICKS_DIR = Path("/Users/quarki3/patent_cliff/output")
STATE = STATEDIR / "patent_mirror_state.json"
LEDGER = STATEDIR / "patent_mirror_ledger.jsonl"
CLOSED_TRADES = STATEDIR / "patent_closed_trades.csv"
CLOSED_TRADES_FIELDS = ["ts_open", "ts_close", "symbol", "entry", "exit", "pnl_pct", "exit_reason"]

PER_PICK_USD = 2.00
MAX_PICKS = 3

TARGET_PCT = 0.15                    # +15% take-profit
STOP_PCT = -0.10                     # -10% stop-loss
TIME_EXIT_TRADING_DAYS = 60

# Buys only — a stop-loss must never be delayed by a cooldown meant to stop
# runaway re-entries. See get_positions() docstring for the incident this
# cooldown is a second layer of defense against.
ORDER_COOLDOWN_SEC = 24 * 60 * 60

ET = ZoneInfo("America/New_York")
BENCHMARK_SYMBOL = "XPH"             # sector ETF: pharma/biotech, for edge-vs-beta comparison

ALPACA_BASE   = "https://paper-api.alpaca.markets"
ALPACA_KEY    = os.environ.get("ALPACA_PAPER_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_PAPER_SECRET", "")


def _headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET,
            "Content-Type": "application/json"}


def market_is_open(now_ts: float | None = None) -> bool:
    """Regular US equity session: Mon-Fri 09:30-16:00 ET. No holiday calendar —
    a rejected/queued order on a market holiday is a harmless no-op, not a risk."""
    dt = datetime.fromtimestamp(now_ts if now_ts is not None else time.time(), tz=ET)
    if dt.weekday() >= 5:
        return False
    open_t = dt.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = dt.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= dt <= close_t


def _trading_days_between(ts_open: int, ts_now: int) -> int:
    d0 = datetime.fromtimestamp(ts_open, tz=ET).date()
    d1 = datetime.fromtimestamp(ts_now, tz=ET).date()
    days = 0
    d = d0
    while d < d1:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def top_picks() -> list[str]:
    files = sorted(PICKS_DIR.glob("portfolio_picks_*.csv"))
    if not files:
        return []
    try:
        with files[-1].open() as f:
            rdr = csv.DictReader(f)
            rows = list(rdr)
    except Exception:
        return []
    tradable = [r["ticker"] for r in rows
                if r.get("us_listed_alpaca_tradable") == "True"]
    return tradable[:MAX_PICKS]


def load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def get_positions(target_syms: set[str]) -> dict[str, dict] | None:
    """Return positions, or None on ANY fetch failure.

    FAIL CLOSED: returning {} on an API error made tick() think it held
    nothing and fire $2 buys per pick every 30 min — 243 runaway orders /
    $486 attempted over 2026-07-10..15, 174 of them queued on a weekend.
    A failed read must skip the cycle, never look like an empty book."""
    if not ALPACA_KEY:
        return None
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=_headers(), timeout=10)
        if not r.ok:
            return None
        out = {}
        for p in r.json():
            sym = p["symbol"]
            if sym not in target_syms:
                continue
            if p.get("asset_class") != "us_equity":
                continue
            out[sym] = {
                "qty": float(p.get("qty", 0)),
                "mv":  float(p.get("market_value", 0)),
                "avg_entry": float(p.get("avg_entry_price", 0)),
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
            }
        return out
    except Exception:
        return None


def submit_buy(sym: str, notional: float) -> dict:
    if not ALPACA_KEY:
        return {"skipped": True}
    try:
        r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=_headers(),
                          json={"symbol": sym, "side": "buy", "type": "market",
                                "time_in_force": "day", "notional": round(notional, 2)},
                          timeout=12)
        if not r.ok:
            return {"error": f"HTTP {r.status_code}: {r.text[:150]}"}
        return r.json()
    except Exception as e:
        return {"error": repr(e)}


def submit_sell(sym: str, qty: float) -> dict:
    if not ALPACA_KEY:
        return {"skipped": True}
    try:
        r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=_headers(),
                          json={"symbol": sym, "side": "sell", "type": "market",
                                "time_in_force": "day", "qty": str(round(qty, 6))},
                          timeout=12)
        if not r.ok:
            return {"error": f"HTTP {r.status_code}: {r.text[:150]}"}
        return r.json()
    except Exception as e:
        return {"error": repr(e)}


def _append_closed_trade(row: dict) -> None:
    STATEDIR.mkdir(exist_ok=True)
    is_new = not CLOSED_TRADES.exists()
    with CLOSED_TRADES.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CLOSED_TRADES_FIELDS)
        if is_new:
            w.writeheader()
        w.writerow(row)


def _fetch_benchmark_pct(since_ts: int) -> float | None:
    """XPH close-to-close % return since `since_ts` — lets the governor/edge_ledger
    compare the mirror against the pharma/biotech sector, not just other arms."""
    try:
        import yfinance as yf
        start = datetime.fromtimestamp(since_ts, tz=timezone.utc).date()
        hist = yf.Ticker(BENCHMARK_SYMBOL).history(start=str(start))
        if hist.empty:
            return None
        first_close = float(hist["Close"].iloc[0])
        last_close = float(hist["Close"].iloc[-1])
        if first_close <= 0:
            return None
        return round((last_close - first_close) / first_close * 100, 3)
    except Exception:
        return None


def tick() -> None:
    now = int(time.time())
    state = load_state()
    open_positions: dict[str, int] = dict(state.get("open_positions") or {})
    last_buy_ts: dict[str, int] = dict(state.get("last_buy_ts") or {})
    book_start_ts = state.get("book_start_ts")

    picks = top_picks()
    # Track anything we still hold locally too, so an exit still fires for a
    # symbol that has rolled off the current top-3 picks list on a rescan.
    target_syms = set(picks) | set(open_positions.keys())
    if not target_syms:
        log_tick("patent_mirror", "no picks")
        return

    positions = get_positions(target_syms)
    if positions is None:
        # Positions fetch failed — never treat as "empty book" (runaway-buy bug).
        log_tick("patent_mirror", "[skip] positions fetch failed — no orders this cycle")
        return

    is_open = market_is_open(now)
    actions = []
    closed_this_tick: set[str] = set()

    # ── Exits: target / stop / time. Conditions are evaluated even when the
    # market is closed (so a hit gets logged), but orders only fire in-session.
    for sym, pos in positions.items():
        qty, avg_entry = pos["qty"], pos["avg_entry"]
        if qty <= 0 or avg_entry <= 0:
            continue
        plpc = (pos["mv"] / qty - avg_entry) / avg_entry
        ts_open = open_positions.get(sym)
        if ts_open is None:
            # Position exists on Alpaca with no locally-tracked entry (e.g. held
            # before this exit logic shipped) — start the clock now rather than
            # never evaluating a time exit for it.
            ts_open = now
            open_positions[sym] = ts_open
        days_held = _trading_days_between(ts_open, now)

        reason = None
        if plpc >= TARGET_PCT:
            reason = "target"
        elif plpc <= STOP_PCT:
            reason = "stop"
        elif days_held >= TIME_EXIT_TRADING_DAYS:
            reason = "time"
        if not reason:
            continue
        if not is_open:
            log_tick("patent_mirror", f"[hold] {sym} hit {reason} exit but market closed — deferring")
            continue

        res = submit_sell(sym, qty)
        ok = "id" in res
        actions.append({"sym": sym, "side": "SELL", "reason": reason,
                         "result": ("ok:" + res["id"][:16]) if ok else str(res.get("error", "skipped"))[:80]})
        if ok:
            exit_price = avg_entry * (1 + plpc)
            _append_closed_trade({
                "ts_open": ts_open, "ts_close": now, "symbol": sym,
                "entry": round(avg_entry, 4), "exit": round(exit_price, 4),
                "pnl_pct": round(plpc * 100, 3), "exit_reason": reason,
            })
            closed_this_tick.add(sym)
            open_positions.pop(sym, None)

    # ── Entries: only current top picks, only in-session, only outside the
    # per-symbol buy cooldown.
    if is_open:
        for sym in picks:
            if sym in closed_this_tick:
                continue  # just exited this tick — don't immediately re-buy
            if now - last_buy_ts.get(sym, 0) < ORDER_COOLDOWN_SEC:
                continue
            cur = positions.get(sym, {}).get("mv", 0.0)
            # If we already hold something on this ticker (from this mirror or others),
            # only add if we're clearly below target. Idempotent — no churn.
            if cur < PER_PICK_USD * 0.5:
                res = submit_buy(sym, PER_PICK_USD)
                ok = "id" in res
                actions.append({"sym": sym, "side": "BUY", "notional": PER_PICK_USD,
                                 "result": ("ok:" + res["id"][:16]) if ok else str(res.get("error", "skipped"))[:80]})
                if ok:
                    last_buy_ts[sym] = now
                    open_positions.setdefault(sym, now)
                    if book_start_ts is None:
                        book_start_ts = now
    else:
        log_tick("patent_mirror", "[skip] market closed — no new entries this cycle")

    # Compute pnl% against the amount actually invested (pre-order snapshot;
    # next tick's fetch reflects any fills placed just above).
    invested = sum(p["mv"] for p in positions.values())
    total_unreal = sum(p["unrealized_pl"] for p in positions.values())
    pnl_pct = round(total_unreal / max(invested, 1) * 100, 3) if invested else 0.0
    if book_start_ts is None and open_positions:
        # No BUY has fired since this field was introduced, but positions already
        # exist (e.g. carried over from before this code shipped) — anchor the
        # benchmark window to the earliest tracked entry instead of staying null.
        book_start_ts = min(open_positions.values())
    benchmark_xph_pct = _fetch_benchmark_pct(book_start_ts) if book_start_ts else None

    STATE.write_text(json.dumps({
        "ts":       now,
        "picks":    picks,
        "positions": positions,
        "open_positions": open_positions,
        "last_buy_ts": last_buy_ts,
        "book_start_ts": book_start_ts,
        "invested":  round(invested, 2),
        "unrealized": round(total_unreal, 2),
        "pnl_pct":   pnl_pct,
        "benchmark_xph_pct": benchmark_xph_pct,
        "market_open": is_open,
        "last_actions": actions,
    }, indent=2))
    if actions:
        with LEDGER.open("a") as f:
            for a in actions:
                f.write(json.dumps({"ts": now, **a}) + "\n")
    bench_str = f"{benchmark_xph_pct:+.2f}%" if benchmark_xph_pct is not None else "n/a"
    emit_live("patent_mirror",
              f"[patent] {len(positions)} held  invested=${invested:.2f}  "
              f"pnl_pct={pnl_pct:+.2f}%  vs XPH={bench_str}  {len(actions)} orders")
    log_tick("patent_mirror", f"held={len(positions)} pnl_pct={pnl_pct:+.2f}% vs_xph={bench_str}")


if __name__ == "__main__":
    tick()
