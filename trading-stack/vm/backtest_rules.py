#!/usr/bin/env python3
"""Backtest the governor rule variants over 60 days of real 15-min prices.

Sources (current compositions, held fixed — see caveats in the report):
  qc       = JPM 86.05 / AMD 13.95
  patent   = VTRS / RDY / TEVA equal
  congress = INTC 41.77 / AB 25.03 / UBER 10.44 / GOOGL 7.72 / AMZN 7.62 /
             NVDA 4.50 / AAPL 1.47 / VST 1.46

Variants (identical underlying returns; only the allocation rules differ):
  steady        6h window, L1 .10, sticky .05, power 1.0   (old default)
  adaptive_raw  4h window, L1 .04, sticky .02, power 1.6   (what ran 7/30-31)
  adaptive_gated raw + market-hours-only publishing + leader persistence 4
  full_new      gated + daily -1.5% circuit breaker        (deployed today)

Mechanics mirrored from strategy_governor.py: rolling-mean cumulative P/L per
source, raw = max(.05, (mean-baseline) + EDGE .10 + .20), ^power, +sticky*prev,
4 phantom idle sources at raw .05 (their weight sits as cash), normalize,
L1 hysteresis. The governor evaluates every 15 min AROUND THE CLOCK on
last-known prices (that is how the real one runs), so overnight flips are
reproducible; trades only execute on market bars. Cost model: 5 bps of
traded notional per rebalance (fractional market orders, liquid names).
"""
import json
import math

import numpy as np
import pandas as pd
import yfinance as yf

COMPS = {
    "qc":       {"JPM": 0.8605, "AMD": 0.1395},
    "patent":   {"VTRS": 1 / 3, "RDY": 1 / 3, "TEVA": 1 / 3},
    "congress": {"INTC": 0.4177, "AB": 0.2503, "UBER": 0.1044, "GOOGL": 0.0772,
                 "AMZN": 0.0762, "NVDA": 0.0450, "AAPL": 0.0147, "VST": 0.0146},
}
PHANTOMS = 4          # idle sources the real governor still hands raw=0.05
EDGE = 0.10
OFFSET = 0.20
FLOOR = 0.05
COST = 0.0005         # 5 bps of traded notional
STEP_S = 900

VARIANTS = {
    "steady":         dict(win_h=6, l1=0.10, sticky=0.05, power=1.0,
                           persist=1, hours_gate=False, breaker=None),
    "adaptive_raw":   dict(win_h=4, l1=0.04, sticky=0.02, power=1.6,
                           persist=1, hours_gate=False, breaker=None),
    "adaptive_gated": dict(win_h=4, l1=0.04, sticky=0.02, power=1.6,
                           persist=4, hours_gate=True, breaker=None),
    "full_new":       dict(win_h=4, l1=0.04, sticky=0.02, power=1.6,
                           persist=4, hours_gate=True, breaker=-1.5),
    "full_cap80":     dict(win_h=4, l1=0.04, sticky=0.02, power=1.6,
                           persist=4, hours_gate=True, breaker=-1.5, cap=0.80),
    "full_cap60":     dict(win_h=4, l1=0.04, sticky=0.02, power=1.6,
                           persist=4, hours_gate=True, breaker=-1.5, cap=0.60),
    "full_cap50":     dict(win_h=4, l1=0.04, sticky=0.02, power=1.6,
                           persist=4, hours_gate=True, breaker=-1.5, cap=0.50),
}


def apply_cap(w: dict, cap: float) -> dict:
    """Cap any source's weight, redistributing the excess pro-rata to the
    uncapped rest (iterative water-filling)."""
    w = dict(w)
    for _ in range(6):
        over = {s: v for s, v in w.items() if v > cap}
        if not over:
            break
        excess = sum(v - cap for v in over.values())
        for s in over:
            w[s] = cap
        under = {s: v for s, v in w.items() if v < cap}
        pool = sum(under.values())
        if pool <= 0:
            break
        for s in under:
            w[s] = w[s] + excess * (w[s] / pool)
    return w

tickers = sorted({t for c in COMPS.values() for t in c})
px = yf.download(tickers, period="60d", interval="15m",
                 progress=False, auto_adjust=True)["Close"].ffill().dropna()
px.index = px.index.tz_convert("America/New_York")

# Source NAVs on market bars
nav_bars = pd.DataFrame({
    s: sum(w * px[t] / px[t].iloc[0] for t, w in c.items())
    for s, c in COMPS.items()
})

# Continuous 15-min wall-clock timeline (incl. nights/weekends), price ffill
full_idx = pd.date_range(nav_bars.index[0], nav_bars.index[-1],
                         freq="15min", tz="America/New_York")
nav = nav_bars.reindex(full_idx).ffill()
is_bar = pd.Series(full_idx.isin(nav_bars.index), index=full_idx)
pnl_pct = (nav / nav.iloc[0] - 1) * 100
rets = nav / nav.shift(1) - 1   # zero while closed (ffill)
sessions = pd.Series(full_idx.date, index=full_idx)

SRC = list(COMPS)


def run(v):
    win = int(v["win_h"] * 3600 / STEP_S)
    published = None          # dict weights over SRC + phantom mass
    applied = None
    port = 1.0
    curve = []
    pending = {"name": None, "count": 0}
    publishes = rebalances = 0
    turnover_sum = 0.0
    gated_hours = gated_persist = 0
    frozen_days = set()
    day_open_port = None
    cur_day = None

    for i in range(len(full_idx)):
        ts = full_idx[i]
        bar = bool(is_bar.iloc[i])

        # accrue returns on market bars
        if bar and applied is not None and i > 0:
            r = sum(applied.get(s, 0.0) * rets[s].iloc[i] for s in SRC)
            port *= (1 + r)
        curve.append(port)

        d = sessions.iloc[i]
        if d != cur_day and bar:
            cur_day = d
            day_open_port = port

        # circuit breaker state for today
        frozen = v["breaker"] is not None and d in frozen_days
        if (v["breaker"] is not None and bar and day_open_port
                and (port / day_open_port - 1) * 100 <= v["breaker"]):
            frozen_days.add(d)
            frozen = True

        if i < win:
            continue

        # governor evaluation (every 15 min, market open or not)
        means = {s: pnl_pct[s].iloc[i - win + 1:i + 1].mean() for s in SRC}
        baseline = sum(means.values()) / len(means)
        raw = {s: max(FLOOR, (means[s] - baseline) + EDGE + OFFSET)
               for s in SRC}
        if v["power"] != 1.0:
            raw = {s: r_ ** v["power"] for s, r_ in raw.items()}
        prev = published or {}
        for s in SRC:
            raw[s] = raw[s] + v["sticky"] * prev.get(s, 0.0)
        total = sum(raw.values()) + PHANTOMS * FLOOR
        new_w = {s: raw[s] / total for s in SRC}
        if v.get("cap"):
            new_w = apply_cap(new_w, v["cap"])

        if published is None:
            published = new_w
            applied = dict(new_w) if bar else None
            publishes += 1
            continue

        l1 = sum(abs(new_w[s] - published.get(s, 0)) for s in SRC)
        if l1 < v["l1"]:
            pass
        else:
            blocked = False
            if v["hours_gate"] and not bar:
                gated_hours += 1
                blocked = True
            else:
                top_new = max(new_w, key=new_w.get)
                top_prev = max(published, key=published.get)
                if top_new != top_prev and v["persist"] > 1:
                    if pending["name"] == top_new:
                        pending["count"] += 1
                    else:
                        pending = {"name": top_new, "count": 1}
                    if pending["count"] < v["persist"]:
                        gated_persist += 1
                        blocked = True
            if not blocked:
                published = new_w
                publishes += 1

        # execution: converge applied -> published on market bars
        if bar and not frozen:
            if applied is None:
                applied = dict(published)
                rebalances += 1
            else:
                tw = sum(abs(published[s] - applied.get(s, 0)) for s in SRC)
                if tw > 0.02:
                    port *= (1 - tw * COST)
                    turnover_sum += tw
                    applied = dict(published)
                    rebalances += 1

    curve = pd.Series(curve, index=full_idx)
    daily = curve.groupby(sessions).last()
    dd = ((curve / curve.cummax()) - 1).min() * 100
    worst_day = (daily / daily.shift(1) - 1).min() * 100
    return {
        "return_pct": (port - 1) * 100,
        "max_dd_pct": dd,
        "worst_day_pct": worst_day,
        "publishes": publishes,
        "rebalances": rebalances,
        "turnover_x": turnover_sum,
        "cost_drag_pct": turnover_sum * COST * 100,
        "gated_overnight": gated_hours,
        "gated_persistence": gated_persist,
        "frozen_days": len(frozen_days),
    }


print(f"span: {full_idx[0]:%Y-%m-%d} → {full_idx[-1]:%Y-%m-%d} "
      f"({len(nav_bars)} bars, {len(set(sessions[is_bar]))} sessions)")
print("\nsource buy-and-hold over the span:")
for s in SRC:
    navd = nav_bars[s]
    sdd = ((navd / navd.cummax()) - 1).min() * 100
    print(f"  {s:9} {(navd.iloc[-1]/navd.iloc[0]-1)*100:+7.2f}%  (maxDD {sdd:.1f}%)")
ew = nav_bars.mean(axis=1)
print(f"  {'equal-mix':9} {(ew.iloc[-1]/ew.iloc[0]-1)*100:+7.2f}%  "
      f"(maxDD {((ew/ew.cummax())-1).min()*100:.1f}%)")

print("")
hdr = (f"{'variant':<15}{'return':>8}{'maxDD':>8}{'worstDay':>9}"
       f"{'pubs':>6}{'rebals':>7}{'turnover':>9}{'cost':>7}"
       f"{'gate:ovnt':>10}{'gate:pers':>10}{'frozen':>7}")
print(hdr)
print("-" * len(hdr))
results = {}
for name, v in VARIANTS.items():
    r = run(v)
    results[name] = r
    print(f"{name:<15}{r['return_pct']:>+7.2f}%{r['max_dd_pct']:>7.1f}%"
          f"{r['worst_day_pct']:>+8.2f}%{r['publishes']:>6}{r['rebalances']:>7}"
          f"{r['turnover_x']:>8.1f}x{r['cost_drag_pct']:>6.2f}%"
          f"{r['gated_overnight']:>10}{r['gated_persistence']:>10}"
          f"{r['frozen_days']:>7}")

json_out = {"span": [str(full_idx[0]), str(full_idx[-1])], "results": results}
with open("/home/ubuntu/trading-data/backtest_rules_result.json", "w") as f:
    json.dump(json_out, f, indent=2, default=float)
print("\nsaved -> trading-data/backtest_rules_result.json")
