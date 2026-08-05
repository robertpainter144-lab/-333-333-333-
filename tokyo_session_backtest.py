#!/usr/bin/env python3
"""
tokyo_session_backtest.py
─────────────────────────
100-strategy sweep — Tokyo morning session (00:00–06:00 UTC = 09:00–15:00 JST)

Instruments : ^N225 (Nikkei 225) — primary; EWJ (iShares MSCI Japan ETF) — fallback
              NOTE: EWJ trades US hours (09:30-16:00 ET), NOT the Tokyo cash session.
              EWJ is a correlated proxy only. ^N225 is the true Tokyo session instrument.
Entry window: 00:00–03:00 UTC  = 09:00–12:00 JST  (morning session only)
Direction   : Long only (bullish bias)
Exit        : ATR trailing stop  OR  force-close at 06:00 UTC (end of session)
Data        : yfinance 1-hour bars, last 2 years

100 strategies = 5 signals × 5 EMA pairs × 4 ATR trail multipliers
Signals:
  1. ema_cross   — fast EMA crosses above slow EMA
  2. rsi_bounce  — RSI drops < 40 then turns up (still < 60)
  3. momentum    — 5-bar price momentum crosses zero upward
  4. bb_bounce   — close was below lower BB, now back above it
  5. vwap_cross  — close crosses above session VWAP (resets 00:00 UTC daily)
"""

import warnings
warnings.filterwarnings('ignore')

import sys
import time
import numpy as np
import pandas as pd
import yfinance as yf
from itertools import product

# ─── CONFIG ──────────────────────────────────────────────────────────────────
SYMBOLS = {
    'N225': '^N225',   # Nikkei 225 — primary (actual Tokyo session)
    'EWJ' : 'EWJ',    # iShares MSCI Japan ETF — fallback proxy (US hours)
}
SES_START  = 0    # UTC hour, inclusive  (09:00 JST)
SES_END    = 6    # UTC hour, exclusive  (15:00 JST) → force close here
ENTRY_END  = 3    # UTC hour  (12:00 JST) — no new entries after this
ATR_PER    = 14
RSI_PER    = 14
BB_PER     = 20
COMM       = 0.0005   # 0.05% per trade (round-trip)
MIN_TRADES = 10       # filter strategies with < 10 trades

# ─── DATA ────────────────────────────────────────────────────────────────────
def fetch(name: str, ticker: str) -> pd.DataFrame:
    print(f'  {name} ({ticker}) ... ', end='', flush=True)
    raw = yf.download(ticker, period='2y', interval='1h',
                      auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.columns = [c.lower() for c in raw.columns]
    raw = raw[['open', 'high', 'low', 'close', 'volume']].dropna()
    raw.index = pd.to_datetime(raw.index, utc=True)
    raw = raw.sort_index()
    span = f'{raw.index[0].date()} → {raw.index[-1].date()}'
    print(f'{len(raw):,} bars  ({span})')
    return raw

# ─── INDICATORS ──────────────────────────────────────────────────────────────
def build_indicators(df: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    d = df.copy()
    c = d['close']

    # EMAs + cross signal
    ef = c.ewm(span=fast, adjust=False).mean()
    es = c.ewm(span=slow,  adjust=False).mean()
    d['ema_fast'] = ef
    d['ema_slow'] = es
    d['sig_ema']  = (ef > es) & (ef.shift() <= es.shift())

    # ATR (Wilder / EWM)
    tr = pd.concat([
        d['high'] - d['low'],
        (d['high'] - c.shift()).abs(),
        (d['low']  - c.shift()).abs(),
    ], axis=1).max(axis=1)
    d['atr'] = tr.ewm(span=ATR_PER, adjust=False).mean()

    # RSI bounce
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(com=RSI_PER - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=RSI_PER - 1, adjust=False).mean()
    rsi   = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    d['rsi'] = rsi
    d['sig_rsi'] = (rsi.shift() < 40) & (rsi > rsi.shift()) & (rsi < 60)

    # 5-bar momentum crossing zero
    mom = c - c.shift(5)
    d['sig_mom'] = (mom.shift() <= 0) & (mom > 0)

    # Bollinger Band lower bounce
    bb_mid = c.rolling(BB_PER).mean()
    bb_lo  = bb_mid - 2 * c.rolling(BB_PER).std()
    d['sig_bb'] = (c.shift() < bb_lo.shift()) & (c >= bb_lo)

    # Session VWAP — resets every UTC calendar day
    d['_day'] = d.index.normalize()
    tp  = (d['high'] + d['low'] + c) / 3
    tpv = tp * d['volume']
    d['vwap'] = (tpv.groupby(d['_day']).cumsum()
                 / d['volume'].groupby(d['_day']).cumsum())
    d['sig_vwap'] = (c.shift() < d['vwap'].shift()) & (c >= d['vwap'])
    d.drop(columns=['_day'], inplace=True)

    return d.dropna(subset=['atr', 'rsi'])

# ─── BACKTEST ENGINE ─────────────────────────────────────────────────────────
SIG_COLS = {
    'ema_cross' : 'sig_ema',
    'rsi_bounce': 'sig_rsi',
    'momentum'  : 'sig_mom',
    'bb_bounce' : 'sig_bb',
    'vwap_cross': 'sig_vwap',
}

def backtest(df_ind: pd.DataFrame, sig_col: str, trail: float) -> list:
    """Returns list of per-trade % returns (net of commission)."""
    hrs  = df_ind.index.hour
    sigs = df_ind[sig_col].to_numpy(dtype=bool)
    cls  = df_ind['close'].to_numpy(dtype=float)
    his  = df_ind['high'].to_numpy(dtype=float)
    los  = df_ind['low'].to_numpy(dtype=float)
    atrs = df_ind['atr'].to_numpy(dtype=float)
    n    = len(df_ind)

    trades: list = []
    in_pos = False
    entry  = hw = stop = 0.0

    for i in range(1, n):
        h      = int(hrs[i])
        in_ses = SES_START <= h < SES_END
        ok_ent = SES_START <= h < ENTRY_END

        if in_pos:
            if his[i] > hw:
                hw = his[i]
            atr_i = atrs[i] if atrs[i] > 0 else cls[i] * 0.01
            stop  = hw - trail * atr_i

            if los[i] <= stop:           # trailing stop hit
                xpx = max(stop, los[i])
                trades.append((xpx - entry) / entry - COMM)
                in_pos = False
                continue

            if not in_ses:               # session ended → force close
                trades.append((cls[i] - entry) / entry - COMM)
                in_pos = False
                continue

        elif in_ses and ok_ent and sigs[i]:   # entry signal
            atr_i  = atrs[i] if atrs[i] > 0 else cls[i] * 0.01
            entry  = cls[i]
            hw     = cls[i]
            stop   = entry - trail * atr_i
            in_pos = True

    if in_pos:   # file ends mid-session
        trades.append((cls[-1] - entry) / entry - COMM)

    return trades

# ─── METRICS ─────────────────────────────────────────────────────────────────
def metrics(trades: list, date_range_years: float = 2.0) -> dict:
    n = len(trades)
    if n < MIN_TRADES:
        return dict(sharpe=-99.0, cagr_pct=0.0, ret_pct=0.0, wr=0.0, n=n, dd=99.0,
                    avg_ret=0.0, profit_factor=0.0)
    a      = np.array(trades, dtype=float)
    eq     = np.cumprod(1 + a)
    ret    = float(eq[-1] - 1)
    wr     = float((a > 0).mean())
    avg    = float(a.mean())
    std    = float(a.std())
    sharpe = avg / std * np.sqrt(252) if std > 0 else 0.0

    peak   = np.maximum.accumulate(eq)
    dd     = float(((peak - eq) / peak).max())

    years  = max(date_range_years, 1 / 252)
    cagr   = (1 + ret) ** (1 / years) - 1

    wins   = a[a > 0].sum()
    losses = -a[a < 0].sum()
    pf     = wins / losses if losses > 0 else 0.0

    return dict(
        sharpe=round(sharpe, 3),
        cagr_pct=round(cagr * 100, 2),
        ret_pct=round(ret * 100, 1),
        wr=round(wr * 100, 1),
        n=n,
        dd=round(dd * 100, 1),
        avg_ret=round(avg * 100, 3),
        profit_factor=round(pf, 2),
    )

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    W = 74
    bar  = '═' * W
    sep  = '─' * W

    print(bar)
    print('  TOKYO MORNING SESSION BACKTEST  — Nikkei / JPY-linked instruments')
    print('  100 Strategies  ·  2-Year Hourly Data  ·  Long Only')
    print('  Primary : ^N225  (Nikkei 225 — actual Tokyo cash session)')
    print('  Fallback: EWJ    (US-listed ETF proxy — NOTE: trades US hours, not Tokyo)')
    print('  Session : 00:00–06:00 UTC  (09:00–15:00 JST)')
    print('  Entry   : 00:00–03:00 UTC  (09:00–12:00 JST morning session)')
    print('  Signals : EMA cross · RSI bounce · Momentum · BB bounce · VWAP cross')
    print(bar)

    # ── Download ──────────────────────────────────────────────────────────────
    print('\n▸ Downloading 1h data:')
    raws: dict[str, pd.DataFrame] = {}
    n225_ok = False
    for name, ticker in SYMBOLS.items():
        try:
            df_tmp = fetch(name, ticker)
            if df_tmp.empty:
                print(f'    {name}: empty — skipped')
                continue
            # For EWJ: note it's a US-hours proxy, mark it
            if name == 'EWJ' and not n225_ok:
                print('  ⚠ EWJ operates US hours (09:30-16:00 ET), NOT the Tokyo session.')
                print('    Using as correlated proxy only — session window will be inactive.')
            if name == 'N225':
                n225_ok = True
            raws[name] = df_tmp
        except Exception as e:
            print(f'    FAILED {name}: {e}')

    # If EWJ is the only one and N225 is unavailable, keep EWJ but flag it
    if not raws:
        print('No data fetched. Exiting.')
        sys.exit(1)
    if not n225_ok:
        print('  NOTE: ^N225 unavailable; EWJ results are a US-hours proxy only.')

    # ── Strategy grid: 5 × 5 × 4 = 100 ──────────────────────────────────────
    signals    = ['ema_cross', 'rsi_bounce', 'momentum', 'bb_bounce', 'vwap_cross']
    ema_pairs  = [(5, 20), (8, 21), (10, 30), (13, 34), (21, 50)]
    trail_atrs = [1.5, 2.0, 2.5, 3.0]
    grid       = list(product(signals, ema_pairs, trail_atrs))  # 100 exactly

    # ── Build indicator frames (cache by sym × EMA pair) ─────────────────────
    print(f'\n▸ Computing indicators ({len(ema_pairs)} EMA pairs × {len(raws)} instruments)...')
    cache: dict[tuple, pd.DataFrame] = {}
    for sym, df in raws.items():
        for ep in ema_pairs:
            key = (sym, ep)
            cache[key] = build_indicators(df, ep[0], ep[1])
            print(f'  {sym} EMA({ep[0]},{ep[1]}): {len(cache[key])} bars after dropna')

    # ── Run all 300 combos ────────────────────────────────────────────────────
    print(f'\n▸ Running {len(grid)} strategies × {len(raws)} instruments '
          f'= {len(grid)*len(raws)} backtests...')
    # Pre-compute date-range years per instrument for CAGR
    date_years = {}
    for sym, df in raws.items():
        days = max((df.index[-1] - df.index[0]).days, 1)
        date_years[sym] = days / 365.25

    t0   = time.time()
    rows = []
    for sid, (sig, ep, tr) in enumerate(grid, 1):
        for sym in raws:
            df_ind = cache.get((sym, ep))
            if df_ind is None or df_ind.empty:
                continue
            sc = SIG_COLS.get(sig)
            if sc is None or sc not in df_ind.columns:
                continue
            t     = backtest(df_ind, sc, tr)
            m     = metrics(t, date_range_years=date_years.get(sym, 2.0))
            rows.append(dict(id=sid, sym=sym, signal=sig,
                             fast=ep[0], slow=ep[1], trail=tr, **m))
        if sid % 25 == 0:
            pct = sid / len(grid) * 100
            print(f'  {sid:3d}/100  ({pct:.0f}%)  —  {time.time()-t0:.1f}s elapsed')

    elapsed = time.time() - t0
    print(f'  Done in {elapsed:.1f}s\n')

    df_res = pd.DataFrame(rows)
    valid  = df_res[df_res['n'] >= MIN_TRADES].copy()

    if valid.empty:
        print('No strategies met the minimum-trades threshold.')
        df_res.to_csv('/Users/quarki3/tokyo_backtest_results.csv', index=False)
        return

    # ── Per-instrument top 5 ─────────────────────────────────────────────────
    col_h = (f"  {'#':<3} {'Signal':<13} {'EMA':<10} {'Trl':<5} "
             f"{'Sharpe':>7} {'CAGR%':>7} {'Ret%':>8} {'WR%':>6} "
             f"{'N':>6} {'DD%':>6} {'PF':>5}")

    print(bar)
    print('  TOP 5 PER INSTRUMENT  (ranked by Sharpe)')
    print(bar)
    for sym in list(raws.keys()):
        sub = valid[valid['sym'] == sym].sort_values('sharpe', ascending=False)
        print(f'\n  ▸ {sym}')
        print(col_h)
        print('  ' + sep[2:])
        for rk, (_, r) in enumerate(sub.head(5).iterrows(), 1):
            ema = f"({r['fast']:2},{r['slow']:2})"
            print(
                f"  {rk:<3} {r['signal']:<13} {ema:<10} {r['trail']:<5} "
                f"{r['sharpe']:>7.3f} {r['cagr_pct']:>+7.2f} {r['ret_pct']:>8.1f} {r['wr']:>6.1f} "
                f"{r['n']:>6} {r['dd']:>6.1f} {r['profit_factor']:>5.2f}"
            )

    # ── Overall top 10 ────────────────────────────────────────────────────────
    top10 = valid.sort_values('sharpe', ascending=False).head(10)
    col_h2 = (f"  {'#':<3} {'Sym':<5} {'Signal':<13} {'EMA':<10} {'Trl':<5} "
              f"{'Sharpe':>7} {'CAGR%':>7} {'Ret%':>8} {'WR%':>6} "
              f"{'N':>6} {'DD%':>6} {'PF':>5}")

    print(f'\n{bar}')
    print('  OVERALL TOP 10  (all instruments, ranked by Sharpe)')
    print(bar)
    print(col_h2)
    print('  ' + sep[2:])
    for rk, (_, r) in enumerate(top10.iterrows(), 1):
        ema = f"({r['fast']:2},{r['slow']:2})"
        print(
            f"  {rk:<3} {r['sym']:<5} {r['signal']:<13} {ema:<10} {r['trail']:<5} "
            f"{r['sharpe']:>7.3f} {r['cagr_pct']:>+7.2f} {r['ret_pct']:>8.1f} {r['wr']:>6.1f} "
            f"{r['n']:>6} {r['dd']:>6.1f} {r['profit_factor']:>5.2f}"
        )

    # ── Winner ────────────────────────────────────────────────────────────────
    w = valid.loc[valid['sharpe'].idxmax()]
    print(f'\n{bar}')
    print('  ★  WINNER')
    print(sep)
    print(f"  Instrument  : {w['sym']}")
    print(f"  Signal      : {w['signal'].upper()}  │  EMA({w['fast']},{w['slow']})  │  ATR Trail {w['trail']}×")
    print(sep)
    print(f"  CAGR        : {w['cagr_pct']:+.2f}%  (annualized)")
    print(f"  Sharpe      : {w['sharpe']:.3f}")
    print(f"  Total Return: {w['ret_pct']:.1f}%  over ~2 years")
    print(f"  Win Rate    : {w['wr']:.1f}%  ({w['n']} trades)")
    print(f"  Avg Trade   : {w['avg_ret']:.3f}%")
    print(f"  Profit Fac. : {w['profit_factor']:.2f}")
    print(f"  Max Drawdown: {w['dd']:.1f}%")
    print(bar)

    # ── Also show per-instrument winners ─────────────────────────────────────
    print('\n  Per-instrument winners:')
    per_inst_results = {}
    for sym in raws:
        sub = valid[valid['sym'] == sym]
        if sub.empty:
            continue
        bw = sub.loc[sub['sharpe'].idxmax()]
        per_inst_results[sym] = bw.to_dict()
        print(f"  {sym:<5}: {bw['signal']:<13} EMA({bw['fast']},{bw['slow']})  "
              f"Trail {bw['trail']}×  →  Sharpe {bw['sharpe']:.3f}  "
              f"CAGR {bw['cagr_pct']:+.2f}%  Ret {bw['ret_pct']:.1f}%  "
              f"WR {bw['wr']:.1f}%  DD {bw['dd']:.1f}%")
    print(bar)

    # ── Save CSV ──────────────────────────────────────────────────────────────
    out = '/Users/quarki3/tokyo_backtest_results.csv'
    df_res.sort_values(['sym', 'sharpe'], ascending=[True, False]).to_csv(out, index=False)
    print(f'\n  Full results → {out}')
    print(f'  Rows: {len(df_res)} total  ({len(valid)} with ≥{MIN_TRADES} trades)\n')

    # ── Write swarm result JSON ───────────────────────────────────────────────
    import json
    from pathlib import Path
    sym_used = list(raws.keys())
    n225_present = 'N225' in sym_used

    # Build headline for JSON summary
    headline_parts = []
    for sym in sym_used:
        if sym in per_inst_results:
            r = per_inst_results[sym]
            headline_parts.append(
                f"{sym}: best={r['signal']} EMA({int(r['fast'])},{int(r['slow'])}) "
                f"CAGR={r['cagr_pct']:+.2f}% Sharpe={r['sharpe']:.3f} MaxDD={r['dd']:+.1f}%"
            )

    limitation = (
        "^N225 was the primary instrument (actual Tokyo cash session 00:00-06:00 UTC). "
        if n225_present else
        "^N225 data unavailable; EWJ (US-hours proxy) used — results do NOT represent the Tokyo session. "
    )

    summary = (
        f"Tokyo session backtest on {', '.join(sym_used)} using 1h yfinance bars, "
        f"100-strategy sweep (5 signals × 5 EMA pairs × 4 ATR trail multiples), session 00:00-06:00 UTC (09:00-15:00 JST). "
        + limitation
        + " | ".join(headline_parts)
        + f". Overall winner: {w['sym']} {w['signal']} EMA({int(w['fast'])},{int(w['slow'])}) "
          f"CAGR={w['cagr_pct']:+.2f}% Sharpe={w['sharpe']:.3f} MaxDD={w['dd']:+.1f}%."
    )

    result = {
        "task"         : "tokyo_session_backtest",
        "summary"      : summary,
        "files_changed": [
            "/Users/quarki3/tokyo_session_backtest.py",
            out,
        ],
        "success"      : True,
    }
    out_json = Path("/Users/quarki3/swarm_results/completed/tokyo_session_backtest.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, indent=2))
    print(f'  Swarm result → {out_json}\n')


if __name__ == '__main__':
    main()
