#!/usr/bin/env bash
# Smoke-test that the headless gateway and bot are healthy. Run on the VM.

set -uo pipefail
fail=0

check() {
  if eval "$2"; then
    echo "  ✓ $1"
  else
    echo "  ✗ $1"
    fail=1
  fi
}

echo "=== container ==="
check "ib-gateway container running"    "docker ps --format '{{.Names}}' | grep -q '^ib-gateway$'"
check "container healthcheck = healthy" "[ \"$(docker inspect -f '{{.State.Health.Status}}' ib-gateway 2>/dev/null)\" = healthy ]"

echo ""
echo "=== port ==="
check "127.0.0.1:4001 listening"        "ss -ltn | grep -q '127.0.0.1:4001 '"

echo ""
echo "=== bot ==="
check "ibkr-watcher active"             "systemctl is-active --quiet ibkr-watcher"
check "ibkr-equity-logger active"       "systemctl is-active --quiet ibkr-equity-logger"

echo ""
echo "=== last 5 watcher log lines ==="
tail -5 /var/log/ibkr_watcher.log 2>/dev/null || echo "  (no log yet)"

echo ""
echo "=== last 5 equity log lines ==="
tail -5 /var/log/ibkr_equity.log 2>/dev/null || echo "  (no log yet)"

exit $fail
