#!/usr/bin/env python3
"""Weekly shadow-ledger review reminder (D22/D22a).

Summarizes pt_shadow.jsonl + elig_shadow.jsonl against the promotion bars and
pushes the result via ntfy so Robert gets an automatic 'revisit this' nudge
with real progress numbers, not a blind calendar ping.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

STATE = Path("/home/ubuntu/scanner/state")
PT = STATE / "pt_shadow.jsonl"
ELIG = STATE / "elig_shadow.jsonl"
START_TS = 1785679200  # 2026-08-02, when both shadows shipped


def _rows(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for ln in p.read_text().splitlines():
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out


def main() -> None:
    weeks = (time.time() - START_TS) / (7 * 86400)
    pt = _rows(PT)
    el = _rows(ELIG)
    pt_syms = sorted({r.get("sym") for r in pt if r.get("sym")})
    el_syms = sorted({r.get("sym") for r in el if r.get("sym")})
    el_incr = [r for r in el if not r.get("also_blocked_by")]

    # D22a promotion bar for the eligibility gate: >=3 distinct symbols,
    # >=10 events incremental over the existing guards.
    bar = (f"elig bar: {len(el_syms)}/3 symbols, {len(el_incr)}/10 incremental"
           f" | PT bar: {weeks:.1f}/8-12 wks of shadow")

    msg = (f"Shadow-ledger review (wk {weeks:.1f}): "
           f"PT {len(pt)} events ({', '.join(pt_syms) or 'none'}); "
           f"elig {len(el)} events ({', '.join(el_syms) or 'none'}). "
           f"{bar}. Revisit with Claude when a bar is close or curiosity strikes.")

    topic = None
    for ln in Path("/home/ubuntu/ntfy.env").read_text().splitlines():
        if ln.startswith("NTFY_TOPIC="):
            topic = ln.split("=", 1)[1].strip()
    if topic:
        requests.post(f"https://ntfy.sh/{topic}", data=msg.encode(),
                      headers={"Title": "D22 shadow review", "Priority": "3",
                               "Tags": "bell"}, timeout=15)
    # SMS leg (Robert 2026-08-02: "text me for things like this") — no-op
    # until /home/ubuntu/twilio.env is populated.
    try:
        sys.path.insert(0, "/home/ubuntu/scanner")
        import send_sms
        send_sms.send(msg)
    except Exception as e:
        print(f"sms skip: {e}")
    print(msg)


if __name__ == "__main__":
    main()
