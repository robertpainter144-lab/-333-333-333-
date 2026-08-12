"""Portfolio construction — ranks -> targets, with the cost-control stack.

The pieces that made the original harness's numbers survivable, in order:
  1. Hold-band hysteresis: enter names ranked in the top `enter_pct`; keep
     holding until a name decays below `exit_pct` (45th percentile). This
     is THE turnover lever.
  2. Weight dead-band: ignore target changes under `deadband_bp` of equity.
  3. Beta hedge: short the index to net beta ~0 (ablation switch).
  4. Trend overlay (conditional/ablatable): cut gross when index < 200d MA.
     On a hedged book this mostly cuts alpha, not tail — the original
     harness's ablation showed overlays-off netting MORE (10.4% vs 7.2%);
     kept as a switch so that finding stays reproducible.
  5. Downside-deviation targeter: scale gross so trailing downside
     deviation annualizes to `target_ddev` (6%).
"""
from __future__ import annotations

import numpy as np

REBAL_EVERY = 3            # twice-weekly-ish re-ranking cadence
N_HOLD = 40                # book breadth target
ENTER_PCT = 90.0           # enter names above this cross-sectional percentile
# RAISED 2026-08-12 (45->60): alpha horizons are 5d (reversal) / 20d (PEAD);
# holding to the 45th pct kept stale zero-alpha names, diluting the book
# (~68 avg breadth, gross ~6.6%). 60 exits them near alpha exhaustion.
EXIT_PCT = 60.0            # hold until decay below this percentile
DEADBAND_BP = 40.0         # ignore weight changes smaller than this
TARGET_DDEV = 0.06         # annualized downside deviation target
TREND_WIN = 200
GROSS_CAP = 1.0


def build_targets(panel: dict, score: np.ndarray, *,
                  hysteresis=True, beta_hedge=True, trend_overlay=False,
                  ddev_target=True, strat_rets_so_far=None) -> tuple:
    """Walk the panel and emit target weight matrices.

    Returns (w, hedge, gross_path):
      w:      D x N long weights decided at close of day t (execute t+1)
      hedge:  D      index short weight (negative) decided same time
      gross:  D      gross scalar applied (for the exposure chart)
    strat_rets_so_far: optional callable(t)->array of realized strategy daily
      returns through t-1, used by the ddev targeter (execution supplies it;
      falling back to index-derived proxy keeps this module standalone).
    """
    close, index, beta = panel["close"], panel["index"], panel["beta"]
    D, N = score.shape
    w = np.zeros((D, N), dtype=np.float32)
    hedge = np.zeros(D, dtype=np.float32)
    gross = np.ones(D, dtype=np.float32)

    held = np.zeros(N, dtype=bool)
    idx_ma = np.full(D, np.nan)
    for t in range(TREND_WIN, D):
        idx_ma[t] = index[t - TREND_WIN:t].mean()

    prev_w = np.zeros(N, dtype=np.float32)
    for t in range(D):
        s = score[t]
        ok = np.isfinite(s)
        if ok.sum() < 50 or t < 65:
            w[t] = prev_w * 0.0
            held[:] = False
            prev_w = w[t]
            continue

        if t % REBAL_EVERY == 0:
            pct = np.full(N, np.nan)
            pct[ok] = _pct_rank(s[ok])
            if hysteresis:
                stay = held & (pct >= EXIT_PCT)
                # FIX 2026-08-12: entries were UNCAPPED (all names >= ENTER_PCT
                # = 40 names per rebalance), ballooning breadth past N_HOLD and
                # diluting fresh alpha. Cap: fill only the room left by `stay`
                # with the best-scored candidates.
                new_held = stay.copy()
                room = N_HOLD - int(new_held.sum())
                cand = (~held) & (pct >= ENTER_PCT) & ok
                if room > 0 and cand.any():
                    ci = np.where(cand)[0]
                    new_held[ci[np.argsort(-s[ci])][:room]] = True
                # backfill toward breadth with best non-held if we shrank
                if new_held.sum() < N_HOLD * 0.6:
                    order = np.argsort(-np.where(ok, s, -np.inf))
                    for i in order:
                        if new_held.sum() >= N_HOLD * 0.6:
                            break
                        new_held[i] = True
                held = new_held
            else:
                order = np.argsort(-np.where(ok, s, -np.inf))
                held = np.zeros(N, dtype=bool)
                held[order[:N_HOLD]] = True
        # dead names always drop
        held &= np.isfinite(close[t])

        target = np.zeros(N, dtype=np.float32)
        nh = held.sum()
        if nh:
            target[held] = 1.0 / nh

        g = GROSS_CAP
        if trend_overlay and np.isfinite(idx_ma[t]) and index[t] < idx_ma[t]:
            g *= 0.4
        if ddev_target and strat_rets_so_far is not None and t > 130:
            rets = strat_rets_so_far(t)
            if rets is not None and len(rets) > 60:
                d = rets[-120:]
                dd = d[d < 0]
                ann = (np.sqrt((dd ** 2).mean()) * np.sqrt(252)) if len(dd) > 5 else None
                if ann and ann > 1e-4:
                    # FIX 2026-08-12: cap at 1.0 — the (0.3, 1.5) upper bound
                    # let the targeter LEVER UP a bleeding book when trailing
                    # ddev looked calm; the targeter must only de-risk.
                    g *= float(np.clip(TARGET_DDEV / ann, 0.3, 1.0))
        target = target * g
        gross[t] = g

        # weight dead-band — applied POST-gross so both sides of the
        # comparison are in the same units (review fix: comparing the
        # pre-gross target to the post-gross prev_w disabled the band
        # whenever gross != 1). Band-frozen names keep yesterday's weight;
        # no renormalization — small drift from exact equal-weight is the
        # point of a dead-band.
        if DEADBAND_BP > 0:
            small = (np.abs(target - prev_w) < DEADBAND_BP / 1e4) & (target > 0) & (prev_w > 0)
            target[small] = prev_w[small]

        w[t] = target
        if beta_hedge:
            hedge[t] = -float((target * beta).sum())
        prev_w = target
    return w, hedge, gross


def _pct_rank(x: np.ndarray) -> np.ndarray:
    # FIX 2026-08-12: average tied ranks. argsort tie-breaking by index order
    # let zero-score names (e.g. PEAD-only sleeve between announcements) sit
    # at index-dependent percentiles: high-index stale names never crossed
    # EXIT_PCT and were held forever (pead-only ablation grossed ~0%).
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x)); ranks[order] = np.arange(len(x), dtype=np.float64)
    _, inv = np.unique(x, return_inverse=True)
    sums = np.bincount(inv, weights=ranks)
    cnts = np.bincount(inv)
    ranks = (sums / cnts)[inv]
    return 100.0 * ranks / max(len(x) - 1, 1)
