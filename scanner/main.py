"""Scanner orchestrator — one daemon thread per source, so slow sources
(EVM's multichain scan takes ~60s) never block fast ones (momentum at 30s).

Everything writes to state/signals.jsonl + state/live_activity.jsonl.
"""
from __future__ import annotations

import argparse
import threading
import time
import traceback
from pathlib import Path

from sources import hyperliquid, solana, breadth, evm, cex_bridge, prop_desk
from sources import auto_tune_source, watchdog, qc_mirror, patent_mirror, combined_mirror
from sources import autopilot_mirror, autopilot_feed
from sources import congress_feed, congress_mirror
from sources import strategy_governor, value_screen, context_watcher, edge_ledger
from signals import log_tick, STATEDIR

SOURCES = [
    ("prop_desk",   prop_desk.tick,          15),   # multi-strategy prop-desk-lite
    ("auto_tune",   auto_tune_source.tick, 5 * 60), # self-teaching brain
    ("watchdog",    watchdog.tick,          60),    # self-heal + surface anomalies
    ("qc_mirror",       qc_mirror.tick,        5 * 60),  # QC-only mirror (kept for reference)
    ("patent_mirror",   patent_mirror.tick,   30 * 60),  # patent picks mirror
    ("combined_mirror", combined_mirror.tick,  5 * 60),  # ENSEMBLE — the target for $50 real money
    ("autopilot_mirror", autopilot_mirror.tick, 5 * 60),  # Autopilot 'Actively Managed' paper mirror
    ("autopilot_feed",   autopilot_feed.tick,  30 * 60),  # Robinhood sensor → autopilot_holdings.json
    ("congress_feed",    congress_feed.tick,   60 * 60),  # House Clerk PTRs → congress_holdings.json
    ("congress_mirror",  congress_mirror.tick,  5 * 60),  # Pelosi PTR paper mirror
    ("strategy_governor", strategy_governor.tick, 15 * 60),  # rank paper vs live; flag winners
    ("edge_ledger", edge_ledger.tick, 15 * 60),  # aggregate proven-edge verdict per strategy
    ("value_screen",    value_screen.tick,      6 * 3600),  # dad's value rules, ~6h cadence
    ("context_watcher", context_watcher.tick,   30 * 60),   # keep CONTEXT.md honest
    ("hyperliquid", hyperliquid.tick,   2 * 60),
    ("solana",      solana.tick,        5 * 60),
    ("breadth",     breadth.tick,       5 * 60),
    ("evm",         evm.tick,          10 * 60),
    ("cex_bridge",  cex_bridge.tick,   10 * 60),
]

STATEDIR.mkdir(exist_ok=True)
LOGF = STATEDIR / "orchestrator.log"


def _log(msg: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    print(line, end="", flush=True)
    with LOGF.open("a") as f:
        f.write(line)


def _worker(name: str, fn, cadence: int, stop: threading.Event):
    while not stop.is_set():
        try:
            t0 = time.time()
            fn()
            _log(f"{name} ok in {time.time()-t0:.1f}s")
        except Exception as e:
            _log(f"{name} FAIL: {e!r}")
            log_tick(name, f"error: {e!r}")
        # sleep in small chunks so shutdown is responsive
        for _ in range(cadence):
            if stop.is_set():
                return
            time.sleep(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Run every source once and exit")
    args = ap.parse_args()

    if args.once:
        for name, fn, _ in SOURCES:
            try:
                fn()
                _log(f"{name} ok (once)")
            except Exception as e:
                _log(f"{name} FAIL: {e!r}")
        return

    _log(f"scanner started (threaded) — {len(SOURCES)} sources: " +
         ", ".join(f"{n}({cad}s)" for n, _, cad in SOURCES))

    stop = threading.Event()
    threads = []
    for name, fn, cadence in SOURCES:
        t = threading.Thread(target=_worker, args=(name, fn, cadence, stop),
                             name=name, daemon=True)
        t.start()
        threads.append(t)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        _log("shutdown")
        stop.set()


if __name__ == "__main__":
    main()
