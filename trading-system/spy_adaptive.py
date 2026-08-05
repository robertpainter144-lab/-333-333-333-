#!/usr/bin/env python3
# DISABLED: failed OOS validation 2026-06-14
# OOS task efcabbe9: true IS Sharpe 0.656 (not 0.90), degradation ratio 1.977 (overfit flag).
# OOS Sharpe 1.297 is entirely 2025 bull-regime tailwind, not robust edge.
# DO NOT deploy live. Weight = 0. Revisit only after neutral/bear-market OOS clears Sharpe > 1.0.
# See: /Users/quarki3/swarm_results/completed/spy_adaptive_oos.json
"""
SPY Open Range — Adaptive Regime Strategy v2
Base: EMA25, trail=3.0×ATR, ADX(20)<35 direction-aware

Adaptive layers:
  1. Direction-aware ADX  : block longs only in BEARISH trend (NDI>PDI, ADX>35)
                            allow longs freely in bull trend (PDI>NDI) regardless of ADX
  2. Gap filter           : skip entry on gap-up/down days above threshold
  3. EOD vol-close        : force-close longs at 15:50 when ATR > N×median ATR
                            → targets overnight gap risk on extreme vol days (Apr 2025)
  4. EOD loss-close       : force-close longs at 15:50 if unrealized < -1×ATR×shares
  5. Short regime         : bear_trend only, rally to EMA → short with tighter trail
                            always force-close shorts at EOD

Sweep:
  gap_pct      : [0=off, 0.5, 1.0]       max abs open gap % before skipping entry
  eod_vol_mult : [0=off, 1.3, 1.5, 2.0]  close longs at EOD if ATR > mult×20bar-median
  short_trail  : [2.0, 2.5, 3.0]         ATR mult for short trailing stop
  eod_loss     : [False, True]            force-close longs at EOD if down > 1×ATR

Previous best: gap=off, vol=off, strl=2.5×, eod=N → Sharpe +0.88, +$133,060, DD -11.4%
"""

import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE      = Path("spy_5min_bars.csv")
OUTPUT_CSV = "spy_adaptive_results.csv"

EMA_PERIOD    = 25
ATR_PERIOD    = 14
ADX_PERIOD    = 20
LONG_TRAIL    = 3.0
ADX_THRESHOLD = 35
ENTRY_START   = 9 * 60 + 30
ENTRY_END     = 11 * 60
EOD_MINS      = 15 * 60 + 50
ATR_MEDIAN_W  = 20
INITIAL_EQ    = 100_000.0
LEVERAGE      = 2.0
COMM          = 0.005

BASELINE = dict(sharpe=0.88, pf=1.27, net_pnl=133060, max_dd_pct=-11.4, n=1016)

GAP_PCTS      = [0.0, 0.5, 1.0]
EOD_VOL_MULTS = [0.0, 1.3, 1.5, 2.0]
SHORT_TRAILS  = [2.0, 2.5, 3.0]
EOD_LOSSES    = [False, True]


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()

    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"]     = tr.ewm(span=ATR_PERIOD, adjust=False).mean()
    df["atr_med"] = df["atr"].rolling(ATR_MEDIAN_W).median()

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

    # Gap: (today open - yesterday close) / yesterday close, forward-filled to all bars
    df["_date"]       = df.index.date
    daily_close       = df.groupby("_date")["close"].last()
    daily_close_s     = pd.Series(daily_close.values,
                                  index=pd.to_datetime(list(daily_close.index)))
    prev_close_daily  = daily_close_s.shift(1)
    bar_dates         = pd.to_datetime(df.index.date)
    df["prev_close"]  = bar_dates.map(prev_close_daily.to_dict()).values
    first_bar         = ~pd.Series(df.index.date, index=df.index).duplicated()
    df["gap_pct"]     = np.where(
        first_bar & df["prev_close"].notna(),
        (df["open"] - df["prev_close"]) / df["prev_close"] * 100,
        np.nan
    )
    df["gap_pct"] = df["gap_pct"].ffill()
    df.drop(columns=["_date"], inplace=True)
    return df


# ── Regime classifier ─────────────────────────────────────────────────────────

def classify_regime(adx, pdi, ndi):
    """Direction-aware: high ADX only blocks longs when bearish."""
    if adx <= ADX_THRESHOLD:
        return "ranging"
    return "bull_trend" if pdi >= ndi else "bear_trend"


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame, gap_pct: float, eod_vol_mult: float,
                 short_trail: float, eod_loss: bool) -> dict:
    warmup   = 100
    trades   = []
    position = None
    equity   = INITIAL_EQ

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        close = row["close"]
        ema   = row["ema"]
        atr   = row["atr"]
        atr_m = row["atr_med"]
        adx   = row["adx"]
        pdi   = row["pdi"]
        ndi   = row["ndi"]
        gap   = row["gap_pct"] if not np.isnan(row["gap_pct"]) else 0.0
        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute

        regime = classify_regime(adx, pdi, ndi)
        gap_ok = (gap_pct == 0.0) or (abs(gap) <= gap_pct)

        # ── Manage open position ─────────────────────────────────────────────
        if position is not None:
            side = position["side"]
            exit_now = False

            if mins >= EOD_MINS:
                if side == "short":
                    exit_now = True
                elif eod_vol_mult > 0.0 and not np.isnan(atr_m) and atr_m > 0:
                    # Close longs on extreme-vol days to avoid overnight gap risk
                    exit_now = atr > eod_vol_mult * atr_m
                if not exit_now and eod_loss and side == "long":
                    unrealized = (close - position["entry_price"]) * position["shares"]
                    exit_now = unrealized < -1.0 * atr * position["shares"]

            if side == "long":
                new_stop = close - LONG_TRAIL * atr
                if new_stop > position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close <= position["trail_stop"]
            else:
                new_stop = close + short_trail * atr
                if new_stop < position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close >= position["trail_stop"]

            if stop_hit or exit_now:
                shares = position["shares"]
                gross  = (close - position["entry_price"]) * shares if side == "long" \
                     else (position["entry_price"] - close) * shares
                pnl    = gross - shares * COMM * 2
                equity += pnl
                trades.append({
                    "side":      side,
                    "pnl":       pnl,
                    "bars_held": i - position["entry_idx"],
                    "exit":      "eod" if exit_now else "trail_stop",
                    "regime":    position["regime"],
                    "month":     ts.to_period("M"),
                    "year":      ts.year,
                })
                position = None

        # ── Entry ────────────────────────────────────────────────────────────
        if position is None and ENTRY_START <= mins < ENTRY_END:
            if not gap_ok:
                continue

            shares = int((equity * LEVERAGE) / close)
            if shares < 1:
                continue

            if regime in ("ranging", "bull_trend") and close > ema:
                position = {
                    "side":        "long",
                    "entry_price": close,
                    "shares":      shares,
                    "trail_stop":  close - LONG_TRAIL * atr,
                    "entry_idx":   i,
                    "regime":      regime,
                }
            elif regime == "bear_trend" and close >= ema:
                position = {
                    "side":        "short",
                    "entry_price": close,
                    "shares":      shares,
                    "trail_stop":  close + short_trail * atr,
                    "entry_idx":   i,
                    "regime":      regime,
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
            "exit": "end_of_data", "regime": position["regime"],
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


def yearly_breakdown(trades, label=""):
    t = pd.DataFrame(trades)
    if label:
        print(f"\n  {label}")
    print(f"  {'Year':<6}  {'n':>4}  {'L/S':>9}  {'Win%':>5}  {'PF':>5}"
          f"  {'PnL':>12}  {'MaxDD':>7}  {'AvgHold':>8}")
    print("  " + "─" * 72)
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
        avg_h  = grp["bars_held"].mean() * 5 / 60
        print(
            f"  {yr:<6}  {gn:>4}  {longs:>4}L/{shorts:<4}S  "
            f"{gw/gn*100:>4.0f}%  {pf_s:>5}  "
            f"${g.sum():>+10,.0f}  {g_dd_pct:>+6.1f}%  {avg_h:>6.1f}hr"
        )


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not CACHE.exists():
        import sys
        sys.exit(f"Cache not found: {CACHE}")

    print(f"Loading bars from {CACHE}")
    raw = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    print(f"  {len(raw):,} bars  {raw.index[0].date()} → {raw.index[-1].date()}")
    print("  Adding indicators ...")
    df = add_indicators(raw)
    print(f"  Done.\n")

    total = len(GAP_PCTS) * len(EOD_VOL_MULTS) * len(SHORT_TRAILS) * len(EOD_LOSSES)
    print(f"Previous best: gap=off, vol=off, strl=2.5×, eod=N  →  "
          f"Sharpe {BASELINE['sharpe']:+.2f}  PF {BASELINE['pf']:.2f}  "
          f"PnL +${BASELINE['net_pnl']:,}  DD {BASELINE['max_dd_pct']}%\n")
    print(f"Adaptive regime v2 — {total} combinations")
    print(f"  Direction-aware ADX: longs blocked only in bear_trend (NDI>PDI, ADX>{ADX_THRESHOLD})")
    print(f"  Long trail fixed : {LONG_TRAIL}×ATR")
    print(f"  gap_pct       : {GAP_PCTS}  (% max open gap, 0=off)")
    print(f"  eod_vol_mult  : {EOD_VOL_MULTS}  (close longs at EOD if ATR > mult×20bar-median, 0=off)")
    print(f"  short_trail   : {SHORT_TRAILS}  (ATR mult for shorts)")
    print(f"  eod_loss      : {EOD_LOSSES}  (close longs at EOD if unrealized < -1×ATR)\n")

    rows = []
    for idx, (gap, evm, st, eod) in enumerate(
        product(GAP_PCTS, EOD_VOL_MULTS, SHORT_TRAILS, EOD_LOSSES), 1
    ):
        m = run_backtest(df, gap, evm, st, eod)
        rows.append({
            "gap_pct": gap, "eod_vol_mult": evm,
            "short_trail": st, "eod_loss": eod,
            **{k: v for k, v in m.items() if k != "trades"},
        })
        sh_s  = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        pf_s  = f"{m['pf']:.2f}"      if not np.isnan(m["pf"])     else " n/a"
        beats = not np.isnan(m["sharpe"]) and m["sharpe"] > BASELINE["sharpe"] and m["n"] >= 50
        flag  = " ◄ BETTER" if beats else ""
        print(
            f"  [{idx:>3}/{total}]  gap≤{gap:.1f}%  eodvol≤{evm:.1f}×  "
            f"strl={st}×  eod={'Y' if eod else 'N'}"
            f"  │  n={m['n']:>4}  wr={m['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%{flag}"
        )

    results = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    results.to_csv(OUTPUT_CSV, index=False)

    print(f"\n{'='*110}")
    print("  TOP 10 BY SHARPE  (n ≥ 50)")
    print(f"{'='*110}")
    top = results[results["n"] >= 50].head(10)
    for _, r in top.iterrows():
        sh_s  = f"{r['sharpe']:+.2f}" if not np.isnan(r["sharpe"]) else "  n/a"
        pf_s  = f"{r['pf']:.2f}"      if not np.isnan(r["pf"])     else " n/a"
        beats = not np.isnan(r["sharpe"]) and r["sharpe"] > BASELINE["sharpe"]
        flag  = " ◄ BEATS BASELINE" if beats else ""
        print(
            f"  gap≤{r['gap_pct']:.1f}%  eodvol≤{r['eod_vol_mult']:.1f}×  "
            f"strl={r['short_trail']}×  eod={'Y' if r['eod_loss'] else 'N'}"
            f"  │  n={int(r['n']):>4}  wr={r['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${r['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%{flag}"
        )
    print(f"{'='*110}")

    print(f"\n{'='*110}")
    print("  PER-YEAR — TOP 3 CONFIGS")
    print(f"{'='*110}")
    for _, r in top.head(3).iterrows():
        m = run_backtest(df, r["gap_pct"], r["eod_vol_mult"], r["short_trail"], r["eod_loss"])
        label = (
            f"gap≤{r['gap_pct']:.1f}%  eodvol≤{r['eod_vol_mult']:.1f}×  "
            f"strl={r['short_trail']}×  eod={'Y' if r['eod_loss'] else 'N'}"
            f"  │  n={int(r['n'])}  sharpe={r['sharpe']:+.2f}  "
            f"pnl=${r['net_pnl']:+,.0f}  dd={r['max_dd_pct']:+.1f}%"
        )
        yearly_breakdown(m["trades"], label)

    print(f"\nFull results → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
