---
name: PoC10 Bot
description: $10 paper trading reliability test bot — launched 2026-06-15, running on Alpaca PAPER
type: project
originSessionId: 2cad3334-15d0-4269-974c-6b3092210690
---
## Files
- `/Users/quarki3/poc10_bot.py` — main bot (PID varies; launched with `nohup python3 poc10_bot.py &`)
- `/Users/quarki3/track_record.jsonl` — event log, all entries tagged `"tag": "poc10"`
- `/Users/quarki3/swarm_results/completed/poc10.json` — result + first_orders

## Architecture
- `Broker` ABC → `AlpacaBroker` (swap to `IBKRBroker` for live)
- `SubLedger` tracks only poc10's own positions — snapshots legacy Alpaca positions at startup and never touches them
- Three sleeves: crypto $4, equity $3, defensive $3
- Hard stop −3% from entry on all; trailing stop −2% from peak on crypto sleeve
- 60-second tick loop; stops checked first, then entries

## Sleeve config (final)
| Sleeve | Budget | Symbols | Notes |
|--------|--------|---------|-------|
| crypto | $4.00 | IBIT, ETHA | Crypto ETFs — Alpaca has $10 min per direct BTC/ETH order, incompatible with $4 sleeve |
| equity | $3.00 | QQQ, SPY | Momentum gate (5-min SMA), hard stop |
| defensive | $3.00 | GLD | Always-hold when market open, hard stop |

## Alpaca crypto limitation
Direct crypto (BTC/USD, ETH/USD) requires $10 minimum notional per order on Alpaca paper.
Substituted with IBIT (BlackRock Bitcoin ETF) and ETHA (BlackRock Ethereum ETF).
**Why:** Alpaca error `"cost basis must be >= minimal amount of order 10"`.
**How to apply:** Any future bot targeting crypto on Alpaca paper must allocate ≥$10 per crypto symbol, or use crypto ETFs instead.

## First run results (2026-06-15 15:48 ET)
- 4 buy orders placed, 0 errors, all filled
- IBIT: 0.0528 shares @ $37.72, MV $1.99
- ETHA: 0.1445 shares @ $13.77, MV $1.99
- SPY: 0.0020 shares @ $754.78, MV $1.49
- GLD: 0.0075 shares @ $396.27, MV $2.99
- Bot running in background, tick every 60s
- Log: /tmp/poc10_bot.log
