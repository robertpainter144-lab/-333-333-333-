"""
IBKR adaptive stop manager.

When a long position is held, attaches a server-side stop bracket so protection
is enforced by IBKR even if this bot is offline. Three layers, computed from
per-symbol ATR + market regime (VIX, SPY trend, vol burst):

    1. Native TRAIL  (SELL) — adaptive trail % from BASE_K × ATR × VIX × SPY × burst,
                              floored at TRAIL_FLOOR_PCT, capped at _trail_cap(atr_pct)
                              (never tighter than TRAIL_CAP_ATR_MULT × ATR%).
    2. Native STOP   (SELL) — entry × (1 − HARD_DROP_PCT). Floor protection.
    3. OCA group     — either fill cancels the other.

The watcher (separate process) layers on profit-step + time-stop logic.
"""
from __future__ import annotations

import json
import sys
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ib_insync import IB, Stock, StopOrder, Order

try:
    import notify
except Exception:
    notify = None

try:
    import arms as ARMS
except Exception:
    ARMS = None

# Reuse the equity-side adaptive_stops module so trail logic stays consistent.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "qc_portfolio"))
try:
    import adaptive_stops as A
    ADAPTIVE_OK = True
except Exception as e:
    print(f"[stop_manager] adaptive_stops import failed: {e}", file=sys.stderr)
    ADAPTIVE_OK = False

ROOT = Path(__file__).resolve().parent
ATTACH_STATE = ROOT / "stop_state.json"

# Static safety floors (independent of adaptive scaling)
HARD_DROP_PCT_FLOOR = 0.05    # 5% hard stop floor (never wider on equities)
HARD_DROP_PCT_CAP   = 0.10    # 10% hard stop cap
TRAIL_FLOOR_PCT     = 0.020   # 2% min trail
TRAIL_CAP_ATR_MULT  = 3.0     # trail cap never tighter than 3.0 × the symbol's ATR% (whipsaw guard)
TRAIL_CAP_MIN_PCT   = 0.10    # trail cap floor
TRAIL_CAP_ABS_MAX   = 0.20    # trail cap absolute ceiling


def _trail_cap(atr_pct: float) -> float:
    return min(TRAIL_CAP_ABS_MAX, max(TRAIL_CAP_MIN_PCT, TRAIL_CAP_ATR_MULT * atr_pct))


# ── Data ──────────────────────────────────────────────────────────────

@dataclass
class StopBracket:
    symbol: str
    qty: float
    entry_price: float
    trail_pct: float       # 0–1
    hard_stop_price: float
    oca_group: str
    trail_order_id: int
    stop_order_id: int
    attached_at: str
    components: dict       # how the trail was computed (atr, vix mult, etc.)
    entry_date: str
    last_modified: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state() -> dict:
    if ATTACH_STATE.exists():
        return json.loads(ATTACH_STATE.read_text())
    return {"brackets": {}}   # symbol -> StopBracket dict


def _save_state(s: dict) -> None:
    ATTACH_STATE.write_text(json.dumps(s, indent=2, default=str))


# ── Adaptive parameter selection ──────────────────────────────────────

CATALYSTS_FILE = Path(__file__).resolve().parent / "catalysts.json"
CATALYST_LOOKAHEAD_DAYS = 30      # only apply widening if catalyst is within this window


def _catalyst_widening(symbol: str) -> tuple[float, dict]:
    """If `symbol` has an upcoming catalyst inside CATALYST_LOOKAHEAD_DAYS,
    return (multiplier, meta). Otherwise (1.0, {})."""
    if not CATALYSTS_FILE.exists():
        return 1.0, {}
    try:
        catalogs = json.loads(CATALYSTS_FILE.read_text())
    except Exception:
        return 1.0, {}
    entry = catalogs.get(symbol)
    if not entry or symbol.startswith("_"):
        return 1.0, {}
    try:
        cat_date = datetime.fromisoformat(entry["catalyst_date"]).date()
    except Exception:
        return 1.0, {}
    today = datetime.now(timezone.utc).date()
    days_out = (cat_date - today).days
    if days_out < 0 or days_out > CATALYST_LOOKAHEAD_DAYS:
        return 1.0, {}
    mult = float(entry.get("catalyst_mult", 1.5))
    return mult, {
        "catalyst_type": entry.get("catalyst_type"),
        "catalyst_date": entry["catalyst_date"],
        "days_to_catalyst": days_out,
        "catalyst_mult": mult,
    }


def compute_trail_pct(symbol: str) -> tuple[float, dict]:
    """Returns (trail_pct, components_dict)."""
    if not ADAPTIVE_OK:
        base = 0.04
        cat_mult, cat_meta = _catalyst_widening(symbol)
        return base * cat_mult, {"fallback": "adaptive_stops unavailable",
                                  "trail": round(base * cat_mult, 5), **cat_meta}
    ctx = A.get_market_context()
    if ctx is None:
        base = 0.04
        cat_mult, cat_meta = _catalyst_widening(symbol)
        return base * cat_mult, {"fallback": "no market ctx",
                                  "trail": round(base * cat_mult, 5), **cat_meta}
    dec = A.effective_trail(symbol, ctx, fallback_pct=0.04)
    # Apply catalyst widening BEFORE capping — a widened trail can exceed the
    # normal CAP by design, so use a proportional cap when a catalyst is live.
    cat_mult, cat_meta = _catalyst_widening(symbol)
    widened = dec.trail_pct * cat_mult
    effective_cap = _trail_cap(dec.atr_pct) * cat_mult
    trail = max(TRAIL_FLOOR_PCT, min(effective_cap, widened))
    comp = {
        "atr_pct":     round(dec.atr_pct, 5),
        "vix":         round(ctx.vix, 2),
        "vix_mult":    dec.vix_mult,
        "spy_trend":   "up" if ctx.spy_above_200sma else "down",
        "spy_mult":    dec.spy_mult,
        "burst_mult":  dec.burst_mult,
        "raw_trail":   round(dec.trail_pct, 5),
        "trail":       round(trail, 5),
        **cat_meta,
    }
    return trail, comp


def compute_hard_drop(atr_pct: float, hard_cap: float = HARD_DROP_PCT_CAP,
                      symbol: str | None = None) -> float:
    """Hard-stop = min(cap, max(FLOOR, 2 × ATR%)) — widened when `symbol` has
    an upcoming catalyst."""
    raw = 2.0 * atr_pct if atr_pct > 0 else HARD_DROP_PCT_FLOOR
    hard = max(HARD_DROP_PCT_FLOOR, min(hard_cap, raw))
    if symbol:
        cat_mult, _ = _catalyst_widening(symbol)
        # Cap widening at 2× floor and 2× cap to avoid runaway
        hard = max(HARD_DROP_PCT_FLOOR, min(hard_cap * 2.0, hard * cat_mult))
    return hard


# ── Order placement ───────────────────────────────────────────────────

def _market_price(ib: IB, contract) -> Optional[float]:
    """Best-effort current price via IBKR delayed feed → yfinance fallback."""
    try:
        ib.reqMarketDataType(3)
        t = ib.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
        for _ in range(15):
            ib.sleep(0.4)
            for fld in (t.last, t.close, t.bid, t.ask):
                try:
                    v = float(fld)
                    if v > 0:
                        ib.cancelMktData(contract)
                        return v
                except Exception:
                    pass
        ib.cancelMktData(contract)
    except Exception:
        pass
    try:
        import yfinance as yf
        h = yf.Ticker(contract.symbol).history(period="1d", interval="5m")
        if not h.empty:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return None


def _qualify(ib: IB, symbol: str, primary_exch: str = "SMART") -> Stock:
    # Try common exchanges if primary_exch is unknown
    candidates = [primary_exch] if primary_exch != "SMART" else ["NYSE", "ARCA", "NASDAQ"]
    for ex in candidates:
        c = Stock(symbol, "SMART", "USD", primaryExchange=ex)
        q = ib.qualifyContracts(c)
        if q:
            return q[0]
    # Fall back to no primaryExchange (IBKR will pick if unambiguous)
    c = Stock(symbol, "SMART", "USD")
    q = ib.qualifyContracts(c)
    if q:
        return q[0]
    raise RuntimeError(f"could not qualify contract for {symbol}")


def attach_stops(ib: IB, symbol: str, qty: float, entry_price: float,
                 primary_exch: str = "SMART",
                 min_hard_stop_price: Optional[float] = None) -> StopBracket:
    """Attach native TRAIL + STOP in OCA. Returns the bracket record.

    `min_hard_stop_price`, when given, floors the hard stop at that price —
    used after a profit-step to enforce a true break-even floor (the hard
    stop must never sit below the original entry)."""
    contract = _qualify(ib, symbol, primary_exch)

    # 1. Compute adaptive trail %
    trail_pct, components = compute_trail_pct(symbol)
    atr_pct = components.get("atr_pct", 0.0)

    # Per-arm override
    arm = ARMS.get(symbol) if ARMS is not None else None
    if arm is not None:
        # Looser-arm trails get a wider cap so trail_mult > 1 isn't silently clipped
        trail_cap = _trail_cap(atr_pct) * max(1.0, arm.trail_mult)
        trail_pct = max(TRAIL_FLOOR_PCT, min(trail_cap, trail_pct * arm.trail_mult))
        hard_cap = arm.hard_cap_pct
        components["arm"] = arm.label
        components["trail_mult"] = arm.trail_mult
        components["trail"] = round(trail_pct, 5)
    else:
        hard_cap = HARD_DROP_PCT_CAP

    # 2. Hard stop price (catalyst-aware if this symbol has a dated event)
    hard_drop = compute_hard_drop(atr_pct, hard_cap, symbol=symbol)
    hard_stop_price = round(entry_price * (1.0 - hard_drop), 2)
    if min_hard_stop_price is not None:
        hard_stop_price = max(hard_stop_price, round(min_hard_stop_price, 2))

    # 2b. Underwater guard — refuse to place a hard stop at/above current market.
    # On 2026-06-22, watcher placed PINS hard stop at $19.69 while market was
    # already $19.50; the stop fired immediately for an instant market exit
    # disguised as a stop trigger. Catch that here before it happens again.
    current_price = _market_price(ib, contract)
    UNDERWATER_BUFFER = 0.01  # require at least 1% headroom below market
    skip_hard_stop = False
    if current_price is not None and hard_stop_price >= current_price * (1.0 - UNDERWATER_BUFFER):
        skip_hard_stop = True
        components["hard_stop_skipped"] = True
        components["hard_stop_skip_reason"] = (
            f"market ${current_price:.2f} too close to hard_stop ${hard_stop_price:.2f}"
        )
        components["market_at_attach"] = round(current_price, 4)
        print(f"[stops] {symbol}: UNDERWATER — market ${current_price:.2f} ≤ "
              f"hard_stop ${hard_stop_price:.2f} × 1.01; placing TRAIL only", file=sys.stderr)
    elif current_price is not None:
        components["market_at_attach"] = round(current_price, 4)

    # 3. OCA group name (unique per attach)
    oca = f"ibkr_bot_{symbol}_{uuid.uuid4().hex[:8]}"

    # 4. TRAIL SELL order (server-side trailing)
    trail = Order()
    trail.action          = "SELL"
    trail.orderType       = "TRAIL"
    trail.totalQuantity   = float(qty)
    trail.trailingPercent = round(trail_pct * 100.0, 3)  # IBKR wants 0-100, not 0-1
    trail.tif             = "GTC"
    trail.outsideRth      = False
    trail.ocaGroup        = oca
    trail.ocaType         = 1   # 1 = CANCEL_WITH_BLOCK (canonical for OCA)
    trail.transmit        = skip_hard_stop  # transmit immediately if trail-only

    if not skip_hard_stop:
        # 5. STOP SELL order (hard floor)
        stop = StopOrder("SELL", float(qty), hard_stop_price)
        stop.tif         = "GTC"
        stop.outsideRth  = False
        stop.ocaGroup    = oca
        stop.ocaType     = 1
        stop.transmit    = True   # transmit both when this one lands

        trail_trade = ib.placeOrder(contract, trail)
        stop_trade  = ib.placeOrder(contract, stop)
        stop_order_id = stop_trade.order.orderId
    else:
        trail_trade = ib.placeOrder(contract, trail)
        stop_order_id = -1  # sentinel: no hard stop placed
    ib.sleep(2.0)

    bracket = StopBracket(
        symbol           = symbol,
        qty              = float(qty),
        entry_price      = float(entry_price),
        trail_pct        = float(trail_pct),
        hard_stop_price  = float(hard_stop_price),
        oca_group        = oca,
        trail_order_id   = trail_trade.order.orderId,
        stop_order_id    = stop_order_id,
        attached_at      = _now(),
        components       = components,
        entry_date       = _now(),
        last_modified    = _now(),
    )

    state = _load_state()
    state["brackets"][symbol] = asdict(bracket)
    _save_state(state)

    if skip_hard_stop:
        print(f"[stops] {symbol}: TRAIL {trail_pct*100:.2f}% (id={bracket.trail_order_id})  "
              f"STOP skipped (would have been ${hard_stop_price}, market ${current_price:.2f})  "
              f"OCA={oca}")
    else:
        print(f"[stops] {symbol}: TRAIL {trail_pct*100:.2f}% (id={bracket.trail_order_id})  "
              f"STOP ${hard_stop_price} (id={bracket.stop_order_id})  "
              f"OCA={oca}")

    if notify is not None:
        try:
            notify.notify_stop_attached(symbol=symbol, trail_pct=trail_pct,
                                        hard_stop=hard_stop_price, entry_price=entry_price)
        except Exception as e:
            print(f"[notify] stop_attached err: {e}", file=sys.stderr)

    return bracket


def cancel_bracket(ib: IB, symbol: str) -> None:
    state = _load_state()
    br = state["brackets"].get(symbol)
    if not br:
        print(f"[stops] no bracket on file for {symbol}")
        return
    for trade in ib.openTrades():
        if trade.order.orderId in (br["trail_order_id"], br["stop_order_id"]):
            ib.cancelOrder(trade.order)
    ib.sleep(1)
    state["brackets"].pop(symbol, None)
    _save_state(state)
    print(f"[stops] cancelled bracket for {symbol}")


def modify_trail(ib: IB, symbol: str, new_trail_pct: float) -> None:
    """Adjust an existing TRAIL order's trailingPercent."""
    state = _load_state()
    br = state["brackets"].get(symbol)
    if not br:
        print(f"[stops] no bracket for {symbol}")
        return
    for trade in ib.openTrades():
        if trade.order.orderId == br["trail_order_id"]:
            trade.order.trailingPercent = round(new_trail_pct * 100.0, 3)
            ib.placeOrder(trade.contract, trade.order)
            br["trail_pct"]     = new_trail_pct
            br["last_modified"] = _now()
            state["brackets"][symbol] = br
            _save_state(state)
            print(f"[stops] {symbol}: trail → {new_trail_pct*100:.2f}%")
            return
    print(f"[stops] trail order for {symbol} not found among open trades")


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["attach", "cancel", "modify", "list"])
    p.add_argument("--symbol", help="ticker (required for attach/cancel/modify)")
    p.add_argument("--qty",    type=float, default=1.0)
    p.add_argument("--entry",  type=float, help="entry price (required for attach)")
    p.add_argument("--exch",   default="SMART", help="primary exchange (NYSE/ARCA/NASDAQ/SMART)")
    p.add_argument("--trail",  type=float, help="new trail % (0-100), required for modify")
    args = p.parse_args()

    ib = IB()
    ib.connect("127.0.0.1", 4001, clientId=27, timeout=10)
    print(f"connected: {ib.managedAccounts()}")

    try:
        if args.action == "attach":
            assert args.symbol and args.entry, "--symbol and --entry required"
            attach_stops(ib, args.symbol, args.qty, args.entry, args.exch)
        elif args.action == "cancel":
            assert args.symbol, "--symbol required"
            cancel_bracket(ib, args.symbol)
        elif args.action == "modify":
            assert args.symbol and args.trail is not None, "--symbol and --trail required"
            modify_trail(ib, args.symbol, args.trail / 100.0)
        elif args.action == "list":
            state = _load_state()
            print(json.dumps(state, indent=2, default=str))
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
