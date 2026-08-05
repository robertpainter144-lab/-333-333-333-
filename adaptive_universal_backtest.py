#!/usr/bin/env python3
"""
Adaptive Universal Backtest — any Alpaca-supported symbol
==========================================================
Adapts to market conditions at TWO timeframes:

  DAILY regime (higher timeframe context):
    - Build daily bars from 5-min data
    - Daily EMA trend filter: only long above daily EMA, only short below
    - Daily ADX: only trade when daily trend has real momentum (ADX > threshold)
    - Daily vol regime: classify the day's character before the open

  INTRADAY confirmation (entry quality):
    - Volume trigger: only enter if current bar volume > N× time-slot average
      (volume profile normalized per 30-min bucket — avoids penalizing 10:30 bars
       vs. the high-volume open)
    - Intraday vol regime: ATR percentile → adaptive EMA period + trail mult

  Why this matters for single stocks like CX:
    - CX 2023: $8→$13 sustained uptrend → daily EMA filter catches the whole move
    - CX 2022/2024: downtrends → daily filter blocks longs, takes shorts instead
    - Blind intraday-only strategy took long entries INTO those downtrends → -$47k/-$52k

Sweep (48 combinations):
  daily_ema_period : [10, 20]          daily EMA period for trend direction
  daily_adx_min    : [0, 20, 25]       minimum daily ADX to allow entries (0=off)
  vol_confirm_mult : [0.0, 1.0, 1.5]  bar vol must be > N× its time-slot average (0=off)
  vol_trail_adapt  : [False, True]     adapt intraday trail mult to vol regime

Usage: change SYMBOL at top. Data auto-fetched & cached as {symbol}_5min_bars.csv.
"""

import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOL = "CX"   # ← change to any ticker

ALPACA_KEY    = "CK4HXKGM3HWRKLXWRU3CDLB6LX"
ALPACA_SECRET = "7i1mhApwTyzqWprLyvGvbzuDJbNSquMRTCnoiXfJSEWk"
DATA_BASE     = "https://data.sandbox.alpaca.markets"

ATR_PERIOD    = 14
ADX_PERIOD    = 20
INTRA_ADX_THR = 35      # intraday ADX threshold (existing logic)
ENTRY_START   = 9 * 60 + 30
ENTRY_END     = 11 * 60
EOD_MINS      = 15 * 60 + 50
INITIAL_EQ    = 100_000.0
LEVERAGE      = 2.0
COMM          = 0.005

EMA_BY_VOL   = (15, 25, 35)
TRAIL_BY_VOL = (2.0, 3.0, 4.0)
VOL_RANK_WIN = 63 * 78

BASELINE = dict(symbol="SPY", sharpe=0.94, net_pnl=144_674, max_dd_pct=-12.1)

S_DAILY_EMA  = [10, 20]
S_DAILY_ADX  = [0, 20, 25]
S_VOL_CONF   = [0.0, 1.0, 1.5]
S_TRAIL_ADPT = [False, True]


# ── Data ──────────────────────────────────────────────────────────────────────
def fetch_bars(symbol: str, years: int = 5) -> pd.DataFrame:
    end   = pd.Timestamp.now(tz="America/New_York").floor("min")
    start = end - pd.DateOffset(years=years)
    hdrs  = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    bars, token = [], None
    # Try SIP first (needed for ADRs like CX), fall back to IEX
    for feed in ("sip", "iex"):
        bars, token = [], None
        try:
            while True:
                params = dict(symbols=symbol, timeframe="5Min",
                              start=start.isoformat(), end=end.isoformat(),
                              limit=10_000, feed=feed)
                if token:
                    params["page_token"] = token
                r = requests.get(f"{DATA_BASE}/v2/stocks/bars", headers=hdrs, params=params)
                r.raise_for_status()
                data  = r.json()
                chunk = data.get("bars", {}).get(symbol, [])
                bars.extend(chunk)
                token = data.get("next_page_token")
                print(f"    {len(bars):,} bars ({feed}) ...", end="\r")
                if not token:
                    break
            if bars:
                print(f"\n  {len(bars):,} total ({feed})")
                break
        except Exception:
            continue
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    df = (df.set_index("t")
            .rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
          [["open","high","low","close","volume"]]
          .sort_index())
    df.index = df.index.tz_localize(None)
    df = df.between_time("09:30", "16:00")
    return df


def load_or_fetch(symbol: str) -> pd.DataFrame:
    cache = Path(f"{symbol.lower()}_5min_bars.csv")
    if cache.exists():
        df = pd.read_csv(cache, index_col=0, parse_dates=True)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, utc=True)
        if df.index.tz is not None:
            df.index = df.index.tz_convert("America/New_York").tz_localize(None)
        df = df.between_time("09:30", "16:00")
        print(f"  Loaded {len(df):,} bars  ({df.index[0].date()} → {df.index[-1].date()})")
        return df
    print(f"  Fetching {symbol} ...")
    df = fetch_bars(symbol)
    df.to_csv(cache)
    print(f"  Saved → {cache}")
    return df


# ── Daily regime ──────────────────────────────────────────────────────────────
def build_daily_regime(df: pd.DataFrame) -> pd.DataFrame:
    """
    Resample 5-min bars to daily OHLCV, compute EMA + ADX on daily bars.
    Returns a DataFrame indexed by DATE with columns:
      ema_10, ema_20, daily_adx, daily_pdi, daily_ndi, daily_close
    """
    d = df.resample("B").agg(
        open=("open","first"), high=("high","max"),
        low=("low","min"),   close=("close","last"),
        volume=("volume","sum")
    ).dropna(subset=["close"])

    for p in (10, 20):
        d[f"ema_{p}"] = d["close"].ewm(span=p, adjust=False).mean()

    hl  = d["high"] - d["low"]
    hcp = (d["high"] - d["close"].shift(1)).abs()
    lcp = (d["low"]  - d["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    up   = d["high"] - d["high"].shift(1)
    down = d["low"].shift(1) - d["low"]
    pdm  = np.where((up > down) & (up > 0), up, 0.0)
    ndm  = np.where((down > up) & (down > 0), down, 0.0)
    atr_w = tr.ewm(span=14, adjust=False).mean()
    pdi   = 100 * pd.Series(pdm, index=d.index).ewm(span=14, adjust=False).mean() / atr_w
    ndi   = 100 * pd.Series(ndm, index=d.index).ewm(span=14, adjust=False).mean() / atr_w
    dx    = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    d["daily_adx"] = dx.ewm(span=14, adjust=False).mean()
    d["daily_pdi"] = pdi
    d["daily_ndi"] = ndi

    # Use PREVIOUS day's values (no lookahead)
    for col in ["ema_10","ema_20","daily_adx","daily_pdi","daily_ndi","close"]:
        d[f"prev_{col}"] = d[col].shift(1)

    return d


# ── Volume profile ────────────────────────────────────────────────────────────
def build_volume_profile(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    For each bar, compute the rolling average volume for that TIME SLOT
    across the previous `window` trading days.
    Returns a Series aligned to df.index.
    """
    df2     = df.copy()
    df2["slot"] = df2.index.strftime("%H:%M")
    df2["date"] = df2.index.date

    avgs = {}
    for slot, grp in df2.groupby("slot"):
        # rolling mean of volume for this time slot, lagged by 1 day
        rolled = grp["volume"].rolling(window, min_periods=5).mean().shift(1)
        avgs[slot] = rolled

    result = pd.Series(index=df.index, dtype=float)
    for slot, ser in avgs.items():
        result.update(ser)
    return result


# ── 5-min indicators ──────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    for p in set(EMA_BY_VOL):
        df[f"ema_{p}"] = df["close"].ewm(span=p, adjust=False).mean()

    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

    df["natr"]     = df["atr"] / df["close"]
    df["vol_rank"] = df["natr"].rolling(VOL_RANK_WIN, min_periods=300).rank(pct=True)

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


# ── Intraday adaptive params ───────────────────────────────────────────────────
def get_intraday_params(vol_rank, adx, pdi, ndi, vol_trail_adapt: bool):
    if np.isnan(vol_rank) or vol_rank <= 0.33:
        bucket = 0
    elif vol_rank <= 0.67:
        bucket = 1
    else:
        bucket = 2
    ema_col = f"ema_{EMA_BY_VOL[bucket]}"
    trail   = TRAIL_BY_VOL[bucket] if vol_trail_adapt else TRAIL_BY_VOL[1]

    if adx > INTRA_ADX_THR and pdi >= ndi:
        intra_regime = "bull"
    elif adx > INTRA_ADX_THR and ndi > pdi:
        intra_regime = "bear"
    else:
        intra_regime = "ranging"

    return ema_col, trail, intra_regime


# ── Backtest ──────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, daily: pd.DataFrame, vol_avg: pd.Series,
                 daily_ema_period: int, daily_adx_min: int,
                 vol_confirm_mult: float, vol_trail_adapt: bool) -> dict:

    daily_ema_col  = f"prev_ema_{daily_ema_period}"
    daily_adx_col  = "prev_daily_adx"
    daily_pdi_col  = "prev_daily_pdi"
    daily_ndi_col  = "prev_daily_ndi"
    daily_close_col = "prev_close"

    # Pre-build a date → daily row mapping for fast lookup
    daily_lookup = daily.to_dict("index")

    warmup   = 400
    trades   = []
    position = None
    equity   = INITIAL_EQ

    for i in range(warmup, len(df)):
        row      = df.iloc[i]
        ts       = df.index[i]
        close    = row["close"]
        atr      = row["atr"]
        adx      = row["adx"]
        pdi      = row["pdi"]
        ndi      = row["ndi"]
        vol_rank = row["vol_rank"]
        mins     = ts.hour * 60 + ts.minute
        bar_vol  = row["volume"]

        # ── Daily regime lookup ──────────────────────────────────────────────
        today = ts.date()
        # Find previous business day's data
        dr = daily_lookup.get(
            pd.Timestamp(today) - pd.offsets.BDay(0)
            if pd.Timestamp(today) in daily_lookup
            else pd.Timestamp(today)
        )
        # Fallback: scan for the right key
        if dr is None:
            ts_day = pd.Timestamp(today)
            for delta in range(0, 5):
                key = ts_day - pd.Timedelta(days=delta)
                if key in daily_lookup:
                    dr = daily_lookup[key]
                    break

        if dr is None:
            continue

        d_ema   = dr.get(daily_ema_col, np.nan)
        d_adx   = dr.get(daily_adx_col, np.nan)
        d_pdi   = dr.get(daily_pdi_col, np.nan)
        d_ndi   = dr.get(daily_ndi_col, np.nan)
        d_close = dr.get(daily_close_col, np.nan)

        if np.isnan(d_ema) or np.isnan(d_adx):
            continue

        # Daily trend direction (from previous close vs previous EMA)
        # True = bullish context (price above daily EMA), False = bearish
        daily_bullish = d_close > d_ema
        daily_bearish = d_close < d_ema

        # Daily ADX filter: skip if trend not strong enough
        if daily_adx_min > 0 and d_adx < daily_adx_min:
            # Market is ranging at daily level — no directional entries
            if position is not None and mins >= EOD_MINS:
                if position["side"] == "short":
                    pass  # let existing position manage itself
            # Still manage open position, but don't enter new ones
            if position is not None:
                side  = position["side"]
                entry = position["entry_price"]
                if side == "long":
                    new_stop = close - position["trail_mult"] * atr
                    if new_stop > position["trail_stop"]:
                        position["trail_stop"] = new_stop
                    stop_hit = close <= position["trail_stop"]
                else:
                    new_stop = close + position["trail_mult"] * atr
                    if new_stop < position["trail_stop"]:
                        position["trail_stop"] = new_stop
                    stop_hit = close >= position["trail_stop"]
                exit_now = mins >= EOD_MINS and side == "short"
                if stop_hit or exit_now:
                    shares = position["shares"]
                    gross  = (close - entry) * shares if side == "long" \
                         else (entry - close) * shares
                    pnl    = gross - shares * COMM * 2
                    equity += pnl
                    trades.append(dict(
                        side=side, pnl=pnl, bars_held=i - position["entry_idx"],
                        exit="eod" if exit_now else "trail",
                        regime=position["regime"], vol_bucket=position["vol_bucket"],
                        year=ts.year, month=ts.to_period("M"),
                    ))
                    position = None
            continue

        ema_col, trail_mult, intra_regime = get_intraday_params(
            vol_rank, adx, pdi, ndi, vol_trail_adapt
        )
        ema = row[ema_col]

        # ── Manage open position ─────────────────────────────────────────────
        if position is not None:
            side  = position["side"]
            entry = position["entry_price"]

            if side == "long":
                new_stop = close - position["trail_mult"] * atr
                if new_stop > position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close <= position["trail_stop"]
            else:
                new_stop = close + position["trail_mult"] * atr
                if new_stop < position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close >= position["trail_stop"]

            exit_now = mins >= EOD_MINS and side == "short"

            # If daily trend flips against us mid-position, tighten trail
            if side == "long" and daily_bearish:
                tighter = close - 1.5 * atr
                if tighter > position["trail_stop"]:
                    position["trail_stop"] = tighter
            elif side == "short" and daily_bullish:
                tighter = close + 1.5 * atr
                if tighter < position["trail_stop"]:
                    position["trail_stop"] = tighter

            if stop_hit or exit_now:
                shares = position["shares"]
                gross  = (close - entry) * shares if side == "long" \
                     else (entry - close) * shares
                pnl    = gross - shares * COMM * 2
                equity += pnl
                trades.append(dict(
                    side=side, pnl=pnl, bars_held=i - position["entry_idx"],
                    exit="eod" if exit_now else "trail",
                    regime=position["regime"], vol_bucket=position["vol_bucket"],
                    year=ts.year, month=ts.to_period("M"),
                ))
                position = None

        # ── Entry ────────────────────────────────────────────────────────────
        if position is None and ENTRY_START <= mins < ENTRY_END:
            shares = int((equity * LEVERAGE) / close)
            if shares < 1:
                continue

            # Volume confirmation: is this bar unusually active?
            avg_vol = vol_avg.get(ts, np.nan)
            vol_ok  = (vol_confirm_mult == 0.0 or np.isnan(avg_vol)
                       or bar_vol >= vol_confirm_mult * avg_vol)

            if not vol_ok:
                continue

            vb = ("low" if np.isnan(vol_rank) or vol_rank <= 0.33
                  else "mid" if vol_rank <= 0.67 else "high")

            # Long: requires BOTH daily bullish context AND intraday EMA cross
            can_long  = daily_bullish and intra_regime in ("ranging", "bull")
            # Short: requires BOTH daily bearish context AND intraday EMA cross
            can_short = daily_bearish and intra_regime == "bear"

            if can_long and close > ema:
                position = dict(
                    side="long", entry_price=close, shares=shares,
                    trail_stop=close - trail_mult * atr, trail_mult=trail_mult,
                    entry_idx=i, regime=intra_regime, vol_bucket=vb,
                )
            elif can_short and close <= ema:
                position = dict(
                    side="short", entry_price=close, shares=shares,
                    trail_stop=close + trail_mult * atr, trail_mult=trail_mult,
                    entry_idx=i, regime=intra_regime, vol_bucket=vb,
                )

    if position is not None:
        side   = position["side"]
        close  = df.iloc[-1]["close"]
        ts     = df.index[-1]
        shares = position["shares"]
        gross  = (close - position["entry_price"]) * shares if side == "long" \
             else (position["entry_price"] - close) * shares
        pnl    = gross - shares * COMM * 2
        equity += pnl
        trades.append(dict(
            side=side, pnl=pnl, bars_held=len(df)-1-position["entry_idx"],
            exit="end_of_data", regime=position["regime"],
            vol_bucket=position["vol_bucket"],
            year=ts.year, month=ts.to_period("M"),
        ))

    if not trades:
        return dict(n=0, sharpe=np.nan, net_pnl=0, max_dd_pct=0,
                    win_rate=0, pf=np.nan, trades=[])

    pnl_s      = pd.Series([t["pnl"] for t in trades])
    wins       = (pnl_s > 0).sum()
    losses     = (pnl_s <= 0).sum()
    eq_s       = INITIAL_EQ + pnl_s.cumsum()
    hwm        = eq_s.cummax()
    dd         = eq_s - hwm
    max_dd_pct = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
    n          = len(pnl_s)
    std        = pnl_s.std(ddof=1) if n > 1 else np.nan
    n_days     = max((df.index[-1] - df.index[0]).days, 1)
    tpy        = n / (n_days / 252)
    sharpe     = (pnl_s.mean() / std) * np.sqrt(tpy) if std and std > 0 else np.nan
    pf         = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
                 if losses and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    return dict(
        n=n,
        win_rate=round(wins / n * 100, 1),
        pf=round(float(pf), 3) if not np.isnan(pf) else np.nan,
        net_pnl=round(float(pnl_s.sum()), 0),
        sharpe=round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        max_dd_pct=round(float(max_dd_pct), 2),
        trades=trades,
    )


# ── Output ────────────────────────────────────────────────────────────────────
def print_yearly(trades, label=""):
    t = pd.DataFrame(trades)
    if label:
        print(f"\n  {label}")
    print(f"  {'Year':<6} {'n':>4} {'L/S':>9} {'Win%':>5} {'PF':>5}"
          f" {'PnL':>12} {'MaxDD':>7} {'AvgHold':>8}")
    print("  " + "─" * 74)
    for yr, grp in t.groupby("year"):
        g  = grp["pnl"]
        gn = len(g); gw = (g > 0).sum()
        ls = grp["side"].value_counts()
        lo = ls.get("long", 0); sh = ls.get("short", 0)
        eq  = INITIAL_EQ + g.cumsum()
        hwm = eq.cummax(); dd = eq - hwm
        ddp = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
        pf  = (-g[g>0].sum() / g[g<0].sum()) if (gn-gw) and g[g<0].sum() != 0 else np.nan
        avg = grp["bars_held"].mean() * 5 / 60
        print(
            f"  {yr:<6} {gn:>4} {lo:>4}L/{sh:<4}S "
            f"{gw/gn*100:>4.0f}%  "
            f"{f'{pf:.2f}' if not np.isnan(pf) else ' n/a':>5} "
            f"${g.sum():>+10,.0f}  {ddp:>+6.1f}%  {avg:>6.1f}hr"
        )


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\nAdaptive Universal Backtest — {SYMBOL}")
    print("=" * 90)

    df = load_or_fetch(SYMBOL)
    print("  Building indicators ...")
    df  = add_indicators(df)
    print("  Building daily regime ...")
    daily = build_daily_regime(df)
    print("  Building volume profile ...")
    vol_avg = build_volume_profile(df)
    print(f"  Done. {len(daily)} trading days.\n")

    total = len(S_DAILY_EMA) * len(S_DAILY_ADX) * len(S_VOL_CONF) * len(S_TRAIL_ADPT)
    print(f"SPY best (dynamic_adaptive_backtest.py): "
          f"Sharpe {BASELINE['sharpe']:+.2f}  "
          f"PnL +${BASELINE['net_pnl']:,}  DD {BASELINE['max_dd_pct']}%\n")
    print(f"Running {total} combinations on {SYMBOL} ...")
    print(f"  daily_ema_period  : {S_DAILY_EMA}")
    print(f"  daily_adx_min     : {S_DAILY_ADX}")
    print(f"  vol_confirm_mult  : {S_VOL_CONF}")
    print(f"  vol_trail_adapt   : {S_TRAIL_ADPT}")
    print()

    rows = []
    for idx, (dema, dadx, vcm, vta) in enumerate(
        product(S_DAILY_EMA, S_DAILY_ADX, S_VOL_CONF, S_TRAIL_ADPT), 1
    ):
        m = run_backtest(df, daily, vol_avg, dema, dadx, vcm, vta)
        rows.append(dict(
            daily_ema=dema, daily_adx=dadx,
            vol_confirm=vcm, trail_adapt=vta,
            **{k: v for k, v in m.items() if k != "trades"},
        ))
        sh_s  = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        pf_s  = f"{m['pf']:.2f}"      if not np.isnan(m["pf"])     else " n/a"
        pos   = not np.isnan(m["sharpe"]) and m["sharpe"] > 0
        flag  = " ◄ POSITIVE" if pos else ""
        print(
            f"  [{idx:>2}/{total}]  dEMA={dema:<2}  dADX≥{dadx:<2}  vol≥{vcm:.1f}×  "
            f"trail={'dyn' if vta else 'fix'}"
            f"  │  n={m['n']:>4}  wr={m['win_rate']:>4.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%{flag}"
        )

    results = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    results.to_csv(f"{SYMBOL.lower()}_adaptive_results.csv", index=False)

    sep = "=" * 90
    print(f"\n{sep}")
    print(f"  TOP 10 BY SHARPE  (n ≥ 30)  —  {SYMBOL}")
    print(sep)
    top = results[results["n"] >= 30].head(10)
    for _, r in top.iterrows():
        sh_s  = f"{r['sharpe']:+.2f}" if not np.isnan(r["sharpe"]) else "  n/a"
        pf_s  = f"{r['pf']:.2f}"      if not np.isnan(r["pf"])     else " n/a"
        print(
            f"  dEMA={int(r['daily_ema']):<2}  dADX≥{int(r['daily_adx']):<2}  "
            f"vol≥{r['vol_confirm']:.1f}×  trail={'dyn' if r['trail_adapt'] else 'fix'}"
            f"  │  n={int(r['n']):>4}  wr={r['win_rate']:>4.1f}%  pf={pf_s:>5}"
            f"  pnl=${r['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%"
        )
    print(sep)

    print(f"\n{sep}")
    print(f"  PER-YEAR — TOP 3 CONFIGS")
    print(sep)
    for _, r in top.head(3).iterrows():
        m = run_backtest(df, daily, vol_avg,
                         int(r["daily_ema"]), int(r["daily_adx"]),
                         r["vol_confirm"], r["trail_adapt"])
        label = (
            f"dEMA={int(r['daily_ema'])}  dADX≥{int(r['daily_adx'])}  "
            f"vol≥{r['vol_confirm']:.1f}×  trail={'dyn' if r['trail_adapt'] else 'fix'}"
            f"  │  n={int(r['n'])}  sharpe={r['sharpe']:+.2f}  "
            f"pnl=${r['net_pnl']:+,.0f}  dd={r['max_dd_pct']:+.1f}%"
        )
        print_yearly(m["trades"], label)

    print(f"\n{sep}")
    print(f"  FACTOR ANALYSIS — avg Sharpe by setting  (n ≥ 30)")
    print(sep)
    sub = results[results["n"] >= 30]
    for col, vals in [("daily_ema", S_DAILY_EMA), ("daily_adx", S_DAILY_ADX),
                      ("vol_confirm", S_VOL_CONF), ("trail_adapt", S_TRAIL_ADPT)]:
        line = f"  {col:<18}"
        for v in vals:
            avg = sub[sub[col] == v]["sharpe"].mean()
            line += f"  {v!s:>5}: {avg:+.3f}"
        print(line)
    print(sep + "\n")


if __name__ == "__main__":
    main()
