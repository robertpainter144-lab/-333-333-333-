#!/usr/bin/env python3
"""
Representative 10-year performance screen across *liquid tradable proxies* for
major traditional asset classes. NOT exhaustive (no full US listing database,
no all mutual funds, no chain-level options).

Data: Yahoo Finance daily adjusted closes via yfinance.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252
RISK_FREE = 0.04
YEARS = 10

# Curated liquid proxies — ETFs/index funds where possible; futures as continuous symbols.
# Options: no single "call" time series; included buy-write / covered-call ETFs as rough income-style proxies.
PROXY_UNIVERSE: dict[str, str] = {
    # US equity — broad & style
    "SPY": "US large blend",
    "VOO": "US large S&P500",
    "VTI": "US total market",
    "QQQ": "US growth / Nasdaq 100",
    "IWM": "US small cap",
    "VUG": "US large growth",
    "VTV": "US large value",
    "DIA": "Dow 30",
    # International equity
    "EFA": "EAFE developed",
    "VEA": "EAFE (Vanguard)",
    "EEM": "Emerging mkts",
    "VWO": "EM (Vanguard)",
    "IEFA": "ex-US developed",
    "FXI": "China large-cap",
    "EWJ": "Japan",
    "EWZ": "Brazil",
    # Fixed income
    "BND": "US aggregate bond",
    "AGG": "US aggregate bond",
    "TLT": "Long Treasury",
    "IEF": "7-10yr Treasury",
    "SHY": "1-3yr Treasury",
    "TIP": "TIPS",
    "LQD": "Investment-grade corp",
    "HYG": "High-yield corp",
    "MUB": "National muni",
    "EMB": "EM sovereign USD",
    "BNDX": "Intl bond hedged",
    # Real assets / commodities
    "GLD": "Gold",
    "IAU": "Gold alt",
    "SLV": "Silver",
    "DBC": "Broad commodities",
    "USO": "WTI oil (ETF)",
    "UNG": "Nat gas (ETF)",
    "PDBC": "Commodities opt",
    # Real estate
    "VNQ": "US REITs",
    "SCHH": "US REIT alt",
    # Vol / alt (often poor long holds — shown for comparison)
    "VXX": "Short-term VIX futures",
    # Options-income *proxies* (not individual calls)
    "JEPI": "Equity + options income",
    "QYLD": "Nasdaq buy-write",
    "XYLD": "S&P buy-write",
    "JEPQ": "Nasdaq equity + income",
    # Futures (continuous) — index & rates
    "ES=F": "E-mini S&P future",
    "NQ=F": "Nasdaq 100 future",
    "RTY=F": "Russell 2000 future",
    "ZN=F": "10Y Treasury note future",
    "GC=F": "Gold future",
    "CL=F": "Crude future",
    # Misc traditional
    "PFF": "Preferred stock",
    "VTIP": "Short TIPS",
    "SPTL": "Long gov bond",
    "SCHZ": "AGG-like",
    "BIV": "Interm bond",
    "BSV": "Short bond",
}


def download_closes(tickers: list[str], years: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(365.25 * years) + 45)
    raw = yf.download(
        tickers=tickers,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=True,
        group_by="column",
    )
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"].copy()
    else:
        closes = raw[["Close"]].copy()
        closes.columns = tickers[:1]
    closes = closes.dropna(how="all", axis=1)
    return closes.sort_index().ffill()


def metrics(series: pd.Series, rets: pd.Series) -> dict:
    s = series.dropna()
    r = rets.dropna()
    if len(s) < TRADING_DAYS * 2:
        return {}
    total = float(s.iloc[-1] / s.iloc[0] - 1.0)
    n = len(s)
    years = n / TRADING_DAYS
    cagr = (1.0 + total) ** (1.0 / years) - 1.0 if years > 0 else np.nan
    vol = float(r.std() * np.sqrt(TRADING_DAYS)) if len(r) > 20 else np.nan
    excess = r - RISK_FREE / TRADING_DAYS
    sharpe = (
        float(excess.mean() / excess.std() * np.sqrt(TRADING_DAYS))
        if excess.std() > 1e-12
        else np.nan
    )
    peak = s.cummax()
    max_dd = float(((s / peak) - 1.0).min())
    return {
        "cagr": cagr,
        "total_return": total,
        "vol": vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "days": n,
    }


def main() -> None:
    tickers = list(PROXY_UNIVERSE.keys())
    print("Downloading ~10y daily data for representative proxy universe...")
    print(f"Tickers: {len(tickers)} (not exhaustive)\n")
    closes = download_closes(tickers, YEARS)
    rets = closes.pct_change()

    rows = []
    for t in closes.columns:
        m = metrics(closes[t], rets[t])
        if not m:
            continue
        rows.append(
            {
                "ticker": t,
                "description": PROXY_UNIVERSE.get(t, ""),
                **m,
            }
        )

    df = pd.DataFrame(rows)
    df = df.sort_values("cagr", ascending=False)

    print("=== Rank by CAGR (past ~10y, overlapping sample; proxies only) ===\n")
    fmt = {
        "cagr": lambda x: f"{100*x:.2f}%",
        "total_return": lambda x: f"{100*x:.2f}%",
        "vol": lambda x: f"{100*x:.2f}%",
        "sharpe": lambda x: f"{x:.2f}",
        "max_dd": lambda x: f"{100*x:.2f}%",
        "days": lambda x: f"{int(x)}",
    }
    pd.set_option("display.max_rows", 80)
    pd.set_option("display.width", 140)
    print(
        df.to_string(
            columns=[
                "ticker",
                "description",
                "cagr",
                "vol",
                "sharpe",
                "max_dd",
                "total_return",
                "days",
            ],
            formatters=fmt,
            index=False,
        )
    )

    print("\n--- Notes ---")
    print("- This is an automated Python script (yfinance), not IBKR, not a manual table.")
    print("- No scan of 'all' stocks/options/funds; many mutual funds lack clean daily series here.")
    print("- Individual call options are path-dependent; buy-write ETFs are imperfect proxies.")
    print("- Futures shown are continuous contracts; roll drag/carry not modeled beyond price change.")
    print("- Past performance does not predict future results.")


if __name__ == "__main__":
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        main()
