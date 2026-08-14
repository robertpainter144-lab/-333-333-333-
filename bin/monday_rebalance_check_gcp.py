#!/usr/bin/env python3
"""GCP twin of the Mac's Monday-open rebalance check (D48: no
single-hardware dependency). The Mac check reads local mirror state and
sends a quiet confirmation at 07:50 MT; this one runs 07:55 MT and asks
the Alpaca LIVE API directly whether any orders exist since the open —
so it still fires if the Mac is asleep. Pages (P4) ONLY on failure;
silent when orders are flowing (the Mac already confirms quietly).
One-shot for 2026-08-17; disables its own timer afterward."""
import datetime
import json
import subprocess
import urllib.request

ENV_FILE = "/home/ubuntu/alpaca_live.env"
NOTIFY = "/home/ubuntu/bin/quarki_notify.py"
OPEN_UTC = "2026-08-17T13:30:00Z"


def _env() -> dict:
    env = {}
    for ln in open(ENV_FILE):
        ln = ln.strip()
        if "=" in ln and not ln.startswith("#"):
            k, v = ln.split("=", 1)
            env[k.strip()] = v.strip().strip('"\'')
    return env


def notify(level: str, title: str, msg: str) -> None:
    subprocess.run([NOTIFY, level, title, msg], timeout=30)


def check() -> None:
    env = _env()
    req = urllib.request.Request(
        "https://api.alpaca.markets/v2/orders?status=all&limit=20"
        "&after=" + OPEN_UTC,
        headers={"APCA-API-KEY-ID": env["APCA_API_KEY_ID"],
                 "APCA-API-SECRET-KEY": env["APCA_API_SECRET_KEY"]})
    orders = json.loads(urllib.request.urlopen(req, timeout=20).read())
    if not orders:
        notify("action", "Rebalance check (GCP): no live orders since open",
               "25 minutes after the Monday open the Alpaca live account "
               "shows zero orders. Either the Mac is asleep/off (the live "
               "mirror only runs there) or the deferred TEVA/INTC buys are "
               "stuck. If you are traveling, this is the accepted Mac-off "
               "gap; otherwise wake the Mac and check the Book tab.")
    # Orders present: stay silent — the Mac check already confirmed quietly.


if __name__ == "__main__":
    if datetime.date.today().isoformat() == "2026-08-17":
        try:
            check()
        except Exception as e:
            try:
                notify("action", "Rebalance check (GCP) failed",
                       f"The GCP-side Monday-open check crashed ({e}). "
                       f"Glance at the Book tab to confirm buys are flowing.")
            except Exception:
                pass
    subprocess.Popen(["sudo", "systemctl", "disable", "--now",
                      "quarki-monday-check.timer"])
