---
name: Update CONTEXT.md whenever we ship
description: Every meaningful code/infra change must append to trading-stack/CONTEXT.md changelog + get auto-pushed
type: feedback
originSessionId: c777b35a-8784-4ed8-b477-cb1bcb76d2f3
---
Every meaningful change to any trading repo — code, infra, endpoint, launchd job, deployment — MUST get an entry in `/Users/quarki3/trading-stack/CONTEXT.md` under `## 📝 CHANGELOG` before or immediately after the change. Repo path is now versioned; autopush includes it in the 15-min rotation.

**Why:** The user explicitly and repeatedly asked for a tracker doc kept in lock-step with GitHub auto-push, so nothing is forgotten or lost across sessions. On 2026-07-16 CONTEXT.md was allowed to lag by 3 days while multiple ships happened (webapp, Cloudflare tunnel, Oracle Caddy, ticker chart fix) — user caught it and rebuked the drift. `trading-stack/` was not even a git repo until then.

**How to apply:**
- After any file edit / launchd install / repo create / systemd change / new endpoint / new source, before responding "done", append a dated changelog entry to `trading-stack/CONTEXT.md`.
- Format: `- YYYY-MM-DD HH:MM — <what happened, why, key file:line>`.
- If a change spans multiple files/systems, one bullet is fine — cross-reference key paths.
- Auto-push runs every 15 min; the guardrail in `~/bin/trading-autopush.sh` now logs `WARNING` when code repos change without CONTEXT.md being touched in the last 24h. Watch for that in `/tmp/trading-autopush.log`.
- Do NOT lump multiple sessions' worth of changes into one entry — write them as you go.
