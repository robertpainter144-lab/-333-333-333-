#!/usr/bin/env python3
"""
ES Mini — Mean Reversion Improvement Sweep (round 3)
Fixed: EMA25/61, reversion_ratio=0.80, ATR_STOP_MULT=2.0, no TOD filter
Sweeps:
  - min_dev_pct          : fine-grained entry deviation threshold
  - trend_strength_pct   : min % gap between EMA25 and EMA61 (no entry in choppy/weak trend)
  - breakeven_trigger_pts: once up X ES points, slide stop to break-even
"""

import warnings
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BARS_CACHE      = Path("es_bars_5min_60d.csv")
EMA_FAST        = 25
EMA_SLOW        = 61
ATR_PERIOD      = 14
ATR_STOP_MULT   = 2.0
REVERSION_RATIO = 0.80
ES_MULTIPLIER   = 50
COMMISSION      = 4.50
INITIAL_CAPITAL = 25_000
NUM_CONTRACTS   = 1
OUTPUT_CSV      = "es_mean_rev_sweep3_results.csv"

BASELINE = dict(n=11, win_rate=63.6, pf=2.78, net_pnl=4838, sharpe=2.94, max_dd_pct=-4.2)

# ── Sweep grid ────────────────────────────────────────────────────────────────
MIN_DEV_PCTS          = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]
TREND_STRENGTH_PCTS   = [0.0, 0.10, 0.20, 0.30, 0.40]  # % gap EMA25 vs EMA61
BREAKEVEN_TRIGGER_PTS = [0, 3, 5, 8, 12]               # 0 = disabled


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    df["atr"] = pd.concat([hl, hcp, lcp], axis=1).max(axis=1).ewm(span=ATR_PERIOD, adjust=False).mean()
    return df


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    min_dev_pct: float,
    trend_strength_pct: float,
    breakeven_pts: float,
) -> dict:
    warmup      = max(EMA_FAST, EMA_SLOW, ATR_PERIOD)
    trades      = []
    position    = None
    min_dev     = min_dev_pct / 100.0
    trend_min   = trend_strength_pct / 100.0

    for i in range(warmup, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]
        close = row["close"]
        ema_f = row["ema_fast"]
        ema_s = row["ema_slow"]
        atr   = row["atr"]
        pef   = prev["ema_fast"]
        pes   = prev["ema_slow"]

        if position is not None:
            side        = position["side"]
            entry_price = position["entry_price"]
            entry_dev   = position["entry_dev"]
            stop_price  = position["stop_price"]
            bars_held   = i - position["entry_idx"]
            golden = pef <= pes and ema_f > ema_s
            death  = pef >= pes and ema_f < ema_s

            # Break-even stop: slide once up breakeven_pts
            if breakeven_pts > 0 and not position.get("be_triggered", False):
                pts_up = (close - entry_price) if side == "long" else (entry_price - close)
                if pts_up >= breakeven_pts:
                    be_stop = entry_price + 0.25 if side == "long" else entry_price - 0.25
                    if side == "long":
                        position["stop_price"] = max(position["stop_price"], be_stop)
                    else:
                        position["stop_price"] = min(position["stop_price"], be_stop)
                    position["be_triggered"] = True
                    stop_price = position["stop_price"]

            target_price = (entry_price + REVERSION_RATIO * entry_dev) if side == "long" \
                      else (entry_price - REVERSION_RATIO * entry_dev)

            exit_reason = None
            if side == "long":
                if   close <= stop_price:    exit_reason = "atr_stop"
                elif death:                  exit_reason = "trend_flip"
                elif close >= target_price:  exit_reason = "reversion_target"
            else:
                if   close >= stop_price:    exit_reason = "atr_stop"
                elif golden:                 exit_reason = "trend_flip"
                elif close <= target_price:  exit_reason = "reversion_target"

            if exit_reason:
                pts = (close - entry_price) if side == "long" else (entry_price - close)
                pnl = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
                trades.append({
                    "side": side, "pts": pts, "pnl": pnl,
                    "exit_reason": exit_reason, "bars_held": bars_held,
                })
                position = None

        if position is None:
            uptrend    = ema_f > ema_s
            downtrend  = ema_f < ema_s
            dev_long   = (ema_f - close) / ema_f
            dev_short  = (close - ema_f) / ema_f
            # trend strength: gap between fast and slow as % of price
            strength   = abs(ema_f - ema_s) / close

            if uptrend and close < ema_f and dev_long >= min_dev and strength >= trend_min:
                position = {
                    "side": "long", "entry_price": close,
                    "entry_dev": ema_f - close,
                    "stop_price": close - ATR_STOP_MULT * atr,
                    "entry_idx": i, "be_triggered": False,
                }
            elif downtrend and close > ema_f and dev_short >= min_dev and strength >= trend_min:
                position = {
                    "side": "short", "entry_price": close,
                    "entry_dev": close - ema_f,
                    "stop_price": close + ATR_STOP_MULT * atr,
                    "entry_idx": i, "be_triggered": False,
                }

    if position is not None:
        side  = position["side"]
        close = df.iloc[-1]["close"]
        pts   = (close - position["entry_price"]) if side == "long" else (position["entry_price"] - close)
        pnl   = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
        trades.append({"side": side, "pts": pts, "pnl": pnl,
                        "exit_reason": "end_of_data",
                        "bars_held": len(df) - 1 - position["entry_idx"]})

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
        "pf":         round(float(pf), 3)     if not np.isnan(pf)     else np.nan,
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
    df = pd.read_csv(BARS_CACHE, index_col=0, parse_dates=True)
    df = add_indicators(df)

    total = len(MIN_DEV_PCTS) * len(TREND_STRENGTH_PCTS) * len(BREAKEVEN_TRIGGER_PTS)
    print(
        f"Running {total} combinations  (EMA{EMA_FAST}/{EMA_SLOW}, rev={REVERSION_RATIO})\n"
        f"  min_dev_pct          : {MIN_DEV_PCTS}\n"
        f"  trend_strength_pct   : {TREND_STRENGTH_PCTS}\n"
        f"  breakeven_trigger_pts: {BREAKEVEN_TRIGGER_PTS}\n"
        f"\n  Baseline: n={BASELINE['n']}  wr={BASELINE['win_rate']}%  "
        f"pf={BASELINE['pf']}  pnl=+${BASELINE['net_pnl']:,}  "
        f"sharpe=+{BASELINE['sharpe']}  dd={BASELINE['max_dd_pct']}%\n"
    )

    rows = []
    for idx, (min_dev, trend_str, be_pts) in enumerate(
        product(MIN_DEV_PCTS, TREND_STRENGTH_PCTS, BREAKEVEN_TRIGGER_PTS), 1
    ):
        m = run_backtest(df, min_dev, trend_str, be_pts)
        rows.append({
            "min_dev_pct": min_dev, "trend_strength_pct": trend_str,
            "breakeven_pts": be_pts, **m,
        })
        sh_s  = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        pf_s  = f"{m['pf']:.2f}"      if not np.isnan(m["pf"])     else " n/a"
        beats = (not np.isnan(m["sharpe"]) and m["sharpe"] > BASELINE["sharpe"] and m["n"] >= 8)
        flag  = " ◄ BETTER" if beats else ""
        print(
            f"  [{idx:>3}/{total}]  dev≥{min_dev:.2f}%  str≥{trend_str:.2f}%  be={be_pts:>2}pts"
            f"  │  n={m['n']:>3}  wr={m['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+9,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%{flag}"
        )

    results = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    results.to_csv(OUTPUT_CSV, index=False)

    print(f"\n{'='*82}")
    print("  TOP 15 BY SHARPE  (n ≥ 8)")
    print(f"{'='*82}")
    top = results[results["n"] >= 8].head(15)
    for _, r in top.iterrows():
        sh_s  = f"{r['sharpe']:+.2f}" if not np.isnan(r["sharpe"]) else "  n/a"
        pf_s  = f"{r['pf']:.2f}"      if not np.isnan(r["pf"])     else " n/a"
        beats = not np.isnan(r["sharpe"]) and r["sharpe"] > BASELINE["sharpe"]
        flag  = " ◄ BEATS BASELINE" if beats else ""
        print(
            f"  dev≥{r['min_dev_pct']:.2f}%  str≥{r['trend_strength_pct']:.2f}%  be={int(r['breakeven_pts']):>2}pts"
            f"  │  n={int(r['n']):>3}  wr={r['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${r['net_pnl']:>+9,.0f}  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%{flag}"
        )
    print(f"{'='*82}")
    print(f"\nFull results → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
