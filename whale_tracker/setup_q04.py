"""Create Q04, splice curated wallets in, execute, print entity-labeled transfer feed."""
import time
from collections import defaultdict
import re

import bootstrap
from curated_wallets import addresses, label_for, category_for

env = bootstrap.load_env()
api = env["DUNE_API_KEY"]

# Splice curated addresses into Q04 SQL. The curated CTE closes with `\n),`.
raw = open("04_curated_transfer_feed.sql").read()
wallets = addresses()
values  = ",\n        ".join(f"({w})" for w in wallets)
new_cte = f"curated (address) AS (\n    VALUES\n        {values}\n),"
pattern = re.compile(r"curated \(address\) AS \(.*?\n\),", re.DOTALL)
sql     = pattern.sub(new_cte, raw, count=1)

q04_id = env.get("DUNE_Q04_ID", "").strip()
if q04_id:
    q04_id = int(q04_id)
    print(f"Reusing Q04 id={q04_id}, PATCHing SQL…")
    bootstrap.update_query_sql(api, q04_id, sql)
else:
    print("Creating Q04 (curated transfer feed)…")
    for attempt in range(6):
        try:
            q04_id = bootstrap.create_query(api, "whale_tracker: curated transfer feed", sql)
            break
        except RuntimeError as e:
            if "429" in str(e):
                wait = 45 * (attempt + 1)
                print(f"429 on create, waiting {wait}s…")
                time.sleep(wait)
                continue
            raise
    env["DUNE_Q04_ID"] = str(q04_id)
    bootstrap.save_env(env)
    print(f"  query_id={q04_id}")

print("Executing Q04…")
for attempt in range(6):
    try:
        feed = bootstrap.execute_and_fetch(api, q04_id)
        break
    except RuntimeError as e:
        if "429" in str(e):
            wait = 45 * (attempt + 1)
            print(f"429 on execute, waiting {wait}s…")
            time.sleep(wait)
            continue
        raise
else:
    raise SystemExit("execution failed after retries")

# Client-side label + category
for r in feed:
    r["entity"]   = label_for(r.get("our_wallet", ""))
    r["category"] = category_for(r.get("our_wallet", ""))

print(f"\n=== {len(feed)} transfers ≥$50k in last 7d from curated wallets ===\n")
print(f"{'time':<20} {'cat':<4} {'entity':<24} {'dir':<4} {'token':<10} {'amount':>14} {'counterparty':<44}")
print("-" * 130)
for row in feed[:60]:
    ts     = str(row.get("block_time", ""))[:19]
    cat    = row.get("category", "?")
    entity = row.get("entity", "?")[:23]
    d      = row.get("direction", "?")
    tok    = (row.get("token_symbol") or "?")[:9]
    amt    = row.get("amount_usd", 0) or 0
    cp     = (row.get("counterparty") or "?")[:43]
    print(f"{ts:<20} {cat:<4} {entity:<24} {d:<4} {tok:<10} ${amt:>12,.0f} {cp}")

# Aggregate
by_entity = defaultdict(lambda: {"in": 0, "out": 0, "cat": "?"})
for r in feed:
    e   = r["entity"]
    d   = r.get("direction", "?")
    amt = r.get("amount_usd", 0) or 0
    if   d == "IN":  by_entity[e]["in"]  += amt
    elif d == "OUT": by_entity[e]["out"] += amt
    by_entity[e]["cat"] = r["category"]

print(f"\n=== Net flow by entity (7d) ===")
print(f"{'entity':<28} {'cat':<4} {'inflow':>14} {'outflow':>14} {'net':>14}")
print("-" * 80)
for e, s in sorted(by_entity.items(), key=lambda kv: abs(kv[1]["in"] - kv[1]["out"]), reverse=True):
    net = s["in"] - s["out"]
    arrow = "↑ acc" if net > 0 else "↓ dist" if net < 0 else "flat"
    print(f"{e:<28} {s['cat']:<4} ${s['in']:>12,.0f} ${s['out']:>12,.0f} ${net:>12,.0f} {arrow}")
