#!/usr/bin/env python3
"""
OOS validation: current top-5 (3M return ranked) vs diversified (risk-adjusted) portfolio.

Runs the qc_portfolio daily backtest engine, splits equity curve 70/30 IS/OOS,
and computes CAGR / Sharpe / MaxDD for each window.

Writes result to:
    /Users/quarki3/swarm_results/completed/qc_diversified_oos.json
"""
import sys
import os
import json
import math

import numpy as np
import pandas as pd

# ── Path setup: patch config BEFORE importing backtest ───────────────────────
sys.path.insert(0, os.path.expanduser("~/qc_portfolio"))

import config

# Add proxy universes for strategies not yet in STRATEGY_CONFIGS
config.STRATEGY_CONFIGS[395] = {
    "name": "Kijun ATR Dip Buyer",
    # Dip-buyer on tech large-caps; single-winner momentum approximates
    "universe": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "QQQ"],
    "alloc": "single_winner",
}
config.STRATEGY_CONFIGS[50] = {
    "name": "Dynamic VIX-SPY Regime Strategy",
    # Regime-switch: risk-on (SPY/QQQ/IWM) vs risk-off (TLT/GLD/SHV)
    # top3_equal momentum naturally rotates toward whichever regime is winning
    "universe": ["SPY", "QQQ", "IWM", "TLT", "GLD", "SHV"],
    "alloc": "top3_equal",
}
config.STRATEGY_CONFIGS[313] = {
    "name": "Large-Cap Quality Factor Stock Basket",
    # Quality tilt: stable moats, low leverage, high FCF
    "universe": ["AAPL", "MSFT", "GOOGL", "JNJ", "PG", "JPM", "V", "MA", "UNH", "HD"],
    "alloc": "top3_equal",
}

from backtest import run_backtest  # noqa: E402 — must come after config patch

sys.path.insert(0, os.path.expanduser("~"))
from oos_validate import split_is_oos, report  # noqa: E402

# ── Portfolio definitions ─────────────────────────────────────────────────────
CURRENT_IDS    = [245, 395, 46, 345, 123]   # current top-5 by raw 3M return
DIVERSIFIED_IDS = [123, 395, 50, 252, 313]  # diversified risk-adjusted selection

LOOKBACK_YEARS = 5   # ~1 260 trading days; OOS = last 30% ≈ 18 months

# ── Run backtests ─────────────────────────────────────────────────────────────
print("=" * 62)
print("  QC Diversified Portfolio — IS/OOS Validation")
print("=" * 62)

print(f"\n[1/2] Running CURRENT portfolio ({CURRENT_IDS}) …")
res_cur = run_backtest(
    strategy_ids=CURRENT_IDS,
    lookback_years=LOOKBACK_YEARS,
    trail_stop_pct=config.TRAIL_STOP_PCT,
    dd_ladder=config.DD_LADDER,
    initial_capital=100_000,
    rebalance_days=1,
)

print(f"\n[2/2] Running DIVERSIFIED portfolio ({DIVERSIFIED_IDS}) …")
res_div = run_backtest(
    strategy_ids=DIVERSIFIED_IDS,
    lookback_years=LOOKBACK_YEARS,
    trail_stop_pct=config.TRAIL_STOP_PCT,
    dd_ladder=config.DD_LADDER,
    initial_capital=100_000,
    rebalance_days=1,
)

if res_cur is None or res_div is None:
    print("[ERROR] Backtest returned None — check yfinance connectivity")
    sys.exit(1)

# ── Extract equity curves → daily returns ─────────────────────────────────────
cur_ec = res_cur["equity_curve"]
div_ec = res_div["equity_curve"]

cur_ret = cur_ec.pct_change().dropna()
div_ret = div_ec.pct_change().dropna()

# Align on common trading dates (both portfolios must overlap)
common = cur_ret.index.intersection(div_ret.index)
if len(common) < 50:
    print(f"[ERROR] Only {len(common)} common dates — not enough data")
    sys.exit(1)

cur_ret = cur_ret.loc[common]
div_ret = div_ret.loc[common]

print(f"\nAligned series: {len(common)} trading days  "
      f"({common[0].date()} → {common[-1].date()})")

# ── IS / OOS split (70% IS, 30% OOS = ~18 months held-out) ───────────────────
cur_df = pd.DataFrame({"ret": cur_ret})
div_df = pd.DataFrame({"ret": div_ret})

cur_is_df,  cur_oos_df  = split_is_oos(cur_df,  oos_frac=0.30)
div_is_df,  div_oos_df  = split_is_oos(div_df,  oos_frac=0.30)

split_date = cur_oos_df.index[0].date()
print(f"IS  window : {cur_is_df.index[0].date()} → {cur_is_df.index[-1].date()}  "
      f"({len(cur_is_df)} days)")
print(f"OOS window : {split_date} → {cur_oos_df.index[-1].date()}  "
      f"({len(cur_oos_df)} days, held-out)")

# ── IS / OOS reports ──────────────────────────────────────────────────────────
print()
cur_rep = report("Current  (top-5 by 3M return)",        cur_is_df["ret"], cur_oos_df["ret"])
div_rep = report("Diversified (risk-adjusted selection)", div_is_df["ret"], div_oos_df["ret"])

# ── Full-period summary metrics (for the output JSON) ─────────────────────────
def _full_metrics(ret: pd.Series) -> dict:
    n = len(ret)
    years = n / 252
    total = float((1.0 + ret).prod())
    cagr = total ** (1.0 / years) - 1.0 if years > 0 and total > 0 else float("nan")
    mu, sigma = ret.mean(), ret.std(ddof=1)
    sharpe = float(mu / sigma * math.sqrt(252)) if sigma > 0 else 0.0
    equity = (1.0 + ret).cumprod()
    peak   = equity.cummax()
    maxdd  = float(((equity - peak) / peak).min())
    return {
        "cagr":   round(cagr * 100, 2),
        "sharpe": round(sharpe, 3),
        "maxdd":  round(abs(maxdd) * 100, 2),
    }

cur_full = _full_metrics(cur_ret)
div_full = _full_metrics(div_ret)

# ── Verdict logic ─────────────────────────────────────────────────────────────
cur_oos_dd   = abs(cur_rep["oos_max_dd"])
div_oos_dd   = abs(div_rep["oos_max_dd"])
dd_delta_oos = cur_oos_dd - div_oos_dd     # positive = diversified has smaller DD OOS

cur_deg = cur_rep["degradation"]  # OOS_Sharpe / IS_Sharpe
div_deg = div_rep["degradation"]

# OOS holds if:
#   1. DD improvement persists OOS (>= 5 percentage points — "material")
#   2. Diversified OOS Sharpe degradation is not catastrophic (>= 0.40)
#   3. Diversified OOS Sharpe is not negative when current is positive
cur_oos_sharpe = cur_rep["oos_sharpe"]
div_oos_sharpe = div_rep["oos_sharpe"]

dd_material      = dd_delta_oos >= 5.0
div_deg_ok       = div_deg is not None and div_deg >= 0.40
sharpe_not_worse = div_oos_sharpe >= (cur_oos_sharpe * 0.50) if cur_oos_sharpe > 0 else True

oos_holds = dd_material and div_deg_ok and sharpe_not_worse

if oos_holds:
    verdict = "adopt-diversified"
    summary = (
        f"Diversified portfolio cuts OOS MaxDD by {dd_delta_oos:.1f}pp "
        f"({cur_oos_dd:.1f}% → {div_oos_dd:.1f}%). "
        f"OOS Sharpe degradation ratio {div_deg:.2f} is acceptable (≥0.40). "
        f"Risk-adjusted return holds out-of-sample. "
        f"Full-period: current CAGR={cur_full['cagr']}% Sharpe={cur_full['sharpe']} MaxDD={cur_full['maxdd']}%; "
        f"diversified CAGR={div_full['cagr']}% Sharpe={div_full['sharpe']} MaxDD={div_full['maxdd']}%."
    )
else:
    verdict = "keep-current"
    reasons = []
    if not dd_material:
        reasons.append(
            f"OOS DD improvement only {dd_delta_oos:.1f}pp (need ≥5pp); "
            f"diversification gains don't survive OOS"
        )
    if not div_deg_ok:
        deg_str = f"{div_deg:.2f}" if div_deg is not None else "undefined"
        reasons.append(
            f"Diversified OOS Sharpe degradation {deg_str} is severe (< 0.40); "
            f"diversified book overfits IS"
        )
    if not sharpe_not_worse:
        reasons.append(
            f"Diversified OOS Sharpe {div_oos_sharpe:.3f} collapses vs "
            f"current {cur_oos_sharpe:.3f} OOS"
        )
    summary = "; ".join(reasons) if reasons else "Diversified does not improve risk-adjusted return OOS."

# ── Assemble result ───────────────────────────────────────────────────────────
result = {
    "task": "qc_diversified_oos",
    "window": {
        "total_days": int(len(common)),
        "is_days": int(len(cur_is_df)),
        "oos_days": int(len(cur_oos_df)),
        "oos_frac": 0.30,
        "is_start": str(cur_is_df.index[0].date()),
        "is_end": str(cur_is_df.index[-1].date()),
        "oos_start": str(split_date),
        "oos_end": str(cur_oos_df.index[-1].date()),
    },
    "current": {
        "cagr":        cur_full["cagr"],
        "sharpe":      cur_full["sharpe"],
        "maxdd":       cur_full["maxdd"],
        "is_cagr":     cur_rep["is_cagr"],
        "is_sharpe":   cur_rep["is_sharpe"],
        "is_maxdd":    abs(cur_rep["is_max_dd"]),
        "oos_cagr":    cur_rep["oos_cagr"],
        "oos_sharpe":  cur_rep["oos_sharpe"],
        "oos_maxdd":   cur_oos_dd,
        "degradation": cur_deg,
    },
    "diversified": {
        "cagr":        div_full["cagr"],
        "sharpe":      div_full["sharpe"],
        "maxdd":       div_full["maxdd"],
        "is_cagr":     div_rep["is_cagr"],
        "is_sharpe":   div_rep["is_sharpe"],
        "is_maxdd":    abs(div_rep["is_max_dd"]),
        "oos_cagr":    div_rep["oos_cagr"],
        "oos_sharpe":  div_rep["oos_sharpe"],
        "oos_maxdd":   div_oos_dd,
        "degradation": div_deg,
    },
    "dd_improvement_oos_pp": round(dd_delta_oos, 2),
    "oos_holds":  oos_holds,
    "verdict":    verdict,
    "summary":    summary,
    "success":    True,
}

# ── Write JSON ────────────────────────────────────────────────────────────────
out_path = os.path.expanduser(
    "~/swarm_results/completed/qc_diversified_oos.json"
)
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w") as f:
    json.dump(result, f, indent=2)

print("\n" + "=" * 62)
print(f"  VERDICT: {verdict}")
print(f"  OOS holds: {oos_holds}")
print(f"  DD improvement OOS: {dd_delta_oos:.1f}pp  "
      f"({cur_oos_dd:.1f}% → {div_oos_dd:.1f}%)")
cur_dep_str = f"{cur_deg:.3f}" if cur_deg is not None else "N/A"
div_dep_str = f"{div_deg:.3f}" if div_deg is not None else "N/A"
print(f"  Current  IS/OOS degradation: {cur_dep_str}")
print(f"  Diversif IS/OOS degradation: {div_dep_str}")
print(f"\n  {summary}")
print("=" * 62)
print(f"\nWritten → {out_path}")
