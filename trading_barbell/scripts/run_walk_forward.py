#!/usr/bin/env python3
"""Example: walk-forward optimization over core trend params."""

import sys
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest.engine import BarbellBacktest, BacktestConfig
from src.optimization.walk_forward import WalkForwardOptimizer
from main import generate_synthetic_ohlcv


def run_backtest_for_wf(data: pd.DataFrame, params: dict) -> pd.Series:
    cfg = BacktestConfig(
        core_fast_ema=params.get("fast_ema", 20),
        core_slow_ema=params.get("slow_ema", 50),
        core_vol_target=params.get("vol_target", 0.10),
    )
    engine = BarbellBacktest(cfg)
    result = engine.run(
        close=data["close"],
        high=data["high"],
        low=data["low"],
    )
    return result.total_returns


def main():
    df = generate_synthetic_ohlcv(n_bars=504 * 2)  # ~4 years
    wf = WalkForwardOptimizer(train_bars=252, test_bars=63, step_bars=63, objective="sharpe")
    param_grid = {
        "fast_ema": [10, 20, 30],
        "slow_ema": [40, 50, 60],
        "vol_target": [0.08, 0.10, 0.12],
    }
    oos_returns, best_params_list = wf.run(df, param_grid, run_backtest_for_wf)
    print("Walk-forward OOS returns length:", len(oos_returns))
    print("Best params per fold (first 3):", best_params_list[:3])


if __name__ == "__main__":
    main()
