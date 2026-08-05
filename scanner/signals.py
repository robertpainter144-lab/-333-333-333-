"""Unified signal writer. Every source calls emit() with a dict."""
from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Lock

ROOT      = Path(__file__).resolve().parent
STATEDIR  = ROOT / "state"
FEEDFILE  = STATEDIR / "signals.jsonl"
SEENFILE  = STATEDIR / "seen.json"
_LOCK     = Lock()


def _load_seen() -> set[str]:
    if not SEENFILE.exists():
        return set()
    try:
        return set(json.loads(SEENFILE.read_text()))
    except Exception:
        return set()


def _save_seen(seen: set[str]) -> None:
    STATEDIR.mkdir(exist_ok=True)
    tmp = SEENFILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(seen)[-5000:]))
    tmp.replace(SEENFILE)


def emit(source: str, signal_type: str, symbol: str, chain: str, size_usd: float,
         *, dedup_key: str | None = None, direction: str = "?",
         wallets: int = 0, url: str = "", note: str = "", raw: dict | None = None) -> bool:
    """Write a signal to signals.jsonl. Returns True if new (deduped by dedup_key)."""
    with _LOCK:
        seen = _load_seen()
        key = dedup_key or f"{source}:{signal_type}:{chain}:{symbol}:{int(time.time()) // 3600}"
        if key in seen:
            return False
        seen.add(key)
        _save_seen(seen)

        row = {
            "ts":          int(time.time()),
            "source":      source,
            "signal_type": signal_type,
            "symbol":      symbol,
            "chain":       chain,
            "direction":   direction,
            "size_usd":    round(float(size_usd), 2),
            "wallets":     wallets,
            "url":         url,
            "note":        note,
            "raw":         raw or {},
        }
        STATEDIR.mkdir(exist_ok=True)
        with FEEDFILE.open("a") as f:
            f.write(json.dumps(row) + "\n")
        return True


def log_tick(source: str, note: str) -> None:
    """Cheap per-source heartbeat, for the app to show 'last tick' per source."""
    STATEDIR.mkdir(exist_ok=True)
    tickfile = STATEDIR / f"tick_{source}.txt"
    tickfile.write_text(f"{int(time.time())}\t{note}")
    # Also stream into live_activity for the app's live feed
    emit_live(source, note)


LIVE_FILE = STATEDIR / "live_activity.jsonl"
_MAX_LIVE_ROWS = 300


def emit_live(source: str, activity: str, **extra) -> None:
    """Append a one-line live-activity row. Bounded to last ~300 rows.
    Not deduped — this is a pulse, not a signal."""
    with _LOCK:
        STATEDIR.mkdir(exist_ok=True)
        row = {"ts": int(time.time()), "source": source, "activity": activity}
        row.update(extra)
        existing = []
        if LIVE_FILE.exists():
            try:
                existing = LIVE_FILE.read_text().splitlines()[-(_MAX_LIVE_ROWS - 1):]
            except Exception:
                existing = []
        existing.append(json.dumps(row))
        LIVE_FILE.write_text("\n".join(existing) + "\n")
