#!/usr/bin/env python3
"""
walk_forward_20y.py  —  20-Year Walk-Forward Adaptive Backtest
==============================================================
Period : 2005-01-03 → 2025-12-31  (20 full calendar years)
Data   : yfinance daily close, auto-adjusted (splits + dividends)
Universe: 24 assets — all confirmed to have data from 2004

Architecture  (zero look-ahead bias at every layer)
──────────────────────────────────────────────────
1. Tactical Asset Rotation (TAR)
   Monthly: rank assets by backward-looking composite momentum.
   Absolute filter: any asset with negative momentum → replaced by SHY (cash).

2. Regime Overlay
   4 regimes detected from SPY price history only:
     BULL_TREND  – above 200SMA, ADX>20, positive 63D momentum
     BEAR_TREND  – below 200SMA, ADX>20, negative 63D momentum
     HIGH_VOL    – 20D realized vol > 30% ann.
     RANGING     – everything else
   Regime at time T is computed using data through T-1 (.shift(1)).

3. Volatility Targeting
   Each month, scale weights so portfolio vol ≈ 15% ann.
   Computed from trailing 20-day realized vol. Naturally halves
   exposure during crises without predicting them.

4. Walk-Forward Parameter Selection  ← anti-overfitting / self-adapting
   At each of 240 monthly rebalances:
     a. Run 9 vectorized mini-backtests on PRIOR 252 trading days
        (grid: momentum lookback × top-N holdings)
     b. Select combo with highest Sharpe on that trailing window
     c. Apply selected params forward-only for the next ~21 days
   The parameters at each moment are *learned from the past* and
   applied to the future — matching exactly what a live system would do.

Look-ahead enforcement
──────────────────────
• All momentum / indicator signals computed at close of day T
• Trades execute at open of day T+1 (approximated as close T+1 in daily sim)
• Mini-optimizer training window ends strictly before the test month
• No future prices ever accessed for decision-making

Market crises covered
─────────────────────
  2007-09  GFC (SPY -56 %)
  2010-11  Euro sovereign crisis (-20 %)
  2015     China flash crash (-12 %)
  2018 Q4  Rate-fear selloff (-20 %)
  2020     COVID crash (-34 %)
  2022     Rate-shock bear (-25 %)
  2025     Tariff shock (-15 %)
"""

import warnings, sys
warnings.filterwarnings('ignore')

import numpy as np
import pandas as pd
import yfinance as yf
from itertools import product
from pathlib import Path

# ─── Universe ─────────────────────────────────────────────────────────────────
UNIVERSE = [
    # Broad US equity
    'SPY',   # S&P 500
    'QQQ',   # Nasdaq 100
    'IWM',   # Russell 2000 small-cap
    'DIA',   # Dow Jones
    # Bonds
    'TLT',   # 20Y+ Treasury
    'IEF',   # 7-10Y Treasury
    'SHY',   # 1-3Y Treasury (cash proxy)
    'AGG',   # Broad bond aggregate
    # Alternatives
    'GLD',   # Gold  (starts Nov 2004 — NaN handled gracefully)
    # International
    'EFA',   # MSCI EAFE (developed ex-US)
    'EEM',   # Emerging markets
    'EWJ',   # Japan
    'EWZ',   # Brazil
    'FXI',   # China large-cap
    # Real estate
    'VNQ',   # US REITs
    # Sectors (all from 1998)
    'XLE',   # Energy
    'XLK',   # Technology
    'XLF',   # Financials
    'XLV',   # Healthcare
    'XLU',   # Utilities
    'XLI',   # Industrials
    'XLB',   # Materials
    'XLP',   # Consumer staples
    'XLY',   # Consumer discretionary
]
CASH        = 'SHY'
DATA_START  = '2004-01-01'
BT_START    = '2005-01-03'
BT_END      = '2025-12-31'
INIT_EQ     = 100_000.0
COMM_PCT    = 0.001           # 0.1% per rebalance leg (realistic for ETFs)

# Structural constants (not grid-searchable — invariant to 20Y hindsight)
VOL_WINDOW  = 20              # realized-vol estimation window (days)
ADX_WINDOW  = 14
SMA_LONG    = 200
TARGET_VOL  = 0.15            # portfolio vol target (annual)
HIGH_VOL_THR= 0.30            # SPY realized vol above this → HIGH_VOL regime

# Walk-forward grid (searched every month on trailing 252D)
GRID_MOMLB = [63, 126, 252]   # momentum lookback (trading days)
GRID_TOPN  = [3, 4, 5]        # top-N assets to hold
# 9 combos total → fast vectorized mini-backtests, selected fresh each month

CACHE = Path('wf20y_prices.csv')

CRISES = {
    'GFC peak→trough':   ('2007-10-09', '2009-03-09'),
    'Euro crisis':       ('2011-07-07', '2011-10-03'),
    'China flash crash': ('2015-07-20', '2015-08-25'),
    'Q4-2018 selloff':   ('2018-10-03', '2018-12-24'),
    'COVID crash':       ('2020-02-19', '2020-03-23'),
    '2022 rate shock':   ('2022-01-03', '2022-10-12'),
    '2025 tariff shock': ('2025-02-19', '2025-04-08'),
}


# ─── Data ─────────────────────────────────────────────────────────────────────
def load_prices() -> pd.DataFrame:
    if CACHE.exists():
        df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
        if df.index[-1] >= pd.Timestamp.today() - pd.Timedelta(days=3):
            print(f"Loaded {len(df)} days from cache.")
            return df
        print("Cache stale — re-downloading...")
    print(f"Downloading 22Y daily data for {len(UNIVERSE)} assets...")
    raw = yf.download(UNIVERSE, start=DATA_START, end=BT_END,
                      auto_adjust=True, progress=True)['Close']
    raw.to_csv(CACHE)
    return raw


# ─── Indicator helpers ────────────────────────────────────────────────────────
def _ema(s: pd.Series, span: int) -> pd.Series:
    return s.ewm(span=span, min_periods=span).mean()

def _atr(close: pd.Series, period: int = 14) -> pd.Series:
    # Daily data: high = low = close (we only have close from yfinance daily)
    # Approximate ATR as rolling std of returns * sqrt(period) * price
    return close.pct_change().rolling(period).std() * close * np.sqrt(period)

def _adx_approx(close: pd.Series, period: int = 14) -> tuple:
    """
    Directional-movement approximation from close-only data.
    pdi  = rolling N-day % of up-days (normalized 0-100)
    ndi  = rolling N-day % of down-days (normalized 0-100)
    adx  = EMA of |pdi-ndi| / (pdi+ndi+1e-9) * 100
    """
    chg  = close.pct_change()
    up   = chg.clip(lower=0)
    dn   = (-chg).clip(lower=0)
    pdi  = up.rolling(period).mean() / (up.rolling(period).mean() +
                                         dn.rolling(period).mean() + 1e-9) * 100
    ndi  = dn.rolling(period).mean() / (up.rolling(period).mean() +
                                         dn.rolling(period).mean() + 1e-9) * 100
    dx   = ((pdi - ndi).abs() / (pdi + ndi + 1e-9)) * 100
    adx  = dx.ewm(span=period, min_periods=period).mean()
    return adx, pdi, ndi

def build_regime(prices: pd.DataFrame) -> pd.Series:
    spy    = prices['SPY'].ffill()
    sma200 = spy.rolling(SMA_LONG).mean()
    sma50  = spy.rolling(50).mean()
    rv20   = spy.pct_change().rolling(VOL_WINDOW).std() * np.sqrt(252)
    adx, pdi, ndi = _adx_approx(spy, ADX_WINDOW)
    mom63  = spy.pct_change(63)

    # Fast crash detector: SPY >7% below 20-day running high → HIGH_VOL
    spy_20h     = spy.rolling(20).max()
    fast_crash  = (spy / spy_20h - 1) < -0.07

    high_vol = (rv20 > HIGH_VOL_THR) | fast_crash
    # Bear: below 200 OR below 50 with negative momentum
    bear     = (~high_vol) & ((spy < sma200) | (spy < sma50)) & (adx > 20) & (ndi > pdi) & (mom63 < 0)
    bull     = (~high_vol) & (~bear) & (spy > sma200) & (adx > 20) & (pdi > ndi) & (mom63 > 0)

    reg = pd.Series('RANGING', index=spy.index)
    reg[high_vol] = 'HIGH_VOL'
    reg[bull]     = 'BULL_TREND'
    reg[bear]     = 'BEAR_TREND'

    # Shift by 1 — today's regime is computed from yesterday's close
    return reg.shift(1).fillna('RANGING')


# ─── Vectorized mini-backtest (called 9× per month on trailing 252D window) ───
def _mini_backtest(prices_win: pd.DataFrame, mom_lb: int, top_n: int) -> float:
    """
    Vectorized TAR simulation on a trailing window.
    Returns annualized Sharpe.  No regime overlay (kept simple for speed).
    No look-ahead: all signals computed from prices_win which is strictly historical.
    """
    cols = list(prices_win.columns)
    rets = prices_win.pct_change().fillna(0)

    # Monthly rebalance dates within window
    rebal_idx = pd.date_range(prices_win.index[0], prices_win.index[-1], freq='BME')
    rebal_idx = [d for d in rebal_idx if d in prices_win.index]

    # Build weight matrix (zero-initialized)
    W = pd.DataFrame(0.0, index=prices_win.index, columns=cols)

    for i, rb in enumerate(rebal_idx):
        end_rb = rebal_idx[i+1] if i+1 < len(rebal_idx) else prices_win.index[-1]
        rb_loc = prices_win.index.get_loc(rb)

        # 12-1 momentum: exclude last 21 days (short-term reversal avoidance)
        lb_loc  = max(0, rb_loc - mom_lb)
        skip_loc= max(0, rb_loc - 21)
        p0  = prices_win.iloc[lb_loc]
        p1m = prices_win.iloc[skip_loc]   # 1M ago
        mom = (p1m / p0.replace(0, np.nan)) - 1

        # Per-asset trend filter: only hold if in uptrend
        ema100  = prices_win.iloc[:rb_loc+1].ewm(span=100, min_periods=30).mean().iloc[-1]
        in_trend= prices_win.iloc[rb_loc] > ema100

        # Filter + rank
        pos = mom[(mom >= 0) & in_trend].drop(CASH, errors='ignore').dropna()
        if pos.empty:
            selected = [CASH]
        else:
            selected = pos.sort_values(ascending=False).head(top_n).index.tolist()

        # Assign equal weight for the hold period (rb+1 through end_rb)
        w  = 1.0 / len(selected)
        hold_mask = (prices_win.index > rb) & (prices_win.index <= end_rb)
        for t in selected:
            if t in W.columns:
                W.loc[hold_mask, t] = w

    port_rets = (rets * W).sum(axis=1)
    dr = port_rets.dropna()
    if len(dr) < 10 or dr.std() < 1e-9:
        return -99.0
    return dr.mean() / dr.std() * np.sqrt(252)


def select_params(prices: pd.DataFrame, as_of_loc: int, train_days: int = 252):
    """
    Select best (mom_lb, top_n) by running mini-backtests on
    the trailing train_days window ending at as_of_loc-1.
    Returns (mom_lb, top_n) — never touches future data.
    """
    start_loc = max(0, as_of_loc - train_days)
    window    = prices.iloc[start_loc:as_of_loc]
    if len(window) < 63:
        return 252, 4   # default before enough history

    best_sh, best_p = -999.0, (252, 4)
    for mom_lb, top_n in product(GRID_MOMLB, GRID_TOPN):
        sh = _mini_backtest(window, mom_lb, top_n)
        if sh > best_sh:
            best_sh, best_p = sh, (mom_lb, top_n)
    return best_p


# ─── Portfolio construction ───────────────────────────────────────────────────
def build_weights(prices_to_date: pd.DataFrame, regime: str,
                  mom_lb: int, top_n: int) -> dict:
    """
    All inputs are data up to (and including) the rebalance date.
    Zero look-ahead by construction.
    """
    cols = list(prices_to_date.columns)
    n    = len(prices_to_date)
    p0   = prices_to_date.iloc[-min(mom_lb, n)]
    p1m  = prices_to_date.iloc[-min(21, n)]   # 1M ago (for momentum skip)
    p1   = prices_to_date.iloc[-1]

    # 12-1 momentum: 12M return excluding last month (avoids short-term reversal)
    mom  = (p1m / p0.replace(0, np.nan) - 1).dropna()

    # Per-asset trend filter: only consider if price > own 100D EMA
    ema100 = prices_to_date.ewm(span=100, min_periods=50).mean().iloc[-1]
    in_trend = p1 > ema100

    # Absolute filter: negative momentum → cash; and must be in uptrend
    pos  = mom[(mom >= 0) & in_trend].drop(CASH, errors='ignore')

    # ── Regime overlay ────────────────────────────────────────────────────────
    if regime == 'HIGH_VOL':
        safe = {CASH: 0.5}
        for t in ['TLT', 'GLD']:
            if t in cols:
                safe[t] = 0.25
        return safe

    if regime == 'BEAR_TREND':
        defensive = ['TLT', 'GLD', 'IEF', 'AGG', CASH]
        # From momentum-positive set, prefer defensive assets
        def_pos  = [t for t in pos.sort_values(ascending=False).index if t in defensive]
        off_pos  = [t for t in pos.sort_values(ascending=False).index if t not in defensive]
        # Force at least half to defensive; fill rest with top momentum
        n_def    = max(1, top_n // 2 + (top_n % 2))
        selected = (def_pos[:n_def] + off_pos)[:top_n]
        # If we couldn't find enough momentum-positive defensives, add TLT/GLD by force
        while len(selected) < 2:
            for fallback in ['TLT', 'GLD', CASH]:
                if fallback not in selected and fallback in cols:
                    selected.append(fallback)
                    break
            else:
                break
        return {t: 1.0/len(selected) for t in selected}

    # BULL_TREND or RANGING
    if pos.empty:
        return {CASH: 1.0}
    selected = pos.sort_values(ascending=False).head(top_n).index.tolist()
    return {t: 1.0/len(selected) for t in selected}


def apply_vol_target(weights: dict, prices_to_date: pd.DataFrame) -> dict:
    """Scale weights toward TARGET_VOL. Defensive floor at 20 % exposure."""
    if not weights:
        return weights

    recent = prices_to_date.tail(VOL_WINDOW + 1).pct_change().dropna()
    vols   = {t: recent[t].std() * np.sqrt(252) if t in recent.columns else 0.12
              for t in weights}
    port_vol = sum(w * vols.get(t, 0.12) for t, w in weights.items())
    if port_vol < 1e-6:
        return weights

    scale = np.clip(TARGET_VOL / port_vol, 0.20, 1.50)
    return {t: w * scale for t, w in weights.items()}


# ─── Main back-test loop ──────────────────────────────────────────────────────
def run_backtest(prices: pd.DataFrame, regime: pd.Series):
    rets = prices.pct_change().fillna(0)

    # Monthly rebalance schedule (business month-end) within backtest window
    rebal_all = pd.date_range(BT_START, BT_END, freq='BME')
    rebal_all = [d for d in rebal_all if d in prices.index]

    equity      = INIT_EQ
    weights     = {CASH: 1.0}
    records     = []
    rebal_log   = []

    n = len(rebal_all)
    print(f"\nRunning 20Y walk-forward backtest ({BT_START} → {BT_END})")
    print(f"{n} monthly rebalances · 9 mini-backtests each → {9*n} training runs")
    print("─" * 60)

    for i, rb in enumerate(rebal_all):
        if i % 12 == 0:
            pct = i / n * 100
            print(f"  [{pct:4.0f}%]  {rb.date()}  eq=${equity:>10,.0f}  "
                  f"regime={regime.get(rb, '?'):12s}  "
                  f"holdings={list(weights.keys())}")
            sys.stdout.flush()

        end_rb = rebal_all[i+1] if i+1 < n else pd.Timestamp(BT_END)

        # ── Simulate daily P&L for current hold period ───────────────────────
        period_idx  = (rets.index >= rb) & (rets.index <= end_rb)
        period_rets = rets.loc[period_idx]

        for date, row in period_rets.iterrows():
            dr = sum(weights.get(t, 0.0) * row.get(t, 0.0) for t in weights)
            equity *= (1 + dr)
            records.append({
                'date'   : date,
                'equity' : equity,
                'day_ret': dr,
                'regime' : regime.get(date, 'RANGING'),
            })

        # ── Rebalance: select params on trailing 252D, then build new weights ─
        rb_loc  = prices.index.get_loc(rb)
        mom_lb, top_n = select_params(prices, rb_loc)
        cur_reg = regime.get(rb, 'RANGING')

        prices_to_rb = prices.iloc[:rb_loc + 1]
        new_w = build_weights(prices_to_rb, cur_reg, mom_lb, top_n)
        new_w = apply_vol_target(new_w, prices_to_rb)

        # Commission on turnover
        all_t    = set(weights) | set(new_w)
        turnover = sum(abs(new_w.get(t, 0) - weights.get(t, 0)) for t in all_t)
        equity  *= (1 - turnover * COMM_PCT)

        rebal_log.append({
            'date'   : rb,
            'regime' : cur_reg,
            'mom_lb' : mom_lb,
            'top_n'  : top_n,
            'holdings': ', '.join(new_w.keys()),
            'equity' : round(equity, 2),
        })
        weights = new_w

    df_eq    = pd.DataFrame(records).set_index('date')
    df_rebal = pd.DataFrame(rebal_log)
    return df_eq, df_rebal


# ─── Metrics ─────────────────────────────────────────────────────────────────
def _cagr(eq): return (eq.iloc[-1]/eq.iloc[0])**(252/len(eq)) - 1
def _sharpe(r): return (r.mean()/r.std()*np.sqrt(252)) if r.std() > 0 else 0
def _sortino(r):
    d = r[r<0]; ds = d.std()*np.sqrt(252) if len(d)>0 else 0.001
    return r.mean()*252/ds
def _maxdd(eq): return ((eq - eq.expanding().max())/eq.expanding().max()).min()*100
def _calmar(eq): dd = abs(_maxdd(eq))/100; return _cagr(eq)/dd if dd else 0


def print_report(df_eq: pd.DataFrame, prices: pd.DataFrame, df_rebal: pd.DataFrame):
    eq  = df_eq['equity']
    dr  = df_eq['day_ret']

    spy_px = prices['SPY'].reindex(df_eq.index).ffill()
    spy_r  = spy_px.pct_change().fillna(0)
    spy_eq = INIT_EQ * (1 + spy_r).cumprod()

    sep = "═" * 72

    print(f"\n{sep}")
    print("  20-YEAR WALK-FORWARD ADAPTIVE BACKTEST  (2005–2025)")
    print(sep)
    print(f"\n  {'Metric':<38}  {'Strategy':>10}  {'SPY B&H':>10}")
    print(f"  {'─'*38}  {'─'*10}  {'─'*10}")

    def row(label, sv, bv, fmt='.2f'):
        print(f"  {label:<38}  {sv:>10{fmt}}  {bv:>10{fmt}}")

    row('CAGR (%)',           _cagr(eq)*100,     _cagr(spy_eq)*100)
    row('Sharpe Ratio',       _sharpe(dr),        _sharpe(spy_r))
    row('Sortino Ratio',      _sortino(dr),       _sortino(spy_r))
    row('Max Drawdown (%)',   _maxdd(eq),         _maxdd(spy_eq))
    row('Calmar Ratio',       _calmar(eq),        _calmar(spy_eq))
    row('Total Return (%)',   (eq.iloc[-1]/INIT_EQ-1)*100,
                              (spy_eq.iloc[-1]/INIT_EQ-1)*100)
    print(f"  {'Final Equity ($100k start)':<38}  ${eq.iloc[-1]:>9,.0f}  ${spy_eq.iloc[-1]:>9,.0f}")

    # ── Year by year ─────────────────────────────────────────────────────────
    print(f"\n  {'─'*72}")
    print("  YEAR-BY-YEAR PERFORMANCE")
    print(f"  {'Year':6}  {'Strategy':>10}  {'SPY':>8}  {'MaxDD':>8}  {'Calmar':>7}  {'Regime'}")
    print(f"  {'─'*6}  {'─'*10}  {'─'*8}  {'─'*8}  {'─'*7}  {'─'*12}")

    for yr in sorted(df_eq.index.year.unique()):
        m   = df_eq.index.year == yr
        yeq = df_eq.loc[m, 'equity']
        ydr = df_eq.loc[m, 'day_ret']
        ys  = spy_r[m]
        if len(yeq) < 2: continue
        yret  = (yeq.iloc[-1]/yeq.iloc[0]-1)*100
        sret  = ((1+ys).prod()-1)*100
        ydd   = _maxdd(yeq)
        ycal  = _calmar(yeq) if abs(_maxdd(yeq)) > 0.1 else 0
        top_r = df_eq.loc[m,'regime'].mode()[0]
        beat  = "✓" if yret > sret else " "
        print(f"  {yr:6d}  {yret:>9.1f}%  {sret:>7.1f}%  {ydd:>7.1f}%  {ycal:>7.2f}  {top_r} {beat}")

    # ── Crisis windows ────────────────────────────────────────────────────────
    print(f"\n  {'─'*72}")
    print("  CRISIS-PERIOD PERFORMANCE")
    print(f"  {'Crisis':30}  {'Strategy':>10}  {'SPY':>8}  {'Advantage':>10}")
    print(f"  {'─'*30}  {'─'*10}  {'─'*8}  {'─'*10}")

    for name, (s, e) in CRISES.items():
        try:
            s2 = df_eq.index[df_eq.index >= pd.Timestamp(s)][0]
            e2 = df_eq.index[df_eq.index <= pd.Timestamp(e)][-1]
            strat = (df_eq.loc[e2,'equity']/df_eq.loc[s2,'equity']-1)*100
            spy_d = (spy_eq.loc[e2]/spy_eq.loc[s2]-1)*100
            adv   = strat - spy_d
            print(f"  {name:30}  {strat:>9.1f}%  {spy_d:>7.1f}%  {adv:>+10.1f}%")
        except Exception:
            print(f"  {name:30}  {'N/A':>9}")

    # ── By regime ────────────────────────────────────────────────────────────
    print(f"\n  {'─'*72}")
    print("  PERFORMANCE BY MARKET REGIME")
    print(f"  {'Regime':14}  {'Days':>6}  {'Ann %':>8}  {'Sharpe':>8}  {'MaxDD':>8}")
    print(f"  {'─'*14}  {'─'*6}  {'─'*8}  {'─'*8}  {'─'*8}")

    for reg in ['BULL_TREND','BEAR_TREND','HIGH_VOL','RANGING']:
        m = df_eq['regime'] == reg
        if m.sum() < 20: continue
        rd = df_eq.loc[m,'day_ret']
        req= df_eq.loc[m,'equity']
        print(f"  {reg:14}  {m.sum():>6d}  {rd.mean()*252*100:>7.1f}%  "
              f"{_sharpe(rd):>8.2f}  {_maxdd(req):>7.1f}%")

    # ── Walk-forward param diversity ─────────────────────────────────────────
    print(f"\n  {'─'*72}")
    print("  WALK-FORWARD PARAMETER ADAPTATION (sample)")
    print(f"  {'Date':12}  {'mom_lb':>8}  {'top_n':>6}  {'Regime':>12}  Holdings")
    print(f"  {'─'*12}  {'─'*8}  {'─'*6}  {'─'*12}  {'─'*28}")
    sample = df_rebal.iloc[::12]   # every 12th month
    for _, row2 in sample.iterrows():
        h = row2['holdings'][:28]
        print(f"  {str(row2['date'])[:10]:12}  {row2['mom_lb']:>8}  {row2['top_n']:>6}  "
              f"{row2['regime']:>12}  {h}")

    print(f"\n{sep}\n")


def main():
    prices = load_prices()

    # Filter to confirmed available tickers
    avail  = [t for t in UNIVERSE if t in prices.columns]
    miss   = [t for t in UNIVERSE if t not in prices.columns]
    if miss:
        print(f"  Not in download: {miss} (skipped)")
    prices = prices[avail].ffill()

    print(f"  Universe: {avail}")
    print(f"  Data: {prices.index[0].date()} → {prices.index[-1].date()} | "
          f"{len(prices)} days | {len(avail)} assets")

    regime = build_regime(prices)

    print(f"\n  Regime distribution:")
    for r, cnt in regime.value_counts().items():
        print(f"    {r:14s}: {cnt:5d} days ({cnt/len(regime)*100:.1f}%)")

    df_eq, df_rebal = run_backtest(prices, regime)

    print_report(df_eq, prices, df_rebal)

    # Save outputs
    df_eq.to_csv('wf20y_equity_curve.csv')
    df_rebal.to_csv('wf20y_rebalance_log.csv', index=False)

    # Full comparison CSV for charting
    spy_px = prices['SPY'].reindex(df_eq.index).ffill()
    spy_r  = spy_px.pct_change().fillna(0)
    pd.DataFrame({
        'strategy_equity': df_eq['equity'],
        'spy_equity':      INIT_EQ * (1 + spy_r).cumprod(),
        'regime':          df_eq['regime'],
        'day_return':      df_eq['day_ret'],
    }).to_csv('wf20y_comparison.csv')

    print("Saved: wf20y_equity_curve.csv")
    print("Saved: wf20y_rebalance_log.csv")
    print("Saved: wf20y_comparison.csv")


if __name__ == '__main__':
    main()
