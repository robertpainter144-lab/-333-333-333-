#!/usr/bin/env python3
"""
SPY Open Range Trend — Parameter Sweep
Improves on baseline: EMA(34), trail 2.5×ATR, 09:30–11:00, no EOD close
Sharpe +0.35  |  PF 1.08  |  Net +$37,247  |  MaxDD -24.2%

Sweeps:
  ema_period    : [20, 25, 30, 34, 40, 50]
  trail_mult    : [1.5, 2.0, 2.5, 3.0, 3.5]
  entry_mins    : [30, 60, 90]          window after 09:30
  eod_close     : [False, True]         force-close at 15:50 ET
"""

import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE        = Path("spy_5min_bars.csv")
OUTPUT_CSV   = "spy_ort_sweep_results.csv"

ATR_PERIOD      = 14
INITIAL_EQUITY  = 100_000.0
LEVERAGE        = 2.0
COMM_PER_SHARE  = 0.005
ENTRY_START     = 9 * 60 + 30   # 570
EOD_CLOSE_MINS  = 15 * 60 + 50  # 950 = 15:50 ET

BASELINE = dict(sharpe=0.35, pf=1.08, net_pnl=37247, max_dd_pct=-24.2, n=1146)

EMA_PERIODS  = [20, 25, 30, 34, 40, 50]
TRAIL_MULTS  = [1.5, 2.0, 2.5, 3.0, 3.5]
ENTRY_WIDTHS = [30, 60, 90]      # minutes after 09:30
EOD_CLOSES   = [False, True]


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame, ema_period: int) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = df["close"].ewm(span=ema_period, adjust=False).mean()
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    df["atr"] = pd.concat([hl, hcp, lcp], axis=1).max(axis=1).ewm(
        span=ATR_PERIOD, adjust=False).mean()
    return df


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame, trail_mult: float,
                 entry_end: int, eod_close: bool) -> dict:
    warmup   = 60
    trades   = []
    position = None
    equity   = INITIAL_EQUITY

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        close = row["close"]
        ema   = row["ema"]
        atr   = row["atr"]
        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute

        if position is not None:
            new_stop = close - trail_mult * atr
            if new_stop > position["trail_stop"]:
                position["trail_stop"] = new_stop

            exit_reason = None
            if eod_close and mins >= EOD_CLOSE_MINS:
                exit_reason = "eod"
            elif close <= position["trail_stop"]:
                exit_reason = "trail_stop"

            if exit_reason:
                shares  = position["shares"]
                gross   = (close - position["entry_price"]) * shares
                comm    = shares * COMM_PER_SHARE * 2
                pnl     = gross - comm
                equity += pnl
                trades.append({
                    "pnl":       pnl,
                    "bars_held": i - position["entry_idx"],
                    "month":     ts.to_period("M"),
                    "year":      ts.year,
                    "exit":      exit_reason,
                })
                position = None

        if position is None and ENTRY_START <= mins < entry_end:
            if close > ema:
                shares = int((equity * LEVERAGE) / close)
                if shares < 1:
                    continue
                position = {
                    "entry_price": close,
                    "shares":      shares,
                    "trail_stop":  close - trail_mult * atr,
                    "entry_idx":   i,
                }

    # Close open position at end of data
    if position is not None:
        close  = df.iloc[-1]["close"]
        ts     = df.index[-1]
        shares = position["shares"]
        gross  = (close - position["entry_price"]) * shares
        comm   = shares * COMM_PER_SHARE * 2
        pnl    = gross - comm
        equity += pnl
        trades.append({
            "pnl":       pnl,
            "bars_held": len(df) - 1 - position["entry_idx"],
            "month":     ts.to_period("M"),
            "year":      ts.year,
            "exit":      "end_of_data",
        })

    if not trades:
        return {"n": 0, "win_rate": 0, "pf": np.nan,
                "net_pnl": 0, "sharpe": np.nan, "max_dd_pct": 0}

    pnl_s      = pd.Series([t["pnl"] for t in trades])
    wins       = (pnl_s > 0).sum()
    losses     = (pnl_s <= 0).sum()
    equity_s   = INITIAL_EQUITY + pnl_s.cumsum()
    hwm        = equity_s.cummax()
    dd         = equity_s - hwm
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


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not CACHE.exists():
        import sys
        sys.exit(f"Cache not found: {CACHE}\nRun spy_ort_backtest.py first.")

    print(f"Loading bars from {CACHE}")
    raw = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    print(f"  {len(raw):,} bars  {raw.index[0].date()} → {raw.index[-1].date()}\n")

    total = len(EMA_PERIODS) * len(TRAIL_MULTS) * len(ENTRY_WIDTHS) * len(EOD_CLOSES)
    print(f"Baseline : EMA34  trail=2.5×  entry=90min  eod=N  →  "
          f"Sharpe {BASELINE['sharpe']:+.2f}  PF {BASELINE['pf']:.2f}  "
          f"PnL +${BASELINE['net_pnl']:,}  DD {BASELINE['max_dd_pct']}%\n")
    print(f"Running {total} combinations")
    print(f"  ema_period  : {EMA_PERIODS}")
    print(f"  trail_mult  : {TRAIL_MULTS}")
    print(f"  entry_width : {ENTRY_WIDTHS} min")
    print(f"  eod_close   : {EOD_CLOSES}\n")

    rows = []
    # Cache indicator sets per EMA period (expensive to recompute each time)
    indicator_cache = {}

    for idx, (ema, trail, width, eod) in enumerate(
        product(EMA_PERIODS, TRAIL_MULTS, ENTRY_WIDTHS, EOD_CLOSES), 1
    ):
        if ema not in indicator_cache:
            indicator_cache[ema] = add_indicators(raw, ema)
        df = indicator_cache[ema]

        entry_end = ENTRY_START + width * 60  # convert minutes to bar-minutes
        m = run_backtest(df, trail, entry_end, eod)
        rows.append({
            "ema": ema, "trail_mult": trail,
            "entry_mins": width, "eod_close": eod,
            **{k: v for k, v in m.items() if k != "trades"},
        })
        sh_s   = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        pf_s   = f"{m['pf']:.2f}"      if not np.isnan(m["pf"])     else " n/a"
        beats  = not np.isnan(m["sharpe"]) and m["sharpe"] > BASELINE["sharpe"] and m["n"] >= 50
        flag   = " ◄ BETTER" if beats else ""
        print(
            f"  [{idx:>3}/{total}]  EMA{ema:<2}  trail={trail}×  "
            f"entry={width:>2}min  eod={'Y' if eod else 'N'}"
            f"  │  n={m['n']:>4}  wr={m['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%{flag}"
        )

    results = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    results.to_csv(OUTPUT_CSV, index=False)

    print(f"\n{'='*100}")
    print("  TOP 20 BY SHARPE  (n ≥ 50)")
    print(f"{'='*100}")
    top = results[results["n"] >= 50].head(20)
    for _, r in top.iterrows():
        sh_s  = f"{r['sharpe']:+.2f}" if not np.isnan(r["sharpe"]) else "  n/a"
        pf_s  = f"{r['pf']:.2f}"      if not np.isnan(r["pf"])     else " n/a"
        beats = not np.isnan(r["sharpe"]) and r["sharpe"] > BASELINE["sharpe"]
        flag  = " ◄ BEATS BASELINE" if beats else ""
        print(
            f"  EMA{int(r['ema']):<2}  trail={r['trail_mult']}×  "
            f"entry={int(r['entry_mins']):>2}min  eod={'Y' if r['eod_close'] else 'N'}"
            f"  │  n={int(r['n']):>4}  wr={r['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${r['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%{flag}"
        )
    print(f"{'='*100}")

    # Per-year breakdown for the single best config
    best = top.iloc[0]
    ema_b  = int(best["ema"])
    if ema_b not in indicator_cache:
        indicator_cache[ema_b] = add_indicators(raw, ema_b)
    df_b   = indicator_cache[ema_b]
    entry_end_b = ENTRY_START + int(best["entry_mins"]) * 60
    m_b    = run_backtest(df_b, best["trail_mult"], entry_end_b, best["eod_close"])
    t_df   = pd.DataFrame(m_b["trades"])

    print(f"\n  Per-year — best config: "
          f"EMA{ema_b}  trail={best['trail_mult']}×  "
          f"entry={int(best['entry_mins'])}min  eod={'Y' if best['eod_close'] else 'N'}")
    print(f"  {'Year':<6}  {'n':>4}  {'Win%':>5}  {'PF':>5}  {'PnL':>12}  {'MaxDD':>9}")
    print("  " + "─" * 55)
    for yr, grp in t_df.groupby("year"):
        g    = grp["pnl"]
        gn   = len(g)
        gw   = (g > 0).sum()
        gl   = gn - gw
        g_eq = INITIAL_EQUITY + g.cumsum()
        g_hwm = g_eq.cummax()
        g_dd  = g_eq - g_hwm
        g_dd_pct = (g_dd.min() / g_hwm.loc[g_dd.idxmin()]) * 100 if g_dd.min() < 0 else 0.0
        g_pf = (-g[g > 0].sum() / g[g < 0].sum()) if gl and g[g < 0].sum() != 0 else np.nan
        pf_s = f"{g_pf:.2f}" if not np.isnan(g_pf) else " n/a"
        print(
            f"  {yr:<6}  {gn:>4}  {gw/gn*100:>4.0f}%  {pf_s:>5}  "
            f"${g.sum():>+10,.0f}  {g_dd_pct:>+8.1f}%"
        )

    print(f"\nFull results → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
