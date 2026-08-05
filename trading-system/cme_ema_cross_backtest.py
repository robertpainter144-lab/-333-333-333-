#!/usr/bin/env python3
"""
Backtest a 20/50 EMA golden/death cross strategy on CME futures
using 5-minute bars over the past month.

Default behavior:
- Symbol: ES=F (E-mini S&P 500 futures)
- Timeframe: 5 minutes
- Window: last 30 days
- Strategy: long-only (enter on golden cross, exit on death cross)

Dependencies:
  pip install yfinance pandas numpy
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass
class BacktestResult:
    symbol: str
    bars: int
    trades: int
    win_rate: float
    total_return_pct: float
    buy_hold_return_pct: float
    max_drawdown_pct: float
    avg_trade_return_pct: float
    final_equity: float


def download_data(symbol: str, interval: str, lookback_days: int) -> pd.DataFrame:
    """
    Download historical bars from Yahoo Finance.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    df = yf.download(
        symbol,
        start=start,
        end=end,
        interval=interval,
        auto_adjust=False,
        progress=False,
    )

    if df.empty:
        raise ValueError(
            f"No data returned for {symbol}. "
            "Try another symbol or adjust interval/lookback."
        )

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    required = {"Open", "High", "Low", "Close"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Missing expected columns: {sorted(missing)}")

    return df.dropna(subset=["Close"]).copy()


def compute_signals(df: pd.DataFrame, fast_ema: int, slow_ema: int) -> pd.DataFrame:
    out = df.copy()
    out["ema_fast"] = out["Close"].ewm(span=fast_ema, adjust=False).mean()
    out["ema_slow"] = out["Close"].ewm(span=slow_ema, adjust=False).mean()

    prev_fast = out["ema_fast"].shift(1)
    prev_slow = out["ema_slow"].shift(1)
    now_fast = out["ema_fast"]
    now_slow = out["ema_slow"]

    # Golden cross: fast EMA crosses above slow EMA.
    out["golden_cross"] = (prev_fast <= prev_slow) & (now_fast > now_slow)
    # Death cross: fast EMA crosses below slow EMA.
    out["death_cross"] = (prev_fast >= prev_slow) & (now_fast < now_slow)
    return out


def run_backtest(
    df: pd.DataFrame,
    symbol: str,
    fast_ema: int = 20,
    slow_ema: int = 50,
    initial_equity: float = 10_000.0,
) -> tuple[BacktestResult, pd.DataFrame]:
    if fast_ema >= slow_ema:
        raise ValueError("fast_ema must be less than slow_ema for this strategy.")

    data = compute_signals(df, fast_ema=fast_ema, slow_ema=slow_ema)
    data["ret"] = data["Close"].pct_change().fillna(0.0)

    position = np.zeros(len(data), dtype=float)
    in_pos = False

    for i in range(1, len(data)):
        if not in_pos and bool(data["golden_cross"].iloc[i]):
            in_pos = True
        elif in_pos and bool(data["death_cross"].iloc[i]):
            in_pos = False
        position[i] = 1.0 if in_pos else 0.0

    data["position"] = pd.Series(position, index=data.index)
    data["strategy_ret"] = data["position"].shift(1).fillna(0.0) * data["ret"]

    data["equity_curve"] = initial_equity * (1.0 + data["strategy_ret"]).cumprod()
    data["buy_hold_curve"] = initial_equity * (1.0 + data["ret"]).cumprod()

    rolling_peak = data["equity_curve"].cummax()
    drawdown = (data["equity_curve"] / rolling_peak) - 1.0
    max_drawdown_pct = float(drawdown.min() * 100.0)

    # Build trade list: returns from golden cross entry to death cross exit.
    trade_returns = []
    entry_price: Optional[float] = None
    for i in range(len(data)):
        if entry_price is None and bool(data["golden_cross"].iloc[i]):
            entry_price = float(data["Close"].iloc[i])
            continue
        if entry_price is not None and bool(data["death_cross"].iloc[i]):
            exit_price = float(data["Close"].iloc[i])
            trade_returns.append((exit_price / entry_price) - 1.0)
            entry_price = None

    trades = len(trade_returns)
    wins = int(sum(1 for r in trade_returns if r > 0))
    win_rate = (wins / trades * 100.0) if trades else 0.0
    avg_trade_return_pct = (
        float(np.mean(trade_returns) * 100.0) if trade_returns else 0.0
    )

    total_return_pct = float((data["equity_curve"].iloc[-1] / initial_equity - 1.0) * 100.0)
    buy_hold_return_pct = float((data["buy_hold_curve"].iloc[-1] / initial_equity - 1.0) * 100.0)

    result = BacktestResult(
        symbol=symbol,
        bars=len(data),
        trades=trades,
        win_rate=win_rate,
        total_return_pct=total_return_pct,
        buy_hold_return_pct=buy_hold_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        avg_trade_return_pct=avg_trade_return_pct,
        final_equity=float(data["equity_curve"].iloc[-1]),
    )
    return result, data


def print_report(result: BacktestResult, fast_ema: int, slow_ema: int, interval: str, lookback_days: int) -> None:
    print("\n=== CME Futures EMA Cross Backtest ===")
    print(f"Symbol:              {result.symbol}")
    print(f"Interval:            {interval}")
    print(f"Lookback:            {lookback_days} days")
    print(f"EMA periods:         fast={fast_ema}, slow={slow_ema}")
    print(f"Bars loaded:         {result.bars}")
    print(f"Trades:              {result.trades}")
    print(f"Win rate:            {result.win_rate:.2f}%")
    print(f"Avg trade return:    {result.avg_trade_return_pct:.3f}%")
    print(f"Strategy return:     {result.total_return_pct:.3f}%")
    print(f"Buy & hold return:   {result.buy_hold_return_pct:.3f}%")
    print(f"Max drawdown:        {result.max_drawdown_pct:.3f}%")
    print(f"Final equity:        ${result.final_equity:,.2f}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backtest 20/50 EMA golden/death cross on CME futures."
    )
    parser.add_argument("--symbol", default="ES=F", help="Yahoo symbol for futures (default: ES=F)")
    parser.add_argument("--interval", default="5m", help="Bar interval (default: 5m)")
    parser.add_argument("--lookback-days", type=int, default=30, help="Lookback window in days (default: 30)")
    parser.add_argument("--fast-ema", type=int, default=20, help="Fast EMA period (default: 20)")
    parser.add_argument("--slow-ema", type=int, default=50, help="Slow EMA period (default: 50)")
    parser.add_argument(
        "--csv-out",
        default="",
        help="Optional CSV output path for bar-by-bar results.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data = download_data(
        symbol=args.symbol,
        interval=args.interval,
        lookback_days=args.lookback_days,
    )
    result, with_signals = run_backtest(
        data,
        symbol=args.symbol,
        fast_ema=args.fast_ema,
        slow_ema=args.slow_ema,
    )
    print_report(
        result,
        fast_ema=args.fast_ema,
        slow_ema=args.slow_ema,
        interval=args.interval,
        lookback_days=args.lookback_days,
    )

    if args.csv_out:
        with_signals.to_csv(args.csv_out)
        print(f"\nSaved detailed output: {args.csv_out}")


if __name__ == "__main__":
    main()
