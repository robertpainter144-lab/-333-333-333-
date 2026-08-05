#!/usr/bin/env python3
"""
Swing scanner / backtest v1 — multi-ETF universe, daily bars.

Rules (long-only, one position at a time):
- Signal evaluated on completed daily bar (t-1), trade at next open.
- Entry filters:
  - Close > MA(50)
  - Close > highest close of prior 20 sessions (20-day breakout)
  - Volume > 1.1 × 20-day average volume
- Ranking among candidates: volume_ratio × (20-day momentum %)
- Exit (first hit wins each day using OHLC):
  - Take profit +3% (intraday high)
  - Stop loss -1.5% (intraday low)
  - Time exit: close position at close of 3rd session after entry
- Fees: IBKR Lite style — $0 commission, sell-side regulatory fees only.

This is a research prototype, not trading advice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

# --- Config ---
START_CAPITAL = 500.0
LOOKBACK_DAYS = 400  # extra for warm-up
MA_TREND = 50
BREAKOUT_LOOKBACK = 20
VOL_LOOKBACK = 20
VOL_MULT = 1.1
TAKE_PROFIT = 0.03
STOP_LOSS = 0.015
MAX_HOLD_DAYS = 3

SEC_FEE_RATE = 0.0000278
FINRA_TAF_PER_SHARE = 0.000166
FINRA_TAF_CAP = 8.30

UNIVERSE = [
    "SPLG",
    "QQQM",
    "IWM",
    "DIA",
    "XLE",
    "XLK",
    "XLF",
    "XLU",
    "DBC",
    "GLDM",
    "TLT",
    "VEA",
    "VWO",
    "EEM",
    "EFA",
]


def sell_reg_fees(shares: float, sell_value: float) -> float:
    finra = min(FINRA_TAF_CAP, FINRA_TAF_PER_SHARE * shares)
    sec = SEC_FEE_RATE * sell_value
    return finra + sec


def load_symbol(sym: str, start: datetime, end: datetime) -> Optional[pd.DataFrame]:
    df = yf.download(
        sym,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    if df.empty or not {"Open", "High", "Low", "Close", "Volume"}.issubset(df.columns):
        return None
    return df.dropna(subset=["Open", "High", "Low", "Close"]).copy()


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c = out["Close"]
    out["ma50"] = c.rolling(MA_TREND).mean()
    out["vol20"] = out["Volume"].rolling(VOL_LOOKBACK).mean()
    out["prior_high20"] = c.shift(1).rolling(BREAKOUT_LOOKBACK).max()
    out["mom20"] = c / c.shift(20) - 1.0
    return out


def signal_row(row: pd.Series) -> bool:
    if any(
        math.isnan(float(row.get(k, np.nan)))
        for k in ("ma50", "prior_high20", "vol20")
    ):
        return False
    close = float(row["Close"])
    if close <= float(row["ma50"]):
        return False
    if close <= float(row["prior_high20"]):
        return False
    vol = float(row["Volume"])
    if vol <= VOL_MULT * float(row["vol20"]):
        return False
    return True


def score_row(row: pd.Series) -> float:
    vol_ratio = float(row["Volume"]) / max(float(row["vol20"]), 1e-9)
    mom = float(row["mom20"]) if not math.isnan(float(row["mom20"])) else 0.0
    return max(vol_ratio, 0.0) * (1.0 + max(mom, 0.0))


@dataclass
class Position:
    symbol: str
    shares: float
    entry_price: float
    entry_day_idx: int
    days_held: int


def run_backtest() -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    data: Dict[str, pd.DataFrame] = {}
    for sym in UNIVERSE:
        d = load_symbol(sym, start, end)
        if d is None or len(d) < MA_TREND + BREAKOUT_LOOKBACK + 5:
            continue
        data[sym] = enrich(d)

    if not data:
        raise SystemExit("No data loaded")

    # Common calendar: intersection of dates (US ETFs should align; keep strict intersection)
    common_idx = None
    for d in data.values():
        common_idx = d.index if common_idx is None else common_idx.intersection(d.index)
    common_idx = common_idx.sort_values()
    window_days = 365
    if len(common_idx) > window_days:
        common_idx = common_idx[-window_days:]

    cash = START_CAPITAL
    pos: Optional[Position] = None
    equity_curve: List[float] = []
    trade_pnls: List[float] = []
    trades = 0

    # Reindex every symbol to the same calendar (do not drop rows per symbol)
    aligned: Dict[str, pd.DataFrame] = {}
    for sym, d in data.items():
        aligned[sym] = d.reindex(common_idx).ffill()

    for i in range(1, len(common_idx)):
        day = common_idx[i]
        prev_day = common_idx[i - 1]

        # --- Manage open position ---
        if pos is not None:
            bar = aligned[pos.symbol].loc[day]
            o, h, l, cl = (
                float(bar["Open"]),
                float(bar["High"]),
                float(bar["Low"]),
                float(bar["Close"]),
            )
            entry = pos.entry_price
            tp = entry * (1.0 + TAKE_PROFIT)
            sl = entry * (1.0 - STOP_LOSS)

            exit_px: Optional[float] = None
            # Conservative: if both stop and target touched same day, take worse (stop first)
            if l <= sl:
                exit_px = sl
            elif h >= tp:
                exit_px = tp
            else:
                pos.days_held += 1
                if pos.days_held >= MAX_HOLD_DAYS:
                    exit_px = cl

            if exit_px is not None:
                val = pos.shares * exit_px
                fees = sell_reg_fees(pos.shares, val)
                cash += val - fees
                trade_pnls.append((val - fees) - (pos.shares * entry))
                trades += 1
                pos = None

        # --- New entries only when flat ---
        if pos is None and cash > 1.0:
            candidates: List[Tuple[str, float]] = []
            for sym, df_sym in aligned.items():
                prev = df_sym.loc[prev_day]
                if signal_row(prev):
                    candidates.append((sym, score_row(prev)))

            if candidates:
                candidates.sort(key=lambda x: x[1], reverse=True)
                best_sym = candidates[0][0]
                row_today = aligned[best_sym].loc[day]
                px = float(row_today["Open"])
                if px > 0 and not math.isnan(px):
                    shares = cash / px
                    if shares > 0:
                        cost = shares * px
                        cash -= cost
                        pos = Position(
                            symbol=best_sym,
                            shares=shares,
                            entry_price=px,
                            entry_day_idx=i,
                            days_held=0,
                        )

        # Mark-to-market equity
        mtm = cash
        if pos is not None:
            cl = float(aligned[pos.symbol].loc[day]["Close"])
            mtm += pos.shares * cl
        equity_curve.append(mtm)

    # Liquidate at last close if still open
    if pos is not None:
        last_day = common_idx[-1]
        cl = float(aligned[pos.symbol].loc[last_day]["Close"])
        val = pos.shares * cl
        fees = sell_reg_fees(pos.shares, val)
        cash += val - fees
        trade_pnls.append((val - fees) - (pos.shares * pos.entry_price))
        trades += 1
        pos = None

    final = cash
    ret_pct = (final / START_CAPITAL - 1.0) * 100.0
    eq = pd.Series([START_CAPITAL] + equity_curve)
    max_dd = float(((eq / eq.cummax()) - 1.0).min() * 100.0)
    wins = sum(1 for p in trade_pnls if p > 0)

    print("=== Swing scanner backtest v1 ===")
    print(f"Universe: {', '.join(UNIVERSE)}")
    print(f"Calendar days in window: {len(common_idx)}")
    print(f"Start: ${START_CAPITAL:,.2f}")
    print(f"Final: ${final:,.2f}")
    print(f"Return: {ret_pct:.2f}%")
    print(f"Trades: {trades}")
    print(f"Win rate: {(wins / trades * 100.0) if trades else 0.0:.2f}%")
    print(f"Max drawdown: {max_dd:.2f}%")
    print(
        f"Params: TP={TAKE_PROFIT*100:.1f}% SL={STOP_LOSS*100:.1f}% max_hold={MAX_HOLD_DAYS}d"
    )


if __name__ == "__main__":
    run_backtest()
