"""
Funding-rate awareness for perps: trim long exposure when funding is extremely negative.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


class FundingAwareness:
    """
    Adjusts position size for funding. E.g. when funding is very negative,
    reduce long exposure to avoid paying; optional short bias scaling.
    """

    def __init__(
        self,
        lookback: int = 8,
        extreme_negative_threshold: float = -0.01,
        trim_long_exposure_pct: float = 0.5,
    ):
        self.lookback = lookback
        self.extreme_negative_threshold = extreme_negative_threshold
        self.trim_long_exposure_pct = trim_long_exposure_pct

    def adjustment(self, funding_rate: pd.Series, position: pd.Series) -> pd.Series:
        """
        Returns multiplier for position (1.0 = no change).
        When funding is extremely negative and position > 0, trim to trim_long_exposure_pct.
        """
        # Use rolling mean funding to smooth
        fund = funding_rate.rolling(self.lookback, min_periods=1).mean()
        is_long = position > 0
        extreme_neg = fund <= self.extreme_negative_threshold
        mult = np.ones_like(position, dtype=float)
        mult = np.where(is_long & extreme_neg, self.trim_long_exposure_pct, mult)
        return pd.Series(mult, index=position.index)

    def apply(
        self,
        position: pd.Series,
        funding_rate: pd.Series,
    ) -> pd.Series:
        """Return position * adjustment (position in [-1,1] or raw size)."""
        adj = self.adjustment(funding_rate, position)
        return position * adj
