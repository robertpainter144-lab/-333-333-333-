"""
Push the curated whale list into Q03 (bypassing the auto-derived Q02),
execute, and print an entity-labeled feed.

This is the "hand-picked cabal" workflow. Run whenever you update
curated_wallets.py — no rate-limit issues, just a PATCH.
"""
import time
from collections import defaultdict

import bootstrap
from curated_wallets import addresses, label_for, category_for

env    = bootstrap.load_env()
api    = env["DUNE_API_KEY"]
q03_id = int(env["DUNE_Q03_ID"])

wallets = addresses()
print(f"Curated list: {len(wallets)} wallets")

sql     = open("03_live_whale_feed.sql").read()
spliced = bootstrap.splice_wallets(sql, wallets)
bootstrap.update_query_sql(api, q03_id, spliced)
print(f"Q03 (id={q03_id}) PATCHed with curated list")

# Run with backoff for rate limits
for attempt in range(6):
    try:
        feed = bootstrap.execute_and_fetch(api, q03_id)
        break
    except RuntimeError as e:
        if "429" in str(e):
            wait = 30 * (attempt + 1)
            print(f"429, waiting {wait}s…")
            time.sleep(wait)
            continue
        raise
else:
    raise SystemExit("execution failed after 6 tries")

# Client-side label + category join
for r in feed:
    r["entity"]   = label_for(r.get("wallet", ""))
    r["category"] = category_for(r.get("wallet", ""))

print(f"\n=== {len(feed)} trades ≥$25k in last 7d from curated wallets ===\n")
print(f"{'time':<20} {'category':<4} {'entity':<24} {'action':<26} {'amount':>14} {'dex':<12}")
print("-" * 110)
for row in feed[:60]:
    ts     = str(row.get("block_time", ""))[:19]
    cat    = row.get("category", "?")
    entity = row.get("entity", "?")[:23]
    action = (row.get("action", "?") or "?")[:25]
    amt    = row.get("amount_usd", 0) or 0
    dex    = (row.get("dex", "") or "")[:11]
    print(f"{ts:<20} {cat:<4} {entity:<24} {action:<26} ${amt:>12,.0f} {dex:<12}")

# By-entity summary
by_entity = defaultdict(lambda: {"n": 0, "usd": 0, "cat": "?"})
for r in feed:
    e = r["entity"]
    by_entity[e]["n"]   += 1
    by_entity[e]["usd"] += r.get("amount_usd", 0) or 0
    by_entity[e]["cat"]  = r["category"]

print(f"\n=== By entity ===")
print(f"{'entity':<28} {'cat':<4} {'trades':>7} {'volume':>15}")
print("-" * 60)
for e, s in sorted(by_entity.items(), key=lambda kv: kv[1]["usd"], reverse=True):
    print(f"{e:<28} {s['cat']:<4} {s['n']:>7} ${s['usd']:>13,.0f}")
