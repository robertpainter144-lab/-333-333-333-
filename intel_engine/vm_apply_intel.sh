#!/bin/bash
# vm_apply_intel.sh — install the D40 intel engine on quarki-stack.
# Run as root:  sudo bash /home/ubuntu/lab-stack/intel_engine/vm_apply_intel.sh
set -e
REPO=/home/ubuntu/lab-stack

echo "== [1/4] pull latest lab repo =="
sudo -u ubuntu git -C "$REPO" pull --rebase -q || true
test -f "$REPO/intel_engine/collect.py"

echo "== [2/4] systemd units =="
cat > /etc/systemd/system/quarki-intel.service << 'EOF'
[Unit]
Description=Quarki intel engine — news/social/sentiment collector (D40)
[Service]
Type=oneshot
User=ubuntu
WorkingDirectory=/home/ubuntu/lab-stack/intel_engine
ExecStart=/usr/bin/python3 /home/ubuntu/lab-stack/intel_engine/collect.py
EOF
cat > /etc/systemd/system/quarki-intel.timer << 'EOF'
[Unit]
Description=Quarki intel engine every 30 min
[Timer]
OnBootSec=3min
OnUnitActiveSec=30min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now quarki-intel.timer

echo "== [3/4] first collection (takes ~1 min, Reddit is rate-limit shy) =="
systemctl start quarki-intel.service
sleep 2
tail -3 "$REPO/intel_engine/state/intel_history.jsonl" 2>/dev/null || echo "  (history starts on first success)"

echo "== [4/4] restart dashboard to load /api/crypto/intel + /api/equity/intel =="
systemctl restart quarki-dashboard.service
sleep 12
echo "crypto/intel:"; curl -s -m 8 127.0.0.1:8080/api/crypto/intel | head -c 200; echo
echo "equity/intel:"; curl -s -m 8 127.0.0.1:8080/api/equity/intel | head -c 200; echo
echo "INTEL_APPLY_DONE"
