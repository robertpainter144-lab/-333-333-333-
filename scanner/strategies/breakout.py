"""Breakout strategy: enter when price breaks a rolling N-min high with
ATR expansion.  Trend-day / regime-shift strategy.

Uses 15-min rolling high and requires the current 2-min move to be > 1.2× the
average step-size (ATR%). Prevents entries on drift-through, requires real
directional force.
"""
from __future__ import annotations

from .base import (
    Signal, load_series, pct, price_at_offset,
    compute_atr_pct, realized_vol_pct, high_over,
)

BREAKOUT_WINDOW_S      = 15 * 60
ATR_EXPANSION_MULT     = 1.2
MIN_HISTORY_SEC        = 15 * 60
MIN_VOL_PCT            = 0.05


def evaluate(sym: str, held: dict | None) -> Signal | None:
    series = load_series(sym, n=120)
    if len(series) < 6:
        return None
    now = series[-1]["ts"]
    if now - series[0]["ts"] < MIN_HISTORY_SEC:
        return None

    px_now = series[-1]["price"]
    typ_2m = realized_vol_pct(series, horizon_s=120)   # for sigma
    if typ_2m < MIN_VOL_PCT:
        return None

    hi     = high_over(series, window_s=BREAKOUT_WINDOW_S)
    atr    = compute_atr_pct(series, window_s=BREAKOUT_WINDOW_S)

    # 2m velocity for the expansion check
    px_2m  = price_at_offset(series, now, 120)
    if px_2m is None or atr <= 0:
        return None
    v_2m   = pct(px_now, px_2m)

    # ── Entry: new 15m high AND 2m step > ATR × mult ───────────────
    if held is None:
        if hi > 0 and px_now > hi and v_2m > atr * ATR_EXPANSION_MULT:
            excess_bps = (px_now / hi - 1) * 10_000   # bps above prior high
            score = round(v_2m / atr, 3)
            return Signal(
                sym=sym, strategy="breakout", action="BUY",
                score=score, sigma=round(v_2m / typ_2m, 2) if typ_2m else 0.0,
                expected_move=round(atr * 3, 3),
                px=px_now,
                note=(f"BREAKOUT {excess_bps:+.0f}bps > 15m hi ${hi:.4f}, "
                      f"v2m={v_2m:+.2f}% ATR={atr:.2f}%"),
                raw={"prior_hi": hi, "excess_bps": excess_bps,
                     "v_2m": v_2m, "atr": atr, "typ_2m": typ_2m},
            )
        return None

    # ── Exit: falls back below entry OR gives back > 1× ATR from peak ──
    entry_px = held["entry_px"]
    peak_px  = max(held.get("peak_px", entry_px), px_now)
    held["peak_px"] = peak_px
    drawdown_pct = pct(px_now, peak_px)

    if px_now < entry_px:
        return Signal(
            sym=sym, strategy="breakout", action="SELL",
            score=abs(pct(px_now, entry_px)), sigma=0.0,
            expected_move=0.0, px=px_now,
            note=f"below entry ${entry_px:.4f} (pnl {pct(px_now, entry_px):+.2f}%)",
            raw={"reason": "failed_breakout"},
        )
    if drawdown_pct <= -atr:
        return Signal(
            sym=sym, strategy="breakout", action="SELL",
            score=abs(drawdown_pct / atr) if atr > 0 else 1.0,
            sigma=0.0, expected_move=0.0, px=px_now,
            note=f"gave back {drawdown_pct:+.2f}% > 1×ATR {atr:.2f}% from peak",
            raw={"reason": "trailing_atr"},
        )
    return None
