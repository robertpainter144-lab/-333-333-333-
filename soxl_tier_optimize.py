#!/usr/bin/env python3
"""
SOXL Profit-Tier & Pyramid Optimizer
Tests 4 tier configs × 4 pyramid add counts = 16 combinations.
Picks winner by Sharpe with drawdown < 45%.
"""

import warnings
from pathlib import Path
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE = Path("/Users/quarki3/soxl_5min_bars.csv")

EMA_PERIOD    = 25
ATR_PERIOD    = 14
ADX_PERIOD    = 20
ADX_THRESHOLD = 35
STRONG_ADX    = 45

TRAIL_INIT    = 3.0
TRAIL_POST1   = 2.0
TRAIL_POST2   = 1.5
TRAIL_EOD1    = 2.0
TRAIL_EOD2    = 1.0

DIP_ATR_BACK  = 0.5
MIN_PROFIT    = 0.3
ADD_FRAC      = 1.0
COMM          = 0.005
LEVERAGE      = 1.0

ENTRY_START       = 9 * 60 + 30
ENTRY_END_BASE    = 11 * 60
ENTRY_END_EXT     = 14 * 60
PYRAMID_END       = 14 * 60 + 30
EOD_TIGHTEN       = 15 * 60
EOD_VERY_TIGHT    = 15 * 60 + 30
EOD_CLOSE_SHORTS  = 15 * 60 + 50
MAX_REENTRIES     = 2


def load_data():
    df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    df = df.between_time("09:30", "16:00")
    return df


def add_indicators(df):
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


def run_backtest(df, profit_tiers, max_adds, init_eq=100_000.0):
    """
    profit_tiers: list of (atr_level, fraction) sorted ascending.
      e.g. [(2.0, 1/3), (4.0, 1/3)] → sell 1/3 at +2ATR, 1/3 at +4ATR, trail 1/3.
    """
    warmup    = 100
    trades    = []
    position  = None
    equity    = init_eq
    day_reentries = {}
    n_tiers = len(profit_tiers)

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
        date  = str(ts.date())

        regime    = classify_regime(adx, pdi, ndi)
        entry_end = ENTRY_END_EXT if adx > STRONG_ADX else ENTRY_END_BASE

        if position is not None:
            side = position["side"]
            partials = position["partials"]

            # EOD + partial-based trail width
            if mins >= EOD_VERY_TIGHT:
                trail_w = TRAIL_EOD2
            elif mins >= EOD_TIGHTEN:
                trail_w = TRAIL_EOD1
            elif partials >= 2:
                trail_w = TRAIL_POST2
            elif partials >= 1:
                trail_w = TRAIL_POST1
            else:
                trail_w = TRAIL_INIT

            if side == "long" and close > position["pos_high"]:
                position["pos_high"] = close

            # Tiered partial profit taking (generalized)
            if side == "long" and partials < n_tiers and position["total_shares"] > 0:
                tier_atr, tier_frac = profit_tiers[partials]
                profit_atr = (close - position["avg_entry"]) / atr if atr > 0 else 0
                if profit_atr >= tier_atr:
                    sell_shares = position["total_shares"] * tier_frac
                    pnl_partial = (close - position["avg_entry"]) * sell_shares - sell_shares * COMM * 2
                    equity += pnl_partial
                    position["total_shares"] -= sell_shares
                    position["partials"]     += 1

            # Pyramid: add on dip within winning long
            if (side == "long"
                    and position["n_adds"] < max_adds
                    and mins < PYRAMID_END):
                dip_from_high    = position["pos_high"] - close
                profit_per_share = close - position["avg_entry"]
                trend_ok         = close > ema
                if (dip_from_high >= DIP_ATR_BACK * atr
                        and profit_per_share >= MIN_PROFIT * atr
                        and trend_ok):
                    add_shares = position["orig_shares"] * ADD_FRAC
                    old_cost   = position["avg_entry"] * position["total_shares"]
                    new_cost   = close * add_shares
                    position["total_shares"] += add_shares
                    position["avg_entry"]     = (old_cost + new_cost) / position["total_shares"]
                    position["n_adds"]        += 1

            # Trailing stop
            if side == "long":
                new_stop = close - trail_w * atr
                if new_stop > position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close <= position["trail_stop"]
            else:
                new_stop = close + trail_w * atr
                if new_stop < position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close >= position["trail_stop"]

            eod_close = (mins >= EOD_CLOSE_SHORTS and side == "short") or mins >= 15 * 60 + 55

            if stop_hit or eod_close:
                shares = position["total_shares"]
                gross  = (close - position["avg_entry"]) * shares if side == "long" \
                     else (position["avg_entry"] - close) * shares
                pnl    = gross - shares * COMM * 2
                equity += pnl
                trades.append({
                    "date": date, "side": side, "pnl": pnl,
                    "bars_held": i - position["entry_idx"],
                    "exit": "eod" if eod_close else "trail",
                    "regime": position["regime"],
                    "n_adds": position["n_adds"],
                    "partials": position["partials"],
                    "year": ts.year, "month": ts.to_period("M"),
                })
                position = None

        # Entry
        reentries_today = day_reentries.get(date, 0)
        can_enter = (position is None
                     and ENTRY_START <= mins < entry_end
                     and reentries_today <= MAX_REENTRIES)

        if can_enter:
            shares = (equity * LEVERAGE) / close
            if shares * close < 1.0:
                continue

            if regime in ("ranging", "bull_trend") and close > ema:
                position = {
                    "side":         "long",
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close - TRAIL_INIT * atr,
                    "entry_idx":    i,
                    "regime":       regime,
                    "pos_high":     close,
                    "n_adds":       0,
                    "partials":     0,
                    "entry_time":   ts.strftime("%H:%M"),
                }
            elif regime == "bear_trend" and close < ema:
                position = {
                    "side":         "short",
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close + TRAIL_INIT * atr,
                    "entry_idx":    i,
                    "regime":       regime,
                    "pos_high":     close,
                    "n_adds":       0,
                    "partials":     0,
                    "entry_time":   ts.strftime("%H:%M"),
                }

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
            "date": str(ts.date()), "side": side, "pnl": pnl,
            "bars_held": len(df) - 1 - position["entry_idx"],
            "exit": "end_of_data", "regime": position["regime"],
            "n_adds": position["n_adds"], "partials": position["partials"],
            "year": ts.year, "month": ts.to_period("M"),
        })

    if not trades:
        return dict(n=0, sharpe=np.nan, net_pnl=0, max_dd_pct=0,
                    win_rate=0, pf=np.nan, final_eq=init_eq)

    pnl_s      = pd.Series([t["pnl"] for t in trades])
    eq_s       = init_eq + pnl_s.cumsum()
    hwm        = eq_s.cummax()
    dd         = eq_s - hwm
    max_dd_pct = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
    n          = len(pnl_s)
    wins       = (pnl_s > 0).sum()
    losses     = (pnl_s <= 0).sum()
    std        = pnl_s.std(ddof=1) if n > 1 else np.nan
    n_days_    = max((df.index[-1] - df.index[0]).days, 1)
    tpy        = n / (n_days_ / 252)
    sharpe     = (pnl_s.mean() / std) * np.sqrt(tpy) if std and std > 0 else np.nan
    pf         = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
                 if losses and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    return dict(
        n=n,
        win_rate=round(wins / n * 100, 1),
        pf=round(float(pf), 3) if not np.isnan(pf) else None,
        net_pnl=round(float(pnl_s.sum()), 2),
        final_eq=round(float(eq_s.iloc[-1]), 2),
        sharpe=round(float(sharpe), 3) if not np.isnan(sharpe) else None,
        max_dd_pct=round(float(max_dd_pct), 2),
        trades=trades,
    )


TIER_CONFIGS = {
    "A": {
        "label": "33%@+1.5ATR / 33%@+3ATR / trail",
        "tiers": [(1.5, 1/3), (3.0, 1/3)],
    },
    "B": {
        "label": "33%@+2ATR / 33%@+4ATR / trail (current)",
        "tiers": [(2.0, 1/3), (4.0, 1/3)],
    },
    "C": {
        "label": "25%@+1ATR / 25%@+2ATR / 25%@+3ATR / trail",
        "tiers": [(1.0, 1/4), (2.0, 1/4), (3.0, 1/4)],
    },
    "D": {
        "label": "50%@+2ATR / trail rest",
        "tiers": [(2.0, 0.5)],
    },
}

PYRAMID_COUNTS = [2, 3, 4, 5]


def main():
    print("Loading data ...")
    df = load_data()
    df = add_indicators(df)
    print(f"  {len(df):,} bars  ({df.index[0].date()} → {df.index[-1].date()})")
    print()

    results = []
    best = None

    header = f"{'Config':<6} {'Adds':>4}  {'n':>4}  {'Sharpe':>7}  {'DD%':>7}  {'WR%':>5}  {'PF':>5}  {'Net PnL':>12}"
    print(header)
    print("─" * len(header))

    for tier_key, tier_cfg in TIER_CONFIGS.items():
        for max_adds in PYRAMID_COUNTS:
            r = run_backtest(df, tier_cfg["tiers"], max_adds)
            sh  = r["sharpe"]
            dd  = r["max_dd_pct"]
            pf  = r["pf"]
            sh_s = f"{sh:+.3f}" if sh is not None else "   n/a"
            pf_s = f"{pf:.3f}"  if pf is not None else "  n/a"
            print(f"  {tier_key:<4} adds={max_adds}  n={r['n']:>4}  "
                  f"sharpe={sh_s}  dd={dd:>+6.1f}%  wr={r['win_rate']:>4.1f}%  "
                  f"pf={pf_s}  pnl=${r['net_pnl']:>+12,.2f}")

            row = dict(
                tier=tier_key,
                tier_label=tier_cfg["label"],
                tiers=tier_cfg["tiers"],
                max_adds=max_adds,
                n=r["n"],
                sharpe=sh,
                max_dd_pct=dd,
                win_rate=r["win_rate"],
                pf=pf,
                net_pnl=r["net_pnl"],
                final_eq=r["final_eq"],
            )
            results.append(row)

            # Candidate: best Sharpe with drawdown < 45%
            if sh is not None and dd > -45:
                if best is None or sh > best["sharpe"]:
                    best = row

    print()
    if best is None:
        print("No configuration passed the DD < 45% filter!")
        # Fall back to best Sharpe overall
        valid = [r for r in results if r["sharpe"] is not None]
        best = max(valid, key=lambda x: x["sharpe"])
        print(f"Fallback best: tier={best['tier']} adds={best['max_adds']}")

    print(f"\n{'='*70}")
    print(f"  WINNER: Config {best['tier']}  adds={best['max_adds']}")
    print(f"  {best['tier_label']}")
    print(f"  Sharpe={best['sharpe']:+.3f}  DD={best['max_dd_pct']:+.1f}%  WR={best['win_rate']}%  PF={best['pf']}  PnL=${best['net_pnl']:+,.2f}")
    print(f"{'='*70}")

    return results, best


if __name__ == "__main__":
    import json

    results, best = main()

    # Prepare JSON-safe results list
    clean_results = []
    for r in results:
        clean_results.append({
            "tier": r["tier"],
            "tier_label": r["tier_label"],
            "tiers": [[t[0], round(t[1], 6)] for t in r["tiers"]],
            "max_adds": r["max_adds"],
            "n": r["n"],
            "sharpe": r["sharpe"],
            "max_dd_pct": r["max_dd_pct"],
            "win_rate": r["win_rate"],
            "pf": r["pf"],
            "net_pnl": r["net_pnl"],
            "final_eq": r["final_eq"],
        })

    output = {
        "task": "soxl_optimize",
        "best_config": {
            "tier": best["tier"],
            "tier_label": best["tier_label"],
            "tiers": [[t[0], round(t[1], 6)] for t in best["tiers"]],
            "max_adds": best["max_adds"],
            "sharpe": best["sharpe"],
            "max_dd_pct": best["max_dd_pct"],
            "win_rate": best["win_rate"],
            "pf": best["pf"],
            "net_pnl": best["net_pnl"],
        },
        "results": clean_results,
        "summary": (
            f"Best config: Tier {best['tier']} ({best['tier_label']}) "
            f"with {best['max_adds']} pyramid adds. "
            f"Sharpe={best['sharpe']:+.3f}, DD={best['max_dd_pct']:+.1f}%, "
            f"WR={best['win_rate']}%, PnL=${best['net_pnl']:+,.2f}"
        ),
        "success": True,
    }

    out_path = "/Users/quarki3/swarm_results/completed/soxl_optimize.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults written to {out_path}")
    print(f"Best tier constants: {best['tiers']}")
    print(f"Best max_adds: {best['max_adds']}")
