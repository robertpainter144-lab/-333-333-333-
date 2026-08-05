#!/usr/bin/env python3
"""
ES Mini — 60-Day Mean Reversion Backtest
Data     : 5-min RTH TRADES bars from IB Gateway (two 30-day chunks)
Strategy : EMA20 / EMA61 mean reversion (long and short)
  Long   : close < EMA20, EMA20 > EMA61 → fade dip, exit when close > EMA20
  Short  : close > EMA20, EMA20 < EMA61 → fade bounce, exit when close < EMA20
  Stop   : ATR_STOP_MULT × ATR14 from entry
  Also exits on EMA cross against the trade (trend invalidation)
"""

import sys
import time
import threading
import warnings
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

warnings.filterwarnings("ignore")
logging.getLogger("ibapi").setLevel(logging.CRITICAL)

# ── Parameters ────────────────────────────────────────────────────────────────
IB_HOST         = "127.0.0.1"
IB_PORT         = 4001          # 4001 = live gateway, 4002 = paper
IB_CLIENT_ID    = 20
DAYS_BACK       = 60

EMA_FAST        = 20
EMA_SLOW        = 61
ATR_PERIOD      = 14
ATR_STOP_MULT   = 1.5           # stop distance = multiplier × ATR

ES_MULTIPLIER   = 50            # $50 per full point (ES mini)
COMMISSION      = 4.50          # round-trip ($2.25/side approx)
INITIAL_CAPITAL = 25_000
NUM_CONTRACTS   = 1

OUTPUT_CSV      = "es_mean_rev_60d_trades.csv"
OUTPUT_PNG      = "es_mean_rev_60d_equity.png"

INFO_CODES = {2104, 2106, 2107, 2108, 2119, 2158}


# ── IB Wrapper ────────────────────────────────────────────────────────────────

class IBFetcher(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self._bars: dict[int, list] = {}
        self._done: dict[int, threading.Event] = {}
        self._errors: list = []
        self._contract_details: list = []
        self._cd_done = threading.Event()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode not in INFO_CODES:
            self._errors.append((reqId, errorCode, errorString))
            print(f"  IB error {errorCode}: {errorString}")

    def nextValidId(self, orderId):
        pass

    def contractDetails(self, reqId, details):
        self._contract_details.append(details.contract)

    def contractDetailsEnd(self, reqId):
        self._cd_done.set()

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

    def fetch_chunk(
        self,
        req_id: int,
        contract: Contract,
        end_str: str,
        duration: str,
    ) -> pd.DataFrame:
        self._bars[req_id] = []
        self._done[req_id] = threading.Event()
        self.reqHistoricalData(
            reqId          = req_id,
            contract       = contract,
            endDateTime    = end_str,
            durationStr    = duration,
            barSizeSetting = "5 mins",
            whatToShow     = "TRADES",
            useRTH         = 1,
            formatDate     = 1,
            keepUpToDate   = False,
            chartOptions   = [],
        )
        ok = self._done[req_id].wait(timeout=90)
        rows = self._bars.pop(req_id, [])
        del self._done[req_id]
        if not ok:
            print(f"  Warning: request {req_id} timed out — {len(rows)} bars received so far")
        return pd.DataFrame(rows)


# ── Data Fetch ────────────────────────────────────────────────────────────────

def fetch_ibkr_data() -> pd.DataFrame:
    app = IBFetcher()
    app.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)

    thread = threading.Thread(target=app.run, daemon=True)
    thread.start()
    time.sleep(3)

    if not app.isConnected():
        sys.exit(
            f"\nCannot connect to IB Gateway at {IB_HOST}:{IB_PORT}.\n"
            "Check: Gateway is running, API connections are enabled, "
            "127.0.0.1 is trusted, and port matches (live=4001, paper=4002)."
        )
    print(f"Connected to IB Gateway  port={IB_PORT}")

    # Discover all available ES quarterly FUT contracts
    lookup = Contract()
    lookup.symbol   = "ES"
    lookup.secType  = "FUT"
    lookup.exchange = "CME"
    lookup.currency = "USD"

    app.reqContractDetails(50, lookup)
    app._cd_done.wait(timeout=20)

    if not app._contract_details:
        sys.exit("Could not retrieve ES contract details from IB Gateway.")

    # Keep contracts that overlap with the lookback window:
    # expired after (today - DAYS_BACK) AND expiring within 120 days from today
    # (excludes far-future deferred contracts with no trading history yet)
    cutoff_lo = (datetime.utcnow() - timedelta(days=DAYS_BACK + 5)).strftime("%Y%m%d")
    cutoff_hi = (datetime.utcnow() + timedelta(days=120)).strftime("%Y%m%d")
    contracts = sorted(
        [
            c for c in app._contract_details
            if cutoff_lo <= c.lastTradeDateOrContractMonth[:8] <= cutoff_hi
        ],
        key=lambda c: c.lastTradeDateOrContractMonth,
    )
    print(f"Found {len(contracts)} ES contract(s) covering {DAYS_BACK}-day window")

    today_str = datetime.utcnow().strftime("%Y%m%d")
    all_dfs   = []

    for idx, contract in enumerate(contracts):
        exp   = contract.lastTradeDateOrContractMonth[:8]   # YYYYMMDD
        label = f"ES {exp}"
        # For expired contracts use expiry as end; for active use "" (latest bar)
        end_str = "" if exp >= today_str else exp + " 23:59:59"
        print(f"  [{idx+1}/{len(contracts)}] {label}  end={end_str or 'now'} ...", end=" ", flush=True)

        df_chunk = app.fetch_chunk(
            req_id   = 200 + idx,
            contract = contract,
            end_str  = end_str,
            duration = "2 M",
        )
        if df_chunk.empty:
            print("no data")
        else:
            all_dfs.append(df_chunk)
            print(f"{len(df_chunk)} bars")

        if idx < len(contracts) - 1:
            time.sleep(12)

    app.disconnect()

    if not all_dfs:
        sys.exit("No historical data received from IB Gateway.")

    raw = (
        pd.concat(all_dfs)
          .drop_duplicates(subset="date")
          .sort_values("date")
          .reset_index(drop=True)
    )
    raw["date"] = pd.to_datetime(raw["date"])
    raw = raw.set_index("date")

    # Trim to the requested DAYS_BACK window
    cutoff_dt = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=DAYS_BACK)
    if raw.index.tz is not None:
        cutoff_dt = cutoff_dt.tz_localize("UTC")
    raw = raw[raw.index >= cutoff_dt]

    print(f"\nTotal 5-min bars: {len(raw)}  ({raw.index[0].date()} → {raw.index[-1].date()})")
    return raw


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema_fast"] = df["close"].ewm(span=EMA_FAST, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW, adjust=False).mean()

    hl   = df["high"] - df["low"]
    hcp  = (df["high"] - df["close"].shift(1)).abs()
    lcp  = (df["low"]  - df["close"].shift(1)).abs()
    tr   = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()
    return df


# ── Backtest Engine ───────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bar-by-bar simulation.
    Signals evaluated on bar close; trade fills at the same close (conservative
    intraday mean-reversion: fill the bar that triggers — adjust to next open if
    you prefer slippage modelling).
    One position at a time. Long and short.
    """
    warmup   = max(EMA_FAST, EMA_SLOW, ATR_PERIOD)
    trades   = []
    position = None   # dict with keys: side, entry_price, stop_price, entry_idx, entry_time

    for i in range(warmup, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]

        close    = row["close"]
        ema_f    = row["ema_fast"]
        ema_s    = row["ema_slow"]
        atr      = row["atr"]
        pef      = prev["ema_fast"]
        pes      = prev["ema_slow"]

        if position is not None:
            side         = position["side"]
            entry_price  = position["entry_price"]
            stop_price   = position["stop_price"]
            entry_idx    = position["entry_idx"]
            entry_time   = position["entry_time"]
            bars_held    = i - entry_idx

            # Detect EMA cross against the trade
            golden_cross = pef <= pes and ema_f > ema_s
            death_cross  = pef >= pes and ema_f < ema_s

            exit_reason = None
            if side == "long":
                if close <= stop_price:
                    exit_reason = "atr_stop"
                elif death_cross:
                    exit_reason = "trend_flip"
                elif close >= ema_f:
                    exit_reason = "mean_reversion"
            else:  # short
                if close >= stop_price:
                    exit_reason = "atr_stop"
                elif golden_cross:
                    exit_reason = "trend_flip"
                elif close <= ema_f:
                    exit_reason = "mean_reversion"

            if exit_reason:
                pts = (close - entry_price) if side == "long" else (entry_price - close)
                pnl = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
                trades.append({
                    "entry_time":  entry_time,
                    "exit_time":   df.index[i],
                    "side":        side,
                    "entry_price": round(float(entry_price), 2),
                    "exit_price":  round(float(close), 2),
                    "bars_held":   bars_held,
                    "atr_at_entry": round(float(position["atr_at_entry"]), 2),
                    "stop_price":  round(float(stop_price), 2),
                    "pts":         round(float(pts), 2),
                    "pnl":         round(pnl, 2),
                    "exit_reason": exit_reason,
                })
                position = None

        # Entry logic (only when flat)
        if position is None:
            uptrend   = ema_f > ema_s
            downtrend = ema_f < ema_s

            if uptrend and close < ema_f:
                # Price dipped below fast EMA in uptrend — fade back to EMA20
                stop  = close - ATR_STOP_MULT * atr
                position = {
                    "side":         "long",
                    "entry_price":  close,
                    "stop_price":   stop,
                    "atr_at_entry": atr,
                    "entry_idx":    i,
                    "entry_time":   df.index[i],
                }
            elif downtrend and close > ema_f:
                # Price bounced above fast EMA in downtrend — fade back to EMA20
                stop  = close + ATR_STOP_MULT * atr
                position = {
                    "side":         "short",
                    "entry_price":  close,
                    "stop_price":   stop,
                    "atr_at_entry": atr,
                    "entry_idx":    i,
                    "entry_time":   df.index[i],
                }

    # Force-close any open trade at end of data
    if position is not None:
        side        = position["side"]
        entry_price = position["entry_price"]
        stop_price  = position["stop_price"]
        close       = df.iloc[-1]["close"]
        pts = (close - entry_price) if side == "long" else (entry_price - close)
        pnl = pts * ES_MULTIPLIER * NUM_CONTRACTS - COMMISSION
        trades.append({
            "entry_time":  position["entry_time"],
            "exit_time":   df.index[-1],
            "side":        side,
            "entry_price": round(float(entry_price), 2),
            "exit_price":  round(float(close), 2),
            "bars_held":   len(df) - 1 - position["entry_idx"],
            "atr_at_entry": round(float(position["atr_at_entry"]), 2),
            "stop_price":  round(float(stop_price), 2),
            "pts":         round(float(pts), 2),
            "pnl":         round(pnl, 2),
            "exit_reason": "end_of_data",
        })

    return pd.DataFrame(trades)


# ── Metrics ───────────────────────────────────────────────────────────────────

def compute_metrics(trades: pd.DataFrame, bars: pd.DataFrame) -> dict:
    pnl = trades["pnl"]
    trades = trades.copy()
    trades["cum_pnl"] = pnl.cumsum()
    trades["equity"]  = INITIAL_CAPITAL + trades["cum_pnl"]

    hwm        = trades["equity"].cummax()
    dd         = trades["equity"] - hwm
    max_dd_abs = dd.min()
    max_dd_pct = (max_dd_abs / hwm.loc[dd.idxmin()]) * 100 if not dd.empty else 0.0

    n        = len(pnl)
    wins     = (pnl > 0).sum()
    losses   = (pnl < 0).sum()
    mean_pnl = pnl.mean()
    std_pnl  = pnl.std(ddof=1) if n > 1 else np.nan
    avg_win  = pnl[pnl > 0].mean() if wins  else 0.0
    avg_loss = pnl[pnl < 0].mean() if losses else 0.0
    profit_factor = (-pnl[pnl > 0].sum() / pnl[pnl < 0].sum()) if losses and pnl[pnl < 0].sum() != 0 else np.nan

    sample_days     = max((bars.index[-1] - bars.index[0]).days, 1)
    trades_per_year = n / (sample_days / 252)
    sharpe = (mean_pnl / std_pnl) * np.sqrt(trades_per_year) if std_pnl and std_pnl > 0 else np.nan

    exit_breakdown = trades["exit_reason"].value_counts().to_dict()

    return dict(
        n_trades       = n,
        wins           = int(wins),
        losses         = int(losses),
        win_rate       = wins / n * 100 if n else 0.0,
        avg_win        = avg_win,
        avg_loss       = avg_loss,
        avg_pts        = trades["pts"].mean(),
        profit_factor  = profit_factor,
        total_pnl      = pnl.sum(),
        sharpe         = sharpe,
        max_dd_abs     = max_dd_abs,
        max_dd_pct     = max_dd_pct,
        exit_breakdown = exit_breakdown,
        trades         = trades,
    )


# ── Plot ──────────────────────────────────────────────────────────────────────

def plot_results(m: dict, bars: pd.DataFrame) -> None:
    trades = m["trades"]
    equity = [INITIAL_CAPITAL] + list(trades["equity"])
    hwm    = pd.Series(equity).cummax()
    dd     = pd.Series(equity) - hwm

    sharpe_str = f"{m['sharpe']:.2f}" if not np.isnan(m["sharpe"]) else "n/a"
    pf_str     = f"{m['profit_factor']:.2f}" if not np.isnan(m["profit_factor"]) else "n/a"

    title = (
        f"ES Mini  5-min  EMA{EMA_FAST}/EMA{EMA_SLOW} Mean Reversion  "
        f"60 days  |  Trades: {m['n_trades']}  Win%: {m['win_rate']:.1f}  "
        f"PF: {pf_str}  Sharpe: {sharpe_str}  Net: ${m['total_pnl']:,.0f}"
    )

    fig, axes = plt.subplots(
        3, 1, figsize=(14, 11),
        gridspec_kw={"height_ratios": [3, 1.5, 1]},
    )
    fig.suptitle(title, fontsize=9)
    fmt = plt.FuncFormatter(lambda v, _: f"${v:,.0f}")

    ax1 = axes[0]
    ax1.plot(equity,      color="steelblue",  lw=1.6, label="Equity")
    ax1.plot(list(hwm),   color="#aaa",       lw=0.9, ls="--", label="HWM")
    ax1.axhline(INITIAL_CAPITAL, color="black", lw=0.5, ls=":")
    ax1.set_ylabel("Account Equity (USD)")
    ax1.yaxis.set_major_formatter(fmt)
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.25)

    ax2 = axes[1]
    ax2.fill_between(range(len(dd)), dd, 0, color="crimson", alpha=0.45)
    ax2.set_ylabel("Drawdown (USD)")
    ax2.yaxis.set_major_formatter(fmt)
    ax2.grid(True, alpha=0.25)

    ax3 = axes[2]
    colors = ["seagreen" if p > 0 else "tomato" for p in trades["pnl"]]
    ax3.bar(range(m["n_trades"]), trades["pnl"], color=colors, alpha=0.8, width=0.8)
    ax3.axhline(0, color="black", lw=0.5)
    ax3.set_xlabel("Trade #")
    ax3.set_ylabel("Trade PnL (USD)")
    ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax3.grid(True, alpha=0.25)

    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=150, bbox_inches="tight")
    print(f"Chart saved → {OUTPUT_PNG}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    bars   = fetch_ibkr_data()
    bars   = add_indicators(bars)
    trades = run_backtest(bars)

    if trades.empty:
        print("\nNo trades generated.")
        print(f"\nEMA20 vs EMA61 relationship on last 20 bars:")
        tail = bars[["close", "ema_fast", "ema_slow"]].tail(20)
        print(tail.to_string())
        return

    m = compute_metrics(trades, bars)

    sharpe_str = f"{m['sharpe']:.2f}"  if not np.isnan(m["sharpe"])        else "n/a"
    pf_str     = f"{m['profit_factor']:.2f}" if not np.isnan(m["profit_factor"]) else "n/a"

    print()
    print("=" * 50)
    print("     ES MINI  5-MIN  MEAN REVERSION  60 DAYS")
    print(f"     EMA{EMA_FAST} / EMA{EMA_SLOW}  |  ATR{ATR_PERIOD} stop ×{ATR_STOP_MULT}")
    print("=" * 50)
    print(f"  Period             : {bars.index[0].date()} – {bars.index[-1].date()}")
    print(f"  5-min bars (RTH)   : {len(bars)}")
    print(f"  Total trades       : {m['n_trades']}")
    print(f"  Longs / Shorts     : {(trades['side']=='long').sum()} / {(trades['side']=='short').sum()}")
    print(f"  Wins / Losses      : {m['wins']} / {m['losses']}")
    print(f"  Win rate           : {m['win_rate']:.1f}%")
    print(f"  Avg win            : ${m['avg_win']:>10,.2f}")
    print(f"  Avg loss           : ${m['avg_loss']:>10,.2f}")
    print(f"  Avg pts/trade      : {m['avg_pts']:>+.2f} pts")
    print(f"  Profit factor      : {pf_str}")
    print(f"  Net PnL            : ${m['total_pnl']:>10,.2f}")
    print(f"  Sharpe ratio       : {sharpe_str}")
    print(f"  Max drawdown (USD) : ${m['max_dd_abs']:>10,.2f}")
    print(f"  Max drawdown (%)   : {m['max_dd_pct']:.2f}%")
    print()
    print("  Exit breakdown:")
    for reason, count in sorted(m["exit_breakdown"].items(), key=lambda x: -x[1]):
        print(f"    {reason:<22} : {count}")
    print("=" * 50)

    keep = [c for c in [
        "entry_time", "exit_time", "side",
        "entry_price", "exit_price", "bars_held",
        "atr_at_entry", "stop_price", "pts", "pnl", "exit_reason",
    ] if c in trades.columns]
    trades[keep].to_csv(OUTPUT_CSV, index=False)
    print(f"\nTrade log → {OUTPUT_CSV}")

    plot_results(m, bars)


if __name__ == "__main__":
    main()
