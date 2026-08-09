#!/usr/bin/env python3
"""Refresh state/whale_signals.json from the whale_tracker Q03 smart-money feed.

Runs every 6h via launchd com.quarki3.crypto-whale-refresh (Dune credits:
4 executions/day). Output drives the sleeve's macro entry gate:
  risk_on = real altcoin/major buys $ / (buys $ + de-risk-to-stable $)
On any failure the old file is left in place; the sleeve treats a stale
file as momentum-only fallback, so this can never block exits or rails.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
sys.path.insert(0, str(HOME / "whale_tracker"))
import bootstrap  # noqa: E402

OUT = Path(__file__).resolve().parent / "state" / "whale_signals.json"

STABLES = {"USDC", "USDT", "USDe", "DAI", "FRAX", "LUSD", "GUSD", "TUSD", "PYUSD",
           "USD₮", "USDT0", "USDC.e", "USDm", "USD₮0", "sUSDe", "GHO", "crvUSD", "FDUSD"}
MAJORS = {"ETH", "WETH", "BTC", "WBTC", "stETH", "wstETH", "rETH", "cbETH", "weETH"}

env = bootstrap.load_env()
feed = bootstrap.execute_and_fetch(env["DUNE_API_KEY"], int(env["DUNE_Q03_ID"]))
if not feed:
    # Empty feed = no signal (leave any previous file for the staleness check);
    # never write a risk_on=0 file that would read as whale-blocked.
    print("Q03 returned 0 rows — leaving whale_signals.json untouched "
          "(sleeve falls back to momentum-only). Try whale_tracker/run_curated.py.")
    sys.exit(0)

buys = derisk = major_buys = 0.0
for r in feed:
    bought, sold = r.get("bought_symbol", ""), r.get("sold_symbol", "")
    usd = r.get("amount_usd", 0) or 0
    if bought in STABLES and sold in STABLES:
        continue
    if bought in STABLES:
        derisk += usd
    else:
        buys += usd
        if bought in MAJORS:
            major_buys += usd

risk_on = buys / max(buys + derisk, 1)
out = {"asof": datetime.now(timezone.utc).isoformat(timespec="seconds"),
       "risk_on": round(risk_on, 4),
       "real_buys_usd": round(buys), "derisk_usd": round(derisk),
       "major_buys_usd": round(major_buys), "rows": len(feed)}
tmp = OUT.with_suffix(".tmp")
tmp.write_text(json.dumps(out, indent=2))
tmp.replace(OUT)
print(f"{out['asof']} risk_on={risk_on:.2f} buys=${buys:,.0f} derisk=${derisk:,.0f}")
