#!/usr/bin/env python3
"""
Compile all strategy results vs buy-and-hold benchmarks and write final JSON.
"""
import json, math, os

# ── Load benchmark data computed by benchmark_vs_buyhold.py ──────────────────
with open('/tmp/bm_raw.json') as f:
    bm = json.load(f)

# Helpers
def cagr_from_pnl(initial, net_pnl, n_years):
    final = initial + net_pnl
    return round((final / initial) ** (1 / n_years) * 100 - 100, 2)

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 1 — SPY Adaptive (spy_adaptive.py)
# Window: 2021-05-07 → 2026-05-06 (4.98 yr)
# Config: EMA25, ATR14, ADX20, long-trail 3.0×ATR, 2:1 leverage, $100k equity
# Source: spy_adaptive_backtest.json, spy_adaptive_oos.json
# ─────────────────────────────────────────────────────────────────────────────
bm_spy = bm['spy_window']
spy_adaptive = {
    "name": "SPY Adaptive (2:1 leverage, ORT trend-follow)",
    "window": "2021-05-07 → 2026-05-06",
    "n_years": bm_spy['n_years'],
    # Strategy metrics
    "cagr": cagr_from_pnl(100_000, 138_777, bm_spy['n_years']),  # ~19.1%
    "sharpe": 0.90,
    "maxdd": -10.7,
    "net_pnl": 138_777,
    "n_trades": 1046,
    # Benchmark metrics (same window)
    "qqq_cagr": bm_spy['qqq_bh']['cagr_pct'],
    "qqq_sharpe": bm_spy['qqq_bh']['sharpe'],
    "qqq_maxdd": bm_spy['qqq_bh']['maxdd_pct'],
    "spy_cagr": bm_spy['spy_bh']['cagr_pct'],
    "spy_sharpe": bm_spy['spy_bh']['sharpe'],
    "spy_maxdd": bm_spy['spy_bh']['maxdd_pct'],
    "qqq_gld_7030": bm_spy['qqq_gld_7030'],
    "spy_tlt_6040": bm_spy['spy_tlt_6040'],
    # Alpha
    "alpha_vs_qqq_cagr_pp": round(cagr_from_pnl(100_000, 138_777, bm_spy['n_years']) - bm_spy['qqq_bh']['cagr_pct'], 2),
    "alpha_vs_spy_cagr_pp": round(cagr_from_pnl(100_000, 138_777, bm_spy['n_years']) - bm_spy['spy_bh']['cagr_pct'], 2),
    "sharpe_vs_qqq": round(0.90 - bm_spy['qqq_bh']['sharpe'], 3),
    "dd_improvement_vs_qqq_pp": round(abs(-10.7) - abs(bm_spy['qqq_bh']['maxdd_pct']), 2),
    "beats_qqq_riskadj": True,   # Sharpe 0.90 > 0.796 AND MaxDD -10.7% >> -35.1%
    "beats_70_30_qqq_gld_riskadj": False,  # Sharpe 0.90 < 1.078 (loses on Sharpe)
    "caveat": (
        "2:1 leverage — ~half CAGR without it (~9-10%, below QQQ). "
        "Sharpe 0.90 tuned on full 2021-2026 data; true IS Sharpe (2021-Oct2024) is 0.656. "
        "OOS Sharpe 1.297 but entirely from 2025 bull tailwind. "
        "70/30 QQQ+GLD passive blend has HIGHER Sharpe (1.078 vs 0.90) at zero management effort."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 2 — SOXL Regime (5-condition gate)
# Window: 2021-05-07 → 2026-05-07 (~5 yr)
# Source: soxl_regime_strategy.json
# NOTE: SOXL is itself 3x leveraged. This strategy trades the 3x ETF.
# ─────────────────────────────────────────────────────────────────────────────
bm_soxl = bm['soxl_window']
soxl_regime = {
    "name": "SOXL Regime (5-condition gate, short-hold)",
    "window": "2021-05-07 → 2026-05-07",
    "n_years": bm_soxl['n_years'],
    "cagr": 13.29,
    "sharpe": 1.211,
    "maxdd": -7.79,
    "net_pnl": 86_579,
    "n_trades": 160,
    "qqq_cagr": bm_soxl['qqq_bh']['cagr_pct'],
    "qqq_sharpe": bm_soxl['qqq_bh']['sharpe'],
    "qqq_maxdd": bm_soxl['qqq_bh']['maxdd_pct'],
    "spy_cagr": bm_soxl['spy_bh']['cagr_pct'],
    "spy_sharpe": bm_soxl['spy_bh']['sharpe'],
    "spy_maxdd": bm_soxl['spy_bh']['maxdd_pct'],
    "qqq_gld_7030": bm_soxl['qqq_gld_7030'],
    "spy_tlt_6040": bm_soxl['spy_tlt_6040'],
    "alpha_vs_qqq_cagr_pp": round(13.29 - bm_soxl['qqq_bh']['cagr_pct'], 2),
    "alpha_vs_spy_cagr_pp": round(13.29 - bm_soxl['spy_bh']['cagr_pct'], 2),
    "sharpe_vs_qqq": round(1.211 - bm_soxl['qqq_bh']['sharpe'], 3),
    "dd_improvement_vs_qqq_pp": round(abs(-7.79) - abs(bm_soxl['qqq_bh']['maxdd_pct']), 2),
    "beats_qqq_riskadj": True,   # Sharpe 1.211 >> 0.794 AND MaxDD -7.8% >> -35.1%
    "beats_70_30_qqq_gld_riskadj": True,  # Sharpe 1.211 > 1.077 AND MaxDD -7.8% >> -28.1%
    "caveat": (
        "SOXL itself is 3x levered. Strategy lags QQQ on raw CAGR (-3.2pp). "
        "Only 30% time in market. 160 trades is statistically meaningful for Sharpe. "
        "Win rate 55.6%, profit factor 1.739. This is the STRONGEST risk-adjusted result we have."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 3 — ES Mean-Rev (es_mean_rev_5yr.py)
# DISQUALIFIED — definitively overfit
# Source: es_meanrev_oos.json
# ─────────────────────────────────────────────────────────────────────────────
es_meanrev = {
    "name": "ES Mean-Rev (es_mean_rev_5yr.py)",
    "window": "2026-03-09 → 2026-05-05 (57 DAYS, not 5yr as named)",
    "n_years": 0.16,
    "cagr": None,
    "sharpe": None,  # IS 6.19 is statistically meaningless
    "maxdd": None,
    "qqq_cagr": None,
    "qqq_sharpe": None,
    "qqq_maxdd": None,
    "beats_qqq_riskadj": False,
    "beats_70_30_qqq_gld_riskadj": False,
    "disqualified": True,
    "caveat": (
        "DISQUALIFIED. Script runs on 57 days of data (cache file es_bars_5min_60d.csv), "
        "not 5 years as implied by name. n=8 trades total. Sharpe 6.19 is pure noise "
        "(std error > 7.7 at n=8). 0 trades in OOS period. 54-param sweep on 8 trades = "
        "extreme data snooping. Results are meaningless — do not cite."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 4 — Mock Portfolio Bot (mock_portfolio.py)
# No backtest results — live trading bot only
# ─────────────────────────────────────────────────────────────────────────────
mock_portfolio = {
    "name": "Mock Portfolio Bot (mock_portfolio.py)",
    "window": "live trading / paper only",
    "cagr": None,
    "sharpe": None,
    "maxdd": None,
    "qqq_cagr": None,
    "qqq_sharpe": None,
    "qqq_maxdd": None,
    "beats_qqq_riskadj": False,
    "beats_70_30_qqq_gld_riskadj": False,
    "disqualified": True,
    "caveat": (
        "No backtest metrics available — this is a live trading bot, not a backtested strategy. "
        "The mock_audit.json documented 9 bugs fixed in the execution logic but has no "
        "return/Sharpe/MaxDD history to evaluate against benchmarks."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 5 — QC Portfolio (N=3 strategies, top-N selection)
# Window: 2021-08-09 → 2026-06-12 (4.83 yr) — from qc_diversified_oos.json
# Source: qc_top_n.json, qc_diversified_oos.json
# ─────────────────────────────────────────────────────────────────────────────
bm_qc = bm['qc_all_window']
qc_portfolio = {
    "name": "QC Portfolio (Top-3 QC Leaderboard, N=3 best by Sharpe/DD)",
    "window": "2021-08-09 → 2026-06-12",
    "n_years": bm_qc['n_years'],
    # From qc_top_n.json N=3 result (best Sharpe/DD tradeoff)
    "cagr": 40.12,
    "sharpe": 1.06,
    "maxdd": -53.24,
    "qqq_cagr": bm_qc['qqq_bh']['cagr_pct'],
    "qqq_sharpe": bm_qc['qqq_bh']['sharpe'],
    "qqq_maxdd": bm_qc['qqq_bh']['maxdd_pct'],
    "spy_cagr": bm_qc['spy_bh']['cagr_pct'],
    "spy_sharpe": bm_qc['spy_bh']['sharpe'],
    "spy_maxdd": bm_qc['spy_bh']['maxdd_pct'],
    "qqq_gld_7030": bm_qc['qqq_gld_7030'],
    "spy_tlt_6040": bm_qc['spy_tlt_6040'],
    "alpha_vs_qqq_cagr_pp": round(40.12 - bm_qc['qqq_bh']['cagr_pct'], 2),
    "alpha_vs_spy_cagr_pp": round(40.12 - bm_qc['spy_bh']['cagr_pct'], 2),
    "sharpe_vs_qqq": round(1.06 - bm_qc['qqq_bh']['sharpe'], 3),
    "dd_improvement_vs_qqq_pp": round(abs(-53.24) - abs(bm_qc['qqq_bh']['maxdd_pct']), 2),
    # Technically Sharpe 1.06 > QQQ 0.749, but MaxDD -53.2% is FAR WORSE than QQQ's -35.1%
    # Risk-adjusted accounting for DD: NO
    "beats_qqq_riskadj": False,   # MaxDD -53.24% vs QQQ -35.1% — WORSE on key risk measure despite higher Sharpe
    "beats_70_30_qqq_gld_riskadj": False,  # MaxDD -53.24% vs QQQ+GLD -28.1% — catastrophically worse
    "caveat": (
        "CAGR 40.12% is real but requires tolerating -53.2% MaxDD (a $53k loss on $100k). "
        "To recover from -53% you need +113% — that takes years. "
        "MaxDD is WORSE than QQQ (-53.2% vs -35.1%), driven by 3x leveraged ETF strategies (TQQQ/SOXL/LABU). "
        "Sharpe 1.06 technically edges QQQ (0.749) but the DD regime makes this a false win — "
        "Sharpe does not capture tail risk adequately here. "
        "OOS is 2025 bull period (QC OOS Sharpe 1.41, but from extreme bull tailwind). "
        "Realistic deployment would require iron discipline to hold through -50% drawdowns."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 6 — Risk Parity (ERC)
# Window: 2021-01-05 → 2026-06-12 (5.42 yr)
# Source: risk_parity.json
# ─────────────────────────────────────────────────────────────────────────────
bm_rp = bm['rp_window']
risk_parity_erc = {
    "name": "Risk Parity — Equal Risk Contribution (ERC)",
    "window": "2021-01-05 → 2026-06-12",
    "n_years": bm_rp['n_years'],
    "cagr": 6.91,
    "sharpe": 0.259,
    "maxdd": -25.12,
    "qqq_cagr": bm_rp['qqq_bh']['cagr_pct'],
    "qqq_sharpe": bm_rp['qqq_bh']['sharpe'],
    "qqq_maxdd": bm_rp['qqq_bh']['maxdd_pct'],
    "spy_cagr": bm_rp['spy_bh']['cagr_pct'],
    "spy_sharpe": bm_rp['spy_bh']['sharpe'],
    "spy_maxdd": bm_rp['spy_bh']['maxdd_pct'],
    "qqq_gld_7030": bm_rp['qqq_gld_7030'],
    "spy_tlt_6040": bm_rp['spy_tlt_6040'],
    "alpha_vs_qqq_cagr_pp": round(6.91 - bm_rp['qqq_bh']['cagr_pct'], 2),
    "sharpe_vs_qqq": round(0.259 - bm_rp['qqq_bh']['sharpe'], 3),
    "beats_qqq_riskadj": False,
    "beats_70_30_qqq_gld_riskadj": False,
    "caveat": (
        "Clear loser. CAGR 6.91% vs QQQ 17.41% (-10.5pp). Sharpe 0.259 vs QQQ 0.827. "
        "2022 rate-shock devastated TLT bonds (-40%), which ERC overweights. "
        "The period 2021-2026 was a hostile environment for classical risk-parity (stocks AND bonds both crashed in 2022)."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 7 — Risk Parity (Momentum variant)
# Same window as ERC
# Source: risk_parity.json
# ─────────────────────────────────────────────────────────────────────────────
risk_parity_momentum = {
    "name": "Risk Parity — Momentum weighted",
    "window": "2021-01-05 → 2026-06-12",
    "n_years": bm_rp['n_years'],
    "cagr": 11.11,
    "sharpe": 0.49,
    "maxdd": -15.08,
    "qqq_cagr": bm_rp['qqq_bh']['cagr_pct'],
    "qqq_sharpe": bm_rp['qqq_bh']['sharpe'],
    "qqq_maxdd": bm_rp['qqq_bh']['maxdd_pct'],
    "spy_cagr": bm_rp['spy_bh']['cagr_pct'],
    "spy_sharpe": bm_rp['spy_bh']['sharpe'],
    "spy_maxdd": bm_rp['spy_bh']['maxdd_pct'],
    "qqq_gld_7030": bm_rp['qqq_gld_7030'],
    "spy_tlt_6040": bm_rp['spy_tlt_6040'],
    "alpha_vs_qqq_cagr_pp": round(11.11 - bm_rp['qqq_bh']['cagr_pct'], 2),
    "sharpe_vs_qqq": round(0.49 - bm_rp['qqq_bh']['sharpe'], 3),
    "beats_qqq_riskadj": False,
    "beats_70_30_qqq_gld_riskadj": False,
    "caveat": (
        "Sharpe 0.49 vs QQQ 0.827 — loses on risk-adjusted return. "
        "MaxDD -15.1% is better than QQQ -35.1% but the CAGR penalty (-6.3pp) is too steep. "
        "You're giving up 6pp of return annually just to halve the drawdown — "
        "a 60/40 SPY+TLT does similar with less complexity (though also badly hurt in 2022)."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY 8 — Diversified ETF book (concentrated: QQQ/TQQQ/XLK/SPY)
# This is a passive fixed-weight ETF portfolio, NOT an active strategy
# Source: diversified_walkforward.json
# Reporting IS period for full-period perspective
# ─────────────────────────────────────────────────────────────────────────────
bm_div_is = bm['div_is_window']
bm_div_oos = bm['div_oos_window']

diversified_concentrated = {
    "name": "Diversified ETF Book — Concentrated (QQQ/TQQQ/XLK/SPY 40/20/20/20) — passive hold",
    "window": "IS: 2021-01-04 → 2024-10-22 | OOS: 2024-10-23 → 2026-06-12",
    "n_years": {"is": bm_div_is['n_years'], "oos": bm_div_oos['n_years']},
    "cagr": {"is": 17.46, "oos": 33.75},
    "sharpe": {"is": 0.682, "oos": 1.105},
    "maxdd": {"is": -45.06, "oos": -30.69},
    "qqq_cagr": {"is": bm_div_is['qqq_bh']['cagr_pct'], "oos": bm_div_oos['qqq_bh']['cagr_pct']},
    "qqq_sharpe": {"is": bm_div_is['qqq_bh']['sharpe'], "oos": bm_div_oos['qqq_bh']['sharpe']},
    "qqq_maxdd": {"is": bm_div_is['qqq_bh']['maxdd_pct'], "oos": bm_div_oos['qqq_bh']['maxdd_pct']},
    "beats_qqq_riskadj": False,  # IS: Sharpe 0.682 < QQQ 0.69, MaxDD worse; OOS: Sharpe 1.105 < QQQ 1.222
    "beats_70_30_qqq_gld_riskadj": False,
    "caveat": (
        "PASSIVE portfolio, not an active strategy. Includes TQQQ (3x leveraged), driving the MaxDD to -45%. "
        "IS Sharpe 0.682 ≈ QQQ 0.690 (essentially the same). OOS Sharpe 1.105 < QQQ OOS 1.222. "
        "Adding TQQQ gives CAGR boost but at a severe drawdown cost. Does not beat index risk-adjusted."
    ),
}

diversified_balanced = {
    "name": "Diversified ETF Book — Balanced (QQQ/SPY/IWM/TLT/GLD 20/20/20/20/20) — passive hold",
    "window": "IS: 2021-01-04 → 2024-10-22 | OOS: 2024-10-23 → 2026-06-12",
    "n_years": {"is": bm_div_is['n_years'], "oos": bm_div_oos['n_years']},
    "cagr": {"is": 6.87, "oos": 19.38},
    "sharpe": {"is": 0.563, "oos": 1.324},
    "maxdd": {"is": -25.55, "oos": -12.84},
    "qqq_cagr": {"is": bm_div_is['qqq_bh']['cagr_pct'], "oos": bm_div_oos['qqq_bh']['cagr_pct']},
    "qqq_sharpe": {"is": bm_div_is['qqq_bh']['sharpe'], "oos": bm_div_oos['qqq_bh']['sharpe']},
    "qqq_maxdd": {"is": bm_div_is['qqq_bh']['maxdd_pct'], "oos": bm_div_oos['qqq_bh']['maxdd_pct']},
    # IS: Sharpe 0.563 < QQQ 0.690 — NO. OOS: Sharpe 1.324 > QQQ 1.222 — YES but it's passive hold
    "beats_qqq_riskadj": "OOS only (1.324 > 1.222) — but IS fails (0.563 < 0.690). Full-period: mixed.",
    "beats_70_30_qqq_gld_riskadj": False,  # IS badly underperforms; OOS marginally better MaxDD
    "caveat": (
        "PASSIVE portfolio, not an active strategy. 2022 TLT massacre dragged IS Sharpe to 0.563 (worse than QQQ). "
        "OOS Sharpe 1.324 > QQQ 1.222 is notable — a balanced multi-asset passive hold beat pure equity in the "
        "Oct-2024 to Jun-2026 period, largely because GLD and IWM diversified away QQQ volatility. "
        "This is the best argument FOR passive diversification, not for active management."
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# LOW-EFFORT ALTERNATIVES: summary across windows
# ─────────────────────────────────────────────────────────────────────────────
low_effort_comparison = {
    "70_30_qqq_gld": {
        "description": "Buy-and-hold 70% QQQ + 30% GLD, never touch it",
        "over_spy_soxl_window": bm['spy_window']['qqq_gld_7030'],
        "over_rp_window": bm['rp_window']['qqq_gld_7030'],
        "over_qc_window": bm['qc_all_window']['qqq_gld_7030'],
        "verdict": (
            "Sharpe 1.016-1.078 across all windows. "
            "Beats SPY Adaptive (0.90) — zero effort vs 1,046 trades/5yr. "
            "Loses to SOXL Regime (1.211) and OOS balanced ETF (1.324). "
            "MaxDD -28.1% is the main weakness — acceptable if GLD provides crisis hedge."
        ),
    },
    "60_40_spy_tlt": {
        "description": "Buy-and-hold 60% SPY + 40% TLT, never touch it",
        "over_spy_soxl_window": bm['spy_window']['spy_tlt_6040'],
        "over_rp_window": bm['rp_window']['spy_tlt_6040'],
        "verdict": (
            "Sharpe 0.50-0.54 across all windows. "
            "LOSES to every strategy and to QQQ buy-and-hold. "
            "The 2022 rate shock (TLT -40%) permanently broke the 60/40 paradigm for this period. "
            "Never use SPY+TLT as a low-effort baseline — QQQ alone is better."
        ),
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# FINAL VERDICT
# ─────────────────────────────────────────────────────────────────────────────
honest_verdict = (
    "HONEST FINDING: Only ONE strategy clearly beats buy-and-hold on a risk-adjusted basis "
    "including vs a simple passive blend — the SOXL Regime (Sharpe 1.211, MaxDD -7.8%). "
    "Everything else either fails outright or requires a serious asterisk.\n\n"

    "SOXL REGIME [WINNER]: Sharpe 1.211 beats QQQ (0.794) and 70/30 QQQ+GLD (1.077). "
    "MaxDD -7.8% vs QQQ -35.1%. CAGR 13.3% lags QQQ's 16.5% but the risk-adjusted edge is real. "
    "Caveat: SOXL is itself 3x leveraged, so underlying semi exposure is much larger.\n\n"

    "SPY ADAPTIVE [CONDITIONAL]: Beats QQQ on Sharpe (0.90 vs 0.796) and dramatically on MaxDD. "
    "BUT 2:1 leverage is required for the CAGR advantage — unleveraged CAGR ~9-10% would LOSE to QQQ. "
    "More critically: a passive 70/30 QQQ+GLD blend has HIGHER Sharpe (1.078) with zero management. "
    "The 0.90 Sharpe was computed on fully-in-sample data; true IS Sharpe is 0.656. "
    "VERDICT: Conditional pass vs QQQ alone; fails vs the relevant passive alternative.\n\n"

    "QC PORTFOLIO [DANGEROUS FALSE WIN]: CAGR 40.12% is impressive, Sharpe 1.06 edges QQQ (0.749). "
    "But MaxDD -53.24% is WORSE than QQQ's -35.1%. A -53% drawdown requires 113% recovery. "
    "The Sharpe does not capture this tail risk. Practically: most investors would panic-sell at -30%. "
    "This is not a genuine win when MaxDD exceeds the benchmark.\n\n"

    "RISK PARITY (ERC) [CLEAR LOSER]: CAGR 6.91%, Sharpe 0.259 vs QQQ CAGR 17.41%, Sharpe 0.827. "
    "Lost on every single metric. 2022 bond crash destroyed the thesis. Don't use classical ERC.\n\n"

    "RISK PARITY (MOMENTUM) [LOSER]: Sharpe 0.49 vs QQQ 0.827. Better MaxDD but insufficient alpha.\n\n"

    "ES MEAN-REV [DISQUALIFIED]: Not a real result. 8 trades on 57 days = noise, not alpha.\n\n"

    "THE LOW-EFFORT TEST: Would 70/30 QQQ+GLD have beaten our best active strategy risk-adjusted? "
    "YES — it beats SPY Adaptive on Sharpe (1.077 vs 0.90). "
    "NO — SOXL Regime has higher Sharpe (1.211) AND much better MaxDD (-7.8% vs -28.1%). "
    "SOXL Regime is the only strategy that clears the low-effort passive bar AND the index bar. "
    "SUMMARY: We have one genuine winner (SOXL Regime). One conditional pass with caveats "
    "(SPY Adaptive). The rest do not beat buy-and-hold on risk-adjusted metrics."
)

# ─────────────────────────────────────────────────────────────────────────────
# Assemble output
# ─────────────────────────────────────────────────────────────────────────────
output = {
    "task": "benchmark_vs_buyhold",
    "run_date": "2026-06-15",
    "methodology": (
        "Buy-and-hold benchmarks computed from yfinance adjusted close prices. "
        "Sharpe: annualized daily returns / daily return std × sqrt(252), RF=0 (matching strategy backtests). "
        "MaxDD: max peak-to-trough drawdown on equity curve. "
        "CAGR: (final/initial)^(1/n_years) - 1. "
        "All windows matched exactly to each strategy's test period."
    ),
    "per_strategy": [
        spy_adaptive,
        soxl_regime,
        es_meanrev,
        mock_portfolio,
        qc_portfolio,
        risk_parity_erc,
        risk_parity_momentum,
        diversified_concentrated,
        diversified_balanced,
    ],
    "low_effort_alternatives": low_effort_comparison,
    "benchmark_summary": {
        "spy_soxl_window_2021_05_to_2026_05": {
            "qqq": bm['spy_window']['qqq_bh'],
            "spy": bm['spy_window']['spy_bh'],
            "qqq_gld_7030": bm['spy_window']['qqq_gld_7030'],
            "spy_tlt_6040": bm['spy_window']['spy_tlt_6040'],
        },
        "rp_window_2021_01_to_2026_06": {
            "qqq": bm['rp_window']['qqq_bh'],
            "spy": bm['rp_window']['spy_bh'],
            "qqq_gld_7030": bm['rp_window']['qqq_gld_7030'],
            "spy_tlt_6040": bm['rp_window']['spy_tlt_6040'],
        },
        "qc_window_2021_08_to_2026_06": {
            "qqq": bm['qc_all_window']['qqq_bh'],
            "spy": bm['qc_all_window']['spy_bh'],
            "qqq_gld_7030": bm['qc_all_window']['qqq_gld_7030'],
            "spy_tlt_6040": bm['qc_all_window']['spy_tlt_6040'],
        },
        "div_oos_window_2024_10_to_2026_06": {
            "qqq": bm['div_oos_window']['qqq_bh'],
            "spy": bm['div_oos_window']['spy_bh'],
            "qqq_gld_7030": bm['div_oos_window']['qqq_gld_7030'],
            "spy_tlt_6040": bm['div_oos_window']['spy_tlt_6040'],
        },
    },
    "scoreboard": {
        "beats_qqq_AND_spy_on_sharpe_and_dd": ["SOXL Regime"],
        "beats_qqq_sharpe_only_with_caveats": ["SPY Adaptive (2:1 leverage; tuned on full data)", "QC Portfolio (MaxDD worse than QQQ)"],
        "beats_nothing": ["Risk Parity ERC", "Risk Parity Momentum", "Diversified Concentrated IS"],
        "disqualified": ["ES Mean-Rev (overfit, n=8)", "Mock Portfolio (no backtest)"],
        "passive_beats_active": ["70/30 QQQ+GLD beats SPY Adaptive on Sharpe (1.078 vs 0.90)"],
        "one_strategy_beats_passive_blend": "SOXL Regime (Sharpe 1.211 > 70/30 QQQ+GLD 1.077, MaxDD -7.8% vs -28.1%)",
    },
    "any_strategy_beats_buyhold": True,
    "any_strategy_beats_low_effort_passive": True,
    "honest_verdict": honest_verdict,
    "success": True,
}

# Write output
os.makedirs('/Users/quarki3/swarm_results/completed', exist_ok=True)
out_path = '/Users/quarki3/swarm_results/completed/benchmark_vs_buyhold.json'
with open(out_path, 'w') as f:
    json.dump(output, f, indent=2)
print(f"Written: {out_path}")

# Print summary table
print("\n" + "="*80)
print("STRATEGY vs BUY-AND-HOLD SUMMARY (same date windows)")
print("="*80)
print(f"{'Strategy':<42} {'CAGR':>6} {'Sharpe':>7} {'MaxDD':>7} | {'QQQ':>6} {'Sharpe':>7} {'MaxDD':>7} | {'Beats QQQ':>10}")
print("-"*80)

rows = [
    ("SPY Adaptive (2:1 lev)", 19.1, 0.90, -10.7, bm['spy_window']['qqq_bh']),
    ("SOXL Regime (5-cond gate)", 13.3, 1.211, -7.8, bm['soxl_window']['qqq_bh']),
    ("QC Portfolio N=3 (full)", 40.12, 1.06, -53.24, bm['qc_all_window']['qqq_bh']),
    ("Risk Parity ERC", 6.91, 0.259, -25.12, bm['rp_window']['qqq_bh']),
    ("Risk Parity Momentum", 11.11, 0.49, -15.08, bm['rp_window']['qqq_bh']),
    ("70/30 QQQ+GLD [passive]", bm['spy_window']['qqq_gld_7030']['cagr_pct'], bm['spy_window']['qqq_gld_7030']['sharpe'], bm['spy_window']['qqq_gld_7030']['maxdd_pct'], bm['spy_window']['qqq_bh']),
    ("60/40 SPY+TLT [passive]", bm['spy_window']['spy_tlt_6040']['cagr_pct'], bm['spy_window']['spy_tlt_6040']['sharpe'], bm['spy_window']['spy_tlt_6040']['maxdd_pct'], bm['spy_window']['qqq_bh']),
    ("QQQ BH [benchmark]", bm['spy_window']['qqq_bh']['cagr_pct'], bm['spy_window']['qqq_bh']['sharpe'], bm['spy_window']['qqq_bh']['maxdd_pct'], bm['spy_window']['qqq_bh']),
]

for name, cagr, sharpe, dd, qqq in rows:
    beats = "YES" if (sharpe > qqq['sharpe'] and abs(dd) < abs(qqq['maxdd_pct'])) else ("SHARPE" if sharpe > qqq['sharpe'] else "NO")
    print(f"{name:<42} {cagr:>5.1f}% {sharpe:>7.3f} {dd:>7.1f}% | {qqq['cagr_pct']:>5.1f}% {qqq['sharpe']:>7.3f} {qqq['maxdd_pct']:>7.1f}% | {beats:>10}")

print("="*80)
print("\nLOW-EFFORT ALTERNATIVE — 70/30 QQQ+GLD (spy/soxl window):")
m = bm['spy_window']['qqq_gld_7030']
print(f"  CAGR {m['cagr_pct']}%  Sharpe {m['sharpe']}  MaxDD {m['maxdd_pct']}%")
print(f"  → Beats SPY Adaptive on Sharpe ({m['sharpe']} > 0.90)")
print(f"  → LOSES to SOXL Regime on Sharpe (1.211 > {m['sharpe']}) AND MaxDD (-7.8% vs {m['maxdd_pct']}%)")
