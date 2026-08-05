#!/usr/bin/env python3
"""
Block-bootstrap MaxDD distribution for SPY Adaptive Regime strategy.
Uses best config: gap=off, eod_vol_mult=off, short_trail=3.0x, eod_loss=False
Runs 2000 bootstrap paths, reports p5/p50/p95 MaxDD and terminal return.
"""

import warnings
import json
import numpy as np
import pandas as pd
from pathlib import Path

warnings.filterwarnings("ignore")

# ── Same constants as spy_adaptive.py ──────────────────────────────────────────
CACHE         = Path("/Users/quarki3/spy_5min_bars.csv")
EMA_PERIOD    = 25
ATR_PERIOD    = 14
ADX_PERIOD    = 20
LONG_TRAIL    = 3.0
SHORT_TRAIL   = 3.0
ADX_THRESHOLD = 35
ENTRY_START   = 9 * 60 + 30
ENTRY_END     = 11 * 60
EOD_MINS      = 15 * 60 + 50
ATR_MEDIAN_W  = 20
INITIAL_EQ    = 100_000.0
LEVERAGE      = 2.0
COMM          = 0.005


def add_indicators(df):
    df = df.copy()
    df["ema"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"]     = tr.ewm(span=ATR_PERIOD, adjust=False).mean()
    df["atr_med"] = df["atr"].rolling(ATR_MEDIAN_W).median()
    up   = df["high"] - df["high"].shift(1)
    down = df["low"].shift(1) - df["low"]
    pdm  = np.where((up > down) & (up > 0), up, 0.0)
    ndm  = np.where((down > up) & (down > 0), down, 0.0)
    atr_w = tr.ewm(span=ADX_PERIOD, adjust=False).mean()
    pdi   = 100 * pd.Series(pdm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean() / atr_w
    ndi   = 100 * pd.Series(ndm, index=df.index).ewm(span=ADX_PERIOD, adjust=False).mean() / atr_w
    dx    = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    df["adx"] = dx.ewm(span=ADX_PERIOD, adjust=False).mean()
    df["pdi"] = pdi
    df["ndi"] = ndi
    df["_date"]      = df.index.date
    daily_close      = df.groupby("_date")["close"].last()
    daily_close_s    = pd.Series(daily_close.values,
                                 index=pd.to_datetime(list(daily_close.index)))
    prev_close_daily = daily_close_s.shift(1)
    bar_dates        = pd.to_datetime(df.index.date)
    df["prev_close"] = bar_dates.map(prev_close_daily.to_dict()).values
    first_bar        = ~pd.Series(df.index.date, index=df.index).duplicated()
    df["gap_pct"]    = np.where(
        first_bar & df["prev_close"].notna(),
        (df["open"] - df["prev_close"]) / df["prev_close"] * 100,
        np.nan
    )
    df["gap_pct"] = df["gap_pct"].ffill()
    df.drop(columns=["_date"], inplace=True)
    return df


def classify_regime(adx, pdi, ndi):
    if adx <= ADX_THRESHOLD:
        return "ranging"
    return "bull_trend" if pdi >= ndi else "bear_trend"


def run_backtest_trades(df):
    """Run best-config backtest; return array of per-trade dollar PnL."""
    warmup   = 100
    trades   = []
    position = None
    equity   = INITIAL_EQ

    for i in range(warmup, len(df)):
        row   = df.iloc[i]
        close = row["close"]
        ema   = row["ema"]
        atr   = row["atr"]
        adx   = row["adx"]
        pdi   = row["pdi"]
        ndi   = row["ndi"]
        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute
        regime = classify_regime(adx, pdi, ndi)

        if position is not None:
            side = position["side"]
            exit_now = False
            if mins >= EOD_MINS and side == "short":
                exit_now = True

            if side == "long":
                new_stop = close - LONG_TRAIL * atr
                if new_stop > position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close <= position["trail_stop"]
            else:
                new_stop = close + SHORT_TRAIL * atr
                if new_stop < position["trail_stop"]:
                    position["trail_stop"] = new_stop
                stop_hit = close >= position["trail_stop"]

            if stop_hit or exit_now:
                shares = position["shares"]
                gross  = (close - position["entry_price"]) * shares if side == "long" \
                     else (position["entry_price"] - close) * shares
                pnl    = gross - shares * COMM * 2
                equity += pnl
                trades.append(pnl)
                position = None

        if position is None and ENTRY_START <= mins < ENTRY_END:
            shares = int((equity * LEVERAGE) / close)
            if shares < 1:
                continue
            if regime in ("ranging", "bull_trend") and close > ema:
                position = {
                    "side": "long",
                    "entry_price": close,
                    "shares": shares,
                    "trail_stop": close - LONG_TRAIL * atr,
                    "entry_idx": i,
                }
            elif regime == "bear_trend" and close >= ema:
                position = {
                    "side": "short",
                    "entry_price": close,
                    "shares": shares,
                    "trail_stop": close + SHORT_TRAIL * atr,
                    "entry_idx": i,
                }

    if position is not None:
        side   = position["side"]
        close  = df.iloc[-1]["close"]
        shares = position["shares"]
        gross  = (close - position["entry_price"]) * shares if side == "long" \
             else (position["entry_price"] - close) * shares
        pnl    = gross - shares * COMM * 2
        equity += pnl
        trades.append(pnl)

    return np.array(trades)


def maxdd_from_pnl(pnl_arr, initial=INITIAL_EQ):
    """Compute MaxDD% from array of dollar PnL values."""
    eq = initial + np.cumsum(pnl_arr)
    eq = np.concatenate([[initial], eq])
    hwm = np.maximum.accumulate(eq)
    dd = (eq - hwm) / hwm
    return float(dd.min())   # negative


def block_bootstrap(pnl_arr, n_paths=2000, block_size=10, rng=None):
    """
    Circular block bootstrap of per-trade PnL.
    Returns (maxdd_arr, terminal_return_arr) for n_paths simulations,
    each path the same length as the original sequence.
    """
    if rng is None:
        rng = np.random.default_rng(42)
    n = len(pnl_arr)
    n_blocks = int(np.ceil(n / block_size))
    maxdds  = np.empty(n_paths)
    term_rets = np.empty(n_paths)

    for k in range(n_paths):
        starts = rng.integers(0, n, size=n_blocks)
        path = []
        for s in starts:
            block = []
            for j in range(block_size):
                block.append(pnl_arr[(s + j) % n])
            path.extend(block)
        path = np.array(path[:n])   # trim to original length
        maxdds[k]    = maxdd_from_pnl(path)
        terminal_eq  = INITIAL_EQ + np.sum(path)
        term_rets[k] = (terminal_eq / INITIAL_EQ - 1.0) * 100.0

    return maxdds, term_rets


def main():
    print("Loading bars …")
    raw = pd.read_csv(CACHE, index_col=0, parse_dates=True)
    print(f"  {len(raw):,} bars  {raw.index[0].date()} → {raw.index[-1].date()}")

    print("Adding indicators …")
    df = add_indicators(raw)

    print("Running base backtest …")
    pnl_arr = run_backtest_trades(df)
    n_trades = len(pnl_arr)
    print(f"  {n_trades} trades extracted")

    actual_maxdd = maxdd_from_pnl(pnl_arr) * 100
    actual_term  = (INITIAL_EQ + np.sum(pnl_arr)) / INITIAL_EQ * 100 - 100
    print(f"  Historical MaxDD: {actual_maxdd:.1f}%  Terminal return: {actual_term:.1f}%")

    # Block size: ~10 trades ≈ 2-3 weeks of trading, captures local autocorrelation
    BLOCK_SIZE = 10
    N_PATHS    = 2000
    print(f"\nBlock-bootstrapping {N_PATHS} paths (block={BLOCK_SIZE} trades) …")
    maxdds, term_rets = block_bootstrap(pnl_arr, N_PATHS, BLOCK_SIZE)

    # Report percentiles (MaxDD is negative, so p5 is most severe in absolute terms)
    p5_dd  = float(np.percentile(maxdds, 5))   # 5th pct = most severe
    p50_dd = float(np.percentile(maxdds, 50))
    p95_dd = float(np.percentile(maxdds, 95))  # 95th pct = mildest

    p5_ret  = float(np.percentile(term_rets, 5))
    p50_ret = float(np.percentile(term_rets, 50))
    p95_ret = float(np.percentile(term_rets, 95))

    # Convention: report MaxDD as positive percentages for readability
    print(f"\n{'='*60}")
    print(f"  MaxDD distribution  (worst → best)")
    print(f"    p5  (worst 5%): {abs(p5_dd)*100:.1f}%")
    print(f"    p50 (median):   {abs(p50_dd)*100:.1f}%")
    print(f"    p95 (best 5%):  {abs(p95_dd)*100:.1f}%")
    print(f"\n  Terminal return distribution  (5y path, same length as original)")
    print(f"    p5  (worst 5%): {p5_ret:.1f}%")
    print(f"    p50 (median):   {p50_ret:.1f}%")
    print(f"    p95 (best 5%):  {p95_ret:.1f}%")

    account_threatening = abs(p5_dd) * 100 > 40.0
    flag_msg = "WARNING: 95th-pct MaxDD exceeds 40% — account-threatening tail risk." \
        if account_threatening else "OK: 95th-pct MaxDD is below 40% threshold."
    print(f"\n  {flag_msg}")
    print(f"{'='*60}")

    # Sanity check
    if actual_maxdd < -60 or actual_term > 500:
        skeptic_note = "Backtest numbers appear unrealistically good — treat with skepticism."
    elif abs(p5_dd) * 100 < 5:
        skeptic_note = "Bootstrap MaxDD distribution implausibly tight — verify trade count."
    else:
        skeptic_note = "Results appear plausible given strategy characteristics."

    result = {
        "task":         "mc_drawdown_dist",
        "strategy":     "SPY Adaptive Regime v2 (EMA25, ATR trail 3x, direction-aware ADX35)",
        "n_trades":     n_trades,
        "n_paths":      N_PATHS,
        "block_size":   BLOCK_SIZE,
        "historical_maxdd_pct":    round(abs(actual_maxdd), 2),
        "historical_term_ret_pct": round(actual_term, 2),
        # MaxDD percentiles — reported as positive (magnitude)
        "p5_maxdd":    round(abs(p5_dd) * 100, 2),
        "p50_maxdd":   round(abs(p50_dd) * 100, 2),
        "p95_maxdd":   round(abs(p95_dd) * 100, 2),
        # Terminal return percentiles
        "p5_term_ret":  round(p5_ret, 2),
        "p50_term_ret": round(p50_ret, 2),
        "p95_term_ret": round(p95_ret, 2),
        "account_threatening_flag": account_threatening,
        "skeptic_note": skeptic_note,
        "summary": (
            f"SPY Adaptive Regime v2 ({n_trades} trades, 5yr). "
            f"Historical MaxDD {abs(actual_maxdd):.1f}% is a single draw; "
            f"block-bootstrap ({N_PATHS} paths, block={BLOCK_SIZE}) gives "
            f"p5/p50/p95 MaxDD of "
            f"{abs(p5_dd)*100:.1f}% / {abs(p50_dd)*100:.1f}% / {abs(p95_dd)*100:.1f}%. "
            f"{'95th-pct MaxDD exceeds 40% account-threatening threshold.' if account_threatening else '95th-pct MaxDD is below 40% threshold — tail risk is manageable for a well-capitalized account.'} "
            f"{skeptic_note}"
        ),
        "success": True,
    }

    out_path = Path("/Users/quarki3/swarm_results/completed/mc_drawdown_dist.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResult written → {out_path}")
    return result


if __name__ == "__main__":
    main()
