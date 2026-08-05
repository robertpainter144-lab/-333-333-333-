"""Market breadth signals — CoinGecko + DEXScreener (multi-chain).

- CoinGecko trending: attention leaders across all crypto
- CoinGecko top gainers/losers 24h
- DEXScreener trending across all chains (not just Solana)
- Fear/Greed index

All free public APIs. No key needed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit, log_tick

CG_TRENDING = "https://api.coingecko.com/api/v3/search/trending"
CG_MARKETS  = ("https://api.coingecko.com/api/v3/coins/markets"
               "?vs_currency=usd&order=market_cap_desc&per_page=250&page=1"
               "&price_change_percentage=24h")
FEAR_GREED  = "https://api.alternative.me/fng/?limit=1"
DEXS_TOP    = "https://api.dexscreener.com/token-boosts/top/v1"


def _get(url: str) -> dict | list:
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def scan_trending() -> int:
    hits = 0
    try:
        j = _get(CG_TRENDING)
    except Exception as e:
        return 0
    for i, c in enumerate((j.get("coins") or [])[:10]):
        item = c.get("item", {})
        sym  = item.get("symbol", "?")
        name = item.get("name", "?")
        cap  = item.get("market_cap_btc", 0) or 0
        rank = i + 1
        if emit("coingecko", "trending", sym, "any",
                size_usd=float(cap) * 96000,   # approx
                direction=f"RANK_{rank}",
                dedup_key=f"cg_trend:{sym}:{rank}",
                note=f"CG trending #{rank}: {name}",
                url=f"https://coingecko.com/en/coins/{item.get('id','')}",
                raw=item):
            hits += 1
    return hits


def scan_gainers_losers() -> int:
    """Big movers in top-250 by market cap (real caps, not memecoin noise)."""
    hits = 0
    try:
        rows = _get(CG_MARKETS)
    except Exception:
        return 0
    if not isinstance(rows, list):
        return 0
    # Gainers ≥ +10% / day
    gainers = [r for r in rows if (r.get("price_change_percentage_24h") or 0) >= 10]
    losers  = [r for r in rows if (r.get("price_change_percentage_24h") or 0) <= -10]
    for r in sorted(gainers, key=lambda x: x.get("price_change_percentage_24h", 0), reverse=True)[:10]:
        sym = r.get("symbol", "?").upper()
        pct = r.get("price_change_percentage_24h", 0)
        cap = r.get("market_cap", 0) or 0
        vol = r.get("total_volume", 0) or 0
        if emit("coingecko", "big_gainer_24h", sym, "any",
                size_usd=vol, direction="PUMP",
                dedup_key=f"cg_gain:{sym}:{int(pct)}",
                note=f"+{pct:.1f}% 24h  mc=${cap/1e6:.1f}M  vol=${vol/1e6:.1f}M",
                url=f"https://coingecko.com/en/coins/{r.get('id','')}",
                raw={"pct": pct, "mc": cap, "vol": vol}):
            hits += 1
    for r in sorted(losers, key=lambda x: x.get("price_change_percentage_24h", 0))[:10]:
        sym = r.get("symbol", "?").upper()
        pct = r.get("price_change_percentage_24h", 0)
        cap = r.get("market_cap", 0) or 0
        vol = r.get("total_volume", 0) or 0
        if emit("coingecko", "big_loser_24h", sym, "any",
                size_usd=vol, direction="DUMP",
                dedup_key=f"cg_loss:{sym}:{int(pct)}",
                note=f"{pct:.1f}% 24h  mc=${cap/1e6:.1f}M  vol=${vol/1e6:.1f}M",
                url=f"https://coingecko.com/en/coins/{r.get('id','')}",
                raw={"pct": pct, "mc": cap, "vol": vol}):
            hits += 1
    return hits


def scan_fear_greed() -> int:
    try:
        j = _get(FEAR_GREED)
    except Exception:
        return 0
    data = (j.get("data") or [{}])[0]
    val  = int(data.get("value", 0) or 0)
    cls  = data.get("value_classification", "?")
    if val <= 25 or val >= 75:   # only emit extremes
        direction = "EXTREME_FEAR" if val <= 25 else "EXTREME_GREED"
        return int(emit("alt", "fear_greed", "MARKET", "any",
                        size_usd=float(val), direction=direction,
                        dedup_key=f"fg:{val // 5}",
                        note=f"F&G Index: {val} — {cls}",
                        url="https://alternative.me/crypto/fear-and-greed-index/",
                        raw=data))
    return 0


def scan_multichain_boosts() -> int:
    """DEXScreener boosts across ALL chains (not just Solana)."""
    hits = 0
    try:
        boosts = _get(DEXS_TOP)
    except Exception:
        return 0
    if not isinstance(boosts, list):
        return 0
    for b in boosts[:40]:
        chain = b.get("chainId", "?")
        if chain == "solana":
            continue   # handled by solana.py
        addr  = b.get("tokenAddress", "")
        amt   = b.get("totalAmount", 0) or 0
        if not addr or amt < 200:
            continue
        if emit("dexscreener", "boost", addr[:10], chain,
                size_usd=float(amt), direction="ATTENTION",
                dedup_key=f"dex_boost:{chain}:{addr}",
                note=f"paid ${amt} in boosts on {chain}",
                url=f"https://dexscreener.com/{chain}/{addr}",
                raw={"chain": chain, "addr": addr}):
            hits += 1
    return hits


def tick() -> None:
    a = scan_trending()
    b = scan_gainers_losers()
    c = scan_fear_greed()
    d = scan_multichain_boosts()
    log_tick("breadth", f"trending={a} movers={b} fear_greed={c} multichain_boosts={d}")


if __name__ == "__main__":
    tick()
