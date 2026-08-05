---
name: IBKR live trading bot
description: $500 funded IBKR Pro account (U24971994); DARK since 2026-07-20 — see [[project_overhaul_2026_07_20]] for retirement/reactivation status
type: project
originSessionId: f79454ac-8313-46a2-b087-d3a15f0e06df
modified: 2026-07-20T17:08:31.234Z
---

**RETIRED/DARK as of 2026-07-20.** Account flat since 2026-07-09. Structural audit found commissions were 38% of losses plus exit-logic flaws. `com.quarki3.ibkr-dashboard` launchd job booted out (plist left in place); `ibkr-equity-logger` and `ibkr-sidecar` were already unloaded. IB Gateway deliberately left down. Status and reactivation checklist tracked in `/Users/quarki3/ibkr_bot/STATUS.md`: reactivate only when paper trading proves n>=30 trades with positive expectancy, position sizing raised to >=$150/trade, and IB Gateway relogin (needs user 2FA) is done. Everything below this point is historical context from the build/live-testing phase (2026-06-17 to 2026-06-22) and no longer reflects current operating state.

**Read-Only flag cleared 2026-06-22 ~12:50 PM ET.** Test BUY 1 F filled at $14.2150 (orderId 24). No Error 321. DO Gateway connection + API order placement now works unattended — Mac can be off / gateway running headless. Previously had to email api@interactivebrokers.com; cleared on its own (likely IBKR ticket processed over the weekend).

**Underwater-stop bug fixed 2026-06-22.** On the first successful watcher cycle (right after Read-Only cleared), the hard-stop logic placed STOP orders at `entry × (1 − hard_drop%)`. PINS (entry $21.40, stop $19.69) and SNAP (entry $5.26, stop $4.74) were both already trading at/below those levels → server fired both stops instantly for market-style exits. Lost ~$2 on each. Patch in `stop_manager.attach_stops`: now fetches current market price, and if `hard_stop_price >= current_price × 0.99`, skips the hard stop and places trail-only (TRAIL anchors at current and is safe to attach underwater). The skip is recorded in `bracket.components.hard_stop_skipped` for diagnosis.

**Daily day-trade cap enforced 2026-06-22.** Per-symbol DTG.has_buy_today already blocks same-day round-trips; added `DAILY_DAY_TRADE_CAP=1` global in `day_trade_guard.py`. `safe_to_sell` now also refuses if `count_round_trips_today(ib) >= 1` — defense against a 2nd unintended day-trade after a 1st has slipped through (e.g. via a server-side stop on a same-day buy). **Why:** user explicitly said prioritize this above all else, max 1 trigger per day. **How to apply:** any future automation that sells must call DTG.safe_to_sell first; cap=1 is intentionally tight given PDT exposure window through ~2026-07-15.

Live IBKR trading bot built tonight. Same "PoC10-style" $10-at-a-time reliability test pattern, but on real money instead of paper.

**Account**
- IBKR Pro, account `U24971994`, $500 USD funded.
- Live API on port `4001` (IB Gateway live mode — NOT paper).
- No market-data subscription → API quotes come from delayed feed + yfinance fallback.

**Bot location**
- `/Users/quarki3/ibkr_bot/main.py` — single-shot client using `ib_insync==0.9.86`.
- `state.json` + `transactions.csv` for run history.
- Defaults: 1 share of `F` (Ford, NYSE), GTC, outsideRth=True. Configurable via `--symbol`, `--qty`, `--primary-exch`.
- Safety cap: `MAX_ORDER_USD = 50` — anything over needs `--force-large`.

**First live order (2026-06-17 ~07:00 UTC)**
- orderId 12: BUY 1 F @ $14.44 GTC outsideRth=True → status `Submitted`.
- Will fill at next exchange session (pre-market 4 AM ET or regular open).
- IBKR `cashQty` fractional fails on BOTH limit AND market orders for this account (Error 10244, "Cash Quantity cannot be used for this order"). Root cause: account does NOT have fractional shares permission enabled. To enable: IBKR Account Management → Settings → Trading Permissions → Fractional Shares. Until then, use whole-share orders only.
- **IBKR cancel-order requires the original placing clientId.** `ib.cancelOrder()` from a different clientId fails with Error 10147 "OrderId X not found", even when `reqAllOpenOrders` clearly shows the order. Workaround: `IB.connect(clientId=<original>)` before cancelling. clientId=0 (master) does NOT bypass this on Gateway 1.98+.

**Stop system (built 2026-06-17 same night)**
- `stop_manager.py` — places OCA-grouped TRAIL + STOP on fills. Trail % comes from `qc_portfolio/adaptive_stops.py` (BASE_K × ATR × VIX × SPY × burst). Hard stop = `entry × (1 − min(10%, 2×ATR))`.
- `watcher.py` — launchd-supervised daemon (`com.quarki3.ibkr-watcher.plist`, clientId=29), ticks every 5min. Auto-attaches stops to new positions, profit-step at +2×ATR for qty≥2, time-stop at 7d with <1% progress, refreshes trail % if regime shifts >30%.
- `screener.py` — picks $1-25 liquid US names with steady 30d momentum (not blow-off). Used to source candidate list.
- `VERDICT.md` — full writeup, regenerate by re-running screener.

**Trade approval pattern (user explicit)**
- User says "mother bird" mode: approve every trade. Bot must NOT auto-fire new entries.
- Format: user replies "approve AAL PINS SNAP" → bot fires 1-share BUYs.
- Watcher only attaches stops to filled positions; never opens new ones.

**Known gaps (build next)**
- IBKR positions not yet shown on NiceGUI dashboard.
- No automated signal-driven entries (intentional — user approval required for now).

**Why:** User wanted a live-money reliability test of the framework before scaling up. $10/$14 starter losses are noise to him; the goal is to catch bugs (auth, order routing, position tracking) at trivial $ exposure.

**How to apply:**
- `python3.10 ~/ibkr_bot/main.py --status` for read-only account check.
- IB Gateway must stay running and logged in for the bot to connect — but GTC orders persist on IBKR's servers even if the gateway is offline.
- Do NOT add an exit/stop bot until exit logic is wired in. The F position currently has no automated downside protection.
