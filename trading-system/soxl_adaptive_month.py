#!/usr/bin/env python3
"""
SOXL Fully-Adaptive Strategy — Last Month Detail
=================================================

Six live market-condition dimensions drive every parameter every bar:

  1. Vol regime      rolling ATR percentile (63-day)  → low / mid / high
  2. Trend strength  ADX level                        → weak / moderate / strong
  3. Opening gap     |open - prev_close| / prev_close → small / medium / large
  4. Streak          consecutive wins / losses        → boost / normal / cut
  5. Volume pulse    open-bar vol vs time-slot avg    → light / normal / heavy
  6. EMA slope       5-bar rate of change             → flat / rising / ripping

Parameter grid by vol × trend (9 cells):
  ┌──────────┬──────────────────────────────────────────────────────────┐
  │          │  weak ADX (<25)  │ moderate (25-45) │   strong (>45)    │
  ├──────────┼──────────────────┼──────────────────┼───────────────────┤
  │ low vol  │  trail=2.5 ema=20│  trail=2.5 ema=20│  trail=2.0 ema=18 │
  │          │  dip=0.3 adds=3  │  dip=0.35 adds=3 │  dip=0.25 adds=5  │
  │          │  entry→11:30     │  entry→13:00     │  entry→15:00      │
  ├──────────┼──────────────────┼──────────────────┼───────────────────┤
  │ mid vol  │  trail=3.0 ema=25│  trail=3.0 ema=25│  trail=2.5 ema=22 │
  │          │  dip=0.5 adds=2  │  dip=0.5 adds=2  │  dip=0.4 adds=3   │
  │          │  entry→11:00     │  entry→12:00     │  entry→13:30      │
  ├──────────┼──────────────────┼──────────────────┼───────────────────┤
  │ high vol │  trail=4.5 ema=32│  trail=4.0 ema=30│  trail=3.5 ema=28 │
  │          │  dip=1.0 adds=1  │  dip=0.8 adds=1  │  dip=0.6 adds=2   │
  │          │  entry→10:30     │  entry→11:00     │  entry→12:30      │
  └──────────┴──────────────────┴──────────────────┴───────────────────┘

Multiplier modifiers on top of grid values:
  Gap large (>4%)   → size ×0.40, trail ×1.3
  Gap medium (2-4%) → size ×0.70, trail ×1.1
  Gap small (<2%)   → size ×1.00
  Streak +3 wins    → size ×1.30
  Streak +1-2 wins  → size ×1.10
  Streak -2 losses  → size ×0.75
  Streak -3+ losses → size ×0.50
  Volume heavy      → dip ×0.8  (easier to pyramid on conviction bars)
  Volume light      → dip ×1.2, min_profit ×1.2  (harder to add on thin bars)
  EMA ripping (>0.3% slope per bar) → trail ×0.85 (tight trail, ride it)
  EMA flat (<0.05% slope) → entry skip if not strong trend
"""

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

CACHE     = Path("soxl_5min_bars.csv")
LEVERAGE  = 1.0
COMM      = 0.005

ENTRY_START     = 9 * 60 + 30
EOD_CLOSE_SHORT = 15 * 60 + 50
EOD_CLOSE_ALL   = 15 * 60 + 55

# Vol-regime rolling window: 63 trading days × ~78 bars/day
VOL_RANK_WIN = 63 * 78
VOL_RANK_MIN = 300

# Volume time-slot rolling window: 63 days
VOL_SLOT_WIN = 63

# ── Adaptive parameter grid ───────────────────────────────────────────────────
# Keys: (vol_regime, trend_regime)  →  dict of base params
GRID = {
    ("low",  "weak"):     dict(trail=2.5, ema=20, dip=0.30, min_profit=0.35, max_adds=3, entry_end=11*60+30),
    ("low",  "moderate"): dict(trail=2.5, ema=20, dip=0.35, min_profit=0.40, max_adds=3, entry_end=13*60),
    ("low",  "strong"):   dict(trail=2.0, ema=18, dip=0.25, min_profit=0.30, max_adds=5, entry_end=15*60),
    ("mid",  "weak"):     dict(trail=3.0, ema=25, dip=0.50, min_profit=0.50, max_adds=2, entry_end=11*60),
    ("mid",  "moderate"): dict(trail=3.0, ema=25, dip=0.50, min_profit=0.50, max_adds=2, entry_end=12*60),
    ("mid",  "strong"):   dict(trail=2.5, ema=22, dip=0.40, min_profit=0.40, max_adds=3, entry_end=13*60+30),
    ("high", "weak"):     dict(trail=4.5, ema=32, dip=1.00, min_profit=0.80, max_adds=1, entry_end=10*60+30),
    ("high", "moderate"): dict(trail=4.0, ema=30, dip=0.80, min_profit=0.70, max_adds=1, entry_end=11*60),
    ("high", "strong"):   dict(trail=3.5, ema=28, dip=0.60, min_profit=0.55, max_adds=2, entry_end=12*60+30),
}

GAP_LARGE  = 0.04   # 4%
GAP_MEDIUM = 0.02   # 2%


# ── Indicators ────────────────────────────────────────────────────────────────
def build_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # Multiple EMAs (for the adaptive grid)
    for p in [18, 20, 22, 25, 28, 30, 32]:
        df[f"ema_{p}"] = df["close"].ewm(span=p, adjust=False).mean()

    # ATR (Wilder)
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()

    # ADX / PDI / NDI (period 20)
    up   = df["high"] - df["high"].shift(1)
    down = df["low"].shift(1) - df["low"]
    pdm  = np.where((up > down) & (up > 0), up, 0.0)
    ndm  = np.where((down > up) & (down > 0), down, 0.0)
    atr_w = tr.ewm(span=20, adjust=False).mean()
    pdi   = 100 * pd.Series(pdm, index=df.index).ewm(span=20, adjust=False).mean() / atr_w
    ndi   = 100 * pd.Series(ndm, index=df.index).ewm(span=20, adjust=False).mean() / atr_w
    dx    = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    df["adx"] = dx.ewm(span=20, adjust=False).mean()
    df["pdi"] = pdi
    df["ndi"] = ndi

    # Rolling ATR percentile rank → vol regime
    df["vol_rank"] = df["atr"].rolling(VOL_RANK_WIN, min_periods=VOL_RANK_MIN).rank(pct=True)

    # EMA slope: 5-bar rate of change of EMA-25 (normalised by price)
    ema25 = df["ema_25"]
    df["ema_slope"] = (ema25 - ema25.shift(5)) / (ema25.shift(5) * 5)  # % per bar

    # Volume time-slot: rolling 63-day average volume per 5-min slot
    df["time_slot"] = df.index.hour * 60 + df.index.minute
    df["vol_avg_slot"] = (
        df.groupby("time_slot")["volume"]
          .transform(lambda s: s.rolling(VOL_SLOT_WIN, min_periods=5).mean())
    )
    df["vol_ratio"] = df["volume"] / df["vol_avg_slot"].replace(0, np.nan)

    return df


# ── Regime classifiers ────────────────────────────────────────────────────────
def vol_regime(vol_rank: float) -> str:
    if np.isnan(vol_rank):
        return "mid"
    if vol_rank < 0.33:
        return "low"
    if vol_rank < 0.67:
        return "mid"
    return "high"


def trend_regime(adx: float) -> str:
    if adx < 25:
        return "weak"
    if adx < 45:
        return "moderate"
    return "strong"


def direction(pdi: float, ndi: float) -> str:
    return "bull" if pdi >= ndi else "bear"


# ── Adaptive param resolver ───────────────────────────────────────────────────
def resolve_params(row, streak: int, gap_pct: float) -> dict:
    vr  = vol_regime(row["vol_rank"])
    tr  = trend_regime(row["adx"])
    p   = dict(GRID[(vr, tr)])   # copy base

    # Gap modifier → trail and size
    gap_abs = abs(gap_pct)
    if gap_abs > GAP_LARGE:
        p["gap_label"]  = "LARGE"
        p["size_mult"]  = 0.40
        p["trail"]      = round(p["trail"] * 1.30, 2)
    elif gap_abs > GAP_MEDIUM:
        p["gap_label"]  = "medium"
        p["size_mult"]  = 0.70
        p["trail"]      = round(p["trail"] * 1.10, 2)
    else:
        p["gap_label"]  = "small"
        p["size_mult"]  = 1.00

    # Streak modifier → size
    if streak >= 3:
        p["size_mult"] = round(p["size_mult"] * 1.30, 3)
        p["streak_label"] = f"+{streak}W"
    elif streak >= 1:
        p["size_mult"] = round(p["size_mult"] * 1.10, 3)
        p["streak_label"] = f"+{streak}W"
    elif streak <= -3:
        p["size_mult"] = round(p["size_mult"] * 0.50, 3)
        p["streak_label"] = f"{streak}L"
    elif streak <= -2:
        p["size_mult"] = round(p["size_mult"] * 0.75, 3)
        p["streak_label"] = f"{streak}L"
    else:
        p["streak_label"] = "0"

    # Volume modifier → dip threshold
    vol_r = row.get("vol_ratio", 1.0)
    if not np.isnan(vol_r) and vol_r > 1.5:
        p["vol_label"] = "heavy"
        p["dip"]       = round(p["dip"] * 0.80, 3)
    elif not np.isnan(vol_r) and vol_r < 0.5:
        p["vol_label"] = "light"
        p["dip"]       = round(p["dip"] * 1.20, 3)
        p["min_profit"] = round(p["min_profit"] * 1.20, 3)
    else:
        p["vol_label"] = "normal"

    # EMA-slope modifier → trail tightening on ripping trend
    slope = row.get("ema_slope", 0.0)
    if not np.isnan(slope):
        if slope > 0.003:
            p["slope_label"] = "RIPPING"
            p["trail"]       = round(p["trail"] * 0.85, 2)
        elif slope > 0.001:
            p["slope_label"] = "rising"
        elif slope < -0.001:
            p["slope_label"] = "falling"
        else:
            p["slope_label"] = "flat"
    else:
        p["slope_label"] = "—"

    # Annotate regimes
    p["vol_regime"]   = vr
    p["trend_regime"] = tr

    return p


# ── Streak tracker ────────────────────────────────────────────────────────────
def current_streak(trades: list) -> int:
    """Positive = consecutive wins, negative = consecutive losses."""
    if not trades:
        return 0
    s = 0
    for t in reversed(trades):
        won = t["pnl"] > 0
        if s == 0:
            s = 1 if won else -1
        elif won and s > 0:
            s += 1
        elif not won and s < 0:
            s -= 1
        else:
            break
    return s


# ── Opening gap for a trading day ────────────────────────────────────────────
def daily_gaps(df: pd.DataFrame) -> dict:
    """Returns dict of date_str → gap_pct."""
    first  = df.groupby(df.index.date)["open"].first()
    last   = df.groupby(df.index.date)["close"].last()
    gaps   = {}
    dates  = sorted(first.index)
    for i in range(1, len(dates)):
        d    = dates[i]
        prev = dates[i - 1]
        gaps[str(d)] = (first[d] - last[prev]) / last[prev]
    gaps[str(dates[0])] = 0.0
    return gaps


# ── Backtest ──────────────────────────────────────────────────────────────────
def run_adaptive(df: pd.DataFrame,
                 init_eq: float = 10.0,
                 log_dates: set = None,
                 verbose: bool = False) -> dict:
    warmup  = VOL_RANK_WIN + 200
    trades  = []
    position = None
    equity   = init_eq
    gaps     = daily_gaps(df)

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        close = row["close"]
        atr   = row["atr"]
        adx   = row["adx"]
        pdi   = row["pdi"]
        ndi   = row["ndi"]
        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute
        date  = str(ts.date())
        loud  = log_dates and date in log_dates

        streak = current_streak(trades)
        gap_pct = gaps.get(date, 0.0)
        p = resolve_params(row, streak, gap_pct)

        ema = row[f"ema_{p['ema']}"]
        dir_ = direction(pdi, ndi)

        # ── Manage open position ─────────────────────────────────────────────
        if position is not None:
            side = position["side"]

            # Use the params that were active at entry (don't shift mid-trade)
            ep   = position["params"]
            e_ema = row[f"ema_{ep['ema']}"]   # EMA period frozen to entry params

            if side == "long" and close > position["pos_high"]:
                position["pos_high"] = close

            # Pyramid add (uses entry params, not current params)
            if (side == "long"
                    and position["n_adds"] < ep["max_adds"]
                    and ENTRY_START <= mins < ep["entry_end"] + 30):
                dip  = position["pos_high"] - close
                prof = close - position["avg_entry"]
                if (dip >= ep["dip"] * atr
                        and prof >= ep["min_profit"] * atr
                        and close > e_ema):
                    add_sh  = position["orig_shares"] * 1.0
                    old_c   = position["avg_entry"] * position["total_shares"]
                    position["total_shares"] += add_sh
                    position["avg_entry"]     = (old_c + close * add_sh) / position["total_shares"]
                    position["n_adds"]        += 1
                    if loud:
                        print(f"      ADD #{position['n_adds']}: +{add_sh:.5f} sh @ ${close:.2f}"
                              f"  avg=${position['avg_entry']:.2f}"
                              f"  dip={dip/atr:.2f}×ATR")

            # Update trail (use current trail width — can adapt mid-position)
            cur_trail = p["trail"]
            if side == "long":
                ns = close - cur_trail * atr
                if ns > position["trail_stop"]:
                    position["trail_stop"] = ns
                stop_hit = close <= position["trail_stop"]
            else:
                ns = close + cur_trail * atr
                if ns < position["trail_stop"]:
                    position["trail_stop"] = ns
                stop_hit = close >= position["trail_stop"]

            eod = (mins >= EOD_CLOSE_SHORT and side == "short") or mins >= EOD_CLOSE_ALL

            if stop_hit or eod:
                sh    = position["total_shares"]
                gross = (close - position["avg_entry"]) * sh if side == "long" \
                    else (position["avg_entry"] - close) * sh
                pnl   = gross - sh * COMM * 2
                equity += pnl
                reason = "EOD" if eod else "STOP"
                if loud:
                    ep_ = position["params"]
                    print(f"    EXIT {reason} @ {ts.strftime('%H:%M')}  ${close:.2f}"
                          f"  sh={sh:.5f}  stop=${position['trail_stop']:.2f}"
                          f"  pnl=${pnl:+.4f}  equity=${equity:.4f}"
                          f"  [trail={ep_['trail']}× ema={ep_['ema']}]")
                trades.append({
                    "date": date, "side": side, "pnl": pnl,
                    "entry_time": position["entry_time"],
                    "exit_time":  ts.strftime("%H:%M"),
                    "entry":      position["entry_price"],
                    "exit":       close,
                    "n_adds":     position["n_adds"],
                    "exit_reason":reason,
                    "year":       ts.year,
                    "month":      ts.to_period("M"),
                    "vol_regime": position["params"]["vol_regime"],
                    "trend_regime": position["params"]["trend_regime"],
                    "gap_label":  position["params"]["gap_label"],
                    "trail":      position["params"]["trail"],
                    "ema":        position["params"]["ema"],
                    "max_adds":   position["params"]["max_adds"],
                    "size_mult":  position["params"]["size_mult"],
                    "streak_at_entry": position["streak"],
                })
                position = None

        # ── Entry ────────────────────────────────────────────────────────────
        if position is None and ENTRY_START <= mins < p["entry_end"]:
            base_shares = (equity * LEVERAGE) / close
            shares = base_shares * p["size_mult"]
            if shares * close < 0.10:
                continue

            go_long  = dir_ in ("bull",) and close > ema
            go_short = dir_ == "bear" and close < ema

            # Skip if EMA is flat AND trend is weak (no edge)
            if p["trend_regime"] == "weak" and p.get("slope_label") == "flat":
                continue

            if go_long or go_short:
                side = "long" if go_long else "short"
                trail_0 = p["trail"] * atr
                position = {
                    "side":         side,
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close - trail_0 if side == "long" else close + trail_0,
                    "pos_high":     close,
                    "n_adds":       0,
                    "params":       dict(p),
                    "streak":       streak,
                    "entry_time":   ts.strftime("%H:%M"),
                }
                if loud:
                    print(f"\n  {date}  ENTER {side}"
                          f"  @ {ts.strftime('%H:%M')}  ${close:.2f}"
                          f"  {shares:.5f} sh (${shares*close:.2f})"
                          f"  stop=${position['trail_stop']:.2f}"
                          f"\n          params: trail={p['trail']}× ema={p['ema']}"
                          f" dip={p['dip']}× adds={p['max_adds']}"
                          f" entry_end={p['entry_end']//60}:{p['entry_end']%60:02d}"
                          f"\n          regime: vol={p['vol_regime']} trend={p['trend_regime']}"
                          f" dir={dir_} ADX={adx:.0f}"
                          f"\n          mods:   gap={p['gap_label']}({gap_pct*100:+.1f}%)"
                          f" streak={p['streak_label']}"
                          f" vol={p['vol_label']} slope={p['slope_label']}"
                          f" size_mult={p['size_mult']:.2f}×")

    # Close open position at end of data
    if position is not None:
        sh    = position["total_shares"]
        close = df.iloc[-1]["close"]
        ts    = df.index[-1]
        gross = (close - position["avg_entry"]) * sh if position["side"] == "long" \
            else (position["avg_entry"] - close) * sh
        pnl   = gross - sh * COMM * 2
        equity += pnl
        trades.append({
            "date": str(ts.date()), "side": position["side"], "pnl": pnl,
            "entry_time": position["entry_time"], "exit_time": ts.strftime("%H:%M"),
            "entry": position["entry_price"], "exit": close,
            "n_adds": position["n_adds"], "exit_reason": "open",
            "year": ts.year, "month": ts.to_period("M"),
            "vol_regime": position["params"]["vol_regime"],
            "trend_regime": position["params"]["trend_regime"],
            "gap_label": position["params"]["gap_label"],
            "trail": position["params"]["trail"],
            "ema": position["params"]["ema"],
            "max_adds": position["params"]["max_adds"],
            "size_mult": position["params"]["size_mult"],
            "streak_at_entry": position["streak"],
        })

    if not trades:
        return dict(n=0, sharpe=np.nan, net_pnl=0, max_dd_pct=0,
                    win_rate=0, pf=np.nan, final_eq=init_eq, trades=[])

    pnl_s  = pd.Series([t["pnl"] for t in trades])
    wins   = (pnl_s > 0).sum()
    eq_s   = init_eq + pnl_s.cumsum()
    hwm    = eq_s.cummax()
    dd     = eq_s - hwm
    max_dd = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
    n      = len(pnl_s)
    std    = pnl_s.std(ddof=1) if n > 1 else np.nan
    n_days = max((df.index[-1] - df.index[0]).days, 1)
    tpy    = n / (n_days / 252)
    sharpe = (pnl_s.mean() / std) * np.sqrt(tpy) if std and std > 0 else np.nan
    pf     = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
             if (n - wins) and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    return dict(n=n, win_rate=round(wins/n*100, 1),
                pf=round(float(pf), 3)       if not np.isnan(pf)     else np.nan,
                net_pnl=round(float(pnl_s.sum()), 4),
                final_eq=round(float(eq_s.iloc[-1]), 4),
                sharpe=round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
                max_dd_pct=round(float(max_dd), 2),
                trades=trades)


# ── Fixed baseline (same as soxl_live_bot.py) ────────────────────────────────
def run_fixed(df: pd.DataFrame, init_eq: float = 10.0) -> dict:
    warmup   = VOL_RANK_WIN + 200
    trades   = []
    position = None
    equity   = init_eq

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        close = row["close"]
        atr   = row["atr"]
        adx   = row["adx"]
        pdi   = row["pdi"]
        ndi   = row["ndi"]
        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute
        date  = str(ts.date())

        ema = row["ema_25"]
        reg_dir = "bull" if pdi >= ndi else "bear"

        if position is not None:
            side = position["side"]
            if side == "long" and close > position["pos_high"]:
                position["pos_high"] = close

            if (side == "long" and position["n_adds"] < 2
                    and ENTRY_START <= mins < 11*60 + 120):
                dip  = position["pos_high"] - close
                prof = close - position["avg_entry"]
                if dip >= 0.5*atr and prof >= 0.5*atr and close > ema:
                    add  = position["orig_shares"]
                    old  = position["avg_entry"] * position["total_shares"]
                    position["total_shares"] += add
                    position["avg_entry"]     = (old + close * add) / position["total_shares"]
                    position["n_adds"]        += 1

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

            eod = (mins >= EOD_CLOSE_SHORT and side == "short") or mins >= EOD_CLOSE_ALL

            if stop_hit or eod:
                sh    = position["total_shares"]
                gross = (close - position["avg_entry"]) * sh if side == "long" \
                    else (position["avg_entry"] - close) * sh
                pnl   = gross - sh * COMM * 2
                equity += pnl
                trades.append({"date": date, "pnl": pnl, "n_adds": position["n_adds"],
                                "year": ts.year, "month": ts.to_period("M")})
                position = None

        if position is None and ENTRY_START <= mins < 11*60:
            shares = (equity * LEVERAGE) / close
            if shares * close < 0.10:
                continue
            if adx > 35 and reg_dir == "bear" and close > ema:
                pass  # skip bear-trend longs
            elif reg_dir in ("bull",) and close > ema:
                position = {"side": "long", "entry_price": close,
                            "avg_entry": close, "total_shares": shares,
                            "orig_shares": shares, "trail_stop": close - 3.0*atr,
                            "pos_high": close, "n_adds": 0}
            elif adx > 35 and reg_dir == "bear" and close < ema:
                position = {"side": "short", "entry_price": close,
                            "avg_entry": close, "total_shares": shares,
                            "orig_shares": shares, "trail_stop": close + 3.0*atr,
                            "pos_high": close, "n_adds": 0}

    if not trades:
        return dict(n=0, net_pnl=0, final_eq=init_eq, win_rate=0, sharpe=np.nan,
                    max_dd_pct=0, trades=[])
    pnl_s  = pd.Series([t["pnl"] for t in trades])
    wins   = (pnl_s > 0).sum()
    eq_s   = init_eq + pnl_s.cumsum()
    hwm    = eq_s.cummax()
    dd     = eq_s - hwm
    max_dd = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
    n      = len(pnl_s)
    std    = pnl_s.std(ddof=1) if n > 1 else np.nan
    n_days = max((df.index[-1] - df.index[0]).days, 1)
    tpy    = n / (n_days / 252)
    sharpe = (pnl_s.mean() / std) * np.sqrt(tpy) if std and std > 0 else np.nan
    pf     = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
             if (n - wins) and pnl_s[pnl_s < 0].sum() != 0 else np.nan
    return dict(n=n, win_rate=round(wins/n*100, 1),
                pf=round(float(pf), 3) if not np.isnan(pf) else np.nan,
                net_pnl=round(float(pnl_s.sum()), 4),
                final_eq=round(float(eq_s.iloc[-1]), 4),
                sharpe=round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
                max_dd_pct=round(float(max_dd), 2), trades=trades)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    init_eq = 10.0

    print(f"\nSOXL Fully-Adaptive Strategy — Last Month Detail")
    print("=" * 90)

    df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    df = df.between_time("09:30", "16:00")
    print(f"  Loaded {len(df):,} bars  {df.index[0].date()} → {df.index[-1].date()}")
    print("  Building indicators (rolling vol rank takes a moment) ...")
    df = build_indicators(df)

    all_dates = sorted(df.index.normalize().unique())
    last22    = [str(d.date()) for d in all_dates[-22:]]
    log_set   = set(last22)

    print(f"  Last-month window: {last22[0]} → {last22[-1]}"
          f"  ({len(last22)} trading days)")
    print()

    # ── Adaptive run ─────────────────────────────────────────────────────────
    print("─" * 90)
    print("  ADAPTIVE TRADE LOG — last month")
    print("─" * 90)
    adp = run_adaptive(df, init_eq=init_eq, log_dates=log_set)
    print()

    t_adp = pd.DataFrame(adp["trades"]) if adp["trades"] else pd.DataFrame()
    last22_trades = t_adp[t_adp["date"].isin(log_set)] if len(t_adp) else pd.DataFrame()

    # Day-by-day summary
    print(f"  {'Date':<12} {'n':>2} {'W':>2} {'PnL':>9} "
          f"{'Vol':>5} {'Trnd':>5} {'Trail':>6} {'EMA':>4} "
          f"{'Adds':>4} {'Gap':>6} {'SizeMlt':>7}")
    print("  " + "─" * 80)

    gaps = daily_gaps(df)
    for d in last22:
        grp = last22_trades[last22_trades["date"] == d] if len(last22_trades) else pd.DataFrame()
        gap_pct = gaps.get(d, 0.0) * 100

        # Regime info: use the regime from the first trade of that day
        if len(grp):
            vr  = grp.iloc[0].get("vol_regime", "?")
            trd = grp.iloc[0].get("trend_regime", "?")
            glab = grp.iloc[0].get("gap_label", "?")
            tr  = grp.iloc[0].get("trail", 0)
            ema = int(grp.iloc[0].get("ema", 0))
            adds = grp["n_adds"].mean()
            sm  = grp.iloc[0].get("size_mult", 1.0)
            pnl = grp["pnl"].sum()
            n   = len(grp)
            w   = (grp["pnl"] > 0).sum()
        else:
            # Get regime from bar data for this day (informational)
            day_bars = df[df.index.normalize() == pd.Timestamp(d)]
            if len(day_bars):
                first = day_bars.iloc[min(VOL_RANK_WIN + 200, len(day_bars)-1)]
                vr  = vol_regime(first["vol_rank"])
                trd = trend_regime(first["adx"])
                tr  = GRID[(vr, trd)]["trail"]
                ema = GRID[(vr, trd)]["ema"]
                # gap modifier
                ga  = abs(gap_pct / 100)
                if ga > GAP_LARGE:   glab = "LARGE"; tr = round(tr * 1.3, 1)
                elif ga > GAP_MEDIUM: glab = "med";  tr = round(tr * 1.1, 1)
                else: glab = "small"
            else:
                vr = trd = glab = "?"; tr = 0; ema = 0
            pnl = 0; n = 0; w = 0; adds = 0; sm = 1.0

        flag = "◄ WIN" if n and w == n else ("✗" if n and w == 0 else "")
        print(f"  {d:<12} {n:>2} {w:>2} ${pnl:>7.2f} "
              f"{vr:>5} {trd:>8} {tr:>6.1f}× {ema:>4} "
              f"{adds:>4.1f}×  {gap_pct:>+5.1f}%  {sm:>6.2f}×  {flag}")

    # Totals
    print()
    last22_pnl  = last22_trades["pnl"].sum() if len(last22_trades) else 0
    last22_wins = (last22_trades["pnl"] > 0).sum() if len(last22_trades) else 0
    print(f"  Last-month total: ${last22_pnl:+.4f}"
          f"  ({last22_wins}/{len(last22_trades)} wins)"
          f"  on ${t_adp[~t_adp['date'].isin(log_set)]['pnl'].sum() + init_eq:.4f} running equity")

    # ── Full period comparison ────────────────────────────────────────────────
    print()
    print("─" * 90)
    print("  FULL 5-YEAR: ADAPTIVE vs FIXED  (starting $10.00)")
    print("─" * 90)
    fix = run_fixed(df, init_eq=init_eq)

    def fmt(r):
        sh = f"{r['sharpe']:+.2f}" if not np.isnan(r['sharpe']) else " n/a"
        pf = f"{r['pf']:.3f}" if not np.isnan(r['pf']) else " n/a"
        return (f"  ${init_eq:.2f} → ${r['final_eq']:>8.2f}"
                f"  ({(r['final_eq']/init_eq-1)*100:>+6.0f}%)"
                f"  Sharpe {sh}  DD {r['max_dd_pct']:+.1f}%"
                f"  WR {r['win_rate']}%  PF {pf}"
                f"  n={r['n']}")

    print(f"  ADAPTIVE:  {fmt(adp)}")
    print(f"  FIXED:     {fmt(fix)}")
    delta = adp["final_eq"] - fix["final_eq"]
    print(f"  Delta:     ${delta:>+.2f}  adaptive {'beats' if delta > 0 else 'trails'} fixed")

    # ── Year-by-year ─────────────────────────────────────────────────────────
    print()
    print(f"  {'Year':<6} {'n_adp':>6} {'WR':>5} {'PnL_adp':>10}  │  {'n_fix':>6} {'WR':>5} {'PnL_fix':>10}  │ Delta")
    print("  " + "─" * 75)
    t_fix = pd.DataFrame(fix["trades"]) if fix["trades"] else pd.DataFrame()
    for yr in sorted(set(t_adp["year"].unique()) | (set(t_fix["year"].unique()) if len(t_fix) else set())):
        ga = t_adp[t_adp["year"] == yr] if len(t_adp) else pd.DataFrame()
        gf = t_fix[t_fix["year"] == yr] if len(t_fix) else pd.DataFrame()
        pa = ga["pnl"].sum() if len(ga) else 0
        pf_ = gf["pnl"].sum() if len(gf) else 0
        na = len(ga); nf = len(gf)
        wa = (ga["pnl"] > 0).mean()*100 if na else 0
        wf = (gf["pnl"] > 0).mean()*100 if nf else 0
        print(f"  {yr:<6} {na:>6} {wa:>4.0f}%  ${pa:>8.2f}  │  {nf:>6} {wf:>4.0f}%  ${pf_:>8.2f}  │  ${pa-pf_:>+8.2f}")

    # ── Regime performance breakdown ─────────────────────────────────────────
    if len(t_adp):
        print()
        print("─" * 90)
        print("  ADAPTIVE — P&L by (vol_regime × trend_regime)")
        print("─" * 90)
        print(f"  {'Regime':<22} {'n':>4} {'WR%':>5} {'Avg PnL':>10} {'Total PnL':>12} {'Avg Adds':>9}")
        print("  " + "─" * 70)
        for (vr, tr_), grp in t_adp.groupby(["vol_regime", "trend_regime"]):
            g  = grp["pnl"]
            na = len(g); wa = (g > 0).sum()
            adds_avg = grp["n_adds"].mean()
            print(f"  {f'{vr} × {tr_}':<22} {na:>4} {wa/na*100:>4.0f}%  "
                  f"${g.mean():>8.4f}  ${g.sum():>10.4f}  {adds_avg:>8.2f}x")

        print()
        print("  ADAPTIVE — P&L by gap size")
        print(f"  {'Gap':<12} {'n':>4} {'WR%':>5} {'Avg PnL':>10} {'Avg trail':>10} {'Avg size':>10}")
        print("  " + "─" * 60)
        for gl, grp in t_adp.groupby("gap_label"):
            g  = grp["pnl"]
            na = len(g); wa = (g > 0).sum()
            tr_avg = grp["trail"].mean()
            sm_avg = grp["size_mult"].mean()
            print(f"  {gl:<12} {na:>4} {wa/na*100:>4.0f}%  ${g.mean():>8.4f}"
                  f"  {tr_avg:>9.2f}×  {sm_avg:>9.2f}×")

        print()
        print("  ADAPTIVE — P&L by entry streak")
        print(f"  {'Streak':<12} {'n':>4} {'WR%':>5} {'Avg PnL':>10} {'Avg size':>10}")
        print("  " + "─" * 50)
        for sk, grp in sorted(t_adp.groupby("streak_at_entry")):
            g  = grp["pnl"]
            na = len(g); wa = (g > 0).sum()
            sm_avg = grp["size_mult"].mean()
            print(f"  {sk:>+3} streak   {na:>4} {wa/na*100:>4.0f}%  ${g.mean():>8.4f}  {sm_avg:>9.2f}×")

    # ── Scale table ───────────────────────────────────────────────────────────
    print()
    print("─" * 90)
    print("  SCALE COMPARISON — adaptive strategy")
    print("─" * 90)
    for eq in [10, 100, 500, 1000]:
        ra = run_adaptive(df, init_eq=float(eq))
        rf = run_fixed(df, init_eq=float(eq))
        sh_a = f"{ra['sharpe']:+.2f}" if not np.isnan(ra['sharpe']) else " n/a"
        sh_f = f"{rf['sharpe']:+.2f}" if not np.isnan(rf['sharpe']) else " n/a"
        print(f"  ${eq:>5}  adaptive: ${ra['final_eq']:>9.2f} ({(ra['final_eq']/eq-1)*100:>+6.0f}%)"
              f"  Sh={sh_a}  DD={ra['max_dd_pct']:+.1f}%"
              f"  │  fixed: ${rf['final_eq']:>9.2f} ({(rf['final_eq']/eq-1)*100:>+6.0f}%)"
              f"  Sh={sh_f}  DD={rf['max_dd_pct']:+.1f}%")

    print("=" * 90)


if __name__ == "__main__":
    main()
