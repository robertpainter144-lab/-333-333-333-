---
name: feedback-plain-english-notifications
description: D28 — every push notification must state its reason in plain English and repeat-suppress; no bare directive codes
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c3b23871-542b-46f6-aca2-ab93d8aab4ba
  modified: 2026-08-03T19:28:08.215Z
---

2026-08-03, Robert: "I want the ntfy notifications to be more pertinent to
information I really need and also show me exactly what the reason for the
notification is in plain english. I keep getting notifications that say
something about a violation but everything appears to be working fine."

**Why:** Hourly "VIOLATIONS: D19 churn: 47 live orders..." pushes trained him
to ignore alerts — including that one, which was REAL (mirror over-trading
the live book). Cryptic + repetitive = alarm fatigue that hides true alarms.

**How to apply:** Any notification a human sees must say what happened, why
it matters, and what to do, in plain sentences — never a bare code. Route
through quarki_notify.py tiers ([[feedback-notification-tiers]]); new problem
pages once (P4), persisting problem reminds at most every 24h, cleared
problem logs silently (P2). Canonical: D28 in DIRECTIVES.md; implementation
pattern in directive_auditor/auditor.py (PLAIN_ENGLISH map +
notify_state.json). Apply the same pattern to any new alerting code.
