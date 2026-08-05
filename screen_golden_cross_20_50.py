#!/usr/bin/env python3
"""
Screen for a 20/50 SMA *bullish* cross on the latest bar:
  SMA(20) > SMA(50) today AND SMA(20) <= SMA(50) yesterday.

Universe: S&P 500 + Nasdaq-100 + curated ETFs (bonds, commodities, sectors,
intl, vol, buy-write, etc.) + continuous futures symbols. Not literally every
listed security worldwide — Yahoo Finance limits + runtime.

Data: yfinance daily adjusted close.
"""

from __future__ import annotations

import io
import sys
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

MIN_BARS = 55

# --- Curated: ETFs + a few CEFs / proxies (bonds/commodities as ETFs) ---
ETF_AND_MORE = [
    # Broad equity
    "VTI",
    "VOO",
    "SPY",
    "QQQ",
    "QQQM",
    "IWM",
    "DIA",
    "VT",
    "VXUS",
    "VEA",
    "VWO",
    "IEFA",
    "EEM",
    "EFA",
    "ACWI",
    "URTH",
    # Factors / style
    "VUG",
    "VTV",
    "IWF",
    "IWD",
    "IJH",
    "IJR",
    "VB",
    "VO",
    "AVUV",
    "DFSV",
    "USMV",
    "QUAL",
    "MTUM",
    "SIZE",
    "VLUE",
    # Sectors
    "XLK",
    "XLF",
    "XLE",
    "XLV",
    "XLI",
    "XLY",
    "XLP",
    "XLU",
    "XLRE",
    "XLB",
    "XLC",
    "SMH",
    "SOXX",
    "XBI",
    "IBB",
    "XOP",
    "GDX",
    "GDXJ",
    "SIL",
    "URA",
    # Bonds / rates
    "BND",
    "AGG",
    "TLT",
    "IEF",
    "SHY",
    "TIP",
    "LQD",
    "HYG",
    "JNK",
    "EMB",
    "MUB",
    "BIL",
    "SGOV",
    "SPTL",
    "SPTS",
    "GOVT",
    "VGIT",
    "VGLT",
    "BNDX",
    "IAGG",
    # Commodities / metals / ag
    "GLD",
    "IAU",
    "GLDM",
    "SLV",
    "PPLT",
    "PALL",
    "DBC",
    "PDBC",
    "USO",
    "UNG",
    "WEAT",
    "CORN",
    "SOYB",
    "CPER",
    "PICK",
    # REITs / infra
    "VNQ",
    "SCHH",
    "REET",
    "ICF",
    # Vol / alt / income proxies
    "VXX",
    "SVXY",
    "JEPI",
    "JEPQ",
    "QYLD",
    "XYLD",
    # Crypto-related equities/ETFs
    "BITO",
    "IBIT",
    "GBTC",
    "COIN",
    "MSTR",
]

# Continuous futures (Yahoo-style)
FUTURES = [
    "ES=F",
    "NQ=F",
    "RTY=F",
    "YM=F",
    "CL=F",
    "NG=F",
    "RB=F",
    "HO=F",
    "GC=F",
    "SI=F",
    "HG=F",
    "PL=F",
    "PA=F",
    "ZB=F",
    "ZN=F",
    "ZF=F",
    "ZT=F",
    "6E=F",
    "6J=F",
    "6B=F",
    "DX-Y.NYB",
]

# Small sample of open-end mutual funds (Yahoo tickers vary)
MUTUAL_FUNDS = [
    "VTSAX",
    "VFIAX",
    "FXAIX",
    "SWPPX",
    "VBMFX",
    "VBTLX",
]


def load_sp500() -> list[str]:
    url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
    import urllib.request

    raw = urllib.request.urlopen(url, timeout=45).read()
    df = pd.read_csv(io.StringIO(raw.decode()))
    return df["Symbol"].str.replace(".", "-", regex=False).tolist()


def load_nasdaq100() -> list[str]:
    import urllib.request

    # Wikipedia table
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    try:
        tables = pd.read_html(url)
        for t in tables:
            if "Ticker" in t.columns:
                return t["Ticker"].astype(str).str.replace(".", "-", regex=False).tolist()
            if "Symbol" in t.columns:
                return t["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
    except Exception:
        pass
    return []


def unique_tickers(lists: list[list[str]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for x in lst:
            x = str(x).strip().upper()
            if x and x not in seen:
                seen.add(x)
                out.append(x)
    return out


def golden_cross_20_50(close: pd.Series) -> tuple[bool, pd.Timestamp | None]:
    """Bullish cross on last bar."""
    c = close.dropna()
    if len(c) < MIN_BARS:
        return False, None
    sma20 = c.rolling(20).mean()
    sma50 = c.rolling(50).mean()
    s20 = sma20.iloc[-1]
    s50 = sma50.iloc[-1]
    s20p = sma20.iloc[-2]
    s50p = sma50.iloc[-2]
    if np.isnan(s20) or np.isnan(s50) or np.isnan(s20p) or np.isnan(s50p):
        return False, None
    crossed = (s20 > s50) and (s20p <= s50p)
    return bool(crossed), c.index[-1]


def process_batch(tickers: list[str], start, end) -> list[tuple[str, pd.Timestamp]]:
    hits: list[tuple[str, pd.Timestamp]] = []
    if not tickers:
        return hits
    try:
        data = yf.download(
            tickers,
            start=start,
            end=end,
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
            group_by="column",
        )
    except Exception:
        return hits
    if not isinstance(data.columns, pd.MultiIndex):
        return hits
    closes = data["Close"]
    for sym in closes.columns:
        ser = closes[sym]
        ok, dt = golden_cross_20_50(ser)
        if ok and dt is not None:
            hits.append((sym, dt))
    return hits


def main() -> None:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=400)

    spx = load_sp500()
    ndx = load_nasdaq100()
    universe = unique_tickers(
        [spx, ndx, ETF_AND_MORE, FUTURES, MUTUAL_FUNDS]
    )
    print(f"Universe size (deduped): {len(universe)}")
    print(f"Cross rule: SMA(20) crosses ABOVE SMA(50) on latest bar vs prior bar")
    print(f"Data range request: {start} -> {end}\n")

    all_hits: list[tuple[str, pd.Timestamp]] = []
    batch_size = 60
    for i in range(0, len(universe), batch_size):
        batch = universe[i : i + batch_size]
        all_hits.extend(process_batch(batch, start, end))
        sys.stdout.write(f"\r  Batches {i // batch_size + 1}/{(len(universe) + batch_size - 1) // batch_size}")
        sys.stdout.flush()

    print("\n")
    if not all_hits:
        print("No golden crosses (20/50) found on the latest session in this universe.")
        return

    all_hits.sort(key=lambda x: x[0])
    print(f"=== Hits: {len(all_hits)} ===\n")
    for sym, dt in all_hits:
        print(f"  {sym}\t(as of {dt.date()})")

    print(
        "\nNotes:\n"
        "- 'Latest bar' = last trading day in Yahoo data (may lag calendar 'today').\n"
        "- Mutual funds often update slowly; many symbols may have no usable daily series.\n"
        "- Futures use continuous symbols; rolls distort levels vs front-month reality.\n"
        "- This is not an exhaustive global listing database."
    )


if __name__ == "__main__":
    main()
