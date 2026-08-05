#!/usr/bin/env python3
"""
SPY Dip-Pyramid Backtest
========================
Takes the winning strategy (EMA25, 3×ATR trail, direction-aware ADX, no filters)
and adds one layer: buy the dip WITHIN a winning position.

Pyramid logic:
  After entry, track the highest close reached (pos_high).
  When price pulls back >= dip_atr_back × ATR from pos_high,
  AND unrealized profit is still >= min_profit_atr × ATR per share,
  AND close is still above EMA (trend intact),
  → add add_fraction × original_shares at current price.
  Repeat up to max_adds times per trade.
  The single trailing stop (3×ATR) covers the entire position throughout.

Sweep (24 combinations):
  dip_atr_back   : [0.3, 0.5, 0.8]   ATR pullback from pos_high to trigger add
  min_profit_atr : [0.5, 1.0]         min ATR profit (vs avg entry) before adding
  max_adds       : [1, 2]             max pyramid adds per trade
  add_fraction   : [0.5, 1.0]         add size as fraction of original shares

Baseline: ema=fix, trail=3.0×, be=off, boost=off → Sharpe +0.88, +$168,495, DD -18.2%
(from spy_ort_sweep.py — the raw return champion)
"""

import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE = Path("spy_5min_bars.csv")

EMA_PERIOD    = 25
ATR_PERIOD    = 14
ADX_PERIOD    = 20
ADX_THRESHOLD = 35
LONG_TRAIL    = 3.0
SHORT_TRAIL   = 3.0
ENTRY_START   = 9 * 60 + 30
ENTRY_END     = 11 * 60
EOD_MINS      = 15 * 60 + 50
INITIAL_EQ    = 100_000.0
LEVERAGE      = 2.0
COMM          = 0.005

BASELINE = dict(sharpe=0.88, net_pnl=168_495, max_dd_pct=-18.2, n=1046)

S_DIP_ATR    = [0.3, 0.5, 0.8]
S_MIN_PROFIT = [0.5, 1.0]
S_MAX_ADDS   = [1, 2]
S_ADD_FRAC   = [0.5, 1.0]


# ── Indicators ────────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()

    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

    up   = df["high"] - df["high"].shift(1)
    down = df["low"].shift(1) - df["low"]
    pdm  = np.where((up > down) & (up > 0), up, 0.0)
    ndm  = np.where((down > up) & (down > 0), down, 0.0)
    atr_w = tr.ewm(span=ADX_PERIOD, adjust=False).mean()
    pdi   = 100 * pd.Series(pdm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean() / atr_w
    ndi   = 100 * pd.Series(ndm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean() / atr_w
    dx    = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    df["adx"] = dx.ewm(span=ADX_PERIOD, adjust=False).mean()
    df["pdi"] = pdi
    df["ndi"] = ndi
    return df


def classify_regime(adx, pdi, ndi):
    if adx <= ADX_THRESHOLD:
        return "ranging"
    return "bull_trend" if pdi >= ndi else "bear_trend"


# ── Backtest ──────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame,
                 dip_atr_back: float,
                 min_profit_atr: float,
                 max_adds: int,
                 add_fraction: float) -> dict:
    warmup   = 100
    trades   = []
    position = None
    equity   = INITIAL_EQ

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        close = row["close"]
        ema   = row["ema"]
        atr   = row["atr"]
        adx   = row["adx"]
        pdi   = row["pdi"]
        ndi   = row["ndi"]
        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute

        regime = classify_regime(adx, pdi, ndi)

        # ── Manage open position ─────────────────────────────────────────────
        if position is not None:
            side = position["side"]

            # Update position high (for longs only — track best price reached)
            if side == "long" and close > position["pos_high"]:
                position["pos_high"] = close

            # ── Pyramid: add on dip within winning long ──────────────────────
            if (side == "long"
                    and position["n_adds"] < max_adds
                    and ENTRY_START <= mins < ENTRY_END + 120):  # allow adds up to 13:00
                dip_from_high = position["pos_high"] - close
                profit_per_share = close - position["avg_entry"]
                trend_ok = close > ema  # trend still intact

                if (dip_from_high >= dip_atr_back * atr
                        and profit_per_share >= min_profit_atr * atr
                        and trend_ok):
                    add_shares = max(1, int(position["orig_shares"] * add_fraction))
                    # Recalculate avg entry and total shares
                    old_cost = position["avg_entry"] * position["total_shares"]
                    new_cost = close * add_shares
                    position["total_shares"] += add_shares
                    position["avg_entry"] = (old_cost + new_cost) / position["total_shares"]
                    position["n_adds"] += 1
                    # Trail stop stays put (don't reset for adds — we keep the
                    # existing high-water trail which is already protecting profit)

            # Update trailing stop
            if side == "long":
                new_stop = close - LONG_TRAIL * atr
                if new_stop > position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close <= position["trail_stop"]
            else:
                new_stop = close + SHORT_TRAIL * atr
                if new_stop < position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close >= position["trail_stop"]

            exit_now = mins >= EOD_MINS and side == "short"

            if stop_hit or exit_now:
                shares = position["total_shares"]
                gross  = (close - position["avg_entry"]) * shares if side == "long" \
                     else (position["avg_entry"] - close) * shares
                pnl    = gross - shares * COMM * 2
                equity += pnl
                trades.append({
                    "side":      side,
                    "pnl":       pnl,
                    "bars_held": i - position["entry_idx"],
                    "exit":      "eod" if exit_now else "trail",
                    "regime":    position["regime"],
                    "n_adds":    position["n_adds"],
                    "year":      ts.year,
                    "month":     ts.to_period("M"),
                })
                position = None

        # ── Entry ────────────────────────────────────────────────────────────
        if position is None and ENTRY_START <= mins < ENTRY_END:
            shares = int((equity * LEVERAGE) / close)
            if shares < 1:
                continue

            if regime in ("ranging", "bull_trend") and close > ema:
                position = {
                    "side":         "long",
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close - LONG_TRAIL * atr,
                    "entry_idx":    i,
                    "regime":       regime,
                    "pos_high":     close,
                    "n_adds":       0,
                }
            elif regime == "bear_trend" and close >= ema:
                position = {
                    "side":         "short",
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close + SHORT_TRAIL * atr,
                    "entry_idx":    i,
                    "regime":       regime,
                    "pos_high":     close,
                    "n_adds":       0,
                }

    # Close at end of data
    if position is not None:
        side   = position["side"]
        close  = df.iloc[-1]["close"]
        ts     = df.index[-1]
        shares = position["total_shares"]
        gross  = (close - position["avg_entry"]) * shares if side == "long" \
             else (position["avg_entry"] - close) * shares
        pnl    = gross - shares * COMM * 2
        equity += pnl
        trades.append({
            "side": side, "pnl": pnl,
            "bars_held": len(df) - 1 - position["entry_idx"],
            "exit": "end_of_data", "regime": position["regime"],
            "n_adds": position["n_adds"],
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

    t = pd.DataFrame(trades)
    adds_taken = t["n_adds"].sum()
    pct_with_adds = (t["n_adds"] > 0).mean() * 100

    return dict(
        n=n,
        win_rate=round(wins / n * 100, 1),
        pf=round(float(pf), 3)      if not np.isnan(pf)     else np.nan,
        net_pnl=round(float(pnl_s.sum()), 0),
        sharpe=round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        max_dd_pct=round(float(max_dd_pct), 2),
        adds_taken=int(adds_taken),
        pct_with_adds=round(pct_with_adds, 1),
        trades=trades,
    )


# ── Output ────────────────────────────────────────────────────────────────────
def print_yearly(trades, label=""):
    t = pd.DataFrame(trades)
    if label:
        print(f"\n  {label}")
    print(f"  {'Year':<6} {'n':>4} {'Win%':>5} {'PF':>5} {'PnL':>12} {'MaxDD':>7} {'Adds':>5}")
    print("  " + "─" * 60)
    for yr, grp in t.groupby("year"):
        g  = grp["pnl"]
        gn = len(g); gw = (g > 0).sum()
        eq = INITIAL_EQ + g.cumsum(); hwm = eq.cummax(); dd = eq - hwm
        ddp = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
        pf  = (-g[g > 0].sum() / g[g < 0].sum()) if (gn - gw) and g[g < 0].sum() != 0 else np.nan
        avg_adds = grp["n_adds"].mean()
        print(
            f"  {yr:<6} {gn:>4} {gw/gn*100:>4.0f}%  "
            f"{f'{pf:.2f}' if not np.isnan(pf) else ' n/a':>5} "
            f"${g.sum():>+10,.0f}  {ddp:>+6.1f}%  {avg_adds:>4.2f}x"
        )


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\nSPY Dip-Pyramid Backtest")
    print("=" * 100)
    df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    print(f"  {len(df):,} bars  {df.index[0].date()} → {df.index[-1].date()}")
    print("  Adding indicators ...")
    df = add_indicators(df)

    total = len(S_DIP_ATR) * len(S_MIN_PROFIT) * len(S_MAX_ADDS) * len(S_ADD_FRAC)
    print(f"\nBaseline (no pyramid): Sharpe {BASELINE['sharpe']:+.2f}  "
          f"PnL +${BASELINE['net_pnl']:,}  DD {BASELINE['max_dd_pct']}%  n={BASELINE['n']}\n")
    print(f"Sweeping {total} pyramid configs ...")
    print(f"  dip_atr_back   : {S_DIP_ATR}   (ATR pullback from pos_high to add)")
    print(f"  min_profit_atr : {S_MIN_PROFIT}     (min ATR profit before adding)")
    print(f"  max_adds       : {S_MAX_ADDS}         (max pyramid adds per trade)")
    print(f"  add_fraction   : {S_ADD_FRAC}     (add size as fraction of original)\n")

    rows = []
    for idx, (dip, mp, ma, af) in enumerate(
        product(S_DIP_ATR, S_MIN_PROFIT, S_MAX_ADDS, S_ADD_FRAC), 1
    ):
        m = run_backtest(df, dip, mp, ma, af)
        rows.append(dict(dip_atr=dip, min_profit=mp, max_adds=ma, add_frac=af,
                         **{k: v for k, v in m.items() if k != "trades"}))
        sh_s  = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        pf_s  = f"{m['pf']:.2f}"      if not np.isnan(m["pf"])     else " n/a"
        beats = not np.isnan(m["sharpe"]) and m["net_pnl"] > BASELINE["net_pnl"]
        flag  = f"  ◄ +${m['net_pnl'] - BASELINE['net_pnl']:,.0f}" if beats else ""
        print(
            f"  [{idx:>2}/{total}]  dip={dip:.1f}×  mp={mp:.1f}×  adds≤{ma}  frac={af:.1f}×"
            f"  │  n={m['n']:>4}  wr={m['win_rate']:>4.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%"
            f"  [{m['pct_with_adds']:.0f}% trades added]{flag}"
        )

    results = pd.DataFrame(rows).sort_values("net_pnl", ascending=False)
    results.to_csv("spy_pyramid_results.csv", index=False)

    sep = "=" * 100
    print(f"\n{sep}")
    print("  TOP 10 BY NET PnL  (n ≥ 50)")
    print(sep)
    top = results[results["n"] >= 50].head(10)
    for _, r in top.iterrows():
        sh_s  = f"{r['sharpe']:+.2f}" if not np.isnan(r["sharpe"]) else "  n/a"
        pf_s  = f"{r['pf']:.2f}"      if not np.isnan(r["pf"])     else " n/a"
        delta = r["net_pnl"] - BASELINE["net_pnl"]
        flag  = f"  ◄ +${delta:,.0f} vs baseline" if delta > 0 else f"  ({delta:+,.0f})"
        print(
            f"  dip={r['dip_atr']:.1f}×  mp={r['min_profit']:.1f}×  "
            f"adds≤{int(r['max_adds'])}  frac={r['add_frac']:.1f}×"
            f"  │  n={int(r['n']):>4}  wr={r['win_rate']:>4.1f}%  pf={pf_s:>5}"
            f"  pnl=${r['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%{flag}"
        )
    print(sep)

    print(f"\n{sep}")
    print("  PER-YEAR — TOP 3 CONFIGS")
    print(sep)
    for _, r in top.head(3).iterrows():
        m = run_backtest(df, r["dip_atr"], r["min_profit"], int(r["max_adds"]), r["add_frac"])
        label = (
            f"dip={r['dip_atr']:.1f}×  mp={r['min_profit']:.1f}×  "
            f"adds≤{int(r['max_adds'])}  frac={r['add_frac']:.1f}×"
            f"  │  n={int(r['n'])}  sharpe={r['sharpe']:+.2f}  "
            f"pnl=${r['net_pnl']:+,.0f}  dd={r['max_dd_pct']:+.1f}%"
        )
        print_yearly(m["trades"], label)

    # Factor analysis
    print(f"\n{sep}")
    print("  FACTOR ANALYSIS — avg PnL improvement vs baseline")
    print(sep)
    for col, vals in [("dip_atr", S_DIP_ATR), ("min_profit", S_MIN_PROFIT),
                      ("max_adds", S_MAX_ADDS), ("add_frac", S_ADD_FRAC)]:
        line = f"  {col:<14}"
        for v in vals:
            avg = results[results[col] == v]["net_pnl"].mean()
            line += f"  {v!s:>4}: ${avg:>+10,.0f}"
        print(line)
    print(sep + "\n")


if __name__ == "__main__":
    main()
