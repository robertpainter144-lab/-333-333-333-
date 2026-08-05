#!/usr/bin/env python3
"""
SOXL Regime-Gated Short-Hold Strategy
======================================

REGIME GATE (daily, evaluated on PREVIOUS day's closed bar — no look-ahead):
  1. Semi trend up  : SMH close > 50-EMA  AND  20-EMA > 50-EMA (daily)
  2. Not chopping   : SMH ADX(14) > 20 (daily)
  3. Risk-on macro  : SPY close > 200-SMA (daily)
  4. Volatility cap : VIX < 25 (daily, if available)
  5. SOXL self-trend: SOXL 20-EMA > 50-EMA AND SOXL close > 50-EMA (daily)
     → extra check: filters out bear rallies where SMH may recover before SOXL
  When ALL true → GATE OPEN. Otherwise → hold cash.

ENTRY (5-min intraday, long-only):
  - Gate must be open for the current trading day
  - Window: 9:35–11:00 ET (first qualifying bar only — one entry per day)
  - Trigger: SOXL 5-min close > 25-EMA AND close > prior 5-min close
             AND close > prior TRADING DAY's SOXL close (day-over-day momentum)

POSITION SIZING (risk-first):
  - Risk 2% of equity per trade
  - Stop distance = 1.25 × prev-day's SOXL daily ATR(14)
  - Shares = (equity × 0.02) / stop_distance
  - Max position notional = 30% of equity (leverage cap)

EXITS (short-hold, prefer same-day closure):
  - ATR(14) trailing stop at 1.25× prev-day SOXL daily ATR (swarm-optimised mult)
  - Tiered: sell 50% at +2×daily_ATR profit, trail remainder at 1.25×
  - Hard stop: entry − min(8%·entry, 2×daily_ATR) — set once, never moves
  - Let trail work overnight (no forced EOD close on day 1 or 2)
  - Force EOD close at 15:55 on day 3 (MAX HOLD = 3 trading days)

DESIGN NOTES:
  - soxl_pyramid_backtest.py is flagged as buggy (all-negative Sharpe; SHORT
    entry condition is inverted: uses close>=ema in bear_trend instead of <ema).
    This is a clean rewrite.
  - 1.25× applied to DAILY ATR (not 5-min). On 5-min bars, 1.25× ATR gives
    ~0.5-1% trail which fires constantly on SOXL's noise. Daily ATR×1.25 is
    the natural width for a 1-3 day short-hold on a 3× leveraged ETF.
  - SOXL self-trend gate (condition 5) adds the critical filter missing in
    soxl_pyramid_backtest: during 2022 bear-market rallies SMH may briefly
    look "trended" but SOXL's own EMA structure (leveraged + decay) remains
    bearish. Requiring SOXL 20>50 EMA eliminates those false entries.
  - No look-ahead contamination: all signals use PREVIOUS day's closed bars.
"""

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

# ── Paths ─────────────────────────────────────────────────────────────────────
SOXL_CACHE   = Path("soxl_5min_bars.csv")
DAILY_CACHE  = Path("daily_regime_cache.csv")
QC_STATE     = Path("qc_portfolio/portfolio_state.json")
RESULT_DIR   = Path("swarm_results/completed")
RESULT_FILE  = RESULT_DIR / "soxl_regime_strategy.json"

# ── Strategy parameters ───────────────────────────────────────────────────────
# Regime gate (daily)
GATE_EMA_FAST   = 20
GATE_EMA_SLOW   = 50
GATE_ADX_WIN    = 14
GATE_ADX_THR    = 20.0
GATE_SPY_SMA    = 200
GATE_VIX_MAX    = 25.0
GATE_SOXL_TREND = True   # require SOXL 20-EMA > 50-EMA AND close > 50-EMA

# Intraday signal (5-min EMA for entry trigger only)
ENTRY_EMA       = 25
ENTRY_ATR       = 14

# Stop widths — applied to DAILY SOXL ATR (previous day's close)
TRAIL_MULT      = 1.25   # swarm-optimised mult (on daily ATR)
TAKE1_MULT      = 2.0    # sell 50% at +2 × daily ATR profit
TAKE1_FRAC      = 0.50
HARD_STOP_PCT   = 0.08   # 8% hard floor
HARD_STOP_MULT  = 2.0    # 2 × daily ATR hard floor

# Position sizing
RISK_PER_TRADE  = 0.02   # 2% of equity per trade
MAX_NOTIONAL    = 0.30   # cap at 30% of equity

# Timing (minutes past midnight ET)
ENTRY_START     = 9 * 60 + 35
ENTRY_END       = 11 * 60
EOD_FORCE       = 15 * 60 + 55   # force close on day MAX_HOLD_DAYS only

MAX_HOLD_DAYS   = 3    # close by EOD of 3rd trading day (days_held≥2 + mins≥EOD)

COMM            = 0.005  # $/share per side
INIT_EQ         = 100_000.0


# ── External daily regime data: SMH / SPY / VIX ──────────────────────────────

def _flat_yf(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.droplevel(1, axis=1)
    df.index = pd.to_datetime(df.index).normalize()
    return df


def _daily_adx(df: pd.DataFrame, period: int) -> pd.Series:
    hi, lo, cl = df["High"], df["Low"], df["Close"]
    tr  = pd.concat([(hi - lo), (hi - cl.shift(1)).abs(),
                     (lo - cl.shift(1)).abs()], axis=1).max(axis=1)
    up  = hi - hi.shift(1)
    dn  = lo.shift(1) - lo
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    atr_e = tr.ewm(span=period, adjust=False).mean()
    pdi = 100 * pdm.ewm(span=period, adjust=False).mean() / atr_e
    ndi = 100 * ndm.ewm(span=period, adjust=False).mean() / atr_e
    dx  = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(span=period, adjust=False).mean()


def fetch_external_daily(start="2019-01-01", end="2026-06-16") -> pd.DataFrame:
    if DAILY_CACHE.exists():
        df = pd.read_csv(DAILY_CACHE, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index).normalize()
        print(f"  Loaded external daily cache ({df.index[0].date()} → {df.index[-1].date()})")
        return df

    print("  Fetching SMH / SPY / ^VIX daily via yfinance ...")
    smh = _flat_yf(yf.download("SMH", start=start, end=end, progress=False, auto_adjust=True))
    spy = _flat_yf(yf.download("SPY", start=start, end=end, progress=False, auto_adjust=True))
    try:
        vix = _flat_yf(yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=True))
        has_vix = len(vix) > 10
    except Exception:
        vix = None; has_vix = False

    smh_c    = smh["Close"]
    ema_fast = smh_c.ewm(span=GATE_EMA_FAST, adjust=False).mean()
    ema_slow = smh_c.ewm(span=GATE_EMA_SLOW, adjust=False).mean()
    adx      = _daily_adx(smh, GATE_ADX_WIN)
    spy_c    = spy["Close"]
    spy_s200 = spy_c.rolling(GATE_SPY_SMA, min_periods=GATE_SPY_SMA).mean()

    cond_trend = (smh_c > ema_slow) & (ema_fast > ema_slow)
    cond_adx   = adx > GATE_ADX_THR
    cond_spy   = spy_c > spy_s200

    out = pd.DataFrame({
        "smh_close":  smh_c, "smh_ema20": ema_fast, "smh_ema50": ema_slow,
        "smh_adx":    adx,
        "spy_close":  spy_c, "spy_sma200": spy_s200,
        "cond_smh_trend": cond_trend, "cond_adx": cond_adx, "cond_spy": cond_spy,
    })
    if has_vix:
        vc = vix["Close"].reindex(out.index).ffill()
        out["vix_close"] = vc
        out["cond_vix"]  = vc < GATE_VIX_MAX

    out.to_csv(DAILY_CACHE)
    print(f"  Saved external daily cache ({len(out)} rows)")
    return out


# ── SOXL daily signals from resampled 5-min data ─────────────────────────────

def build_soxl_daily(df5: pd.DataFrame) -> pd.DataFrame:
    """
    Resample 5-min SOXL to daily OHLCV. Compute:
      - ATR(14) daily
      - EMA-20 and EMA-50 on daily closes
      - Prior-day close (for intraday entry momentum check)
    All shifted +1 so each date's value is the PREVIOUS day's (no look-ahead).
    """
    daily = (df5.resample("B")
               .agg({"open": "first", "high": "max",
                     "low": "min",    "close": "last"})
               .dropna())

    cl = daily["close"]
    hi = daily["high"]
    lo = daily["low"]
    tr = pd.concat([(hi - lo), (hi - cl.shift(1)).abs(),
                    (lo - cl.shift(1)).abs()], axis=1).max(axis=1)
    atr14  = tr.ewm(span=14, adjust=False).mean()
    ema20  = cl.ewm(span=20, adjust=False).mean()
    ema50  = cl.ewm(span=50, adjust=False).mean()

    # Shift +1: values for date D reflect close of day D-1
    out = pd.DataFrame({
        "soxl_daily_close": cl.shift(1),
        "soxl_daily_atr":   atr14.shift(1),
        "soxl_ema20":       ema20.shift(1),
        "soxl_ema50":       ema50.shift(1),
    })
    out.index = pd.to_datetime(out.index).normalize()
    return out


# ── Combine external + SOXL signals into regime gate ─────────────────────────

def build_regime(ext: pd.DataFrame, soxl_d: pd.DataFrame) -> pd.DataFrame:
    """
    Merge external and SOXL daily signals, compute combined gate_open.
    """
    reg = ext.join(soxl_d, how="outer")

    ext_gate  = reg["cond_smh_trend"] & reg["cond_adx"] & reg["cond_spy"]
    if "cond_vix" in reg.columns:
        ext_gate = ext_gate & reg["cond_vix"]

    # SOXL self-trend: 20-EMA > 50-EMA AND close > 50-EMA (already prev-day shifted)
    if GATE_SOXL_TREND:
        cond_soxl = ((reg["soxl_ema20"] > reg["soxl_ema50"])
                     & (reg["soxl_daily_close"] > reg["soxl_ema50"]))
        reg["cond_soxl_trend"] = cond_soxl
        gate = ext_gate & cond_soxl
    else:
        gate = ext_gate

    # Already prev-day shifted (both ext and soxl_d were shifted in their builders)
    reg["gate_open"] = gate
    return reg


# ── Intraday indicators (5-min) ───────────────────────────────────────────────

def add_intraday_ind(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema"]        = df["close"].ewm(span=ENTRY_EMA, adjust=False).mean()
    tr               = pd.concat(
        [df["high"] - df["low"],
         (df["high"] - df["close"].shift(1)).abs(),
         (df["low"]  - df["close"].shift(1)).abs()], axis=1
    ).max(axis=1)
    df["atr5"]       = tr.ewm(span=ENTRY_ATR, adjust=False).mean()
    df["prev_close"] = df["close"].shift(1)
    return df


# ── Backtest engine ───────────────────────────────────────────────────────────

def run_backtest(df5: pd.DataFrame,
                 regime: pd.DataFrame,
                 n_samples: int = 12) -> tuple:

    warmup    = 150
    trades    = []
    pos       = None
    equity    = INIT_EQ
    entered_today: set = set()
    sample_buf = []

    all_dates = sorted(df5.index.normalize().unique())
    date_ord  = {d: i for i, d in enumerate(all_dates)}

    print(f"\n{'─'*76}")
    print(f"  BACKTEST  {all_dates[0].date()} → {all_dates[-1].date()}")
    print(f"  Regime : SMH(20>50EMA + ADX>{GATE_ADX_THR}) + SPY>200SMA + "
          f"VIX<{GATE_VIX_MAX} + SOXL(20>50EMA)")
    print(f"  Entry  : close > {ENTRY_EMA}-EMA (5-min) + close > prev 5-min + "
          f"close > prior day's SOXL close")
    print(f"  Trail  : {TRAIL_MULT}× prev-day daily ATR  (daily basis, not 5-min)")
    print(f"  Sizing : risk {int(RISK_PER_TRADE*100)}%/trade  max {int(MAX_NOTIONAL*100)}% notional")
    print(f"  Exits  : trail + hard(min 8%,{HARD_STOP_MULT}×ATR) + 50%@+{TAKE1_MULT}×ATR + "
          f"EOD day {MAX_HOLD_DAYS}")
    print(f"{'─'*76}\n")

    for i in range(warmup, len(df5)):
        row        = df5.iloc[i]
        close      = float(row["close"])
        ema        = float(row["ema"])
        prev_close = float(row["prev_close"]) if not np.isnan(row["prev_close"]) else close
        ts         = df5.index[i]
        date       = ts.normalize()
        date_str   = str(date.date())
        mins       = ts.hour * 60 + ts.minute

        if np.isnan(ema) or close <= 0:
            continue

        date_key = pd.Timestamp(date.date())
        if date_key not in regime.index:
            continue

        # Daily SOXL signals (prev-day shifted already)
        d_atr        = regime.loc[date_key, "soxl_daily_atr"]
        d_prev_close = regime.loc[date_key, "soxl_daily_close"]
        gate_raw     = regime.loc[date_key, "gate_open"]

        if pd.isna(d_atr) or pd.isna(gate_raw) or d_atr <= 0:
            continue

        gate_open = bool(gate_raw)

        # ── Manage open position ──────────────────────────────────────────────
        if pos is not None:
            entry_ord   = date_ord.get(pos["entry_date"], 0)
            current_ord = date_ord.get(date, 0)
            days_held   = current_ord - entry_ord

            # Force EOD on 3rd day (days_held=2) or backstop on 4th day
            eod_hit      = (days_held >= MAX_HOLD_DAYS - 1 and mins >= EOD_FORCE)
            max_hold_hit = (days_held >= MAX_HOLD_DAYS)

            lock_atr = pos["entry_datr"]

            # Tiered partial: sell 50% at +2× daily ATR
            if (not pos["partial_taken"]
                    and close >= pos["avg_entry"] + TAKE1_MULT * lock_atr):
                sell_sh = pos["total_shares"] * TAKE1_FRAC
                part_pnl = (close - pos["avg_entry"]) * sell_sh - sell_sh * COMM * 2
                equity += part_pnl
                pos["total_shares"]  -= sell_sh
                pos["partial_taken"]  = True
                pos["locked_pnl"]    += part_pnl

            # Ratchet trailing stop using current day's daily ATR (already prev-shifted)
            current_datr = d_atr if not pd.isna(d_atr) and d_atr > 0 else lock_atr
            new_trail = close - TRAIL_MULT * current_datr
            if new_trail > pos["trail_stop"]:
                pos["trail_stop"] = new_trail

            trail_hit = close <= pos["trail_stop"]
            hard_hit  = close <= pos["hard_stop"]

            if trail_hit or hard_hit or eod_hit or max_hold_hit:
                shares     = pos["total_shares"]
                exit_price = close
                if hard_hit and not trail_hit and not eod_hit and not max_hold_hit:
                    exit_price = pos["hard_stop"]

                gross     = (exit_price - pos["avg_entry"]) * shares
                pnl       = gross - shares * COMM * 2
                equity   += pnl
                total_pnl = pnl + pos["locked_pnl"]

                reason = ("MAX_HOLD" if max_hold_hit else
                          "EOD"      if eod_hit      else
                          "HARD"     if hard_hit      else "TRAIL")

                rec = {
                    "entry_date":  pos["entry_date_str"],
                    "entry_time":  pos["entry_time"],
                    "exit_date":   date_str,
                    "exit_time":   ts.strftime("%H:%M"),
                    "entry_price": round(pos["avg_entry"],  4),
                    "exit_price":  round(exit_price, 4),
                    "pnl":         round(total_pnl,  4),
                    "pnl_pct":     round((exit_price / pos["avg_entry"] - 1) * 100, 3),
                    "exit_reason": reason,
                    "days_held":   days_held,
                    "partial":     pos["partial_taken"],
                    "entry_datr":  round(lock_atr, 4),
                    "trail_stop":  round(pos["trail_stop"], 4),
                    "hard_stop":   round(pos["hard_stop"],  4),
                    "entry_idx":   pos["entry_bar_idx"],
                    "exit_idx":    i,
                    "year":        ts.year,
                }
                trades.append(rec)
                if len(sample_buf) < n_samples:
                    sample_buf.append(rec)
                pos = None

        # ── Entry logic ───────────────────────────────────────────────────────
        can_enter = (pos is None
                     and gate_open
                     and date not in entered_today
                     and ENTRY_START <= mins < ENTRY_END)

        if can_enter:
            # Intraday momentum: close > 5-min EMA, > prior 5-min close,
            # AND > prior TRADING DAY's close (no gap-down entries)
            prior_day_close = float(d_prev_close) if not pd.isna(d_prev_close) else 0.0
            entry_signal = (close > ema
                            and close > prev_close
                            and close > prior_day_close)

            if entry_signal:
                trail_dist  = TRAIL_MULT * d_atr
                hard_dist   = min(close * HARD_STOP_PCT, HARD_STOP_MULT * d_atr)
                stop_dist   = min(trail_dist, hard_dist)

                risk_dollars  = equity * RISK_PER_TRADE
                shares_risk   = risk_dollars / max(stop_dist, 0.01)
                shares_capped = (equity * MAX_NOTIONAL) / close
                shares        = min(shares_risk, shares_capped)

                if shares * close < 1.0:
                    continue

                pos = {
                    "avg_entry":      close,
                    "total_shares":   shares,
                    "trail_stop":     close - trail_dist,
                    "hard_stop":      close - hard_dist,
                    "entry_datr":     d_atr,
                    "entry_date":     date,
                    "entry_date_str": date_str,
                    "entry_time":     ts.strftime("%H:%M"),
                    "partial_taken":  False,
                    "locked_pnl":     0.0,
                    "entry_bar_idx":  i,
                }
                entered_today.add(date)

    # Close residual position at end of data
    if pos is not None:
        ts        = df5.index[-1]
        close_f   = float(df5.iloc[-1]["close"])
        shares    = pos["total_shares"]
        pnl       = (close_f - pos["avg_entry"]) * shares - shares * COMM * 2
        equity   += pnl
        d_ord_l   = date_ord.get(ts.normalize(), 0)
        days_held = d_ord_l - date_ord.get(pos["entry_date"], 0)
        rec = {
            "entry_date":  pos["entry_date_str"],
            "entry_time":  pos["entry_time"],
            "exit_date":   str(ts.date()),
            "exit_time":   ts.strftime("%H:%M"),
            "entry_price": round(pos["avg_entry"], 4),
            "exit_price":  round(close_f, 4),
            "pnl":         round(pnl + pos["locked_pnl"], 4),
            "pnl_pct":     round((close_f / pos["avg_entry"] - 1) * 100, 3),
            "exit_reason": "END_DATA",
            "days_held":   days_held,
            "partial":     pos["partial_taken"],
            "entry_datr":  round(pos["entry_datr"], 4),
            "trail_stop":  round(pos["trail_stop"], 4),
            "hard_stop":   round(pos["hard_stop"], 4),
            "entry_idx":   pos["entry_bar_idx"],
            "exit_idx":    len(df5) - 1,
            "year":        ts.year,
        }
        trades.append(rec)
        if len(sample_buf) < n_samples:
            sample_buf.append(rec)

    return equity, trades, sample_buf


# ── Statistics ────────────────────────────────────────────────────────────────

def compute_stats(trades: list, init_eq: float, df5: pd.DataFrame) -> dict:
    if not trades:
        return {"error": "no_trades", "num_trades": 0}

    tdf  = pd.DataFrame(trades)
    pnls = tdf["pnl"]
    n    = len(pnls)
    wins = int((pnls > 0).sum())

    eq       = init_eq + pnls.cumsum()
    hwm      = eq.cummax()
    dd       = eq - hwm
    max_dd_p = float(abs(dd.min() / hwm.loc[dd.idxmin()]) * 100) if dd.min() < 0 else 0.0

    span_days = max((df5.index[-1] - df5.index[0]).days, 1)
    n_yrs     = span_days / 365.25
    final_eq  = float(init_eq + pnls.sum())
    cagr      = (final_eq / init_eq) ** (1.0 / n_yrs) - 1.0

    gw  = float(pnls[pnls > 0].sum()) if wins else 0.0
    gl  = float(pnls[pnls < 0].sum()) if (n - wins) else 0.0
    pf  = gw / abs(gl) if gl != 0 else np.nan

    std_t = float(pnls.std(ddof=1)) if n > 1 else np.nan
    tpy   = n / n_yrs
    sh_t  = float(pnls.mean() / std_t * np.sqrt(tpy)) if std_t and std_t > 0 else np.nan

    tdf["exit_dt"] = pd.to_datetime(tdf["exit_date"])
    dp   = tdf.groupby("exit_dt")["pnl"].sum()
    days = pd.DatetimeIndex(df5.index.normalize().unique())
    dr   = dp.reindex(days, fill_value=0.0) / init_eq
    sh_d = float(dr.mean() / dr.std(ddof=1) * np.sqrt(252)) if dr.std() > 0 else np.nan

    total_bars  = len(df5) - warmup_bars()
    bars_in_mkt = int((tdf["exit_idx"] - tdf["entry_idx"]).sum())
    pct_in_mkt  = min(bars_in_mkt / max(total_bars, 1) * 100, 100.0)

    return {
        "num_trades":         n,
        "win_rate":           round(wins / n * 100, 1),
        "profit_factor":      round(float(pf), 3) if not np.isnan(pf) else None,
        "sharpe_daily":       round(sh_d, 3)  if not np.isnan(sh_d) else None,
        "sharpe_per_trade":   round(sh_t, 3)  if not np.isnan(sh_t) else None,
        "cagr_pct":           round(cagr * 100, 2),
        "max_dd_pct":         round(max_dd_p, 2),
        "avg_hold_days":      round(float(tdf["days_held"].mean()), 2),
        "pct_time_in_market": round(pct_in_mkt, 1),
        "final_eq":           round(final_eq, 2),
        "net_pnl":            round(float(pnls.sum()), 2),
        "n_years":            round(n_yrs, 2),
        "exit_breakdown":     tdf["exit_reason"].value_counts().to_dict(),
    }


def warmup_bars() -> int:
    return 150


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(stats: dict, trades: list, sample_buf: list, regime: pd.DataFrame):
    if not trades:
        print("  NO TRADES generated.")
        return

    tdf = pd.DataFrame(trades)

    print(f"\n{'═'*76}")
    print(f"  RESULTS  {stats['num_trades']} trades / {stats['n_years']:.1f} yr")
    print(f"  ${stats['final_eq']:,.0f} final  (${stats['net_pnl']:+,.0f} net PnL)")
    print(f"{'═'*76}")
    print(f"  Sharpe (daily returns)  : {stats['sharpe_daily']:+.3f}")
    print(f"  Sharpe (per-trade ann)  : {stats['sharpe_per_trade']:+.3f}")
    print(f"  CAGR                    : {stats['cagr_pct']:+.2f}%")
    print(f"  Max Drawdown            : -{stats['max_dd_pct']:.2f}%")
    print(f"  Win Rate                : {stats['win_rate']:.1f}%")
    print(f"  Profit Factor           : {stats['profit_factor']:.3f}")
    print(f"  Avg Hold (trading days) : {stats['avg_hold_days']:.2f}")
    print(f"  % Bars In Market        : {stats['pct_time_in_market']:.1f}%")
    print(f"  Exit breakdown          : {stats['exit_breakdown']}")

    print(f"\n  {'Year':<6} {'n':>4} {'Win%':>5} {'PnL':>12}  Exits")
    print(f"  {'─'*65}")
    for yr, grp in tdf.groupby("year"):
        g  = grp["pnl"]; gn = len(g); gw = (g > 0).sum()
        ec = grp["exit_reason"].value_counts().to_dict()
        print(f"  {yr:<6} {gn:>4} {gw/gn*100:>4.0f}%  ${g.sum():>+10,.0f}  "
              + "  ".join(f"{k}:{v}" for k, v in ec.items()))

    gate_pct = regime["gate_open"].dropna().mean() * 100
    print(f"\n  Gate open {gate_pct:.1f}% of all daily bars")
    if "cond_vix" in regime.columns:
        vix_block = (1 - regime["cond_vix"].dropna().mean()) * 100
        print(f"  VIX ≥ {GATE_VIX_MAX}: blocks {vix_block:.1f}% of otherwise-open days")
    if "cond_soxl_trend" in regime.columns:
        soxl_block = (1 - regime["cond_soxl_trend"].dropna().mean()) * 100
        print(f"  SOXL self-trend: blocks {soxl_block:.1f}% of days (20-EMA > 50-EMA check)")

    try:
        with open(QC_STATE) as f:
            qs = json.load(f)
        soxl_fav = any("SOXL" in v for v in qs.get("allocations", {}).values())
        print(f"  QC signal: SOXL {'FAVORED (bonus signal)' if soxl_fav else 'not in top picks'}")
    except Exception:
        pass

    # Sample trades (prove correctness: entry/exit prices, stops, reason)
    print(f"\n  SAMPLE TRADES (first {len(sample_buf)}) — verifying correctness")
    print(f"  {'─'*76}")
    print(f"  {'EntDate':<10} {'In':>5} {'Out':>5}  {'Entry':>7} {'Exit':>7} "
          f"{'PnL%':>6} {'$PnL':>9} {'Reason':<9} {'dATR':>5} {'Days':>4}")
    print(f"  {'─'*76}")
    for t in sample_buf:
        print(f"  {t['entry_date']:<10} {t['entry_time']:>5} {t['exit_time']:>5}  "
              f"${t['entry_price']:>6.2f} ${t['exit_price']:>6.2f} "
              f"{t['pnl_pct']:>+5.1f}% ${t['pnl']:>+8.0f} "
              f"{t['exit_reason']:<9} ${t['entry_datr']:>4.2f} {t['days_held']:>4}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\nSOXL Regime-Gated Short-Hold  (5-cond gate + daily-ATR trail)")
    print("=" * 76)

    # 1. External daily data (SMH / SPY / VIX)
    ext_daily = fetch_external_daily("2019-01-01", "2026-06-16")
    ext_daily.index = pd.to_datetime(ext_daily.index).normalize()

    # 2. Load 5-min SOXL bars
    print("  Loading SOXL 5-min cache ...")
    df5 = pd.read_csv(SOXL_CACHE, index_col=0, parse_dates=True)
    if df5.index.tz is not None:
        df5.index = df5.index.tz_convert("America/New_York").tz_localize(None)
    df5 = df5.between_time("09:30", "16:00").copy()
    df5 = add_intraday_ind(df5)
    print(f"  {len(df5):,} bars  ({df5.index[0].date()} → {df5.index[-1].date()})")

    # 3. SOXL daily signals from 5-min data (prev-day shifted)
    soxl_d = build_soxl_daily(df5)
    print(f"  SOXL daily ATR: mean=${soxl_d['soxl_daily_atr'].mean():.2f}  "
          f"max=${soxl_d['soxl_daily_atr'].max():.2f}")

    # 4. Build combined regime gate
    regime = build_regime(ext_daily, soxl_d)
    regime.index = pd.to_datetime(regime.index).normalize()
    n_open  = int(regime["gate_open"].sum())
    n_total = int(regime["gate_open"].notna().sum())
    print(f"  Gate open {n_open}/{n_total} days ({n_open/max(n_total,1)*100:.1f}%)")

    # 5. Backtest
    final_eq, trades, sample_buf = run_backtest(df5, regime, n_samples=12)

    # 6. Stats + report
    stats = compute_stats(trades, INIT_EQ, df5)
    print_report(stats, trades, sample_buf, regime)

    # 7. Write result JSON
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    sharpe  = stats.get("sharpe_daily") or stats.get("sharpe_per_trade") or 0.0
    cagr_f  = stats.get("cagr_pct", 0.0) / 100.0
    maxdd_f = stats.get("max_dd_pct", 0.0) / 100.0
    success = (sharpe is not None and sharpe > 0.5
               and stats.get("num_trades", 0) >= 20
               and stats.get("cagr_pct", 0) > 3)

    summary = (
        f"Regime-gated SOXL short-hold: {stats['num_trades']} trades over "
        f"{stats['n_years']:.1f}yr. "
        f"Sharpe(daily)={sharpe:.3f}, CAGR={stats['cagr_pct']:.1f}%, "
        f"MaxDD={stats['max_dd_pct']:.1f}%, WR={stats['win_rate']:.1f}%, "
        f"AvgHold={stats['avg_hold_days']:.2f}d. "
        f"5-condition gate: SMH(20>50EMA+ADX>{GATE_ADX_THR})+SPY>200SMA+"
        f"VIX<{GATE_VIX_MAX}+SOXL(20>50EMA), all prev-day shifted (no lookahead). "
        f"Entry: 5-min close>EMA + close>prev5min + close>prior_day_close. "
        f"Trail: {TRAIL_MULT}×daily SOXL ATR. Sizing: {int(RISK_PER_TRADE*100)}% risk/trade."
    )

    result = {
        "task":               "soxl_regime_strategy",
        "sharpe":             sharpe,
        "sharpe_per_trade":   stats.get("sharpe_per_trade"),
        "cagr":               round(cagr_f, 4),
        "cagr_pct":           stats.get("cagr_pct"),
        "maxdd":              round(maxdd_f, 4),
        "maxdd_pct":          stats.get("max_dd_pct"),
        "avg_hold_days":      stats.get("avg_hold_days"),
        "num_trades":         stats.get("num_trades"),
        "win_rate":           stats.get("win_rate"),
        "profit_factor":      stats.get("profit_factor"),
        "pct_time_in_market": stats.get("pct_time_in_market"),
        "init_eq":            INIT_EQ,
        "final_eq":           stats.get("final_eq"),
        "net_pnl":            stats.get("net_pnl"),
        "n_years":            stats.get("n_years"),
        "exit_breakdown":     stats.get("exit_breakdown"),
        "gate_params": {
            "smh_ema_fast":     GATE_EMA_FAST,
            "smh_ema_slow":     GATE_EMA_SLOW,
            "adx_threshold":    GATE_ADX_THR,
            "spy_sma":          GATE_SPY_SMA,
            "vix_max":          GATE_VIX_MAX,
            "soxl_self_trend":  GATE_SOXL_TREND,
            "gate_shift":       "1 day (no look-ahead)",
        },
        "trade_params": {
            "trail_atr_mult":     TRAIL_MULT,
            "trail_atr_basis":    "daily SOXL ATR (not 5-min)",
            "take1_atr":          TAKE1_MULT,
            "take1_frac":         TAKE1_FRAC,
            "hard_stop_pct":      HARD_STOP_PCT,
            "hard_stop_atr":      HARD_STOP_MULT,
            "risk_per_trade":     RISK_PER_TRADE,
            "max_notional_frac":  MAX_NOTIONAL,
            "max_hold_days":      MAX_HOLD_DAYS,
            "entry_signal":       "close>EMA25(5m) + close>prev5m + close>prev_day_close",
        },
        "summary":  summary,
        "success":  success,
    }

    with open(RESULT_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"  Result → {RESULT_FILE}")
    print(f"  SUCCESS={success}  Sharpe={sharpe:.3f}  "
          f"CAGR={stats['cagr_pct']:+.1f}%  MaxDD=-{stats['max_dd_pct']:.1f}%")
    print("=" * 76 + "\n")

    return result


if __name__ == "__main__":
    main()
