#!/usr/bin/env python3
"""
Multi-Strategy Market Scanner
==============================
Every morning (or on demand), scans ~200 symbols across the whole market,
scores each one against 5 distinct strategies, ranks by expected daily P&L,
and surfaces the best (symbol, strategy) combination to trade today.

Strategies:
  1. trend_momentum    EMA cross + ADX + dip-pyramid  (what we built)
  2. gap_continuation  Large pre-mkt gap + vol surge → ride first hour
  3. vwap_bounce       Price rejects VWAP in trending market
  4. range_breakout    N-bar consolidation breaks out with ATR expansion
  5. relative_strength Within-sector leaders vs laggards (long/short pair)

Scoring inputs per symbol:
  • Gap %          pre-market open vs prior close
  • ATR %          ATR / price  (normalised volatility)
  • Vol ratio      today's pre-mkt vol vs 20-day avg
  • ADX / PDI-NDI  trend strength + direction
  • Price vs VWAP  where is price relative to fair value
  • EMA slope      rate of change of trend
  • Regime         vol rank (low/mid/high) from rolling percentile

Output: ranked table of top opportunities with suggested entry, stop, target,
        expected daily P&L scaled to your account size.

Usage:
  python3 market_scanner.py                  # scan + show top picks
  python3 market_scanner.py --backtest 30    # replay last 30 days, show best daily picks
  python3 market_scanner.py --live           # scan every 5 min during market hours
"""

import sys
import time
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# ── Alpaca ────────────────────────────────────────────────────────────────────
KEY    = "CK4HXKGM3HWRKLXWRU3CDLB6LX"
SECRET = "7i1mhApwTyzqWprLyvGvbzuDJbNSquMRTCnoiXfJSEWk"
DATA   = "https://data.sandbox.alpaca.markets"

def hdrs():
    return {"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SECRET}

# ── Universe — 200 symbols across all market sectors ─────────────────────────
UNIVERSE = {
    # Leveraged / high-beta ETFs  (best for momentum strategies)
    "lev_etf": ["SOXL","TQQQ","UPRO","LABU","FNGU","TECL","DPST","NAIL",
                "UDOW","TNA","SPXL","HIBL","CURE","WANT","DFEN"],

    # Mega-cap tech  (liquid, daily catalyst driven)
    "mega_tech": ["NVDA","AAPL","MSFT","AMZN","META","GOOGL","TSLA",
                  "AVGO","AMD","QCOM","MU","AMAT","LRCX","KLAC","ASML"],

    # Growth / momentum
    "growth": ["PLTR","CRWD","DDOG","NET","SNOW","COIN","APP","MSTR",
               "SMCI","ARM","NFLX","CRM","NOW","UBER","LYFT"],

    # Sector ETFs  (cleaner signals, less noise)
    "sector_etf": ["XLK","XLE","XLF","XLV","XLI","XLY","XLC","XLB",
                   "XLP","XLRE","XLU","SMH","SOXX","IBB","XBI"],

    # Broad market ETFs  (market-regime anchors)
    "broad_etf": ["SPY","QQQ","IWM","DIA","VTI","MDY","EEM","GLD",
                  "SLV","USO","TLT","HYG","LQD","VXX","UVXY"],

    # High-ATR singles  (large movers)
    "high_atr": ["MARA","RIOT","HOOD","SOFI","RIVN","LCID","NIO","XPEV",
                 "BIDU","SE","GRAB","LAZR","CHPT","BLNK","SPCE"],

    # Financials + energy (sector rotation plays)
    "fin_energy": ["JPM","GS","MS","BAC","C","XOM","CVX","OXY","SLB","HAL",
                   "KMI","WMB","DVN","FANG","MRO"],

    # Healthcare / biotech
    "health": ["LLY","UNH","JNJ","ABBV","MRK","PFE","AMGN","GILD",
               "BIIB","MRNA","BNTX","NVAX","VRTX","REGN","BMY"],
}

ALL_SYMBOLS = [s for group in UNIVERSE.values() for s in group]

# ── Strategy definitions ──────────────────────────────────────────────────────
#
# Each strategy scorer returns:
#   score      0-100  (higher = better setup today)
#   direction  "long" | "short" | None
#   params     dict of suggested trail/ema/adds etc.
#   reason     short string explaining the score

def score_trend_momentum(sym_data: dict) -> dict:
    """
    EMA cross + ADX regime + dip-pyramid.
    Best when: high ADX, price above/below EMA, vol rank high.
    """
    adx   = sym_data.get("adx", 0)
    pdi   = sym_data.get("pdi", 0)
    ndi   = sym_data.get("ndi", 0)
    vr    = sym_data.get("vol_rank", 0.5)
    slope = sym_data.get("ema_slope", 0)
    gap   = abs(sym_data.get("gap_pct", 0))
    close = sym_data.get("close", 0)
    ema   = sym_data.get("ema25", close)
    atr   = sym_data.get("atr", 0)
    atr_pct = atr / close if close else 0

    score = 0
    direction = None

    # ADX component (0-40 pts)
    if adx > 50:   score += 40
    elif adx > 35: score += 30
    elif adx > 25: score += 15
    else:          score += 5

    # Low vol regime penalty — empirically the worst regime for this strategy
    if vr < 0.25:        score -= 30
    elif vr < 0.35:      score -= 15

    # Vol rank (0-25 pts) — strategy thrives in high vol
    score += int(vr * 25)

    # EMA alignment (0-20 pts)
    if close > ema * 1.002 and pdi > ndi:
        direction = "long"; score += 20
    elif close < ema * 0.998 and ndi > pdi:
        direction = "short"; score += 20
    elif close > ema:
        direction = "long"; score += 10

    # Slope boost — ripping trend (0-10 pts)
    if abs(slope) > 0.003: score += 10
    elif abs(slope) > 0.001: score += 5

    # Sector momentum boost: if sector is also moving, conviction is higher
    sector_mom = sym_data.get("sector_avg_mom1d", 0)
    if direction == "long"  and sector_mom > 0.005: score += 8
    elif direction == "short" and sector_mom < -0.005: score += 8

    # Gap penalty — wide gap increases whipsaw risk
    if gap > 0.06:   score -= 20
    elif gap > 0.03: score -= 8

    # ATR% sanity — need meaningful moves
    if atr_pct < 0.002: score -= 15  # too calm to trade

    # Params based on regime
    if vr > 0.67:
        params = dict(trail=3.5, ema=28, dip=0.6, max_adds=2, entry_end="12:30")
    elif vr > 0.33:
        params = dict(trail=3.0, ema=25, dip=0.5, max_adds=2, entry_end="11:00")
    else:
        params = dict(trail=2.5, ema=20, dip=0.35, max_adds=3, entry_end="13:00")

    reason = (f"ADX={adx:.0f} vr={vr:.2f} slope={slope*100:.3f}%/bar "
              f"gap={gap*100:.1f}%")
    return dict(score=max(0, min(100, score)), direction=direction,
                params=params, reason=reason, strategy="trend_momentum")


def score_gap_continuation(sym_data: dict) -> dict:
    """
    Large pre-mkt gap (2-8%) + vol surge → first-hour continuation.
    Best when: clear catalyst, gap aligns with primary trend, volume confirming.
    """
    gap_pct   = sym_data.get("gap_pct", 0)
    vol_r     = sym_data.get("vol_ratio", 1)
    atr       = sym_data.get("atr", 0)
    close     = sym_data.get("close", 1)
    atr_pct   = atr / close
    adx       = sym_data.get("adx", 0)
    trend_dir = sym_data.get("trend_dir", "neutral")

    score = 0
    direction = "long" if gap_pct > 0 else "short"

    # Hard volume floor — no volume means no catalyst, no trade
    if vol_r < 0.8:
        return dict(score=0, direction=None, params={},
                    reason=f"vol_ratio={vol_r:.1f}× insufficient",
                    strategy="gap_continuation")

    gap_abs = abs(gap_pct)

    # Ideal gap range 2-6%
    if 0.02 <= gap_abs <= 0.04:   score += 45
    elif 0.04 < gap_abs <= 0.06: score += 35
    elif 0.06 < gap_abs <= 0.08: score += 20
    elif gap_abs > 0.08:          score += 5
    else:                         score += 0   # gap too small

    # Trend alignment — gap into the wind is a fade, not continuation
    # Strong penalty when ADX > 30 and gap opposes established trend
    if adx > 30:
        if direction == "long"  and trend_dir == "down": score -= 30
        if direction == "short" and trend_dir == "up":   score -= 30
    # Bonus when gap confirms trend
    if adx > 25:
        if direction == "long"  and trend_dir == "up":   score += 15
        if direction == "short" and trend_dir == "down": score += 15

    # Volume confirmation (0-30 pts)
    if vol_r > 3.0:   score += 30
    elif vol_r > 2.0: score += 20
    elif vol_r > 1.5: score += 10
    elif vol_r > 1.0: score += 5

    # ADX shows momentum (0-10 pts)
    if adx > 40: score += 10
    elif adx > 25: score += 5

    # Volatility needed
    if atr_pct > 0.03:    score += 10
    elif atr_pct > 0.015: score += 5

    params = dict(trail=2.0, ema=9, dip=0.5, max_adds=1,
                  entry_end="10:30", hold_max_mins=90)
    reason = (f"gap={gap_pct*100:+.1f}% vol={vol_r:.1f}× trend={trend_dir} "
              f"ADX={adx:.0f}")
    return dict(score=max(0, min(100, score)), direction=direction,
                params=params, reason=reason, strategy="gap_continuation")


def score_vwap_bounce(sym_data: dict) -> dict:
    """
    Price pulls back to VWAP in a trending market → enter on VWAP touch.
    Best when: moderate ADX, price previously away from VWAP, retest incoming.
    """
    vwap    = sym_data.get("vwap", 0)
    close   = sym_data.get("close", 0)
    adx     = sym_data.get("adx", 0)
    pdi     = sym_data.get("pdi", 0)
    ndi     = sym_data.get("ndi", 0)
    vr      = sym_data.get("vol_rank", 0.5)
    atr     = sym_data.get("atr", 0)

    if not vwap or not close or not atr:
        return dict(score=0, direction=None, params={}, reason="no VWAP", strategy="vwap_bounce")

    dist_pct = (close - vwap) / vwap   # positive = above VWAP

    score = 0
    direction = None

    # Setup: price is close to VWAP (within 0.5-1.5 ATR) but trend intact
    dist_atr = abs(close - vwap) / atr if atr else 99
    if 0.3 <= dist_atr <= 1.5:
        score += 40   # sweet spot — near enough for bounce, not at it yet
    elif dist_atr < 0.3:
        score += 20   # already AT vwap, might be chopping
    else:
        score += 5    # too far from VWAP

    # Trend direction determines which side to lean
    if adx > 30 and pdi > ndi and close > vwap:
        direction = "long";  score += 25  # bull trend, price above VWAP → buy dip to VWAP
    elif adx > 30 and ndi > pdi and close < vwap:
        direction = "short"; score += 25  # bear trend, price below VWAP → short bounce to VWAP
    elif adx > 20:
        direction = "long" if pdi > ndi else "short"; score += 10

    # Moderate vol is ideal (extreme vol = VWAP unreliable)
    if 0.30 < vr < 0.70: score += 20
    elif vr < 0.30:       score += 10
    else:                 score += 5    # high vol → VWAP drifts fast

    # Gap penalty — gappy open makes VWAP anchor unreliable
    if abs(sym_data.get("gap_pct", 0)) > 0.03: score -= 15

    params = dict(trail=2.0, ema=25, dip=0.3, max_adds=2, entry_end="14:00")
    reason = (f"dist_vwap={dist_atr:.1f}×ATR ADX={adx:.0f} vr={vr:.2f} "
              f"above={'yes' if close > vwap else 'no'}")
    return dict(score=max(0, min(100, score)), direction=direction,
                params=params, reason=reason, strategy="vwap_bounce")


def score_range_breakout(sym_data: dict) -> dict:
    """
    N-bar consolidation → price breaks above range high (or below low)
    with ATR expansion and volume. Captures the compression-then-explosion pattern.
    Best when: low-vol regime transitioning to high, tight range breaks out.
    """
    high20   = sym_data.get("high_20bar", 0)
    low20    = sym_data.get("low_20bar", 0)
    close    = sym_data.get("close", 0)
    atr      = sym_data.get("atr", 0)
    atr_prev = sym_data.get("atr_5bar_ago", atr)
    vol_r    = sym_data.get("vol_ratio", 1)
    vr       = sym_data.get("vol_rank", 0.5)
    adx      = sym_data.get("adx", 0)

    if not high20 or not close or not atr:
        return dict(score=0, direction=None, params={}, reason="insufficient data",
                    strategy="range_breakout")

    range_size  = (high20 - low20) / close if close else 0
    atr_expand  = atr / atr_prev if atr_prev else 1
    dist_to_top = (close - high20) / atr if atr else 0   # negative = below top
    dist_to_bot = (low20 - close) / atr if atr else 0    # negative = above bot

    score = 0
    direction = None

    # Price breaking out of range (0-35 pts)
    if -0.1 <= dist_to_top <= 0.5:        # price just above range high
        direction = "long";  score += 35
    elif 0.5 < dist_to_top <= 1.5:        # price somewhat above — still valid
        direction = "long";  score += 20
    elif -0.1 <= dist_to_bot <= 0.5:      # price just below range low
        direction = "short"; score += 35
    elif 0.5 < dist_to_bot <= 1.5:
        direction = "short"; score += 20

    # ATR expansion = volatility contracting → expanding (0-25 pts)
    if atr_expand > 1.3:   score += 25
    elif atr_expand > 1.1: score += 15
    elif atr_expand < 0.9: score -= 10   # still compressing, wait

    # Volume confirming (0-25 pts)
    if vol_r > 2.5:   score += 25
    elif vol_r > 1.5: score += 15
    elif vol_r > 1.0: score += 5

    # Tight range = more coiled spring (0-15 pts)
    if range_size < 0.02:   score += 15
    elif range_size < 0.04: score += 8

    # Low ADX before breakout is ideal (coiling)
    if adx < 20: score += 10
    elif adx < 30: score += 5

    params = dict(trail=2.5, ema=20, dip=0.4, max_adds=2, entry_end="13:00")
    reason = (f"range={range_size*100:.1f}% atr_exp={atr_expand:.2f}× "
              f"vol={vol_r:.1f}× dist_top={dist_to_top:.1f}×ATR")
    return dict(score=max(0, min(100, score)), direction=direction,
                params=params, reason=reason, strategy="range_breakout")


def score_relative_strength(sym: str, sym_data: dict, sector_scores: dict) -> dict:
    """
    Within-sector relative strength: strongest vs weakest.
    Goes long the leader, short the laggard.
    Best when: clear sector rotation underway, spread > 1% within sector.
    """
    sector  = sym_data.get("sector", None)
    rs_rank = sym_data.get("rs_rank_in_sector", 0.5)   # 0=weakest, 1=strongest
    sector_adx = sector_scores.get(sector, {}).get("adx", 0) if sector else 0
    mom5d   = sym_data.get("mom_5day", 0)   # 5-day return
    vol_r   = sym_data.get("vol_ratio", 1)

    score = 0
    direction = None

    # Extreme RS rank = clear leader/laggard
    if rs_rank > 0.85:
        direction = "long";  score += 40
    elif rs_rank > 0.70:
        direction = "long";  score += 25
    elif rs_rank < 0.15:
        direction = "short"; score += 40
    elif rs_rank < 0.30:
        direction = "short"; score += 25

    # Sector trending (0-25 pts)
    if sector_adx > 40:   score += 25
    elif sector_adx > 25: score += 15

    # 5-day momentum confirmation
    if direction == "long"  and mom5d > 0.03:  score += 20
    elif direction == "short" and mom5d < -0.03: score += 20
    elif direction and abs(mom5d) > 0.01:       score += 8

    # Volume interest
    if vol_r > 1.5: score += 15
    elif vol_r > 1.0: score += 7

    params = dict(trail=3.0, ema=25, dip=0.5, max_adds=1, entry_end="12:00")
    reason = (f"rs_rank={rs_rank:.2f} sector={sector} "
              f"mom5d={mom5d*100:+.1f}% vol={vol_r:.1f}×")
    return dict(score=max(0, min(100, score)), direction=direction,
                params=params, reason=reason, strategy="relative_strength")


# ── Expected daily P&L estimator ─────────────────────────────────────────────
# Historical R-multiples per strategy, calibrated to our backtests
# trend_momentum: dip-pyramid with ADX filter → avg 3.5R wins (measured on SOXL/AMD/ARM)
# gap_continuation: first-hour gap play → smaller wins, but higher WR when aligned
STRAT_EDGE = {
    "trend_momentum":    dict(wr=0.44, avg_r_win=3.5, avg_r_loss=1.0),
    "gap_continuation":  dict(wr=0.50, avg_r_win=1.8, avg_r_loss=1.0),
    "vwap_bounce":       dict(wr=0.48, avg_r_win=2.0, avg_r_loss=1.0),
    "range_breakout":    dict(wr=0.40, avg_r_win=3.5, avg_r_loss=1.0),
    "relative_strength": dict(wr=0.46, avg_r_win=2.5, avg_r_loss=1.0),
}

def r_expectancy(strategy: str, score: float) -> float:
    """R-expectancy × confidence — trail-independent signal quality metric for ranking."""
    sp    = STRAT_EDGE.get(strategy, dict(wr=0.40, avg_r_win=2.0, avg_r_loss=1.0))
    exp_r = sp["wr"] * sp["avg_r_win"] - (1 - sp["wr"]) * sp["avg_r_loss"]
    conf  = (score / 100) ** 1.3
    return round(exp_r * conf, 4)

def expected_pnl(score: float, atr: float, close: float,
                 account_size: float, strategy: str,
                 trail: float = 3.0) -> float:
    """Dollar P&L estimate using 2% risk sizing. Used for display only, not ranking."""
    sp        = STRAT_EDGE.get(strategy, dict(wr=0.40, avg_r_win=2.0, avg_r_loss=1.0))
    risk_amt  = account_size * 0.02
    stop_dist = trail * atr
    shares    = (risk_amt / stop_dist) if stop_dist > 0 else 0
    exp_r     = sp["wr"] * sp["avg_r_win"] - (1 - sp["wr"]) * sp["avg_r_loss"]
    conf      = (score / 100) ** 1.3
    return round(shares * atr * exp_r * conf, 2)


# ── Data fetch helpers ────────────────────────────────────────────────────────
def fetch_batch_daily(symbols: list, n_days: int = 22) -> dict:
    """Fetch daily bars for multiple symbols at once (fast batch endpoint)."""
    end   = pd.Timestamp.now(tz="America/New_York").floor("D")
    start = end - pd.DateOffset(days=n_days * 2)
    out   = {}
    # Alpaca batch: up to 100 symbols per request
    for chunk_start in range(0, len(symbols), 100):
        chunk = symbols[chunk_start:chunk_start+100]
        params = {"symbols": ",".join(chunk), "timeframe": "1Day",
                  "start": start.isoformat(), "end": end.isoformat(),
                  "limit": 1000, "feed": "iex"}
        r = requests.get(f"{DATA}/v2/stocks/bars", headers=hdrs(), params=params)
        if r.status_code != 200:
            continue
        data = r.json().get("bars", {})
        for sym, bars in data.items():
            if bars:
                df = pd.DataFrame(bars)
                df["t"] = pd.to_datetime(df["t"], utc=True)
                df = df.set_index("t").rename(
                    columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
                out[sym] = df[["open","high","low","close","volume"]]
    return out


def fetch_intraday_snapshot(symbols: list) -> dict:
    """Fetch latest 5-min bars for multiple symbols (intraday snapshot)."""
    out = {}
    for chunk_start in range(0, len(symbols), 50):
        chunk = symbols[chunk_start:chunk_start+50]
        params = {"symbols": ",".join(chunk), "timeframe": "5Min",
                  "limit": 100, "feed": "iex"}
        r = requests.get(f"{DATA}/v2/stocks/bars", headers=hdrs(), params=params)
        if r.status_code != 200:
            continue
        data = r.json().get("bars", {})
        for sym, bars in data.items():
            if len(bars) >= 10:
                df = pd.DataFrame(bars)
                df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
                df = df.set_index("t").rename(
                    columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
                out[sym] = df[["open","high","low","close","volume"]]
    return out


# ── Feature computation ───────────────────────────────────────────────────────
def compute_features(sym: str, daily_df: pd.DataFrame,
                     intraday_df: pd.DataFrame | None,
                     sector_map: dict) -> dict:
    """Compute all features needed for strategy scoring."""
    if daily_df is None or len(daily_df) < 10:
        return {}

    d = daily_df.copy()
    close    = float(d["close"].iloc[-1])
    prev_close = float(d["close"].iloc[-2]) if len(d) > 1 else close
    gap_pct  = (close - prev_close) / prev_close if prev_close else 0

    # ATR (14-day Wilder)
    hl  = d["high"] - d["low"]
    hcp = (d["high"] - d["close"].shift(1)).abs()
    lcp = (d["low"]  - d["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    atr = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])

    # ADX / PDI / NDI — proper rolling Wilder smoothing over full history
    up   = d["high"] - d["high"].shift(1)
    down = d["low"].shift(1) - d["low"]
    pdm_s = pd.Series(np.where((up > down) & (up > 0), up.values, 0.0), index=d.index)
    ndm_s = pd.Series(np.where((down > up) & (down > 0), down.values, 0.0), index=d.index)
    aw    = tr.ewm(span=14, adjust=False).mean()
    pdi_s = 100 * pdm_s.ewm(span=14, adjust=False).mean() / aw
    ndi_s = 100 * ndm_s.ewm(span=14, adjust=False).mean() / aw
    dx_s  = 100 * (pdi_s - ndi_s).abs() / (pdi_s + ndi_s).replace(0, np.nan).fillna(0)
    adx   = float(dx_s.ewm(span=14, adjust=False).mean().iloc[-1])
    pdi   = float(pdi_s.iloc[-1])
    ndi   = float(ndi_s.iloc[-1])

    # EMA slope
    ema25 = float(d["close"].ewm(span=25, adjust=False).mean().iloc[-1])
    ema25_5 = float(d["close"].ewm(span=25, adjust=False).mean().iloc[-6]) if len(d) > 5 else ema25
    ema_slope = (ema25 - ema25_5) / (ema25_5 * 5) if ema25_5 else 0

    # Vol rank (rolling percentile of ATR over last 63 days)
    atr_series = tr.ewm(span=14, adjust=False).mean().tail(63)
    vol_rank   = float(atr_series.rank(pct=True).iloc[-1]) if len(atr_series) > 5 else 0.5

    # Volume ratio
    avg_vol = float(d["volume"].tail(20).mean()) if "volume" in d.columns else 0
    cur_vol = float(d["volume"].iloc[-1]) if "volume" in d.columns else 0
    vol_ratio = cur_vol / avg_vol if avg_vol else 1.0

    # 5-day return
    mom_5day = (close / float(d["close"].iloc[-6]) - 1) if len(d) > 5 else 0

    # 20-bar high/low (from intraday if available, else daily)
    if intraday_df is not None and len(intraday_df) >= 20:
        high20 = float(intraday_df["high"].tail(20).max())
        low20  = float(intraday_df["low"].tail(20).min())
        # VWAP approximation from intraday
        typical = (intraday_df["high"] + intraday_df["low"] + intraday_df["close"]) / 3
        vwap    = float((typical * intraday_df["volume"]).sum() / intraday_df["volume"].sum()) \
                  if intraday_df["volume"].sum() > 0 else close
        atr_5bar = float(tr.ewm(span=14,adjust=False).mean().iloc[-6]) if len(d) > 5 else atr
    else:
        # Use 10 daily bars for range_breakout lookback (was 5, too tight)
        high20 = float(d["high"].tail(10).max())
        low20  = float(d["low"].tail(10).min())
        vwap   = close
        atr_5bar = float(tr.ewm(span=14,adjust=False).mean().iloc[-6]) if len(d) > 5 else atr

    # 1-day and 20-day return for sector rotation
    mom_1day = (close / prev_close - 1) if prev_close else 0
    mom_20day = (close / float(d["close"].iloc[-21]) - 1) if len(d) > 20 else 0

    trend_dir = "up" if pdi > ndi else "down"

    return dict(
        symbol=sym, close=close, prev_close=prev_close,
        gap_pct=gap_pct, atr=atr, atr_pct=atr/close if close else 0,
        adx=adx, pdi=pdi, ndi=ndi, ema25=ema25, ema_slope=ema_slope,
        vol_rank=vol_rank, vol_ratio=vol_ratio,
        mom_1day=mom_1day, mom_5day=mom_5day, mom_20day=mom_20day,
        high_20bar=high20, low_20bar=low20, vwap=vwap,
        atr_5bar_ago=atr_5bar,
        sector=sector_map.get(sym, "other"),
        trend_dir=trend_dir,
        rs_rank_in_sector=0.5,   # filled in by two-pass sector ranking
    )


# ── Main scan logic ───────────────────────────────────────────────────────────
def run_scan(account_size: float = 500.0, top_n: int = 10,
             verbose: bool = True) -> pd.DataFrame:

    now = pd.Timestamp.now(tz="America/New_York")

    if verbose:
        print(f"\n{'='*80}")
        print(f"  MARKET SCANNER  {now.strftime('%Y-%m-%d %H:%M ET')}"
              f"  account=${account_size:,.0f}")
        print(f"{'='*80}")
        print(f"  Universe: {len(ALL_SYMBOLS)} symbols across {len(UNIVERSE)} sectors")
        print(f"  Fetching daily bars ...", flush=True)

    # Build sector map
    sector_map = {}
    for sector, syms in UNIVERSE.items():
        for s in syms: sector_map[s] = sector

    # Fetch daily data for full universe
    daily_data  = fetch_batch_daily(ALL_SYMBOLS, n_days=30)
    found = len(daily_data)
    if verbose: print(f"  Got daily data for {found}/{len(ALL_SYMBOLS)} symbols")

    if found == 0:
        print("  No data returned. Check API keys / market hours.")
        return pd.DataFrame()

    # Quick pre-filter: top 60 by ATR% and vol (reduces intraday fetch needed)
    # Min price $15 — penny stocks blow up the daily simulation model
    quick_scores = []
    for sym, df in daily_data.items():
        if len(df) < 5: continue
        c    = float(df["close"].iloc[-1])
        if c < 15.0: continue   # skip penny stocks
        pc   = float(df["close"].iloc[-2]) if len(df) > 1 else c
        gap  = abs((c - pc) / pc) if pc else 0
        hl   = df["high"] - df["low"]
        atr  = float(hl.ewm(span=14,adjust=False).mean().iloc[-1])
        atr_pct = atr / c if c else 0
        if atr_pct > 0.08: continue   # skip extreme-vol penny-like names
        avg_v = float(df["volume"].tail(10).mean()) if "volume" in df.columns else 1
        cur_v = float(df["volume"].iloc[-1]) if "volume" in df.columns else 1
        vol_r = cur_v / avg_v if avg_v else 1
        quick_scores.append(dict(sym=sym, atr_pct=atr_pct, gap=gap, vol_r=vol_r,
                                 score=atr_pct*30 + gap*20 + min(vol_r,3)*15))
    quick_df = pd.DataFrame(quick_scores).sort_values("score", ascending=False)
    top60    = list(quick_df.head(60)["sym"])

    if verbose: print(f"  Pre-filtered to top 60 by ATR%/gap/volume. Fetching intraday ...", flush=True)

    # Fetch intraday for top 60
    intraday_data = fetch_intraday_snapshot(top60)
    if verbose: print(f"  Intraday data for {len(intraday_data)}/{len(top60)} symbols")

    # ── Pass 1: compute features for all candidates ───────────────────────────
    all_feats = {}
    for sym in top60:
        df_d = daily_data.get(sym)
        df_i = intraday_data.get(sym)
        if df_d is None or len(df_d) < 10: continue
        feat = compute_features(sym, df_d, df_i, sector_map)
        if not feat: continue
        if feat["close"] < 15.0: continue
        if feat["atr_pct"] > 0.08: continue
        all_feats[sym] = feat

    # ── Sector enrichment: RS rank + sector avg 1-day momentum + sector ADX ───
    sector_moms: dict[str, list] = {}
    sector_adxs: dict[str, list] = {}
    for sym, feat in all_feats.items():
        sec = feat["sector"]
        sector_moms.setdefault(sec, []).append((sym, feat["mom_1day"]))
        sector_adxs.setdefault(sec, []).append(feat["adx"])

    sector_avg_mom1d: dict[str, float] = {}
    sector_avg_adx:   dict[str, float] = {}
    for sec, entries in sector_moms.items():
        sector_avg_mom1d[sec] = float(np.mean([v for _, v in entries]))
        n = len(entries)
        sorted_e = sorted(entries, key=lambda x: x[1])
        for rank_i, (sym, _) in enumerate(sorted_e):
            all_feats[sym]["rs_rank_in_sector"] = rank_i / max(n - 1, 1) if n > 1 else 0.5
    for sec, adxs in sector_adxs.items():
        sector_avg_adx[sec] = float(np.mean(adxs))

    for sym, feat in all_feats.items():
        feat["sector_avg_mom1d"] = sector_avg_mom1d.get(feat["sector"], 0)
        feat["sector_avg_adx"]   = sector_avg_adx.get(feat["sector"], 0)

    # Build sector_scores dict for relative_strength scorer
    sector_scores = {sec: {"adx": adx} for sec, adx in sector_avg_adx.items()}

    # ── Pass 2: score all strategies ──────────────────────────────────────────
    rows = []
    for sym, feat in all_feats.items():
        strat_scores = [
            score_trend_momentum(feat),
            score_gap_continuation(feat),
            score_vwap_bounce(feat),
            score_range_breakout(feat),
            score_relative_strength(sym, feat, sector_scores),
        ]
        for ss in strat_scores:
            if ss["direction"] is None or ss["score"] < 30:
                continue
            r_exp = r_expectancy(ss["strategy"], ss["score"])
            exp   = expected_pnl(ss["score"], feat["atr"], feat["close"],
                                 account_size, ss["strategy"],
                                 trail=ss["params"].get("trail", 3.0))
            rows.append(dict(
                symbol=sym, strategy=ss["strategy"],
                score=ss["score"], direction=ss["direction"],
                r_exp=r_exp,
                close=round(feat["close"], 2),
                atr=round(feat["atr"], 4),
                atr_pct=round(feat["atr_pct"]*100, 2),
                gap_pct=round(feat["gap_pct"]*100, 2),
                vol_ratio=round(feat["vol_ratio"], 1),
                adx=round(feat["adx"], 1),
                vol_rank=round(feat["vol_rank"], 2),
                exp_pnl=exp,
                reason=ss["reason"],
                params=ss["params"],
                sector=feat["sector"],
            ))

    if not rows:
        print("  No qualifying setups found.")
        return pd.DataFrame()

    # Rank by R-expectancy (trail-independent signal quality), not raw dollar PnL
    result = pd.DataFrame(rows).sort_values("r_exp", ascending=False)

    if verbose:
        now_mins = now.hour * 60 + now.minute

        def entry_expired(params):
            ee = params.get("entry_end", "16:00") if isinstance(params, dict) else "16:00"
            try:
                h, m = map(int, ee.split(":"))
                return now_mins > h * 60 + m
            except Exception:
                return False

        print(f"\n{'─'*80}")
        print(f"  TOP {top_n} OPPORTUNITIES  (ranked by R-expectancy × confidence)")
        print(f"{'─'*80}")
        print(f"  {'#':>2}  {'Symbol':<7} {'Strategy':<20} {'Dir':<6} "
              f"{'Score':>5} {'R-exp':>6} {'Gap%':>6} {'ATR%':>5} "
              f"{'ADX':>5} {'ExpPnL':>8}  {'Note'}")
        print("  " + "─" * 82)

        rank = 1
        for _, r in result.iterrows():
            if rank > top_n: break
            exp_s  = f"${r['exp_pnl']:>6.2f}" if r["exp_pnl"] >= 0 else f"-${abs(r['exp_pnl']):>5.2f}"
            note   = "EXPIRED" if entry_expired(r["params"]) else ""
            print(f"  {rank:>2}  {r['symbol']:<7} {r['strategy']:<20} {r['direction']:<6} "
                  f"{r['score']:>5.0f} {r['r_exp']:>6.3f} {r['gap_pct']:>+5.1f}% "
                  f"{r['atr_pct']:>4.1f}% {r['adx']:>5.1f} {exp_s:>8}  {note}")
            rank += 1

        # Multi-pick: top 3 distinct symbols with position details
        print(f"\n{'─'*80}")
        print(f"  TODAY'S TRADES  (2% risk each, max 6% total account exposure)")
        print(f"{'─'*80}")
        seen_syms: set = set()
        pick_n = 1
        for _, r in result.iterrows():
            if r["symbol"] in seen_syms or pick_n > 3: break
            expired = entry_expired(r["params"])
            seen_syms.add(r["symbol"])
            trail    = r["params"].get("trail", 3.0) if isinstance(r["params"], dict) else 3.0
            stop_d   = trail * r["atr"]
            shares   = (account_size * 0.02 / stop_d) if stop_d > 0 else 0
            entry_end = r["params"].get("entry_end", "—") if isinstance(r["params"], dict) else "—"
            status = "  ⚠ ENTRY WINDOW CLOSED" if expired else f"  entry before {entry_end} ET"
            print(f"  #{pick_n}  {r['symbol']:<6} {r['direction'].upper():<5} "
                  f"via {r['strategy']:<20}  score={r['score']:.0f}  exp=${r['exp_pnl']:.2f}")
            print(f"       shares={shares:.2f}  stop={stop_d:.2f} away  ADX={r['adx']:.0f}"
                  f"  [{r['sector']}]{status}")
            print(f"       signal: {r['reason']}")
            pick_n += 1
        print(f"{'='*80}\n")

    return result


# ── Backtest: replay last N days using daily scans ────────────────────────────
def backtest_scanner(n_days: int = 30, account_size: float = 500.0):
    """
    Replay the scanner over the last n_days.
    Each day: pick the top-scored (symbol, strategy) pair,
    simulate that trade using the day's actual open/high/low/close.
    """
    print(f"\n{'='*80}")
    print(f"  SCANNER BACKTEST — last {n_days} trading days  (${account_size:,.0f})")
    print(f"{'='*80}")

    daily_data = fetch_batch_daily(ALL_SYMBOLS, n_days=n_days + 30)
    if not daily_data:
        print("  No data.")
        return

    # Get common trading dates
    all_dates = sorted(set.intersection(*[
        set(df.index.normalize().unique())
        for df in daily_data.values() if len(df) > 5
    ]))[-n_days:]

    equity = account_size
    trades = []

    sector_map = {}
    for sector, syms in UNIVERSE.items():
        for s in syms: sector_map[s] = sector

    print(f"  Simulating {len(all_dates)} trading days ...\n")
    print(f"  {'Date':<12} {'Symbol':<7} {'Strategy':<20} {'Dir':<6} "
          f"{'Score':>5} {'ExpPnL':>8} {'ActPnL':>8} {'Equity':>10}")
    print("  " + "─" * 76)

    for i, date in enumerate(all_dates):
        date_str = str(date.date()) if hasattr(date, 'date') else str(date)[:10]

        # Pass 1: build features on data available BEFORE this date
        day_feats = {}
        for sym, df in daily_data.items():
            hist = df[df.index.normalize() < date]
            if len(hist) < 15: continue
            feat = compute_features(sym, hist, None, sector_map)
            if not feat: continue
            if feat["close"] < 15.0: continue
            if feat["atr_pct"] > 0.08: continue
            day_feats[sym] = feat

        # Sector enrichment
        sec_moms: dict = {}
        sec_adxs: dict = {}
        for sym, feat in day_feats.items():
            sec_moms.setdefault(feat["sector"], []).append((sym, feat["mom_1day"]))
            sec_adxs.setdefault(feat["sector"], []).append(feat["adx"])
        sec_avg:  dict = {}
        sec_adx_avg: dict = {}
        for sec, entries in sec_moms.items():
            sec_avg[sec] = float(np.mean([v for _, v in entries]))
            n = len(entries)
            for rank_i, (s, _) in enumerate(sorted(entries, key=lambda x: x[1])):
                day_feats[s]["rs_rank_in_sector"] = rank_i / max(n-1,1) if n > 1 else 0.5
        for sec, adxs in sec_adxs.items():
            sec_adx_avg[sec] = float(np.mean(adxs))
        for sym, feat in day_feats.items():
            feat["sector_avg_mom1d"] = sec_avg.get(feat["sector"], 0)
            feat["sector_avg_adx"]   = sec_adx_avg.get(feat["sector"], 0)
        bt_sector_scores = {s: {"adx": a} for s, a in sec_adx_avg.items()}

        # Pass 2: score
        rows = []
        for sym, feat in day_feats.items():
            for scorer in [score_trend_momentum, score_gap_continuation,
                           score_vwap_bounce, score_range_breakout,
                           lambda f: score_relative_strength(sym, f, bt_sector_scores)]:
                ss = scorer(feat)
                if ss["direction"] is None or ss["score"] < 40: continue
                r_exp = r_expectancy(ss["strategy"], ss["score"])
                exp   = expected_pnl(ss["score"], feat["atr"], feat["close"],
                                     equity, ss["strategy"],
                                     trail=ss["params"].get("trail", 3.0))
                rows.append(dict(symbol=sym, strategy=ss["strategy"],
                                 score=ss["score"], direction=ss["direction"],
                                 r_exp=r_exp, atr=feat["atr"], close=feat["close"],
                                 exp_pnl=exp, params=ss["params"]))

        if not rows: continue
        best = max(rows, key=lambda x: x["r_exp"])

        # Simulate actual trade on this date
        sym  = best["symbol"]
        df_s = daily_data.get(sym)
        if df_s is None: continue
        today_bar = df_s[df_s.index.normalize() == date]
        if len(today_bar) == 0: continue

        day_open  = float(today_bar["open"].iloc[0])
        day_high  = float(today_bar["high"].iloc[0])
        day_low   = float(today_bar["low"].iloc[0])
        day_close = float(today_bar["close"].iloc[0])
        atr       = best["atr"]
        direction = best["direction"]
        # Risk-based sizing: risk 2% of equity per trade (stop = trail×ATR away)
        risk_amt      = equity * 0.02
        stop_dist     = best["params"].get("trail", 3.0) * atr
        shares        = (risk_amt / stop_dist) if stop_dist > 0 else 0

        # Simple daily simulation: enter at open, trail stop, exit at close
        if direction == "long":
            trail_stop = day_open - best["params"].get("trail", 3.0) * atr
            if day_low <= trail_stop:
                exit_price = trail_stop   # stopped out
            else:
                exit_price = day_close
            pnl = (exit_price - day_open) * shares
        else:
            trail_stop = day_open + best["params"].get("trail", 3.0) * atr
            if day_high >= trail_stop:
                exit_price = trail_stop
            else:
                exit_price = day_close
            pnl = (day_open - exit_price) * shares

        pnl -= shares * 0.005 * 2   # commission
        equity += pnl
        trades.append(dict(date=date_str, symbol=sym,
                           strategy=best["strategy"], direction=direction,
                           score=best["score"], exp_pnl=best["exp_pnl"],
                           act_pnl=round(pnl, 2), equity=round(equity, 2)))

        act_s = f"${pnl:>+7.2f}"
        exp_s = f"${best['exp_pnl']:>6.2f}"
        print(f"  {date_str:<12} {sym:<7} {best['strategy']:<20} "
              f"{direction:<6} {best['score']:>5.0f} {exp_s:>8} {act_s:>8} "
              f"${equity:>9,.2f}")

    if trades:
        t = pd.DataFrame(trades)
        pnl_s = pd.Series(t["act_pnl"])
        wins  = (pnl_s > 0).sum()
        sep   = "=" * 80
        print(f"\n{sep}")
        print(f"  BACKTEST SUMMARY  (${account_size:,.0f} start)")
        print(sep)
        print(f"  ${account_size:,.0f} → ${equity:,.2f}  "
              f"({(equity/account_size-1)*100:+.1f}%)")
        print(f"  Win rate  : {wins}/{len(t)} ({wins/len(t)*100:.0f}%)")
        print(f"  Avg PnL/day: ${pnl_s.mean():+.2f}")
        print(f"  Best day  : ${pnl_s.max():+.2f}  ({t.loc[pnl_s.idxmax(),'symbol']})")
        print(f"  Worst day : ${pnl_s.min():+.2f}  ({t.loc[pnl_s.idxmin(),'symbol']})")

        print(f"\n  Symbols traded:")
        sym_counts = t.groupby("symbol")["act_pnl"].agg(["count","sum","mean"])
        for sym, row in sym_counts.sort_values("sum", ascending=False).head(10).iterrows():
            print(f"    {sym:<8} {int(row['count']):>2}× traded  "
                  f"total=${row['sum']:>+8.2f}  avg/trade=${row['mean']:>+7.2f}")

        print(f"\n  Strategies used:")
        strat_counts = t.groupby("strategy")["act_pnl"].agg(["count","sum","mean"])
        for s, row in strat_counts.sort_values("sum", ascending=False).iterrows():
            print(f"    {s:<22} {int(row['count']):>2}× "
                  f"total=${row['sum']:>+8.2f}  avg=${row['mean']:>+7.2f}")
        print(sep)


# ══════════════════════════════════════════════════════════════════════════════
# AGGRESSIVE MODE — aim for 2× account as fast as possible
# Philosophy: concentrate 20-40% of account in the single best setup each day.
#             Stop = 1.5 ATR (cut fast), Target = 3 ATR (let it run).
#             Focus universe: 3× leveraged ETFs + highest-beta singles.
# ══════════════════════════════════════════════════════════════════════════════

EXPLOSIVE_UNIVERSE = [
    # 3× leveraged ETFs — highest daily % swings
    "SOXL", "TQQQ", "UPRO", "LABU", "FNGU", "TECL", "SPXL", "TNA", "UDOW", "NAIL",
    # High-beta mega-tech (5-15%/day on catalyst)
    "NVDA", "AMD", "ARM", "MSTR", "PLTR", "APP", "CRWD", "COIN",
    # Volatile biotech
    "MRNA", "BNTX", "NVAX",
    # Crypto-adjacent
    "MARA", "RIOT",
]


def score_explosive(sym_data: dict) -> dict:
    """
    Single scorer optimised for maximum single-day % gain.
    Combines trend strength (ADX), momentum (slope + 5d return),
    vol regime (high vol = big swings available), and gap alignment.
    """
    adx       = sym_data.get("adx", 0)
    vr        = sym_data.get("vol_rank", 0.5)
    slope     = sym_data.get("ema_slope", 0)
    gap       = sym_data.get("gap_pct", 0)
    mom5d     = sym_data.get("mom_5day", 0)
    atr_pct   = sym_data.get("atr_pct", 0)
    close     = sym_data.get("close", 0)
    ema       = sym_data.get("ema25", close)
    trend_dir = sym_data.get("trend_dir", "neutral")
    vol_r     = sym_data.get("vol_ratio", 1)

    score = 0
    # Direction: follow the dominant trend (PDI vs NDI already baked into trend_dir)
    direction = "long" if trend_dir == "up" else "short"

    # Trend strength (0-35 pts) — ADX is the backbone
    if adx > 55:   score += 35
    elif adx > 40: score += 27
    elif adx > 30: score += 18
    elif adx > 20: score += 8

    # Vol regime (0-30 pts) — need high vol for big swings
    if vr > 0.80:        score += 30
    elif vr > 0.65:      score += 22
    elif vr > 0.50:      score += 14
    elif vr < 0.30:      score -= 20   # low vol = no fuel

    # 5-day momentum confirms direction (0-20 pts)
    if direction == "long"  and mom5d > 0.08: score += 20
    elif direction == "long"  and mom5d > 0.04: score += 13
    elif direction == "long"  and mom5d > 0.01: score += 6
    elif direction == "short" and mom5d < -0.08: score += 20
    elif direction == "short" and mom5d < -0.04: score += 13
    elif direction == "short" and mom5d < -0.01: score += 6

    # EMA slope — is the trend accelerating? (0-10 pts)
    if direction == "long"  and slope > 0.006: score += 10
    elif direction == "long"  and slope > 0.002: score += 5
    elif direction == "short" and slope < -0.006: score += 10
    elif direction == "short" and slope < -0.002: score += 5

    # Gap alignment = catalyst confirms direction (0-8 pts)
    if direction == "long"  and gap > 0.02: score += 8
    elif direction == "short" and gap < -0.02: score += 8
    # Gap opposing strong trend = red flag
    if adx > 35:
        if direction == "long"  and gap < -0.04: score -= 18
        if direction == "short" and gap > 0.04:  score -= 18

    # ATR% = daily range available (0-10 pts)
    if atr_pct > 0.08:   score += 10
    elif atr_pct > 0.05: score += 6
    elif atr_pct > 0.03: score += 3

    # Volume surge today = institutional participation (0-7 pts)
    if vol_r > 2.0:   score += 7
    elif vol_r > 1.3: score += 3

    # EMA price alignment (0-5 pts)
    if direction == "long"  and close > ema * 1.001: score += 5
    elif direction == "short" and close < ema * 0.999: score += 5

    params = dict(trail=1.5, ema=25, entry_end="11:30", target_r=3.0)
    reason = (f"ADX={adx:.0f} vr={vr:.2f} mom5d={mom5d*100:+.1f}% "
              f"slope={slope*100:.3f}%/bar atr%={atr_pct*100:.1f}% gap={gap*100:+.1f}%")
    return dict(score=max(0, min(100, score)), direction=direction,
                params=params, reason=reason, strategy="explosive")


def aggressive_position(score: float, atr: float, close: float, equity: float,
                        max_pct: float = 0.40) -> dict:
    """
    Aggressive risk sizing based on signal confidence.
    Stop  = 1.5 × ATR from entry (cut fast)
    Target = 3.0 × ATR from entry (asymmetric, let winners run)
    max_pct caps the bet size (default 40%, set higher for yolo mode).
    """
    if score >= 88:   pct = min(max_pct, 1.0)
    elif score >= 78: pct = min(max_pct * 0.75, 1.0)
    elif score >= 65: pct = min(max_pct * 0.50, 1.0)
    else:             pct = 0.0

    notional   = equity * pct
    shares     = notional / close if close else 0
    stop_dist  = 1.5 * atr
    tgt_dist   = 3.0 * atr
    max_loss   = shares * stop_dist
    max_gain   = shares * tgt_dist

    return dict(
        pct=pct, notional=round(notional, 2), shares=round(shares, 4),
        stop_dist=round(stop_dist, 4), tgt_dist=round(tgt_dist, 4),
        max_loss=round(max_loss, 2), max_gain=round(max_gain, 2),
        loss_pct=round(max_loss / equity * 100, 1) if equity else 0,
        gain_pct=round(max_gain / equity * 100, 1) if equity else 0,
    )


def run_aggressive(account_size: float = 500.0):
    """Scan explosive universe, show today's highest-conviction aggressive setup."""
    now = pd.Timestamp.now(tz="America/New_York")
    sector_map = {s: "explosive" for s in EXPLOSIVE_UNIVERSE}

    print(f"\n{'#'*80}")
    print(f"  AGGRESSIVE SCANNER  {now.strftime('%Y-%m-%d %H:%M ET')}"
          f"  account=${account_size:,.0f}")
    print(f"  Goal: find today's highest-probability explosive move")
    print(f"{'#'*80}")
    print(f"  Fetching data for {len(EXPLOSIVE_UNIVERSE)} high-volatility names ...", flush=True)

    daily_data = fetch_batch_daily(EXPLOSIVE_UNIVERSE, n_days=30)
    print(f"  Got data for {len(daily_data)}/{len(EXPLOSIVE_UNIVERSE)} symbols")

    # Compute features (no price floor — leveraged ETFs are legitimately cheap)
    feats = {}
    for sym, df in daily_data.items():
        if len(df) < 15: continue
        feat = compute_features(sym, df, None, sector_map)
        if feat: feats[sym] = feat

    # Score every symbol
    rows = []
    for sym, feat in feats.items():
        ss = score_explosive(feat)
        if ss["score"] < 50 or ss["direction"] is None: continue
        pos = aggressive_position(ss["score"], feat["atr"], feat["close"], account_size)
        if pos["pct"] == 0: continue
        rows.append(dict(
            symbol=sym, score=ss["score"], direction=ss["direction"],
            close=round(feat["close"], 2), atr=feat["atr"],
            atr_pct=round(feat["atr_pct"]*100, 2),
            adx=round(feat["adx"], 1), vol_rank=round(feat["vol_rank"], 2),
            mom5d=round(feat["mom_5day"]*100, 1),
            gap=round(feat["gap_pct"]*100, 2),
            pos=pos, reason=ss["reason"],
        ))

    if not rows:
        print("  No explosive setups today.")
        return

    # Tie-break by ADX × vol_rank (higher = more confirmed trend)
    rows.sort(key=lambda x: (x["score"], x["adx"] * x["vol_rank"]), reverse=True)

    print(f"\n{'─'*80}")
    print(f"  ALL QUALIFYING SETUPS  (min score 65, sorted by conviction)")
    print(f"{'─'*80}")
    print(f"  {'#':>2}  {'Sym':<6} {'Dir':<6} {'Score':>5}  "
          f"{'ADX':>5} {'VR':>5} {'Mom5d':>7} {'ATR%':>5}  "
          f"{'Bet%':>5} {'NotionalPut':>11} {'WinTarget':>10} {'MaxLoss':>9}")
    print("  " + "─" * 78)

    for i, r in enumerate(rows[:10], 1):
        p = r["pos"]
        print(f"  {i:>2}  {r['symbol']:<6} {r['direction']:<6} {r['score']:>5}  "
              f"{r['adx']:>5.1f} {r['vol_rank']:>5.2f} {r['mom5d']:>+6.1f}% "
              f"{r['atr_pct']:>4.1f}%  "
              f"{p['pct']*100:>4.0f}%  ${p['notional']:>9,.2f}  "
              f"+${p['max_gain']:>7,.2f} ({p['gain_pct']:>+.1f}%)  "
              f"-${p['max_loss']:>6,.2f} ({p['loss_pct']:>.1f}%)")

    best = rows[0]
    p    = best["pos"]
    now_mins = now.hour * 60 + now.minute
    expired  = now_mins > 11 * 60 + 30

    print(f"\n{'─'*80}")
    print(f"  TODAY'S TRADE: {best['symbol']} {best['direction'].upper()}")
    print(f"{'─'*80}")
    print(f"  Price     : ${best['close']:.2f}   ATR: ${best['atr']:.2f} ({best['atr_pct']:.1f}%)")
    print(f"  ADX       : {best['adx']:.0f}   Vol rank: {best['vol_rank']:.2f}   5d mom: {best['mom5d']:+.1f}%")
    print(f"  Signal    : {best['reason']}")
    print()
    print(f"  POSITION   : ${p['notional']:,.2f}  ({p['pct']*100:.0f}% of ${account_size:,.0f})")
    print(f"  Shares     : {p['shares']:.4f}")
    print(f"  Stop       : 1.5 ATR = ${best['close'] - p['stop_dist']:.2f}  "
          f"(lose ${p['max_loss']:.2f} = -{p['loss_pct']:.1f}% account)")
    print(f"  Target     : 3.0 ATR = ${best['close'] + p['tgt_dist']:.2f}  "
          f"(gain ${p['max_gain']:.2f} = +{p['gain_pct']:.1f}% account)")
    print(f"  R/R ratio  : {p['tgt_dist']/p['stop_dist']:.1f}:1")
    if expired:
        print(f"  ENTRY      : ⚠  Entry window closed (past 11:30 ET)")
    else:
        print(f"  ENTRY      : BUY at open or on first pullback to EMA  (before 11:30 ET)")
    print(f"{'#'*80}\n")


def backtest_aggressive(n_days: int = 30, account_size: float = 500.0,
                        max_pct: float = 0.40):
    """
    Replay aggressive scanner over last n_days.
    Uses intraday target/stop sim: if day_high hits 3R target → win; if day_low hits 1.5R stop → loss.
    max_pct controls bet sizing ceiling (0.40 = default, 1.0 = all-in).
    """
    pct_label = f"{max_pct*100:.0f}% max"
    print(f"\n{'#'*80}")
    print(f"  AGGRESSIVE BACKTEST — last {n_days} days  start=${account_size:,.0f}")
    print(f"  Sizing: up to {pct_label} per trade  Stop: 1.5 ATR  Target: 3 ATR")
    print(f"{'#'*80}")

    sector_map = {s: "explosive" for s in EXPLOSIVE_UNIVERSE}
    daily_data = fetch_batch_daily(EXPLOSIVE_UNIVERSE, n_days=n_days + 60)
    if not daily_data:
        print("  No data."); return

    # Anchor to SOXL or AMD dates — most liquid, always has data.
    # Strict intersection breaks when thin symbols (NVAX, MARA) have data gaps.
    anchor = next((s for s in ("SOXL","AMD","TQQQ","NVDA") if s in daily_data),
                  list(daily_data.keys())[0])
    all_dates = sorted(daily_data[anchor].index.normalize().unique())[-n_days:]

    equity = account_size
    trades = []

    print(f"  Simulating {len(all_dates)} trading days (anchor={anchor}) ...\n")
    print(f"  {'Date':<12} {'Sym':<6} {'Dir':<6} {'Sc':>3} {'Bet%':>4}"
          f" {'Entry':>7} {'Stop':>7} {'Tgt':>7} {'Exit':>7} {'$PnL':>8} {'Eq':>10}")
    print("  " + "─" * 80)

    for date in all_dates:
        date_str = str(date.date()) if hasattr(date, 'date') else str(date)[:10]

        # Build features on history up to (not including) this date
        candidates = []
        for sym, df in daily_data.items():
            hist = df[df.index.normalize() < date]
            if len(hist) < 15: continue
            feat = compute_features(sym, hist, None, sector_map)
            if not feat: continue
            ss = score_explosive(feat)
            if ss["score"] < 65 or ss["direction"] is None: continue
            pos = aggressive_position(ss["score"], feat["atr"], feat["close"],
                                      equity, max_pct)
            if pos["pct"] == 0: continue
            candidates.append(dict(symbol=sym, score=ss["score"],
                                   direction=ss["direction"], feat=feat,
                                   pos=pos, atr=feat["atr"]))

        if not candidates: continue
        best = max(candidates, key=lambda x: x["score"])

        sym = best["symbol"]
        df_s = daily_data.get(sym)
        if df_s is None: continue
        today_bar = df_s[df_s.index.normalize() == date]
        if len(today_bar) == 0: continue

        entry     = float(today_bar["open"].iloc[0])
        day_high  = float(today_bar["high"].iloc[0])
        day_low   = float(today_bar["low"].iloc[0])
        day_close = float(today_bar["close"].iloc[0])
        atr       = best["atr"]
        direction = best["direction"]

        stop_dist = 1.5 * atr
        tgt_dist  = 3.0 * atr
        pos       = aggressive_position(best["score"], atr, entry, equity, max_pct)
        shares    = pos["shares"]

        # Simulate: target or stop hit intraday, otherwise close
        if direction == "long":
            stop_px = entry - stop_dist
            tgt_px  = entry + tgt_dist
            if day_low  <= stop_px:  exit_px = stop_px;  outcome = "stop"
            elif day_high >= tgt_px: exit_px = tgt_px;   outcome = "target"
            else:                    exit_px = day_close; outcome = "close"
            pnl = (exit_px - entry) * shares
        else:
            stop_px = entry + stop_dist
            tgt_px  = entry - tgt_dist
            if day_high >= stop_px:  exit_px = stop_px;  outcome = "stop"
            elif day_low  <= tgt_px: exit_px = tgt_px;   outcome = "target"
            else:                    exit_px = day_close; outcome = "close"
            pnl = (entry - exit_px) * shares

        pnl   -= shares * 0.005 * 2   # commission
        prev_eq = equity
        equity += pnl
        day_pct = pnl / prev_eq * 100
        trades.append(dict(date=date_str, symbol=sym, direction=direction,
                           score=best["score"], bet_pct=pos["pct"]*100,
                           entry=entry, stop=stop_px, target=tgt_px,
                           exit=exit_px, outcome=outcome,
                           pnl=round(pnl, 2), day_pct=round(day_pct, 2),
                           equity=round(equity, 2)))

        marker = "✓" if pnl > 0 else "✗"
        print(f"  {date_str:<12} {sym:<6} {direction:<6} {best['score']:>3.0f} "
              f"{pos['pct']*100:>3.0f}%"
              f" {entry:>7.2f} {stop_px:>7.2f} {tgt_px:>7.2f} {exit_px:>7.2f}"
              f" {day_pct:>+6.1f}%  {marker} {outcome}")

    if not trades:
        print("  No trades."); return

    t     = pd.DataFrame(trades)
    pnl_s = t["pnl"]
    pct_s = t["day_pct"]
    wins  = (pnl_s > 0).sum()
    outcomes = t["outcome"].value_counts()

    print(f"\n{'#'*80}")
    print(f"  AGGRESSIVE BACKTEST SUMMARY")
    print(f"{'#'*80}")
    print(f"  ${account_size:,.2f} → ${equity:,.2f}  ({(equity/account_size-1)*100:+.1f}%)")
    print(f"  Win rate    : {wins}/{len(t)} ({wins/len(t)*100:.0f}%)")
    print(f"  Avg/day     : ${pnl_s.mean():+.2f}  ({pct_s.mean():+.2f}% of account)")
    print(f"  Best day    : {pct_s.max():+.1f}%  "
          f"({t.loc[pct_s.idxmax(),'symbol']} — {t.loc[pct_s.idxmax(),'outcome']})")
    print(f"  Worst day   : {pct_s.min():+.1f}%  "
          f"({t.loc[pct_s.idxmin(),'symbol']} — {t.loc[pct_s.idxmin(),'outcome']})")
    print(f"  Outcomes    : target={outcomes.get('target',0)}  "
          f"stop={outcomes.get('stop',0)}  close={outcomes.get('close',0)}")
    print()

    # Daily return distribution
    bins = [(-100,-15),(-15,-10),(-10,-5),(-5,-2),(-2,0),(0,2),(2,5),(5,10),(10,15),(15,100)]
    print(f"  Daily % return distribution:")
    for lo, hi in bins:
        count = ((pct_s >= lo) & (pct_s < hi)).sum()
        bar   = "█" * count
        label = f"{lo:+.0f}% to {hi:+.0f}%"
        print(f"    {label:>16}  {bar:<20} {count:>2}d")
    print()
    print(f"  {'Symbol':<8} {'N':>3} {'WR%':>5} {'Total%':>8} {'Avg/tr%':>9} {'Best%':>8}")
    print("  " + "─" * 48)
    for sym, g in t.groupby("symbol"):
        w = (g["pnl"] > 0).sum()
        print(f"  {sym:<8} {len(g):>3}  {w/len(g)*100:>4.0f}%  "
              f"{g['day_pct'].sum():>+7.1f}%  {g['day_pct'].mean():>+8.1f}%  "
              f"{g['day_pct'].max():>+7.1f}%")
    print(f"{'#'*80}\n")


# ── Live loop ─────────────────────────────────────────────────────────────────
def run_live(account_size: float = 500.0, rescan_mins: int = 5):
    print(f"\nMarket Scanner — LIVE  (${account_size:,.0f}  rescan every {rescan_mins} min)")
    print("Ctrl+C to stop\n")
    while True:
        try:
            now  = pd.Timestamp.now(tz="America/New_York")
            mins = now.hour * 60 + now.minute
            if 9*60+25 <= mins <= 16*60:
                run_scan(account_size=account_size)
            else:
                print(f"  {now.strftime('%H:%M')}  outside market hours — sleeping")
            time.sleep(rescan_mins * 60)
        except KeyboardInterrupt:
            print("\n  Scanner stopped.")
            break
        except Exception as e:
            print(f"  ERROR: {e}")
            time.sleep(60)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]
    size = 500.0
    if "--size" in args:
        idx = args.index("--size")
        try: size = float(args[idx+1])
        except: pass

    yolo = "--yolo" in args   # 100% sizing instead of 40%
    max_pct = 1.0 if yolo else 0.40

    if "--aggressive" in args or yolo:
        if "--backtest" in args:
            idx = args.index("--backtest")
            days = 30
            try: days = int(args[idx+1])
            except: pass
            backtest_aggressive(n_days=days, account_size=size, max_pct=max_pct)
        else:
            run_aggressive(account_size=size)
    elif "--backtest" in args:
        idx = args.index("--backtest")
        days = 30
        try: days = int(args[idx+1])
        except: pass
        backtest_scanner(n_days=days, account_size=size)
    elif "--live" in args:
        run_live(account_size=size)
    else:
        run_scan(account_size=size)
