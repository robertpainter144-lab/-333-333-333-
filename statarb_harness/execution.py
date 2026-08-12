"""Execution layer — T+1 discipline, sqrt-impact costs, delist handling.

Targets decided at close of day t fill at the OPEN of day t+1; the position
then earns open(t+1) -> close(t+1) on day t+1 and full close-to-close after.
Implementation: hold yesterday's weights for the close(t-1)->open(t) leg,
then the new weights for open(t)->close(t). No same-day information ever
touches a fill.

Cost model per trade leg (as fraction of traded notional):
    half_spread + IMPACT_K * sqrt(trade_$ / ADV_$) * daily_vol
Delisting: any name that dies while held realizes its terminal print (the
panel already embeds the terminal loss in its last close) and trades out at
the death-day price with full costs — no quiet disappearances.
"""
from __future__ import annotations

import numpy as np

IMPACT_K = 0.3


def run(panel: dict, w: np.ndarray, hedge: np.ndarray,
        equity0: float = 1_000_000.0) -> dict:
    close, open_, adv = panel["close"], panel["open"], panel["adv"]
    spread = panel["spread_bp"] / 1e4
    index = panel["index"]
    D, N = w.shape

    r_oc = np.where(np.isfinite(close) & np.isfinite(open_),
                    close / open_ - 1.0, 0.0)          # open->close
    r_co = np.zeros_like(r_oc)                          # prev close->open
    r_co[1:] = np.where(np.isfinite(open_[1:]) & np.isfinite(close[:-1]),
                        open_[1:] / close[:-1] - 1.0, 0.0)
    idx_r = np.zeros(D)
    idx_r[1:] = index[1:] / index[:-1] - 1.0

    # trailing 20d name vol for the impact term (lagged)
    r_cc = np.zeros_like(r_oc)
    r_cc[1:] = np.where(np.isfinite(close[1:]) & np.isfinite(close[:-1]),
                        close[1:] / close[:-1] - 1.0, 0.0)
    vol20 = np.full((D, N), 0.02)
    for t in range(21, D):
        # std of RETURNS (review fix: std of |returns| understated vol and
        # therefore the impact cost — costs must not be quietly flattered)
        vol20[t] = r_cc[t - 20:t].std(axis=0).clip(0.005, 0.10)

    equity = np.empty(D)
    equity[0] = equity0
    strat_ret = np.zeros(D)
    cost_paid = np.zeros(D)
    traded = np.zeros(D)
    live_w = np.zeros(N)                                # weights in force
    live_h = 0.0
    for t in range(1, D):
        eq = equity[t - 1]
        # overnight leg on yesterday's book (names). The hedge is modeled as
        # a close-rebalanced index future: yesterday's hedge rides the FULL
        # close-to-close index move today (added below), so crash gaps hit
        # longs and hedge together — no unhedged overnight window.
        overnight = float((live_w * r_co[t]).sum())
        # fill names to the new targets at the open
        tgt, tgt_h = w[t - 1], hedge[t - 1]              # decided at close t-1
        dead = ~np.isfinite(close[t])
        tgt = np.where(dead, 0.0, tgt)
        dw = tgt - live_w
        trade_frac = np.abs(dw)
        # review fix: a delisting forced-exit trades at the POST-terminal
        # value, not the pre-crash nominal weight — fade the dead name's
        # exit notional by its terminal-day return.
        just_died = dead & (live_w > 0)
        fade = np.ones(N)
        if just_died.any():
            fade[just_died] = np.clip(1.0 + r_cc[t - 1][just_died], 0.1, 1.0)
        trade_usd = trade_frac * eq * fade
        with np.errstate(divide="ignore", invalid="ignore"):
            impact = IMPACT_K * np.sqrt(
                np.where(adv[t - 1] > 0, trade_usd / adv[t - 1], 0.0)) * vol20[t]
        leg_cost = trade_usd * (spread + impact)
        h_trade = abs(tgt_h - live_h) * eq
        leg_cost_h = h_trade * (0.5 / 1e4)               # index hedge: ~0.5bp
        costs = float(leg_cost.sum() + leg_cost_h)
        # intraday leg on the new names book; hedge decided at close t-1
        # earns TODAY's index move (review fix: live_h here was one decision
        # staler than the book it offsets — pay-today-protect-tomorrow)
        intraday = float((tgt * r_oc[t]).sum())
        ret = overnight + intraday + float(tgt_h * idx_r[t]) - costs / eq
        equity[t] = eq * (1.0 + ret)
        strat_ret[t] = ret
        cost_paid[t] = costs
        traded[t] = float(trade_usd.sum() + h_trade)
        live_w, live_h = tgt, tgt_h

    years = D / 252.0
    ann_turnover = traded.sum() / equity.mean() / years
    cost_drag = cost_paid.sum() / equity.mean() / years
    return {"equity": equity, "ret": strat_ret, "costs": cost_paid,
            "traded": traded, "ann_turnover": ann_turnover,
            "cost_drag_ann": cost_drag}
