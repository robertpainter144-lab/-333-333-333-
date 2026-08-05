#!/usr/bin/env python3
"""
ES Open Range — Regime-Adaptive Strategy Sweep

Adds two regime filters on top of the open-range mean-rev strategy:
  1. adx_max         : skip entry when ADX(14) > threshold (no mean-rev in trending markets)
  2. trend_dir_only  : only trade longs in EMA uptrend, shorts in EMA downtrend

Also tests a hybrid mode:
  3. trend_follow    : when ADX > adx_max (trending), switch to trend-following entries
                       (short rallies to EMA in downtrend, long dips to EMA in uptrend)
                       instead of skipping

Entry window: 09:30–11:00 ET  |  Force-close: 15:55 ET
Bars: es_bars_11m_5min.csv
"""

import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

BARS_CACHE       = Path("es_bars_11m_5min.csv")
EMA_FAST         = 20
EMA_SLOW         = 61
ATR_PERIOD       = 14
ADX_PERIOD       = 14
ATR_STOP_MULT    = 2.0
REVERSION_RATIO  = 1.0
ES_MULTIPLIER    = 50
COMMISSION       = 4.50
INITIAL_CAPITAL  = 25_000
NUM_CONTRACTS    = 1
OUTPUT_CSV       = "es_open_range_regime_results.csv"

ENTRY_START_MINS = 9 * 60 + 30    # 570 = 09:30
ENTRY_END_MINS   = 11 * 60         # 660 = 11:00
FORCE_CLOSE_MINS = 15 * 60 + 55   # 955 = 15:55

# Sweep grid
MIN_DEV_PCTS     = [0.20, 0.30, 0.40]
ADX_MAXES        = [0, 20, 25, 30]     # 0 = disabled
TREND_DIR_ONLYS  = [False, True]
TREND_FOLLOW_MODES = [False, True]     # True = trend-follow when ADX exceeds adx_max


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    # ATR
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

    # ADX (Wilder smoothed)
    up_move   = df["high"] - df["high"].shift(1)
    down_move = df["low"].shift(1) - df["low"]
    pdm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    ndm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    df["pdm_s"]  = pd.Series(pdm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean()
    df["ndm_s"]  = pd.Series(ndm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean()
    df["atr_w"]  = tr.ewm(span=ADX_PERIOD, adjust=False).mean()
    df["pdi"]    = 100 * df["pdm_s"] / df["atr_w"]
    df["ndi"]    = 100 * df["ndm_s"] / df["atr_w"]
    denom = (df["pdi"] + df["ndi"]).replace(0, np.nan)
    dx = 100 * (df["pdi"] - df["ndi"]).abs() / denom
    df["adx"]    = dx.ewm(span=ADX_PERIOD, adjust=False).mean()

    return df


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    min_dev_pct: float,
    adx_max: float,
    trend_dir_only: bool,
    trend_follow_mode: bool,
) -> dict:
    warmup   = max(EMA_FAST, EMA_SLOW, ATR_PERIOD, ADX_PERIOD) + 5
    trades   = []
    position = None
    min_dev  = min_dev_pct / 100.0

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        prev  = df.iloc[i - 1]
        close = row["close"]
        ema_f = row["ema_fast"]
        ema_s = row["ema_slow"]
        atr   = row["atr"]
        adx   = row["adx"]
        pef   = prev["ema_fast"]
        pes   = prev["ema_slow"]

        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute

        # ── Manage open position ──────────────────────────────────────────────
        if position is not None:
            side        = position["side"]
            entry_price = position["entry_price"]
            entry_dev   = position["entry_dev"]
            stop_price  = position["stop_price"]
            bars_held   = i - position["entry_idx"]
            mode        = position["mode"]

            golden = pef <= pes and ema_f > ema_s
            death  = pef >= pes and ema_f < ema_s

            exit_reason = None

            # Force close near EOD
            if mins >= FORCE_CLOSE_MINS:
                exit_reason = "force_close_eod"
            elif mode == "mean_rev":
                target_price = (entry_price + REVERSION_RATIO * entry_dev) if side == "long" \
                          else (entry_price - REVERSION_RATIO * entry_dev)
                if side == "long":
                    if   close <= stop_price:   exit_reason = "atr_stop"
                    elif death:                 exit_reason = "trend_flip"
                    elif close >= target_price: exit_reason = "reversion_target"
                else:
                    if   close >= stop_price:   exit_reason = "atr_stop"
                    elif golden:                exit_reason = "trend_flip"
                    elif close <= target_price: exit_reason = "reversion_target"
            else:  # trend_follow
                target_price = position["target_price"]
                if side == "long":
                    if   close <= stop_price:   exit_reason = "atr_stop"
                    elif death:                 exit_reason = "trend_flip"
                    elif close >= target_price: exit_reason = "trend_target"
                else:
                    if   close >= stop_price:   exit_reason = "atr_stop"
                    elif golden:                exit_reason = "trend_flip"
                    elif close <= target_price: exit_reason = "trend_target"

            if exit_reason:
                pts = (close - entry_price) if side == "long" else (entry_price - close)
                pnl = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
                trades.append({
                    "side": side, "pts": pts, "pnl": pnl,
                    "exit_reason": exit_reason, "bars_held": bars_held,
                    "mode": mode, "month": ts.to_period("M"),
                })
                position = None

        # ── Entry logic ───────────────────────────────────────────────────────
        if position is None and ENTRY_START_MINS <= mins < ENTRY_END_MINS:
            uptrend   = ema_f > ema_s
            downtrend = ema_f < ema_s

            # ADX regime check
            trending = (adx_max > 0) and (adx > adx_max)

            if not trending:
                # Mean-reversion entry
                dev_long  = (ema_f - close) / ema_f
                dev_short = (close - ema_f) / ema_f

                long_ok  = uptrend  and close < ema_f and dev_long  >= min_dev
                short_ok = downtrend and close > ema_f and dev_short >= min_dev

                if trend_dir_only:
                    # Only trade in macro trend direction
                    pass  # long_ok/short_ok already require uptrend/downtrend respectively

                if long_ok:
                    position = {
                        "side": "long", "entry_price": close,
                        "entry_dev": ema_f - close,
                        "stop_price": close - ATR_STOP_MULT * atr,
                        "entry_idx": i, "mode": "mean_rev",
                    }
                elif short_ok:
                    position = {
                        "side": "short", "entry_price": close,
                        "entry_dev": close - ema_f,
                        "stop_price": close + ATR_STOP_MULT * atr,
                        "entry_idx": i, "mode": "mean_rev",
                    }

            elif trend_follow_mode:
                # Trend-following entry: price breaks in direction of trend
                # Long: uptrend AND price crossed above EMA fast from below (momentum)
                # Short: downtrend AND price crossed below EMA fast from above
                prev_close = prev["close"]
                prev_ema_f = pef

                long_break  = uptrend   and prev_close < prev_ema_f and close >= ema_f
                short_break = downtrend and prev_close > prev_ema_f and close <= ema_f

                if not trend_dir_only:
                    # Both directions allowed in trend mode
                    pass
                # In trend_dir_only mode with trend_follow, still only trade in macro direction
                # (which is already enforced by uptrend/downtrend check above)

                if long_break:
                    target = close + ATR_STOP_MULT * atr   # 2×ATR target
                    position = {
                        "side": "long", "entry_price": close,
                        "entry_dev": ema_f - close,
                        "stop_price": close - ATR_STOP_MULT * atr,
                        "target_price": target,
                        "entry_idx": i, "mode": "trend_follow",
                    }
                elif short_break:
                    target = close - ATR_STOP_MULT * atr
                    position = {
                        "side": "short", "entry_price": close,
                        "entry_dev": close - ema_f,
                        "stop_price": close + ATR_STOP_MULT * atr,
                        "target_price": target,
                        "entry_idx": i, "mode": "trend_follow",
                    }

    # Force-close any open position at end of data
    if position is not None:
        side  = position["side"]
        close = df.iloc[-1]["close"]
        pts   = (close - position["entry_price"]) if side == "long" else (position["entry_price"] - close)
        pnl   = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
        trades.append({
            "side": side, "pts": pts, "pnl": pnl,
            "exit_reason": "end_of_data", "bars_held": len(df) - 1 - position["entry_idx"],
            "mode": position["mode"],
            "month": df.index[-1].to_period("M"),
        })

    if not trades:
        return {"n": 0, "win_rate": 0, "pf": np.nan, "net_pnl": 0,
                "sharpe": np.nan, "max_dd_pct": 0, "avg_pts": 0, "trades": []}

    pnl_s      = pd.Series([t["pnl"] for t in trades])
    wins       = (pnl_s > 0).sum()
    losses     = (pnl_s < 0).sum()
    equity     = INITIAL_CAPITAL + pnl_s.cumsum()
    hwm        = equity.cummax()
    dd         = equity - hwm
    max_dd_pct = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0

    n           = len(pnl_s)
    mean_pnl    = pnl_s.mean()
    std_pnl     = pnl_s.std(ddof=1) if n > 1 else np.nan
    sample_days = max((df.index[-1] - df.index[0]).days, 1)
    tpy         = n / (sample_days / 252)
    sharpe      = (mean_pnl / std_pnl) * np.sqrt(tpy) if std_pnl and std_pnl > 0 else np.nan
    pf          = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
                  if losses and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    return {
        "n":          n,
        "win_rate":   round(wins / n * 100, 1),
        "pf":         round(float(pf), 3)     if not np.isnan(pf)     else np.nan,
        "net_pnl":    round(float(pnl_s.sum()), 2),
        "sharpe":     round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        "max_dd_pct": round(float(max_dd_pct), 2),
        "avg_pts":    round(float(pd.Series([t["pts"] for t in trades]).mean()), 3),
        "trades":     trades,
    }


def monthly_breakdown(trades, label):
    if not trades:
        return
    t_df = pd.DataFrame(trades)
    for month, grp in t_df.groupby("month"):
        pnl_s  = grp["pnl"]
        wins   = (pnl_s > 0).sum()
        n      = len(pnl_s)
        avg_w  = pnl_s[pnl_s > 0].mean() if wins else 0
        avg_l  = pnl_s[pnl_s <= 0].mean() if (pnl_s <= 0).any() else 0
        longs  = (grp["side"] == "long").sum()
        shorts = (grp["side"] == "short").sum()
        pf_val = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
                 if (pnl_s < 0).any() else np.nan
        pf_s   = f"{pf_val:.2f}" if not np.isnan(pf_val) else " n/a"
        print(
            f"    {month}  {n:>3}t  {longs}L/{shorts}S"
            f"  wr={wins/n*100:.0f}%  pf={pf_s}"
            f"  pnl=${pnl_s.sum():>+8,.0f}"
            f"  avgW=${avg_w:>+7,.0f}  avgL=${avg_l:>+7,.0f}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not BARS_CACHE.exists():
        import sys
        sys.exit(f"Cache not found: {BARS_CACHE}\nRun es_open_range_backtest.py first.")

    print(f"Loading bars from {BARS_CACHE}")
    df = pd.read_csv(BARS_CACHE, index_col=0, parse_dates=True)
    df = add_indicators(df)
    print(f"  {len(df):,} bars  {df.index[0].date()} → {df.index[-1].date()}\n")

    # Baseline: no filters, dev≥0.20%, rev=1.0 (from previous run)
    baseline = {"n": 28, "sharpe": -0.28, "net_pnl": -1251, "max_dd_pct": -21.9}
    print(f"  Baseline (no regime filter): "
          f"n={baseline['n']}  sharpe={baseline['sharpe']:+.2f}  "
          f"pnl=${baseline['net_pnl']:,}  dd={baseline['max_dd_pct']}%\n")

    # Full grid: adx_max × trend_dir_only × trend_follow × min_dev_pct
    total = len(ADX_MAXES) * len(TREND_DIR_ONLYS) * len(TREND_FOLLOW_MODELS := TREND_FOLLOW_MODES) * len(MIN_DEV_PCTS)
    print(f"Running {total} combinations\n"
          f"  adx_max         : {ADX_MAXES}\n"
          f"  trend_dir_only  : {TREND_DIR_ONLYS}\n"
          f"  trend_follow    : {TREND_FOLLOW_MODES}\n"
          f"  min_dev_pct     : {MIN_DEV_PCTS}\n")

    rows = []
    for idx, (adx_max, tdo, tfm, mdp) in enumerate(
        product(ADX_MAXES, TREND_DIR_ONLYS, TREND_FOLLOW_MODES, MIN_DEV_PCTS), 1
    ):
        m = run_backtest(df, mdp, adx_max, tdo, tfm)
        rows.append({
            "adx_max": adx_max, "trend_dir_only": tdo,
            "trend_follow": tfm, "min_dev_pct": mdp, **{k: v for k, v in m.items() if k != "trades"},
        })
        sh_s  = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        pf_s  = f"{m['pf']:.2f}"      if not np.isnan(m["pf"])     else " n/a"
        beats = not np.isnan(m["sharpe"]) and m["sharpe"] > 0 and m["n"] >= 5
        flag  = " ◄ POSITIVE" if beats else ""
        print(
            f"  [{idx:>3}/{total}]  adx<{adx_max if adx_max else '--':>2}  "
            f"dir={'Y' if tdo else 'N'}  tf={'Y' if tfm else 'N'}  dev≥{mdp:.2f}%"
            f"  │  n={m['n']:>3}  wr={m['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+9,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%{flag}"
        )

    results = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    results.to_csv(OUTPUT_CSV, index=False)

    # Top configs
    print(f"\n{'='*100}")
    print("  TOP 15 BY SHARPE  (n ≥ 5)")
    print(f"{'='*100}")
    top = results[results["n"] >= 5].head(15)
    for _, r in top.iterrows():
        sh_s  = f"{r['sharpe']:+.2f}" if not np.isnan(r["sharpe"]) else "  n/a"
        pf_s  = f"{r['pf']:.2f}"      if not np.isnan(r["pf"])     else " n/a"
        beats = not np.isnan(r["sharpe"]) and r["sharpe"] > 0
        flag  = " ◄ POSITIVE" if beats else ""
        print(
            f"  adx<{int(r['adx_max']) if r['adx_max'] else '--':>2}  "
            f"dir={'Y' if r['trend_dir_only'] else 'N'}  tf={'Y' if r['trend_follow'] else 'N'}  "
            f"dev≥{r['min_dev_pct']:.2f}%"
            f"  │  n={int(r['n']):>3}  wr={r['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${r['net_pnl']:>+9,.0f}  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%{flag}"
        )
    print(f"{'='*100}")

    # Per-month breakdown for top 5 positive configs
    pos_configs = results[(results["n"] >= 5) & (results["sharpe"] > 0)].head(5)
    if len(pos_configs):
        print(f"\n{'='*100}")
        print("  PER-MONTH BREAKDOWN — TOP POSITIVE CONFIGS")
        print(f"{'='*100}")
        for _, r in pos_configs.iterrows():
            m = run_backtest(df, r["min_dev_pct"], r["adx_max"], r["trend_dir_only"], r["trend_follow"])
            pf_s = f"{r['pf']:.2f}" if not np.isnan(r["pf"]) else "n/a"
            print(f"\n  adx<{int(r['adx_max']) if r['adx_max'] else '--'}  dir={'Y' if r['trend_dir_only'] else 'N'}  "
                  f"tf={'Y' if r['trend_follow'] else 'N'}  dev≥{r['min_dev_pct']:.2f}%"
                  f"  │  n={int(r['n'])}  wr={r['win_rate']:.1f}%  pf={pf_s}"
                  f"  sharpe={r['sharpe']:+.2f}  pnl=${r['net_pnl']:+,.0f}  dd={r['max_dd_pct']:+.1f}%")
            monthly_breakdown(m["trades"], "")
    else:
        print("\n  No positive-Sharpe configs found with n≥5.")
        print("  Best available configs:")
        for _, r in results[results["n"] >= 5].head(3).iterrows():
            m = run_backtest(df, r["min_dev_pct"], r["adx_max"], r["trend_dir_only"], r["trend_follow"])
            pf_s = f"{r['pf']:.2f}" if not np.isnan(r["pf"]) else "n/a"
            print(f"\n  adx<{int(r['adx_max']) if r['adx_max'] else '--'}  dir={'Y' if r['trend_dir_only'] else 'N'}  "
                  f"tf={'Y' if r['trend_follow'] else 'N'}  dev≥{r['min_dev_pct']:.2f}%"
                  f"  │  n={int(r['n'])}  wr={r['win_rate']:.1f}%  pf={pf_s}"
                  f"  sharpe={r['sharpe']:+.2f}  pnl=${r['net_pnl']:+,.0f}  dd={r['max_dd_pct']:+.1f}%")
            monthly_breakdown(m["trades"], "")

    print(f"\nFull results → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
