"""
Adaptive per-symbol trailing-stop sizing.

The legacy engine used a single TRAIL_STOP_PCT for every position. That fires
too often on leveraged single-name ETFs (SOXL/TQQQ swing >3% intraday on noise)
and too rarely on slow names (TLT/GLD). This module returns an effective trail
percent per symbol, blended from market and instrument context:

    trail_pct = clip(BASE_K * atr_pct(symbol) * vix_mult * spy_mult * burst_mult
                     * event_mult, FLOOR_PCT, CAP_PCT)

Each multiplier widens in regimes where premature stops would whipsaw, and
tightens in regimes where mean-reverting tape rewards quick exits.

Market context (VIX, SPY trend, realized-vol burst) is computed once per
trading day and cached. Per-symbol ATR is cached per (symbol, date).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

try:
    from config import (
        LEVERAGED_TICKERS, LEV_TRAIL_MULT,
        LEV_NO_ENTRY_VIX, LEV_NO_ENTRY_BURST,
    )
except Exception:
    LEVERAGED_TICKERS = set()
    LEV_TRAIL_MULT      = 0.85
    LEV_NO_ENTRY_VIX    = 22.0
    LEV_NO_ENTRY_BURST  = 1.5

# ── Tunables (defaults sized for top-of-leaderboard QC strategies) ───────────
BASE_K          = 2.25   # base trail = 2.25 × ATR%; ≈3% on a 1.3%-ATR name
FLOOR_PCT       = 0.015  # never trail tighter than 1.5%
CAP_PCT         = 0.20   # absolute ceiling on the effective (ATR-scaled) cap
CAP_ATR_MULT    = 3.0    # effective cap never tighter than 3.0 × the symbol's ATR%
CAP_MIN_PCT     = 0.10   # effective cap floor
ATR_WINDOW      = 14

# VIX regime → trail multiplier (calmer tape ⇒ tighter stops)
VIX_BANDS = [
    # (vix_upper_bound, trail_multiplier)
    (15.0, 0.85),    # complacent — mean-revert tape, tighten
    (22.0, 1.00),    # normal
    (30.0, 1.30),    # elevated — give trades more room
    (float("inf"), 1.60),  # stress — wide stops or stand aside
]
VIX_DAILY_SPIKE_PCT = 0.15   # +15% VIX in a day → widen further
VIX_SPIKE_MULT      = 1.20

# SPY trend → multiplier (risk-on vs risk-off)
SPY_OFF_MULT = 1.10          # SPY < 200-SMA: macro risk-off → slightly wider
SPY_ON_MULT  = 1.00

# Realized vol burst (5d realized > 1.5× trailing 60d) → widen
BURST_RATIO_THRESHOLD = 1.5
BURST_MULT            = 1.25

# Event calendar widener (placeholder hook — set externally per session).
# Map upcoming FOMC/CPI/NFP within N hours to a multiplier > 1.
DEFAULT_EVENT_MULT = 1.00

# Stress overlay → suggests engine reduce exposure rather than just widen stops
STRESS_VIX = 32.0


# ── Per-day market context, cached once ──────────────────────────────────────
@dataclass
class MarketContext:
    asof: date
    vix: float
    vix_change: float            # 1-day fractional change
    spy_above_200sma: bool
    realized_vol_5d: float        # annualized
    realized_vol_60d: float       # annualized
    event_mult: float = DEFAULT_EVENT_MULT

    @property
    def vix_mult(self) -> float:
        m = next(mult for ub, mult in VIX_BANDS if self.vix < ub)
        if self.vix_change >= VIX_DAILY_SPIKE_PCT:
            m *= VIX_SPIKE_MULT
        return m

    @property
    def spy_mult(self) -> float:
        return SPY_ON_MULT if self.spy_above_200sma else SPY_OFF_MULT

    @property
    def burst_mult(self) -> float:
        if self.realized_vol_60d <= 0:
            return 1.0
        return BURST_MULT if (self.realized_vol_5d / self.realized_vol_60d) >= BURST_RATIO_THRESHOLD else 1.0

    @property
    def in_stress(self) -> bool:
        return self.vix >= STRESS_VIX


_ctx_cache: dict[date, MarketContext] = {}
_atr_cache: dict[tuple[str, date], float] = {}


def _today() -> date:
    return datetime.utcnow().date()


def _annualized_realized_vol(closes: pd.Series, window: int) -> float:
    if len(closes) < window + 1:
        return 0.0
    rets = closes.pct_change().dropna().tail(window)
    if rets.empty:
        return 0.0
    return float(rets.std() * math.sqrt(252))


def get_market_context(force_refresh: bool = False) -> Optional[MarketContext]:
    """Refresh VIX / SPY / realized-vol context once per trading day."""
    today = _today()
    if not force_refresh and today in _ctx_cache:
        return _ctx_cache[today]
    try:
        data = yf.download(["^VIX", "SPY"], period="1y", interval="1d",
                           progress=False, auto_adjust=True, threads=True)
        if data.empty:
            return _ctx_cache.get(today)
        close = data["Close"]
        vix_series = close["^VIX"].dropna()
        spy_series = close["SPY"].dropna()
        if vix_series.empty or spy_series.empty:
            return _ctx_cache.get(today)

        vix       = float(vix_series.iloc[-1])
        vix_chg   = float(vix_series.iloc[-1] / vix_series.iloc[-2] - 1.0) if len(vix_series) > 1 else 0.0
        spy_200   = float(spy_series.tail(200).mean()) if len(spy_series) >= 200 else float(spy_series.mean())
        above_200 = float(spy_series.iloc[-1]) > spy_200
        rv5       = _annualized_realized_vol(spy_series, 5)
        rv60      = _annualized_realized_vol(spy_series, 60)

        ctx = MarketContext(
            asof=today,
            vix=vix,
            vix_change=vix_chg,
            spy_above_200sma=above_200,
            realized_vol_5d=rv5,
            realized_vol_60d=rv60,
        )
        _ctx_cache[today] = ctx
        return ctx
    except Exception as e:
        print(f"[adaptive_stops] context refresh failed: {e}")
        return _ctx_cache.get(today)


# ── Per-symbol ATR% ──────────────────────────────────────────────────────────
def _atr_pct(symbol: str) -> Optional[float]:
    """14-day ATR / close, cached per (symbol, date)."""
    key = (symbol, _today())
    if key in _atr_cache:
        return _atr_cache[key]
    try:
        df = yf.download(symbol, period="3mo", interval="1d",
                         progress=False, auto_adjust=True)
        if df.empty or len(df) < ATR_WINDOW + 1:
            return None
        high  = df["High"].astype(float).squeeze("columns") if df["High"].ndim > 1 else df["High"].astype(float)
        low   = df["Low"].astype(float).squeeze("columns")  if df["Low"].ndim  > 1 else df["Low"].astype(float)
        close = df["Close"].astype(float).squeeze("columns") if df["Close"].ndim > 1 else df["Close"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(ATR_WINDOW).mean().iloc[-1]
        last_close = float(close.iloc[-1])
        if last_close <= 0 or not math.isfinite(atr):
            return None
        atr_pct = float(atr) / last_close
        _atr_cache[key] = atr_pct
        return atr_pct
    except Exception as e:
        print(f"[adaptive_stops] atr error {symbol}: {e}")
        return None


# ── Public API ───────────────────────────────────────────────────────────────
@dataclass
class TrailDecision:
    symbol: str
    trail_pct: float
    atr_pct: float
    vix_mult: float
    spy_mult: float
    burst_mult: float
    event_mult: float
    components: dict = field(default_factory=dict)


def is_leveraged(symbol: str) -> bool:
    return symbol.upper() in LEVERAGED_TICKERS


def dynamic_cap(atr_pct: float) -> float:
    """Trail cap must never be tighter than CAP_ATR_MULT × ATR% — a fixed cap
    clips high-ATR names to 1-2× ATR, which whipsaws them out on normal noise."""
    return min(CAP_PCT, max(CAP_MIN_PCT, CAP_ATR_MULT * atr_pct))


def entry_allowed(symbol: str, ctx: Optional[MarketContext] = None) -> tuple[bool, str]:
    """Whether opening a new position in `symbol` is permitted right now.

    Non-leveraged: always allowed.
    Leveraged: blocked when VIX is elevated or 5d-realized vol is bursting.
    Returns (ok, reason).
    """
    if not is_leveraged(symbol):
        return True, ""
    if ctx is None:
        ctx = get_market_context()
    if ctx is None:
        return True, "no_ctx"
    if ctx.vix >= LEV_NO_ENTRY_VIX:
        return False, f"vix={ctx.vix:.1f}≥{LEV_NO_ENTRY_VIX}"
    if ctx.realized_vol_60d > 0 and ctx.realized_vol_5d / ctx.realized_vol_60d >= LEV_NO_ENTRY_BURST:
        ratio = ctx.realized_vol_5d / ctx.realized_vol_60d
        return False, f"vol_burst={ratio:.2f}≥{LEV_NO_ENTRY_BURST}"
    return True, ""


def effective_trail(symbol: str, ctx: Optional[MarketContext] = None,
                    event_mult: float = DEFAULT_EVENT_MULT,
                    fallback_pct: float = 0.03) -> TrailDecision:
    """Return the effective trail percent for a single symbol."""
    if ctx is None:
        ctx = get_market_context()

    atr = _atr_pct(symbol)
    if atr is None or ctx is None:
        return TrailDecision(symbol, fallback_pct, atr or 0.0,
                             1.0, 1.0, 1.0, 1.0,
                             components={"reason": "fallback"})

    vix_m   = ctx.vix_mult
    spy_m   = ctx.spy_mult
    burst_m = ctx.burst_mult
    ev_m    = event_mult
    lev_m   = LEV_TRAIL_MULT if is_leveraged(symbol) else 1.0

    cap = dynamic_cap(atr)
    raw = BASE_K * atr * vix_m * spy_m * burst_m * ev_m * lev_m
    trail = max(FLOOR_PCT, min(cap, raw))

    return TrailDecision(
        symbol=symbol,
        trail_pct=trail,
        atr_pct=atr,
        vix_mult=vix_m,
        spy_mult=spy_m,
        burst_mult=burst_m,
        event_mult=ev_m,
        components={
            "base_k": BASE_K,
            "raw": raw,
            "cap": cap,
            "vix": ctx.vix,
            "vix_chg": ctx.vix_change,
            "spy_above_200": ctx.spy_above_200sma,
            "rv5_over_rv60": (ctx.realized_vol_5d / ctx.realized_vol_60d)
                              if ctx.realized_vol_60d else 0.0,
            "in_stress": ctx.in_stress,
            "leveraged": is_leveraged(symbol),
            "lev_mult": lev_m,
        },
    )


def adaptive_dd_ladder(default_ladder: list[tuple[float, float]],
                       ctx: Optional[MarketContext] = None) -> list[tuple[float, float]]:
    """Scale portfolio drawdown-ladder thresholds by realized-vol regime.

    Calm regimes (rv < 12% ann) → fire ladder earlier (tighten thresholds).
    Stress regimes (rv > 22% ann) → fire later (give the portfolio room).
    """
    if ctx is None:
        ctx = get_market_context()
    if ctx is None or ctx.realized_vol_60d <= 0:
        return default_ladder
    rv = ctx.realized_vol_60d
    if rv < 0.12:
        scale = 0.70
    elif rv > 0.22:
        scale = 1.30
    else:
        scale = 1.00
    return [(round(thr * scale, 4), tgt) for thr, tgt in default_ladder]


def describe(decision: TrailDecision) -> str:
    c = decision.components
    return (f"{decision.symbol:6s} trail={decision.trail_pct*100:.2f}%  "
            f"atr={decision.atr_pct*100:.2f}%  "
            f"vix×{decision.vix_mult:.2f} spy×{decision.spy_mult:.2f} "
            f"burst×{decision.burst_mult:.2f} ev×{decision.event_mult:.2f}  "
            f"raw={c.get('raw', 0)*100:.2f}%")
