#!/bin/bash
# auto_sync.sh — every minute, stage anything changed in the repo, commit,
# and push. Mirrors the pattern at /Users/quarki3/trading-system/scripts/auto_sync.sh
# but simpler — this repo is the source of truth, not a mirror, so no rsync.
#
# Idempotent: bails cleanly if no changes.

set -euo pipefail

REPO=/Users/quarki3/trading-companion
LOG=/tmp/trading-companion-autosync.log

cd "$REPO"

ts() { date +"%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

# Defensive scrub of common secret patterns before staging.
find "$REPO" -type f \( -name '*.sh' -o -name '*.py' -o -name '*.ts' -o -name '*.svelte' \) \
  -not -path '*/.git/*' -not -path '*/node_modules/*' -not -path '*/.svelte-kit/*' \
  -not -path '*/build/*' -not -path '*/.venv/*' -print0 \
  | xargs -0 sed -i '' -E \
      -e 's/(COMPANION_API_KEY=)["'\'']?[A-Za-z0-9_-]{20,}["'\'']?/\1/g' \
      -e 's/(QC_API_TOKEN=)["'\'']?[A-Za-z0-9_-]{20,}["'\'']?/\1/g' \
      -e 's/(IBEAM_PASSWORD=)["'\'']?[^[:space:]"'\'']{6,}["'\'']?/\1/g' \
      -e 's/(tskey-auth-[A-Za-z0-9_-]+)/<REDACTED-TS-AUTHKEY>/g' \
      2>/dev/null || true

git add -A

if git diff --cached --quiet; then
    exit 0
fi

git -c user.name="quarki3-autosync" \
    -c user.email="robert.m.painter@gmail.com" \
    commit -m "auto: $(ts)" >> "$LOG" 2>&1

if ! git push origin main >> "$LOG" 2>&1; then
    log "push failed; retrying once after rebase"
    git pull --rebase origin main >> "$LOG" 2>&1 || true
    git push origin main >> "$LOG" 2>&1 || log "push retry failed; will retry next tick"
fi

log "synced"
