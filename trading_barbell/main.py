#!/usr/bin/env python3
"""
Barbell capital compounding system — entrypoint.
Run backtest with config/default.yaml or generate synthetic data for demo.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

from backtest.engine import BarbellBacktest, BacktestConfig, BacktestResult, config_from_dict, load_config_yaml


def generate_synthetic_ohlcv(n_bars: int = 504, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV for demo (e.g. 2 years daily)."""
    np.random.seed(seed)
    log_ret = np.random.standard_t(df=5, size=n_bars) * 0.01
    close = 100 * np.exp(np.cumsum(log_ret))
    high = close * (1 + np.abs(np.random.randn(n_bars) * 0.005))
    low = close * (1 - np.abs(np.random.randn(n_bars) * 0.005))
    open_ = np.roll(close, 1)
    open_[0] = 100
    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }, index=pd.date_range("2022-01-01", periods=n_bars, freq="B"))


def run_backtest(
    config_path: str | None = None,
    data_path: str | None = None,
    synthetic_bars: int = 504,
) -> BacktestResult:
    if config_path and Path(config_path).exists():
        raw = load_config_yaml(config_path)
        config = config_from_dict(raw)
    else:
        config = BacktestConfig()
    if data_path and Path(data_path).exists():
        df = pd.read_csv(data_path, index_col=0, parse_dates=True)
        close = df["close"]
        high = df["high"] if "high" in df.columns else df["close"]
        low = df["low"] if "low" in df.columns else df["close"]
    else:
        df = generate_synthetic_ohlcv(n_bars=synthetic_bars)
        close = df["close"]
        high = df["high"]
        low = df["low"]
    funding = df["funding_rate"] if "funding_rate" in df.columns else None
    engine = BarbellBacktest(config)
    return engine.run(close=close, high=high, low=low, funding_rate=funding)


def print_metrics(result: BacktestResult, data_label: str = "") -> None:
    m = result.metrics
    header = "--- Barbell Backtest Results ---"
    if data_label:
        header += f" ({data_label})"
    print(header)
    print(f"  Bars: {m.bars}")
    bnh = getattr(result, "bnh_annual_return", None)
    if bnh is not None:
        print(f"  Buy-and-hold (underlying): {bnh:.2%} ann.")
    print(f"  Strategy annual return: {m.annual_return:.2%}")
    if bnh is not None:
        excess = m.annual_return - bnh
        print(f"  Excess vs B&H:            {excess:+.2%}")
    print(f"  Annual vol:    {m.annual_vol:.2%}")
    print(f"  Sharpe:        {m.sharpe:.2f}")
    print(f"  Max drawdown:  {m.max_drawdown:.2%}")
    print(f"  Calmar:        {m.calmar:.2f}")
    print(f"  Win rate:      {m.win_rate:.1%}")
    print(f"  Avg turnover:  {m.avg_turnover:.4f}")
    print(f"  Avg leverage:  {m.avg_leverage:.4f}")
    print("--------------------------------")


def main() -> None:
    parser = argparse.ArgumentParser(description="Barbell capital compounding backtest")
    parser.add_argument("--config", "-c", default="config/default.yaml", help="Config YAML path")
    parser.add_argument("--data", "-d", help="OHLCV CSV path (optional)")
    parser.add_argument("--synthetic", type=int, default=504, help="Bars of synthetic data if no --data")
    parser.add_argument("--out", "-o", help="Write equity curve to CSV")
    args = parser.parse_args()
    result = run_backtest(
        config_path=args.config,
        data_path=args.data,
        synthetic_bars=args.synthetic,
    )
    data_label = f"data={args.data}" if args.data else f"synthetic ({args.synthetic} bars)"
    print_metrics(result, data_label)
    if args.out:
        result.equity.to_csv(args.out)
        print(f"Equity curve written to {args.out}")


if __name__ == "__main__":
    main()
