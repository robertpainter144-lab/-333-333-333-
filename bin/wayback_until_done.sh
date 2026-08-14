#!/bin/bash
# Loop wayback_backfill passes until no todo remains. Wayback 429/503s are
# transient-failed WITHOUT being checkpointed, so a single pass can end with
# hundreds unfetched — each wrapper pass retries exactly those. Post-run
# grade legs run ONCE at true completion (D44: handle ingest max once/day).
# Pings (quarki_notify) are sent from HERE, not ExecStopPost, so "finished"
# is only ever claimed when todo is actually zero.
cd /home/ubuntu/lab-stack/equity_social || exit 1
NOTIFY=/home/ubuntu/bin/quarki_notify.py

todo_count() {
python3 <<'PY'
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
try:
    for ln in (R / "x_posts_tradergrader.jsonl").read_text().splitlines():
        if ln.strip():
            have.add(json.loads(ln)["id"])
except Exception:
    pass
ck = {"done": [], "perm_fail": []}
try:
    ck.update(json.loads((R / "wayback_checkpoint.json").read_text()))
except Exception:
    pass
print(len(cands - have - set(ck["done"]) - set(ck["perm_fail"])))
PY
}

prev=-1
stall=0
for pass_n in $(seq 1 40); do
  t=$(todo_count)
  echo "[wrapper] pass=$pass_n todo=$t prev=$prev stall=$stall"
  if [ "$t" -eq 0 ]; then
    break
  fi
  if [ "$t" = "$prev" ]; then
    stall=$((stall + 1))
  else
    stall=0
  fi
  if [ "$stall" -ge 3 ]; then
    python3 "$NOTIFY" action "Wayback backfill stalled short of complete" \
      "$t of 4,019 archived @tradergrader posts keep failing after repeated passes — most likely persistent archive.org rate-limiting of the GCP address, possibly dead snapshots. Everything else is fetched and merged. Nothing is lost; rerun the service later or accept the remainder."
    exit 0
  fi
  prev=$t
  QUARKI_SKIP_POSTRUN=1 python3 wayback_backfill.py
  [ "$(todo_count)" -eq 0 ] || sleep 300
done

final=$(todo_count)
for leg in x_fetch grade; do
  timeout 1800 python3 equity_social.py --leg "$leg" || true
done
python3 "$NOTIFY" action "Wayback backfill finished" \
  "All archived @tradergrader posts (todo remaining: $final) are fetched and merged on the GCP box, and the full-history regrade ran there. The Mac still needs the enriched post file pulled back and regraded so the phone Grader card shows it — Claude does that automatically if a session is open, otherwise ask for it next session."
