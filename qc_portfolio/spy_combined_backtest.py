"""
Combined SPY overlay: SMA200 tactical + leveraged sleeve, stacked.

State machine driven by two signals (SPY vs SMA200, VIX level):
    STRONG_BULL    SPY > SMA200  AND  VIX < 20   →   80% SPY + 20% TQQQ
    CAUTIOUS_BULL  SPY > SMA200  AND  VIX ≥ 20   →   100% SPY
    BEAR           SPY ≤ SMA200                  →   100% SHY (capital preservation)

Comparing four strategies head-to-head over 2011-2026 (TQQQ available):
    SPY buy-and-hold
    SMA200 tactical alone
    Leveraged sleeve alone
    Combined (the new variant)

Usage:
    python3.10 spy_combined_backtest.py
"""
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

SYMS  = ["SPY", "TQQQ", "SHY", "^VIX"]
YEARS = 14
WARMUP_BARS = 220


def _fetch():
    end   = datetime.now()
    start = end - timedelta(days=YEARS * 365 + WARMUP_BARS + 60)
    print(f"Downloading {SYMS} from {start.date()} → {end.date()} ...")
    raw = yf.download(SYMS, start=start.strftime("%Y-%m-%d"),
                      end=end.strftime("%Y-%m-%d"),
                      progress=False, auto_adjust=True, threads=True)
    close = raw["Close"].copy()
    close.columns = [str(c) for c in close.columns]
    close = close.dropna(how="all").ffill()
    needed = ["SPY", "TQQQ", "SHY", "^VIX"]
    return close.dropna(subset=needed)


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


def _spy_buyhold(close, initial=100_000):
    spy = close["SPY"].iloc[WARMUP_BARS:]
    return (spy / spy.iloc[0]) * initial


def _sma200_tactical(close, initial=100_000):
    spy, shy = close["SPY"], close["SHY"]
    sma200 = spy.rolling(200).mean()
    above  = spy > sma200

    ec = pd.Series(index=close.index[WARMUP_BARS:], dtype=float)
    cash = float(initial)
    shares_spy = shares_shy = 0.0
    state = None
    for date in ec.index:
        target = "SPY" if bool(above.loc[date]) else "SHY"
        p_spy, p_shy = float(spy.loc[date]), float(shy.loc[date])
        if state is None:
            if target == "SPY":
                shares_spy = cash / p_spy
            else:
                shares_shy = cash / p_shy
            cash = 0.0
            state = target
        elif target != state:
            if state == "SPY":
                cash = shares_spy * p_spy; shares_spy = 0.0
            else:
                cash = shares_shy * p_shy; shares_shy = 0.0
            if target == "SPY":
                shares_spy = cash / p_spy
            else:
                shares_shy = cash / p_shy
            cash = 0.0
            state = target
        ec.loc[date] = shares_spy * p_spy + shares_shy * p_shy + cash
    return ec


def _leveraged_sleeve(close, initial=100_000):
    spy, tqqq, vix = close["SPY"], close["TQQQ"], close["^VIX"]
    sma200 = spy.rolling(200).mean()
    bullish = (vix < 20) & (spy > sma200)
    REBAL_DRIFT = 0.02

    ec = pd.Series(index=close.index[WARMUP_BARS:], dtype=float)
    shares_spy = shares_tqqq = 0.0
    cash = float(initial)
    state = None
    for date in ec.index:
        p_spy, p_tqqq = float(spy.loc[date]), float(tqqq.loc[date])
        target = "80_20" if bool(bullish.loc[date]) else "100_spy"
        if state is None:
            if target == "80_20":
                shares_spy  = 0.80 * cash / p_spy
                shares_tqqq = 0.20 * cash / p_tqqq
            else:
                shares_spy  = cash / p_spy
            cash = 0.0
            state = target
            ec.loc[date] = shares_spy * p_spy + shares_tqqq * p_tqqq
            continue
        port = shares_spy * p_spy + shares_tqqq * p_tqqq + cash
        if target != state:
            if target == "80_20":
                shares_spy  = 0.80 * port / p_spy
                shares_tqqq = 0.20 * port / p_tqqq
            else:
                shares_spy  = port / p_spy
                shares_tqqq = 0.0
            cash = 0.0
            state = target
        elif state == "80_20":
            w_spy = (shares_spy * p_spy) / port if port > 0 else 0
            if abs(w_spy - 0.80) > REBAL_DRIFT:
                shares_spy  = 0.80 * port / p_spy
                shares_tqqq = 0.20 * port / p_tqqq
        ec.loc[date] = shares_spy * p_spy + shares_tqqq * p_tqqq + cash
    return ec


def _combined(close, initial=100_000):
    """SMA200 tactical (BEAR → SHY) wrapping leveraged sleeve (BULL → SPY/TQQQ)."""
    spy, tqqq, shy, vix = close["SPY"], close["TQQQ"], close["SHY"], close["^VIX"]
    sma200 = spy.rolling(200).mean()
    bull   = spy > sma200
    calm   = vix < 20
    REBAL_DRIFT = 0.02

    ec = pd.Series(index=close.index[WARMUP_BARS:], dtype=float)
    shares_spy = shares_tqqq = shares_shy = 0.0
    cash = float(initial)
    state = None   # "STRONG_BULL" | "CAUTIOUS_BULL" | "BEAR"

    for date in ec.index:
        p_spy  = float(spy.loc[date])
        p_tqqq = float(tqqq.loc[date])
        p_shy  = float(shy.loc[date])
        b      = bool(bull.loc[date])
        c      = bool(calm.loc[date])

        if not b:
            target = "BEAR"
        elif c:
            target = "STRONG_BULL"
        else:
            target = "CAUTIOUS_BULL"

        port = shares_spy * p_spy + shares_tqqq * p_tqqq + shares_shy * p_shy + cash

        if state is None:
            # Initial allocation
            if target == "STRONG_BULL":
                shares_spy  = 0.80 * cash / p_spy
                shares_tqqq = 0.20 * cash / p_tqqq
            elif target == "CAUTIOUS_BULL":
                shares_spy  = cash / p_spy
            else:
                shares_shy  = cash / p_shy
            cash = 0.0
            state = target
        elif target != state:
            # Liquidate current holdings to cash, then rebuild
            cash = port
            shares_spy = shares_tqqq = shares_shy = 0.0
            if target == "STRONG_BULL":
                shares_spy  = 0.80 * cash / p_spy
                shares_tqqq = 0.20 * cash / p_tqqq
            elif target == "CAUTIOUS_BULL":
                shares_spy  = cash / p_spy
            else:
                shares_shy  = cash / p_shy
            cash = 0.0
            state = target
        elif state == "STRONG_BULL":
            # Drift rebalance inside strong bull only
            w_spy = (shares_spy * p_spy) / port if port > 0 else 0
            if abs(w_spy - 0.80) > REBAL_DRIFT:
                shares_spy  = 0.80 * port / p_spy
                shares_tqqq = 0.20 * port / p_tqqq

        ec.loc[date] = shares_spy * p_spy + shares_tqqq * p_tqqq + shares_shy * p_shy + cash
    return ec


def _regime_breakdown(close):
    """Print how often each combined-strategy regime fires."""
    spy, vix = close["SPY"].iloc[WARMUP_BARS:], close["^VIX"].iloc[WARMUP_BARS:]
    sma200 = close["SPY"].rolling(200).mean().loc[spy.index]
    bull   = spy > sma200
    calm   = vix < 20
    strong = (bull & calm).sum()
    caut   = (bull & ~calm).sum()
    bear   = (~bull).sum()
    tot    = len(spy)
    print(f"  Regime distribution over {tot} trading days:")
    print(f"    STRONG_BULL   (SPY>SMA200, VIX<20)   {strong:>5} days  ({strong/tot*100:>5.1f}%)")
    print(f"    CAUTIOUS_BULL (SPY>SMA200, VIX≥20)   {caut:>5} days  ({caut/tot*100:>5.1f}%)")
    print(f"    BEAR          (SPY≤SMA200)           {bear:>5} days  ({bear/tot*100:>5.1f}%)")


def main():
    close = _fetch()
    print(f"Window: {close.index[0].date()} → {close.index[-1].date()} ({len(close)} days)")
    print()
    _regime_breakdown(close)
    print()

    runs = [
        ("SPY buy-and-hold",        _spy_buyhold),
        ("SMA200 tactical",         _sma200_tactical),
        ("Leveraged sleeve",        _leveraged_sleeve),
        ("Combined (tactical+lev)", _combined),
    ]
    results = []
    for name, fn in runs:
        try:
            results.append(_metrics(fn(close), name))
        except Exception as e:
            print(f"  ERROR  {name}: {e}")

    print("="*88)
    print(f"  COMBINED STRATEGY TEST  ({results[0]['years']}yr, 2011-2026)")
    print("="*88)
    print(f"  {'Strategy':<28}  {'CAGR':>7}  {'Sharpe':>7}  {'MaxDD':>7}  {'Final $':>14}")
    print("-"*88)
    for r in results:
        marker = "  ← ref" if r["name"] == "SPY buy-and-hold" else ""
        print(f"  {r['name']:<28}  {r['cagr']:>6.2f}%  {r['sharpe']:>7.2f}  "
              f"{r['max_dd']:>6.2f}%  ${r['final']:>12,.0f}{marker}")
    print("="*88)

    ref = results[0]
    print(f"\n  vs SPY (CAGR {ref['cagr']}%, Sharpe {ref['sharpe']}, DD {ref['max_dd']}%):")
    for r in results[1:]:
        d_cagr   = r["cagr"]   - ref["cagr"]
        d_sharpe = r["sharpe"] - ref["sharpe"]
        d_dd     = r["max_dd"] - ref["max_dd"]
        wins = []
        if d_cagr   > 0: wins.append(f"CAGR +{d_cagr:.2f}pp")
        if d_sharpe > 0: wins.append(f"Sharpe +{d_sharpe:.2f}")
        if d_dd     < 0: wins.append(f"DD {d_dd:.1f}pp")
        loses = []
        if d_cagr   < 0: loses.append(f"CAGR {d_cagr:.2f}pp")
        if d_sharpe < 0: loses.append(f"Sharpe {d_sharpe:.2f}")
        if d_dd     > 0: loses.append(f"DD +{d_dd:.1f}pp")
        verdict = ", ".join(wins) if wins else "—"
        loss_str = f"  [worse: {', '.join(loses)}]" if loses else ""
        print(f"    {r['name']:<28}  wins: {verdict}{loss_str}")


if __name__ == "__main__":
    main()
