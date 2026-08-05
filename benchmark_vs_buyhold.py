#!/usr/bin/env python3
"""
Benchmark all strategies vs buy-and-hold QQQ, SPY, 70/30 QQQ+GLD, 60/40 SPY+TLT.
Apples-to-apples: uses EXACT strategy date windows for each comparison.
"""
import yfinance as yf
import numpy as np
import pandas as pd
import json
import os
from datetime import datetime

RF_ANNUAL = 0.045  # avg risk-free (4.5% ~ Fed funds avg 2021-2026)
# NOTE: many strategy backtests use RF=0 for Sharpe. We compute both but use RF=0
# for the "reported" number so comparisons to strategy-reported Sharpes are fair.

def get_prices(tickers, start, end):
    df = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df = df['Close']
    else:
        df = df[['Close']]
        df.columns = tickers if isinstance(tickers, list) else [tickers]
    return df.ffill().dropna(how='all')

def portfolio_rets(prices, weights):
    """Daily portfolio returns, rebalanced daily to fixed weights."""
    rets = prices.pct_change().dropna()
    w = np.array([weights[t] for t in prices.columns])
    return (rets.values * w).sum(axis=1)

def compute_metrics(rets_array, n_years, use_rf=False):
    rf_daily = RF_ANNUAL / 252 if use_rf else 0.0
    total = float(np.prod(1 + rets_array)) - 1.0
    cagr = (1 + total) ** (1 / n_years) - 1
    excess = rets_array - rf_daily
    sharpe = float(np.mean(excess) / np.std(rets_array, ddof=1) * np.sqrt(252))
    cum = np.cumprod(1 + rets_array)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    maxdd = float(np.min(dd))
    return {
        'cagr_pct': round(cagr * 100, 2),
        'sharpe': round(sharpe, 3),
        'maxdd_pct': round(maxdd * 100, 2),
    }

# ── Download all needed data ──────────────────────────────────────────────────
print("Downloading market data...")
tickers = ['QQQ', 'SPY', 'GLD', 'TLT']
all_px = get_prices(tickers, '2020-12-01', '2026-06-16')
print(f"Got data: {all_px.index[0].date()} → {all_px.index[-1].date()}, columns={list(all_px.columns)}")

def bh_block(start, end):
    """Compute all benchmark metrics for a window."""
    px = all_px.loc[start:end].copy()
    n_years = len(px) / 252.0

    # Single-asset
    def single(t):
        r = px[t].pct_change().dropna().values
        return compute_metrics(r, n_years)

    qqq = single('QQQ')
    spy = single('SPY')

    # 70/30 QQQ + GLD
    p7030 = px[['QQQ', 'GLD']].dropna()
    r7030 = portfolio_rets(p7030, {'QQQ': 0.70, 'GLD': 0.30})
    m7030 = compute_metrics(r7030, len(p7030) / 252.0)

    # 60/40 SPY + TLT
    p6040 = px[['SPY', 'TLT']].dropna()
    r6040 = portfolio_rets(p6040, {'SPY': 0.60, 'TLT': 0.40})
    m6040 = compute_metrics(r6040, len(p6040) / 252.0)

    return {
        'actual_start': str(px.index[0].date()),
        'actual_end': str(px.index[-1].date()),
        'n_years': round(n_years, 2),
        'qqq_bh': qqq,
        'spy_bh': spy,
        'qqq_gld_7030': m7030,
        'spy_tlt_6040': m6040,
    }

# ── Benchmark windows per strategy ───────────────────────────────────────────
print("Computing benchmarks...")

win_spy    = ('2021-05-07', '2026-05-06')
win_soxl   = ('2021-05-07', '2026-05-07')   # ~same window
win_rp     = ('2021-01-05', '2026-06-12')
win_qc_all = ('2021-08-09', '2026-06-12')   # qc_diversified_oos full window
win_div_is = ('2021-01-04', '2024-10-22')   # diversified walkforward IS
win_div_oos= ('2024-10-23', '2026-06-12')   # diversified walkforward OOS

bm = {
    'spy_window':    bh_block(*win_spy),
    'soxl_window':   bh_block(*win_soxl),
    'rp_window':     bh_block(*win_rp),
    'qc_all_window': bh_block(*win_qc_all),
    'div_is_window': bh_block(*win_div_is),
    'div_oos_window':bh_block(*win_div_oos),
}

for k, v in bm.items():
    print(f"\n{k}: {v['actual_start']} → {v['actual_end']} ({v['n_years']}yr)")
    print(f"  QQQ: CAGR={v['qqq_bh']['cagr_pct']}% Sharpe={v['qqq_bh']['sharpe']} MaxDD={v['qqq_bh']['maxdd_pct']}%")
    print(f"  SPY: CAGR={v['spy_bh']['cagr_pct']}% Sharpe={v['spy_bh']['sharpe']} MaxDD={v['spy_bh']['maxdd_pct']}%")
    print(f"  70/30 QQQ+GLD: CAGR={v['qqq_gld_7030']['cagr_pct']}% Sharpe={v['qqq_gld_7030']['sharpe']} MaxDD={v['qqq_gld_7030']['maxdd_pct']}%")
    print(f"  60/40 SPY+TLT: CAGR={v['spy_tlt_6040']['cagr_pct']}% Sharpe={v['spy_tlt_6040']['sharpe']} MaxDD={v['spy_tlt_6040']['maxdd_pct']}%")

print("\nDone. Benchmarks saved to variable 'bm'.")

# Save raw benchmark data for use by main script
with open('/tmp/bm_raw.json', 'w') as f:
    json.dump(bm, f, indent=2)
print("Saved /tmp/bm_raw.json")
