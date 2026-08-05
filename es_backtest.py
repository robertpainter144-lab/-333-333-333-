#!/usr/bin/env python3
"""
ES Mini Futures Backtest — IB Gateway / ibapi edition
Data    : 5-min CONTFUT bars from IB Gateway → resampled to 25-min
Strategy: EMA25 / EMA61 crossover + 14-period ATR filter
Entry   : golden cross  (EMA25 crosses above EMA61)
Exit    : death  cross  (EMA25 crosses below EMA61)
Filter  : only enter when ATR14 is in [ATR_MIN, ATR_MAX]
Note    : original spec was ATR [2,3]; current ES 25-min ATR14 ≈ 10–25 pts.
          Adjust ATR_MIN / ATR_MAX to the regime you care about.
"""

import sys
import time
import threading
import warnings
import logging
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime, timedelta, timezone

from ibapi.client  import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

warnings.filterwarnings("ignore")
logging.getLogger("ibapi").setLevel(logging.CRITICAL)

# ──────────────────────────────────────────────────────────────────────────────
# PARAMETERS  — edit here
# ──────────────────────────────────────────────────────────────────────────────
IB_HOST        = "127.0.0.1"
IB_PORT        = 4001           # IB Gateway: 4001=live, 4002=paper
IB_CLIENT_ID   = 12

YEARS_BACK     = 3              # years of history (6-month chunks, ~11 s apart)

EMA_FAST       = 25
EMA_SLOW       = 61
ATR_PERIOD     = 14
ATR_MIN        = 8.0            # set 2.0 / 3.0 for historical low-vol data
ATR_MAX        = 25.0
ES_MULTIPLIER  = 50             # $50 per point (ES mini)
COMMISSION     = 5.0            # $5 round-trip
INITIAL_CAPITAL = 100_000

BAR_MINUTES    = 25
OUTPUT_CSV     = "trade_log.csv"
OUTPUT_PNG     = "equity_curve.png"

INFO_CODES     = {2104, 2106, 2107, 2108, 2119, 2158}  # benign status codes


# ──────────────────────────────────────────────────────────────────────────────
# IB WRAPPER  — minimal, historical data only
# ──────────────────────────────────────────────────────────────────────────────

class IBFetcher(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self._bars: dict[int, list] = {}
        self._done:  dict[int, threading.Event] = {}
        self._errors: list = []

    # ── connection callbacks ──────────────────────────────────────────────────
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode not in INFO_CODES:
            self._errors.append((reqId, errorCode, errorString))

    def nextValidId(self, orderId):
        pass

    def contractDetails(self, reqId, details):
        pass   # overridden dynamically in fetch_ibkr_data

    def contractDetailsEnd(self, reqId):
        pass   # overridden dynamically in fetch_ibkr_data

    # ── historical data callbacks ─────────────────────────────────────────────
    def historicalData(self, reqId, bar):
        self._bars.setdefault(reqId, []).append({
            "date":   bar.date,
            "open":   bar.open,
            "high":   bar.high,
            "low":    bar.low,
            "close":  bar.close,
            "volume": bar.volume,
        })

    def historicalDataEnd(self, reqId, start, end):
        if reqId in self._done:
            self._done[reqId].set()

    # ── request helper ────────────────────────────────────────────────────────
    def fetch_chunk(self, req_id: int, contract: Contract,
                    end_str: str, duration: str,
                    bar_size: str, what_to_show: str) -> pd.DataFrame:
        self._bars[req_id] = []
        self._done[req_id] = threading.Event()
        self.reqHistoricalData(
            reqId           = req_id,
            contract        = contract,
            endDateTime     = end_str,
            durationStr     = duration,
            barSizeSetting  = bar_size,
            whatToShow      = what_to_show,
            useRTH          = 1,
            formatDate      = 1,
            keepUpToDate    = False,
            chartOptions    = [],
        )
        finished = self._done[req_id].wait(timeout=60)
        if not finished:
            raise TimeoutError(f"Historical data request {req_id} timed out.")
        rows = self._bars.pop(req_id, [])
        del self._done[req_id]
        return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────────────────────────────
# DATA — fetch from IB Gateway
# ──────────────────────────────────────────────────────────────────────────────

def fetch_ibkr_data() -> pd.DataFrame:
    app = IBFetcher()
    app.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)

    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    time.sleep(3)

    if not app.isConnected():
        sys.exit(f"Could not connect to IB Gateway at {IB_HOST}:{IB_PORT}.\n"
                 "Check: Gateway running, API enabled, trusted IP includes 127.0.0.1.")

    print(f"Connected to IB Gateway  port={IB_PORT}")

    # ── discover all available ES quarterly contracts ─────────────────────────
    lookup = Contract()
    lookup.symbol = "ES"; lookup.secType = "FUT"
    lookup.exchange = "CME"; lookup.currency = "USD"

    app._contract_details: list = []
    app._cd_done = threading.Event()

    original_cd  = app.contractDetails
    original_cde = app.contractDetailsEnd

    def _cd(reqId, details):
        app._contract_details.append(details.contract)
    def _cde(reqId):
        app._cd_done.set()

    app.contractDetails    = _cd
    app.contractDetailsEnd = _cde

    app.reqContractDetails(50, lookup)
    app._cd_done.wait(timeout=20)

    app.contractDetails    = original_cd
    app.contractDetailsEnd = original_cde

    if not app._contract_details:
        sys.exit("Could not retrieve ES contract details from IB Gateway.")

    # Sort by expiry ascending, keep only quarterly expirations (YYYYMMDD)
    contracts = sorted(
        app._contract_details,
        key=lambda c: c.lastTradeDateOrContractMonth
    )
    # Filter to within YEARS_BACK + 1 year from now
    cutoff = (datetime.now() - timedelta(days=(YEARS_BACK + 1) * 365)).strftime("%Y%m%d")
    contracts = [c for c in contracts if c.lastTradeDateOrContractMonth >= cutoff]

    print(f"Found {len(contracts)} ES contracts covering the backtest window")

    # ── fetch 5-min TRADES bars per contract, working backwards ──────────────
    all_dfs   = []
    req_id    = 100

    for idx, contract in enumerate(contracts):
        exp   = contract.lastTradeDateOrContractMonth
        label = f"ES {exp}"
        print(f"  [{idx+1}/{len(contracts)}] {label} ...", end=" ", flush=True)

        try:
            df_chunk = app.fetch_chunk(
                req_id       = req_id,
                contract     = contract,
                end_str      = "",        # up to last available bar
                duration     = "6 M",
                bar_size     = "5 mins",
                what_to_show = "TRADES",
            )
        except TimeoutError:
            print("timeout — skipping")
            req_id += 1
            time.sleep(11)
            continue

        req_id += 1

        if df_chunk.empty:
            print("no data")
        else:
            all_dfs.append(df_chunk)
            print(f"{len(df_chunk)} bars")

        if idx < len(contracts) - 1:
            time.sleep(11)   # IB pacing

    app.disconnect()

    if not all_dfs:
        sys.exit("No historical data received from IB Gateway.")

    raw = (
        pd.concat(all_dfs)
          .drop_duplicates(subset="date")
          .sort_values("date")
          .set_index("date")
    )
    raw.index = pd.to_datetime(raw.index, utc=True)
    print(f"\nRaw 5-min bars : {len(raw)}")

    # Resample to 25-min OHLCV
    bars = (
        raw.resample(f"{BAR_MINUTES}min")
           .agg(Open=("open","first"), High=("high","max"),
                Low=("low","min"),   Close=("close","last"),
                Volume=("volume","sum"))
           .dropna(subset=["Open","Close"])
    )
    print(f"25-min bars    : {len(bars)}")
    return bars


# ──────────────────────────────────────────────────────────────────────────────
# INDICATORS
# ──────────────────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["Close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["Close"].ewm(span=EMA_SLOW, adjust=False).mean()

    hl  = df["High"] - df["Low"]
    hcp = (df["High"] - df["Close"].shift(1)).abs()
    lcp = (df["Low"]  - df["Close"].shift(1)).abs()
    df["atr"] = (
        pd.concat([hl, hcp, lcp], axis=1)
          .max(axis=1)
          .ewm(span=ATR_PERIOD, adjust=False)
          .mean()
    )
    return df


# ──────────────────────────────────────────────────────────────────────────────
# SIGNALS
# ──────────────────────────────────────────────────────────────────────────────

def add_signals(df: pd.DataFrame) -> pd.DataFrame:
    df   = df.copy()
    fast = df["ema_fast"]
    slow = df["ema_slow"]

    df["golden_cross"] = (fast >  slow) & (fast.shift(1) <= slow.shift(1))
    df["death_cross"]  = (fast <= slow) & (fast.shift(1) >  slow.shift(1))
    df["atr_ok"]       = df["atr"].between(ATR_MIN, ATR_MAX)
    return df


# ──────────────────────────────────────────────────────────────────────────────
# BACKTEST ENGINE
# ──────────────────────────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame) -> pd.DataFrame:
    warmup   = max(EMA_FAST, EMA_SLOW, ATR_PERIOD)
    trades   = []
    position = None   # (entry_time, entry_price, entry_bar_idx)

    for i in range(warmup, len(df)):
        row = df.iloc[i]

        if position is None:
            if row["golden_cross"] and row["atr_ok"]:
                position = (df.index[i], row["Close"], i)
        else:
            if row["death_cross"]:
                e_time, e_px, e_idx = position
                x_px = row["Close"]
                pnl  = (x_px - e_px) * ES_MULTIPLIER - COMMISSION
                trades.append(dict(
                    entry_time  = e_time,
                    exit_time   = df.index[i],
                    entry_price = round(float(e_px), 2),
                    exit_price  = round(float(x_px), 2),
                    bars_held   = i - e_idx,
                    pnl         = round(pnl, 2),
                ))
                position = None

    # Force-close any open trade at end of data
    if position is not None:
        e_time, e_px, e_idx = position
        x_px = df.iloc[-1]["Close"]
        pnl  = (x_px - e_px) * ES_MULTIPLIER - COMMISSION
        trades.append(dict(
            entry_time  = e_time,
            exit_time   = df.index[-1],
            entry_price = round(float(e_px), 2),
            exit_price  = round(float(x_px), 2),
            bars_held   = len(df) - 1 - e_idx,
            pnl         = round(pnl, 2),
            note        = "open at end of data",
        ))

    return pd.DataFrame(trades)


# ──────────────────────────────────────────────────────────────────────────────
# METRICS
# ──────────────────────────────────────────────────────────────────────────────

def compute_metrics(trades: pd.DataFrame, bars: pd.DataFrame) -> dict:
    pnl = trades["pnl"]
    trades["cum_pnl"] = pnl.cumsum()
    trades["equity"]  = INITIAL_CAPITAL + trades["cum_pnl"]

    hwm        = trades["equity"].cummax()
    dd         = trades["equity"] - hwm
    max_dd_abs = dd.min()
    max_dd_pct = (max_dd_abs / hwm.loc[dd.idxmin()]) * 100

    n        = len(pnl)
    mean_pnl = pnl.mean()
    std_pnl  = pnl.std(ddof=1) if n > 1 else np.nan

    sample_days     = max((bars.index[-1] - bars.index[0]).days, 1)
    trades_per_year = n / (sample_days / 252)
    sharpe = (mean_pnl / std_pnl) * np.sqrt(trades_per_year) \
             if (std_pnl and std_pnl > 0) else np.nan

    wins   = (pnl > 0).sum()
    losses = (pnl < 0).sum()
    return dict(
        n_trades   = n,
        wins       = int(wins),
        losses     = int(losses),
        win_rate   = wins / n * 100,
        avg_win    = pnl[pnl > 0].mean() if wins   else 0.0,
        avg_loss   = pnl[pnl < 0].mean() if losses else 0.0,
        total_pnl  = pnl.sum(),
        sharpe     = sharpe,
        max_dd_abs = max_dd_abs,
        max_dd_pct = max_dd_pct,
    )


# ──────────────────────────────────────────────────────────────────────────────
# PLOTTING
# ──────────────────────────────────────────────────────────────────────────────

def plot_results(trades: pd.DataFrame, m: dict) -> None:
    equity = [INITIAL_CAPITAL] + list(trades["equity"])
    hwm    = pd.Series(equity).cummax()
    dd     = pd.Series(equity) - hwm
    x      = range(len(equity))

    sharpe_str = f"{m['sharpe']:.2f}" if not np.isnan(m['sharpe']) else "n/a"
    title = (
        f"ES Mini — {BAR_MINUTES}-min  EMA{EMA_FAST}/EMA{EMA_SLOW} crossover"
        f"  |  ATR14 [{ATR_MIN}–{ATR_MAX} pts]  |  "
        f"Sharpe {sharpe_str}   MaxDD {m['max_dd_abs']:,.0f}   "
        f"WinRate {m['win_rate']:.1f}%   PnL {m['total_pnl']:,.0f}"
    )

    fig, axes = plt.subplots(
        3, 1, figsize=(14, 11),
        gridspec_kw={"height_ratios": [3, 1.5, 1]},
    )
    fig.suptitle(title, fontsize=9)
    fmt = plt.FuncFormatter(lambda v, _: f"{v:,.0f}")

    ax1 = axes[0]
    ax1.plot(x, equity, color="steelblue", lw=1.6, label="Equity")
    ax1.plot(x, hwm,    color="#999",      lw=0.8, ls="--", label="HWM")
    ax1.axhline(INITIAL_CAPITAL, color="black", lw=0.5, ls=":")
    ax1.set_ylabel("Account Equity (USD)")
    ax1.yaxis.set_major_formatter(fmt)
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.25)

    ax2 = axes[1]
    ax2.fill_between(x, dd, 0, color="crimson", alpha=0.45)
    ax2.set_ylabel("Drawdown (USD)")
    ax2.yaxis.set_major_formatter(fmt)
    ax2.grid(True, alpha=0.25)

    ax3 = axes[2]
    colors = ["seagreen" if p > 0 else "tomato" for p in trades["pnl"]]
    ax3.bar(range(m["n_trades"]), trades["pnl"], color=colors, alpha=0.8, width=0.8)
    ax3.axhline(0, color="black", lw=0.5)
    ax3.set_xlabel("Trade #")
    ax3.set_ylabel("Trade PnL (USD)")
    ax3.yaxis.set_major_formatter(fmt)
    ax3.grid(True, alpha=0.25)

    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches="tight")
    plt.show()
    print(f"Chart saved  → {OUTPUT_PNG}")


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    bars   = fetch_ibkr_data()
    bars   = add_indicators(bars)
    bars   = add_signals(bars)
    trades = run_backtest(bars)

    if trades.empty:
        print("\nNo trades generated.")
        print(f"\nATR14 distribution on {len(bars)} bars:")
        print(bars["atr"].describe().round(2).to_string())
        print(f"\nFilter [{ATR_MIN}, {ATR_MAX}] matched "
              f"{bars['atr_ok'].sum()} bars — adjust if 0.")
        return

    m = compute_metrics(trades, bars)

    sharpe_str = f"{m['sharpe']:.2f}" if not np.isnan(m['sharpe']) else "n/a"
    print()
    print("=" * 44)
    print("          BACKTEST RESULTS")
    print("=" * 44)
    print(f"  Period              : {bars.index[0].date()} – {bars.index[-1].date()}")
    print(f"  25-min bars (RTH)   : {len(bars)}")
    print(f"  Total trades        : {m['n_trades']}")
    print(f"  Wins / Losses       : {m['wins']} / {m['losses']}")
    print(f"  Win rate            : {m['win_rate']:.1f}%")
    print(f"  Avg win             : {m['avg_win']:,.2f}")
    print(f"  Avg loss            : {m['avg_loss']:,.2f}")
    print(f"  Net PnL             : {m['total_pnl']:,.2f}")
    print(f"  Sharpe ratio        : {sharpe_str}")
    print(f"  Max drawdown (USD)  : {m['max_dd_abs']:,.2f}")
    print(f"  Max drawdown (%)    : {m['max_dd_pct']:.2f}%")
    print("=" * 44)

    keep = [c for c in
            ["entry_time","exit_time","entry_price","exit_price",
             "bars_held","pnl","cum_pnl","note"]
            if c in trades.columns]
    trades[keep].to_csv(OUTPUT_CSV, index=False)
    print(f"Trade log saved → {OUTPUT_CSV}")

    plot_results(trades, m)


if __name__ == "__main__":
    main()
