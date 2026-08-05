"""
Capital compounding engine: equity curve from returns with optional barbell split.
Survival-first: tracks core vs aggression PnL separately.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional, Tuple


class CompoundingEngine:
    """
    Builds equity curve from period returns. Supports:
    - Single series (core-only) or core + aggression contributions
    - Start capital; compounding is multiplicative
    """

    def __init__(self, initial_capital: float = 1.0):
        self.initial_capital = initial_capital

    def equity_from_returns(
        self,
        returns: pd.Series,
        start_capital: Optional[float] = None,
    ) -> pd.Series:
        """Single series: equity = start_capital * cumprod(1 + returns)."""
        cap = start_capital if start_capital is not None else self.initial_capital
        r = returns.fillna(0)
        return (cap * (1 + r).cumprod()).reindex(returns.index).ffill().fillna(cap)

    def equity_from_components(
        self,
        core_returns: pd.Series,
        aggression_returns: pd.Series,
        core_weight: float,
        aggression_weight: float,
        start_capital: Optional[float] = None,
    ) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Combined equity from core and aggression sleeves.
        Total return = core_weight * core_ret + aggression_weight * aggression_ret
        Returns: (total_equity, core_equity, aggression_equity)
        """
        cap = start_capital if start_capital is not None else self.initial_capital
        cr = core_returns.reindex(core_returns.index.union(aggression_returns.index)).fillna(0)
        ar = aggression_returns.reindex(cr.index).fillna(0)
        total_ret = core_weight * cr + aggression_weight * ar
        total_equity = self.equity_from_returns(total_ret, cap)
        core_equity = self.equity_from_returns(cr, cap * core_weight)
        aggression_equity = self.equity_from_returns(ar, cap * aggression_weight)
        return total_equity, core_equity, aggression_equity
