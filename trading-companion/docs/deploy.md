# Deploying to Oracle Cloud

Target: a 24/7 public URL the family member can bookmark and "Add to Home
Screen" on iPhone Safari. No dependence on your Mac being on.

This guide assumes you can SSH into an Oracle Cloud VM. Total time: ~45 min
end-to-end, mostly waiting on Oracle's provisioning.

## 0. Prereqs you handle (account-level, can't be scripted)

1. **Oracle Cloud account** — sign up at https://www.oracle.com/cloud/free/.
   The "Always Free" tier includes 4 ARM (Ampere A1) cores and 24 GB RAM
   total, which is more than enough.
2. **Pick a region** that's free-tier-enabled and geographically close.
3. **Provision a VM:**
   - Compute → Instances → Create Instance
   - Image: **Canonical Ubuntu 22.04**
   - Shape: **VM.Standard.A1.Flex** — 2 OCPU, 12 GB RAM (well within free)
   - **Add an SSH public key** (use your existing `~/.ssh/id_ed25519.pub`
     or generate one with `ssh-keygen -t ed25519`)
   - Network: assign a public IPv4
4. **Open the firewall:**
   - Oracle Cloud's default VCN blocks everything. In the VCN's Security
     List, add ingress rules for TCP **80** and **443** from `0.0.0.0/0`.
   - Inside Ubuntu: `sudo ufw allow 80,443/tcp && sudo ufw enable`
5. **Get a domain** — the cheapest legit path is Cloudflare Registrar (~$9/yr).
   Free alternative for testing: a [DuckDNS](https://www.duckdns.org/)
   subdomain like `<name>.duckdns.org`.
6. **Point DNS at the VM's public IP:**
   - At your registrar (or DuckDNS), create an `A` record pointing
     `companion.<yourdomain>` at the public IP from step 3.

## 1. SSH in and run the bootstrap script

```bash
ssh ubuntu@<vm-public-ip>
curl -fsSL https://raw.githubusercontent.com/Quarki33/trading-companion/main/scripts/oracle_bootstrap.sh | sudo bash
```

The script:
- installs Docker + docker compose
- clones the repo to `/opt/trading-companion`
- creates `infra/backend.env` and `infra/ibeam.env` skeletons
- prompts for the values it can't guess (companion API key, IBKR creds,
  Alpaca keys, QC keys, the hostname)
- updates `infra/Caddyfile` to use your hostname
- runs `docker compose up -d`
- prints the public URL once Caddy has provisioned TLS

## 2. Populate secrets

The script will leave you with `infra/backend.env`, `infra/ibeam.env`, and a
ready-to-edit `infra/Caddyfile`. Fill in:

```bash
# infra/backend.env
COMPANION_API_KEY=<generate with: openssl rand -base64 32>
ALPACA_PAPER_KEY=<family member's paper key>
ALPACA_PAPER_SECRET=<same>
QC_USER_ID=<their QC user id>
QC_API_TOKEN=<their QC api token>

# infra/ibeam.env
IBEAM_ACCOUNT=<family member's IBKR username>
IBEAM_PASSWORD=<their IBKR password>
```

Then restart:
```bash
cd /opt/trading-companion/infra
docker compose up -d
docker compose logs -f caddy
```

The Caddy log should show `certificate obtained successfully` within ~30s.

## 3. Send the URL to the family member

Once `https://companion.<yourdomain>` returns the Set up screen in a
browser, email or text them two things:

```
URL:   https://companion.yourdomain.com
Token: <whatever you set as COMPANION_API_KEY>
```

They open the URL in **Safari** on iPhone, paste both into the Set up
screen, then Share → "Add to Home Screen." Done.

## 4. Keeping it updated

- Auto-sync on your Mac pushes commits to GitHub every minute.
- To pull updates onto the VM, SSH in and run:
  ```bash
  cd /opt/trading-companion && git pull
  docker compose -f infra/docker-compose.yml up -d --build
  ```
- Or wire up a cron job to do that automatically (see `scripts/oracle_bootstrap.sh`
  for a commented-out example).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Caddy logs `no such host` | DNS hasn't propagated | wait 5 min, retry |
| Caddy logs `connection refused` from `:80` | UFW or Oracle VCN blocking | re-check both firewalls |
| ibeam container restarting | bad IBKR password or 2FA wall | check `docker compose logs ibeam` |
| iPhone "Add to Home Screen" missing | not on Safari, or HTTP not HTTPS | use Safari, ensure TLS up |
| Family member can hit URL but onboarding fails | wrong API token | re-share `COMPANION_API_KEY` |
