#!/usr/bin/env python3
"""
Quick strategy lab for ES=F 5-minute bars over recent history.

Searches EMA crossover variants and compares:
- long-only crossover
- long-only with trend filter (close > EMA 200)
- long/short crossover

Includes a simple per-position-change transaction cost model (bps).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import product

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass
class Result:
    mode: str
    fast: int
    slow: int
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_like: float
    trades: int
    win_rate: float


def download(symbol: str = "ES=F", days: int = 30, interval: str = "5m") -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    df = yf.download(symbol, start=start, end=end, interval=interval, progress=False)
    if df.empty:
        raise RuntimeError("No data returned.")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df.dropna(subset=["Close"]).copy()


def make_emas(df: pd.DataFrame, spans: list[int]) -> pd.DataFrame:
    out = df.copy()
    for s in sorted(set(spans)):
        out[f"ema_{s}"] = out["Close"].ewm(span=s, adjust=False).mean()
    out["ret"] = out["Close"].pct_change().fillna(0.0)
    return out


def score(data: pd.DataFrame, position: pd.Series, cost_bps: float) -> tuple[float, float, float, int]:
    pos = position.fillna(0.0).astype(float)
    changes = pos.diff().abs().fillna(0.0)
    # Cost applied each time position changes (enter/exit/reverse).
    cost = changes * (cost_bps / 10000.0)
    strat_ret = pos.shift(1).fillna(0.0) * data["ret"] - cost

    equity = (1.0 + strat_ret).cumprod()
    total_return_pct = float((equity.iloc[-1] - 1.0) * 100.0)
    dd = equity / equity.cummax() - 1.0
    max_drawdown_pct = float(dd.min() * 100.0)

    # Sharpe-like metric for relative ranking only.
    vol = float(strat_ret.std())
    sharpe_like = float((strat_ret.mean() / vol) * np.sqrt(252 * 24 * 12)) if vol > 0 else 0.0
    trades = int(changes[changes > 0].count())
    return total_return_pct, max_drawdown_pct, sharpe_like, trades


def trade_win_rate(data: pd.DataFrame, position: pd.Series) -> float:
    pos = position.fillna(0.0).astype(float).values
    closes = data["Close"].values
    returns = []
    entry_idx = None
    entry_side = 0.0

    for i in range(1, len(pos)):
        prev_side = pos[i - 1]
        curr_side = pos[i]

        # Open new trade when moving from flat to non-flat.
        if entry_idx is None and prev_side == 0.0 and curr_side != 0.0:
            entry_idx = i
            entry_side = curr_side
            continue

        if entry_idx is not None:
            # Close trade on flatten or reversal.
            if curr_side == 0.0 or curr_side != entry_side:
                entry_px = closes[entry_idx]
                exit_px = closes[i]
                r = (exit_px / entry_px - 1.0) if entry_side > 0 else (entry_px / exit_px - 1.0)
                returns.append(r)

                # Reversal: immediately open opposite trade at same bar.
                if curr_side != 0.0 and curr_side != entry_side:
                    entry_idx = i
                    entry_side = curr_side
                else:
                    entry_idx = None
                    entry_side = 0.0

    # Close any still-open trade at final bar.
    if entry_idx is not None:
        entry_px = closes[entry_idx]
        exit_px = closes[-1]
        r = (exit_px / entry_px - 1.0) if entry_side > 0 else (entry_px / exit_px - 1.0)
        returns.append(r)

    if not returns:
        return 0.0
    wins = sum(1 for r in returns if r > 0)
    return wins / len(returns) * 100.0


def run_search(df: pd.DataFrame, cost_bps: float = 1.0) -> list[Result]:
    fasts = [8, 10, 12, 15, 20, 25, 30]
    slows = [35, 50, 75, 100, 150, 200]
    spans = list(set(fasts + slows + [200]))
    data = make_emas(df, spans=spans)
    idx_et = data.index.tz_convert("America/New_York") if data.index.tz is not None else data.index
    is_rth = pd.Series(
        (idx_et.hour > 9) | ((idx_et.hour == 9) & (idx_et.minute >= 30)),
        index=data.index,
    ) & pd.Series(
        (idx_et.hour < 16) | ((idx_et.hour == 16) & (idx_et.minute == 0)),
        index=data.index,
    )
    out: list[Result] = []

    for fast, slow in product(fasts, slows):
        if fast >= slow:
            continue
        ef = data[f"ema_{fast}"]
        es = data[f"ema_{slow}"]
        prev_ef = ef.shift(1)
        prev_es = es.shift(1)
        gc = (prev_ef <= prev_es) & (ef > es)
        dc = (prev_ef >= prev_es) & (ef < es)

        # Mode 1: long-only
        p = np.zeros(len(data), dtype=float)
        in_pos = False
        for i in range(1, len(data)):
            if not in_pos and bool(gc.iloc[i]):
                in_pos = True
            elif in_pos and bool(dc.iloc[i]):
                in_pos = False
            p[i] = 1.0 if in_pos else 0.0
        p1 = pd.Series(p, index=data.index)
        tr, mdd, sh, t = score(data, p1, cost_bps=cost_bps)
        out.append(Result("long_only", fast, slow, tr, mdd, sh, t, trade_win_rate(data, p1)))

        # Mode 2: long-only with 200 EMA trend filter
        p = np.zeros(len(data), dtype=float)
        in_pos = False
        trend = data["Close"] > data["ema_200"]
        for i in range(1, len(data)):
            if not in_pos and bool(gc.iloc[i]) and bool(trend.iloc[i]):
                in_pos = True
            elif in_pos and (bool(dc.iloc[i]) or not bool(trend.iloc[i])):
                in_pos = False
            p[i] = 1.0 if in_pos else 0.0
        p2 = pd.Series(p, index=data.index)
        tr, mdd, sh, t = score(data, p2, cost_bps=cost_bps)
        out.append(Result("long_trend200", fast, slow, tr, mdd, sh, t, trade_win_rate(data, p2)))

        # Mode 3: long/short
        p = np.zeros(len(data), dtype=float)
        side = 0.0
        for i in range(1, len(data)):
            if bool(gc.iloc[i]):
                side = 1.0
            elif bool(dc.iloc[i]):
                side = -1.0
            p[i] = side
        p3 = pd.Series(p, index=data.index)
        tr, mdd, sh, t = score(data, p3, cost_bps=cost_bps)
        out.append(Result("long_short", fast, slow, tr, mdd, sh, t, trade_win_rate(data, p3)))

        # Mode 4: long/short, but only during regular US cash hours.
        p = np.zeros(len(data), dtype=float)
        side = 0.0
        for i in range(1, len(data)):
            if not bool(is_rth.iloc[i]):
                side = 0.0
            else:
                if bool(gc.iloc[i]):
                    side = 1.0
                elif bool(dc.iloc[i]):
                    side = -1.0
            p[i] = side
        p4 = pd.Series(p, index=data.index)
        tr, mdd, sh, t = score(data, p4, cost_bps=cost_bps)
        out.append(Result("long_short_rth", fast, slow, tr, mdd, sh, t, trade_win_rate(data, p4)))
    return out


def main() -> None:
    df = download()
    results = run_search(df, cost_bps=1.0)
    table = pd.DataFrame([r.__dict__ for r in results]).sort_values(
        by=["total_return_pct", "sharpe_like"], ascending=[False, False]
    )
    print("\nTop 12 strategies (30d, ES=F, 5m, 1bps cost/event):")
    print(table.head(12).to_string(index=False))

    robust = table[(table["max_drawdown_pct"] > -4.0) & (table["total_return_pct"] > 0.0)]
    if not robust.empty:
        print("\nBest lower-drawdown candidates:")
        print(robust.head(8).to_string(index=False))
    else:
        print("\nNo candidate met the lower-drawdown filter on this 30-day sample.")


if __name__ == "__main__":
    main()
