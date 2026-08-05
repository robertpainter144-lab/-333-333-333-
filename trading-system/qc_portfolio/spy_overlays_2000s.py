"""
Regime-robustness test: SMA200 tactical vs SPY buy-and-hold, 2000-2011.

This window captures two real bear markets (dot-com 2000-2002, GFC 2007-2009)
that the 2011-2026 backtest didn't touch. If SMA200 tactical only "works" in
the post-GFC bull, it'll show here.

Leveraged sleeve can't be tested — TQQQ launched 2010-02. Buy-the-dip and RSP
are tested for completeness (RSP launched 2003-04; pre-2003 it's SPY).

Out-of-market position is SHY when available (launched 2002-07), cash at 0%
yield before that — conservative against the tactical strategy.

Usage:
    python3.10 spy_overlays_2000s.py
"""
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime

START = "2000-01-01"
END   = "2011-12-31"
WARMUP_BARS = 220   # need 200-day SMA


def _fetch():
    syms = ["SPY", "SHY", "RSP"]
    print(f"Downloading {syms} from {START} → {END} ...")
    raw = yf.download(syms, start=START, end=END,
                      progress=False, auto_adjust=True, threads=True)
    close = raw["Close"].copy()
    close.columns = [str(c) for c in close.columns]
    close = close.dropna(how="all").ffill()
    return close


def _metrics(ec, name, initial=100_000):
    final  = float(ec.iloc[-1])
    days   = (ec.index[-1] - ec.index[0]).days
    years  = max(days / 365.25, 0.1)
    cagr   = ((final / initial) ** (1 / years) - 1) * 100
    rets   = ec.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    rm     = ec.cummax()
    max_dd = float(((ec - rm) / rm).min() * 100)
    return dict(name=name, cagr=round(cagr,2), sharpe=round(sharpe,2),
                max_dd=round(abs(max_dd),2), final=round(final,0), years=round(years,1))


# ─── SPY buy-and-hold ───────────────────────────────────────────────────────

def _spy_buyhold(close, initial=100_000):
    spy = close["SPY"].iloc[WARMUP_BARS:]
    return (spy / spy.iloc[0]) * initial


# ─── SMA200 tactical (SPY ↔ SHY/cash) ───────────────────────────────────────

def _sma200_tactical(close, initial=100_000):
    spy = close["SPY"]
    shy = close["SHY"]   # NaN before 2002-07
    sma200 = spy.rolling(200).mean()
    above  = spy > sma200

    ec = pd.Series(index=close.index[WARMUP_BARS:], dtype=float)
    cash       = float(initial)
    shares_spy = 0.0
    shares_shy = 0.0
    state      = None

    for date in ec.index:
        in_spy   = bool(above.loc[date])
        target   = "SPY" if in_spy else "OUT"
        p_spy    = float(spy.loc[date])
        p_shy    = shy.loc[date]
        shy_ok   = pd.notna(p_shy) and float(p_shy) > 0
        p_shy    = float(p_shy) if shy_ok else None

        if state is None:
            if target == "SPY":
                shares_spy = cash / p_spy; cash = 0.0
                state = "SPY"
            else:
                if shy_ok:
                    shares_shy = cash / p_shy; cash = 0.0
                    state = "SHY"
                else:
                    state = "CASH"   # cash at 0% yield (conservative)
            # mark
        elif target == "SPY" and state != "SPY":
            # Move to SPY from SHY or cash
            if state == "SHY":
                cash = shares_shy * p_shy; shares_shy = 0.0
            shares_spy = cash / p_spy; cash = 0.0
            state = "SPY"
        elif target == "OUT" and state == "SPY":
            cash = shares_spy * p_spy; shares_spy = 0.0
            if shy_ok:
                shares_shy = cash / p_shy; cash = 0.0
                state = "SHY"
            else:
                state = "CASH"
        elif target == "OUT" and state == "CASH" and shy_ok:
            # SHY became available while we were sitting in cash → move into SHY
            shares_shy = cash / p_shy; cash = 0.0
            state = "SHY"

        # Mark-to-market
        val = shares_spy * p_spy
        if state == "SHY" and shy_ok:
            val += shares_shy * p_shy
        val += cash
        ec.loc[date] = val

    return ec


# ─── RSP equal-weight (with SPY substitute before RSP launch) ───────────────

def _rsp_buyhold(close, initial=100_000):
    spy = close["SPY"]
    rsp = close["RSP"]   # launched 2003-04-24
    rsp_start = rsp.first_valid_index()
    ec = pd.Series(index=close.index[WARMUP_BARS:], dtype=float)

    in_rsp     = False
    shares_spy = initial / float(spy.iloc[WARMUP_BARS])
    shares_rsp = 0.0
    for date in ec.index:
        p_spy = float(spy.loc[date])
        if not in_rsp and date >= rsp_start:
            p_rsp = float(rsp.loc[date])
            if p_rsp > 0:
                port = shares_spy * p_spy
                shares_rsp = port / p_rsp
                shares_spy = 0.0
                in_rsp = True
        if in_rsp:
            ec.loc[date] = shares_rsp * float(rsp.loc[date])
        else:
            ec.loc[date] = shares_spy * p_spy
    return ec


# ─── Buy-the-dip ladder (uses SHY where available, else cash) ───────────────

def _buy_the_dip(close, initial=100_000):
    spy = close["SPY"]
    shy = close["SHY"]
    ec  = pd.Series(index=close.index[WARMUP_BARS:], dtype=float)

    TRANCHES = [(0.05, 1/3), (0.10, 2/3), (0.15, 1.0)]

    p_spy0 = float(spy.iloc[WARMUP_BARS])
    shares_spy = 0.80 * initial / p_spy0
    p_shy0     = shy.iloc[WARMUP_BARS]
    if pd.notna(p_shy0) and float(p_shy0) > 0:
        shares_shy = 0.20 * initial / float(p_shy0)
        cash = 0.0
    else:
        shares_shy = 0.0
        cash       = 0.20 * initial   # cash at 0% yield
    reserve_spent_frac = 0.0
    peak_spy = p_spy0

    for date in ec.index:
        p_spy = float(spy.loc[date])
        p_shy = shy.loc[date]
        shy_ok = pd.notna(p_shy) and float(p_shy) > 0
        p_shy  = float(p_shy) if shy_ok else None

        if cash > 0 and shy_ok:
            shares_shy += cash / p_shy
            cash = 0.0

        if p_spy > peak_spy:
            peak_spy = p_spy
            if reserve_spent_frac > 0:
                shy_val  = shares_shy * p_shy if shy_ok else 0.0
                port     = shares_spy * p_spy + shy_val + cash
                target_spy_val = 0.80 * port
                cur_spy_val    = shares_spy * p_spy
                if cur_spy_val > target_spy_val:
                    sell_val = cur_spy_val - target_spy_val
                    shares_spy -= sell_val / p_spy
                    if shy_ok:
                        shares_shy += sell_val / p_shy
                    else:
                        cash += sell_val
                reserve_spent_frac = 0.0

        dd = (peak_spy - p_spy) / peak_spy if peak_spy > 0 else 0.0
        target_spent = 0.0
        for thresh, frac in TRANCHES:
            if dd >= thresh:
                target_spent = max(target_spent, frac)

        if target_spent > reserve_spent_frac:
            shy_val  = shares_shy * p_shy if shy_ok else 0.0
            port     = shares_spy * p_spy + shy_val + cash
            extra_frac = target_spent - reserve_spent_frac
            move_val   = extra_frac * 0.20 * port
            avail      = shy_val + cash
            move_val   = min(move_val, avail)
            shares_spy += move_val / p_spy
            if shy_ok and shares_shy * p_shy >= move_val:
                shares_shy -= move_val / p_shy
            else:
                used_shy = shares_shy * p_shy if shy_ok else 0.0
                shares_shy = 0.0
                cash -= (move_val - used_shy)
            reserve_spent_frac = target_spent

        shy_val = shares_shy * p_shy if shy_ok else 0.0
        ec.loc[date] = shares_spy * p_spy + shy_val + cash

    return ec


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    close = _fetch()
    print(f"Window: {close.index[0].date()} → {close.index[-1].date()} "
          f"({len(close)} trading days)")

    runs = [
        ("SPY buy-and-hold",   _spy_buyhold),
        ("SMA200 tactical",    _sma200_tactical),
        ("RSP equal-weight",   _rsp_buyhold),
        ("Buy-the-dip ladder", _buy_the_dip),
    ]
    results = []
    for name, fn in runs:
        try:
            ec = fn(close)
            results.append(_metrics(ec, name))
        except Exception as e:
            print(f"  ERROR  {name}: {e}")

    print("\n" + "="*82)
    print(f"  REGIME-ROBUSTNESS TEST  (2000-2011, ~{results[0]['years']}yr)")
    print(f"  Window includes: dot-com bust (2000-02) + GFC (2007-09)")
    print("="*82)
    print(f"  {'Strategy':<24}  {'CAGR':>7}  {'Sharpe':>7}  {'MaxDD':>7}  {'Final $':>14}")
    print("-"*82)
    for r in results:
        marker = "  ←" if r["name"] == "SPY buy-and-hold" else ""
        print(f"  {r['name']:<24}  {r['cagr']:>6.2f}%  {r['sharpe']:>7.2f}  "
              f"{r['max_dd']:>6.2f}%  ${r['final']:>12,.0f}{marker}")
    print("="*82)

    ref = results[0]
    print(f"\n  SPY ref: CAGR {ref['cagr']:.2f}%  Sharpe {ref['sharpe']:.2f}  DD {ref['max_dd']:.2f}%")
    print(f"\n  Strategies that beat SPY on Sharpe:")
    for r in results[1:]:
        if r["sharpe"] > ref["sharpe"]:
            print(f"    ✓ {r['name']:<24}  Sharpe {r['sharpe']:>5.2f} (+{r['sharpe']-ref['sharpe']:.2f}) "
                  f"DD {r['max_dd']:>5.2f}% (Δ {r['max_dd']-ref['max_dd']:+.1f}%)")

    print(f"\n  Strategies that beat SPY on CAGR:")
    for r in results[1:]:
        if r["cagr"] > ref["cagr"]:
            print(f"    ✓ {r['name']:<24}  CAGR {r['cagr']:>5.2f}% (+{r['cagr']-ref['cagr']:.2f}) "
                  f"DD {r['max_dd']:>5.2f}% (Δ {r['max_dd']-ref['max_dd']:+.1f}%)")


if __name__ == "__main__":
    main()
