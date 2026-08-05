---
name: IBKR three-arm $60 experiment
description: Live A/B/C comparison on IBKR account U24971994; arms test 50/50 vs 75/25 vs QC-substitute stop philosophies; deployed 2026-06-17
type: project
originSessionId: f79454ac-8313-46a2-b087-d3a15f0e06df
---
User-authorized $60 deployment (lifting the previous $20 total auto-fire ceiling). Three arms, $20 each, 1 share each (single-share is the only sizing without fractional permission).

**Positions opened 2026-06-17 in pre-market:**

| Arm | Symbol | Qty | Fill price | Notional | Philosophy |
|-----|--------|-----|------------|----------|------------|
| A — balanced 50/50         | F    | 1 | $14.41 | $14.41 | Moderate ATR (4.9%), steady momentum; equal weight to profit/risk |
| B — aggressive 75/25       | AAL  | 1 | $15.79 | $15.79 | Strongest momentum (+28% 30d, +52% 90d); looser trail to let winners run |
| C — QC top-5 substitute    | PINS | 1 | $21.22 | $21.22 | QC top-5 universe (AMD/NVDA/etc.) priced $170-540/share, can't fit $20 — PINS is closest momentum-driven cheap substitute |

Plus the pre-experiment SNAP position (1 share @ $5.26) from the original "$10 starter" — kept separate from the arms.

**Total IBKR exposure post-deploy:** $56.68 of $500 funded.

**Day-1 rule (today, Jun 17):** NO stops attached because of the active day-trade restriction. All four positions naked overnight. Max adverse case ~$57, realistic <$5.

**Day-2 rule (tomorrow, Jun 18):** Re-enable watcher with "no-same-day-sell" baked in. Watcher auto-attaches OCA brackets to all four positions using adaptive_stops engine but with per-arm overrides:

- Arm A (F): default adaptive (BASE_K=2.25), hard stop 10%
- Arm B (AAL): scaled trail ×1.25 (looser, let winners breathe), hard stop 12%, no profit-step
- Arm C (PINS): default adaptive, hard stop 8% (tightest), profit-step active

**Comparison metrics to track:** per-arm P&L, max DD per arm, fill+stop event log, time-to-recovery after drawdowns.

**Why:** User wanted live data on which stop philosophy actually performs best, not just backtest-derived intuition. Three different risk profiles running on real money simultaneously.

**How to apply:**
- Watcher must read arm labels from `~/ibkr_bot/transactions.csv` (column "reason" populated with "armA_balanced_50_50" etc.) to route to per-arm stop logic.
- Arm assignment is by symbol since each arm holds a single name. If we add more positions later, switch to a positions_to_arms map file.
- Dashboard A/B page can extend to four-way: prod / shadow / armA / armB / armC.

**Lifted constraint:**
- Previous $20 auto-fire ceiling has been replaced for this experiment with explicit per-arm authorization. Future autonomous trades still default to $20 total ceiling UNTIL user explicitly authorizes more.
