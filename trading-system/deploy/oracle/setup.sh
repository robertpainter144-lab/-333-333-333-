#!/usr/bin/env bash
# Provision a fresh Ubuntu ARM Always-Free VM for the trading bots.
# Idempotent — safe to re-run.
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/ubuntu/trading}"

echo "== 1) system packages =="
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git rsync curl ufw

echo "== 2) Tailscale =="
if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sudo sh
fi
# (User authenticates with: sudo tailscale up — interactive, do once after first boot)

echo "== 3) Python deps =="
pip3 install --break-system-packages --user -r "${REPO_DIR}/deploy/oracle/requirements.txt"

echo "== 4) firewall =="
sudo ufw allow OpenSSH
sudo ufw --force enable
# 8080 is NOT exposed to the public internet; only reachable over the tailnet.
# Oracle's default iptables also blocks it; that's intentional.

echo "== 5) secrets.env check =="
if [ ! -f "${REPO_DIR}/secrets.env" ]; then
    cat <<'EOF' > "${REPO_DIR}/secrets.env"
# Paste your Alpaca keys here (no quotes, no export prefix).
ALPACA_PAPER_KEY=
ALPACA_PAPER_SECRET=
ALPACA_BROKER_KEY=
ALPACA_BROKER_SECRET=
ALPACA_BROKER_ACCT=
EOF
    chmod 600 "${REPO_DIR}/secrets.env"
    echo "  → wrote ${REPO_DIR}/secrets.env stub. Edit it before starting services."
fi

echo "== 6) systemd units =="
sudo cp "${REPO_DIR}/deploy/oracle/systemd/qc-portfolio.service" /etc/systemd/system/
sudo cp "${REPO_DIR}/deploy/oracle/systemd/ng-dashboard.service" /etc/systemd/system/
sudo touch /var/log/qc-portfolio.log /var/log/ng-dashboard.log
sudo chown ubuntu:ubuntu /var/log/qc-portfolio.log /var/log/ng-dashboard.log
sudo systemctl daemon-reload

echo
echo "== DONE =="
echo "Next steps (manual):"
echo "  1) sudo tailscale up                # authenticate the VM to your tailnet"
echo "  2) edit ${REPO_DIR}/secrets.env     # paste Alpaca keys"
echo "  3) sudo systemctl enable --now qc-portfolio ng-dashboard"
echo "  4) journalctl -fu qc-portfolio ng-dashboard"
echo "  5) http://<vm-tailnet-ip>:8080 from your phone over Tailscale"
