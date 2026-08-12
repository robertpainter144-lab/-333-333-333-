"""Signal layer — illiquidity-scaled reversal + SUE-decay PEAD.

Point-in-time discipline: every signal at day t uses ONLY data through the
close of day t. The backtest executes resulting targets at the OPEN of t+1
(execution.py) — nothing here may peek forward.

Each signal returns a D x N score matrix (higher = more attractive to own).
Ablation flags let the backtest run any subset.
"""
from __future__ import annotations

import numpy as np


def _rolling_sum(x: np.ndarray, w: int) -> np.ndarray:
    """Trailing w-day sum ending AT each row (inclusive)."""
    c = np.nancumsum(x, axis=0)
    out = c.copy()
    out[w:] = c[w:] - c[:-w]
    return out


def daily_returns(close: np.ndarray) -> np.ndarray:
    r = np.full_like(close, np.nan)
    r[1:] = close[1:] / close[:-1] - 1.0
    return r


def reversal_score(panel: dict) -> np.ndarray:
    """Short-term reversal: z of past-5d market-residual return, negated,
    scaled up for illiquid names (where the planted/real effect lives)."""
    close, beta, illiq = panel["close"], panel["beta"], panel["illiq"]
    mkt = panel["mkt_ret"]
    r = daily_returns(close)
    resid = r - beta[None, :] * mkt[:, None]
    p5 = _rolling_sum(np.nan_to_num(resid), 5)
    # normalize by trailing 60d residual vol (lagged: through t)
    vol = np.full_like(p5, np.nan)
    for t in range(60, p5.shape[0]):
        vol[t] = np.nanstd(resid[t - 60:t], axis=0) * np.sqrt(5.0)
    z = p5 / np.where(vol > 1e-9, vol, np.nan)
    score = -z * (0.35 + 0.65 * illiq[None, :])
    score[:65] = np.nan                       # warmup
    return score


def pead_score(panel: dict, horizon: int = 20) -> np.ndarray:
    """SUE-proportional drift: score = latest announcement's SUE, decaying
    linearly to zero over `horizon` days; zero when no live announcement."""
    sue = panel["sue"]
    D, N = sue.shape
    score = np.zeros((D, N), dtype=np.float32)
    live_sue = np.zeros(N, dtype=np.float32)
    live_left = np.zeros(N, dtype=int)
    for t in range(D):
        ann = sue[t] != 0
        live_sue[ann] = sue[t][ann]
        live_left[ann] = horizon
        alive = live_left > 0
        score[t][alive] = live_sue[alive] * (live_left[alive] / horizon)
        live_left[alive] -= 1
    return score


def combined_score(panel: dict, use_reversal=True, use_pead=True) -> np.ndarray:
    """Cross-sectional z-blend of the enabled sleeves (equal risk weight)."""
    parts = []
    if use_reversal:
        parts.append(reversal_score(panel))
    if use_pead:
        parts.append(pead_score(panel))
    if not parts:
        raise ValueError("no sleeves enabled")
    out = np.zeros_like(parts[0])
    for p in parts:
        mu = np.nanmean(p, axis=1, keepdims=True)
        sd = np.nanstd(p, axis=1, keepdims=True)
        out = out + np.nan_to_num((p - mu) / np.where(sd > 1e-9, sd, np.nan))
    dead = ~np.isfinite(panel["close"])
    out[dead] = -np.inf                      # never rank a dead name
    return out
