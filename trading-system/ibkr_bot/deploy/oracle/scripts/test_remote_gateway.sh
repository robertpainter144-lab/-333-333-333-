#!/usr/bin/env bash
# Run on your Mac after Oracle VM is up. Verifies you can reach the headless
# gateway over Tailscale and the bot services are healthy.
#
# Usage:
#   ./test_remote_gateway.sh                       # uses ssh alias 'ibkr-gw'
#   ORACLE_HOST=ubuntu@100.1.2.3 ./test_remote_gateway.sh

set -uo pipefail
HOST="${ORACLE_HOST:-ibkr-gw}"

echo "=== reachability ==="
if ssh -o ConnectTimeout=5 "$HOST" "echo ok" >/dev/null 2>&1; then
  echo "  ✓ SSH to $HOST works"
else
  echo "  ✗ SSH to $HOST failed — check ~/.ssh/config or Tailscale status on the VM"
  exit 1
fi

echo ""
echo "=== remote container ==="
ssh "$HOST" "sudo docker ps --format '{{.Names}}\t{{.Status}}' | grep ib-gateway"
ssh "$HOST" "sudo docker inspect -f '  health: {{.State.Health.Status}}' ib-gateway"

echo ""
echo "=== remote port ==="
ssh "$HOST" "ss -ltn | grep '127.0.0.1:4001 ' || echo '  ✗ 4001 not bound'"

echo ""
echo "=== remote systemd units ==="
ssh "$HOST" "for u in ibkr-watcher ibkr-equity-logger; do printf '  %s: ' \$u; systemctl is-active \$u; done"

echo ""
echo "=== last 5 watcher log lines ==="
ssh "$HOST" "tail -5 /var/log/ibkr_watcher.log 2>/dev/null || echo '  (no log yet)'"

echo ""
echo "=== last 5 equity log lines ==="
ssh "$HOST" "tail -5 /var/log/ibkr_equity.log 2>/dev/null || echo '  (no log yet)'"

echo ""
echo "=== tailnet IP (for SPY overlay plist) ==="
ssh "$HOST" "sudo tailscale ip -4"
