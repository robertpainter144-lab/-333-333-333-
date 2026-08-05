"""Post-process Q03 feed: separate real buys from stable rotations."""
from collections import Counter, defaultdict
import bootstrap

env  = bootstrap.load_env()
feed = bootstrap.execute_and_fetch(env["DUNE_API_KEY"], int(env["DUNE_Q03_ID"]))

STABLES = {"USDC","USDT","USDe","DAI","FRAX","LUSD","GUSD","TUSD","PYUSD",
           "USD₮","USDT0","USDC.e","USDm","USD₮0","sUSDe","GHO","crvUSD","FDUSD"}
MAJORS  = {"ETH","WETH","BTC","WBTC","stETH","wstETH","rETH","cbETH","weETH"}

real_buys, stable_rot, derisk = [], [], []
for r in feed:
    bought, sold = r.get("bought_symbol",""), r.get("sold_symbol","")
    if bought in STABLES and sold in STABLES:
        stable_rot.append(r)
    elif bought in STABLES:                              # sold something for stable
        derisk.append(r)
    elif bought not in STABLES | MAJORS:                 # bought a non-cash, non-major token
        real_buys.append(r)

def print_rows(rows, limit=30, title=""):
    print(f"\n=== {title} ({len(rows)} rows) ===")
    print(f"{'time':<20} {'wallet':<44} {'buying':<12} {'sold':<12} {'amount':>14} {'dex':<12}")
    print("-" * 120)
    for r in rows[:limit]:
        ts     = str(r.get("block_time",""))[:19]
        w      = r.get("wallet","")[:43]
        b      = (r.get("bought_symbol","") or "")[:11]
        s      = (r.get("sold_symbol","") or "")[:11]
        amt    = r.get("amount_usd",0) or 0
        dex    = (r.get("dex","") or "")[:11]
        print(f"{ts:<20} {w:<44} {b:<12} {s:<12} ${amt:>12,.0f} {dex:<12}")

# Real buys — the "these wallets are betting on X" signal
print_rows(sorted(real_buys, key=lambda r: r.get("amount_usd",0), reverse=True),
           limit=30, title="Biggest non-stable, non-major BUYS in last 7d")

# Aggregate real buys by token to see conviction
print(f"\n=== Tokens with the most whale-buy volume (real buys only) ===")
by_token = defaultdict(lambda: {"usd":0, "buys":0, "wallets":set()})
for r in real_buys:
    tok = r.get("bought_symbol") or "?"
    by_token[tok]["usd"]     += r.get("amount_usd",0) or 0
    by_token[tok]["buys"]    += 1
    by_token[tok]["wallets"].add(r.get("wallet",""))
print(f"{'token':<15} {'total buy $':>15} {'#buys':>7} {'#distinct wallets':>20}")
print("-" * 60)
for tok, s in sorted(by_token.items(), key=lambda kv: kv[1]["usd"], reverse=True)[:20]:
    print(f"{tok:<15} ${s['usd']:>13,.0f} {s['buys']:>7} {len(s['wallets']):>20}")

# Global risk-on/off tally
total_derisk = sum(r.get("amount_usd",0) or 0 for r in derisk)
total_buy    = sum(r.get("amount_usd",0) or 0 for r in real_buys)
total_rot    = sum(r.get("amount_usd",0) or 0 for r in stable_rot)
print(f"\n=== 7d flow summary across 200 smart-money wallets ===")
print(f"  DE-RISK (sold → stable):  ${total_derisk:>15,.0f}  ({len(derisk):>4} trades)")
print(f"  REAL BUYS (into altcoin): ${total_buy:>15,.0f}  ({len(real_buys):>4} trades)")
print(f"  STABLE ROTATIONS:         ${total_rot:>15,.0f}  ({len(stable_rot):>4} trades)")
ratio = total_derisk / max(total_buy, 1)
print(f"  DE-RISK / BUY ratio:      {ratio:.2f}x   ({'⚠ bearish' if ratio > 2 else 'balanced' if ratio > 0.8 else '↑ risk-on'})")
