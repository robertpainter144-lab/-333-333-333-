# Trading Companion

Private PWA that surfaces validated regime signals from Robert's research and
lets a trusted family member view their IBKR portfolio and place orders
against their own Alpaca paper or IBKR account.

**Audience:** one family member only. Not for public release.

## Why a PWA

- One codebase serves iPhone, Android, and desktop browsers.
- "Add to Home Screen" gets an app-like icon and full-screen mode on iOS/Android.
- No app store, no $99/yr Apple Developer, no signing certs, no Xcode.

## Why no QuantConnect dependency

An earlier iteration of this repo proxied QC's API to deploy strategies on the
family member's behalf. We dropped that — QC's API access is gated behind a
$84/mo subscription, which is too expensive for a single-user app. Strategy
authorship stays in the research repo (`trading-system`); the companion just
publishes the resulting regimes via a JSON feed.

## Architecture

```
┌──────────────┐    HTTPS     ┌──────────────────┐    HTTP    ┌──────────────┐
│  PWA (web/)  │ ───────────▶ │  FastAPI backend │ ─────────▶ │  ibeam       │
│  SvelteKit   │              │  (Oracle Cloud)  │            │  (IBKR CP)   │
└──────────────┘              └──────────────────┘            └──────┬───────┘
                                     │                               │
                                     ▼                               ▼
                              Alpaca paper API                  IBKR servers
```

- **PWA** stores backend URL + bearer token in `localStorage`. Three tabs:
  Signals, Portfolio, Settings.
- **Backend** serves the signal feed, proxies IBKR via `ibeam`, and submits
  Alpaca paper orders directly.
- **Signals feed** is a JSON file the backend reads; the research repo
  writes to it on each regime update.

Trust boundary: IBKR session lives **server-side** in `ibeam`. Alpaca keys
live in `backend.env`. The phone holds only a backend bearer token.

## Directory layout

```
backend/   FastAPI: signals + ibkr proxy + alpaca client
web/       SvelteKit PWA (adapter-static)
infra/     docker-compose for ibeam + backend + web + Caddy TLS
scripts/   auto_sync.sh + oracle_bootstrap.sh
docs/      PWA install, architecture, Oracle deploy guide
```

## Local dev

### Backend

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in COMPANION_API_KEY, Alpaca keys
uvicorn app.main:app --reload
```

### Web

```bash
cd web
npm install
npm run dev
```

## Deployment

See `docs/deploy.md`. Bootstrap script provisions Docker on a fresh Oracle
Cloud Ampere A1 VM, brings up `docker compose up -d`, and Caddy auto-issues
TLS for whichever hostname you pick.

## Auto-sync to GitHub

`scripts/auto_sync.sh` runs every 60 seconds via launchd
(`infra/launchd/com.quarki3.companion-sync.plist`). Stages, commits, and
pushes anything that changed.

## Status

Scaffold + signal feed + IBKR/Alpaca clients. Paper-mode default. Live IBKR
gated behind explicit settings toggle.
