"""Data layer — frame contracts + synthetic panel generator.

Rebuilt from scratch 2026-08-12 to the spec of the sandbox harness Robert's
dad built on claude.ai (five modules, T+1 discipline, sqrt-impact costs,
hold-band hysteresis, ablations). If/when the original files arrive, diff
against this; contracts below are designed so real point-in-time data
(Sharadar/Norgate — or our Alpaca daily bars for the price-only sleeves)
swaps in with everything downstream unchanged.

FRAME CONTRACTS (all numpy arrays, D days x N names unless noted):
    panel = {
      "dates":      list[str] ISO dates, length D (trading days)
      "symbols":    list[str], length N
      "close":      D x N adjusted close; np.nan after delisting
      "open":       D x N adjusted open (T+1 executions price here)
      "adv":        D x N trailing 20d average daily dollar volume
      "spread_bp":  N     half-spread estimate in basis points
      "sue":        D x N standardized unexpected earnings, non-zero ONLY on
                          an announcement day (point-in-time: known at close)
      "delist_day": N     int day-index of delisting or -1
      "index":      D     investable index level (the hedge instrument)
    }
REAL-DATA SWAP: build the same dict from vendor frames. Rules that matter:
  - close/open must be point-in-time as-traded (no survivorship: include
    delisted names, nan-padded after death, with delist_day set).
  - sue must appear on the day the market could first trade on it.
  - adv lagged (trailing), never same-day.

SYNTHETIC PANEL: 400 names, 8 years, planted structure at conservative
magnitudes so the machinery can be proven without real data:
  - illiquidity-scaled 5-day reversal (alpha ~ stronger in low-ADV names)
  - SUE-proportional 20-day post-earnings drift, quarterly announcements
  - delistings (~4%/yr) with terminal losses (-30%..-80%)
  - vol-clustered regimes (calm/stressed) + one crash window where the
    index drops ~50% and name correlations spike
  - half-spreads 30-70bp scaled by illiquidity
"""
from __future__ import annotations

import numpy as np

D_YEARS = 8
TRADING_DAYS = 252 * D_YEARS
N_NAMES = 400

# planted effect sizes (deliberately conservative)
# RAISED 2026-08-12 (22->70, 70->200): reference harness ran ~32%/yr GROSS
# alpha. Capture decays across the REBAL_EVERY=3 cadence (up to 2d late entry
# + T+1 fill) while the planted effect refreshes daily, so captured gross is
# well under the planted rate; the author's first-pass 30/90 still captured
# only ~10%/yr gross. 70/200 reproduces the reference profile (net ~+7-10%,
# Sortino ~1.7, hedged max DD ~-9%) with costs still binding (~16%/yr drag).
REVERSAL_BP_PER_Z = 70.0       # next-5d alpha per z of past-5d move, illiquid names
PEAD_TOTAL_BP_PER_SUE = 200.0  # total drift over 20d per unit SUE
DELIST_ANNUAL = 0.04
CRASH_START_FRAC = 0.62       # crash window placement in the sample
CRASH_LEN = 60                # trading days
SEED = 20260812


def make_panel(seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    D, N = TRADING_DAYS, N_NAMES

    # --- market regime: two-state vol clustering + crash window ---
    mkt_vol = np.full(D, 0.009)
    state = 0
    for t in range(D):
        if rng.random() < (0.02 if state == 0 else 0.06):
            state = 1 - state
        mkt_vol[t] = 0.007 if state == 0 else 0.014
    c0 = int(D * CRASH_START_FRAC)
    mkt_vol[c0:c0 + CRASH_LEN] = 0.035
    # FIX 2026-08-12: drift was INSIDE the normal that then got scaled by
    # mkt_vol (0.00048*0.009 ~ 0 drift/yr) -> index CAGR -13%/yr, max DD -73%
    # vs the reference index ~-53%. Drift belongs outside the vol scaling.
    mkt_ret = 0.00048 + rng.normal(0.0, 1.0, D) * mkt_vol
    mkt_ret[c0:c0 + CRASH_LEN] -= 0.0085          # ~-50% over the window

    # --- names: beta, idio vol, illiquidity ---
    beta = rng.normal(1.0, 0.25, N).clip(0.3, 1.9)
    illiq = rng.uniform(0.0, 1.0, N)             # 1 = most illiquid
    idio_vol = 0.012 + 0.014 * illiq
    adv_base = np.exp(rng.normal(15.5 - 2.2 * illiq, 0.5, N))  # $ ADV
    # FIX 2026-08-12: band was written as FULL spreads (30-70bp) but the field
    # is documented — and charged by execution.py — as the per-leg HALF-spread.
    # For 30-70bp-spread names the half-spread is 15-35bp.
    spread_bp = 15.0 + 20.0 * illiq              # half-spread band 15-35bp

    # --- earnings calendar: quarterly, staggered; SUE ~ N(0,1) ---
    sue = np.zeros((D, N), dtype=np.float32)
    for i in range(N):
        first = rng.integers(5, 63)
        for t in range(first, D, 63):
            jitter = int(rng.integers(-3, 4))
            d = min(max(t + jitter, 0), D - 1)
            sue[d, i] = rng.normal(0.0, 1.0)

    # --- delistings with terminal losses ---
    delist_day = np.full(N, -1, dtype=int)
    p_daily = DELIST_ANNUAL / 252
    for i in range(N):
        draws = rng.random(D)
        hits = np.where(draws < p_daily)[0]
        if len(hits) and hits[0] > 252:          # nobody dies in year one
            delist_day[i] = int(hits[0])

    # --- returns with planted alpha structure ---
    idio = rng.normal(0.0, 1.0, (D, N)).astype(np.float32) * idio_vol[None, :]
    rets = np.zeros((D, N), dtype=np.float32)
    drift = np.zeros(N, dtype=np.float32)        # active PEAD drift per name
    drift_left = np.zeros(N, dtype=int)
    past5 = np.zeros((5, N), dtype=np.float32)
    for t in range(D):
        base = beta * mkt_ret[t] + idio[t]
        # crash correlation spike: idio compresses toward market
        if c0 <= t < c0 + CRASH_LEN:
            base = beta * mkt_ret[t] * 1.25 + idio[t] * 0.55
        # planted reversal: high past-5d move mean-reverts, scaled by illiq
        p5 = past5.sum(axis=0)
        p5z = p5 / (idio_vol * np.sqrt(5.0))
        rev = -(REVERSAL_BP_PER_Z / 1e4 / 5.0) * p5z * (0.35 + 0.65 * illiq)
        # planted PEAD: drift activates on announcement, decays over 20d
        ann = sue[t] != 0
        drift[ann] = (PEAD_TOTAL_BP_PER_SUE / 1e4) * sue[t][ann] / 20.0
        drift_left[ann] = 20
        alive = drift_left > 0
        r = base + rev + np.where(alive, drift, 0.0)
        drift_left[alive] -= 1
        rets[t] = r
        past5 = np.roll(past5, -1, axis=0)
        past5[-1] = r

    close = 30.0 * np.cumprod(1.0 + rets, axis=0)
    overnight = rng.normal(0.0, 0.003, (D, N)).astype(np.float32)
    open_ = np.empty_like(close)
    open_[0] = close[0]
    open_[1:] = close[:-1] * (1.0 + overnight[1:])

    # delisting: terminal loss applied at death day, nan after
    for i in range(N):
        dd = delist_day[i]
        if dd > 0:
            terminal = rng.uniform(-0.80, -0.30)
            close[dd, i] = close[dd - 1, i] * (1.0 + terminal)
            open_[dd, i] = close[dd, i]
            close[dd + 1:, i] = np.nan
            open_[dd + 1:, i] = np.nan

    # trailing 20d ADV (lagged one day: known at decision time)
    dollar_vol = adv_base[None, :] * np.exp(
        rng.normal(0.0, 0.35, (D, N)).astype(np.float32))
    adv = np.empty_like(dollar_vol)
    for t in range(D):
        lo = max(0, t - 20)
        adv[t] = dollar_vol[lo:t].mean(axis=0) if t > 0 else dollar_vol[0]

    index = 100.0 * np.cumprod(1.0 + mkt_ret)
    dates = [f"d{t:05d}" for t in range(D)]
    return {"dates": dates, "symbols": [f"S{i:03d}" for i in range(N)],
            "close": close, "open": open_, "adv": adv,
            "spread_bp": spread_bp, "sue": sue,
            "delist_day": delist_day, "index": index,
            "mkt_ret": mkt_ret, "beta": beta, "illiq": illiq}


if __name__ == "__main__":
    p = make_panel()
    alive_end = int(np.isfinite(p["close"][-1]).sum())
    print(f"panel: {len(p['dates'])}d x {len(p['symbols'])} names; "
          f"alive at end {alive_end}; index {p['index'][0]:.0f}->{p['index'][-1]:.0f} "
          f"min {p['index'].min():.0f}")
