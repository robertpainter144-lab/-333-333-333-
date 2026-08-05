"""Self-teaching prop-desk brain.

Reads the closed-trade ledger (state/prop_ledger.jsonl) and decides autonomously:
  - Disable a strategy when its rolling-window mean pnl is clearly negative.
  - Re-enable after a cooldown.
  - Boost a strategy's size when it shows edge.
  - Tighten / loosen the cost gate based on churn vs signal quality.
  - Promote from paper -> live once we've earned it.

Every decision is logged to state/decisions.jsonl with reason + evidence, so the
UI can render the "brain" as a running commentary.

Configuration flows through state/auto_tune_config.json, which portfolio.py reads.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
LEDGER = STATE / "prop_ledger.jsonl"
CONFIG = STATE / "auto_tune_config.json"
DECISIONS = STATE / "decisions.jsonl"

# Tunables for the brain itself.
WINDOW_TRADES        = 30        # rolling window of latest trades per strategy
MIN_N_TO_JUDGE_KILL  = 10        # need >= N trades before we kill a strategy
MIN_N_TO_JUDGE_BOOST = 10
KILL_MEAN_PNL        = -0.15     # if rolling mean <= this, disable
BOOST_MEAN_PNL       = 0.05      # a real, small positive edge
DISABLE_COOLDOWN_S   = 60 * 60   # 1h disable window before re-enable trial

# Promotion criteria (strict — designed to catch REAL edge, not variance):
PROMOTE_MIN_TRADES   = 30
PROMOTE_MIN_MEAN     = 0.05      # +0.05% mean per trade net of cost model
PROMOTE_MIN_SHARPE   = 1.0       # mean/std >= 1.0
PROMOTE_MIN_LATE_MEAN = 0.02     # last 20% of trades must still be positive

DEFAULT_CONFIG = {
    "version":         1,
    "min_expected_pct": 0.70,   # cost gate (2x roundtrip)
    "strategies": {
        "momentum":       {"enabled": True, "kelly_mult": 1.0, "disabled_until_ts": 0},
        "momentum_5m":    {"enabled": True, "kelly_mult": 1.0, "disabled_until_ts": 0},
        "momentum_15m":   {"enabled": True, "kelly_mult": 1.0, "disabled_until_ts": 0},
        "mean_reversion": {"enabled": True, "kelly_mult": 1.0, "disabled_until_ts": 0},
        "breakout":       {"enabled": True, "kelly_mult": 1.0, "disabled_until_ts": 0},
        "whale_primary":  {"enabled": True, "kelly_mult": 1.0, "disabled_until_ts": 0},
    },
    "notes": "seeded default with multi-timeframe variants",
}


def load_config() -> dict:
    if not CONFIG.exists():
        write_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        c = json.loads(CONFIG.read_text())
        # Fill missing keys
        for k, v in DEFAULT_CONFIG.items():
            if k not in c:
                c[k] = v
        for s, defs in DEFAULT_CONFIG["strategies"].items():
            c.setdefault("strategies", {}).setdefault(s, defs)
        return c
    except Exception:
        return dict(DEFAULT_CONFIG)


def write_config(cfg: dict) -> None:
    STATE.mkdir(exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True))
    tmp.replace(CONFIG)


def load_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for ln in LEDGER.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def append_decision(row: dict) -> None:
    STATE.mkdir(exist_ok=True)
    row["ts"] = int(time.time())
    with DECISIONS.open("a") as f:
        f.write(json.dumps(row) + "\n")


def last_decisions(n: int = 60) -> list[dict]:
    if not DECISIONS.exists():
        return []
    out = []
    for ln in DECISIONS.read_text().splitlines()[-n:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return list(reversed(out))     # newest first


def rolling_stats(ledger: list[dict], strategy: str, window: int = WINDOW_TRADES) -> dict:
    sel = [t for t in ledger if t.get("strategy") == strategy][-window:]
    n = len(sel)
    if n == 0:
        return {"n": 0, "mean": 0.0, "total": 0.0, "wins": 0, "losses": 0}
    total = sum(t.get("pnl_pct", 0) for t in sel)
    wins = sum(1 for t in sel if t.get("pnl_pct", 0) > 0)
    losses = sum(1 for t in sel if t.get("pnl_pct", 0) < 0)
    return {"n": n, "mean": round(total / n, 3),
            "total": round(total, 3), "wins": wins, "losses": losses}


def strategy_health(ledger: list[dict]) -> dict[str, dict]:
    out = {}
    strategies = ("momentum", "momentum_5m", "momentum_15m",
                  "mean_reversion", "breakout", "whale_primary")
    import math
    for s in strategies:
        st = rolling_stats(ledger, s)
        conf = round(abs(st["mean"]) * (st["n"] ** 0.5), 3)
        # Sharpe on rolling window
        sel = [t for t in ledger if t.get("strategy") == s][-WINDOW_TRADES:]
        sharpe = 0.0
        if len(sel) >= 3:
            pnls = [t.get("pnl_pct", 0) for t in sel]
            mean = sum(pnls) / len(pnls)
            var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
            std = math.sqrt(var) if var > 0 else 0
            sharpe = round(mean / std, 3) if std > 0 else 0.0
        # Late-period edge: last 20% of trades
        late = sel[int(len(sel) * 0.8):]
        late_mean = round(sum(t.get("pnl_pct", 0) for t in late) / len(late), 3) if late else 0.0
        out[s] = {**st, "confidence": conf, "sharpe": sharpe, "late_mean": late_mean}
    return out


def tick() -> list[dict]:
    """Run one auto-tune evaluation, apply decisions, return the list of
    decisions made this tick (for immediate emission)."""
    now = int(time.time())
    cfg = load_config()
    ledger = load_ledger()
    health = strategy_health(ledger)
    decisions_made = []

    strategies_cfg = cfg["strategies"]

    # ─── Per-strategy actions ────────────────────────────────────────
    for name, sc in strategies_cfg.items():
        h = health.get(name, {})
        # Re-enable a previously-disabled strategy once the cooldown is over
        if not sc["enabled"] and now >= sc.get("disabled_until_ts", 0):
            sc["enabled"] = True
            sc["disabled_until_ts"] = 0
            d = {"type": "reenable_strategy", "strategy": name,
                 "reason": "cooldown_expired",
                 "evidence": {"cooldown_min": DISABLE_COOLDOWN_S // 60}}
            append_decision(d); decisions_made.append(d)
            continue

        # KILL: rolling-window mean pnl is clearly negative
        if sc["enabled"] and h["n"] >= MIN_N_TO_JUDGE_KILL and h["mean"] <= KILL_MEAN_PNL:
            sc["enabled"] = False
            sc["disabled_until_ts"] = now + DISABLE_COOLDOWN_S
            d = {"type": "disable_strategy", "strategy": name,
                 "reason": f"rolling mean {h['mean']:+.3f}% ≤ kill threshold {KILL_MEAN_PNL}%",
                 "evidence": h}
            append_decision(d); decisions_made.append(d)
            continue

        # BOOST: strong positive rolling edge (rare, but nice when it happens)
        if sc["enabled"] and h["n"] >= MIN_N_TO_JUDGE_BOOST and h["mean"] >= BOOST_MEAN_PNL:
            new_mult = round(min(2.0, sc["kelly_mult"] + 0.2), 2)
            if new_mult != sc["kelly_mult"]:
                old = sc["kelly_mult"]
                sc["kelly_mult"] = new_mult
                d = {"type": "boost_strategy", "strategy": name,
                     "reason": f"rolling mean {h['mean']:+.3f}% ≥ boost threshold {BOOST_MEAN_PNL}%",
                     "evidence": {**h, "kelly_mult_before": old, "kelly_mult_after": new_mult}}
                append_decision(d); decisions_made.append(d)
                continue

        # De-boost: performance regressed after a boost — pull back to 1.0
        if sc["enabled"] and sc["kelly_mult"] > 1.0 and h["n"] >= MIN_N_TO_JUDGE_KILL and h["mean"] < 0:
            old = sc["kelly_mult"]
            sc["kelly_mult"] = 1.0
            d = {"type": "reset_kelly", "strategy": name,
                 "reason": f"boost regressed: mean {h['mean']:+.3f}% after boost",
                 "evidence": {**h, "kelly_mult_before": old, "kelly_mult_after": 1.0}}
            append_decision(d); decisions_made.append(d)

    # ─── Global cost-gate tuning ────────────────────────────────────
    # If ALL strategies are disabled, loosen the cost gate to let something fire.
    # If all are wildly bleeding, tighten it.
    active_n = sum(1 for s in strategies_cfg.values() if s["enabled"])
    total_recent = sum(rolling_stats(ledger, s)["total"] for s in strategies_cfg)
    total_n = sum(rolling_stats(ledger, s)["n"] for s in strategies_cfg)
    if active_n == 0 and cfg["min_expected_pct"] > 0.3:
        old = cfg["min_expected_pct"]
        cfg["min_expected_pct"] = 0.30
        d = {"type": "loosen_cost_gate",
             "reason": "all strategies disabled — need to see something to learn",
             "evidence": {"min_expected_pct_before": old, "after": 0.30}}
        append_decision(d); decisions_made.append(d)
    elif total_n >= 20 and total_recent <= -8.0 and cfg["min_expected_pct"] < 1.5:
        old = cfg["min_expected_pct"]
        cfg["min_expected_pct"] = round(min(1.5, cfg["min_expected_pct"] + 0.2), 2)
        d = {"type": "tighten_cost_gate",
             "reason": f"broad bleed: total_pnl {total_recent:+.2f}% over last {total_n} trades",
             "evidence": {"min_expected_pct_before": old, "after": cfg["min_expected_pct"]}}
        append_decision(d); decisions_made.append(d)

    # ─── Promotion consideration (strict — designed to catch REAL edge) ─
    # A strategy is READY_FOR_LIVE only when:
    #   1. n_trades >= 30 (statistical basis)
    #   2. rolling mean pnl >= +0.05% per trade net of cost model
    #   3. rolling Sharpe >= 1.0 (positive mean and reasonable stability)
    #   4. Late 20% of trades still positive (not front-loaded luck)
    # We check per-strategy, and mark the whole system READY if ANY strategy
    # satisfies all four bars. The user still has to manually flip ALPACA_MODE.
    import math
    ready_strategy = None
    for name, h in health.items():
        if (h["n"] >= PROMOTE_MIN_TRADES and
            h["mean"] >= PROMOTE_MIN_MEAN and
            h.get("sharpe", 0) >= PROMOTE_MIN_SHARPE and
            h.get("late_mean", 0) >= PROMOTE_MIN_LATE_MEAN):
            ready_strategy = name
            break

    if ready_strategy and not cfg.get("ready_for_live"):
        cfg["ready_for_live"] = True
        cfg["ready_strategy"] = ready_strategy
        h = health[ready_strategy]
        d = {"type": "ready_for_live", "strategy": ready_strategy,
             "reason": (f"passed all 4 gates: n={h['n']}, mean={h['mean']:+.3f}%, "
                        f"sharpe={h.get('sharpe',0):.2f}, "
                        f"late_mean={h.get('late_mean',0):+.3f}%"),
             "evidence": h}
        append_decision(d); decisions_made.append(d)
    elif cfg.get("ready_for_live") and ready_strategy is None:
        # Regressed — no strategy currently qualifies
        cfg["ready_for_live"] = False
        cfg.pop("ready_strategy", None)
        d = {"type": "revoke_ready_for_live",
             "reason": "no strategy currently meets all 4 promotion gates",
             "evidence": health}
        append_decision(d); decisions_made.append(d)

    cfg["version"] += 1 if decisions_made else 0
    cfg["notes"] = f"auto-tune tick {time.strftime('%H:%M:%S')} — {len(decisions_made)} decisions"
    write_config(cfg)
    return decisions_made


def apply_initial_bootstrap() -> list[dict]:
    """First-run action: seed a decision to reflect current strategy health
    so the timeline isn't empty when the user opens the app."""
    ledger = load_ledger()
    if not ledger:
        return []
    health = strategy_health(ledger)
    ds = []
    for name, h in health.items():
        if h["n"] >= 5:
            ds.append({
                "type": "observation", "strategy": name,
                "reason": f"initial snapshot at auto-tune boot",
                "evidence": h,
            })
    for d in ds:
        append_decision(d)
    return ds


if __name__ == "__main__":
    if not DECISIONS.exists():
        apply_initial_bootstrap()
    made = tick()
    print(json.dumps({"decisions_made": made, "config": load_config()}, indent=2))
