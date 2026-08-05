---
name: project-improvement-bot
description: "D29 always-on improvement bot — 4h headless sonnet cycles, tier-gated, never touches live money; MOVED to VM 2026-08-03 late (quarki-improve.timer), Mac launchd bot disabled"
metadata: 
  node_type: memory
  type: project
  originSessionId: 500b8363-c6ed-410b-9da2-045e600c8bbe
  modified: 2026-08-04T02:30:35.111Z
---

**Improvement bot (D29, shipped 2026-08-03).** Robert asked for a constantly
running bot spending a small slice of spare Claude compute on profit-oriented
research/improvements. Lives at `~/improvement_bot/` on the Mac:

- `MISSION.md` — per-cycle charter: one bounded, verified improvement; VM is
  where the stack lives; close-out = LOG.md entry + BACKLOG.md update +
  CONTEXT.md if shipped + quarki_notify P2 (P4 only if Robert must act).
- `BACKLOG.md` — persistent priority queue (bot maintains it).
- `run_cycle.sh` — wrapper: gate = auto_tier GREEN && 5h<50% && weekly<60%
  (reads ~/consumption_manager/state.json), mkdir lock, 60-min watchdog,
  runs `claude -p --model sonnet --dangerously-skip-permissions`.
- `LOG.md` — cycle journal; `logs/` per-cycle transcripts + gate.log;
  `proposals/` — patches the bot may NOT apply (live-money paths).
- **VM edition (canonical since 2026-08-03 late):** lives at VM
  `~/improvement_bot/`, systemd `quarki-improve.timer` (4h, oneshot service),
  runs `~/.local/bin/claude -p --model sonnet --dangerously-skip-permissions`.
  Gate reads `~/trading-data/consumption/state.json` (Mac-synced): GREEN
  fresh → GO; GREEN stale>24h (Mac off) → GO-STALE; AMBER/RED/unreadable →
  skip. Robert's goal: everything survives the Mac being off (see
  trading-stack/MOBILE.md for the Android/Termux workflow).
- Mac launchd `com.quarki3.improvement-bot` DISABLED 2026-08-03 late (plist
  renamed .disabled) so two bots never double-run. Classifier note: it blocks
  installing/enabling the scheduled skip-permissions agent even under D30 —
  the systemd enable step needs Robert's `!` paste.
- **First cycle (2026-08-03 18:44 MDT) worked end-to-end**: found the D24
  leader-check state-file gap by reading CONTEXT.md, deployed a verified fix
  to the VM with backup, reconciled with a concurrently-staged session fix
  (exact-match asserts prevented a clobber), logged + silent P2, stopped at
  one item per charter. It reads CONTEXT.md/BACKLOG.md for work items —
  documenting findings there is how sessions hand it work. NOTE: concurrent
  sessions editing the same VM files as the bot must use exact-match asserts;
  the bot runs with --dangerously-skip-permissions and can ship mid-session.

Hard rails in MISSION.md: never edit live order flow/weights (propose + P4
instead), all DIRECTIVES.md rules bind, shelved verdicts stay shelved,
promotion gates sacred, no Workflow tool, ≤4 subagents pinned haiku/sonnet.
Related: [[project-consumption-manager]], [[feedback-workflow-model-tiers]].
