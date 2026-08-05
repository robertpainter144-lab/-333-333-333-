#!/usr/bin/env python3
"""
SOXL Improved Bot — 5-Day Detail + Alpaca Paper Live Execution
==============================================================

Improvements over base dip-pyramid:
  1. Tiered profit taking  : sell 33% at +2×ATR, 33% at +4×ATR, trail last 33%
  2. Trail tightens after partials : 2×ATR after 1st take, 1.5×ATR after 2nd
  3. Re-entry after stop   : re-enter on new EMA cross, up to 2× per day
  4. More pyramid adds     : up to 4 adds (was 2)
  5. EOD trail tightening  : 2×ATR at 15:00, 1×ATR at 15:30
  6. Fractional shares     : full float precision (works at any $ amount)
  7. Extended entry window : entries allowed to 14:00 when ADX > 45

Usage:
  python3 soxl_improved_bot.py                # 5-day backtest (default)
  python3 soxl_improved_bot.py --live         # live Alpaca paper bot
  python3 soxl_improved_bot.py --compare      # compare old vs new on 5 days
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

# Alpaca — swap to your PK-paper keys for app.alpaca.markets visibility
ALPACA_KEY    = "CK4HXKGM3HWRKLXWRU3CDLB6LX"
ALPACA_SECRET = "7i1mhApwTyzqWprLyvGvbzuDJbNSquMRTCnoiXfJSEWk"
ALPACA_ACCT   = "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV"
ALPACA_TRADE  = "https://broker-api.sandbox.alpaca.markets"
DATA_BASE     = "https://data.sandbox.alpaca.markets"

# ── Indicator params ──────────────────────────────────────────────────────────
EMA_PERIOD    = 25
ATR_PERIOD    = 14
ADX_PERIOD    = 20
ADX_THRESHOLD = 35
STRONG_ADX    = 45       # extended entry when ADX > this

# ── Trail params ──────────────────────────────────────────────────────────────
TRAIL_INIT    = 3.0      # initial trail (ATR units)
TRAIL_POST1   = 2.0      # after 1st partial take
TRAIL_POST2   = 1.5      # after 2nd partial take
TRAIL_EOD1    = 2.0      # after 15:00
TRAIL_EOD2    = 1.0      # after 15:30

# ── Partial profit taking ─────────────────────────────────────────────────────
# optimized by swarm on 2026-06-14
TAKE1_ATR     = 2.0      # take 50% at +2×ATR profit
TAKE2_ATR     = 99.0     # second tier disabled (unreachable); trail the other 50%
TAKE_FRAC     = 0.5      # fraction to sell at each level

# ── Pyramid ──────────────────────────────────────────────────────────────────
DIP_ATR_BACK  = 0.5      # pullback from pos_high to trigger add
MIN_PROFIT    = 0.3      # min profit (ATR) before pyramiding
MAX_ADDS      = 5        # max adds per trade
ADD_FRAC      = 1.0      # add size as fraction of original

# ── Timing ───────────────────────────────────────────────────────────────────
ENTRY_START   = 9 * 60 + 30
ENTRY_END_BASE = 11 * 60
ENTRY_END_EXT  = 14 * 60
PYRAMID_END   = 14 * 60 + 30
EOD_TIGHTEN   = 15 * 60
EOD_VERY_TIGHT = 15 * 60 + 30
EOD_CLOSE_SHORTS = 15 * 60 + 50

MAX_REENTRIES = 2        # re-entries per day after being stopped out

LEVERAGE      = 1.0      # SOXL already 3×; don't add margin
COMM          = 0.005    # $/share per leg


# ── Data ──────────────────────────────────────────────────────────────────────
def load_data() -> pd.DataFrame:
    df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    df = df.between_time("09:30", "16:00")
    return df


def fetch_recent_bars(n_days=10) -> pd.DataFrame:
    end   = pd.Timestamp.now(tz="America/New_York").floor("min")
    start = end - pd.DateOffset(days=n_days * 2)
    hdrs  = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    bars, token = [], None
    while True:
        params = {"symbols": SYMBOL, "timeframe": "5Min",
                  "start": start.isoformat(), "end": end.isoformat(),
                  "limit": 10_000, "feed": "iex"}
        if token:
            params["page_token"] = token
        r = requests.get(f"{DATA_BASE}/v2/stocks/bars", headers=hdrs, params=params)
        r.raise_for_status()
        data  = r.json()
        chunk = data.get("bars", {}).get(SYMBOL, [])
        bars.extend(chunk)
        token = data.get("next_page_token")
        if not token:
            break
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    df = (df.set_index("t")
            .rename(columns={"o": "open", "h": "high", "l": "low",
                             "c": "close", "v": "volume"})
          [["open", "high", "low", "close", "volume"]]
          .sort_index())
    df.index = df.index.tz_localize(None)
    return df.between_time("09:30", "16:00")


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


def classify_regime(adx, pdi, ndi):
    if adx <= ADX_THRESHOLD:
        return "ranging"
    return "bull_trend" if pdi >= ndi else "bear_trend"


# ── Core backtest (improved) ──────────────────────────────────────────────────
def run_improved(df: pd.DataFrame,
                 init_eq: float = 100_000.0,
                 log_dates: set = None,
                 verbose: bool = False) -> dict:
    """
    log_dates : set of date strings like {'2026-05-05'} to print detail for.
    """
    warmup    = 100
    trades    = []
    position  = None
    equity    = init_eq
    day_reentries = {}   # date → count of re-entries used

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
        printing = verbose or (log_dates and date in log_dates)

        regime = classify_regime(adx, pdi, ndi)
        entry_end = ENTRY_END_EXT if adx > STRONG_ADX else ENTRY_END_BASE

        # ── Manage open position ─────────────────────────────────────────────
        if position is not None:
            side = position["side"]

            # Determine active trail width based on EOD time and partials taken
            if mins >= EOD_VERY_TIGHT:
                trail_w = TRAIL_EOD2
            elif mins >= EOD_TIGHTEN:
                trail_w = TRAIL_EOD1
            elif position["partials"] >= 2:
                trail_w = TRAIL_POST2
            elif position["partials"] >= 1:
                trail_w = TRAIL_POST1
            else:
                trail_w = TRAIL_INIT

            # Track position high
            if side == "long" and close > position["pos_high"]:
                position["pos_high"] = close

            # ── Tiered partial profit taking ─────────────────────────────────
            if side == "long" and position["partials"] < 2 and position["total_shares"] > 0:
                profit_atr = (close - position["avg_entry"]) / atr if atr > 0 else 0
                take_level = TAKE2_ATR if position["partials"] == 1 else TAKE1_ATR

                if profit_atr >= take_level:
                    sell_shares = position["total_shares"] * TAKE_FRAC
                    gross = (close - position["avg_entry"]) * sell_shares
                    pnl_partial = gross - sell_shares * COMM * 2
                    equity += pnl_partial
                    position["total_shares"] -= sell_shares
                    position["partials"] += 1
                    if printing:
                        lvl = "1st" if position["partials"] == 1 else "2nd"
                        print(f"      PARTIAL {lvl}: sold {sell_shares:.4f} sh @ ${close:.2f}"
                              f"  profit={profit_atr:.1f}×ATR  locked=${pnl_partial:+.2f}"
                              f"  remaining={position['total_shares']:.4f} sh"
                              f"  trail→{trail_w}×ATR")

            # ── Pyramid: add on dip within winning long ──────────────────────
            if (side == "long"
                    and position["n_adds"] < MAX_ADDS
                    and mins < PYRAMID_END):
                dip_from_high    = position["pos_high"] - close
                profit_per_share = close - position["avg_entry"]
                trend_ok         = close > ema

                if (dip_from_high >= DIP_ATR_BACK * atr
                        and profit_per_share >= MIN_PROFIT * atr
                        and trend_ok):
                    add_shares = position["orig_shares"] * ADD_FRAC
                    old_cost   = position["avg_entry"] * position["total_shares"]
                    new_cost   = close * add_shares
                    position["total_shares"] += add_shares
                    position["avg_entry"]     = (old_cost + new_cost) / position["total_shares"]
                    position["n_adds"]        += 1
                    if printing:
                        profit_atr = (close - position["avg_entry"]) / atr if atr > 0 else 0
                        print(f"      ADD #{position['n_adds']}: +{add_shares:.4f} sh @ ${close:.2f}"
                              f"  avg_entry=${position['avg_entry']:.2f}"
                              f"  dip={dip_from_high/atr:.2f}×ATR  total={position['total_shares']:.4f} sh")

            # ── Update trailing stop ─────────────────────────────────────────
            if side == "long":
                new_stop = close - trail_w * atr
                if new_stop > position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close <= position["trail_stop"]
            else:
                new_stop = close + trail_w * atr
                if new_stop < position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close >= position["trail_stop"]

            eod_close = (mins >= EOD_CLOSE_SHORTS and side == "short") or mins >= 15 * 60 + 55

            if stop_hit or eod_close:
                shares = position["total_shares"]
                gross  = (close - position["avg_entry"]) * shares if side == "long" \
                     else (position["avg_entry"] - close) * shares
                pnl    = gross - shares * COMM * 2
                equity += pnl
                reason = "EOD" if eod_close else "STOP"
                if printing:
                    total_pnl = pnl + position.get("locked_pnl", 0)
                    print(f"    EXIT  {reason} @ {ts.strftime('%H:%M')}  "
                          f"price=${close:.2f}  shares={shares:.4f}"
                          f"  trail_stop=${position['trail_stop']:.2f}"
                          f"  trade_pnl=${pnl:+.2f}  total_w_partials=${total_pnl:+.2f}"
                          f"  equity=${equity:,.2f}")
                trades.append({
                    "date": date, "side": side, "pnl": pnl,
                    "bars_held": i - position["entry_idx"],
                    "exit": "eod" if eod_close else "trail",
                    "regime": position["regime"],
                    "n_adds": position["n_adds"],
                    "partials": position["partials"],
                    "year": ts.year, "month": ts.to_period("M"),
                    "entry_time": position["entry_time"],
                    "exit_time": ts.strftime("%H:%M"),
                })
                position = None

        # ── Entry ────────────────────────────────────────────────────────────
        reentries_today = day_reentries.get(date, 0)
        can_enter = (position is None
                     and ENTRY_START <= mins < entry_end
                     and reentries_today <= MAX_REENTRIES)

        if can_enter:
            shares = (equity * LEVERAGE) / close   # fractional
            if shares * close < 1.0:               # min $1 notional
                continue

            if regime in ("ranging", "bull_trend") and close > ema:
                position = {
                    "side":         "long",
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close - TRAIL_INIT * atr,
                    "entry_idx":    i,
                    "regime":       regime,
                    "pos_high":     close,
                    "n_adds":       0,
                    "partials":     0,
                    "locked_pnl":   0,
                    "entry_time":   ts.strftime("%H:%M"),
                }
                if printing:
                    print(f"\n  {date}  ENTRY long @ {ts.strftime('%H:%M')}  "
                          f"price=${close:.2f}  shares={shares:.4f}"
                          f"  stop=${close - TRAIL_INIT * atr:.2f}"
                          f"  ADX={adx:.1f}({regime})  equity=${equity:,.2f}")

            elif regime == "bear_trend" and close < ema:
                position = {
                    "side":         "short",
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close + TRAIL_INIT * atr,
                    "entry_idx":    i,
                    "regime":       regime,
                    "pos_high":     close,
                    "n_adds":       0,
                    "partials":     0,
                    "locked_pnl":   0,
                    "entry_time":   ts.strftime("%H:%M"),
                }
                if printing:
                    print(f"\n  {date}  ENTRY short @ {ts.strftime('%H:%M')}  "
                          f"price=${close:.2f}  shares={shares:.4f}"
                          f"  stop=${close + TRAIL_INIT * atr:.2f}"
                          f"  ADX={adx:.1f}({regime})  equity=${equity:,.2f}")

    if position is not None:
        side   = position["side"]
        close  = df.iloc[-1]["close"]
        ts     = df.index[-1]
        shares = position["total_shares"]
        gross  = (close - position["avg_entry"]) * shares if side == "long" \
             else (position["avg_entry"] - close) * shares
        pnl    = gross - shares * COMM * 2
        equity += pnl
        trades.append({
            "date": str(ts.date()), "side": side, "pnl": pnl,
            "bars_held": len(df) - 1 - position["entry_idx"],
            "exit": "end_of_data", "regime": position["regime"],
            "n_adds": position["n_adds"], "partials": position["partials"],
            "year": ts.year, "month": ts.to_period("M"),
            "entry_time": position["entry_time"],
            "exit_time": ts.strftime("%H:%M"),
        })

    if not trades:
        return dict(n=0, sharpe=np.nan, net_pnl=0, max_dd_pct=0,
                    win_rate=0, pf=np.nan, final_eq=init_eq, trades=[])

    pnl_s      = pd.Series([t["pnl"] for t in trades])
    eq_s       = init_eq + pnl_s.cumsum()
    hwm        = eq_s.cummax()
    dd         = eq_s - hwm
    max_dd_pct = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
    n          = len(pnl_s)
    wins       = (pnl_s > 0).sum()
    losses     = (pnl_s <= 0).sum()
    std        = pnl_s.std(ddof=1) if n > 1 else np.nan
    n_days_    = max((df.index[-1] - df.index[0]).days, 1)
    tpy        = n / (n_days_ / 252)
    sharpe     = (pnl_s.mean() / std) * np.sqrt(tpy) if std and std > 0 else np.nan
    pf         = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
                 if losses and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    return dict(
        n=n, win_rate=round(wins / n * 100, 1),
        pf=round(float(pf), 3) if not np.isnan(pf) else np.nan,
        net_pnl=round(float(pnl_s.sum()), 2),
        final_eq=round(float(eq_s.iloc[-1]), 2),
        sharpe=round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        max_dd_pct=round(float(max_dd_pct), 2),
        trades=trades,
    )


# ── Old baseline (no partials, no re-entry, max_adds=2) ──────────────────────
def run_baseline(df, init_eq=100_000.0):
    """Replicate the best config from previous sweep for comparison."""
    warmup   = 100
    trades   = []
    position = None
    equity   = init_eq

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
        regime = classify_regime(adx, pdi, ndi)

        if position is not None:
            side = position["side"]
            if side == "long" and close > position["pos_high"]:
                position["pos_high"] = close

            if (side == "long" and position["n_adds"] < 2
                    and ENTRY_START <= mins < ENTRY_END_BASE + 120):
                dip   = position["pos_high"] - close
                prof  = close - position["avg_entry"]
                if dip >= 0.5 * atr and prof >= 0.5 * atr and close > ema:
                    add  = position["orig_shares"] * 1.0
                    old  = position["avg_entry"] * position["total_shares"]
                    position["total_shares"] += add
                    position["avg_entry"] = (old + close * add) / position["total_shares"]
                    position["n_adds"] += 1

            if side == "long":
                ns = close - 3.0 * atr
                if ns > position["trail_stop"]:
                    position["trail_stop"] = ns
                stop_hit = close <= position["trail_stop"]
            else:
                ns = close + 3.0 * atr
                if ns < position["trail_stop"]:
                    position["trail_stop"] = ns
                stop_hit = close >= position["trail_stop"]

            eod = mins >= 15 * 60 + 55

            if stop_hit or eod:
                shares = position["total_shares"]
                gross  = (close - position["avg_entry"]) * shares if side == "long" \
                     else (position["avg_entry"] - close) * shares
                pnl    = gross - shares * COMM * 2
                equity += pnl
                trades.append({
                    "date": date, "side": side, "pnl": pnl,
                    "n_adds": position["n_adds"],
                    "year": ts.year, "month": ts.to_period("M"),
                })
                position = None

        if position is None and ENTRY_START <= mins < ENTRY_END_BASE:
            shares = (equity * LEVERAGE) / close
            if shares * close < 1.0:
                continue
            if regime in ("ranging", "bull_trend") and close > ema:
                position = {"side": "long", "entry_price": close,
                            "avg_entry": close, "total_shares": shares,
                            "orig_shares": shares,
                            "trail_stop": close - 3.0 * atr,
                            "entry_idx": i, "regime": regime,
                            "pos_high": close, "n_adds": 0}
            elif regime == "bear_trend" and close < ema:
                position = {"side": "short", "entry_price": close,
                            "avg_entry": close, "total_shares": shares,
                            "orig_shares": shares,
                            "trail_stop": close + 3.0 * atr,
                            "entry_idx": i, "regime": regime,
                            "pos_high": close, "n_adds": 0}

    if not trades:
        return dict(n=0, net_pnl=0, final_eq=init_eq, trades=[])
    pnl_s = pd.Series([t["pnl"] for t in trades])
    return dict(n=len(pnl_s), net_pnl=round(float(pnl_s.sum()), 2),
                win_rate=round((pnl_s > 0).mean() * 100, 1),
                final_eq=round(float(init_eq + pnl_s.sum()), 2), trades=trades)


# ── Alpaca paper trading helpers ──────────────────────────────────────────────
def alpaca_headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}


def get_account_equity(use_broker=True):
    if use_broker:
        import base64
        creds = base64.b64encode(f"{ALPACA_KEY}:{ALPACA_SECRET}".encode()).decode()
        hdrs = {"Authorization": f"Basic {creds}"}
        r = requests.get(
            f"{ALPACA_TRADE}/v1/trading/accounts/{ALPACA_ACCT}/account",
            headers=hdrs)
        r.raise_for_status()
        return float(r.json().get("equity", 0))
    else:
        r = requests.get(f"{ALPACA_TRADE}/v2/account", headers=alpaca_headers())
        r.raise_for_status()
        return float(r.json()["equity"])


def get_positions():
    import base64
    creds = base64.b64encode(f"{ALPACA_KEY}:{ALPACA_SECRET}".encode()).decode()
    hdrs  = {"Authorization": f"Basic {creds}"}
    r = requests.get(
        f"{ALPACA_TRADE}/v1/trading/accounts/{ALPACA_ACCT}/positions",
        headers=hdrs)
    if r.status_code == 200:
        return r.json()
    return []


def place_order(side, notional=None, qty=None, symbol=SYMBOL):
    import base64
    creds = base64.b64encode(f"{ALPACA_KEY}:{ALPACA_SECRET}".encode()).decode()
    hdrs  = {"Authorization": f"Basic {creds}",
             "Content-Type": "application/json"}
    body = {"symbol": symbol, "side": side, "type": "market",
            "time_in_force": "day"}
    if notional:
        body["notional"] = str(round(notional, 2))
    elif qty:
        body["qty"] = str(round(qty, 6))
    r = requests.post(
        f"{ALPACA_TRADE}/v1/trading/accounts/{ALPACA_ACCT}/orders",
        headers=hdrs, json=body)
    return r.status_code, r.json()


def get_latest_bar():
    hdrs = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    r = requests.get(f"{DATA_BASE}/v2/stocks/{SYMBOL}/bars/latest",
                     headers=hdrs, params={"feed": "iex"})
    if r.status_code == 200:
        b = r.json().get("bar", {})
        return b
    return None


# ── 5-Day backtest report ─────────────────────────────────────────────────────
def run_5day_report(init_eq=10.0):
    print(f"\nSOXL Improved Strategy — Last 5 Trading Days")
    print("=" * 80)

    df = load_data()
    df = add_indicators(df)

    # Get last 5 trading days
    all_dates  = sorted(df.index.normalize().unique())
    last5_dates = [str(d.date()) for d in all_dates[-5:]]
    print(f"  Period   : {last5_dates[0]} → {last5_dates[-1]}")
    print(f"  SOXL now : ${df['close'].iloc[-1]:.2f}")
    print(f"  Start eq : ${init_eq:,.2f}")
    print()

    log_dates = set(last5_dates)

    # Run improved with verbose logging for last 5 days
    print("─" * 80)
    print("  IMPROVED STRATEGY  (tiered exits + 4 adds + re-entry + EOD tighten)")
    print("─" * 80)
    imp = run_improved(df, init_eq=init_eq, log_dates=log_dates, verbose=False)

    # Per-day summary for last 5 days
    t_imp = pd.DataFrame(imp["trades"])
    if len(t_imp):
        last5_trades_imp = t_imp[t_imp["date"].isin(last5_dates)]
    else:
        last5_trades_imp = pd.DataFrame()

    print()
    print("  Day-by-day P&L (last 5 days):")
    print(f"  {'Date':<12} {'n':>3} {'Win%':>5} {'PnL':>10}  Adds  Partials")
    print("  " + "─" * 55)
    running = init_eq
    # rebuild running equity from all trades up to start of 5-day window
    all_trades_imp = pd.DataFrame(imp["trades"]) if imp["trades"] else pd.DataFrame()
    if len(all_trades_imp):
        pre5 = all_trades_imp[~all_trades_imp["date"].isin(last5_dates)]
        running = init_eq + pre5["pnl"].sum() if len(pre5) else init_eq
    for d in last5_dates:
        grp = last5_trades_imp[last5_trades_imp["date"] == d] if len(last5_trades_imp) else pd.DataFrame()
        if len(grp) == 0:
            print(f"  {d:<12} {'0':>3}   n/a  {'$0.00':>10}  —     —")
            continue
        g     = grp["pnl"]
        wins  = (g > 0).sum()
        wr    = wins / len(g) * 100
        total = g.sum()
        running += total
        avg_adds = grp["n_adds"].mean() if "n_adds" in grp.columns else 0
        avg_partials = grp["partials"].mean() if "partials" in grp.columns else 0
        print(f"  {d:<12} {len(g):>3} {wr:>4.0f}%  ${total:>8.2f}  {avg_adds:>4.2f}x  {avg_partials:>5.2f}x")
    print()
    last5_pnl = last5_trades_imp["pnl"].sum() if len(last5_trades_imp) else 0
    print(f"  5-day P&L          : ${last5_pnl:+.2f}")

    # Full-period stats
    print()
    print("─" * 80)
    print(f"  FULL 5-YEAR SUMMARY  (${init_eq:,.2f} starting)")
    print("─" * 80)
    print(f"  Improved : ${init_eq:,.2f} → ${imp['final_eq']:,.2f}"
          f"  ({(imp['final_eq']/init_eq - 1)*100:+.0f}%)"
          f"  Sharpe {imp['sharpe']:+.2f}  DD {imp['max_dd_pct']:+.1f}%"
          f"  WR {imp['win_rate']}%")

    # Baseline comparison
    base = run_baseline(df, init_eq=init_eq)
    print(f"  Baseline : ${init_eq:,.2f} → ${base['final_eq']:,.2f}"
          f"  ({(base['final_eq']/init_eq - 1)*100:+.0f}%)"
          f"  WR {base['win_rate']}%")

    delta_pct = (imp["final_eq"] - base["final_eq"]) / base["final_eq"] * 100
    print(f"  Improvement: +${imp['final_eq'] - base['final_eq']:,.2f}"
          f"  ({delta_pct:+.1f}% better)")

    avg_daily  = imp["net_pnl"] / max((df.index[-1] - df.index[0]).days, 1)
    last12_start = (pd.Timestamp.now() - pd.DateOffset(months=12)).to_period("M")
    if len(all_trades_imp):
        last12 = all_trades_imp[all_trades_imp["month"] >= last12_start]
        avg_daily_12 = last12["pnl"].sum() / 252
    else:
        avg_daily_12 = 0
    print(f"  Avg $/day  : ${avg_daily:+.4f}  (5yr)   ${avg_daily_12:+.4f}  (last 12mo)")
    print()

    # Year-by-year
    if len(all_trades_imp):
        print(f"  {'Year':<6} {'n':>4} {'Win%':>5} {'PnL':>10}  Adds  Partials")
        print("  " + "─" * 55)
        for yr, grp in all_trades_imp.groupby("year"):
            g    = grp["pnl"]
            gn   = len(g); gw = (g > 0).sum()
            adds = grp["n_adds"].mean() if "n_adds" in grp.columns else 0
            parts = grp["partials"].mean() if "partials" in grp.columns else 0
            print(f"  {yr:<6} {gn:>4} {gw/gn*100:>4.0f}%  ${g.sum():>8.2f}  {adds:>4.2f}x  {parts:>5.2f}x")

    print()
    print("  Ready for live trading. Run with --live to start Alpaca paper bot.")
    print("=" * 80)

    return imp


# ── Live bot ──────────────────────────────────────────────────────────────────
def run_live_bot(init_eq_target=10.0):
    """
    Poll Alpaca every 60s, apply the improved strategy in real-time.
    Uses broker sandbox by default; swap to paper-api.alpaca.markets + PK keys
    for app.alpaca.markets visibility.
    """
    print(f"\nSOXL Live Bot — target ${init_eq_target} notional")
    print("=" * 80)
    print("  Fetching recent bars for indicator warmup ...")
    df_hist = fetch_recent_bars(n_days=30)
    df_hist = add_indicators(df_hist)
    print(f"  Warmed up on {len(df_hist)} bars  ({df_hist.index[0].date()} → {df_hist.index[-1].date()})")

    try:
        acct_equity = get_account_equity(use_broker=True)
        print(f"  Account equity: ${acct_equity:,.2f}")
    except Exception as e:
        print(f"  Could not fetch account equity: {e}")
        acct_equity = init_eq_target

    state = {
        "position":   None,    # None or dict with side/shares/avg_entry/etc
        "equity":     acct_equity,
        "day_trades": 0,
        "today":      None,
    }

    print(f"  Starting bot loop. Ctrl+C to stop.")
    print("─" * 80)

    while True:
        try:
            now   = pd.Timestamp.now(tz="America/New_York")
            today = str(now.date())
            mins  = now.hour * 60 + now.minute

            # Reset daily counters at start of new day
            if state["today"] != today:
                state["today"]      = today
                state["day_trades"] = 0
                print(f"\n  ── {today} ── new trading day ──")

            # Outside market hours — sleep longer
            if mins < ENTRY_START or mins > 16 * 60 + 5:
                print(f"  {now.strftime('%H:%M')}  outside market hours, sleeping 5 min ...")
                time.sleep(300)
                continue

            # Fetch latest bar
            bar = get_latest_bar()
            if not bar:
                time.sleep(30)
                continue

            close = bar.get("c", 0)
            if close <= 0:
                time.sleep(30)
                continue

            # Build a tiny df from history + this bar to compute indicators
            new_row = pd.DataFrame([{
                "open": bar["o"], "high": bar["h"], "low": bar["l"],
                "close": bar["c"], "volume": bar.get("v", 0)
            }], index=[now.tz_localize(None)])
            df_live = pd.concat([df_hist, new_row])
            df_live = add_indicators(df_live)
            latest  = df_live.iloc[-1]

            ema   = latest["ema"]
            atr   = latest["atr"]
            adx   = latest["adx"]
            pdi   = latest["pdi"]
            ndi   = latest["ndi"]
            regime = classify_regime(adx, pdi, ndi)

            pos = state["position"]

            # ── Manage open position ─────────────────────────────────────────
            if pos is not None:
                side = pos["side"]
                if side == "long" and close > pos.get("pos_high", close):
                    pos["pos_high"] = close

                # Tiered partial takes
                if side == "long" and pos["partials"] < 2:
                    profit_atr = (close - pos["avg_entry"]) / atr if atr > 0 else 0
                    take_lvl   = TAKE2_ATR if pos["partials"] == 1 else TAKE1_ATR
                    if profit_atr >= take_lvl:
                        sell_shares = pos["total_shares"] * TAKE_FRAC
                        code, resp  = place_order("sell", qty=sell_shares)
                        pos["total_shares"] -= sell_shares
                        pos["partials"] += 1
                        lvl = "1st" if pos["partials"] == 1 else "2nd"
                        print(f"  {now.strftime('%H:%M')}  PARTIAL {lvl}: sell {sell_shares:.4f} sh"
                              f"  profit={profit_atr:.1f}×ATR  remaining={pos['total_shares']:.4f}")

                # Trail width
                if mins >= EOD_VERY_TIGHT:
                    trail_w = TRAIL_EOD2
                elif mins >= EOD_TIGHTEN:
                    trail_w = TRAIL_EOD1
                elif pos["partials"] >= 2:
                    trail_w = TRAIL_POST2
                elif pos["partials"] >= 1:
                    trail_w = TRAIL_POST1
                else:
                    trail_w = TRAIL_INIT

                if side == "long":
                    ns = close - trail_w * atr
                    if ns > pos["trail_stop"]:
                        pos["trail_stop"] = ns
                    stop_hit = close <= pos["trail_stop"]
                else:
                    ns = close + trail_w * atr
                    if ns < pos["trail_stop"]:
                        pos["trail_stop"] = ns
                    stop_hit = close >= pos["trail_stop"]

                eod = mins >= 15 * 60 + 55

                if stop_hit or eod:
                    code, resp = place_order("sell" if side == "long" else "buy_to_cover",
                                             qty=pos["total_shares"])
                    reason = "EOD" if eod else "STOP"
                    pnl    = (close - pos["avg_entry"]) * pos["total_shares"]
                    print(f"  {now.strftime('%H:%M')}  EXIT {reason}  ${close:.2f}"
                          f"  shares={pos['total_shares']:.4f}  PnL~${pnl:+.2f}  [{code}]")
                    state["position"] = None
                    state["day_trades"] += 1

                print(f"  {now.strftime('%H:%M')}  HOLD {side}  ${close:.2f}"
                      f"  stop=${pos['trail_stop']:.2f}  avg=${pos['avg_entry']:.2f}"
                      f"  PnL~${(close - pos['avg_entry']) * pos['total_shares']:+.2f}"
                      f"  [{trail_w}×ATR trail]")

            # ── Entry signal ─────────────────────────────────────────────────
            entry_end = ENTRY_END_EXT if adx > STRONG_ADX else ENTRY_END_BASE
            if (state["position"] is None
                    and ENTRY_START <= mins < entry_end
                    and state["day_trades"] <= MAX_REENTRIES):
                if regime in ("ranging", "bull_trend") and close > ema:
                    notional = init_eq_target * LEVERAGE
                    code, resp = place_order("buy", notional=notional)
                    shares = notional / close
                    state["position"] = {
                        "side":         "long",
                        "avg_entry":    close,
                        "total_shares": shares,
                        "orig_shares":  shares,
                        "trail_stop":   close - TRAIL_INIT * atr,
                        "pos_high":     close,
                        "n_adds":       0,
                        "partials":     0,
                    }
                    print(f"  {now.strftime('%H:%M')}  ENTER long  ${close:.2f}"
                          f"  notional=${notional:.2f}  ~{shares:.4f} sh"
                          f"  stop=${close - TRAIL_INIT * atr:.2f}"
                          f"  ADX={adx:.1f}({regime})  [{code}]")

            time.sleep(60)

        except KeyboardInterrupt:
            print("\n  Bot stopped by user.")
            break
        except Exception as e:
            print(f"  ERROR: {e}")
            time.sleep(30)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "--5day"

    if mode == "--live":
        run_live_bot(init_eq_target=10.0)
    elif mode == "--compare":
        df = load_data()
        df = add_indicators(df)
        all_dates  = sorted(df.index.normalize().unique())
        last5_dates = set(str(d.date()) for d in all_dates[-5:])
        print("\nOLD baseline — last 5 days:")
        base = run_baseline(df, 10.0)
        bt = pd.DataFrame(base["trades"])
        if len(bt):
            print(bt[bt["date"].isin(last5_dates)][["date","side","pnl","n_adds"]].to_string(index=False))
        print(f"  5-day PnL: ${bt[bt['date'].isin(last5_dates)]['pnl'].sum():+.2f}" if len(bt) else "  No trades")

        print("\nNEW improved — last 5 days:")
        imp = run_improved(df, 10.0, log_dates=last5_dates, verbose=False)
        it = pd.DataFrame(imp["trades"])
        if len(it):
            cols = [c for c in ["date","side","pnl","n_adds","partials","entry_time","exit_time"] if c in it.columns]
            print(it[it["date"].isin(last5_dates)][cols].to_string(index=False))
        print(f"  5-day PnL: ${it[it['date'].isin(last5_dates)]['pnl'].sum():+.2f}" if len(it) else "  No trades")
    else:
        run_5day_report(init_eq=10.0)
