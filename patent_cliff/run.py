"""
Orchestrator: scan → sec_signals → portfolio → dashboard.

Default mode is dry-run: builds the data pipeline and the dashboard but places
NO orders. Add --paper to fire on Alpaca paper.

Run:
    python3.10 run.py                              # full pipeline, dry-run
    python3.10 run.py --paper                       # + Alpaca paper orders
    python3.10 run.py --no-sec                      # skip the LLM step
    python3.10 run.py --open                        # also open the dashboard
    python3.10 run.py --refresh                     # re-pull FDA + SEC caches
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def _run(name: str, args: list[str]):
    cmd = [PY, str(ROOT / name)] + args
    print(f"\n══════ {name} ══════")
    print(" ".join(cmd))
    rc = subprocess.call(cmd)
    if rc != 0:
        print(f"\n{name} exited with code {rc} — continuing with downstream steps")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", action="store_true", help="Fire Alpaca paper orders")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--budget", type=float, default=1000.0)
    ap.add_argument("--window-months", type=int, default=24)
    ap.add_argument("--no-sec", action="store_true", help="Skip SEC/LLM step")
    ap.add_argument("--refresh", action="store_true",
                    help="Force re-download of Orange + Purple + Paragraph IV + SEC caches")
    ap.add_argument("--open", action="store_true", help="Open the dashboard in browser")
    args = ap.parse_args()

    scan_args = ["--window-months", str(args.window_months)]
    if args.refresh: scan_args.append("--refresh")
    _run("scan.py", scan_args)

    if not args.no_sec:
        sec_args = ["--refresh"] if args.refresh else []
        _run("sec_signals.py", sec_args)

    port_args = ["--top", str(args.top), "--budget", str(args.budget)]
    if args.paper: port_args.append("--paper")
    _run("portfolio.py", port_args)

    dash_args = ["--open"] if args.open else []
    _run("dashboard.py", dash_args)

    print("\n══════ pipeline complete ══════")
    print(f"Dashboard: file://{ROOT}/output/dashboard.html")


if __name__ == "__main__":
    main()
