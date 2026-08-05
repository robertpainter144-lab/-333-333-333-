#!/usr/bin/env python3
"""
5-year historical analysis for the IBKR-style universe in swing_scanner_backtest_v2.

- Starting notional: $500 (fractional shares assumed for allocation math).
- Pulls ~5 years of daily adjusted closes via yfinance.
- Ranks symbols by risk-adjusted metrics and rolling 1-year (252 trading day) experience.
- Suggests equal-weight and max-Sharpe long-only weights (research / education — not advice).

Past performance does not predict future results; "next 365 days" is unknowable.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.optimize import minimize

# Mirror universe from swing_scanner_backtest_v2.py (edit one place if you change it)
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

START_CAPITAL = 500.0
YEARS = 5
TRADING_DAYS = 252
RISK_FREE_ANNUAL = 0.04  # rough cash yield for Sharpe denominator context


def _download_closes(symbols: list[str], years: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(365.25 * years) + 30)
    data = yf.download(
        tickers=symbols,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=True,
        group_by="column",
    )
    if isinstance(data.columns, pd.MultiIndex):
        if "Close" in data.columns.get_level_values(0):
            closes = data["Close"].copy()
        else:
            closes = data.xs("Close", axis=1, level=0)
    else:
        closes = data[["Close"]].copy()
        closes.columns = symbols[:1]
    closes = closes.dropna(how="all", axis=1)
    # require enough history
    min_len = TRADING_DAYS * 2
    keep = [c for c in closes.columns if closes[c].notna().sum() >= min_len]
    return closes[keep].sort_index().ffill()


def annualized_return(daily: pd.Series) -> float:
    daily = daily.dropna()
    if len(daily) < 2:
        return float("nan")
    total = float(daily.iloc[-1] / daily.iloc[0] - 1.0)
    n = len(daily)
    years = n / TRADING_DAYS
    if years <= 0:
        return float("nan")
    return (1.0 + total) ** (1.0 / years) - 1.0


def annualized_vol(returns: pd.Series) -> float:
    r = returns.dropna()
    if len(r) < 20:
        return float("nan")
    return float(r.std() * np.sqrt(TRADING_DAYS))


def max_drawdown(prices: pd.Series) -> float:
    p = prices.dropna()
    if len(p) < 2:
        return float("nan")
    peak = p.cummax()
    dd = (p / peak) - 1.0
    return float(dd.min())


def rolling_1y_stats(returns: pd.Series) -> dict:
    """Overlapping 252-day trailing windows."""
    r = returns.dropna()
    if len(r) < TRADING_DAYS + 1:
        return {"n_windows": 0, "median_1y": np.nan, "pct_positive": np.nan, "worst_1y": np.nan, "best_1y": np.nan}
    roll = r.rolling(TRADING_DAYS).apply(lambda x: (1.0 + x).prod() - 1.0, raw=False)
    roll = roll.dropna()
    if roll.empty:
        return {"n_windows": 0, "median_1y": np.nan, "pct_positive": np.nan, "worst_1y": np.nan, "best_1y": np.nan}
    return {
        "n_windows": int(len(roll)),
        "median_1y": float(roll.median()),
        "pct_positive": float((roll > 0).mean()),
        "worst_1y": float(roll.min()),
        "best_1y": float(roll.max()),
    }


def sharpe_ratio(returns: pd.Series, rf_annual: float = RISK_FREE_ANNUAL) -> float:
    r = returns.dropna()
    if len(r) < 40:
        return float("nan")
    excess = r - rf_annual / TRADING_DAYS
    v = excess.std() * np.sqrt(TRADING_DAYS)
    if v < 1e-12:
        return float("nan")
    return float(excess.mean() / excess.std() * np.sqrt(TRADING_DAYS))


def max_sharpe_weights(mu: np.ndarray, cov: np.ndarray) -> np.ndarray:
    n = len(mu)
    x0 = np.ones(n) / n

    def neg_sharpe(w: np.ndarray) -> float:
        port_ret = float(w @ mu)
        port_vol = float(np.sqrt(w @ cov @ w))
        if port_vol < 1e-12:
            return 1e9
        return -(port_ret - RISK_FREE_ANNUAL) / port_vol

    cons = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
    bounds = tuple((0.0, 1.0) for _ in range(n))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = minimize(neg_sharpe, x0, method="SLSQP", bounds=bounds, constraints=cons)
    if not res.success:
        return x0
    w = np.maximum(res.x, 0.0)
    s = w.sum()
    return w / s if s > 0 else x0


@dataclass
class Row:
    symbol: str
    cagr: float
    vol: float
    sharpe: float
    max_dd: float
    median_1y: float
    pct_pos_1y: float
    worst_1y: float
    best_1y: float


def main() -> None:
    print("Downloading daily data (this may take a minute)...")
    closes = _download_closes(UNIVERSE, YEARS)
    # align to common dates for portfolio optimization
    closes = closes.dropna(how="any", axis=0)
    if len(closes) < TRADING_DAYS + 50:
        raise SystemExit("Not enough overlapping history for analysis.")

    rets = closes.pct_change().dropna()
    rows: list[Row] = []
    for sym in closes.columns:
        px = closes[sym]
        r = rets[sym]
        rs = rolling_1y_stats(r)
        rows.append(
            Row(
                symbol=sym,
                cagr=annualized_return(px),
                vol=annualized_vol(r),
                sharpe=sharpe_ratio(r),
                max_dd=max_drawdown(px),
                median_1y=rs["median_1y"],
                pct_pos_1y=rs["pct_positive"],
                worst_1y=rs["worst_1y"],
                best_1y=rs["best_1y"],
            )
        )

    df = pd.DataFrame([vars(x) for x in rows])
    df = df.sort_values("sharpe", ascending=False, na_position="last")

    mu = rets.mean().values * TRADING_DAYS
    cov = rets.cov().values * TRADING_DAYS
    symbols_list = list(rets.columns)
    w_max = max_sharpe_weights(mu, cov)
    w_eq = np.ones(len(symbols_list)) / len(symbols_list)

    print()
    print("=== IBKR pool — buy-and-hold style stats (not a trading-system backtest) ===")
    print(f"Period: ~{YEARS} years of overlapping daily data, ending {closes.index[-1].date()}")
    print(f"Symbols with data: {len(symbols_list)} / {len(UNIVERSE)}")
    print(f"Notional: ${START_CAPITAL:,.2f} (fractional shares assumed)")
    print(f"Risk-free used in Sharpe: {RISK_FREE_ANNUAL:.1%} annualized")
    print()
    print("Rolling ~1 year = 252 trading-day trailing return; windows overlap (not independent).")
    print("Forward 365 calendar days cannot be forecast from this; rankings are descriptive only.")
    print()

    show = df.head(25)[
        [
            "symbol",
            "cagr",
            "vol",
            "sharpe",
            "max_dd",
            "median_1y",
            "pct_pos_1y",
            "worst_1y",
            "best_1y",
        ]
    ]
    pd.set_option("display.max_rows", 30)
    pd.set_option("display.width", 120)
    print("--- Top 25 by historical Sharpe (5y sample) ---")
    print(
        show.to_string(
            formatters={
                "cagr": lambda x: f"{100*x:.2f}%",
                "vol": lambda x: f"{100*x:.2f}%",
                "sharpe": lambda x: f"{x:.2f}",
                "max_dd": lambda x: f"{100*x:.2f}%",
                "median_1y": lambda x: f"{100*x:.2f}%",
                "pct_pos_1y": lambda x: f"{100*x:.1f}%",
                "worst_1y": lambda x: f"{100*x:.2f}%",
                "best_1y": lambda x: f"{100*x:.2f}%",
            },
            index=False,
        )
    )

    # Portfolio-level simplistic metrics (full universe — concentrated; use top-k for small accounts)
    top_k = 12
    top_syms = df.head(top_k)["symbol"].tolist()
    sub = rets[top_syms]
    mu_s = sub.mean().values * TRADING_DAYS
    cov_s = sub.cov().values * TRADING_DAYS
    w_top = max_sharpe_weights(mu_s, cov_s)
    w_eq_top = np.ones(top_k) / top_k

    def port_ann_return_vol(w: np.ndarray, sub_rets: pd.DataFrame) -> tuple[float, float]:
        mu_d = sub_rets.mean().values
        cov_d = sub_rets.cov().values
        mr = float(w @ mu_d * TRADING_DAYS)
        v = float(np.sqrt(w @ cov_d * TRADING_DAYS @ w))
        return mr, v

    mr_eq, vol_eq = port_ann_return_vol(w_eq_top, sub)
    mr_ms, vol_ms = port_ann_return_vol(w_top, sub)

    print()
    print(f"--- Diversified subset (top {top_k} by Sharpe) — illustrative weights ---")
    print(f"Equal-weight approx CAGR (linear, not compounded): {100*mr_eq:.2f}%, ann. vol ~{100*vol_eq:.2f}%")
    print(f"Max-Sharpe (long-only, same subset) approx CAGR: {100*mr_ms:.2f}%, ann. vol ~{100*vol_ms:.2f}%")
    print()
    alloc = pd.DataFrame(
        {
            "symbol": top_syms,
        "equal_weight_pct": (100 * w_eq_top).round(2),
            "max_sharpe_pct": (100 * w_top).round(2),
        }
    )
    alloc["eq_dollars"] = (START_CAPITAL * w_eq_top).round(2)
    alloc["ms_dollars"] = (START_CAPITAL * w_top).round(2)
    print(alloc.to_string(index=False))

    print()
    print("--- Full-universe max-Sharpe weights (often very concentrated — use with care) ---")
    full_alloc = pd.DataFrame(
        {"symbol": symbols_list, "weight_pct": (100 * w_max).round(2)}
    )
    full_alloc = full_alloc[full_alloc["weight_pct"] > 0.5].sort_values("weight_pct", ascending=False)
    print(full_alloc.to_string(index=False))


if __name__ == "__main__":
    main()
