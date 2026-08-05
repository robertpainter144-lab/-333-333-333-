"""
Background equity logger. Polls IBKR every LOG_INTERVAL_SEC, appends a row to
equity_history.csv. Feeds the dashboard's main portfolio chart.

Runs as its own launchd job (clientId=51 — doesn't collide with watcher=29 or
main.py one-shots=23).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ib_insync import IB

GATEWAY_HOST = os.environ.get("IBKR_GATEWAY_HOST", "127.0.0.1")
GATEWAY_PORT = int(os.environ.get("IBKR_GATEWAY_PORT", "4001"))

ROOT = Path(__file__).resolve().parent
HISTORY_CSV   = ROOT / "equity_history.csv"
POSITIONS_JSON = ROOT / "positions_snapshot.json"

LOG_INTERVAL_SEC = 60
CLIENT_ID        = 51


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_csv():
    if not HISTORY_CSV.exists():
        with HISTORY_CSV.open("w", newline="") as f:
            csv.writer(f).writerow(["timestamp", "equity", "cash", "buying_power", "available"])


def snapshot_account(ib: IB) -> dict:
    # If Gateway dropped the connection (nightly logout, restart, etc.),
    # managedAccounts() returns []. Try to reconnect once before raising.
    accts = ib.managedAccounts()
    if not accts:
        try:
            ib.disconnect()
        except Exception:
            pass
        ib.connect(GATEWAY_HOST, GATEWAY_PORT, clientId=CLIENT_ID, timeout=15)
        accts = ib.managedAccounts()
        if not accts:
            raise RuntimeError("IBKR Gateway reconnect failed — no managed accounts")
    acct = accts[0]
    # Ensure the portfolio subscription is active — ib.portfolio() only returns
    # items after reqAccountUpdates(True) has been triggered and IB has pushed
    # at least one update. Cheap to call repeatedly; ib_insync dedupes.
    try:
        ib.reqAccountUpdates(True, acct)
        ib.sleep(1.5)
    except Exception:
        pass
    summary = {s.tag: float(s.value)
               for s in ib.accountSummary(acct)
               if s.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower",
                            "AvailableFunds")}
    # Prefer ib.portfolio() — it returns PortfolioItem objects with marketPrice,
    # marketValue, unrealizedPNL already computed by IBKR. This is the same data
    # source that populates NetLiquidation. Crucially it does NOT require a
    # market-data subscription (reqMktData was failing with Error 354 on this
    # account — no ushmds farm access).
    positions = []
    portfolio_items = ib.portfolio(acct) if hasattr(ib, "portfolio") else []
    if portfolio_items:
        for item in portfolio_items:
            c = item.contract
            qty     = float(item.position)
            avg     = float(item.averageCost or 0)
            mkt_px  = float(item.marketPrice or 0)
            mkt_val = float(item.marketValue or (qty * mkt_px))
            upl     = float(item.unrealizedPNL) if item.unrealizedPNL is not None else (mkt_val - qty * avg)
            positions.append({
                "symbol":         c.symbol,
                "qty":            qty,
                "avg_cost":       avg,
                "market_price":   mkt_px if mkt_px > 0 else avg,
                "market_value":   mkt_val,
                "unrealized_pl":  upl,
                "exchange":       c.primaryExchange or "",
                "account":        acct,
            })
    else:
        # Fallback: portfolio() unavailable (very old ib_insync). Return raw
        # positions with market_price == avg_cost so the dashboard degrades
        # gracefully rather than crashing.
        for p in ib.positions(acct):
            qty = float(p.position)
            avg = float(p.avgCost or 0)
            positions.append({
                "symbol":         p.contract.symbol,
                "qty":            qty,
                "avg_cost":       avg,
                "market_price":   avg,
                "market_value":   qty * avg,
                "unrealized_pl":  0.0,
                "exchange":       p.contract.primaryExchange or "",
                "account":        acct,
            })
    return {
        "account":   acct,
        "ts":        _now_iso(),
        "equity":    summary.get("NetLiquidation", 0),
        "cash":      summary.get("TotalCashValue", 0),
        "bp":        summary.get("BuyingPower", 0),
        "available": summary.get("AvailableFunds", 0),
        "positions": positions,
    }


def write_history_row(snap: dict) -> None:
    _ensure_csv()
    with HISTORY_CSV.open("a", newline="") as f:
        csv.writer(f).writerow([
            snap["ts"], snap["equity"], snap["cash"], snap["bp"], snap["available"],
        ])


def write_positions(snap: dict) -> None:
    """Persist the latest snapshot, but DON'T overwrite a known-good positions
    list with an empty one. IBKR Gateway sometimes reports positions=[] for one
    or two ticks while it's disconnecting, which can clobber a fresh snapshot
    just before the daemon crashes on ConnectionRefused. Guard against that:
    if the new snap has 0 positions AND a previous snap with positions exists
    AND equity didn't change, treat it as a transient race and skip the write.
    """
    new_positions = snap.get("positions") or []
    if not new_positions and POSITIONS_JSON.exists():
        try:
            prev = json.loads(POSITIONS_JSON.read_text())
            prev_pos = prev.get("positions") or []
            prev_eq  = float(prev.get("equity") or 0)
            new_eq   = float(snap.get("equity") or 0)
            # Same equity but suddenly zero positions = bogus transient
            if prev_pos and abs(prev_eq - new_eq) < 0.01:
                print(f"[equity_logger] skipping write — transient 0-positions "
                      f"while equity unchanged (${new_eq:.2f})", flush=True)
                return
        except Exception:
            pass
    POSITIONS_JSON.write_text(json.dumps(snap, indent=2))


def main():
    daemon = "--daemon" in sys.argv
    ib = IB()
    ib.connect(GATEWAY_HOST, GATEWAY_PORT, clientId=CLIENT_ID, timeout=15)
    print(f"equity_logger connected: {ib.managedAccounts()} via {GATEWAY_HOST}:{GATEWAY_PORT}", flush=True)

    try:
        if not daemon:
            snap = snapshot_account(ib)
            write_history_row(snap)
            write_positions(snap)
            print(json.dumps(snap, indent=2, default=str))
            return
        while True:
            try:
                snap = snapshot_account(ib)
                write_history_row(snap)
                write_positions(snap)
                print(f"[{snap['ts'][11:19]}] eq=${snap['equity']:,.2f} "
                      f"cash=${snap['cash']:,.2f} positions={len(snap['positions'])}",
                      flush=True)
            except Exception as e:
                print(f"[equity_logger err] {e}", file=sys.stderr, flush=True)
            time.sleep(LOG_INTERVAL_SEC)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
