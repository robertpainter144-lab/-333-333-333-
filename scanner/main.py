"""Scanner orchestrator — one daemon thread per source, so slow sources
(EVM's multichain scan takes ~60s) never block fast ones (momentum at 30s).

Everything writes to state/signals.jsonl + state/live_activity.jsonl.
"""
from __future__ import annotations

import argparse
import os
import threading
import time
import traceback
from pathlib import Path

import importlib
from signals import log_tick, STATEDIR

# (name, module, fn, cadence). Imported LAZILY in _load_sources so a host that
# only runs a subset (SCANNER_ONLY) never imports the others — and a source
# whose local module/dep is missing is skipped, not fatal.
_SOURCE_SPECS = [
    ("prop_desk",        "prop_desk",         "tick", 15),
    ("auto_tune",        "auto_tune_source",  "tick", 5 * 60),
    ("watchdog",         "watchdog",          "tick", 60),
    ("qc_mirror",        "qc_mirror",         "tick", 5 * 60),
    ("patent_mirror",    "patent_mirror",     "tick", 30 * 60),
    ("combined_mirror",  "combined_mirror",   "tick", 5 * 60),
    ("autopilot_mirror", "autopilot_mirror",  "tick", 5 * 60),
    ("autopilot_feed",   "autopilot_feed",    "tick", 30 * 60),
    ("congress_feed",    "congress_feed",     "tick", 60 * 60),
    ("congress_mirror",  "congress_mirror",   "tick", 5 * 60),
    ("strategy_governor","strategy_governor", "tick", 15 * 60),
    ("edge_ledger",      "edge_ledger",       "tick", 15 * 60),
    ("value_screen",     "value_screen",      "tick", 6 * 3600),
    ("autopilot_catalog", "autopilot_catalog", "tick", 6 * 3600),
    ("dip_buyer",         "dip_buyer",         "tick", 6 * 3600),
    ("context_watcher",  "context_watcher",   "tick", 30 * 60),
    ("hyperliquid",      "hyperliquid",       "tick", 2 * 60),
    ("solana",           "solana",            "tick", 5 * 60),
    ("breadth",          "breadth",           "tick", 5 * 60),
    ("evm",              "evm",               "tick", 10 * 60),
    ("cex_bridge",       "cex_bridge",        "tick", 10 * 60),
]


def _load_sources(keep):
    """Import the sources we intend to run. keep=None -> all; else a name set.
    A source whose import fails (missing local module/dep) is skipped, so one
    bad source can't crash the orchestrator."""
    out = []
    for name, mod, fn, cad in _SOURCE_SPECS:
        if keep is not None and name not in keep:
            continue
        try:
            m = importlib.import_module(f"sources.{mod}")
            out.append((name, getattr(m, fn), cad))
        except Exception as e:
            print(f"[scanner] skip source '{name}': {e!r}", flush=True)
    return out

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

    # SCANNER_ONLY="a,b,c" runs just that subset (memory-constrained hosts, e.g.
    # the GCP e2-micro that only needs the mirror/governor panels, not the full
    # crypto/prop-desk sweep). Empty/unset = all sources.
    _only = os.environ.get("SCANNER_ONLY", "").strip()
    _keep = {s.strip() for s in _only.split(",") if s.strip()} if _only else None
    sources = _load_sources(_keep)

    if args.once:
        for name, fn, _ in sources:
            try:
                fn()
                _log(f"{name} ok (once)")
            except Exception as e:
                _log(f"{name} FAIL: {e!r}")
        return

    _log(f"scanner started (threaded) — {len(sources)} sources: " +
         ", ".join(f"{n}({cad}s)" for n, _, cad in sources))

    stop = threading.Event()
    threads = []
    for name, fn, cadence in sources:
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
