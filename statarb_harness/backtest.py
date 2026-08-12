"""Backtest runner — metrics, noise floor, ablations.

Usage:
    python3 backtest.py               # full run + ablations + noise floor
    python3 backtest.py --quick       # base config only
Outputs results.json (+ prints a table). Chart building lives with the
caller (the results page embeds the equity curves from results.json).
"""
from __future__ import annotations

import argparse
import json
import numpy as np

from data import make_panel
from signals import combined_score
import portfolio
from execution import run as execute


def metrics(res: dict, index: np.ndarray) -> dict:
    eq, r = res["equity"], res["ret"]
    years = len(eq) / 252.0
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1.0
    dd = r[r < 0]
    ddev = float(np.sqrt((dd ** 2).mean()) * np.sqrt(252)) if len(dd) else 0.0
    sortino = float((r.mean() * 252) / ddev) if ddev > 1e-9 else float("inf")
    vol = float(r.std() * np.sqrt(252))
    peak = np.maximum.accumulate(eq)
    maxdd = float(((eq - peak) / peak).min())
    ir = index[-1] / index[0]
    idx_cagr = ir ** (1 / years) - 1.0
    ipeak = np.maximum.accumulate(index)
    idx_maxdd = float(((index - ipeak) / ipeak).min())
    return {"cagr": round(cagr * 100, 2), "vol": round(vol * 100, 2),
            "sortino": round(sortino, 2), "max_dd": round(maxdd * 100, 2),
            "ann_turnover_x": round(res["ann_turnover"], 1),
            "cost_drag_ann_pct": round(res["cost_drag_ann"] * 100, 2),
            "index_cagr": round(idx_cagr * 100, 2),
            "index_max_dd": round(idx_maxdd * 100, 2)}


def one_run(panel, *, hysteresis=True, beta_hedge=True, trend_overlay=False,
            ddev_target=True, score=None) -> tuple:
    s = score if score is not None else combined_score(panel)
    realized = {"r": None}

    def rets_so_far(t):
        return realized["r"][:t] if realized["r"] is not None else None

    # two-pass so the ddev targeter sees realized strategy returns:
    # pass 1 without targeter to get a return path, pass 2 with it.
    w, h, g = portfolio.build_targets(panel, s, hysteresis=hysteresis,
                                      beta_hedge=beta_hedge,
                                      trend_overlay=trend_overlay,
                                      ddev_target=False)
    res = execute(panel, w, h)
    if ddev_target:
        realized["r"] = res["ret"]
        w, h, g = portfolio.build_targets(panel, s, hysteresis=hysteresis,
                                          beta_hedge=beta_hedge,
                                          trend_overlay=trend_overlay,
                                          ddev_target=True,
                                          strat_rets_so_far=rets_so_far)
        res = execute(panel, w, h)
    return res, g


def noise_floor(panel, n_random=20, seed=7) -> float:
    """Best Sortino among random-rank strategies run through the SAME
    machinery — the bar a real signal must clear."""
    rng = np.random.default_rng(seed)
    best = -9.0
    D, N = panel["sue"].shape
    for k in range(n_random):
        s = rng.normal(0, 1, (D, N)).astype(np.float32)
        s[~np.isfinite(panel["close"])] = -np.inf
        res, _ = one_run(panel, score=s, ddev_target=False)
        m = metrics(res, panel["index"])
        best = max(best, m["sortino"])
    return round(best, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    panel = make_panel()
    out = {}

    res, gross = one_run(panel)
    out["base"] = metrics(res, panel["index"])
    out["base_equity"] = [round(float(x), 1) for x in res["equity"][::4]]
    out["index_curve"] = [round(float(x), 3) for x in panel["index"][::4]]
    out["gross_path"] = [round(float(x), 3) for x in gross[::4]]
    print("BASE (hysteresis + hedge + ddev, no trend):", out["base"])

    if not args.quick:
        res_n, _ = one_run(panel, hysteresis=False, ddev_target=False)
        out["naive_no_hysteresis"] = metrics(res_n, panel["index"])
        print("NAIVE (no hysteresis):", out["naive_no_hysteresis"])

        res_t, _ = one_run(panel, trend_overlay=True)
        out["with_trend_overlay"] = metrics(res_t, panel["index"])
        print("WITH TREND OVERLAY:", out["with_trend_overlay"])

        res_u, _ = one_run(panel, beta_hedge=False, trend_overlay=True)
        out["unhedged_with_trend"] = metrics(res_u, panel["index"])
        print("UNHEDGED + TREND:", out["unhedged_with_trend"])

        out["noise_floor_sortino"] = noise_floor(panel, n_random=20)
        print("NOISE FLOOR (best of 20 random):", out["noise_floor_sortino"])

    json.dump(out, open("results.json", "w"), indent=1)
    print("written results.json")


if __name__ == "__main__":
    main()
