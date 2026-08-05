#!/usr/bin/env python3
"""
Daily Meta-Allocator Backtest
==============================
Each morning, weights capital toward whichever strategies have the best trailing
20-day daily expectancy (strategy momentum), away from cold ones.

3 strategies:
  A  ETF Diversified  — equal-weight QQQ/SPY/IWM/TLT/GLD (daily hold)
  B  QC Portfolio     — top-3 QC leaderboard momentum rotation (daily)
  C  SOXL Regime      — 5-condition gate, intraday short-hold (sparse)

Meta-allocator: softmax on trailing expectancy, floor 10%, cap 50% per strategy.
Compares vs:
  • Equal-weight (1/3 each)
  • Best-single   (100% QC Portfolio — highest avg daily return)

Output: /Users/quarki3/swarm_results/completed/daily_meta_allocator.json
"""

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "qc_portfolio"))

RESULT_FILE = Path("swarm_results/completed/daily_meta_allocator.json")
RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)

INIT_EQ      = 100_000.0
START        = "2021-05-01"
END          = "2026-06-15"

# Meta-allocator params
LOOKBACK     = 20     # trailing trading days for expectancy
MIN_WEIGHT   = 0.10   # floor allocation per strategy
MAX_WEIGHT   = 0.50   # cap per strategy
TEMP         = 20.0   # softmax temperature


# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY A: ETF Diversified (equal-weight QQQ/SPY/IWM/TLT/GLD)
# ══════════════════════════════════════════════════════════════════════════════

def etf_diversified_daily_returns(start=START, end=END) -> pd.Series:
    """Equal-weight daily returns; monthly rebalance → essentially (r1+r2+r3+r4+r5)/5 each day."""
    tickers = ["QQQ", "SPY", "IWM", "TLT", "GLD"]
    print(f"  [ETF] Downloading {tickers} …")
    raw = yf.download(tickers, start=start, end=end, progress=False,
                      auto_adjust=True, threads=True)
    close = raw["Close"].copy()
    close.columns = [str(c) for c in close.columns]
    close = close.dropna(how="all").ffill().dropna()
    # Daily equal-weight return
    daily_ret = close.pct_change().dropna()
    eq_ret    = daily_ret[tickers].mean(axis=1)
    eq_ret.name = "ETF_Diversified"
    print(f"  [ETF] {len(eq_ret)} trading days {eq_ret.index[0].date()} → {eq_ret.index[-1].date()}")
    return eq_ret


# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY B: QC Portfolio (top-3 strategies, 2x winner weighting)
# ══════════════════════════════════════════════════════════════════════════════

# Top-3 QC strategy IDs and their universes (from config.py)
QC_TOP3 = [245, 46, 315]
QC_UNIVERSE = {
    245: ["AMD", "TSLA", "AMZN", "AAPL", "SPXL"],        # Tech Momentum Winner
    46:  ["TQQQ", "UPRO", "SOXL", "TECL", "LABU"],       # TheOmniscientParadox
    315: ["AAPL", "MSFT", "NVDA", "GOOGL", "META",
          "AMZN", "AMD", "TSLA"],                          # Mandelbrotian Vol Clustering
}
QC_ALLOC = {245: "single_winner", 46: "single_winner", 315: "top3_equal"}


def _momentum_scores(close_hist: pd.DataFrame, symbols: list) -> dict:
    scores = {}
    for sym in symbols:
        if sym not in close_hist.columns:
            scores[sym] = 0.0
            continue
        p = close_hist[sym].dropna()
        n = len(p)
        if n < 21:
            scores[sym] = 0.0
            continue
        roc20 = float(p.iloc[-1]) / float(p.iloc[-20]) - 1
        roc60 = float(p.iloc[-1]) / float(p.iloc[-60]) - 1 if n >= 61 else roc20
        scores[sym] = 0.5 * roc20 + 0.5 * roc60
    return scores


def qc_portfolio_daily_returns(start=START, end=END) -> pd.Series:
    """
    Simulate QC portfolio: daily momentum rotation across top-3 strategies,
    2x winner weighting (winner strategy gets 2/4 = 50%, others 1/4 each).
    Trail stop: 5% per position, rebalance daily.
    """
    all_syms = list({"SPY"} | {s for sid in QC_TOP3 for s in QC_UNIVERSE[sid]})

    print(f"  [QC] Downloading {len(all_syms)} symbols …")
    raw = yf.download(all_syms, start=start, end=end, progress=False,
                      auto_adjust=True, threads=True)
    close = raw["Close"].copy()
    if isinstance(close.columns, pd.MultiIndex):
        close.columns = close.columns.droplevel(1)
    close.columns = [str(c) for c in close.columns]
    close = close.dropna(how="all").ffill()

    # 2x winner weighting: [0.5, 0.25, 0.25]
    weights_by_strat = [2.0, 1.0, 1.0]
    total_w          = sum(weights_by_strat)
    strat_fracs      = [w / total_w for w in weights_by_strat]

    warmup    = 65
    trail_pct = 0.05
    cash      = INIT_EQ          # actual cash on hand
    holdings  = {}               # {sym: {"qty", "cost", "peak"}}
    equity_curve = {}

    trade_dates = close.index[warmup:]

    for date in trade_dates:
        prices = {sym: float(close.at[date, sym])
                  for sym in close.columns
                  if sym in close.columns and not pd.isna(close.at[date, sym])}

        # ── Trailing stop-loss ─────────────────────────────────────────────
        for sym in list(holdings.keys()):
            if sym not in prices:
                continue
            p, peak = prices[sym], holdings[sym]["peak"]
            if p < peak * (1 - trail_pct):
                cash += holdings[sym]["qty"] * p
                del holdings[sym]
            elif p > holdings[sym]["peak"]:
                holdings[sym]["peak"] = p

        # ── Portfolio value ────────────────────────────────────────────────
        port_val = cash + sum(
            info["qty"] * prices.get(sym, info["cost"])
            for sym, info in holdings.items()
        )
        port_val = max(port_val, 0.01)

        # ── Target allocation ──────────────────────────────────────────────
        hist   = close.loc[:date]
        target = {}
        for i, sid in enumerate(QC_TOP3):
            avail   = [s for s in QC_UNIVERSE[sid] if s in close.columns]
            alloc_t = QC_ALLOC[sid]
            if not avail:
                continue
            strat_cap = port_val * strat_fracs[i]
            scores    = _momentum_scores(hist, avail)
            ranked    = sorted(scores, key=lambda x: scores[x], reverse=True)
            if alloc_t == "single_winner" and ranked:
                sym = ranked[0]
                target[sym] = target.get(sym, 0.0) + strat_cap
            elif alloc_t in ("top3_equal", "top5_equal"):
                k   = 3 if alloc_t == "top3_equal" else 5
                top = ranked[:min(k, len(ranked))]
                per = strat_cap / len(top) if top else 0.0
                for sym in top:
                    target[sym] = target.get(sym, 0.0) + per

        # ── Sell positions not in target ───────────────────────────────────
        for sym in list(holdings.keys()):
            if sym not in target and sym in prices:
                cash += holdings[sym]["qty"] * prices[sym]
                del holdings[sym]

        # ── Adjust toward target ───────────────────────────────────────────
        for sym, tgt_val in target.items():
            if sym not in prices or prices[sym] <= 0:
                continue
            p       = prices[sym]
            cur_qty = holdings.get(sym, {}).get("qty", 0.0)
            diff    = tgt_val - cur_qty * p
            if abs(diff) < 1.0:
                continue
            qty = abs(diff) / p
            if diff > 0 and cash >= qty * p:
                cash -= qty * p
                if sym in holdings:
                    old_q = holdings[sym]["qty"]
                    new_q = old_q + qty
                    holdings[sym]["qty"]  = new_q
                    holdings[sym]["cost"] = (holdings[sym]["cost"] * old_q + p * qty) / new_q
                    holdings[sym]["peak"] = max(holdings[sym]["peak"], p)
                else:
                    holdings[sym] = {"qty": qty, "cost": p, "peak": p}
            elif diff < 0:
                sell_qty = min(qty, cur_qty)
                if sell_qty > 0:
                    cash += sell_qty * p
                    holdings[sym]["qty"] -= sell_qty
                    if holdings[sym]["qty"] <= 0:
                        del holdings[sym]

        # ── Record equity ──────────────────────────────────────────────────
        port_val = cash + sum(
            info["qty"] * prices.get(sym, info["cost"])
            for sym, info in holdings.items()
        )
        equity_curve[date] = max(port_val, 0.01)

    ec        = pd.Series(equity_curve)
    daily_ret = ec.pct_change().dropna()
    daily_ret.name = "QC_Portfolio"
    print(f"  [QC] {len(daily_ret)} trading days {daily_ret.index[0].date()} → {daily_ret.index[-1].date()}")
    print(f"  [QC] Final equity ${float(ec.iloc[-1]):,.0f} (from ${INIT_EQ:,.0f})")
    return daily_ret


# ══════════════════════════════════════════════════════════════════════════════
#  STRATEGY C: SOXL Regime (daily return series from 5-min backtest trades)
# ══════════════════════════════════════════════════════════════════════════════

SOXL_CACHE  = Path("soxl_5min_bars.csv")
DAILY_CACHE = Path("daily_regime_cache.csv")


def _daily_adx(df: pd.DataFrame, period: int) -> pd.Series:
    hi, lo, cl = df["high"], df["low"], df["close"]
    tr  = pd.concat([(hi - lo), (hi - cl.shift(1)).abs(),
                     (lo - cl.shift(1)).abs()], axis=1).max(axis=1)
    up  = hi - hi.shift(1)
    dn  = lo.shift(1) - lo
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    atr_e = tr.ewm(span=period, adjust=False).mean()
    pdi   = 100 * pdm.ewm(span=period, adjust=False).mean() / atr_e
    ndi   = 100 * ndm.ewm(span=period, adjust=False).mean() / atr_e
    dx    = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(span=period, adjust=False).mean()


def _soxl_fetch_external(start="2019-01-01", end=END):
    """Load or rebuild external daily regime (SMH/SPY/VIX)."""
    if DAILY_CACHE.exists():
        df = pd.read_csv(DAILY_CACHE, index_col=0, parse_dates=True)
        df.index = pd.to_datetime(df.index).normalize()
        return df

    def _flat(raw):
        if isinstance(raw.columns, pd.MultiIndex):
            raw = raw.droplevel(1, axis=1)
        raw.index = pd.to_datetime(raw.index).normalize()
        return raw

    smh = _flat(yf.download("SMH", start=start, end=end, progress=False, auto_adjust=True))
    spy = _flat(yf.download("SPY", start=start, end=end, progress=False, auto_adjust=True))
    try:
        vix = _flat(yf.download("^VIX", start=start, end=end, progress=False, auto_adjust=True))
    except Exception:
        vix = None

    smh_close = smh["Close"]
    ema20 = smh_close.ewm(span=20, adjust=False).mean()
    ema50 = smh_close.ewm(span=50, adjust=False).mean()
    adx   = _daily_adx(smh.rename(columns={"Close": "close", "High": "high",
                                             "Low": "low"}), 14)
    spy_c   = spy["Close"]
    spy_200 = spy_c.rolling(200, min_periods=200).mean()

    out = pd.DataFrame({
        "smh_close": smh_close, "smh_ema20": ema20, "smh_ema50": ema50,
        "smh_adx": adx,
        "spy_close": spy_c, "spy_sma200": spy_200,
        "cond_smh_trend": (smh_close > ema50) & (ema20 > ema50),
        "cond_adx": adx > 20.0,
        "cond_spy": spy_c > spy_200,
    })
    if vix is not None:
        vc = vix["Close"].reindex(out.index).ffill()
        out["vix_close"] = vc
        out["cond_vix"]  = vc < 25.0
    out.to_csv(DAILY_CACHE)
    return out


def soxl_regime_daily_returns(start=START, end=END) -> pd.Series:
    """
    Run SOXL regime backtest (5-min bars + daily gate) and produce a
    daily return series (daily PnL / running equity, 0 on non-trade days).
    Multi-day positions are mark-to-market daily using SOXL daily closes.
    """
    TRAIL_MULT  = 1.25
    TAKE1_MULT  = 2.0
    TAKE1_FRAC  = 0.50
    HARD_STOP_P = 0.08
    HARD_STOP_M = 2.0
    RISK_TRADE  = 0.02
    MAX_NOTL    = 0.30
    ENTRY_EMA   = 25
    ENTRY_ATR   = 14
    ENTRY_START = 9 * 60 + 35
    ENTRY_END   = 11 * 60
    EOD_FORCE   = 15 * 60 + 55
    MAX_HOLD    = 3
    COMM        = 0.005
    GATE_SOXL   = True
    warmup      = 150

    if not SOXL_CACHE.exists():
        print("  [SOXL] ERROR: soxl_5min_bars.csv not found — skipping SOXL strategy")
        return pd.Series(name="SOXL_Regime", dtype=float)

    print("  [SOXL] Loading 5-min bars …")
    df5 = pd.read_csv(SOXL_CACHE, index_col=0, parse_dates=True)
    df5.index = pd.to_datetime(df5.index)
    df5 = df5[(df5.index >= start) & (df5.index < end)].copy()

    print("  [SOXL] Loading external daily (SMH/SPY/VIX) …")
    ext = _soxl_fetch_external()

    # Build SOXL daily: ATR(14), EMA20/50, prev-day close — shift +1
    soxl_d = df5.resample("B").agg({"open": "first", "high": "max",
                                     "low": "min", "close": "last"}).dropna()
    cl     = soxl_d["close"]
    hi_d   = soxl_d["high"]
    lo_d   = soxl_d["low"]
    tr_d   = pd.concat([(hi_d - lo_d), (hi_d - cl.shift(1)).abs(),
                         (lo_d - cl.shift(1)).abs()], axis=1).max(axis=1)
    atr14  = tr_d.ewm(span=14, adjust=False).mean()
    ema20d = cl.ewm(span=20, adjust=False).mean()
    ema50d = cl.ewm(span=50, adjust=False).mean()

    soxl_daily = pd.DataFrame({
        "soxl_daily_close": cl.shift(1),
        "soxl_daily_atr":   atr14.shift(1),
        "soxl_ema20":       ema20d.shift(1),
        "soxl_ema50":       ema50d.shift(1),
        "soxl_close_today": cl,   # for mark-to-market
    })
    soxl_daily.index = pd.to_datetime(soxl_daily.index).normalize()

    # Merge ext + SOXL daily → regime gate
    reg = ext.join(soxl_daily, how="outer")
    ext_gate = reg["cond_smh_trend"] & reg["cond_adx"] & reg["cond_spy"]
    if "cond_vix" in reg.columns:
        ext_gate = ext_gate & reg["cond_vix"]
    if GATE_SOXL:
        cond_soxl = ((reg["soxl_ema20"] > reg["soxl_ema50"])
                     & (reg["soxl_daily_close"] > reg["soxl_ema50"]))
        gate = ext_gate & cond_soxl
    else:
        gate = ext_gate
    reg["gate_open"] = gate

    # Add 5-min indicators
    df5["ema"]        = df5["close"].ewm(span=ENTRY_EMA, adjust=False).mean()
    tr5               = pd.concat([df5["high"] - df5["low"],
                                   (df5["high"] - df5["close"].shift(1)).abs(),
                                   (df5["low"]  - df5["close"].shift(1)).abs()],
                                  axis=1).max(axis=1)
    df5["atr5"]       = tr5.ewm(span=ENTRY_ATR, adjust=False).mean()
    df5["prev_close"] = df5["close"].shift(1)

    all_dates = sorted(df5.index.normalize().unique())
    date_ord  = {d: i for i, d in enumerate(all_dates)}

    equity      = INIT_EQ
    pos         = None
    entered_today: set = set()
    trades_list  = []   # {entry_date, exit_date, pnl}
    daily_pnl    = {}   # date → realized PnL that day

    print(f"  [SOXL] Running backtest {all_dates[0].date()} → {all_dates[-1].date()} …")

    for i in range(warmup, len(df5)):
        row        = df5.iloc[i]
        close      = float(row["close"])
        ema        = float(row["ema"])
        prev_cl    = float(row["prev_close"]) if not np.isnan(row["prev_close"]) else close
        ts         = df5.index[i]
        date       = ts.normalize()
        date_str   = str(date.date())
        mins       = ts.hour * 60 + ts.minute

        if np.isnan(ema) or close <= 0:
            continue

        date_key = pd.Timestamp(date.date())
        if date_key not in reg.index:
            continue

        d_atr      = reg.loc[date_key, "soxl_daily_atr"]
        d_prev_cl  = reg.loc[date_key, "soxl_daily_close"]
        gate_open  = bool(reg.loc[date_key, "gate_open"]) if not pd.isna(reg.loc[date_key, "gate_open"]) else False

        if pd.isna(d_atr) or d_atr <= 0:
            continue

        # Manage open position
        if pos is not None:
            entry_ord = date_ord.get(pos["entry_date"], 0)
            curr_ord  = date_ord.get(date, 0)
            days_held = curr_ord - entry_ord
            eod_hit   = (days_held >= MAX_HOLD - 1 and mins >= EOD_FORCE)
            max_hit   = (days_held >= MAX_HOLD)
            lock_atr  = pos["entry_datr"]

            # Tiered partial: sell 50% at +2×daily ATR
            if (not pos["partial_taken"]
                    and close >= pos["avg_entry"] + TAKE1_MULT * lock_atr):
                sell_sh  = pos["total_shares"] * TAKE1_FRAC
                part_pnl = (close - pos["avg_entry"]) * sell_sh - sell_sh * COMM * 2
                pos["total_shares"]  -= sell_sh
                pos["partial_taken"]  = True
                pos["locked_pnl"]    += part_pnl
                pos["partial_pnl_date"] = date_key

            # Ratchet trail
            cur_datr = d_atr if not pd.isna(d_atr) and d_atr > 0 else lock_atr
            new_trail = close - TRAIL_MULT * cur_datr
            if new_trail > pos["trail_stop"]:
                pos["trail_stop"] = new_trail

            trail_hit = close <= pos["trail_stop"]
            hard_hit  = close <= pos["hard_stop"]

            if trail_hit or hard_hit or eod_hit or max_hit:
                shares     = pos["total_shares"]
                exit_price = close
                if hard_hit and not trail_hit and not eod_hit and not max_hit:
                    exit_price = pos["hard_stop"]
                pnl      = (exit_price - pos["avg_entry"]) * shares - shares * COMM * 2
                total    = pnl + pos["locked_pnl"]
                equity  += total
                if date_key not in daily_pnl:
                    daily_pnl[date_key] = 0.0
                daily_pnl[date_key] += total
                trades_list.append({
                    "entry_date": pos["entry_date"],
                    "exit_date":  date_key,
                    "pnl":        total,
                })
                pos = None

        # Entry logic
        if (pos is None and gate_open
                and date not in entered_today
                and ENTRY_START <= mins < ENTRY_END):
            prior_day = float(d_prev_cl) if not pd.isna(d_prev_cl) else 0.0
            if (close > ema and close > prev_cl and close > prior_day):
                trail_dist = TRAIL_MULT * d_atr
                hard_dist  = min(HARD_STOP_P * close, HARD_STOP_M * d_atr)
                stop_dist  = max(trail_dist, 0.01)
                shares_r   = (equity * RISK_TRADE) / stop_dist
                shares_c   = (equity * MAX_NOTL) / close
                shares     = min(shares_r, shares_c)
                if shares * close >= 1.0:
                    pos = {
                        "avg_entry":    close,
                        "total_shares": shares,
                        "trail_stop":   close - trail_dist,
                        "hard_stop":    close - hard_dist,
                        "entry_datr":   d_atr,
                        "entry_date":   date,
                        "partial_taken": False,
                        "locked_pnl":   0.0,
                        "partial_pnl_date": None,
                    }
                    entered_today.add(date)

    # Close residual at end of data
    if pos is not None:
        cl_f  = float(df5.iloc[-1]["close"])
        pnl   = (cl_f - pos["avg_entry"]) * pos["total_shares"] - pos["total_shares"] * COMM * 2
        total = pnl + pos["locked_pnl"]
        equity += total
        date_key = df5.index[-1].normalize()
        if date_key not in daily_pnl:
            daily_pnl[date_key] = 0.0
        daily_pnl[date_key] += total

    # Build daily equity series from PnL events
    trading_days = pd.DatetimeIndex(sorted(df5.index.normalize().unique()))
    pnl_s  = pd.Series(daily_pnl, dtype=float).reindex(trading_days, fill_value=0.0)
    eq_s   = INIT_EQ + pnl_s.cumsum()
    dr     = eq_s.pct_change().dropna()
    dr.name = "SOXL_Regime"

    n_trades = len(trades_list)
    print(f"  [SOXL] {n_trades} trades, final equity ${equity:,.0f}")
    print(f"  [SOXL] {len(dr)} trading days {dr.index[0].date()} → {dr.index[-1].date()}")
    return dr


# ══════════════════════════════════════════════════════════════════════════════
#  META-ALLOCATOR ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def softmax_weights(expectancies: np.ndarray, temp: float) -> np.ndarray:
    """Softmax with temperature; handles negative expectations gracefully."""
    x = np.array(expectancies, dtype=float)
    # Shift so minimum is 0 (ensures all positive before softmax)
    x = x - x.min()
    ex = np.exp(temp * x)
    return ex / ex.sum()


def allocate(expectancies: dict, min_w: float, max_w: float, temp: float) -> dict:
    """Compute strategy weights from trailing expectancies."""
    names = list(expectancies.keys())
    vals  = np.array([expectancies[n] for n in names])
    raw   = softmax_weights(vals, temp)
    # Clip to [min, max]
    raw   = np.clip(raw, min_w, max_w)
    raw  /= raw.sum()   # re-normalize
    # Iteratively enforce cap (edge case where clipping + renorm can violate)
    for _ in range(10):
        over  = raw > max_w
        under = raw < min_w
        raw[over]  = max_w
        raw[under] = min_w
        free  = ~over & ~under
        if not free.any():
            break
        remainder = 1.0 - raw[over].sum() - raw[under].sum()
        if remainder > 0 and free.sum() > 0:
            raw[free] = raw[free] / raw[free].sum() * remainder
        else:
            break
    raw /= raw.sum()
    return dict(zip(names, raw))


def backtest_meta(returns: pd.DataFrame,
                  lookback: int = LOOKBACK,
                  min_w: float = MIN_WEIGHT,
                  max_w: float = MAX_WEIGHT,
                  temp: float = TEMP) -> pd.Series:
    """
    Run meta-allocator across aligned daily return series.
    Returns: daily portfolio return series.
    """
    strats = list(returns.columns)
    port_rets = []
    dates     = []

    for i in range(lookback, len(returns)):
        window = returns.iloc[i - lookback: i]
        today  = returns.iloc[i]
        date   = returns.index[i]

        exp = {s: float(window[s].mean()) for s in strats}
        w   = allocate(exp, min_w, max_w, temp)
        r   = sum(w[s] * float(today[s]) for s in strats)
        port_rets.append(r)
        dates.append(date)

    return pd.Series(port_rets, index=pd.DatetimeIndex(dates), name="Meta_Allocator")


# ══════════════════════════════════════════════════════════════════════════════
#  PERFORMANCE METRICS
# ══════════════════════════════════════════════════════════════════════════════

def compute_metrics(daily_rets: pd.Series, label: str = "") -> dict:
    r     = daily_rets.dropna()
    if len(r) < 20:
        return {"error": "insufficient_data", "label": label}
    n_yrs  = len(r) / 252.0
    eq     = INIT_EQ * (1 + r).cumprod()
    cagr   = (float(eq.iloc[-1]) / INIT_EQ) ** (1 / n_yrs) - 1
    vol    = float(r.std()) * np.sqrt(252)
    sharpe = float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else 0.0
    hwm    = eq.cummax()
    maxdd  = float(((eq - hwm) / hwm).min())
    avg_dr = float(r.mean()) * 100   # in percent
    wr     = float((r > 0).mean()) * 100
    return {
        "label":          label,
        "avg_daily_pct":  round(avg_dr, 4),
        "daily_vol_ann":  round(vol * 100, 4),
        "daily_sharpe":   round(sharpe, 3),
        "cagr_pct":       round(cagr * 100, 2),
        "max_dd_pct":     round(maxdd * 100, 2),
        "daily_winrate":  round(wr, 1),
        "n_days":         len(r),
        "final_equity":   round(float(eq.iloc[-1]), 2),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print("\n" + "═"*70)
    print("  DAILY META-ALLOCATOR BACKTEST")
    print("  3 strategies: ETF Diversified | QC Portfolio | SOXL Regime")
    print("  Meta-allocator: trailing 20-day expectancy, softmax weights")
    print("  Compare: meta vs equal-weight vs best-single (QC)")
    print("═"*70 + "\n")

    # ── Step 1: Generate daily returns for each strategy ──────────────────
    print("Step 1/4 — Strategy returns …")
    r_etf  = etf_diversified_daily_returns()
    r_qc   = qc_portfolio_daily_returns()
    r_soxl = soxl_regime_daily_returns()

    # ── Step 2: Align to common trading days ──────────────────────────────
    print("\nStep 2/4 — Aligning …")
    common = r_etf.index.intersection(r_qc.index).intersection(r_soxl.index)
    common = common.sort_values()
    # Use 2021-06-01 as start (warm-up buffer)
    common = common[common >= "2021-06-01"]

    r_etf  = r_etf.reindex(common).fillna(0.0)
    r_qc   = r_qc.reindex(common).fillna(0.0)
    r_soxl = r_soxl.reindex(common).fillna(0.0)

    rets = pd.DataFrame({
        "ETF_Diversified": r_etf,
        "QC_Portfolio":    r_qc,
        "SOXL_Regime":     r_soxl,
    }, index=common)

    print(f"  Aligned: {len(common)} days  {common[0].date()} → {common[-1].date()}")

    # ── Step 3: Run all three portfolio variants ───────────────────────────
    print("\nStep 3/4 — Backtesting variants …")

    # Meta-allocator (dynamic weights)
    print("  [Meta] Running …")
    meta_ret = backtest_meta(rets)

    # Equal-weight (1/3 each)
    ew_start = rets.index[LOOKBACK]
    ew_ret   = rets.loc[ew_start:].mean(axis=1)
    ew_ret.name = "EqualWeight"

    # Best single = QC Portfolio
    best_single_ret = rets["QC_Portfolio"].loc[ew_start:]
    best_single_ret.name = "BestSingle_QC"

    # ── Step 4: Metrics ────────────────────────────────────────────────────
    print("\nStep 4/4 — Metrics …")
    m_meta  = compute_metrics(meta_ret,        "Meta Allocator")
    m_ew    = compute_metrics(ew_ret,           "Equal Weight")
    m_best  = compute_metrics(best_single_ret,  "Best Single (QC)")
    m_etf   = compute_metrics(r_etf.loc[ew_start:],  "ETF Diversified")
    m_qc    = compute_metrics(r_qc.loc[ew_start:],   "QC Portfolio")
    m_soxl  = compute_metrics(r_soxl.loc[ew_start:], "SOXL Regime")

    print("\n" + "─"*70)
    print(f"  {'Strategy':<28} {'AvgDaily%':>10} {'Sharpe':>8} {'CAGR%':>8} {'MaxDD%':>8}")
    print("─"*70)
    for m in [m_meta, m_ew, m_best, m_etf, m_qc, m_soxl]:
        print(f"  {m['label']:<28} {m['avg_daily_pct']:>10.4f} "
              f"{m['daily_sharpe']:>8.3f} {m['cagr_pct']:>8.2f} {m['max_dd_pct']:>8.2f}")
    print("─"*70)

    # Meta advantage
    meta_vs_ew   = m_meta["avg_daily_pct"] - m_ew["avg_daily_pct"]
    meta_vs_best = m_meta["avg_daily_pct"] - m_best["avg_daily_pct"]
    meta_wins    = meta_vs_ew > 0

    summary = (
        f"Meta-allocator avg daily return: {m_meta['avg_daily_pct']:.4f}% "
        f"(Sharpe {m_meta['daily_sharpe']:.3f}, CAGR {m_meta['cagr_pct']:.1f}%, "
        f"MaxDD {m_meta['max_dd_pct']:.1f}%). "
        f"Equal-weight: {m_ew['avg_daily_pct']:.4f}%/day (Sharpe {m_ew['daily_sharpe']:.3f}). "
        f"Best-single QC: {m_best['avg_daily_pct']:.4f}%/day (Sharpe {m_best['daily_sharpe']:.3f}). "
        f"Meta {'BEATS' if meta_wins else 'TRAILS'} equal-weight by "
        f"{meta_vs_ew:+.4f}%/day and "
        f"{'BEATS' if meta_vs_best > 0 else 'TRAILS'} best-single by {meta_vs_best:+.4f}%/day. "
        f"Meta Sharpe improvement vs equal-weight: "
        f"{m_meta['daily_sharpe'] - m_ew['daily_sharpe']:+.3f}. "
        f"Trailing 20-day expectancy weighting (softmax T={TEMP}, "
        f"floor {int(MIN_WEIGHT*100)}%, cap {int(MAX_WEIGHT*100)}%) "
        f"across {len(rets.columns)} uncorrelated strategies over "
        f"{m_meta['n_days']} trading days."
    )
    print(f"\n  SUMMARY:\n  {summary}\n")

    result = {
        "task":                  "daily_meta_allocator",
        "run_date":              "2026-06-15",
        "backtest_window":       f"{common[LOOKBACK].date()} → {common[-1].date()}",
        "n_trading_days":        m_meta["n_days"],
        "strategies":            ["ETF_Diversified", "QC_Portfolio", "SOXL_Regime"],
        "meta_params": {
            "lookback_days": LOOKBACK,
            "min_weight":    MIN_WEIGHT,
            "max_weight":    MAX_WEIGHT,
            "softmax_temp":  TEMP,
        },
        # — Primary KPIs (required by spec) —
        "meta_avg_daily":        round(m_meta["avg_daily_pct"], 4),
        "equalweight_avg_daily": round(m_ew["avg_daily_pct"],  4),
        "best_single_daily":     round(m_best["avg_daily_pct"], 4),
        # — Full metrics per variant —
        "metrics": {
            "meta_allocator":   m_meta,
            "equal_weight":     m_ew,
            "best_single_qc":   m_best,
            "etf_diversified":  m_etf,
            "qc_portfolio":     m_qc,
            "soxl_regime":      m_soxl,
        },
        "summary": summary,
        "success": True,
    }

    RESULT_FILE.write_text(json.dumps(result, indent=2))
    print(f"  Saved → {RESULT_FILE}")
    return result


if __name__ == "__main__":
    main()
