---
name: Trading Companion (PWA)
description: SvelteKit PWA + FastAPI/ibeam backend for a single trusted family member to view regime signals and trade their own IBKR — fully separated from Robert's personal stack
type: project
originSessionId: 2209c7dd-0431-458a-8b18-e3016da8ac50
modified: 2026-07-28T19:32:30.296Z
---
## What it is

User-facing companion app (PWA) for a single trusted family member to follow
the validated regime outputs of Robert's research repo (`trading-system`) and
trade their own IBKR account. Not public, not multi-tenant — one family member.

## Path & repo

- Local: `/Users/quarki3/trading-companion/`
- Remote: `https://github.com/Quarki33/trading-companion` (private, pushed 2026-06-20)
- Local main branch has two commits: initial scaffold (SwiftUI iOS) and a
  pivot to SvelteKit PWA.
- Uncommitted local change: onboarding URL auto-prefixes `https://` when scheme
  is missing — will fold into next commit.

## Stack

| Piece | Tech | Why |
|---|---|---|
| Client | SvelteKit 2 + Svelte 5 runes, `adapter-static` | Single PWA covers iPhone + Android + desktop. No Xcode required (Robert only had 7.6 GiB free; Xcode needs ~30 GiB). "Add to Home Screen" gives native-feel install. |
| Backend | FastAPI (Python 3.12), `httpx` | Proxies IBKR Client Portal Web API, serves regime feed. |
| IBKR gateway | `voyz/ibeam` Docker image | Wraps the official IBKR CP Gateway, handles 2FA + keepalive. iPhones can't run a gateway, so it lives server-side. |
| TLS / fan-in | Caddy | Routes `/auth /portfolio /signals /orders` to backend, everything else to web. |
| Hosting plan | Oracle Cloud (separate from personal `qc_portfolio` bot) | Robert's existing AMD micro VM only has 1 GB RAM and is busy. New deployment likely needs an Ampere A1 free-tier instance. |

## Why a PWA, not native iOS

Three options were weighed: native SwiftUI + SideStore, native SwiftUI +
TestFlight ($99/yr), or PWA. Robert had two blockers: no Apple Developer
account, and only 7.6 GiB free on his Mac (Xcode + Simulator need ~30 GiB).
PWA was chosen — same backend works, no install ceremony for the family
member, and Robert can test in any browser on his Mac.

Security tradeoff accepted: bearer token in `localStorage` instead of iOS
Keychain. Mitigated by keeping the IBKR session itself server-side in
`ibeam` (never on the phone) and planning short-lived backend tokens.

## Separation from personal stack

Family member's app and Robert's personal `trading-system` must stay
isolated. Current separation:

- Physically distinct directories (`trading-companion/` sibling of
  `trading-system/`, not nested).
- Different Docker stack on different ports.
- Separate `COMPANION_API_KEY` (rotated independently).
- Family member's IBKR creds live only in the companion stack's
  `ibeam.env` (never near Robert's IBKR bot creds).
- **One-way data flow:** research repo writes `signals/regimes.json`,
  companion backend reads it. No back-channel.

## Trust boundary

- Backend = trusted infra Robert operates.
- Phone holds: backend URL + bearer token only.
- IBKR session lives in `ibeam` server-side. Losing the phone never exposes
  IBKR creds.

## Family-member install flow

1. Family member opens the deployed URL in Safari on iPhone.
2. Share → "Add to Home Screen" → an icon lands on the home screen.
3. First launch shows the onboarding screen — they paste the backend URL +
   API token Robert shared. Stored in `localStorage`.
4. They tap "Connect IBKR" in Settings → browser navigates to
   `/auth/ibkr/start` → IBKR's own SSO page → session lives in `ibeam`.
5. Daily re-auth required (CP Gateway sessions die every 6–24h).

## Auto-sync (live as of 2026-06-20)

- `scripts/auto_sync.sh` + `~/Library/LaunchAgents/com.quarki3.companion-sync.plist`.
- Job label `com.quarki3.companion-sync`, 60s interval, RunAtLoad.
- Mirrors the pattern from `trading-system`'s `com.quarki3.trading-sync` but
  simpler (no rsync — repo is source of truth, not a mirror).
- Logs: `/tmp/trading-companion-autosync.log` + `.stderr.log`.
- To stop: `launchctl bootout gui/$(id -u)/com.quarki3.companion-sync`.

## QC-native architecture (refactored 2026-06-20)

The earlier ibeam/Alpaca-proxy direction was scrapped after Robert pointed
out QC already maintains direct FIX-IBKR + Alpaca integrations. The
companion is now a thin UX wrapper over QC's API:

- Strategy = single QC live deployment, pinned to one brokerage chosen
  at deploy time (`qc_paper`, `ibkr`, `alpaca_paper`, `alpaca_live`).
- "Deploy to two brokerages" = two QC deployments = two strategy records.
- `POST /strategies/from-code` does the full QC dance: create_project →
  upsert_file → create_compile → wait_for_compile → deploy_live.
- `POST /strategies/{id}/sync` is read-only: returns QC live state +
  holdings + equity.
- `DELETE /strategies/{id}` best-effort stops the QC live algo.
- **No brokerage credentials touch this backend.** Family member links
  IBKR/Alpaca in their QC portal once.
- **Modules deleted:** `app/ibkr.py`, `app/alpaca.py`, `app/auth.py`,
  `app/portfolio.py`, `app/orders.py`. Order-mirroring code gone.
- **infra/ibeam.env.example deleted.** `infra/docker-compose.yml` no
  longer has ibeam.
- Required env reduced to: `COMPANION_API_KEY`, `QC_USER_ID`,
  `QC_API_TOKEN`.

## QC dependency dropped (reverted 2026-06-20)

QC discontinued the cheap Personal tier; current tiers start at $84/mo
("Researcher"). Too expensive for a single-family-member app. The QC-native
refactor from earlier that day was reverted:

- Re-restored `app/ibkr.py`, `app/alpaca.py`, `app/auth.py`,
  `app/portfolio.py`, `app/orders.py` from git history.
- Re-added `ibeam` to `infra/docker-compose.yml` + restored
  `infra/ibeam.env.example`.
- Re-included auth/portfolio/orders routers in `main.py`.
- Restored `web/src/routes/portfolio/+page.svelte` and
  `web/src/routes/settings/+page.svelte` to broker-direct shape.
- **Strategies tab dropped from navigation** — was QC-only.
- `app/qc.py`, `app/strategies.py`, `app/storage.py` deleted.

Net architecture is now: PWA → backend → ibeam (IBKR) + direct Alpaca REST.
Family member needs an Alpaca paper key + their own IBKR account. No QC
subscription required.

## Deployment LIVE (2026-06-20)

- **Public URL:** `https://companion-rp.mooo.com` (Let's Encrypt cert via Caddy)
- **VM:** Oracle Cloud `companion`, AMD VM.Standard.E2.1.Micro (free tier),
  Ubuntu 22.04, public IP `152.70.137.159`, private IP `10.0.1.41`.
  Provisioned 2026-06-20.
- **VCN:** existing `vcn-20260615-2238` (shared with trading-system VM),
  new subnet `subnet-20260620-1409` CIDR `10.0.1.0/24`.
- **SSH access:** `ssh -i ~/.ssh/oracle_companion ubuntu@152.70.137.159` or
  just `ssh companion` (entry in `~/.ssh/config`).
- **Stack on VM:** `/opt/trading-companion/`. Three containers:
  `companion-web` (nginx + SvelteKit build), `companion-backend`
  (FastAPI), `companion-caddy` (TLS termination on 80/443).
  **ibeam container is commented out** in compose until IBKR creds are
  populated.
- **Generated `COMPANION_API_KEY`** (in `/opt/trading-companion/.companion-api-key`):
  `d2pT3wTtKNzZVYITTDdDOvXVIPdS4ggwqiH6QFx7`.
- **Domain:** `companion-rp.mooo.com` via FreeDNS afraid.org. A record
  points at `152.70.137.159`.
- **Update path:** `rsync -avz --delete --exclude=.git --exclude=node_modules
  --exclude=.svelte-kit --exclude=build --exclude=__pycache__ --exclude=.venv
  --exclude=*.log --exclude=backend/data /Users/quarki3/trading-companion/
  companion:/tmp/companion/` then `ssh companion 'sudo rsync -a /tmp/companion/
  /opt/trading-companion/ && sudo chown -R ubuntu:ubuntu /opt/trading-companion
  && cd /opt/trading-companion/infra && sudo docker compose up -d --build'`.
- **Dad onboarding:** opens URL in Safari → enters URL + token → tabs render
  → Share → Add to Home Screen. He has not yet been sent the link as of
  this writing.

## Deployment (planned, not yet executed)

- Target: **Oracle Cloud Ampere A1 free tier** (separate VM from the AMD
  micro VM running `qc_portfolio` for personal stack isolation).
- `docs/deploy.md` is the end-to-end guide (sign up → VM → DNS → SSH → run
  script).
- `scripts/oracle_bootstrap.sh` is idempotent: installs Docker, opens
  iptables 80/443, clones the repo to `/opt/trading-companion`, drops
  env skeletons, prompts for the hostname, runs `docker compose up -d`.
- Hostname: not yet picked. Free option = DuckDNS subdomain;
  $9/yr option = Cloudflare Registrar.
- Delivery to family member: email/text the URL + `COMPANION_API_KEY`.
  They Safari → Add to Home Screen.

## Order safety rails (live as of 2026-06-22)

`backend/app/risk.py` is the single gate for `/orders`. Every call goes
through `risk.preflight(order)` which enforces, in order:

1. `block_market_orders` (default true) — MKT orders rejected with 422.
2. `limit_price` + `quantity` required so notional is knowable.
3. Per-order notional ≤ `per_order_max_notional_usd` (default $100).
4. Cumulative accepted notional today ≤ `daily_notional_cap_usd`
   (default $200). State lives in `data/orders-YYYY-MM-DD.jsonl`.
5. `trading_mode` env: `"paper"` (default) logs but does not forward to
   ibeam; `"live"` forwards. Switch to `"live"` only when Dad is ready.

Every preflight call (accept or reject) appends a JSONL audit record.
`GET /orders/limits` returns current rails + today's used notional so
the PWA can render headroom. Smoke-tested 2026-06-22 against all six
rejection/accept paths.

## Open / pending

- Generate real PWA icons (currently auto-generated placeholders at
  `web/static/icons/icon-{180,192,512}.png`).
- Hook research repo's regime emitter to write `backend/signals/regimes.json`.
- Wire `/orders/limits` into the PWA portfolio screen so headroom is
  visible (backend done, no frontend yet).
- Send Dad the URL + `COMPANION_API_KEY` — not yet sent as of 2026-06-22.

## Intern developer build (2026-07-28)

Robert asked for a shareable dev version for intern Linus — testing side
only, no live trading. Built at `/Users/quarki3/trading-companion-dev/`
(fresh git repo, single commit, NO history from the private repo) plus
`~/Desktop/trading-companion-dev.zip` (31 KB, git-archive of HEAD).

Removed vs the real app: `app/ibkr.py`, `app/auth.py`, ibeam service +
env, Caddy, `scripts/` (auto_sync had Robert's email; bootstrap points at
his GitHub), launchd plist, `docs/deploy.md`, real regime signals
(replaced with fake SPY/rebalance demo data), all IBKR/QC UI (settings
REAL path, portfolio IBKR card, `ibkrStatus` API). Orders + portfolio
rewired to Alpaca **paper** only; `trading_mode` concept deleted
(hardcoded paper); risk rails kept. Verified: backend imports + smoke
test on py3.12 venv, `svelte-check` 0 errors, prod build OK.

Pushed 2026-07-28 to `Quarki33/MVYTraders` (private intern repo, Linus =
`lmunn1` already collaborator) as subdir `trading-companion/`, commit
283b0fe, CI hoisted to root workflows. Files were force-added past the
repo's over-broad root .gitignore. Zip also at
`~/Desktop/trading-companion-dev.zip`.

## Key local commands

```bash
# Backend dev
cd /Users/quarki3/trading-companion/backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
COMPANION_API_KEY=dev uvicorn app.main:app --reload

# Web dev (testable in any browser on the Mac, no Xcode)
cd /Users/quarki3/trading-companion/web
npm install
npm run dev   # http://localhost:5173
```
