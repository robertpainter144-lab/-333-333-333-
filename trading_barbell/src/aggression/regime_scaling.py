"""
Applies regime-based scaling to aggression sleeve: can go to zero in low vol.
"""

from __future__ import annotations

import pandas as pd
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..shared.regime import VolatilityRegime


class RegimeScaledAggression:
    """
    Takes raw aggression position/signal and multiplies by regime scale
    (e.g. 0 in low vol, 0.5 normal, 1.0 expansion).
    """

    def __init__(
        self,
        regime_classifier: "VolatilityRegime",
        low_scale: float = 0.0,
        normal_scale: float = 0.5,
        expansion_scale: float = 1.0,
    ):
        self.regime_classifier = regime_classifier
        self.low_scale = low_scale
        self.normal_scale = normal_scale
        self.expansion_scale = expansion_scale

    def scale(
        self,
        aggression_signal: pd.Series,
        volatility: pd.Series,
    ) -> pd.Series:
        """Return aggression_signal * regime_scale(volatility)."""
        regime = self.regime_classifier.classify(volatility)
        scale = self.regime_classifier.scale_factor(
            regime,
            low_scale=self.low_scale,
            normal_scale=self.normal_scale,
            expansion_scale=self.expansion_scale,
        )
        return aggression_signal * scale
