"""
Per-arm stop overrides for the three-arm $60 experiment (2026-06-17).

Arms are routed by symbol; if a position is not in OVERRIDES it gets the
stop_manager defaults (pre-experiment SNAP behaves this way).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArmOverride:
    label: str
    trail_mult: float        # multiplier applied to adaptive trail %
    hard_cap_pct: float      # max hard-stop drop (overrides stop_manager.HARD_DROP_PCT_CAP)
    allow_profit_step: bool  # watcher honours this


OVERRIDES: dict[str, ArmOverride] = {
    "F":    ArmOverride("armA_balanced_50_50", 1.00, 0.10, True),
    "AAL":  ArmOverride("armB_aggro_75_25",    1.25, 0.12, False),
    "PINS": ArmOverride("armC_qc_substitute",  1.00, 0.08, True),
}


def get(symbol: str) -> ArmOverride | None:
    return OVERRIDES.get(symbol)
