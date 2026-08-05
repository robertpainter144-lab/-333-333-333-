---
name: project-consumption-manager
description: "D25 meta-level Claude usage manager — meter, tiers, gates on Mac+VM; budgets empirical; AMBER=cap swarms 4-6"
metadata: 
  node_type: memory
  type: project
  originSessionId: ef5c274c-7332-43a5-8737-5227f522b1bd
  modified: 2026-08-03T00:14:36.086Z
---

Built 2026-08-02 after burst 429s during the 24-agent sweeps ([[project-mirror-sweep]], [[project-bug-sweep-2026-08-02]]) and weekly burn hitting ~83%.

- **Mac**: `~/consumption_manager/consumption_manager.py` (`scan|gate|calibrate`) parses `~/.claude/projects/**/*.jsonl` (deduped by message id, pricing-ratio weighted units, per-model multiplier) → rolling 5h/7d windows → GREEN(<70%)/AMBER(<90%)/RED tier in `state.json`. launchd `com.quarki3.consumption-manager` every 15 min. Tier transitions notify via `~/bin/quarki_notify.py` (ported from VM; RED→action P4, else routine P2).
- **VM**: state synced each scan to `~/trading-data/consumption/state.json`; `~/bin/claude_gate.sh` exits 0/1/2 for Claude-invoking jobs (future auditor LLM pass, sweeps). Auditor has a D25 freshness check (>48h stale = violation).
- **Budgets (calibrated vs official /usage 2026-08-02)**: 5h = 117M units (official 93% ≙ 108.35M measured). Weekly = 537M provisional — official weekly limit is a **FIXED window resetting Sundays 5:59pm America/Denver** (`weekly_reset_anchor` in config; UTC-stepped so ~1h DST drift), NOT rolling 7d — the rolling version over-read 93.6% when official said 1%. +50% weekly promo through 2026-08-19 → true cap higher until then; recalibrate mid-week by comparing state.json pct vs /usage. The 08-02 429s were burst throttling from parallel fan-out, NOT budget exhaustion.
- **Policy (D25)**: AMBER → defer optional swarms/cloud sweeps, cap agent fan-out at 4–6; RED → interactive/critical only. Claude should check `state.json` tier before launching big workflows.
- Fixed same day: `~/scanner/sources/context_watcher.py` was appending "UNKNOWN STATE FILE" spam to CONTEXT.md every 30 min since ~07-29 (4,233 dup lines collapsed); now idempotent (skips files already mentioned in the doc).

2026-08-02 eve: RED fire-drill — heavy sessions SIGSTOPped by gate; ~/bin/unfreeze_watcher.py (one-shot, pid-checked) auto-SIGCONTs them when tier leaves RED & 5h<75%. SIGSTOP on a Claude TUI leaks mouse-escape garbage into the shell — fix with `reset`, do not kill the session.

2026-08-02 night: **D26 session governor** (`~/consumption_manager/session_governor.py`, launchd `com.quarki3.session-governor` every 120s) is now the meta-manager over all live Mac Claude processes. Invariant: interactive (tty-attached) and infra (daemon/bg-spare/spare pty hosts) processes are NEVER SIGSTOPped and get force-SIGCONTed if found stopped (P4 alert — this happened to the daemon after the fire-drill). Only headless sessions (tty ??, --session-id/-p/--resume) are governed: auto_tier RED=pause all, AMBER=cap at state.json max_concurrent_heavy (oldest keep running), GREEN=resume all. SIGSTOP only, never SIGKILL. Supersedes unfreeze_watcher. governor_state.json + governor.log in the same dir. Robert's "gittree" = misremembered `git worktree` / `claude --worktree` (parallel-session file isolation, unrelated to usage governance); `claude agents` lists/stops background sessions.
