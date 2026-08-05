"""
Sync IBKR fill history into transactions.csv.

IBKR is the authoritative source for actual fills. Local files miss orders placed
via one-shot scripts, cancellations, etc. This pulls ib.fills() each invocation
and upserts rows into transactions.csv keyed by execId, so re-runs are idempotent.

Each row gets an `explanation` populated from decisions.json when there's a
symbol-match (entry-side fills only — exits get a generated explanation later
when the watcher fires).

Usage:
    python3 fill_sync.py            # one-shot sync
    python3 fill_sync.py --daemon   # loop every 60s
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ib_insync import IB

ROOT = Path(__file__).resolve().parent
TXNS = ROOT / "transactions.csv"
DECISIONS = ROOT / "decisions.json"

COLS = ["ts", "action", "symbol", "cashQty", "totalQuantity", "limitPrice",
        "status", "orderId", "permId", "filled", "avgFillPrice", "reason",
        "explanation", "execId"]


def _load_decisions() -> dict:
    if not DECISIONS.exists():
        return {"positions": {}, "transaction_history": []}
    try:
        return json.loads(DECISIONS.read_text())
    except Exception:
        return {"positions": {}, "transaction_history": []}


def _existing_exec_ids() -> set:
    if not TXNS.exists():
        return set()
    ids = set()
    with TXNS.open() as f:
        for row in csv.DictReader(f):
            eid = row.get("execId") or ""
            if eid:
                ids.add(eid)
    return ids


def _read_all_rows() -> list[dict]:
    if not TXNS.exists():
        return []
    with TXNS.open() as f:
        return list(csv.DictReader(f))


def _write_all_rows(rows: list[dict]) -> None:
    with TXNS.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _explain_entry(symbol: str, decisions: dict) -> str:
    """Use the position's stored explanation if we have one."""
    p = decisions.get("positions", {}).get(symbol)
    if p:
        bits = []
        if p.get("decision_label"): bits.append(f"[{p['decision_label']}]")
        if p.get("explanation"):    bits.append(p["explanation"])
        if p.get("protective_stops"): bits.append(f"Stops: {p['protective_stops']}")
        if p.get("exit_plan"):        bits.append(f"Exit plan: {p['exit_plan']}")
        return "\n\n".join(bits)
    return ""


def _decision_label_for(symbol: str, decisions: dict) -> str:
    p = decisions.get("positions", {}).get(symbol)
    return p.get("decision_label", "") if p else ""


def sync_from_ib(ib: IB) -> int:
    """Pull all fills from IBKR; add any new ones to transactions.csv. Returns # added."""
    decisions = _load_decisions()
    existing  = _existing_exec_ids()
    rows      = _read_all_rows()
    added     = 0

    try:
        fills = ib.fills()
    except Exception as e:
        print(f"[fill_sync] ib.fills() error: {e}", file=sys.stderr)
        return 0

    for f in fills:
        eid = f.execution.execId
        if not eid or eid in existing:
            continue
        sym = f.contract.symbol
        side = f.execution.side  # "BOT" or "SLD"
        action = "BUY" if side == "BOT" else "SELL"
        qty = float(f.execution.shares or 0)
        px  = float(f.execution.price or 0)
        ts = f.execution.time.isoformat() if f.execution.time else ""
        reason = _decision_label_for(sym, decisions) if action == "BUY" else "exit"
        explanation = _explain_entry(sym, decisions) if action == "BUY" else ""
        row = {
            "ts":            ts,
            "action":        action,
            "symbol":        sym,
            "cashQty":       0,
            "totalQuantity": qty,
            "limitPrice":    "",
            "status":        "Filled",
            "orderId":       f.execution.orderId,
            "permId":        f.execution.permId,
            "filled":        qty,
            "avgFillPrice":  px,
            "reason":        reason,
            "explanation":   explanation,
            "execId":        eid,
        }
        rows.append(row)
        existing.add(eid)
        added += 1

    if added:
        # Sort by timestamp (oldest first), then rewrite
        rows.sort(key=lambda r: r.get("ts") or "")
        _write_all_rows(rows)
        print(f"[fill_sync] added {added} new fills, total rows now {len(rows)}",
              flush=True)
    return added


def main() -> None:
    if not os.environ.get("IBKR_HOST"):
        os.environ["IBKR_HOST"] = "127.0.0.1"

    daemon = "--daemon" in sys.argv
    ib = IB()
    ib.connect(os.environ["IBKR_HOST"], 4001, clientId=55, timeout=15)
    print(f"fill_sync connected: {ib.managedAccounts()}", flush=True)

    try:
        if not daemon:
            n = sync_from_ib(ib)
            print(f"  added {n} fills.")
            return
        while True:
            try:
                n = sync_from_ib(ib)
                if n:
                    print(f"  [{datetime.now(timezone.utc).isoformat()[11:19]}] +{n} fills",
                          flush=True)
            except Exception as e:
                print(f"[fill_sync] err: {e}", file=sys.stderr, flush=True)
            time.sleep(60)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
