#!/usr/bin/env python3
# DISABLED: failed OOS validation 2026-06-14
# Sharpe 6.19 is overfit noise: only 57 days of data (not 5yr), n=8 trades,
# 0 trades in OOS window.  Best params (EMA20/50, min_dev=0.55%, rev_ratio=0.80)
# have NO out-of-sample edge.  Do NOT deploy.  Fix: fetch real 5yr IB data first.
"""
ES Mini — 5-Year Mean Reversion Backtest + Parameter Sweep
Data   : 5-min RTH TRADES bars via IB Gateway, looping quarterly ES contracts
Sweep  : EMA pairs × min_dev_pct × reversion_ratio
Output : Total stats + per-year breakdown for every config
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

# ── Connection ────────────────────────────────────────────────────────────────
IB_HOST       = "127.0.0.1"
IB_PORT       = 4001
IB_CLIENT_ID  = 22
YEARS_BACK    = 5

# ── Fixed strategy params ─────────────────────────────────────────────────────
ATR_PERIOD    = 14
ATR_STOP_MULT = 2.0
ES_MULTIPLIER = 50
COMMISSION    = 4.50
INITIAL_CAP   = 25_000
NUM_CONTRACTS = 1

# ── Sweep grid ────────────────────────────────────────────────────────────────
EMA_PAIRS      = [(20, 50), (20, 61), (25, 61)]
MIN_DEV_PCTS   = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
REV_RATIOS     = [0.65, 0.80, 1.00]

BARS_CACHE    = Path("es_bars_5min_60d.csv")
SWEEP_CSV     = "es_mean_rev_5yr_sweep.csv"
DETAIL_CSV    = "es_mean_rev_5yr_top_yearly.csv"
INFO_CODES    = {2104, 2106, 2107, 2108, 2119, 2158}


# ── Quarterly contract months for the last N years ────────────────────────────

def quarterly_months(years_back: int) -> list[str]:
    """Return YYYYMM strings for each H/M/U/Z contract back N years from today."""
    months = [3, 6, 9, 12]           # H, M, U, Z
    now    = datetime.utcnow()
    result = []
    for delta_yr in range(years_back + 1):
        yr = now.year - delta_yr
        for m in months:
            ym = f"{yr}{m:02d}"
            # Skip future contracts (current month + 1 is fine for active front month)
            if int(ym) <= int(now.strftime("%Y%m")) + 1:
                result.append(ym)
    return sorted(set(result))


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

    # Build explicit quarterly contract list — IB reqContractDetails only returns
    # active contracts, so we construct each expired contract by month code directly.
    contract_months = quarterly_months(YEARS_BACK)
    today_ym = datetime.utcnow().strftime("%Y%m")
    print(f"Fetching {len(contract_months)} quarterly contracts "
          f"({contract_months[0]} → {contract_months[-1]})\n")

    all_dfs = []
    for idx, ym in enumerate(contract_months):
        contract = Contract()
        contract.symbol   = "ES"
        contract.secType  = "FUT"
        contract.exchange = "CME"
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = ym

        # Active contract: empty end (latest bar). Expired: use last day of that month.
        if ym >= today_ym:
            end_str = ""
        else:
            # End at the last day of the contract month (IB will cap at actual expiry)
            yr, mo = int(ym[:4]), int(ym[4:])
            next_mo = datetime(yr, mo, 1) + timedelta(days=32)
            last_day = (next_mo.replace(day=1) - timedelta(days=1))
            end_str = last_day.strftime("%Y%m%d") + " 23:59:59"

        print(f"  [{idx+1:>2}/{len(contract_months)}] ES {ym}  ...", end=" ", flush=True)
        df_chunk = app.fetch_chunk(200 + idx, contract, end_str, "6 M")

        if df_chunk.empty:
            print("no data")
        else:
            all_dfs.append(df_chunk)
            print(f"{len(df_chunk):,} bars")

        if idx < len(contract_months) - 1:
            time.sleep(11)   # IB pacing

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

    cutoff_dt = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=YEARS_BACK * 365)
    raw = raw[raw.index >= cutoff_dt]
    print(f"\nTotal bars: {len(raw):,}  ({raw.index[0].date()} → {raw.index[-1].date()})")
    return raw


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    df["atr"] = pd.concat([hl, hcp, lcp], axis=1).max(axis=1).ewm(span=ATR_PERIOD, adjust=False).mean()
    return df


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame, min_dev_pct: float, rev_ratio: float) -> list[dict]:
    """Returns list of trade dicts (including entry_time for yearly slicing)."""
    warmup   = max(df.columns.tolist() and 61, ATR_PERIOD)  # conservative
    warmup   = 80   # enough for any EMA pair in our grid
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
            entry_dev   = position["entry_dev"]
            stop_price  = position["stop_price"]
            bars_held   = i - position["entry_idx"]
            golden = pef <= pes and ema_f > ema_s
            death  = pef >= pes and ema_f < ema_s

            target = (entry_price + rev_ratio * entry_dev) if side == "long" \
                else  (entry_price - rev_ratio * entry_dev)

            exit_reason = None
            if side == "long":
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
                    "entry_time": position["entry_time"],
                    "exit_time":  df.index[i],
                    "side":       side,
                    "entry_price": round(float(entry_price), 2),
                    "exit_price":  round(float(close), 2),
                    "bars_held":   bars_held,
                    "pts":         round(float(pts), 2),
                    "pnl":         round(pnl, 2),
                    "exit_reason": exit_reason,
                })
                position = None

        if position is None:
            uptrend   = ema_f > ema_s
            downtrend = ema_f < ema_s
            dev_long  = (ema_f - close) / ema_f
            dev_short = (close - ema_f) / ema_f

            if uptrend and close < ema_f and dev_long >= min_dev:
                position = {"side": "long",  "entry_price": close,
                            "entry_dev": ema_f - close,
                            "stop_price": close - ATR_STOP_MULT * atr,
                            "entry_idx": i, "entry_time": df.index[i]}
            elif downtrend and close > ema_f and dev_short >= min_dev:
                position = {"side": "short", "entry_price": close,
                            "entry_dev": close - ema_f,
                            "stop_price": close + ATR_STOP_MULT * atr,
                            "entry_idx": i, "entry_time": df.index[i]}

    if position is not None:
        side  = position["side"]
        close = df.iloc[-1]["close"]
        pts   = (close - position["entry_price"]) if side == "long" else (position["entry_price"] - close)
        pnl   = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
        trades.append({
            "entry_time": position["entry_time"], "exit_time": df.index[-1],
            "side": side, "entry_price": round(float(position["entry_price"]), 2),
            "exit_price": round(float(close), 2),
            "bars_held": len(df) - 1 - position["entry_idx"],
            "pts": round(float(pts), 2), "pnl": round(pnl, 2),
            "exit_reason": "end_of_data",
        })
    return trades


# ── Metrics helpers ───────────────────────────────────────────────────────────

def metrics_from_trades(trades: list[dict], label: str = "") -> dict:
    if not trades:
        return {"label": label, "n": 0, "longs": 0, "shorts": 0,
                "win_rate": 0, "pf": np.nan, "net_pnl": 0,
                "sharpe": np.nan, "max_dd": 0, "max_dd_pct": 0,
                "avg_win": 0, "avg_loss": 0, "avg_pts": 0,
                "best_trade": 0, "worst_trade": 0}

    pnl_s  = pd.Series([t["pnl"] for t in trades])
    wins   = (pnl_s > 0).sum()
    losses = (pnl_s < 0).sum()
    equity = INITIAL_CAP + pnl_s.cumsum()
    hwm    = equity.cummax()
    dd     = equity - hwm
    i_min  = dd.idxmin()
    max_dd     = dd.min()
    max_dd_pct = (max_dd / hwm.loc[i_min]) * 100 if hwm.loc[i_min] != 0 else 0

    n        = len(pnl_s)
    mean_pnl = pnl_s.mean()
    std_pnl  = pnl_s.std(ddof=1) if n > 1 else np.nan

    entry_times = pd.Series([t["entry_time"] for t in trades])
    sample_days = max((entry_times.max() - entry_times.min()).days, 1)
    tpy     = n / (sample_days / 252)
    sharpe  = (mean_pnl / std_pnl) * np.sqrt(tpy) if (std_pnl and std_pnl > 0) else np.nan
    pf      = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
              if losses and pnl_s[pnl_s < 0].sum() != 0 else np.nan
    longs  = sum(1 for t in trades if t["side"] == "long")
    shorts = sum(1 for t in trades if t["side"] == "short")

    return {
        "label":       label,
        "n":           n,
        "longs":       longs,
        "shorts":      shorts,
        "win_rate":    round(wins / n * 100, 1),
        "pf":          round(float(pf), 2)      if not np.isnan(pf)     else np.nan,
        "net_pnl":     round(float(pnl_s.sum()), 0),
        "sharpe":      round(float(sharpe), 2)  if not np.isnan(sharpe) else np.nan,
        "max_dd":      round(float(max_dd), 0),
        "max_dd_pct":  round(float(max_dd_pct), 1),
        "avg_win":     round(float(pnl_s[pnl_s > 0].mean()), 0) if wins   else 0,
        "avg_loss":    round(float(pnl_s[pnl_s < 0].mean()), 0) if losses else 0,
        "avg_pts":     round(float(pd.Series([t["pts"] for t in trades]).mean()), 2),
        "best_trade":  round(float(pnl_s.max()), 0),
        "worst_trade": round(float(pnl_s.min()), 0),
    }


def monthly_breakdown(trades: list[dict]) -> list[dict]:
    if not trades:
        return []
    by_month: dict[str, list] = {}
    for t in trades:
        key = t["entry_time"].strftime("%Y-%m")
        by_month.setdefault(key, []).append(t)
    rows = []
    for mo in sorted(by_month):
        m = metrics_from_trades(by_month[mo], label=mo)
        rows.append(m)
    return rows


def print_monthly(rows: list[dict], config_label: str) -> None:
    print(f"\n  {config_label}")
    print(f"  {'Month':<8} {'n':>4} {'L/S':>7} {'Win%':>6} {'PF':>5} "
          f"{'PnL':>9} {'Sharpe':>7} {'MaxDD':>8} {'DD%':>6} "
          f"{'AvgW':>7} {'AvgL':>7} {'AvgPts':>7} {'Best':>8} {'Worst':>8}")
    print("  " + "-" * 112)
    for r in rows:
        pf_s = f"{r['pf']:.2f}"      if not (isinstance(r['pf'],     float) and np.isnan(r['pf']))     else " n/a"
        sh_s = f"{r['sharpe']:+.2f}" if not (isinstance(r['sharpe'], float) and np.isnan(r['sharpe'])) else "  n/a"
        print(
            f"  {r['label']:<8} {r['n']:>4} "
            f"{r['longs']:>3}L/{r['shorts']:<3}S "
            f"{r['win_rate']:>5.1f}% {pf_s:>5} "
            f"${r['net_pnl']:>+8,.0f} {sh_s:>7} "
            f"${r['max_dd']:>7,.0f} {r['max_dd_pct']:>+5.1f}% "
            f"${r['avg_win']:>+6,.0f} ${r['avg_loss']:>+7,.0f} "
            f"{r['avg_pts']:>+6.2f}pts "
            f"${r['best_trade']:>+7,.0f} ${r['worst_trade']:>+8,.0f}"
        )
    print("  " + "-" * 112)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    # DISABLED: failed OOS validation 2026-06-14 — weight = 0, do not run live
    raise SystemExit(
        "ES mean-rev (EMA20/50, min_dev=0.55%, rev_ratio=0.80) is SHELVED: "
        "Sharpe 6.19 was overfit on 57d of data (n=8 trades, 0 OOS trades). "
        "Fetch real 5-year IB data before re-enabling."
    )

    # ── 1. Load or fetch bars ─────────────────────────────────────────────────
    if BARS_CACHE.exists():
        print(f"Loading cached bars → {BARS_CACHE}")
        raw = pd.read_csv(BARS_CACHE, index_col=0, parse_dates=True)
        print(f"  {len(raw):,} bars  ({raw.index[0].date()} → {raw.index[-1].date()})")
    else:
        raw = fetch_bars()
        raw.to_csv(BARS_CACHE)
        print(f"Cached → {BARS_CACHE}")

    total_combos = len(EMA_PAIRS) * len(MIN_DEV_PCTS) * len(REV_RATIOS)
    print(f"\nSweep: {total_combos} combinations over {YEARS_BACK} years\n"
          f"  EMA pairs    : {EMA_PAIRS}\n"
          f"  min_dev_pct  : {MIN_DEV_PCTS}\n"
          f"  rev_ratio    : {REV_RATIOS}\n")

    # ── 2. Sweep ──────────────────────────────────────────────────────────────
    sweep_rows = []
    all_trades_store = {}

    for idx, ((fast, slow), min_dev, rev) in enumerate(
        product(EMA_PAIRS, MIN_DEV_PCTS, REV_RATIOS), 1
    ):
        df  = add_indicators(raw, fast, slow)
        trs = run_backtest(df, min_dev, rev)
        m   = metrics_from_trades(trs)
        key = f"EMA{fast}/{slow}  dev≥{min_dev:.2f}%  rev={rev:.2f}"
        m["config"] = key
        sweep_rows.append({
            "ema_fast": fast, "ema_slow": slow,
            "min_dev_pct": min_dev, "rev_ratio": rev,
            **{k: v for k, v in m.items() if k != "label"},
        })
        all_trades_store[key] = trs

        sh_s = f"{m['sharpe']:+.2f}" if not (isinstance(m['sharpe'], float) and np.isnan(m['sharpe'])) else "  n/a"
        pf_s = f"{m['pf']:.2f}"      if not (isinstance(m['pf'], float)     and np.isnan(m['pf']))     else " n/a"
        print(
            f"  [{idx:>2}/{total_combos}]  {key}"
            f"  │  n={m['n']:>4}  wr={m['win_rate']:>5.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%"
        )

    results = pd.DataFrame(sweep_rows)
    results_sorted = results.sort_values("sharpe", ascending=False)
    results_sorted.to_csv(SWEEP_CSV, index=False)

    # ── 3. Summary table ──────────────────────────────────────────────────────
    print(f"\n{'='*90}")
    print("  TOTAL RESULTS — ALL CONFIGS  (sorted by Sharpe, n ≥ 30)")
    print(f"{'='*90}")
    print(f"  {'Config':<38} {'n':>4} {'Win%':>6} {'PF':>5} {'PnL':>11} "
          f"{'Sharpe':>7} {'MaxDD':>9} {'DD%':>6}")
    print("  " + "-" * 88)
    for _, r in results_sorted[results_sorted["n"] >= 30].iterrows():
        sh_s = f"{r['sharpe']:+.2f}" if not np.isnan(r['sharpe']) else "  n/a"
        pf_s = f"{r['pf']:.2f}"      if not np.isnan(r['pf'])     else " n/a"
        key  = f"EMA{int(r['ema_fast'])}/{int(r['ema_slow'])}  dev≥{r['min_dev_pct']:.2f}%  rev={r['rev_ratio']:.2f}"
        print(
            f"  {key:<38} {int(r['n']):>4}  {r['win_rate']:>5.1f}%  {pf_s:>5}"
            f"  ${r['net_pnl']:>+10,.0f}  {sh_s:>7}  ${r['max_dd']:>8,.0f}  {r['max_dd_pct']:>+5.1f}%"
        )
    print(f"{'='*90}")

    # ── 4. Per-month breakdown for top 5 ─────────────────────────────────────
    top5 = results_sorted[results_sorted["n"] >= 5].head(5)
    print(f"\n{'='*115}")
    print("  PER-MONTH BREAKDOWN — TOP 5 CONFIGS BY SHARPE")
    print(f"{'='*115}")

    detail_rows = []
    for _, r in top5.iterrows():
        key = f"EMA{int(r['ema_fast'])}/{int(r['ema_slow'])}  dev≥{r['min_dev_pct']:.2f}%  rev={r['rev_ratio']:.2f}"
        trs = all_trades_store[key]
        mo_rows = monthly_breakdown(trs)
        total_m = metrics_from_trades(trs, label="TOTAL")

        sh_s = f"{total_m['sharpe']:+.2f}" if not (isinstance(total_m['sharpe'], float) and np.isnan(total_m['sharpe'])) else "n/a"
        print_monthly(
            mo_rows + [total_m],
            config_label=f"{key}  │  Total: n={total_m['n']}  sharpe={sh_s}  pnl=${total_m['net_pnl']:+,.0f}",
        )

        for row in mo_rows + [total_m]:
            detail_rows.append({"config": key, **row})

    pd.DataFrame(detail_rows).to_csv(DETAIL_CSV, index=False)
    print(f"\nSweep results  → {SWEEP_CSV}")
    print(f"Monthly detail → {DETAIL_CSV}")


if __name__ == "__main__":
    main()
