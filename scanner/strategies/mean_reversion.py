"""Mean-reversion strategy: buy oversold, sell overbought.

Uses RSI over a 15-min window. Only fires on liquid coins (RSI is noisy on thin
tokens). Alpaca crypto can't short easily, so overbought → SELL only means "exit
if held", not "open short".
"""
from __future__ import annotations

from .base import (
    Signal, load_series, pct, is_major_liq,
    compute_rsi, realized_vol_pct, price_at_offset,
)

RSI_OVERSOLD    = 30.0
RSI_OVERBOUGHT  = 70.0
MIN_HISTORY_SEC = 15 * 60
MIN_VOL_PCT     = 0.03


def evaluate(sym: str, held: dict | None) -> Signal | None:
    # Mean-rev on chop demands liquidity — thin coins whiplash and eat costs
    if not is_major_liq(sym):
        return None

    series = load_series(sym, n=120)
    if len(series) < 6:
        return None
    now = series[-1]["ts"]
    if now - series[0]["ts"] < MIN_HISTORY_SEC:
        return None

    px_now = series[-1]["price"]
    rsi    = compute_rsi(series, period_s=15 * 60)
    # Target/stop scale off typical 15-min move (mean-revert horizon)
    typ_15m = realized_vol_pct(series, horizon_s=900)
    if typ_15m < MIN_VOL_PCT:
        return None

    # ── Entry: oversold ────────────────────────────────────────────
    if held is None and rsi <= RSI_OVERSOLD:
        score = round((RSI_OVERSOLD - rsi) / 5.0, 3)   # 5 rsi points = 1.0 score
        return Signal(
            sym=sym, strategy="mean_reversion", action="BUY",
            score=score, sigma=0.0,
            expected_move=round(typ_15m, 3),   # target ~1σ 15m mean-revert
            px=px_now,
            note=f"RSI15={rsi:.1f} oversold typ15m={typ_15m:.2f}%",
            raw={"rsi": rsi, "typ_15m": typ_15m, "kind": "oversold"},
        )

    # ── Exit: overbought or reverted to mean ───────────────────────
    if held is not None:
        if rsi >= RSI_OVERBOUGHT:
            score = round((rsi - RSI_OVERBOUGHT) / 5.0, 3)
            return Signal(
                sym=sym, strategy="mean_reversion", action="SELL",
                score=score, sigma=0.0,
                expected_move=0.0, px=px_now,
                note=f"RSI15={rsi:.1f} overbought — take profit",
                raw={"rsi": rsi, "kind": "overbought_exit"},
            )
        # Neutral zone → exit if we've made ≥ typ_15m (mean-reverted enough)
        entry_px = held["entry_px"]
        pnl_pct = pct(px_now, entry_px)
        if pnl_pct >= typ_15m:
            return Signal(
                sym=sym, strategy="mean_reversion", action="SELL",
                score=1.0, sigma=0.0,
                expected_move=0.0, px=px_now,
                note=f"RSI15={rsi:.1f} reverted pnl {pnl_pct:+.2f}% ≥ typ15m {typ_15m:.2f}%",
                raw={"rsi": rsi, "pnl_pct": pnl_pct, "kind": "target_hit"},
            )
        # Stop: down > 1.5× typ_15m from entry
        if pnl_pct <= -1.5 * typ_15m:
            return Signal(
                sym=sym, strategy="mean_reversion", action="SELL",
                score=abs(pnl_pct), sigma=0.0,
                expected_move=0.0, px=px_now,
                note=f"stop-loss pnl {pnl_pct:+.2f}% ≤ -1.5×typ15m",
                raw={"pnl_pct": pnl_pct, "kind": "stop_loss"},
            )

    return None
