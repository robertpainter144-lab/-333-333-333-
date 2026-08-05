"""Hyperliquid perp signals — no API key needed.

- Extreme funding rates → crowded trades
- Large recent trades per coin → whale activity
- OI leaders + volume leaders

Public REST: https://api.hyperliquid.xyz/info
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit, log_tick

API = "https://api.hyperliquid.xyz/info"

# Thresholds
LARGE_TRADE_USD    = 250_000    # single trade size
EXTREME_FUNDING    = 0.0005     # per 1h ≈ ~50% APR
WHALE_TRADES_COINS = ["BTC", "ETH", "SOL", "HYPE", "PEPE", "WIF", "DOGE",
                      "kBONK", "TRUMP", "SUI", "FARTCOIN"]


def _post(body: dict) -> dict | list:
    r = requests.post(API, json=body, timeout=15,
                      headers={"Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()


def scan_funding_and_oi() -> int:
    """Fetch all perp markets; flag extreme funding + top OI/volume leaders."""
    data = _post({"type": "metaAndAssetCtxs"})
    if not isinstance(data, list) or len(data) < 2:
        return 0
    meta, ctxs = data[0], data[1]
    universe = meta.get("universe", [])
    hits = 0
    for coin, ctx in zip(universe, ctxs):
        name = coin.get("name", "?")
        try:
            funding    = float(ctx.get("funding", 0))
            mark_px    = float(ctx.get("markPx", 0))
            open_int   = float(ctx.get("openInterest", 0)) * mark_px
            day_vol    = float(ctx.get("dayNtlVlm", 0))
        except (TypeError, ValueError):
            continue

        # extreme funding — flag both directions
        if abs(funding) >= EXTREME_FUNDING and open_int > 5_000_000:
            direction = "SHORT_CROWDED" if funding < 0 else "LONG_CROWDED"
            if emit("hyperliquid", "extreme_funding", name, "hyperliquid",
                    size_usd=open_int, direction=direction,
                    dedup_key=f"hl_fund:{name}:{'-' if funding<0 else '+'}",
                    note=f"funding={funding*100:.3f}%/1h  OI=${open_int/1e6:.1f}M  vol24h=${day_vol/1e6:.1f}M",
                    url=f"https://app.hyperliquid.xyz/trade/{name}",
                    raw={"funding": funding, "oi": open_int, "vol24h": day_vol}):
                hits += 1

        # big open interest — always emit top-of-mind coins with $50M+ OI
        if open_int > 50_000_000:
            emit("hyperliquid", "large_oi", name, "hyperliquid",
                 size_usd=open_int,
                 dedup_key=f"hl_oi:{name}",
                 note=f"OI=${open_int/1e6:.1f}M  vol24h=${day_vol/1e6:.1f}M  funding={funding*100:.3f}%",
                 url=f"https://app.hyperliquid.xyz/trade/{name}",
                 raw={"oi": open_int})
    return hits


def scan_whale_trades() -> int:
    """Fetch recent trades on liquid coins, flag single trades ≥ threshold."""
    hits = 0
    for coin in WHALE_TRADES_COINS:
        try:
            trades = _post({"type": "recentTrades", "coin": coin})
        except Exception:
            continue
        if not isinstance(trades, list):
            continue
        for t in trades[:100]:
            try:
                px   = float(t.get("px", 0))
                sz   = float(t.get("sz", 0))
                usd  = px * sz
                side = t.get("side", "?")  # "A"=Ask/sell, "B"=Bid/buy
                tid  = t.get("tid") or t.get("hash", "")
            except (TypeError, ValueError):
                continue
            if usd < LARGE_TRADE_USD:
                continue
            direction = "BUY" if side == "B" else "SELL"
            emit("hyperliquid", "whale_trade", coin, "hyperliquid",
                 size_usd=usd, direction=direction,
                 dedup_key=f"hl_trd:{coin}:{tid}",
                 note=f"{direction} ${usd/1e3:.0f}k @ {px}",
                 url=f"https://app.hyperliquid.xyz/trade/{coin}",
                 raw={"px": px, "sz": sz, "side": side})
            hits += 1
    return hits


def tick() -> None:
    n1 = scan_funding_and_oi()
    n2 = scan_whale_trades()
    log_tick("hyperliquid", f"funding_hits={n1} whale_trades={n2}")


if __name__ == "__main__":
    tick()
