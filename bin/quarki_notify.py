#!/usr/bin/env python3
"""Central tiered notifier for the quarki stack (2026-08-02).

Robert's rule: self-healed events must not buzz the phone — only things that
genuinely need him do. Four levels map to ntfy priorities:

  critical  P5  needs Robert NOW ($5 DD breach, self-heal failing). Also
                sends SMS via scanner/send_sms.py once twilio.env exists.
  action    P4  needs a decision soon (directive violations, disk low,
                circuit breaker fired, promotion bar reached)
  info      P3  scheduled summaries (weekly shadow review)
  routine   P2  self-healed / autonomous events — delivered silently, no
                pop-up; visible in the ntfy app log only

Phone side: in the ntfy app set "priority high+" to alarm/override-DND and
leave low/min silent (defaults already do this).

Usage:  from quarki_notify import notify
        notify("critical", "LIVE DRAWDOWN", "...")
CLI:    quarki_notify.py <level> <title> <message...>
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

NTFY_ENV = next((p for p in (Path("/home/ubuntu/ntfy.env"),
                             Path.home() / "ntfy.env") if p.exists()),
                Path("/home/ubuntu/ntfy.env"))
LEVELS = {
    "critical": ("5", "rotating_light"),
    "action":   ("4", "warning"),
    "info":     ("3", "bell"),
    "routine":  ("2", "gear"),
}


def _topic() -> str | None:
    try:
        for ln in NTFY_ENV.read_text().splitlines():
            if ln.strip().startswith("NTFY_TOPIC="):
                return ln.split("=", 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return None


def notify(level: str, title: str, msg: str) -> bool:
    prio, tag = LEVELS.get(level, LEVELS["info"])
    topic = _topic()
    ok = False
    if topic:
        try:
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}", data=msg.encode()[:3800],
                headers={"Title": title.encode("ascii", "replace").decode(),
                         "Priority": prio, "Tags": tag})
            urllib.request.urlopen(req, timeout=15)
            ok = True
        except Exception:
            pass
    if level == "critical":
        # SMS leg — silent no-op until twilio.env is populated.
        try:
            sys.path.insert(0, "/home/ubuntu/scanner")
            import send_sms
            send_sms.send(f"{title}: {msg}"[:300])
        except Exception:
            pass
    return ok


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("usage: quarki_notify.py <critical|action|info|routine> <title> <msg...>")
        sys.exit(1)
    notify(sys.argv[1], sys.argv[2], " ".join(sys.argv[3:]))
