"""
Defensive-universe strategy comparison.

Same signals + simulate engine as strategy_compare.py, but runs on diversified
broad-index ETF baskets instead of the existing high-beta leveraged-ETF strategies.
Adds tighter risk configs in pursuit of the persona's 15% max-DD ceiling.

Universes tested (each treated as one "strategy"):
  101 broad_etfs       : SPY, QQQ, IWM, TLT, GLD               (top3_equal)
  102 growth_defensive : SPY, QQQ, XLP, XLU, GLD, TLT          (top3_equal)
  103 all_weather      : SPY, QQQ, IWM, TLT, GLD, EFA, AGG     (top3_equal)
  104 spy_qqq_picker   : SPY, QQQ                              (single_winner)
  105 broad_5_equal    : SPY, QQQ, IWM, TLT, GLD               (top5_equal)

Usage:
    python3.10 strategy_compare_defensive.py
"""
import sys
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

import strategy_compare as SC
import config as CFG

# Inject five defensive "strategies" into STRATEGY_CONFIGS so simulate() can
# look them up. IDs 101-105 don't collide with real QC IDs.
DEFENSIVE_CONFIGS = {
    101: {"name": "broad_etfs",       "universe": ["SPY","QQQ","IWM","TLT","GLD"],            "alloc": "top3_equal"},
    102: {"name": "growth_defensive", "universe": ["SPY","QQQ","XLP","XLU","GLD","TLT"],      "alloc": "top3_equal"},
    103: {"name": "all_weather",      "universe": ["SPY","QQQ","IWM","TLT","GLD","EFA","AGG"], "alloc": "top3_equal"},
    104: {"name": "spy_qqq_picker",   "universe": ["SPY","QQQ"],                              "alloc": "single_winner"},
    105: {"name": "broad_5_equal",    "universe": ["SPY","QQQ","IWM","TLT","GLD"],            "alloc": "top5_equal"},
}
CFG.STRATEGY_CONFIGS.update(DEFENSIVE_CONFIGS)
SC.STRATEGY_CONFIGS.update(DEFENSIVE_CONFIGS)

STRATEGY_IDS = list(DEFENSIVE_CONFIGS.keys())

# Tighter risk configs in pursuit of persona's DD ≤ 15%
TIGHT_LADDER     = [(0.05, 0.50), (0.08, 0.25), (0.12, 0.00)]
ULTRA_LADDER     = [(0.04, 0.50), (0.07, 0.00)]

RISK_CONFIGS = {
    "no_risk":     dict(trail_stop=None,  cap=1.0,  ladder=None,         reb=1),
    "optimized":   dict(trail_stop=0.12,  cap=0.40, ladder=SC.LADDER,    reb=3),
    "tight":       dict(trail_stop=0.08,  cap=0.30, ladder=SC.LADDER,    reb=2),
    "very_tight":  dict(trail_stop=0.05,  cap=0.25, ladder=TIGHT_LADDER, reb=2),
    "ultra_tight": dict(trail_stop=0.04,  cap=0.20, ladder=ULTRA_LADDER, reb=1),
}


def _all_symbols():
    syms = {"SPY"}
    for sid in STRATEGY_IDS:
        syms.update(DEFENSIVE_CONFIGS[sid]["universe"])
    return sorted(syms)


def main():
    syms = _all_symbols()
    end   = datetime.now()
    start = end - timedelta(days=14*365 + 120)

    print(f"Downloading {len(syms)} symbols ({', '.join(syms)})...")
    raw = yf.download(syms, start=start.strftime("%Y-%m-%d"),
                      end=end.strftime("%Y-%m-%d"),
                      progress=False, auto_adjust=True, threads=True)
    if raw.empty:
        print("Download failed"); return

    close = raw["Close"].copy() if "Close" in raw else raw.copy()
    if isinstance(close, pd.Series):
        close = close.to_frame(syms[0])
    close.columns = [str(c) for c in close.columns]
    close = close.dropna(how="all").ffill()

    low_df = None
    if "Low" in raw:
        low_df = raw["Low"].copy()
        low_df.columns = [str(c) for c in low_df.columns]
        low_df = low_df.reindex(close.index).ffill()

    spy_full = close["SPY"].iloc[120:] if "SPY" in close.columns else pd.Series()

    CAP   = 100_000
    total = len(SC.SIGNALS) * len(RISK_CONFIGS)
    done  = 0
    results = []

    print(f"\nRunning {total} combinations on defensive universes...\n")

    for sig_name, score_fn in SC.SIGNALS.items():
        for risk_name, risk in RISK_CONFIGS.items():
            done += 1
            try:
                ec, trades_list = SC.simulate(close, low_df, STRATEGY_IDS,
                                              score_fn, risk,
                                              initial_capital=CAP)
                m = SC.metrics(ec, trades_list, CAP)
                if not m: continue
                results.append({
                    "signal": sig_name.strip(),
                    "risk":   risk_name,
                    **m,
                })
                print(f"  [{done:>3}/{total}] {sig_name.split('(')[0].strip():<22} "
                      f"+ {risk_name:<11}  "
                      f"CAGR {m['cagr']:>6.1f}%  Sharpe {m['sharpe']:>5.2f}  "
                      f"DD {m['max_dd']:>5.1f}%  WR {m['win_rate']:>4.1f}%")
            except Exception as e:
                print(f"  [{done:>3}/{total}] ERROR {sig_name} + {risk_name}: {e}")

    if not results:
        print("No results"); return

    results.sort(key=lambda r: (r["sharpe"], r["cagr"]), reverse=True)

    print("\n" + "="*94)
    print("  DEFENSIVE UNIVERSES (~14y)  —  sorted by Sharpe")
    print("="*94)
    print(f"  {'#':>3}  {'Signal':<22}  {'Risk':<12}  {'CAGR':>6}  {'Sharpe':>6}  "
          f"{'MaxDD':>6}  {'WinRt':>6}  {'Final $':>12}")
    print("-"*94)
    for i, r in enumerate(results[:25], 1):
        print(f"  {i:>3}  {r['signal']:<22}  {r['risk']:<12}  "
              f"{r['cagr']:>5.1f}%  {r['sharpe']:>6.2f}  {r['max_dd']:>5.1f}%  "
              f"{r['win_rate']:>5.1f}%  ${r['final']:>11,.0f}")

    print("="*94)

    # Persona-clearance check
    cleared = [r for r in results
               if r["sharpe"] > 1.0 and r["max_dd"] < 15.0
               and r["win_rate"] > 50 and r["cagr"] >= 15 and r["cagr"] <= 40]
    print("\n  PERSONA-CLEARANCE FILTER")
    print("  Sharpe > 1.0, MaxDD < 15%, WinRate > 50%, CAGR 15-40%")
    print("-"*94)
    if cleared:
        for r in cleared[:15]:
            print(f"  ✓ {r['signal']:<22}  {r['risk']:<12}  "
                  f"CAGR {r['cagr']:>5.1f}%  Sharpe {r['sharpe']:>5.2f}  "
                  f"DD {r['max_dd']:>5.1f}%  WR {r['win_rate']:>5.1f}%")
    else:
        print("  ✗ NOTHING clears all four bars.")
        # Show closest call
        partial = [r for r in results if r["sharpe"] > 1.0 and r["max_dd"] < 25.0]
        if partial:
            print("\n  CLOSEST CALLS (Sharpe > 1, DD < 25%):")
            for r in partial[:8]:
                print(f"    {r['signal']:<22}  {r['risk']:<12}  "
                      f"CAGR {r['cagr']:>5.1f}%  Sharpe {r['sharpe']:>5.2f}  "
                      f"DD {r['max_dd']:>5.1f}%  WR {r['win_rate']:>5.1f}%")

    if not spy_full.empty:
        spy_rets  = spy_full.pct_change().dropna()
        spy_sharpe = float(spy_rets.mean() / spy_rets.std() * np.sqrt(252)) if spy_rets.std() > 0 else 0
        spy_rm     = spy_full.cummax()
        spy_dd     = float(((spy_full - spy_rm) / spy_rm).min() * 100)
        spy_years  = (spy_full.index[-1] - spy_full.index[0]).days / 365.25
        spy_cagr   = ((spy_full.iloc[-1]/spy_full.iloc[0])**(1/spy_years)-1)*100
        print(f"\n  SPY buy-and-hold reference:  CAGR {spy_cagr:.1f}%  "
              f"Sharpe {spy_sharpe:.2f}  MaxDD {abs(spy_dd):.1f}%")


if __name__ == "__main__":
    main()
