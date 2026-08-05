"""
Strict drawdown control for the CORE sleeve.
Reduces or pauses size when drawdown exceeds thresholds; recovery required for full size.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


class DrawdownController:
    """
    Tracks running equity / peak and applies a scale factor to position size
    when in drawdown. Survival-first: cut size in DD, require recovery before full size.
    """

    def __init__(
        self,
        max_drawdown_pct: float = 0.15,
        dd_reduction_factor: float = 0.5,
        recovery_required: float = 0.02,
    ):
        self.max_drawdown_pct = max_drawdown_pct
        self.dd_reduction_factor = dd_reduction_factor
        self.recovery_required = recovery_required

    def scale_factor(
        self,
        equity_curve: pd.Series,
        current_idx: int,
    ) -> float:
        """
        Returns multiplier in (0, 1] for position size.
        - At peak or after recovery: 1.0
        - In drawdown beyond threshold: dd_reduction_factor
        - After DD, need recovery_required (e.g. 2%) before returning to 1.0
        """
        if current_idx < 1 or equity_curve.iloc[: current_idx + 1].empty:
            return 1.0
        segment = equity_curve.iloc[: current_idx + 1]
        peak = segment.expanding().max()
        dd = (segment - peak) / peak.replace(0, np.nan)
        dd_pct = dd.iloc[-1] if not pd.isna(dd.iloc[-1]) else 0.0

        if dd_pct <= -self.max_drawdown_pct:
            return self.dd_reduction_factor
        # recovery: from the last peak, need +recovery_required to go back to 1.0
        last_peak = peak.iloc[-1]
        recovery_level = last_peak * (1 + self.recovery_required)
        if segment.iloc[-1] >= recovery_level:
            return 1.0
        # still in "recovery zone" — keep reduced size
        if dd_pct < 0:
            return self.dd_reduction_factor
        return 1.0

    def scale_series(
        self,
        equity_curve: pd.Series,
    ) -> pd.Series:
        """Vectorized: scale factor at each bar (for backtest)."""
        out = np.ones(len(equity_curve))
        peak = np.nan
        in_recovery = False
        recovery_level = np.nan
        for i in range(len(equity_curve)):
            eq = equity_curve.iloc[i]
            if np.isnan(eq):
                out[i] = 1.0
                continue
            if np.isnan(peak) or eq > peak:
                peak = eq
                recovery_level = peak * (1 + self.recovery_required)
                in_recovery = False
            dd_pct = (eq - peak) / peak if peak and peak != 0 else 0.0
            if dd_pct <= -self.max_drawdown_pct:
                out[i] = self.dd_reduction_factor
                in_recovery = True
            elif in_recovery:
                if eq >= recovery_level:
                    out[i] = 1.0
                    in_recovery = False
                else:
                    out[i] = self.dd_reduction_factor
            else:
                out[i] = 1.0
        return pd.Series(out, index=equity_curve.index)
