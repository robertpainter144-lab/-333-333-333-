#!/usr/bin/env python3
"""
Swing scanner / backtest v2 — ETFs + liquid large-cap stocks, regime filter.

Extensions vs v1:
- Universe: core ETFs + S&P 100-style liquid US stocks (single-name risk: diversify later).
- Regime (evaluated on prior close): allow new entries only if
    SPY close > SPY MA(50) AND VIX close < VIX_MAX (default 30).
- Same entry/exit mechanics as v1 (breakout + volume, TP/SL/time stop).
- IBKR Lite: $0 commission; sell-side regulatory fees on stocks/ETFs.

Research prototype — not trading advice.
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
LOOKBACK_DAYS = 450
MA_TREND = 50
BREAKOUT_LOOKBACK = 20
VOL_LOOKBACK = 20
VOL_MULT = 1.1
TAKE_PROFIT = 0.03
STOP_LOSS = 0.015
MAX_HOLD_DAYS = 3

# Regime
VIX_MAX = 30.0
SPY_MA = 50

SEC_FEE_RATE = 0.0000278
FINRA_TAF_PER_SHARE = 0.000166
FINRA_TAF_CAP = 8.30

ETF_UNIVERSE = [
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
    "SMH",
    "XBI",
]

# Liquid large caps (S&P 100-style subset; edit as needed)
STOCK_UNIVERSE = [
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "NVDA",
    "META",
    "TSLA",
    "BRK-B",
    "UNH",
    "JNJ",
    "V",
    "PG",
    "JPM",
    "MA",
    "HD",
    "CVX",
    "MRK",
    "ABBV",
    "PEP",
    "KO",
    "COST",
    "AVGO",
    "WMT",
    "MCD",
    "CSCO",
    "ACN",
    "TMO",
    "DHR",
    "VZ",
    "ADBE",
    "NFLX",
    "CRM",
    "NKE",
    "PM",
    "TXN",
    "NEE",
    "LIN",
    "DIS",
    "BMY",
    "UPS",
    "RTX",
    "QCOM",
    "HON",
    "LOW",
    "AMGN",
    "INTU",
    "IBM",
    "GE",
    "CAT",
    "BA",
    "GS",
    "MS",
    "BLK",
    "SCHW",
    "AXP",
    "DE",
    "GILD",
    "MDT",
    "ISRG",
    "SYK",
    "ZTS",
    "SPGI",
    "TJX",
    "CB",
    "MMC",
    "ADI",
    "CME",
    "MO",
    "BKNG",
    "REGN",
    "EOG",
    "SLB",
    "SO",
    "DUK",
    "PLD",
]

UNIVERSE = ETF_UNIVERSE + STOCK_UNIVERSE


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


def build_regime_series(start: datetime, end: datetime) -> pd.Series:
    """Boolean Series indexed by date: True = allow new longs."""
    spy = load_symbol("SPY", start, end)
    vix = load_symbol("^VIX", start, end)
    if spy is None or vix is None or len(spy) < SPY_MA + 5 or len(vix) < 5:
        return pd.Series(dtype=bool)

    spy = spy.copy()
    spy["ma"] = spy["Close"].rolling(SPY_MA).mean()
    spy["bull"] = spy["Close"] > spy["ma"]
    vix = vix.copy()
    vix["ok"] = vix["Close"] < VIX_MAX

    aligned = spy[["bull"]].join(vix[["ok"]], how="inner", rsuffix="_vix")
    aligned["regime_ok"] = aligned["bull"] & aligned["ok"]
    return aligned["regime_ok"].dropna()


@dataclass
class Position:
    symbol: str
    shares: float
    entry_price: float
    days_held: int


def run_backtest() -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=LOOKBACK_DAYS)

    regime = build_regime_series(start, end)
    if regime.empty:
        raise SystemExit("Could not build regime series (SPY/VIX)")

    data: Dict[str, pd.DataFrame] = {}
    for sym in UNIVERSE:
        d = load_symbol(sym, start, end)
        if d is None or len(d) < MA_TREND + BREAKOUT_LOOKBACK + 5:
            continue
        data[sym] = enrich(d)

    if not data:
        raise SystemExit("No symbol data loaded")

    common_idx = None
    for d in data.values():
        common_idx = d.index if common_idx is None else common_idx.intersection(d.index)
    common_idx = common_idx.intersection(regime.index)
    common_idx = common_idx.sort_values()
    window_days = 365
    if len(common_idx) > window_days:
        common_idx = common_idx[-window_days:]

    aligned: Dict[str, pd.DataFrame] = {}
    for sym, d in data.items():
        aligned[sym] = d.reindex(common_idx).ffill()

    cash = START_CAPITAL
    pos: Optional[Position] = None
    equity_curve: List[float] = []
    trade_pnls: List[float] = []
    trades = 0

    for i in range(1, len(common_idx)):
        day = common_idx[i]
        prev_day = common_idx[i - 1]

        regime_ok = bool(regime.loc[prev_day]) if prev_day in regime.index else False

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

        if pos is None and cash > 1.0 and regime_ok:
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
                        cash -= shares * px
                        pos = Position(
                            symbol=best_sym,
                            shares=shares,
                            entry_price=px,
                            days_held=0,
                        )

        mtm = cash
        if pos is not None:
            cl = float(aligned[pos.symbol].loc[day]["Close"])
            mtm += pos.shares * cl
        equity_curve.append(mtm)

    if pos is not None:
        last_day = common_idx[-1]
        cl = float(aligned[pos.symbol].loc[last_day]["Close"])
        val = pos.shares * cl
        fees = sell_reg_fees(pos.shares, val)
        cash += val - fees
        trade_pnls.append((val - fees) - (pos.shares * pos.entry_price))
        trades += 1

    final = cash
    ret_pct = (final / START_CAPITAL - 1.0) * 100.0
    eq = pd.Series([START_CAPITAL] + equity_curve)
    max_dd = float(((eq / eq.cummax()) - 1.0).min() * 100.0)
    wins = sum(1 for p in trade_pnls if p > 0)

    print("=== Swing scanner backtest v2 ===")
    print(f"Symbols loaded: {len(data)} / {len(UNIVERSE)}")
    print(f"Calendar days: {len(common_idx)}")
    print(f"Regime: SPY > MA({SPY_MA}) AND VIX < {VIX_MAX}")
    print(f"Start: ${START_CAPITAL:,.2f}")
    print(f"Final: ${final:,.2f}")
    print(f"Return: {ret_pct:.2f}%")
    print(f"Trades: {trades}")
    print(f"Win rate: {(wins / trades * 100.0) if trades else 0.0:.2f}%")
    print(f"Max drawdown: {max_dd:.2f}%")


if __name__ == "__main__":
    run_backtest()
