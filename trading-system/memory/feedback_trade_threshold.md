---
name: $20 total IBKR auto-fire ceiling
description: Bot may freely use up to $20 of TOTAL IBKR exposure (sum of all positions + pending orders); anything that would push total over $20 requires push-notification approval (ntfy since 2026-07-28)
type: feedback
originSessionId: f79454ac-8313-46a2-b087-d3a15f0e06df
---

For IBKR live trades on account U24971994 ($500 funded, "mother bird" mode):

- **$20 is an AGGREGATE budget, not a per-trade cap.** Total = sum of (open positions market value) + (pending orders notional) + (proposed new trade notional).
- **If projected_total ≤ $20**: bot fires without approval.
- **If projected_total > $20**: send ntfy push (see reference-ntfy-alerts), wait for explicit "approve <ticker>" reply before placing.

**Why:** User explicitly clarified 2026-06-17 — the $20 is the bot's autonomous-mode risk wallet for now, not a green light to stack repeated $20 trades. He framed it as "mother bird pushing kids out of the nest" — keep total exposure trivial until trust accumulates.

**How to apply:**
- BEFORE every IBKR `placeOrder` call: query IBKR for current positions × current price, plus current pending orders × limit price, plus proposed_qty × proposed_price = projected_total.
- If projected_total ≤ $20: place directly, log to transactions.csv.
- If projected_total > $20: call `notify.propose_trade(...)` from `~/ibkr_bot/notify.py`, write proposal to `~/ibkr_bot/pending.json`, do NOT call `placeOrder`.
- User reply "approve <ticker>" in chat is the only way over-ceiling trades fire.
- Existing SNAP order id=18 ($10 limit) consumed up to $10 of the $20 wallet on placement.
- The Alpaca-side bots (qc_portfolio, qc_portfolio_test) are paper-money only — this gate is IBKR-specific.

**Profit-taking is already covered** by the existing adaptive stop stack (trail + hard stop + profit-step + time-stop). No new code needed for "take profit" behavior — the watcher daemon handles it.

**Notification transport:** ntfy.sh since 2026-07-28 (Pushover trial expired). Helper at `~/ibkr_bot/notify.py` reads `NTFY_TOPIC` from env or `~/ntfy.env`. When keys are missing the notifier is a silent no-op so the bot keeps working in dev mode.
