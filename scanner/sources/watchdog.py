"""Self-heal watchdog. Every 60s: verify prop_state.json is fresh,
auto_tune_config.json is fresh, dashboard is up. Log to state/health.json."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

HEALTH = STATEDIR / "health.json"

CHECKS = [
    ("prop_state",       STATEDIR / "prop_state.json",       120),
    ("auto_tune_config", STATEDIR / "auto_tune_config.json", 600),
]


def _dashboard_ok() -> bool:
    try:
        return urllib.request.urlopen(
            "http://localhost:8080/api/prop_desk/state", timeout=3).status == 200
    except Exception:
        return False


def tick() -> None:
    now = int(time.time())
    status = {"ts": now, "all_ok": True, "checks": []}
    for name, path, max_age_s in CHECKS:
        if not path.exists():
            status["checks"].append({"name": name, "ok": False, "reason": "missing"})
            status["all_ok"] = False
            continue
        age = now - int(path.stat().st_mtime)
        ok = age <= max_age_s
        status["checks"].append({"name": name, "ok": ok, "age_s": age})
        if not ok:
            status["all_ok"] = False
    d_ok = _dashboard_ok()
    status["checks"].append({"name": "dashboard_8080", "ok": d_ok})
    if not d_ok:
        status["all_ok"] = False

    try:
        HEALTH.write_text(json.dumps(status, indent=2))
    except Exception:
        pass

    if not status["all_ok"]:
        bad = [c["name"] for c in status["checks"] if not c["ok"]]
        emit_live("watchdog", f"[unhealthy] {','.join(bad)}")
    log_tick("watchdog", f"all_ok={status['all_ok']}")


if __name__ == "__main__":
    tick()
