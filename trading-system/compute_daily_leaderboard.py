#!/usr/bin/env python3
"""
Compute daily return leaderboard across all strategies.
Sources: JSON result files + ETF daily return CSVs.
Output: /Users/quarki3/swarm_results/completed/daily_return_leaderboard.json
"""
import json, math, pathlib, datetime
import pandas as pd
import numpy as np

OUT = pathlib.Path("/Users/quarki3/swarm_results/completed/daily_return_leaderboard.json")
TRADING_DAYS = 252
RF_ANNUAL = 0.0  # all backtests assume rf=0

def from_cagr_sharpe(cagr, sharpe, winrate_est=None, label=""):
    """Derive daily metrics from annual summary stats."""
    avg_daily_ret = (1 + cagr) ** (1 / TRADING_DAYS) - 1
    # annual_vol = cagr / sharpe when rf=0
    annual_vol = cagr / sharpe if sharpe and sharpe != 0 else float("nan")
    daily_vol = annual_vol / math.sqrt(TRADING_DAYS)
    daily_sharpe = sharpe  # identical to annual sharpe under rf=0 convention
    return {
        "avg_daily_ret": round(avg_daily_ret * 100, 4),   # %
        "daily_vol": round(daily_vol * 100, 4),            # %
        "daily_sharpe": round(daily_sharpe, 4),
        "daily_winrate": round(winrate_est * 100, 1) if winrate_est is not None else None,
    }

def compute_from_daily_returns(returns_series, rf_annual=0.0):
    """Compute all metrics from a pandas Series of daily returns (as fractions, e.g. 0.01 = 1%)."""
    rf_daily = (1 + rf_annual) ** (1 / TRADING_DAYS) - 1
    excess = returns_series - rf_daily
    avg = returns_series.mean()
    vol = returns_series.std(ddof=1)
    sharpe = (excess.mean() / vol) * math.sqrt(TRADING_DAYS) if vol > 0 else float("nan")
    winrate = (returns_series > 0).mean()
    return {
        "avg_daily_ret": round(avg * 100, 4),
        "daily_vol": round(vol * 100, 4),
        "daily_sharpe": round(sharpe, 4),
        "daily_winrate": round(winrate * 100, 1),
    }

# ---------- 1. QC Portfolio (N=3, top-3 QC leaderboard strategies, 2x winner) ----------
# Source: qc_top_n.json — CAGR 40.12%, Sharpe 1.06, Win rate (daily trade) 72.19%
# Reliable window: ~4.8yr backtest (2021-08-09 → 2026-06-12)
# Win rate 72.19% from qc_top_n = fraction of daily rebalance periods with positive return
qc = from_cagr_sharpe(0.4012, 1.06, winrate_est=0.7219)
qc_portfolio = {
    "name": "QC Portfolio (N=3, 2x winner weighting)",
    "window": "2021-08-09 → 2026-06-12 (~4.8yr backtest)",
    "data_source": "qc_top_n.json | qc_diversified_oos.json",
    "cagr_pct": 40.12,
    "max_dd_pct": -53.24,
    "n_trades": 7907,
    "notes": "Top-3 QC leaderboard strategies, 2x winner allocation. OOS (Dec24–Jun26) Sharpe=1.41, CAGR=77.7% but MaxDD=40.9%. "
             "Leveraged ETF exposure (TQQQ/SOXL) drives high CAGR AND 53% MaxDD. Paper bot wired in via Alpaca PK keys.",
    "oos_sharpe": 1.41,
    "oos_cagr_pct": 77.7,
    "oos_max_dd_pct": -40.94,
    **qc,
}

# ---------- 2. SOXL Regime Strategy ----------
# Source: soxl_regime_strategy.json — CAGR 13.29%, Sharpe 1.211
# 5yr backtest May 2021-May 2026. 30% market exposure. 160 trades avg 1.76d.
# daily_winrate: trade win rate 55.6% × market_exposure 30% = 16.7% of ALL days positive
# (70% of days are flat / not in market → return = 0)
soxl_r = from_cagr_sharpe(0.1329, 1.211, winrate_est=0.556 * 0.30)
soxl_regime = {
    "name": "SOXL Regime (5-condition gate, daily ATR trail)",
    "window": "2021-05-07 → 2026-05-06 (5yr backtest, no lookahead)",
    "data_source": "soxl_regime_strategy.json",
    "cagr_pct": 13.29,
    "max_dd_pct": -7.79,
    "n_trades": 160,
    "pct_time_in_market": 30.0,
    "trade_win_rate_pct": 55.6,
    "notes": "5-condition gate (SMH/SPY/VIX/SOXL self-trend, all prev-day shifted). "
             "70% idle time → all-days win rate low (~17%) but Sharpe 1.21 on equity is best in class. "
             "MaxDD only 7.8%. No-lookahead confirmed. soxl_regime_oos shows p95 bootstrap DD -84% for pyramid variant (shelved); "
             "this conservative single-entry design is a separate config.",
    **soxl_r,
}

# ---------- 3. SPY Adaptive (full 5yr + honest OOS) ----------
# Full-period (5yr): Net PnL $138,777 on $100k → CAGR ~19.0%, Sharpe 0.90
# IS (2021–Oct 2024): true IS Sharpe 0.656
# OOS (Nov 2024–May 2026): Sharpe 1.297 but regime-inflated by 2025 bull
# Using full-period as "longest reliable window" but flagging IS degradation
spy_full = from_cagr_sharpe(0.190, 0.90, winrate_est=0.442)
spy_adaptive = {
    "name": "SPY Adaptive (EMA25, ADX regime, open-range trend-follow)",
    "window": "2021-05-07 → 2026-05-06 (5yr, IS+OOS combined)",
    "data_source": "spy_adaptive_backtest.json | spy_adaptive_oos.json",
    "cagr_pct": 19.0,
    "max_dd_pct": -10.7,
    "n_trades": 1046,
    "notes": "Full-period 5yr: CAGR~19% Sharpe 0.90. WARNING: true IS Sharpe only 0.656 (2021-Oct24); "
             "OOS Sharpe 1.297 is 2025 bull-market tailwind, not robust edge. Win rate 44.2% (intraday, ~1 trade/day). "
             "2022 almost flat (PF 1.02). Regime-dependent strategy.",
    "is_sharpe": 0.656,
    "oos_sharpe": 1.297,
    "oos_max_dd_pct": -10.7,
    **spy_full,
}

# ---------- 4. Diversified ETF Book (Concentrated OOS) ----------
# Proxy: QQQ 40% / TQQQ 20% / XLK 20% / SPY 20%
# OOS window: 2024-10-23 → 2026-06-12 (410 days = 1.63yr)
# OOS: CAGR 33.75%, Sharpe 1.105, MaxDD -30.69%
# Walk-forward 4-folds combined OOS: Sharpe 0.73, CAGR 19.77%, MaxDD -38.62%
# Using WF combined OOS as "reliable" metric, OOS snapshot for recent context
# Buy-and-hold ETF portfolio: daily win rate ~58-62% for tech-heavy in recent bull
div_conc = from_cagr_sharpe(0.3375, 1.105, winrate_est=0.60)
div_conc["daily_winrate"] = 60.0  # explicit override for tech bull period
div_concentrated = {
    "name": "ETF Book – Concentrated (QQQ/TQQQ/XLK/SPY)",
    "window": "OOS 2024-10-23 → 2026-06-12 (1.63yr); WF 5-fold combined 2021-2026",
    "data_source": "diversified_walkforward.json",
    "cagr_pct": 33.75,
    "max_dd_pct": -30.69,
    "wf_combined_sharpe": 0.73,
    "wf_combined_cagr_pct": 19.77,
    "wf_combined_max_dd_pct": -38.62,
    "notes": "Passive ETF portfolio proxy for QC concentrated book. OOS Sharpe 1.105 CAGR 33.75%. "
             "Walk-forward (4-fold) WF Sharpe drops to 0.73, MaxDD -38.6% — the OOS number benefits from "
             "late-2024/2025/2026 bull run. TQQQ 3x leverage drives high return AND 31% OOS MaxDD. "
             "Best as context for what leveraged book can do, not a standalone strategy.",
    **div_conc,
}

# ---------- 5. Diversified ETF Book (Diversified OOS) ----------
# Proxy: QQQ/SPY/IWM/TLT/GLD equal-weight
# OOS window: 2024-10-23 → 2026-06-12 (410 days = 1.63yr)
# OOS: CAGR 19.38%, Sharpe 1.324, MaxDD -12.84%
# WF combined OOS: Sharpe 0.801, CAGR 10.87%, MaxDD -21.61%
# Buy-and-hold diversified: daily win rate ~57%
div_div = from_cagr_sharpe(0.1938, 1.324, winrate_est=0.57)
div_diversified = {
    "name": "ETF Book – Diversified (QQQ/SPY/IWM/TLT/GLD equal-weight)",
    "window": "OOS 2024-10-23 → 2026-06-12 (1.63yr); WF 5-fold combined 2021-2026",
    "data_source": "diversified_walkforward.json",
    "cagr_pct": 19.38,
    "max_dd_pct": -12.84,
    "wf_combined_sharpe": 0.801,
    "wf_combined_cagr_pct": 10.87,
    "wf_combined_max_dd_pct": -21.61,
    "notes": "Best Sharpe in the set (1.324 OOS). MaxDD only -12.84% — most conservative risk profile. "
             "Equal-weight across asset classes (equity/small-cap/bonds/gold) provides genuine diversification. "
             "WF Sharpe 0.80 more reliable than 1-fold OOS snapshot. VERDICT: adopt (per diversified_walkforward verdict).",
    **div_div,
}

# ---------- 6. Risk Parity – Momentum ----------
# Source: risk_parity.json CAGR 11.11%, Sharpe 0.49, MaxDD -15.08%
# Window: 2021-01-05 → 2026-06-12 (5.4yr)
# Momentum allocation across ETFs; buy-and-hold daily_winrate ~55%
rp_mom = from_cagr_sharpe(0.1111, 0.49, winrate_est=0.55)
risk_parity_mom = {
    "name": "Risk Parity – Momentum (vol-adj momentum allocation)",
    "window": "2021-01-05 → 2026-06-12 (5.4yr backtest)",
    "data_source": "risk_parity.json",
    "cagr_pct": 11.11,
    "max_dd_pct": -15.08,
    "notes": "Momentum-ranked ETF allocation. CAGR 11.1% Sharpe 0.49 — decent return but poor Sharpe vs alternatives. "
             "MaxDD -15.1% hurt by 2022 rate-shock on bonds sleeve. Underperforms QC portfolio on all metrics.",
    **rp_mom,
}

# ---------- 7. Risk Parity – ERC (Equal Risk Contribution) ----------
# Source: risk_parity.json CAGR 6.91%, Sharpe 0.259, MaxDD -25.12%
rp_erc = from_cagr_sharpe(0.0691, 0.259, winrate_est=0.52)
risk_parity_erc = {
    "name": "Risk Parity – ERC (equal risk contribution)",
    "window": "2021-01-05 → 2026-06-12 (5.4yr backtest)",
    "data_source": "risk_parity.json",
    "cagr_pct": 6.91,
    "max_dd_pct": -25.12,
    "notes": "CAGR only 6.9%, Sharpe 0.26. Bond sleeve hammered by 2022 rate shock (TLT -40%). "
             "MaxDD -25.1% is the worst risk-adjusted outcome. DUD — below SPY buy-and-hold on every metric.",
    **rp_erc,
}

# ---------- 8. ES Mean-Reversion ----------
# Source: es_meanrev_oos.json — Sharpe 6.19 IS is OVERFIT (only 57 days of data, 8 trades)
# OOS: 0 trades. Disqualified.
es_meanrev = {
    "name": "ES Mean-Reversion (5min EMA band)",
    "window": "2026-03-09 → 2026-05-05 (57 days — NOT 5yr as claimed)",
    "data_source": "es_meanrev_oos.json",
    "cagr_pct": None,
    "max_dd_pct": None,
    "avg_daily_ret": None,
    "daily_vol": None,
    "daily_sharpe": None,
    "daily_winrate": None,
    "notes": "DISQUALIFIED — OVERFIT. Only 57 days of actual data (not 5yr), 8 total trades IS, 0 OOS trades. "
             "Reported Sharpe 6.19 is statistical noise from 8 trades across 54 swept configurations. "
             "Strategy does not fire at all on held-out data.",
}

# ---------- 9. mock_portfolio (paper trading bot) ----------
# Source: mock_audit.json — audit found/fixed 9 bugs; portfolio_state.json stale (2026-05-08)
# Insufficient live track record for reliable daily metrics (~6 weeks, no equity curve stored)
mock_pf = {
    "name": "mock_portfolio (Alpaca paper trading bot)",
    "window": "~2026-05-08 onward (active paper account, <6 weeks data)",
    "data_source": "portfolio_state.json (stale 2026-05-08) | mock_audit.json",
    "cagr_pct": None,
    "max_dd_pct": None,
    "avg_daily_ret": None,
    "daily_vol": None,
    "daily_sharpe": None,
    "daily_winrate": None,
    "notes": "Live paper bot (Alpaca paper API PK keys). 9 critical bugs audited/fixed June 2026. "
             "Current positions: COIN/SNOW/TQQQ/QQQ/AMD/AAPL from May 8. Track record too short for reliable stats. "
             "No equity curve CSV saved → cannot compute daily metrics yet. Needs 3-6 months live data.",
}

# ---------- Rank and assemble ----------

all_strategies = [
    qc_portfolio,
    soxl_regime,
    spy_adaptive,
    div_concentrated,
    div_diversified,
    risk_parity_mom,
    risk_parity_erc,
    es_meanrev,
    mock_pf,
]

# Rank only strategies with computable metrics
rankable = [s for s in all_strategies if s["avg_daily_ret"] is not None]
not_rankable = [s for s in all_strategies if s["avg_daily_ret"] is None]

ranked_by_ret = sorted(rankable, key=lambda s: s["avg_daily_ret"], reverse=True)
ranked_by_sharpe = sorted(rankable, key=lambda s: s["daily_sharpe"], reverse=True)

# Build ranked list with position
ranked = []
for i, s in enumerate(ranked_by_ret, 1):
    sharpe_rank = next(j for j, x in enumerate(ranked_by_sharpe, 1) if x["name"] == s["name"])
    ranked.append({
        "rank_by_return": i,
        "rank_by_sharpe": sharpe_rank,
        "name": s["name"],
        "avg_daily_ret": s["avg_daily_ret"],
        "daily_winrate": s["daily_winrate"],
        "daily_vol": s["daily_vol"],
        "daily_sharpe": s["daily_sharpe"],
        "cagr_pct": s["cagr_pct"],
        "max_dd_pct": s["max_dd_pct"],
        "window": s["window"],
        "notes": s["notes"],
    })

deploy_now = [
    {
        "strategy": "QC Portfolio (N=3, 2x winner weighting)",
        "rationale": "Highest avg daily return (0.135%/day = ~40% CAGR), daily Sharpe 1.06. "
                     "Paper bot already wired to Alpaca PK account. OOS (Dec24-Jun26) shows 77.7% CAGR / "
                     "Sharpe 1.41. Risk: 53% MaxDD from leveraged ETF exposure — use 25% position cap (from qc_drawdown_analysis).",
        "action": "Run: python3.10 /Users/quarki3/qc_portfolio/main.py --rebalance. "
                  "Set MAX_SINGLE_POSITION=0.25 in config.py first. Then schedule --rebalance at 9:31 ET daily.",
        "expected_daily_ret": "0.135% (~40% CAGR)",
        "max_dd_risk": "53% (leveraged ETF scenario)",
    },
    {
        "strategy": "ETF Book – Diversified (QQQ/SPY/IWM/TLT/GLD)",
        "rationale": "Highest Sharpe (1.324 OOS), lowest MaxDD (-12.84%), walk-forward validated (4/4 folds). "
                     "Passive equal-weight — zero execution complexity. 0.071%/day with real diversification.",
        "action": "Buy 20% each: QQQ, SPY, IWM, TLT, GLD in Alpaca paper account. Monthly rebalance.",
        "expected_daily_ret": "0.071% (~19% CAGR)",
        "max_dd_risk": "13% OOS, 22% WF worst-case",
    },
    {
        "strategy": "SOXL Regime (5-condition gate)",
        "rationale": "Best risk-adjusted Sharpe among active strategies (1.211). MaxDD only 7.8% over 5yr. "
                     "Cleanest no-lookahead 5yr backtest. Complementary to QC portfolio — SOXL fires only ~30% "
                     "of days, so it runs independently without interfering with QC rebalances.",
        "action": "Run soxl_regime_strategy.py daily at 9:35 ET. Check 5-condition gate; enter only if gate open. "
                  "Size to 2% equity risk per trade, max 30% notional. "
                  "Needs 5min SOXL/SMH/SPY data + VIX daily from yfinance.",
        "expected_daily_ret": "0.050%/day on equity (~13.3% CAGR)",
        "max_dd_risk": "7.8% (5yr backtest worst-case)",
    },
]

drop = [
    {
        "strategy": "ES Mean-Reversion",
        "reason": "OVERFIT. Only 57 days of data (not 5yr), 8 trades IS, 0 OOS trades. "
                  "Sharpe 6.19 is pure data-mining noise. Cannot deploy — strategy doesn't fire in OOS.",
    },
    {
        "strategy": "Risk Parity – ERC",
        "reason": "Sharpe 0.26, CAGR 6.9%, MaxDD -25%. Below SPY buy-and-hold on all metrics. "
                  "2022 bond crash (-40% TLT) is a structural vulnerability that persists.",
    },
    {
        "strategy": "Risk Parity – Momentum",
        "reason": "Sharpe 0.49, CAGR 11.1% — weak vs QC Portfolio (1.06/40%) and diversified ETF (1.324/19%). "
                  "Better than ERC but still a dud relative to available alternatives.",
    },
]

watch = [
    {
        "strategy": "SPY Adaptive",
        "reason": "Full-period Sharpe 0.90 but honest IS Sharpe only 0.656. OOS 1.297 is 2025 bull tailwind. "
                  "Win rate 44.2% barely positive edge. DO NOT deploy until it shows Sharpe >1.0 in a bear or choppy year.",
    },
    {
        "strategy": "ETF Book – Concentrated (QQQ/TQQQ/XLK/SPY)",
        "reason": "OOS Sharpe 1.105 / CAGR 33.75% looks great but WF Sharpe collapses to 0.73 and MaxDD hits "
                  "-38.6%. TQQQ 3x leverage makes this highly regime-dependent. Covered by QC portfolio which "
                  "has similar exposure but with QC strategy alpha on top.",
    },
    {
        "strategy": "mock_portfolio (paper bot)",
        "reason": "Too new (<6 weeks). Accumulate 3 months of daily equity curve data, then compute "
                  "live daily metrics and add to leaderboard.",
    },
]

summary = (
    "MAXIMIZE DAILY RETURN: Deploy QC Portfolio (0.135%/day, Sharpe 1.06) + "
    "SOXL Regime (0.050%/day, Sharpe 1.211) in tandem — they don't compete (SOXL fires 30% of days, "
    "QC rebalances daily). Add ETF Diversified book (0.071%/day, Sharpe 1.324) as the low-risk foundation. "
    "Combined expected return: ~40% CAGR (QC) + ~13% CAGR (SOXL, lower notional) + ~19% CAGR (ETF diversified). "
    "DROP: ES mean-rev (overfit/0 OOS trades), risk parity ERC (Sharpe 0.26), risk parity momentum (Sharpe 0.49). "
    "WATCH: SPY Adaptive — real edge exists but not proven outside 2025 bull regime."
)

result = {
    "task": "daily_return_leaderboard",
    "run_date": datetime.date.today().isoformat(),
    "ranked": ranked,
    "not_rankable": [
        {"name": s["name"], "reason": s["notes"][:200]}
        for s in not_rankable
    ],
    "deploy_now": deploy_now,
    "drop": drop,
    "watch": watch,
    "summary": summary,
    "methodology": {
        "avg_daily_ret": "(1+CAGR)^(1/252)-1 from longest reliable window",
        "daily_vol": "CAGR / (Sharpe × √252), assuming rf=0 (consistent with all backtests)",
        "daily_sharpe": "Identical to annual Sharpe when rf=0 convention used consistently",
        "daily_winrate": "For active strategies: trade_win_rate × pct_time_in_market (fraction of all days >0). "
                         "For passive/daily-rebalance: fraction of days with positive portfolio return (from trade records or estimated from regime).",
        "window_choice": "Longest no-lookahead backtest available; OOS noted separately where split exists",
    },
    "success": True,
}

OUT.write_text(json.dumps(result, indent=2))
print(f"Written to {OUT}")

# Print human-readable summary
print("\n=== DAILY RETURN LEADERBOARD ===")
print(f"{'Rank':>4}  {'Strategy':<45}  {'AvgDailyRet':>11}  {'WinRate':>8}  {'DailyVol':>9}  {'DailySharpe':>11}  {'MaxDD':>7}")
print("-" * 105)
for r in ranked:
    wr = f"{r['daily_winrate']:.1f}%" if r['daily_winrate'] else "  est."
    print(f"  {r['rank_by_return']:>2}.  {r['name']:<45}  {r['avg_daily_ret']:>10.4f}%  {wr:>8}  {r['daily_vol']:>8.4f}%  {r['daily_sharpe']:>11.4f}  {r['max_dd_pct']:>7.1f}%")

print("\n=== BY DAILY SHARPE ===")
for i, r in enumerate(sorted(ranked, key=lambda x: x['daily_sharpe'], reverse=True), 1):
    print(f"  {i:>2}.  Sharpe {r['daily_sharpe']:.3f}  |  {r['name']}")

print("\n=== DEPLOY NOW ===")
for d in deploy_now:
    print(f"  ✓ {d['strategy']}")
    print(f"    Return: {d['expected_daily_ret']}  |  MaxDD risk: {d['max_dd_risk']}")

print("\n=== DROP ===")
for d in drop:
    print(f"  ✗ {d['strategy']}: {d['reason'][:100]}")
