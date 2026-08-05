"""Vol-normalized momentum strategy.

Enter when the 2-min move exceeds N × 1-hour realized vol AND the move is
accelerating. Vol-normalization means 0.15% on BTC and 0.6% on SHIB can both
be 1.5σ moves — apples-to-apples across the universe.
"""
from __future__ import annotations

import time
from typing import Iterable

from .base import (
    UNIVERSE, Signal, load_series, pct, price_at_offset,
    realized_vol_pct, is_major_liq,
)

# Tunables. Sigma bar means "this move is > N × typical 2m move".
# 2026-07-10: widened exits + tightened entries to reduce churn.
ENTRY_SIGMA        = 2.0       # was 1.3 — fewer, higher-conviction entries
ENTRY_MIN_ACCEL_PP = 0.05      # was 0.01 — need real accel, not noise
EXIT_SIGMA         = -1.0      # was -0.5 — give trades room
EXIT_DRAWDOWN_SIGMA = -1.5     # was -1.0 — trail wider so we hold winners
MIN_HISTORY_SEC    = 10 * 60   # need ≥10 min of history before we act
MIN_VOL_PCT        = 0.05      # skip totally flat coins where vol=0 breaks math


def evaluate(sym: str, held: dict | None) -> Signal | None:
    """Called by the portfolio manager per-tick per-symbol.
    `held` is that symbol's open-position dict if we're currently long, else None.
    Returns a BUY / SELL / None decision.
    """
    series = load_series(sym, n=120)
    if len(series) < 6:
        return None
    now = series[-1]["ts"]
    if now - series[0]["ts"] < MIN_HISTORY_SEC:
        return None

    px_now = series[-1]["price"]
    px_2m  = price_at_offset(series, now, 120)
    px_4m  = price_at_offset(series, now, 240)
    if px_2m is None or px_4m is None:
        return None

    v_2m    = pct(px_now, px_2m)
    v_2m4m  = pct(px_2m, px_4m)
    accel   = v_2m - v_2m4m
    # typical 2m move for sigma comparison; typical 15m move for target
    typ_2m  = realized_vol_pct(series, horizon_s=120)
    typ_15m = realized_vol_pct(series, horizon_s=900)
    if typ_2m < MIN_VOL_PCT:
        return None

    sigma_now = v_2m / typ_2m if typ_2m > 0 else 0.0

    # ── Entry: move is >= 1.5σ AND accelerating ────────────────────
    if held is None:
        if sigma_now >= ENTRY_SIGMA and accel >= ENTRY_MIN_ACCEL_PP:
            # If we're 1.5σ into a 2m move, extrapolate to a ~15m continuation
            expected_move = min(3.0, max(typ_15m, abs(v_2m) * 2))
            score = round(sigma_now + accel * 5, 3)
            return Signal(
                sym=sym, strategy="momentum", action="BUY",
                score=score, sigma=round(sigma_now, 2),
                expected_move=round(expected_move, 3),
                px=px_now,
                note=(f"σ={sigma_now:+.2f} v2m={v_2m:+.2f}% accel={accel:+.2f}pp "
                      f"typ2m={typ_2m:.2f}% typ15m={typ_15m:.2f}%"),
                raw={"v_2m": v_2m, "v_2m4m": v_2m4m, "accel": accel,
                     "typ_2m": typ_2m, "typ_15m": typ_15m},
            )
        return None

    # ── Exit logic (we're holding) ─────────────────────────────────
    peak_px = held.get("peak_px", held["entry_px"])
    peak_px = max(peak_px, px_now)
    held["peak_px"] = peak_px
    drawdown_pct = pct(px_now, peak_px)
    drawdown_sigma = drawdown_pct / typ_2m if typ_2m > 0 else 0.0

    if sigma_now <= EXIT_SIGMA:
        return Signal(sym=sym, strategy="momentum", action="SELL",
                      score=abs(sigma_now), sigma=round(sigma_now, 2),
                      expected_move=0.0, px=px_now,
                      note=f"momentum reversed σ={sigma_now:+.2f} v2m={v_2m:+.2f}%",
                      raw={"reason": "sigma_reversal", "sigma": sigma_now})

    if drawdown_sigma <= EXIT_DRAWDOWN_SIGMA:
        return Signal(sym=sym, strategy="momentum", action="SELL",
                      score=abs(drawdown_sigma),
                      sigma=round(drawdown_sigma, 2),
                      expected_move=0.0, px=px_now,
                      note=f"drawdown {drawdown_pct:+.2f}% ({drawdown_sigma:+.2f}σ) from peak",
                      raw={"reason": "peak_drawdown", "drawdown_pct": drawdown_pct})

    return None
