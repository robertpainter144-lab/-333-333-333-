"""
Comprehensive strategy comparison.
Tests all meaningful momentum signals × risk configs × portfolio constructions
over max available history. Downloads price data once, runs everything in memory.

Usage:
    python3.10 strategy_compare.py
"""
import sys, itertools
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
from config import STRATEGY_CONFIGS, LARGE_CAP_UNIVERSE, LEVERAGED_ETF_UNIVERSE

# ── Universe ───────────────────────────────────────────────────────────────

STRATEGY_IDS = list(STRATEGY_CONFIGS.keys())[:5]

def _all_symbols():
    syms = {"SPY"}
    for sid in STRATEGY_IDS:
        syms.update(STRATEGY_CONFIGS[sid].get("universe", []))
    return sorted(syms)

# ── Momentum signals (return score dict {sym: float}) ─────────────────────

def _roc(series, n):
    if len(series) <= n: return 0.0
    v0 = float(series.iloc[-n-1])
    return (float(series.iloc[-1]) / v0 - 1) if v0 > 0 else 0.0

def _vol(series, n=20):
    if len(series) < n+1: return 1.0
    r = series.pct_change().dropna().iloc[-n:]
    return float(r.std() * np.sqrt(252)) if len(r) > 1 else 1.0

def _sma(series, n):
    if len(series) < n: return float(series.iloc[-1])
    return float(series.iloc[-n:].mean())

def score_blend_20_60(sym, hist):
    p = hist[sym].dropna()
    return 0.5*_roc(p,20) + 0.5*_roc(p,60)

def score_roc_5(sym, hist):
    return _roc(hist[sym].dropna(), 5)

def score_roc_20(sym, hist):
    return _roc(hist[sym].dropna(), 20)

def score_roc_60(sym, hist):
    return _roc(hist[sym].dropna(), 60)

def score_roc_252(sym, hist):
    return _roc(hist[sym].dropna(), 252)

def score_mom_12_1(sym, hist):
    p = hist[sym].dropna()
    return _roc(p, 252) - _roc(p, 21)

def score_vol_adj(sym, hist):
    p   = hist[sym].dropna()
    raw = 0.5*_roc(p,20) + 0.5*_roc(p,60)
    v   = _vol(p, 20)
    return raw / max(v, 0.01)

def score_sma_trend(sym, hist):
    p = hist[sym].dropna()
    if len(p) < 200: return 0.0
    above = float(p.iloc[-1]) > _sma(p, 200)
    raw   = 0.5*_roc(p,20) + 0.5*_roc(p,60)
    return raw if above else -999.0   # SMA200 filter: don't hold downtrends

def score_dual_momentum(sym, hist):
    # Absolute momentum: only hold if positive 12M return
    p = hist[sym].dropna()
    abs_mom = _roc(p, 252)
    if abs_mom < 0:
        return -999.0
    return abs_mom - _roc(p, 21)  # 12-1 relative momentum

def score_equal_weight(sym, hist):
    return 1.0   # ignores signal — equal weight all available


def _rsi(series, n=5):
    """Wilder RSI on a price series."""
    deltas = series.diff().dropna()
    if len(deltas) < n:
        return 50.0
    window = deltas.iloc[-n:]
    gains  = float(window.where(window > 0, 0.0).sum())
    losses = float(-window.where(window < 0, 0.0).sum())
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))


def score_regime_switch(sym, hist):
    """User-proposed regime-switching strategy.

    Regime (computed on SPY):
      Trending      : SMA50 > SMA200  AND  20d RV < 60d RV
      MeanReverting : SMA50 < SMA200  OR   5d RV / 60d RV > 1.5
      Mixed         : everything else

    Action:
      Trending      → 12-1 cross-sectional momentum
      MeanReverting → enter only if 5d RSI < 30, score = -RSI (more oversold = higher)
      Mixed         → 20d momentum, gated to positive only (defensive)

    Free parameters: vol_burst_ratio (1.5), rsi_lookback (5), rsi_oversold (30).
    Other constants are textbook defaults — 50/200 SMA, 12-1 momentum.
    """
    if "SPY" not in hist.columns:
        return 0.0
    spy = hist["SPY"].dropna()
    if len(spy) < 200:
        return 0.0

    sma50  = float(spy.iloc[-50:].mean())
    sma200 = float(spy.iloc[-200:].mean())
    spy_rets = spy.pct_change().dropna()
    if len(spy_rets) < 60:
        return 0.0
    rv20 = float(spy_rets.iloc[-20:].std())
    rv60 = float(spy_rets.iloc[-60:].std())
    rv5  = float(spy_rets.iloc[-5:].std())

    trending      = (sma50 > sma200) and (rv20 < rv60)
    mean_reverting = (sma50 < sma200) or (rv5 / max(rv60, 1e-9) > 1.5)
    # Trending wins if both flag true (Trending check first, mean_reverting fallback)

    p = hist[sym].dropna()
    if len(p) < 252:
        return 0.0

    if trending:
        return _roc(p, 252) - _roc(p, 21)
    if mean_reverting:
        rsi5 = _rsi(p, n=5)
        if rsi5 < 30.0:
            return -rsi5   # more oversold → higher score
        return -999.0      # not oversold → skip
    # Mixed regime: defensive 20d momentum, positive-only gate
    r20 = _roc(p, 20)
    return r20 if r20 > 0 else -999.0


SIGNALS = {
    "blend_20_60    (current)":  score_blend_20_60,
    "roc_5d         (ultra-fast)": score_roc_5,
    "roc_20d        (fast)":     score_roc_20,
    "roc_60d        (medium)":   score_roc_60,
    "roc_252d       (1-year)":   score_roc_252,
    "mom_12_1       (classic)":  score_mom_12_1,
    "vol_adj        (risk-adj)": score_vol_adj,
    "sma200_trend   (trend)":    score_sma_trend,
    "dual_momentum  (abs+rel)":  score_dual_momentum,
    "equal_weight   (baseline)": score_equal_weight,
    "regime_switch  (mom/MR)":   score_regime_switch,
}

# ── Risk configurations ────────────────────────────────────────────────────

LADDER = [(0.10, 0.75), (0.15, 0.50), (0.20, 0.25), (0.25, 0.0)]

RISK_CONFIGS = {
    "no_risk":   dict(trail_stop=None,  cap=1.0,  ladder=None,  reb=1),
    "optimized": dict(trail_stop=0.12,  cap=0.40, ladder=LADDER, reb=3),
    "loose":     dict(trail_stop=0.20,  cap=0.60, ladder=LADDER, reb=3),
    "tight":     dict(trail_stop=0.08,  cap=0.30, ladder=LADDER, reb=2),
}

# ── Core simulation (shares data across runs) ─────────────────────────────

def simulate(close, low_df, strategy_ids, score_fn, risk, initial_capital=100_000, warmup=120):
    trail_stop  = risk["trail_stop"]
    cap_frac    = risk["cap"]
    ladder      = risk["ladder"]
    reb_days    = risk["reb"]

    n = len(strategy_ids)
    base_w      = [2.0 if i == 0 else 1.0 for i in range(n)]
    bt_weights  = [w / sum(base_w) for w in base_w]

    cash     = float(initial_capital)
    holdings = {}
    peak_eq  = float(initial_capital)
    last_reb = None
    exposure_level = 1.0
    tiers_fired    = set()

    equity_curve = {}
    trades = []

    trade_dates = close.index[warmup:]

    for date in trade_dates:
        prices = {s: float(close.at[date,s])
                  for s in close.columns
                  if not pd.isna(close.at[date,s])}
        lows   = ({s: float(low_df.at[date,s])
                   for s in low_df.columns
                   if not pd.isna(low_df.at[date,s])}
                  if low_df is not None else prices)

        # Mark to market
        port_val = cash + sum(info["qty"]*prices.get(s, info["cost"])
                               for s,info in holdings.items())

        # Trailing stop
        if trail_stop:
            for sym in list(holdings.keys()):
                if sym not in prices: continue
                p_close = prices[sym]
                p_check = lows.get(sym, p_close)
                peak    = holdings[sym]["peak"]
                stop_px = peak * (1 - trail_stop)
                if p_check < stop_px:
                    fill = min(p_check, stop_px)
                    qty  = holdings[sym]["qty"]
                    cash += qty * fill
                    trades.append({"date":date,"sym":sym,"side":"sell","pnl":(fill-holdings[sym]["cost"])*qty})
                    del holdings[sym]
                else:
                    if p_close > holdings[sym]["peak"]:
                        holdings[sym]["peak"] = p_close

        # Drawdown ladder / protection
        port_val  = cash + sum(info["qty"]*prices.get(s,info["cost"]) for s,info in holdings.items())
        prev_peak = peak_eq
        peak_eq   = max(peak_eq, port_val)
        dd        = (peak_eq - port_val) / peak_eq if peak_eq > 0 else 0

        if peak_eq > prev_peak:
            tiers_fired.clear()
            exposure_level = 1.0

        if ladder:
            flagged = set()
            for thresh, tgt_exp in sorted(ladder):
                if dd >= thresh and thresh not in tiers_fired and exposure_level > tgt_exp:
                    sell_frac = 1.0 - (tgt_exp / exposure_level) if exposure_level > 0 else 1.0
                    for sym in list(holdings.keys()):
                        if sym in flagged or sym not in prices: continue
                        qty = holdings[sym]["qty"] * sell_frac
                        if qty > 0:
                            cash += qty * prices[sym]
                            holdings[sym]["qty"] -= qty
                            if holdings[sym]["qty"] <= 0: del holdings[sym]
                            trades.append({"date":date,"sym":sym,"side":"sell","pnl":0})
                            flagged.add(sym)
                    tiers_fired.add(thresh)
                    exposure_level = tgt_exp

        # Rebalance
        should_reb = last_reb is None or (date - last_reb).days >= reb_days
        if should_reb:
            hist   = close.loc[:date]
            target = {}

            for i, sid in enumerate(strategy_ids):
                cfg      = STRATEGY_CONFIGS.get(sid, {})
                universe = cfg.get("universe", LARGE_CAP_UNIVERSE)
                alloc_t  = cfg.get("alloc", "single_winner")
                avail    = [s for s in universe if s in close.columns]
                if not avail: continue

                port_val  = cash + sum(info["qty"]*prices.get(s,info["cost"]) for s,info in holdings.items())
                strat_cap = port_val * bt_weights[i]
                scores    = {s: score_fn(s, hist) for s in avail if s in hist.columns}
                ranked    = sorted(scores, key=lambda x: scores[x], reverse=True)
                # Filter out anything with sentinel score
                ranked    = [s for s in ranked if scores[s] > -100]

                if alloc_t == "single_winner" and ranked:
                    sym = ranked[0]
                    target[sym] = target.get(sym, 0.0) + strat_cap
                elif alloc_t in ("top3_equal","top5_equal"):
                    k   = 3 if alloc_t == "top3_equal" else 5
                    top = ranked[:min(k, len(ranked))]
                    for sym in top:
                        target[sym] = target.get(sym, 0.0) + strat_cap / len(top)

            # Apply position cap
            if cap_frac < 1.0:
                port_val = cash + sum(info["qty"]*prices.get(s,info["cost"]) for s,info in holdings.items())
                cap_val  = port_val * cap_frac
                for sym in list(target):
                    if target[sym] > cap_val: target[sym] = cap_val

            # Close positions not in target
            for sym in list(holdings.keys()):
                if sym not in target and sym in prices:
                    qty = holdings[sym]["qty"]
                    p   = prices[sym]
                    cash += qty * p
                    trades.append({"date":date,"sym":sym,"side":"sell","pnl":(p-holdings[sym]["cost"])*qty})
                    del holdings[sym]

            # Adjust toward target
            for sym, tgt_val in target.items():
                if sym not in prices or prices[sym] <= 0: continue
                p       = prices[sym]
                cur_qty = holdings.get(sym, {}).get("qty", 0.0)
                diff    = tgt_val - cur_qty * p
                if abs(diff) < max(1.0, initial_capital * 0.0001): continue
                qty = abs(diff) / p
                if diff > 0 and cash >= qty * p:
                    cash -= qty * p
                    if sym in holdings:
                        old_q, old_c = holdings[sym]["qty"], holdings[sym]["cost"]
                        new_q = old_q + qty
                        holdings[sym] = {"qty": new_q,
                                         "cost": (old_c*old_q + p*qty)/new_q,
                                         "peak": max(holdings[sym]["peak"], p)}
                    else:
                        holdings[sym] = {"qty": qty, "cost": p, "peak": p}
                    trades.append({"date":date,"sym":sym,"side":"buy","pnl":0})
                elif diff < 0:
                    sell_qty = min(qty, cur_qty)
                    if sell_qty > 0:
                        pnl = (p - holdings[sym]["cost"]) * sell_qty
                        cash += sell_qty * p
                        holdings[sym]["qty"] -= sell_qty
                        if holdings[sym]["qty"] <= 0: del holdings[sym]
                        trades.append({"date":date,"sym":sym,"side":"sell","pnl":pnl})
            last_reb = date

        # Snapshot
        port_val = cash + sum(info["qty"]*prices.get(s,info["cost"]) for s,info in holdings.items())
        equity_curve[date] = port_val

    ec = pd.Series(equity_curve)
    return ec, trades

# ── Metrics ────────────────────────────────────────────────────────────────

def metrics(ec, trades, cap):
    if ec.empty or len(ec) < 10:
        return {}
    final = float(ec.iloc[-1])
    days  = (ec.index[-1] - ec.index[0]).days
    years = max(days / 365.25, 0.1)
    cagr  = ((final / cap) ** (1/years) - 1) * 100
    rets  = ec.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    rm     = ec.cummax()
    max_dd = float(((ec - rm) / rm).min() * 100)
    sells  = [t for t in trades if t["side"] == "sell"]
    wins   = sum(1 for t in sells if t.get("pnl", 0) > 0)
    wr     = (wins / len(sells) * 100) if sells else 0
    return {"cagr": round(cagr,2), "sharpe": round(sharpe,2),
            "max_dd": round(abs(max_dd),2), "win_rate": round(wr,1),
            "final": round(final,2)}

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    syms = _all_symbols()
    end   = datetime.now()
    start = end - timedelta(days=14*365 + 120)

    print(f"Downloading {len(syms)} symbols (single fetch for all runs)...")
    raw = yf.download(syms, start=start.strftime("%Y-%m-%d"),
                      end=end.strftime("%Y-%m-%d"),
                      progress=False, auto_adjust=True, threads=True)
    if raw.empty:
        print("Download failed"); return

    close = raw["Close"].copy() if "Close" in raw else raw.copy()
    if isinstance(close, pd.Series):
        close = close.to_frame(syms[0])
    close.columns = [str(c) for c in close.columns]
    close = close.dropna(how="all").ffill()

    low_df = None
    if "Low" in raw:
        low_df = raw["Low"].copy()
        low_df.columns = [str(c) for c in low_df.columns]
        low_df = low_df.reindex(close.index).ffill()

    spy_full = close["SPY"].iloc[120:] if "SPY" in close.columns else pd.Series()

    CAP = 100_000
    results = []
    total = len(SIGNALS) * len(RISK_CONFIGS)
    done  = 0

    print(f"Running {total} combinations...\n")

    for sig_name, score_fn in SIGNALS.items():
        for risk_name, risk in RISK_CONFIGS.items():
            done += 1
            try:
                ec, trades_list = simulate(close, low_df, STRATEGY_IDS,
                                           score_fn, risk,
                                           initial_capital=CAP)
                m = metrics(ec, trades_list, CAP)
                if not m: continue

                # 3M and 1Y sub-period metrics
                cutoff_3m = ec.index[-1] - pd.DateOffset(months=3)
                cutoff_1y = ec.index[-1] - pd.DateOffset(years=1)
                ec_3m = ec.loc[ec.index >= cutoff_3m]
                ec_1y = ec.loc[ec.index >= cutoff_1y]
                ret_3m = (ec_3m.iloc[-1]/ec_3m.iloc[0]-1)*100 if len(ec_3m)>1 else 0
                ret_1y = (ec_1y.iloc[-1]/ec_1y.iloc[0]-1)*100 if len(ec_1y)>1 else 0

                results.append({
                    "signal":    sig_name.strip(),
                    "risk":      risk_name,
                    "cagr":      m["cagr"],
                    "sharpe":    m["sharpe"],
                    "max_dd":    m["max_dd"],
                    "win_rate":  m["win_rate"],
                    "final":     m["final"],
                    "ret_3m":    round(ret_3m, 1),
                    "ret_1y":    round(ret_1y, 1),
                })
                print(f"  [{done:>3}/{total}] {sig_name.split('(')[0].strip():<22} "
                      f"+ {risk_name:<10}  "
                      f"CAGR {m['cagr']:>6.1f}%  Sharpe {m['sharpe']:>5.2f}  "
                      f"DD {m['max_dd']:>5.1f}%  "
                      f"3M {ret_3m:>+6.1f}%  1Y {ret_1y:>+6.1f}%")
            except Exception as e:
                print(f"  [{done:>3}/{total}] ERROR {sig_name} + {risk_name}: {e}")

    if not results:
        print("No results"); return

    # ── Full-period table sorted by Sharpe ────────────────────────────────
    results.sort(key=lambda r: (r["sharpe"], r["cagr"]), reverse=True)

    print("\n\n" + "="*88)
    print("  FULL PERIOD (~14y)  —  sorted by Sharpe")
    print("="*88)
    print(f"  {'#':>3}  {'Signal':<22}  {'Risk':<10}  "
          f"{'CAGR':>6}  {'Sharpe':>6}  {'MaxDD':>6}  "
          f"{'3M ret':>7}  {'1Y ret':>7}  {'Final $':>12}")
    print("-"*88)
    for i, r in enumerate(results[:20], 1):
        print(f"  {i:>3}  {r['signal']:<22}  {r['risk']:<10}  "
              f"{r['cagr']:>5.1f}%  {r['sharpe']:>6.2f}  {r['max_dd']:>5.1f}%  "
              f"{r['ret_3m']:>+6.1f}%  {r['ret_1y']:>+6.1f}%  "
              f"${r['final']:>11,.0f}")
    print("="*88)

    # ── Best 3M performance (closest to QC's 87%) ─────────────────────────
    by_3m = sorted(results, key=lambda r: r["ret_3m"], reverse=True)
    print("\n  TOP 10 by recent 3M return  (QC weighted avg = +87.0%)")
    print(f"  {'Signal':<22}  {'Risk':<10}  {'3M':>7}  {'1Y':>7}  {'CAGR':>6}  {'Sharpe':>6}  {'MaxDD':>6}")
    print("-"*72)
    for r in by_3m[:10]:
        print(f"  {r['signal']:<22}  {r['risk']:<10}  "
              f"{r['ret_3m']:>+6.1f}%  {r['ret_1y']:>+6.1f}%  "
              f"{r['cagr']:>5.1f}%  {r['sharpe']:>6.2f}  {r['max_dd']:>5.1f}%")

    # ── Winner ────────────────────────────────────────────────────────────
    best = results[0]
    print(f"\n  ★  BEST OVERALL: {best['signal']} + {best['risk']}")
    print(f"     CAGR {best['cagr']:.1f}%  |  Sharpe {best['sharpe']:.2f}  |  "
          f"MaxDD {best['max_dd']:.1f}%  |  Final ${best['final']:,.0f}")
    print(f"     Recent 3M: {best['ret_3m']:+.1f}%  |  1Y: {best['ret_1y']:+.1f}%\n")

    # SPY reference
    if not spy_full.empty:
        spy_years = (spy_full.index[-1] - spy_full.index[0]).days / 365.25
        spy_cagr  = ((spy_full.iloc[-1]/spy_full.iloc[0])**(1/spy_years)-1)*100
        spy_3m    = (spy_full.iloc[-1]/spy_full.loc[spy_full.index >= spy_full.index[-1]-pd.DateOffset(months=3)].iloc[0]-1)*100
        print(f"  SPY buy-and-hold: CAGR {spy_cagr:.1f}%  |  3M {spy_3m:+.1f}%")

if __name__ == "__main__":
    main()
