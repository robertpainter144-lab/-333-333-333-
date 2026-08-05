---
name: $5 drawdown = mandatory review/repair
description: Live-account drawdown ≥ 3% of contributed capital ($5 floor) triggers a full assessment and repair action, NOT a hold. Revised from flat $5 on 2026-08-04 after the $450 deposit.
type: feedback
originSessionId: d207120d-c6c4-4118-a720-bca459d749a8
modified: 2026-08-04T19:54:35.194Z
---
Any time a live trading portfolio draws down **3% of contributed capital
(Σ deposits − withdrawals; $5 floor)** from peak — $15 at the current $500
book — I must:
1. **Review** — per-position P/L, distance to stops, thesis intact?
2. **Assess** — is the drawdown noise or signal? Which positions are the bleeders?
3. **Repair** — take a concrete action (tighten a stop, close a broken position, hedge, pivot). Not just "hold and monitor."

**Why:** Small live accounts are strategy-proving vehicles; unreviewed bleed
kills them. But the original flat $5 (set when the book was $50, i.e. 10%)
became 1% of the $500 book after the 2026-08-04 ACH — the book's ordinary
1σ day was ±$12 (INTC 41% at 5.9% daily vol), so a flat $5 forced reviews on
noise, the same "whipsaw tax" the 2026-08-02 profit-taking audit rejected.
Robert chose option 2 on 2026-08-04: percent-based trigger (3%/2% amber,
$5/$4 floors) PLUS the single-name cap in combined_mirror.py lowered
0.90 → 0.35. This is a **repair trigger**, not a stop-loss — it forces
judgment, not automatic selling.

**How to apply:**
- Amber at 2% of contributed ($4 floor; $10 now), violation at 3% ($5 floor; $15 now).
- Enforced in: `vm_watchdog.py` (`dd_thresholds()`), `directive_auditor/auditor.py`
  D6 check, Book.tsx DrawdownGauge — all read contributed capital from
  `/api/transfers/alpaca_live`. Canonical text: DIRECTIVES.md D6 (rev 2026-08-04).
- When the trigger fires, DO NOT answer "hold" — surface an assessment with a
  proposed repair. Applies to real-money accounts only, never paper arms.
- User has explicitly called this out as a violation once (2026-07-02) — do
  not make the same mistake twice.
