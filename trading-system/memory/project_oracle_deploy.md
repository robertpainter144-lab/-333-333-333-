---
name: Oracle Cloud trading deployment
description: AMD micro VM running the qc_portfolio bot 24/7 with adaptive+lev stops; cutover from Mac done 2026-06-17
type: project
originSessionId: f79454ac-8313-46a2-b087-d3a15f0e06df
---
The prod QC portfolio bot now runs on an Oracle Cloud Always-Free AMD VM, not the Mac.

**VM identity**
- Hostname: `oracle-trader`
- Tailnet IP: `100.73.225.4`
- Public IP: `129.153.96.201`
- Shape: `VM.Standard.E2.1.Micro` (1 vCPU, 952 MB RAM, 45 GB disk, Ubuntu 20.04)
- ARM A1.Flex was out of capacity in AD-1 — fell back to AMD. Retry ARM later for the dashboard.
- SSH key: `~/.ssh/oracle_trading` (Oracle-generated; downloaded copy in `~/Downloads/ssh-key-2026-06-16.key*` — paper-only blast radius, OK for now)

**Runtime stack on VM**
- Python 3.11.15 installed via `uv` (not apt — deadsnakes PPA wasn't fetching packages).
- Venv at `/home/ubuntu/trading/venv`
- Code at `/home/ubuntu/trading/{qc_portfolio,qc_portfolio_test,alpaca_keys.py,deploy}`
- Secrets at `/home/ubuntu/trading/secrets.env` (chmod 600, contains ALPACA_* env vars)
- 2 GB swap file at `/swapfile` (in fstab)

**Systemd service**
- Unit: `/etc/systemd/system/qc-portfolio.service`
- Runs `venv/bin/python main.py --daemon` as user `ubuntu`
- Log: `/var/log/qc-portfolio.log` (`journalctl -u qc-portfolio` also works)
- Restart=on-failure, RestartSec=10

**Why:** Mac wasn't a viable 24/7 host (sleep, close lid, restarts). Oracle Always-Free VM is $0 forever.

**How to apply:**
- To deploy code changes: edit on Mac, then `rsync -avz -e "ssh -i ~/.ssh/oracle_trading" --delete --exclude '__pycache__' --exclude '*.log' --exclude '.daemon.lock' --exclude 'state.json' --exclude 'transactions.csv' qc_portfolio qc_portfolio_test alpaca_keys.py ubuntu@129.153.96.201:/home/ubuntu/trading/` then `ssh ubuntu@129.153.96.201 sudo systemctl restart qc-portfolio`.
- Do NOT run a second qc_portfolio daemon on the Mac — they'd double-rebalance the same Alpaca paper account.
- The ng_dashboard still runs on the Mac (PID 41625) and reads from the same Alpaca account, so the A/B page works without needing the VM to host the GUI. Moving the dashboard to Oracle requires an ARM VM (won't fit in 952 MB).
- Bundle scripts at `~/deploy/oracle/` (push.sh, setup.sh, systemd units) — note push.sh references Tailscale IP path; pass `ubuntu@100.73.225.4` to use it.
