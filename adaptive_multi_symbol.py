#!/usr/bin/env python3
"""
Adaptive Strategy — Multi-Symbol Comparison
============================================
Runs the fully-adaptive dip-pyramid strategy on:
  SPY   (S&P 500 ETF)          LEVERAGE=2.0
  QQQ   (Nasdaq-100 ETF)       LEVERAGE=2.0
  CX    (CEMEX ADR)            LEVERAGE=1.0
  SOXL  (3× Semis ETF)         LEVERAGE=1.0

Same adaptive parameter grid as soxl_adaptive_month.py — all params
driven by vol × trend regime, gap size, streak, volume, EMA slope.

Starting equity: $500
"""

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

ALPACA_KEY    = "CK4HXKGM3HWRKLXWRU3CDLB6LX"
ALPACA_SECRET = "7i1mhApwTyzqWprLyvGvbzuDJbNSquMRTCnoiXfJSEWk"
DATA_BASE     = "https://data.sandbox.alpaca.markets"

COMM          = 0.005

ENTRY_START        = 9 * 60 + 30
EOD_CLOSE_SHORT    = 15 * 60 + 50
EOD_CLOSE_ALL      = 15 * 60 + 55

VOL_RANK_WIN  = 63 * 78
VOL_RANK_MIN  = 300
VOL_SLOT_WIN  = 63
WARMUP        = VOL_RANK_WIN + 200

SYMBOLS = {
    "SPY":  dict(leverage=2.0, cache="spy_5min_bars.csv",  feed="iex"),
    "QQQ":  dict(leverage=2.0, cache="qqq_5min_bars.csv",  feed="iex"),
    "CX":   dict(leverage=1.0, cache="cx_5min_bars.csv",   feed="sip"),
    "SOXL": dict(leverage=1.0, cache="soxl_5min_bars.csv", feed="iex"),
}

# ── Adaptive parameter grid ───────────────────────────────────────────────────
GRID = {
    ("low",  "weak"):     dict(trail=2.5, ema=20, dip=0.30, min_profit=0.35, max_adds=3, entry_end=11*60+30),
    ("low",  "moderate"): dict(trail=2.5, ema=20, dip=0.35, min_profit=0.40, max_adds=3, entry_end=13*60),
    ("low",  "strong"):   dict(trail=2.0, ema=18, dip=0.25, min_profit=0.30, max_adds=5, entry_end=15*60),
    ("mid",  "weak"):     dict(trail=3.0, ema=25, dip=0.50, min_profit=0.50, max_adds=2, entry_end=11*60),
    ("mid",  "moderate"): dict(trail=3.0, ema=25, dip=0.50, min_profit=0.50, max_adds=2, entry_end=12*60),
    ("mid",  "strong"):   dict(trail=2.5, ema=22, dip=0.40, min_profit=0.40, max_adds=3, entry_end=13*60+30),
    ("high", "weak"):     dict(trail=4.5, ema=32, dip=1.00, min_profit=0.80, max_adds=1, entry_end=10*60+30),
    ("high", "moderate"): dict(trail=4.0, ema=30, dip=0.80, min_profit=0.70, max_adds=1, entry_end=11*60),
    ("high", "strong"):   dict(trail=3.5, ema=28, dip=0.60, min_profit=0.55, max_adds=2, entry_end=12*60+30),
}

GAP_LARGE  = 0.04
GAP_MEDIUM = 0.02


# ── Data helpers ──────────────────────────────────────────────────────────────
def fetch_bars(symbol: str, feed: str = "iex") -> pd.DataFrame:
    end   = pd.Timestamp.now(tz="America/New_York").floor("min")
    start = end - pd.DateOffset(years=5)
    hdrs  = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    bars, token = [], None
    print(f"    Fetching {symbol} ({feed}) ...", end="", flush=True)
    while True:
        params = {"symbols": symbol, "timeframe": "5Min",
                  "start": start.isoformat(), "end": end.isoformat(),
                  "limit": 10_000, "feed": feed}
        if token:
            params["page_token"] = token
        r = requests.get(f"{DATA_BASE}/v2/stocks/bars", headers=hdrs, params=params)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
        data  = r.json()
        bars.extend(data.get("bars", {}).get(symbol, []))
        token = data.get("next_page_token")
        print(f" {len(bars):,}", end="\r", flush=True)
        if not token:
            break
    print(f"    {symbol}: {len(bars):,} bars fetched          ")
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    df = (df.set_index("t")
            .rename(columns={"o":"open","h":"high","l":"low","c":"close","v":"volume"})
          [["open","high","low","close","volume"]]
          .sort_index())
    df.index = df.index.tz_localize(None)
    return df.between_time("09:30","16:00")


def load_symbol(symbol: str, cfg: dict) -> pd.DataFrame:
    cache = Path(cfg["cache"])
    if cache.exists():
        # Read index as plain strings to avoid mixed-tz parse errors (CX DST transitions)
        df = pd.read_csv(cache, index_col=0, parse_dates=False)
        sample = str(df.index[0])
        if any(c in sample for c in ["+", "-0", "-1", "Z"]) and len(sample) > 19:
            # Tz-aware strings (CX has offsets like "2021-05-03 09:30:00-04:00")
            df.index = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York").tz_localize(None)
        else:
            # Already tz-naive ET strings (SPY, QQQ, SOXL)
            df.index = pd.to_datetime(df.index)
        # Standardise columns
        df.columns = [c.lower() for c in df.columns]
        needed = ["open","high","low","close"]
        df = df[[c for c in needed if c in df.columns] +
                (["volume"] if "volume" in df.columns else [])]
        if "volume" not in df.columns:
            df["volume"] = 0          # SPY cache has no volume — fill with 0
        df = df[["open","high","low","close","volume"]]
        df = df.between_time("09:30","16:00")
        print(f"    {symbol}: {len(df):,} bars from cache  "
              f"({df.index[0].date()} → {df.index[-1].date()})")
        return df
    df = fetch_bars(symbol, cfg["feed"])
    df.to_csv(cache)
    return df


# ── Indicators ────────────────────────────────────────────────────────────────
def build_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for p in [18, 20, 22, 25, 28, 30, 32]:
        df[f"ema_{p}"] = df["close"].ewm(span=p, adjust=False).mean()
    hl  = df["high"] - df["low"]
    hcp = (df["high"] - df["close"].shift(1)).abs()
    lcp = (df["low"]  - df["close"].shift(1)).abs()
    tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()
    up   = df["high"] - df["high"].shift(1)
    down = df["low"].shift(1) - df["low"]
    pdm  = np.where((up > down) & (up > 0), up, 0.0)
    ndm  = np.where((down > up) & (down > 0), down, 0.0)
    atr_w = tr.ewm(span=20, adjust=False).mean()
    pdi   = 100 * pd.Series(pdm, index=df.index).ewm(span=20, adjust=False).mean() / atr_w
    ndi   = 100 * pd.Series(ndm, index=df.index).ewm(span=20, adjust=False).mean() / atr_w
    dx    = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    df["adx"] = dx.ewm(span=20, adjust=False).mean()
    df["pdi"] = pdi
    df["ndi"] = ndi
    df["vol_rank"] = df["atr"].rolling(VOL_RANK_WIN, min_periods=VOL_RANK_MIN).rank(pct=True)
    ema25 = df["ema_25"]
    df["ema_slope"] = (ema25 - ema25.shift(5)) / (ema25.shift(5) * 5)
    df["time_slot"] = df.index.hour * 60 + df.index.minute
    df["vol_avg_slot"] = (
        df.groupby("time_slot")["volume"]
          .transform(lambda s: s.rolling(VOL_SLOT_WIN, min_periods=5).mean())
    )
    df["vol_ratio"] = df["volume"] / df["vol_avg_slot"].replace(0, np.nan)
    return df


def vol_regime(vr):
    if np.isnan(vr): return "mid"
    return "low" if vr < 0.33 else "high" if vr > 0.67 else "mid"

def trend_regime(adx):
    return "weak" if adx < 25 else "strong" if adx >= 45 else "moderate"

def daily_gaps(df):
    first = df.groupby(df.index.date)["open"].first()
    last  = df.groupby(df.index.date)["close"].last()
    gaps  = {}
    dates = sorted(first.index)
    for i in range(1, len(dates)):
        d, prev = dates[i], dates[i-1]
        gaps[str(d)] = (first[d] - last[prev]) / last[prev]
    if dates: gaps[str(dates[0])] = 0.0
    return gaps

def streak(trades):
    if not trades: return 0
    s = 0
    for t in reversed(trades):
        won = t["pnl"] > 0
        if s == 0:   s = 1 if won else -1
        elif won and s > 0:  s += 1
        elif not won and s < 0: s -= 1
        else: break
    return s

def resolve(row, sk, gap_pct):
    vr = vol_regime(row["vol_rank"])
    tr = trend_regime(row["adx"])
    p  = dict(GRID[(vr, tr)])
    ga = abs(gap_pct)
    if ga > GAP_LARGE:
        p["size_mult"] = 0.40; p["trail"] = round(p["trail"] * 1.30, 2); p["gap_lbl"] = "LARGE"
    elif ga > GAP_MEDIUM:
        p["size_mult"] = 0.70; p["trail"] = round(p["trail"] * 1.10, 2); p["gap_lbl"] = "med"
    else:
        p["size_mult"] = 1.00; p["gap_lbl"] = "small"
    if sk >= 3:   p["size_mult"] = round(p["size_mult"] * 1.30, 3)
    elif sk >= 1: p["size_mult"] = round(p["size_mult"] * 1.10, 3)
    elif sk <= -3: p["size_mult"] = round(p["size_mult"] * 0.50, 3)
    elif sk <= -2: p["size_mult"] = round(p["size_mult"] * 0.75, 3)
    vr_ = row.get("vol_ratio", 1.0)
    if not np.isnan(vr_) and vr_ > 1.5:
        p["dip"] = round(p["dip"] * 0.80, 3)
    elif not np.isnan(vr_) and vr_ < 0.5:
        p["dip"] = round(p["dip"] * 1.20, 3)
        p["min_profit"] = round(p["min_profit"] * 1.20, 3)
    slope = row.get("ema_slope", 0.0)
    if not np.isnan(slope) and slope > 0.003:
        p["trail"] = round(p["trail"] * 0.85, 2)
    p["vr"] = vr; p["tr"] = tr
    return p


# ── Backtest ──────────────────────────────────────────────────────────────────
def backtest(df, leverage, init_eq=500.0):
    gaps     = daily_gaps(df)
    trades   = []
    position = None
    equity   = init_eq

    for i in range(WARMUP, len(df)):
        row   = df.iloc[i]
        close = row["close"]
        atr   = row["atr"]
        pdi   = row["pdi"]
        ndi   = row["ndi"]
        ts    = df.index[i]
        mins  = ts.hour * 60 + ts.minute
        date  = str(ts.date())

        sk      = streak(trades)
        gap_pct = gaps.get(date, 0.0)
        p       = resolve(row, sk, gap_pct)
        ema     = row[f"ema_{p['ema']}"]
        bull    = pdi >= ndi

        if position is not None:
            side = position["side"]
            ep   = position["params"]
            e_ema = row[f"ema_{ep['ema']}"]

            if side == "long" and close > position["pos_high"]:
                position["pos_high"] = close

            if (side == "long"
                    and position["n_adds"] < ep["max_adds"]
                    and ENTRY_START <= mins < ep["entry_end"] + 30):
                dip  = position["pos_high"] - close
                prof = close - position["avg_entry"]
                if dip >= ep["dip"]*atr and prof >= ep["min_profit"]*atr and close > e_ema:
                    add   = position["orig_shares"]
                    old_c = position["avg_entry"] * position["total_shares"]
                    position["total_shares"] += add
                    position["avg_entry"]     = (old_c + close*add) / position["total_shares"]
                    position["n_adds"]        += 1

            cur_trail = p["trail"]
            if side == "long":
                ns = close - cur_trail * atr
                if ns > position["trail_stop"]: position["trail_stop"] = ns
                stop_hit = close <= position["trail_stop"]
            else:
                ns = close + cur_trail * atr
                if ns < position["trail_stop"]: position["trail_stop"] = ns
                stop_hit = close >= position["trail_stop"]

            eod = (mins >= EOD_CLOSE_SHORT and side == "short") or mins >= EOD_CLOSE_ALL

            if stop_hit or eod:
                sh    = position["total_shares"]
                gross = (close - position["avg_entry"])*sh if side == "long" \
                    else (position["avg_entry"] - close)*sh
                pnl   = gross - sh*COMM*2
                equity += pnl
                trades.append({
                    "date": date, "side": side, "pnl": pnl,
                    "n_adds": position["n_adds"],
                    "year": ts.year, "month": ts.to_period("M"),
                    "vr": position["params"]["vr"],
                    "tr": position["params"]["tr"],
                    "gap_lbl": position["params"]["gap_lbl"],
                    "trail": position["params"]["trail"],
                    "size_mult": position["params"]["size_mult"],
                })
                position = None

        if position is None and ENTRY_START <= mins < p["entry_end"]:
            shares = (equity * leverage) / close * p["size_mult"]
            if shares * close < 0.10: continue
            if p["tr"] == "weak" and abs(row.get("ema_slope", 0)) < 0.001:
                continue  # skip flat weak-trend entries
            if bull and close > ema:
                position = {"side": "long",  "entry_price": close,
                            "avg_entry": close, "total_shares": shares,
                            "orig_shares": shares,
                            "trail_stop": close - p["trail"]*atr,
                            "pos_high": close, "n_adds": 0, "params": dict(p)}
            elif not bull and close < ema:
                position = {"side": "short", "entry_price": close,
                            "avg_entry": close, "total_shares": shares,
                            "orig_shares": shares,
                            "trail_stop": close + p["trail"]*atr,
                            "pos_high": close, "n_adds": 0, "params": dict(p)}

    if position is not None:
        sh    = position["total_shares"]
        close = df.iloc[-1]["close"]
        ts    = df.index[-1]
        gross = (close - position["avg_entry"])*sh if position["side"] == "long" \
            else (position["avg_entry"] - close)*sh
        pnl   = gross - sh*COMM*2
        equity += pnl
        trades.append({"date": str(ts.date()), "side": position["side"], "pnl": pnl,
                       "n_adds": position["n_adds"], "year": ts.year,
                       "month": ts.to_period("M"),
                       "vr": position["params"]["vr"], "tr": position["params"]["tr"],
                       "gap_lbl": position["params"]["gap_lbl"],
                       "trail": position["params"]["trail"],
                       "size_mult": position["params"]["size_mult"]})

    if not trades:
        return dict(n=0, sharpe=np.nan, net_pnl=0, max_dd_pct=0,
                    win_rate=0, pf=np.nan, final_eq=init_eq,
                    last_month_pnl=0, last_month_n=0, trades=[])

    pnl_s  = pd.Series([t["pnl"] for t in trades])
    wins   = (pnl_s > 0).sum()
    eq_s   = init_eq + pnl_s.cumsum()
    hwm    = eq_s.cummax()
    dd     = eq_s - hwm
    max_dd = (dd.min() / hwm.loc[dd.idxmin()])*100 if dd.min() < 0 else 0.0
    n      = len(pnl_s)
    std    = pnl_s.std(ddof=1) if n > 1 else np.nan
    n_days = max((df.index[-1] - df.index[0]).days, 1)
    tpy    = n / (n_days / 252)
    sharpe = (pnl_s.mean()/std)*np.sqrt(tpy) if std and std > 0 else np.nan
    pf     = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
             if (n-wins) and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    t = pd.DataFrame(trades)
    last22_start = sorted(df.index.normalize().unique())[-22]
    last22_set   = set(str(d.date()) for d in df.index.normalize().unique()
                       if d >= last22_start)
    lm = t[t["date"].isin(last22_set)]

    return dict(
        n=n, win_rate=round(wins/n*100, 1),
        pf=round(float(pf), 3)       if not np.isnan(pf)     else np.nan,
        net_pnl=round(float(pnl_s.sum()), 2),
        final_eq=round(float(eq_s.iloc[-1]), 2),
        sharpe=round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        max_dd_pct=round(float(max_dd), 2),
        last_month_pnl=round(float(lm["pnl"].sum()), 2) if len(lm) else 0,
        last_month_n=len(lm),
        last_month_wr=round((lm["pnl"] > 0).mean()*100, 1) if len(lm) else 0,
        trades=trades,
    )


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    INIT_EQ = 500.0
    print(f"\nAdaptive Strategy — Multi-Symbol Comparison  (${INIT_EQ:.0f} starting)")
    print("=" * 90)

    results = {}
    dfs     = {}

    for sym, cfg in SYMBOLS.items():
        print(f"\n  {sym}:")
        try:
            df = load_symbol(sym, cfg)
            print(f"    Building indicators ...", flush=True)
            df = build_indicators(df)
            print(f"    Running backtest ...", flush=True)
            r  = backtest(df, leverage=cfg["leverage"], init_eq=INIT_EQ)
            results[sym] = r
            dfs[sym]     = df
            print(f"    Done: ${INIT_EQ:.0f} → ${r['final_eq']:,.2f}  Sharpe {r['sharpe']:+.2f}")
        except Exception as e:
            print(f"    FAILED: {e}")
            results[sym] = None

    # ── Summary table ─────────────────────────────────────────────────────────
    sep = "=" * 90
    print(f"\n\n{sep}")
    print(f"  5-YEAR SUMMARY  (${INIT_EQ:.0f} → X)")
    print(sep)
    print(f"  {'Symbol':<7} {'Lev':>4}  {'Start':>7} → {'End':>10}  "
          f"{'Return':>8}  {'Sharpe':>7}  {'MaxDD':>7}  {'WR%':>5}  {'PF':>6}  {'n':>5}")
    print("  " + "─" * 82)
    for sym, r in results.items():
        if r is None:
            print(f"  {sym:<7}  — NO DATA")
            continue
        lev = SYMBOLS[sym]["leverage"]
        ret = (r["final_eq"]/INIT_EQ - 1)*100
        sh  = f"{r['sharpe']:+.2f}" if not np.isnan(r['sharpe']) else "  n/a"
        pf  = f"{r['pf']:.3f}"      if not np.isnan(r['pf'])     else "  n/a"
        print(f"  {sym:<7} {lev:>4}×  ${INIT_EQ:>6.0f} → ${r['final_eq']:>9,.2f}  "
              f"{ret:>+7.0f}%  {sh:>7}  {r['max_dd_pct']:>+6.1f}%  "
              f"{r['win_rate']:>4.1f}%  {pf:>6}  {r['n']:>5}")
    print(sep)

    # ── Last month ────────────────────────────────────────────────────────────
    print(f"\n  LAST MONTH (~22 trading days)")
    print("  " + "─" * 70)
    print(f"  {'Symbol':<7}  {'PnL':>9}  {'WR%':>5}  {'n':>4}  "
          f"{'$/day (trd)':>12}  {'%/day':>8}  {'%/month':>9}")
    print("  " + "─" * 70)
    for sym, r in results.items():
        if r is None:
            print(f"  {sym:<7}  NO DATA")
            continue
        t       = pd.DataFrame(r["trades"]) if r["trades"] else pd.DataFrame()
        if not len(t):
            continue
        df      = dfs[sym]
        last22_start = sorted(df.index.normalize().unique())[-22]
        last22_set   = set(str(d.date()) for d in df.index.normalize().unique()
                           if d >= last22_start)
        lm      = t[t["date"].isin(last22_set)] if "date" in t.columns else pd.DataFrame()

        # running equity at start of last month
        pre_lm  = t[~t["date"].isin(last22_set)]
        start_eq_lm = INIT_EQ + pre_lm["pnl"].sum() if len(pre_lm) else INIT_EQ

        lm_pnl  = lm["pnl"].sum() if len(lm) else 0
        lm_n    = len(lm)
        lm_wr   = (lm["pnl"] > 0).mean()*100 if lm_n else 0
        pct_day = lm_pnl / start_eq_lm / 22 * 100 if start_eq_lm else 0
        pct_mon = lm_pnl / start_eq_lm * 100        if start_eq_lm else 0
        dpp     = lm_pnl / 22
        print(f"  {sym:<7}  ${lm_pnl:>8.2f}  {lm_wr:>4.1f}%  {lm_n:>4}  "
              f"${dpp:>10.2f}/day  {pct_day:>+7.2f}%  {pct_mon:>+8.2f}%")
    print("  " + "─" * 70)

    # ── Year-by-year per symbol ───────────────────────────────────────────────
    print(f"\n  YEAR-BY-YEAR  (${INIT_EQ:.0f} start, adaptive)")
    print("  " + "─" * 80)
    all_years = sorted({yr for r in results.values()
                        if r and r["trades"]
                        for yr in pd.DataFrame(r["trades"])["year"].unique()})

    hdr = f"  {'Year':<6}"
    for sym in results:
        if results[sym]: hdr += f"  {sym:>14}"
    print(hdr)
    print("  " + "─" * 80)
    for yr in all_years:
        row_s = f"  {yr:<6}"
        for sym, r in results.items():
            if r is None:
                row_s += f"  {'NO DATA':>14}"
                continue
            t = pd.DataFrame(r["trades"])
            g = t[t["year"] == yr]["pnl"] if len(t) else pd.Series(dtype=float)
            if not len(g):
                row_s += f"  {'—':>14}"
                continue
            wr = (g > 0).mean()*100
            row_s += f"  ${g.sum():>+8.2f}({wr:>3.0f}%)"
        print(row_s)
    print("  " + "─" * 80)

    # ── Regime breakdown (combined) ───────────────────────────────────────────
    print(f"\n  REGIME P&L BREAKDOWN (avg per trade, all symbols combined)")
    print("  " + "─" * 60)
    all_trades = []
    for sym, r in results.items():
        if r and r["trades"]:
            for t_ in r["trades"]:
                t_["symbol"] = sym
            all_trades.extend(r["trades"])
    if all_trades:
        ta = pd.DataFrame(all_trades)
        print(f"  {'Regime':<22}  {'n':>5}  {'WR%':>5}  {'Avg PnL':>10}  {'Total PnL':>12}")
        print("  " + "─" * 60)
        for (vr, tr_), grp in ta.groupby(["vr","tr"]):
            g  = grp["pnl"]; n_ = len(g); w_ = (g > 0).sum()
            print(f"  {f'{vr} × {tr_}':<22}  {n_:>5}  {w_/n_*100:>4.0f}%  "
                  f"${g.mean():>8.4f}  ${g.sum():>10.2f}")

    print(f"\n{sep}\n")


if __name__ == "__main__":
    main()
