---
name: stack-hosted-on-oracle-vm
description: 2026-07-27 FULL stack migration Mac→Oracle VM; Mac hosts NOTHING; all ops now via ssh + systemd on oracle-trader
metadata: 
  node_type: memory
  type: project
  originSessionId: 20280baf-085c-46dc-b24e-17271cfda81d
  modified: 2026-07-27T16:31:53.761Z
---

2026-07-27: User directive "don't host any part of it on the mac" → entire stack migrated to the Oracle trading VM. **The Mac is no longer the host for anything trading-related** — all 18 launchd trading services stopped+disabled (plists intact; nothing deleted per user's no-erase rule).

## VM ops facts
- SSH: `ssh -i ~/.ssh/oracle_trading ubuntu@129.153.96.201` (tailnet: oracle-trader / 100.73.225.4)
- Python: `/home/ubuntu/quarki-venv` (uv-managed 3.10.20; use `uv pip install --python ~/quarki-venv/bin/python`)
- Path shim: `/Users/quarki3 → /home/ubuntu` symlink (hardcoded Mac paths resolve)
- Secrets: `~/quarki.env` (EnvironmentFile for all units); git creds in `~/.git-credentials`
- Services: quarki-{scanner,dashboard,qc,paper-arm,soxl,poc10}.service; timers: quarki-watchdog (1min, `~/bin/vm_watchdog.py`), quarki-autopush (15min, `~/bin/vm-autopush.sh`, commits 8 repos), quarki-patent (daily)
- Restart pattern: `sudo systemctl restart quarki-<name>` (replaces launchctl kickstart — [[feedback_dashboard_restart]] is Mac-era)
- Caddy on VM proxies public URL → localhost:8080 (backup: Caddyfile.bak-mac-proxy)
- RAM is tight: 952Mi + 2G swap; units have MemoryMax caps — prefer lightweight additions
- Pre-existing separate deploy still running: qc-portfolio.service + qc-crypto.service from ~/trading/ (older duplicate qc engines, user never authorized stopping — candidates to retire WITH permission)

## Not migrated (stopped on Mac, documented)
ibkr-* + spy-overlay (need IB Gateway GUI on a desktop; IBKR dark since 07-20), swarm orchestrator (needs claude CLI auth), companion-sync/trading-sync (fed companion VM FROM the Mac — companion PWA data may go stale), mvy-watch.

## Android
Quarki3 Terminal app points at `http://oracle-trader.tail33de20.ts.net:8080/terminal/`; VM tailnet IP in cleartext allowlist. Old app also needs repointing if used (was Mac tailnet IP).

Related: [[project_oracle_deploy]], [[lab-vs-live-divergence-fixes]], [[project_trading_dashboard]]
