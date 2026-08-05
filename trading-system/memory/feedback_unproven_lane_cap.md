---
name: feedback-unproven-lane-cap
description: D34 — no governor lane above 35% live weight until it passes the D7 gate; congress was at 99% on a 6-day record
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 42d60416-3067-4ebc-9456-b9eeb7cb284c
  modified: 2026-08-04T21:14:20.713Z
---

2026-08-04: Review found the congress lane holding 99% of the live Alpaca book
on a 6-day paper record (verdict UNPROVEN, gate stats null, paper book 42%
INTC). Robert first leaned "let it ride / just optimize for profit", then
delegated the call on expected-profit grounds; the decision was the cap, not
the streak ([[user_allocation_style]], [[project_congress_tracker]]).

**Why:** with ~5 daily observations, edge is indistinguishable from luck;
99% concentration maximizes the best case, not expected compound growth
(variance drag). Cap keeps D19 ride-the-winners up to 35% while honoring D9's
proof requirement — resolves their conflict permanently.

**How to apply:** D34 in DIRECTIVES.md. `strategy_governor._cap_weights`
enforces it on every publish (held/gated included; kill-switch freeze
exempt). `governor_config.json`: `unproven_cap` (0.35), `proven_lanes`
(lanes that passed the D7 gate — currently empty). OPEN: mirror ledgers log
order deltas only, no realized per-trade P/L, so the D7 gate cannot score
any lane yet — instrumentation fix (cost basis + pnl_pct on sells in
qc/congress/patent/autopilot mirror ledger writers) is required before any
lane can earn cap removal. When Robert says "optimize for profit" he means
the goal, not "remove risk rules" — frame recommendations as expected
compound growth vs streak-chasing and he accepts the risk-managed option.
