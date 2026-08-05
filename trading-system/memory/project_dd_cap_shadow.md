---
name: project-dd-cap-shadow
description: $20 drawdown-cap + rebuy shadow experiment on VM (quarki-ddcap.timer); zero orders; review after 8 weeks or first stop-out
metadata: 
  node_type: memory
  type: project
  originSessionId: 42d60416-3067-4ebc-9456-b9eeb7cb284c
  modified: 2026-08-04T20:53:10.418Z
---

2026-08-04: Robert asked whether the live Alpaca book could cap drawdown at
~$20 and rebuy "at the bottom of the dip or on the way back up right where we
sold". Not deployed live — D22a rejected 2–8% hard stops (whipsaw tax on
mean-reverting names, $20 = 4% of the $500 book is in-band), the cap is soft
under overnight gaps (INTC ~35% of book), and cash-account T+1/GFV limits
round-trip speed ([[project_profit_taking_audit]], [[feedback_paper_vs_real]]).

Shadow lane instead (zero orders): `~/scanner/dd_cap_shadow.py` on the VM,
`quarki-ddcap.timer` every 5 min, simulating two variants vs live equity:
- `reentry_at_exit` — stop all at DD ≥ $20 from variant peak, rebuy when live
  equity recovers to the exit level
- `reentry_on_bounce` — same stop, rebuy at +1% off the post-exit trough
Both model 7.5 bps/side costs and a 20h re-entry lockout. State:
`scanner/state/dd_cap_shadow_state.json`; ledger: `dd_cap_shadow.jsonl`
(STOP_OUT / REENTRY / curve rows).

Review after ≥8 weeks OR the first real stop-out event. Promotion bar =
D22a: fresh verification, whipsaw tax vs crash payoff quantified. Expect the
whipsaw variant costs to dominate unless a genuine crash lands in-window.
