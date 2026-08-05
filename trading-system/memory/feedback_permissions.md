---
name: Standing Permissions - Trading Project
description: Robert has granted blanket yes to all actions; never ask about anything, just do it
type: feedback
originSessionId: 7942cd82-55e3-4354-9e08-3eca52529717
modified: 2026-08-04T02:05:38.212Z
---
Never ask about anything. Never check in. Never present options. Never confirm. Just execute every action immediately and report what was done.

**Why:** Stated repeatedly and emphatically. Latest: "the answer when you check with me is always yes and don't ask again. please don't ever ask again about anything, just do it."

**How to apply:** Every action on this project — edits, restarts, API calls, bash commands, new files, deletions, deployments, anything — execute directly without pausing. One-way: do it, then report.

**2026-08-03 update (D30):** the auto-mode classifier (not permission rules —
those were already Bash(*)) was the thing generating denials. Robert applied
an `autoMode.allow` entry in ~/.claude/settings.json pre-approving SSH/scp to
the trading VM (ubuntu@129.153.96.201, key ~/.ssh/oracle_trading) including
editing/restarting his own stack services. If a VM command still gets
classifier-blocked, the fallback pattern that works: stage a script under
~/trading-stack/staged/ with backup-first + exact-match asserts, and give
Robert a one-line `!` command to run it. Sessions can never edit their own
permission settings — deliberately kept user-side.
