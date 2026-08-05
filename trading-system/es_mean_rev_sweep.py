#!/usr/bin/env python3
"""
ES Mini — EMA20/61 Mean Reversion Parameter Sweep
Fetches bars once (caches to parquet), then grid-searches:
  - atr_stop_mult  : ATR multiplier for stop distance
  - min_dev_pct    : min % price must deviate from EMA20 before entry is valid
Results sorted by Sharpe ratio.
"""

import sys
import time
import threading
import warnings
import logging
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logging.getLogger("ibapi").setLevel(logging.CRITICAL)

# ── Parameters ────────────────────────────────────────────────────────────────
IB_HOST        = "127.0.0.1"
IB_PORT        = 4001
IB_CLIENT_ID   = 21
DAYS_BACK      = 60

EMA_FAST       = 20
EMA_SLOW       = 61
ATR_PERIOD     = 14

ES_MULTIPLIER  = 50
COMMISSION     = 4.50
INITIAL_CAPITAL = 25_000
NUM_CONTRACTS  = 1

BARS_CACHE     = Path("es_bars_5min_60d.csv")
OUTPUT_CSV     = "es_mean_rev_sweep_results.csv"
INFO_CODES     = {2104, 2106, 2107, 2108, 2119, 2158}

# ── Sweep grid ────────────────────────────────────────────────────────────────
ATR_STOP_MULTS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
MIN_DEV_PCTS   = [0.0, 0.15, 0.30, 0.50, 0.75]   # min % deviation from EMA20


# ── IB Fetcher ────────────────────────────────────────────────────────────────

def fetch_bars() -> pd.DataFrame:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract

    class IBFetcher(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self._bars: dict = {}
            self._done: dict = {}
            self._contract_details: list = []
            self._cd_done = threading.Event()

        def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
            if errorCode not in INFO_CODES:
                print(f"  IB error {errorCode}: {errorString}")

        def nextValidId(self, orderId): pass

        def contractDetails(self, reqId, details):
            self._contract_details.append(details.contract)

        def contractDetailsEnd(self, reqId):
            self._cd_done.set()

        def historicalData(self, reqId, bar):
            self._bars.setdefault(reqId, []).append({
                "date": bar.date, "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "volume": bar.volume,
            })

        def historicalDataEnd(self, reqId, start, end):
            if reqId in self._done:
                self._done[reqId].set()

        def fetch_chunk(self, req_id, contract, end_str, duration):
            self._bars[req_id] = []
            self._done[req_id] = threading.Event()
            self.reqHistoricalData(
                reqId=req_id, contract=contract, endDateTime=end_str,
                durationStr=duration, barSizeSetting="5 mins",
                whatToShow="TRADES", useRTH=1, formatDate=1,
                keepUpToDate=False, chartOptions=[],
            )
            ok = self._done[req_id].wait(timeout=90)
            rows = self._bars.pop(req_id, [])
            del self._done[req_id]
            if not ok:
                print(f"  Warning: req {req_id} timed out ({len(rows)} bars)")
            return pd.DataFrame(rows)

    app = IBFetcher()
    app.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    threading.Thread(target=app.run, daemon=True).start()
    time.sleep(3)

    if not app.isConnected():
        sys.exit(f"Cannot connect to IB Gateway at {IB_HOST}:{IB_PORT}.")
    print(f"Connected to IB Gateway  port={IB_PORT}")

    lookup = Contract()
    lookup.symbol = "ES"; lookup.secType = "FUT"
    lookup.exchange = "CME"; lookup.currency = "USD"
    app.reqContractDetails(50, lookup)
    app._cd_done.wait(timeout=20)

    cutoff_lo = (datetime.utcnow() - timedelta(days=DAYS_BACK + 5)).strftime("%Y%m%d")
    cutoff_hi = (datetime.utcnow() + timedelta(days=120)).strftime("%Y%m%d")
    contracts = sorted(
        [c for c in app._contract_details
         if cutoff_lo <= c.lastTradeDateOrContractMonth[:8] <= cutoff_hi],
        key=lambda c: c.lastTradeDateOrContractMonth,
    )
    print(f"Found {len(contracts)} ES contract(s) for {DAYS_BACK}-day window")

    today_str = datetime.utcnow().strftime("%Y%m%d")
    all_dfs = []
    for idx, contract in enumerate(contracts):
        exp = contract.lastTradeDateOrContractMonth[:8]
        end_str = "" if exp >= today_str else exp + " 23:59:59"
        print(f"  [{idx+1}/{len(contracts)}] ES {exp}  end={end_str or 'now'} ...", end=" ", flush=True)
        df_chunk = app.fetch_chunk(200 + idx, contract, end_str, "2 M")
        if df_chunk.empty:
            print("no data")
        else:
            all_dfs.append(df_chunk)
            print(f"{len(df_chunk)} bars")
        if idx < len(contracts) - 1:
            time.sleep(12)

    app.disconnect()
    if not all_dfs:
        sys.exit("No data received.")

    raw = (pd.concat(all_dfs)
             .drop_duplicates(subset="date")
             .sort_values("date")
             .reset_index(drop=True))
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.set_index("date")

    cutoff_dt = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=DAYS_BACK)
    raw = raw[raw.index >= cutoff_dt]
    print(f"Bars after trim: {len(raw)}  ({raw.index[0].date()} → {raw.index[-1].date()})")
    return raw


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    df["atr"] = pd.concat([hl, hcp, lcp], axis=1).max(axis=1).ewm(span=ATR_PERIOD, adjust=False).mean()
    return df


# ── Single-run Backtest ───────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame, atr_stop_mult: float, min_dev_pct: float) -> dict:
    warmup   = max(EMA_FAST, EMA_SLOW, ATR_PERIOD)
    trades   = []
    position = None
    min_dev  = min_dev_pct / 100.0

    for i in range(warmup, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]
        close = row["close"]
        ema_f = row["ema_fast"]
        ema_s = row["ema_slow"]
        atr   = row["atr"]
        pef   = prev["ema_fast"]
        pes   = prev["ema_slow"]

        if position is not None:
            side        = position["side"]
            entry_price = position["entry_price"]
            stop_price  = position["stop_price"]
            bars_held   = i - position["entry_idx"]
            golden = pef <= pes and ema_f > ema_s
            death  = pef >= pes and ema_f < ema_s

            exit_reason = None
            if side == "long":
                if close <= stop_price:          exit_reason = "atr_stop"
                elif death:                      exit_reason = "trend_flip"
                elif close >= ema_f:             exit_reason = "mean_reversion"
            else:
                if close >= stop_price:          exit_reason = "atr_stop"
                elif golden:                     exit_reason = "trend_flip"
                elif close <= ema_f:             exit_reason = "mean_reversion"

            if exit_reason:
                pts = (close - entry_price) if side == "long" else (entry_price - close)
                pnl = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
                trades.append({"side": side, "pts": pts, "pnl": pnl, "exit_reason": exit_reason, "bars_held": bars_held})
                position = None

        if position is None:
            uptrend   = ema_f > ema_s
            downtrend = ema_f < ema_s
            dev_long  = (ema_f - close) / ema_f  # positive when close < ema_f
            dev_short = (close - ema_f) / ema_f  # positive when close > ema_f

            if uptrend and close < ema_f and dev_long >= min_dev:
                position = {"side": "long",  "entry_price": close,
                            "stop_price": close - atr_stop_mult * atr,
                            "entry_idx": i}
            elif downtrend and close > ema_f and dev_short >= min_dev:
                position = {"side": "short", "entry_price": close,
                            "stop_price": close + atr_stop_mult * atr,
                            "entry_idx": i}

    if position is not None:
        side  = position["side"]
        close = df.iloc[-1]["close"]
        pts   = (close - position["entry_price"]) if side == "long" else (position["entry_price"] - close)
        pnl   = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
        trades.append({"side": side, "pts": pts, "pnl": pnl,
                        "exit_reason": "end_of_data",
                        "bars_held": len(df) - 1 - position["entry_idx"]})

    if not trades:
        return {"n": 0, "win_rate": 0, "pf": np.nan, "net_pnl": 0,
                "sharpe": np.nan, "max_dd": 0, "max_dd_pct": 0,
                "avg_win": 0, "avg_loss": 0, "avg_pts": 0}

    pnl_s   = pd.Series([t["pnl"] for t in trades])
    wins    = (pnl_s > 0).sum()
    losses  = (pnl_s < 0).sum()
    cum_pnl = pnl_s.cumsum()
    equity  = INITIAL_CAPITAL + cum_pnl
    hwm     = equity.cummax()
    dd      = equity - hwm
    max_dd  = dd.min()
    max_dd_pct = (max_dd / hwm.loc[dd.idxmin()]) * 100

    n           = len(pnl_s)
    mean_pnl    = pnl_s.mean()
    std_pnl     = pnl_s.std(ddof=1) if n > 1 else np.nan
    sample_days = max((df.index[-1] - df.index[0]).days, 1)
    tpy         = n / (sample_days / 252)
    sharpe      = (mean_pnl / std_pnl) * np.sqrt(tpy) if std_pnl and std_pnl > 0 else np.nan
    pf          = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) if losses and pnl_s[pnl_s < 0].sum() != 0 else np.nan
    avg_pts     = pd.Series([t["pts"] for t in trades]).mean()

    return {
        "n":         n,
        "win_rate":  wins / n * 100,
        "pf":        round(float(pf), 3) if not np.isnan(pf) else np.nan,
        "net_pnl":   round(float(pnl_s.sum()), 2),
        "sharpe":    round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        "max_dd":    round(float(max_dd), 2),
        "max_dd_pct": round(float(max_dd_pct), 2),
        "avg_win":   round(float(pnl_s[pnl_s > 0].mean()), 2) if wins else 0,
        "avg_loss":  round(float(pnl_s[pnl_s < 0].mean()), 2) if losses else 0,
        "avg_pts":   round(float(avg_pts), 3),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # Load or fetch bars
    if BARS_CACHE.exists():
        print(f"Loading cached bars from {BARS_CACHE}")
        bars = pd.read_csv(BARS_CACHE, index_col=0, parse_dates=True)
    else:
        bars = fetch_bars()
        bars.to_csv(BARS_CACHE)
        print(f"Bars cached → {BARS_CACHE}")

    bars = add_indicators(bars)

    total = len(ATR_STOP_MULTS) * len(MIN_DEV_PCTS)
    print(f"\nRunning {total} combinations  "
          f"(ATR mults: {ATR_STOP_MULTS}  |  min dev %: {MIN_DEV_PCTS})\n")

    rows = []
    for idx, (atr_mult, min_dev) in enumerate(product(ATR_STOP_MULTS, MIN_DEV_PCTS), 1):
        m = run_backtest(bars, atr_mult, min_dev)
        rows.append({
            "atr_stop_mult": atr_mult,
            "min_dev_pct":   min_dev,
            **m,
        })
        sharpe_str = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        print(
            f"  [{idx:>2}/{total}]  ATR×{atr_mult:.1f}  dev≥{min_dev:.2f}%"
            f"  │  trades={m['n']:>3}  wr={m['win_rate']:>5.1f}%"
            f"  pf={m['pf'] if not np.isnan(m['pf']) else 'n/a':>5}"
            f"  pnl=${m['net_pnl']:>+9,.0f}"
            f"  sharpe={sharpe_str}"
            f"  dd={m['max_dd_pct']:>+6.1f}%"
        )

    results = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    results.to_csv(OUTPUT_CSV, index=False)

    print(f"\n{'='*70}")
    print("  TOP 10 BY SHARPE RATIO")
    print(f"{'='*70}")
    top = results.head(10)
    for _, r in top.iterrows():
        pf_s     = f"{r['pf']:.2f}" if not np.isnan(r['pf']) else " n/a"
        sh_s     = f"{r['sharpe']:+.2f}" if not np.isnan(r['sharpe']) else "  n/a"
        print(
            f"  ATR×{r['atr_stop_mult']:.1f}  dev≥{r['min_dev_pct']:.2f}%"
            f"  │  n={int(r['n']):>3}  wr={r['win_rate']:>5.1f}%"
            f"  pf={pf_s:>5}  pnl=${r['net_pnl']:>+9,.0f}"
            f"  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%"
        )
    print(f"{'='*70}")
    print(f"\nFull results → {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
