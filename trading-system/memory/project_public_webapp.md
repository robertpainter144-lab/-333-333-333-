---
name: Public Web App (Oracle→Mac Caddy proxy)
description: Permanent HTTPS URL for the mobile trading web app, no domain purchase needed
type: project
originSessionId: c777b35a-8784-4ed8-b477-cb1bcb76d2f3
---
# Public Web URL for Trading Dashboard

**URL:** `https://129-153-96-201.sslip.io/app`

Free, permanent, HTTPS. No domain purchase, no signup, no monthly fee.

## Architecture

```
Browser (anywhere on the internet)
  ↓ HTTPS 443
Oracle VM (129.153.96.201) — Caddy 2.11.4
  ↓ HTTP proxy via Tailscale (100.71.5.12:8080)
Mac (Quarki3s-MacBook-Air) — NiceGUI FastAPI dashboard
  → serves /app (webapp.html, mirrors Android UI)
  → serves /api/* (portfolio, qc, ticker, insider, patent-cliff)
```

Why `sslip.io` works: it's a wildcard DNS service — `129-153-96-201.sslip.io` (or the dotted form `129.153.96.201.sslip.io`) resolves to `129.153.96.201` unconditionally. No account or config on their side.

## Files/config

**Oracle VM** (`ssh -i ~/.ssh/oracle_trading ubuntu@129.153.96.201`):
- `/etc/caddy/Caddyfile`:
  ```
  129-153-96-201.sslip.io {
    reverse_proxy 100.71.5.12:8080 {
      header_up Host {upstream_hostport}
    }
  }
  ```
- `sudo systemctl status caddy` — Caddy runs on 80/443, LE cert auto-renewed
- iptables opened for 80/443 (persisted)
- Oracle VCN Security List already had 80/443 open (from a previous config)

**Mac**:
- `ng_dashboard/api.py` — added `@fastapi_app.get("/app")` that serves `static/webapp.html`
- `ng_dashboard/static/webapp.html` — mobile-first HTML/JS app, tabs (📕 Book / 🧪 Lab), position cards with single-tap→chart and double-tap→Finviz
- `ng_dashboard/static/` mounted at `/static/*`

## How to apply

- **Rebuild the webapp UI**: edit `~/ng_dashboard/static/webapp.html`, no restart needed — it's served fresh per request. If the change adds new API deps, `launchctl kickstart -k gui/501/com.quarki3.dashboard` restarts the FastAPI.
- **If the Mac's Tailscale IP changes**: update the `reverse_proxy` line in the Oracle Caddyfile, then `ssh ... sudo systemctl reload caddy`.
- **If dashboard is down on Mac**: the URL returns 502 from Caddy. Fix the Mac side and Caddy recovers.
- **If Oracle VM is down**: the URL is dead. Restart the VM in the Oracle console.

## Why not Tailscale Funnel

Tried it 2026-07-16 — Tailscale published only an AAAA (IPv6) record, no A (IPv4). Free-tier tailnet was IPv6-only via DERP, unreachable from most consumer networks. Kept it enabled but unused; the Oracle+Caddy path is the actual working setup.

## Why not Cloudflare Tunnel

Cloudflare "quick" tunnels (`*.trycloudflare.com`) work fine but URLs are ephemeral — change on every cloudflared restart. A named Cloudflare tunnel needs a domain (~$10/yr), which we skipped since the free Oracle+sslip.io path is equivalent.

2026-07-25: Public-access gate added in `api.py mount()` — X-Forwarded-For requests (Caddy) are default-denied without a key from `~/trading-data/dashboard_keys.json` (owner=full, guest=/guest + read-only portfolio/equity/activity/ticker, account ids stripped). Consumer guest page at `/guest` (`static/guest.html`, MVYTRADER branding). Direct tailnet/localhost unaffected. See [[project-github-sharing]]. NOTE: after `launchctl kickstart -k`, verify only ONE `main.py` owns :8080 (`lsof -nP -iTCP:8080`) — stale orphans shadow restarts.
