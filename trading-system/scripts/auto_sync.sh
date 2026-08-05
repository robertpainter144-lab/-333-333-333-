#!/bin/bash
# auto_sync.sh — pulls the latest Mac-side trading code into the repo,
# commits, and pushes to GitHub. Designed to be run periodically by launchd.
# Idempotent: if nothing changed, no commit is made.

set -euo pipefail

REPO=/Users/quarki3/trading-system
LOG=/tmp/trading-system-autosync.log

cd "$REPO"

ts() { date +"%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] $*" >> "$LOG"; }

log "=== starting sync ==="

# 1. Mirror Mac source dirs into the repo (rsync with --delete so removals propagate).
rsync -a --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='logs/' --exclude='.idea' \
  /Users/quarki3/ng_dashboard/ "$REPO/ng_dashboard/" 2>>"$LOG"

rsync -a --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='logs/' \
  /Users/quarki3/qc_portfolio/ "$REPO/qc_portfolio/" 2>>"$LOG"

rsync -a --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='logs/' \
  /Users/quarki3/qc_portfolio_test/ "$REPO/qc_portfolio_test/" 2>>"$LOG"

rsync -a --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='logs/' \
  /Users/quarki3/ibkr_bot/ "$REPO/ibkr_bot/" 2>>"$LOG"

rsync -a --delete \
  --exclude='build/' --exclude='.gradle/' --exclude='.idea/' \
  --exclude='dist/' --exclude='keystore/' --exclude='local.properties' \
  --exclude='*.apk' \
  /Users/quarki3/trading_android/ "$REPO/trading_android/" 2>>"$LOG"

rsync -a /Users/quarki3/deploy/ "$REPO/deploy/" 2>>"$LOG"
cp /Users/quarki3/alpaca_keys.py "$REPO/alpaca_keys.py"

# 2. Mirror the Claude memory folder so context survives the Mac dying.
rsync -a --delete \
  /Users/quarki3/.claude/projects/-Users-quarki3/memory/ "$REPO/memory/" 2>>"$LOG"

# 3. Defensive: re-scrub any secrets that might have leaked in (env var values,
# raw API keys). Anything looking like ALPACA_*_KEY=<actual-value> gets blanked.
find "$REPO" -type f \( -name '*.sh' -o -name '*.py' -o -name '*.env' \) \
  -not -path '*/.git/*' -print0 \
  | xargs -0 sed -i '' -E \
      -e 's/(ALPACA_[A-Z_]+=)["'\'']?[A-Za-z0-9_-]{20,}["'\'']?/\1/g' \
      -e 's/(tskey-auth-[A-Za-z0-9_-]+)/<REDACTED-TS-AUTHKEY>/g' \
      2>/dev/null || true

# 4. Stage everything.
git add -A

# 5. Bail cleanly if nothing changed.
if git diff --cached --quiet; then
    log "no changes"
    exit 0
fi

# 6. Commit + push.
git -c user.name="quarki3-autosync" \
    -c user.email="robert.m.painter@gmail.com" \
    commit -m "auto: $(ts)" >> "$LOG" 2>&1

git push origin main >> "$LOG" 2>&1
log "=== pushed ==="
