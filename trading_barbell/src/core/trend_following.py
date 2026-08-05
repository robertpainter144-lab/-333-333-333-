"""
Volatility-adjusted trend-following strategy for the CORE sleeve.
Long-term compounding, low leverage (1-2x max), survival-first.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional, Tuple


class TrendFollowing:
    """
    Dual moving-average trend following with volatility-adjusted signals.
    Outputs continuous signal in [-1, 1]; position sizing is applied elsewhere.
    """

    def __init__(
        self,
        fast_period: int = 20,
        slow_period: int = 50,
        vol_lookback: int = 20,
        vol_floor: float = 0.005,
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.vol_lookback = vol_lookback
        self.vol_floor = vol_floor

    def fit(self, close: pd.Series, high: pd.Series, low: pd.Series) -> None:
        """Precompute indicators if needed (no-op for this rule-based strategy)."""
        pass

    def signal(self, close: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
        """
        Raw trend signal: +1 long, -1 short, 0 flat.
        Uses EMA crossover; volatility is used in position sizing, not here.
        """
        fast = close.ewm(span=self.fast_period, adjust=False).mean()
        slow = close.ewm(span=self.slow_period, adjust=False).mean()
        raw = np.where(fast > slow, 1.0, np.where(fast < slow, -1.0, 0.0))
        return pd.Series(raw, index=close.index)

    def volatility(self, close: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
        """Annualized volatility estimate (close returns, rolled)."""
        ret = close.pct_change()
        vol = ret.rolling(self.vol_lookback).std() * np.sqrt(252)
        return vol.clip(lower=self.vol_floor)
