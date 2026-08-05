# IBKR bot → Oracle Cloud cutover runbook

Goal: bot + IB Gateway both run on a free-tier Oracle VM. Mac can be off, terminal closed, the works.

## Prerequisites you must complete (cannot be automated)

### 1. IBKR-side: enable IB Key mobile push 2FA

`gnzsnz/ib-gateway-docker` + IBC can keep the JVM session alive, but IBKR Pro live accounts (yours: `U24971994`) still require periodic 2FA approval. IB Key on a mobile is the only path that works unattended:

1. On your phone, install **IBKR Mobile** (App Store / Play Store).
2. Sign in once with `U24971994`.
3. In IBKR Mobile → Settings → IB Key → enable. Set the activation code from Client Portal → User Settings → Secure Login System.
4. Confirm: when you log in to Client Portal in a browser, you should get a push notification on the phone instead of needing a secure-code card.

Without this, IBC will get hung at the 2FA challenge daily and the container will need to be restarted manually.

### 2. Oracle Console: provision the VM

1. Sign in to Oracle Cloud.
2. Compute → Instances → Create instance.
3. Shape: **VM.Standard.A1.Flex** (Ampere ARM, free tier). Memory says A1 was out of capacity in AD-1 last time — try AD-2 or AD-3, or fall back to `VM.Standard.E2.1.Micro` (AMD) with the caveat that it has only ~952 MB RAM and you'd want to drop the qc_portfolio bot from `oracle-trader` and consolidate (not recommended).
4. OCPUs: 2, Memory: 6 GB (well under the free 4 OCPU / 24 GB monthly limit).
5. OS: Ubuntu 22.04 LTS.
6. SSH: add your existing `~/.ssh/oracle_trading.pub` (or generate a new keypair).
7. VCN: re-use the existing `vcn-20260615-2238`. Subnet: create a new one or reuse.
8. **Do not** open port 4001 in the security list. Gateway is bound to 127.0.0.1 inside the VM only.

Note the public IP and Tailscale IP after provisioning. Suggested SSH alias in `~/.ssh/config`:

```
Host ibkr-gw
    HostName 100.x.y.z          # tailnet IP after `tailscale up`
    User ubuntu
    IdentityFile ~/.ssh/oracle_trading
```

## The cutover

```bash
# On the new VM (SSH in first)
curl -fsSL https://raw.githubusercontent.com/.../bootstrap.sh -o bootstrap.sh
# Or copy from your Mac:
scp /Users/quarki3/ibkr_bot/deploy/oracle/scripts/bootstrap.sh ibkr-gw:~/
ssh ibkr-gw bash ~/bootstrap.sh
# Tailscale will prompt with an auth URL — open it on any browser to join the tailnet.

# From your Mac — push the bot source + gateway compose
ORACLE_HOST=ibkr-gw /Users/quarki3/ibkr_bot/deploy/oracle/scripts/push.sh

# On the VM — fill in credentials
ssh ibkr-gw
cd /opt/ib-gateway
cp env.example .env
chmod 600 .env
vim .env                              # TWS_USERID, TWS_PASSWORD, TRADING_MODE=live

cd ~/ibkr_bot
cp deploy/oracle/systemd/runtime.env.example runtime.env
chmod 600 runtime.env
vim runtime.env                       # paste Pushover token/user from local pushover.env

# Start the gateway
cd /opt/ib-gateway
docker compose up -d
docker logs -f ib-gateway

# Within ~30s you should see a 2FA challenge on your phone. Approve it.
# Within ~90s the container should report "API server is ready".
# Verify: ss -ltn | grep 4001  → 127.0.0.1:4001 listening

# Start the bot services
sudo systemctl enable --now ibkr-watcher ibkr-equity-logger
sudo bash ~/ibkr_bot/deploy/oracle/scripts/verify.sh

# On the MAC — turn off the local launchd jobs so they don't double-trade
launchctl bootout gui/$(id -u)/com.quarki3.ibkr-watcher
launchctl bootout gui/$(id -u)/com.quarki3.ibkr-equity-logger
# (You can also quit local IB Gateway 10.46 now.)
```

## What's intentionally NOT solved here

- **`com.quarki3.ibkr-sidecar`, `com.quarki3.ibkr-dashboard`** still run on the Mac. The NiceGUI dashboard reads `equity_history.csv` from the Mac filesystem, so if you want the dashboard to follow the bot to Oracle, you'd need to either (a) rsync `equity_history.csv` Oracle→Mac periodically, or (b) move the dashboard too. Pick one later.
- **`com.quarki3.spy-overlay`, `com.qcportfolio.ibkr`** are separate jobs — leave them on the Mac unless you also want to migrate them. The spy-overlay job uses IB Gateway too, so it currently won't work with Mac Gateway off. If you want spy-overlay to also use the remote gateway: set `IBKR_GATEWAY_HOST=100.x.y.z` in its launchd plist `EnvironmentVariables`.

## Rollback

If anything goes wrong, you can return to the Mac setup in one command:

```bash
# On the VM
sudo systemctl stop ibkr-watcher ibkr-equity-logger
cd /opt/ib-gateway && docker compose down

# On the Mac
open "/Users/quarki3/Applications/IB Gateway 10.46/IB Gateway 10.46.app"
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.quarki3.ibkr-watcher.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.quarki3.ibkr-equity-logger.plist
```

GTC orders and OCA brackets persist on IBKR's servers either way, so no positions are at risk during the swap.
