#!/usr/bin/env python3
"""
Deploy ~all available cash into one broad US-listed ETF via Interactive Brokers
TWS / IB Gateway socket API (ib_insync).

Design goal for a small account (~$500): one diversified equity ETF, minimal
complexity, low ongoing fees (ETF expense ratio), fractional/notional where IBKR
allows — *not* a performance guarantee.

Prerequisites
-------------
1. Install: pip install -r requirements.txt
2. Run **Trader Workstation** or **IB Gateway** with **API enabled**:
   Configure → API → Settings → Enable ActiveX and Socket Clients; set port.
3. Paper trading: typically port **7497**. Live: often **7496** (confirm in TWS).

Usage
-----
  export DEPLOY_USD=500 SYMBOL=VT
  python deploy_tws.py --dry-run          # qualify contract + preview only
  python deploy_tws.py --confirm          # submit order (live or paper per TWS)

Environment variables: DEPLOY_USD, SYMBOL, IB_HOST, IB_PORT, IB_CLIENT_ID
See .env.example
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from ib_insync import IB, MarketOrder, Order, Stock, util


def env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return float(v)


def env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


def env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or v.strip() == "" else v.strip()


def build_contract(symbol: str) -> Stock:
    # US ETFs / stocks: SMART router, USD
    return Stock(symbol.upper(), "SMART", "USD")


def main() -> None:
    if load_dotenv:
        load_dotenv()

    p = argparse.ArgumentParser(description="IBKR TWS: deploy notional into one ETF")
    p.add_argument(
        "--amount",
        type=float,
        default=None,
        help="USD notional (overrides DEPLOY_USD)",
    )
    p.add_argument("--symbol", default=None, help="Ticker (overrides SYMBOL)")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--client-id", type=int, default=None)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Connect, qualify contract, show mid; do not trade",
    )
    p.add_argument(
        "--confirm",
        action="store_true",
        help="Required to actually place an order (safety)",
    )
    p.add_argument(
        "--use-shares",
        action="store_true",
        help="Size by share quantity from last price instead of cashQty notional",
    )
    args = p.parse_args()

    amount = args.amount if args.amount is not None else env_float("DEPLOY_USD", 500.0)
    symbol = args.symbol if args.symbol else env_str("SYMBOL", "VT")
    host = args.host if args.host else env_str("IB_HOST", "127.0.0.1")
    port = args.port if args.port is not None else env_int("IB_PORT", 7497)
    client_id = args.client_id if args.client_id is not None else env_int("IB_CLIENT_ID", 17)

    if amount <= 0:
        sys.exit("DEPLOY_USD / --amount must be positive")

    ib = IB()
    try:
        ib.connect(host, port, clientId=client_id, readonly=False)
    except Exception as e:
        sys.exit(
            f"Could not connect to TWS/Gateway at {host}:{port} — is it running and API enabled? ({e})"
        )

    c = build_contract(symbol)
    ib.qualifyContracts(c)
    if not c.conId:
        sys.exit(f"Could not qualify contract for {symbol}")

    # Historical daily bar — works without live market data subscriptions
    bars = ib.reqHistoricalData(
        c,
        endDateTime="",
        durationStr="10 D",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
    )
    ref: Optional[float] = None
    if bars:
        ref = float(bars[-1].close)

    print(f"Qualified: {symbol} conId={c.conId}  ref_px≈{ref}")

    if args.dry_run or not args.confirm:
        if ref and ref > 0:
            approx_sh = amount / ref
            print(f"Dry-run: ~{approx_sh:.4f} shares for ${amount:,.2f} notional (estimate)")
        print("No order sent. Use --confirm to place order (omit --dry-run).")
        ib.disconnect()
        return

    # Live order path
    if args.use_shares:
        if not ref or ref <= 0:
            ib.disconnect()
            sys.exit("Need a usable historical price to size by shares.")
        qty = round(amount / ref, 6)
        if qty <= 0:
            ib.disconnect()
            sys.exit("Computed share quantity <= 0")
        order = MarketOrder("BUY", qty)
        trade = ib.placeOrder(c, order)
    else:
        # Notional: cashQty (US-eligible stocks/ETFs per IBKR; may reject — retry with --use-shares)
        order = Order(
            action="BUY",
            orderType="MKT",
            totalQuantity=0.0,
            cashQty=float(amount),
            tif="DAY",
        )
        trade = ib.placeOrder(c, order)

    print("Order submitted:", trade)
    ib.sleep(1)
    while not trade.isDone():
        ib.waitOnUpdate(timeout=30)

    print("Final status:", trade.orderStatus.status, trade.orderStatus.filled, trade.orderStatus.avgFillPrice)
    ib.disconnect()


if __name__ == "__main__":
    util.patchAsyncio()
    main()
