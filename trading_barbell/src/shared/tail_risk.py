"""
Tail risk / flash-crash filter: pause or reduce size after extreme moves.
Ensures strategy survives flash crashes.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


class TailRiskFilter:
    """
    Detects large single-bar moves (e.g. >5%) and:
    - Pauses new entries for N bars
    - Reduces position size for a period
    """

    def __init__(
        self,
        move_threshold_pct: float = 0.05,
        pause_bars: int = 3,
        reduce_size_pct: float = 0.25,
    ):
        self.move_threshold_pct = move_threshold_pct
        self.pause_bars = pause_bars
        self.reduce_size_pct = reduce_size_pct

    def detect(self, returns: pd.Series) -> pd.Series:
        """Boolean: True when |return| >= move_threshold_pct."""
        return np.abs(returns) >= self.move_threshold_pct

    def scale_after_event(self, returns: pd.Series) -> pd.Series:
        """
        For each bar, returns scale factor in [reduce_size_pct, 1.0].
        After a flash event, scale is reduce_size_pct for the next pause_bars, then back to 1.
        """
        event = self.detect(returns)
        scale = np.ones(len(returns), dtype=float)
        for i in range(len(returns)):
            if event.iloc[i]:
                for j in range(1, self.pause_bars + 1):
                    if i + j < len(scale):
                        scale[i + j] = min(scale[i + j], self.reduce_size_pct)
        return pd.Series(scale, index=returns.index)

    def apply_to_position(
        self,
        position: pd.Series,
        returns: pd.Series,
    ) -> pd.Series:
        """Multiply position by tail-risk scale (vectorized)."""
        scale = self.scale_after_event(returns)
        return position * scale
