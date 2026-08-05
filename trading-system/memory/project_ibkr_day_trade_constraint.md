---
name: IBKR day-trade restriction (28-day window)
description: Account U24971994 does not qualify for day trading (Liquid Net Worth < $5k); Net Worth field locked until ~2026-07-15 due to recent upgrade request
type: project
originSessionId: f79454ac-8313-46a2-b087-d3a15f0e06df
---
IBKR live account U24971994 is under a Pattern Day Trader (PDT) tripwire because the declared Liquid Net Worth is < $5,000 on file.

**The rule:**
- A "day trade" = same-day buy AND sell of the same symbol.
- IBKR shows "Day Trades Left: N" (rolling 5-business-day window). User had 1 left as of 2026-06-17 04:11 ET.
- If 2+ day trades happen in 5 days → account flagged "closing-only" for 30 days. That means: existing positions can be closed, cash is freely withdrawable, but NEW BUY orders are rejected for 30 days.
- $500 funded; money never gets "locked" — withdrawals always work — but the trading strategy stalls.

**The fix is blocked for 28 days:**
- User tried 2026-06-17 to update Liquid Net Worth via Client Portal → blocked with "Changes to your Income and Net Worth are temporarily restricted due to your recent upgrade request. You will be able to make changes again in 28 days."
- Earliest unlock: ~2026-07-15. After that, update Liquid Net Worth > $25k → become PDT-qualified → restriction lifts.

**Strategy adaptation in the meantime:**
- **Never voluntarily sell same-day.** Stop brackets must only attach starting Day 2 of holding.
- Day 1 accepts naked downside. With $5-$20 position sizing, max single-name loss is trivial.
- Day-1 risk mitigations already in place: small sizing, entry_allowed regime check, no leveraged ETFs in IBKR universe.

**Why:** User cannot bypass the restriction for 28 days. Strategy adapted around it.

**How to apply:**
- **`day_trade_guard.py`** module exposes `safe_to_sell(ib, symbol)` — queries `ib.fills()` live, returns False if any BUY execution for that symbol falls on today's US Eastern calendar date. Fail-safe: returns False on any error.
- Watcher's three sell paths (auto-attach stops, time stop, profit step) all gate through `DTG.safe_to_sell()`. If False, action is skipped with a `skip_X (day_trade_guard)` log entry.
- The guard automatically becomes inert tomorrow because the buy timestamp shifts from "today" to "yesterday." No code change needed at the boundary — calendar-date based.
- Mark this as a TEMPORARY layer — after 2026-07-15 and Liquid Net Worth update, the guard can stay (zero cost) or be made configurable. Keep it as a safety belt.
- Existing positions safe to attach stops starting 2026-06-18: SNAP, F, AAL, PINS.
- Bracket records currently cleared via `_paused_until` field in stop_state.json.
- Manual override: any future `--force-sell` script bypasses the guard. The bot autonomously will never burn a day trade.

**Current state (2026-06-17 ~04:15 ET):**
- Watcher: stopped (`launchctl bootout`).
- SNAP: 1 share, avg $5.2627, no stop bracket attached (cancelled to avoid day-trade trigger).
- No other open orders or positions.
