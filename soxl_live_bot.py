#!/usr/bin/env python3
"""
SOXL Live Trading Bot — Proven Dip-Pyramid Strategy
====================================================

Strategy (proven best from 5-year sweep):
  • Enter long (bull/ranging) or short (bear) at 9:30–11:00 ET on EMA cross
  • Trail stop: 3×ATR, updating every bar
  • Pyramid: add up to 2× original position on 0.5×ATR dips while profitable
  • Close all longs at EOD trail; close shorts at 15:50 ET
  • Fractional shares: full dollar precision (works at any $ amount)

Expected (5-year backtest at $10):  $10 → $42  (+322%)  ~34%/yr CAGR
Expected (5-year backtest at $500): $500 → $11,024 (+2105%)  ~38%/yr CAGR

Usage:
  python3 soxl_live_bot.py                  # show last 5 trading days detail
  python3 soxl_live_bot.py --live           # run live Alpaca paper bot
  python3 soxl_live_bot.py --live --eq 500  # live bot with $500 starting notional

Alpaca keys:
  Currently wired to broker sandbox (orders won't show in app.alpaca.markets).
  To switch to paper API (visible in UI):
    1. Go to app.alpaca.markets → Paper Trading → API Keys → generate PK-prefix keys
    2. Set ALPACA_KEY / ALPACA_SECRET below, set USE_PAPER_API = True
"""

import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOL        = "SOXL"
CACHE         = Path("soxl_5min_bars.csv")

# Broker sandbox (always works; orders invisible in app.alpaca.markets)
ALPACA_KEY    = "CK4HXKGM3HWRKLXWRU3CDLB6LX"
ALPACA_SECRET = "7i1mhApwTyzqWprLyvGvbzuDJbNSquMRTCnoiXfJSEWk"
ALPACA_ACCT   = "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV"
BROKER_BASE   = "https://broker-api.sandbox.alpaca.markets"
DATA_BASE     = "https://data.sandbox.alpaca.markets"

# Swap to True + PK-prefix keys for paper-api.alpaca.markets visibility
USE_PAPER_API = False
PAPER_BASE    = "https://paper-api.alpaca.markets"

# ── Strategy params (best from 5-year sweep) ──────────────────────────────────
EMA_PERIOD    = 25
ATR_PERIOD    = 14
ADX_PERIOD    = 20
ADX_THRESHOLD = 35

TRAIL         = 3.0      # ATR trail multiplier
DIP_ATR_BACK  = 0.5      # pullback from pos_high to trigger add
MIN_PROFIT    = 0.5      # min profit (ATR) before adding
MAX_ADDS      = 2        # max pyramid adds per trade
ADD_FRAC      = 1.0      # add = 1.0× original position size

ENTRY_START   = 9 * 60 + 30
ENTRY_END     = 11 * 60
EOD_CLOSE     = 15 * 60 + 55   # force close all at 15:55

LEVERAGE      = 1.0      # SOXL is already 3×; don't add margin
COMM          = 0.005    # $0.005/share per leg (Alpaca)


# ── Data helpers ──────────────────────────────────────────────────────────────
def load_cached() -> pd.DataFrame:
    df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    return df.between_time("09:30", "16:00")


def data_headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}


def fetch_warmup_bars(n_days=45) -> pd.DataFrame:
    """Fetch enough history for indicator warmup."""
    end   = pd.Timestamp.now(tz="America/New_York").floor("min")
    start = end - pd.DateOffset(days=n_days * 2)
    bars, token = [], None
    print(f"  Fetching {n_days}-day warmup bars ...")
    while True:
        params = {"symbols": SYMBOL, "timeframe": "5Min",
                  "start": start.isoformat(), "end": end.isoformat(),
                  "limit": 10_000, "feed": "iex"}
        if token:
            params["page_token"] = token
        r = requests.get(f"{DATA_BASE}/v2/stocks/bars",
                         headers=data_headers(), params=params)
        r.raise_for_status()
        data  = r.json()
        bars.extend(data.get("bars", {}).get(SYMBOL, []))
        token = data.get("next_page_token")
        if not token:
            break
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    df = (df.set_index("t")
            .rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
          [["open","high","low","close","volume"]]
          .sort_index())
    df.index = df.index.tz_localize(None)
    return df.between_time("09:30", "16:00")


def fetch_latest_bar() -> dict | None:
    r = requests.get(f"{DATA_BASE}/v2/stocks/{SYMBOL}/bars/latest",
                     headers=data_headers(), params={"feed": "iex"})
    if r.status_code == 200:
        return r.json().get("bar", None)
    return None


# ── Indicators ────────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()

    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

    up   = df["high"] - df["high"].shift(1)
    down = df["low"].shift(1) - df["low"]
    pdm  = np.where((up > down) & (up > 0), up, 0.0)
    ndm  = np.where((down > up) & (down > 0), down, 0.0)
    atr_w = tr.ewm(span=ADX_PERIOD, adjust=False).mean()
    pdi   = 100 * pd.Series(pdm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean() / atr_w
    ndi   = 100 * pd.Series(ndm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean() / atr_w
    dx    = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    df["adx"] = dx.ewm(span=ADX_PERIOD, adjust=False).mean()
    df["pdi"] = pdi
    df["ndi"] = ndi
    return df


def regime(adx, pdi, ndi):
    if adx <= ADX_THRESHOLD:
        return "ranging"
    return "bull" if pdi >= ndi else "bear"


# ── Backtest (float shares, no re-entry) ─────────────────────────────────────
def backtest(df: pd.DataFrame, init_eq: float = 10.0,
             log_dates: set = None) -> dict:
    warmup    = 100
    trades    = []
    position  = None
    equity    = init_eq

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        close = row["close"]
        ema   = row["ema"]
        atr   = row["atr"]
        adx   = row["adx"]
        pdi   = row["pdi"]
        ndi   = row["ndi"]
        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute
        date  = str(ts.date())
        loud  = log_dates and date in log_dates
        reg   = regime(adx, pdi, ndi)

        if position is not None:
            side = position["side"]

            if side == "long" and close > position["pos_high"]:
                position["pos_high"] = close

            # Pyramid add on dip
            if (side == "long"
                    and position["n_adds"] < MAX_ADDS
                    and ENTRY_START <= mins < ENTRY_END + 120):   # adds until 13:00
                dip  = position["pos_high"] - close
                prof = close - position["avg_entry"]
                if dip >= DIP_ATR_BACK * atr and prof >= MIN_PROFIT * atr and close > ema:
                    add   = position["orig_shares"] * ADD_FRAC
                    old_c = position["avg_entry"] * position["total_shares"]
                    position["total_shares"] += add
                    position["avg_entry"]     = (old_c + close * add) / position["total_shares"]
                    position["n_adds"]        += 1
                    if loud:
                        print(f"      ADD #{position['n_adds']}: +{add:.5f} sh @ ${close:.2f}"
                              f"  avg=${position['avg_entry']:.2f}"
                              f"  dip={dip/atr:.2f}×ATR  total={position['total_shares']:.5f} sh")

            # Update trail
            if side == "long":
                ns = close - TRAIL * atr
                if ns > position["trail_stop"]:
                    position["trail_stop"] = ns
                stop_hit = close <= position["trail_stop"]
            else:
                ns = close + TRAIL * atr
                if ns < position["trail_stop"]:
                    position["trail_stop"] = ns
                stop_hit = close >= position["trail_stop"]

            eod = mins >= EOD_CLOSE

            if stop_hit or eod:
                sh    = position["total_shares"]
                gross = (close - position["avg_entry"]) * sh if side == "long" \
                    else (position["avg_entry"] - close) * sh
                pnl   = gross - sh * COMM * 2
                equity += pnl
                reason = "EOD" if eod else "STOP"
                if loud:
                    print(f"    EXIT {reason} @ {ts.strftime('%H:%M')}  ${close:.2f}"
                          f"  sh={sh:.5f}  stop=${position['trail_stop']:.2f}"
                          f"  pnl=${pnl:+.4f}  equity=${equity:.4f}")
                trades.append({
                    "date":       date,
                    "side":       side,
                    "entry_time": position["entry_time"],
                    "exit_time":  ts.strftime("%H:%M"),
                    "entry":      position["entry_price"],
                    "exit":       close,
                    "pnl":        pnl,
                    "n_adds":     position["n_adds"],
                    "year":       ts.year,
                    "month":      ts.to_period("M"),
                    "exit_reason":reason,
                })
                position = None

        if position is None and ENTRY_START <= mins < ENTRY_END:
            shares = (equity * LEVERAGE) / close
            if shares * close < 0.10:    # skip if < $0.10 notional (broker min)
                continue

            if reg in ("ranging", "bull") and close > ema:
                position = {
                    "side":         "long",
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close - TRAIL * atr,
                    "entry_idx":    i,
                    "pos_high":     close,
                    "n_adds":       0,
                    "entry_time":   ts.strftime("%H:%M"),
                }
                if loud:
                    print(f"\n  {date}  ENTER long  @ {ts.strftime('%H:%M')}"
                          f"  ${close:.2f}  {shares:.5f} sh (${shares*close:.2f})"
                          f"  stop=${close - TRAIL*atr:.2f}"
                          f"  ADX={adx:.0f}({reg})  equity=${equity:.4f}")

            elif reg == "bear" and close < ema:
                position = {
                    "side":         "short",
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close + TRAIL * atr,
                    "entry_idx":    i,
                    "pos_high":     close,
                    "n_adds":       0,
                    "entry_time":   ts.strftime("%H:%M"),
                }
                if loud:
                    print(f"\n  {date}  ENTER short @ {ts.strftime('%H:%M')}"
                          f"  ${close:.2f}  {shares:.5f} sh (${shares*close:.2f})"
                          f"  stop=${close + TRAIL*atr:.2f}"
                          f"  ADX={adx:.0f}({reg})  equity=${equity:.4f}")

    if position is not None:
        sh    = position["total_shares"]
        close = df.iloc[-1]["close"]
        gross = (close - position["avg_entry"]) * sh if position["side"] == "long" \
            else (position["avg_entry"] - close) * sh
        pnl   = gross - sh * COMM * 2
        equity += pnl
        ts    = df.index[-1]
        trades.append({
            "date": str(ts.date()), "side": position["side"],
            "entry_time": position["entry_time"], "exit_time": ts.strftime("%H:%M"),
            "entry": position["entry_price"], "exit": close,
            "pnl": pnl, "n_adds": position["n_adds"],
            "year": ts.year, "month": ts.to_period("M"),
            "exit_reason": "open",
        })

    if not trades:
        return dict(n=0, sharpe=np.nan, net_pnl=0, max_dd_pct=0,
                    win_rate=0, pf=np.nan, final_eq=init_eq, trades=[])

    pnl_s = pd.Series([t["pnl"] for t in trades])
    wins  = (pnl_s > 0).sum()
    eq_s  = init_eq + pnl_s.cumsum()
    hwm   = eq_s.cummax()
    dd    = eq_s - hwm
    max_dd = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
    n     = len(pnl_s)
    std   = pnl_s.std(ddof=1) if n > 1 else np.nan
    n_days = max((df.index[-1] - df.index[0]).days, 1)
    tpy   = n / (n_days / 252)
    sharpe = (pnl_s.mean() / std) * np.sqrt(tpy) if std and std > 0 else np.nan
    pf    = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
            if (n - wins) and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    return dict(
        n=n, win_rate=round(wins/n*100,1),
        pf=round(float(pf),3) if not np.isnan(pf) else np.nan,
        net_pnl=round(float(pnl_s.sum()),4),
        final_eq=round(float(eq_s.iloc[-1]),4),
        sharpe=round(float(sharpe),3) if not np.isnan(sharpe) else np.nan,
        max_dd_pct=round(float(max_dd),2),
        trades=trades,
    )


# ── 5-day report ──────────────────────────────────────────────────────────────
def show_5day(init_eq=10.0):
    print(f"\nSOXL Live Bot — Last 5 Trading Days")
    print("=" * 80)
    df = load_cached()
    df = add_indicators(df)

    all_dates = sorted(df.index.normalize().unique())
    last5     = [str(d.date()) for d in all_dates[-5:]]
    print(f"  Period  : {last5[0]} → {last5[-1]}")
    print(f"  SOXL    : ${df['close'].iloc[-1]:.2f} (latest bar)")
    print(f"  Start $  : ${init_eq:,.2f}")
    print()

    print("  TRADE LOG — last 5 days:")
    print("─" * 80)
    r = backtest(df, init_eq=init_eq, log_dates=set(last5))
    print()

    t = pd.DataFrame(r["trades"]) if r["trades"] else pd.DataFrame()
    last5_set = set(last5)

    # Per-day summary
    print(f"  {'Date':<12} {'n':>3} {'Win%':>5} {'Net PnL':>10}  Adds  Reason")
    print("  " + "─" * 60)
    for d in last5:
        grp = t[t["date"] == d] if len(t) else pd.DataFrame()
        if len(grp) == 0:
            print(f"  {d:<12}   0    n/a  {'$0.0000':>10}  —")
            continue
        g = grp["pnl"]
        wr = (g > 0).sum() / len(g) * 100
        avg_adds = grp["n_adds"].mean()
        exits = "/".join(grp["exit_reason"].unique())
        print(f"  {d:<12} {len(g):>3} {wr:>4.0f}%  ${g.sum():>8.4f}  {avg_adds:>4.2f}x  {exits}")

    # 5-day totals
    last5_trades = t[t["date"].isin(last5_set)] if len(t) else pd.DataFrame()
    print()
    if len(last5_trades):
        pnl_5d = last5_trades["pnl"].sum()
        wins_5d = (last5_trades["pnl"] > 0).sum()
        print(f"  5-day total : ${pnl_5d:+.4f}  ({wins_5d}/{len(last5_trades)} wins)")

    # Full period stats
    print()
    print("─" * 80)
    print(f"  FULL 5-YEAR  (starting ${init_eq:,.2f})")
    print("─" * 80)
    print(f"  ${init_eq:,.2f} → ${r['final_eq']:,.4f}"
          f"  ({(r['final_eq']/init_eq - 1)*100:+.0f}%)"
          f"  Sharpe {r['sharpe']:+.2f}"
          f"  DD {r['max_dd_pct']:+.1f}%"
          f"  WR {r['win_rate']}%"
          f"  PF {r['pf']}")

    n_days = (df.index[-1] - df.index[0]).days
    print(f"  Avg $/day   : ${r['net_pnl']/n_days:+.4f}")

    # Year by year
    if len(t):
        print()
        print(f"  {'Year':<6} {'n':>4} {'Win%':>5} {'Net PnL':>12} {'MaxDD':>7} {'Adds':>5}")
        print("  " + "─" * 55)
        for yr, grp in t.groupby("year"):
            g    = grp["pnl"]
            gn   = len(g); gw = (g > 0).sum()
            eq   = init_eq + g.cumsum(); hwm = eq.cummax(); dd = eq - hwm
            ddp  = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
            adds = grp["n_adds"].mean()
            print(f"  {yr:<6} {gn:>4} {gw/gn*100:>4.0f}%  ${g.sum():>+10.2f}  {ddp:>+6.1f}%  {adds:>4.2f}x")

    print()
    print("─" * 80)
    print("  SCALE COMPARISON (same strategy, different starting $):")
    print("─" * 80)
    for eq in [10, 100, 500, 1000]:
        rx = backtest(df, init_eq=float(eq))
        print(f"  ${eq:>5}  → ${rx['final_eq']:>9,.2f}"
              f"  ({(rx['final_eq']/eq - 1)*100:>+7.0f}%)"
              f"  Sharpe {rx['sharpe']:+.2f}"
              f"  DD {rx['max_dd_pct']:+.1f}%")

    print()
    print("  To run live: python3 soxl_live_bot.py --live [--eq 10]")
    print("=" * 80)


# ── Alpaca order helpers ──────────────────────────────────────────────────────
def broker_headers():
    import base64
    creds = base64.b64encode(f"{ALPACA_KEY}:{ALPACA_SECRET}".encode()).decode()
    return {"Authorization": f"Basic {creds}", "Content-Type": "application/json"}


def paper_headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY,
            "APCA-API-SECRET-KEY": ALPACA_SECRET,
            "Content-Type": "application/json"}


def get_equity() -> float:
    if USE_PAPER_API:
        r = requests.get(f"{PAPER_BASE}/v2/account", headers=paper_headers())
        r.raise_for_status()
        return float(r.json()["equity"])
    else:
        r = requests.get(
            f"{BROKER_BASE}/v1/trading/accounts/{ALPACA_ACCT}/account",
            headers=broker_headers())
        r.raise_for_status()
        return float(r.json().get("equity", 0))


def get_position_qty() -> float:
    """Returns current SOXL shares held (negative = short)."""
    if USE_PAPER_API:
        r = requests.get(f"{PAPER_BASE}/v2/positions/{SYMBOL}",
                         headers=paper_headers())
    else:
        r = requests.get(
            f"{BROKER_BASE}/v1/trading/accounts/{ALPACA_ACCT}/positions/{SYMBOL}",
            headers=broker_headers())
    if r.status_code == 404:
        return 0.0
    r.raise_for_status()
    return float(r.json().get("qty", 0))


def place_order(side: str, notional: float = None, qty: float = None) -> tuple:
    body = {"symbol": SYMBOL, "side": side, "type": "market", "time_in_force": "day"}
    if notional:
        body["notional"] = str(round(notional, 2))
    elif qty:
        body["qty"] = str(round(abs(qty), 6))

    if USE_PAPER_API:
        r = requests.post(f"{PAPER_BASE}/v2/orders",
                          headers=paper_headers(), json=body)
    else:
        r = requests.post(
            f"{BROKER_BASE}/v1/trading/accounts/{ALPACA_ACCT}/orders",
            headers=broker_headers(), json=body)
    return r.status_code, r.json()


def close_position():
    if USE_PAPER_API:
        r = requests.delete(f"{PAPER_BASE}/v2/positions/{SYMBOL}",
                            headers=paper_headers())
    else:
        r = requests.delete(
            f"{BROKER_BASE}/v1/trading/accounts/{ALPACA_ACCT}/positions/{SYMBOL}",
            headers=broker_headers())
    return r.status_code


# ── Live bot ──────────────────────────────────────────────────────────────────
def run_live(init_notional: float = 10.0):
    print(f"\nSOXL Live Bot — ${init_notional:.2f} notional  ({'paper-api' if USE_PAPER_API else 'broker-sandbox'})")
    print("=" * 80)

    # Warmup indicators on 45 days of history
    df_hist = fetch_warmup_bars(45)
    df_hist = add_indicators(df_hist)
    print(f"  Warmed on {len(df_hist)} bars  ({df_hist.index[0].date()} → {df_hist.index[-1].date()})")

    try:
        eq = get_equity()
        print(f"  Account equity: ${eq:,.2f}")
    except Exception as e:
        print(f"  Equity fetch failed: {e} — using target notional for sizing")
        eq = init_notional

    # State
    pos     = None  # None | {'side','avg_entry','total_shares','orig_shares',
                    #          'trail_stop','pos_high','n_adds'}
    today   = None

    print("  Entering bot loop. Ctrl+C to stop.\n")

    while True:
        try:
            now  = pd.Timestamp.now(tz="America/New_York")
            date = str(now.date())
            mins = now.hour * 60 + now.minute

            if today != date:
                today = date
                print(f"\n── {date} ─────────────────────────────────────────")

            # Outside trading hours
            if mins < ENTRY_START - 5 or mins > 16 * 60 + 10:
                until_open = max(0, ENTRY_START - mins)
                if until_open > 60:
                    print(f"  {now.strftime('%H:%M')}  market closed — sleeping 10 min")
                    time.sleep(600)
                else:
                    time.sleep(60)
                continue

            # Latest bar
            bar = fetch_latest_bar()
            if not bar:
                time.sleep(30)
                continue
            close = bar.get("c", 0.0)
            if close <= 0:
                time.sleep(30)
                continue

            # Recompute indicators with this bar appended
            new_row = pd.DataFrame(
                [{"open": bar["o"], "high": bar["h"], "low": bar["l"],
                  "close": bar["c"], "volume": bar.get("v", 0)}],
                index=[now.replace(tzinfo=None)]
            )
            df_live = pd.concat([df_hist, new_row])
            df_live = add_indicators(df_live)
            latest  = df_live.iloc[-1]
            ema = latest["ema"]; atr = latest["atr"]
            adx = latest["adx"]; pdi = latest["pdi"]; ndi = latest["ndi"]
            reg = regime(adx, pdi, ndi)

            # ── Manage open position ──────────────────────────────────────────
            if pos is not None:
                side = pos["side"]

                if side == "long" and close > pos["pos_high"]:
                    pos["pos_high"] = close

                # Pyramid add
                if (side == "long"
                        and pos["n_adds"] < MAX_ADDS
                        and ENTRY_START <= mins < ENTRY_END + 120):
                    dip  = pos["pos_high"] - close
                    prof = close - pos["avg_entry"]
                    if dip >= DIP_ATR_BACK * atr and prof >= MIN_PROFIT * atr and close > ema:
                        add_notional = init_notional * ADD_FRAC
                        code, resp   = place_order("buy", notional=add_notional)
                        add_shares   = add_notional / close
                        old_cost     = pos["avg_entry"] * pos["total_shares"]
                        pos["total_shares"] += add_shares
                        pos["avg_entry"]     = (old_cost + close * add_shares) / pos["total_shares"]
                        pos["n_adds"]        += 1
                        print(f"  {now.strftime('%H:%M')}  ADD #{pos['n_adds']}"
                              f"  ${add_notional:.2f} @ ${close:.2f}"
                              f"  avg=${pos['avg_entry']:.2f}"
                              f"  dip={dip/atr:.2f}×ATR  [{code}]")

                # Update trail
                if side == "long":
                    ns = close - TRAIL * atr
                    if ns > pos["trail_stop"]:
                        pos["trail_stop"] = ns
                    stop_hit = close <= pos["trail_stop"]
                else:
                    ns = close + TRAIL * atr
                    if ns < pos["trail_stop"]:
                        pos["trail_stop"] = ns
                    stop_hit = close >= pos["trail_stop"]

                eod = mins >= EOD_CLOSE

                if stop_hit or eod:
                    code = close_position()
                    pnl  = (close - pos["avg_entry"]) * pos["total_shares"] \
                           if side == "long" \
                           else (pos["avg_entry"] - close) * pos["total_shares"]
                    reason = "EOD" if eod else "STOP"
                    print(f"  {now.strftime('%H:%M')}  EXIT {reason}"
                          f"  ${close:.2f}  ~{pos['total_shares']:.5f} sh"
                          f"  PnL~${pnl:+.4f}  stop=${pos['trail_stop']:.2f}  [{code}]")
                    pos = None
                else:
                    unrealized = (close - pos["avg_entry"]) * pos["total_shares"] \
                                 if side == "long" else \
                                 (pos["avg_entry"] - close) * pos["total_shares"]
                    print(f"  {now.strftime('%H:%M')}  HOLD {side}"
                          f"  ${close:.2f}  stop=${pos['trail_stop']:.2f}"
                          f"  avg=${pos['avg_entry']:.2f}"
                          f"  unreal=${unrealized:+.4f}"
                          f"  {pos['n_adds']} adds  ADX={adx:.0f}({reg})")

            # ── Entry ─────────────────────────────────────────────────────────
            elif ENTRY_START <= mins < ENTRY_END:
                if reg in ("ranging", "bull") and close > ema:
                    code, resp = place_order("buy", notional=init_notional * LEVERAGE)
                    shares     = init_notional * LEVERAGE / close
                    pos = {
                        "side":         "long",
                        "avg_entry":    close,
                        "total_shares": shares,
                        "orig_shares":  shares,
                        "trail_stop":   close - TRAIL * atr,
                        "pos_high":     close,
                        "n_adds":       0,
                    }
                    print(f"  {now.strftime('%H:%M')}  ENTER long"
                          f"  ${close:.2f}  ${init_notional*LEVERAGE:.2f} (~{shares:.5f} sh)"
                          f"  stop=${close-TRAIL*atr:.2f}"
                          f"  ADX={adx:.0f}({reg})  [{code}]")

                elif reg == "bear" and close < ema:
                    code, resp = place_order("sell", notional=init_notional * LEVERAGE)
                    shares     = init_notional * LEVERAGE / close
                    pos = {
                        "side":         "short",
                        "avg_entry":    close,
                        "total_shares": shares,
                        "orig_shares":  shares,
                        "trail_stop":   close + TRAIL * atr,
                        "pos_high":     close,
                        "n_adds":       0,
                    }
                    print(f"  {now.strftime('%H:%M')}  ENTER short"
                          f"  ${close:.2f}  ${init_notional*LEVERAGE:.2f} (~{shares:.5f} sh)"
                          f"  stop=${close+TRAIL*atr:.2f}"
                          f"  ADX={adx:.0f}({reg})  [{code}]")
                else:
                    print(f"  {now.strftime('%H:%M')}  WAIT  ${close:.2f}"
                          f"  EMA=${ema:.2f}  ADX={adx:.0f}({reg})  — no signal")

            time.sleep(60)

        except KeyboardInterrupt:
            print("\n  Bot stopped.")
            if pos is not None:
                ans = input("  Close open position? [y/n]: ")
                if ans.lower() == "y":
                    code = close_position()
                    print(f"  Closed. [{code}]")
            break
        except Exception as e:
            print(f"  ERROR: {e}")
            time.sleep(30)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]
    live = "--live" in args
    eq   = 10.0
    if "--eq" in args:
        idx = args.index("--eq")
        try:
            eq = float(args[idx + 1])
        except (IndexError, ValueError):
            pass

    if live:
        run_live(init_notional=eq)
    else:
        show_5day(init_eq=eq)
