"""Shared primitives for prop-desk-lite strategies.

Data model, vol calc, cost model, cluster map, universe.
Everything is stateless helpers — strategies own their own state where needed.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal

ROOT      = Path(__file__).resolve().parent.parent
PRICE_DIR = ROOT / "state" / "prices"

# ── Universe & metadata ────────────────────────────────────────────────
# CoinGecko id, cluster, is_major_liquidity (>=$500M 24h)
UNIVERSE: dict[str, tuple[str, str, bool]] = {
    # (cg_id, cluster, is_major_liq)
    "BTC":    ("bitcoin",                    "majors",  True),
    "ETH":    ("ethereum",                   "majors",  True),
    "SOL":    ("solana",                     "majors",  True),
    "XRP":    ("ripple",                     "l1s",     True),
    "DOGE":   ("dogecoin",                   "memes",   True),
    "AVAX":   ("avalanche-2",                "l1s",     False),
    "LTC":    ("litecoin",                   "l1s",     False),
    "LINK":   ("chainlink",                  "other",   False),
    "UNI":    ("uniswap",                    "defi",    False),
    "AAVE":   ("aave",                       "defi",    False),
    "BCH":    ("bitcoin-cash",               "l1s",     False),
    "DOT":    ("polkadot",                   "l1s",     False),
    "MKR":    ("maker",                      "defi",    False),
    "YFI":    ("yearn-finance",              "defi",    False),
    "SHIB":   ("shiba-inu",                  "memes",   False),
    "BAT":    ("basic-attention-token",      "other",   False),
    "GRT":    ("the-graph",                  "aidepin", False),
    "ADA":    ("cardano",                    "l1s",     False),
    "ARB":    ("arbitrum",                   "other",   False),
    "BONK":   ("bonk",                       "memes",   False),
    "CRV":    ("curve-dao-token",            "defi",    False),
    "FIL":    ("filecoin",                   "other",   False),
    "HYPE":   ("hyperliquid",                "other",   False),
    "LDO":    ("lido-dao",                   "defi",    False),
    "ONDO":   ("ondo-finance",               "other",   False),
    "PAXG":   ("pax-gold",                   "other",   False),
    "PEPE":   ("pepe",                       "memes",   False),
    "POL":    ("polygon-ecosystem-token",    "l1s",     False),
    "RENDER": ("render-token",               "aidepin", False),
    "SKY":    ("sky",                        "defi",    False),
    "SUSHI":  ("sushi",                      "defi",    False),
    "TRUMP":  ("official-trump",             "memes",   False),
    "WIF":    ("dogwifcoin",                 "memes",   False),
    "XTZ":    ("tezos",                      "l1s",     False),
}

CLUSTERS = ("majors", "l1s", "memes", "defi", "aidepin", "other")


# ── Cost model ─────────────────────────────────────────────────────────
# Alpaca crypto: 0.15% taker fee both sides + ~0.05% spread average = 0.35% roundtrip.
# Real desks measure per-symbol; we approximate.
ROUNDTRIP_COST_PCT = 0.35


# ── Signal dataclass ───────────────────────────────────────────────────
Action = Literal["BUY", "SELL", "HOLD"]


@dataclass
class Signal:
    sym:              str
    strategy:         str
    action:           Action
    score:            float                 # strategy-specific conviction 0..∞
    sigma:            float                 # move size in standard deviations
    expected_move:    float                 # % move we're targeting
    px:               float                 # entry price hint
    note:             str
    raw:              dict = field(default_factory=dict)

    def dict(self) -> dict:
        return asdict(self)


# ── Series & vol helpers ───────────────────────────────────────────────
def load_series(sym: str, n: int = 120) -> list[dict]:
    """Load last N rows of state/prices/<SYM>.jsonl.

    The momentum source is already writing per-token 30s samples there,
    so we can piggy-back on that file — no separate collector needed."""
    p = PRICE_DIR / f"{sym}.jsonl"
    if not p.exists():
        return []
    try:
        return [json.loads(ln) for ln in p.read_text().splitlines()[-n:]]
    except Exception:
        return []


def price_at_offset(series: list[dict], now_ts: int, offset_s: int, tol_s: int = 45) -> float | None:
    target = now_ts - offset_s
    best, best_d = None, tol_s
    for r in series:
        d = abs(r["ts"] - target)
        if d <= best_d:
            best, best_d = r, d
    return best["price"] if best else None


def pct(a: float, b: float) -> float:
    if not b:
        return 0.0
    return (a - b) / b * 100.0


def realized_vol_pct(series: list[dict], horizon_s: int = 3600) -> float:
    """Typical percent move over `horizon_s` seconds.

    Prior version returned per-step std, which is ~15× smaller than hourly vol at
    15s cadence and caused every strategy target to be too small to clear the
    cost gate. This scales per-step log-return std to the requested horizon via
    the standard √T rule.

    Example: BTC per-15s std ≈ 0.02% → hourly vol ≈ 0.02% × √240 ≈ 0.31%.
    """
    if len(series) < 3:
        return 0.0
    prices = [r["price"] for r in series if r["price"] > 0]
    if len(prices) < 3:
        return 0.0
    rets = []
    for i in range(1, len(prices)):
        if prices[i-1] > 0:
            rets.append(math.log(prices[i] / prices[i-1]))
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    per_step_std = math.sqrt(var)

    step_s = (series[-1]["ts"] - series[0]["ts"]) / max(1, len(series) - 1)
    if step_s <= 0:
        return per_step_std * 100.0
    scale = math.sqrt(horizon_s / step_s)
    return per_step_std * scale * 100.0    # typical horizon move in %


def compute_rsi(series: list[dict], period_s: int = 15 * 60) -> float:
    """RSI over rolling period. Uses close-to-close returns from series."""
    if len(series) < 3:
        return 50.0
    cutoff = series[-1]["ts"] - period_s
    xs = [r["price"] for r in series if r["ts"] >= cutoff and r["price"] > 0]
    if len(xs) < 3:
        return 50.0
    gains = losses = 0.0
    for i in range(1, len(xs)):
        d = xs[i] - xs[i-1]
        if d > 0: gains += d
        else:     losses -= d
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def compute_atr_pct(series: list[dict], window_s: int = 15 * 60) -> float:
    """ATR% — average absolute step return over the window, as %."""
    if len(series) < 3:
        return 0.0
    cutoff = series[-1]["ts"] - window_s
    prices = [r["price"] for r in series if r["ts"] >= cutoff and r["price"] > 0]
    if len(prices) < 3:
        return 0.0
    steps = [abs(pct(prices[i], prices[i-1])) for i in range(1, len(prices))]
    return sum(steps) / len(steps)


def high_over(series: list[dict], window_s: int) -> float:
    """Max price over last N seconds (excluding current). Used for breakout."""
    if len(series) < 2:
        return 0.0
    cutoff  = series[-1]["ts"] - window_s
    prices  = [r["price"] for r in series[:-1] if r["ts"] >= cutoff]
    return max(prices) if prices else 0.0


def cluster_of(sym: str) -> str:
    return UNIVERSE.get(sym, ("", "other", False))[1]


def is_major_liq(sym: str) -> bool:
    return UNIVERSE.get(sym, ("", "other", False))[2]
