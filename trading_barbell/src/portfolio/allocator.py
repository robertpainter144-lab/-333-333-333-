"""
Barbell capital allocator: core (80–95%) vs aggression (5–20%).
Aggression can go to zero without affecting core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class BarbellWeights:
    core: float
    aggression: float

    def validate(self) -> None:
        assert 0 <= self.core <= 1 and 0 <= self.aggression <= 1
        assert abs(self.core + self.aggression - 1.0) < 1e-6


class BarbellAllocator:
    """
    Manages core_pct and aggression_pct. Optionally can reduce aggression to 0
    when regime is unfavorable (caller applies regime logic).
    """

    def __init__(
        self,
        core_pct_min: float = 0.80,
        core_pct_max: float = 0.95,
        aggression_pct_min: float = 0.05,
        aggression_pct_max: float = 0.20,
        aggression_can_zero: bool = True,
    ):
        self.core_pct_min = core_pct_min
        self.core_pct_max = core_pct_max
        self.aggression_pct_min = aggression_pct_min
        self.aggression_pct_max = aggression_pct_max
        self.aggression_can_zero = aggression_can_zero

    def get_weights(
        self,
        use_aggression: bool = True,
    ) -> BarbellWeights:
        """
        Returns (core, aggression) weights summing to 1.
        If use_aggression is False (e.g. regime says no), aggression=0, core=1.
        """
        if not use_aggression and self.aggression_can_zero:
            return BarbellWeights(core=1.0, aggression=0.0)
        core = (self.core_pct_min + self.core_pct_max) / 2.0
        aggression = 1.0 - core
        aggression = max(self.aggression_pct_min, min(self.aggression_pct_max, aggression))
        core = 1.0 - aggression
        w = BarbellWeights(core=core, aggression=aggression)
        w.validate()
        return w
