---
name: SPY Overlay Strategy (IBKR, currently dry-run locked)
description: SMA200 tactical strategy for IBKR — 1% hysteresis band, flip-only trading, crash alerting via Pushover, hard-locked to dry-run pending 3-5 clean sessions + explicit user approval. NOT currently placing live orders.
type: project
originSessionId: e435dce1-ead3-4c05-b243-d5ebdca4e52d
modified: 2026-07-20T17:20:58.165Z
---
## Strategy
SMA200 tactical, deployed live on IBKR account U24971994 starting 2026-06-23.

**Logic (updated 2026-07-20 with hysteresis)**
- `BULL`  SPY > SMA200 * 1.01  → hold SCHX (Schwab US Large-Cap, >0.99 correlation to SPY)
- `BEAR`  SPY < SMA200 * 0.99  → hold SHY (1-3y Treasuries)
- inside the ±1% band → hold whatever regime was last active (hysteresis, persisted to `spy_overlay_hysteresis_state.json`)
- Trades fire ONLY on an actual regime flip (or the very first run) — never rebalances on price drift. Raw SMA200 cross had flipped 78x 2011-2026 (15x in 2022 alone); the band exists to cut that whipsaw.

Why SCHX not SPY: IBKR can't trade SPY in $170 budget (1 share = $744). SCHX at $29 lets us hit budget with whole shares.

Why not the full Combined strategy (SMA200 + leveraged sleeve): IBKR account's Complex/Leveraged ETP permission is locked. "Incompatible Objectives / Unavailable." Income/Net Worth and Investment Experience fields are frozen for 28 days (cooldown from 2026-06-23 Read-Only lift). Even after the cooldown, Liquid Net Worth < $5k likely won't pass IBKR's leveraged-ETP suitability matrix. **TQQQ leg cannot be added** until account is funded to $10k+ liquid and cooldowns expire.

## Backtest validation (14yr 2011-2026)
| Metric | SPY ref | SMA200 tactical | Delta |
|---|---|---|---|
| Sharpe | 0.91 | 1.02 | +0.11 ✓ |
| MaxDD  | 33.72% | 20.91% | -12.8pp ✓ |
| CAGR   | 14.82% | 11.80% | -3.02pp ✗ |

Also tested 2000-2011 (dot-com + GFC): CAGR 3.02% vs SPY 0.94%, Sharpe 0.34 vs 0.15, DD 21% vs 55%. **Genuinely robust across regimes.**

## Files
- `/Users/quarki3/qc_portfolio/spy_overlay_ibkr.py` — single-shot daily bot
- `/Users/quarki3/qc_portfolio/spy_overlay_bot.py` — earlier Alpaca-paper version (unused)
- `/Users/quarki3/qc_portfolio/spy_overlays_backtest.py` — 14yr backtest (4 overlays + SPY)
- `/Users/quarki3/qc_portfolio/spy_overlays_2000s.py` — 2000-2011 regime-robustness test
- `/Users/quarki3/qc_portfolio/spy_combined_backtest.py` — SMA200 + leveraged sleeve combined (for when TQQQ unlocks)
- `/Users/quarki3/qc_portfolio/spy_overlay_ibkr_state.json` — persisted trade history (written only on real --execute)
- `/Users/quarki3/qc_portfolio/spy_overlay_hysteresis_state.json` — persisted hysteresis regime, updated every run regardless of dry/execute
- `/Users/quarki3/qc_portfolio/spy_overlay_ibkr.log` — strategy log
- `/Users/quarki3/qc_portfolio/spy_overlay_dryrun.log` — intended-action log written during dry-run (no orders placed)

## Schedule
- launchd job `com.quarki3.spy-overlay` exists at `/Users/quarki3/Library/LaunchAgents/com.quarki3.spy-overlay.plist` (fires daily 7:45 AM Mountain) but is **currently unloaded** — do not load it until deployment is approved.
- Logs: `/tmp/spy_overlay.log` + `/tmp/spy_overlay.err` (launchd) — not currently active.

## Current state (2026-07-20)
**NOT live.** Script is hard-locked to dry-run (`DRY_RUN_LOCKED = True` in `spy_overlay_ibkr.py`) regardless of CLI flags — `--execute` is silently downgraded to dry-run while the lock is on. No orders placed, launchd job not loaded.

Pre-deployment fixes applied 2026-07-20 (see [[project_overhaul_2026_07_20]]):
1. 1% hysteresis band around SMA200, persisted regime state, replacing the raw cross (78 flips 2011-2026).
2. Flip-only trading — plan only computed on an actual regime flip or the first-ever run, never on drift.
3. Top-level crash handler: logs traceback + sends Pushover alert (creds `~/ibkr_bot/pushover.env`) + exits nonzero. Fixes the Jul-8 silent-death bug (bare START lines, no alert, no nonzero exit).
4. Dry-run intended-action logging to `spy_overlay_dryrun.log`.

Default budget bumped from $170 → **$460** (new allocation plan). Verified: py_compile clean; live test run hit IB Gateway offline (expected — IBKR is dark, see [[project_ibkr_bot]]) which exercised the crash-alert path for real (logged CRASH, sent Pushover, exit 1) while regime/hysteresis logic computed and logged correctly beforehand (BULL, band $686.92-$700.80). The dry-run trade-plan logging path still needs a live Gateway connection (to read positions) to be exercised — happens naturally on the first real dry-run session.

**Path to going live:** 3-5 clean dry-run sessions once Gateway is back up + explicit user approval + user 2FA gateway login → then flip `DRY_RUN_LOCKED = False` and load the launchd job.

**Why:** User's stated goal pivoted from "test multiple strategies" to "beat SPY buy-and-hold" after discovering the production qc_portfolio rotation was destroying capital (-26% CAGR vs SPY +14.82%). SPY overlay (SMA200 tactical) was chosen as the realistic deployable alternative — beats SPY on Sharpe and DD, marginally trails on CAGR. Full Combined strategy (with leveraged sleeve) waits on permission unlock.

**How to apply:**
- Check current state (always dry-run while locked): `python3.10 ~/qc_portfolio/spy_overlay_ibkr.py`
- Change budget: `--budget 250`
- The day-trade guard from `ibkr_bot/day_trade_guard.py` is integrated; will refuse to sell SCHX during any day-trade lock window even if regime flips.
- When TQQQ becomes tradable (Complex/Leveraged ETP permission grant), upgrade to the Combined strategy by porting logic from `spy_combined_backtest.py` into the live bot.
- Do not flip `DRY_RUN_LOCKED` or load the launchd job without explicit user approval — see [[feedback_paper_vs_real]].
