#!/usr/bin/env python3
"""Record a Kraken Funded balance point (D62). Kraken Funded has no API,
so the in-app equity chart is fed by hand: Robert reports the balance, a
Claude session runs this. Usage:
    funded_update.py <balance> [note...]
Appends {ts, bal} to funded.json's 'equity' series (capped), updates the
headline balance + updated stamp. Idempotent-ish: a point within 30s of
the last one at the same balance is skipped so rapid re-runs don't dupe."""
import sys
import json
import time
from pathlib import Path

ROOT = Path.home() / "lab-stack" / "crypto_sleeve"
if not ROOT.exists():
    ROOT = Path.home() / "crypto_sleeve"
FJ = ROOT / "state" / "funded.json"


def main():
    if len(sys.argv) < 2:
        print("usage: funded_update.py <balance> [note...]")
        return
    bal = round(float(sys.argv[1]), 2)
    note = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""
    d = json.loads(FJ.read_text())
    eq = d.get("equity") or []
    now = int(time.time())
    if not (eq and now - eq[-1]["ts"] < 30 and abs(eq[-1]["bal"] - bal) < 1e-6):
        eq.append({"ts": now, "bal": bal})
    d["equity"] = eq[-2000:]
    d["balance"] = bal
    d["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    if note:
        d["last_note"] = note
    tmp = FJ.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    tmp.replace(FJ)
    chg = (bal / d["start_balance"] - 1) * 100 if d.get("start_balance") else 0
    print(f"recorded ${bal:.2f} ({chg:+.2f}% vs start) — {len(d['equity'])} points")


if __name__ == "__main__":
    main()
