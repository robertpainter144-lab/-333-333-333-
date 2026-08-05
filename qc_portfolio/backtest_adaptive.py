"""
A/B backtest: legacy flat-3% trailing stop vs the adaptive stop stack.

Walk-forward, daily rebalance, identical signal path. The only differences
between runs are:
  - trail %: adaptive uses BASE_K × ATR% × VIX_mult × SPY_trend × burst_mult,
    clipped to (FLOOR, CAP). Legacy uses a fixed trail_stop_pct.
  - hard stop: adaptive only (entry − min(8%, 2×ATR%)).
  - time stop: adaptive only (idle > N sessions with <1% progress).
  - profit step: adaptive only (sell half at +2×ATR%, ratchet trail to BE).
  - DD ladder: adaptive scales thresholds by realized-vol regime.

Run:
    python3.10 backtest_adaptive.py --years 5
"""
from __future__ import annotations

import argparse
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from config import (
    STRATEGY_CONFIGS, LARGE_CAP_UNIVERSE, LEVERAGED_ETF_UNIVERSE,
    DD_LADDER,
    LEVERAGED_TICKERS, LEV_TIME_STOP_DAYS, LEV_TRAIL_MULT,
    LEV_NO_ENTRY_VIX, LEV_NO_ENTRY_BURST, LEV_MAX_PORTFOLIO_PCT,
)
import adaptive_stops as A


# ── Precomputation helpers ───────────────────────────────────────────────────
def _precompute_market_context(close: pd.DataFrame, vix: pd.Series) -> dict:
    """Return per-date dict with vix, vix_chg, spy_above_200, rv5, rv60."""
    spy = close["SPY"]
    rv5  = spy.pct_change().rolling(5).std()  * math.sqrt(252)
    rv60 = spy.pct_change().rolling(60).std() * math.sqrt(252)
    sma200 = spy.rolling(200).mean()
    above  = spy > sma200
    vix_chg = vix.pct_change()
    out = {}
    for d in close.index:
        v = vix.get(d)
        if v is None or pd.isna(v):
            continue
        out[d] = {
            "vix":         float(v),
            "vix_chg":     float(vix_chg.get(d, 0.0) or 0.0),
            "spy_above":   bool(above.get(d, False)),
            "rv5":         float(rv5.get(d, 0.0) or 0.0),
            "rv60":        float(rv60.get(d, 0.0) or 0.0),
        }
    return out


def _precompute_atr_pct(high: pd.DataFrame, low: pd.DataFrame, close: pd.DataFrame,
                        window: int = 14) -> pd.DataFrame:
    """14-day ATR / close for every symbol-date."""
    prev = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev).abs(),
        (low - prev).abs(),
    ]).groupby(level=0).max() if False else None
    # Simpler element-wise:
    tr = (high - low).combine(
        (high - prev).abs(), np.maximum
    ).combine((low - prev).abs(), np.maximum)
    atr = tr.rolling(window).mean()
    return (atr / close).replace([np.inf, -np.inf], np.nan)


def _trail_pct_for(ctx_row: dict, atr_pct: float, leveraged: bool = False) -> float:
    """Same formula as adaptive_stops.effective_trail, parameterized."""
    vix = ctx_row["vix"]
    m = next(mult for ub, mult in A.VIX_BANDS if vix < ub)
    if ctx_row["vix_chg"] >= A.VIX_DAILY_SPIKE_PCT:
        m *= A.VIX_SPIKE_MULT
    spy_m = A.SPY_ON_MULT if ctx_row["spy_above"] else A.SPY_OFF_MULT
    rv5, rv60 = ctx_row["rv5"], ctx_row["rv60"]
    burst_m = A.BURST_MULT if (rv60 > 0 and rv5 / rv60 >= A.BURST_RATIO_THRESHOLD) else 1.0
    lev_m = LEV_TRAIL_MULT if leveraged else 1.0
    raw = A.BASE_K * atr_pct * m * spy_m * burst_m * lev_m
    return max(A.FLOOR_PCT, min(A.CAP_PCT, raw))


def _lev_entry_blocked(ctx_row: dict) -> bool:
    """Block new leveraged-ETF entries when VIX is elevated or vol bursting."""
    if ctx_row["vix"] >= LEV_NO_ENTRY_VIX:
        return True
    rv5, rv60 = ctx_row["rv5"], ctx_row["rv60"]
    return rv60 > 0 and rv5 / rv60 >= LEV_NO_ENTRY_BURST


def _scale_ladder(default_ladder, rv60):
    if rv60 <= 0:
        return list(default_ladder)
    if rv60 < 0.12:
        scale = 0.70
    elif rv60 > 0.22:
        scale = 1.30
    else:
        scale = 1.00
    return [(thr * scale, tgt) for thr, tgt in default_ladder]


# ── Simulation core ──────────────────────────────────────────────────────────
def _momentum_score(prices: pd.Series) -> float:
    n = len(prices)
    if n < 21:
        return 0.0
    curr  = float(prices.iloc[-1])
    roc20 = curr / float(prices.iloc[-20]) - 1
    roc60 = curr / float(prices.iloc[-60]) - 1 if n >= 61 else roc20
    return 0.5 * roc20 + 0.5 * roc60


def simulate(close, high, low, vix, strategy_ids, *,
             adaptive: bool, fixed_trail: float = 0.03,
             initial_capital: float = 100_000.0,
             rebalance_days: int = 1,
             time_stop_days: int = 7,
             time_stop_progress: float = 0.01,
             hard_floor: float = 0.08,
             hard_mult: float = 2.0,
             profit_mult: float = 2.0,
             use_lev_rules: bool = False):
    warmup = 200  # for SPY 200-SMA
    trade_dates = close.index[warmup:]

    ctx_map  = _precompute_market_context(close, vix) if adaptive else None
    atr_pct  = _precompute_atr_pct(high, low, close) if adaptive else None

    n = len(strategy_ids)
    base_w = [1.0] * n
    base_w[0] = 2.0
    bt_weights = [w / sum(base_w) for w in base_w]

    cash, peak_eq, last_reb = float(initial_capital), float(initial_capital), None
    exposure_level, tiers_fired = 1.0, set()
    holdings = {}  # sym -> {qty, cost, peak, entry_date, step_taken}
    trades, equity_curve, dd_curve = [], {}, {}

    for date in trade_dates:
        prices = {s: float(close.at[date, s]) for s in close.columns
                  if not pd.isna(close.at[date, s])}
        lows   = {s: float(low.at[date, s])   for s in low.columns
                  if not pd.isna(low.at[date, s])}

        ctx = ctx_map.get(date) if adaptive else None

        # ── Stop checks (per symbol) ────────────────────────────────────────
        for sym in list(holdings.keys()):
            if sym not in prices:
                continue
            h = holdings[sym]
            p_close = prices[sym]
            p_check = lows.get(sym, p_close)
            entry   = h["cost"]
            peak    = h["peak"]

            leveraged = use_lev_rules and sym in LEVERAGED_TICKERS
            if adaptive and ctx is not None and sym in atr_pct.columns:
                a = atr_pct.at[date, sym]
                if not pd.isna(a):
                    trail = _trail_pct_for(ctx, float(a), leveraged=leveraged)
                    hard  = min(hard_floor, hard_mult * float(a))
                else:
                    trail, hard = fixed_trail, hard_floor
                    a = 0.0
            else:
                trail, hard, a = fixed_trail, hard_floor, 0.0

            # Hard stop
            if p_check <= entry * (1.0 - hard):
                fill = min(p_check, entry * (1.0 - hard))
                trades.append({"date": date, "symbol": sym, "side": "sell",
                               "qty": h["qty"], "price": fill,
                               "pnl": (fill - entry) * h["qty"],
                               "reason": "hard_stop"})
                cash += h["qty"] * fill
                del holdings[sym]
                continue

            # Trail stop
            stop_px = peak * (1 - trail)
            if p_check < stop_px:
                fill = min(p_check, stop_px)
                trades.append({"date": date, "symbol": sym, "side": "sell",
                               "qty": h["qty"], "price": fill,
                               "pnl": (fill - entry) * h["qty"],
                               "reason": "trail_stop"})
                cash += h["qty"] * fill
                del holdings[sym]
                continue

            # Adaptive-only: profit step + time stop
            if adaptive and a > 0:
                if not h["step_taken"] and p_close >= entry * (1.0 + profit_mult * a):
                    half = h["qty"] * 0.5
                    trades.append({"date": date, "symbol": sym, "side": "sell",
                                   "qty": half, "price": p_close,
                                   "pnl": (p_close - entry) * half,
                                   "reason": "profit_step"})
                    cash += half * p_close
                    h["qty"]        -= half
                    h["peak"]        = max(h["peak"], entry)
                    h["step_taken"]  = True
                    if h["qty"] <= 0:
                        del holdings[sym]
                        continue

            if adaptive:
                held = (date - h["entry_date"]).days
                limit = LEV_TIME_STOP_DAYS if leveraged else time_stop_days
                if held >= limit and p_close < entry * (1.0 + time_stop_progress):
                    trades.append({"date": date, "symbol": sym, "side": "sell",
                                   "qty": h["qty"], "price": p_close,
                                   "pnl": (p_close - entry) * h["qty"],
                                   "reason": "time_stop"})
                    cash += h["qty"] * p_close
                    del holdings[sym]
                    continue

            if p_close > h["peak"]:
                h["peak"] = p_close

        # ── Portfolio drawdown ladder ───────────────────────────────────────
        port_val = cash + sum(h["qty"] * prices.get(s, h["cost"])
                              for s, h in holdings.items())
        prev_peak = peak_eq
        peak_eq   = max(peak_eq, port_val)
        dd        = (peak_eq - port_val) / peak_eq if peak_eq > 0 else 0.0

        if peak_eq > prev_peak:
            tiers_fired.clear()
            exposure_level = 1.0

        ladder = (_scale_ladder(DD_LADDER, ctx["rv60"]) if adaptive and ctx
                  else list(DD_LADDER))
        for threshold, target_exp in sorted(ladder):
            if dd >= threshold and threshold not in tiers_fired and exposure_level > target_exp:
                sell_frac = 1.0 - (target_exp / exposure_level) if exposure_level > 0 else 1.0
                for sym in list(holdings.keys()):
                    if sym not in prices:
                        continue
                    qty = holdings[sym]["qty"] * sell_frac
                    if qty <= 0:
                        continue
                    p = prices[sym]
                    trades.append({"date": date, "symbol": sym, "side": "sell",
                                   "qty": qty, "price": p,
                                   "pnl": (p - holdings[sym]["cost"]) * qty,
                                   "reason": f"ladder_{int(threshold*100)}pct"})
                    cash += qty * p
                    holdings[sym]["qty"] -= qty
                    if holdings[sym]["qty"] <= 0:
                        del holdings[sym]
                tiers_fired.add(threshold)
                exposure_level = target_exp

        # ── Rebalance ───────────────────────────────────────────────────────
        if last_reb is None or (date - last_reb).days >= rebalance_days:
            hist = close.loc[:date]
            target = {}
            port_val = cash + sum(h["qty"] * prices.get(s, h["cost"])
                                  for s, h in holdings.items())
            for i, sid in enumerate(strategy_ids):
                cfg = STRATEGY_CONFIGS.get(sid, {})
                univ = cfg.get("universe", LARGE_CAP_UNIVERSE)
                alloc = cfg.get("alloc", "single_winner")
                avail = [s for s in univ if s in close.columns]
                if not avail:
                    continue
                strat_cap = port_val * bt_weights[i]
                scores = {s: _momentum_score(hist[s].dropna()) for s in avail}
                ranked = sorted(scores, key=scores.get, reverse=True)
                if alloc == "single_winner" and ranked:
                    target[ranked[0]] = target.get(ranked[0], 0) + strat_cap
                elif alloc in ("top3_equal", "top5_equal"):
                    k = 3 if alloc == "top3_equal" else 5
                    top = ranked[:k]
                    for s in top:
                        target[s] = target.get(s, 0) + strat_cap / len(top)

            # Leveraged-ETF rules: entry veto + aggregate cap (mirror compute_target)
            if use_lev_rules and ctx is not None:
                if _lev_entry_blocked(ctx):
                    for sym in list(target.keys()):
                        if sym not in LEVERAGED_TICKERS:
                            continue
                        held_val = (holdings.get(sym, {}).get("qty", 0)
                                    * prices.get(sym, 0))
                        if target[sym] > held_val:
                            target[sym] = held_val
                lev_total = sum(target[s] for s in target if s in LEVERAGED_TICKERS)
                lev_cap   = port_val * LEV_MAX_PORTFOLIO_PCT
                if lev_total > lev_cap and lev_total > 0:
                    scale = lev_cap / lev_total
                    for s in list(target.keys()):
                        if s in LEVERAGED_TICKERS:
                            target[s] *= scale

            for sym in list(holdings.keys()):
                if sym not in target and sym in prices:
                    p, h = prices[sym], holdings[sym]
                    trades.append({"date": date, "symbol": sym, "side": "sell",
                                   "qty": h["qty"], "price": p,
                                   "pnl": (p - h["cost"]) * h["qty"],
                                   "reason": "rebalance"})
                    cash += h["qty"] * p
                    del holdings[sym]
            for sym, tgt_val in target.items():
                if sym not in prices or prices[sym] <= 0:
                    continue
                p = prices[sym]
                cur_qty = holdings.get(sym, {}).get("qty", 0.0)
                diff = tgt_val - cur_qty * p
                if abs(diff) < 50:
                    continue
                qty = abs(diff) / p
                if diff > 0 and cash >= qty * p:
                    cash -= qty * p
                    if sym in holdings:
                        old_q, old_c = holdings[sym]["qty"], holdings[sym]["cost"]
                        new_q = old_q + qty
                        holdings[sym]["qty"]  = new_q
                        holdings[sym]["cost"] = (old_c * old_q + p * qty) / new_q
                        holdings[sym]["peak"] = max(holdings[sym]["peak"], p)
                    else:
                        holdings[sym] = {"qty": qty, "cost": p, "peak": p,
                                         "entry_date": date, "step_taken": False}
                    trades.append({"date": date, "symbol": sym, "side": "buy",
                                   "qty": qty, "price": p, "pnl": 0,
                                   "reason": "rebalance"})
                elif diff < 0:
                    sell_qty = min(qty, cur_qty)
                    if sell_qty > 0:
                        h = holdings[sym]
                        trades.append({"date": date, "symbol": sym, "side": "sell",
                                       "qty": sell_qty, "price": p,
                                       "pnl": (p - h["cost"]) * sell_qty,
                                       "reason": "rebalance"})
                        cash += sell_qty * p
                        h["qty"] -= sell_qty
                        if h["qty"] <= 0:
                            del holdings[sym]
            last_reb = date

        port_val = cash + sum(h["qty"] * prices.get(s, h["cost"])
                              for s, h in holdings.items())
        equity_curve[date] = port_val
        dd_curve[date]     = max((peak_eq - port_val) / peak_eq * 100, 0.0) if peak_eq > 0 else 0.0

    ec = pd.Series(equity_curve)
    rets = ec.pct_change().dropna()
    years = max((ec.index[-1] - ec.index[0]).days / 365.25, 0.1)
    cagr  = ((float(ec.iloc[-1]) / initial_capital) ** (1 / years) - 1) * 100
    sharpe = float(rets.mean() / rets.std() * math.sqrt(252)) if rets.std() > 0 else 0.0
    rm = ec.cummax()
    max_dd = float(((ec - rm) / rm).min() * 100)
    sells = [t for t in trades if t["side"] == "sell"]
    wins = sum(1 for t in sells if t["pnl"] > 0)
    reasons = {}
    for t in sells:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {
        "metrics": {
            "cagr":   round(cagr, 2),
            "sharpe": round(sharpe, 2),
            "max_dd": round(abs(max_dd), 2),
            "win_rate": round(wins / len(sells) * 100 if sells else 0, 2),
            "trades":   len(sells),
            "final":    round(float(ec.iloc[-1]), 2),
        },
        "reasons": reasons,
        "equity":  ec,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=5.0)
    ap.add_argument("--ids", default=None, help="comma-separated strategy ids")
    ap.add_argument("--fixed", type=float, default=0.03, help="legacy fixed trail %")
    args = ap.parse_args()

    sids = ([int(x) for x in args.ids.split(",")]
            if args.ids else list(STRATEGY_CONFIGS.keys())[:5])
    print(f"strategy_ids = {sids}")

    symbols = {"SPY"}
    for sid in sids:
        cfg = STRATEGY_CONFIGS.get(sid, {})
        symbols.update(cfg.get("universe", LARGE_CAP_UNIVERSE))

    end = datetime.now()
    start = end - timedelta(days=int(args.years * 365) + 240)
    print(f"[BT] downloading {len(symbols)} symbols + ^VIX {start.date()} → {end.date()}")
    raw = yf.download(list(symbols) + ["^VIX"], start=start.strftime("%Y-%m-%d"),
                      end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True,
                      threads=True, group_by="ticker") if False else yf.download(
        list(symbols) + ["^VIX"], start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"), progress=False, auto_adjust=True, threads=True)

    if raw.empty:
        print("no data")
        return
    close = raw["Close"].dropna(how="all").ffill()
    high  = raw["High"].reindex(close.index).ffill()
    low   = raw["Low"].reindex(close.index).ffill()
    if "^VIX" not in close.columns:
        print("VIX missing")
        return
    vix = close["^VIX"]
    close = close.drop(columns=["^VIX"])
    high  = high.drop(columns=["^VIX"])
    low   = low.drop(columns=["^VIX"])

    print(f"[BT] running fixed-{args.fixed*100:.1f}% baseline …")
    base = simulate(close, high, low, vix, sids, adaptive=False, fixed_trail=args.fixed)
    print(f"[BT] running adaptive …")
    adapt = simulate(close, high, low, vix, sids, adaptive=True)
    print(f"[BT] running adaptive + leveraged rules …")
    lev = simulate(close, high, low, vix, sids, adaptive=True, use_lev_rules=True)

    def fmt(m):
        return (f"CAGR={m['cagr']}%  Sharpe={m['sharpe']}  MaxDD={m['max_dd']}%  "
                f"WinRate={m['win_rate']}%  trades={m['trades']}  final=${m['final']:,}")
    print()
    print("─" * 88)
    print(f"FIXED-{args.fixed*100:.1f}%   {fmt(base['metrics'])}")
    print(f"  exit reasons: {base['reasons']}")
    print(f"ADAPTIVE       {fmt(adapt['metrics'])}")
    print(f"  exit reasons: {adapt['reasons']}")
    print(f"ADAPTIVE+LEV   {fmt(lev['metrics'])}")
    print(f"  exit reasons: {lev['reasons']}")
    print("─" * 88)


if __name__ == "__main__":
    main()
