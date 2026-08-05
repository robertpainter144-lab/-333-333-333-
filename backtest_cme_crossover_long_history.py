#!/usr/bin/env python3
"""
Long-history backtest: ES=F 30/75 EMA long/short crossover (same signals as cme_strategy_lab).

Yahoo Finance caps intraday history (5m ~60d, 1h ~730d). A 5-year run uses daily bars.
The 5m session-rule version cannot be replayed identically on daily OHLC; we report:
  - Daily 5y: long/short crossover, hold between signals (analogous to 24h 5m mode).
  - Optional 1h (max ~2y): long/short with and without RTH flatten (closer to crossover chart).
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf


DEFAULT_OUT = Path("/Users/quarki3/cme_crossover_5y_backtest.json")


@dataclass
class BacktestSummary:
    symbol: str
    interval: str
    fast: int
    slow: int
    mode: str
    start: str
    end: str
    n_bars: int
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ann: float
    position_changes: int
    win_rate_pct: float
    cost_bps_per_change: float
    note: str


def _download(symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
    df = yf.download(symbol, start=start, end=end, interval=interval, progress=False)
    if df.empty:
        raise RuntimeError(f"No data for {symbol} {interval} {start}..{end}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df[["Open", "High", "Low", "Close"]].dropna().copy()


def make_emas(df: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = out["Close"].ewm(span=fast, adjust=False).mean()
    out["ema_slow"] = out["Close"].ewm(span=slow, adjust=False).mean()
    out["ret"] = out["Close"].pct_change().fillna(0.0)
    return out


def position_long_short(gc: pd.Series, dc: pd.Series) -> np.ndarray:
    side = 0.0
    p = np.zeros(len(gc), dtype=float)
    for i in range(1, len(gc)):
        if bool(gc.iloc[i]):
            side = 1.0
        elif bool(dc.iloc[i]):
            side = -1.0
        p[i] = side
    return p


def position_long_short_rth(
    data: pd.DataFrame, gc: pd.Series, dc: pd.Series
) -> np.ndarray:
    idx_et = data.index.tz_convert("America/New_York") if data.index.tz is not None else data.index
    mask = (
        ((idx_et.hour > 9) | ((idx_et.hour == 9) & (idx_et.minute >= 30)))
        & ((idx_et.hour < 16) | ((idx_et.hour == 16) & (idx_et.minute == 0)))
    )
    is_rth = pd.Series(mask, index=data.index)
    side = 0.0
    p = np.zeros(len(data), dtype=float)
    for i in range(1, len(data)):
        if not bool(is_rth.iloc[i]):
            side = 0.0
        else:
            if bool(gc.iloc[i]):
                side = 1.0
            elif bool(dc.iloc[i]):
                side = -1.0
        p[i] = side
    return p


def score(
    data: pd.DataFrame,
    position: np.ndarray,
    cost_bps: float,
    bars_per_year: float,
) -> tuple[float, float, float, int]:
    pos = pd.Series(position, index=data.index).astype(float)
    changes = pos.diff().abs().fillna(0.0)
    cost = changes * (cost_bps / 10_000.0)
    strat_ret = pos.shift(1).fillna(0.0) * data["ret"] - cost

    equity = (1.0 + strat_ret).cumprod()
    total_return_pct = float((equity.iloc[-1] - 1.0) * 100.0)
    dd = equity / equity.cummax() - 1.0
    max_drawdown_pct = float(dd.min() * 100.0)

    vol = float(strat_ret.std())
    sharpe_ann = float((strat_ret.mean() / vol) * np.sqrt(bars_per_year)) if vol > 0 else 0.0
    n_changes = int((changes > 0).sum())
    return total_return_pct, max_drawdown_pct, sharpe_ann, n_changes


def trade_win_rate(data: pd.DataFrame, position: np.ndarray) -> float:
    pos = position.astype(float)
    closes = data["Close"].values
    returns: list[float] = []
    entry_idx: int | None = None
    entry_side = 0.0

    for i in range(1, len(pos)):
        prev_side = pos[i - 1]
        curr_side = pos[i]

        if entry_idx is None and prev_side == 0.0 and curr_side != 0.0:
            entry_idx = i
            entry_side = curr_side
            continue

        if entry_idx is not None:
            if curr_side == 0.0 or curr_side != entry_side:
                entry_px = closes[entry_idx]
                exit_px = closes[i]
                r = (exit_px / entry_px - 1.0) if entry_side > 0 else (entry_px / exit_px - 1.0)
                returns.append(r)

                if curr_side != 0.0 and curr_side != entry_side:
                    entry_idx = i
                    entry_side = curr_side
                else:
                    entry_idx = None
                    entry_side = 0.0

    if entry_idx is not None:
        entry_px = closes[entry_idx]
        exit_px = closes[-1]
        r = (exit_px / entry_px - 1.0) if entry_side > 0 else (entry_px / exit_px - 1.0)
        returns.append(r)

    if not returns:
        return 0.0
    return sum(1 for r in returns if r > 0) / len(returns) * 100.0


def run_one(
    df: pd.DataFrame,
    interval_label: str,
    fast: int,
    slow: int,
    mode: str,
    cost_bps: float,
    bars_per_year: float,
    note: str,
) -> BacktestSummary:
    data = make_emas(df, fast, slow)
    prev_f = data["ema_fast"].shift(1)
    prev_s = data["ema_slow"].shift(1)
    gc = (prev_f <= prev_s) & (data["ema_fast"] > data["ema_slow"])
    dc = (prev_f >= prev_s) & (data["ema_fast"] < data["ema_slow"])

    if mode == "long_short":
        p = position_long_short(gc, dc)
    elif mode == "long_short_rth":
        p = position_long_short_rth(data, gc, dc)
    else:
        raise ValueError(mode)

    tr, mdd, sh, nch = score(data, p, cost_bps, bars_per_year)
    wr = trade_win_rate(data, p)

    return BacktestSummary(
        symbol="ES=F",
        interval=interval_label,
        fast=fast,
        slow=slow,
        mode=mode,
        start=data.index.min().isoformat(),
        end=data.index.max().isoformat(),
        n_bars=len(data),
        total_return_pct=round(tr, 4),
        max_drawdown_pct=round(mdd, 4),
        sharpe_ann=round(sh, 4),
        position_changes=nch,
        win_rate_pct=round(wr, 2),
        cost_bps_per_change=cost_bps,
        note=note,
    )


def main() -> int:
    p = argparse.ArgumentParser(description="CME 30/75 crossover long-history backtest.")
    p.add_argument("--years", type=float, default=5.0, help="Years of daily history (default 5)")
    p.add_argument("--fast", type=int, default=30)
    p.add_argument("--slow", type=int, default=75)
    p.add_argument("--cost-bps", type=float, default=1.0, help="Cost per |Δposition| (same as lab)")
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()

    end = datetime.now(timezone.utc)
    start_d = end - timedelta(days=int(365 * args.years))

    results: list[BacktestSummary] = []

    # --- Daily: full requested horizon (Yahoo ~5y+ for ES=F daily) ---
    df_d = _download("ES=F", "1d", start_d, end)
    results.append(
        run_one(
            df_d,
            "1d",
            args.fast,
            args.slow,
            "long_short",
            args.cost_bps,
            bars_per_year=252.0,
            note="Daily bars: same crossover as 5m lab; hold between signals (overnight risk). "
            "Not identical to 5m session-flatten backtest.",
        )
    )

    # --- Hourly: Yahoo allows ~730d; RTH flatten meaningful here ---
    start_h = end - timedelta(days=729)
    try:
        df_h = _download("ES=F", "1h", start_h, end)
        results.append(
            run_one(
                df_h,
                "1h",
                args.fast,
                args.slow,
                "long_short",
                args.cost_bps,
                bars_per_year=252 * 23,
                note="~2y of 1h bars (Yahoo limit). No session flatten. Sharpe uses ~23h/day bars/year.",
            )
        )
        results.append(
            run_one(
                df_h,
                "1h",
                args.fast,
                args.slow,
                "long_short_rth",
                args.cost_bps,
                bars_per_year=252 * 23,
                note="~2y of 1h bars; flat outside 09:30–16:00 ET (approx cash-session filter).",
            )
        )
    except RuntimeError as e:
        results.append(
            BacktestSummary(
                symbol="ES=F",
                interval="1h",
                fast=args.fast,
                slow=args.slow,
                mode="skipped",
                start="",
                end="",
                n_bars=0,
                total_return_pct=0.0,
                max_drawdown_pct=0.0,
                sharpe_ann=0.0,
                position_changes=0,
                win_rate_pct=0.0,
                cost_bps_per_change=args.cost_bps,
                note=f"Hourly download failed: {e}",
            )
        )

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "yahoo_limits": "5m ~60d; 1h ~730d; daily multi-year — see note on each run.",
        "runs": [asdict(r) for r in results],
    }
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
