#!/usr/bin/env python3
"""
SPY Open Range Trend — ADX Regime Filter Sweep
Base config: EMA25, trail=3.0×ATR, entry 09:30–11:00, no EOD close
Baseline: Sharpe +0.68  |  PF 1.14  |  Net +$168,495  |  MaxDD -18.2%
Problem years: 2022 (-$19,854)  2026 partial

Sweeps:
  adx_max       : [0=off, 20, 25, 30, 35]  — skip longs when ADX > threshold
  adx_short     : [False, True]             — when ADX > threshold, short rallies to EMA instead
  adx_period    : [14, 20]                  — ADX smoothing period
"""

import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE      = Path("spy_5min_bars.csv")
OUTPUT_CSV = "spy_ort_adx_results.csv"

EMA_PERIOD   = 25
ATR_PERIOD   = 14
TRAIL_MULT   = 3.0
ENTRY_START  = 9 * 60 + 30
ENTRY_END    = 11 * 60
INITIAL_EQ   = 100_000.0
LEVERAGE     = 2.0
COMM         = 0.005

BASELINE = dict(sharpe=0.68, pf=1.14, net_pnl=168495, max_dd_pct=-18.2, n=2089)

ADX_MAXES   = [0, 20, 25, 30, 35]
ADX_SHORTS  = [False, True]
ADX_PERIODS = [14, 20]


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame, adx_period: int) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()

    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

    # ADX
    up   = df["high"] - df["high"].shift(1)
    down = df["low"].shift(1) - df["low"]
    pdm  = np.where((up > down) & (up > 0), up, 0.0)
    ndm  = np.where((down > up) & (down > 0), down, 0.0)
    atr_w = tr.ewm(span=adx_period, adjust=False).mean()
    pdi   = 100 * pd.Series(pdm, index=df.index).ewm(span=adx_period, adjust=False).mean() / atr_w
    ndi   = 100 * pd.Series(ndm, index=df.index).ewm(span=adx_period, adjust=False).mean() / atr_w
    dx    = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    df["adx"] = dx.ewm(span=adx_period, adjust=False).mean()
    df["pdi"] = pdi
    df["ndi"] = ndi
    return df


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame, adx_max: float, adx_short: bool) -> dict:
    warmup   = 80
    trades   = []
    position = None
    equity   = INITIAL_EQ

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        close = row["close"]
        ema   = row["ema"]
        atr   = row["atr"]
        adx   = row["adx"]
        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute
        trending = adx_max > 0 and adx > adx_max
        downtrend = row["ndi"] > row["pdi"]

        # ── Manage open position ─────────────────────────────────────────────
        if position is not None:
            side  = position["side"]
            new_stop = close - TRAIL_MULT * atr if side == "long" \
                  else close + TRAIL_MULT * atr

            if side == "long":
                if new_stop > position["trail_stop"]:
                    position["trail_stop"] = new_stop
                hit = close <= position["trail_stop"]
            else:
                if new_stop < position["trail_stop"]:
                    position["trail_stop"] = new_stop
                hit = close >= position["trail_stop"]

            if hit:
                shares = position["shares"]
                gross  = (close - position["entry_price"]) * shares \
                         if side == "long" \
                         else (position["entry_price"] - close) * shares
                comm   = shares * COMM * 2
                pnl    = gross - comm
                equity += pnl
                trades.append({
                    "side":      side,
                    "pnl":       pnl,
                    "bars_held": i - position["entry_idx"],
                    "month":     ts.to_period("M"),
                    "year":      ts.year,
                })
                position = None

        # ── Entry ────────────────────────────────────────────────────────────
        if position is None and ENTRY_START <= mins < ENTRY_END:
            shares = int((equity * LEVERAGE) / close)
            if shares < 1:
                continue

            if not trending:
                # Mean-reversion / trend-follow long (normal regime)
                if close > ema:
                    position = {
                        "side":        "long",
                        "entry_price": close,
                        "shares":      shares,
                        "trail_stop":  close - TRAIL_MULT * atr,
                        "entry_idx":   i,
                    }
            elif adx_short and downtrend:
                # Trending down + ADX high → short rallies above EMA
                if close >= ema:
                    position = {
                        "side":        "short",
                        "entry_price": close,
                        "shares":      shares,
                        "trail_stop":  close + TRAIL_MULT * atr,
                        "entry_idx":   i,
                    }

    # Close open position at end of data
    if position is not None:
        side   = position["side"]
        close  = df.iloc[-1]["close"]
        ts     = df.index[-1]
        shares = position["shares"]
        gross  = (close - position["entry_price"]) * shares if side == "long" \
             else (position["entry_price"] - close) * shares
        pnl    = gross - shares * COMM * 2
        equity += pnl
        trades.append({
            "side": side, "pnl": pnl,
            "bars_held": len(df) - 1 - position["entry_idx"],
            "month": ts.to_period("M"), "year": ts.year,
        })

    if not trades:
        return {"n": 0, "win_rate": 0, "pf": np.nan,
                "net_pnl": 0, "sharpe": np.nan, "max_dd_pct": 0, "trades": []}

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

    return {
        "n":          n,
        "win_rate":   round(wins / n * 100, 1),
        "pf":         round(float(pf), 3)     if not np.isnan(pf)     else np.nan,
        "net_pnl":    round(float(pnl_s.sum()), 0),
        "sharpe":     round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        "max_dd_pct": round(float(max_dd_pct), 2),
        "trades":     trades,
    }


def yearly_breakdown(trades, label):
    t = pd.DataFrame(trades)
    print(f"\n  {label}")
    print(f"  {'Year':<6}  {'n':>4}  {'L/S':>7}  {'Win%':>5}  {'PF':>5}  {'PnL':>12}  {'MaxDD':>7}")
    print("  " + "─" * 60)
    for yr, grp in t.groupby("year"):
        g      = grp["pnl"]
        gn     = len(g)
        gw     = (g > 0).sum()
        gl     = gn - gw
        longs  = (grp["side"] == "long").sum()
        shorts = (grp["side"] == "short").sum()
        g_eq   = INITIAL_EQ + g.cumsum()
        g_hwm  = g_eq.cummax()
        g_dd   = g_eq - g_hwm
        g_dd_pct = (g_dd.min() / g_hwm.loc[g_dd.idxmin()]) * 100 if g_dd.min() < 0 else 0.0
        g_pf   = (-g[g > 0].sum() / g[g < 0].sum()) if gl and g[g < 0].sum() != 0 else np.nan
        pf_s   = f"{g_pf:.2f}" if not np.isnan(g_pf) else " n/a"
        print(
            f"  {yr:<6}  {gn:>4}  {longs}L/{shorts}S  "
            f"{gw/gn*100:>4.0f}%  {pf_s:>5}  "
            f"${g.sum():>+10,.0f}  {g_dd_pct:>+6.1f}%"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not CACHE.exists():
        import sys
        sys.exit(f"Cache not found: {CACHE}")

    print(f"Loading bars from {CACHE}")
    raw = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    print(f"  {len(raw):,} bars  {raw.index[0].date()} → {raw.index[-1].date()}\n")

    total = len(ADX_MAXES) * len(ADX_SHORTS) * len(ADX_PERIODS)
    print(f"Base: EMA{EMA_PERIOD}  trail={TRAIL_MULT}×  → "
          f"Sharpe {BASELINE['sharpe']:+.2f}  PF {BASELINE['pf']:.2f}  "
          f"PnL +${BASELINE['net_pnl']:,}  DD {BASELINE['max_dd_pct']}%\n")
    print(f"Running {total} combinations")
    print(f"  adx_max    : {ADX_MAXES}")
    print(f"  adx_short  : {ADX_SHORTS}")
    print(f"  adx_period : {ADX_PERIODS}\n")

    ind_cache = {}
    rows = []

    for idx, (adx_max, adx_short, adx_per) in enumerate(
        product(ADX_MAXES, ADX_SHORTS, ADX_PERIODS), 1
    ):
        if adx_per not in ind_cache:
            ind_cache[adx_per] = add_indicators(raw, adx_per)
        df = ind_cache[adx_per]

        m = run_backtest(df, adx_max, adx_short)
        rows.append({
            "adx_max": adx_max, "adx_short": adx_short,
            "adx_period": adx_per,
            **{k: v for k, v in m.items() if k != "trades"},
        })
        sh_s  = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        pf_s  = f"{m['pf']:.2f}"      if not np.isnan(m["pf"])     else " n/a"
        beats = not np.isnan(m["sharpe"]) and m["sharpe"] > BASELINE["sharpe"] and m["n"] >= 50
        flag  = " ◄ BETTER" if beats else ""
        print(
            f"  [{idx:>2}/{total}]  adx<{adx_max if adx_max else '--':>2}  "
            f"short={'Y' if adx_short else 'N'}  adxper={adx_per}"
            f"  │  n={m['n']:>4}  wr={m['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%{flag}"
        )

    results = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    results.to_csv(OUTPUT_CSV, index=False)

    print(f"\n{'='*95}")
    print("  TOP 10 BY SHARPE  (n ≥ 50)")
    print(f"{'='*95}")
    top = results[results["n"] >= 50].head(10)
    for _, r in top.iterrows():
        sh_s  = f"{r['sharpe']:+.2f}" if not np.isnan(r["sharpe"]) else "  n/a"
        pf_s  = f"{r['pf']:.2f}"      if not np.isnan(r["pf"])     else " n/a"
        beats = not np.isnan(r["sharpe"]) and r["sharpe"] > BASELINE["sharpe"]
        flag  = " ◄ BEATS BASELINE" if beats else ""
        print(
            f"  adx<{int(r['adx_max']) if r['adx_max'] else '--':>2}  "
            f"short={'Y' if r['adx_short'] else 'N'}  adxper={int(r['adx_period'])}"
            f"  │  n={int(r['n']):>4}  wr={r['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${r['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%{flag}"
        )
    print(f"{'='*95}")

    # Yearly breakdown for top 3 configs
    print(f"\n{'='*95}")
    print("  PER-YEAR — TOP 3 CONFIGS")
    print(f"{'='*95}")
    for _, r in top.head(3).iterrows():
        adx_per = int(r["adx_period"])
        if adx_per not in ind_cache:
            ind_cache[adx_per] = add_indicators(raw, adx_per)
        m = run_backtest(ind_cache[adx_per], r["adx_max"], r["adx_short"])
        label = (f"adx<{int(r['adx_max']) if r['adx_max'] else '--'}  "
                 f"short={'Y' if r['adx_short'] else 'N'}  adxper={int(r['adx_period'])}"
                 f"  │  n={int(r['n'])}  sharpe={r['sharpe']:+.2f}  "
                 f"pnl=${r['net_pnl']:+,.0f}  dd={r['max_dd_pct']:+.1f}%")
        yearly_breakdown(m["trades"], label)

    print(f"\nFull results → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
