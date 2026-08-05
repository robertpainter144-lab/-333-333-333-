"""
Volatility regime classification: low, normal, expansion.
Used to scale aggression sleeve and optionally core.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Literal

RegimeLabel = Literal["low", "normal", "expansion"]


class VolatilityRegime:
    """
    Classifies each bar into low / normal / expansion based on
    rolling volatility percentiles (e.g. 25th / 75th).
    """

    def __init__(
        self,
        lookback: int = 20,
        low_percentile: float = 0.25,
        high_percentile: float = 0.75,
    ):
        self.lookback = lookback
        self.low_percentile = low_percentile
        self.high_percentile = high_percentile

    def fit(self, volatility: pd.Series) -> None:
        """No fit needed; percentiles are computed rolling."""
        pass

    def classify(self, volatility: pd.Series) -> pd.Series:
        """
        Returns categorical: 'low' | 'normal' | 'expansion'.
        Based on rolling percentiles of volatility.
        """
        low = volatility.rolling(self.lookback).quantile(self.low_percentile)
        high = volatility.rolling(self.lookback).quantile(self.high_percentile)
        vol = volatility
        regime = np.where(vol <= low, "low", np.where(vol >= high, "expansion", "normal"))
        return pd.Series(regime, index=volatility.index)

    def scale_factor(
        self,
        regime_series: pd.Series,
        low_scale: float = 0.0,
        normal_scale: float = 0.5,
        expansion_scale: float = 1.0,
    ) -> pd.Series:
        """Map regime to a multiplier for aggression sleeve."""
        scale = regime_series.map({
            "low": low_scale,
            "normal": normal_scale,
            "expansion": expansion_scale,
        })
        return scale.fillna(normal_scale)
