#!/usr/bin/env bash
# Run ON THE VM after bootstrap.sh, push.sh, and after you've populated
# /opt/ib-gateway/.env and /home/ubuntu/ibkr_bot/runtime.env with creds.
# Idempotent. Brings up gateway + bot services and prints a verification.

set -euo pipefail

echo "=== sanity-check creds files ==="
for f in /opt/ib-gateway/.env /home/ubuntu/ibkr_bot/runtime.env; do
  if [ ! -f "$f" ]; then
    echo "  ✗ missing $f — see CUTOVER.md step E"
    exit 1
  fi
  if [ "$(stat -c %a "$f")" != "600" ]; then
    echo "  ! $f is not chmod 600 — fixing"
    chmod 600 "$f"
  fi
done
echo "  ✓ creds files in place and 600"

echo ""
echo "=== bringing up gateway container ==="
cd /opt/ib-gateway
sudo docker compose up -d
echo "  ✓ gateway started. Tail logs: sudo docker logs -f ib-gateway"
echo "  → Approve 2FA push on your phone within ~30s if prompted."

echo ""
echo "  → Waiting up to 120s for container to report healthy ..."
for i in $(seq 1 24); do
  status=$(sudo docker inspect -f '{{.State.Health.Status}}' ib-gateway 2>/dev/null || echo "")
  if [ "$status" = "healthy" ]; then
    echo "  ✓ gateway healthy after ${i}x5s"
    break
  fi
  sleep 5
done

echo ""
echo "=== enabling bot services ==="
sudo systemctl daemon-reload
sudo systemctl enable --now ibkr-watcher ibkr-equity-logger
sleep 5

echo ""
echo "=== verification ==="
bash /home/ubuntu/ibkr_bot/deploy/oracle/scripts/verify.sh

echo ""
echo "=== tailnet IP (for SPY overlay plist on Mac) ==="
sudo tailscale ip -4

echo ""
echo "=== cutover complete on this VM ==="
echo "On your Mac, now:"
echo "  1. launchctl bootout gui/\$(id -u)/com.quarki3.ibkr-watcher"
echo "  2. launchctl bootout gui/\$(id -u)/com.quarki3.ibkr-equity-logger"
echo "  3. Edit ~/Library/LaunchAgents/com.quarki3.spy-overlay.plist: change IBKR_GATEWAY_HOST 127.0.0.1 → tailnet IP above"
echo "  4. launchctl bootout gui/\$(id -u)/com.quarki3.spy-overlay && launchctl bootstrap gui/\$(id -u) ~/Library/LaunchAgents/com.quarki3.spy-overlay.plist"
echo "  5. Quit local IB Gateway 10.46 — Mac can now sleep."
