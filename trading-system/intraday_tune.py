#!/usr/bin/env python3
"""
intraday_tune.py — Coordinate-ascent parameter sweep for mock_portfolio.py
Sweeps: SCAN_MIN_SCORE, MAX_POSITIONS, MAX_POS_PCT, TRAIL_MULT, MIN_TRAIL_PCT,
        DAILY_PROFIT_TARGET, DAILY_LOSS_LIMIT
Metric: avg daily P&L on 5-min yfinance bars (paper-realistic fills)
"""

import json, time as _time, warnings
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

ET = ZoneInfo("America/New_York")
RESULT_FILE = Path("/Users/quarki3/swarm_results/completed/intraday_daily_tune.json")
INITIAL_CASH = 50_000.0
COMM = 0.005
ADX_THRESH = 25
RV_MIN = 1.5

TUNE_UNIVERSE = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","JPM","V",
    "AMD","NFLX","QCOM","COIN","PLTR","CRWD","APP","MU","PANW",
    "QQQ","IWM","XLK","XLF","XLE","SOXL","TQQQ","GLD","USO",
    "BTC-USD","ETH-USD",
]

DEFAULT_CFG = dict(
    SCAN_MIN_SCORE=35, MAX_POSITIONS=6, MAX_POS_PCT=0.20,
    TRAIL_MULT=1.25,   MIN_TRAIL_PCT=0.012,
    DAILY_PROFIT_TARGET=0.025, DAILY_LOSS_LIMIT=-0.012,
)

SWEEP_GRID = dict(
    SCAN_MIN_SCORE=[20, 25, 30, 35, 40, 45, 50, 55, 60],
    MAX_POSITIONS=[2, 3, 4, 5, 6, 7, 8, 10],
    MAX_POS_PCT=[0.10, 0.15, 0.20, 0.25, 0.30],
    TRAIL_MULT=[0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5],
    MIN_TRAIL_PCT=[0.005, 0.008, 0.010, 0.012, 0.015, 0.020],
    DAILY_PROFIT_TARGET=[0.010, 0.015, 0.020, 0.025, 0.030, 0.040, 0.999],
    DAILY_LOSS_LIMIT=[-0.005, -0.008, -0.010, -0.012, -0.015, -0.020, -0.030],
)


# ── Indicators (identical to bot logic) ──────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = df["Close"].ewm(span=25, adjust=False).mean()
    hl  = df["High"] - df["Low"]
    hcp = (df["High"] - df["Close"].shift(1)).abs()
    lcp = (df["Low"]  - df["Close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()
    up  = df["High"] - df["High"].shift(1)
    dn  = df["Low"].shift(1) - df["Low"]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    aw  = tr.ewm(span=20, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(span=20, adjust=False).mean() / aw
    ndi = 100 * pd.Series(ndm, index=df.index).ewm(span=20, adjust=False).mean() / aw
    dx  = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    df["adx"] = dx.ewm(span=20, adjust=False).mean()
    df["pdi"]  = pdi
    df["ndi"]  = ndi
    delta  = df["Close"].diff()
    gain   = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss   = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    rs     = gain / loss.replace(0, 1e-10)
    df["rsi"]  = 100 - 100 / (1 + rs)
    df["mom3"] = df["Close"].pct_change(3) * 100
    # VWAP with daily reset
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = tp * df["Volume"]
    vwap_vals, cum_pv, cum_vol, prev_d = [], 0.0, 0.0, None
    for i in range(len(df)):
        d = df.index[i].date()
        if d != prev_d:
            cum_pv, cum_vol, prev_d = 0.0, 0.0, d
        v_pv = float(pv.iloc[i]) if pd.notna(pv.iloc[i]) else 0.0
        v_vol = float(df["Volume"].iloc[i]) if pd.notna(df["Volume"].iloc[i]) else 0.0
        cum_pv  += v_pv
        cum_vol += v_vol
        vwap_vals.append(cum_pv / cum_vol if cum_vol > 0 else float(df["Close"].iloc[i]))
    df["vwap"] = vwap_vals
    return df


# ── Data fetch ────────────────────────────────────────────────────────────────

def fetch_data() -> dict:
    print(f"Fetching 5-min bars for {len(TUNE_UNIVERSE)} symbols (60-day window)…")
    sym_data = {}

    # Batch download
    try:
        raw = yf.download(
            TUNE_UNIVERSE, period="60d", interval="5m",
            auto_adjust=True, progress=False, group_by="ticker",
        )
    except Exception as e:
        print(f"Batch download error: {e} — falling back to individual")
        raw = None

    for sym in TUNE_UNIVERSE:
        try:
            if raw is not None and isinstance(raw.columns, pd.MultiIndex):
                lvl0 = raw.columns.get_level_values(0)
                if sym in lvl0:
                    df = raw[sym].copy().dropna(how="all")
                else:
                    raise KeyError(sym)
            elif raw is not None and not isinstance(raw.columns, pd.MultiIndex):
                df = raw.copy().dropna(how="all")
            else:
                raise KeyError("batch failed")

            if df.empty or len(df) < 50:
                raise ValueError("insufficient rows")

        except Exception:
            try:
                df = yf.Ticker(sym).history(period="60d", interval="5m", auto_adjust=True)
                df = df.dropna(how="all")
                if df.empty or len(df) < 50:
                    print(f"  {sym}: skip (too few bars)")
                    continue
            except Exception as e2:
                print(f"  {sym}: skip ({e2})")
                continue

        try:
            df.index = pd.DatetimeIndex(df.index).tz_convert(ET)
            df = df[df["Close"] > 0].dropna(subset=["Close"])
            if len(df) < 50:
                print(f"  {sym}: skip ({len(df)} bars after clean)")
                continue
            df = add_indicators(df)
            sym_data[sym] = df
            print(f"  {sym}: {len(df)} bars  "
                  f"({df.index[0].date()} → {df.index[-1].date()})")
        except Exception as e:
            print(f"  {sym}: indicator error — {e}")

    return sym_data


# ── Build per-day structures ──────────────────────────────────────────────────

def build_day_cache(sym_data: dict):
    """
    Returns:
      trading_days  — sorted list of date objects with ≥1 RTH bar
      day_cache     — {sym: {day: {"open_p", "prev_adv", "bars" (RTH DataFrame + cum_vol)}}}
    """
    def rth_mask(df):
        h, m = df.index.hour, df.index.minute
        return ((h > 9) | ((h == 9) & (m >= 30))) & (h < 16)

    # Collect all RTH trading days across symbols
    rth_days_set = set()
    for sym, df in sym_data.items():
        mask = rth_mask(df)
        for d in df.index[mask].date:
            rth_days_set.add(d)
    trading_days = sorted(rth_days_set)

    if not trading_days:
        return [], {}

    print(f"\n{len(trading_days)} RTH trading days: "
          f"{trading_days[0]} → {trading_days[-1]}")

    day_cache = {}
    for sym, df in sym_data.items():
        day_cache[sym] = {}
        rth_df = df[rth_mask(df)]

        # Per-day total RTH volume (for prev_adv)
        day_vol_map: dict[date, float] = {}
        for d in trading_days:
            ddf = rth_df[rth_df.index.date == d]
            if not ddf.empty:
                day_vol_map[d] = float(ddf["Volume"].sum())

        sorted_vol_days = sorted(day_vol_map)

        for d in trading_days:
            ddf = rth_df[rth_df.index.date == d].copy()
            if ddf.empty:
                continue

            open_p = float(ddf.iloc[0]["Open"])
            if not (np.isfinite(open_p) and open_p > 0):
                open_p = float(ddf.iloc[0]["Close"])

            # 5-day rolling prev ADV from RTH volume
            past = [pd_ for pd_ in sorted_vol_days if pd_ < d][-5:]
            prev_adv = float(np.mean([day_vol_map[p] for p in past])) if past else 0.0

            # Running cumulative volume within the day
            cum = 0.0
            cum_vols = []
            for v in ddf["Volume"]:
                v_f = float(v) if pd.notna(v) else 0.0
                cum += v_f
                cum_vols.append(cum)
            ddf["cum_vol"] = cum_vols

            day_cache[sym][d] = {
                "open_p":   open_p,
                "prev_adv": prev_adv,
                "bars":     ddf,
            }

    return trading_days, day_cache


# ── Scoring (mirrors bot's _score_symbol exactly) ────────────────────────────

def _get(row, col, default=0.0):
    v = row.get(col, default) if hasattr(row, "get") else getattr(row, col, default)
    return float(v) if pd.notna(v) else default


def score_bar(row, open_p: float, prev_adv: float):
    """Returns (signal, score, atr, close)."""
    close  = _get(row, "Close", 0.0)
    if close <= 0:
        return "watch", 0, 0.0, 0.0

    atr   = _get(row, "atr",  close * 0.01)
    if not (np.isfinite(atr) and atr > 0):
        atr = close * 0.01
    adx   = _get(row, "adx",  0.0)
    pdi   = _get(row, "pdi",  0.0)
    ndi   = _get(row, "ndi",  0.0)
    ema   = _get(row, "ema",  close)
    rsi   = _get(row, "rsi",  50.0)
    mom3  = _get(row, "mom3", 0.0)
    vwap  = _get(row, "vwap", close)
    cum_v = _get(row, "cum_vol", 0.0)

    reg = "ranging"
    if adx > ADX_THRESH:
        reg = "bull" if pdi >= ndi else "bear"

    rv       = cum_v / prev_adv if prev_adv > 0 else 1.0
    chg      = (close - open_p) / open_p * 100 if open_p > 0 else 0.0
    vwap_pct = (close - vwap) / vwap * 100 if vwap > 0 else 0.0

    rv_ok  = rv >= RV_MIN
    rsi_ok = rsi < 75

    if not (reg in ("ranging", "bull") and close > ema and rv_ok and rsi_ok):
        return "watch", 0, atr, close

    adx_eff    = max(adx, ADX_THRESH)
    mom_bonus  = min(abs(mom3) / 3.0, 1.0) * 8 if mom3 > 0 else 0.0
    vwap_bonus = max(0.0, min(vwap_pct / 1.0, 1.0)) * 7
    score = int(
        min(max(rv, 0.1) / 3.0, 1.0) * 26 +
        min(abs(chg) / 5.0, 1.0) * 30 +
        min(adx_eff / 50.0, 1.0) * 29 +
        mom_bonus + vwap_bonus
    )
    return "long", score, atr, close


# ── Simulation (one config, one pass over all days) ──────────────────────────

def run_simulation(cfg: dict, trading_days: list, day_cache: dict) -> dict:
    MIN_SCORE   = cfg["SCAN_MIN_SCORE"]
    MAX_POS     = cfg["MAX_POSITIONS"]
    POS_PCT     = cfg["MAX_POS_PCT"]
    TRAIL_M     = cfg["TRAIL_MULT"]
    TRAIL_FLOOR = cfg["MIN_TRAIL_PCT"]
    PT          = cfg["DAILY_PROFIT_TARGET"]
    LL          = cfg["DAILY_LOSS_LIMIT"]

    syms = list(day_cache.keys())
    daily_pnls: list[float] = []
    n_trades = 0

    for day in trading_days:
        avail = {s: day_cache[s][day] for s in syms if day in day_cache[s]}
        if not avail:
            continue

        # Merge all bar timestamps for this day (already RTH-only)
        all_times = sorted(set(t for dd in avail.values() for t in dd["bars"].index))
        all_times = [t for t in all_times
                     if (t.hour > 9 or (t.hour == 9 and t.minute >= 35)) and
                        (t.hour < 15 or (t.hour == 15 and t.minute <= 45))]
        if not all_times:
            continue

        cash        = INITIAL_CASH
        positions   = {}   # sym → {entry_price, shares, trail_stop, last_price, unrealized}
        day_pnl     = 0.0
        day_halted  = False
        last_scan_t = None

        for bar_time in all_times:
            mins = bar_time.hour * 60 + bar_time.minute

            # EOD: close all remaining positions
            if mins >= 15 * 60 + 45:
                for sym, pos in list(positions.items()):
                    p   = pos.get("last_price", pos["entry_price"])
                    pnl = (p - pos["entry_price"]) * pos["shares"] - pos["shares"] * COMM * 2
                    day_pnl += pnl
                    cash += pos["shares"] * p - pos["shares"] * COMM
                    n_trades += 1
                positions.clear()
                break

            # Update each open position, apply exits
            for sym in list(positions.keys()):
                if sym not in avail:
                    continue
                bars_df = avail[sym]["bars"]
                if bar_time not in bars_df.index:
                    continue
                row   = bars_df.loc[bar_time]
                close = _get(row, "Close", 0.0)
                if close <= 0:
                    continue
                atr = _get(row, "atr", close * 0.01)
                if not (np.isfinite(atr) and atr > 0):
                    atr = close * 0.01
                ema = _get(row, "ema", close)

                pos = positions[sym]
                # Ratchet trail stop
                min_dist = close * TRAIL_FLOOR
                new_stop = close - max(TRAIL_M * atr, min_dist)
                if new_stop > pos["trail_stop"]:
                    pos["trail_stop"] = new_stop
                pos["last_price"]  = close
                pos["unrealized"]  = (close - pos["entry_price"]) * pos["shares"]

                exit_p = None
                if close <= pos["trail_stop"]:
                    exit_p = close
                elif np.isfinite(ema) and ema > 0 and close < ema * 0.999:
                    exit_p = close

                if exit_p is not None:
                    pnl = (exit_p - pos["entry_price"]) * pos["shares"] - pos["shares"] * COMM * 2
                    day_pnl += pnl
                    cash    += pos["shares"] * exit_p - pos["shares"] * COMM
                    n_trades += 1
                    del positions[sym]

            if day_halted:
                break

            # Daily P&L guard
            unrl      = sum(p.get("unrealized", 0.0) for p in positions.values())
            total_day = day_pnl + unrl
            day_pct   = total_day / INITIAL_CASH
            if day_pct >= PT or day_pct <= LL:
                for sym, pos in list(positions.items()):
                    p   = pos.get("last_price", pos["entry_price"])
                    pnl = (p - pos["entry_price"]) * pos["shares"] - pos["shares"] * COMM * 2
                    day_pnl += pnl
                    cash    += pos["shares"] * p - pos["shares"] * COMM
                    n_trades += 1
                positions.clear()
                day_halted = True
                break

            # No new entries in the last 20 min of RTH
            if mins >= 15 * 60 + 25:
                continue

            # Rescan every 20 min
            if last_scan_t is None or (bar_time - last_scan_t).total_seconds() >= 20 * 60:
                last_scan_t = bar_time

                if len(positions) < MAX_POS:
                    candidates = []
                    for sym, dd in avail.items():
                        if sym in positions:
                            continue
                        bars_df = dd["bars"]
                        if bar_time not in bars_df.index:
                            continue
                        row = bars_df.loc[bar_time]
                        sig, score, atr, close = score_bar(row, dd["open_p"], dd["prev_adv"])
                        if sig == "long" and score >= MIN_SCORE and close > 0:
                            candidates.append((score, sym, atr, close))

                    candidates.sort(reverse=True)
                    for _, sym, atr, close in candidates:
                        if len(positions) >= MAX_POS or sym in positions:
                            break
                        cur_eq = cash + sum(
                            p.get("last_price", p["entry_price"]) * p["shares"]
                            for p in positions.values()
                        )
                        budget = min(cur_eq * POS_PCT, cash * 0.95)
                        shares = int(budget / close) if close > 0 else 0
                        if shares < 1:
                            continue
                        cost = shares * close + shares * COMM
                        if cost > cash:
                            shares = max(int((cash * 0.95) / (close + COMM)), 0)
                        if shares < 1:
                            continue

                        cash -= shares * close + shares * COMM
                        min_dist = close * TRAIL_FLOOR
                        trail    = close - max(TRAIL_M * atr, min_dist)
                        positions[sym] = {
                            "entry_price": close,
                            "shares":      shares,
                            "trail_stop":  trail,
                            "last_price":  close,
                            "unrealized":  0.0,
                        }

        # Force-close any positions still open (missed EOD break)
        for sym, pos in list(positions.items()):
            p   = pos.get("last_price", pos["entry_price"])
            pnl = (p - pos["entry_price"]) * pos["shares"] - pos["shares"] * COMM * 2
            day_pnl += pnl
            cash    += pos["shares"] * p - pos["shares"] * COMM
            n_trades += 1
        positions.clear()

        daily_pnls.append(day_pnl)

    n = len(daily_pnls)
    if n == 0:
        return {"avg_daily_pnl": 0.0, "daily_winrate": 0.0, "n_days": 0, "n_trades": 0, "total_pnl": 0.0}

    pos_days = sum(1 for p in daily_pnls if p > 0)
    return {
        "avg_daily_pnl": round(float(np.mean(daily_pnls)), 2),
        "daily_winrate": round(float(pos_days / n), 3),
        "n_days":        n,
        "n_trades":      n_trades,
        "total_pnl":     round(float(sum(daily_pnls)), 2),
    }


# ── Coordinate ascent ─────────────────────────────────────────────────────────

def coordinate_ascent(trading_days, day_cache):
    cfg = DEFAULT_CFG.copy()

    print("\n=== Baseline ===")
    base = run_simulation(cfg, trading_days, day_cache)
    best_pnl = base["avg_daily_pnl"]
    print(f"  avg_daily_pnl=${best_pnl:.2f}  "
          f"win_rate={base['daily_winrate']:.1%}  "
          f"trades={base['n_trades']}  days={base['n_days']}")

    all_sweep: list[dict] = []

    for rnd in range(1, 6):
        print(f"\n=== Round {rnd} ===")
        improved = False

        for param, grid in SWEEP_GRID.items():
            best_val     = cfg[param]
            best_val_pnl = best_pnl

            for v in grid:
                r = run_simulation({**cfg, param: v}, trading_days, day_cache)
                all_sweep.append({"config": {**cfg, param: v}, "avg_daily_pnl": r["avg_daily_pnl"],
                                   "daily_winrate": r["daily_winrate"]})
                if r["avg_daily_pnl"] > best_val_pnl:
                    best_val_pnl = r["avg_daily_pnl"]
                    best_val     = v

            if best_val != cfg[param]:
                print(f"  {param}: {cfg[param]} → {best_val}  "
                      f"(${best_pnl:.2f} → ${best_val_pnl:.2f}/day)")
                cfg[param] = best_val
                best_pnl   = best_val_pnl
                improved   = True
            else:
                print(f"  {param}: {cfg[param]} (no change, best=${best_pnl:.2f})")

        if not improved:
            print(f"Converged after {rnd} round(s).")
            break

    final = run_simulation(cfg, trading_days, day_cache)
    print(f"\n=== Best Config ===")
    for k, v in cfg.items():
        marker = "←" if DEFAULT_CFG[k] != v else ""
        print(f"  {k}: {v}  {marker}")
    print(f"  avg_daily_pnl: ${final['avg_daily_pnl']:.2f}")
    print(f"  daily_winrate: {final['daily_winrate']:.1%}")
    print(f"  n_trades: {final['n_trades']}  n_days: {final['n_days']}")

    return cfg, final, all_sweep


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    t0 = _time.time()

    sym_data = fetch_data()
    if len(sym_data) < 5:
        raise RuntimeError(f"Only {len(sym_data)} symbols loaded — aborting")

    trading_days, day_cache = build_day_cache(sym_data)
    if len(trading_days) < 10:
        raise RuntimeError(f"Only {len(trading_days)} trading days — aborting")

    # Baseline for comparison
    baseline = run_simulation(DEFAULT_CFG, trading_days, day_cache)

    best_cfg, best_result, all_sweep = coordinate_ascent(trading_days, day_cache)

    elapsed = _time.time() - t0

    output = {
        "task":          "intraday_daily_tune",
        "best_config":   best_cfg,
        "avg_daily_ret": best_result["avg_daily_pnl"],
        "daily_winrate": best_result["daily_winrate"],
        "n_days_tested": best_result["n_days"],
        "n_trades":      best_result["n_trades"],
        "total_pnl":     best_result["total_pnl"],
        "baseline": {
            "config":        DEFAULT_CFG,
            "avg_daily_pnl": baseline["avg_daily_pnl"],
            "daily_winrate": baseline["daily_winrate"],
        },
        "param_changes": {
            k: {"from": DEFAULT_CFG[k], "to": best_cfg[k]}
            for k in DEFAULT_CFG if DEFAULT_CFG[k] != best_cfg[k]
        },
        "summary": (
            f"Coordinate-ascent over {len(SWEEP_GRID)} params × "
            f"{sum(len(v) for v in SWEEP_GRID.values())} grid points. "
            f"Best: ${best_result['avg_daily_pnl']:.2f}/day "
            f"({best_result['daily_winrate']:.1%} win-rate) over "
            f"{best_result['n_days']} days. "
            f"Baseline was ${baseline['avg_daily_pnl']:.2f}/day."
        ),
        "elapsed_s": round(elapsed, 1),
        "success":   True,
    }

    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(output, indent=2))
    print(f"\nResult → {RESULT_FILE}")
    print(f"Elapsed: {elapsed:.1f}s")
    return output


if __name__ == "__main__":
    main()
