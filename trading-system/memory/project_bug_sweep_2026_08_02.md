---
name: project_bug_sweep_2026_08_02
description: Ultracode trading-stack bug sweep (2026-08-02) — findings + which fixes applied/deployed
metadata: 
  node_type: memory
  type: project
  originSessionId: 16197afd-b2b4-48ad-8801-f7469799efdf
  modified: 2026-08-03T00:20:11.993Z
---

Ultracode parallel bug sweep of the whole trading stack on 2026-08-02. 12 finders
(9 module + 3 cross-cutting) over local working copies + 1 agent over the
trading-stack GitHub deploy repo, then adversarial verify on every high.

**Result:** 97 raw → 26 high CONFIRMED, 5 refuted, 66 med/low (unverified leads).
Full report: `/Users/quarki3/trading-stack/BUG_SWEEP_2026-08-02.md`.
Dominant theme: money-guards/safety-nets that fail silently or fail open.

**DEPLOYED to VM 2026-08-02 ~14:10 UTC** (via edits to the VM's CLEAN copies, not
the drifted Mac repos; originals in *.bak-<ts>): ng_dashboard (api.py + owner_catalog.js
moved to private/) + quarki-dashboard restarted — privacy leak verified closed
(/static/owner_catalog.js now 403, was 200); scanner (prop_desk.py) + quarki-scanner
restarted; ibkr_bot main.py/notify.py copied (non-git, DARK, no restart). trading-companion
risk.py NOT deployed (isolated stack, not on VM). VM autopush commits ng_dashboard+scanner
to GitHub automatically. IMPORTANT: Mac /Users repos are DRIFTED dev copies with foreign
uncommitted changes — never `git add -A`/push from Mac; the VM is authoritative and
autopushes itself.

**Fixes APPLIED 2026-08-02 (edited on Mac working copies, then deployed to VM as above):**
1. ibkr_bot/main.py + notify.py — D5 $20 aggregate ceiling now actually enforced
   (was dead code); fails closed; added --approved flag.
2. scanner/sources/prop_desk.py — live orders blocked unless auto-tuner ready_for_live
   (was advisory-only); paper unaffected.
3. ng_dashboard/api.py — D1 leak: owner_catalog.js moved out of public /static to
   ng_dashboard/private/; _OPEN_PATHS excludes /static/owner_*. LIVE leak — deploy first.
4. ng_dashboard/api.py — paper/broker portfolio fail-open $0/-100% fixed → stale placeholder.
5. trading-companion/backend/app/risk.py — reject non-positive/non-finite price*qty;
   daily cap rolls over on ET trading day, not UTC midnight.

**DEPLOYED batch 2 (VM, 2026-08-02 ~21:02 UTC, scanner restarted):** 3 fail-open
mirrors (qc_mirror/congress_mirror/autopilot_mirror get_positions → None on error +
tick skips cycle, matching hardened combined_mirror — closes runaway-buy class) +
signals.py dedup eviction (age-based insertion-ordered dict instead of sorted()[-5000:]
which silently evicted cex/bridge/gainer prefixes → whale-score double-count). All
compile + logic-verified; seen.json self-heals as keys churn.

**DEPLOYED batch 3 (VM, 2026-08-02 ~21:18 UTC, scanner restarted):** governor
kill-switch freeze fix (strategy_governor.py + combined_mirror.py). Kill switch (-8%)
now republishes current composition with fresh ts + frozen:true instead of going
silent; combined_mirror._governor_frozen() holds all trading when frozen (desired={},
D21 freeze=hold), stale governor file ignored (dead-governor safety), auto-clears on
recovery. Fixed the equal-blend force-rebalance-mid-drawdown bug. Validated with a
5-case deterministic contract test against real deployed code on the VM.

**DEPLOYED batch 4 (VM, 2026-08-02 ~21:27 UTC, scanner restarted):** prop_desk
phantom-close fix. _get_position returns _FETCH_ERR sentinel (network/non-404) vs None
(genuine 404); holds snapshotted before manage(); new _sell_completed() only closes on
real order id / signal_only / genuine no_pos, else restores the hold and retries (no
orphaned position, no phantom close in the D7 ledger). Validated with 4-group
deterministic test against deployed code.

**NOT yet fixed (top remaining CONFIRMED highs):** non-atomic portfolio_state.json
races (dashboard clobbers bot; scanner torn-read of qc_portfolio), trading_swarm.py:1162
force-liquidation of any symbol absent from scan universe, qc_portfolio rebalancer
ignoring exposure_level + treating API error as flat book, scanner strategy math
(breakout ATR scale, momentum entry-filter-before-exit), trading-companion ibkr.py
ticker-vs-conid live submit. Deploy-layer (from earlier sweep): vm-autopush.sh D1 guard
only scans src/, auditor.py D17/D20/D21 checks unimplemented, no writer for alpaca_live
peak file (D6 fails open). See [[project_overhaul_2026_07_20]] [[project_directive_auditor]].

**Deploy VERIFIED complete (2026-08-03 00:20 UTC directive audit):** VM copies confirmed
carrying all batches — api.py empty-acct guard + _empty_portfolio, /static owner_* exclusion
(guest+keyless /api/catalog both 403), prop_desk promotion gate + phantom-close sentinel,
notify.py propose_trade delivery-gated. VM trading-stack clean + autopushed. Also enforced
the paper-arm retirement: quarki-paper-arm was disabled but still RUNNING (something started
it 21:00 UTC); stopped 2026-08-03 00:20 UTC, now inactive; vm_watchdog.py has no paper-arm
reference so it stays down. See [[project_vm_migration]] [[feedback_dashboard_restart]].
