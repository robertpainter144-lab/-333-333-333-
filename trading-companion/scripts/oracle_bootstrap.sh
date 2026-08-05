#!/bin/bash
# oracle_bootstrap.sh — provision a fresh Ubuntu 22.04 Oracle Cloud VM to
# run the trading-companion stack. Run as root on the VM:
#
#   curl -fsSL https://raw.githubusercontent.com/Quarki33/trading-companion/main/scripts/oracle_bootstrap.sh | sudo bash
#
# Idempotent: re-runs safely; skips steps that already completed.

set -euo pipefail

REPO_URL=https://github.com/Quarki33/trading-companion.git
INSTALL_DIR=/opt/trading-companion

log() { echo "[bootstrap] $*"; }

# --- 1. Docker --------------------------------------------------------------

if ! command -v docker >/dev/null 2>&1; then
    log "installing Docker"
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg lsb-release git
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -y
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
fi

# --- 2. Firewall ------------------------------------------------------------

if command -v ufw >/dev/null 2>&1; then
    ufw allow OpenSSH || true
    ufw allow 80/tcp || true
    ufw allow 443/tcp || true
    ufw --force enable
fi

# Oracle's iptables ruleset blocks everything by default; this opens 80/443.
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT || true
iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT || true
netfilter-persistent save 2>/dev/null || iptables-save > /etc/iptables/rules.v4 2>/dev/null || true

# --- 3. Clone repo ----------------------------------------------------------

if [ ! -d "$INSTALL_DIR/.git" ]; then
    log "cloning repo to $INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
else
    log "repo already cloned; pulling latest"
    git -C "$INSTALL_DIR" pull --ff-only
fi

cd "$INSTALL_DIR/infra"

# --- 4. Skeleton env files (don't overwrite if they already exist) ---------

if [ ! -f backend.env ]; then
    log "creating infra/backend.env skeleton"
    cat > backend.env <<'EOF'
COMPANION_API_KEY=replace-me
IBEAM_BASE_URL=https://ibeam:5000
SIGNALS_PATH=/app/signals/regimes.json

# Family member's Alpaca paper-trading keys (sign up at alpaca.markets).
ALPACA_PAPER_KEY=
ALPACA_PAPER_SECRET=

DAILY_NOTIONAL_CAP_USD=50
DEFAULT_TRADING_MODE=paper
DATA_PATH=/app/data
EOF
fi

if [ ! -f ibeam.env ]; then
    log "creating infra/ibeam.env skeleton"
    cat > ibeam.env <<'EOF'
IBEAM_ACCOUNT=
IBEAM_PASSWORD=
IBEAM_GATEWAY_BASE_URL=https://localhost:5000
IBEAM_INPUTS_DIR=/srv/inputs
IBEAM_OUTPUTS_DIR=/srv/outputs
EOF
fi

mkdir -p data signals

# --- 5. Hostname in Caddyfile ----------------------------------------------

if grep -q "companion.example.com" Caddyfile; then
    echo
    echo "Edit infra/Caddyfile and replace 'companion.example.com' with"
    echo "your real hostname (e.g. companion.yourdomain.com or"
    echo "<something>.duckdns.org)."
    echo
    read -r -p "Hostname (leave blank to skip and edit later): " HOSTNAME_INPUT
    if [ -n "${HOSTNAME_INPUT:-}" ]; then
        sed -i "s/companion.example.com/${HOSTNAME_INPUT}/g" Caddyfile
    fi
fi

# --- 6. Bring it up --------------------------------------------------------

log "running docker compose up -d"
docker compose up -d

echo
echo "================================================================"
echo "Bootstrap done. Next:"
echo "  1. Fill in real secrets in infra/backend.env and infra/ibeam.env"
echo "  2. docker compose up -d   # to pick up env changes"
echo "  3. docker compose logs -f caddy   # watch TLS provisioning"
echo "  4. Open https://<your-hostname>/ in a browser"
echo "================================================================"

# Uncomment to auto-pull repo changes every 5 min:
# echo "*/5 * * * * cd $INSTALL_DIR && git pull --ff-only && docker compose -f infra/docker-compose.yml up -d --build" \
#   | crontab -
