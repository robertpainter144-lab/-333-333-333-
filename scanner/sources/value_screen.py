"""Value Screen — Robert's dad's simplified value-investing rules.

Rules (verbatim from the source):
  - P/E ratio below 30
  - Price over $1
  - Current volume over 100,000 shares
  - Country USA
  - Stocks/ETFs
  - Rank by percent change × market cap
  - Separate by industry
  - Look for stuff that's been consistently growing but might be correcting right now

Ticker universe: S&P 500 constituents (~500 names, keeps yfinance calls under 10min).
Cadence: once every 6 hours. Persisted picks -> state/value_screen_state.json.

Fits alongside QC/Insider/Patent/Whale in the ensemble mirror. If sustained
edge shows up in the strategy_governor, weight goes to 20% each (5-way blend).
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

STATE = STATEDIR / "value_screen_state.json"
LEDGER = STATEDIR / "value_screen_ledger.jsonl"

# Small starting universe — S&P 100 mega-caps. Big enough for coverage per
# industry, small enough for a single-tick scan in ~2 min.
UNIVERSE = [
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "META", "TSLA", "BRK-B",
    "LLY", "AVGO", "JPM", "V", "UNH", "XOM", "MA", "PG", "JNJ", "HD",
    "COST", "ABBV", "WMT", "MRK", "NFLX", "BAC", "CVX", "KO", "AMD",
    "ADBE", "PEP", "TMO", "CSCO", "CRM", "MCD", "ABT", "ORCL", "ACN",
    "LIN", "DHR", "TXN", "DIS", "WFC", "VZ", "PM", "COP", "NEE", "INTC",
    "IBM", "CMCSA", "PFE", "AMGN", "UNP", "LOW", "SPGI", "RTX", "HON",
    "BX", "CAT", "GS", "AXP", "T", "BLK", "QCOM", "AMT", "BKNG", "PLD",
    "ISRG", "NOW", "SBUX", "MS", "MDT", "SYK", "GILD", "TJX", "MO",
    "REGN", "LMT", "VRTX", "PGR", "SCHW", "C", "ELV", "MDLZ", "ETN",
    "SO", "ADP", "MU", "BSX", "DE", "ADI", "CB", "MMC", "BMY", "ZTS",
    "GE", "DUK", "CI", "TMUS", "CVS", "AMAT", "PANW",
]

# Filter thresholds (dad's rules)
MAX_PE            = 30.0
MIN_PRICE         = 1.0
MIN_AVG_VOLUME    = 100_000
COUNTRY           = "United States"

# Growth/correction detection
MIN_REVENUE_GROWTH = 0.0     # positive YoY revenue growth (loosened from +5% since S&P 100 mega-caps often at zero)
MIN_EPS_GROWTH     = -0.5    # allow moderate EPS declines (correction candidates)
CORRECTION_MIN     = -0.25   # bonus for currently 2-25% below 20d SMA (broadened)
CORRECTION_MAX     = -0.005
TREND_INTACT       = 0.85    # price still ≥ 85% of 200-day SMA (broadened)


def _get_info(ticker: str) -> dict | None:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        return t.info
    except Exception:
        return None


def _get_history(ticker: str, period: str = "1y") -> list[float]:
    try:
        import yfinance as yf
        h = yf.Ticker(ticker).history(period=period, interval="1d")
        return [float(x) for x in h["Close"].tolist() if x == x]
    except Exception:
        return []


def _sma(prices: list[float], n: int) -> float | None:
    if len(prices) < n:
        return None
    return sum(prices[-n:]) / n


def screen_one(ticker: str) -> dict | None:
    """Apply dad's screen. Return dict of stats if pass; None if filtered."""
    info = _get_info(ticker)
    if not info:
        return None

    price     = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
    pe        = info.get("trailingPE") or info.get("forwardPE") or 999.0
    volume    = info.get("averageVolume") or 0
    country   = info.get("country", "")
    industry  = info.get("industry", "Unknown")
    mktcap    = info.get("marketCap") or 0
    prev      = info.get("previousClose") or price
    pct_chg   = ((price - prev) / prev * 100) if prev > 0 else 0

    # ── Hard filters (dad's rules) ──────────────────────────────────
    if price < MIN_PRICE:                                return None
    if pe <= 0 or pe > MAX_PE:                            return None
    if volume < MIN_AVG_VOLUME:                           return None
    if country and country != COUNTRY:                    return None

    # ── Growth / correction score (all soft — used for ranking, not filtering) ──
    rev_growth = info.get("revenueGrowth") or 0
    eps_growth = info.get("earningsGrowth") or 0
    consistent_grow = (rev_growth >= MIN_REVENUE_GROWTH and eps_growth >= MIN_EPS_GROWTH)

    hist = _get_history(ticker, period="6mo")
    sma20  = _sma(hist, 20) or price
    sma200 = _sma(hist, 200) or price
    pct_from_sma20  = (price - sma20) / sma20 if sma20 else 0
    pct_from_sma200 = (price - sma200) / sma200 if sma200 else 0
    in_correction = (CORRECTION_MIN <= pct_from_sma20 <= CORRECTION_MAX)
    trend_intact  = (pct_from_sma200 >= (TREND_INTACT - 1.0))

    # ── Composite score: dad's rank criterion × correction bonus ────
    # Ranked by |pct_change| × log(marketCap), per-industry, correction-only.
    import math
    base = abs(pct_chg) * math.log(max(mktcap, 1))
    grow_bonus  = 1.5 if consistent_grow else 1.0
    corr_bonus  = 1.5 if in_correction else 1.0
    trend_bonus = 1.2 if trend_intact else 0.8
    score = base * grow_bonus * corr_bonus * trend_bonus

    return {
        "ticker":     ticker,
        "price":      round(price, 2),
        "pe":         round(pe, 2),
        "volume":     int(volume),
        "market_cap": int(mktcap),
        "pct_chg":    round(pct_chg, 3),
        "industry":   industry,
        "sma_20":     round(sma20, 2),
        "sma_200":    round(sma200, 2),
        "pct_from_sma20":  round(pct_from_sma20 * 100, 2),
        "pct_from_sma200": round(pct_from_sma200 * 100, 2),
        "rev_growth": round(rev_growth * 100, 2) if rev_growth else 0,
        "eps_growth": round(eps_growth * 100, 2) if eps_growth else 0,
        "consistent_grow": consistent_grow,
        "in_correction":   in_correction,
        "trend_intact":    trend_intact,
        "score":      round(score, 2),
    }


def tick() -> None:
    now = int(time.time())
    passing: list[dict] = []
    scanned = 0
    for t in UNIVERSE:
        try:
            r = screen_one(t)
            scanned += 1
            if r:
                passing.append(r)
        except Exception as e:
            emit_live("value_screen", f"[err] {t}: {e!r}")

    # Group by industry, take top-3 per industry by score
    by_industry: dict[str, list[dict]] = defaultdict(list)
    for r in passing:
        by_industry[r["industry"]].append(r)
    for k in by_industry:
        by_industry[k].sort(key=lambda x: x["score"], reverse=True)
        by_industry[k] = by_industry[k][:3]

    # Overall top-N across all industries for the mirror
    top_overall = sorted(passing, key=lambda x: x["score"], reverse=True)[:10]

    state = {
        "ts":       now,
        "scanned":  scanned,
        "passing":  len(passing),
        "by_industry": {k: v for k, v in by_industry.items()},
        "top_10":   top_overall,
        "started_ts": now if not STATE.exists() else
                      json.loads(STATE.read_text()).get("started_ts", now),
    }
    try:
        # Preserve started_ts across runs
        if STATE.exists():
            prev = json.loads(STATE.read_text())
            state["started_ts"] = prev.get("started_ts", now)
    except Exception:
        pass
    STATE.write_text(json.dumps(state, indent=2, default=float))

    emit_live("value_screen",
              f"[value] scanned={scanned}  passing={len(passing)}  "
              f"top={','.join(r['ticker'] for r in top_overall[:5])}")
    log_tick("value_screen",
             f"scanned={scanned} passing={len(passing)} industries={len(by_industry)}")


if __name__ == "__main__":
    tick()
