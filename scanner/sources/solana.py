"""Solana source — DEXScreener + Jupiter public APIs.

DEXScreener trending/boosted tokens (no key) → memecoin momentum.
Birdeye trending fallback if configured.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit, log_tick

DEXS_BOOSTS = "https://api.dexscreener.com/token-boosts/top/v1"
DEXS_TRENDING_LATEST = "https://api.dexscreener.com/token-boosts/latest/v1"
DEXS_TOKEN_PAIRS = "https://api.dexscreener.com/latest/dex/tokens/"

MIN_LIQ_USD = 25_000
MIN_VOL_5M  = 5_000
MAX_TOKENS_PER_TICK = 30


def _get(url: str) -> dict | list:
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    return r.json()


def scan_boosted() -> int:
    """DEXScreener 'boosts' = paid trending. Extract top Solana tokens with real liq."""
    try:
        boosts = _get(DEXS_BOOSTS)
    except Exception as e:
        log_tick("solana", f"boosts fail: {e}")
        return 0
    if not isinstance(boosts, list):
        return 0

    sol_tokens = [b for b in boosts if b.get("chainId") == "solana"][:MAX_TOKENS_PER_TICK]
    hits = 0
    for b in sol_tokens:
        addr = b.get("tokenAddress")
        if not addr:
            continue
        try:
            j = _get(DEXS_TOKEN_PAIRS + addr)
        except Exception:
            continue
        pairs = j.get("pairs") or []
        pairs = [p for p in pairs if p.get("chainId") == "solana"]
        if not pairs:
            continue
        # take highest-liq pair
        pair = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd", 0) or 0)
        liq   = (pair.get("liquidity") or {}).get("usd", 0) or 0
        vol5m = (pair.get("volume")    or {}).get("m5",   0) or 0
        chg5m = (pair.get("priceChange") or {}).get("m5", 0) or 0
        chg1h = (pair.get("priceChange") or {}).get("h1", 0) or 0
        sym   = pair.get("baseToken", {}).get("symbol", "?")
        if liq < MIN_LIQ_USD or vol5m < MIN_VOL_5M:
            continue

        # Momentum signal: 5m volume + price change
        direction = "PUMP" if chg5m > 0 else "DUMP"
        emit("solana", "dex_momentum", sym, "solana",
             size_usd=vol5m,
             direction=direction,
             dedup_key=f"sol_mom:{addr}:{int(chg5m)}",
             note=f"5m: {chg5m:+.1f}%  1h: {chg1h:+.1f}%  vol5m=${vol5m:,.0f}  liq=${liq:,.0f}",
             url=f"https://dexscreener.com/solana/{addr}",
             raw={"addr": addr, "liq": liq, "vol5m": vol5m, "chg5m": chg5m, "chg1h": chg1h})
        hits += 1
    return hits


def scan_latest_boosts() -> int:
    """Freshly boosted (paid promo) Solana tokens — early-attention signal."""
    try:
        latest = _get(DEXS_TRENDING_LATEST)
    except Exception:
        return 0
    if not isinstance(latest, list):
        return 0
    hits = 0
    for b in latest:
        if b.get("chainId") != "solana":
            continue
        addr = b.get("tokenAddress")
        amt  = b.get("totalAmount", 0) or 0
        if not addr or amt < 100:
            continue
        emit("solana", "fresh_boost", addr[:10], "solana",
             size_usd=float(amt),
             direction="ATTENTION",
             dedup_key=f"sol_boost:{addr}",
             note=f"paid ${amt} in DEXScreener boosts (attention buy)",
             url=f"https://dexscreener.com/solana/{addr}",
             raw={"addr": addr, "boost_usd": amt})
        hits += 1
        if hits >= 20:
            break
    return hits


def tick() -> None:
    n1 = scan_boosted()
    n2 = scan_latest_boosts()
    log_tick("solana", f"boosted={n1} fresh={n2}")


if __name__ == "__main__":
    tick()
