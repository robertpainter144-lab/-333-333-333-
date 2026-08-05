#!/usr/bin/env python3
"""
Dynamic Adaptive Backtest — any Alpaca-supported symbol
========================================================
Improvements over spy_adaptive.py (baseline: Sharpe +0.90, +$138k, DD -10.7%):

  1. ASSET-AGNOSTIC  : set SYMBOL at top; data auto-fetched & cached per ticker
  2. VOL REGIME      : rolling ATR percentile (63-day) → low / mid / high buckets
  3. DYNAMIC EMA     : period scales with vol regime  (15 → 25 → 35)
  4. DYNAMIC TRAIL   : ATR mult scales with vol regime (2.0 → 3.0 → 4.0)
  5. TREND BOOST     : wider trail during strong bull trends (let winners run)
  6. BREAKEVEN STOP  : once N×ATR in profit → floor stop at entry price

Sweep (36 combinations):
  vol_ema_adapt   : [False, True]         adapt EMA period to vol regime
  vol_trail_adapt : [False, True]         adapt trail mult to vol regime
  breakeven_mult  : [0.0, 1.0, 1.5]      ATR gain to set trail floor = entry
  trend_boost     : [0.0, 0.3, 0.6]      extra trail ATR mult in strong bull trends

Usage for any symbol:
  Change SYMBOL = "QQQ"  (or AAPL, GLD, BTC/USD, etc.)
  Data is fetched from Alpaca sandbox and cached as {symbol}_5min_bars.csv.
"""

import os
import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOL = "SPY"   # ← change to any Alpaca-supported ticker

ALPACA_KEY    = "CK4HXKGM3HWRKLXWRU3CDLB6LX"
ALPACA_SECRET = "7i1mhApwTyzqWprLyvGvbzuDJbNSquMRTCnoiXfJSEWk"
DATA_BASE     = "https://data.sandbox.alpaca.markets"

# Fixed base parameters
ATR_PERIOD    = 14
ADX_PERIOD    = 20
ADX_THRESHOLD = 35
ENTRY_START   = 9 * 60 + 30   # 09:30 ET
ENTRY_END     = 11 * 60        # 11:00 ET (open-range window)
EOD_MINS      = 15 * 60 + 50   # force-close shorts at 15:50
INITIAL_EQ    = 100_000.0
LEVERAGE      = 2.0
COMM          = 0.005          # $/share per leg

# Vol-regime bucketed parameters — low / mid / high
# Mid bucket matches the fixed best from spy_adaptive.py so we can measure lift
EMA_BY_VOL   = (15, 25, 35)      # EMA period  per vol bucket
TRAIL_BY_VOL = (2.0, 3.0, 4.0)   # trail mult  per vol bucket

# Rolling percentile window for vol regime: 63 trading days of 5-min bars
# 63 days × ~78 bars/day ≈ 4,914 bars; capped at min_periods=300 for warmup
VOL_RANK_WIN = 63 * 78

BASELINE = dict(sharpe=0.90, net_pnl=138_777, max_dd_pct=-10.7)

# Sweep axes
S_VOL_EMA   = [False, True]
S_VOL_TRAIL = [False, True]
S_BREAKEVEN = [0.0, 1.0, 1.5]
S_BOOST     = [0.0, 0.3, 0.6]


# ── Data ──────────────────────────────────────────────────────────────────────
def fetch_bars(symbol: str, years: int = 5) -> pd.DataFrame:
    end   = pd.Timestamp.now(tz="America/New_York").floor("min")
    start = end - pd.DateOffset(years=years)
    hdrs  = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    bars, token = [], None
    print(f"  Fetching {symbol} 5-min bars from Alpaca ...")
    while True:
        params = {
            "symbols": symbol, "timeframe": "5Min",
            "start": start.isoformat(), "end": end.isoformat(),
            "limit": 10_000, "feed": "iex", "session": "regular",
        }
        if token:
            params["page_token"] = token
        r = requests.get(f"{DATA_BASE}/v2/stocks/bars", headers=hdrs, params=params)
        r.raise_for_status()
        data = r.json()
        chunk = data.get("bars", {}).get(symbol, [])
        bars.extend(chunk)
        token = data.get("next_page_token")
        print(f"    {len(bars):,} bars ...", end="\r")
        if not token:
            break
    print(f"\n  {len(bars):,} total bars")
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    df = (df.set_index("t")
            .rename(columns={"o": "open", "h": "high", "l": "low",
                             "c": "close", "v": "volume"})
          [["open", "high", "low", "close", "volume"]]
          .sort_index())
    return df


def load_or_fetch(symbol: str) -> pd.DataFrame:
    cache = Path(f"{symbol.lower()}_5min_bars.csv")
    if cache.exists():
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        if df.index.tz is not None:
            df.index = df.index.tz_convert("America/New_York")
        df = df.between_time("09:30", "16:00")
        print(f"  Loaded {len(df):,} bars from {cache}  "
              f"({df.index[0].date()} → {df.index[-1].date()})")
        return df
    df = fetch_bars(symbol)
    df.index = df.index.tz_localize(None)  # strip tz before saving to CSV
    df.to_csv(cache)
    print(f"  Saved to {cache}")
    return df


# ── Indicators ────────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # All EMA periods we'll need
    for p in set(EMA_BY_VOL):
        df[f"ema_{p}"] = df["close"].ewm(span=p, adjust=False).mean()

    # ATR (Wilder EMA of True Range)
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

    # Volatility regime: rolling percentile of normalized ATR (ATR as % of price)
    # A percentile rank at each bar tells us where current vol sits in the
    # distribution of the past 63 trading days — adapts to any asset's vol scale.
    df["natr"]     = df["atr"] / df["close"]
    df["vol_rank"] = df["natr"].rolling(VOL_RANK_WIN, min_periods=300).rank(pct=True)

    # ADX / PDI / NDI
    up   = df["high"] - df["high"].shift(1)
    down = df["low"].shift(1)  - df["low"]
    pdm  = np.where((up > down) & (up > 0), up, 0.0)
    ndm  = np.where((down > up) & (down > 0), down, 0.0)
    atr_w = tr.ewm(span=ADX_PERIOD, adjust=False).mean()
    pdi   = (100 * pd.Series(pdm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean()
             / atr_w)
    ndi   = (100 * pd.Series(ndm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean()
             / atr_w)
    dx    = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    df["adx"] = dx.ewm(span=ADX_PERIOD, adjust=False).mean()
    df["pdi"] = pdi
    df["ndi"] = ndi

    # Daily gap %
    df["_date"]      = df.index.date
    daily_close      = df.groupby("_date")["close"].last()
    daily_close_s    = pd.Series(daily_close.values,
                                  index=pd.to_datetime(list(daily_close.index)))
    prev_close_daily = daily_close_s.shift(1)
    bar_dates        = pd.to_datetime(df.index.date)
    df["prev_close"] = bar_dates.map(prev_close_daily.to_dict()).values
    first_bar        = ~pd.Series(df.index.date, index=df.index).duplicated()
    df["gap_pct"]    = np.where(
        first_bar & df["prev_close"].notna(),
        (df["open"] - df["prev_close"]) / df["prev_close"] * 100,
        np.nan,
    )
    df["gap_pct"] = df["gap_pct"].ffill()
    df.drop(columns=["_date"], inplace=True)
    return df


# ── Adaptive parameter selection (per bar) ───────────────────────────────────
def get_adaptive_params(vol_rank, adx, pdi, ndi,
                        vol_ema_adapt: bool, vol_trail_adapt: bool,
                        trend_boost: float):
    """
    Returns (ema_col, trail_mult, regime, vol_bucket_label).
    Vol bucket thresholds: low ≤ 33rd pct | mid ≤ 67th | high > 67th.
    """
    if np.isnan(vol_rank) or vol_rank <= 0.33:
        bucket, vb_label = 0, "low"
    elif vol_rank <= 0.67:
        bucket, vb_label = 1, "mid"
    else:
        bucket, vb_label = 2, "high"

    ema_p = EMA_BY_VOL[bucket]   if vol_ema_adapt   else EMA_BY_VOL[1]
    trail = TRAIL_BY_VOL[bucket] if vol_trail_adapt else TRAIL_BY_VOL[1]

    if adx > ADX_THRESHOLD and pdi >= ndi:
        regime = "bull"
        # In confirmed uptrends the best trades run further; give more room.
        trail += trend_boost
    elif adx > ADX_THRESHOLD and ndi > pdi:
        regime = "bear"
    else:
        regime = "ranging"

    return f"ema_{ema_p}", trail, regime, vb_label


# ── Single backtest run ───────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame,
                 vol_ema_adapt: bool, vol_trail_adapt: bool,
                 breakeven_mult: float, trend_boost: float) -> dict:
    warmup   = 400
    trades   = []
    position = None
    equity   = INITIAL_EQ

    for i in range(warmup, len(df)):
        row      = df.iloc[i]
        close    = row["close"]
        atr      = row["atr"]
        adx      = row["adx"]
        pdi      = row["pdi"]
        ndi      = row["ndi"]
        vol_rank = row["vol_rank"]
        ts       = df.index[i]
        mins     = ts.hour * 60 + ts.minute

        ema_col, trail_mult, regime, vb = get_adaptive_params(
            vol_rank, adx, pdi, ndi, vol_ema_adapt, vol_trail_adapt, trend_boost
        )
        ema = row[ema_col]

        # ── Manage open position ─────────────────────────────────────────────
        if position is not None:
            side  = position["side"]
            entry = position["entry_price"]

            # Breakeven: once unrealized gain >= breakeven_mult × ATR, floor stop at entry
            if breakeven_mult > 0 and not position["be_hit"]:
                gain = (close - entry) if side == "long" else (entry - close)
                if gain >= breakeven_mult * atr:
                    position["be_hit"] = True

            if side == "long":
                new_stop = close - position["trail_mult"] * atr
                if position["be_hit"]:
                    new_stop = max(new_stop, entry)
                if new_stop > position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close <= position["trail_stop"]
            else:
                new_stop = close + position["trail_mult"] * atr
                if position["be_hit"]:
                    new_stop = min(new_stop, entry)
                if new_stop < position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close >= position["trail_stop"]

            # Force-close shorts at EOD; let longs run via trail
            exit_now = mins >= EOD_MINS and side == "short"

            if stop_hit or exit_now:
                shares = position["shares"]
                gross  = (close - entry) * shares if side == "long" \
                     else (entry - close) * shares
                pnl    = gross - shares * COMM * 2
                equity += pnl
                trades.append({
                    "side":       side,
                    "pnl":        pnl,
                    "bars_held":  i - position["entry_idx"],
                    "exit":       "eod" if exit_now else "trail",
                    "regime":     position["regime"],
                    "vol_bucket": position["vol_bucket"],
                    "be_hit":     position["be_hit"],
                    "year":       ts.year,
                    "month":      ts.to_period("M"),
                })
                position = None

        # ── Entry ────────────────────────────────────────────────────────────
        if position is None and ENTRY_START <= mins < ENTRY_END:
            shares = int((equity * LEVERAGE) / close)
            if shares < 1:
                continue

            if regime in ("ranging", "bull") and close > ema:
                position = dict(
                    side="long", entry_price=close, shares=shares,
                    trail_stop=close - trail_mult * atr, trail_mult=trail_mult,
                    entry_idx=i, regime=regime, vol_bucket=vb, be_hit=False,
                )
            elif regime == "bear" and close >= ema:
                position = dict(
                    side="short", entry_price=close, shares=shares,
                    trail_stop=close + trail_mult * atr, trail_mult=trail_mult,
                    entry_idx=i, regime=regime, vol_bucket=vb, be_hit=False,
                )

    # Close any open position at end of data
    if position is not None:
        side  = position["side"]
        close = df.iloc[-1]["close"]
        ts    = df.index[-1]
        shares = position["shares"]
        gross  = (close - position["entry_price"]) * shares if side == "long" \
             else (position["entry_price"] - close) * shares
        pnl    = gross - shares * COMM * 2
        equity += pnl
        trades.append({
            "side": side, "pnl": pnl, "bars_held": len(df) - 1 - position["entry_idx"],
            "exit": "end_of_data", "regime": position["regime"],
            "vol_bucket": position["vol_bucket"], "be_hit": position["be_hit"],
            "year": ts.year, "month": ts.to_period("M"),
        })

    if not trades:
        return dict(n=0, sharpe=np.nan, net_pnl=0, max_dd_pct=0,
                    win_rate=0, pf=np.nan, trades=[])

    pnl_s      = pd.Series([t["pnl"] for t in trades])
    wins       = (pnl_s > 0).sum()
    losses     = (pnl_s <= 0).sum()
    eq_s       = INITIAL_EQ + pnl_s.cumsum()
    hwm        = eq_s.cummax()
    dd         = eq_s - hwm
    max_dd_pct = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
    n          = len(pnl_s)
    std        = pnl_s.std(ddof=1) if n > 1 else np.nan
    n_days     = max((df.index[-1] - df.index[0]).days, 1)
    tpy        = n / (n_days / 252)
    sharpe     = (pnl_s.mean() / std) * np.sqrt(tpy) if std and std > 0 else np.nan
    pf         = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
                 if losses and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    return dict(
        n=n,
        win_rate=round(wins / n * 100, 1),
        pf=round(float(pf), 3)     if not np.isnan(pf)     else np.nan,
        net_pnl=round(float(pnl_s.sum()), 0),
        sharpe=round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        max_dd_pct=round(float(max_dd_pct), 2),
        trades=trades,
    )


# ── Output helpers ────────────────────────────────────────────────────────────
def print_yearly(trades, label=""):
    t = pd.DataFrame(trades)
    if label:
        print(f"\n  {label}")
    print(f"  {'Year':<6} {'n':>4} {'L/S':>9} {'Win%':>5} {'PF':>5}"
          f" {'PnL':>12} {'MaxDD':>7} {'AvgHold':>8}")
    print("  " + "─" * 74)
    for yr, grp in t.groupby("year"):
        g      = grp["pnl"]
        gn, gw = len(g), (g > 0).sum()
        ls     = grp["side"].value_counts()
        longs  = ls.get("long", 0)
        shorts = ls.get("short", 0)
        g_eq   = INITIAL_EQ + g.cumsum()
        g_hwm  = g_eq.cummax()
        g_dd   = g_eq - g_hwm
        dd_pct = (g_dd.min() / g_hwm.loc[g_dd.idxmin()]) * 100 if g_dd.min() < 0 else 0.0
        pf_v   = (-g[g > 0].sum() / g[g < 0].sum()) if (gn - gw) and g[g < 0].sum() != 0 else np.nan
        avg_h  = grp["bars_held"].mean() * 5 / 60
        pf_s   = f"{pf_v:.2f}" if not np.isnan(pf_v) else " n/a"
        print(
            f"  {yr:<6} {gn:>4} {longs:>4}L/{shorts:<4}S "
            f"{gw/gn*100:>4.0f}%  {pf_s:>5} "
            f"${g.sum():>+10,.0f}  {dd_pct:>+6.1f}%  {avg_h:>6.1f}hr"
        )


def print_vol_breakdown(trades, label=""):
    t = pd.DataFrame(trades)
    if label:
        print(f"\n  {label} — by vol regime")
    print(f"  {'Bucket':<6} {'n':>4} {'Win%':>5} {'Avg PnL':>9} {'Total PnL':>12}")
    print("  " + "─" * 40)
    for vb in ["low", "mid", "high"]:
        g = t[t["vol_bucket"] == vb]["pnl"] if "vol_bucket" in t.columns else pd.Series()
        if len(g) == 0:
            continue
        wr  = (g > 0).sum() / len(g) * 100
        print(f"  {vb:<6} {len(g):>4} {wr:>4.0f}%  ${g.mean():>8,.0f}  ${g.sum():>+10,.0f}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\nDynamic Adaptive Backtest — {SYMBOL}")
    print("=" * 90)

    df = load_or_fetch(SYMBOL)
    print("  Building indicators ...")
    df = add_indicators(df)
    valid = df["vol_rank"].notna().sum()
    print(f"  vol_rank valid from bar {df['vol_rank'].first_valid_index()}\n")

    total = len(S_VOL_EMA) * len(S_VOL_TRAIL) * len(S_BREAKEVEN) * len(S_BOOST)
    print(f"Baseline (spy_adaptive.py):  Sharpe {BASELINE['sharpe']:+.2f}  "
          f"PnL +${BASELINE['net_pnl']:,}  DD {BASELINE['max_dd_pct']}%\n")
    print(f"Running {total} combinations ...")
    print(f"  EMA by vol regime   : {EMA_BY_VOL}  (low/mid/high period)")
    print(f"  Trail by vol regime : {TRAIL_BY_VOL}  (low/mid/high ATR mult)")
    print(f"  vol_ema_adapt  sweep: {S_VOL_EMA}")
    print(f"  vol_trail_adapt     : {S_VOL_TRAIL}")
    print(f"  breakeven_mult      : {S_BREAKEVEN}")
    print(f"  trend_boost         : {S_BOOST}")
    print()

    rows = []
    for idx, (vea, vta, be, tb) in enumerate(
        product(S_VOL_EMA, S_VOL_TRAIL, S_BREAKEVEN, S_BOOST), 1
    ):
        m = run_backtest(df, vea, vta, be, tb)
        rows.append(dict(
            vol_ema_adapt=vea, vol_trail_adapt=vta, breakeven=be, trend_boost=tb,
            **{k: v for k, v in m.items() if k != "trades"},
        ))
        sh_s  = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        pf_s  = f"{m['pf']:.2f}"      if not np.isnan(m["pf"])     else " n/a"
        beats = not np.isnan(m["sharpe"]) and m["sharpe"] > BASELINE["sharpe"]
        flag  = " ◄ BETTER" if beats else ""
        print(
            f"  [{idx:>2}/{total}]  ema={'dyn' if vea else 'fix'}  trail={'dyn' if vta else 'fix'}"
            f"  be={be:.1f}×  boost={tb:.1f}×"
            f"  │  n={m['n']:>4}  wr={m['win_rate']:>4.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%{flag}"
        )

    results = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    results.to_csv(f"{SYMBOL.lower()}_dynamic_results.csv", index=False)

    sep = "=" * 90
    print(f"\n{sep}")
    print("  TOP 10 BY SHARPE  (n ≥ 50)")
    print(sep)
    top = results[results["n"] >= 50].head(10)
    for _, r in top.iterrows():
        sh_s  = f"{r['sharpe']:+.2f}" if not np.isnan(r["sharpe"]) else "  n/a"
        pf_s  = f"{r['pf']:.2f}"      if not np.isnan(r["pf"])     else " n/a"
        beats = not np.isnan(r["sharpe"]) and r["sharpe"] > BASELINE["sharpe"]
        flag  = "  ◄ BEATS BASELINE" if beats else ""
        print(
            f"  ema={'dyn' if r['vol_ema_adapt'] else 'fix'}  "
            f"trail={'dyn' if r['vol_trail_adapt'] else 'fix'}  "
            f"be={r['breakeven']:.1f}×  boost={r['trend_boost']:.1f}×"
            f"  │  n={int(r['n']):>4}  wr={r['win_rate']:>4.1f}%  pf={pf_s:>5}"
            f"  pnl=${r['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%{flag}"
        )
    print(sep)

    # Per-year and vol-bucket breakdown for top 3
    print(f"\n{sep}")
    print("  PER-YEAR  +  VOL-BUCKET BREAKDOWN — TOP 3 CONFIGS")
    print(sep)
    for _, r in top.head(3).iterrows():
        m = run_backtest(df, r["vol_ema_adapt"], r["vol_trail_adapt"],
                         r["breakeven"], r["trend_boost"])
        label = (
            f"ema={'dyn' if r['vol_ema_adapt'] else 'fix'}  "
            f"trail={'dyn' if r['vol_trail_adapt'] else 'fix'}  "
            f"be={r['breakeven']:.1f}×  boost={r['trend_boost']:.1f}×"
            f"  │  n={int(r['n'])}  sharpe={r['sharpe']:+.2f}  "
            f"pnl=${r['net_pnl']:+,.0f}  dd={r['max_dd_pct']:+.1f}%"
        )
        print_yearly(m["trades"], label)
        print_vol_breakdown(m["trades"])

    print(f"\nFull results → {SYMBOL.lower()}_dynamic_results.csv\n")

    # Summary: what moved the needle
    print(sep)
    print("  FACTOR ANALYSIS — avg Sharpe by setting  (n ≥ 50)")
    print(sep)
    sub = results[results["n"] >= 50]
    for col, vals in [("vol_ema_adapt", [False, True]),
                      ("vol_trail_adapt", [False, True]),
                      ("breakeven", S_BREAKEVEN),
                      ("trend_boost", S_BOOST)]:
        line = f"  {col:<18}"
        for v in vals:
            avg = sub[sub[col] == v]["sharpe"].mean()
            line += f"  {v!s:>5}: {avg:+.3f}"
        print(line)
    print(sep + "\n")


if __name__ == "__main__":
    main()
