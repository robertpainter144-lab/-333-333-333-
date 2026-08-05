#!/usr/bin/env python3
"""
bb_rsi_backtest.py
──────────────────
5-year sweep: Bollinger Bands(20,2) + RSI(14) bullish strategy
US equities + Japan/Tokyo markets  ·  Daily bars  ·  Long-only

Signals (3 modes):
  bb_bounce   — close touched/below lower BB, now back above  +  RSI < thresh & turning up
  bb_breakout — close crosses above SMA20 from below  +  RSI > 50  +  bands expanding
  bb_both     — either signal fires

Parameter sweep (108 combos × 17 symbols = 1,836 backtests):
  rsi_thresh : 35 / 40 / 45
  atr_trail  : 1.5 / 2.0 / 2.5 / 3.0×
  max_hold   : 10 / 15 / 20 bars

Entry : next-bar open (signal fires at close, enter at tomorrow's open — realistic)
Exit  : ATR trailing stop  |  close ≥ upper BB (target hit)  |  RSI ≥ 75  |  max-hold
"""

import warnings
warnings.filterwarnings('ignore')

import sys
import time
import numpy as np
import pandas as pd
import yfinance as yf
from itertools import product

# ─── UNIVERSE ─────────────────────────────────────────────────────────────────
US_SYMBOLS = {
    'SPY'  : 'SPY',
    'QQQ'  : 'QQQ',
    'TQQQ' : 'TQQQ',
    'IWM'  : 'IWM',
    'AAPL' : 'AAPL',
    'MSFT' : 'MSFT',
    'NVDA' : 'NVDA',
    'AMZN' : 'AMZN',
    'TSLA' : 'TSLA',
    'META' : 'META',
    'XLE'  : 'XLE',
    'GLD'  : 'GLD',
}

JP_SYMBOLS = {
    'EWJ'     : 'EWJ',       # Japan broad ETF (USD-denominated, most reliable)
    'Toyota'  : '7203.T',
    'Sony'    : '6758.T',
    'SoftBnk' : '9984.T',
    'Keyence' : '6861.T',
}

ALL_SYMBOLS = {**US_SYMBOLS, **JP_SYMBOLS}

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BB_PER      = 20
BB_STD_MULT = 2.0
RSI_PER     = 14
ATR_PER     = 14
EXP_BARS    = 5       # bars back to compare BB width for "expanding" check
COMM        = 0.001   # 0.1% round-trip incl. slippage
MIN_TRADES  = 15      # minimum trades to score a strategy

RSI_THRESHOLDS = [35, 40, 45]
ATR_TRAILS     = [1.5, 2.0, 2.5, 3.0]
MAX_HOLDS      = [10, 15, 20]
SIGNALS        = ['bb_bounce', 'bb_breakout', 'bb_both']

SIG_COLS = {
    'bb_bounce'  : 'sig_bounce',
    'bb_breakout': 'sig_breakout',
    'bb_both'    : 'sig_both',
}


# ─── DATA ─────────────────────────────────────────────────────────────────────
def fetch(name: str, ticker: str) -> pd.DataFrame | None:
    print(f'  {name:10s} ({ticker:8s}) ... ', end='', flush=True)
    try:
        raw = yf.download(ticker, period='5y', interval='1d',
                          auto_adjust=True, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw.columns = [c.lower() for c in raw.columns]
        raw = raw[['open', 'high', 'low', 'close', 'volume']].dropna()
        raw.index = pd.to_datetime(raw.index)
        raw = raw.sort_index()
        if len(raw) < 200:
            print(f'only {len(raw)} bars — SKIP')
            return None
        span = f'{raw.index[0].date()} → {raw.index[-1].date()}'
        print(f'{len(raw):,} bars  ({span})')
        return raw
    except Exception as e:
        print(f'FAILED: {e}')
        return None


# ─── INDICATORS ───────────────────────────────────────────────────────────────
def build_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    c = d['close']

    mid   = c.rolling(BB_PER).mean()
    std_r = c.rolling(BB_PER).std()
    d['bb_mid']   = mid
    d['bb_upper'] = mid + BB_STD_MULT * std_r
    d['bb_lower'] = mid - BB_STD_MULT * std_r
    d['bb_std']   = std_r

    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(com=RSI_PER - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=RSI_PER - 1, adjust=False).mean()
    d['rsi'] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    tr = pd.concat([
        d['high'] - d['low'],
        (d['high'] - c.shift()).abs(),
        (d['low']  - c.shift()).abs(),
    ], axis=1).max(axis=1)
    d['atr'] = tr.ewm(span=ATR_PER, adjust=False).mean()

    return d.dropna(subset=['bb_mid', 'rsi', 'atr'])


def add_signals(df: pd.DataFrame, rsi_thresh: float) -> pd.DataFrame:
    d   = df.copy()
    c   = d['close']
    rsi = d['rsi']
    mid = d['bb_mid']
    lo  = d['bb_lower']
    std = d['bb_std']

    # BB lower bounce: was at/below lower band, now back above it, RSI was low & rising
    was_below = c.shift(1) <= lo.shift(1)
    back_up   = c >= lo
    rsi_low   = rsi.shift(1) < rsi_thresh
    rsi_up    = rsi > rsi.shift(1)
    d['sig_bounce'] = was_below & back_up & rsi_low & rsi_up

    # BB midline breakout: crosses above SMA20, RSI > 50, bands expanding
    cross_mid  = (c.shift(1) < mid.shift(1)) & (c >= mid)
    rsi_bull   = rsi > 50
    expanding  = std > std.shift(EXP_BARS)
    d['sig_breakout'] = cross_mid & rsi_bull & expanding

    d['sig_both'] = d['sig_bounce'] | d['sig_breakout']
    return d


# ─── BACKTEST ENGINE ──────────────────────────────────────────────────────────
def backtest(df: pd.DataFrame, sig_col: str,
             atr_trail: float, max_hold: int) -> list:
    opens  = df['open'].to_numpy(float)
    highs  = df['high'].to_numpy(float)
    lows   = df['low'].to_numpy(float)
    closes = df['close'].to_numpy(float)
    atrs   = df['atr'].to_numpy(float)
    uppers = df['bb_upper'].to_numpy(float)
    rsis   = df['rsi'].to_numpy(float)
    sigs   = df[sig_col].to_numpy(bool)
    dates  = df.index
    n      = len(df)

    trades     = []
    in_pos     = False
    entry_px   = hw = stop = 0.0
    hold_count = 0
    entry_date = None

    for i in range(1, n - 1):   # need i+1 for next-open entry
        if in_pos:
            hold_count += 1
            if highs[i] > hw:
                hw = highs[i]
            atr_i = atrs[i] if atrs[i] > 0 else closes[i] * 0.01
            stop  = hw - atr_trail * atr_i

            exit_px = None
            if lows[i] <= stop:
                exit_px = max(stop, lows[i])
            elif closes[i] >= uppers[i]:        # upper-BB target hit
                exit_px = closes[i]
            elif rsis[i] >= 75:                 # overbought exit
                exit_px = closes[i]
            elif hold_count >= max_hold:        # time-based exit
                exit_px = closes[i]

            if exit_px is not None:
                ret = (exit_px - entry_px) / entry_px - COMM
                trades.append({'ret': ret, 'date': entry_date, 'hold': hold_count})
                in_pos = False

        elif sigs[i]:    # signal on close of bar i → enter at open of bar i+1
            atr_i    = atrs[i] if atrs[i] > 0 else closes[i] * 0.01
            entry_px = opens[i + 1]
            if entry_px <= 0:
                continue
            hw         = entry_px
            stop       = entry_px - atr_trail * atr_i
            in_pos     = True
            hold_count = 0
            entry_date = dates[i + 1]

    if in_pos:
        ret = (closes[-1] - entry_px) / entry_px - COMM
        trades.append({'ret': ret, 'date': entry_date, 'hold': hold_count})

    return trades


# ─── METRICS ──────────────────────────────────────────────────────────────────
def metrics(trades: list) -> dict:
    n = len(trades)
    if n < MIN_TRADES:
        return dict(sharpe=-99.0, ret_pct=0.0, wr=0.0, n=n, dd=99.0,
                    avg_ret=0.0, profit_factor=0.0, avg_hold=0.0)
    a  = np.array([t['ret'] for t in trades], float)
    eq = np.cumprod(1 + a)

    ret    = float(eq[-1] - 1)
    wr     = float((a > 0).mean())
    avg    = float(a.mean())
    std    = float(a.std())
    sharpe = avg / std * np.sqrt(252) if std > 0 else 0.0

    peak = np.maximum.accumulate(eq)
    dd   = float(((peak - eq) / peak).max())

    wins   = a[a > 0].sum()
    losses = -a[a < 0].sum()
    pf     = wins / losses if losses > 0 else 0.0
    avg_h  = float(np.mean([t['hold'] for t in trades]))

    return dict(
        sharpe=round(sharpe, 3),
        ret_pct=round(ret * 100, 1),
        wr=round(wr * 100, 1),
        n=n,
        dd=round(dd * 100, 1),
        avg_ret=round(avg * 100, 3),
        profit_factor=round(pf, 2),
        avg_hold=round(avg_h, 1),
    )


def year_by_year(trades: list) -> dict:
    by_yr: dict[int, list] = {}
    for t in trades:
        if t['date'] is None:
            continue
        yr = pd.Timestamp(t['date']).year
        by_yr.setdefault(yr, []).append(t['ret'])
    out = {}
    for yr, rets in sorted(by_yr.items()):
        a  = np.array(rets, float)
        eq = np.cumprod(1 + a)
        pk = np.maximum.accumulate(eq)
        dd = float(((pk - eq) / pk).max()) if len(eq) > 1 else 0.0
        out[yr] = {'ret': round((eq[-1] - 1) * 100, 1), 'n': len(rets),
                   'dd': round(dd * 100, 1)}
    return out


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    W   = 80
    bar = '═' * W
    sep = '─' * W

    grid  = list(product(SIGNALS, RSI_THRESHOLDS, ATR_TRAILS, MAX_HOLDS))
    total_combos = len(grid) * len(ALL_SYMBOLS)

    print(bar)
    print('  BB(20,2) + RSI(14) BULLISH SCREENER — 5-YEAR BACKTEST')
    print('  Signals : BB Lower Bounce  |  BB Midline Breakout  |  Either')
    print(f'  Sweep   : {len(SIGNALS)} signals × {len(RSI_THRESHOLDS)} RSI thresholds × '
          f'{len(ATR_TRAILS)} ATR trails × {len(MAX_HOLDS)} max-holds = {len(grid)} combos')
    print(f'  Universe: {len(US_SYMBOLS)} US  +  {len(JP_SYMBOLS)} Japan  =  {len(ALL_SYMBOLS)} symbols')
    print(f'  Total   : {len(grid)} × {len(ALL_SYMBOLS)} = {total_combos} backtests')
    print(bar)

    # ── Download ──────────────────────────────────────────────────────────────
    print('\n▸ Downloading 5y daily data:')
    raws: dict[str, pd.DataFrame] = {}
    for name, ticker in ALL_SYMBOLS.items():
        df = fetch(name, ticker)
        if df is not None:
            raws[name] = df

    print(f'\n  {len(raws)}/{len(ALL_SYMBOLS)} symbols loaded.\n')
    if not raws:
        sys.exit('No data fetched.')

    # ── Indicators ────────────────────────────────────────────────────────────
    print('▸ Computing base indicators...')
    ind: dict[str, pd.DataFrame] = {}
    for name, df in raws.items():
        ind[name] = build_indicators(df)
        print(f'  {name:10s}: {len(ind[name]):,} bars after warmup')

    # ── Signal cache by (name, rsi_thresh) ───────────────────────────────────
    print('\n▸ Pre-computing signals...')
    sig_cache: dict[tuple, pd.DataFrame] = {}
    for name in ind:
        for rsi_t in RSI_THRESHOLDS:
            sig_cache[(name, rsi_t)] = add_signals(ind[name], rsi_t)
    print(f'  {len(sig_cache)} signal frames cached.\n')

    # ── Sweep ─────────────────────────────────────────────────────────────────
    real_total = len(grid) * len(raws)
    print(f'▸ Running {real_total} backtests...')
    t0   = time.time()
    rows = []
    done = 0
    milestone = max(1, real_total // 10)

    for sig, rsi_t, atr_t, mh in grid:
        sc = SIG_COLS[sig]
        for name in raws:
            df_s   = sig_cache[(name, rsi_t)]
            trades = backtest(df_s, sc, atr_t, mh)
            m      = metrics(trades)
            mkt    = 'JP' if name in JP_SYMBOLS else 'US'
            rows.append(dict(sym=name, market=mkt, signal=sig,
                             rsi_t=rsi_t, atr_t=atr_t, max_hold=mh,
                             trades=trades, **m))
            done += 1
            if done % milestone == 0:
                print(f'  {done:5d}/{real_total}  ({done/real_total*100:.0f}%)'
                      f'  —  {time.time()-t0:.1f}s')

    print(f'  Done in {time.time()-t0:.1f}s\n')

    # Build display df (drop raw trade lists)
    df_res = pd.DataFrame([{k: v for k, v in r.items() if k != 'trades'} for r in rows])
    valid  = df_res[df_res['n'] >= MIN_TRADES].copy()

    if valid.empty:
        print('No strategies met the minimum-trades threshold.')
        df_res.to_csv('/Users/quarki3/bb_rsi_backtest_results.csv', index=False)
        return

    # ── Per-symbol top 5 ─────────────────────────────────────────────────────
    col_h = (f"  {'#':<3} {'Signal':<13} {'RSIt':>5} {'Trl':>5} {'MH':>4} "
             f"{'Sharpe':>7} {'Ret%':>8} {'WR%':>6} "
             f"{'N':>5} {'DD%':>6} {'PF':>5} {'AvgH':>5}")

    print(bar)
    print('  TOP 5 PER SYMBOL  (Sharpe-ranked)')
    print(bar)
    for name in list(raws.keys()):
        sub = valid[valid['sym'] == name].sort_values('sharpe', ascending=False)
        if sub.empty:
            continue
        mkt = 'JP' if name in JP_SYMBOLS else 'US'
        print(f'\n  ▸ {name} [{mkt}]')
        print(col_h)
        print('  ' + sep[2:])
        for rk, (_, r) in enumerate(sub.head(5).iterrows(), 1):
            print(
                f"  {rk:<3} {r['signal']:<13} {r['rsi_t']:>5} {r['atr_t']:>5} {r['max_hold']:>4} "
                f"{r['sharpe']:>7.3f} {r['ret_pct']:>8.1f} {r['wr']:>6.1f} "
                f"{r['n']:>5} {r['dd']:>6.1f} {r['profit_factor']:>5.2f} {r['avg_hold']:>5.1f}"
            )

    # ── Overall top 10 ────────────────────────────────────────────────────────
    top10  = valid.sort_values('sharpe', ascending=False).head(10)
    col_h2 = (f"  {'#':<3} {'Sym':<10} {'Mkt':>4} {'Signal':<13} {'RSIt':>5} "
              f"{'Trl':>5} {'MH':>4} {'Sharpe':>7} {'Ret%':>8} {'WR%':>6} "
              f"{'N':>5} {'DD%':>6} {'PF':>5}")

    print(f'\n{bar}')
    print('  OVERALL TOP 10  (all symbols, Sharpe-ranked)')
    print(bar)
    print(col_h2)
    print('  ' + sep[2:])
    for rk, (_, r) in enumerate(top10.iterrows(), 1):
        print(
            f"  {rk:<3} {r['sym']:<10} {r['market']:>4} {r['signal']:<13} "
            f"{r['rsi_t']:>5} {r['atr_t']:>5} {r['max_hold']:>4} "
            f"{r['sharpe']:>7.3f} {r['ret_pct']:>8.1f} {r['wr']:>6.1f} "
            f"{r['n']:>5} {r['dd']:>6.1f} {r['profit_factor']:>5.2f}"
        )

    # ── US vs Japan best ──────────────────────────────────────────────────────
    print(f'\n{bar}')
    print('  BEST STRATEGY BY MARKET')
    print(bar)
    for mkt in ['US', 'JP']:
        sub = valid[valid['market'] == mkt].sort_values('sharpe', ascending=False)
        if sub.empty:
            print(f'\n  ▸ {mkt}: no valid strategies')
            continue
        bw = sub.iloc[0]
        print(f'\n  ▸ Best {mkt}  →  {bw["sym"]}')
        print(f"    Signal  : {bw['signal'].upper()}")
        print(f"    Params  : RSI<{bw['rsi_t']}  Trail={bw['atr_t']}×ATR  MaxHold={bw['max_hold']}d")
        print(f"    Sharpe  : {bw['sharpe']:.3f}   Return: {bw['ret_pct']:.1f}%   WR: {bw['wr']:.1f}%")
        print(f"    Max DD  : {bw['dd']:.1f}%   PF: {bw['profit_factor']:.2f}   Trades: {bw['n']}")

    # ── Winner + year-by-year ─────────────────────────────────────────────────
    w_row    = valid.loc[valid['sharpe'].idxmax()]
    w_trades = next(
        r['trades'] for r in rows
        if (r['sym'] == w_row['sym'] and r['signal'] == w_row['signal'] and
            r['rsi_t'] == w_row['rsi_t'] and r['atr_t'] == w_row['atr_t'] and
            r['max_hold'] == w_row['max_hold'])
    )

    print(f'\n{bar}')
    print('  ★  OVERALL WINNER')
    print(sep)
    print(f"  Symbol  : {w_row['sym']}  [{w_row['market']}]")
    print(f"  Signal  : {w_row['signal'].upper()}")
    print(f"  Params  : RSI thresh={w_row['rsi_t']}  ATR trail={w_row['atr_t']}×  "
          f"Max hold={w_row['max_hold']} days")
    print(sep)
    print(f"  Sharpe       : {w_row['sharpe']:.3f}")
    print(f"  Total Return : {w_row['ret_pct']:.1f}%  over 5 years")
    print(f"  Win Rate     : {w_row['wr']:.1f}%  ({w_row['n']} trades)")
    print(f"  Avg Hold     : {w_row['avg_hold']:.1f} days")
    print(f"  Max Drawdown : {w_row['dd']:.1f}%")
    print(f"  Profit Factor: {w_row['profit_factor']:.2f}")
    print(f"  Avg Trade    : {w_row['avg_ret']:.3f}%")

    yby = year_by_year(w_trades)
    if yby:
        print(f'\n  Year-by-year ({w_row["sym"]} — {w_row["signal"]}):')
        print(f"  {'Year':>6}  {'Return%':>8}  {'Trades':>7}  {'MaxDD%':>7}")
        print('  ' + '─' * 34)
        for yr, d in yby.items():
            print(f"  {yr:>6}  {d['ret']:>8.1f}  {d['n']:>7}  {d['dd']:>7.1f}")
    print(bar)

    # ── Save ──────────────────────────────────────────────────────────────────
    out = '/Users/quarki3/bb_rsi_backtest_results.csv'
    df_res.sort_values(['sym', 'sharpe'], ascending=[True, False]).to_csv(out, index=False)
    print(f'\n  Full results → {out}')
    print(f'  Rows: {len(df_res)} total  ({len(valid)} with ≥{MIN_TRADES} trades)\n')


if __name__ == '__main__':
    main()
