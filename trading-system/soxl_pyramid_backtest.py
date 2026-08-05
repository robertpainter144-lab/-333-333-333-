#!/usr/bin/env python3
"""
SOXL Dip-Pyramid Backtest
=========================
Same strategy as spy_dip_pyramid_backtest.py applied to SOXL (Direxion 3× Semis).

Key differences vs SPY run:
  - LEVERAGE = 1.0  (SOXL is already 3×; don't compound with margin)
  - Feed: SIP (NYSE Arca-listed ETF; IEX may be missing)
  - All pyramid params swept identically so results are directly comparable.

Sweep (24 combinations):
  dip_atr_back   : [0.3, 0.5, 0.8]
  min_profit_atr : [0.5, 1.0]
  max_adds       : [1, 2]
  add_fraction   : [0.5, 1.0]

$500 scaling shown at the end of the winning config.
"""

import warnings
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

SYMBOL        = "SOXL"
CACHE         = Path("soxl_5min_bars.csv")

ALPACA_KEY    = "CK4HXKGM3HWRKLXWRU3CDLB6LX"
ALPACA_SECRET = "7i1mhApwTyzqWprLyvGvbzuDJbNSquMRTCnoiXfJSEWk"
DATA_BASE     = "https://data.sandbox.alpaca.markets"

EMA_PERIOD    = 25
ATR_PERIOD    = 14
ADX_PERIOD    = 20
ADX_THRESHOLD = 35
LONG_TRAIL    = 3.0
SHORT_TRAIL   = 3.0
ENTRY_START   = 9 * 60 + 30
ENTRY_END     = 11 * 60
EOD_MINS      = 15 * 60 + 50
INITIAL_EQ    = 100_000.0
LEVERAGE      = 1.0        # SOXL is already 3×
COMM          = 0.005

S_DIP_ATR    = [0.3, 0.5, 0.8]
S_MIN_PROFIT = [0.5, 1.0]
S_MAX_ADDS   = [1, 2]
S_ADD_FRAC   = [0.5, 1.0]


# ── Data ──────────────────────────────────────────────────────────────────────
def fetch_bars(feed="sip") -> pd.DataFrame:
    end   = pd.Timestamp.now(tz="America/New_York").floor("min")
    start = end - pd.DateOffset(years=5)
    hdrs  = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    bars, token = [], None
    print(f"  Fetching {SYMBOL} 5-min bars (feed={feed}) ...")
    while True:
        params = {
            "symbols": SYMBOL, "timeframe": "5Min",
            "start": start.isoformat(), "end": end.isoformat(),
            "limit": 10_000, "feed": feed,
        }
        if token:
            params["page_token"] = token
        r = requests.get(f"{DATA_BASE}/v2/stocks/bars", headers=hdrs, params=params)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        data  = r.json()
        chunk = data.get("bars", {}).get(SYMBOL, [])
        bars.extend(chunk)
        token = data.get("next_page_token")
        print(f"    {len(bars):,} bars ...", end="\r")
        if not token:
            break
    print(f"\n  {len(bars):,} total bars fetched")
    df = pd.DataFrame(bars)
    df["t"] = pd.to_datetime(df["t"], utc=True).dt.tz_convert("America/New_York")
    df = (df.set_index("t")
            .rename(columns={"o": "open", "h": "high", "l": "low",
                             "c": "close", "v": "volume"})
          [["open", "high", "low", "close", "volume"]]
          .sort_index())
    return df


def load_or_fetch() -> pd.DataFrame:
    if CACHE.exists():
        df = pd.read_csv(CACHE, index_col=0, parse_dates=True)
        if df.index.tz is not None:
            df.index = df.index.tz_convert("America/New_York").tz_localize(None)
        df = df.between_time("09:30", "16:00")
        print(f"  Loaded {len(df):,} bars from cache  "
              f"({df.index[0].date()} → {df.index[-1].date()})")
        return df
    # Try SIP first; fall back to IEX
    try:
        df = fetch_bars("sip")
    except Exception as e:
        print(f"  SIP failed ({e}), trying IEX ...")
        df = fetch_bars("iex")
    df.index = df.index.tz_localize(None)
    df.to_csv(CACHE)
    print(f"  Saved to {CACHE}")
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
def run_backtest(df: pd.DataFrame,
                 dip_atr_back: float,
                 min_profit_atr: float,
                 max_adds: int,
                 add_fraction: float,
                 init_eq: float = INITIAL_EQ) -> dict:
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
                    and position["n_adds"] < max_adds
                    and ENTRY_START <= mins < ENTRY_END + 120):
                dip_from_high    = position["pos_high"] - close
                profit_per_share = close - position["avg_entry"]
                trend_ok         = close > ema

                if (dip_from_high >= dip_atr_back * atr
                        and profit_per_share >= min_profit_atr * atr
                        and trend_ok):
                    add_shares = max(1, int(position["orig_shares"] * add_fraction))
                    old_cost   = position["avg_entry"] * position["total_shares"]
                    new_cost   = close * add_shares
                    position["total_shares"] += add_shares
                    position["avg_entry"]     = (old_cost + new_cost) / position["total_shares"]
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

            exit_now = mins >= EOD_MINS

            if stop_hit or exit_now:
                shares = position["total_shares"]
                gross  = (close - position["avg_entry"]) * shares if side == "long" \
                     else (position["avg_entry"] - close) * shares
                pnl    = gross - shares * COMM * 2
                equity += pnl
                trades.append({
                    "side":      side,
                    "pnl":       pnl,
                    "bars_held": i - position["entry_idx"],
                    "exit":      "eod" if exit_now else "trail",
                    "regime":    position["regime"],
                    "n_adds":    position["n_adds"],
                    "year":      ts.year,
                    "month":     ts.to_period("M"),
                })
                position = None

        if position is None and ENTRY_START <= mins < ENTRY_END:
            shares = int((equity * LEVERAGE) / close)
            if shares < 1:
                continue

            if regime in ("ranging", "bull_trend") and close > ema:
                position = {
                    "side":         "long",
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close - LONG_TRAIL * atr,
                    "entry_idx":    i,
                    "regime":       regime,
                    "pos_high":     close,
                    "n_adds":       0,
                }
            elif regime == "bear_trend" and close >= ema:
                position = {
                    "side":         "short",
                    "entry_price":  close,
                    "avg_entry":    close,
                    "total_shares": shares,
                    "orig_shares":  shares,
                    "trail_stop":   close + SHORT_TRAIL * atr,
                    "entry_idx":    i,
                    "regime":       regime,
                    "pos_high":     close,
                    "n_adds":       0,
                }

    if position is not None:
        side   = position["side"]
        close  = df.iloc[-1]["close"]
        ts     = df.index[-1]
        shares = position["total_shares"]
        gross  = (close - position["avg_entry"]) * shares if side == "long" \
             else (position["avg_entry"] - close) * shares
        pnl    = gross - shares * COMM * 2
        equity += pnl
        trades.append({
            "side": side, "pnl": pnl,
            "bars_held": len(df) - 1 - position["entry_idx"],
            "exit": "end_of_data", "regime": position["regime"],
            "n_adds": position["n_adds"],
            "year": ts.year, "month": ts.to_period("M"),
        })

    if not trades:
        return dict(n=0, sharpe=np.nan, net_pnl=0, max_dd_pct=0,
                    win_rate=0, pf=np.nan, trades=[])

    pnl_s      = pd.Series([t["pnl"] for t in trades])
    wins       = (pnl_s > 0).sum()
    losses     = (pnl_s <= 0).sum()
    eq_s       = init_eq + pnl_s.cumsum()
    hwm        = eq_s.cummax()
    dd         = eq_s - hwm
    max_dd_pct = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
    n          = len(pnl_s)
    std        = pnl_s.std(ddof=1) if n > 1 else np.nan
    n_days     = max((df.index[-1] - df.index[0]).days, 1)
    tpy        = n / (n_days / 365)
    sharpe     = (pnl_s.mean() / std) * np.sqrt(tpy) if std and std > 0 else np.nan
    pf         = (-pnl_s[pnl_s > 0].sum() / pnl_s[pnl_s < 0].sum()) \
                 if losses and pnl_s[pnl_s < 0].sum() != 0 else np.nan

    t = pd.DataFrame(trades)
    adds_taken    = t["n_adds"].sum()
    pct_with_adds = (t["n_adds"] > 0).mean() * 100

    return dict(
        n=n,
        win_rate=round(wins / n * 100, 1),
        pf=round(float(pf), 3)       if not np.isnan(pf)     else np.nan,
        net_pnl=round(float(pnl_s.sum()), 0),
        final_eq=round(float(eq_s.iloc[-1]), 2),
        sharpe=round(float(sharpe), 3) if not np.isnan(sharpe) else np.nan,
        max_dd_pct=round(float(max_dd_pct), 2),
        adds_taken=int(adds_taken),
        pct_with_adds=round(pct_with_adds, 1),
        trades=trades,
    )


# ── Output helpers ────────────────────────────────────────────────────────────
def print_yearly(trades, init_eq, label=""):
    t = pd.DataFrame(trades)
    if label:
        print(f"\n  {label}")
    print(f"  {'Year':<6} {'n':>4} {'Win%':>5} {'PF':>5} {'PnL':>12} {'MaxDD':>7} {'Adds':>5}")
    print("  " + "─" * 62)
    for yr, grp in t.groupby("year"):
        g  = grp["pnl"]
        gn = len(g); gw = (g > 0).sum()
        eq = init_eq + g.cumsum(); hwm = eq.cummax(); dd = eq - hwm
        ddp = (dd.min() / hwm.loc[dd.idxmin()]) * 100 if dd.min() < 0 else 0.0
        pf  = (-g[g > 0].sum() / g[g < 0].sum()) if (gn - gw) and g[g < 0].sum() != 0 else np.nan
        avg_adds = grp["n_adds"].mean()
        print(
            f"  {yr:<6} {gn:>4} {gw/gn*100:>4.0f}%  "
            f"{f'{pf:.2f}' if not np.isnan(pf) else ' n/a':>5} "
            f"${g.sum():>+10,.0f}  {ddp:>+6.1f}%  {avg_adds:>4.2f}x"
        )


def scale_to_500(df, best_dip, best_mp, best_ma, best_af):
    print("\n" + "=" * 80)
    print("  $500 SCALING — SOXL vs SPY comparison")
    print("=" * 80)
    m500 = run_backtest(df, best_dip, best_mp, best_ma, best_af, init_eq=500.0)
    t    = pd.DataFrame(m500["trades"])
    last12 = t[t["month"] >= (pd.Timestamp.now() - pd.DateOffset(months=12)).to_period("M")]
    avg_daily_full  = m500["net_pnl"] / max((df.index[-1] - df.index[0]).days, 1)
    avg_daily_last12 = last12["pnl"].sum() / 252 if len(last12) else 0

    print(f"  Starting equity : $500")
    print(f"  Ending equity   : ${m500['final_eq']:,.2f}")
    print(f"  Total return    : {(m500['final_eq']/500 - 1)*100:+.1f}%")
    print(f"  Net PnL         : ${m500['net_pnl']:+,.2f}")
    print(f"  Avg PnL/day     : ${avg_daily_full:+.2f}  (full period)")
    print(f"  Avg PnL/day     : ${avg_daily_last12:+.2f}  (last 12 months)")
    print(f"  Sharpe          : {m500['sharpe']:+.2f}")
    print(f"  Max drawdown    : {m500['max_dd_pct']:+.1f}%")
    print(f"  Trades          : {m500['n']}")
    print(f"  Win rate        : {m500['win_rate']}%")
    print()
    print("  Year-by-year at $500:")
    print_yearly(m500["trades"], 500.0)
    print()
    print("  SPY comparison (same $500, same best config):")
    print("    $500 → $2,363  (+373%)  $1.49/day avg  $3.51/day last 12mo  (from prev run)")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print(f"\nSOXL Dip-Pyramid Backtest  (LEVERAGE={LEVERAGE}×  SOXL is already 3× semis)")
    print("=" * 100)
    df = load_or_fetch()
    print(f"  Price range: ${df['close'].min():.2f} – ${df['close'].max():.2f}  "
          f"(avg ATR/day ~ ${df['close'].diff().abs().mean()*78:.2f})")
    print("  Adding indicators ...")
    df = add_indicators(df)

    total = len(S_DIP_ATR) * len(S_MIN_PROFIT) * len(S_MAX_ADDS) * len(S_ADD_FRAC)
    print(f"\nSweeping {total} pyramid configs ...")

    rows = []
    best = None
    for idx, (dip, mp, ma, af) in enumerate(
        product(S_DIP_ATR, S_MIN_PROFIT, S_MAX_ADDS, S_ADD_FRAC), 1
    ):
        m = run_backtest(df, dip, mp, ma, af)
        rows.append(dict(dip_atr=dip, min_profit=mp, max_adds=ma, add_frac=af,
                         **{k: v for k, v in m.items() if k != "trades"}))
        sh_s = f"{m['sharpe']:+.2f}" if not np.isnan(m["sharpe"]) else "  n/a"
        pf_s = f"{m['pf']:.2f}"      if not np.isnan(m["pf"])     else " n/a"
        print(
            f"  [{idx:>2}/{total}]  dip={dip:.1f}×  mp={mp:.1f}×  adds≤{ma}  frac={af:.1f}×"
            f"  │  n={m['n']:>4}  wr={m['win_rate']:>4.1f}%  pf={pf_s:>5}"
            f"  pnl=${m['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={m['max_dd_pct']:>+6.1f}%"
            f"  [{m['pct_with_adds']:.0f}% trades added]"
        )
        if best is None or m["net_pnl"] > best["net_pnl"]:
            best = dict(dip=dip, mp=mp, ma=ma, af=af, **m)

    results = pd.DataFrame(rows).sort_values("net_pnl", ascending=False)
    results.to_csv("soxl_pyramid_results.csv", index=False)

    sep = "=" * 100
    print(f"\n{sep}")
    print("  TOP 10 BY NET PnL  (n ≥ 50)")
    print(sep)
    top = results[results["n"] >= 50].head(10)
    for _, r in top.iterrows():
        sh_s = f"{r['sharpe']:+.2f}" if not np.isnan(r["sharpe"]) else "  n/a"
        pf_s = f"{r['pf']:.2f}"      if not np.isnan(r["pf"])     else " n/a"
        print(
            f"  dip={r['dip_atr']:.1f}×  mp={r['min_profit']:.1f}×  "
            f"adds≤{int(r['max_adds'])}  frac={r['add_frac']:.1f}×"
            f"  │  n={int(r['n']):>4}  wr={r['win_rate']:>4.1f}%  pf={pf_s:>5}"
            f"  pnl=${r['net_pnl']:>+10,.0f}  sharpe={sh_s}  dd={r['max_dd_pct']:>+6.1f}%"
        )
    print(sep)

    print(f"\n{sep}")
    print("  PER-YEAR — TOP 3 CONFIGS")
    print(sep)
    for _, r in top.head(3).iterrows():
        m = run_backtest(df, r["dip_atr"], r["min_profit"], int(r["max_adds"]), r["add_frac"])
        label = (
            f"dip={r['dip_atr']:.1f}×  mp={r['min_profit']:.1f}×  "
            f"adds≤{int(r['max_adds'])}  frac={r['add_frac']:.1f}×"
            f"  │  n={int(r['n'])}  sharpe={r['sharpe']:+.2f}  "
            f"pnl=${r['net_pnl']:+,.0f}  dd={r['max_dd_pct']:+.1f}%"
        )
        print_yearly(m["trades"], INITIAL_EQ, label)

    # $500 scaling on best config
    br = top.iloc[0]
    scale_to_500(df, br["dip_atr"], br["min_profit"], int(br["max_adds"]), br["add_frac"])

    print(sep)
    print("  FACTOR ANALYSIS — avg PnL")
    print(sep)
    for col, vals in [("dip_atr", S_DIP_ATR), ("min_profit", S_MIN_PROFIT),
                      ("max_adds", S_MAX_ADDS), ("add_frac", S_ADD_FRAC)]:
        line = f"  {col:<14}"
        for v in vals:
            avg = results[results[col] == v]["net_pnl"].mean()
            line += f"  {v!s:>4}: ${avg:>+10,.0f}"
        print(line)
    print(sep + "\n")


if __name__ == "__main__":
    main()
