---
name: Paper for testing, IBKR for deployment — never mix
description: Real money (IBKR) is never for experimenting; only deployed after paper data has proven a strategy works. All A/B tests, philosophy comparisons, new strategies live on Alpaca paper or virtual ledgers.
type: feedback
originSessionId: f79454ac-8313-46a2-b087-d3a15f0e06df
---
User explicitly corrected (2026-06-17) a category error I'd been making.

**The rule:**
- **Live testing = paper money only.** Alpaca paper, shadow engines (qc_portfolio_test), virtual crypto ledgers (qc_portfolio_crypto). All "experiments," "A/B tests," "philosophy comparisons" live here.
- **Real trading = IBKR.** Only happens after paper data has *already proven* a strategy works.
- Real money never tests an idea. It deploys an idea that's already passed testing.

**Why:** User framed it as "we are only entering into real trading once we have enough data to make sure we're safe." Real money is for execution of validated strategies, not for learning what works. Even tiny ($10, $20) real-money positions are still real money and shouldn't be used as test beds — that conflates "small dollar amount" with "okay to deploy without evidence."

**How to apply:**
- When proposing any new strategy/philosophy/A/B, default to Alpaca paper with meaningful sizing (e.g. $5k–$15k per arm), not IBKR.
- Before suggesting any new IBKR deployment: confirm the strategy has paper data showing it beats a baseline by some threshold (Sharpe > X over N sessions, max DD within Y%, etc.).
- The current IBKR positions (F, AAL, PINS, SNAP from 2026-06-17) were placed before this rule was articulated — they stay where they are for now (closing burns the day-trade allowance), but they're treated as legacy real-money, not as ongoing experiments.
- The three-arm philosophy test (50/50 vs 75/25 vs QC-style) should be re-run on Alpaca paper with QQQM/AMD/SOXL at proper sizing, NOT on IBKR.
- Memory `project_three_arm_experiment.md` reflects the original (mis-framed) IBKR test. Treat that as historical context, not as a template for future tests.
