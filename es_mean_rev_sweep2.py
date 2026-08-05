#!/usr/bin/env python3
"""
ES Mini — Mean Reversion Improvement Sweep (round 2)
Fixed: min_dev_pct=0.50%, ATR stop mult=2.0 (stops never fire anyway)
Sweeps:
  - ema_pairs        : (fast, slow) EMA period combinations
  - reversion_ratio  : exit when price has reverted this fraction back to EMA_fast
                       (1.0 = full touch, 0.5 = halfway, 0.75 = 75% of way back)
  - tod_filter       : skip bars in first/last 30 min of RTH (09:30–10:00, 15:30–16:00)

Bars loaded from cached CSV. Run es_mean_rev_sweep.py first if cache is missing.
"""

import sys
import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Fixed parameters ──────────────────────────────────────────────────────────
BARS_CACHE      = Path("es_bars_5min_60d.csv")
ATR_PERIOD      = 14
ATR_STOP_MULT   = 2.0
MIN_DEV_PCT     = 0.50          # fixed at best from sweep 1
ES_MULTIPLIER   = 50
COMMISSION      = 4.50
INITIAL_CAPITAL = 25_000
NUM_CONTRACTS   = 1
OUTPUT_CSV      = "es_mean_rev_sweep2_results.csv"

# ── Sweep grid ────────────────────────────────────────────────────────────────
EMA_PAIRS = [
    (10, 20), (10, 30), (10, 40),
    (15, 30), (15, 45),
    (20, 40), (20, 50), (20, 61),
    (25, 61), (25, 80),
    (30, 80),
]
REVERSION_RATIOS = [0.50, 0.65, 0.80, 1.00]   # fraction of deviation to capture
TOD_FILTERS      = [False, True]               # True = skip open/close 30 min


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    df["atr"] = pd.concat([hl, hcp, lcp], axis=1).max(axis=1).ewm(span=ATR_PERIOD, adjust=False).mean()
    return df


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    fast: int,
    slow: int,
    reversion_ratio: float,
    tod_filter: bool,
) -> dict:
    warmup   = max(fast, slow, ATR_PERIOD)
    trades   = []
    position = None
    min_dev  = MIN_DEV_PCT / 100.0

    for i in range(warmup, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]
        close = row["close"]
        ema_f = row["ema_fast"]
        ema_s = row["ema_slow"]
        atr   = row["atr"]
        pef   = prev["ema_fast"]
        pes   = prev["ema_slow"]

        # Time-of-day filter: skip 09:30–10:00 and 15:30–16:00 ET
        if tod_filter:
            ts = df.index[i]
            minute = ts.hour * 60 + ts.minute
            # RTH open window (13:30–14:00 UTC = 09:30–10:00 ET) and
            # RTH close window (20:30–21:00 UTC = 15:30–16:00 ET)
            # Handle both tz-aware and naive (naive assumed ET-equivalent from IB)
            h, m = ts.hour, ts.minute
            in_open_noise  = (h == 9  and m < 30)  or (h == 9  and m >= 30 and m < 60)
            in_close_noise = (h == 15 and m >= 30) or (h == 16 and m == 0)
            # For IB timestamps that come as "YYYYMMDD HH:MM:SS" in ET:
            in_open_noise  = (h == 9  and 30 <= m < 60)
            in_close_noise = (h == 15 and m >= 30)
            if in_open_noise or in_close_noise:
                if position is not None:
                    # Don't enter new, but allow exit checks to run below
                    pass
                else:
                    continue   # skip entry consideration entirely

        if position is not None:
            side         = position["side"]
            entry_price  = position["entry_price"]
            entry_dev    = position["entry_dev"]      # abs deviation at entry
            stop_price   = position["stop_price"]
            bars_held    = i - position["entry_idx"]
            golden = pef <= pes and ema_f > ema_s
            death  = pef >= pes and ema_f < ema_s

            # Compute partial target price
            if side == "long":
                # target: entry_price + reversion_ratio * entry_dev
                target_price = entry_price + reversion_ratio * entry_dev
            else:
                target_price = entry_price - reversion_ratio * entry_dev

            exit_reason = None
            if side == "long":
                if   close <= stop_price:          exit_reason = "atr_stop"
                elif death:                        exit_reason = "trend_flip"
                elif close >= target_price:        exit_reason = "reversion_target"
            else:
                if   close >= stop_price:          exit_reason = "atr_stop"
                elif golden:                       exit_reason = "trend_flip"
                elif close <= target_price:        exit_reason = "reversion_target"

            if exit_reason:
                pts = (close - entry_price) if side == "long" else (entry_price - close)
                pnl = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
                trades.append({
                    "side": side, "pts": pts, "pnl": pnl,
                    "exit_reason": exit_reason, "bars_held": bars_held,
                })
                position = None

        if position is None:
            uptrend   = ema_f > ema_s
            downtrend = ema_f < ema_s
            dev_long  = (ema_f - close) / ema_f
            dev_short = (close - ema_f) / ema_f

            if uptrend and close < ema_f and dev_long >= min_dev:
                position = {
                    "side": "long", "entry_price": close,
                    "entry_dev": ema_f - close,
                    "stop_price": close - ATR_STOP_MULT * atr,
                    "entry_idx": i,
                }
            elif downtrend and close > ema_f and dev_short >= min_dev:
                position = {
                    "side": "short", "entry_price": close,
                    "entry_dev": close - ema_f,
                    "stop_price": close + ATR_STOP_MULT * atr,
                    "entry_idx": i,
                }

    # Force-close open trade
    if position is not None:
        side  = position["side"]
        close = df.iloc[-1]["close"]
        pts   = (close - position["entry_price"]) if side == "long" else (position["entry_price"] - close)
        pnl   = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
        trades.append({"side": side, "pts": pts, "pnl": pnl,
                        "exit_reason": "end_of_data", "bars_held": len(df) - 1 - position["entry_idx"]})

    if not trades:
        return {"n": 0, "win_rate": 0, "pf": np.nan, "net_pnl": 0,
                "sharpe": np.nan, "max_dd_pct": 0, "avg_pts": 0}

    pnl_s  = pd.Series([t["pnl"] for t in trades])
    wins   = (pnl_s > 0).sum()
    losses = (pnl_s < 0).sum()
    equity = INITIAL_CAPITAL + pnl_s.cumsum()
    hwm    = equity.cummax()
    dd     = equity - hwm
    max_dd_pct = (dd.min() / hwm.loc[dd.idxmin()]) * 100

    n        = len(pnl_s)
    mean_pnl = pnl_s.mean()
    std_pnl  = pnl_s.std(ddof=1) if n > 1 else np.nan
    sample_days = max((df.index[-1] - df.index[0]).days, 1)
    tpy      = n / (sample_days / 252)
    sharpe   = (mean_pnl / std_pnl) * np.sqrt(tpy) if std_pnl and std_pnl > 0 else np.nan
    pf       = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
               if losses and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    return {
        "n":          n,
        "win_rate":   round(wins / n * 100, 1),
        "pf":         round(float(pf), 3)    if not np.isnan(pf)     else np.nan,
        "net_pnl":    round(float(pnl_s.sum()), 2),
        "sharpe":     round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        "max_dd_pct": round(float(max_dd_pct), 2),
        "avg_pts":    round(float(pd.Series([t["pts"] for t in trades]).mean()), 3),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not BARS_CACHE.exists():
        sys.exit(f"Cache not found: {BARS_CACHE}\nRun es_mean_rev_sweep.py first.")

    print(f"Loading bars from {BARS_CACHE}")
    raw = pd.read_csv(BARS_CACHE, index_col=0, parse_dates=True)

    total = len(EMA_PAIRS) * len(REVERSION_RATIOS) * len(TOD_FILTERS)
    print(f"Running {total} combinations\n"
          f"  EMA pairs       : {len(EMA_PAIRS)}  {EMA_PAIRS}\n"
          f"  Reversion ratio : {REVERSION_RATIOS}\n"
          f"  TOD filter      : {TOD_FILTERS}\n"
          f"  Fixed min_dev   : {MIN_DEV_PCT}%  |  ATR mult: {ATR_STOP_MULT}\n")

    baseline = {"n": 11, "win_rate": 63.6, "pf": 3.10,
                "net_pnl": 3600, "sharpe": 2.73, "max_dd_pct": -6.8}
    print(f"  Baseline (EMA20/61, ratio=1.0, no TOD): "
          f"n=11  wr=63.6%  pf=3.10  pnl=+$3,600  sharpe=+2.73  dd=-6.8%\n")

    rows = []
    for idx, ((fast, slow), rev_ratio, tod) in enumerate(
        product(EMA_PAIRS, REVERSION_RATIOS, TOD_FILTERS), 1
    ):
        df = add_indicators(raw, fast, slow)
        m  = run_backtest(df, fast, slow, rev_ratio, tod)
        rows.append({
            "ema_fast": fast, "ema_slow": slow,
            "reversion_ratio": rev_ratio, "tod_filter": tod,
            **m,
        })
        sh_s = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        pf_s = f"{m['pf']:.2f}"      if not np.isnan(m["pf"])     else " n/a"
        flag = " ◄ BETTER" if (not np.isnan(m["sharpe"]) and m["sharpe"] > baseline["sharpe"] and m["n"] >= 8) else ""
        print(
            f"  [{idx:>3}/{total}]  EMA{fast}/{slow:<2}  rev={rev_ratio:.2f}  tod={'Y' if tod else 'N'}"
            f"  │  n={m['n']:>3}  wr={m['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+9,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%{flag}"
        )

    results = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    results.to_csv(OUTPUT_CSV, index=False)

    print(f"\n{'='*80}")
    print("  TOP 15 BY SHARPE  (n ≥ 8)")
    print(f"{'='*80}")
    top = results[results["n"] >= 8].head(15)
    for _, r in top.iterrows():
        sh_s = f"{r['sharpe']:+.2f}" if not np.isnan(r["sharpe"]) else "  n/a"
        pf_s = f"{r['pf']:.2f}"      if not np.isnan(r["pf"])     else " n/a"
        flag = " ◄ BEATS BASELINE" if not np.isnan(r["sharpe"]) and r["sharpe"] > baseline["sharpe"] else ""
        print(
            f"  EMA{int(r['ema_fast'])}/{int(r['ema_slow']):<2}  rev={r['reversion_ratio']:.2f}  tod={'Y' if r['tod_filter'] else 'N'}"
            f"  │  n={int(r['n']):>3}  wr={r['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${r['net_pnl']:>+9,.0f}  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%{flag}"
        )
    print(f"{'='*80}")
    print(f"\nFull results → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
