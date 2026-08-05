"""
End-to-end bootstrap:
  1. Create query 02 on Dune (or reuse if id is already in dune.env)
  2. Execute it → top smart-money wallets
  3. Splice those wallets into query 01
  4. Create query 01 on Dune
  5. Execute + print the coordinated-accumulation hits
  6. Persist both query IDs to dune.env

Free-tier Dune API allows creating private queries. This script is meant to
be run once (and re-run whenever you want to refresh the wallet list).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT    = Path(__file__).resolve().parent
KEYFILE = ROOT / "dune.env"
Q01_SQL = ROOT / "01_coordinated_accumulation.sql"
Q02_SQL = ROOT / "02_derive_smart_money.sql"
Q03_SQL = ROOT / "03_live_whale_feed.sql"

DUNE_BASE       = "https://api.dune.com/api/v1"
STATUS_POLL_S   = 6
STATUS_MAX_TRY  = 80  # 8min ceiling


def load_env() -> dict:
    env = {}
    if KEYFILE.exists():
        for line in KEYFILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            env[k] = v
    return env


def save_env(env: dict) -> None:
    lines = [f"{k}={v}" for k, v in env.items()]
    KEYFILE.write_text("\n".join(lines) + "\n")


def dune_headers(api_key: str) -> dict:
    return {"X-Dune-API-Key": api_key, "Content-Type": "application/json"}


def create_query(api_key: str, name: str, sql: str) -> int:
    """Create a private saved query on Dune. Returns numeric query_id."""
    payload = {
        "name":       name,
        "query_sql":  sql,
        "is_private": False,
    }
    r = requests.post(f"{DUNE_BASE}/query", headers=dune_headers(api_key),
                      json=payload, timeout=30)
    if not r.ok:
        raise RuntimeError(f"create_query failed [{r.status_code}]: {r.text}")
    return int(r.json()["query_id"])


def update_query_sql(api_key: str, query_id: int, sql: str) -> None:
    r = requests.patch(f"{DUNE_BASE}/query/{query_id}",
                       headers=dune_headers(api_key),
                       json={"query_sql": sql}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"update_query_sql failed [{r.status_code}]: {r.text}")


def execute_and_fetch(api_key: str, query_id: int) -> list[dict]:
    """Execute a saved query and block until results come back."""
    hdr = {"X-Dune-API-Key": api_key}
    r = requests.post(f"{DUNE_BASE}/query/{query_id}/execute", headers=hdr, timeout=30)
    if not r.ok:
        raise RuntimeError(f"execute failed [{r.status_code}]: {r.text}")
    eid = r.json()["execution_id"]
    print(f"  execution_id={eid}")

    for i in range(STATUS_MAX_TRY):
        time.sleep(STATUS_POLL_S)
        s = requests.get(f"{DUNE_BASE}/execution/{eid}/status", headers=hdr, timeout=15).json()
        state = s.get("state", "")
        if i % 3 == 0:
            print(f"  [{i * STATUS_POLL_S:>3}s] state={state}")
        if state == "QUERY_STATE_COMPLETED":
            break
        if state.startswith("QUERY_STATE_FAILED"):
            raise RuntimeError(f"query {query_id} failed: {json.dumps(s)}")
    else:
        raise RuntimeError(f"query {query_id} timed out")

    res = requests.get(f"{DUNE_BASE}/execution/{eid}/results", headers=hdr, timeout=60).json()
    return res.get("result", {}).get("rows", [])


def splice_wallets(sql_text: str, wallets: list[str]) -> str:
    """Replace the placeholder VALUES block in 01_coordinated_accumulation.sql."""
    if not wallets:
        raise ValueError("no wallets to splice")
    values = ",\n        ".join(f"({w})" for w in wallets)
    new_block = (
        "smart_money (address) AS (\n"
        "    VALUES\n"
        f"        {values}\n"
        "),"
    )
    # Match up to the CTE's outer `),` which sits on its own line at column 0
    pattern = re.compile(r"smart_money \(address\) AS \(.*?\n\),", re.DOTALL)
    if not pattern.search(sql_text):
        raise RuntimeError("could not find smart_money CTE to splice")
    return pattern.sub(new_block, sql_text, count=1)


def main() -> None:
    env     = load_env()
    api_key = env.get("DUNE_API_KEY", "")
    if not api_key:
        sys.exit("missing DUNE_API_KEY in dune.env")

    # ── Step 1: query 02 (derive smart money) ────────────────────────────
    q02_sql = Q02_SQL.read_text()
    q02_id  = env.get("DUNE_Q02_ID", "").strip()
    if q02_id:
        q02_id = int(q02_id)
        print(f"Reusing query 02 id={q02_id}, updating SQL…")
        update_query_sql(api_key, q02_id, q02_sql)
    else:
        print("Creating query 02 (derive smart money)…")
        q02_id = create_query(api_key, "whale_tracker: derive smart money", q02_sql)
        env["DUNE_Q02_ID"] = str(q02_id)
        save_env(env)
        print(f"  query_id={q02_id}")

    print("Executing query 02… (this can take a few minutes)")
    rows02 = execute_and_fetch(api_key, q02_id)
    print(f"Query 02 returned {len(rows02)} smart-money wallets")

    if not rows02:
        sys.exit("query 02 returned no wallets — check the SQL or widen the params")

    wallets = [r["wallet"] for r in rows02 if r.get("wallet")]
    print("Top 5:")
    for r in rows02[:5]:
        print(f"  {r['wallet']}  net=${r.get('net_usd', 0):>12,.0f}  "
              f"edge={r.get('edge_ratio', 0):.3f}  breadth={r.get('token_breadth')}")

    # ── Step 2: splice wallets into query 01 ─────────────────────────────
    q01_original = Q01_SQL.read_text()
    q01_spliced  = splice_wallets(q01_original, wallets)
    print(f"Spliced {len(wallets)} wallets into query 01 SQL")

    # ── Step 3: create/update query 01 on Dune ───────────────────────────
    q01_id = env.get("DUNE_Q01_ID", "").strip()
    if q01_id:
        q01_id = int(q01_id)
        print(f"Reusing query 01 id={q01_id}, updating SQL with fresh wallets…")
        update_query_sql(api_key, q01_id, q01_spliced)
    else:
        print("Creating query 01 (coordinated accumulation)…")
        q01_id = create_query(api_key, "whale_tracker: coordinated accumulation", q01_spliced)
        env["DUNE_Q01_ID"]    = str(q01_id)
        env["DUNE_QUERY_ID"]  = str(q01_id)   # runner.py polls this one
        save_env(env)
        print(f"  query_id={q01_id}")

    # ── Step 4: execute query 01 ─────────────────────────────────────────
    print("Executing query 01 (coordinated accumulation, last 24h)…")
    hits = execute_and_fetch(api_key, q01_id)
    print(f"\n=== {len(hits)} coordinated-accumulation hits in last 24h ===")
    for h in hits[:20]:
        print(f"  {h.get('symbol','?'):<10} {h.get('blockchain','?'):<10} "
              f"wallets={h.get('distinct_wallets')} "
              f"agg=${h.get('agg_usd', 0):>12,.0f} "
              f"window={h.get('accum_window_min')}min")
    if not hits:
        print("  (no hits — expected on quiet days; loosen params in query 01 to test)")

    # ── Step 5: query 03 (live whale-trade feed) ─────────────────────────
    q03_sql      = Q03_SQL.read_text()
    q03_spliced  = splice_wallets(q03_sql, wallets)
    q03_id       = env.get("DUNE_Q03_ID", "").strip()
    if q03_id:
        q03_id = int(q03_id)
        print(f"\nReusing query 03 id={q03_id}, updating SQL with fresh wallets…")
        update_query_sql(api_key, q03_id, q03_spliced)
    else:
        print("\nCreating query 03 (live whale-trade feed)…")
        q03_id = create_query(api_key, "whale_tracker: live whale feed", q03_spliced)
        env["DUNE_Q03_ID"] = str(q03_id)
        save_env(env)
        print(f"  query_id={q03_id}")

    print("Executing query 03 (last 7d, all SM wallets, ≥$25k)…")
    feed = execute_and_fetch(api_key, q03_id)
    print(f"\n=== Live whale feed: {len(feed)} trades ≥$25k in last 7d ===")

    # Group by wallet+token for pattern surface
    from collections import defaultdict
    by_wallet = defaultdict(list)
    for row in feed:
        by_wallet[row.get('entity', '(unlabeled)')].append(row)

    print(f"\n{'time':<20} {'entity':<25} {'action':<25} {'amount':>15} {'dex':<12}")
    print("-" * 100)
    for row in feed[:40]:
        ts     = str(row.get('block_time', ''))[:19]
        entity = (row.get('entity') or '(unlabeled)')[:24]
        action = (row.get('action') or '?')[:24]
        amt    = row.get('amount_usd', 0) or 0
        dex    = (row.get('dex') or '?')[:11]
        print(f"{ts:<20} {entity:<25} {action:<25} ${amt:>13,.0f} {dex:<12}")


if __name__ == "__main__":
    main()
