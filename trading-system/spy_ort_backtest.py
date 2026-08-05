#!/usr/bin/env python3
"""
SPY Open Range Trend Follow — Trailing Stop Backtest
Account : $100,000  |  Leverage: 2:1 margin  |  Long-only

Entry : close > EMA(34) during 09:30–11:00 ET (first occurrence each session)
Exit  : trailing stop at 2.5 × ATR(14) below current close (never lowered)
Data  : SPY 5-min bars via yfinance (60-day limit, free/instant)
        → for multi-year history, set DATA_SOURCE = "alpaca" and add keys below
"""

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Strategy ──────────────────────────────────────────────────────────────────
EMA_PERIOD   = 34
ATR_PERIOD   = 14
TRAIL_MULT   = 2.5
ENTRY_START  = 9 * 60 + 30    # 570 = 09:30 ET
ENTRY_END    = 11 * 60         # 660 = 11:00 ET

# ── Account ───────────────────────────────────────────────────────────────────
INITIAL_EQUITY  = 100_000.0
LEVERAGE        = 2.0          # 2:1 margin → buying power = 2× equity
COMM_PER_SHARE  = 0.005        # $0.005/share each way → $0.01 round-trip

# ── Data ──────────────────────────────────────────────────────────────────────
DATA_SOURCE    = "alpaca"      # "yfinance" (60d free) | "alpaca" (5yr free)
ALPACA_KEY     = "CK4HXKGM3HWRKLXWRU3CDLB6LX"
ALPACA_SECRET  = "7i1mhApwTyzqWprLyvGvbzuDJbNSquMRTCnoiXfJSEWk"
ALPACA_BASE    = "https://data.sandbox.alpaca.markets"
CACHE        = Path("spy_5min_bars.csv")
OUTPUT_CSV   = "spy_ort_trades.csv"


# ── Data fetch ────────────────────────────────────────────────────────────────

def fetch_yfinance() -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("Run: pip install yfinance")
    print("Downloading SPY 5-min bars via yfinance (last 60 days) ...")
    raw = yf.download("SPY", period="60d", interval="5m", progress=False, auto_adjust=True)
    if raw.empty:
        sys.exit("yfinance returned no data")
    df = raw[["Open", "High", "Low", "Close"]].copy()
    df.columns = ["open", "high", "low", "close"]
    df.index.name = "date"
    # Strip timezone, keep only RTH (09:30–16:00 ET)
    df.index = pd.to_datetime(df.index).tz_convert("America/New_York").tz_localize(None)
    df = df.between_time("09:30", "15:55")
    return df.dropna()


def fetch_alpaca() -> pd.DataFrame:
    try:
        import requests
    except ImportError:
        sys.exit("Run: pip install requests")
    if not ALPACA_KEY:
        sys.exit("Set ALPACA_KEY in the script")

    from datetime import datetime, timedelta

    headers = {
        "APCA-API-KEY-ID":     ALPACA_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET,
    }
    base = f"{ALPACA_BASE}/v2/stocks/bars"
    end     = datetime.utcnow()
    start   = end - timedelta(days=5 * 365)
    bars    = []
    token   = None

    print("Downloading SPY 5-min bars via Alpaca (up to 5 years) ...")
    while True:
        params = {
            "symbols":   "SPY",
            "timeframe": "5Min",
            "start":     start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end":       end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "feed":      "iex",
            "limit":     10000,
            "sort":      "asc",
        }
        if token:
            params["page_token"] = token
        r = requests.get(base, headers=headers, params=params, timeout=30)
        if r.status_code == 401:
            sys.exit("Alpaca auth failed — check your ALPACA_KEY")
        r.raise_for_status()
        data  = r.json()
        chunk = data.get("bars", {}).get("SPY", [])
        if not chunk:
            break
        bars.extend(chunk)
        token = data.get("next_page_token")
        if not token:
            break
        print(f"  {len(bars):,} bars so far ...", end="\r", flush=True)

    if not bars:
        sys.exit("Alpaca returned no data")

    print(f"  {len(bars):,} bars total")
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"]).dt.tz_convert("America/New_York").dt.tz_localize(None)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
    df = df.set_index("date")[["open", "high", "low", "close"]]
    df = df.between_time("09:30", "15:55")
    return df.sort_index().dropna()


def fetch_bars() -> pd.DataFrame:
    if DATA_SOURCE == "alpaca":
        return fetch_alpaca()
    return fetch_yfinance()


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    df["atr"] = pd.concat([hl, hcp, lcp], axis=1).max(axis=1).ewm(
        span=ATR_PERIOD, adjust=False).mean()
    return df


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame) -> list[dict]:
    warmup   = max(EMA_PERIOD, ATR_PERIOD) + 5
    trades   = []
    position = None
    equity   = INITIAL_EQUITY

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        close = row["close"]
        ema   = row["ema"]
        atr   = row["atr"]
        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute

        # ── Manage open position ─────────────────────────────────────────────
        if position is not None:
            new_stop = close - TRAIL_MULT * atr
            if new_stop > position["trail_stop"]:
                position["trail_stop"] = new_stop

            if close <= position["trail_stop"]:
                shares   = position["shares"]
                gross    = (close - position["entry_price"]) * shares
                comm     = shares * COMM_PER_SHARE * 2
                pnl      = gross - comm
                equity  += pnl
                trades.append({
                    "entry_time":  position["entry_time"],
                    "exit_time":   ts,
                    "entry_price": round(position["entry_price"], 4),
                    "exit_price":  round(close, 4),
                    "trail_stop":  round(position["trail_stop"], 4),
                    "shares":      shares,
                    "gross":       round(gross, 2),
                    "comm":        round(comm, 2),
                    "pnl":         round(pnl, 2),
                    "bars_held":   i - position["entry_idx"],
                    "equity":      round(equity, 2),
                    "month":       ts.to_period("M"),
                    "year":        ts.year,
                })
                position = None

        # ── Entry ────────────────────────────────────────────────────────────
        if position is None and ENTRY_START <= mins < ENTRY_END:
            if close > ema:
                shares = int((equity * LEVERAGE) / close)
                if shares < 1:
                    continue
                position = {
                    "entry_price": close,
                    "entry_time":  ts,
                    "shares":      shares,
                    "trail_stop":  close - TRAIL_MULT * atr,
                    "entry_idx":   i,
                }

    # Close any position still open at end of data
    if position is not None:
        close  = df.iloc[-1]["close"]
        ts     = df.index[-1]
        shares = position["shares"]
        gross  = (close - position["entry_price"]) * shares
        comm   = shares * COMM_PER_SHARE * 2
        pnl    = gross - comm
        equity += pnl
        trades.append({
            "entry_time":  position["entry_time"],
            "exit_time":   ts,
            "entry_price": round(position["entry_price"], 4),
            "exit_price":  round(close, 4),
            "trail_stop":  round(position["trail_stop"], 4),
            "shares":      shares,
            "gross":       round(gross, 2),
            "comm":        round(comm, 2),
            "pnl":         round(pnl, 2),
            "bars_held":   len(df) - 1 - position["entry_idx"],
            "equity":      round(equity, 2),
            "month":       ts.to_period("M"),
            "year":        ts.year,
        })

    return trades


# ── Reporting ─────────────────────────────────────────────────────────────────

def sharpe(pnl_s: pd.Series, days: int) -> float:
    n     = len(pnl_s)
    std   = pnl_s.std(ddof=1)
    if n < 2 or std == 0:
        return float("nan")
    tpy   = n / (days / 252)
    return (pnl_s.mean() / std) * np.sqrt(tpy)

def pf(pnl_s: pd.Series) -> float:
    w = pnl_s[pnl_s > 0].sum()
    l = pnl_s[pnl_s < 0].sum()
    return float(-w / l) if l != 0 else float("nan")

def max_dd(equity_s: pd.Series):
    hwm  = equity_s.cummax()
    dd   = equity_s - hwm
    idx  = dd.idxmin()
    pct  = (dd.loc[idx] / hwm.loc[idx]) * 100 if dd.min() < 0 else 0.0
    return dd.min(), pct

def print_report(trades: list[dict], df: pd.DataFrame):
    if not trades:
        print("\nNo trades generated.")
        return

    t = pd.DataFrame(trades)
    pnl_s  = t["pnl"]
    eq_s   = t["equity"]
    n_days = max((df.index[-1] - df.index[0]).days, 1)
    n      = len(pnl_s)
    wins   = (pnl_s > 0).sum()
    dd_abs, dd_pct = max_dd(eq_s)
    ret_pct = (eq_s.iloc[-1] - INITIAL_EQUITY) / INITIAL_EQUITY * 100
    sh      = sharpe(pnl_s, n_days)
    pf_val  = pf(pnl_s)
    avg_bars = t["bars_held"].mean()

    SEP = "=" * 82
    print(f"\n{SEP}")
    print(f"  TOTAL  {df.index[0].date()} → {df.index[-1].date()}"
          f"  ({n_days} days / {n_days//252:.1f} yrs)")
    print(SEP)
    print(f"  Trades    : {n}  ({wins}W / {n-wins}L)   Win rate: {wins/n*100:.1f}%")
    print(f"  Profit Fac: {pf_val:.2f}" if not np.isnan(pf_val) else "  Profit Fac: n/a")
    print(f"  Net PnL   : ${pnl_s.sum():>+,.0f}   Return: {ret_pct:+.1f}%")
    print(f"  Sharpe    : {sh:+.2f}" if not np.isnan(sh) else "  Sharpe    : n/a")
    print(f"  Max DD    : ${dd_abs:>,.0f}  ({dd_pct:.1f}%)")
    print(f"  Avg hold  : {avg_bars:.1f} bars  ({avg_bars * 5 / 60:.1f} hrs)")
    print(f"  Avg win   : ${pnl_s[pnl_s>0].mean():>+,.0f}"
          f"    Avg loss: ${pnl_s[pnl_s<=0].mean():>+,.0f}")
    print(f"  Final eq  : ${eq_s.iloc[-1]:>,.0f}")
    print(SEP)

    # Per-year breakdown
    print(f"\n  {'Year':<6}  {'n':>4}  {'Win%':>5}  {'PF':>5}  {'PnL':>12}"
          f"  {'Sharpe':>7}  {'MaxDD':>9}  {'DD%':>6}")
    print("  " + "─" * 72)
    for yr, grp in t.groupby("year"):
        g    = grp["pnl"]
        g_eq = grp["equity"]
        gn   = len(g)
        gw   = (g > 0).sum()
        gd   = max((grp["exit_time"].max() - grp["entry_time"].min()).days, 1)
        g_dd_abs, g_dd_pct = max_dd(g_eq)
        g_pf = pf(g)
        g_sh = sharpe(g, gd)
        print(
            f"  {yr:<6}  {gn:>4}  {gw/gn*100:>4.0f}%  "
            f"{'n/a' if np.isnan(g_pf) else f'{g_pf:.2f}':>5}  "
            f"${g.sum():>+10,.0f}  "
            f"{'n/a' if np.isnan(g_sh) else f'{g_sh:+.2f}':>7}  "
            f"${g_dd_abs:>+8,.0f}  {g_dd_pct:>+5.1f}%"
        )

    # Per-month breakdown
    print(f"\n  {'Month':<8}  {'n':>3}  {'Win%':>5}  {'PF':>5}  {'PnL':>12}"
          f"  {'AvgHold':>8}  {'Best':>10}  {'Worst':>10}")
    print("  " + "─" * 78)
    for mo, grp in t.groupby("month"):
        g   = grp["pnl"]
        gn  = len(g)
        gw  = (g > 0).sum()
        gl  = gn - gw
        g_pf = pf(g)
        ah  = grp["bars_held"].mean() * 5 / 60
        print(
            f"  {str(mo):<8}  {gn:>3}  {gw/gn*100:>4.0f}%  "
            f"{'n/a' if np.isnan(g_pf) else f'{g_pf:.2f}':>5}  "
            f"${g.sum():>+10,.0f}  "
            f"{ah:>7.1f}hr  ${g.max():>+8,.0f}  ${g.min():>+8,.0f}"
        )

    t.drop(columns=["month", "year"]).to_csv(OUTPUT_CSV, index=False)
    print(f"\nTrade log → {OUTPUT_CSV}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if CACHE.exists():
        print(f"Loading cached bars from {CACHE}")
        df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    else:
        df = fetch_bars()
        df.to_csv(CACHE)
        print(f"Cached → {CACHE}")

    print(f"  {len(df):,} bars  {df.index[0].date()} → {df.index[-1].date()}")
    df = add_indicators(df)

    print(f"\nStrategy : EMA({EMA_PERIOD}) open-range breakout  |  "
          f"Trail stop {TRAIL_MULT}×ATR({ATR_PERIOD})  |  Long-only")
    print(f"Account  : ${INITIAL_EQUITY:,.0f}  |  {LEVERAGE:.0f}:1 leverage  |  "
          f"Comm ${COMM_PER_SHARE*2:.3f}/share RT")
    print(f"Entry    : 09:30–11:00 ET\n")

    trades = run_backtest(df)
    print_report(trades, df)


if __name__ == "__main__":
    main()
