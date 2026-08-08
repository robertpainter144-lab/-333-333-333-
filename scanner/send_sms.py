#!/usr/bin/env python3
"""SMS sender for stack reminders/alerts (Twilio).

Reads /home/ubuntu/twilio.env (TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, SMS_TO).
Exits quietly if the env file is missing/incomplete so timers that call this
keep working before Twilio is configured (ntfy remains the fallback channel).

Usage: send_sms.py "message text"
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

ENV = (Path.home() / "twilio.env")


def _env() -> dict:
    if not ENV.exists():
        return {}
    out = {}
    for ln in ENV.read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def send(msg: str) -> bool:
    e = _env()
    sid, tok = e.get("TWILIO_SID"), e.get("TWILIO_TOKEN")
    frm, to = e.get("TWILIO_FROM"), e.get("SMS_TO")
    if not all([sid, tok, frm, to]):
        print("send_sms: twilio.env incomplete — skipping (ntfy is fallback)")
        return False
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, tok),
        data={"From": frm, "To": to, "Body": msg[:1500]},
        timeout=20)
    ok = r.status_code in (200, 201)
    print(f"send_sms: HTTP {r.status_code}" + ("" if ok else f" {r.text[:200]}"))
    return ok


if __name__ == "__main__":
    send(" ".join(sys.argv[1:]) or "Quarki stack: empty test message")
