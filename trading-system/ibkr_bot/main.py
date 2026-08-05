"""
IBKR $10 starter bot — connects to IB Gateway, places one fractional-cash order.

Usage:
    python3.10 main.py                       # default: $10 of SPY, outsideRth, GTC
    python3.10 main.py --symbol QQQ --cash 10
    python3.10 main.py --status              # just print account + positions, no order

Designed to be fired by launchd / cron at market open, or run interactively.
State + transactions logged under ~/ibkr_bot/.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from ib_insync import IB, Stock, MarketOrder, LimitOrder

try:
    import notify
except Exception:
    notify = None

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state.json"
TXNS  = ROOT / "transactions.csv"

# ── Defaults ──────────────────────────────────────────────────────────
HOST       = os.environ.get("IBKR_GATEWAY_HOST", "127.0.0.1")
PORT       = int(os.environ.get("IBKR_GATEWAY_PORT", "4001"))  # 4001 live, 4002 paper
CLIENT_ID  = 23
TIMEOUT    = 15

DEFAULT_SYMBOL = "F"      # Ford ~$14 — cheapest liquid name that doesn't need fractional
DEFAULT_QTY    = 1
PRIMARY_EXCH   = "NYSE"   # F is NYSE; SPY/QQQ/IWM would be "ARCA"

# Safety: never queue a single order whose notional exceeds this without --force-large
MAX_ORDER_USD  = 50.0

# D5: total (aggregate) IBKR exposure above this requires push approval BEFORE firing.
# Hardcoded here (not only in notify) so the gate holds even if notify fails to import.
AGGREGATE_CEILING_USD = 20.0


class ApprovalRequired(Exception):
    """Raised when an order would push aggregate IBKR exposure past the D5 ceiling
    and no explicit approval was given. The order is NOT placed."""

# Commission floor: IBKR charges ~1% per side on micro orders, so a
# sub-$150 position cannot clear round-trip friction. Refuse below this
# unless --force-small is passed.
MIN_POSITION_USD = 150.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_state(d: dict) -> None:
    STATE.write_text(json.dumps(d, indent=2, default=str))


def _load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"orders": [], "last_run": None}


def _log_txn(row: dict) -> None:
    new = not TXNS.exists()
    cols = ["ts", "action", "symbol", "cashQty", "totalQuantity", "limitPrice",
            "status", "orderId", "permId", "filled", "avgFillPrice", "reason"]
    with TXNS.open("a") as f:
        if new:
            f.write(",".join(cols) + "\n")
        f.write(",".join(str(row.get(c, "")) for c in cols) + "\n")


def connect_ib() -> IB:
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=TIMEOUT)
    return ib


def account_snapshot(ib: IB) -> dict:
    acct = ib.managedAccounts()[0]
    summary = {s.tag: s.value for s in ib.accountSummary(acct)
               if s.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower",
                            "AvailableFunds")}
    positions = [{
        "symbol":  p.contract.symbol,
        "qty":     float(p.position),
        "avgCost": float(p.avgCost),
    } for p in ib.positions(acct)]
    return {
        "account":   acct,
        "equity":    float(summary.get("NetLiquidation", 0)),
        "cash":      float(summary.get("TotalCashValue", 0)),
        "available": float(summary.get("AvailableFunds", 0)),
        "buying_pw": float(summary.get("BuyingPower", 0)),
        "positions": positions,
    }


def fetch_quote(ib: IB, contract) -> tuple[float, float, float]:
    """Return (bid, ask, last). Tries IBKR delayed feed, then yfinance fallback."""
    def _f(x):
        try:
            x = float(x)
            return 0.0 if math.isnan(x) or x <= 0 else x
        except Exception:
            return 0.0

    ib.reqMarketDataType(3)  # 3 = delayed (free, 15min)
    ticker = ib.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
    bid = ask = last = 0.0
    for _ in range(20):
        ib.sleep(0.5)
        bid  = _f(getattr(ticker, "bid",  0))
        ask  = _f(getattr(ticker, "ask",  0))
        last = _f(getattr(ticker, "last", 0)) or _f(getattr(ticker, "close", 0))
        if max(bid, ask, last) > 0:
            ib.cancelMktData(contract)
            return bid, ask, last
    ib.cancelMktData(contract)
    # Fallback: yfinance (we already depend on it elsewhere)
    try:
        import yfinance as yf
        h = yf.Ticker(contract.symbol).history(period="2d", interval="5m")
        if not h.empty:
            px = float(h["Close"].iloc[-1])
            return 0.0, 0.0, px
    except Exception:
        pass
    return 0.0, 0.0, 0.0


def place_share_buy(ib: IB, symbol: str, qty: int, primary_exch: str = PRIMARY_EXCH,
                    outside_rth: bool = True, force_large: bool = False,
                    force_small: bool = False, current_exposure_usd: float = 0.0,
                    approved: bool = False) -> dict:
    contract = Stock(symbol, "SMART", "USD", primaryExchange=primary_exch)
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise RuntimeError(f"could not qualify contract for {symbol}")
    contract = qualified[0]

    bid, ask, last = fetch_quote(ib, contract)
    ref_price = ask or last or bid
    if not ref_price:
        raise RuntimeError(f"no quote for {symbol} (bid={bid} ask={ask} last={last})")

    notional = qty * ref_price
    if notional > MAX_ORDER_USD and not force_large:
        raise ValueError(f"order notional ${notional:.2f} exceeds safety cap ${MAX_ORDER_USD}; "
                         f"pass --force-large to override")
    if notional < MIN_POSITION_USD and not force_small:
        raise ValueError(f"order notional ${notional:.2f} is below the commission floor "
                         f"${MIN_POSITION_USD:.0f}; IBKR's ~1%/side commission cannot be "
                         f"cleared by a position this small; pass --force-small to override")

    # D5: aggregate-exposure ceiling. Firing here would push TOTAL IBKR exposure
    # (existing positions + this order) past the auto-fire ceiling, so it must be
    # approved by push first. Fails closed: if the notification can't be sent, or
    # notify isn't importable, the order is still blocked.
    projected_total = current_exposure_usd + notional
    if projected_total > AGGREGATE_CEILING_USD and not approved:
        sent = False
        if notify is not None:
            try:
                sent = notify.propose_trade(
                    symbol, "BUY", qty, ref_price, projected_total,
                    reason="auto-fire $20 aggregate ceiling (D5)",
                )
            except Exception as e:
                print(f"[notify] propose_trade err: {e}", file=sys.stderr)
        raise ApprovalRequired(
            f"projected total IBKR exposure ${projected_total:.2f} "
            f"(existing ${current_exposure_usd:.2f} + order ${notional:.2f}) exceeds the "
            f"${AGGREGATE_CEILING_USD:.2f} auto-fire ceiling; approval push "
            f"{'sent' if sent else 'FAILED TO SEND'}. Order NOT placed — "
            f"re-run with --approved after you approve it.")

    # Limit slightly above mid; if only last known, pad 0.2%
    if bid and ask:
        mid   = (bid + ask) / 2
        cap   = ask * 1.001
        limit = min(cap, mid + (ask - bid) * 0.75)
    else:
        limit = ref_price * 1.002
    limit = round(limit, 2)

    order = LimitOrder("BUY", totalQuantity=qty, lmtPrice=limit)
    order.outsideRth = bool(outside_rth)
    order.tif        = "GTC"
    order.transmit   = True

    trade = ib.placeOrder(contract, order)
    ib.sleep(2.0)

    info = {
        "ts":            _now(),
        "action":        "BUY",
        "symbol":        symbol,
        "cashQty":       0,
        "totalQuantity": qty,
        "limitPrice":    limit,
        "orderId":       trade.order.orderId,
        "permId":        getattr(trade.order, "permId", 0),
        "status":        trade.orderStatus.status,
        "filled":        float(trade.orderStatus.filled or 0),
        "avgFillPrice":  float(trade.orderStatus.avgFillPrice or 0),
        "reason":        "starter",
        "bid":           bid,
        "ask":           ask,
        "last":          last,
        "outside_rth":   outside_rth,
    }
    _log_txn(info)

    # Pushover notification
    if notify is not None:
        try:
            notify.notify_order_placed(
                symbol=symbol, side="BUY", qty=qty, limit_price=limit,
                order_id=trade.order.orderId, total_exposure_after=qty * limit,
            )
        except Exception as e:
            print(f"[notify] order_placed err: {e}", file=sys.stderr)

    return info


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol",  default=DEFAULT_SYMBOL)
    p.add_argument("--qty",     type=int, default=DEFAULT_QTY)
    p.add_argument("--primary-exch", default=PRIMARY_EXCH, help="ARCA for SPY/QQQ, NYSE for F/T, NASDAQ for AAPL")
    p.add_argument("--status",  action="store_true", help="account state only, no order")
    p.add_argument("--rth-only", action="store_true", help="regular hours only (default: outside-RTH allowed)")
    p.add_argument("--force-large", action="store_true", help="bypass MAX_ORDER_USD safety cap")
    p.add_argument("--force-small", action="store_true", help="bypass MIN_POSITION_USD commission floor")
    p.add_argument("--approved", action="store_true",
                   help="confirm you approved a trade that exceeds the $20 aggregate ceiling (D5)")
    args = p.parse_args()

    print(f"connecting to IB Gateway at {HOST}:{PORT} (clientId={CLIENT_ID})…")
    ib = connect_ib()
    print(f"  serverVersion={ib.client.serverVersion()}  accounts={ib.managedAccounts()}")

    snap = account_snapshot(ib)
    print(f"  account {snap['account']}: NetLiq=${snap['equity']:.2f}  "
          f"Avail=${snap['available']:.2f}  BP=${snap['buying_pw']:.2f}")
    if snap["positions"]:
        for pos in snap["positions"]:
            print(f"    {pos['symbol']:6}  qty={pos['qty']}  avg=${pos['avgCost']:.4f}")
    else:
        print("  positions: none")

    if args.status:
        _save_state({**_load_state(), "last_run": _now(), "snapshot": snap})
        ib.disconnect()
        return

    # Current aggregate exposure = market value of open positions = NetLiq − cash.
    current_exposure = max(0.0, snap["equity"] - snap["cash"])

    print(f"\nplacing BUY {args.qty} share(s) of {args.symbol} "
          f"(outsideRth={not args.rth_only}, GTC)…")
    print(f"  current IBKR exposure ${current_exposure:.2f} / ${AGGREGATE_CEILING_USD:.2f} ceiling")
    try:
        info = place_share_buy(
            ib, args.symbol, args.qty,
            primary_exch=args.primary_exch,
            outside_rth=not args.rth_only,
            force_large=args.force_large,
            force_small=args.force_small,
            current_exposure_usd=current_exposure,
            approved=args.approved,
        )
    except ApprovalRequired as e:
        print(f"\n  BLOCKED (D5): {e}", file=sys.stderr)
        _save_state({**_load_state(), "last_run": _now(), "snapshot": snap,
                     "last_block": {"ts": _now(), "reason": str(e)}})
        ib.disconnect()
        sys.exit(2)
    print(f"  orderId={info['orderId']}  status={info['status']}  "
          f"limit=${info['limitPrice']:.2f}  bid=${info['bid']:.2f}  ask=${info['ask']:.2f}")
    if info["filled"] > 0:
        print(f"  partial/full fill: qty={info['filled']}  avg=${info['avgFillPrice']:.4f}")

    state = _load_state()
    state["orders"].append(info)
    state["last_run"] = _now()
    state["snapshot"] = snap
    _save_state(state)

    ib.disconnect()


if __name__ == "__main__":
    main()
