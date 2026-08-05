---
name: Multi-source Crypto Scanner
description: Unified "scan everything" scanner at ~/scanner/ feeding signals into the trading app's whale page via /api/whales
type: project
originSessionId: c777b35a-8784-4ed8-b477-cb1bcb76d2f3
---
Built 2026-07-08 in response to "just scan everything." Broader replacement for the narrow Dune-only whale runner.

**Location:** `/Users/quarki3/scanner/`

**Architecture:**
- `main.py` — orchestrator, runs each source at its own cadence in one process
- `signals.py` — unified `emit()` writer with dedup, writes to `state/signals.jsonl`
- `sources/hyperliquid.py` — 5min. Extreme funding + large trades (BTC/ETH/SOL/HYPE/PEPE/WIF/DOGE/kBONK/TRUMP/SUI/FARTCOIN) + top OI leaders. Public API, no key.
- `sources/solana.py` — 10min. DEXScreener top boosts + fresh boosts. Real momentum data (5m/1h price+vol+liq).
- `sources/breadth.py` — 10min. CoinGecko trending + top-250 24h movers (±10%) + Fear/Greed + multi-chain DEX boosts.
- `sources/evm.py` — 15min. Etherscan V2 across ETH/Base/Arb/OP/Polygon/BSC. Imports `curated_wallets` from `~/whale_tracker/`. Emits coordinated_accumulation + large_transfer.
- `sources/cex_bridge.py` — 15min. Big deposits/withdrawals to 14 known CEX cold wallets (Binance/Coinbase/OKX/Kraken/Bybit/Gate/Bitget) + 6 bridge contracts.

**Signal schema (jsonl):**
```json
{"ts":..., "source":"hyperliquid|solana|evm|cex|bridge|coingecko|dexscreener|alt",
 "signal_type":"extreme_funding|whale_trade|dex_momentum|cex_withdrawal|...",
 "symbol":..., "chain":..., "direction":"BUY|SELL|PUMP|DUMP|...",
 "size_usd":..., "wallets":..., "url":..., "note":..., "raw":{...}}
```

**App integration:** `_whale_status()` in `/Users/quarki3/ng_dashboard/main.py` now reads scanner/state/signals.jsonl and injects scanner rows into the `alerts[]` array of `/api/whales`. Android app renders them unchanged — no client update needed.

**Running:**
- Orchestrator: `nohup python3 /Users/quarki3/scanner/main.py >> /Users/quarki3/scanner/state/orchestrator.stdout 2>&1 &`
- Also legacy: `nohup python3 /Users/quarki3/whale_tracker/runner.py --loop &` still running Dune Q01
- ng_dashboard auto-restarts on kill (probably launchd); restart is instant

**Known caveats / TODO:**
- CoinGecko 24h % change can be nonsensical for thin tokens (saw CASHCAT reporting +93M%). Filter by min market cap or bound the display.
- token_price_hint() in evm.py is rough — WBTC=$96k/ETH=$3800 hardcoded. Real prices needed for accurate USD sizing.
- No Solana on-chain wallet tracking yet — only DEXScreener market signal. Adding Helius/Solscan requires an API key.
- No perp DEX coverage beyond Hyperliquid (GMX/Jupiter Perps still stubbed in plans).
- No launchd persistence — dies on reboot.
- Signal review UI is the existing "Q01 COORDINATED ACCUMULATION ALERTS" section. Rename to "All Signals" if the user wants clarity.

**Why:** Robert said "just scan everything" after I flagged that we were barely scratching the crypto universe (1 chain, 6 wallets, DEX-only). This scanner covers 6 EVM chains, Solana DEXes, Hyperliquid perps, CEX flows, bridge flows, and cross-market breadth. Not Nansen — but wide.

**How to apply:** New signal ideas should be added as `sources/<name>.py` with a `tick()` function. Register in `main.py::SOURCES`. Everything else (dedup, feed persistence, app display) works automatically.
