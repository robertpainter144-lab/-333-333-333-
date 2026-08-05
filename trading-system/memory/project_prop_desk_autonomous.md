---
name: Prop-Desk Autonomous Mode
description: Prop-desk running fully unattended — expanded strategy roster, strict promotion gate, self-healing watchdog, silent-until-promoted wakeup loop
type: project
originSessionId: c777b35a-8784-4ed8-b477-cb1bcb76d2f3
---
Switched into autonomous mode 2026-07-10 11:42 after Robert asked me to be "more agentic" and stop prompting every few minutes.

**Expanded strategy roster (6 variants):**
- momentum (2m window, 2.0σ entry)
- momentum_5m (5m window, 1.8σ entry)
- momentum_15m (15m window, 1.5σ entry)
- mean_reversion (RSI15)
- breakout (15m high + ATR expansion)
- whale_primary (score ≥ 3.5 standalone entry)

**Strict promotion gate (all 4 must pass on same strategy):**
- n_trades ≥ 30
- rolling mean pnl ≥ +0.05% per trade
- rolling Sharpe ≥ 1.0
- late-period mean ≥ +0.02% (last 20% of trades still positive — no early-luck bias)

Only when a single strategy passes all 4 does auto_tune write `ready_for_live: true` + the strategy name into `state/auto_tune_config.json`.

**Self-healing watchdog** (sources/watchdog.py, 60s cadence):
- Verifies prop_state.json fresh <2min
- Verifies auto_tune_config.json fresh <10min
- Verifies dashboard :8080 responds
- Writes state/health.json for the UI
- Emits [unhealthy] live activity on failure

**Silent-until-promoted wakeup loop:**
- Every 1h: check health + ready_for_live + trade count growth
- Reports to user ONLY on: outage (with auto-restart), READY_FOR_LIVE moment, or genuine 2h+ stall
- Otherwise stays silent — user is done prompting for a while

**Cost model context:**
- Alpaca crypto roundtrip: 0.35% estimate. Cost gate (auto-tuned) at 0.70% (2×) default.
- Strategies below 0.5% mean edge get killed by the brain within 10 trades.
- The promotion gate (0.05% mean pnl) is deliberately modest — even that with n>=30 and Sharpe>=1 would be a genuine, if small, edge.

**Ledger reset points:**
- 2026-07-10 11:36 first reset (old 194-trade bleed archived to prop_ledger.old.jsonl).
- 2026-07-10 11:42 second reset for expanded 6-strategy roster.

**Fresh starting point (11:42 MDT):** 0 trades, all 6 strategies enabled, cost gate 0.70%, no promotion. From here the system runs itself.

**Why:** Robert said "be more agentic and autonomous... get me to a point where I no longer need to work on this because it's working on itself." The brain now has more strategies to try, stricter criteria for calling one "profitable," self-healing infrastructure, and a wakeup loop that goes silent unless something worth reporting happens.

**How to apply:** DO NOT poke this unless the wakeup surfaces an issue. If Robert asks for status, hit /api/prop_desk/state and evaluate.py. If a strategy hits READY_FOR_LIVE, the next hourly wakeup will surface it automatically.
