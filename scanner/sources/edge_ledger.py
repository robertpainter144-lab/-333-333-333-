"""Edge Ledger — one place that answers 'which strategy has proven edge?'

Evidence today is scattered across state/prop_state.json, the qc/patent/combined
mirrors, qc_portfolio's own transactions, and the alpaca_paper_arm experiment.
This source aggregates all of it into state/edge_ledger.json, one row per
strategy, using the SAME promotion-gate math as auto_tune.py so "PROVEN" here
means exactly what it means to the auto-tune brain.

Strictly read-only over every other system's files. This module never writes
anywhere except state/edge_ledger.json.
"""
from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

OUT_FILE = STATEDIR / "edge_ledger.json"

PROP_STATE   = STATEDIR / "prop_state.json"
PROP_LEDGER  = STATEDIR / "prop_ledger.jsonl"

MIRROR_LEDGERS = {
    "qc_mirror":       (STATEDIR / "qc_mirror_state.json",       STATEDIR / "qc_mirror_ledger.jsonl",       "paper"),
    "autopilot_mirror": (STATEDIR / "autopilot_mirror_state.json", STATEDIR / "autopilot_mirror_ledger.jsonl", "paper"),
    "congress_mirror":  (STATEDIR / "congress_mirror_state.json",  STATEDIR / "congress_mirror_ledger.jsonl",  "paper"),
    "patent_mirror":   (STATEDIR / "patent_mirror_state.json",   STATEDIR / "patent_mirror_ledger.jsonl",   "paper"),
    "combined_mirror": (STATEDIR / "combined_mirror_state.json", STATEDIR / "combined_mirror_ledger.jsonl", "live"),
}

QC_PORTFOLIO_TXNS = Path("/Users/quarki3/qc_portfolio/transactions.csv")
ALPACA_ARM_CLOSED = Path("/Users/quarki3/alpaca_paper_arm_state/closed_trades.csv")

# Promotion gate — identical thresholds to auto_tune.py's PROMOTE_* constants,
# so a PROVEN verdict here means the same thing it means to the auto-tune brain.
PROVEN_MIN_N         = 30
PROVEN_MIN_EXPECTANCY = 0.05
PROVEN_MIN_SHARPE    = 1.0
PROMISING_MIN_N      = 10

# Backtest-only strategies never produce a closed-trade ledger — no live or
# paper fills exist for these. The numbers below are UNVERIFIED claims lifted
# from each strategy's own backtest report (see trading memory), not evidence
# from a running system. n_closed_trades is hardcoded to 0 on purpose.
BACKTEST_CLAIMS = {
    "SOXL_regime": {
        "sharpe": 1.211, "total_pnl_pct": None, "win_rate": None, "expectancy_pct": None,
        "note": "5yr backtest claim: Sharpe 1.211, CAGR 13.3%, MaxDD -7.8%, 160 trades. Never traded live or paper.",
    },
    "ES_mean_rev": {
        "sharpe": None, "total_pnl_pct": None, "win_rate": None, "expectancy_pct": None,
        "note": "ES Mini mean-reversion/regime-adaptive backtest claim. Never traded live or paper.",
    },
    "spy_overlay": {
        "sharpe": None, "total_pnl_pct": None, "win_rate": None, "expectancy_pct": None,
        "note": "SMA200 tactical overlay (SCHX/SHY) backtest claim. A live launchd deployment exists, "
                "but no closed-trade ledger is aggregated here yet, so this entry reflects backtest-only evidence.",
    },
}


def _verdict(n: int | None, expectancy_pct: float | None, sharpe: float | None) -> str:
    if n is None or n < 10:
        return "UNPROVEN"
    if (n >= PROVEN_MIN_N and expectancy_pct is not None and expectancy_pct >= PROVEN_MIN_EXPECTANCY
            and sharpe is not None and sharpe >= PROVEN_MIN_SHARPE):
        return "PROVEN"
    if expectancy_pct is not None and expectancy_pct < 0:
        return "LOSING"
    if expectancy_pct is not None and expectancy_pct > 0:
        return "PROMISING"
    return "UNPROVEN"


def _row(name: str, mode: str, n: int, win_rate, expectancy_pct, sharpe,
         total_pnl_pct, last_trade_ts) -> dict:
    return {
        "name": name,
        "mode": mode,
        "n_closed_trades": n,
        "win_rate": win_rate,
        "expectancy_pct": expectancy_pct,
        "sharpe": sharpe,
        "total_pnl_pct": total_pnl_pct,
        "last_trade_ts": last_trade_ts,
        "verdict": _verdict(n, expectancy_pct, sharpe),
    }


def _parse_ts(s) -> int | None:
    """Best-effort coercion of a timestamp column to a unix epoch int."""
    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s)
    try:
        return int(float(s))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


# ─── prop_desk (state/prop_state.json + prop_ledger.jsonl) ──────────────────
def _prop_desk_rows() -> list[dict]:
    if not PROP_STATE.exists():
        return []
    try:
        state = json.loads(PROP_STATE.read_text())
    except Exception:
        return []
    mode = "paper" if state.get("alpaca_mode", "paper") != "live" else "live"
    strat_health = state.get("strat_health", {}) or {}

    last_ts_by_strat: dict[str, int] = {}
    if PROP_LEDGER.exists():
        try:
            for line in PROP_LEDGER.read_text().splitlines():
                d = json.loads(line)
                strat = d.get("strategy")
                ts = d.get("exit_ts")
                if strat and ts:
                    last_ts_by_strat[strat] = max(last_ts_by_strat.get(strat, 0), int(ts))
        except Exception:
            pass

    rows = []
    for strat, h in strat_health.items():
        n = int(h.get("n", 0))
        wins, losses = h.get("wins", 0), h.get("losses", 0)
        win_rate = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else None
        expectancy = h.get("mean") if n > 0 else None
        sharpe = h.get("sharpe") if n > 0 else None
        rows.append(_row(
            name=f"prop_desk:{strat}", mode=mode, n=n, win_rate=win_rate,
            expectancy_pct=expectancy, sharpe=sharpe,
            total_pnl_pct=h.get("total") if n > 0 else None,
            last_trade_ts=last_ts_by_strat.get(strat),
        ))
    return rows


# ─── qc_portfolio (transactions.csv — real qty/price, true FIFO round trips) ─
def _qc_portfolio_closed_trades() -> list[dict]:
    if not QC_PORTFOLIO_TXNS.exists():
        return []
    try:
        rows = list(csv.DictReader(QC_PORTFOLIO_TXNS.open()))
    except Exception:
        return []
    lots: dict[str, deque] = defaultdict(deque)  # symbol -> deque of [qty, entry_price]
    closed = []
    for r in rows:
        try:
            sym = r["symbol"]
            side = (r.get("side") or "").lower()
            qty = float(r["qty"])
            price = float(r["price"])
            ts = _parse_ts(r.get("timestamp"))
        except (KeyError, ValueError, TypeError):
            continue
        if qty <= 0 or price <= 0:
            continue
        if side == "buy":
            lots[sym].append([qty, price])
        elif side == "sell":
            remaining = qty
            while remaining > 1e-9 and lots[sym]:
                lot_qty, entry_price = lots[sym][0]
                take = min(lot_qty, remaining)
                pnl_pct = (price - entry_price) / entry_price * 100 if entry_price else 0.0
                closed.append({"ts": ts, "pnl_pct": pnl_pct})
                lot_qty -= take
                remaining -= take
                if lot_qty <= 1e-9:
                    lots[sym].popleft()
                else:
                    lots[sym][0][0] = lot_qty
    return closed


def _qc_portfolio_row() -> dict | None:
    closed = _qc_portfolio_closed_trades()
    if not closed:
        return None
    pnls = [c["pnl_pct"] for c in closed]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    win_rate = round(wins / n * 100, 1)
    expectancy = round(sum(pnls) / n, 4)
    sharpe = round(statistics.mean(pnls) / statistics.pstdev(pnls), 3) if n > 1 and statistics.pstdev(pnls) > 0 else None
    last_ts = max((c["ts"] for c in closed if c["ts"] is not None), default=None)
    return _row(name="qc_portfolio", mode="paper", n=n, win_rate=win_rate,
                expectancy_pct=expectancy, sharpe=sharpe,
                total_pnl_pct=round(sum(pnls), 3), last_trade_ts=last_ts)


# ─── qc_mirror / patent_mirror / combined_mirror ────────────────────────────
# These are continuously-rebalanced dollar mirrors, not discrete round-trip
# strategies — the ledgers record BUY/SELL dollar deltas, not matched
# entry/exit prices, so a true per-trade pnl_pct can't be reconstructed.
# n_closed_trades here is a proxy: the count of successfully-executed
# SELL/EXIT fills (i.e. how many times this mirror has trimmed a position).
# total_pnl_pct is the mirror's current book-level pnl_pct (unrealized +
# realized blended) straight from its own state file, not a per-trade
# expectancy — expectancy/sharpe are left null since they aren't computable
# from what these ledgers actually record.
def _mirror_rows() -> list[dict]:
    rows = []
    for name, (state_path, ledger_path, mode) in MIRROR_LEDGERS.items():
        state = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except Exception:
                state = {}
        n_closed = 0
        last_ts = state.get("ts")
        if ledger_path.exists():
            try:
                for line in ledger_path.read_text().splitlines():
                    d = json.loads(line)
                    ts = d.get("ts")
                    if ts:
                        last_ts = max(last_ts or 0, int(ts))
                    side = d.get("side")
                    result = str(d.get("result", ""))
                    if side in ("SELL", "EXIT") and result.startswith("ok:"):
                        n_closed += 1
            except Exception:
                pass
        rows.append(_row(
            name=name, mode=state.get("mode", mode), n=n_closed,
            win_rate=None, expectancy_pct=None, sharpe=None,
            total_pnl_pct=state.get("pnl_pct"), last_trade_ts=last_ts,
        ))
    return rows


# ─── alpaca_paper_arm (closed_trades.csv already has matched pnl_pct) ───────
def _alpaca_paper_arm_rows() -> list[dict]:
    if not ALPACA_ARM_CLOSED.exists():
        return []
    try:
        trades = list(csv.DictReader(ALPACA_ARM_CLOSED.open()))
    except Exception:
        return []
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        arm = t.get("arm") or "unknown"
        by_arm[arm].append(t)

    rows = []
    for arm, arm_trades in by_arm.items():
        pnls = []
        last_ts = None
        for t in arm_trades:
            try:
                pnls.append(float(t["pnl_pct"]))
            except (KeyError, ValueError):
                continue
            ts = _parse_ts(t.get("ts_close"))
            if ts is not None:
                last_ts = max(last_ts or 0, ts)
        n = len(pnls)
        if n == 0:
            continue
        wins = sum(1 for p in pnls if p > 0)
        win_rate = round(wins / n * 100, 1)
        expectancy = round(sum(pnls) / n, 4)
        sharpe = round(statistics.mean(pnls) / statistics.pstdev(pnls), 3) if n > 1 and statistics.pstdev(pnls) > 0 else None
        rows.append(_row(name=f"alpaca_paper_arm:{arm}", mode="paper", n=n,
                          win_rate=win_rate, expectancy_pct=expectancy, sharpe=sharpe,
                          total_pnl_pct=round(sum(pnls), 3), last_trade_ts=last_ts))
    return rows


# ─── backtest-only claims (never traded, hardcoded from each backtest report)
def _backtest_only_rows() -> list[dict]:
    rows = []
    for name, claim in BACKTEST_CLAIMS.items():
        row = _row(name=name, mode="backtest-only", n=0, win_rate=claim["win_rate"],
                    expectancy_pct=claim["expectancy_pct"], sharpe=claim["sharpe"],
                    total_pnl_pct=claim["total_pnl_pct"], last_trade_ts=None)
        row["note"] = claim["note"]
        rows.append(row)
    return rows


def build() -> dict:
    strategies = []
    strategies.extend(_prop_desk_rows())
    qc_row = _qc_portfolio_row()
    if qc_row:
        strategies.append(qc_row)
    strategies.extend(_mirror_rows())
    strategies.extend(_alpaca_paper_arm_rows())
    strategies.extend(_backtest_only_rows())
    return {"ts": int(time.time()), "strategies": strategies}


def tick() -> None:
    payload = build()
    STATEDIR.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(payload, indent=2, default=float))
    proven = [s["name"] for s in payload["strategies"] if s["verdict"] == "PROVEN"]
    emit_live("edge_ledger",
              f"[ledger] {len(payload['strategies'])} strategies tracked" +
              (f"  PROVEN: {','.join(proven)}" if proven else "  none proven yet"))
    log_tick("edge_ledger", f"{len(payload['strategies'])} strategies, {len(proven)} proven")


if __name__ == "__main__":
    tick()
