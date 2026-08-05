"""
Unified whale-transfer feed via Etherscan V2 API (multi-chain).

Reads ETHERSCAN_API_KEY from etherscan.env (or env).
Grab a free key at https://etherscan.io/register — takes 30 seconds.

Etherscan V2 covers Ethereum, Base, Arbitrum, Optimism, Polygon, BSC, etc
via a single endpoint (chainid=1/8453/42161/10/137/56).

Pulls ERC-20 transfers for each curated wallet, tags entity labels, sorts
by time or size, prints a live feed. Also flags dormant wallets so the
curated list can be pruned.
"""
from __future__ import annotations

import argparse
import os
import time
from collections import defaultdict
from pathlib import Path

import requests

from curated_wallets import addresses, label_for, category_for, counterparty_label

ROOT    = Path(__file__).resolve().parent
KEYFILE = ROOT / "etherscan.env"

API_BASE = "https://api.etherscan.io/v2/api"

# Chain IDs supported by Etherscan V2. Add more as needed.
CHAINS = {
    1:     "ethereum",
    8453:  "base",
    42161: "arbitrum",
    10:    "optimism",
    137:   "polygon",
    56:    "bsc",
}

# Rate limits: free tier = 5 req/sec, so 0.25s between calls is safe.
CALL_DELAY_S = 0.25

# Dormant threshold: no txns in this many days = flag for removal
DORMANT_DAYS = 30

# ── Common stablecoins & majors for filtering "real buys" ────────────
STABLES = {"USDC","USDT","USDe","DAI","FRAX","LUSD","GUSD","TUSD","PYUSD",
           "USDS","BUSD","USDD","crvUSD","GHO","FDUSD","USD1","USD₮","USD₮0"}
MAJORS  = {"ETH","WETH","BTC","WBTC","stETH","wstETH","rETH","cbETH","weETH"}


def load_key() -> str:
    k = os.environ.get("ETHERSCAN_API_KEY", "")
    if not k and KEYFILE.exists():
        for line in KEYFILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k_, _, v = line.partition("=")
            if k_ == "ETHERSCAN_API_KEY":
                k = v
                break
    return k


def fetch_erc20(chain_id: int, address: str, api_key: str,
                page: int = 1, offset: int = 100) -> list[dict]:
    """Fetch recent ERC-20 transfers for a wallet on one chain."""
    params = {
        "chainid":    chain_id,
        "module":     "account",
        "action":     "tokentx",
        "address":    address,
        "page":       page,
        "offset":     offset,
        "sort":       "desc",
        "apikey":     api_key,
    }
    r = requests.get(API_BASE, params=params, timeout=30)
    if not r.ok:
        return []
    j = r.json()
    if j.get("status") not in ("1", 1):    # "0" = No transactions found, still ok
        return []
    return j.get("result", []) or []


def fetch_native(chain_id: int, address: str, api_key: str,
                 page: int = 1, offset: int = 100) -> list[dict]:
    """Fetch recent native-token (ETH/MATIC/etc) transfers."""
    params = {
        "chainid":    chain_id,
        "module":     "account",
        "action":     "txlist",
        "address":    address,
        "page":       page,
        "offset":     offset,
        "sort":       "desc",
        "apikey":     api_key,
    }
    r = requests.get(API_BASE, params=params, timeout=30)
    if not r.ok:
        return []
    j = r.json()
    if j.get("status") not in ("1", 1):
        return []
    return j.get("result", []) or []


def normalize_erc20(tx: dict, chain_name: str, our_wallet: str) -> dict:
    """Normalize an Etherscan tokentx entry into our unified format."""
    ts       = int(tx.get("timeStamp", 0))
    is_out   = tx.get("from", "").lower() == our_wallet.lower()
    value    = float(tx.get("value", 0))
    decimals = int(tx.get("tokenDecimal", 18) or 18)
    amount   = value / (10 ** decimals)
    return {
        "ts":          ts,
        "chain":       chain_name,
        "direction":   "OUT" if is_out else "IN",
        "our_wallet":  our_wallet,
        "counterparty": tx.get("to" if is_out else "from", ""),
        "token":       tx.get("tokenSymbol", "?"),
        "token_addr":  tx.get("contractAddress", ""),
        "amount":      amount,
        "tx_hash":     tx.get("hash", ""),
    }


def pull_all(api_key: str, hours: int) -> tuple[list[dict], dict]:
    """Pull recent transfers for every curated wallet on every chain."""
    cutoff  = int(time.time()) - hours * 3600
    feed    = []
    last_tx = {}   # wallet -> most recent txn timestamp (for dormancy check)

    for addr in addresses():
        last_tx[addr.lower()] = 0
        for chain_id, chain_name in CHAINS.items():
            txs = fetch_erc20(chain_id, addr, api_key, offset=100)
            time.sleep(CALL_DELAY_S)
            for tx in txs:
                ts = int(tx.get("timeStamp", 0))
                last_tx[addr.lower()] = max(last_tx[addr.lower()], ts)
                if ts >= cutoff:
                    feed.append(normalize_erc20(tx, chain_name, addr))
            print(f"  {addr[:10]}… on {chain_name:<10} → {len(txs)} txns", flush=True)

    return feed, last_tx


def print_feed(feed: list[dict], limit: int = 60, sort_by: str = "time"):
    if sort_by == "size":
        feed = sorted(feed, key=lambda r: r["amount"], reverse=True)
    else:
        feed = sorted(feed, key=lambda r: r["ts"], reverse=True)

    print(f"\n=== {len(feed)} transfers (last N hours) — sorted by {sort_by} ===\n")
    print(f"{'time':<20} {'chain':<10} {'cat':<4} {'entity':<22} {'dir':<4} {'token':<10} "
          f"{'amount':>18} {'counterparty':<42}")
    print("-" * 140)
    for row in feed[:limit]:
        ts     = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(row["ts"]))
        chain  = row["chain"][:9]
        cat    = category_for(row["our_wallet"])
        ent    = label_for(row["our_wallet"])[:21]
        d      = row["direction"]
        tok    = row["token"][:9]
        amt    = row["amount"]
        cp     = counterparty_label(row["counterparty"])[:41]
        print(f"{ts:<20} {chain:<10} {cat:<4} {ent:<22} {d:<4} {tok:<10} {amt:>18,.4f} {cp}")


def by_token(feed: list[dict]):
    """Group by (token, direction) to see what's being accumulated / distributed."""
    agg = defaultdict(lambda: {"n": 0, "amt": 0, "entities": set()})
    for r in feed:
        key = (r["token"], r["direction"])
        agg[key]["n"]        += 1
        agg[key]["amt"]      += r["amount"]
        agg[key]["entities"].add(label_for(r["our_wallet"]))

    print(f"\n=== Aggregate by (token, direction) ===")
    print(f"{'token':<10} {'dir':<4} {'txns':>6} {'total amount':>20} {'#entities':>10}  entities")
    print("-" * 100)
    rows = sorted(agg.items(), key=lambda kv: kv[1]["amt"], reverse=True)
    for (tok, d), s in rows[:30]:
        ents = ", ".join(sorted(s["entities"]))[:60]
        print(f"{tok:<10} {d:<4} {s['n']:>6} {s['amt']:>20,.2f} {len(s['entities']):>10}  {ents}")


def counterparty_map(feed: list[dict], min_hits: int = 3):
    """Recurring counterparties across curated wallets → these are the CEXes / OTC desks."""
    cp = defaultdict(lambda: {"n": 0, "entities": set()})
    for r in feed:
        c = r["counterparty"].lower()
        cp[c]["n"]        += 1
        cp[c]["entities"].add(label_for(r["our_wallet"]))
    print(f"\n=== Recurring counterparties (≥{min_hits} hits) — likely CEXes / OTC ===")
    print(f"{'counterparty':<44} {'label':<30} {'hits':>6}  entities touched")
    print("-" * 130)
    hits = [(c, s) for c, s in cp.items() if s["n"] >= min_hits]
    for c, s in sorted(hits, key=lambda kv: kv[1]["n"], reverse=True)[:20]:
        ents  = ", ".join(sorted(s["entities"]))[:50]
        clabel = counterparty_label(c)
        print(f"{c:<44} {clabel:<30} {s['n']:>6}  {ents}")


def check_dormancy(last_tx: dict[str, int]):
    now      = int(time.time())
    dormant  = []
    print(f"\n=== Dormant-wallet check (no txns in >{DORMANT_DAYS}d) ===")
    for a, ts in last_tx.items():
        if ts == 0:
            dormant.append((a, "never seen on any tracked chain"))
        else:
            age_d = (now - ts) / 86400
            if age_d > DORMANT_DAYS:
                dormant.append((a, f"last activity {age_d:.0f}d ago"))
    if not dormant:
        print("  All curated wallets active in the last 30d ✓")
    for a, why in dormant:
        print(f"  {a}  {label_for(a):<22}  {why}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=24, help="lookback window in hours")
    ap.add_argument("--sort",  choices=["time", "size"], default="time")
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args()

    key = load_key()
    if not key:
        print("ERROR: no ETHERSCAN_API_KEY set.")
        print("  1. Grab a free key: https://etherscan.io/register")
        print("  2. Save it: echo 'ETHERSCAN_API_KEY=<key>' > etherscan.env")
        raise SystemExit(1)

    print(f"Pulling {args.hours}h of transfers across {len(CHAINS)} chains "
          f"for {len(addresses())} wallets…\n")
    feed, last_tx = pull_all(key, args.hours)

    print_feed(feed, limit=args.limit, sort_by=args.sort)
    by_token(feed)
    counterparty_map(feed, min_hits=3)
    check_dormancy(last_tx)


if __name__ == "__main__":
    main()
