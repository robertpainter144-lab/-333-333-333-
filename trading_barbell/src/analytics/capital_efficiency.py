"""
Capital efficiency tracking: turnover, leverage, risk-adjusted returns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class CapitalEfficiencyMetrics:
    """Summary of capital efficiency over a period."""
    annual_return: float = 0.0
    annual_vol: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    calmar: float = 0.0
    avg_turnover: float = 0.0
    avg_leverage: float = 0.0
    win_rate: float = 0.0
    bars: int = 0


class CapitalEfficiencyTracker:
    """
    Tracks returns, position changes (turnover), and leverage over time.
    Computes risk-adjusted metrics and capital efficiency stats.
    """

    def __init__(self):
        self.returns: list = []
        self.turnover: list = []
        self.leverage: list = []

    def update(
        self,
        period_return: float,
        position: float,
        prev_position: float,
    ) -> None:
        self.returns.append(period_return)
        self.turnover.append(abs(position - prev_position))
        self.leverage.append(abs(position))

    def metrics(self, periods_per_year: float = 252) -> CapitalEfficiencyMetrics:
        """Compute summary metrics from collected series."""
        r = pd.Series(self.returns)
        t = pd.Series(self.turnover)
        lev = pd.Series(self.leverage)
        if r.empty:
            return CapitalEfficiencyMetrics(bars=0)
        n = len(r)
        ann_ret = (1 + r).prod() ** (periods_per_year / n) - 1 if n else 0.0
        ann_vol = r.std() * np.sqrt(periods_per_year) if r.std() > 0 else 0.0
        sharpe = ann_ret / ann_vol if ann_vol else 0.0
        eq = (1 + r).cumprod()
        dd = (eq - eq.cummax()) / eq.cummax()
        max_dd = abs(dd.min()) if not dd.empty else 0.0
        calmar = ann_ret / max_dd if max_dd else 0.0
        avg_turnover = t.mean() if not t.empty else 0.0
        avg_lev = lev.mean() if not lev.empty else 0.0
        win_rate = (r > 0).mean() if not r.empty else 0.0
        return CapitalEfficiencyMetrics(
            annual_return=ann_ret,
            annual_vol=ann_vol,
            sharpe=sharpe,
            max_drawdown=max_dd,
            calmar=calmar,
            avg_turnover=avg_turnover,
            avg_leverage=avg_lev,
            win_rate=win_rate,
            bars=n,
        )
