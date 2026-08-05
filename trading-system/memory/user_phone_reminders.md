---
name: user-phone-reminders
description: "Robert's phone number and preference to be texted for reminders; current delivery channel is ntfy push"
metadata: 
  node_type: memory
  type: user
  originSessionId: 2e781da2-772d-4283-bbfe-b26f2f8d1604
  modified: 2026-08-02T14:22:16.941Z
---

Robert's phone: 914-841-8804. He wants reminders/notifications "texted" to him (2026-08-02: "you can text me for things like this and future reminders").

**Current reality:** no SMS capability in the stack (no Twilio account). Delivery channels that work today: (1) ntfy push to his phone — topic in ~/ntfy.env on Mac+VM, requires the ntfy app subscribed ([[reference-ntfy-alerts]]); this is what VM timers use (e.g. quarki-shadow-review.timer, Mondays 14:00 UTC). (2) PushNotification tool from a live session — reaches phone only when Remote Control is connected.

**Twilio pipeline (built 2026-08-02, awaiting his signup):** `~/scanner/send_sms.py` on the VM sends via Twilio REST using `/home/ubuntu/twilio.env` (TWILIO_SID/TOKEN/FROM, SMS_TO=+19148418804; template at `~/twilio.env.template`, chmod 600). shadow_review.py already calls it (ntfy + SMS); it silently no-ops until the env file is filled. Textbelt free tier is US-blocked (tested).

**How to apply:** route automated reminders through ntfy + send_sms.py; once twilio.env is populated, wire send_sms into circuit-breaker/D5-approval alerts too. Never paste Twilio tokens in chat (D17) — Robert fills the env file himself.
