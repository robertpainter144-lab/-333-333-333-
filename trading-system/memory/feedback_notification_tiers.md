---
name: feedback-notification-tiers
description: Robert wants self-healed events silent; only page him when a human is actually needed — use quarki_notify.py tiers
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 2e781da2-772d-4283-bbfe-b26f2f8d1604
  modified: 2026-08-02T21:00:35.514Z
---

2026-08-02, Robert: "I've been getting a lot of messages about stuff that's going wrong... when my system could probably be self healing a lot of the time before I even see the notifications."

**Why:** high-priority pings for events the stack already auto-fixed train him to ignore alerts, which defeats the real ones (D6 drawdown, heal failures).

**How to apply:** every new alert goes through `~/bin/quarki_notify.py` on the VM (`from quarki_notify import notify`), never raw ntfy posts. Levels: `critical` P5 (human needed NOW: D6 $5 DD, self-heal not sticking ≥3×/45min; also SMS once twilio.env exists) · `action` P4 (decision soon: violations, disk, breaker, $4 amber DD) · `info` P3 (scheduled summaries) · `routine` P2 (silent: anything the system fixed itself). Default posture: if the stack handled it, it's `routine`. Prefer designing self-healing first, then a silent log line, then a page — in that order. Ref [[reference-ntfy-alerts]], [[user-phone-reminders]].
