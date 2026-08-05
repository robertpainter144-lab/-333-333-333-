#!/usr/bin/env python3
"""
IBKR one-shot connection check.

Run AFTER IB Gateway is up:
    python3 ibkr_connection_check.py [--port 4002]   # paper
    python3 ibkr_connection_check.py [--port 4001]   # live

Exits 0 on success ("IBKR READY"), 1 on failure.
"""

import argparse
import socket
import sys
import time

HOST      = "127.0.0.1"
CLIENT_ID = 99   # ephemeral check — won't conflict with bot (20) or qc_portfolio (1)

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=4001,
                    help="4001=live gateway, 4002=paper gateway")
args = parser.parse_args()
PORT = args.port


def banner(msg: str):
    print(msg, flush=True)


banner(f"\n=== IBKR Connection Check  host={HOST}  port={PORT} ===\n")

# ── 1. TCP reachability ───────────────────────────────────────────────────────
banner(f"[1/4] TCP probe {HOST}:{PORT} ...")
try:
    with socket.create_connection((HOST, PORT), timeout=3):
        banner(f"      OK — port {PORT} is open")
except OSError as e:
    banner(f"      FAIL — port {PORT} closed or IB Gateway not running: {e}")
    banner("\nManual steps to fix:")
    banner("  1. Download IB Gateway: https://www.interactivebrokers.com/en/trading/ibgateway.php")
    banner("  2. Launch IB Gateway, log in with your IBKR credentials")
    banner("  3. Settings → API → Settings:")
    banner("       ✓ Enable ActiveX and Socket Clients")
    banner("       ✓ Socket port: 4001 (live) or 4002 (paper)")
    banner("       ✓ Add 127.0.0.1 to Trusted IP Addresses")
    banner("       ✗ Uncheck 'Read-Only API'")
    banner("  4. Re-run this script")
    sys.exit(1)

# ── 2. ib_insync import ───────────────────────────────────────────────────────
banner("\n[2/4] Importing ib_insync ...")
try:
    import nest_asyncio
    nest_asyncio.apply()
    import logging
    logging.getLogger("ib_insync").setLevel(logging.CRITICAL)
    from ib_insync import IB, Stock
    banner("      OK — ib_insync imported")
except ImportError as e:
    banner(f"      FAIL — {e}")
    banner("      Fix: pip install ib_insync nest_asyncio")
    sys.exit(1)

# ── 3. Connect and account summary ───────────────────────────────────────────
banner(f"\n[3/4] Connecting to IB Gateway (clientId={CLIENT_ID}) ...")
ib = IB()
try:
    ib.connect(HOST, PORT, clientId=CLIENT_ID, readonly=True, timeout=10)
    ib.sleep(1)
    banner("      OK — connected")
except Exception as e:
    banner(f"      FAIL — {e}")
    banner("      Possible causes: wrong clientId in use, read-only mode, firewall")
    sys.exit(1)

try:
    accounts = ib.managedAccounts()
    acct_id  = accounts[0] if accounts else ""
    banner(f"      Account: {acct_id}  (all accounts: {accounts})")

    summary = {s.tag: s.value for s in ib.accountSummary(acct_id)}
    nlv   = float(summary.get("NetLiquidation", 0))
    cash  = float(summary.get("TotalCashValue", 0))
    ccy   = summary.get("Currency", "USD")
    banner(f"      NLV={nlv:,.2f} {ccy}   Cash={cash:,.2f} {ccy}")

    if nlv <= 0:
        banner("\n      WARNING: NetLiquidation is 0 — account may be unfunded or wrong environment")
except Exception as e:
    banner(f"      FAIL — account summary error: {e}")
    ib.disconnect()
    sys.exit(1)

# ── 4. Qualify a test contract ────────────────────────────────────────────────
banner("\n[4/4] Qualifying AAPL Stock contract ...")
try:
    contract = Stock("AAPL", "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if qualified:
        banner(f"      OK — {qualified[0]}")
    else:
        banner("      WARNING: qualifyContracts returned empty list (market may be closed)")
except Exception as e:
    banner(f"      FAIL — {e}")

ib.disconnect()

# ── Summary ───────────────────────────────────────────────────────────────────
mode = "PAPER" if PORT == 4002 else "LIVE"
banner(f"\n{'='*55}")
banner(f"  IBKR READY  ({mode} account={acct_id}  NLV=${nlv:,.2f})")
banner(f"{'='*55}")
banner("\nNext step: in mock_portfolio.py set IBKR_LIVE_ENABLED = True")
banner("           and change IBKR_PORT to match (4001=live, 4002=paper)\n")
sys.exit(0)
