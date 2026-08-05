"""
Fetch real OHLCV data from Yahoo Finance.
"""

from __future__ import annotations

import pandas as pd
from typing import Optional


def fetch_ohlcv(
    symbol: str = "SPY",
    period: str = "2y",
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Download OHLCV from Yahoo Finance.
    symbol: ticker (e.g. SPY, BTC-USD, ETH-USD, QQQ)
    period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max
    interval: 1d, 1wk, 1mo (for period > 1d)
    """
    try:
        import yfinance as yf
    except ImportError:
        raise ImportError("Install yfinance: pip install yfinance") from None
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=True)
    if df.empty or len(df) < 20:
        raise ValueError(f"No or insufficient data for {symbol} (period={period})")
    df.columns = [c.lower() for c in df.columns]
    required = ["open", "high", "low", "close"]
    if not all(c in df.columns for c in required):
        raise ValueError(f"Missing OHLC columns; got {list(df.columns)}")
    return df[required].dropna(how="all").sort_index()
