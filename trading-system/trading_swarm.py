#!/usr/bin/env python3
"""
Trading Swarm — powered by Swarms.ai
======================================
Self-contained multi-agent trading system.  Five specialized agents
collaborate every morning to find and execute the highest-probability
explosive move.

  Phase 1 (parallel):   MarketScanner + CatalystHunter + RegimeAnalyst
  Phase 2 (sequential): RiskManager → TradeDecision

All data-fetch / scoring / backtest logic lives in this file.
No external imports from market_scanner.py needed.

Usage:
  python3 trading_swarm.py                    # run swarm (paper mode)
  python3 trading_swarm.py --live             # run swarm + execute Alpaca order
  python3 trading_swarm.py --scan             # quick scan, no LLM
  python3 trading_swarm.py --aggressive       # aggressive scan, no LLM
  python3 trading_swarm.py --backtest 30      # full-universe backtest, no LLM
  python3 trading_swarm.py --backtest 30 --aggressive  # aggressive backtest
  python3 trading_swarm.py --size 1000        # account size (default 500)
  python3 trading_swarm.py --yolo             # 100% position sizing
  python3 trading_swarm.py --model gpt-4o     # swap LLM backend

Environment variables (override hardcoded defaults):
  APCA_KEY          / APCA_SECRET          → market data keys (sandbox)
  APCA_PAPER_KEY    / APCA_PAPER_SECRET    → paper trading keys (PK-prefix)
  ANTHROPIC_API_KEY                        → required for swarm agents
"""

import json
import os
import re
import sys
import time
import warnings
import argparse
import concurrent.futures
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")


# ══════════════════════════════════════════════════════════════════════════════
#  CREDENTIALS
#  Data keys: sandbox (CK4H…)    → data.sandbox.alpaca.markets
#  Paper keys: PK-prefix         → paper-api.alpaca.markets  (account + orders)
#  Set env vars to override.
# ══════════════════════════════════════════════════════════════════════════════

KEY    = os.getenv("APCA_KEY",    "CK4HXKGM3HWRKLXWRU3CDLB6LX")
SECRET = os.getenv("APCA_SECRET", "7i1mhApwTyzqWprLyvGvbzuDJbNSquMRTCnoiXfJSEWk")
DATA   = "https://data.sandbox.alpaca.markets"

PAPER_KEY    = os.getenv("APCA_PAPER_KEY",    "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV")
PAPER_SECRET = os.getenv("APCA_PAPER_SECRET", "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV")
BROKER       = "https://paper-api.alpaca.markets"

def hdrs():       return {"APCA-API-KEY-ID": KEY,       "APCA-API-SECRET-KEY": SECRET}
def paper_hdrs(): return {"APCA-API-KEY-ID": PAPER_KEY, "APCA-API-SECRET-KEY": PAPER_SECRET}


# ══════════════════════════════════════════════════════════════════════════════
#  UNIVERSES
# ══════════════════════════════════════════════════════════════════════════════

UNIVERSE = {
    "lev_etf":    ["SOXL","TQQQ","UPRO","LABU","FNGU","TECL","DPST","NAIL",
                   "UDOW","TNA","SPXL","HIBL","CURE","WANT","DFEN"],
    "mega_tech":  ["NVDA","AAPL","MSFT","AMZN","META","GOOGL","TSLA",
                   "AVGO","AMD","QCOM","MU","AMAT","LRCX","KLAC","ASML"],
    "growth":     ["PLTR","CRWD","DDOG","NET","SNOW","COIN","APP","MSTR",
                   "SMCI","ARM","NFLX","CRM","NOW","UBER","LYFT"],
    "sector_etf": ["XLK","XLE","XLF","XLV","XLI","XLY","XLC","XLB",
                   "XLP","XLRE","XLU","SMH","SOXX","IBB","XBI"],
    "broad_etf":  ["SPY","QQQ","IWM","DIA","VTI","MDY","EEM","GLD",
                   "SLV","USO","TLT","HYG","LQD","VXX","UVXY"],
    "high_atr":   ["MARA","RIOT","HOOD","SOFI","RIVN","LCID","NIO","XPEV",
                   "BIDU","SE","GRAB","LAZR","CHPT","BLNK","SPCE"],
    "fin_energy": ["JPM","GS","MS","BAC","C","XOM","CVX","OXY","SLB","HAL",
                   "KMI","WMB","DVN","FANG","MRO"],
    "health":     ["LLY","UNH","JNJ","ABBV","MRK","PFE","AMGN","GILD",
                   "BIIB","MRNA","BNTX","NVAX","VRTX","REGN","BMY"],
}
ALL_SYMBOLS = [s for group in UNIVERSE.values() for s in group]

EXPLOSIVE_UNIVERSE = [
    # 3× leveraged ETFs — single-day 10-30% moves during sector runs
    "SOXL","TQQQ","UPRO","LABU","FNGU","TECL","SPXL","TNA","UDOW","NAIL",
    # 2× single-stock ETFs — amplified beta on the market's best movers
    "NVDL","TSLL","MSFO","CONL","BITX",
    # high-beta tech + AI
    "NVDA","AMD","ARM","MSTR","PLTR","APP","CRWD","COIN","SMCI","HOOD",
    # biotech binary catalysts
    "MRNA","BNTX","NVAX",
    # crypto proxies — can do 20-40% on BTC/ETH momentum days
    "MARA","RIOT","IBIT",
    # high-vol small/mid cap
    "ASTS","RKLB","LUNR",
]

# Crypto trades 24/7 — used after hours and on weekends
CRYPTO_UNIVERSE = [
    "BTC-USD","ETH-USD","SOL-USD","DOGE-USD",
    "AVAX-USD","LINK-USD","DOT-USD","ADA-USD",
    "AAVE-USD","LTC-USD","XRP-USD","ATOM-USD",
]
# Alpaca paper API uses BTCUSD format for crypto orders
CRYPTO_ALPACA = {s: s.replace("-USD", "USD") for s in CRYPTO_UNIVERSE}

# Rocket-mode constants — one high-conviction bet at 40% when signals stack
ROCKET_PCT           = 0.40   # capital allocated to the single rocket slot
CORE_PCT             = 0.15   # each of the 4 core slots
ROCKET_CATALYST_MIN  = 4      # catalyst_score threshold (2+ catalysts stacked)
ROCKET_ADX_MIN       = 50     # trend must be confirmed
ROCKET_EXIT_SCORE    = 65     # exit rocket faster than core (core exits at 55)


# ══════════════════════════════════════════════════════════════════════════════
#  DATA LAYER
# ══════════════════════════════════════════════════════════════════════════════

def _bars_to_df(bars: list) -> pd.DataFrame:
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"], utc=True)
    df = df.set_index("t").rename(
        columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
    return df[["open","high","low","close","volume"]]


def fetch_batch_daily(symbols: list, n_days: int = 22) -> dict:
    end   = pd.Timestamp.now(tz="America/New_York").floor("D")
    start = end - pd.DateOffset(days=n_days * 2)
    out   = {}

    # Split crypto (BTC-USD) from equities
    crypto_syms = [s for s in symbols if s in CRYPTO_ALPACA]
    equity_syms = [s for s in symbols if s not in CRYPTO_ALPACA]

    # ── Equity bars via Alpaca stocks endpoint ────────────────────────────────
    for i in range(0, len(equity_syms), 100):
        chunk  = equity_syms[i:i+100]
        params = {"symbols": ",".join(chunk), "timeframe": "1Day",
                  "start": start.isoformat(), "end": end.isoformat(),
                  "limit": 1000, "feed": "iex"}
        r = requests.get(f"{DATA}/v2/stocks/bars", headers=hdrs(), params=params, timeout=15)
        if r.status_code != 200: continue
        for sym, bars in r.json().get("bars", {}).items():
            if bars:
                out[sym] = _bars_to_df(bars)

    # ── Crypto bars via yfinance (BTC-USD format, reliable 24/7) ─────────────
    if crypto_syms:
        try:
            raw = yf.download(crypto_syms, period="60d", progress=False,
                              auto_adjust=True, group_by="ticker")
            for sym in crypto_syms:
                try:
                    df = raw[sym] if len(crypto_syms) > 1 else raw
                    df = df.dropna(how="all")
                    if df.empty: continue
                    if df.index.tz is None:
                        df.index = df.index.tz_localize("UTC")
                    else:
                        df.index = df.index.tz_convert("UTC")
                    df.columns = [c.lower() for c in df.columns]
                    out[sym] = df[["open","high","low","close","volume"]]
                except Exception:
                    pass
        except Exception:
            pass

    return out


def fetch_intraday_snapshot(symbols: list) -> dict:
    out = {}
    for i in range(0, len(symbols), 50):
        chunk  = symbols[i:i+50]
        params = {"symbols": ",".join(chunk), "timeframe": "5Min",
                  "limit": 100, "feed": "iex"}
        r = requests.get(f"{DATA}/v2/stocks/bars", headers=hdrs(), params=params, timeout=15)
        if r.status_code != 200: continue
        for sym, bars in r.json().get("bars", {}).items():
            if len(bars) >= 10:
                df = pd.DataFrame(bars)
                df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
                df = df.set_index("t").rename(
                    columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
                out[sym] = df[["open","high","low","close","volume"]]
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  FEATURE COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

def compute_features(sym: str, daily_df: pd.DataFrame,
                     intraday_df, sector_map: dict) -> dict:
    if daily_df is None or len(daily_df) < 10: return {}
    d          = daily_df.copy()
    close      = float(d["close"].iloc[-1])
    prev_close = float(d["close"].iloc[-2]) if len(d) > 1 else close
    gap_pct    = (close - prev_close) / prev_close if prev_close else 0

    hl  = d["high"] - d["low"]
    hcp = (d["high"] - d["close"].shift(1)).abs()
    lcp = (d["low"]  - d["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    atr = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])

    up    = d["high"] - d["high"].shift(1)
    down  = d["low"].shift(1) - d["low"]
    pdm_s = pd.Series(np.where((up > down) & (up > 0), up.values, 0.0), index=d.index)
    ndm_s = pd.Series(np.where((down > up) & (down > 0), down.values, 0.0), index=d.index)
    aw    = tr.ewm(span=14, adjust=False).mean()
    pdi_s = 100 * pdm_s.ewm(span=14, adjust=False).mean() / aw
    ndi_s = 100 * ndm_s.ewm(span=14, adjust=False).mean() / aw
    dx_s  = 100 * (pdi_s - ndi_s).abs() / (pdi_s + ndi_s).replace(0, np.nan).fillna(0)
    adx   = float(dx_s.ewm(span=14, adjust=False).mean().iloc[-1])
    pdi   = float(pdi_s.iloc[-1])
    ndi   = float(ndi_s.iloc[-1])

    ema25   = float(d["close"].ewm(span=25, adjust=False).mean().iloc[-1])
    ema25_5 = float(d["close"].ewm(span=25, adjust=False).mean().iloc[-6]) if len(d) > 5 else ema25
    ema_slope = (ema25 - ema25_5) / (ema25_5 * 5) if ema25_5 else 0

    atr_series = tr.ewm(span=14, adjust=False).mean().tail(63)
    vol_rank   = float(atr_series.rank(pct=True).iloc[-1]) if len(atr_series) > 5 else 0.5

    avg_vol   = float(d["volume"].tail(20).mean()) if "volume" in d.columns else 0
    cur_vol   = float(d["volume"].iloc[-1])        if "volume" in d.columns else 0
    vol_ratio = cur_vol / avg_vol if avg_vol else 1.0

    mom_5day  = (close / float(d["close"].iloc[-6]) - 1) if len(d) > 5 else 0
    mom_1day  = (close / prev_close - 1) if prev_close else 0
    mom_20day = (close / float(d["close"].iloc[-21]) - 1) if len(d) > 20 else 0

    if intraday_df is not None and len(intraday_df) >= 20:
        high20   = float(intraday_df["high"].tail(20).max())
        low20    = float(intraday_df["low"].tail(20).min())
        typical  = (intraday_df["high"] + intraday_df["low"] + intraday_df["close"]) / 3
        vwap     = float((typical * intraday_df["volume"]).sum() / intraday_df["volume"].sum()) \
                   if intraday_df["volume"].sum() > 0 else close
        atr_5bar = float(tr.ewm(span=14, adjust=False).mean().iloc[-6]) if len(d) > 5 else atr
    else:
        high20   = float(d["high"].tail(10).max())
        low20    = float(d["low"].tail(10).min())
        vwap     = close
        atr_5bar = float(tr.ewm(span=14, adjust=False).mean().iloc[-6]) if len(d) > 5 else atr

    return dict(
        symbol=sym, close=close, prev_close=prev_close,
        gap_pct=gap_pct, atr=atr, atr_pct=atr/close if close else 0,
        adx=adx, pdi=pdi, ndi=ndi, ema25=ema25, ema_slope=ema_slope,
        vol_rank=vol_rank, vol_ratio=vol_ratio,
        mom_1day=mom_1day, mom_5day=mom_5day, mom_20day=mom_20day,
        high_20bar=high20, low_20bar=low20, vwap=vwap,
        atr_5bar_ago=atr_5bar,
        sector=sector_map.get(sym, "other"),
        trend_dir="up" if pdi > ndi else "down",
        rs_rank_in_sector=0.5,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY SCORERS
# ══════════════════════════════════════════════════════════════════════════════

def score_trend_momentum(f: dict) -> dict:
    adx, pdi, ndi = f.get("adx",0), f.get("pdi",0), f.get("ndi",0)
    vr, slope     = f.get("vol_rank",0.5), f.get("ema_slope",0)
    gap           = abs(f.get("gap_pct",0))
    close, ema    = f.get("close",0), f.get("ema25", f.get("close",0))
    atr_pct       = f.get("atr",0) / close if close else 0
    score, dir_   = 0, None

    if adx > 50:   score += 40
    elif adx > 35: score += 30
    elif adx > 25: score += 15
    else:          score += 5
    if vr < 0.25:  score -= 30
    elif vr < 0.35: score -= 15
    score += int(vr * 25)
    if close > ema*1.002 and pdi > ndi:  dir_ = "long";  score += 20
    elif close < ema*0.998 and ndi > pdi: dir_ = "short"; score += 20
    elif close > ema:                     dir_ = "long";  score += 10
    if abs(slope) > 0.003: score += 10
    elif abs(slope) > 0.001: score += 5
    sec_mom = f.get("sector_avg_mom1d", 0)
    if dir_ == "long"  and sec_mom >  0.005: score += 8
    elif dir_ == "short" and sec_mom < -0.005: score += 8
    if gap > 0.06:   score -= 20
    elif gap > 0.03: score -= 8
    if atr_pct < 0.002: score -= 15
    vr_params = (dict(trail=3.5, ema=28, dip=0.6, max_adds=2, entry_end="12:30") if vr > 0.67
                 else dict(trail=3.0, ema=25, dip=0.5, max_adds=2, entry_end="11:00") if vr > 0.33
                 else dict(trail=2.5, ema=20, dip=0.35, max_adds=3, entry_end="13:00"))
    return dict(score=max(0,min(100,score)), direction=dir_, params=vr_params,
                reason=f"ADX={adx:.0f} vr={vr:.2f} slope={slope*100:.3f}%/bar gap={gap*100:.1f}%",
                strategy="trend_momentum")


def score_gap_continuation(f: dict) -> dict:
    gap_pct, vol_r = f.get("gap_pct",0), f.get("vol_ratio",1)
    atr, close     = f.get("atr",0), f.get("close",1)
    adx, trend_dir = f.get("adx",0), f.get("trend_dir","neutral")
    atr_pct = atr / close
    dir_    = "long" if gap_pct > 0 else "short"
    if vol_r < 0.8:
        return dict(score=0, direction=None, params={},
                    reason=f"vol_ratio={vol_r:.1f}× insufficient", strategy="gap_continuation")
    gap_abs = abs(gap_pct)
    if gap_abs < 0.01:
        return dict(score=0, direction=None, params={},
                    reason=f"gap={gap_pct*100:+.1f}% too small for gap_continuation",
                    strategy="gap_continuation")
    score   = (45 if 0.02<=gap_abs<=0.04 else 35 if gap_abs<=0.06 else 20 if gap_abs<=0.08 else 5)
    if adx > 30:
        if dir_=="long"  and trend_dir=="down": score -= 30
        if dir_=="short" and trend_dir=="up":   score -= 30
    if adx > 25:
        if dir_=="long"  and trend_dir=="up":   score += 15
        if dir_=="short" and trend_dir=="down": score += 15
    score += (30 if vol_r>3 else 20 if vol_r>2 else 10 if vol_r>1.5 else 5 if vol_r>1 else 0)
    score += (10 if adx>40 else 5 if adx>25 else 0)
    score += (10 if atr_pct>0.03 else 5 if atr_pct>0.015 else 0)
    return dict(score=max(0,min(100,score)), direction=dir_,
                params=dict(trail=2.0, ema=9, dip=0.5, max_adds=1, entry_end="10:30", hold_max_mins=90),
                reason=f"gap={gap_pct*100:+.1f}% vol={vol_r:.1f}× trend={trend_dir} ADX={adx:.0f}",
                strategy="gap_continuation")


def score_vwap_bounce(f: dict) -> dict:
    vwap, close = f.get("vwap",0), f.get("close",0)
    adx, pdi, ndi = f.get("adx",0), f.get("pdi",0), f.get("ndi",0)
    vr, atr = f.get("vol_rank",0.5), f.get("atr",0)
    if not vwap or not close or not atr:
        return dict(score=0, direction=None, params={}, reason="no VWAP", strategy="vwap_bounce")
    dist_atr = abs(close-vwap)/atr if atr else 99
    score = (40 if 0.3<=dist_atr<=1.5 else 20 if dist_atr<0.3 else 5)
    dir_ = None
    if adx>30 and pdi>ndi and close>vwap:  dir_="long";  score+=25
    elif adx>30 and ndi>pdi and close<vwap: dir_="short"; score+=25
    elif adx>20: dir_="long" if pdi>ndi else "short"; score+=10
    score += (20 if 0.30<vr<0.70 else 10 if vr<0.30 else 5)
    if abs(f.get("gap_pct",0)) > 0.03: score -= 15
    return dict(score=max(0,min(100,score)), direction=dir_,
                params=dict(trail=2.0, ema=25, dip=0.3, max_adds=2, entry_end="14:00"),
                reason=f"dist_vwap={dist_atr:.1f}×ATR ADX={adx:.0f} vr={vr:.2f}",
                strategy="vwap_bounce")


def score_range_breakout(f: dict) -> dict:
    high20, low20 = f.get("high_20bar",0), f.get("low_20bar",0)
    close, atr    = f.get("close",0), f.get("atr",0)
    atr_prev      = f.get("atr_5bar_ago", atr)
    vol_r, vr, adx = f.get("vol_ratio",1), f.get("vol_rank",0.5), f.get("adx",0)
    if not high20 or not close or not atr:
        return dict(score=0, direction=None, params={}, reason="insufficient data", strategy="range_breakout")
    range_size  = (high20-low20)/close if close else 0
    atr_expand  = atr/atr_prev if atr_prev else 1
    dist_top    = (close-high20)/atr if atr else 0
    dist_bot    = (low20-close)/atr  if atr else 0
    score, dir_ = 0, None
    if -0.1<=dist_top<=0.5:   dir_="long";  score+=35
    elif 0.5<dist_top<=1.5:   dir_="long";  score+=20
    elif -0.1<=dist_bot<=0.5: dir_="short"; score+=35
    elif 0.5<dist_bot<=1.5:   dir_="short"; score+=20
    score += (25 if atr_expand>1.3 else 15 if atr_expand>1.1 else -10 if atr_expand<0.9 else 0)
    score += (25 if vol_r>2.5 else 15 if vol_r>1.5 else 5 if vol_r>1.0 else 0)
    score += (15 if range_size<0.02 else 8 if range_size<0.04 else 0)
    score += (10 if adx<20 else 5 if adx<30 else 0)
    return dict(score=max(0,min(100,score)), direction=dir_,
                params=dict(trail=2.5, ema=20, dip=0.4, max_adds=2, entry_end="13:00"),
                reason=f"range={range_size*100:.1f}% atr_exp={atr_expand:.2f}× vol={vol_r:.1f}×",
                strategy="range_breakout")


def score_relative_strength(sym: str, f: dict, sector_scores: dict) -> dict:
    sector   = f.get("sector")
    rs_rank  = f.get("rs_rank_in_sector", 0.5)
    sec_adx  = sector_scores.get(sector, {}).get("adx", 0) if sector else 0
    mom5d    = f.get("mom_5day", 0)
    vol_r    = f.get("vol_ratio", 1)
    score, dir_ = 0, None
    if rs_rank>0.85:   dir_="long";  score+=40
    elif rs_rank>0.70: dir_="long";  score+=25
    elif rs_rank<0.15: dir_="short"; score+=40
    elif rs_rank<0.30: dir_="short"; score+=25
    score += (25 if sec_adx>40 else 15 if sec_adx>25 else 0)
    if dir_=="long"   and mom5d> 0.03: score+=20
    elif dir_=="short" and mom5d<-0.03: score+=20
    elif dir_ and abs(mom5d)>0.01:      score+=8
    score += (15 if vol_r>1.5 else 7 if vol_r>1.0 else 0)
    return dict(score=max(0,min(100,score)), direction=dir_,
                params=dict(trail=3.0, ema=25, dip=0.5, max_adds=1, entry_end="12:00"),
                reason=f"rs_rank={rs_rank:.2f} sector={sector} mom5d={mom5d*100:+.1f}% vol={vol_r:.1f}×",
                strategy="relative_strength")


def score_explosive(f: dict) -> dict:
    adx       = f.get("adx", 0)
    vr        = f.get("vol_rank", 0.5)
    slope     = f.get("ema_slope", 0)
    gap       = f.get("gap_pct", 0)
    mom5d     = f.get("mom_5day", 0)
    atr_pct   = f.get("atr_pct", 0)
    close     = f.get("close", 0)
    ema       = f.get("ema25", close)
    trend_dir = f.get("trend_dir", "neutral")
    vol_r     = f.get("vol_ratio", 1)
    direction = "long" if trend_dir == "up" else "short"
    score     = 0

    if adx>55:   score+=35
    elif adx>40: score+=27
    elif adx>30: score+=18
    elif adx>20: score+=8
    if vr>0.80:  score+=30
    elif vr>0.65: score+=22
    elif vr>0.50: score+=14
    elif vr<0.30: score-=20
    if direction=="long":
        if mom5d>0.08: score+=20
        elif mom5d>0.04: score+=13
        elif mom5d>0.01: score+=6
    else:
        if mom5d<-0.08: score+=20
        elif mom5d<-0.04: score+=13
        elif mom5d<-0.01: score+=6
    if direction=="long":
        if slope>0.006: score+=10
        elif slope>0.002: score+=5
    else:
        if slope<-0.006: score+=10
        elif slope<-0.002: score+=5
    if direction=="long"  and gap>0.02: score+=8
    elif direction=="short" and gap<-0.02: score+=8
    if adx>35:
        if direction=="long"  and gap<-0.04: score-=18
        if direction=="short" and gap>0.04:  score-=18
    if atr_pct>0.08: score+=10
    elif atr_pct>0.05: score+=6
    elif atr_pct>0.03: score+=3
    if vol_r>2.0: score+=7
    elif vol_r>1.3: score+=3
    if direction=="long"  and close>ema*1.001: score+=5
    elif direction=="short" and close<ema*0.999: score+=5

    return dict(score=max(0,min(100,score)), direction=direction,
                params=dict(trail=1.5, ema=25, entry_end="11:30", target_r=3.0),
                reason=(f"ADX={adx:.0f} vr={vr:.2f} mom5d={mom5d*100:+.1f}% "
                        f"slope={slope*100:.3f}%/bar atr%={atr_pct*100:.1f}% gap={gap*100:+.1f}%"),
                strategy="explosive")


# ══════════════════════════════════════════════════════════════════════════════
#  R-EXPECTANCY + POSITION SIZING
# ══════════════════════════════════════════════════════════════════════════════

STRAT_EDGE = {
    "trend_momentum":    dict(wr=0.44, avg_r_win=3.5, avg_r_loss=1.0),
    "gap_continuation":  dict(wr=0.50, avg_r_win=1.8, avg_r_loss=1.0),
    "vwap_bounce":       dict(wr=0.48, avg_r_win=2.0, avg_r_loss=1.0),
    "range_breakout":    dict(wr=0.40, avg_r_win=3.5, avg_r_loss=1.0),
    "relative_strength": dict(wr=0.46, avg_r_win=2.5, avg_r_loss=1.0),
}

def r_expectancy(strategy: str, score: float) -> float:
    sp   = STRAT_EDGE.get(strategy, dict(wr=0.40, avg_r_win=2.0, avg_r_loss=1.0))
    er   = sp["wr"]*sp["avg_r_win"] - (1-sp["wr"])*sp["avg_r_loss"]
    conf = (score/100)**1.3
    return round(er*conf, 4)

def expected_pnl(score, atr, close, account_size, strategy, trail=3.0):
    sp       = STRAT_EDGE.get(strategy, dict(wr=0.40, avg_r_win=2.0, avg_r_loss=1.0))
    risk_amt = account_size * 0.02
    stop_d   = trail * atr
    shares   = risk_amt/stop_d if stop_d else 0
    er       = sp["wr"]*sp["avg_r_win"] - (1-sp["wr"])*sp["avg_r_loss"]
    conf     = (score/100)**1.3
    return round(shares*atr*er*conf, 2)

def aggressive_position(score: float, atr: float, close: float,
                        equity: float, max_pct: float = 0.40) -> dict:
    if score>=88:   pct = min(max_pct, 1.0)
    elif score>=78: pct = min(max_pct*0.75, 1.0)
    elif score>=65: pct = min(max_pct*0.50, 1.0)
    else:           pct = 0.0
    notional  = equity*pct
    shares    = notional/close if close else 0
    stop_dist = 1.5*atr
    tgt_dist  = 3.0*atr
    max_loss  = shares*stop_dist
    max_gain  = shares*tgt_dist
    return dict(pct=pct, notional=round(notional,2), shares=round(shares,4),
                stop_dist=round(stop_dist,4), tgt_dist=round(tgt_dist,4),
                max_loss=round(max_loss,2), max_gain=round(max_gain,2),
                loss_pct=round(max_loss/equity*100,1) if equity else 0,
                gain_pct=round(max_gain/equity*100,1) if equity else 0)


# ══════════════════════════════════════════════════════════════════════════════
#  SECTOR ENRICHMENT HELPER
# ══════════════════════════════════════════════════════════════════════════════

def enrich_with_sector(all_feats: dict):
    sec_moms, sec_adxs = {}, {}
    for sym, feat in all_feats.items():
        sec = feat["sector"]
        sec_moms.setdefault(sec, []).append((sym, feat["mom_1day"]))
        sec_adxs.setdefault(sec, []).append(feat["adx"])
    sec_avg_mom, sec_avg_adx = {}, {}
    for sec, entries in sec_moms.items():
        sec_avg_mom[sec] = float(np.mean([v for _,v in entries]))
        n = len(entries)
        for ri, (sym, _) in enumerate(sorted(entries, key=lambda x: x[1])):
            all_feats[sym]["rs_rank_in_sector"] = ri/max(n-1,1) if n>1 else 0.5
    for sec, adxs in sec_adxs.items():
        sec_avg_adx[sec] = float(np.mean(adxs))
    for sym, feat in all_feats.items():
        feat["sector_avg_mom1d"] = sec_avg_mom.get(feat["sector"], 0)
        feat["sector_avg_adx"]   = sec_avg_adx.get(feat["sector"], 0)
    return {sec: {"adx": adx} for sec, adx in sec_avg_adx.items()}


# ══════════════════════════════════════════════════════════════════════════════
#  SWARM TOOL FUNCTIONS
#  These are the functions agents call.  Keep return types JSON strings.
# ══════════════════════════════════════════════════════════════════════════════

def get_earnings_calendar(symbols: str = "SOXL,AMD,TQQQ,NVDA,ARM,MARA,RIOT") -> str:
    """
    Return next earnings date, days until earnings, and EPS estimate range for each symbol.
    Earnings within 1-5 days = pre-earnings momentum window (historically strong).
    Earnings today or tomorrow = high risk of overnight gap — size down or avoid.
    Leveraged ETFs (SOXL, TQQQ etc) have no earnings; focus is on single-name holdings.
    """
    sym_list = [s.strip().upper() for s in symbols.split(",")][:10]
    today    = pd.Timestamp.now().normalize()   # tz-naive to match yfinance dates
    results  = {}

    for sym in sym_list:
        try:
            t   = yf.Ticker(sym)
            cal = t.calendar or {}
            dates = cal.get("Earnings Date", [])
            if dates:
                ed       = pd.Timestamp(dates[0])
                days_out = int((ed - today).days)
                # classify the setup
                if days_out < 0:
                    window = "just_reported"
                elif days_out == 0:
                    window = "TODAY — avoid or size tiny"
                elif days_out == 1:
                    window = "TOMORROW — gap risk high"
                elif days_out <= 5:
                    window = "pre_earnings_momentum_window"
                elif days_out <= 14:
                    window = "upcoming_soon"
                else:
                    window = "not_imminent"

                results[sym] = {
                    "next_earnings":  str(ed.date()),
                    "days_away":      days_out,
                    "window":         window,
                    "eps_est_low":    cal.get("Earnings Low"),
                    "eps_est_high":   cal.get("Earnings High"),
                    "eps_est_avg":    cal.get("Earnings Average"),
                    "rev_est_avg":    cal.get("Revenue Average"),
                }
            else:
                results[sym] = {"next_earnings": None, "days_away": None,
                                 "window": "no_data_or_etf"}
        except Exception as e:
            results[sym] = {"next_earnings": None, "days_away": None, "window": f"error: {e}"}

    # summary: symbols with imminent earnings
    hot = [s for s, v in results.items()
           if v.get("window","") in ("pre_earnings_momentum_window",
                                     "TODAY — avoid or size tiny",
                                     "TOMORROW — gap risk high")]
    return json.dumps({
        "as_of": today.strftime("%Y-%m-%d"),
        "imminent_earnings_symbols": hot,
        "detail": results,
    }, indent=2, default=str)


def check_earnings_gate(symbol: str) -> str:
    """
    Hard earnings gate for a single candidate symbol.
    Returns gate_verdict BLOCK if earnings are today or tomorrow (US/Eastern),
    PASS if not imminent, or UNCHECKED if the date could not be fetched.
    Fails open: UNCHECKED does NOT block the trade.
    """
    import pytz
    symbol = symbol.strip().upper()
    tz_et = pytz.timezone("America/New_York")
    today_et = datetime.now(tz_et).date()

    def _fetch():
        t = yf.Ticker(symbol)
        cal = t.calendar or {}
        return cal.get("Earnings Date", [])

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_fetch)
            dates = fut.result(timeout=8)

        if not dates:
            print(f"  [earnings_gate] {symbol}: no earnings date in yfinance — trade unchecked")
            return json.dumps({
                "symbol": symbol, "gate_verdict": "UNCHECKED",
                "reason": "No earnings date available in yfinance; trade allowed (fail open).",
            })

        next_ed = pd.Timestamp(dates[0]).date()
        days_away = (next_ed - today_et).days

        if days_away in (0, 1):
            label = "TODAY" if days_away == 0 else "TOMORROW"
            reason = (f"{symbol} has earnings {label} ({next_ed}). "
                      "Hard gate: reject to avoid overnight gap risk.")
            print(f"  [earnings_gate] BLOCK — {reason}")
            return json.dumps({
                "symbol": symbol, "gate_verdict": "BLOCK",
                "earnings_date": str(next_ed), "days_away": days_away,
                "reason": reason,
            })

        return json.dumps({
            "symbol": symbol, "gate_verdict": "PASS",
            "earnings_date": str(next_ed), "days_away": days_away,
            "reason": f"Earnings {days_away} days away — no immediate gap risk.",
        })

    except concurrent.futures.TimeoutError:
        print(f"  [earnings_gate] {symbol}: yfinance timeout — trade unchecked")
        return json.dumps({
            "symbol": symbol, "gate_verdict": "UNCHECKED",
            "reason": "yfinance earnings fetch timed out; trade allowed (fail open).",
        })
    except Exception as e:
        print(f"  [earnings_gate] {symbol}: error ({e}) — trade unchecked")
        return json.dumps({
            "symbol": symbol, "gate_verdict": "UNCHECKED",
            "reason": f"Could not fetch earnings date ({e}); trade allowed (fail open).",
        })


_IV_CACHE: dict = {}          # sym -> (timestamp, result_dict)
_IV_CACHE_TTL = 3600          # seconds — re-fetch IV data at most once per hour

def _fetch_iv_one(sym: str) -> tuple:
    """Fetch IV/HV data for one symbol. Returns (sym, result_dict)."""
    now_ts = time.time()
    cached = _IV_CACHE.get(sym)
    if cached and (now_ts - cached[0]) < _IV_CACHE_TTL:
        return sym, cached[1]
    try:
        t     = yf.Ticker(sym)
        price = t.fast_info.get("lastPrice") or t.fast_info.get("regularMarketPrice")
        if not price: raise ValueError("no price")
        exps   = t.options
        atm_iv = None
        for exp in exps[:3]:
            try:
                chain = t.option_chain(exp)
                calls = chain.calls.dropna(subset=["impliedVolatility","volume"])
                calls = calls[calls["volume"] > 0]
                if calls.empty: continue
                row   = calls.iloc[(calls["strike"] - price).abs().argsort().iloc[0]]
                iv_v  = float(row["impliedVolatility"])
                if iv_v > 0.01: atm_iv = round(iv_v * 100, 1); break
            except Exception: continue
        hist = yf.download(sym, period="1y", progress=False, auto_adjust=True)
        if len(hist) < 30: raise ValueError("short history")
        close_s     = hist["Close"].squeeze()
        log_ret     = np.log(close_s / close_s.shift(1)).dropna()
        hv20_series = (log_ret.rolling(20).std() * np.sqrt(252) * 100).dropna()
        hv_now      = float(hv20_series.iloc[-1])
        hv_rank     = float((hv20_series < hv_now).mean() * 100)
        iv_hv_spread = round(atm_iv - hv_now, 1) if atm_iv else None
        if atm_iv and atm_iv > hv_now * 1.3 and hv_rank > 70:
            signal = "ELEVATED_IV — big move priced in"
        elif atm_iv and atm_iv < hv_now * 0.85:
            signal = "CHEAP_IV — options cheap, market underpricing vol"
        elif hv_rank < 25:
            signal = "LOW_VOL_COILING — compression, potential breakout"
        elif hv_rank > 75:
            signal = "HIGH_VOL_EXPANSION — trending, momentum valid"
        else:
            signal = "NEUTRAL"
        result = {"price": round(price,2), "atm_iv": atm_iv,
                  "hv20": round(hv_now,1), "iv_hv_spread": iv_hv_spread,
                  "hv_rank": round(hv_rank,0), "signal": signal}
        _IV_CACHE[sym] = (now_ts, result)
        return sym, result
    except Exception as e:
        return sym, {"error": str(e)}


def get_iv_rank(symbols: str = "NVDA,AMD,ARM,MARA,RIOT,MRNA,CRWD,COIN") -> str:
    """
    For each symbol return ATM IV, HV20, IV/HV spread, HV rank percentile, signal.
    Results are cached for 1 hour — fetched in parallel for speed.

    High hv_rank (>75) + rising iv = vol expansion, big move coming.
    Low hv_rank (<25) = stock coiling, spring loaded.
    High iv_hv_spread = institutions paying up, something coming.
    """
    from concurrent.futures import ThreadPoolExecutor
    sym_list = [s.strip().upper() for s in symbols.split(",")][:8]
    results  = {}

    with ThreadPoolExecutor(max_workers=6) as pool:
        for sym, res in pool.map(_fetch_iv_one, sym_list):
            results[sym] = res

    return json.dumps({"iv_rank_data": results}, indent=2)


def get_unusual_options_activity(symbols: str = "NVDA,AMD,SOXL,MARA,RIOT,CRWD,COIN,ARM") -> str:
    """
    Detect unusual options activity: single-day call or put volume that is abnormally
    high relative to open interest. Institutions and informed traders leave footprints
    in the options market before big moves.

    vol_oi_ratio > 0.5 = heavy single-day buying relative to existing positions.
    Bullish sweep = high call vol/OI with strikes above current price (out-of-money calls).
    Bearish sweep = high put vol/OI with strikes below current price.
    """
    sym_list = [s.strip().upper() for s in symbols.split(",")][:8]
    results  = {}

    for sym in sym_list:
        try:
            t     = yf.Ticker(sym)
            price = t.fast_info.get("lastPrice") or t.fast_info.get("regularMarketPrice")
            if not price: continue
            exps  = t.options
            if not exps: continue

            chain = t.option_chain(exps[0])
            calls = chain.calls.dropna(subset=["volume","openInterest"])
            puts  = chain.puts.dropna(subset=["volume","openInterest"])

            total_call_vol = int(calls["volume"].sum())
            total_call_oi  = int(calls["openInterest"].sum())
            total_put_vol  = int(puts["volume"].sum())
            total_put_oi   = int(puts["openInterest"].sum())

            call_ratio = total_call_vol / total_call_oi if total_call_oi else 0
            put_ratio  = total_put_vol  / total_put_oi  if total_put_oi  else 0
            pc_ratio   = total_put_vol  / total_call_vol if total_call_vol else 1

            # Find the single most active strike
            calls_sorted = calls.sort_values("volume", ascending=False).head(1)
            puts_sorted  = puts.sort_values("volume",  ascending=False).head(1)
            top_call = (f"{calls_sorted['strike'].iloc[0]}c "
                        f"vol={int(calls_sorted['volume'].iloc[0]):,} "
                        f"OI={int(calls_sorted['openInterest'].iloc[0]):,}"
                        ) if len(calls_sorted) else "—"
            top_put  = (f"{puts_sorted['strike'].iloc[0]}p "
                        f"vol={int(puts_sorted['volume'].iloc[0]):,} "
                        f"OI={int(puts_sorted['openInterest'].iloc[0]):,}"
                        ) if len(puts_sorted) else "—"

            # Interpret
            if call_ratio > 0.5 and pc_ratio < 0.5:
                bias = "BULLISH_SWEEP — heavy call buying, smart money positioning long"
            elif put_ratio > 0.5 and pc_ratio > 1.5:
                bias = "BEARISH_SWEEP — heavy put buying, downside protection or short bet"
            elif call_ratio > 0.3:
                bias = "CALL_INTEREST — elevated call activity, mildly bullish"
            else:
                bias = "NEUTRAL"

            unusual = call_ratio > 0.4 or put_ratio > 0.4

            results[sym] = {
                "price":          round(price, 2),
                "call_vol":       total_call_vol, "call_oi": total_call_oi,
                "put_vol":        total_put_vol,  "put_oi":  total_put_oi,
                "call_vol_oi":    round(call_ratio, 3),
                "put_vol_oi":     round(put_ratio,  3),
                "put_call_ratio": round(pc_ratio,   2),
                "top_call_strike": top_call,
                "top_put_strike":  top_put,
                "unusual":         unusual,
                "bias":            bias,
            }
        except Exception as e:
            results[sym] = {"error": str(e)}

    unusual_syms = [s for s, v in results.items() if v.get("unusual")]
    return json.dumps({
        "expiry_used": "nearest",
        "unusual_activity_symbols": unusual_syms,
        "detail": results,
    }, indent=2)


SWARM_STATE_FILE  = Path("swarm_state.json")
TRIGGER_FILE      = Path("portfolio_trigger.json")


def _enrich_signals(top_signals: list) -> list:
    """Attach earnings, IV rank, options flow, compression coil, and news to scanner results."""
    if not top_signals:
        return []
    # Crypto tickers have no earnings/IV/options data — skip to avoid 404 spam
    equity_syms = [s["symbol"] for s in top_signals[:8] if "-USD" not in s["symbol"]]
    syms = ",".join(equity_syms) if equity_syms else ""
    earn_data = {"detail": {}}
    iv_data   = {"iv_rank_data": {}}
    opt_data  = {"detail": {}}
    coil_map  = {}   # symbol -> coil_score from detect_compression_breakouts
    news_data = {}   # symbol -> list of {headline, sentiment, created_at}
    if syms:
        try: earn_data = json.loads(get_earnings_calendar(syms))
        except: pass
        try: iv_data   = json.loads(get_iv_rank(syms))
        except: pass
        try: opt_data  = json.loads(get_unusual_options_activity(syms))
        except: pass
        try:
            for row in json.loads(detect_compression_breakouts()).get("compression_setups", []):
                coil_map[row["symbol"]] = row["coil_score"]
        except: pass
        try: news_data = json.loads(get_news_catalysts(syms))
        except: pass

    enriched = []
    for sig in top_signals:
        sym  = sig["symbol"]
        earn = earn_data["detail"].get(sym, {})
        iv   = iv_data["iv_rank_data"].get(sym, {})
        opt  = opt_data["detail"].get(sym, {})

        ed   = earn.get("days_away")
        cat  = 0
        if ed is not None:
            if 2 <= ed <= 5:   cat += 2
            elif ed in (0, 1): cat -= 2   # gap risk
        iv_sig = iv.get("signal", "NEUTRAL")
        if "LOW_VOL_COILING"    in iv_sig: cat += 2
        if "HIGH_VOL_EXPANSION" in iv_sig: cat += 2
        bias = opt.get("bias", "NEUTRAL")
        if bias.startswith("BULLISH_SWEEP"): cat += 2
        elif bias.startswith("BEARISH_SWEEP"): cat -= 1

        # +3 pts compression coil >= 60 per prompt spec
        coil_score = coil_map.get(sym, 0)
        if coil_score >= 60:
            cat += 3

        # +1 pt bullish news catalyst per prompt spec
        if any(n.get("sentiment") == "bullish" for n in news_data.get(sym, [])):
            cat += 1

        # +1 pt high IV vs HV spread (institutions paying up) per prompt spec
        iv_hv_spread = iv.get("iv_hv_spread")
        if iv_hv_spread is not None and iv_hv_spread > 10:
            cat += 1

        enriched.append({**sig,
            "catalyst_score":  cat,
            "earnings_days":   ed,
            "earnings_window": earn.get("window", "no_data_or_etf"),
            "hv_rank":         iv.get("hv_rank", 50),
            "iv_signal":       iv_sig,
            "atm_iv":          iv.get("atm_iv"),
            "iv_hv_spread":    iv_hv_spread,
            "options_bias":    bias,
            "call_vol_oi":     opt.get("call_vol_oi", 0),
            "put_call_ratio":  opt.get("put_call_ratio", 1),
            "coil_score":      coil_score,
        })

    enriched.sort(key=lambda x: x["score"] + x["catalyst_score"] * 4, reverse=True)
    return enriched


def run_portfolio_cycle(account_size: float = 500.0, max_positions: int = 5,
                        slot_pct: float = 0.20, live: bool = False,
                        universe: list | None = None) -> dict:
    """
    Full portfolio management cycle — meant to run every 30 min during market hours.
    Manages up to max_positions slots, writing rich reasoning to swarm_state.json.

    Logic:
      1. Get market regime (sets risk budget).
      2. Scan explosive universe → enrich with earnings/IV/options signals.
      3. Compare against existing positions:
         - Exit if score < 55 OR earnings gap risk (0-1 days).
         - Hold if still strong signal.
         - Enter new slot from top enriched signals not already held.
      4. Writes swarm_state.json with full per-position reasoning.
    """
    alp_raw      = []
    alp_acct     = {}
    alpaca_pos_map = {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M ET")
    log_entries  = []
    is_crypto_session = (universe is not None and universe is not EXPLOSIVE_UNIVERSE
                         and all("-USD" in s for s in (universe or [])))
    session_label = "CRYPTO 24/7" if is_crypto_session else "MARKET HOURS"

    # ── Load existing state ───────────────────────────────────────────────────
    existing_positions   = []
    existing_log         = []
    existing_equity_history = []
    existing_closed_trades  = []
    realized_pnl         = 0.0
    if SWARM_STATE_FILE.exists():
        try:
            prev = json.loads(SWARM_STATE_FILE.read_text())
            existing_positions      = prev.get("portfolio", {}).get("positions", [])
            existing_log            = prev.get("swarm_log", [])
            existing_equity_history = prev.get("equity_history", [])
            existing_closed_trades  = prev.get("closed_trades", [])
            realized_pnl            = prev.get("realized_pnl", 0.0)
        except Exception:
            pass

    # ── If live: fetch Alpaca positions as ground truth ───────────────────────
    if live:
        alp_raw       = get_alpaca_positions()
        alp_acct      = get_alpaca_account()
        pending_sells = get_alpaca_pending_sells()   # symbols with open sell orders
        for ap in alp_raw:
            sym_raw = ap["symbol"]
            sym = sym_raw
            for yf_sym, alp_sym in CRYPTO_ALPACA.items():
                if alp_sym == sym_raw:
                    sym = yf_sym
                    break
            alpaca_pos_map[sym] = ap
        # Rebuild existing_positions from Alpaca truth, keeping state metadata.
        # Skip symbols with pending sell orders — they're already being exited.
        state_by_sym = {p["symbol"]: p for p in existing_positions}
        merged = []
        for sym, ap in alpaca_pos_map.items():
            alpaca_sym = CRYPTO_ALPACA.get(sym, sym)
            if alpaca_sym in pending_sells:
                continue  # sell already queued — don't re-process this position
            real_entry = float(ap["avg_entry_price"])   # actual fill price
            if sym in state_by_sym:
                p = dict(state_by_sym[sym])
                p["entry"]         = real_entry          # correct to actual fill price
                p["current_price"] = float(ap["current_price"])
                p["shares"]        = float(ap["qty"])
                p["notional"]      = abs(float(ap["market_value"]))
            else:
                cur_px = float(ap["current_price"])
                qty    = float(ap["qty"])
                p = {
                    "symbol": sym, "direction": "long", "is_rocket": False,
                    "entry": real_entry, "current_price": cur_px,
                    "shares": qty, "notional": abs(float(ap["market_value"])),
                    "stop": round(real_entry * 0.90, 4),
                    "target": round(real_entry * 1.10, 4),
                    "unrealized_pnl": float(ap["unrealized_pl"]),
                    "unrealized_pct": float(ap.get("unrealized_plpc", 0)) * 100,
                    "entry_time": now, "scanner_score": 50, "catalyst_score": 0,
                    "earnings_days": None, "earnings_window": "unknown",
                    "hv_rank": 0.0, "iv_signal": "", "atm_iv": None,
                    "options_bias": "", "call_vol_oi": 0.0,
                    "trade_thesis": "restored from Alpaca",
                    "signals": {},
                }
            merged.append(p)
        existing_positions = merged
        if alp_acct:
            alp_equity     = float(alp_acct.get("equity", 0))
            alp_unrealized = sum(float(ap["unrealized_pl"]) for ap in alp_raw)
            realized_pnl   = round(alp_equity - account_size - alp_unrealized, 2)

    # ── Process dashboard trigger commands ───────────────────────────────────
    if TRIGGER_FILE.exists():
        try:
            trigger_data = json.loads(TRIGGER_FILE.read_text())
            trigger_entries = trigger_data.get("entries", [])
            if trigger_entries:
                held_syms_now = {p["symbol"] for p in existing_positions}
                for entry in trigger_entries:
                    sym  = entry.get("symbol", "").upper()
                    side = entry.get("side", "long")
                    if not sym or sym in held_syms_now:
                        continue
                    if len(existing_positions) >= max_positions:
                        log_entries.append({"time": now, "action": f"TRIGGER {sym}",
                                            "reason": "at max positions, skipping"})
                        continue
                    daily = fetch_batch_daily([sym], n_days=30)
                    df = daily.get(sym)
                    if df is None or len(df) < 10:
                        log_entries.append({"time": now, "action": f"TRIGGER {sym}",
                                            "reason": "no data"})
                        continue
                    feat = compute_features(sym, df, None, {sym: "trigger"})
                    if not feat:
                        continue
                    atr       = feat["atr"]
                    close     = feat["close"]
                    direction = "long" if side == "long" else "short"
                    notional  = round(account_size * CORE_PCT, 2)
                    shares    = round(notional / close, 4) if close else 0
                    stop_px   = (round(close - atr * 2.0, 2) if direction == "long"
                                 else round(close + atr * 2.0, 2))
                    target_px = (round(close + atr * 2.5, 2) if direction == "long"
                                 else round(close - atr * 2.5, 2))
                    pos = {
                        "symbol": sym, "direction": direction, "is_rocket": False,
                        "entry": close, "current_price": close,
                        "stop": stop_px, "target": target_px,
                        "notional": notional, "shares": shares,
                        "unrealized_pnl": 0.0, "unrealized_pct": 0.0,
                        "entry_time": now, "scanner_score": 0, "catalyst_score": 0,
                        "earnings_days": None, "earnings_window": "dashboard_trigger",
                        "hv_rank": 0.0, "iv_signal": "", "atm_iv": None,
                        "options_bias": "", "call_vol_oi": 0.0,
                        "trade_thesis": f"dashboard trigger {direction}",
                        "signals": {},
                    }
                    existing_positions.append(pos)
                    held_syms_now.add(sym)
                    log_entries.append({"time": now,
                                        "action": f"TRIGGER ENTER {sym} {direction.upper()}",
                                        "reason": "dashboard command"})
                    if live and sym not in alpaca_pos_map:
                        try:
                            place_order(sym,
                                        "buy" if direction == "long" else "sell",
                                        notional)
                        except Exception as _oe:
                            log_entries[-1]["order_error"] = str(_oe)
            TRIGGER_FILE.write_text("{}")  # clear after processing
        except Exception:
            pass

    # ── Regime check ─────────────────────────────────────────────────────────
    try:
        regime_raw  = json.loads(get_market_regime())
        regime      = regime_raw["regime"]
        fear        = regime_raw["fear"]
        sector_lead = regime_raw["sector_leader"]
        regime_max  = regime_raw["recommended_max_position_pct"]
    except Exception:
        regime, fear, sector_lead, regime_max = "BULL_MODERATE", "NORMAL_FEAR", "BROAD", 0.20

    effective_pct = min(slot_pct, regime_max)

    # ── Scan + enrich ─────────────────────────────────────────────────────────
    active_universe = universe if universe is not None else EXPLOSIVE_UNIVERSE
    sector_map = {s: "explosive" for s in active_universe}
    daily_data = fetch_batch_daily(active_universe, n_days=30)
    raw_signals = []
    for sym, df in daily_data.items():
        if len(df) < 15: continue
        feat = compute_features(sym, df, None, sector_map)
        if not feat: continue
        ss  = score_explosive(feat)
        if ss["score"] < 45: continue
        pos = aggressive_position(ss["score"], feat["atr"], feat["close"],
                                   account_size, CORE_PCT)
        raw_signals.append({
            "symbol": sym, "score": ss["score"], "direction": ss["direction"],
            "close": round(feat["close"], 2), "atr": round(feat["atr"], 4),
            "atr_pct": round(feat["atr_pct"] * 100, 2),
            "adx": round(feat["adx"], 1), "vol_rank": round(feat["vol_rank"], 2),
            "mom_5day_pct": round(feat["mom_5day"] * 100, 1),
            "ema_slope_pct": round(feat["ema_slope"] * 100, 3),
            "reason": ss["reason"],
            "bet_pct": int(pos["pct"] * 100), "notional": pos["notional"],
            "stop_dist": pos["stop_dist"], "tgt_dist": pos["tgt_dist"],
        })
    raw_signals.sort(key=lambda x: (x["score"], x["adx"] * x["vol_rank"]), reverse=True)
    # Enrich top 14 — leaves enough candidates for both rocket + 4 core slots
    enriched = _enrich_signals(raw_signals[:14])

    # ── Identify rocket candidate ─────────────────────────────────────────────
    # Rocket = highest catalyst signal with stacked conviction (sweep + earnings window
    # or coil + sweep, etc.).  Gets 40% of capital; 4 core slots share the other 60%.
    _safe_windows = {"TODAY — avoid or size tiny", "TOMORROW — gap risk high"}
    rocket_candidate = next(
        (s for s in sorted(enriched,
                           key=lambda x: x["catalyst_score"] * 5 + x["score"], reverse=True)
         if s["catalyst_score"] >= ROCKET_CATALYST_MIN
         and s["adx"] >= ROCKET_ADX_MIN
         and s.get("earnings_window", "") not in _safe_windows),
        None)

    # ── Portfolio management ──────────────────────────────────────────────────
    kept  = []
    exits = []

    for pos in existing_positions:
        sym      = pos["symbol"]
        match    = next((e for e in enriched if e["symbol"] == sym), None)
        earn_win = pos.get("earnings_window", "")
        gap_risk = earn_win in _safe_windows
        is_rocket_pos   = pos.get("is_rocket", False)
        is_trigger_pos  = earn_win == "dashboard_trigger"
        exit_score_thresh = ROCKET_EXIT_SCORE if is_rocket_pos else 55
        catalyst_gone = (is_rocket_pos and match is not None
                         and match["catalyst_score"] < 2)

        # Trigger positions: hold until trail stop hit (don't exit on missing score)
        if is_trigger_pos and not gap_risk:
            cur_px    = match["close"] if match else pos.get("current_price", pos["entry"])
            hit_stop  = (pos["direction"] == "long"  and cur_px <= pos["stop"]) or \
                        (pos["direction"] == "short" and cur_px >= pos["stop"])
            hit_target = (pos["direction"] == "long"  and cur_px >= pos["target"]) or \
                         (pos["direction"] == "short" and cur_px <= pos["target"])
            if hit_stop or hit_target:
                exit_reason = "target_hit" if hit_target else "trail_stop"
                exit_px     = cur_px
                trade_pnl   = ((exit_px - pos["entry"]) * pos["shares"] if pos["direction"] == "long"
                               else (pos["entry"] - exit_px) * pos["shares"])
                realized_pnl += trade_pnl
                if live:
                    _close_side = "sell" if pos["direction"] == "long" else "buy"
                    try: place_order(sym, _close_side, pos["notional"])
                    except Exception as _se:
                        log_entries.append({"time": now, "action": f"SELL_ERROR {sym}",
                                            "reason": str(_se)})
                exits.append({"symbol": sym, "reason": exit_reason,
                              "realized_pnl": round(trade_pnl, 2)})
                existing_closed_trades.append({
                    "symbol": sym, "direction": pos["direction"], "is_rocket": False,
                    "entry": pos["entry"], "exit_px": round(exit_px, 4),
                    "shares": pos["shares"], "notional": pos["notional"],
                    "entry_time": pos.get("entry_time", ""), "exit_time": now,
                    "realized_pnl": round(trade_pnl, 2), "exit_reason": exit_reason,
                })
                log_entries.append({"time": now, "action": f"EXIT {sym}",
                                    "reason": f"{exit_reason}  pnl=${trade_pnl:+.2f}"})
            else:
                # Update current price and keep
                unreal = ((cur_px - pos["entry"]) * pos["shares"] if pos["direction"] == "long"
                          else (pos["entry"] - cur_px) * pos["shares"])
                kept.append({**pos, "current_price": cur_px,
                             "unrealized_pnl": round(unreal, 2),
                             "unrealized_pct": round(unreal / pos["notional"] * 100
                                                     if pos["notional"] else 0, 2)})
            continue

        if gap_risk or catalyst_gone or (match is None or match["score"] < exit_score_thresh):
            exit_reason = ("earnings_gap_risk" if gap_risk
                           else "catalyst_evaporated" if catalyst_gone
                           else f"score_dropped: {match['score'] if match else 0}")
            exit_px   = (next((e["close"] for e in enriched if e["symbol"] == sym), None)
                         or pos.get("current_price", pos["entry"]))
            trade_pnl = (exit_px - pos["entry"]) * pos["shares"] if pos["direction"] == "long" \
                        else (pos["entry"] - exit_px) * pos["shares"]
            realized_pnl += trade_pnl
            tag = "⚡ ROCKET EXIT" if is_rocket_pos else f"EXIT {sym}"
            if live:
                _close_side = "sell" if pos["direction"] == "long" else "buy"
                try:
                    place_order(sym, _close_side, pos["notional"])
                except Exception as _sell_err:
                    log_entries.append({"time": now, "action": f"SELL_ERROR {sym}",
                                        "reason": str(_sell_err)})
            exits.append({"symbol": sym, "reason": exit_reason,
                          "realized_pnl": round(trade_pnl, 2)})
            existing_closed_trades.append({
                "symbol":       sym,
                "direction":    pos["direction"],
                "is_rocket":    is_rocket_pos,
                "entry":        pos["entry"],
                "exit_px":      round(exit_px, 4),
                "shares":       pos["shares"],
                "notional":     pos["notional"],
                "entry_time":   pos.get("entry_time", ""),
                "exit_time":    now,
                "realized_pnl": round(trade_pnl, 2),
                "exit_reason":  exit_reason,
            })
            log_entries.append({"time": now, "action": tag if is_rocket_pos else f"EXIT {sym}",
                                 "reason": f"{exit_reason}  pnl=${trade_pnl:+.2f}"})
        else:
            cur_price  = match["close"]
            entry_px   = pos["entry"]
            unreal     = (cur_price - entry_px) * pos["shares"] if pos["direction"] == "long" \
                         else (entry_px - cur_price) * pos["shares"]
            unreal_pct = unreal / pos["notional"] * 100 if pos["notional"] else 0
            kept.append({**pos,
                "current_price":  cur_price,
                "unrealized_pnl": round(unreal, 2),
                "unrealized_pct": round(unreal_pct, 2),
                "scanner_score":  match["score"],
                "catalyst_score": match["catalyst_score"],
                "earnings_days":  match["earnings_days"],
                "earnings_window": match["earnings_window"],
                "hv_rank":        match["hv_rank"],
                "iv_signal":      match["iv_signal"],
                "options_bias":   match["options_bias"],
                "call_vol_oi":    match["call_vol_oi"],
            })

    held_syms       = {p["symbol"] for p in kept}
    rocket_held     = any(p.get("is_rocket") for p in kept)
    core_slots_held = sum(1 for p in kept if not p.get("is_rocket"))
    max_core        = max_positions - 1   # 4 core slots

    def _build_position(sig: dict, is_rocket: bool) -> dict:
        """Build a new position dict from an enriched signal."""
        pct       = min(ROCKET_PCT if is_rocket else CORE_PCT, regime_max)
        notional  = round(account_size * pct, 2)
        entry_px  = sig["close"]
        # Tighter stop for rocket: 1.5×ATR; core: 2×ATR
        atr = sig["atr"]
        stop_mult = 1.5 if is_rocket else 2.0
        tgt_mult  = 3.0 if is_rocket else 2.5
        if sig["direction"] == "long":
            stop_px   = round(entry_px - atr * stop_mult, 2)
            target_px = round(entry_px + atr * tgt_mult,  2)
        else:
            stop_px   = round(entry_px + atr * stop_mult, 2)
            target_px = round(entry_px - atr * tgt_mult,  2)
        shares    = round(notional / entry_px, 4)

        earn_win = sig.get("earnings_window", "")
        thesis_parts = []
        if is_rocket:
            thesis_parts.append("⚡ ROCKET")
        if sig["catalyst_score"] >= ROCKET_CATALYST_MIN:
            if sig["options_bias"].startswith("BULLISH_SWEEP"):
                thesis_parts.append(f"smart-money sweep ({sig['call_vol_oi']:.1f}× vol/OI)")
            if sig.get("earnings_days") and 2 <= sig["earnings_days"] <= 5:
                thesis_parts.append(f"earnings in {sig['earnings_days']}d")
            if "LOW_VOL_COILING" in sig["iv_signal"]:
                thesis_parts.append("ATR coil breaking out")
            if "HIGH_VOL_EXPANSION" in sig["iv_signal"]:
                thesis_parts.append("vol expansion confirms")
        thesis_parts.append(f"ADX={sig['adx']:.0f} score={sig['score']}")
        thesis = " + ".join(thesis_parts)

        return {
            "symbol":          sig["symbol"],
            "direction":       sig["direction"],
            "is_rocket":       is_rocket,
            "entry":           entry_px,
            "current_price":   entry_px,
            "stop":            stop_px,
            "target":          target_px,
            "notional":        notional,
            "shares":          shares,
            "unrealized_pnl":  0.0,
            "unrealized_pct":  0.0,
            "entry_time":      now,
            "scanner_score":   sig["score"],
            "catalyst_score":  sig["catalyst_score"],
            "earnings_days":   sig["earnings_days"],
            "earnings_window": earn_win,
            "hv_rank":         sig["hv_rank"],
            "iv_signal":       sig["iv_signal"],
            "atm_iv":          sig.get("atm_iv"),
            "options_bias":    sig["options_bias"],
            "call_vol_oi":     sig["call_vol_oi"],
            "trade_thesis":    thesis,
            "signals": {
                "scanner":  f"score={sig['score']}, ADX={sig['adx']:.0f}, "
                            f"vol_rank={sig['vol_rank']:.2f}, mom5d={sig['mom_5day_pct']:+.1f}%",
                "catalyst": f"{sig['options_bias']}, "
                            f"earnings={sig['earnings_days']}d ({earn_win}), "
                            f"IV={sig['iv_signal']}, HV_rank={sig['hv_rank']:.0f}th",
                "regime":   f"{regime}, fear={fear}, leader={sector_lead}",
                "sizing":   f"${notional:.0f} ({'ROCKET 40%' if is_rocket else f'core {int(pct*100)}%'} "
                            f"of ${account_size:.0f}), stop=${stop_px:.2f}, target=${target_px:.2f}",
            },
        }

    # ── Fill rocket slot (40%) ────────────────────────────────────────────────
    if not rocket_held and rocket_candidate and rocket_candidate["symbol"] not in held_syms:
        pos = _build_position(rocket_candidate, is_rocket=True)
        kept.append(pos)
        held_syms.add(rocket_candidate["symbol"])
        rocket_held = True
        log_entries.append({"time": now,
                             "action": f"⚡ ROCKET {rocket_candidate['symbol']} {rocket_candidate['direction'].upper()}",
                             "reason": pos["trade_thesis"]})
        if live and rocket_candidate["symbol"] not in alpaca_pos_map:
            try: place_order(rocket_candidate["symbol"],
                             "buy" if rocket_candidate["direction"] == "long" else "sell",
                             pos["notional"])
            except Exception as e: log_entries[-1]["order_error"] = str(e)

    # ── Fill core slots (15% each, up to 4) ──────────────────────────────────
    for sig in enriched:
        if core_slots_held >= max_core: break
        sym = sig["symbol"]
        if sym in held_syms: continue
        if sig.get("earnings_window", "") in _safe_windows: continue
        if sig["score"] < 60: continue

        pos = _build_position(sig, is_rocket=False)
        kept.append(pos)
        held_syms.add(sym)
        core_slots_held += 1
        log_entries.append({"time": now,
                             "action": f"ENTER {sym} {sig['direction'].upper()}",
                             "reason": pos["trade_thesis"]})
        if live and sym not in alpaca_pos_map:
            try: place_order(sym, "buy" if sig["direction"] == "long" else "sell", pos["notional"])
            except Exception as e: log_entries[-1]["order_error"] = str(e)

    # ── Equity accounting ────────────────────────────────────────────────────
    if live and alp_acct:
        total_equity     = round(float(alp_acct.get("equity", account_size)), 2)
        unrealized_total = round(sum(float(ap["unrealized_pl"]) for ap in alp_raw), 2)
    else:
        unrealized_total = sum(p.get("unrealized_pnl", 0.0) for p in kept)
        total_equity     = round(account_size + realized_pnl + unrealized_total, 2)
    new_equity_pt    = {"time": now, "equity": total_equity,
                        "realized": round(realized_pnl, 2),
                        "unrealized": round(unrealized_total, 2)}
    equity_history   = (existing_equity_history + [new_equity_pt])[-500:]  # keep ~1 week

    # ── Write state ───────────────────────────────────────────────────────────
    new_log = (log_entries + existing_log)[:50]   # keep last 50 decisions
    closed_trades = existing_closed_trades[-500:]  # keep ~1 year of trades
    state = {
        "last_run":       now,
        "session":        session_label,
        "regime":         regime,
        "fear":           fear,
        "sector_leader":  sector_lead,
        "slot_pct":       effective_pct,
        "account_size":   account_size,
        "realized_pnl":   round(realized_pnl, 2),
        "portfolio": {
            "equity":        total_equity,
            "num_positions": len(kept),
            "positions":     kept,
            "recent_exits":  exits,
        },
        "equity_history":  equity_history,
        "closed_trades":   closed_trades,
        "signal_board":    enriched[:15],
        "swarm_log":       new_log,
    }
    _tmp = SWARM_STATE_FILE.with_suffix(".json.tmp")
    _tmp.write_text(json.dumps(state, indent=2, default=str))
    os.replace(_tmp, SWARM_STATE_FILE)
    rocket_pos = next((p for p in kept if p.get("is_rocket")), None)
    rocket_str = (f"  ROCKET={rocket_pos['symbol']} ${rocket_pos['notional']:.0f} "
                  f"({rocket_pos['unrealized_pct']:+.1f}%)" if rocket_pos else "  no rocket signal")
    pnl_sign   = "+" if total_equity >= account_size else ""
    print(f"  [{now}] Equity: ${total_equity:.2f} ({pnl_sign}{total_equity - account_size:.2f})"
          f"{rocket_str}  |  "
          f"Positions: {len(kept)}  |  Regime: {regime}")
    return state


def run_portfolio_loop(account_size: float = 500.0, max_positions: int = 5,
                       interval_mins: int = 10, live: bool = False):
    """
    Run portfolio cycles 24/7.
    - Market hours (Mon-Fri 09:30-16:00 ET): full universe (stocks + ETFs + crypto)
    - After hours / weekends: crypto-only universe (BTC, ETH, SOL, etc.)
    """
    print(f"\n{'#'*70}")
    print(f"  SWARM PORTFOLIO 24/7 — max {max_positions} positions  account=${account_size:,.0f}")
    print(f"  Interval: {interval_mins} min  |  Mode: {'LIVE' if live else 'PAPER'}")
    print(f"  State file: {SWARM_STATE_FILE.resolve()}")
    print(f"{'#'*70}\n")
    while True:
        try:
            now  = pd.Timestamp.now(tz="America/New_York")
            mins = now.hour * 60 + now.minute
            is_weekday     = now.weekday() < 5
            is_market_hours = is_weekday and (9 * 60 + 30 <= mins < 16 * 60)

            if is_market_hours:
                print(f"  [{now.strftime('%H:%M ET')}] MARKET HOURS — full universe scan", flush=True)
                run_portfolio_cycle(account_size, max_positions, 1.0 / max_positions,
                                    live, universe=EXPLOSIVE_UNIVERSE)
            else:
                session = "WEEKEND" if not is_weekday else "AFTER-HOURS"
                print(f"  [{now.strftime('%H:%M ET')}] {session} — crypto-only scan", flush=True)
                run_portfolio_cycle(account_size, max_positions, 1.0 / max_positions,
                                    live, universe=CRYPTO_UNIVERSE)

            time.sleep(interval_mins * 60)
        except KeyboardInterrupt:
            print("\n  Portfolio loop stopped.")
            break
        except Exception as e:
            print(f"  ERROR: {e}")
            time.sleep(60)


def scan_explosive_universe(account_size: float = 500.0) -> str:
    """
    Scan the high-volatility universe (3× ETFs + high-beta singles) and return
    top scored momentum setups as JSON. Scores 0-100; higher = better setup.
    """
    sector_map = {s: "explosive" for s in EXPLOSIVE_UNIVERSE}
    daily_data = fetch_batch_daily(EXPLOSIVE_UNIVERSE, n_days=30)
    results    = []
    for sym, df in daily_data.items():
        if len(df) < 15: continue
        feat = compute_features(sym, df, None, sector_map)
        if not feat: continue
        ss  = score_explosive(feat)
        if ss["score"] < 50: continue
        pos = aggressive_position(ss["score"], feat["atr"], feat["close"], account_size)
        results.append({
            "symbol": sym, "score": ss["score"], "direction": ss["direction"],
            "close": round(feat["close"],2), "adx": round(feat["adx"],1),
            "vol_rank": round(feat["vol_rank"],2),
            "mom_5day_pct": round(feat["mom_5day"]*100,1),
            "atr_pct": round(feat["atr_pct"]*100,2),
            "ema_slope_pct": round(feat["ema_slope"]*100,3),
            "reason": ss["reason"],
            "bet_pct": int(pos["pct"]*100), "notional": pos["notional"],
            "target_gain_pct": round(pos["gain_pct"],1),
            "max_loss_pct": round(pos["loss_pct"],1),
        })
    results.sort(key=lambda x: (x["score"], x["adx"]*x["vol_rank"]), reverse=True)
    return json.dumps({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M ET"),
        "universe_size": len(EXPLOSIVE_UNIVERSE),
        "symbols_with_data": len(daily_data),
        "qualifying_setups": len(results),
        "top_setups": results[:10],
    }, indent=2)


def scan_full_universe(account_size: float = 500.0) -> str:
    """
    Scan the full 200-symbol universe with all 5 strategies.
    Returns top 10 opportunities ranked by R-expectancy.
    """
    sector_map = {}
    for sec, syms in UNIVERSE.items():
        for s in syms: sector_map[s] = sec
    daily_data = fetch_batch_daily(ALL_SYMBOLS, n_days=30)

    all_feats = {}
    for sym, df in daily_data.items():
        if len(df) < 10: continue
        c = float(df["close"].iloc[-1])
        if c < 15.0 or c == 0: continue
        feat = compute_features(sym, df, None, sector_map)
        if not feat or feat["atr_pct"] > 0.08: continue
        all_feats[sym] = feat

    sector_scores = enrich_with_sector(all_feats)

    rows = []
    for sym, feat in all_feats.items():
        for ss in [score_trend_momentum(feat), score_gap_continuation(feat),
                   score_vwap_bounce(feat), score_range_breakout(feat),
                   score_relative_strength(sym, feat, sector_scores)]:
            if ss["direction"] is None or ss["score"] < 30: continue
            r_exp = r_expectancy(ss["strategy"], ss["score"])
            rows.append({"symbol": sym, "strategy": ss["strategy"],
                         "score": ss["score"], "direction": ss["direction"],
                         "r_exp": r_exp, "close": round(feat["close"],2),
                         "adx": round(feat["adx"],1),
                         "gap_pct": round(feat["gap_pct"]*100,2),
                         "atr_pct": round(feat["atr_pct"]*100,2),
                         "vol_rank": round(feat["vol_rank"],2),
                         "reason": ss["reason"], "sector": feat["sector"]})

    rows.sort(key=lambda x: x["r_exp"], reverse=True)
    return json.dumps({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M ET"),
        "symbols_scanned": len(all_feats),
        "top_opportunities": rows[:10],
    }, indent=2)


def detect_compression_breakouts() -> str:
    """
    Scan for ATR compression setups — coiling into a tight range before breakout.
    compression_ratio < 0.50 = current ATR is less than half 20-day average.
    Higher coil_score = closer to the explosion.
    """
    daily_data = fetch_batch_daily(EXPLOSIVE_UNIVERSE, n_days=60)
    results    = []
    for sym, df in daily_data.items():
        if len(df) < 30: continue
        hl   = df["high"] - df["low"]
        hcp  = (df["high"] - df["close"].shift(1)).abs()
        lcp  = (df["low"]  - df["close"].shift(1)).abs()
        tr   = pd.concat([hl,hcp,lcp], axis=1).max(axis=1)
        atr_now    = float(tr.ewm(span=14,adjust=False).mean().iloc[-1])
        atr_20_avg = float(tr.ewm(span=14,adjust=False).mean().tail(20).mean())
        compression = atr_now/atr_20_avg if atr_20_avg else 1.0
        avg_rng    = float((df["high"]-df["low"]).tail(20).mean())
        tight_days = int(((df["high"]-df["low"]).tail(10) < avg_rng*0.60).sum())
        high_52wk  = float(df["high"].tail(min(252,len(df))).max())
        close      = float(df["close"].iloc[-1])
        pct_high   = close/high_52wk if high_52wk else 0
        avg_vol20  = float(df["volume"].tail(20).mean()) if "volume" in df.columns else 0
        vol5       = float(df["volume"].tail(5).mean())  if "volume" in df.columns else 0
        vol_build  = vol5/avg_vol20 if avg_vol20 else 1.0
        coil = (35 if compression<0.40 else 22 if compression<0.55 else 10 if compression<0.70 else 0)
        coil += (25 if tight_days>=7 else 15 if tight_days>=4 else 7 if tight_days>=2 else 0)
        coil += (20 if pct_high>=0.97 else 10 if pct_high>=0.93 else 0)
        coil += (15 if vol_build>=1.5 else 8 if vol_build>=1.2 else 0)
        if coil >= 35:
            results.append({"symbol": sym, "coil_score": coil,
                             "compression_ratio": round(compression,2),
                             "tight_days": tight_days,
                             "pct_of_52wk_high": round(pct_high*100,1),
                             "volume_build": round(vol_build,2), "close": round(close,2),
                             "expected_move_pct": round((1.0/max(compression,0.1)-1)*10,1)})
    results.sort(key=lambda x: x["coil_score"], reverse=True)
    return json.dumps({"compression_setups": results[:8]}, indent=2)


def get_news_catalysts(symbols: str = "SOXL,AMD,TQQQ,NVDA,ARM") -> str:
    """
    Fetch recent headlines for comma-separated symbols.
    Returns headlines with bullish/bearish/neutral tags.
    """
    bull_kw = {"beat","beats","exceeds","surges","jumps","upgrade","upgrades",
               "approval","buyout","merger","record","raised","outperform","blowout"}
    bear_kw = {"miss","misses","cuts","layoffs","recall","investigation","downgrade",
               "disappoints","warning","weak","suspended","fraud","probe"}
    sym_list = [s.strip().upper() for s in symbols.split(",")][:6]
    out = {}
    for sym in sym_list:
        r = requests.get(f"{DATA}/v1beta1/news", headers=hdrs(),
                         params={"symbols": sym, "limit": 5}, timeout=10)
        if r.status_code != 200: out[sym] = []; continue
        items = []
        for n in r.json().get("news", []):
            h   = n.get("headline","")
            low = h.lower()
            snt = ("bullish" if any(w in low for w in bull_kw)
                   else "bearish" if any(w in low for w in bear_kw) else "neutral")
            items.append({"headline": h, "sentiment": snt,
                          "created_at": n.get("created_at","")[:10]})
        out[sym] = items
    return json.dumps(out, indent=2)


def get_market_regime() -> str:
    """
    Assess today's market regime from SPY, QQQ, SMH, VXX, TLT.
    Returns regime, fear level, sector leader, and recommended max position %.
    """
    anchors = ["SPY","QQQ","SMH","VXX","TLT"]
    daily   = fetch_batch_daily(anchors, n_days=20)
    data    = {}
    for sym, df in daily.items():
        if len(df) < 10: continue
        c   = float(df["close"].iloc[-1])
        c5  = float(df["close"].iloc[-6]) if len(df)>5 else c
        c20 = float(df["close"].iloc[-21]) if len(df)>20 else c
        hl  = df["high"] - df["low"]
        atr = float(hl.ewm(span=14,adjust=False).mean().iloc[-1])
        data[sym] = {"close": round(c,2),
                     "mom_5d_pct":  round((c/c5-1)*100,2)  if c5  else 0,
                     "mom_20d_pct": round((c/c20-1)*100,2) if c20 else 0,
                     "atr_pct":     round(atr/c*100,2)     if c   else 0}
    spy5 = data.get("SPY",{}).get("mom_5d_pct",0)
    qqq5 = data.get("QQQ",{}).get("mom_5d_pct",0)
    smh5 = data.get("SMH",{}).get("mom_5d_pct",0)
    vxx5 = data.get("VXX",{}).get("mom_5d_pct",0)
    regime = ("BULL_STRONG" if spy5>3 and qqq5>3 else
              "BULL_MODERATE" if spy5>0 else
              "BEAR_MODERATE" if spy5>-5 else "BEAR_STRONG")
    fear   = ("HIGH_FEAR" if vxx5>10 else "LOW_FEAR" if vxx5<-5 else "NORMAL_FEAR")
    leader = ("TECH_SEMI" if smh5>qqq5+1 else "BROAD" if abs(smh5-qqq5)<1 else "ROTATION")
    rec_max = (0.40 if regime=="BULL_STRONG" and fear=="LOW_FEAR" else
               0.30 if regime=="BULL_MODERATE" else 0.20 if fear=="NORMAL_FEAR" else 0.10)
    return json.dumps({
        "regime": regime, "fear": fear, "sector_leader": leader,
        "recommended_max_position_pct": rec_max,
        "instruments": data,
        "note": f"Regime={regime}, Fear={fear}, Leader={leader} → max bet={int(rec_max*100)}%",
    }, indent=2)


def calculate_position(symbol: str, score: float,
                       account_size: float = 500.0, max_pct: float = 0.40) -> str:
    """Compute exact position size, stop price, and target price for a symbol."""
    daily = fetch_batch_daily([symbol], n_days=15)
    df    = daily.get(symbol)
    if df is None or len(df) < 5:
        return json.dumps({"error": f"No data for {symbol}"})
    close = float(df["close"].iloc[-1])
    hl    = df["high"] - df["low"]
    atr   = float(hl.ewm(span=14,adjust=False).mean().iloc[-1])
    pos   = aggressive_position(float(score), atr, close, float(account_size), float(max_pct))
    return json.dumps({
        "symbol": symbol, "score": score,
        "close": round(close,2), "atr": round(atr,2),
        "atr_pct": round(atr/close*100,2),
        "bet_pct": int(pos["pct"]*100), "notional": pos["notional"],
        "shares": pos["shares"],
        "stop_price":  round(close-pos["stop_dist"],2),
        "target_price": round(close+pos["tgt_dist"],2),
        "max_loss_usd": pos["max_loss"], "max_gain_usd": pos["max_gain"],
        "loss_pct_acct": pos["loss_pct"], "gain_pct_acct": pos["gain_pct"],
        "rr_ratio": 2.0,
    }, indent=2)


def get_account_status() -> str:
    """Get current Alpaca paper account: equity, buying power, open positions."""
    r  = requests.get(f"{BROKER}/v2/account",   headers=paper_hdrs(), timeout=10)
    r2 = requests.get(f"{BROKER}/v2/positions", headers=paper_hdrs(), timeout=10)
    if r.status_code != 200:
        return json.dumps({"error": r.text,
                           "hint": "Set APCA_PAPER_KEY / APCA_PAPER_SECRET env vars "
                                   "with your PK-prefix paper trading keys."})
    acct = r.json()
    pos  = r2.json() if r2.status_code == 200 else []
    return json.dumps({
        "equity":       acct.get("equity"),
        "buying_power": acct.get("buying_power"),
        "cash":         acct.get("cash"),
        "open_positions": [{"symbol": p["symbol"], "qty": p["qty"],
                            "unrealized_pl": p.get("unrealized_pl")}
                           for p in (pos if isinstance(pos, list) else [])],
    }, indent=2)


def place_order(symbol: str, side: str, notional: float) -> str:
    """Submit a fractional-share market order via Alpaca paper trading."""
    # Crypto symbols: yfinance uses BTC-USD, Alpaca uses BTCUSD
    alpaca_sym = CRYPTO_ALPACA.get(symbol, symbol)
    is_crypto  = symbol in CRYPTO_ALPACA
    body = {"symbol": alpaca_sym, "notional": str(round(notional, 2)),
            "side": side, "type": "market",
            "time_in_force": "gtc" if is_crypto else "day"}
    r = requests.post(f"{BROKER}/v2/orders", headers=paper_hdrs(), json=body, timeout=10)
    if r.status_code in (200, 201):
        o = r.json()
        return json.dumps({"status": "placed", "order_id": o.get("id"),
                           "symbol": o.get("symbol"), "side": side, "notional": notional})
    return json.dumps({"status": "failed", "error": r.text})


def get_alpaca_positions() -> list:
    """Fetch current open positions from Alpaca. Returns [] on error."""
    try:
        r = requests.get(f"{BROKER}/v2/positions", headers=paper_hdrs(), timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return []


def get_alpaca_account() -> dict:
    """Fetch Alpaca account info. Returns {} on error."""
    try:
        r = requests.get(f"{BROKER}/v2/account", headers=paper_hdrs(), timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def get_alpaca_pending_sells() -> set:
    """Return set of symbols that have an open/pending sell order (so we don't re-exit them)."""
    try:
        r = requests.get(f"{BROKER}/v2/orders", headers=paper_hdrs(),
                         params={"status": "open", "limit": 50}, timeout=10)
        if r.status_code == 200:
            orders = r.json()
            return {o["symbol"] for o in orders if o.get("side") == "sell"}
    except Exception:
        pass
    return set()


# ══════════════════════════════════════════════════════════════════════════════
#  DIRECT SCAN / BACKTEST FUNCTIONS  (no LLM — fast, standalone)
# ══════════════════════════════════════════════════════════════════════════════

def run_scan(account_size: float = 500.0, top_n: int = 10):
    now = pd.Timestamp.now(tz="America/New_York")
    print(f"\n{'='*80}")
    print(f"  MARKET SCANNER  {now.strftime('%Y-%m-%d %H:%M ET')}  account=${account_size:,.0f}")
    print(f"{'='*80}")
    print(f"  Universe: {len(ALL_SYMBOLS)} symbols  |  Fetching daily bars ...", flush=True)

    sector_map = {}
    for sec, syms in UNIVERSE.items():
        for s in syms: sector_map[s] = sec
    daily_data  = fetch_batch_daily(ALL_SYMBOLS, n_days=30)
    print(f"  Got data for {len(daily_data)}/{len(ALL_SYMBOLS)} symbols")

    quick = []
    for sym, df in daily_data.items():
        if len(df) < 5: continue
        c   = float(df["close"].iloc[-1])
        if c < 15.0: continue
        pc  = float(df["close"].iloc[-2]) if len(df)>1 else c
        gap = abs((c-pc)/pc) if pc else 0
        hl  = df["high"]-df["low"]
        atr = float(hl.ewm(span=14,adjust=False).mean().iloc[-1])
        atp = atr/c if c else 0
        if atp > 0.08: continue
        av  = float(df["volume"].tail(10).mean()) if "volume" in df.columns else 1
        cv  = float(df["volume"].iloc[-1])         if "volume" in df.columns else 1
        quick.append(dict(sym=sym, score=atp*30+gap*20+min(cv/av if av else 1,3)*15))
    top60 = [x["sym"] for x in sorted(quick, key=lambda x: x["score"], reverse=True)[:60]]
    print(f"  Pre-filtered to top 60  |  Fetching intraday ...", flush=True)
    intraday = fetch_intraday_snapshot(top60)

    all_feats = {}
    for sym in top60:
        df_d = daily_data.get(sym)
        if df_d is None or len(df_d) < 10: continue
        feat = compute_features(sym, df_d, intraday.get(sym), sector_map)
        if not feat or feat["close"] < 15 or feat["atr_pct"] > 0.08: continue
        all_feats[sym] = feat
    sector_scores = enrich_with_sector(all_feats)

    rows = []
    for sym, feat in all_feats.items():
        for ss in [score_trend_momentum(feat), score_gap_continuation(feat),
                   score_vwap_bounce(feat), score_range_breakout(feat),
                   score_relative_strength(sym, feat, sector_scores)]:
            if ss["direction"] is None or ss["score"] < 30: continue
            r_exp = r_expectancy(ss["strategy"], ss["score"])
            exp   = expected_pnl(ss["score"], feat["atr"], feat["close"],
                                 account_size, ss["strategy"],
                                 trail=ss["params"].get("trail",3.0))
            rows.append(dict(symbol=sym, strategy=ss["strategy"],
                             score=ss["score"], direction=ss["direction"],
                             r_exp=r_exp, close=round(feat["close"],2),
                             atr=round(feat["atr"],4), atr_pct=round(feat["atr_pct"]*100,2),
                             gap_pct=round(feat["gap_pct"]*100,2),
                             vol_ratio=round(feat["vol_ratio"],1),
                             adx=round(feat["adx"],1), vol_rank=round(feat["vol_rank"],2),
                             exp_pnl=exp, reason=ss["reason"],
                             params=ss["params"], sector=feat["sector"]))

    if not rows: print("  No qualifying setups found."); return
    result = pd.DataFrame(rows).sort_values("r_exp", ascending=False)
    now_mins = now.hour*60 + now.minute

    def expired(p):
        ee = p.get("entry_end","16:00") if isinstance(p,dict) else "16:00"
        try: h,m = map(int,ee.split(":")); return now_mins > h*60+m
        except: return False

    print(f"\n{'─'*80}")
    print(f"  TOP {top_n}  (ranked by R-expectancy × confidence)")
    print(f"{'─'*80}")
    print(f"  {'#':>2}  {'Symbol':<7} {'Strategy':<20} {'Dir':<6} "
          f"{'Score':>5} {'R-exp':>6} {'Gap%':>6} {'ATR%':>5} {'ADX':>5} {'ExpPnL':>8}  Note")
    print("  " + "─"*82)
    for i, (_, r) in enumerate(result.iterrows(), 1):
        if i > top_n: break
        note = "EXPIRED" if expired(r["params"]) else ""
        exp_s = f"${r['exp_pnl']:>6.2f}" if r["exp_pnl"]>=0 else f"-${abs(r['exp_pnl']):>5.2f}"
        print(f"  {i:>2}  {r['symbol']:<7} {r['strategy']:<20} {r['direction']:<6} "
              f"{r['score']:>5.0f} {r['r_exp']:>6.3f} {r['gap_pct']:>+5.1f}% "
              f"{r['atr_pct']:>4.1f}% {r['adx']:>5.1f} {exp_s:>8}  {note}")

    print(f"\n{'─'*80}")
    print(f"  TODAY'S TOP 3  (2% risk each)")
    print(f"{'─'*80}")
    seen, pick = set(), 1
    for _, r in result.iterrows():
        if r["symbol"] in seen or pick > 3: break
        seen.add(r["symbol"])
        trail   = r["params"].get("trail",3.0) if isinstance(r["params"],dict) else 3.0
        stop_d  = trail * r["atr"]
        shares  = account_size*0.02/stop_d if stop_d else 0
        ee      = r["params"].get("entry_end","—") if isinstance(r["params"],dict) else "—"
        status  = "  ⚠ ENTRY WINDOW CLOSED" if expired(r["params"]) else f"  entry before {ee} ET"
        print(f"  #{pick}  {r['symbol']:<6} {r['direction'].upper():<5} via {r['strategy']:<20}  "
              f"score={r['score']:.0f}  exp=${r['exp_pnl']:.2f}")
        print(f"       shares={shares:.2f}  stop={stop_d:.2f} away  ADX={r['adx']:.0f}  "
              f"[{r['sector']}]{status}")
        print(f"       {r['reason']}")
        pick += 1
    print(f"{'='*80}\n")

    # Write top pick to swarm_daily_trade.json for the dashboard
    top = result.iloc[0]
    _trail  = top["params"].get("trail", 3.0) if isinstance(top["params"], dict) else 3.0
    _stop_d = _trail * top["atr"]
    _long   = top["direction"] == "long"
    _entry  = float(top["close"])
    _stop_p = round(_entry - _stop_d, 2) if _long else round(_entry + _stop_d, 2)
    _rr     = STRAT_EDGE.get(top["strategy"], {"avg_r_win": 2.0})["avg_r_win"]
    _tgt_p  = round(_entry + _rr * _stop_d, 2) if _long else round(_entry - _rr * _stop_d, 2)
    _notional = round(account_size * 0.02 / _stop_d * _entry, 2) if _stop_d else 0.0
    _write_swarm_trade({
        "symbol":       top["symbol"],
        "strategy":     top["strategy"],
        "side":         "buy" if _long else "sell",
        "entry_price":  _entry,
        "stop_price":   _stop_p,
        "target_price": _tgt_p,
        "rr_ratio":     round(_rr, 2),
        "notional":     _notional,
        "score":        int(top["score"]),
        "thesis":       top["reason"],
        "mode":         "scan",
    })


def run_aggressive(account_size: float = 500.0):
    now = pd.Timestamp.now(tz="America/New_York")
    sector_map = {s:"explosive" for s in EXPLOSIVE_UNIVERSE}
    print(f"\n{'#'*80}")
    print(f"  AGGRESSIVE SCANNER  {now.strftime('%Y-%m-%d %H:%M ET')}  account=${account_size:,.0f}")
    print(f"{'#'*80}")
    print(f"  Fetching {len(EXPLOSIVE_UNIVERSE)} symbols ...", flush=True)
    daily_data = fetch_batch_daily(EXPLOSIVE_UNIVERSE, n_days=30)
    print(f"  Got data for {len(daily_data)}/{len(EXPLOSIVE_UNIVERSE)}")

    rows = []
    for sym, df in daily_data.items():
        if len(df) < 15: continue
        feat = compute_features(sym, df, None, sector_map)
        if not feat: continue
        ss  = score_explosive(feat)
        if ss["score"] < 50 or ss["direction"] is None: continue
        pos = aggressive_position(ss["score"], feat["atr"], feat["close"], account_size)
        if pos["pct"] == 0: continue
        rows.append(dict(symbol=sym, score=ss["score"], direction=ss["direction"],
                         close=round(feat["close"],2), atr=feat["atr"],
                         atr_pct=round(feat["atr_pct"]*100,2),
                         adx=round(feat["adx"],1), vol_rank=round(feat["vol_rank"],2),
                         mom5d=round(feat["mom_5day"]*100,1),
                         gap=round(feat["gap_pct"]*100,2), pos=pos, reason=ss["reason"]))
    if not rows: print("  No explosive setups today."); return
    rows.sort(key=lambda x: (x["score"],x["adx"]*x["vol_rank"]), reverse=True)

    print(f"\n{'─'*80}")
    print(f"  {'#':>2}  {'Sym':<6} {'Dir':<6} {'Score':>5}  {'ADX':>5} {'VR':>5} "
          f"{'Mom5d':>7} {'ATR%':>5}  {'Bet%':>5} {'Notional':>10} {'WinTarget':>10} {'MaxLoss':>9}")
    print("  "+"─"*78)
    for i, r in enumerate(rows[:10],1):
        p = r["pos"]
        print(f"  {i:>2}  {r['symbol']:<6} {r['direction']:<6} {r['score']:>5}  "
              f"{r['adx']:>5.1f} {r['vol_rank']:>5.2f} {r['mom5d']:>+6.1f}% {r['atr_pct']:>4.1f}%  "
              f"{p['pct']*100:>4.0f}%  ${p['notional']:>8,.2f}  "
              f"+${p['max_gain']:>7,.2f} ({p['gain_pct']:>+.1f}%)  "
              f"-${p['max_loss']:>6,.2f} ({p['loss_pct']:>.1f}%)")

    best = rows[0]; p = best["pos"]
    expired = (now.hour*60+now.minute) > 11*60+30
    print(f"\n{'─'*80}")
    print(f"  TODAY'S TRADE: {best['symbol']} {best['direction'].upper()}")
    print(f"{'─'*80}")
    print(f"  Price   : ${best['close']:.2f}   ATR: ${best['atr']:.2f} ({best['atr_pct']:.1f}%)")
    print(f"  ADX     : {best['adx']:.0f}  Vol rank: {best['vol_rank']:.2f}  5d mom: {best['mom5d']:+.1f}%")
    print(f"  Signal  : {best['reason']}")
    print(f"  POSITION: ${p['notional']:,.2f}  ({p['pct']*100:.0f}% of ${account_size:,.0f})")
    print(f"  Shares  : {p['shares']:.4f}")
    print(f"  Stop    : 1.5 ATR = ${best['close']-p['stop_dist']:.2f}  (lose ${p['max_loss']:.2f} = -{p['loss_pct']:.1f}%)")
    print(f"  Target  : 3.0 ATR = ${best['close']+p['tgt_dist']:.2f}  (gain ${p['max_gain']:.2f} = +{p['gain_pct']:.1f}%)")
    print(f"  R/R     : {p['tgt_dist']/p['stop_dist']:.1f}:1")
    if expired: print(f"  ENTRY   : ⚠  Entry window closed (past 11:30 ET)")
    else:       print(f"  ENTRY   : BUY at open or first pullback to EMA  (before 11:30 ET)")
    print(f"{'#'*80}\n")


def backtest_scanner(n_days: int = 30, account_size: float = 500.0):
    print(f"\n{'='*80}")
    print(f"  SCANNER BACKTEST — last {n_days} trading days  (${account_size:,.0f})")
    print(f"{'='*80}")
    sector_map = {}
    for sec, syms in UNIVERSE.items():
        for s in syms: sector_map[s] = sec
    daily_data = fetch_batch_daily(ALL_SYMBOLS, n_days=n_days+30)
    if not daily_data: print("  No data."); return

    all_dates = sorted(set.intersection(*[
        set(df.index.normalize().unique()) for df in daily_data.values() if len(df)>5
    ]))[-n_days:]

    equity, trades = account_size, []
    print(f"  Simulating {len(all_dates)} days ...\n")
    print(f"  {'Date':<12} {'Symbol':<7} {'Strategy':<20} {'Dir':<6} "
          f"{'Score':>5} {'ExpPnL':>8} {'ActPnL':>8} {'Equity':>10}")
    print("  "+"─"*76)

    for date in all_dates:
        date_str = str(date.date()) if hasattr(date,"date") else str(date)[:10]
        day_feats = {}
        for sym, df in daily_data.items():
            hist = df[df.index.normalize() < date]
            if len(hist) < 15: continue
            feat = compute_features(sym, hist, None, sector_map)
            if not feat or feat["close"]<15 or feat["atr_pct"]>0.08: continue
            day_feats[sym] = feat
        sec_scores = enrich_with_sector(day_feats)

        rows = []
        for sym, feat in day_feats.items():
            for scorer in [score_trend_momentum, score_gap_continuation,
                           score_vwap_bounce, score_range_breakout,
                           lambda f: score_relative_strength(sym,f,sec_scores)]:
                ss = scorer(feat)
                if ss["direction"] is None or ss["score"]<40: continue
                r_exp = r_expectancy(ss["strategy"], ss["score"])
                exp   = expected_pnl(ss["score"], feat["atr"], feat["close"],
                                     equity, ss["strategy"], ss["params"].get("trail",3.0))
                rows.append(dict(symbol=sym, strategy=ss["strategy"],
                                 score=ss["score"], direction=ss["direction"],
                                 r_exp=r_exp, atr=feat["atr"], close=feat["close"],
                                 exp_pnl=exp, params=ss["params"]))
        if not rows: continue
        best = max(rows, key=lambda x: x["r_exp"])
        sym  = best["symbol"]
        df_s = daily_data.get(sym)
        if df_s is None: continue
        bar = df_s[df_s.index.normalize()==date]
        if len(bar)==0: continue
        entry   = float(bar["open"].iloc[0])
        day_hi  = float(bar["high"].iloc[0])
        day_lo  = float(bar["low"].iloc[0])
        day_cl  = float(bar["close"].iloc[0])
        atr     = best["atr"]
        trail   = best["params"].get("trail",3.0)
        risk    = equity*0.02
        stop_d  = trail*atr
        shares  = risk/stop_d if stop_d else 0
        if best["direction"]=="long":
            ts = entry-trail*atr
            exit_px = ts if day_lo<=ts else day_cl
            pnl = (exit_px-entry)*shares
        else:
            ts = entry+trail*atr
            exit_px = ts if day_hi>=ts else day_cl
            pnl = (entry-exit_px)*shares
        pnl -= shares*0.005*2
        equity += pnl
        trades.append(dict(date=date_str, symbol=sym, strategy=best["strategy"],
                           direction=best["direction"], score=best["score"],
                           exp_pnl=best["exp_pnl"], act_pnl=round(pnl,2), equity=round(equity,2)))
        print(f"  {date_str:<12} {sym:<7} {best['strategy']:<20} {best['direction']:<6} "
              f"{best['score']:>5.0f} ${best['exp_pnl']:>6.2f} ${pnl:>+7.2f} ${equity:>9,.2f}")

    if trades:
        t     = pd.DataFrame(trades)
        pnl_s = pd.Series(t["act_pnl"])
        wins  = (pnl_s>0).sum()
        print(f"\n{'='*80}  SUMMARY")
        print(f"  ${account_size:,.0f} → ${equity:,.2f}  ({(equity/account_size-1)*100:+.1f}%)")
        print(f"  Win rate: {wins}/{len(t)} ({wins/len(t)*100:.0f}%)  "
              f"Avg/day: ${pnl_s.mean():+.2f}")
        print(f"  Best: ${pnl_s.max():+.2f} ({t.loc[pnl_s.idxmax(),'symbol']})  "
              f"Worst: ${pnl_s.min():+.2f} ({t.loc[pnl_s.idxmin(),'symbol']})")
        print(f"{'='*80}\n")


def backtest_aggressive(n_days: int = 30, account_size: float = 500.0,
                        max_pct: float = 0.40):
    pct_label = f"{max_pct*100:.0f}%"
    print(f"\n{'#'*80}")
    print(f"  AGGRESSIVE BACKTEST — last {n_days} days  start=${account_size:,.0f}  max={pct_label}")
    print(f"  Stop: 1.5 ATR  Target: 3 ATR")
    print(f"{'#'*80}")
    sector_map = {s:"explosive" for s in EXPLOSIVE_UNIVERSE}
    daily_data = fetch_batch_daily(EXPLOSIVE_UNIVERSE, n_days=n_days+60)
    if not daily_data: print("  No data."); return
    anchor    = next((s for s in ("SOXL","AMD","TQQQ","NVDA") if s in daily_data),
                     list(daily_data.keys())[0])
    all_dates = sorted(daily_data[anchor].index.normalize().unique())[-n_days:]

    equity, trades = account_size, []
    print(f"  Simulating {len(all_dates)} days (anchor={anchor}) ...\n")
    print(f"  {'Date':<12} {'Sym':<6} {'Dir':<6} {'Sc':>3} {'Bet%':>4} "
          f"{'Entry':>7} {'Stop':>7} {'Tgt':>7} {'Exit':>7} {'$PnL':>8} {'Eq':>10}")
    print("  "+"─"*80)

    for date in all_dates:
        date_str = str(date.date()) if hasattr(date,"date") else str(date)[:10]
        candidates = []
        for sym, df in daily_data.items():
            hist = df[df.index.normalize() < date]
            if len(hist) < 15: continue
            feat = compute_features(sym, hist, None, sector_map)
            if not feat: continue
            ss  = score_explosive(feat)
            if ss["score"] < 65 or ss["direction"] is None: continue
            pos = aggressive_position(ss["score"], feat["atr"], feat["close"], equity, max_pct)
            if pos["pct"] == 0: continue
            candidates.append(dict(symbol=sym, score=ss["score"], direction=ss["direction"],
                                   feat=feat, pos=pos, atr=feat["atr"]))
        if not candidates: continue
        best = max(candidates, key=lambda x: x["score"])
        sym  = best["symbol"]
        df_s = daily_data.get(sym)
        if df_s is None: continue
        bar = df_s[df_s.index.normalize()==date]
        if len(bar)==0: continue
        entry    = float(bar["open"].iloc[0])
        day_hi   = float(bar["high"].iloc[0])
        day_lo   = float(bar["low"].iloc[0])
        day_cl   = float(bar["close"].iloc[0])
        atr      = best["atr"]
        pos      = aggressive_position(best["score"], atr, entry, equity, max_pct)
        shares   = pos["shares"]
        stop_d   = 1.5*atr; tgt_d = 3.0*atr
        if best["direction"]=="long":
            sp, tp = entry-stop_d, entry+tgt_d
            if day_lo<=sp:   ep,out = sp,"stop"
            elif day_hi>=tp: ep,out = tp,"target"
            else:            ep,out = day_cl,"close"
            pnl = (ep-entry)*shares
        else:
            sp, tp = entry+stop_d, entry-tgt_d
            if day_hi>=sp:   ep,out = sp,"stop"
            elif day_lo<=tp: ep,out = tp,"target"
            else:            ep,out = day_cl,"close"
            pnl = (entry-ep)*shares
        pnl -= shares*0.005*2
        prev_eq = equity; equity += pnl
        day_pct = pnl/prev_eq*100
        trades.append(dict(date=date_str, symbol=sym, direction=best["direction"],
                           score=best["score"], bet_pct=pos["pct"]*100,
                           entry=entry, stop=sp, target=tp, exit=ep, outcome=out,
                           pnl=round(pnl,2), day_pct=round(day_pct,2), equity=round(equity,2)))
        mk = "✓" if pnl>0 else "✗"
        print(f"  {date_str:<12} {sym:<6} {best['direction']:<6} {best['score']:>3.0f} "
              f"{pos['pct']*100:>3.0f}%"
              f" {entry:>7.2f} {sp:>7.2f} {tp:>7.2f} {ep:>7.2f}"
              f" {day_pct:>+6.1f}%  {mk} {out}")

    if not trades: print("  No trades."); return
    t     = pd.DataFrame(trades)
    pnl_s = t["pnl"]; pct_s = t["day_pct"]
    wins  = (pnl_s>0).sum()
    outs  = t["outcome"].value_counts()
    print(f"\n{'#'*80}")
    print(f"  ${account_size:,.2f} → ${equity:,.2f}  ({(equity/account_size-1)*100:+.1f}%)")
    print(f"  Win rate: {wins}/{len(t)} ({wins/len(t)*100:.0f}%)  "
          f"Avg/day: ${pnl_s.mean():+.2f} ({pct_s.mean():+.2f}%)")
    print(f"  Best: {pct_s.max():+.1f}% ({t.loc[pct_s.idxmax(),'symbol']})  "
          f"Worst: {pct_s.min():+.1f}% ({t.loc[pct_s.idxmin(),'symbol']})")
    print(f"  target={outs.get('target',0)}  stop={outs.get('stop',0)}  close={outs.get('close',0)}")
    bins = [(-100,-15),(-15,-10),(-10,-5),(-5,-2),(-2,0),(0,2),(2,5),(5,10),(10,15),(15,100)]
    print(f"\n  Daily % return distribution:")
    for lo, hi in bins:
        cnt = ((pct_s>=lo) & (pct_s<hi)).sum()
        print(f"    {lo:+.0f}% to {hi:+.0f}%  {'█'*cnt:<20} {cnt}d")
    print()
    for sym, g in t.groupby("symbol"):
        w = (g["pnl"]>0).sum()
        print(f"  {sym:<8} {len(g):>3}×  WR={w/len(g)*100:.0f}%  "
              f"total={g['day_pct'].sum():>+7.1f}%  avg={g['day_pct'].mean():>+6.1f}%  "
              f"best={g['day_pct'].max():>+6.1f}%")
    print(f"{'#'*80}\n")


# ══════════════════════════════════════════════════════════════════════════════
#  SWARM — AGENT DEFINITIONS + ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════════════

def build_swarm(account_size: float, live: bool, model: str):
    from swarms import Agent, AgentRearrange

    # Fresh run ID prevents agents from loading stale memory from prior runs
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    agent_kwargs = dict(
        model_name=model,
        autosave=False,
        verbose=False,
        output_type="str",
    )

    scanner_agent = Agent(
        agent_name=f"MarketScanner_{run_id}",
        agent_description="Quantitative momentum scanner for explosive-momentum names",
        system_prompt=f"""You are a quant scanner specialising in explosive momentum setups.

Call scan_explosive_universe({account_size}) RIGHT NOW and return a tight JSON summary of
the top 3 candidates. For each include: symbol, direction, score, ADX, vol_rank,
5-day momentum, ATR%, potential_gain_pct, and a one-sentence reason WHY this is
the best setup today vs the others.

Rank by (score × ADX × vol_rank). Output ONLY valid JSON, no prose.""",
        max_loops=1,
        tools=[scan_explosive_universe],
        **agent_kwargs,
    )

    catalyst_agent = Agent(
        agent_name=f"CatalystHunter_{run_id}",
        agent_description="Detects compression breakouts, earnings catalysts, IV signals, and unusual options flow",
        system_prompt="""You are a catalyst specialist. Your edge: spotting moves BEFORE they happen — the way institutional desks do.

The MarketScanner has already run. Your input contains its JSON output.
Extract the scanner's top symbols (up to 5, comma-separated) and use THOSE EXACT
SYMBOLS for every tool call below — do NOT use any hardcoded symbol list.

Run ALL FIVE steps in order:

Step 1 — Call detect_compression_breakouts()
         → Find coiled springs: ATR compressed, tight range, near 52wk high.

Step 2 — Call get_earnings_calendar(<scanner_symbols>)
         → Check who reports soon. Pre-earnings 2-5 days out = momentum window.
           Earnings TODAY or TOMORROW = gap risk, flag as dangerous.

Step 3 — Call get_iv_rank(<scanner_symbols>)
         → Low HV rank (<25) + coiling = spring loaded.
           High IV vs HV = institutions paying up, something coming.

Step 4 — Call get_unusual_options_activity(<scanner_symbols>)
         → BULLISH_SWEEP with call_vol_oi > 0.5 = smart money positioning.
           Cross-reference with compression / earnings setups.

Step 5 — Call get_news_catalysts(<scanner_symbols>)
         → Confirm with recent headlines.

Scoring logic (use this to rank your picks):
  +3 pts  compression coil score >= 60
  +2 pts  earnings in 2-5 days (pre-earnings momentum window)
  -2 pts  earnings TODAY or TOMORROW (gap risk — warn the risk manager)
  +2 pts  unusual options activity (BULLISH_SWEEP)
  +2 pts  IV rank signal = LOW_VOL_COILING or HIGH_VOL_EXPANSION
  +1 pt   bullish news catalyst
  +1 pt   high IV vs HV spread (institutions paying up)

Return JSON:
{
  "top_catalyst_setups": [
    {
      "symbol": "...",
      "catalyst_score": 0-10,
      "catalyst_types": ["compression", "pre_earnings", "options_sweep", "news"],
      "detail": "...",
      "urgency": "today|this_week",
      "risk_flag": null or "earnings_gap_risk"
    }
  ],
  "headline_pick": "SYMBOL — one powerful sentence explaining the edge"
}""",
        max_loops=3,
        tools=[detect_compression_breakouts, get_earnings_calendar,
               get_iv_rank, get_unusual_options_activity, get_news_catalysts],
        **agent_kwargs,
    )

    regime_agent = Agent(
        agent_name=f"RegimeAnalyst_{run_id}",
        agent_description="Market regime and risk environment assessment",
        system_prompt="""You are a macro-regime specialist. Your output sets the risk budget.

Call get_market_regime() and translate the numbers into clear guidance:
- Overall market direction?
- Is fear elevated (VXX rising)?
- Which sector is leading (semis vs broad)?
- Maximum position size we should risk today?

Return JSON:
{
  "regime": "...",
  "fear_level": "...",
  "leading_sector": "...",
  "max_position_pct": 0.XX,
  "regime_verdict": "GO_AGGRESSIVE | GO_NORMAL | GO_LIGHT | NO_TRADE"
}""",
        max_loops=1,
        tools=[get_market_regime],
        **agent_kwargs,
    )

    risk_agent = Agent(
        agent_name=f"RiskManager_{run_id}",
        agent_description="Position sizing, stop and target calculation",
        system_prompt=f"""You are the risk manager. You receive scanner + catalyst + regime outputs.

Your job:
1. Try get_account_status() for live equity. If it fails (401 error), that is OK —
   default to account_size=${account_size:.0f} and continue. Never PASS just because auth failed.
2. Pick ONE symbol (prefer symbols appearing in BOTH scanner AND catalyst outputs).
3. EARNINGS GATE (MANDATORY): Call check_earnings_gate(symbol) for your chosen symbol.
   - If gate_verdict == "BLOCK": set risk_verdict = "REJECTED" and copy the reason string
     verbatim into rejection_reason. Do NOT proceed further.
   - If gate_verdict == "UNCHECKED": add a warning "earnings_unchecked" to warnings[] and
     continue — do NOT block the trade.
   - If gate_verdict == "PASS": continue normally.
4. Call calculate_position(symbol, score, {account_size:.0f}, max_pct) for exact levels.
5. Only REJECT if regime says NO_TRADE, earnings gate is BLOCK, or max_loss > 5% of account.

Return JSON:
{{
  "selected_symbol": "...",
  "direction": "long|short",
  "why_this_symbol": "...",
  "position": {{...}},
  "current_equity": {account_size:.0f},
  "risk_verdict": "APPROVED | REJECTED",
  "rejection_reason": null,
  "warnings": []
}}""",
        max_loops=2,
        tools=[get_account_status, calculate_position, check_earnings_gate],
        **agent_kwargs,
    )

    exec_instruction = (
        "Call place_order(symbol, side, notional) to execute. Report the order ID."
        if live else
        "PAPER MODE — do NOT call any tools. Use the position data already provided by RiskManager."
    )

    decision_agent = Agent(
        agent_name=f"TradeDecision_{run_id}",
        agent_description="Final synthesis and go/no-go trade decision",
        system_prompt=f"""You are the chief trading officer. Make the final call — no hedging.

Rules:
- The RiskManager has already provided the selected symbol, position sizing, stop, and target.
  Use that data directly — do NOT call get_account_status() again.
- Execute only if risk_verdict == APPROVED and regime_verdict != NO_TRADE.
- If signals conflict, explain which you trust most and why.
- Be decisive. A PASS requires a specific reason (not just "auth failed").

{exec_instruction}

Return final JSON:
{{
  "final_decision": "EXECUTE | PASS",
  "symbol": "...",
  "side": "buy|sell",
  "notional": ...,
  "stop_price": ...,
  "target_price": ...,
  "gain_if_target_pct": ...,
  "loss_if_stop_pct": ...,
  "confidence": 0-100,
  "trade_thesis": "one powerful sentence",
  "execution_status": "placed|simulated|skipped"
}}""",
        max_loops=1,
        tools=[place_order] if live else [],
        **agent_kwargs,
    )

    from swarms import AgentRearrange
    swarm = AgentRearrange(
        name="TradingSwarm",
        description="Multi-agent explosive-momentum trading system",
        agents=[scanner_agent, catalyst_agent, regime_agent, risk_agent, decision_agent],
        flow=(f"MarketScanner_{run_id} -> CatalystHunter_{run_id},RegimeAnalyst_{run_id}"
              f" -> RiskManager_{run_id} -> TradeDecision_{run_id}"),
        max_loops=1, verbose=False,
    )
    return swarm


# ══════════════════════════════════════════════════════════════════════════════
#  SWARM TRADE OUTPUT
# ══════════════════════════════════════════════════════════════════════════════

SWARM_TRADE_FILE = Path(__file__).parent / "swarm_daily_trade.json"


def _write_swarm_trade(rec: dict) -> None:
    """Write daily trade decision to swarm_daily_trade.json for the dashboard."""
    now = datetime.now()
    rec.setdefault("timestamp", now.isoformat())
    rec.setdefault("date", now.strftime("%Y-%m-%d"))
    _tmp = SWARM_TRADE_FILE.with_suffix(".json.tmp")
    _tmp.write_text(json.dumps(rec, indent=2, default=str))
    os.replace(_tmp, SWARM_TRADE_FILE)
    print(f"  [swarm] trade JSON written → {SWARM_TRADE_FILE}")


# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="Trading Swarm — self-contained multi-agent trading system")
    p.add_argument("--size",       type=float, default=500.0, help="Account size in dollars")
    p.add_argument("--live",       action="store_true",       help="Execute real Alpaca orders")
    p.add_argument("--scan",       action="store_true",       help="Quick full-universe scan (no LLM)")
    p.add_argument("--aggressive", action="store_true",       help="Aggressive scan / backtest (no LLM)")
    p.add_argument("--backtest",   type=int, metavar="DAYS",  help="Backtest last N days (no LLM)")
    p.add_argument("--yolo",       action="store_true",       help="100%% position sizing")
    p.add_argument("--portfolio",  action="store_true",       help="Run 5-position portfolio manager loop")
    p.add_argument("--positions",  type=int, default=5,       help="Max simultaneous positions (default 5)")
    p.add_argument("--interval",   type=int, default=30,      help="Portfolio rescan interval in minutes")
    p.add_argument("--once",       action="store_true",       help="Run one portfolio cycle then exit")
    p.add_argument("--model",      default="claude-sonnet-4-6", help="LLM model for swarm agents")
    args = p.parse_args()

    max_pct = 1.0 if args.yolo else 0.40

    # ── Portfolio mode ────────────────────────────────────────────────────────
    if args.portfolio or args.once:
        if args.once:
            now  = pd.Timestamp.now(tz="America/New_York")
            mins = now.hour * 60 + now.minute
            is_market = now.weekday() < 5 and (9 * 60 + 30 <= mins < 16 * 60)
            uni = EXPLOSIVE_UNIVERSE if is_market else CRYPTO_UNIVERSE
            run_portfolio_cycle(args.size, args.positions, 1.0 / args.positions,
                                args.live, universe=uni)
        else:
            run_portfolio_loop(args.size, args.positions, args.interval, args.live)
        return

    # ── Direct modes (no LLM) ─────────────────────────────────────────────────
    if args.scan and not args.backtest:
        run_scan(account_size=args.size)
        return

    if args.aggressive and not args.backtest:
        run_aggressive(account_size=args.size)
        return

    if args.backtest:
        if args.aggressive or args.yolo:
            backtest_aggressive(n_days=args.backtest, account_size=args.size, max_pct=max_pct)
        else:
            backtest_scanner(n_days=args.backtest, account_size=args.size)
        return

    # ── Swarm mode (default) ──────────────────────────────────────────────────
    mode = "LIVE" if args.live else "PAPER"
    print(f"\n{'#'*70}")
    print(f"  TRADING SWARM  {datetime.now().strftime('%Y-%m-%d %H:%M ET')}")
    print(f"  Account: ${args.size:,.0f}  |  Mode: {mode}  |  Model: {args.model}")
    print(f"  Agents: MarketScanner, CatalystHunter, RegimeAnalyst,")
    print(f"          RiskManager, TradeDecision")
    print(f"{'#'*70}\n")

    if args.live:
        print("  ⚠  LIVE MODE — real orders will be placed on Alpaca paper account")
        print("     Ctrl+C to abort\n")
        import zoneinfo
        _et = zoneinfo.ZoneInfo("America/New_York")
        _now_et = datetime.now(_et)
        _mins = _now_et.hour * 60 + _now_et.minute
        _is_rth = _now_et.weekday() < 5 and (9 * 60 + 30 <= _mins < 16 * 60)
        if not _is_rth:
            print(f"  Market is closed ({_now_et.strftime('%A %Y-%m-%d %H:%M ET')}).")
            print("  Live mode requires RTH (Mon–Fri 09:30–16:00 US/Eastern). Exiting.")
            sys.exit(0)

    try:
        swarm = build_swarm(account_size=args.size, live=args.live, model=args.model)
    except ImportError:
        print("  ERROR: swarms package not installed.")
        print("  Run:  pip install swarms --break-system-packages")
        sys.exit(1)

    task = (
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M ET')}. "
        f"Account size: ${args.size:,.0f}. "
        f"Goal: identify and {'execute' if args.live else 'recommend'} today's single "
        f"highest-probability explosive move. "
        f"Maximum acceptable loss per trade: 5% of account. "
        f"Minimum acceptable expected gain: 6% of account. "
        f"Prioritize leveraged ETFs and high-momentum tech in a bull regime. "
        f"Be decisive — one trade, fully justified."
    )

    result = swarm.run(task)

    print("\n" + "="*70)
    print("  SWARM FINAL OUTPUT")
    print("="*70)
    print(result)
    print("="*70 + "\n")

    # Parse TradeDecision JSON from result and write swarm_daily_trade.json
    try:
        _td: dict = {}
        _idx = result.rfind('"final_decision"')
        if _idx > 0:
            _start = result.rfind('{', 0, _idx)
            if _start >= 0:
                _depth, _end = 0, -1
                for _i, _c in enumerate(result[_start:], _start):
                    if _c == '{': _depth += 1
                    elif _c == '}':
                        _depth -= 1
                        if _depth == 0:
                            _end = _i
                            break
                if _end > _start:
                    _td = json.loads(result[_start:_end + 1])
        if _td.get("final_decision") in ("EXECUTE", "PASS"):
            _stop_p  = float(_td.get("stop_price",   0) or 0)
            _tgt_p   = float(_td.get("target_price",  0) or 0)
            _gain_p  = float(_td.get("gain_if_target_pct", 0) or 0)
            _loss_p  = float(_td.get("loss_if_stop_pct",   0) or 0)
            _rr      = round(_gain_p / _loss_p, 2) if _loss_p else 2.0
            _entry_p = round((_tgt_p + _rr * _stop_p) / (1 + _rr), 2) if (_stop_p and _tgt_p) else 0.0
            _write_swarm_trade({
                "symbol":       _td.get("symbol", ""),
                "strategy":     "swarm",
                "side":         _td.get("side", "buy"),
                "entry_price":  _entry_p,
                "stop_price":   _stop_p,
                "target_price": _tgt_p,
                "rr_ratio":     _rr,
                "notional":     float(_td.get("notional", 0) or 0),
                "score":        int(_td.get("confidence", 0) or 0),
                "thesis":       _td.get("trade_thesis", result[-800:]),
                "mode":         "live" if args.live else "paper",
            })
    except Exception as _exc:
        print(f"  [swarm trade JSON] warning: {_exc}")


if __name__ == "__main__":
    main()
