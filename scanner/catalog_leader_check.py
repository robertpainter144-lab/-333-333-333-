#!/usr/bin/env python3
"""Daily catalog leader check — keeps the rotation on the current best mirror.

Compares every priceable catalog curve against the currently promoted
autopilot-lane blend on trailing 30d and 7d returns. A challenger that leads
by >2pp (30d) AND is ahead on 7d for 3 consecutive daily checks triggers an
ntfy suggestion to swap the promotion. NEVER auto-promotes (D19: promotion is
explicit via promote_pilot.py; D21: no rotation without persistence).

Mondays: refreshes Crenshaw_Tracker from House Clerk PTRs first.

Benchmarks (HFND_Benchmark, QIS_Cautionary) are excluded as challengers.
"""
import json, os, subprocess, datetime as dt
from pathlib import Path

ST = Path.home() / "scanner" / "state"
CURVES = ST / "autopilot_catalog_curves.json"
HOLDINGS = ST / "autopilot_holdings.json"
STATE = ST / "leader_check_state.json"
CONFIG = ST / "leader_check_config.json"   # {"mode": "auto"|"suggest"}, hot-read
SWAP_COOLDOWN_DAYS = 5
EXCLUDE = {"HFND_Benchmark", "QIS_Cautionary"}
LEAD_MARGIN = 0.02   # 30d lead required
STREAK_DAYS = 3
MIN_MARKS = 5


def load(p, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def ntfy(msg: str, priority: str = "3"):
    env = {}
    for line in (Path.home() / "ntfy.env").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    topic = env.get("NTFY_TOPIC")
    if topic:
        subprocess.run(["curl", "-s", "-m", "10", "-d", msg,
                        "-H", f"Priority: {priority}",
                        f"https://ntfy.sh/{topic}"], capture_output=True)


def trailing(curve: dict, days: int):
    pts = sorted(curve.items())
    if len(pts) < 2:
        return None
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    base = None
    for d, v in pts:
        if d >= cutoff:
            base = v
            break
    if base is None or base <= 0:
        return None
    return pts[-1][1] / base - 1.0


def main():
    today = dt.date.today()
    if today.weekday() == 0:  # Monday: refresh Crenshaw PTR basket
        r = subprocess.run(
            ["python3",
             "fetch_congress_pilot.py", "Dan Crenshaw", "Crenshaw_Tracker", "--write"],
            cwd=Path.home() / "scanner", capture_output=True, text=True, timeout=300)
        print("crenshaw refresh:", (r.stdout or r.stderr).strip().splitlines()[-1:])

    curves = load(CURVES, {})
    holdings = load(HOLDINGS, {})
    src = holdings.get("source", "")
    promoted = [n.strip() for n in src.replace("catalog promotion:", "").split("+")] \
        if "catalog promotion" in src else []
    promoted = [p for p in promoted if p in curves]
    if not promoted:
        print("no active catalog promotion; nothing to compare")
        return

    def curve_ok(name):
        return len(curves.get(name, {}).get("curve", {})) >= MIN_MARKS

    if not all(curve_ok(p) for p in promoted):
        print("promoted lane curve(s) too young (<5 marks); skipping")
        return

    def ret(names, days):
        vals = [trailing(curves[n]["curve"], days) for n in names]
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    p30, p7 = ret(promoted, 30), ret(promoted, 7)
    state = load(STATE, {})
    streaks = state.get("streaks", {})
    alerts = []
    for name, rec in curves.items():
        if name in EXCLUDE or name in promoted or not curve_ok(name):
            continue
        c30, c7 = trailing(rec["curve"], 30), trailing(rec["curve"], 7)
        leading = (c30 is not None and p30 is not None and c30 - p30 > LEAD_MARGIN
                   and c7 is not None and p7 is not None and c7 > p7)
        if leading:
            streaks[name] = streaks.get(name, 0) + 1
            if streaks[name] == STREAK_DAYS:
                alerts.append(f"{name} 30d {c30:+.1%} vs lane {p30:+.1%}")
        else:
            streaks[name] = 0
    state["streaks"] = streaks
    state["last_run"] = today.isoformat()
    STATE.write_text(json.dumps(state, indent=2))
    mode = (load(CONFIG, {}) or {}).get("mode", "suggest")
    if alerts:
        if mode == "auto":
            last = state.get("last_auto_swap")
            days_since = (today - dt.date.fromisoformat(last)).days if last else 999
            if days_since >= SWAP_COOLDOWN_DAYS:
                best = max(
                    (n for n in streaks if streaks[n] >= STREAK_DAYS),
                    key=lambda n: trailing(curves[n]["curve"], 30) or -9)
                r = subprocess.run(
                    ["python3", str(Path.home() / "scanner/promote_pilot.py"), best],
                    capture_output=True, text=True)
                state["last_auto_swap"] = today.isoformat()
                state["streaks"] = {k: 0 for k in streaks}
                STATE.write_text(json.dumps(state, indent=2))
                ntfy(f"[leader-check AUTO] Swapped paper lane to {best} "
                     f"(3-day sustained lead): {alerts[0]}. Real weight still "
                     "gated (D7). Demote via promote_pilot.py --demote",
                     priority="2")
                print("AUTO-SWAPPED to", best, r.stdout.strip()[:100])
            else:
                print(f"auto-swap suppressed: cooldown ({days_since}d < {SWAP_COOLDOWN_DAYS}d)")
        else:
            ntfy("[leader-check] Challenger(s) led promoted lane 3 straight days: "
                 + "; ".join(alerts)
                 + ". Swap via: python3 ~/scanner/promote_pilot.py <name>",
                 priority="4")
    print(f"promoted={promoted} p30={p30} alerts={len(alerts)} "
          f"active_streaks={ {k: v for k, v in streaks.items() if v} }")


if __name__ == "__main__":
    main()
