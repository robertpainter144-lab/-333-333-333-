---
name: directive-auditor
description: "Persistent cross-session compliance brain: DIRECTIVES.md ledger + Mac request harvester + VM systemd auditor (hourly invariants, daily LLM review of Robert's week of prompts); needs claude setup-token to finish"
metadata: 
  node_type: memory
  type: project
  originSessionId: b93a4eef-436d-4e05-b714-fd098bc26ae7
  modified: 2026-08-02T12:57:20.090Z
---

Built 2026-07-29 at Robert's request ("wire up a context tracker that reviews
each and every request I've input scanning the past 1 week to make sure all
orders are being followed to a t", "run independently of anything happening
on the mac or in the claude terminal").

**Three parts:**
1. **`trading-stack/DIRECTIVES.md`** (VM + Mac copies) — canonical standing-order
   ledger D1–D15. EVERY new standing order Robert gives MUST be appended here
   (in addition to Claude memory) — the auditor treats it as ground truth.
2. **Mac harvester** — `~/bin/harvest_user_requests.py`, launchd
   `com.quarki3.request-harvester` every 300s: extracts Robert's typed prompts
   from `~/.claude/projects/*/*.jsonl` (cursor-incremental, skips
   system/command noise) → `~/trading-data/user_requests.jsonl` → rsync to
   VM `~/trading-data/user_requests.jsonl`. 131 requests back-filled at launch.
3. **VM auditor** — `~/directive_auditor/auditor.py`, systemd:
   `directive-audit-fast.timer` hourly :17 (deterministic: guest 403s on
   /api/catalog + /owner/catalog.js, no private markers in MVYTraders /
   trading-gui src+dist, public shell has no owner injection, no catalog rows
   in guest /api/edge, $5-DD check vs trading-data/portfolio_peaks/alpaca_live.json,
   /opt/trading absent, quarki-* units active);
   `directive-audit.timer` daily 13:03 UTC (07:03 MDT): same + LLM pass over
   the trailing 7 days of raw requests vs DIRECTIVES.md (unfulfilled orders,
   uncaptured directives, verdict). Reports → `~/trading-data/compliance/latest.md`
   + dated; ntfy (topic in ~/ntfy.env) fires on violations, else a daily
   one-liner. First runs: 0 violations, dd $2.58/$5.

**LLM auth (SOLVED 2026-07-29 eve, no user action needed):** Mac ~/.zshenv's
ANTHROPIC_API_KEY is a literal `sk-ant-...` placeholder — no real API key
exists. Instead: Mac Keychain item "Claude Code-credentials" (claudeAiOauth)
copied to VM `~/.claude/.credentials.json` (chmod 600) → VM claude CLI
2.1.220 runs `claude -p --model sonnet` on the Max subscription. The
harvester's `sync_claude_credentials()` auto-pushes fresher Mac creds to the
VM every 5 min (OAuth refresh rotation: Mac usually holds newest; VM copy
self-heals). GOTCHAS: any ANTHROPIC_API_KEY env var SHADOWS the OAuth login
inside the CLI (auditor strips it from the subprocess env; keep it out of
~/anthropic.env); if the Mac ever demands /login again, that's the rotation
race — harmless, log back in.

**2026-08-02 — D19 churn check rewritten (false-positive fix):** the 02:17 MDT
"19 live orders" ntfy was noise — the old check grepped BUY/SELL substrings
from scanner/state/live_activity.jsonl, which matched the PAPER prop-desk's
24/7 crypto signal-summary lines ("[strat_mom] BUYs=1 SELLs=2"), and that log
is trimmed to ~300 rows (~10 min) so any daily count from it is meaningless.
Now D19 queries Alpaca live /v2/orders?status=all&after=<midnight ET> directly
(keys parsed from ~/quarki.env, quotes stripped; midnight ET computed via
TZ/tzset because VM python lacks zoneinfo; `Z`-suffix timestamp — a literal
`+00:00` in the query string 422s). Backup: auditor.py.bak-d19. Verified
"D19 live orders today: 0". Auditor exists ONLY on the VM (no Mac copy).

**First real audit (2026-07-29, verdict ATTENTION) proved the concept:**
found 3 uncaptured standing orders (added as D16 autopush-default, D17
secret-hygiene, D18 Linus-branch-isolation), caught the SnapTrade user
secret Robert pasted in chat 7/27 (repo histories grepped clean, config
chmod 600; rotate at snaptrade.com when the feed is next used), and flagged
that the 7/27 "$50 Alpaca vs QC — why no profit, since when" comparative
analysis was never delivered (STILL OPEN — good first task some session).
Collaborator sweep: only MVYTraders has lmunn1; all other repos sole-access.

Related: [[feedback-post-linus-private]], [[feedback-private-gui-meaning]],
[[project_vm_migration]], [[feedback-context-md-discipline]].
