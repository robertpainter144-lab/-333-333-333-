---
name: IBKR bot headless Oracle deployment (in progress)
description: Plan + scaffolding to run ibkr_bot + IB Gateway on Oracle Cloud so Mac can be off; scaffolded 2026-06-26, not yet provisioned
type: project
originSessionId: d207120d-c6c4-4118-a720-bca459d749a8
---
Goal: get `~/ibkr_bot` off the Mac. Currently the bot requires IB Gateway 10.46 running on the Mac with interactive login; if the Mac sleeps or Gateway crashes, the bot goes blind (this happened 2026-06-23 → 2026-06-26, 3 days dark).

**Approach (chosen, "Option A"):**
- Provision an Oracle Cloud Ampere A1 free-tier VM.
- Run `ghcr.io/gnzsnz/ib-gateway:stable` Docker image on that VM with IBC auto-login.
- Bind container port to `127.0.0.1:4001` only (never expose to internet).
- Run watcher.py + equity_logger.py as systemd units ON THE SAME VM → 127.0.0.1 stays valid → no tailnet exposure of 4001 needed.
- Mac → VM access via SSH over Tailscale (existing tailnet).

**Why not other options:**
- Option B (rewrite to ibeam/REST): too heavy. `stop_manager`, `watcher`, `equity_logger` all use `ib_insync` patterns that don't map cleanly to the Client Portal Web API.
- Option C (bot on Oracle, gateway on Mac via Tailscale): doesn't solve the problem — Mac still has to be on.
- Reusing oracle-trader VM: only 488 MB available, no docker, would compete with qc_portfolio.
- Reusing companion VM: only 432 MB available, hosting 3 containers already.

**What's already done (2026-06-26):**

`/Users/quarki3/ibkr_bot/deploy/oracle/`:
- `compose/docker-compose.yml` — gnzsnz/ib-gateway image, IBC config via env, 127.0.0.1-bound ports, daily auto-restart 23:55 ET.
- `compose/env.example` — TWS_USERID, TWS_PASSWORD, TRADING_MODE, TWOFA_DEVICE, VNC_PASSWORD.
- `systemd/ibkr-watcher.service`, `ibkr-equity-logger.service` — systemd units running under ubuntu user, EnvironmentFile=/home/ubuntu/ibkr_bot/runtime.env.
- `systemd/runtime.env.example` — IBKR_GATEWAY_HOST, IBKR_GATEWAY_PORT, Pushover creds.
- `scripts/bootstrap.sh` — installs docker, python3.10, tailscale; creates venv; installs ib_insync. Idempotent.
- `scripts/push.sh` — rsync from Mac to VM (bot source + compose + systemd units). Set ORACLE_HOST env var.
- `scripts/verify.sh` — smoke-tests container + systemd + port + log tails. Run on VM.
- `CUTOVER.md` — full runbook including IB Key 2FA prerequisite and rollback.

**Source code changes already in main branch (Mac copy):**
- `watcher.py`, `equity_logger.py`, `main.py` now read `IBKR_GATEWAY_HOST` (default 127.0.0.1) and `IBKR_GATEWAY_PORT` (default 4001) from env. Same code runs Mac or VM.

**What Robert still needs to do (cannot be automated):**
1. **IBKR side:** enable IB Key mobile push 2FA for account U24971994. Without this, IBC will hang at the 2FA challenge daily.
2. **Oracle Console:** provision Ampere A1 VM (2 OCPU, 6 GB, Ubuntu 22.04). Last note said A1 was out of capacity in AD-1 → try AD-2/AD-3, or fall back to E2.1.Micro.
3. **Add SSH alias** `ibkr-gw` to `~/.ssh/config`.
4. **Run cutover** per `CUTOVER.md`.

**Why:** Mac sleep + closed lid + crashes were leaving the live $500 IBKR bot blind for days at a time. Robert finally said "do whatever it takes" 2026-06-26 after the IB Gateway outage was identified.

**How to apply:**
- If Robert provisions the VM and asks for help finishing the cutover, walk through CUTOVER.md step by step.
- If anything fails during cutover, the rollback section returns to the Mac setup in one command — no positions at risk (GTC + OCA brackets persist on IBKR's servers).
- `com.quarki3.spy-overlay` ALSO uses local IB Gateway; once VM is up, add `IBKR_GATEWAY_HOST=100.x.y.z` to its launchd plist OR migrate it too.
- The NiceGUI dashboard (`com.quarki3.ibkr-dashboard`, `com.quarki3.ibkr-sidecar`) stays on Mac for now; reads equity_history.csv locally. Cross-host sync is a separate problem.
