"""
Close every IBKR position at market and cancel every open order.
One-shot exit script: user wants to leave the account.

Usage:
    python3.10 close_all.py --dry-run     # show what it would do
    python3.10 close_all.py --confirm     # actually place sells

Refuses to sell any symbol bought today (day-trade guard). Everything else
gets a plain MarketOrder(SELL, qty) with outsideRth on so it fills whether
we're in RTH or not.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ib_insync import IB, MarketOrder

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from main import _log_txn, _now  # reuse the shared txn logger
from day_trade_guard import has_buy_today

HOST      = "127.0.0.1"
PORT      = 4001
CLIENT_ID = 88   # unused elsewhere (main=23, watcher=29)
TIMEOUT   = 15


def connect() -> IB:
    ib = IB()
    ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=TIMEOUT)
    return ib


def cancel_all_open_orders(ib: IB) -> int:
    ib.reqAllOpenOrders()
    ib.sleep(1.5)
    open_trades = [t for t in ib.openTrades()
                   if t.orderStatus.status not in ("Filled", "Cancelled", "Inactive")]
    n = len(open_trades)
    if n == 0:
        print("no open orders to cancel")
        return 0
    print(f"issuing reqGlobalCancel for {n} open order(s)")
    ib.reqGlobalCancel()
    ib.sleep(3.0)
    still_open = [t for t in ib.openTrades()
                  if t.orderStatus.status not in ("Filled", "Cancelled", "Inactive")]
    print(f"after global cancel: {len(still_open)} still open")
    for t in still_open:
        print(f"  stuck: id={t.order.orderId} {t.order.action} {t.contract.symbol} "
              f"status={t.orderStatus.status}")
    return n - len(still_open)


def close_all(ib: IB, confirm: bool) -> None:
    acct = ib.managedAccounts()[0]
    positions = ib.positions(acct)
    if not positions:
        print(f"account {acct} has no positions — nothing to close")
        return

    print(f"\naccount {acct}: {len(positions)} position(s)")
    print(f"{'sym':<8}{'qty':>6}  {'avg_cost':>10}  exch")
    for p in positions:
        print(f"{p.contract.symbol:<8}{int(p.position):>6}  "
              f"{p.avgCost:>10.4f}  {p.contract.primaryExchange or p.contract.exchange}")

    blocked = []
    for p in positions:
        if has_buy_today(ib, p.contract.symbol):
            blocked.append(p.contract.symbol)
    if blocked:
        print(f"\nDAY-TRADE GUARD: bought today, will NOT sell: {blocked}")

    to_close = [p for p in positions if p.contract.symbol not in blocked]
    if not to_close:
        print("nothing left to close after guard filter")
        return

    if not confirm:
        print(f"\nDRY RUN — would place {len(to_close)} MarketOrder(SELL) at market.")
        print("Re-run with --confirm to fire.")
        return

    print(f"\nplacing {len(to_close)} market sells...")
    trades = []
    for p in to_close:
        # Force SMART routing — position contracts inherit direct exchange
        # (NASDAQ/ARCA), which IB Gateway's precautionary settings block (10311).
        contract = p.contract
        contract.exchange = "SMART"
        contract.primaryExchange = p.contract.primaryExchange or p.contract.exchange
        order = MarketOrder("SELL", totalQuantity=abs(int(p.position)))
        order.outsideRth = True
        order.tif = "DAY"
        order.transmit = True
        trade = ib.placeOrder(contract, order)
        trades.append((p, trade))
        print(f"  placed SELL {int(p.position)} {contract.symbol} "
              f"orderId={trade.order.orderId}")
        ib.sleep(0.4)

    print("\nwaiting up to 30s for fills...")
    deadline = time.time() + 30
    while time.time() < deadline:
        ib.sleep(1.0)
        pending = [t for _, t in trades
                   if t.orderStatus.status not in ("Filled", "Cancelled", "Inactive")]
        if not pending:
            break

    print("\nfinal per-order status:")
    for p, t in trades:
        st = t.orderStatus
        print(f"  {p.contract.symbol:<6} orderId={t.order.orderId} "
              f"status={st.status} filled={st.filled}/{t.order.totalQuantity} "
              f"avg_fill=${st.avgFillPrice:.4f}")
        _log_txn({
            "ts": _now(),
            "action": "SELL",
            "symbol": p.contract.symbol,
            "cashQty": 0,
            "totalQuantity": int(p.position),
            "limitPrice": 0,
            "status": st.status,
            "orderId": t.order.orderId,
            "permId": getattr(t.order, "permId", 0),
            "filled": float(st.filled or 0),
            "avgFillPrice": float(st.avgFillPrice or 0),
            "reason": "exit_account",
        })

    ib.sleep(2.0)
    remaining = ib.positions(acct)
    print(f"\nremaining positions after close: {len(remaining)}")
    for r in remaining:
        print(f"  {r.contract.symbol} qty={r.position} avg_cost={r.avgCost}")

    summary = {s.tag: s.value for s in ib.accountSummary(acct)
               if s.tag in ("NetLiquidation", "TotalCashValue", "AvailableFunds")}
    print(f"\naccount summary: equity=${summary.get('NetLiquidation')} "
          f"cash=${summary.get('TotalCashValue')} "
          f"available=${summary.get('AvailableFunds')}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true",
                    help="actually place sell orders (default: dry-run)")
    ap.add_argument("--skip-cancel", action="store_true",
                    help="skip the reqGlobalCancel step")
    args = ap.parse_args()

    ib = connect()
    try:
        if not args.skip_cancel:
            cancel_all_open_orders(ib)
        close_all(ib, confirm=args.confirm)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
