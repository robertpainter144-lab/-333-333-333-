# Deploying the stack to any host ("no host is special")

Written 2026-08-07 after the Oracle billing suspension took the whole stack
down for two days. Lessons encoded here:

1. **Any Linux box must be able to become the stack in under an hour** —
   `bootstrap.sh` does that from this repo + a secrets bundle.
2. **Every stack host is watched by a dead-man pager** — the Mac's
   `host_heartbeat.py` (launchd, 5 min) pages ntfy on up→down transitions.
   Add every new host to `~/trading-data-local/heartbeat_hosts.json`.
3. **Live order flow never starts implicitly.** `bootstrap.sh` brings up the
   dashboard and audit timers only; enabling `quarki-scanner` (which fires
   real Alpaca orders) is always an explicit human command.

## Quick start (fresh Ubuntu 22.04+ VPS)

```bash
# on the Mac: build + ship secrets (NEVER commit these)
tar czf /tmp/stack-secrets.tgz -C ~ alpaca_live.env ntfy.env healthchecks.env \
    trading-data/dashboard_keys.json
scp /tmp/stack-secrets.tgz <user>@<host>:~/stack-secrets.tgz
scp ~/lab333/secrets/github_token <user>@<host>:~/github_token

# on the new box
curl -fsSLO https://raw.githubusercontent.com/robertpainter144-lab/-333-333-333-/app-333/deploy/bootstrap.sh
sudo bash bootstrap.sh
tailscale up
```

Then, deliberately and per-lane:

```bash
sudo systemctl enable --now quarki-labbook.timer     # paper lanes first
sudo systemctl enable --now quarki-scanner           # LIVE orders — human call
```

## Host status ledger

| Host | Role | Since | Watched by heartbeat |
|---|---|---|---|
| Oracle VM (129.153.96.201) | primary (suspended — billing, 2026-08-06) | 2026-06-15 | yes (expect_up=false until revived) |
| Mac (quarki3s-macbook-air) | emergency failover: dashboard:8080, lab333-api:8334, guardrail, funnel | 2026-08-07 | is the watcher |

## Known gaps (v0)

- `bootstrap.sh` has not yet been exercised on a truly fresh box — first
  real deploy should be done attended.
- Units assume `/home/ubuntu`; bootstrap rewrites paths, but scripts with
  hardcoded `/home/ubuntu` inside (audited 2026-08-07, most fixed) may
  still lurk in less-traveled lanes.
- The heartbeat watcher runs on the Mac; if the Mac itself is off, nothing
  watches the primary. External option (healthchecks.io free tier — email
  signup, no card) noted but NOT set up (user approval needed for any new
  service).
