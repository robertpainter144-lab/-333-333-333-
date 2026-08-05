---
name: reference-ntfy-alerts
description: ntfy.sh replaced Pushover (trial expired) for ALL stack alerts as of 2026-07-28 — topic lives in ~/ntfy.env on Mac and VM
metadata: 
  node_type: memory
  type: reference
  originSessionId: 63c6bd14-695b-433c-9549-b96bb48c93a6
  modified: 2026-07-28T19:57:47.005Z
---

Pushover died 2026-07-28 (free trial over, "no active devices"; Robert isn't
buying the license). Entire alert stack migrated to **ntfy.sh** same day.

- Secret topic: in `~/ntfy.env` (`NTFY_TOPIC=quarki-alerts-…`, chmod 600) on
  BOTH the Mac and the Oracle VM (`/home/ubuntu/ntfy.env`). The topic name is
  the only auth — treat as a password, never commit it.
- Send: `curl -H "Title: X" -d "msg" https://ntfy.sh/$NTFY_TOPIC`; Python
  senders POST body=message with Title/Priority headers (Pushover priority
  -2..2 maps to ntfy 1..5; titles ASCII-escaped for latin-1 headers).
- Patched senders (VM + dormant Mac copies): `ibkr_bot/notify.py` (central —
  trade approvals for the $20 ceiling in [[feedback_trade_threshold]]),
  `alpaca_paper_arm.py`, `bin/stack_watchdog.py`, `bin/vm_watchdog.py`,
  `bin/mvy_watch.sh` (Linus commit watcher), and the inert
  `ibkr_bot/deploy/oracle/scripts/a1_capacity_loop.sh`.
- End-to-end ping verified sent 2026-07-28 (`python3 notify.py ping` on VM).
- Robert subscribed on his phone and confirmed delivery working 2026-07-28.
  If it ever breaks: check the topic in `~/ntfy.env` matches the app
  subscription; web fallback https://ntfy.sh/<topic> (~12h cache).
