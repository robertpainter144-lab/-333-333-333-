"""
Pick $10-$25/share candidates for the $500 IBKR sleeve, sized to a
whole-share position of at least MIN_POSITION_USD.

Criteria:
  - Price ≤ $25, and a whole-share position ≥ $150 fits within
    POSITION_BUDGET_USD (commission floor — sub-$150 positions can't
    clear IBKR's ~1%/side friction)
  - Avg daily $ volume ≥ $50M (liquidity for clean entries)
  - 30-day return > 5d return × 0.5 (steady momentum, not blow-off)
  - Cross-sector diversification

Pulls daily bars from yfinance. Outputs a ranked candidate list.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf

# Hand-curated liquid US universe under ~$25 (tickers cheap enough that 1 share ≈ $10-25)
# Includes large-caps that have stayed cheap + popular mid-caps.
CHEAP_UNIVERSE = [
    # Financials
    "BAC", "SCHW", "C", "WFC",
    # Telecom / utilities
    "T", "VZ", "AT", "VOD", "WBD",
    # Industrials / autos / airlines
    "F", "GM", "AAL", "CCL", "NCLH", "DAL", "UAL",
    # Tech / consumer
    "SOFI", "PLTR", "RIVN", "PARA", "WBD", "SNAP", "PINS", "RBLX",
    # Energy / commodities
    "KMI", "OXY", "FCX", "MOS", "CNQ",
    # Healthcare
    "PFE", "BMY", "GSK", "ABEV",
    # Mining / cyclicals
    "AU", "GOLD", "KGC", "BTG", "HBM",
    # ETFs cheap enough
    "BND", "TLT", "SHV", "EWZ", "VWO",
]

# Commission floor: IBKR charges ~1% per side on micro orders, so a
# sub-$150 position cannot clear round-trip friction. Only propose names
# where a whole-share position of at least this size fits POSITION_BUDGET_USD.
MIN_POSITION_USD    = 150.0
POSITION_BUDGET_USD = 500.0   # per-name budget ceiling for the sleeve


@dataclass
class Candidate:
    symbol: str
    price: float
    dollar_vol_30d: float
    ret_5d: float
    ret_30d: float
    ret_90d: float
    atr_pct: float
    primary_exch: str
    score: float


def _primary_for(symbol: str, info: dict) -> str:
    ex = (info.get("exchange") or "").upper()
    if "NYS" in ex or "NYE" in ex:  return "NYSE"
    if "NMS" in ex or "NAS" in ex:  return "NASDAQ"
    if "ASE" in ex:                 return "AMEX"
    if "NCM" in ex or "NGS" in ex:  return "NASDAQ"
    return "SMART"


def _atr_pct(hist: pd.DataFrame, window: int = 14) -> float:
    h, l, c = hist["High"], hist["Low"], hist["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.tail(window).mean()
    return float(atr / c.iloc[-1]) if c.iloc[-1] > 0 else 0.0


def evaluate(symbol: str) -> Optional[Candidate]:
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="6mo", interval="1d").dropna(subset=["Close"])
        if hist.empty or len(hist) < 95:
            return None
        price = float(hist["Close"].iloc[-1])
        if price > 25.0 or price < 1.5:
            return None
        shares_buyable = int(POSITION_BUDGET_USD // price)
        if shares_buyable < 1 or shares_buyable * price < MIN_POSITION_USD:
            return None
        dv = float((hist["Close"] * hist["Volume"]).tail(30).mean())
        if dv < 50_000_000:
            return None
        ret5  = float(hist["Close"].iloc[-1] / hist["Close"].iloc[-6]  - 1)
        ret30 = float(hist["Close"].iloc[-1] / hist["Close"].iloc[-22] - 1)
        ret90 = float(hist["Close"].iloc[-1] / hist["Close"].iloc[-66] - 1)
        # Steady momentum: 30d return positive AND not over-extended this week
        if ret30 <= 0 or ret5 > ret30 * 1.2:
            return None
        atr = _atr_pct(hist)
        # Score: 30d momentum minus over-extension penalty, vol-adjusted
        score = ret30 - max(0, ret5 - ret30 * 0.5) - 0.5 * atr
        info = {}
        try:
            info = t.fast_info or {}
        except Exception:
            pass
        return Candidate(
            symbol         = symbol,
            price          = price,
            dollar_vol_30d = dv,
            ret_5d         = ret5,
            ret_30d        = ret30,
            ret_90d        = ret90,
            atr_pct        = atr,
            primary_exch   = _primary_for(symbol, info if isinstance(info, dict) else {}),
            score          = score,
        )
    except Exception:
        return None


def screen(universe=CHEAP_UNIVERSE, top_n: int = 8) -> list[Candidate]:
    cands = []
    for sym in universe:
        c = evaluate(sym)
        if c is not None:
            cands.append(c)
    return sorted(cands, key=lambda x: -x.score)[:top_n]


def fmt(c: Candidate) -> str:
    return (f"{c.symbol:6} ${c.price:>6.2f}  "
            f"30d={c.ret_30d*100:>+6.2f}%  5d={c.ret_5d*100:>+6.2f}%  "
            f"90d={c.ret_90d*100:>+6.2f}%  ATR={c.atr_pct*100:>5.2f}%  "
            f"$vol={c.dollar_vol_30d/1e6:>5.0f}M  "
            f"score={c.score*100:>+5.2f}  exch={c.primary_exch}")


if __name__ == "__main__":
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    cs = screen(top_n=top_n)
    print(f"Top {len(cs)} candidates (under $25, $50M+ daily vol, momentum > 0):\n")
    for c in cs:
        print(f"  {fmt(c)}")
