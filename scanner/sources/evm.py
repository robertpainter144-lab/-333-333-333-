"""EVM multichain source — Etherscan V2 across 6 chains.

Expands on ~/whale_tracker/etherscan_feed.py to write unified signals.
Uses shared curated wallet list but scans on cadence.
"""
from __future__ import annotations

import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, "/Users/quarki3/whale_tracker")
from signals import emit, log_tick
from curated_wallets import WHALES, label_for, counterparty_label

API = "https://api.etherscan.io/v2/api"
CHAINS = {1: "ethereum", 8453: "base", 42161: "arbitrum",
          10: "optimism", 137: "polygon", 56: "bsc"}

STABLES = {"USDC","USDT","USDe","DAI","FRAX","LUSD","GUSD","TUSD","PYUSD",
           "USDS","BUSD","USDD","crvUSD","GHO","FDUSD","USD1","USDG",
           "USD₮","USD₮0","XAUt","PAXG"}
MAJORS  = {"ETH","WETH","BTC","WBTC","stETH","wstETH","rETH","cbETH","weETH"}

LOOKBACK_S = 15 * 60      # only signals fresher than 15min
MIN_TRANSFER_USD = 25_000  # skip dust
CALL_DELAY_S = 0.22


def load_key() -> str:
    k = os.environ.get("ETHERSCAN_API_KEY", "")
    if not k:
        p = Path("/Users/quarki3/whale_tracker/etherscan.env")
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("ETHERSCAN_API_KEY="):
                    k = line.split("=", 1)[1].strip()
                    break
    return k


def token_price_hint(sym: str) -> float | None:
    """Very rough price hint for USD-sizing. Real price feed comes later."""
    s = sym.upper()
    if s in STABLES: return 1.0
    if s in {"ETH", "WETH"}: return 3800
    if s in {"BTC", "WBTC"}: return 96000
    if s in {"SOL"}: return 180
    if s in {"stETH", "wstETH", "rETH", "cbETH", "weETH"}: return 3800
    return None  # unknown — we'll still emit but size_usd=0


def fetch_erc20(chain_id: int, addr: str, key: str) -> list[dict]:
    params = {"chainid": chain_id, "module": "account", "action": "tokentx",
              "address": addr, "page": 1, "offset": 50, "sort": "desc", "apikey": key}
    try:
        r = requests.get(API, params=params, timeout=20)
        j = r.json()
        if j.get("status") not in ("1", 1):
            return []
        return j.get("result", []) or []
    except Exception:
        return []


def tick() -> None:
    key = load_key()
    if not key:
        log_tick("evm", "skip: no ETHERSCAN_API_KEY")
        return

    cutoff = int(time.time()) - LOOKBACK_S
    accumulation: dict[str, dict] = defaultdict(
        lambda: {"buyers": set(), "usd": 0.0, "chain": "?", "sample_tx": ""})
    total_txs = 0
    hits = 0

    for addr_row in WHALES:
        addr = addr_row[0]
        for chain_id, chain_name in CHAINS.items():
            txs = fetch_erc20(chain_id, addr, key)
            time.sleep(CALL_DELAY_S)
            for tx in txs:
                total_txs += 1
                ts = int(tx.get("timeStamp", 0))
                if ts < cutoff:
                    continue
                is_in = tx.get("to", "").lower() == addr.lower()
                sym   = tx.get("tokenSymbol", "?")
                if sym in STABLES or sym in MAJORS:
                    continue
                try:
                    amt = float(tx.get("value", 0)) / (10 ** int(tx.get("tokenDecimal", 18) or 18))
                except (TypeError, ValueError):
                    amt = 0.0

                price = token_price_hint(sym)
                usd   = amt * price if price else 0.0

                if is_in:
                    k = f"{chain_name}:{sym}"
                    accumulation[k]["buyers"].add(label_for(addr))
                    accumulation[k]["usd"] += usd
                    accumulation[k]["chain"] = chain_name
                    accumulation[k]["sample_tx"] = tx.get("hash", "")

                # single-txn large transfer (bidirectional flag)
                if usd >= MIN_TRANSFER_USD:
                    emit("evm", "large_transfer", sym, chain_name,
                         size_usd=usd,
                         direction="IN" if is_in else "OUT",
                         dedup_key=f"evm_lt:{tx.get('hash','')[:20]}:{sym}",
                         note=f"{label_for(addr)} {'received' if is_in else 'sent'} {amt:,.2f} {sym}",
                         url=f"https://{'basescan.org' if chain_name=='base' else chain_name+'scan.com' if chain_name in ('poly','bsc') else 'etherscan.io'}/tx/{tx.get('hash','')}",
                         raw={"tx": tx.get("hash",""), "wallet": addr})
                    hits += 1

    # coordinated accumulation: ≥2 whales bought same token in window
    for k, v in accumulation.items():
        if len(v["buyers"]) < 2:
            continue
        chain, sym = k.split(":", 1)
        emit("evm", "coordinated_accumulation", sym, chain,
             size_usd=v["usd"],
             direction="BUY",
             dedup_key=f"evm_coord:{chain}:{sym}:{int(time.time())//900}",
             wallets=len(v["buyers"]),
             note=f"{len(v['buyers'])} whales bought: " + ", ".join(sorted(v["buyers"])[:4]),
             url=f"https://etherscan.io/tx/{v['sample_tx']}",
             raw={"buyers": sorted(v["buyers"])})
        hits += 1

    log_tick("evm", f"txs_scanned={total_txs} signals={hits}")


if __name__ == "__main__":
    tick()
