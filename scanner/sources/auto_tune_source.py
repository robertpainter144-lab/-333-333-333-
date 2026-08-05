"""Wraps auto_tune.py::tick() as a scanner source. Runs every 5 minutes.

Emits a live heartbeat with the decisions made so the app shows the brain's
thinking in real time. Each decision also goes into state/decisions.jsonl.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import auto_tune
from signals import emit_live, log_tick


def tick() -> None:
    decisions = auto_tune.tick()
    if decisions:
        for d in decisions:
            emit_live("auto_tune",
                      f"[decision] {d.get('type','?')}  {d.get('strategy','')}  — {d.get('reason','')}",
                      **{k: v for k, v in d.items() if k not in ("ts",)})
        log_tick("auto_tune", f"{len(decisions)} decisions applied")
    else:
        cfg = auto_tune.load_config()
        enabled = [k for k, v in cfg.get("strategies", {}).items() if v.get("enabled")]
        emit_live("auto_tune",
                  f"[watch] no changes — active: {','.join(enabled) or 'none'}  "
                  f"cost_gate={cfg.get('min_expected_pct',0):.2f}%")
        log_tick("auto_tune", "no changes")


if __name__ == "__main__":
    tick()
