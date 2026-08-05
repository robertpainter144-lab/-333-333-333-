"""Portfolio manager — takes raw strategy signals + open positions and produces
final ACT signals under prop-desk constraints:

  1. Correlation cap: max 1 open position per cluster (majors/l1s/memes/defi/aidepin/other).
  2. Cost gate: skip if expected_move ≤ 2× cost (0.35% → require ≥0.7% target).
  3. Kelly-lite sizing: base $10 × min(2.0, score/2).
  4. Regime detector: BTC 1h vol → gates which strategies get to fire.
  5. Ensemble bump: if ≥2 strategies BUY same symbol, size *= 1.5.
  6. Whale confluence: recent big-money signals from other scanner sources
     amplify our algo entries. Strong whale-only signals can also open a
     small "whale-primary" position when our algos are silent.

Every tick, called by sources/prop_desk.py:
    manage(hold_state) → list[act_signal]  &  updates hold_state
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from strategies.base import (
    UNIVERSE, CLUSTERS, ROUNDTRIP_COST_PCT, Signal,
    cluster_of, load_series, realized_vol_pct, pct, ROOT as _BASE_ROOT,
)
from strategies import momentum, mean_reversion, breakout

# ─── Whale confluence ────────────────────────────────────────────────
SIGNALS_FEED = _BASE_ROOT / "state" / "signals.jsonl"
WHALE_LOOKBACK_S = 30 * 60          # 30-min rolling look-back

# Weight per non-prop_desk signal type. Positive = bullish. Negative = bearish.
WHALE_WEIGHTS = {
    "coordinated_accumulation": 3.0,
    "whale_trade":              2.0,
    "cex_withdrawal":           1.5,
    "extreme_funding":          1.2,
    "large_transfer":           1.0,
    "dex_momentum":             1.0,
    "trending":                 0.7,
    "big_gainer_24h":           0.6,
    "large_oi":                 0.4,
    "boost":                    0.4,
    "fresh_boost":              0.3,
    "bridge_in":                0.3,
    "bridge_out":               0.3,
    # Bearish
    "cex_deposit":             -1.5,
    "big_loser_24h":           -0.6,
}

WHALE_PRIMARY_MIN_SCORE = 3.5       # whale-only entry requires strong confluence
WHALE_PRIMARY_SIZE_USD  = 10.00     # Alpaca crypto min notional is $10
WHALE_AMP_PER_POINT     = 0.30      # each whale point adds 30% to size
WHALE_AMP_MAX_MULT      = 3.0       # cap on total whale amplification

# Multi-timeframe momentum variants — same code, different windows/thresholds.
# Each variant gets its own tag in the ledger so the brain can score them separately.
class _MomentumVariant:
    def __init__(self, name, entry_sigma, exit_sigma, accel_pp):
        self.name = name
        self.entry_sigma = entry_sigma
        self.exit_sigma = exit_sigma
        self.accel_pp = accel_pp
    def evaluate(self, sym, held):
        import strategies.momentum as m
        saved = (m.ENTRY_SIGMA, m.ENTRY_MIN_ACCEL_PP, m.EXIT_SIGMA)
        m.ENTRY_SIGMA        = self.entry_sigma
        m.EXIT_SIGMA         = self.exit_sigma
        m.ENTRY_MIN_ACCEL_PP = self.accel_pp
        try:
            sig = m.evaluate(sym, held)
            if sig is not None:
                sig.strategy = self.name
            return sig
        finally:
            m.ENTRY_SIGMA, m.ENTRY_MIN_ACCEL_PP, m.EXIT_SIGMA = saved

_mom_2m  = _MomentumVariant("momentum",     2.0, -1.0, 0.05)
_mom_5m  = _MomentumVariant("momentum_5m",  1.8, -1.0, 0.03)
_mom_15m = _MomentumVariant("momentum_15m", 1.5, -1.0, 0.02)

STRATEGIES = {
    "momentum":       _mom_2m.evaluate,
    "momentum_5m":    _mom_5m.evaluate,
    "momentum_15m":   _mom_15m.evaluate,
    "mean_reversion": mean_reversion.evaluate,
    "breakout":       breakout.evaluate,
}

# Base sizing knobs
BASE_SIZE_USD    = 10.00       # Alpaca crypto min ~$10; also cost gate needs a real target
MAX_MULT         = 2.0
MIN_EXPECTED_PCT = ROUNDTRIP_COST_PCT * 2       # default; overridden by auto_tune

# Auto-tune config lives in state/auto_tune_config.json; portfolio reads on every tick.
AUTO_TUNE_CONFIG = _BASE_ROOT / "state" / "auto_tune_config.json"


def _load_auto_tune() -> dict:
    if not AUTO_TUNE_CONFIG.exists():
        return {}
    try:
        return json.loads(AUTO_TUNE_CONFIG.read_text())
    except Exception:
        return {}

# Regime thresholds (BTC 1h realized vol %). Realistic-vol calibration:
# BTC quiet hours run 0.10-0.20%; active 0.4%+. Keep chop bar low so we're
# usually in "mixed" regime and can attempt momentum/breakout.
REGIME_HIGH_VOL_PCT = 0.4
REGIME_LOW_VOL_PCT  = 0.10


def detect_regime() -> str:
    """Simple: use BTC's 1-hour realized vol as the market regime proxy."""
    series = load_series("BTC", n=120)
    if len(series) < 6:
        return "unknown"
    vol = realized_vol_pct(series, horizon_s=3600)
    if vol >= REGIME_HIGH_VOL_PCT:
        return "trending"        # favor momentum + breakout
    if vol <= REGIME_LOW_VOL_PCT:
        return "chop"            # favor mean-reversion
    return "mixed"                # all three eligible


def gated_strategies(regime: str) -> set[str]:
    if regime == "trending": return {"momentum", "breakout"}
    if regime == "chop":     return {"mean_reversion"}
    return {"momentum", "mean_reversion", "breakout"}


def size_for(score: float) -> float:
    """Kelly-lite: base × min(MAX_MULT, score/2)."""
    m = max(0.5, min(MAX_MULT, score / 2.0))
    return round(BASE_SIZE_USD * m, 2)


def load_whale_confluence(now_ts: int) -> dict[str, dict]:
    """Read tail of signals.jsonl, aggregate per-symbol whale score in the last
    30 minutes. Non-prop_desk signals only. Returns {SYM: {score, hits[list]}}"""
    if not SIGNALS_FEED.exists():
        return {}
    try:
        lines = SIGNALS_FEED.read_text().splitlines()[-800:]
    except Exception:
        return {}
    cutoff = now_ts - WHALE_LOOKBACK_S
    agg: dict[str, dict] = defaultdict(lambda: {"score": 0.0, "hits": []})
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("ts", 0) < cutoff:
            continue
        if r.get("source") == "prop_desk":
            continue   # don't count our own signals
        stype = r.get("signal_type", "")
        weight = WHALE_WEIGHTS.get(stype, 0.0)
        if not weight:
            continue
        sym = (r.get("symbol") or "").upper()
        if sym not in UNIVERSE:   # only care about our tradeable universe
            continue
        agg[sym]["score"] += weight
        agg[sym]["hits"].append(stype)
    # round scores
    for sym, d in agg.items():
        d["score"] = round(d["score"], 2)
    return dict(agg)


def manage(hold_state: dict) -> tuple[list[dict], str, dict, list[dict]]:
    """Run all strategies, aggregate to ACT signals under prop-desk rules.
    Whale-confluence layer amplifies size on algo hits AND can open small
    whale-primary positions when whales strongly agree even if our algos are silent.

    Returns:
      acts        — list of act signals with action/size
      regime      — current regime string
      per_sym_agg — dict[sym] -> {buys, sells, top}
      rejections  — list of {sym, reason, note, strategy}
    """
    now_ts      = int(time.time())
    regime      = detect_regime()
    active      = gated_strategies(regime)
    # Auto-tune: strategy on/off + kelly_mult + cost gate override
    tune        = _load_auto_tune()
    tune_strats = tune.get("strategies", {}) if tune else {}
    min_expected = tune.get("min_expected_pct", MIN_EXPECTED_PCT) if tune else MIN_EXPECTED_PCT
    # Apply on/off gating on top of regime gating
    active = {s for s in active if tune_strats.get(s, {}).get("enabled", True)}

    whale_agg   = load_whale_confluence(now_ts)
    open_syms   = set(hold_state.keys())
    cluster_open: dict[str, str] = {}
    for s in open_syms:
        cluster_open[cluster_of(s)] = s

    # Per-symbol: collect all strategy verdicts.
    # Also compute near-miss scores per strategy so we can emit trace lines
    # showing what each strategy is watching each tick.
    per_sym: dict[str, list[Signal]] = defaultdict(list)
    per_strat: dict[str, list[Signal]] = defaultdict(list)   # for trace
    for sym in UNIVERSE:
        held = hold_state.get(sym)
        for name, evaluate in STRATEGIES.items():
            if name not in active:
                continue
            sig = evaluate(sym, held)
            if sig is not None:
                per_sym[sym].append(sig)
                per_strat[name].append(sig)

    acts: list[dict] = []
    per_sym_agg = {}
    rejections: list[dict] = []    # signals filtered by constraints — surface for visibility

    for sym, sigs in per_sym.items():
        buys  = [s for s in sigs if s.action == "BUY"]
        sells = [s for s in sigs if s.action == "SELL"]
        per_sym_agg[sym] = {
            "buys":  len(buys), "sells": len(sells),
            "top":   sigs[0].note if sigs else "",
        }

        # If we have any SELL for a held position → execute exit
        if sym in open_syms and sells:
            top = max(sells, key=lambda s: s.score)
            held = hold_state[sym]
            entry_px = held["entry_px"]
            pnl_pct  = pct(top.px, entry_px) - ROUNDTRIP_COST_PCT   # subtract cost estimate
            acts.append({
                "sym": sym, "action": "SELL",
                "strategy": top.strategy,
                "px": top.px, "size_usd": held.get("size_usd", BASE_SIZE_USD),
                "score": top.score, "note": top.note,
                "pnl_pct_est": round(pnl_pct, 3),
                "entry_px": entry_px, "reasons": [s.strategy for s in sells],
            })
            del hold_state[sym]
            open_syms.discard(sym)
            cluster_open.pop(cluster_of(sym), None)
            continue

        # BUY logic — apply constraints
        if buys and sym not in open_syms:
            # cluster cap
            cl = cluster_of(sym)
            if cl in cluster_open:
                rejections.append({"sym": sym, "reason": "cluster_cap",
                                    "note": f"{cl} already has {cluster_open[cl]}",
                                    "strategy": buys[0].strategy})
                continue
            # cost gate — top signal's expected move must clear the (auto-tuned) threshold
            top = max(buys, key=lambda s: s.score)
            if top.expected_move < min_expected:
                rejections.append({"sym": sym, "reason": "cost_gate",
                                    "note": (f"expected {top.expected_move:.2f}% < "
                                             f"{min_expected:.2f}% required"),
                                    "strategy": top.strategy})
                continue
            # ensemble bump
            strategies_agree = len(buys)
            size = size_for(top.score)
            if strategies_agree >= 2:
                size = round(size * 1.5, 2)
            # Auto-tune per-strategy Kelly boost
            strat_boost = tune_strats.get(top.strategy, {}).get("kelly_mult", 1.0)
            size = round(size * strat_boost, 2)
            # WHALE AMPLIFIER — bigger position when whales confirm
            w = whale_agg.get(sym, {})
            wscore = w.get("score", 0.0)
            whale_mult = min(WHALE_AMP_MAX_MULT, max(0.5, 1.0 + wscore * WHALE_AMP_PER_POINT))
            size = round(size * whale_mult, 2)
            acts.append({
                "sym": sym, "action": "BUY",
                "strategy": top.strategy,
                "px": top.px, "size_usd": size,
                "score": top.score, "note": top.note,
                "expected_move": top.expected_move,
                "reasons": [s.strategy for s in buys],
                "ensemble": strategies_agree,
                "whale_score": wscore,
                "whale_hits":  w.get("hits", []),
                "whale_mult":  round(whale_mult, 2),
            })
            hold_state[sym] = {
                "entry_ts":   now_ts,
                "entry_px":   top.px,
                "peak_px":    top.px,
                "size_usd":   size,
                "strategy":   top.strategy,
                "reasons":    [s.strategy for s in buys],
                "whale_score": wscore,
            }
            open_syms.add(sym)
            cluster_open[cl] = sym

    # ─── WHALE-PRIMARY: open small position when whales agree even if algos silent ─
    whale_primary_entries: list[dict] = []
    for sym, w in whale_agg.items():
        if sym in open_syms:
            continue
        if w["score"] < WHALE_PRIMARY_MIN_SCORE:
            continue
        cl = cluster_of(sym)
        if cl in cluster_open:
            rejections.append({"sym": sym, "reason": "cluster_cap",
                                "note": f"whale-primary blocked: {cl} has {cluster_open[cl]}",
                                "strategy": "whale_primary"})
            continue
        # get a current px hint from series
        ser = load_series(sym, n=10)
        if not ser:
            continue
        px = ser[-1]["price"]
        acts.append({
            "sym": sym, "action": "BUY",
            "strategy": "whale_primary",
            "px": px, "size_usd": WHALE_PRIMARY_SIZE_USD,
            "score": w["score"], "note": f"whale-primary: hits={w['hits']}",
            "expected_move": 0.0,
            "reasons": ["whale_confluence"],
            "ensemble": 0,
            "whale_score": w["score"],
            "whale_hits":  w["hits"],
            "whale_mult":  1.0,
        })
        hold_state[sym] = {
            "entry_ts":   now_ts,
            "entry_px":   px,
            "peak_px":    px,
            "size_usd":   WHALE_PRIMARY_SIZE_USD,
            "strategy":   "whale_primary",
            "reasons":    ["whale_confluence"],
            "whale_score": w["score"],
        }
        open_syms.add(sym)
        cluster_open[cl] = sym
        whale_primary_entries.append({"sym": sym, "score": w["score"]})

    return acts, regime, per_sym_agg, rejections, per_strat
