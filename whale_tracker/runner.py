"""
Poll Dune for the coordinated-accumulation query, alert on new hits.

Reads DUNE_API_KEY and DUNE_QUERY_ID from ~/whale_tracker/dune.env or env.
Alerts via ~/ibkr_bot/notify.py (Pushover). Silent no-op if either is missing.

Run: python3 runner.py            # single poll
     python3 runner.py --loop     # forever, 15min cadence

Meant to be triggered by launchd/cron; the --loop is just for local dev.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import requests

ROOT      = Path(__file__).resolve().parent
KEYFILE   = ROOT / "dune.env"
STATEFILE = ROOT / "state" / "seen.json"
LOGFILE   = ROOT / "state" / "runner.log"

DUNE_EXEC_URL   = "https://api.dune.com/api/v1/query/{qid}/execute"
DUNE_STATUS_URL = "https://api.dune.com/api/v1/execution/{eid}/status"
DUNE_RESULT_URL = "https://api.dune.com/api/v1/execution/{eid}/results"

POLL_INTERVAL_S     = 15 * 60
STATUS_POLL_S       = 5
STATUS_MAX_ATTEMPTS = 60  # 5min ceiling for a single query run

def push(title: str, message: str, priority: int = 0, url: str = "") -> bool:
    # Pushover is reserved for real-money activity. Whale tracker is a
    # market-signal watcher, so alerts stay in runner.log only.
    return False


def _load_keys() -> tuple[str, str]:
    api_key  = os.environ.get("DUNE_API_KEY", "")
    query_id = os.environ.get("DUNE_QUERY_ID", "")
    if (not api_key or not query_id) and KEYFILE.exists():
        for line in KEYFILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            if k == "DUNE_API_KEY"  and not api_key:  api_key  = v
            if k == "DUNE_QUERY_ID" and not query_id: query_id = v
    return api_key, query_id


def _log(msg: str) -> None:
    STATEFILE.parent.mkdir(exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    print(line, end="")
    with LOGFILE.open("a") as f:
        f.write(line)


def _load_seen() -> set[str]:
    if not STATEFILE.exists():
        return set()
    try:
        return set(json.loads(STATEFILE.read_text()))
    except Exception:
        return set()


def _save_seen(seen: set[str]) -> None:
    STATEFILE.parent.mkdir(exist_ok=True)
    STATEFILE.write_text(json.dumps(sorted(seen)))


def _hit_key(row: dict) -> str:
    # Dedup on (chain, token, last_buy timestamp truncated to hour).
    # Same token pinging twice in the same hour is one event.
    return f"{row.get('blockchain','?')}:{row.get('token','?')}:{str(row.get('last_buy',''))[:13]}"


def run_query() -> list[dict]:
    api_key, query_id = _load_keys()
    if not api_key or not query_id:
        _log("skip: missing DUNE_API_KEY or DUNE_QUERY_ID")
        return []

    headers = {"X-Dune-API-Key": api_key}
    r = requests.post(DUNE_EXEC_URL.format(qid=query_id), headers=headers, timeout=30)
    r.raise_for_status()
    eid = r.json()["execution_id"]

    for _ in range(STATUS_MAX_ATTEMPTS):
        time.sleep(STATUS_POLL_S)
        s = requests.get(DUNE_STATUS_URL.format(eid=eid), headers=headers, timeout=15).json()
        state = s.get("state", "")
        if state == "QUERY_STATE_COMPLETED":
            break
        if state.startswith("QUERY_STATE_FAILED"):
            _log(f"query failed: {s}")
            return []
    else:
        _log(f"query timed out (>{STATUS_MAX_ATTEMPTS * STATUS_POLL_S}s)")
        return []

    res = requests.get(DUNE_RESULT_URL.format(eid=eid), headers=headers, timeout=30).json()
    return res.get("result", {}).get("rows", [])


def alert_on_new(rows: list[dict]) -> int:
    seen = _load_seen()
    new_rows = [r for r in rows if _hit_key(r) not in seen]
    for row in new_rows:
        symbol   = row.get("symbol")   or "?"
        chain    = row.get("blockchain") or "?"
        wallets  = row.get("distinct_wallets")
        agg_usd  = row.get("agg_usd")
        window   = row.get("accum_window_min")
        token    = row.get("token") or ""

        push(
            title    = f"Whale accumulation: {symbol} ({chain})",
            message  = (f"{wallets} smart wallets bought {symbol} on {chain}\n"
                        f"agg ${agg_usd:,.0f} over {window} min\n"
                        f"token: {token}"),
            priority = 0,
            url      = f"https://dexscreener.com/{chain}/{token}",
        )
        seen.add(_hit_key(row))
        _log(f"alert: {symbol} {chain} wallets={wallets} agg_usd={agg_usd}")

    _save_seen(seen)
    return len(new_rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true", help="poll forever at 15min cadence")
    args = ap.parse_args()

    while True:
        try:
            rows = run_query()
            n = alert_on_new(rows)
            _log(f"tick: {len(rows)} candidates, {n} new alerts")
        except Exception as e:
            _log(f"tick failed: {e!r}")
        if not args.loop:
            return
        time.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    main()
