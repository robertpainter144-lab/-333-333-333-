"""Strategy Governor.

Every 15 min, snapshot the current P/L of the four parallel books:
  - combined_mirror LIVE ($50 real money)
  - qc_mirror PAPER (QC-only reference)
  - patent_mirror PAPER (patent top-3 mirror)
  - prop_desk PAPER (multi-strategy crypto experiment)

Log each snapshot to state/strategy_governor.jsonl. Compute rolling 6h mean per
source. If any PAPER source beats LIVE by ≥0.3 percentage points AND has held
that lead across 3 consecutive checks (~45 min), flag a promotion candidate.

The governor never switches live money on its own — it just surfaces the
recommendation. User (or a future auto-switcher) decides.
"""
from __future__ import annotations

import json
import sys
import time
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

LOG_JSONL   = STATEDIR / "strategy_governor.jsonl"
ALERT_JSONL = STATEDIR / "strategy_governor_alerts.jsonl"
STATE_FILE  = STATEDIR / "strategy_governor_state.json"
# Weights this governor recommends combined_mirror uses. combined_mirror reads
# this file every tick and uses it INSTEAD of hardcoded 25/25/25/25 as soon as
# the file exists and its criteria are satisfied. This is the auto-switch.
WEIGHTS_FILE = STATEDIR / "governor_weights.json"

# ─── Auto-switch parameters ────────────────────────────────────────────
# Minimum rolling-window observations required from a source before we'll
# assign it any weight above the fallback baseline.
MIN_OBS_PER_SOURCE = 4          # ≥4 governor cycles = ≥1h of data
# How much a challenger's rolling mean must beat baseline (equal-weight) by
# in absolute percentage points to earn extra weight allocation.
EDGE_THRESHOLD_PCT = 0.10
# Hysteresis: only publish new weights if the L1 change > this fraction. Stops
# the ensemble from ping-ponging composition every 15 min.
WEIGHT_CHANGE_MIN_L1 = 0.10
# Kill switch: if live pnl_pct falls below this, pause the auto-switch and
# alert the user. Human override required to resume.
KILL_SWITCH_PCT = -8.0
# Sticky bonus for the currently-live composition — small preference to
# minimize rebalance transaction costs on marginal-lead switches.
STICKY_BONUS = 0.05

LEAD_THRESHOLD_PCT = 0.30
CONSECUTIVE_CYCLES = 3
ROLLING_WINDOW_S = 6 * 3600


def _read_state(path: str, keys: tuple[str, ...] = ("pnl_pct",)) -> dict | None:
    try:
        d = json.loads(Path(path).read_text())
        return {k: d.get(k) for k in keys} | {"ts": d.get("ts", 0)}
    except Exception:
        return None


def _fresh_pnl(d: dict | None, max_age_s: int = 3600) -> float | None:
    """Return pnl_pct only if the state file was written within max_age_s.
    A stale file must read as 'no data', not as a live number — the frozen
    Jul-17 patent reading (+2.063 vs true -0.31) steered a 92% weight."""
    if not d:
        return None
    if int(time.time()) - int(d.get("ts") or 0) > max_age_s:
        return None
    return d.get("pnl_pct")


def snapshot() -> dict:
    """Read each system's current P/L. Returns dict of {name: {pnl_pct, ts}}."""
    live = _read_state("/Users/quarki3/scanner/state/combined_mirror_state.json") or {}
    qc   = _read_state("/Users/quarki3/scanner/state/qc_mirror_state.json") or {}
    pat  = _read_state("/Users/quarki3/scanner/state/patent_mirror_state.json") or {}
    auto = _read_state("/Users/quarki3/scanner/state/autopilot_mirror_state.json") or {}
    cong = _read_state("/Users/quarki3/scanner/state/congress_mirror_state.json") or {}
    prop_pnl = None
    try:
        pd = json.loads(Path("/Users/quarki3/scanner/state/prop_state.json").read_text())
        prop_pnl = (pd.get("ledger") or {}).get("total_pnl")
    except Exception:
        pass
    return {
        "combined_LIVE":   _fresh_pnl(live),
        "qc_PAPER":        _fresh_pnl(qc),
        "patent_PAPER":    _fresh_pnl(pat, max_age_s=2 * 3600),  # 30-min cadence source
        "prop_desk_PAPER": prop_pnl,
        "autopilot_PAPER": _fresh_pnl(auto),
        "congress_PAPER":  _fresh_pnl(cong),
    }


def load_history() -> list[dict]:
    if not LOG_JSONL.exists():
        return []
    out = []
    for ln in LOG_JSONL.read_text().splitlines()[-500:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def rolling_mean(history: list[dict], name: str, window_s: int = ROLLING_WINDOW_S) -> float | None:
    now = int(time.time())
    cutoff = now - window_s
    vals = [h[name] for h in history
            if h.get("ts", 0) >= cutoff and h.get(name) is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals), 3)


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"consecutive_leads": {}}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"consecutive_leads": {}}


def save_state(state: dict) -> None:
    STATEDIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def append_alert(row: dict) -> None:
    STATEDIR.mkdir(exist_ok=True)
    with ALERT_JSONL.open("a") as f:
        f.write(json.dumps(row) + "\n")


GOVERNOR_CONFIG = STATEDIR / "governor_config.json"


def _load_governor_config() -> dict:
    """D34 unproven-lane cap config (re-fix 2026-08-12; the 2026-08-04
    original was a VM-only edit lost in the back-migration). Hot-reloadable.
    Missing/corrupt file = fail-SAFE default (cap enforced, nothing
    pre-proven), not fail-open."""
    try:
        d = json.loads(GOVERNOR_CONFIG.read_text())
        cap = float(d.get("unproven_cap", 0.35))
        # Range-validate: a fat-fingered "35" (meaning 0.35) would otherwise
        # silently no-op the whole mechanism (review finding) — fail SAFE.
        if not (0.0 < cap <= 1.0):
            cap = 0.35
        lanes = d.get("proven_lanes", [])
        # A bare string would set()-explode per character ("qc" → {'q','c'});
        # only a real list/tuple of lane names counts (review finding).
        proven = set(lanes) if isinstance(lanes, (list, tuple)) else set()
        return {"unproven_cap": cap, "proven_lanes": proven}
    except Exception:
        return {"unproven_cap": 0.35, "proven_lanes": set()}


def _cap_weights(weights: dict[str, float], cap: float,
                 proven: set[str]) -> dict[str, float]:
    """Cap any non-proven lane's weight at `cap`, redistributing the excess
    pro-rata to lanes still under their own cap (iterative water-filling —
    same pattern as combined_mirror.py's single-name cap). Lanes in
    `proven_lanes` (D7 gate passed) are uncapped per D34."""
    w = dict(weights)
    for _ in range(6):
        capped_at = {s: (cap if s not in proven else 1.0) for s in w}
        over = {s: v for s, v in w.items() if v > capped_at[s] + 1e-9}
        if not over:
            break
        excess = sum(v - capped_at[s] for s, v in over.items())
        for s in over:
            w[s] = capped_at[s]
        under = {s: v for s, v in w.items() if v < capped_at[s] - 1e-9}
        pool = sum(under.values())
        if pool <= 0:
            break
        for s in under:
            w[s] += excess * (w[s] / pool)
    return w


def tick() -> None:
    now = int(time.time())
    snap = snapshot()
    row = {"ts": now, **snap}
    STATEDIR.mkdir(exist_ok=True)
    with LOG_JSONL.open("a") as f:
        f.write(json.dumps(row) + "\n")

    live = snap.get("combined_LIVE")
    if live is None:
        log_tick("strategy_governor", "no live pnl to compare")
        return

    history = load_history()
    state = load_state()
    challengers = ("qc_PAPER", "patent_PAPER", "prop_desk_PAPER", "autopilot_PAPER",
                   "congress_PAPER")
    lead_counter = state.setdefault("consecutive_leads", {})
    fresh_alerts = []
    for name in challengers:
        rm = rolling_mean(history, name)
        rm_live = rolling_mean(history, "combined_LIVE")
        if rm is None or rm_live is None:
            lead_counter[name] = 0
            continue
        gap = rm - rm_live
        if gap >= LEAD_THRESHOLD_PCT:
            lead_counter[name] = lead_counter.get(name, 0) + 1
            if lead_counter[name] == CONSECUTIVE_CYCLES:
                alert = {
                    "ts": now, "type": "sustained_lead",
                    "challenger": name,
                    "challenger_rolling_pnl": rm,
                    "live_rolling_pnl": rm_live,
                    "gap_pct": round(gap, 3),
                    "cycles": lead_counter[name],
                    "message": (f"{name} has beaten combined_LIVE by "
                                f"{gap:+.2f}% (rolling) for {CONSECUTIVE_CYCLES} cycles. "
                                f"Consider promoting {name}'s composition to the "
                                f"live mirror."),
                }
                append_alert(alert)
                fresh_alerts.append(alert)
        else:
            lead_counter[name] = 0
    save_state(state)

    # Also compute current leader for surfacing in the UI
    ranked = sorted(
        [(k, v) for k, v in snap.items() if v is not None],
        key=lambda kv: kv[1], reverse=True,
    )
    leader_name, leader_pnl = ranked[0] if ranked else ("?", None)

    STATE_FILE_UI = STATEDIR / "strategy_governor_ui.json"
    try:
        STATE_FILE_UI.write_text(json.dumps({
            "ts": now,
            "snapshot": snap,
            "leader":  {"name": leader_name, "pnl_pct": leader_pnl},
            "consecutive_leads": lead_counter,
            "recent_alerts": [json.loads(l) for l in
                              (ALERT_JSONL.read_text().splitlines()[-5:]
                               if ALERT_JSONL.exists() else [])],
        }, indent=2, default=float))
    except Exception:
        pass

    # ─── AUTO-SWITCH: compute recommended source weights ────────────────────
    # Sources contributing to combined_mirror are the paper mirrors that
    # produce a composition (qc, patent, insider, whale, value). We rank each
    # by rolling mean pnl and allocate weight proportional to relative edge.
    source_names = ["qc", "patent", "insider", "whale", "value", "autopilot", "congress"]
    # Map governor names → source keys
    obs_by_source = {
        "qc":     rolling_mean(history, "qc_PAPER"),
        "patent": rolling_mean(history, "patent_PAPER"),
        # Insider and whale mirrors aren't producing composition yet — will
        # default to equal share once they populate a state file.
        "insider": None,
        "whale":   None,
        # Value is a new source; give it a baseline share once we start seeing
        # its state file update.
        "value":   None,
        "autopilot": rolling_mean(history, "autopilot_PAPER"),
        "congress":  rolling_mean(history, "congress_PAPER"),
    }
    # Count history observations per source (min-N gate).
    # Only rows inside the rolling window count — stale rows from a dead
    # scanner must not satisfy MIN_OBS (that's how one frozen +2.063 reading
    # published a 92% patent weight on 2026-07-20).
    n_by_source = {}
    _cutoff = int(time.time()) - ROLLING_WINDOW_S
    for gov_name, src_key in [("qc_PAPER","qc"), ("patent_PAPER","patent"),
                              ("autopilot_PAPER","autopilot"),
                              ("congress_PAPER","congress")]:
        n_by_source[src_key] = sum(1 for h in history
                                     if h.get(gov_name) is not None
                                     and h.get("ts", 0) >= _cutoff)

    # Also check state files for insider / whale / value composition presence
    for src, path in [
        ("insider", "/Users/quarki3/scanner/state/insider_mirror_state.json"),
        ("whale",   "/Users/quarki3/scanner/state/whale_mirror_state.json"),
        ("value",   "/Users/quarki3/scanner/state/value_screen_state.json"),
    ]:
        if Path(path).exists():
            # Treat existence as "1 valid obs" — pnl_pct handled when it populates
            try:
                d = json.loads(Path(path).read_text())
                n_by_source[src] = 1
                obs_by_source[src] = d.get("pnl_pct")
            except Exception:
                pass

    # Kill switch first — if live has bled through the floor, pause and alert
    if live is not None and live <= KILL_SWITCH_PCT:
        alert = {"ts": now, "type": "kill_switch",
                 "live_pnl_pct": live,
                 "message": (f"Live pnl_pct {live:.2f}% ≤ kill switch "
                              f"{KILL_SWITCH_PCT}%. Auto-switch paused; human review required.")}
        append_alert(alert)
        # Do NOT publish new weights — freeze current composition
    else:
        # Compute weights: each qualifying source gets a share proportional to
        # max(0, rolling_mean - baseline + small_offset). Baseline = mean across
        # qualifying sources so relative ordering matters, not absolute levels.
        qualifying = [s for s in source_names
                       if n_by_source.get(s, 0) >= MIN_OBS_PER_SOURCE
                       and obs_by_source.get(s) is not None]
        if len(qualifying) == 0:
            # Not enough data — leave hardcoded equal-weight in place
            pass
        else:
            baseline = sum(obs_by_source[s] for s in qualifying) / len(qualifying)
            raw = {}
            for s in source_names:
                if s in qualifying:
                    raw[s] = max(0.05, (obs_by_source[s] - baseline) + EDGE_THRESHOLD_PCT + 0.20)
                else:
                    # Non-qualifying: keep small baseline weight
                    raw[s] = 0.05
            # Sticky bonus for previously-published weights (transaction-cost aware)
            prev = {}
            try:
                if WEIGHTS_FILE.exists():
                    prev = json.loads(WEIGHTS_FILE.read_text()).get("weights", {})
            except Exception:
                pass
            for s in raw:
                raw[s] += STICKY_BONUS * prev.get(s, 0.0)
            total = sum(raw.values()) or 1.0
            new_weights = {s: round(raw[s] / total, 4) for s in source_names}
            # D34: no unproven lane may exceed the cap, whatever the edge
            # math says — capped BEFORE hysteresis so the L1 gate compares
            # what would actually be published.
            gov_cfg = _load_governor_config()
            new_weights = {s: round(v, 4) for s, v in
                           _cap_weights(new_weights, gov_cfg["unproven_cap"],
                                        gov_cfg["proven_lanes"]).items()}

            # Hysteresis: only publish if L1 change from previous > threshold
            l1_change = sum(abs(new_weights[s] - prev.get(s, 0.20)) for s in source_names)
            if not prev or l1_change >= WEIGHT_CHANGE_MIN_L1:
                WEIGHTS_FILE.write_text(json.dumps({
                    "ts": now,
                    "weights": new_weights,
                    "reason": ("initial" if not prev
                                else f"L1 change {l1_change:.3f} ≥ {WEIGHT_CHANGE_MIN_L1}"),
                    "rolling_pnl": {s: obs_by_source.get(s) for s in source_names},
                    "n_obs":       {s: n_by_source.get(s, 0) for s in source_names},
                }, indent=2))
                emit_live("strategy_governor",
                          f"[auto-switch] new weights: " +
                          "  ".join(f"{s}:{new_weights[s]*100:.0f}%" for s in source_names))
            else:
                # Weights stable under hysteresis — republish prev with a fresh
                # ts, otherwise combined_mirror's 4h staleness guard discards a
                # still-current recommendation and reverts to the equal blend.
                # D34: prev may predate the cap (or the config may have been
                # tightened) — enforce on every republish, not just publishes.
                prev = {s: round(v, 4) for s, v in
                        _cap_weights(prev, gov_cfg["unproven_cap"],
                                     gov_cfg["proven_lanes"]).items()}
                WEIGHTS_FILE.write_text(json.dumps({
                    "ts": now,
                    "weights": prev,
                    "reason": f"refresh (unchanged, L1 {l1_change:.3f} < {WEIGHT_CHANGE_MIN_L1})",
                    "rolling_pnl": {s: obs_by_source.get(s) for s in source_names},
                    "n_obs":       {s: n_by_source.get(s, 0) for s in source_names},
                }, indent=2))

    if fresh_alerts:
        emit_live("strategy_governor",
                  f"[ALERT] {len(fresh_alerts)} strategy(s) beating live for "
                  f"{CONSECUTIVE_CYCLES} cycles: " +
                  ",".join(a["challenger"] for a in fresh_alerts))
    else:
        emit_live("strategy_governor",
                  f"[gov] leader={leader_name}({leader_pnl:+.3f}%)  live={live:+.3f}%  " +
                  " ".join(f"{k}:{lead_counter.get(k,0)}" for k in challengers))
    log_tick("strategy_governor",
             f"leader={leader_name} live={live:+.3f}% alerts={len(fresh_alerts)}")


if __name__ == "__main__":
    tick()
