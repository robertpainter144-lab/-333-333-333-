"""Standalone Q03 executor with rate-limit backoff."""
import time
import bootstrap

env    = bootstrap.load_env()
q03_id = int(env["DUNE_Q03_ID"])
api    = env["DUNE_API_KEY"]

for attempt in range(6):
    try:
        feed = bootstrap.execute_and_fetch(api, q03_id)
        break
    except RuntimeError as e:
        if "429" in str(e):
            wait = 30 * (attempt + 1)
            print(f"429 rate limit, waiting {wait}s (attempt {attempt+1}/6)…")
            time.sleep(wait)
            continue
        raise
else:
    raise SystemExit("query 03 never executed after 6 attempts")

print(f"\n=== {len(feed)} whale trades >=$25k in last 7d ===\n")
print(f"{'time':<20} {'entity':<25} {'action':<28} {'amount':>13} {'dex':<12}")
print("-" * 100)
for row in feed[:50]:
    ts     = str(row.get("block_time", ""))[:19]
    entity = (row.get("entity") or "(unlabeled)")[:24]
    action = (row.get("action") or "?")[:27]
    amt    = row.get("amount_usd", 0) or 0
    dex    = (row.get("dex") or "?")[:11]
    print(f"{ts:<20} {entity:<25} {action:<28} ${amt:>12,.0f} {dex:<12}")

# Wallet activity summary
from collections import defaultdict
activity = defaultdict(lambda: {"n": 0, "usd": 0, "entity": ""})
for row in feed:
    w = row.get("wallet", "?")
    activity[w]["n"]      += 1
    activity[w]["usd"]    += row.get("amount_usd", 0) or 0
    activity[w]["entity"]  = row.get("entity") or "(unlabeled)"

print(f"\n=== Top 15 most-active wallets ===")
print(f"{'wallet':<44} {'entity':<25} {'trades':>7} {'volume':>15}")
print("-" * 100)
for w, s in sorted(activity.items(), key=lambda kv: kv[1]["usd"], reverse=True)[:15]:
    print(f"{w:<44} {s['entity'][:24]:<25} {s['n']:>7} ${s['usd']:>13,.0f}")
