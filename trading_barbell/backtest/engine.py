"""
Backtest engine: runs core + aggression with barbell allocation, regime, funding, tail risk.
Produces equity curve and metrics; survival-first.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

# Assume we're run from project root or path is set
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.trend_following import TrendFollowing
from src.core.position_sizing import volatility_adjusted_size
from src.core.drawdown_control import DrawdownController
from src.shared.regime import VolatilityRegime
from src.shared.funding import FundingAwareness
from src.shared.tail_risk import TailRiskFilter
from src.aggression.breakout import BreakoutStrategy
from src.aggression.regime_scaling import RegimeScaledAggression
from src.portfolio.allocator import BarbellAllocator
from src.portfolio.compounding import CompoundingEngine
from src.analytics.capital_efficiency import CapitalEfficiencyTracker, CapitalEfficiencyMetrics


@dataclass
class BacktestConfig:
    # Core
    core_fast_ema: int = 20
    core_slow_ema: int = 50
    core_vol_lookback: int = 20
    core_vol_floor: float = 0.005
    core_vol_target: float = 0.10
    core_max_leverage: float = 2.0
    core_min_leverage: float = 0.0
    # Drawdown
    max_drawdown_pct: float = 0.15
    dd_reduction_factor: float = 0.5
    recovery_required: float = 0.02
    # Regime
    regime_lookback: int = 20
    aggression_low_scale: float = 0.0
    aggression_normal_scale: float = 0.5
    aggression_expansion_scale: float = 1.0
    # Aggression
    breakout_lookback: int = 20
    breakout_atr_mult: float = 2.0
    # Barbell
    core_pct: float = 0.90
    aggression_pct: float = 0.10
    use_aggression: bool = True
    # Funding (optional)
    funding_enabled: bool = False
    funding_lookback: int = 8
    extreme_funding_threshold: float = -0.01
    trim_long_pct: float = 0.5
    # Tail risk
    tail_risk_enabled: bool = True
    move_threshold_pct: float = 0.05
    pause_bars: int = 3
    reduce_size_pct: float = 0.25
    # Capital
    initial_capital: float = 1.0


@dataclass
class BacktestResult:
    equity: pd.Series
    core_equity: pd.Series
    aggression_equity: pd.Series
    core_returns: pd.Series
    aggression_returns: pd.Series
    total_returns: pd.Series
    core_position: pd.Series
    aggression_position: pd.Series
    regime: pd.Series
    metrics: CapitalEfficiencyMetrics = field(default_factory=CapitalEfficiencyMetrics)
    config: Optional[BacktestConfig] = None
    bnh_annual_return: float = 0.0  # buy-and-hold (underlying) annualized return


class BarbellBacktest:
    """
    Single-asset (or single basket) barbell backtest.
    OHLCV + optional funding rate. Runs core trend + aggression breakout,
    applies regime scaling, funding trim, tail-risk filter, drawdown control.
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()

    def run(
        self,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        funding_rate: Optional[pd.Series] = None,
    ) -> BacktestResult:
        cfg = self.config
        # 1) Core: trend signal + vol
        trend = TrendFollowing(
            fast_period=cfg.core_fast_ema,
            slow_period=cfg.core_slow_ema,
            vol_lookback=cfg.core_vol_lookback,
            vol_floor=cfg.core_vol_floor,
        )
        core_signal = trend.signal(close, high, low)
        vol = trend.volatility(close, high, low)
        core_size = volatility_adjusted_size(
            core_signal,
            vol,
            target_vol=cfg.core_vol_target,
            max_leverage=cfg.core_max_leverage,
            min_leverage=cfg.core_min_leverage,
        )
        # 2) Drawdown control on core (we need equity; do iterative or use prior bar equity proxy)
        dd_control = DrawdownController(
            max_drawdown_pct=cfg.max_drawdown_pct,
            dd_reduction_factor=cfg.dd_reduction_factor,
            recovery_required=cfg.recovery_required,
        )
        # We'll apply DD scale in the loop so we have running equity
        core_returns = core_size.shift(1) * close.pct_change()
        core_returns = core_returns.fillna(0)
        # Build core equity to get DD scale
        comp = CompoundingEngine(cfg.initial_capital)
        core_equity_naive = comp.equity_from_returns(core_returns, cfg.initial_capital * cfg.core_pct)
        dd_scale = dd_control.scale_series(core_equity_naive)
        core_position = (core_size * dd_scale).fillna(0)
        # 3) Tail risk
        if cfg.tail_risk_enabled:
            tail = TailRiskFilter(
                move_threshold_pct=cfg.move_threshold_pct,
                pause_bars=cfg.pause_bars,
                reduce_size_pct=cfg.reduce_size_pct,
            )
            core_position = tail.apply_to_position(core_position, close.pct_change())
        # 4) Funding
        if cfg.funding_enabled and funding_rate is not None:
            fund = FundingAwareness(
                lookback=cfg.funding_lookback,
                extreme_negative_threshold=cfg.extreme_funding_threshold,
                trim_long_exposure_pct=cfg.trim_long_pct,
            )
            core_position = fund.apply(core_position, funding_rate)
        # 5) Aggression
        breakout = BreakoutStrategy(lookback=cfg.breakout_lookback, atr_mult=cfg.breakout_atr_mult)
        agg_signal = breakout.signal(close, high, low)
        regime_clf = VolatilityRegime(lookback=cfg.regime_lookback)
        regime_series = regime_clf.classify(vol)
        scaled_agg = RegimeScaledAggression(
            regime_clf,
            low_scale=cfg.aggression_low_scale,
            normal_scale=cfg.aggression_normal_scale,
            expansion_scale=cfg.aggression_expansion_scale,
        )
        aggression_position = scaled_agg.scale(agg_signal, vol)
        if not cfg.use_aggression:
            aggression_position = aggression_position * 0.0
        # Sizing: simple ATR-based for aggression (scale by vol like core but allow higher risk)
        agg_vol = breakout.volatility(high, low, close)
        vol_floor = cfg.core_vol_floor
        agg_vol = agg_vol.clip(lower=vol_floor * close)
        # Normalize to similar scale as core (e.g. 0.5x target vol for aggression sleeve)
        aggression_position = (aggression_position * (cfg.core_vol_target * 0.5 / (agg_vol / close + 1e-8))).clip(-2, 2)
        aggression_returns = aggression_position.shift(1) * close.pct_change()
        aggression_returns = aggression_returns.fillna(0)
        # 6) Combined
        total_returns = cfg.core_pct * core_returns + cfg.aggression_pct * aggression_returns
        total_equity = comp.equity_from_returns(total_returns, cfg.initial_capital)
        core_equity = comp.equity_from_returns(core_returns, cfg.initial_capital * cfg.core_pct)
        agg_equity = comp.equity_from_returns(aggression_returns, cfg.initial_capital * cfg.aggression_pct)
        # 7) Metrics
        tracker = CapitalEfficiencyTracker()
        prev_core = 0.0
        prev_agg = 0.0
        for i in range(len(total_returns)):
            core_pos = core_position.iloc[i] if i < len(core_position) else 0.0
            agg_pos = aggression_position.iloc[i] if i < len(aggression_position) else 0.0
            tracker.update(
                total_returns.iloc[i],
                core_pos + agg_pos,
                prev_core + prev_agg,
            )
            prev_core = core_pos
            prev_agg = agg_pos
        metrics = tracker.metrics()
        return BacktestResult(
            equity=total_equity,
            core_equity=core_equity,
            aggression_equity=agg_equity,
            core_returns=core_returns,
            aggression_returns=aggression_returns,
            total_returns=total_returns,
            core_position=core_position,
            aggression_position=aggression_position,
            regime=regime_series,
            metrics=metrics,
            config=cfg,
        )


def load_config_yaml(path: str) -> Dict[str, Any]:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def config_from_dict(d: Dict[str, Any]) -> BacktestConfig:
    """Build BacktestConfig from default.yaml-style dict."""
    c = BacktestConfig()
    if "core" in d:
        t = d["core"].get("trend", {})
        c.core_fast_ema = t.get("fast_ema", c.core_fast_ema)
        c.core_slow_ema = t.get("slow_ema", c.core_slow_ema)
        c.core_vol_lookback = t.get("vol_lookback", c.core_vol_lookback)
        c.core_vol_floor = t.get("vol_floor_pct", c.core_vol_floor)
        c.core_vol_target = t.get("vol_target", c.core_vol_target)
        dd = d["core"].get("drawdown", {})
        c.max_drawdown_pct = dd.get("max_drawdown_pct", c.max_drawdown_pct)
        c.dd_reduction_factor = dd.get("dd_reduction_factor", c.dd_reduction_factor)
        c.recovery_required = dd.get("recovery_required", c.recovery_required)
    if "barbell" in d:
        b = d["barbell"]
        c.core_pct = (b.get("core_pct_min", 0.8) + b.get("core_pct_max", 0.95)) / 2
        c.aggression_pct = 1.0 - c.core_pct
        c.use_aggression = b.get("aggression_can_zero", True)
    if "aggression" in d:
        br = d["aggression"].get("breakout", {})
        c.breakout_lookback = br.get("lookback", c.breakout_lookback)
        c.breakout_atr_mult = br.get("atr_mult", c.breakout_atr_mult)
        rs = d["aggression"].get("regime_scaling", {})
        c.aggression_low_scale = rs.get("low_vol_scale", c.aggression_low_scale)
        c.aggression_normal_scale = rs.get("normal_vol_scale", c.aggression_normal_scale)
        c.aggression_expansion_scale = rs.get("expansion_scale", c.aggression_expansion_scale)
    if "regime" in d:
        c.regime_lookback = d["regime"].get("lookback", c.regime_lookback)
    if "funding" in d and d["funding"].get("enabled"):
        c.funding_enabled = True
        c.funding_lookback = d["funding"].get("lookback", c.funding_lookback)
        c.extreme_funding_threshold = d["funding"].get("extreme_negative_threshold", c.extreme_funding_threshold)
        c.trim_long_pct = d["funding"].get("trim_long_exposure_pct", c.trim_long_pct)
    if "tail_risk" in d and d["tail_risk"].get("enabled"):
        c.tail_risk_enabled = True
        c.move_threshold_pct = d["tail_risk"].get("move_threshold_pct", c.move_threshold_pct)
        c.pause_bars = d["tail_risk"].get("pause_bars", c.pause_bars)
        c.reduce_size_pct = d["tail_risk"].get("reduce_size_pct", c.reduce_size_pct)
    return c
