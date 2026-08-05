#!/usr/bin/env bash
# Idempotent bootstrap for a fresh Oracle Cloud Ampere A1 (or AMD) Ubuntu 22.04 VM.
# Run as the `ubuntu` user. Re-runnable.
#
# What it does:
#   1. Installs Docker, Python 3.10, ssh tools, tailscale.
#   2. Joins the tailnet (interactive — paste auth URL into a browser).
#   3. Creates /home/ubuntu/ibkr_bot/ skeleton, opens runtime.env for editing.
#   4. Installs systemd units for watcher + equity_logger (NOT yet enabled).
#   5. Drops the docker-compose for the gateway in /opt/ib-gateway/ (NOT yet started).
#
# After bootstrap, see scripts/finish_cutover.sh for the gated last-mile steps.

set -euo pipefail
SUDO=$(command -v sudo || echo "")

echo "=== ibkr_bot Oracle bootstrap ==="

# 1. apt deps
$SUDO apt update
$SUDO apt install -y python3.10 python3.10-venv python3-pip ca-certificates curl gnupg

# Docker (official repo)
if ! command -v docker >/dev/null; then
  $SUDO install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt update
  $SUDO apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  $SUDO usermod -aG docker ubuntu
  echo "  → docker installed. You may need to logout/login for group to take effect."
fi

# Tailscale
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | $SUDO sh
fi
if ! $SUDO tailscale status >/dev/null 2>&1; then
  echo "  → Joining tailnet. Open the URL it prints in any browser:"
  $SUDO tailscale up
fi

# 2. /opt/ib-gateway/
$SUDO mkdir -p /opt/ib-gateway
$SUDO chown ubuntu:ubuntu /opt/ib-gateway

# 3. /home/ubuntu/ibkr_bot/ skeleton
mkdir -p /home/ubuntu/ibkr_bot
if [ ! -d /home/ubuntu/ibkr_bot/venv ]; then
  python3.10 -m venv /home/ubuntu/ibkr_bot/venv
fi
/home/ubuntu/ibkr_bot/venv/bin/pip install --upgrade pip
/home/ubuntu/ibkr_bot/venv/bin/pip install ib_insync==0.9.86 yfinance pandas requests

# 4. Log dir
$SUDO touch /var/log/ibkr_watcher.log /var/log/ibkr_watcher.err \
            /var/log/ibkr_equity.log /var/log/ibkr_equity.err
$SUDO chown ubuntu:ubuntu /var/log/ibkr_watcher.* /var/log/ibkr_equity.*

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "Next steps (run scripts/finish_cutover.sh when ready):"
echo "  1. rsync the ibkr_bot/ source from your Mac to /home/ubuntu/ibkr_bot/"
echo "  2. Copy compose/.env (gateway creds) to /opt/ib-gateway/.env (chmod 600)"
echo "  3. Copy systemd/runtime.env to /home/ubuntu/ibkr_bot/runtime.env (chmod 600)"
echo "  4. cd /opt/ib-gateway && docker compose up -d"
echo "  5. tail logs until you see '2FA challenge' — approve on mobile"
echo "  6. sudo systemctl enable --now ibkr-watcher ibkr-equity-logger"
