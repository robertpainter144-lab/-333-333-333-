"""Momentum signal engine — replicates QC strategy logic."""
import numpy as np
import pandas as pd


def rsi(prices, period=14):
    """Standard RSI."""
    delta = prices.diff().dropna()
    up   = delta.clip(lower=0)
    down = (-delta).clip(lower=0)
    avg_up   = up.ewm(alpha=1/period, min_periods=period).mean()
    avg_down = down.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_up / avg_down.replace(0, np.nan)
    return float(100 - (100 / (1 + rs)).iloc[-1])


def composite_momentum(prices):
    """
    Vol-adjusted composite momentum used by strategies 245, 46, 60, 123.
    Returns a single float score (higher = stronger momentum).
    """
    n = len(prices)
    if n < 21:
        return 0.0
    curr = float(prices.iloc[-1])

    roc_20  = (curr / float(prices.iloc[-20]) - 1) if n >= 21  else 0.0
    roc_60  = (curr / float(prices.iloc[-60]) - 1) if n >= 61  else roc_20
    roc_120 = (curr / float(prices.iloc[-120])- 1) if n >= 121 else roc_60

    raw = 0.4 * roc_20 + 0.4 * roc_60 + 0.2 * roc_120

    vol20 = float(prices.pct_change().dropna().iloc[-20:].std()) if n >= 21 else 0.01
    score = raw / max(vol20, 1e-4)

    # MA-50 filter
    sma50 = float(prices.iloc[-50:].mean()) if n >= 50 else float(prices.mean())
    score *= (1.0 if curr >= sma50 else 0.75)

    # RSI penalty
    try:
        r = rsi(prices.iloc[-30:], 14)
        if r > 75 or r < 25:
            score *= 0.85
    except Exception:
        pass

    return score


def rank_universe(universe, close_df):
    """
    Rank all symbols in `universe` by composite_momentum.
    Returns list of (symbol, score) sorted descending.
    """
    scored = []
    for sym in universe:
        col = str(sym)
        if col not in close_df.columns:
            scored.append((sym, 0.0))
            continue
        prices = close_df[col].dropna()
        scored.append((sym, composite_momentum(prices)))
    return sorted(scored, key=lambda x: x[1], reverse=True)


def get_allocation(cfg, close_df):
    """
    Determine current {symbol: weight} allocation for a strategy config.
    Weights sum to 1.0.
    """
    universe  = cfg.get("universe", [])
    alloc_type = cfg.get("alloc", "single_winner")

    if not universe:
        return {}

    ranked = rank_universe(universe, close_df)
    syms   = [s for s, _ in ranked]
    scores = {s: sc for s, sc in ranked}

    if alloc_type == "single_winner":
        # Shift to cash proxy if top score is deeply negative
        best, best_score = ranked[0] if ranked else (None, 0)
        if best is None:
            return {}
        if best_score < -5.0:
            return {"SHV": 1.0}   # short-term T-bill proxy as cash
        return {best: 1.0}

    elif alloc_type == "top3_equal":
        top = syms[:min(3, len(syms))]
        w = 1.0 / len(top)
        return {s: w for s in top}

    elif alloc_type == "top5_equal":
        top = syms[:min(5, len(syms))]
        w = 1.0 / len(top)
        return {s: w for s in top}

    elif alloc_type == "proportional":
        pos = {s: max(sc, 1e-6) for s, sc in ranked[:5]}
        total = sum(pos.values())
        return {s: v/total for s, v in pos.items()}

    return {syms[0]: 1.0} if syms else {}


def compute_portfolio_weights(strategies):
    """
    Given top-N strategies sorted by OOS 3M return (desc), return portfolio
    weights where the #1 strategy receives 2× its proportional share.

    strategies: list of config dicts, each with 'statistics' key.
    Returns list of floats summing to 1.0.
    """
    returns = []
    for s in strategies:
        r = s.get("statistics", {}).get("oos 3m return", 0)
        returns.append(max(float(r), 0.01))

    total = sum(returns)
    base  = [r / total for r in returns]
    base[0] *= 2.0                        # double-weight the winner
    total2 = sum(base)
    return [w / total2 for w in base]
