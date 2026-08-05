"""
Volatility-adjusted position sizing for the CORE.
Target volatility scaling with hard leverage cap (e.g. 1-2x).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional


def volatility_adjusted_size(
    signal: pd.Series,
    volatility: pd.Series,
    target_vol: float = 0.10,
    max_leverage: float = 2.0,
    min_leverage: float = 0.0,
) -> pd.Series:
    """
    Size position so that portfolio vol ≈ target_vol.
    position = (target_vol / vol) * sign(signal), capped by max_leverage.
    """
    # avoid div by zero; vol already has floor in trend_following
    vol = volatility.replace(0, np.nan)
    raw_size = (target_vol / vol) * np.sign(signal)
    # cap leverage
    size = np.clip(np.abs(raw_size), min_leverage, max_leverage) * np.sign(raw_size)
    return pd.Series(size, index=signal.index).fillna(0)
