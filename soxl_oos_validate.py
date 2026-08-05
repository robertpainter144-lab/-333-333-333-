#!/usr/bin/env python3
"""
SOXL Regime OOS Validation
===========================
1. IS / OOS split (70% / 30%) on bar data — run best config independently on each slice.
2. Block-bootstrap 2000 paths from IS per-trade fractional returns → p5/p50/p95 MaxDD.
3. Verdict: deploy-to-paper vs shelve.
4. Write /Users/quarki3/swarm_results/completed/soxl_regime_oos.json.
"""

import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ── Best config (from soxl_backtest_reexam.json post-bug-fix) ─────────────────
DIP_ATR_BACK   = 0.5
MIN_PROFIT_ATR = 0.5
MAX_ADDS       = 1
ADD_FRACTION   = 1.0

# ── Fixed indicator / strategy params ─────────────────────────────────────────
EMA_PERIOD    = 25
ATR_PERIOD    = 14
ADX_PERIOD    = 20
ADX_THRESHOLD = 35
LONG_TRAIL    = 3.0
SHORT_TRAIL   = 3.0
ENTRY_START   = 9 * 60 + 30
ENTRY_END     = 11 * 60
EOD_MINS      = 15 * 60 + 50
LEVERAGE      = 1.0
COMM          = 0.005
INITIAL_EQ    = 100.0   # $100 account for realistic p95 MaxDD


# ── Data ──────────────────────────────────────────────────────────────────────
def load_data() -> pd.DataFrame:
    cache = Path("/Users/quarki3/soxl_5min_bars.csv")
    df = pd.read_csv(cache, index_col=0, parse_dates=True)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("America/New_York").tz_localize(None)
    df = df.between_time("09:30", "16:00")
    return df


# ── Indicators ────────────────────────────────────────────────────────────────
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = df["close"].ewm(span=EMA_PERIOD, adjust=False).mean()

    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()

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
    return df


def classify_regime(adx, pdi, ndi):
    if adx <= ADX_THRESHOLD:
        return "ranging"
    return "bull_trend" if pdi >= ndi else "bear_trend"


# ── Backtest ──────────────────────────────────────────────────────────────────
def run_backtest(df: pd.DataFrame, init_eq: float = INITIAL_EQ) -> dict:
    warmup   = 100
    trades   = []
    position = None
    equity   = init_eq

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

            if side == "long" and close > position["pos_high"]:
                position["pos_high"] = close

            if (side == "long"
                    and position["n_adds"] < MAX_ADDS
                    and ENTRY_START <= mins < ENTRY_END + 120):
                dip_from_high    = position["pos_high"] - close
                profit_per_share = close - position["avg_entry"]
                if (dip_from_high >= DIP_ATR_BACK * atr
                        and profit_per_share >= MIN_PROFIT_ATR * atr
                        and close > ema):
                    add_shares = max(1, int(position["orig_shares"] * ADD_FRACTION))
                    old_cost   = position["avg_entry"] * position["total_shares"]
                    position["total_shares"] += add_shares
                    position["avg_entry"]     = (old_cost + close * add_shares) / position["total_shares"]
                    position["n_adds"]        += 1

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

            exit_now = mins >= EOD_MINS   # bug-fixed: close ALL at EOD

            if stop_hit or exit_now:
                shares = position["total_shares"]
                gross  = (close - position["avg_entry"]) * shares if side == "long" \
                     else (position["avg_entry"] - close) * shares
                pnl    = gross - shares * COMM * 2
                # fractional return on equity at trade start
                frac_ret = pnl / position["equity_at_entry"]
                equity += pnl
                trades.append({
                    "side":            side,
                    "pnl":             pnl,
                    "frac_ret":        frac_ret,
                    "equity_at_entry": position["equity_at_entry"],
                    "year":            ts.year,
                    "date":            str(ts.date()),
                })
                position = None

        if position is None and ENTRY_START <= mins < ENTRY_END:
            shares = int((equity * LEVERAGE) / close)
            if shares < 1:
                continue

            if regime in ("ranging", "bull_trend") and close > ema:
                position = {
                    "side":            "long",
                    "entry_price":     close,
                    "avg_entry":       close,
                    "total_shares":    shares,
                    "orig_shares":     shares,
                    "trail_stop":      close - LONG_TRAIL * atr,
                    "pos_high":        close,
                    "n_adds":          0,
                    "equity_at_entry": equity,
                }
            elif regime == "bear_trend" and close >= ema:
                position = {
                    "side":            "short",
                    "entry_price":     close,
                    "avg_entry":       close,
                    "total_shares":    shares,
                    "orig_shares":     shares,
                    "trail_stop":      close + SHORT_TRAIL * atr,
                    "pos_high":        close,
                    "n_adds":          0,
                    "equity_at_entry": equity,
                }

    # close dangling position at end of slice
    if position is not None:
        side   = position["side"]
        close  = df.iloc[-1]["close"]
        shares = position["total_shares"]
        gross  = (close - position["avg_entry"]) * shares if side == "long" \
             else (position["avg_entry"] - close) * shares
        pnl    = gross - shares * COMM * 2
        frac_ret = pnl / position["equity_at_entry"]
        equity += pnl
        trades.append({
            "side":            side,
            "pnl":             pnl,
            "frac_ret":        frac_ret,
            "equity_at_entry": position["equity_at_entry"],
            "year":            df.index[-1].year,
            "date":            str(df.index[-1].date()),
        })

    return {"trades": trades, "final_eq": equity}


# ── Metric helpers ─────────────────────────────────────────────────────────────
def _sharpe_trades(frac_rets: np.ndarray, trades_per_year: float) -> float:
    if len(frac_rets) < 2:
        return float("nan")
    mu    = frac_rets.mean()
    sigma = frac_rets.std(ddof=1)
    if sigma == 0:
        return 0.0
    return float(mu / sigma * math.sqrt(trades_per_year))


def _cagr_trades(frac_rets: np.ndarray, trades_per_year: float) -> float:
    if len(frac_rets) == 0:
        return float("nan")
    total = float((1.0 + frac_rets).prod())
    if total <= 0:
        return -1.0
    years = len(frac_rets) / trades_per_year
    return total ** (1.0 / years) - 1.0


def _max_dd_trades(frac_rets: np.ndarray) -> float:
    if len(frac_rets) == 0:
        return float("nan")
    equity = (1.0 + frac_rets).cumprod()
    peak   = np.maximum.accumulate(equity)
    dd     = (equity - peak) / peak
    return float(dd.min())


def metrics_from_trades(trades: list, df: pd.DataFrame) -> dict:
    if not trades:
        return {"n": 0, "sharpe": float("nan"), "cagr": float("nan"),
                "max_dd": float("nan"), "win_rate": float("nan")}
    frac = np.array([t["frac_ret"] for t in trades])
    n_days   = max((df.index[-1] - df.index[0]).days, 1)
    tpy      = len(frac) / (n_days / 365)
    sharpe   = _sharpe_trades(frac, tpy)
    cagr     = _cagr_trades(frac, tpy)
    max_dd   = _max_dd_trades(frac)
    win_rate = float((frac > 0).mean() * 100)
    return {
        "n":        len(frac),
        "sharpe":   round(sharpe, 3),
        "cagr":     round(cagr * 100, 2),
        "max_dd":   round(max_dd * 100, 2),
        "win_rate": round(win_rate, 1),
    }


# ── Block bootstrap ────────────────────────────────────────────────────────────
def block_bootstrap_maxdd(frac_rets: np.ndarray,
                          n_paths: int = 2000,
                          rng: np.random.Generator = None) -> dict:
    """Block-bootstrap MaxDD distribution (fractional)."""
    if rng is None:
        rng = np.random.default_rng(42)

    n = len(frac_rets)
    if n < 4:
        return {"p5": float("nan"), "p50": float("nan"), "p95": float("nan")}

    block = max(1, int(math.sqrt(n)))   # Patton/Politis rule of thumb
    mdd_paths = []

    for _ in range(n_paths):
        # draw enough blocks to cover n trades
        n_blocks = math.ceil(n / block)
        starts   = rng.integers(0, n - block + 1, size=n_blocks)
        boot     = np.concatenate([frac_rets[s:s+block] for s in starts])[:n]
        # MaxDD on this path
        eq   = (1.0 + boot).cumprod()
        peak = np.maximum.accumulate(eq)
        dd   = (eq - peak) / peak
        mdd_paths.append(float(dd.min()))

    arr = np.array(mdd_paths)
    # Express as positive loss magnitude so percentile ordering is intuitive:
    # p5 = mild tail, p95 = worst-case tail (the risk number that matters)
    abs_arr = np.abs(arr)
    return {
        "p5":  -round(float(np.percentile(abs_arr, 5))  * 100, 2),
        "p50": -round(float(np.percentile(abs_arr, 50)) * 100, 2),
        "p95": -round(float(np.percentile(abs_arr, 95)) * 100, 2),
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\nSOXL Regime OOS Validation")
    print("=" * 70)

    df = load_data()
    print(f"  Bars: {len(df):,}  ({df.index[0].date()} → {df.index[-1].date()})")
    df = add_indicators(df)

    # ── IS / OOS split by bar index (chronological 70/30) ─────────────────
    warmup = 100
    active = df.iloc[warmup:]   # skip warmup for the split boundary
    n_active = len(active)
    is_end = warmup + int(round(n_active * 0.70))

    df_is  = df.iloc[:is_end]
    df_oos = df.iloc[is_end:]

    is_dates  = (df_is.index[0].date(),  df_is.index[-1].date())
    oos_dates = (df_oos.index[0].date(), df_oos.index[-1].date())
    print(f"\n  IS  window: {is_dates[0]} → {is_dates[1]}  ({len(df_is):,} bars)")
    print(f"  OOS window: {oos_dates[0]} → {oos_dates[1]}  ({len(df_oos):,} bars)")

    # ── Run backtest on each slice ─────────────────────────────────────────
    print("\n  Running IS backtest ...")
    res_is  = run_backtest(df_is,  init_eq=INITIAL_EQ)
    print(f"    {len(res_is['trades'])} IS trades  |  equity ${INITIAL_EQ:.0f} → ${res_is['final_eq']:.2f}")

    print("  Running OOS backtest ...")
    res_oos = run_backtest(df_oos, init_eq=INITIAL_EQ)
    print(f"    {len(res_oos['trades'])} OOS trades  |  equity ${INITIAL_EQ:.0f} → ${res_oos['final_eq']:.2f}")

    # ── Metrics ────────────────────────────────────────────────────────────
    m_is  = metrics_from_trades(res_is["trades"],  df_is)
    m_oos = metrics_from_trades(res_oos["trades"], df_oos)

    print(f"\n  {'Metric':<14} {'IS':>12} {'OOS':>12}")
    print(f"  {'-'*40}")
    print(f"  {'Sharpe':<14} {m_is['sharpe']:>12.3f} {m_oos['sharpe']:>12.3f}")
    print(f"  {'CAGR %':<14} {m_is['cagr']:>11.2f}% {m_oos['cagr']:>11.2f}%")
    print(f"  {'MaxDD %':<14} {m_is['max_dd']:>11.2f}% {m_oos['max_dd']:>11.2f}%")
    print(f"  {'Win Rate %':<14} {m_is['win_rate']:>11.1f}% {m_oos['win_rate']:>11.1f}%")
    print(f"  {'N trades':<14} {m_is['n']:>12,} {m_oos['n']:>12,}")

    if m_is["n"] < 30:
        print(f"\n  *** WARNING: IS trade count = {m_is['n']} < 30 — insufficient for statistical inference")

    # degradation
    if abs(m_is["sharpe"]) > 1e-6 and not math.isnan(m_is["sharpe"]):
        degradation = m_oos["sharpe"] / m_is["sharpe"]
        deg_str = f"{degradation:.3f}"
        if degradation < 0:
            deg_flag = "  *** OOS SIGN FLIP — strategy reverses in OOS"
        elif degradation < 0.50:
            deg_flag = "  *** Severe degradation (< 0.50)"
        elif degradation < 0.75:
            deg_flag = "  ** Notable degradation (0.50–0.75)"
        else:
            deg_flag = "  (acceptable degradation)"
    else:
        degradation = None
        deg_str = "N/A"
        deg_flag = ""
    print(f"\n  Degradation (OOS/IS Sharpe): {deg_str}{deg_flag}")

    # ── Block bootstrap on IS trades ───────────────────────────────────────
    print("\n  Block-bootstrapping IS trades (2000 paths) ...")
    if res_is["trades"]:
        frac_is = np.array([t["frac_ret"] for t in res_is["trades"]])
        boot    = block_bootstrap_maxdd(frac_is, n_paths=2000)
    else:
        boot = {"p5": float("nan"), "p50": float("nan"), "p95": float("nan")}

    print(f"  Bootstrap MaxDD distribution (n={len(res_is['trades'])} IS trades, block=√n):")
    print(f"    p5  MaxDD: {boot['p5']:+.2f}%  (best-case tail)")
    print(f"    p50 MaxDD: {boot['p50']:+.2f}%  (median)")
    print(f"    p95 MaxDD: {boot['p95']:+.2f}%  (worst-case tail — use this for $100 account)")

    p95_dd = boot["p95"]
    p95_dollar = abs(p95_dd / 100) * INITIAL_EQ
    print(f"\n  On a ${INITIAL_EQ:.0f} account: p95 MaxDD = -${p95_dollar:.2f}")

    # ── Verdict ────────────────────────────────────────────────────────────
    n_is = m_is["n"]
    oos_sharpe = m_oos["sharpe"]
    is_sharpe  = m_is["sharpe"]

    low_n          = n_is < 30
    oos_sign_flip  = (not math.isnan(oos_sharpe)) and oos_sharpe < 0 and is_sharpe > 0
    severe_degrade = (degradation is not None) and degradation < 0.50
    # survivable: p95 MaxDD (worst-case tail) should be < 60% on $100 account
    unsurvivable   = (not math.isnan(p95_dd)) and p95_dd < -60.0

    reasons = []
    if low_n:         reasons.append(f"n_IS={n_is} < 30 (too few trades)")
    if oos_sign_flip: reasons.append("OOS Sharpe sign flip")
    if severe_degrade:reasons.append(f"degradation={deg_str} < 0.50 (severe)")
    if unsurvivable:  reasons.append(f"p95 MaxDD={p95_dd:.1f}% exceeds -60% on $100 (near-wipeout risk)")

    is_overfit = bool(oos_sign_flip or severe_degrade or low_n)

    if reasons:
        verdict = "shelve"
    else:
        verdict = "deploy-to-paper"

    print(f"\n{'='*70}")
    print(f"  VERDICT: {verdict.upper()}")
    if reasons:
        for r in reasons:
            print(f"    - {r}")
    else:
        print(f"    OOS Sharpe holds ({oos_sharpe:.3f} vs IS {is_sharpe:.3f}, degradation={deg_str})")
        print(f"    p95 MaxDD is survivable ({p95_dd:.1f}%)")
    print(f"  is_overfit = {is_overfit}")
    print(f"{'='*70}\n")

    # ── Write result ───────────────────────────────────────────────────────
    summary_parts = []
    summary_parts.append(
        f"IS Sharpe={is_sharpe:.3f} (n={n_is} trades, {is_dates[0]}→{is_dates[1]}). "
        f"OOS Sharpe={oos_sharpe:.3f} (n={m_oos['n']} trades, {oos_dates[0]}→{oos_dates[1]}). "
        f"Degradation={deg_str}."
    )
    summary_parts.append(
        f"Block-bootstrap p95 MaxDD={p95_dd:.1f}% (≈ -${p95_dollar:.2f} on $100). "
    )
    if reasons:
        summary_parts.append("Shelved: " + "; ".join(reasons) + ".")
    else:
        summary_parts.append("No red flags found — acceptable for paper trading.")

    result = {
        "task":         "soxl_regime_oos",
        "is_sharpe":    is_sharpe,
        "oos_sharpe":   oos_sharpe,
        "is_cagr":      m_is["cagr"],
        "oos_cagr":     m_oos["cagr"],
        "is_max_dd":    m_is["max_dd"],
        "oos_max_dd":   m_oos["max_dd"],
        "n_trades_is":  n_is,
        "n_trades_oos": m_oos["n"],
        "degradation":  round(degradation, 4) if degradation is not None else None,
        "bootstrap_p5_maxdd":  boot["p5"],
        "bootstrap_p50_maxdd": boot["p50"],
        "p95_maxdd":           p95_dd,
        "is_split":     f"{is_dates[0]}→{is_dates[1]}",
        "oos_split":    f"{oos_dates[0]}→{oos_dates[1]}",
        "best_config":  {"dip": DIP_ATR_BACK, "mp": MIN_PROFIT_ATR,
                         "adds": MAX_ADDS, "frac": ADD_FRACTION},
        "verdict":      verdict,
        "is_overfit":   is_overfit,
        "flags":        reasons,
        "summary":      " ".join(summary_parts),
        "success":      True,
    }

    out = Path("/Users/quarki3/swarm_results/completed/soxl_regime_oos.json")
    out.write_text(json.dumps(result, indent=2))
    print(f"  Written → {out}")
    return result


if __name__ == "__main__":
    main()
