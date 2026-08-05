---
name: Whale Tracker (DuneSQL)
description: Coordinated-accumulation crypto whale signal — DuneSQL queries at ~/whale_tracker/, not yet live
type: project
originSessionId: b900e8ef-c15c-473a-b88e-4b293aa26495
---
Scaffolded 2026-07-01 at `~/whale_tracker/`, pipeline validated end-to-end 2026-07-02.

**Live Dune queries (public, on Robert's free-tier account):**
- Q02 id=`7868545` — derive smart money. Ranks Ethereum wallets by realized gains (per-token positive deltas summed) over 30d. Floor: $10k realized gains, $25k gross volume, 3-40 token breadth. Returns top 200.
- Q01 id=`7868558` — coordinated accumulation. Fires when ≥3 wallets from Q02's list buy the same token in 24h for ≥$100k aggregate, ≥$2.5k per buy. Blocklist covers stables (USDC/T/USDe/PYUSD/etc), majors (ETH/BTC), and LSTs (stETH/wstETH/rETH).

**Local files:**
- `bootstrap.py` — refresh: PATCHes Q02 with current SQL, executes, splices top wallets into Q01, PATCHes+executes Q01, prints hits. Re-run whenever the wallet list should refresh. NOTE: splice regex must end at `\n\),` (outer CTE close) not `\),` (matches inside VALUES).
- `runner.py` — polls Q01 on cadence, dedups (chain+token+hour) via `state/seen.json`, alerts via `~/ibkr_bot/notify.py` Pushover. Reads `DUNE_QUERY_ID` from `dune.env` (already set to 7868558).
- `dune.env` — has API key + Q01_ID (as DUNE_QUERY_ID) + Q02_ID.

**Dune MCP:** registered via `claude mcp add --scope user --transport http dune` — connected. Available in NEW Claude Code sessions, not the one that added it.

**Known param behavior:**
- Free-tier caps create_query at ~2/hour (429). PATCH is unlimited — always prefer updating existing IDs.
- edge_ratio in Q02 sits at ~0.5 for all wallets (math artifact of sold≈bought per wallet). Ranking is really by realized_gains magnitude — that's correct, don't over-engineer.
- Trino doesn't allow subqueries in LIMIT — hardcode integers.
- With tuned params, current signal is quiet (0 hits in last 24h). Loosen `min_distinct_wallets` or `min_agg_usd` at top of Q01 to probe historical hits.

**Why:** Robert asked about on-chain whale-tracking as a possible edge. He said "go ahead" to build end-to-end. This is a signal-research project, NOT wired to any broker.

**How to apply:** Next step is human decision on whether to loosen params, wire runner.py to launchd for 24/7, or backtest by widening Q01's `lookback` to historical windows. Don't wire to a broker until Robert says so — see feedback_paper_vs_real.md.

---

## 2026-07-02 pivot: Etherscan API replaces Dune for the "cabal watch" workflow

Dune's `dex.trades` misses MM/VC flow entirely (they don't trade as DEX takers). `erc20_ethereum.evt_Transfer` price-join drops long-tail tokens. Pivoted to Etherscan V2 API (multi-chain single endpoint) which has built-in CEX labels.

**New files:**
- `etherscan_feed.py` — main tool. Multi-chain (ETH/Base/Arb/OP/Polygon/BSC), reads `etherscan.env` (key: `DP78R3NEC8D91U95RC1ZM8A6CVT8XE3U8X`), flags dormant wallets, aggregates by token, dedups counterparties. `--hours N --sort {time,size} --limit N`.
- `curated_wallets.py` — hand-picked list (6 active after pruning) + `COUNTERPARTIES` dict for CEX labels + `counterparty_label()` helper.
- `etherscan.env` — API key.

**Curated wallets (post-prune):**
Active: Wintermute 1, Jump Trading, a16z, Justin Sun, Vitalik (vitalik.eth), Vitalik VB.
Pruned dormant: Wintermute 2/main/Negotiator/Multisig (46-895d idle), Jump 2 (128d), Paradigm (3058d!), Polychain (763d). Public label ≠ active trading wallet.

**Labeled counterparties (2026-07-02 verified via Etherscan Name Tag):**
Gate.io Deposit x2, Bitget Deposit, Coinbase 10, Binance 17, OKX 154, ByBit Deposit.
UNLABELED — flagged for investigation: `0xbcd986563ba93af5c6c92b16ab06e843a281a4a9` — 6 hits with Jump on BASED and LIT flows. Etherscan doesn't know who it is. Private OTC or MM counterparty.

**Known limitation:** with only 6 active wallets in list, feed is currently ~90% Jump Trading. Need to expand list with current-active MM addresses (they rotate monthly), Lookonchain-outed trader wallets, and known insider addresses. That's manual research.

**Dune queries are still on Robert's account (Q01/Q02/Q03/Q04) but superseded** — leave them for now, don't build on that path.

## 2026-07-02: Android integration onto the Alpaca page

Whale scanner now lives INSIDE the Alpaca tab of the Android app (v1.23):
- Refactored `WhalesScreen.kt` → exposes `WhaleSignalsBlock(vm, showAlpacaHook)` reusable composable.
- `PortfolioScreen` renders the block below Positions when `src.id == "alpaca_live"` (with `showAlpacaHook=false` since we're already on Alpaca).
- Standalone `Whales` tab REMOVED from `AppShell.kt`. The `WhalesScreen` composable still exists as the wrapper but is unreachable from nav.
- Bumped versionCode 22 → 23, `~/trading-dist/manifest.json` and `trading-latest.apk` updated with new sha256.

**Why:** Robert asked about on-chain whale tracking as a possible edge. I flagged that raw whale alerts are noisy (CEX shuffles, decoys) and that coordinated-accumulation by rolling-scored smart-money is the more robust signal. He said "do it" — meaning sketch the Dune query, not yet build live infra.

**How to apply:** Next step is Robert runs query 02 on Dune to seed the list, then query 01 to backtest. Do NOT jump to building websocket infra until the Dune backtest shows edge. If he asks for live wiring, that's Alchemy/QuickNode + a Python worker, not this scaffold. Not connected to any broker — this is a signal-research project, not a bot.
