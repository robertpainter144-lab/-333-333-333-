#!/bin/bash
# bootstrap.sh — rebuild the full trading stack on ANY fresh Ubuntu 22.04+/
# Debian 12+ box (v0, 2026-08-07, written after the Oracle outage).
#
# Design goal: no host is special. A replacement VPS, a revived Oracle VM,
# or a borrowed machine becomes the stack in <1 hour:
#
#   1. On the Mac, build the secrets bundle (never in git):
#        tar czf stack-secrets.tgz -C ~ alpaca_live.env ntfy.env \
#            trading-data/dashboard_keys.json
#      and scp it to the new box as ~/stack-secrets.tgz
#   2. Put a GitHub PAT with repo-read on the box at ~/github_token
#   3. sudo bash bootstrap.sh
#
# What it does NOT do (deliberate):
#   - does not start live-order services (quarki-scanner stays disabled until
#     a human runs:  sudo systemctl enable --now quarki-scanner
#     — bringing live order flow up is always an explicit human act)
#   - does not install IB Gateway (manual, needs GUI + 2FA)
set -euo pipefail

STACK_USER="${STACK_USER:-stack}"
REPO="${REPO:-robertpainter144-lab/-333-333-333-}"
HOME_DIR="/home/$STACK_USER"

echo "== [1/7] system packages =="
apt-get update -qq
apt-get install -y -qq git python3 python3-pip python3-venv curl rsync jq

echo "== [2/7] stack user =="
id "$STACK_USER" &>/dev/null || useradd -m -s /bin/bash "$STACK_USER"

echo "== [3/7] tailscale =="
if ! command -v tailscale &>/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
fi
echo "  -> run 'tailscale up' manually after bootstrap (interactive auth)"

echo "== [4/7] clone repo =="
TOKEN=$(tr -d '[:space:]' < "$HOME_DIR/github_token" \
        || tr -d '[:space:]' < ~/github_token)
sudo -u "$STACK_USER" git clone -q \
    "https://x-access-token:${TOKEN}@github.com/${REPO}.git" \
    "$HOME_DIR/stack" 2>/dev/null || \
    (cd "$HOME_DIR/stack" && sudo -u "$STACK_USER" git pull -q)
# scrub the token from the saved remote
cd "$HOME_DIR/stack" && sudo -u "$STACK_USER" git remote set-url origin \
    "https://github.com/${REPO}.git"

echo "== [5/7] python deps =="
sudo -u "$STACK_USER" python3 -m pip install -q --user --break-system-packages \
    -r "$HOME_DIR/stack/deploy/requirements.txt"

echo "== [6/7] secrets =="
if [ -f "$HOME_DIR/stack-secrets.tgz" ]; then
    sudo -u "$STACK_USER" tar xzf "$HOME_DIR/stack-secrets.tgz" -C "$HOME_DIR"
    chmod 600 "$HOME_DIR/alpaca_live.env" 2>/dev/null || true
else
    echo "  !! $HOME_DIR/stack-secrets.tgz missing — services will start" \
         "but nothing that needs keys will work"
fi

echo "== [7/7] systemd units =="
# Units in the repo assume /home/ubuntu — rewrite for the stack user's home.
mkdir -p /etc/systemd/system
for u in "$HOME_DIR/stack/trading-stack/vm/systemd/"*; do
    sed "s|/home/ubuntu|$HOME_DIR|g" "$u" > "/etc/systemd/system/$(basename "$u")"
done
systemctl daemon-reload
# Enable the SAFE core only: dashboard + watchdog + heartbeat-class timers.
# Live-order services (quarki-scanner and friends) stay OFF by design.
SAFE_UNITS="quarki-dashboard.service directive-audit.timer quarki-ddcap.timer"
for u in $SAFE_UNITS; do
    [ -f "/etc/systemd/system/$u" ] && systemctl enable --now "$u" && echo "  up: $u"
done

echo
echo "Bootstrap done. Manual steps remaining:"
echo "  1. tailscale up            (authenticate this box to the tailnet)"
echo "  2. review 'systemctl list-unit-files quarki-*' and enable the lanes"
echo "     you want; live order flow needs an explicit:"
echo "       sudo systemctl enable --now quarki-scanner"
echo "  3. add this host to heartbeat_hosts.json on the Mac (dead-man pager)"
