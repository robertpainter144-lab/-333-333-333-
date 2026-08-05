"""
High-volatility breakout strategy for the AGGRESSION sleeve.
Designed to capture liquidation cascades and volatility expansions.
Dynamically scaled by regime; allowed to go to zero.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    """Average True Range."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(n).mean()


class BreakoutStrategy:
    """
    Breakout: long when close > high of lookback, short when close < low.
    Position size scaled by ATR multiple (wider stops in vol expansion).
    """

    def __init__(
        self,
        lookback: int = 20,
        atr_mult: float = 2.0,
    ):
        self.lookback = lookback
        self.atr_mult = atr_mult

    def signal(self, close: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
        """
        +1 on upside breakout (close > roll_max(high)), -1 on downside (close < roll_min(low)).
        Else 0 (no position).
        """
        roll_high = high.rolling(self.lookback).max()
        roll_low = low.rolling(self.lookback).min()
        long_break = close > roll_high.shift(1)
        short_break = close < roll_low.shift(1)
        raw = np.where(long_break, 1.0, np.where(short_break, -1.0, 0.0))
        return pd.Series(raw, index=close.index)

    def volatility(self, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
        """ATR as vol proxy for sizing."""
        return _atr(high, low, close, self.lookback)
