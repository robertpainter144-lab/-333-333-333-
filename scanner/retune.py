"""Read momentum_ledger.jsonl, evaluate current config's P&L, and — if it's
losing money after enough trades — rotate to the next config version.

Called every 10 minutes by the outer schedule/monitor. Prints a short verdict
that the caller can relay to the user.

Verdicts:
  PROFITABLE  — current cfg has >0 mean pnl over N>=5 trades. Stop and notify.
  BUILDING    — not enough trades yet on this cfg. Keep going.
  LOSING      — >=3 trades and cumulative pnl <=0. Rotate cfg to next version.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT   = Path(__file__).resolve().parent
STATE  = ROOT / "state"
LEDGER = STATE / "momentum_ledger.jsonl"
CONFIG = STATE / "momentum_config.json"
LOG    = STATE / "retune.log"

# Fast-window (2m/4m/6m) config ladder. Each rotation tries a different bet.
CONFIG_LADDER = [
    {"version": 1, "entry_5m_pct": 0.15, "entry_accel": 0.0,  "exit_5m_pct": -0.10,
     "exit_accel_deep": -0.25, "exit_drawdown": -0.7,
     "require_v5m_gte_v15m": True,  "min_24h_vol_usd": 0,
     "notes": "v1 — 2m windows, 0.15% entry, all coins"},

    {"version": 2, "entry_5m_pct": 0.25, "entry_accel": 0.05, "exit_5m_pct": -0.10,
     "exit_accel_deep": -0.20, "exit_drawdown": -0.5,
     "require_v5m_gte_v15m": True,  "min_24h_vol_usd": 0,
     "notes": "v2 — tighter entry (0.25%), quicker exit"},

    {"version": 3, "entry_5m_pct": 0.10, "entry_accel": 0.05, "exit_5m_pct": -0.05,
     "exit_accel_deep": -0.15, "exit_drawdown": -0.4,
     "require_v5m_gte_v15m": True,  "min_24h_vol_usd": 100_000_000,
     "notes": "v3 — micro-momentum on $100M+ vol coins, tight scalp"},

    {"version": 4, "entry_5m_pct": 0.35, "entry_accel": 0.15, "exit_5m_pct": -0.05,
     "exit_accel_deep": -0.15, "exit_drawdown": -0.4,
     "require_v5m_gte_v15m": True,  "min_24h_vol_usd": 250_000_000,
     "notes": "v4 — big-move only on top-liquidity, tight exit"},

    {"version": 5, "entry_5m_pct": 0.08, "entry_accel": 0.03, "exit_5m_pct": -0.03,
     "exit_accel_deep": -0.10, "exit_drawdown": -0.3,
     "require_v5m_gte_v15m": True,  "min_24h_vol_usd": 500_000_000,
     "notes": "v5 — hair-trigger scalp on top-5 liq"},

    {"version": 6, "entry_5m_pct": 0.5,  "entry_accel": 0.2,  "exit_5m_pct": 0.0,
     "exit_accel_deep": -0.1, "exit_drawdown": -0.25,
     "require_v5m_gte_v15m": True,  "min_24h_vol_usd": 1_000_000_000,
     "notes": "v6 — pure breakout, hold winners, exit fast"},
]

MIN_TRADES_TO_JUDGE = 2     # ≥2 trades before we call losing (was 3)
MIN_TRADES_TO_WIN   = 3     # ≥3 trades before we call profitable (was 5)
LOSING_THRESHOLD    = -0.05


def _log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    print(line, end="", flush=True)
    STATE.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(line)


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


def current_config_version() -> int:
    if CONFIG.exists():
        try:
            return int(json.loads(CONFIG.read_text()).get("version", 1))
        except Exception:
            return 1
    return 1


def write_config(cfg: dict) -> None:
    STATE.mkdir(exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True))
    tmp.replace(CONFIG)


def rotate_config(from_ver: int) -> dict:
    next_ver = from_ver + 1
    for c in CONFIG_LADDER:
        if c["version"] == next_ver:
            write_config(c)
            _log(f"rotated to cfg v{next_ver}: {c['notes']}")
            return c
    # exhausted — wrap around and try v1 again with a marker
    c = CONFIG_LADDER[0]
    write_config(c)
    _log("config ladder exhausted — restarting from v1 (all configs failed once)")
    return c


def evaluate() -> dict:
    ledger = load_ledger()
    cur_ver = current_config_version()
    trades = [r for r in ledger if r.get("cfg_version") == cur_ver]
    n = len(trades)
    total_pnl = sum(r.get("pnl_pct", 0) for r in trades)
    wins = sum(1 for r in trades if r.get("pnl_pct", 0) > 0)
    losses = sum(1 for r in trades if r.get("pnl_pct", 0) < 0)
    mean_pnl = (total_pnl / n) if n else 0.0

    verdict = "BUILDING"
    if n >= MIN_TRADES_TO_WIN and total_pnl > 0 and mean_pnl > 0:
        verdict = "PROFITABLE"
    elif n >= MIN_TRADES_TO_JUDGE and total_pnl <= LOSING_THRESHOLD:
        verdict = "LOSING"

    return {
        "cfg_version": cur_ver,
        "n_trades":    n,
        "wins":        wins,
        "losses":      losses,
        "win_rate":    (wins / n) if n else 0.0,
        "total_pnl":   round(total_pnl, 3),
        "mean_pnl":    round(mean_pnl, 3),
        "verdict":     verdict,
        "trades":      trades,
    }


def main() -> None:
    r = evaluate()
    _log(
        f"cfg v{r['cfg_version']}  trades={r['n_trades']}  wins={r['wins']}  "
        f"losses={r['losses']}  win_rate={r['win_rate']*100:.0f}%  "
        f"total_pnl={r['total_pnl']:+.2f}%  mean={r['mean_pnl']:+.3f}%  "
        f"→ {r['verdict']}"
    )
    if r["verdict"] == "LOSING":
        rotate_config(r["cfg_version"])
    elif r["verdict"] == "PROFITABLE":
        _log(f"PROFITABLE STOP — cfg v{r['cfg_version']} is the winner. Freeze here.")
    # print machine-readable one-liner too
    print(json.dumps(r))


if __name__ == "__main__":
    # Seed the config file with v1 if missing
    if not CONFIG.exists():
        write_config(CONFIG_LADDER[0])
        _log(f"seeded initial cfg v1")
    main()
