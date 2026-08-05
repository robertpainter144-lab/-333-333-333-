#!/usr/bin/env python3
"""
ES Mini — 11-Month Mean Reversion Backtest
EMA 20 (fast) / EMA 61 (slow), 5-min RTH bars
Entries : first 90 min of each session only (09:30–10:59 ET)
Exits   : any time during RTH; force-close at 15:55 if still open
Sweep   : min_dev_pct × reversion_ratio
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

# ── Parameters ─────────────────────────────��───────────────────────────��──────
IB_HOST       = "127.0.0.1"
IB_PORT       = 4001
IB_CLIENT_ID  = 23

EMA_FAST      = 20
EMA_SLOW      = 61
ATR_PERIOD    = 14
ATR_STOP_MULT = 2.0

ENTRY_START   = (9, 30)    # ET — first bar allowed for new entries
ENTRY_END     = (11, 0)    # ET — entries cut off at/after this time
FORCE_CLOSE   = (15, 55)   # ET — force-exit any open position

ES_MULTIPLIER = 50
COMMISSION    = 4.50
INITIAL_CAP   = 25_000
NUM_CONTRACTS = 1

BARS_CACHE    = Path("es_bars_11m_5min.csv")
SWEEP_CSV     = "es_open_range_sweep.csv"
DETAIL_CSV    = "es_open_range_monthly.csv"
INFO_CODES    = {2104, 2106, 2107, 2108, 2119, 2158}

# ── Sweep grid ──────────────────────────────────���─────────────────────────���───
MIN_DEV_PCTS = [0.20, 0.30, 0.40, 0.50, 0.60, 0.70]
REV_RATIOS   = [0.50, 0.65, 0.80, 1.00]


# ── IB Data Fetch ──────────────────────────────���───────────────────────────��──

# Front-month ES contracts covering the last ~11 months.
# Each entry: (lastTradeDateOrContractMonth, end_datetime_str)
# Active contract uses "" for end; expired use exact expiry date.
QUARTERLY_CONTRACTS = [
    ("20250620", "20250620 16:00:00"),  # ESM25 — expired Jun 20 2025
    ("20250919", "20250919 16:00:00"),  # ESU25 — expired Sep 19 2025
    ("20251219", "20251219 16:00:00"),  # ESZ25 — expired Dec 19 2025
    ("20260321", "20260321 16:00:00"),  # ESH26 — expired Mar 21 2026
    ("202606",   ""),                   # ESM26 — active front month
]


def fetch_bars() -> pd.DataFrame:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract

    class IBFetcher(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self._bars: dict = {}
            self._done: dict = {}

        def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
            if errorCode not in INFO_CODES:
                print(f"  IB {errorCode}: {errorString}")

        def nextValidId(self, orderId): pass

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
                print(f"    timeout ({len(rows)} bars so far)")
            return pd.DataFrame(rows)

    app = IBFetcher()
    app.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
    threading.Thread(target=app.run, daemon=True).start()
    time.sleep(3)
    if not app.isConnected():
        sys.exit(f"Cannot connect to IB Gateway at {IB_HOST}:{IB_PORT}.")
    print(f"Connected  port={IB_PORT}")

    all_dfs = []
    for idx, (exp_date, end_str) in enumerate(QUARTERLY_CONTRACTS):
        contract          = Contract()
        contract.symbol   = "ES"
        contract.secType  = "FUT"
        contract.exchange = "CME"
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = exp_date

        label = f"ES {exp_date}"
        print(f"  [{idx+1}/{len(QUARTERLY_CONTRACTS)}] {label}  end={end_str or 'now'} ...", end=" ", flush=True)
        df_chunk = app.fetch_chunk(200 + idx, contract, end_str, "6 M")

        if df_chunk.empty:
            print("no data")
        else:
            all_dfs.append(df_chunk)
            print(f"{len(df_chunk):,} bars")

        if idx < len(QUARTERLY_CONTRACTS) - 1:
            time.sleep(11)

    app.disconnect()
    if not all_dfs:
        sys.exit("No data received from IB Gateway.")

    raw = (
        pd.concat(all_dfs)
          .drop_duplicates(subset="date")
          .sort_values("date")
          .reset_index(drop=True)
    )
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.set_index("date")

    # Trim to 11 months
    cutoff_dt = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=335)
    raw = raw[raw.index >= cutoff_dt]
    print(f"\nTotal bars: {len(raw):,}  ({raw.index[0].date()} → {raw.index[-1].date()})")
    return raw


# ── Indicators ──────────────────────��─────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    df["atr"] = pd.concat([hl, hcp, lcp], axis=1).max(axis=1).ewm(span=ATR_PERIOD, adjust=False).mean()
    return df


# ── Backtest ─────────────────────────────��───────────────────────────��────────

def bar_minutes(ts: pd.Timestamp) -> int:
    """Hour*60 + minute of the bar timestamp (ET, naive)."""
    return ts.hour * 60 + ts.minute


ENTRY_START_MINS = ENTRY_START[0] * 60 + ENTRY_START[1]   # 570
ENTRY_END_MINS   = ENTRY_END[0]   * 60 + ENTRY_END[1]     # 660
FORCE_CLOSE_MINS = FORCE_CLOSE[0] * 60 + FORCE_CLOSE[1]   # 955


def run_backtest(df: pd.DataFrame, min_dev_pct: float, rev_ratio: float) -> list[dict]:
    warmup   = max(EMA_FAST, EMA_SLOW, ATR_PERIOD) + 5
    trades   = []
    position = None
    min_dev  = min_dev_pct / 100.0

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        prev  = df.iloc[i - 1]
        ts    = df.index[i]
        mins  = bar_minutes(ts)
        close = float(row["close"])
        ema_f = float(row["ema_fast"])
        ema_s = float(row["ema_slow"])
        atr   = float(row["atr"])
        pef   = float(prev["ema_fast"])
        pes   = float(prev["ema_slow"])

        # ── Manage open position ────────��──────────────────────���──────────────
        if position is not None:
            side        = position["side"]
            entry_price = position["entry_price"]
            entry_dev   = position["entry_dev"]
            stop_price  = position["stop_price"]
            bars_held   = i - position["entry_idx"]
            golden      = pef <= pes and ema_f > ema_s
            death       = pef >= pes and ema_f < ema_s
            target      = (entry_price + rev_ratio * entry_dev) if side == "long" \
                     else (entry_price - rev_ratio * entry_dev)

            exit_reason = None
            if mins >= FORCE_CLOSE_MINS:
                exit_reason = "force_close_eod"
            elif side == "long":
                if   close <= stop_price: exit_reason = "atr_stop"
                elif death:               exit_reason = "trend_flip"
                elif close >= target:     exit_reason = "reversion_target"
            else:
                if   close >= stop_price: exit_reason = "atr_stop"
                elif golden:              exit_reason = "trend_flip"
                elif close <= target:     exit_reason = "reversion_target"

            if exit_reason:
                pts = (close - entry_price) if side == "long" else (entry_price - close)
                pnl = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
                trades.append({
                    "entry_time":  position["entry_time"],
                    "exit_time":   ts,
                    "side":        side,
                    "entry_price": round(entry_price, 2),
                    "exit_price":  round(close, 2),
                    "bars_held":   bars_held,
                    "pts":         round(pts, 2),
                    "pnl":         round(pnl, 2),
                    "exit_reason": exit_reason,
                })
                position = None

        # ── New entry — only within first 90 min ─────────────────────────────
        if position is None and ENTRY_START_MINS <= mins < ENTRY_END_MINS:
            uptrend   = ema_f > ema_s
            downtrend = ema_f < ema_s
            dev_long  = (ema_f - close) / ema_f
            dev_short = (close - ema_f) / ema_f

            if uptrend and close < ema_f and dev_long >= min_dev:
                position = {
                    "side": "long",  "entry_price": close,
                    "entry_dev": ema_f - close,
                    "stop_price": close - ATR_STOP_MULT * atr,
                    "entry_idx": i, "entry_time": ts,
                }
            elif downtrend and close > ema_f and dev_short >= min_dev:
                position = {
                    "side": "short", "entry_price": close,
                    "entry_dev": close - ema_f,
                    "stop_price": close + ATR_STOP_MULT * atr,
                    "entry_idx": i, "entry_time": ts,
                }

    # Force-close anything open at end of data
    if position is not None:
        side  = position["side"]
        close = float(df.iloc[-1]["close"])
        pts   = (close - position["entry_price"]) if side == "long" else (position["entry_price"] - close)
        pnl   = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
        trades.append({
            "entry_time": position["entry_time"], "exit_time": df.index[-1],
            "side": side, "entry_price": round(float(position["entry_price"]), 2),
            "exit_price": round(close, 2),
            "bars_held": len(df) - 1 - position["entry_idx"],
            "pts": round(pts, 2), "pnl": round(pnl, 2),
            "exit_reason": "end_of_data",
        })
    return trades


# ── Metrics ────────────────────────────────���──────────────────────────���───────

def metrics(trades: list[dict], label: str = "") -> dict:
    if not trades:
        return {"label": label, "n": 0, "longs": 0, "shorts": 0,
                "win_rate": 0, "pf": np.nan, "net_pnl": 0,
                "sharpe": np.nan, "max_dd": 0, "max_dd_pct": 0,
                "avg_win": 0, "avg_loss": 0, "avg_pts": 0,
                "best": 0, "worst": 0}

    pnl_s  = pd.Series([t["pnl"] for t in trades])
    wins   = (pnl_s > 0).sum()
    losses = (pnl_s < 0).sum()
    equity = INITIAL_CAP + pnl_s.cumsum()
    hwm    = equity.cummax()
    dd     = equity - hwm
    i_min  = dd.idxmin()
    max_dd     = float(dd.min())
    max_dd_pct = float(max_dd / hwm.loc[i_min] * 100) if hwm.loc[i_min] else 0

    n        = len(pnl_s)
    mean_pnl = float(pnl_s.mean())
    std_pnl  = float(pnl_s.std(ddof=1)) if n > 1 else np.nan
    t_series = pd.Series([t["entry_time"] for t in trades])
    days     = max((t_series.max() - t_series.min()).days, 1)
    tpy      = n / (days / 252)
    sharpe   = (mean_pnl / std_pnl) * np.sqrt(tpy) if std_pnl and std_pnl > 0 else np.nan
    pf       = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
               if losses and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    return {
        "label":      label,
        "n":          n,
        "longs":      sum(1 for t in trades if t["side"] == "long"),
        "shorts":     sum(1 for t in trades if t["side"] == "short"),
        "win_rate":   round(wins / n * 100, 1),
        "pf":         round(float(pf), 2)      if not np.isnan(pf)     else np.nan,
        "net_pnl":    round(float(pnl_s.sum()), 0),
        "sharpe":     round(float(sharpe), 2)  if not np.isnan(sharpe) else np.nan,
        "max_dd":     round(max_dd, 0),
        "max_dd_pct": round(max_dd_pct, 1),
        "avg_win":    round(float(pnl_s[pnl_s > 0].mean()), 0) if wins   else 0,
        "avg_loss":   round(float(pnl_s[pnl_s < 0].mean()), 0) if losses else 0,
        "avg_pts":    round(float(pd.Series([t["pts"] for t in trades]).mean()), 2),
        "best":       round(float(pnl_s.max()), 0),
        "worst":      round(float(pnl_s.min()), 0),
    }


def monthly_breakdown(trades: list[dict]) -> list[dict]:
    by_month: dict[str, list] = {}
    for t in trades:
        key = t["entry_time"].strftime("%Y-%m")
        by_month.setdefault(key, []).append(t)
    return [metrics(by_month[k], label=k) for k in sorted(by_month)]


def print_table(rows: list[dict], title: str) -> None:
    print(f"\n  {title}")
    hdr = (f"  {'Period':<9} {'n':>4} {'L/S':>7} {'Win%':>6} {'PF':>5} "
           f"{'PnL':>9} {'Sharpe':>7} {'MaxDD':>8} {'DD%':>6} "
           f"{'AvgW':>7} {'AvgL':>7} {'Pts':>6} {'Best':>8} {'Worst':>8}")
    print(hdr)
    print("  " + "─" * 114)
    for r in rows:
        pf_s = f"{r['pf']:.2f}"       if not (isinstance(r['pf'],     float) and np.isnan(r['pf']))     else " n/a"
        sh_s = f"{r['sharpe']:+.2f}"  if not (isinstance(r['sharpe'], float) and np.isnan(r['sharpe'])) else "  n/a"
        lbl  = r['label']
        print(
            f"  {lbl:<9} {r['n']:>4}  "
            f"{r['longs']:>2}L/{r['shorts']:<2}S "
            f"{r['win_rate']:>5.1f}% {pf_s:>5} "
            f"${r['net_pnl']:>+8,.0f} {sh_s:>7} "
            f"${r['max_dd']:>7,.0f} {r['max_dd_pct']:>+5.1f}% "
            f"${r['avg_win']:>+6,.0f} ${r['avg_loss']:>+6,.0f} "
            f"{r['avg_pts']:>+5.2f}p "
            f"${r['best']:>+7,.0f} ${r['worst']:>+7,.0f}"
        )
    print("  " + "─" * 114)


# ── Main ─────────────────────────────────────────────────────────���────────────

def main():
    # 1. Data
    if BARS_CACHE.exists():
        print(f"Loading cached bars → {BARS_CACHE}")
        raw = pd.read_csv(BARS_CACHE, index_col=0, parse_dates=True)
        print(f"  {len(raw):,} bars  ({raw.index[0].date()} → {raw.index[-1].date()})")
    else:
        raw = fetch_bars()
        raw.to_csv(BARS_CACHE)
        print(f"Cached → {BARS_CACHE}")

    df = add_indicators(raw)

    total = len(MIN_DEV_PCTS) * len(REV_RATIOS)
    print(
        f"\nEMA{EMA_FAST}/{EMA_SLOW}  ATR{ATR_PERIOD} stop×{ATR_STOP_MULT}"
        f"  |  Entry window: {ENTRY_START[0]:02d}:{ENTRY_START[1]:02d}–{ENTRY_END[0]:02d}:{ENTRY_END[1]:02d} ET"
        f"  |  Force-close: {FORCE_CLOSE[0]:02d}:{FORCE_CLOSE[1]:02d} ET"
        f"\nSweep: {total} combinations\n"
        f"  min_dev_pct : {MIN_DEV_PCTS}\n"
        f"  rev_ratio   : {REV_RATIOS}\n"
    )

    # 2. Sweep
    sweep_rows   = []
    trades_store = {}

    for idx, (min_dev, rev) in enumerate(product(MIN_DEV_PCTS, REV_RATIOS), 1):
        trs = run_backtest(df, min_dev, rev)
        m   = metrics(trs)
        key = f"dev≥{min_dev:.2f}%  rev={rev:.2f}"
        sweep_rows.append({"min_dev_pct": min_dev, "rev_ratio": rev,
                            **{k: v for k, v in m.items() if k != "label"}})
        trades_store[key] = trs

        sh_s = f"{m['sharpe']:+.2f}" if not (isinstance(m['sharpe'], float) and np.isnan(m['sharpe'])) else "  n/a"
        pf_s = f"{m['pf']:.2f}"      if not (isinstance(m['pf'],     float) and np.isnan(m['pf']))     else " n/a"
        print(
            f"  [{idx:>2}/{total}]  {key}"
            f"  │  n={m['n']:>4}  wr={m['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+9,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%"
        )

    results = pd.DataFrame(sweep_rows).sort_values("sharpe", ascending=False)
    results.to_csv(SWEEP_CSV, index=False)

    # 3. Summary table
    MIN_TRADES = max(5, int(len(raw) / (78 * 5)))   # ~1 trade per 5 trading days minimum
    eligible   = results[results["n"] >= MIN_TRADES]

    print(f"\n{'='*90}")
    print(f"  SWEEP SUMMARY  EMA{EMA_FAST}/{EMA_SLOW}  |  Entry 09:30–11:00 ET only  (n ≥ {MIN_TRADES})")
    print(f"{'='*90}")
    print(f"  {'Config':<24} {'n':>4} {'Win%':>6} {'PF':>5} {'PnL':>10} {'Sharpe':>7} {'MaxDD':>8} {'DD%':>6}")
    print("  " + "─" * 72)
    for _, r in eligible.iterrows():
        sh_s = f"{r['sharpe']:+.2f}" if not np.isnan(r['sharpe']) else "  n/a"
        pf_s = f"{r['pf']:.2f}"      if not np.isnan(r['pf'])     else " n/a"
        print(
            f"  dev≥{r['min_dev_pct']:.2f}%  rev={r['rev_ratio']:.2f}  "
            f"{int(r['n']):>4}  {r['win_rate']:>5.1f}%  {pf_s:>5}"
            f"  ${r['net_pnl']:>+9,.0f}  {sh_s:>7}  ${r['max_dd']:>7,.0f}  {r['max_dd_pct']:>+5.1f}%"
        )
    print(f"{'='*90}")

    # 4. Per-month breakdown for top 3 configs
    top3 = eligible.head(3)
    detail_rows = []
    print(f"\n{'='*117}")
    print(f"  PER-MONTH BREAKDOWN — TOP 3 CONFIGS BY SHARPE")
    print(f"{'='*117}")

    for _, r in top3.iterrows():
        key   = f"dev≥{r['min_dev_pct']:.2f}%  rev={r['rev_ratio']:.2f}"
        trs   = trades_store[key]
        mo    = monthly_breakdown(trs)
        total = metrics(trs, label="TOTAL")
        sh_s  = f"{total['sharpe']:+.2f}" if not (isinstance(total['sharpe'], float) and np.isnan(total['sharpe'])) else "n/a"
        print_table(
            mo + [total],
            title=f"EMA{EMA_FAST}/{EMA_SLOW}  {key}  │  n={total['n']}  sharpe={sh_s}  pnl=${total['net_pnl']:+,.0f}",
        )
        for row in mo + [total]:
            detail_rows.append({"config": key, **row})

    pd.DataFrame(detail_rows).to_csv(DETAIL_CSV, index=False)

    # 5. Exit reason breakdown for best config
    best_key = f"dev≥{top3.iloc[0]['min_dev_pct']:.2f}%  rev={top3.iloc[0]['rev_ratio']:.2f}"
    best_trs = trades_store[best_key]
    if best_trs:
        reasons = pd.Series([t["exit_reason"] for t in best_trs]).value_counts()
        print(f"\n  Exit breakdown — best config ({best_key}):")
        for reason, cnt in reasons.items():
            print(f"    {reason:<25} {cnt:>4}")

    print(f"\nSweep results  → {SWEEP_CSV}")
    print(f"Monthly detail → {DETAIL_CSV}")


if __name__ == "__main__":
    main()
