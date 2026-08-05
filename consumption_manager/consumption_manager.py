#!/usr/bin/env python3
"""Meta-level Claude usage consumption manager (D25).

Parses Claude Code transcript JSONLs into rolling 5-hour and 7-day
weighted-unit windows, split into two lanes:
  interactive — top-level session transcripts (Robert typing)
  automated   — everything nested (subagent/workflow agents) + VM transcripts

Publishes two tiers to state.json:
  tier      — display tier vs the full budgets (human dashboard)
  auto_tier — what gates enforce: TOTAL usage vs budgets minus the
              interactive reserve, so automation always stops while
              headroom for typing remains. Any 429 in the last 30 min
              forces auto_tier=RED (the provider's own signal wins).

Subcommands:
  scan       (default) recompute, alert on transitions/forecast, sync to VM
  gate       recompute quietly; exit 0=GREEN 1=AMBER 2=RED on auto_tier
  calibrate  historical peaks + per-429 window evidence for budget tuning
"""
import bisect
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
STATE_PATH = BASE / "state.json"
LOCK_PATH = BASE / "scan.lock"
PROJECTS = Path.home() / ".claude" / "projects"
VM_STAGE = BASE / "vm-projects"

DEFAULT_CONFIG = {
    "five_hour_budget_units": 135_000_000,
    "weekly_budget_units": 537_000_000,
    # Official weekly limit is a FIXED window, not rolling (verified vs /usage
    # 2026-08-02: official 1% right after reset vs rolling-7d 93.6%). Anchor =
    # one known reset instant (5:59pm America/Denver, Sundays); window steps in
    # UTC so it drifts 1h across DST changes — recheck vs /usage each Nov/Mar.
    "weekly_reset_anchor": "2026-08-02T23:59:00+00:00",
    "amber_pct": 0.70,
    "red_pct": 0.90,
    "interactive_reserve_5h_pct": 0.25,
    "interactive_reserve_weekly_pct": 0.15,
    "model_weights": {"opus": 5.0, "fable": 5.0, "mythos": 5.0,
                      "sonnet": 1.0, "haiku": 0.2, "default": 1.0},
    "vm_sync": {
        "enabled": True,
        "host": "ubuntu@129.153.96.201",
        "key": str(Path.home() / ".ssh" / "oracle_trading"),
        "dest": "trading-data/consumption/state.json",
        "ingest_transcripts": True,
    },
    "forecast_alert_hours": 24,
    "forecast_alert_cooldown_s": 21600,
}

FIVE_H = 5 * 3600
WEEK = 7 * 86400


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text()))
        except Exception as e:
            print(f"warn: bad config.json ({e}); using defaults", file=sys.stderr)
    else:
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    return cfg


def model_weight(cfg, model):
    m = (model or "").lower()
    for key, w in cfg["model_weights"].items():
        if key != "default" and key in m:
            return w, True
    return cfg["model_weights"].get("default", 1.0), False


def usage_units(cfg, usage, model):
    cc = usage.get("cache_creation") or {}
    w5 = cc.get("ephemeral_5m_input_tokens", 0)
    w1h = cc.get("ephemeral_1h_input_tokens", 0)
    if not (w5 or w1h):
        w5 = usage.get("cache_creation_input_tokens", 0)
    units = (usage.get("input_tokens", 0) * 1.0
             + w5 * 1.25 + w1h * 2.0
             + usage.get("cache_read_input_tokens", 0) * 0.1
             + usage.get("output_tokens", 0) * 5.0)
    w, known = model_weight(cfg, model)
    return units * w, known


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def transcript_roots():
    roots = [(PROJECTS, None)]
    if VM_STAGE.is_dir():
        roots.append((VM_STAGE, "vm"))
    return roots


def classify(root, tag, f):
    """(session_id, lane) from path. Nested files roll up to the parent
    session; anything below depth 2 (or from the VM) is the automated lane."""
    parts = f.relative_to(root).parts
    if tag == "vm":
        sid = "vm:" + (parts[1].rsplit(".jsonl", 1)[0] if len(parts) > 1 else parts[0])
        return sid, "automated"
    sid = parts[1].rsplit(".jsonl", 1)[0] if len(parts) > 1 else f.stem
    return sid, ("interactive" if len(parts) == 2 else "automated")


def iter_events(cfg, since_epoch):
    """Returns (events, hits429, unknown_models).
    events: list of (ts, units, session_id, lane), deduped by message id."""
    seen = {}
    hits429 = []
    unknown_models = set()
    cutoff_mtime = since_epoch - 3600
    for root, tag in transcript_roots():
        for f in root.rglob("*.jsonl"):
            try:
                if f.stat().st_mtime < cutoff_mtime:
                    continue
            except OSError:
                continue
            sid, lane = classify(root, tag, f)
            try:
                with open(f, errors="replace") as fh:
                    for line in fh:
                        if '"apiErrorStatus"' in line:
                            try:
                                d = json.loads(line)
                                if d.get("apiErrorStatus") == 429:
                                    ts = parse_ts(d.get("timestamp"))
                                    if ts:
                                        hits429.append(ts)
                            except Exception:
                                pass
                            continue  # synthetic error rows carry no real usage
                        if '"usage"' not in line or '"assistant"' not in line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if d.get("type") != "assistant":
                            continue
                        msg = d.get("message") or {}
                        usage = msg.get("usage")
                        if not usage:
                            continue
                        ts = parse_ts(d.get("timestamp"))
                        if ts is None or ts < since_epoch:
                            continue
                        units, known = usage_units(cfg, usage, msg.get("model"))
                        if not known and units > 0:
                            unknown_models.add(msg.get("model") or "?")
                        key = msg.get("id") or d.get("requestId") or f"{sid}:{d.get('uuid')}"
                        seen[key] = (ts, units, sid, lane)
            except OSError:
                continue
    return list(seen.values()), hits429, unknown_models


def week_window(cfg, now):
    """(start, end) of the current OFFICIAL weekly window (fixed anchor)."""
    anchor = parse_ts(cfg.get("weekly_reset_anchor")) or (now - WEEK)
    start = anchor + ((now - anchor) // WEEK) * WEEK
    return start, start + WEEK


def forecast(events, cfg, now, week_used, week_end):
    """Burn projection inside the fixed weekly window. Crossings that land
    after the weekly reset are null — the window zeroes before they hit."""
    day = [e for e in events if e[0] >= now - 86400]
    r_total = sum(e[1] for e in day) / 24.0
    r_int = sum(e[1] for e in day if e[3] == "interactive") / 24.0
    capw = cfg["weekly_budget_units"] * (1 - cfg["interactive_reserve_weekly_pct"])
    hours_left = (week_end - now) / 3600.0

    def hours_to(rate, ceiling):
        if rate <= 0 or week_used >= ceiling:
            return 0 if week_used >= ceiling else None
        h = (ceiling - week_used) / rate
        return round(h, 1) if h <= hours_left else None

    return {
        "net_burn_per_hour_24h": round(r_total),
        "interactive_burn_per_hour": round(r_int),
        "hours_to_week_reset": round(hours_left, 1),
        "hours_to_auto_stop": hours_to(r_total, capw),
        "hours_to_interactive_lockout": hours_to(r_int, cfg["weekly_budget_units"]),
    }


def compute_state(cfg, prev):
    now = time.time()
    events, hits429, unknown = iter_events(cfg, now - WEEK)
    recent_429 = sum(1 for t in hits429 if t >= now - 86400)
    override_429 = any(t >= now - 1800 for t in hits429)

    wk_start, wk_end = week_window(cfg, now)

    def lane_sum(since, lane=None):
        return sum(u for t, u, _, ln in events
                   if t >= since and (lane is None or ln == lane))

    t5, t7 = lane_sum(now - FIVE_H), lane_sum(wk_start)
    lanes = {"interactive_5h": round(lane_sum(now - FIVE_H, "interactive")),
             "automated_5h": round(lane_sum(now - FIVE_H, "automated")),
             "interactive_wk": round(lane_sum(wk_start, "interactive")),
             "automated_wk": round(lane_sum(wk_start, "automated"))}
    burn_30m = sum(u for t, u, _, _ in events if t >= now - 1800)

    b5, bw = cfg["five_hour_budget_units"], cfg["weekly_budget_units"]
    cap5 = b5 * (1 - cfg["interactive_reserve_5h_pct"])
    capw = bw * (1 - cfg["interactive_reserve_weekly_pct"])

    worst = max(t5 / b5, t7 / bw)
    tier = "GREEN" if worst < cfg["amber_pct"] else ("AMBER" if worst < cfg["red_pct"] else "RED")

    aworst = max(t5 / cap5, t7 / capw)
    auto_tier = "GREEN" if aworst < 0.70 else ("AMBER" if aworst < 1.00 else "RED")
    if override_429:
        auto_tier = "RED"

    per = {}
    for t, u, sid, _ in events:
        if t >= now - 1800:
            per[sid] = per.get(sid, 0) + u
    heavy = sorted((s for s, u in per.items() if u >= 250_000), key=lambda s: -per[s])
    mch = {"GREEN": 5, "AMBER": 2, "RED": 0}[auto_tier]
    if any(t >= now - FIVE_H for t in hits429):
        mch = min(mch, 2)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_epoch": int(now),
        "tier": tier,
        "auto_tier": auto_tier,
        "auto_tier_429_override": override_429,
        "five_hour": {"units": round(t5), "budget": b5, "pct": round(t5 / b5 * 100, 1)},
        "weekly": {"units": round(t7), "budget": bw, "pct": round(t7 / bw * 100, 1),
                   "window_start": datetime.fromtimestamp(wk_start, timezone.utc)
                                   .isoformat(timespec="seconds"),
                   "resets_at": datetime.fromtimestamp(wk_end, timezone.utc)
                                .isoformat(timespec="seconds")},
        "auto_caps": {"five_hour": round(cap5), "weekly": round(capw)},
        "lanes": lanes,
        "burn_rate_per_hour": round(burn_30m * 2),
        "headroom_hours_5h": (round(max(0, b5 - t5) / (burn_30m * 2), 1)
                              if burn_30m else None),
        "burst_429s_24h": recent_429,
        "forecast": forecast(events, cfg, now, t7, wk_end),
        "heavy_sessions_30m": heavy[:8],
        "max_concurrent_heavy": mch,
        "unknown_models": sorted(unknown),
        "last_forecast_alert_epoch": (prev or {}).get("last_forecast_alert_epoch", 0),
    }


def ntfy(level, msg):
    notifier = Path.home() / "bin" / "quarki_notify.py"
    if not notifier.exists():
        return
    try:
        subprocess.run([sys.executable, str(notifier), level,
                        "Claude consumption manager", msg],
                       capture_output=True, timeout=20)
    except Exception:
        pass


def atomic_write(path, data):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".state-")
    with os.fdopen(fd, "w") as fh:
        fh.write(data)
    os.replace(tmp, str(path))


def vm_sync(cfg):
    vs = cfg.get("vm_sync", {})
    if not vs.get("enabled"):
        return
    try:
        subprocess.run(["scp", "-q", "-o", "ConnectTimeout=6",
                        "-o", "StrictHostKeyChecking=accept-new",
                        "-i", vs["key"], str(STATE_PATH), f"{vs['host']}:{vs['dest']}"],
                       capture_output=True, timeout=30)
    except Exception:
        pass


def vm_ingest(cfg):
    vs = cfg.get("vm_sync", {})
    if not (vs.get("enabled") and vs.get("ingest_transcripts")):
        return
    VM_STAGE.mkdir(exist_ok=True)
    try:
        subprocess.run(["rsync", "-az", "--timeout=20",
                        "-e", f"ssh -i {vs['key']} -o ConnectTimeout=6",
                        f"{vs['host']}:.claude/projects/", str(VM_STAGE) + "/"],
                       capture_output=True, timeout=60)
    except Exception:
        pass


def cmd_scan(cfg, quiet=False):
    with open(LOCK_PATH, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        prev = None
        if STATE_PATH.exists():
            try:
                prev = json.loads(STATE_PATH.read_text())
            except Exception:
                pass
        vm_ingest(cfg)
        state = compute_state(cfg, prev)

        prev_auto = (prev or {}).get("auto_tier")
        if prev_auto and prev_auto != state["auto_tier"]:
            f, w = state["five_hour"], state["weekly"]
            ntfy("action" if state["auto_tier"] == "RED" else "routine",
                 f"Auto lane {prev_auto} -> {state['auto_tier']}: 5h {f['pct']}% "
                 f"wk {w['pct']}%"
                 + (" (429 in last 30min)" if state["auto_tier_429_override"] else "")
                 + ". " + {"RED": "Automated Claude jobs halted; interactive reserve engaged.",
                           "AMBER": "Optional swarms deferred; fan-out capped.",
                           "GREEN": "All lanes clear."}[state["auto_tier"]])

        fc = state["forecast"]
        h = fc.get("hours_to_interactive_lockout")
        cool = cfg["forecast_alert_cooldown_s"]
        if (h is not None and h <= cfg["forecast_alert_hours"]
                and time.time() - state["last_forecast_alert_epoch"] > cool):
            ntfy("action", f"Projected interactive lockout in ~{h}h even if automation "
                           f"halts now (weekly at {state['weekly']['pct']}%; window "
                           f"resets in {fc['hours_to_week_reset']}h).")
            state["last_forecast_alert_epoch"] = int(time.time())
        if state["unknown_models"] and state["unknown_models"] != (prev or {}).get("unknown_models"):
            ntfy("routine", f"Meter saw unweighted model(s) {state['unknown_models']} — "
                            f"add to model_weights or usage under-counts.")

        atomic_write(STATE_PATH, json.dumps(state, indent=2) + "\n")
        vm_sync(cfg)
    if not quiet:
        print(json.dumps(state, indent=2))
    return state


def cmd_gate(cfg):
    state = cmd_scan(cfg, quiet=True)
    print(state["auto_tier"])
    return {"GREEN": 0, "AMBER": 1, "RED": 2}[state["auto_tier"]]


def cmd_calibrate(cfg):
    now = time.time()
    events, hits429, _ = iter_events(cfg, now - 30 * 86400)
    events.sort()
    ts_list = [e[0] for e in events]
    pref = [0.0]
    for e in events:
        pref.append(pref[-1] + e[1])

    def window_units(end, span):
        lo = bisect.bisect_left(ts_list, end - span)
        hi = bisect.bisect_right(ts_list, end)
        return pref[hi] - pref[lo]

    peak5 = max((window_units(t, FIVE_H) for t in ts_list), default=0)
    peakw = max((window_units(t, WEEK) for t in ts_list), default=0)
    clean5 = max((window_units(t, FIVE_H) for t in ts_list
                  if not any(abs(t - h) < FIVE_H for h in hits429)), default=0)
    print(f"events: {len(events)} over 30d (all transcript roots)")
    print(f"peak 5h window: {peak5:,.0f} | largest 429-free 5h window: {clean5:,.0f}")
    print(f"peak 7d window: {peakw:,.0f}")
    for t in sorted(set(round(h, -2) for h in hits429))[-20:]:
        when = datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="minutes")
        print(f"  429 {when}  5h={window_units(t, FIVE_H):,.0f}  7d={window_units(t, WEEK):,.0f}")


def main():
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        cmd_scan(cfg)
    elif cmd == "gate":
        sys.exit(cmd_gate(cfg))
    elif cmd == "calibrate":
        cmd_calibrate(cfg)
    else:
        print(__doc__)
        sys.exit(64)


if __name__ == "__main__":
    main()
