"""
Walk-forward optimization: train on rolling window, test on out-of-sample.
Re-optimize every step_bars (e.g. quarterly).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Callable, Dict, Any, List, Optional, Tuple

# Objective: e.g. Sharpe, or risk-adjusted return, or smooth equity
ObjectiveFn = Callable[[pd.Series], float]


def _sharpe(returns: pd.Series, annualize: bool = True) -> float:
    if returns.empty or returns.std() == 0:
        return 0.0
    sr = returns.mean() / returns.std()
    if annualize:
        sr = sr * np.sqrt(252)
    return float(sr)


def _sortino(returns: pd.Series, annualize: bool = True) -> float:
    downside = returns[returns < 0]
    if downside.empty or downside.std() == 0:
        return 0.0
    sortino = returns.mean() / downside.std()
    if annualize:
        sortino = sortino * np.sqrt(252)
    return float(sortino)


class WalkForwardOptimizer:
    """
    Splits timeline into train/test windows. On each fold:
    - Train: optimize parameters (e.g. EMA periods, vol target) on train window
    - Test: run with best params on test window (no peeking)
    """

    def __init__(
        self,
        train_bars: int = 252,
        test_bars: int = 63,
        step_bars: int = 63,
        objective: str = "sharpe",  # "sharpe" | "sortino" | "calmar"
    ):
        self.train_bars = train_bars
        self.test_bars = test_bars
        self.step_bars = step_bars
        self.objective = objective

    def _objective_fn(self) -> ObjectiveFn:
        if self.objective == "sharpe":
            return _sharpe
        if self.objective == "sortino":
            return _sortino
        if self.objective == "calmar":
            def calmar(returns: pd.Series) -> float:
                if returns.empty:
                    return 0.0
                eq = (1 + returns).cumprod()
                dd = (eq - eq.cummax()) / eq.cummax()
                max_dd = abs(dd.min())
                if max_dd == 0:
                    return 0.0
                return float(returns.mean() * 252 / max_dd)
            return calmar
        return _sharpe

    def folds(self, length: int) -> List[Tuple[int, int, int, int]]:
        """
        Yields (train_start, train_end, test_start, test_end) indices.
        """
        folds = []
        start = 0
        while start + self.train_bars + self.test_bars <= length:
            train_end = start + self.train_bars
            test_start = train_end
            test_end = min(test_start + self.test_bars, length)
            folds.append((start, train_end, test_start, test_end))
            start += self.step_bars
        return folds

    def run(
        self,
        price_data: pd.DataFrame,
        param_grid: Dict[str, List[Any]],
        run_backtest_fn: Callable[[pd.DataFrame, Dict[str, Any]], pd.Series],
    ) -> Tuple[pd.Series, List[Dict[str, Any]]]:
        """
        run_backtest_fn(data_slice, params) -> returns series.
        Returns (concatenated OOS returns, list of best params per fold).
        """
        obj_fn = self._objective_fn()
        n = len(price_data)
        folds = self.folds(n)
        oos_returns: List[pd.Series] = []
        best_params_list: List[Dict[str, Any]] = []

        for train_start, train_end, test_start, test_end in folds:
            train_data = price_data.iloc[train_start:train_end]
            test_data = price_data.iloc[test_start:test_end]
            best_score = -np.inf
            best_params: Dict[str, Any] = {}
            # Grid search over param_grid
            keys = list(param_grid.keys())
            if not keys:
                run_returns = run_backtest_fn(train_data, {})
                best_params = {}
                best_score = obj_fn(run_returns)
            else:
                from itertools import product
                for combo in product(*(param_grid[k] for k in keys)):
                    params = dict(zip(keys, combo))
                    ret = run_backtest_fn(train_data, params)
                    score = obj_fn(ret)
                    if score > best_score:
                        best_score = score
                        best_params = params.copy()
            oos_ret = run_backtest_fn(test_data, best_params)
            oos_returns.append(oos_ret)
            best_params_list.append(best_params)
        combined = pd.concat(oos_returns, axis=0) if oos_returns else pd.Series(dtype=float)
        return combined, best_params_list
