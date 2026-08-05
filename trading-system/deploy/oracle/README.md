# Oracle Cloud Deploy — Trading Bots

Goal: move the prod QC bot + shadow A/B engine + NiceGUI dashboard off this Mac
onto an Always-Free Oracle ARM Ampere VM so progress persists 24/7 and survives
your Mac being closed.

Cost: $0 forever (Always-Free tier covers 4 vCPU / 24 GB RAM ARM).

---

## Step 1 — Provision the VM (~10 min, browser only)

You do this part in the Oracle Cloud Console. I can't because I don't have your login.

1. Sign in: <https://cloud.oracle.com/>. If you don't have an account, sign up for "Always Free" — credit card required for verification but never charged.
2. **Compute → Instances → Create Instance.**
3. Settings:
   - **Name:** `trading-vm`
   - **Image:** Canonical **Ubuntu 24.04** (Minimal is fine).
   - **Shape:** click "Change shape" → **Ampere → VM.Standard.A1.Flex** → 4 OCPU, 24 GB RAM (the whole free quota).
   - **SSH keys:** click "Generate a key pair for me" → **download both** the public and private key. Save them to `~/.ssh/oracle_trading` and `~/.ssh/oracle_trading.pub` on this Mac.
   - **Networking:** leave defaults (a new VCN gets created). We won't expose port 8080 publicly — Tailscale gives us a private path.
4. Click **Create**. Wait ~2 min for it to show "Running".
5. Note the **Public IPv4 address** from the instance page. You'll use it once to SSH in; after Tailscale is up we'll use the tailnet IP instead.

## Step 2 — First SSH + Tailscale auth (~5 min)

On this Mac:
```bash
chmod 600 ~/.ssh/oracle_trading
ssh -i ~/.ssh/oracle_trading ubuntu@<public-ip>
```

You're in the VM. Install Tailscale and join your tailnet:
```bash
curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up
# It prints a URL — open it on your phone or laptop and sign in as robert.m.painter@gmail.com.
tailscale ip --4   # note the 100.x.x.x address; we'll use this from now on.
```

Exit back to the Mac:
```bash
exit
```

## Step 3 — Push code from Mac and provision (~3 min)

From this Mac, after Step 2:
```bash
~/deploy/oracle/push.sh ubuntu@<vm-tailnet-ip>
ssh -i ~/.ssh/oracle_trading ubuntu@<vm-tailnet-ip>
bash /home/ubuntu/trading/deploy/oracle/setup.sh
```

`setup.sh` will write a `secrets.env` stub. Edit it once with your Alpaca keys:
```bash
nano /home/ubuntu/trading/secrets.env
```

Then start the services:
```bash
sudo systemctl enable --now qc-portfolio ng-dashboard
journalctl -fu qc-portfolio ng-dashboard
```

## Step 4 — Confirm from your phone

In mobile Chrome, with Tailscale connected on the phone:
```
http://<vm-tailnet-ip>:8080
```

You should see the same dashboard you have now, served from the Oracle VM.

## Step 5 — Shut the Mac bots down

Once the Oracle VM is up and running stable for a day, kill the Mac-side daemons so they don't double-trade against the same Alpaca paper account:
```bash
kill $(pgrep -f 'qc_portfolio/main.py --daemon')
kill $(pgrep -f 'ng_dashboard/main.py')
```

You can leave the PoC10 bot running or kill it too — your call.

---

## Architecture once deployed

```
Oracle Cloud Always-Free VM (Ubuntu 24.04, ARM A1.Flex, $0/month)
│
├── /home/ubuntu/trading/qc_portfolio/         ← prod bot (adaptive+lev stops)
├── /home/ubuntu/trading/qc_portfolio_test/    ← shadow next-gen experiment
├── /home/ubuntu/trading/ng_dashboard/         ← NiceGUI dashboard + shadow tick loop
├── /home/ubuntu/trading/secrets.env           ← env file with Alpaca keys (600 perms)
├── /etc/systemd/system/qc-portfolio.service   ← prod daemon, auto-restart, auto-boot
├── /etc/systemd/system/ng-dashboard.service   ← dashboard, auto-restart, auto-boot
└── /var/log/{qc-portfolio,ng-dashboard}.log   ← persistent logs

Tailscale on the VM   →   robert.m.painter@gmail.com tailnet
                          ↓
                Phone (Z Flip4) over Tailscale
                          ↓
                http://<vm-tailnet-ip>:8080
                          ↓
              "Install app" / Add to Home Screen → PWA
```

## What changes vs. the current Mac setup

- **Runs 24/7 without your Mac.** Always-on, even when your laptop sleeps.
- **Auto-restart on crash.** systemd respawns either service in 10 s.
- **Auto-start on reboot.** If Oracle ever reboots the VM, services come back automatically.
- **Persistent logs.** `journalctl -u qc-portfolio` history persists.
- **Same dashboard URL pattern.** Just point your phone at the VM's tailnet IP instead of the Mac's.

## Rollback

If you ever want to fall back to the Mac:
```bash
# On the VM:
sudo systemctl stop qc-portfolio ng-dashboard
# On the Mac:
cd /Users/quarki3/qc_portfolio && nohup python3.10 main.py --daemon >> /tmp/qc_portfolio.log 2>&1 &
nohup python3.10 /Users/quarki3/ng_dashboard/main.py >> /tmp/ng_dashboard.log 2>&1 &
```

State files (`portfolio_state.json`, `transactions.csv`, `state.json`) can be `scp`'d in either direction to preserve progress.
