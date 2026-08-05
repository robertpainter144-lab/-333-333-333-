#!/usr/bin/env bash
# Push the trading code from this Mac to the Oracle VM via rsync over Tailscale.
# Usage:  ./push.sh ubuntu@<vm-tailnet-ip>
set -euo pipefail

TARGET="${1:-}"
if [ -z "$TARGET" ]; then
    echo "Usage: $0 ubuntu@<vm-tailnet-ip>"
    echo "  e.g. $0 ubuntu@100.64.1.10"
    exit 1
fi

LOCAL_ROOT="/Users/quarki3"
REMOTE_ROOT="/home/ubuntu/trading"

# Files & dirs to sync.
INCLUDES=(
  qc_portfolio
  qc_portfolio_test
  ng_dashboard
  alpaca_keys.py
  deploy
)

# Make sure remote root exists.
ssh "$TARGET" "mkdir -p $REMOTE_ROOT"

# Push each include.
for item in "${INCLUDES[@]}"; do
  echo "→ syncing $item …"
  rsync -avz --delete \
    --exclude '__pycache__' --exclude '*.pyc' \
    --exclude 'logs' --exclude '*.log' \
    "$LOCAL_ROOT/$item" "$TARGET:$REMOTE_ROOT/"
done

echo
echo "✓ push complete."
echo "Next on the VM:"
echo "  bash $REMOTE_ROOT/deploy/oracle/setup.sh"
