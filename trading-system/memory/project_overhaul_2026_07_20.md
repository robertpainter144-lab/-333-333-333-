---
name: 2026-07-20-stack-overhaul-reliability-layer
description: "Disk-full root cause, scanner+watchdog under launchd, IBKR dark, governor promoted patent 92%, swarm+workflow overhaul in flight"
metadata: 
  node_type: memory
  type: project
  originSessionId: dfac99cb-9e1b-4f82-8f43-5249b08a8a3a
  modified: 2026-07-20T17:21:38.054Z
---

**Root cause of the July stack-wide silent deaths: disk 100% full (383 MB free).** poc10 logged ENOSPC since Jul 4; IBKR watcher, Alpaca paper watcher (Jul 3), scanner (Jul 17) all died and stayed dead. Freed to 2.2 GB (gradle/npm/browser caches). Big reclaims left for user: Epic Games 36G in /Users/Shared, Desktop 37G.

**Reliability layer installed 2026-07-20:**
- `com.quarki3.scanner` — scanner now under launchd KeepAlive via `~/bin/run_scanner.sh` (was running bare; died Jul 17 leaving live $50 unmanaged 3 days).
- `com.quarki3.stack-watchdog` — `~/bin/stack_watchdog.py` every 5 min: re-bootstraps unloaded jobs, kickstarts stale ones (never SIGKILL per [[never-kill-9-the-ng_dashboard]]), disk ≥1 GB check, Alpaca live $5-DD Pushover alert. Log at ~/trading-data/watchdog.log.
- poc10-bot unloaded (zombie since Jul 4; plist kept).

**Forensics (4-agent sweep):** IBKR −$17.30 = −$6.60 commissions (IBKR charged **1%/side** — not published schedule; reconcile vs Flex statement) + ~−$16 stop-outs (VERA −9.19, RDY −5.44, OCGN −1.41) + flat Jul-9 liquidation. Code audit: momentum entries with mean-rev exits (7d time-stop), 8% trail cap = 1.2–2×ATR whipsaw, pre-market market exits, no commission modeling. Alpaca paper arm (zero commission) also lost −3.4% vs flat SPY → **no strategy has demonstrated edge; nothing ever passed the auto_tune promotion gate.**

**Governor promotion REVERSED same day:** the 92% patent weight was computed off a FROZEN Jul-17 reading (+2.063; true patent pnl −0.31%). Workflow (9 agents, 4/4 refuters confirmed) findings: patent = sector beta (catalyst scores all 0.0, picks 24d stale) + runaway-buy bug (get_positions→{} on API error, 243 orders/$486, book 6x design); qc_portfolio's +86%→+5% round-trip NEVER happened (phantom $93k equity print 2026-07-07 poisoned peak → false 44% DD → frozen at exposure 0 — real record +5.3%/44d, +1.30%/event n=69, only positive forward record in stack); SOXL backtest has lookahead (true Sharpe 0.82 not 1.211); SPY overlay survived refutation → deploy-live-small at $460 pending fixes + dry-run + explicit user approval + 2FA. FIXES APPLIED 2026-07-20: weights reset to equal blend; combined_mirror + patent_mirror get_positions FAIL CLOSED; governor staleness guards; qc phantom peak reset (54,308, exposure 1.0) + daemon restarted. Edge Ledger live: 15 strategies, 0 PROVEN, qc only PROMISING. **CORRECTION (user, 2026-07-20): the only active capital is the ~$49 Alpaca live.** The "$482 idle IBKR cash" was an assumption from the Jul-9 `exit_account` liquidation proceeds; user says the IBKR money is not active (likely withdrawn — gateway down, unverifiable from here). SPY-overlay-on-IBKR decision and $5-DD carve-out are MOOT until new capital is funded. Path: paper proves n≥30 (qc_portfolio nearest, ~4-6 wks) → fund winner via Alpaca (commission-free), not IBKR.

**In flight:** swarm tasks a145394a (ibkr exit fixes), afa57294 (paper arm revival + closed_trades.csv), ebecce84 (IBKR sidecar retire); workflow `capital-allocation-audit` (wf_e87e3272-109) validating patent_cliff / SOXL / SPY-overlay / qc_portfolio → allocation plan for $482 idle IBKR cash.

**fdc33760 (Edge Ledger) done 2026-07-20:** `scanner/sources/edge_ledger.py` aggregates prop_desk, qc_portfolio (real FIFO round-trips from transactions.csv), qc/patent/combined mirrors, alpaca_paper_arm (mirror+broad arms), and hardcoded backtest-only claims (SOXL_regime, ES_mean_rev, spy_overlay — n=0, unverified) into `state/edge_ledger.json` every 15 min, using auto_tune.py's exact PROVEN gate (n>=30, expectancy>=0.05%, sharpe>=1.0). `GET /api/edge` on ng_dashboard serves it read-only. First run: 15 strategies tracked, 0 PROVEN, qc_portfolio is the only PROMISING one (n=85, expectancy +0.49%, sharpe 0.09 — sharpe too low to promote). Confirms the forensics finding: nothing has proven edge yet.

**How to apply:** check `~/trading-data/watchdog.log` + `swarm_results/completed/*.json` for outcomes; doctrine now lives in trading-stack/CONTEXT.md "SCOPE & GOALS" (evidence before capital, $150 IBKR floor, one scoreboard).
