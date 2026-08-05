"""Read prop_ledger.jsonl and report cumulative P&L.

Different from retune.py — no config rotation, no automatic changes.
Just: is the prop desk profitable yet? Break down by strategy.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT   = Path(__file__).resolve().parent
LEDGER = ROOT / "state" / "prop_ledger.jsonl"


def load() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for ln in LEDGER.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def summarize(trades: list[dict]) -> dict:
    by_strat: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_strat[t.get("strategy", "?")].append(t.get("pnl_pct", 0))
    n = len(trades)
    total = sum(t.get("pnl_pct", 0) for t in trades)
    wins  = sum(1 for t in trades if t.get("pnl_pct", 0) > 0)
    losses = sum(1 for t in trades if t.get("pnl_pct", 0) < 0)
    return {
        "n_trades": n, "wins": wins, "losses": losses,
        "win_rate": (wins / n) if n else 0.0,
        "total_pnl": round(total, 3),
        "mean_pnl":  round(total / n, 3) if n else 0.0,
        "by_strategy": {k: {"n": len(v), "total": round(sum(v), 3),
                            "mean": round(sum(v)/len(v), 3) if v else 0}
                         for k, v in by_strat.items()},
    }


def verdict(s: dict) -> str:
    if s["n_trades"] >= 5 and s["total_pnl"] > 0 and s["mean_pnl"] > 0:
        return "PROFITABLE"
    if s["n_trades"] >= 5 and s["total_pnl"] < -0.5:
        return "UNPROFITABLE"
    return "BUILDING"


if __name__ == "__main__":
    trades = load()
    s = summarize(trades)
    v = verdict(s)
    print(json.dumps({**s, "verdict": v}, indent=2))
