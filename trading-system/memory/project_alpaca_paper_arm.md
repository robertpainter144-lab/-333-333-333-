---
name: Alpaca Paper + Live A/B Arm
description: Env-driven Alpaca deployment (paper for testing, live for $50 real money) with mirror+broad arms, catalyst-aware stops, $5 account-DD baseline alert
type: project
originSessionId: d207120d-c6c4-4118-a720-bca459d749a8
modified: 2026-08-02T14:21:27.265Z
---
**RETIRED 2026-08-02.** Was on the VM as `quarki-paper-arm.service`; found HUNG ~26h (process alive + systemd "active" but tick loop stalled since Aug 01 12:23 UTC — watchdog never covered it and can't detect hung-but-active anyway). Superseded by D23 (verified winners go live directly, paper-first narrowed) and its ledger had only ~13 trades (below the n≥30 gate), so it gated nothing live. `sudo systemctl disable --now quarki-paper-arm`; removed from directive auditor SERVICES (`~/directive_auditor/auditor.py`) and from D14 CHECK in DIRECTIVES.md (VM + Mac copies) so no false "service down" alert. State/ledger left on disk at `~/alpaca_paper_arm_state/` (edge_ledger row goes stale, harmless). Reversible: `systemctl enable --now quarki-paper-arm` + re-add to auditor/D14. See [[project_bug_sweep_2026_08_02]] [[feedback_live_eligible_winners]].

Env-driven paper + live A/B rig for the IBKR strategy on Alpaca. Started 2026-07-01 (paper). Live deploy pending $50 ACH clearing (initiated 2026-07-02).

**Live plan:**
- $50 starting capital, grow-before-add mandate.
- Sizing: broad-only, 12 positions × $3 = $36 deployed, ~$14 cash buffer.
- Do NOT run mirror on live while IBKR still holds mirror-arm tickers (F, AAL, VERA, AMRX etc.) — that would double exposure.
- $5 account-DD rule = 10% floor on the $50 account → alert fires at $45 equity.
- Keys required in ~/.zshenv: `ALPACA_LIVE_KEY`, `ALPACA_LIVE_SECRET` from app.alpaca.markets → Home → Generate New Keys (Live tab).

**Env switch:** `--env paper|live` on CLI, or `ALPACA_ARM_ENV` env var. Live uses `~/alpaca_live_arm_state/` (isolated from paper).

**Location:** `/Users/quarki3/alpaca_paper_arm.py` + state at `/Users/quarki3/alpaca_paper_arm_state/`

**Two arms:**
- `mirror` — 8 positions, $10 each (~$80 total). Same tickers as live IBKR (AAL, AMRX, GRAB, SCHX, OCGN, F, VERA, OPEN). Apples-to-apples comparison against the live $500 bot.
- `broad` — 15 positions, $5 each (~$75 total). Wider liquid US universe (BAC, T, VZ, SCHW, C, WFC, GM, DAL, etc.). Bigger sample for statistical power.

**Shared stop math with live bot:**
- Imports `stop_manager.compute_trail_pct` and `stop_manager.compute_hard_drop` from `/Users/quarki3/ibkr_bot/`.
- Same `catalysts.json` — VERA gets 2× widening (16% trail / 19% hard) whether it's held on Alpaca paper or IBKR live.
- No OCA on Alpaca — stops enforced in-process on 1-min tick via peak-tracking software loop.

**Watcher:** now runs under launchd (`com.quarki3.alpaca-paper-watcher`, KeepAlive+RunAtLoad, wrapper `~/bin/run_paper_arm.sh` sourcing `~/.zshenv`), not a bare background process. It died silently 2026-07-03 at tick 1530 with zero exception text (disk-full write raised inside the tick loop's own unguarded except-handler `print()`) and sat unmanaged 17 days since nothing supervised it — fixed 2026-07-20: every file write wrapped non-fatal, tick-error print itself guarded, `_load_state` tolerant of corrupt JSON. See [[project_overhaul_2026_07_20]].

**Exit-philosophy parity with IBKR audit (2026-07-20):** 3×ATR trail floor added in `_compute_stops` (never trails tighter than 3x the symbol's ATR%); stop-outs in `_check_stops` now only submit during regular market hours (9:30-16:00 ET weekdays); no time-stop exists in this file (unlike `ibkr_bot/watcher.py`'s 7-day one) so none was added.

**Closed-trade ledger:** `alpaca_paper_arm_state/closed_trades.csv` (ts_open, ts_close, symbol, arm, entry, exit, pnl_pct, exit_reason) — this is the file promotion/n≥30 gate decisions read. A close is only logged if the sell order actually filled; a rejected sell (broker qty mismatch) leaves the position in state for retry rather than logging a phantom close. Known dust orphan: CCL broad-arm position desynced (state said qty 0.1707, broker only had 0.00002621, worth $0.0007) — untracked now, immaterial.

**CLI:** `--open mirror|broad|both`, `--watch`, `--status`, `--close mirror|broad|all`.

**State files:**
- `mirror.json`, `broad.json` — position ledgers with entry/peak/trail/hard/meta
- `equity.csv` — combined paper equity time series
- `stop_events.jsonl` — every stop-out with trigger, P/L, order id
- `closed_trades.csv` — the promotion-gate ledger (see above)
- `watch.log` / `/tmp/alpaca_paper_watcher.log` — watcher stdout (launchd now, not backgrounded)

**Why:** User wants to validate catalyst-aware stops + adaptive trail + broad picker without risking real capital. Per standing rule: real money only after paper proves the strategy.

**How to apply:** Any change to `stop_manager.py` or `adaptive_stops.py` propagates to paper automatically. Compare mirror-arm P/L to live IBKR P/L on same names to isolate broker/execution effects. Compare broad-arm to mirror to isolate universe/concentration effects. Check `closed_trades.csv` row count when asked about progress toward the n≥30 promotion gate.
