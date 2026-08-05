---
name: project-mirror-sweep
description: 2026-08-02 web sweep for mirrorable top strategies; 6 ETF-mirror catalog rows added (DBMF/KMLM/CTA/RSBT + HFND/QIS benchmarks)
metadata: 
  node_type: memory
  type: project
  originSessionId: a83a22e7-c7a1-4668-8046-e3deea59510d
  modified: 2026-08-02T14:06:00.670Z
---

2026-08-02: Robert asked for a workflow sweep of "top prop desk / Wall St quant bots to mirror." 24-agent workflow (8 lenses → 66 candidates → 15 adversarially verified → 10 survivors). Core finding: no real prop bots are publicly mirrorable; the additive discoveries are **managed-futures replication ETFs — the stack's first negative-equity-beta return streams** (everything else Robert runs is long equity beta).

Added 6 single-ticker rows to the catalog ([[project-autopilot-mirror]] mechanism, `~/scanner/state/autopilot_catalog.json` on VM, struck NAV 100 on 2026-08-02, backup `.bak-20260802`):
- **DBMF_Mirror** (#1): SG CTA replication, 9.15% ann. since 2019, beta -0.18. Realistic 7-9% CAGR / Sharpe 0.4-0.6 / DD -20%.
- **KMLM_Mirror** (#2): trend, no equity futures, beta -0.31, purest crisis hedge; 2022 +30% but 2023-25 flat/negative.
- **CTA_Mirror** (#3): actual Altis signals; +7.5% ann. since 2022 but +1.9% TTM — racing vs DBMF is the point.
- **RSBT_Mirror** (#4): bonds+trend stacked; ~0% since inception, must earn a sleeve on the scoreboard.
- **HFND_Benchmark**: hedge-fund-industry hurdle (+8% ann.); arms that can't beat it don't deserve slots.
- **QIS_Cautionary**: negative benchmark, -51% 1-yr, never buy.

Rejected: Collective2/eToro copy-trading (fees/survivorship or US-unavailable), Striker futures programs (capital far beyond $500 IBKR), RSST/QAI/HFGM (fee-laden equity beta already owned free), MFUT (fund-closure risk, $41M AUM).

Same day, Robert asked for the mirrors in the rotation ("more top options = better"): **promoted DBMF_Mirror + KMLM_Mirror 50/50 into the autopilot lane** via promote_pilot.py (lane was idle since the 7/27 InTheMoney demote). Governor scores it as autopilot_PAPER; 0% real weight until the D7 gate. CTA/RSBT keep racing as catalog rows — swap promotion if their curves lead. App placement: rows auto-surface in the owner Catalog section (/api/catalog → Terminal owner-injection + Kotlin app), no UI change needed.

Watch item: a buy-and-hold 50/50 blend closes few trades, so the n≥30-closed-trades gate may be structurally slow for this lane — revisit gate fit after ~2 weeks of forward marks. Direct live sleeve purchase (pre-proven-record argument) remains undecided/not done. Scanner venv on VM is `/home/ubuntu/quarki-venv/bin/python` (bare python3 lacks yfinance).

**D24 machinery (2026-08-02 evening):** Robert rejected quarterly re-sweeps — wants cutting-edge at all times. Shipped: (a) `~/scanner/catalog_leader_check.py` + `quarki-leadercheck.timer` on VM (weekdays 22:17 UTC) — AUTO mode swaps the PAPER lane to any catalog row with a 3-day >2pp sustained lead (5-day cooldown, ntfy every swap; real money still D7-gated; config `state/leader_check_config.json`, revert to `"suggest"` to disable — note Robert's "turn on auto mode" msg was actually the CLI toggle, auto-swap kept deliberately, flagged to him); (b) weekly cloud routine `weekly-mirror-sweep` (trig_01CvLFv8FPSKJC8bCJNf34Ur, Mon 12:07 UTC) delta-sweeps web vs BASELINE.md and commits to private repo `Quarki33/mirror-sweeps` — review the report in-session weekly; (c) Crenshaw_Tracker wired from House PTRs, Monday auto-refresh (current basket = 1 unpriceable ticker USOU, activates on next parseable buy); Mullin blocked — needs a Senate eFD source (not built).
