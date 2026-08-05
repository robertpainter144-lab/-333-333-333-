---
name: project-profit-taking-audit
description: "2026-08-02 workflow audit of live $50 book exits; verdict = no TP rule survived, shipped sell guards + shadow PT (D22)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 2e781da2-772d-4283-bbfe-b26f2f8d1604
  modified: 2026-08-02T14:15:38.333Z
---

2026-08-02: 17-agent workflow (wf_09d00363-2e6) audited the live Alpaca $50 book (7/13–7/31, 157 fills, 142 FIFO round trips, realized −$2.35) for missed profit-taking.

**Findings:** rebalance-only exits are anti-take-profit (median sell fill = 12th pct of prior-48h range); costliest class = 48h weight-oscillation (sell TEVA 7/27 → +11.6% pop → rebought at top, ~$0.70/3wk total); AMD 7/13 cohort gave back +6.97% peak to −$0.59. All backtested TP/trail rules (+$1.8–2.6 headline) FAILED adversarial verification — 84–97% of every delta was the single AMD crash; ex-AMD = noise; limit fills impossible (market-only notional orders); rule-sells rebought next tick.

**Shipped to combined_mirror.py** (VM + Mac synced, backup .bak-pre-pt-guards): (1) re-entry premium guard — rebuy <48h after full exit only if px ≤ exit×1.01; (2) 24h min-dwell guard on green full-exits (losers never dwell-held); (3) PT engine in SHADOW mode (arm +1%, 0.5% giveback from peak → state/pt_shadow.jsonl, zero orders). Config in risk_config.json (profit_take + sell_guards, hot-reloaded). Recorded as **D22** in DIRECTIVES.md: PT stays shadow until 8–12 wks / ≥150 entry events / clustered-bootstrap CI excludes 0 / no single event >50% of delta.

**Round 2 (same day, wf_f3232b9a-03b):** hard-stop family REJECTED — all 12 cells (avg/peak basis, 2–8%) ex-AMD negative (−$0.38..−$1.49), 11/12 stops later recovered (whipsaw tax; would cost $0.6–2.4/mo); mirror-faithful rebuy sim flips best cell to −$1.15. Eligibility gate shipped SHADOW-ONLY (elig_gate in risk_config: block 5d after last-5 whole-position closes lose >$0.10 or >0.5% notional) → state/elig_shadow.jsonl with also_blocked_by attribution; +$0.36 backtest was 103% one RDY order, but the 7/29 RDY rebuy-spam pathology is live-regime and uncaught by other guards. D22a added. Promotion bar: ≥3 symbols, ≥10 incremental events, positive ex-best-event, then ≥4wk paper A/B.

**Next steps:** review pt_shadow.jsonl + elig_shadow.jsonl after a few weeks against the promotion bars (D22/D22a). Ref [[project-lab-vs-live-fixes]], [[project-trading-dashboard]].
