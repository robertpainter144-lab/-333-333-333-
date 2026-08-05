"""CEX flows + bridge flows.

- Big deposits INTO known CEX hot/cold wallets = potential sell pressure
- Big withdrawals OUT of known CEX = potential accumulation
- Big native transfers into/out of known bridge contracts = cross-chain moves

Uses Etherscan V2 (same key as evm.py).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit, log_tick

API = "https://api.etherscan.io/v2/api"
LOOKBACK_S = 15 * 60
MIN_USD = 500_000
CALL_DELAY_S = 0.22

# CEX cold + hot wallets on ETH (public labels via Etherscan Name Tags).
# Add multi-chain equivalents over time.
CEX_WALLETS = {
    # Binance
    "0xf977814e90da44bfa03b6295a0616a897441acec": ("Binance 8",       "binance"),
    "0x28c6c06298d514db089934071355e5743bf21d60": ("Binance 14",      "binance"),
    "0x56eddb7aa87536c09ccc2793473599fd21a8b17f": ("Binance 17",      "binance"),
    "0x9696f59e4d72e237be84ffd425dcad154bf96976": ("Binance 20",      "binance"),
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": ("Binance 16",      "binance"),
    # Coinbase
    "0xa9d1e08c7793af67e9d92fe308d5697fb81d3e43": ("Coinbase 10",     "coinbase"),
    "0x71660c4005ba85c37ccec55d0c4493e66fe775d3": ("Coinbase 1",      "coinbase"),
    # OKX
    "0x4a4aaa0155237881fbd5c34bfae16e985a7b068d": ("OKX 154",         "okx"),
    "0x3b98b9e9cb75c1cb1729aab8f09ced2936505e4f": ("OKX 217",         "okx"),
    "0xa7efae728d2936e78bda97dc267687568dd593f3": ("OKX",             "okx"),
    # Kraken
    "0x2910543af39aba0cd09dbb2d50200b3e800a63d2": ("Kraken 4",        "kraken"),
    # Bybit
    "0x6643887784cb89cc9537dbb7ea20b00c45abeefb": ("ByBit",           "bybit"),
    # Gate
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": ("Gate.io",         "gate"),
    "0xd6b38bea1b891c9064b1167fb68f8478f6829326": ("Gate.io",         "gate"),
    # Bitget
    "0xc7d3c88077d07b9ede6acd2060e5dacbcf08e668": ("Bitget",          "bitget"),
}

# Bridge contracts (a small starter set — expand later)
BRIDGES_ETH = {
    "0x88ad09518695c6c3712ac10a214be5109a655671": ("Gnosis Omnibridge",     "gnosis"),
    "0xa0c68c638235ee32657e8f720a23cec1bfc77c77": ("Polygon PoS Bridge",    "polygon"),
    "0x99c9fc46f92e8a1c0dec1b1747d010903e884be1": ("Optimism Gateway",      "optimism"),
    "0xa17e78e9d31ba9c1c8f2b40f77f30dbf95e3fe98": ("Arbitrum Gateway",      "arbitrum"),
    "0x3ee18b2214aff97000d974cf647e7c347e8fa585": ("Wormhole ETH",          "wormhole"),
    "0x3154cf16ccdb4c6d922629664174b904d80f2c35": ("Base Bridge",           "base"),
}


def load_key() -> str:
    p = Path("/Users/quarki3/whale_tracker/etherscan.env")
    if p.exists():
        for line in p.read_text().splitlines():
            if line.startswith("ETHERSCAN_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("ETHERSCAN_API_KEY", "")


def fetch_normal_txs(addr: str, key: str) -> list[dict]:
    """Fetch recent normal ETH transactions to/from an address on mainnet."""
    params = {"chainid": 1, "module": "account", "action": "txlist",
              "address": addr, "page": 1, "offset": 50, "sort": "desc", "apikey": key}
    try:
        r = requests.get(API, params=params, timeout=20)
        j = r.json()
        if j.get("status") not in ("1", 1):
            return []
        return j.get("result", []) or []
    except Exception:
        return []


ETH_PRICE = 3800  # rough — refined later


def scan_cex_and_bridges() -> tuple[int, int]:
    key = load_key()
    if not key:
        return 0, 0
    cutoff = int(time.time()) - LOOKBACK_S
    cex_hits, bridge_hits = 0, 0

    targets = list(CEX_WALLETS.items()) + list(BRIDGES_ETH.items())
    for addr, (label, entity) in targets:
        txs = fetch_normal_txs(addr, key)
        time.sleep(CALL_DELAY_S)
        for tx in txs:
            ts = int(tx.get("timeStamp", 0))
            if ts < cutoff:
                continue
            try:
                val_wei = int(tx.get("value", "0"))
            except ValueError:
                continue
            eth_amt = val_wei / 1e18
            usd     = eth_amt * ETH_PRICE
            if usd < MIN_USD:
                continue

            is_deposit = tx.get("to", "").lower() == addr.lower()
            in_cex   = addr in CEX_WALLETS
            src      = "cex" if in_cex else "bridge"
            if in_cex:
                sig_type   = "cex_deposit" if is_deposit else "cex_withdrawal"
                direction  = "SELL_PRESSURE" if is_deposit else "ACCUMULATION"
            else:
                sig_type   = "bridge_in" if is_deposit else "bridge_out"
                direction  = "CROSS_CHAIN"
            if emit(src, sig_type, "ETH", "ethereum",
                    size_usd=usd, direction=direction,
                    dedup_key=f"{src}:{tx.get('hash','')[:20]}",
                    note=f"{eth_amt:,.2f} ETH {'→' if is_deposit else '←'} {label}",
                    url=f"https://etherscan.io/tx/{tx.get('hash','')}",
                    raw={"entity": entity, "label": label, "counterparty": tx.get("from" if is_deposit else "to", "")}):
                if in_cex:
                    cex_hits += 1
                else:
                    bridge_hits += 1
    return cex_hits, bridge_hits


def tick() -> None:
    c, b = scan_cex_and_bridges()
    log_tick("cex_bridge", f"cex_hits={c} bridge_hits={b}")


if __name__ == "__main__":
    tick()
