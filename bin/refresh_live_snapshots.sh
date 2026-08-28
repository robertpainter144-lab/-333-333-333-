#!/bin/bash
# refresh_live_snapshots.sh — one-command disaster-recovery refresh of code
# that RUNS live but is not otherwise versioned (see mac-live-snapshots/
# README.md). Run ON THE MAC from anywhere:
#
#     ~/lab333/upstream333/bin/refresh_live_snapshots.sh [--gcp]
#
# The 15-minute autopush loop then carries the copies to GitHub. Strictly
# read-only against the live system: copies files into the repo working
# tree, never restarts services, never edits the live tree, never pushes.
#
# Why this exists (2026-08-28 audit): the deployed money-math code lived
# ONLY on the Mac and GCP disks — repo ng_dashboard/ is a different
# variant (name collision), and mac-live-snapshots/ was 18 days stale
# while api.py gained the live-NAV reconstruction (_lag_align /
# equity_fine truncation), the funded endpoints, and pip.js?v=7 wiring.
# A single disk failure would have lost the only copy of the logic that
# turns the frozen-engine data into correct on-screen numbers.
#
# --gcp additionally pulls the GCP VM's live api.py and the funded
# ledger's source-of-truth state (funded.json, funded_events.jsonl) into
# gcp-live-snapshots/ over the existing SSH key. The repo's
# crypto_sleeve/state/funded.json stopped tracking the GCP copy on
# 2026-08-25, so without this the funded history has no off-VM backup.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LIVE_NG="${LIVE_NG:-$HOME/ng_dashboard}"
DEST="$REPO_DIR/mac-live-snapshots/ng_dashboard"
STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

GCP_HOST="${GCP_HOST:-ubuntu@34.66.251.16}"
GCP_KEY="${GCP_KEY:-$HOME/.ssh/oracle_trading}"
GCP_DEST="$REPO_DIR/gcp-live-snapshots"

if [ ! -d "$LIVE_NG" ]; then
    echo "!! $LIVE_NG not found — this script must run on the Mac" >&2
    exit 1
fi

echo "== Mac live -> mac-live-snapshots/ =="
mkdir -p "$DEST/private"
changed=0
for f in api.py main.py webapp.html; do
    if [ -f "$LIVE_NG/$f" ] && ! cmp -s "$LIVE_NG/$f" "$DEST/$f"; then
        cp "$LIVE_NG/$f" "$DEST/$f" && echo "  refreshed: $f" && changed=1
    fi
done
if [ -d "$LIVE_NG/private" ]; then
    for p in "$LIVE_NG"/private/*.js; do
        b="$(basename "$p")"
        if ! cmp -s "$p" "$DEST/private/$b"; then
            cp "$p" "$DEST/private/$b" && echo "  refreshed: private/$b" && changed=1
        fi
    done
fi
[ "$changed" = 0 ] && echo "  (already current)"
echo "$STAMP" > "$DEST/.last-refresh"

if [ "${1:-}" = "--gcp" ]; then
    echo "== GCP live -> gcp-live-snapshots/ =="
    if [ ! -f "$GCP_KEY" ]; then
        echo "!! $GCP_KEY missing — skipping GCP pull" >&2
    else
        mkdir -p "$GCP_DEST/ng_dashboard/private" "$GCP_DEST/crypto_sleeve/state"
        scp -q -i "$GCP_KEY" \
            "$GCP_HOST:/home/ubuntu/lab-stack/ng_dashboard/api.py" \
            "$GCP_DEST/ng_dashboard/api.py" && echo "  pulled: ng_dashboard/api.py"
        scp -q -i "$GCP_KEY" \
            "$GCP_HOST:/home/ubuntu/lab-stack/ng_dashboard/private/*.js" \
            "$GCP_DEST/ng_dashboard/private/" && echo "  pulled: ng_dashboard/private/*.js"
        for s in funded.json funded_events.jsonl funded_advisor_state.json; do
            scp -q -i "$GCP_KEY" \
                "$GCP_HOST:/home/ubuntu/lab-stack/crypto_sleeve/state/$s" \
                "$GCP_DEST/crypto_sleeve/state/$s" 2>/dev/null \
                && echo "  pulled: crypto_sleeve/state/$s" \
                || echo "  (absent on VM: $s)"
        done
        echo "$STAMP" > "$GCP_DEST/.last-refresh"
    fi
fi

echo "Done. The autopush loop commits these within ~15 min (or commit now)."
