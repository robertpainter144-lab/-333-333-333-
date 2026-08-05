#!/usr/bin/env bash
# Push ibkr_bot code from Mac → Oracle VM. Run from the Mac.
# Usage:
#   ORACLE_HOST=ubuntu@100.x.y.z ./push.sh
#   (or set in your ~/.ssh/config and pass an alias)

set -euo pipefail
HOST="${ORACLE_HOST:?set ORACLE_HOST=ubuntu@<tailnet-ip-or-alias>}"

SRC="/Users/quarki3/ibkr_bot/"
DEST_BOT="$HOST:/home/ubuntu/ibkr_bot/"
DEST_COMPOSE="$HOST:/opt/ib-gateway/"

EXCLUDES=(
  --exclude '__pycache__'
  --exclude '*.log'
  --exclude '*.err'
  --exclude 'state.json'
  --exclude 'stop_state.json'
  --exclude 'transactions.csv'
  --exclude 'equity_history.csv'
  --exclude 'positions_snapshot.json'
  --exclude 'seen_executions.json'
  --exclude 'decisions.json'
  --exclude 'pushover.env'
  --exclude 'venv'
  --exclude 'deploy/oracle/compose/.env'
  --exclude 'deploy/oracle/systemd/runtime.env'
)

echo "=== pushing bot source to $DEST_BOT ==="
rsync -avz --delete "${EXCLUDES[@]}" "$SRC" "$DEST_BOT"

echo "=== pushing compose to $DEST_COMPOSE ==="
rsync -avz /Users/quarki3/ibkr_bot/deploy/oracle/compose/ "$DEST_COMPOSE"

echo "=== installing systemd units ==="
scp /Users/quarki3/ibkr_bot/deploy/oracle/systemd/ibkr-watcher.service \
    /Users/quarki3/ibkr_bot/deploy/oracle/systemd/ibkr-equity-logger.service \
    "$HOST:/tmp/"
ssh "$HOST" "sudo mv /tmp/ibkr-watcher.service /tmp/ibkr-equity-logger.service /etc/systemd/system/ && sudo systemctl daemon-reload"

echo "=== push complete ==="
echo "On the VM: sudo systemctl restart ibkr-watcher ibkr-equity-logger  (after creds are in place)"
