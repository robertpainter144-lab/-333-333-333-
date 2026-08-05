---
name: Infra Architecture
description: Three-machine trading infra — DO (real money), Hetzner (paper + backend), Oracle (cold). Tailnet IPs, what runs where, costs.
type: project
originSessionId: 6d2c613e-6afd-47a4-b3e0-4fe438229e25
---
As of 2026-06-20, the trading system spans three cloud machines plus the Mac.
**Strict separation: real money (IBKR) and paper money (Alpaca) never share a process.**

## Machines on the tailnet

| Tailnet IP | Hostname | Role | Cost | Specs |
|---|---|---|---|---|
| 100.71.5.12 | `quarki3s-macbook-air` | Mac (dev box; can be off) | n/a | the laptop |
| 100.112.115.66 | `ibkr-trader` | **DigitalOcean droplet, NYC3** | ~$6/mo | 1 vCPU / 1 GB / 35 GB. Public IPv4 `134.209.36.167`. |
| 100.84.238.51 | `trading-vps` | **Hetzner CPX11, Hillsboro** | $20/mo | 2 vCPU / 2 GB + 4 GB swap / 38 GB. Public IPv4 `5.78.111.168`. |
| 100.73.225.4 | `oracle-trader` | Oracle Cloud Always-Free, currently SSH-blocked | $0 | A1.Flex ARM; cluster/cold backup |
| 100.126.150.36 | `roberts-z-flip4` | Android phone | n/a | Galaxy Z Flip 4 |

## What runs where

### DigitalOcean (`100.112.115.66`) — real money only
- `ibkr-gateway.service` — IB Gateway 10.45 headless via Xvfb + IBC 3.21.0. **REAL MONEY.**
- `ibkr-equity-logger.service` — polls Gateway every 60 s → `equity_history.csv`.
- `ibkr-fill-sync.service` — fills history → `transactions.csv` every 60 s.
- `ibkr-dashboard.service` — old NiceGUI dashboard, port 8081 (legacy, kept).
- `ibkr-sidecar.service` — small FastAPI on port 8082 exposing `/positions`, `/equity_history`, `/transactions` over tailnet.
- IBKR creds + 2FA bypass live on this box only. Don't co-locate with paper.
- Code dir: `/home/ubuntu/trading/`. Venv: `/home/ubuntu/trading/venv/`.

### Hetzner (`100.84.238.51`) — paper money + unified Android backend
- `ng-dashboard.service` — FastAPI + NiceGUI on port 8080. Serves the Android app.
  - Alpaca Paper, Alpaca Broker SB (paper), Shadow A/B engine: all in-process.
  - IBKR data: fetched read-only from DO's sidecar at `http://100.112.115.66:8082`.
- Code dir: `/opt/trading/`. Venv: `/opt/trading/venv/`.
- Env (Alpaca keys): `/opt/trading/secrets.env`, perms 600.

### Mac (`100.71.5.12`) — dev only
- Source of truth for the code (everything edits here, syncs to repo + VPS).
- Auto-push: `~/Library/LaunchAgents/com.quarki3.trading-sync.plist` runs `scripts/auto_sync.sh` every 600 s — mirrors all dirs into `/Users/quarki3/trading-system/`, commits, pushes to `github.com/Quarki33/trading-system` main.
- Mac dashboard (`ng_dashboard/main.py` at `100.71.5.12:8080`) is legacy; Android no longer needs it. Kept running but can be killed when convenient.

## Android app default backend
`build.gradle.kts → DEFAULT_BACKEND = http://100.84.238.51:8080` (Hetzner). User can override via Settings.

## Don'ts that already bit us
- **Don't trust the Mac-side `ibkr_bot/` CSVs** — they were stale for 3 days while DO had the truth. DO is canonical for IBKR data.
- **Don't try to put IB Gateway on Hetzner** — DO already runs it, and we wasted time installing a redundant copy. Killed.
- **Don't paste real creds into Claude chat.** When credential collection is needed, deploy a one-shot HTTP form on the VPS (pattern in chat history 2026-06-20).

## Why this works
| Concern | Mitigation |
|---|---|
| Real money corrupted by paper-side bug | Separate process + separate machine |
| Mac off / dies | All services on cloud; Mac is just an editor |
| Code/state lost | Auto-push every 10 min to GitHub |
| Tailnet ACL drift breaks IBKR pull | Hetzner adapter fails open with empty data, never blocks Gateway |
