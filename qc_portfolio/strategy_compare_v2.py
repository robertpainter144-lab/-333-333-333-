"""
strategy_compare_v2.py — Exhaustive multi-phase strategy tournament.

5 phases, ~200 combinations:
  1. Signal tournament      19 signals × fixed universe/alloc/risk
  2. Universe tournament    top-5 signals × 7 universes
  3. Allocation tournament  top-5 signals × best universe × 7 methods
  4. Risk tournament        top-3 combos × 6 risk configs
  5. Grand finals           top-3 sig × top-2 uni × top-3 alloc × top-2 risk

Composite score = geometric mean of (CAGR/100, Sharpe, dd_quality)
  — ranks strategies by all three objectives simultaneously.

Output: stdout + /tmp/strategy_v2_results.txt
Usage:  python3.10 strategy_compare_v2.py [--phase N]
"""
import sys, os
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

OUT_FILE = "/tmp/strategy_v2_results.txt"

# ── Universes ──────────────────────────────────────────────────────────────

UNIVERSES = {
    "qc_current": sorted(set([
        "AMD","TSLA","AMZN","AAPL","SPXL",
        "TQQQ","UPRO","SOXL","TECL","LABU","FAS","FNGU","TNA",
        "MSFT","NVDA","GOOGL","META","AVGO","JPM","QQQ",
        "GLD","TLT","SPY","IWM","EFA","UDOW",
    ])),
    "leveraged_etf": [
        "TQQQ","UPRO","SOXL","TECL","LABU","FAS","FNGU","TNA","SPXL","UDOW",
    ],
    "mega_tech": [
        "NVDA","MSFT","AAPL","META","GOOGL","AMZN","TSLA","AMD","AVGO",
        "ORCL","CRM","ADBE","QCOM",
    ],
    "ai_leaders": [
        "NVDA","AMD","AVGO","MSFT","GOOGL","META","AMZN",
        "TSLA","ORCL","CRM","PLTR","ANET","SMCI",
    ],
    "sectors": [
        "XLK","XLF","XLE","XLV","XLI","XLY","XLB","XLU","XLRE","XLP","SMH","IBB",
    ],
    "broad_mix": sorted(set([
        "TQQQ","UPRO","SOXL","TECL","SPXL",
        "NVDA","MSFT","AAPL","META","GOOGL","AMZN","TSLA","AMD","AVGO",
        "XLK","XLF","XLE","XLV","XLI",
        "GLD","TLT","QQQ","PLTR","ORCL",
    ])),
    "thematic": [
        "TQQQ","SOXL","UPRO","TECL",
        "NVDA","AMD","AVGO","MSFT","META","GOOGL","TSLA","AMZN",
        "PLTR","ORCL",
    ],
}

ALL_SYMS = sorted(set(
    ["SPY"] + [s for uni in UNIVERSES.values() for s in uni]
))

# ── Helpers ────────────────────────────────────────────────────────────────

def _roc(s, n):
    if len(s) <= n: return 0.0
    v0 = float(s.iloc[-n-1])
    return (float(s.iloc[-1]) / v0 - 1) if v0 > 0 else 0.0

def _vol(s, n=20):
    if len(s) < n + 1: return 1.0
    r = s.pct_change().dropna().iloc[-n:]
    return max(float(r.std() * np.sqrt(252)), 0.001) if len(r) > 1 else 1.0

def _sma(s, n):
    if len(s) < n: return float(s.iloc[-1])
    return float(s.iloc[-n:].mean())

def _ema(s, n):
    if len(s) < n: return float(s.iloc[-1])
    return float(s.ewm(span=n, adjust=False).mean().iloc[-1])

def _macd_hist(s):
    if len(s) < 35: return 0.0
    ml  = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
    sig = ml.ewm(span=9, adjust=False).mean()
    h   = float((ml - sig).iloc[-1])
    px  = float(s.iloc[-1])
    return h / px if px > 0 else 0.0

# ── 19 Signals ─────────────────────────────────────────────────────────────

def sig_blend_20_60(sym, hist):
    p = hist[sym].dropna()
    return 0.5*_roc(p,20) + 0.5*_roc(p,60)

def sig_roc_5(sym, hist):
    return _roc(hist[sym].dropna(), 5)

def sig_roc_20(sym, hist):
    return _roc(hist[sym].dropna(), 20)

def sig_roc_60(sym, hist):
    return _roc(hist[sym].dropna(), 60)

def sig_roc_252(sym, hist):
    return _roc(hist[sym].dropna(), 252)

def sig_mom_12_1(sym, hist):
    p = hist[sym].dropna()
    return _roc(p, 252) - _roc(p, 21)

def sig_vol_adj(sym, hist):
    p = hist[sym].dropna()
    return (0.5*_roc(p,20) + 0.5*_roc(p,60)) / _vol(p, 20)

def sig_sma200_trend(sym, hist):
    p = hist[sym].dropna()
    if len(p) < 200: return 0.0
    raw = 0.5*_roc(p,20) + 0.5*_roc(p,60)
    return raw if float(p.iloc[-1]) > _sma(p, 200) else -999.0

def sig_dual_momentum(sym, hist):
    p = hist[sym].dropna()
    if _roc(p, 252) < 0: return -999.0
    return _roc(p, 252) - _roc(p, 21)

def sig_equal_weight(sym, hist):
    return 1.0

def sig_vol_adj_v2(sym, hist):
    """4-lookback vol-adjusted blend (5/20/60/252d)."""
    p = hist[sym].dropna()
    raw = 0.15*_roc(p,5) + 0.30*_roc(p,20) + 0.35*_roc(p,60) + 0.20*(_roc(p,252) if len(p)>=253 else _roc(p,60))
    return raw / _vol(p, 20)

def sig_breakout_52w(sym, hist):
    """Proximity to 52-week high × recent momentum."""
    p = hist[sym].dropna()
    if len(p) < 60: return 0.0
    n    = min(252, len(p))
    hi   = float(p.iloc[-n:].max())
    cur  = float(p.iloc[-1])
    if hi <= 0: return 0.0
    return (cur / hi) * (1 + max(_roc(p, 20), 0))

def sig_momentum_quality(sym, hist):
    """Annualized Sharpe of recent 60-day returns (consistency)."""
    p = hist[sym].dropna()
    if len(p) < 62: return 0.0
    r = p.pct_change().dropna().iloc[-60:]
    return float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0

def sig_confluence(sym, hist):
    """Score only when 5d/20d/60d/252d all agree positive."""
    p = hist[sym].dropna()
    rs = [_roc(p,5), _roc(p,20), _roc(p,60)]
    if len(p) >= 253: rs.append(_roc(p,252))
    if all(r > 0 for r in rs):
        return (0.3*_roc(p,20) + 0.4*_roc(p,60) + 0.3*rs[-1]) / _vol(p, 20)
    return -999.0

def sig_macd(sym, hist):
    return _macd_hist(hist[sym].dropna())

def sig_ema_ratio(sym, hist):
    """EMA(5)/EMA(50) − 1, vol-normalised."""
    p = hist[sym].dropna()
    if len(p) < 55: return 0.0
    ratio = _ema(p, 5) / max(_ema(p, 50), 1e-6) - 1.0
    return ratio / _vol(p, 20)

def sig_alpha_vs_spy(sym, hist):
    """Vol-adjusted excess return vs SPY."""
    p = hist[sym].dropna()
    if "SPY" not in hist.columns or len(p) < 62: return sig_vol_adj(sym, hist)
    spy = hist["SPY"].reindex(p.index).dropna()
    if len(spy) < 62: return sig_vol_adj(sym, hist)
    alpha = (0.5*(_roc(p,20)-_roc(spy,20)) + 0.5*(_roc(p,60)-_roc(spy,60)))
    return alpha / _vol(p, 20)

def sig_trend_intensity(sym, hist):
    """% positive days (20d) × magnitude."""
    p = hist[sym].dropna()
    if len(p) < 22: return 0.0
    r = p.pct_change().dropna().iloc[-20:]
    return float((r > 0).mean()) * (1 + max(_roc(p, 20), 0)) - 0.5

def sig_adaptive_blend(sym, hist):
    """Meta-signal: weighted average of vol_adj + roc_252 + roc_5d."""
    p  = hist[sym].dropna()
    if len(p) < 30: return 0.0
    v  = _vol(p, 20)
    va = sig_vol_adj(sym, hist)
    r252 = (_roc(p,252)/max(v*np.sqrt(12),0.001)) if len(p)>=253 else 0.0
    r5   = _roc(p, 5) / max(v, 0.001)
    return 0.5*va + 0.3*r252 + 0.2*r5

SIGNALS = {
    "blend_20_60":      sig_blend_20_60,
    "roc_5d":           sig_roc_5,
    "roc_20d":          sig_roc_20,
    "roc_60d":          sig_roc_60,
    "roc_252d":         sig_roc_252,
    "mom_12_1":         sig_mom_12_1,
    "vol_adj":          sig_vol_adj,
    "sma200_trend":     sig_sma200_trend,
    "dual_momentum":    sig_dual_momentum,
    "equal_weight":     sig_equal_weight,
    "vol_adj_v2":       sig_vol_adj_v2,
    "breakout_52w":     sig_breakout_52w,
    "momentum_quality": sig_momentum_quality,
    "confluence":       sig_confluence,
    "macd":             sig_macd,
    "ema_ratio":        sig_ema_ratio,
    "alpha_vs_spy":     sig_alpha_vs_spy,
    "trend_intensity":  sig_trend_intensity,
    "adaptive_blend":   sig_adaptive_blend,
}

# ── Allocation methods ─────────────────────────────────────────────────────

ALLOC_METHODS = ["top1","top2_70_30","top3_equal","top5_equal",
                 "proportional","kelly_half","tiered"]

def allocate(ranked, scores, cap, method):
    ranked = [s for s in ranked if scores.get(s, -999) > -100]
    if not ranked: return {}

    if method == "top1":
        return {ranked[0]: cap}

    elif method == "top2_70_30":
        if len(ranked) >= 2:
            return {ranked[0]: cap*0.70, ranked[1]: cap*0.30}
        return {ranked[0]: cap}

    elif method == "top3_equal":
        top = ranked[:min(3, len(ranked))]
        return {s: cap/len(top) for s in top}

    elif method == "top5_equal":
        top = ranked[:min(5, len(ranked))]
        return {s: cap/len(top) for s in top}

    elif method == "proportional":
        top  = ranked[:min(5, len(ranked))]
        pos  = {s: max(scores[s], 1e-6) for s in top if scores[s] > 0}
        if not pos: return {ranked[0]: cap}
        tot  = sum(pos.values())
        return {s: cap * v / tot for s, v in pos.items()}

    elif method == "kelly_half":
        top = ranked[:min(5, len(ranked))]
        pos = {s: max(scores[s], 1e-6) for s in top if scores[s] > 0}
        if not pos: return {ranked[0]: cap}
        tot  = sum(pos.values())
        wts  = {s: min(0.5 * v / tot * len(pos), 0.65) for s, v in pos.items()}
        tot2 = sum(wts.values())
        return {s: cap * w / tot2 for s, w in wts.items()}

    elif method == "tiered":
        if len(ranked) >= 3:
            return {ranked[0]: cap*0.50, ranked[1]: cap*0.30, ranked[2]: cap*0.20}
        elif len(ranked) == 2:
            return {ranked[0]: cap*0.65, ranked[1]: cap*0.35}
        return {ranked[0]: cap}

    return {ranked[0]: cap}

# ── Risk configs ───────────────────────────────────────────────────────────

LADDER = [(0.10, 0.75), (0.15, 0.50), (0.20, 0.25), (0.25, 0.0)]

RISK_CONFIGS = {
    "no_risk":            {"trail": None, "ladder": None, "reb": 1, "regime": None},
    "regime_sma200":      {"trail": None, "ladder": None, "reb": 1, "regime": "sma200"},
    "regime_ema50":       {"trail": None, "ladder": None, "reb": 1, "regime": "ema50"},
    "dd_ladder":          {"trail": None, "ladder": LADDER, "reb": 1, "regime": None},
    "weekly_rebalance":   {"trail": None, "ladder": None, "reb": 7, "regime": None},
    "regime_plus_ladder": {"trail": None, "ladder": LADDER, "reb": 1, "regime": "sma200"},
}

# ── Core simulation ────────────────────────────────────────────────────────

def simulate(close, low_df, regime_ser, universe, score_fn, alloc_method,
             risk, initial_capital=100_000, warmup=252):
    """
    Single-strategy daily simulation.
    regime_ser: {"sma200": bool Series, "ema50": bool Series}  True = bearish
    Returns (equity_curve Series, trades list)
    """
    trail    = risk.get("trail")
    ladder   = risk.get("ladder")
    reb_days = risk.get("reb", 1)
    regime   = risk.get("regime")

    avail = [s for s in universe if s in close.columns]
    if not avail:
        return pd.Series(dtype=float), []

    cash      = float(initial_capital)
    holdings  = {}   # {sym: {"qty", "cost", "peak"}}
    peak_eq   = float(initial_capital)
    last_reb  = None
    exp_level = 1.0
    tiers     = set()
    equity    = {}
    trades    = []

    for date in close.index[warmup:]:
        prices = {s: float(close.at[date, s])
                  for s in avail
                  if not pd.isna(close.at[date, s])}
        if "SPY" in close.columns and not pd.isna(close.at[date, "SPY"]):
            prices["SPY"] = float(close.at[date, "SPY"])
        lows = ({s: float(low_df.at[date, s])
                 for s in avail if not pd.isna(low_df.at[date, s])}
                if low_df is not None else {})

        # Trailing stop
        if trail:
            for sym in list(holdings):
                if sym not in prices: continue
                pk  = holdings[sym]["peak"]
                chk = lows.get(sym, prices[sym])
                if chk < pk * (1 - trail):
                    fill = min(chk, pk*(1-trail))
                    q    = holdings[sym]["qty"]
                    cash += q * fill
                    trades.append({"date":date,"sym":sym,"side":"sell",
                                   "pnl":(fill-holdings[sym]["cost"])*q})
                    del holdings[sym]
                elif prices[sym] > holdings[sym]["peak"]:
                    holdings[sym]["peak"] = prices[sym]

        pv = cash + sum(info["qty"]*prices.get(s,info["cost"])
                        for s,info in holdings.items())
        prev_peak = peak_eq
        peak_eq   = max(peak_eq, pv)
        dd        = (peak_eq - pv) / peak_eq if peak_eq > 0 else 0.0

        if peak_eq > prev_peak:
            tiers.clear()
            exp_level = 1.0

        # DD ladder
        if ladder:
            for thresh, tgt in sorted(ladder):
                if dd >= thresh and thresh not in tiers and exp_level > tgt:
                    sf = (1.0 - tgt/exp_level) if exp_level > 0 else 1.0
                    for sym in list(holdings):
                        if sym not in prices: continue
                        sq = holdings[sym]["qty"] * sf
                        if sq > 0:
                            cash += sq * prices[sym]
                            holdings[sym]["qty"] -= sq
                            if holdings[sym]["qty"] <= 1e-6: del holdings[sym]
                            trades.append({"date":date,"sym":sym,"side":"sell","pnl":0})
                    tiers.add(thresh)
                    exp_level = tgt

        # Regime scalar
        scalar = 1.0
        if regime and date in regime_ser.get(regime, pd.Series(dtype=bool)).index:
            if regime_ser[regime].get(date, False):
                scalar = 0.5

        # Rebalance
        if last_reb is None or (date - last_reb).days >= reb_days:
            hist   = close.loc[:date]
            scores = {s: score_fn(s, hist) for s in avail if s in hist.columns}
            ranked = sorted(avail, key=lambda x: scores.get(x,-999), reverse=True)

            pv        = cash + sum(info["qty"]*prices.get(s,info["cost"])
                                   for s,info in holdings.items())
            investable = pv * scalar
            target     = allocate(ranked, scores, investable, alloc_method)

            # Trim holdings if regime reduced investable
            if scalar < 1.0:
                invested = sum(info["qty"]*prices.get(s,info["cost"])
                               for s,info in holdings.items())
                if invested > investable * 1.05:
                    sf = 1 - investable / invested
                    for sym in list(holdings):
                        if sym not in prices: continue
                        sq = holdings[sym]["qty"] * sf
                        if sq > 0:
                            cash += sq * prices[sym]
                            holdings[sym]["qty"] -= sq
                            if holdings[sym]["qty"] <= 1e-6: del holdings[sym]

            # Close exits
            for sym in list(holdings):
                if sym not in target and sym in prices:
                    q = holdings[sym]["qty"]
                    p = prices[sym]
                    cash += q * p
                    trades.append({"date":date,"sym":sym,"side":"sell",
                                   "pnl":(p-holdings[sym]["cost"])*q})
                    del holdings[sym]

            # Sells first, then buys
            sells, buys = [], []
            for sym, tv in target.items():
                if sym not in prices or prices[sym] <= 0: continue
                p    = prices[sym]
                cq   = holdings.get(sym,{}).get("qty",0.0)
                diff = tv - cq * p
                if abs(diff) < max(1.0, initial_capital*0.0001): continue
                (sells if diff < 0 else buys).append((sym, abs(diff)/p, p))

            for sym, qty, p in sells:
                sq = min(qty, holdings.get(sym,{}).get("qty",0.0))
                if sq > 1e-6 and sym in holdings:
                    cash += sq * p
                    trades.append({"date":date,"sym":sym,"side":"sell",
                                   "pnl":(p-holdings[sym]["cost"])*sq})
                    holdings[sym]["qty"] -= sq
                    if holdings[sym]["qty"] <= 1e-6: del holdings[sym]

            for sym, qty, p in buys:
                cost = qty * p
                if cash >= cost - 0.01:
                    cash -= cost
                elif cash > p * 0.1:
                    qty = cash / p; cost = cash; cash = 0
                else:
                    continue
                if sym in holdings:
                    oq, oc = holdings[sym]["qty"], holdings[sym]["cost"]
                    nq = oq + qty
                    holdings[sym] = {"qty":nq,"cost":(oc*oq+p*qty)/nq,
                                     "peak":max(holdings[sym]["peak"],p)}
                else:
                    holdings[sym] = {"qty":qty,"cost":p,"peak":p}
                trades.append({"date":date,"sym":sym,"side":"buy","pnl":0})

            last_reb = date

        pv = cash + sum(info["qty"]*prices.get(s,info["cost"])
                        for s,info in holdings.items())
        equity[date] = pv

    return pd.Series(equity), trades

# ── Metrics ────────────────────────────────────────────────────────────────

def calc_metrics(ec, trades, cap):
    if ec.empty or len(ec) < 20: return None
    final  = float(ec.iloc[-1])
    years  = max((ec.index[-1]-ec.index[0]).days/365.25, 0.1)
    cagr   = ((final/cap)**(1/years)-1)*100
    rets   = ec.pct_change().dropna()
    sharpe = float(rets.mean()/rets.std()*np.sqrt(252)) if rets.std()>0 else 0.0
    rm     = ec.cummax()
    max_dd = float(((ec-rm)/rm).min()*100)
    neg    = rets[rets<0]
    sortino = float(rets.mean()/neg.std()*np.sqrt(252)) if len(neg)>1 and neg.std()>0 else 0.0
    calmar  = cagr / abs(max_dd) if max_dd else 0.0
    sells   = [t for t in trades if t["side"]=="sell"]
    wins    = sum(1 for t in sells if t.get("pnl",0)>0)
    wr      = wins/len(sells)*100 if sells else 0.0

    def _subret(months=None, years_=None):
        cut = (ec.index[-1]-pd.DateOffset(months=months) if months
               else ec.index[-1]-pd.DateOffset(years=years_))
        sub = ec.loc[ec.index>=cut]
        return (sub.iloc[-1]/sub.iloc[0]-1)*100 if len(sub)>1 else 0.0

    r3m = _subret(months=3)
    r6m = _subret(months=6)
    r1y = _subret(years_=1)
    r2y = _subret(years_=2)

    # Composite: geometric mean of normalised profit/speed/safety
    # profit_speed = CAGR contribution, safety = Sharpe × DD quality
    dd_q = max(0.0, 1 - abs(max_dd)/100)**1.5
    if cagr > 0 and sharpe > 0 and dd_q > 0:
        composite = (cagr/100 * sharpe * dd_q) ** (1/3) * 100
    else:
        composite = 0.0

    return dict(cagr=round(cagr,2), sharpe=round(sharpe,2), max_dd=round(abs(max_dd),2),
                sortino=round(sortino,2), calmar=round(calmar,3), win_rate=round(wr,1),
                final=round(final,2), ret_3m=round(r3m,1), ret_6m=round(r6m,1),
                ret_1y=round(r1y,1), ret_2y=round(r2y,1), composite=round(composite,2))

# ── Phase runner ───────────────────────────────────────────────────────────

def run_phase(title, combos, close, low_df, regime_ser, cap=100_000, fout=None):
    results = []
    total   = len(combos)
    hdr = f"\n{'='*100}\n  PHASE: {title}  ({total} combinations)\n{'='*100}"
    print(hdr)
    if fout: fout.write(hdr+"\n")

    for i, (label, uni, sfn, alloc, risk) in enumerate(combos, 1):
        try:
            ec, tlist = simulate(close, low_df, regime_ser, uni, sfn, alloc, risk,
                                 initial_capital=cap)
            m = calc_metrics(ec, tlist, cap)
            if not m: continue
            results.append({"label":label, **m})
            line = (f"  [{i:>3}/{total}]  {label:<55}  "
                    f"CAGR {m['cagr']:>7.1f}%  Sh {m['sharpe']:>5.2f}  "
                    f"DD {m['max_dd']:>5.1f}%  Comp {m['composite']:>5.2f}  "
                    f"3M {m['ret_3m']:>+6.1f}%  1Y {m['ret_1y']:>+6.1f}%")
            print(line)
            if fout: fout.write(line+"\n")
        except Exception as e:
            print(f"  [{i:>3}/{total}]  ERROR {label}: {e}")
    return results

def print_top(results, n=10, title="TOP RESULTS", fout=None):
    if not results:
        return
    sr = sorted(results, key=lambda r: r["composite"], reverse=True)
    hdr = (f"\n  {title} (by composite = ∛(CAGR × Sharpe × DD_quality))\n"
           f"  {'#':>3}  {'Label':<55}  {'CAGR':>7}  {'Sharpe':>6}  "
           f"{'MaxDD':>6}  {'Comp':>6}  {'3M':>7}  {'1Y':>7}  {'Sortino':>7}  {'Final $':>12}")
    sep = "  " + "-"*120
    print(hdr); print(sep)
    if fout: fout.write(hdr+"\n"+sep+"\n")
    for i, r in enumerate(sr[:n], 1):
        line = (f"  {i:>3}  {r['label']:<55}  "
                f"{r['cagr']:>6.1f}%  {r['sharpe']:>6.2f}  {r['max_dd']:>5.1f}%  "
                f"{r['composite']:>6.2f}  {r['ret_3m']:>+6.1f}%  {r['ret_1y']:>+6.1f}%  "
                f"{r['sortino']:>7.2f}  ${r['final']:>11,.0f}")
        print(line)
        if fout: fout.write(line+"\n")

def extract_field(label, key):
    for part in label.split("|"):
        part = part.strip()
        if part.startswith(key+"="):
            return part.split("=",1)[1].strip()
    return None

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    args       = sys.argv[1:]
    only_phase = None
    for i, a in enumerate(args):
        if "--phase" in a:
            try: only_phase = int(args[i+1]) if "=" not in a else int(a.split("=")[1])
            except: pass

    fout = open(OUT_FILE, "w")

    # Download
    print(f"Downloading {len(ALL_SYMS)} symbols (~14y history)...")
    end   = datetime.now()
    start = end - timedelta(days=14*365 + 252)
    raw   = yf.download(ALL_SYMS, start=start.strftime("%Y-%m-%d"),
                        end=end.strftime("%Y-%m-%d"),
                        progress=False, auto_adjust=True, threads=True)
    if raw.empty:
        print("Download failed"); return

    close = raw["Close"].copy() if "Close" in raw else raw.copy()
    if isinstance(close, pd.Series): close = close.to_frame(ALL_SYMS[0])
    close.columns = [str(c) for c in close.columns]
    close = close.dropna(how="all").ffill()

    low_df = None
    if "Low" in raw:
        low_df = raw["Low"].copy()
        low_df.columns = [str(c) for c in low_df.columns]
        low_df = low_df.reindex(close.index).ffill()

    print(f"  {len(close)} trading days × {len(close.columns)} symbols  "
          f"({close.index[0].date()} → {close.index[-1].date()})")

    # Pre-compute regime signals (avoid O(n²) rolling inside sim loop)
    spy = close["SPY"] if "SPY" in close.columns else None
    regime_ser = {}
    if spy is not None:
        regime_ser["sma200"] = spy < spy.rolling(200, min_periods=1).mean()
        ema50                = spy.ewm(span=50, adjust=False).mean()
        regime_ser["ema50"]  = ema50.diff() < 0  # True = declining = bearish

    CAP      = 100_000
    no_risk  = RISK_CONFIGS["no_risk"]
    base_uni = UNIVERSES["qc_current"]

    # ── Phase 1: Signal tournament ─────────────────────────────────────────
    top_sigs = list(SIGNALS.keys())[:5]  # fallback if phase 1 skipped
    if only_phase in (None, 1):
        p1 = []
        for sn, sfn in SIGNALS.items():
            p1.append((f"sig={sn:<22} uni=qc_current  alloc=top1  risk=no_risk",
                       base_uni, sfn, "top1", no_risk))
        r1 = run_phase("1 — SIGNAL TOURNAMENT", p1, close, low_df, regime_ser, fout=fout)
        print_top(r1, 10, "PHASE 1: TOP SIGNALS", fout)
        sorted_r1  = sorted(r1, key=lambda r: r["composite"], reverse=True)
        top_sigs   = [extract_field(r["label"],"sig") for r in sorted_r1[:5]]
        top_sigs   = [s for s in top_sigs if s]
        print(f"\n  → Top-5 signals: {top_sigs}")

    # ── Phase 2: Universe tournament ──────────────────────────────────────
    best_uni = "qc_current"  # fallback
    if only_phase in (None, 2):
        p2 = []
        for sn in top_sigs[:5]:
            sfn = SIGNALS[sn]
            for un, uni in UNIVERSES.items():
                p2.append((f"sig={sn:<22} uni={un:<15} alloc=top1  risk=no_risk",
                           uni, sfn, "top1", no_risk))
        r2 = run_phase("2 — UNIVERSE TOURNAMENT", p2, close, low_df, regime_ser, fout=fout)
        print_top(r2, 10, "PHASE 2: TOP UNIVERSES", fout)
        if r2:
            br = sorted(r2, key=lambda r: r["composite"], reverse=True)[0]
            best_uni = extract_field(br["label"], "uni") or "qc_current"
        print(f"\n  → Best universe: {best_uni}")

    # ── Phase 3: Allocation tournament ────────────────────────────────────
    best_alloc = "top1"  # fallback
    if only_phase in (None, 3):
        best_universe = UNIVERSES.get(best_uni, base_uni)
        p3 = []
        for sn in top_sigs[:5]:
            sfn = SIGNALS[sn]
            for am in ALLOC_METHODS:
                p3.append((f"sig={sn:<22} uni={best_uni:<12} alloc={am:<12} risk=no_risk",
                           best_universe, sfn, am, no_risk))
        r3 = run_phase("3 — ALLOCATION TOURNAMENT", p3, close, low_df, regime_ser, fout=fout)
        print_top(r3, 10, "PHASE 3: TOP ALLOCATIONS", fout)
        if r3:
            br = sorted(r3, key=lambda r: r["composite"], reverse=True)[0]
            best_alloc = extract_field(br["label"], "alloc") or "top1"
        print(f"\n  → Best allocation: {best_alloc}")

    # ── Phase 4: Risk tournament ───────────────────────────────────────────
    best_risk = "no_risk"  # fallback
    if only_phase in (None, 4):
        best_universe = UNIVERSES.get(best_uni, base_uni)
        p4 = []
        for sn in top_sigs[:3]:
            sfn = SIGNALS[sn]
            for rn, rc in RISK_CONFIGS.items():
                p4.append((f"sig={sn:<22} uni={best_uni:<12} alloc={best_alloc:<12} risk={rn}",
                           best_universe, sfn, best_alloc, rc))
        r4 = run_phase("4 — RISK TOURNAMENT", p4, close, low_df, regime_ser, fout=fout)
        print_top(r4, 10, "PHASE 4: TOP RISK CONFIGS", fout)
        if r4:
            br = sorted(r4, key=lambda r: r["composite"], reverse=True)[0]
            best_risk = extract_field(br["label"], "risk") or "no_risk"
        print(f"\n  → Best risk config: {best_risk}")

    # ── Phase 5: Grand finals ──────────────────────────────────────────────
    if only_phase in (None, 5):
        t3_sigs  = top_sigs[:3]
        t2_unis  = list(dict.fromkeys([best_uni, "broad_mix", "thematic"]))[:2]
        t3_alloc = list(dict.fromkeys([best_alloc, "top3_equal", "tiered", "kelly_half"]))[:3]
        t2_risk  = list(dict.fromkeys([best_risk, "no_risk", "regime_sma200"]))[:2]

        p5 = []
        for sn in t3_sigs:
            sfn = SIGNALS[sn]
            for un in t2_unis:
                uni = UNIVERSES.get(un, base_uni)
                for am in t3_alloc:
                    for rn in t2_risk:
                        rc  = RISK_CONFIGS[rn]
                        lbl = f"sig={sn} | uni={un} | alloc={am} | risk={rn}"
                        p5.append((lbl, uni, sfn, am, rc))

        r5 = run_phase("5 — GRAND FINALS", p5, close, low_df, regime_ser, fout=fout)
        print_top(r5, 15, "PHASE 5: GRAND FINALS", fout)

        all_results = (r1 if only_phase is None else []) + r5
        champion    = sorted(all_results, key=lambda r: r["composite"], reverse=True)[0] if all_results else None

        if champion:
            star = "\n" + "★"*90
            msg  = (f"\n  CHAMPION:\n  {champion['label']}\n"
                    f"  CAGR {champion['cagr']:.1f}%  |  Sharpe {champion['sharpe']:.2f}  |  "
                    f"MaxDD {champion['max_dd']:.1f}%  |  Composite {champion['composite']:.2f}\n"
                    f"  3M {champion['ret_3m']:+.1f}%  |  6M {champion['ret_6m']:+.1f}%  |  "
                    f"1Y {champion['ret_1y']:+.1f}%  |  2Y {champion['ret_2y']:+.1f}%\n"
                    f"  Sortino {champion['sortino']:.2f}  |  Calmar {champion['calmar']:.3f}  |  "
                    f"Win Rate {champion['win_rate']:.1f}%  |  Final ${champion['final']:,.0f}")
            print(star); print(msg); print(star)
            if fout: fout.write(star+"\n"+msg+"\n"+star+"\n")

    # SPY reference
    if spy is not None:
        sp   = spy.iloc[252:]
        yrs  = (sp.index[-1]-sp.index[0]).days/365.25
        cagr = ((sp.iloc[-1]/sp.iloc[0])**(1/yrs)-1)*100
        def _sp(m=None,y=None):
            cut = sp.index[-1]-(pd.DateOffset(months=m) if m else pd.DateOffset(years=y))
            sub = sp.loc[sp.index>=cut]
            return (sub.iloc[-1]/sub.iloc[0]-1)*100 if len(sub)>1 else 0
        line = f"\n  SPY benchmark: CAGR {cagr:.1f}%  3M {_sp(m=3):+.1f}%  1Y {_sp(y=1):+.1f}%"
        print(line)
        if fout: fout.write(line+"\n")

    fout.close()
    print(f"\n  Full results → {OUT_FILE}")

if __name__ == "__main__":
    main()
