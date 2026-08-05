"""
Head-to-head: 4 simple SPY overlays vs SPY buy-and-hold.

Goal: identify a strategy that genuinely beats SPY on risk-adjusted basis (Sharpe,
DD) without exotic data or third-party signals. Same ~14yr window as the
strategy_compare runs.

Overlays tested:
  1. SMA200 tactical    — hold SPY when close > 200-SMA, else SHY
  2. Leveraged sleeve   — 80% SPY + 20% TQQQ when VIX<20 & SPY>SMA200, else 100% SPY
  3. RSP equal-weight   — buy-and-hold equal-weight S&P 500 (no overlay needed)
  4. Buy-the-dip ladder — 80% SPY core + 20% cash; deploy at -5/-10/-15% drawdown
                          from peak; revert to 80/20 on new peak

Usage:
    python3.10 spy_overlays_backtest.py
"""
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

SYMS  = ["SPY", "TQQQ", "RSP", "SHY", "^VIX"]
YEARS = 14
WARMUP_BARS = 220   # need >200 for SMA200


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
    # First date where every overlay-relevant symbol has data:
    needed = ["SPY", "TQQQ", "RSP", "SHY", "^VIX"]
    avail  = close.dropna(subset=needed)
    if avail.empty:
        raise RuntimeError("no overlap window — yfinance returned bad data")
    return avail


# ─── Metrics ────────────────────────────────────────────────────────────────

def _metrics(ec, name, label, initial=100_000):
    final  = float(ec.iloc[-1])
    days   = (ec.index[-1] - ec.index[0]).days
    years  = max(days / 365.25, 0.1)
    cagr   = ((final / initial) ** (1 / years) - 1) * 100
    rets   = ec.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    rm     = ec.cummax()
    max_dd = float(((ec - rm) / rm).min() * 100)
    return {
        "name":   name,
        "label":  label,
        "cagr":   round(cagr, 2),
        "sharpe": round(sharpe, 2),
        "max_dd": round(abs(max_dd), 2),
        "final":  round(final, 0),
        "years":  round(years, 1),
    }


# ─── Strategy 0: SPY buy-and-hold (the reference) ───────────────────────────

def _spy_buyhold(close, initial=100_000):
    spy = close["SPY"].iloc[WARMUP_BARS:]
    return (spy / spy.iloc[0]) * initial


# ─── Strategy 1: SMA200 tactical (SPY ↔ SHY) ────────────────────────────────

def _sma200_tactical(close, initial=100_000):
    spy = close["SPY"]
    shy = close["SHY"]
    sma200 = spy.rolling(200).mean()
    above  = spy > sma200

    ec = pd.Series(index=close.index[WARMUP_BARS:], dtype=float)
    cash = float(initial)
    shares_spy = 0.0
    shares_shy = 0.0
    state = None  # "SPY" or "SHY"

    for date in ec.index:
        in_spy = bool(above.loc[date])
        target = "SPY" if in_spy else "SHY"
        p_spy  = float(spy.loc[date])
        p_shy  = float(shy.loc[date])

        if state is None:
            # Initial entry
            if target == "SPY":
                shares_spy = cash / p_spy; cash = 0.0
            else:
                shares_shy = cash / p_shy; cash = 0.0
            state = target
        elif target != state:
            # Switch
            if state == "SPY":
                cash       = shares_spy * p_spy
                shares_spy = 0.0
                shares_shy = cash / p_shy
                cash       = 0.0
            else:
                cash       = shares_shy * p_shy
                shares_shy = 0.0
                shares_spy = cash / p_spy
                cash       = 0.0
            state = target

        ec.loc[date] = shares_spy * p_spy + shares_shy * p_shy + cash
    return ec


# ─── Strategy 2: Leveraged sleeve (80/20 SPY+TQQQ when favorable, else 100 SPY) ─

def _leveraged_sleeve(close, initial=100_000):
    spy  = close["SPY"]
    tqqq = close["TQQQ"]
    vix  = close["^VIX"]
    sma200 = spy.rolling(200).mean()
    bullish = (vix < 20) & (spy > sma200)

    ec = pd.Series(index=close.index[WARMUP_BARS:], dtype=float)
    # State: shares_spy, shares_tqqq
    shares_spy  = 0.0
    shares_tqqq = 0.0
    cash        = float(initial)
    state       = None  # "80_20" or "100_spy"
    REBAL_DRIFT = 0.02   # rebalance if a leg drifts > 2% from target weight

    for date in ec.index:
        p_spy  = float(spy.loc[date])
        p_tqqq = float(tqqq.loc[date])
        bull   = bool(bullish.loc[date])
        target = "80_20" if bull else "100_spy"

        # Initial entry
        if state is None:
            if target == "80_20":
                spy_alloc  = 0.80 * cash
                tqqq_alloc = 0.20 * cash
                shares_spy  = spy_alloc  / p_spy
                shares_tqqq = tqqq_alloc / p_tqqq
            else:
                shares_spy = cash / p_spy
            cash  = 0.0
            state = target
            ec.loc[date] = shares_spy * p_spy + shares_tqqq * p_tqqq
            continue

        # Regime switch
        port = shares_spy * p_spy + shares_tqqq * p_tqqq + cash
        if target != state:
            if target == "80_20":
                shares_spy  = 0.80 * port / p_spy
                shares_tqqq = 0.20 * port / p_tqqq
            else:
                shares_spy  = port / p_spy
                shares_tqqq = 0.0
            cash  = 0.0
            state = target
        else:
            # Drift-based rebalance only inside 80/20 mode
            if state == "80_20":
                w_spy  = (shares_spy * p_spy) / port if port > 0 else 0
                w_tqqq = (shares_tqqq * p_tqqq) / port if port > 0 else 0
                if abs(w_spy - 0.80) > REBAL_DRIFT:
                    shares_spy  = 0.80 * port / p_spy
                    shares_tqqq = 0.20 * port / p_tqqq

        ec.loc[date] = shares_spy * p_spy + shares_tqqq * p_tqqq + cash
    return ec


# ─── Strategy 3: RSP equal-weight buy-and-hold ──────────────────────────────

def _rsp_buyhold(close, initial=100_000):
    rsp = close["RSP"].iloc[WARMUP_BARS:]
    return (rsp / rsp.iloc[0]) * initial


# ─── Strategy 4: Buy-the-dip ladder ─────────────────────────────────────────

def _buy_the_dip(close, initial=100_000):
    spy = close["SPY"]
    shy = close["SHY"]  # cash reserve earns short-term treasury yield

    ec = pd.Series(index=close.index[WARMUP_BARS:], dtype=float)

    # Tranche thresholds (drawdown-from-peak → cumulative deployed fraction of reserve)
    TRANCHES = [(0.05, 1/3), (0.10, 2/3), (0.15, 1.0)]

    # Initial: 80% SPY core + 20% cash reserve (in SHY)
    p_spy0 = float(spy.iloc[WARMUP_BARS])
    p_shy0 = float(shy.iloc[WARMUP_BARS])
    shares_spy = (0.80 * initial) / p_spy0
    shares_shy = (0.20 * initial) / p_shy0
    reserve_spent_frac = 0.0  # how much of the 20% reserve is deployed
    peak_spy = p_spy0

    for date in ec.index:
        p_spy = float(spy.loc[date])
        p_shy = float(shy.loc[date])

        # Track peak SPY price for drawdown trigger
        if p_spy > peak_spy:
            peak_spy = p_spy
            # On new peak, sell deployed reserve back to 80/20 if currently overweight SPY
            if reserve_spent_frac > 0:
                # Current SPY value / total → if too high, sell SPY back to 80%
                port = shares_spy * p_spy + shares_shy * p_shy
                target_spy_val = 0.80 * port
                cur_spy_val    = shares_spy * p_spy
                if cur_spy_val > target_spy_val:
                    sell_val   = cur_spy_val - target_spy_val
                    shares_spy -= sell_val / p_spy
                    shares_shy += sell_val / p_shy
                reserve_spent_frac = 0.0

        # Check drawdown triggers
        dd = (peak_spy - p_spy) / peak_spy if peak_spy > 0 else 0
        target_spent = 0.0
        for thresh, frac in TRANCHES:
            if dd >= thresh:
                target_spent = max(target_spent, frac)

        # Deploy additional reserve if target_spent > current
        if target_spent > reserve_spent_frac:
            port = shares_spy * p_spy + shares_shy * p_shy
            # Estimate original-reserve dollar value as 20% of original portfolio.
            # On new tranche, sell SHY proportionally, buy SPY.
            extra_frac    = target_spent - reserve_spent_frac
            # Move "extra_frac × 20% of current portfolio" from SHY to SPY
            move_val      = extra_frac * 0.20 * port
            move_val      = min(move_val, shares_shy * p_shy)
            shares_shy   -= move_val / p_shy
            shares_spy   += move_val / p_spy
            reserve_spent_frac = target_spent

        ec.loc[date] = shares_spy * p_spy + shares_shy * p_shy
    return ec


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    close = _fetch()
    n     = len(close)
    print(f"Window: {close.index[0].date()} → {close.index[-1].date()} ({n} trading days)")

    runs = [
        ("SPY buy-and-hold",   "ref",      _spy_buyhold),
        ("SMA200 tactical",    "overlay1", _sma200_tactical),
        ("Leveraged sleeve",   "overlay2", _leveraged_sleeve),
        ("RSP equal-weight",   "overlay3", _rsp_buyhold),
        ("Buy-the-dip ladder", "overlay4", _buy_the_dip),
    ]
    results = []
    for name, label, fn in runs:
        try:
            ec = fn(close)
            m  = _metrics(ec, name, label)
            results.append(m)
        except Exception as e:
            print(f"  ERROR  {name}: {e}")

    print("\n" + "="*88)
    print(f"  RESULTS  ({results[0]['years']} yr window)")
    print("="*88)
    print(f"  {'Strategy':<24}  {'CAGR':>7}  {'Sharpe':>7}  {'MaxDD':>7}  {'Final $':>14}")
    print("-"*88)
    for r in results:
        marker = "  ←" if r["label"] == "ref" else ""
        print(f"  {r['name']:<24}  {r['cagr']:>6.2f}%  {r['sharpe']:>7.2f}  "
              f"{r['max_dd']:>6.2f}%  ${r['final']:>12,.0f}{marker}")
    print("="*88)

    ref = results[0]
    print(f"\n  Strategies that beat SPY on Sharpe (SPY: {ref['sharpe']:.2f}):")
    beat_sharpe = [r for r in results[1:] if r["sharpe"] > ref["sharpe"]]
    if beat_sharpe:
        for r in beat_sharpe:
            print(f"    ✓ {r['name']:<24}  Sharpe {r['sharpe']:>5.2f} (+{r['sharpe']-ref['sharpe']:.2f}) "
                  f"DD {r['max_dd']:>5.2f}% (Δ {r['max_dd']-ref['max_dd']:+.1f}%)")
    else:
        print("    ✗ none.")

    print(f"\n  Strategies that beat SPY on CAGR (SPY: {ref['cagr']:.2f}%):")
    beat_cagr = [r for r in results[1:] if r["cagr"] > ref["cagr"]]
    if beat_cagr:
        for r in beat_cagr:
            print(f"    ✓ {r['name']:<24}  CAGR {r['cagr']:>5.2f}% (+{r['cagr']-ref['cagr']:.2f}) "
                  f"DD {r['max_dd']:>5.2f}% (Δ {r['max_dd']-ref['max_dd']:+.1f}%)")
    else:
        print("    ✗ none.")

    print(f"\n  Strategies that beat SPY on both Sharpe AND CAGR with lower DD:")
    strict = [r for r in results[1:]
              if r["sharpe"] > ref["sharpe"] and r["cagr"] > ref["cagr"] and r["max_dd"] < ref["max_dd"]]
    if strict:
        for r in strict:
            print(f"    ★ {r['name']}")
    else:
        print("    ✗ none — every overlay trades off at least one dimension.")


if __name__ == "__main__":
    main()
