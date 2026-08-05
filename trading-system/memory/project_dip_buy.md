---
name: project-dip-buy
description: "Index dip-buy lane (dad's \"buy QQQ/SPY on sale\" approach) SHELVED 2026-08-03 — regime-only beat every dip variant OOS; SPY Overlay is the surviving live form"
metadata: 
  node_type: memory
  type: project
  originSessionId: c3b23871-542b-46f6-aca2-ab93d8aab4ba
  modified: 2026-08-03T19:15:18.300Z
---

Systematization of Robert's dad's approach ("indices always go up, buy them
on sale"; sub-$5 leg discussed but not backtested — lottery-ticket framing
flaw flagged). Backtested 2026-08-03 at `~/dip_buy/` (Mac, not VM):
`dip_backtest.py` grid — SMA200 regime gate × 5 pullback entries (RSI2<10/5,
3%/5% off 20d high, 3 down closes) × 3 exits (RSI2>70, >SMA5, 10d time stop),
next-open fills, 1bp slippage, IS ≤2017 / OOS 2018– splits.

**SHELVED — SHIP=false.** No combo cleared Sharpe 1.0. Regime-only (long
above SMA200, cash below) beat every dip variant OOS on both tickers (SPY
0.87 / QQQ 0.92 vs best dip 0.53 / 0.72); dip-timing subtracts, same shape as
[[project-value-screen-v2]] round 2. RSI2 edge decayed post-2018 (SPY IS 0.87
→ OOS ~0.33). No round 2 without a structurally new signal (breadth, vol
term structure, flows) — parameter space is exhausted.

Survivors: regime-only = the live [[project-spy-overlay]] (fresh OOS
confirmation, ~40% shallower drawdowns than buy&hold); pullback entries keep
+0.3–1.1%/trade as entry-timing polish for buys happening anyway. Verdict at
`~/dip_buy/VERDICT.md`; full grid in `~/dip_buy/results/grid.csv`. No capital
lane ever created.

**Sub-$5 leg shipped 2026-08-03** as owner-private paper lane on the VM:
`scanner/sources/sub5_watch.py` (6h cadence). NO picking logic — Robert/dad
hand-edit `state/sub5_watchlist.json` (add `{"TICKER": {"added", "thesis"}}`;
set `"status": "closed"` to sell); entry struck at latest close, hard gate
≤ $5.00, $100 virtual per pick; closes → `state/sub5_ledger.jsonl`; edge row
`sub5:watchlist` scored with standard PROVEN gate (n≥30). D1: stripped from
guest `/api/edge`, served on owner-gated `/api/catalog`. Paper-only until
PROVEN; promoted version would trade Alpaca (IBKR friction floor).
