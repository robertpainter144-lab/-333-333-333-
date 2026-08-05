#!/usr/bin/env python3.10
"""
ES Mini paper trading test script  (uses ibapi 9.81.1)
Connects to IB Gateway paper account, requests the ES front-month contract,
prints bid/ask, places a 1-contract limit order 5 ticks below market, cancels it.

MANUAL SETUP REQUIRED (no launchd autostart for IB Gateway):
  1. Download IB Gateway (stable):
       https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
  2. Launch IB Gateway → log in with your IBKR paper trading account
  3. Configure > API > Settings:
       - Enable "Enable ActiveX and Socket Clients"
       - Socket port: 4002  (paper)   /  4001 (live — never for testing)
       - Add 127.0.0.1 to Trusted IP Addresses
       - Uncheck "Read-Only API" to allow order placement
  4. Verify connectivity:
       python3.10 -c "import socket; socket.create_connection(('127.0.0.1',4002),2); print('OPEN')"
  5. Run this script:
       python3.10 /Users/quarki3/es_mini_test.py

ES Mini specs: tick=0.25 pts, tick value=$12.50, multiplier=50, margin ~$12k/contract
"""

import time
import threading
from datetime import datetime

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order

PAPER_PORT  = 4002
HOST        = "127.0.0.1"
CLIENT_ID   = 91        # unique — avoids conflict with qc_portfolio clients
ES_TICK     = 0.25      # ES Mini minimum price increment
TICKS_BELOW = 5         # how far below bid to place the test limit order


def front_month() -> str:
    """Current ES front-month in YYYYMM format.
    Quarterly cycle: Mar(3) Jun(6) Sep(9) Dec(12).
    Rolls ~2 days before the 3rd Friday of the expiry month; use day>14 as proxy.
    """
    now = datetime.now()
    y, m, d = now.year, now.month, now.day
    for q in (3, 6, 9, 12):
        if q > m or (q == m and d <= 14):
            return f"{y}{q:02d}"
    return f"{y + 1}03"


class ESTestApp(EWrapper, EClient):
    def __init__(self):
        EWrapper.__init__(self)
        EClient.__init__(self, self)
        self.next_order_id  = None
        self.es_contract    = None
        self.bid            = None
        self.ask            = None
        self._ctr_done      = threading.Event()
        self._mkt_done      = threading.Event()
        self._order_placed  = threading.Event()
        self._order_cancelled = threading.Event()

    # ------------------------------------------------------------------ #
    #  Connection                                                          #
    # ------------------------------------------------------------------ #
    def nextValidId(self, orderId: int):
        print(f"[TWS]   Connected. Next valid orderId: {orderId}")
        self.next_order_id = orderId
        threading.Thread(target=self._test_sequence, daemon=True).start()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2158):   # benign farm-connectivity notices
            return
        print(f"[ERR]   reqId={reqId}  code={errorCode}  {errorString}")

    # ------------------------------------------------------------------ #
    #  Contract details                                                    #
    # ------------------------------------------------------------------ #
    def contractDetails(self, reqId, contractDetails):
        c = contractDetails.contract
        self.es_contract = c
        print(f"[CTR]   {c.symbol} {c.lastTradeDateOrContractMonth}  "
              f"conId={c.conId}  exchange={c.exchange}  mult={c.multiplier}")

    def contractDetailsEnd(self, reqId):
        self._ctr_done.set()

    # ------------------------------------------------------------------ #
    #  Market data                                                         #
    # ------------------------------------------------------------------ #
    def tickPrice(self, reqId, tickType, price, attrib):
        # 1=bid  2=ask
        if tickType == 1 and price > 0:
            self.bid = price
            print(f"[MKT]   Bid: {price}")
        elif tickType == 2 and price > 0:
            self.ask = price
            print(f"[MKT]   Ask: {price}")
        if self.bid and self.ask:
            self._mkt_done.set()

    # ------------------------------------------------------------------ #
    #  Order callbacks                                                     #
    # ------------------------------------------------------------------ #
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                    permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        print(f"[ORD]   id={orderId}  status={status}  "
              f"filled={filled}  remaining={remaining}")
        if status in ("Submitted", "PreSubmitted"):
            self._order_placed.set()
        if status == "Cancelled":
            self._order_cancelled.set()

    def openOrder(self, orderId, contract, order, orderState):
        print(f"[OPN]   id={orderId}  {contract.symbol}  {order.action}  "
              f"qty={order.totalQuantity}  lmtPx={order.lmtPrice}  "
              f"state={orderState.status}")

    # ------------------------------------------------------------------ #
    #  Test sequence (runs in its own thread)                              #
    # ------------------------------------------------------------------ #
    def _test_sequence(self):
        try:
            expiry = front_month()

            # Step 1: qualify the front-month contract
            spec = Contract()
            spec.symbol   = "ES"
            spec.secType  = "FUT"
            spec.exchange  = "CME"
            spec.currency  = "USD"
            spec.lastTradeDateOrContractMonth = expiry

            print(f"\n[1/3]  Requesting contract details for ES {expiry} ...")
            self.reqContractDetails(1, spec)
            if not self._ctr_done.wait(timeout=10):
                print("[WARN] Contract details timed out; using unqualified spec.")
                self.es_contract = spec
            else:
                print("[CTR]  Details received.")

            contract = self.es_contract

            # Step 2: subscribe to live market data
            print("\n[2/3]  Subscribing to market data (15-s window) ...")
            self.reqMktData(2, contract, "", False, False, [])
            if not self._mkt_done.wait(timeout=15):
                print("[ERR]  No bid/ask received — is IB Gateway running with paper account?")
                self.cancelMktData(2)
                return

            mid = (self.bid + self.ask) / 2
            print(f"[MKT]   Bid={self.bid}  Ask={self.ask}  Mid={mid:.2f}")

            # Step 3: place and cancel a limit order 5 ticks below bid
            limit_px = round(self.bid - TICKS_BELOW * ES_TICK, 2)
            print(f"\n[3/3]  Placing BUY LMT  1 ES @ {limit_px}  "
                  f"({TICKS_BELOW} ticks below bid={self.bid}) ...")

            order = Order()
            order.action        = "BUY"
            order.orderType     = "LMT"
            order.totalQuantity = 1
            order.lmtPrice      = limit_px
            order.tif           = "DAY"

            self.placeOrder(self.next_order_id, contract, order)
            placed = self._order_placed.wait(timeout=10)
            if not placed:
                print("[WARN] Order placement not confirmed within 10 s.")

            time.sleep(2)
            print(f"[3/3]  Cancelling order {self.next_order_id} ...")
            self.cancelOrder(self.next_order_id, "")
            done = self._order_cancelled.wait(timeout=10)
            if done:
                print("[3/3]  Order cancelled. No open position.")
            else:
                print("[WARN] Cancel not confirmed — check TWS Order Monitor.")

            self.cancelMktData(2)
            print("\n[DONE] Test complete.")

        finally:
            time.sleep(1)
            self.disconnect()


def main():
    expiry = front_month()
    print("=" * 60)
    print("  ES Mini Paper Trading Test  (ibapi)")
    print(f"  Host: {HOST}:{PAPER_PORT}  clientId={CLIENT_ID}")
    print(f"  Front-month: {expiry}")
    print("=" * 60 + "\n")

    app = ESTestApp()
    try:
        app.connect(HOST, PAPER_PORT, CLIENT_ID)
    except ConnectionRefusedError:
        print(
            "ERROR: Connection refused on port 4002.\n"
            "IB Gateway is not running or API is not enabled.\n"
            "Follow the MANUAL SETUP steps in this file's docstring."
        )
        return

    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()
    api_thread.join(timeout=60)

    if app.isConnected():
        print("[WARN] Timed out — disconnecting.")
        app.disconnect()


if __name__ == "__main__":
    main()
