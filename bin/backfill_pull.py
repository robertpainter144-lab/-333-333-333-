#!/usr/bin/env python3
"""Poll (launchd, 30-min) until the GCP wayback backfill is truly done —
service inactive AND remote todo == 0 — then pull the enriched
x_posts_tradergrader.jsonl back to the Mac, merge it (dedup by id, Mac
entries win, sorted by ts, atomic write), run the grade leg so the phone
Grader card picks up full history, send a quiet confirmation, and remove
itself. Self-expires with a page after 7 days so it can't orphan.
Context: trading-stack/CONTEXT.md 2026-08-14 wayback entries."""
import json
import os
import subprocess
import time
from pathlib import Path

SSH = ["ssh", "-i", "/Users/quarki3/.ssh/oracle_trading",
       "-o", "ConnectTimeout=15", "ubuntu@34.66.251.16"]
REMOTE_JSONL = "/home/ubuntu/lab-stack/equity_social/x_posts_tradergrader.jsonl"
LOCAL_DIR = Path("/Users/quarki3/equity_social")
LOCAL_JSONL = LOCAL_DIR / "x_posts_tradergrader.jsonl"
NOTIFY = "/Users/quarki3/bin/quarki_notify.py"
PLIST = ("/Users/quarki3/Library/LaunchAgents/"
         "com.quarki3.backfill-pull.plist")
LABEL = "gui/501/com.quarki3.backfill-pull"
INSTALLED_TS = 1786742000  # 2026-08-14 ~15:20 MT; expiry anchor

REMOTE_TODO = r"""
import json
from pathlib import Path
R = Path("/home/ubuntu/lab-stack/equity_social")
rows = json.loads((R / "site_cache/cdx_twitter_tradergrader_full.json").read_text())
if rows and rows[0] and rows[0][0] == "urlkey":
    rows = rows[1:]
cands = set()
for r in rows:
    try:
        tid = r[2].rstrip("/").rsplit("/", 1)[-1]
        if tid.isdigit():
            cands.add(tid)
    except Exception:
        pass
have = set()
for ln in (R / "x_posts_tradergrader.jsonl").read_text().splitlines():
    if ln.strip():
        have.add(json.loads(ln)["id"])
ck = {"done": [], "perm_fail": []}
try:
    ck.update(json.loads((R / "wayback_checkpoint.json").read_text()))
except Exception:
    pass
print(len(cands - have - set(ck["done"]) - set(ck["perm_fail"])))
"""


def notify(level: str, title: str, msg: str) -> None:
    subprocess.run([NOTIFY, level, title, msg], timeout=30)


def self_remove() -> None:
    try:
        os.remove(PLIST)
    except FileNotFoundError:
        pass
    subprocess.Popen(["launchctl", "bootout", LABEL])


def remote_state() -> tuple[str, int]:
    active = subprocess.run(
        SSH + ["systemctl is-active quarki-wayback-backfill"],
        capture_output=True, text=True, timeout=60).stdout.strip()
    todo = subprocess.run(SSH + ["python3", "-"], input=REMOTE_TODO,
                          capture_output=True, text=True, timeout=120)
    return active, int(todo.stdout.strip())


def pull_merge_grade() -> None:
    tmp_remote = str(LOCAL_JSONL) + ".gcp"
    subprocess.run(["scp", "-i", "/Users/quarki3/.ssh/oracle_trading",
                    f"ubuntu@34.66.251.16:{REMOTE_JSONL}", tmp_remote],
                   check=True, timeout=300)
    posts: dict[str, dict] = {}
    for path in (LOCAL_JSONL, Path(tmp_remote)):  # Mac entries win
        try:
            for ln in path.read_text().splitlines():
                if ln.strip():
                    p = json.loads(ln)
                    posts.setdefault(p["id"], p)
        except FileNotFoundError:
            pass
    ordered = sorted(posts.values(), key=lambda p: p["ts"])
    tmp = LOCAL_JSONL.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(p) + "\n" for p in ordered))
    tmp.replace(LOCAL_JSONL)
    os.remove(tmp_remote)
    subprocess.run(["/usr/bin/python3", str(LOCAL_DIR / "equity_social.py"),
                    "--leg", "grade"], timeout=1800)
    notify("routine", "TraderGrader full history is on the Mac",
           f"Pulled the finished wayback backfill from GCP and merged it — "
           f"the Mac store now holds {len(ordered)} posts — then regraded. "
           f"The phone Grader card reflects the full history. Nothing to do.")


def main() -> None:
    if time.time() - INSTALLED_TS > 7 * 86400:
        notify("action", "Backfill pull job expired",
               "Seven days passed and the GCP wayback backfill never "
               "reached todo=0, so the Mac-side pull job removed itself. "
               "Check quarki-wayback-backfill on the VM if this matters.")
        self_remove()
        return
    try:
        active, todo = remote_state()
    except Exception:
        return  # GCP unreachable / transient — try again next interval
    if active == "active" or todo > 0:
        return  # not done yet — stay armed
    try:
        pull_merge_grade()
    except Exception as e:
        notify("action", "Backfill pull to Mac failed",
               f"The GCP backfill is complete but pulling/merging on the "
               f"Mac failed ({e}). The data is safe on the VM at "
               f"{REMOTE_JSONL}; rerun ~/bin/backfill_pull.py by hand.")
    self_remove()


if __name__ == "__main__":
    main()
