# trading-gui — Lovable redesign of the trading dashboard

New frontend for the trading stack, built with [Lovable](https://lovable.dev) against the existing `ng_dashboard` FastAPI backend (which stays untouched — NiceGUI `/app` keeps working during the transition).

## Workflow
1. **Lovable project**: create a new project at lovable.dev.
2. **Connect GitHub**: in Lovable → GitHub → connect the `Quarki33` account and select **this repo** (`trading-gui`). Lovable will push its generated app code here on every edit; the existing autopush/backup discipline then covers it like every other repo. Keep the repo **private** (standing rule: no collaborators, ever).
3. **First prompt**: paste the contents of `LOVABLE_PROMPT.md`. Attach or paste `API.md` when it asks about the backend.
4. **Auth**: when the preview loads, open Settings in the app and enter the public base URL and the **owner key** (from `~/trading-data/dashboard_keys.json` on the Mac). Never commit the key.
5. **Iterate** in Lovable; code lands here automatically. Local pulls: `git pull` in this directory.

## Backend facts (already verified 2026-07-27)
- CORS is open (`*`) on the API; auth is per-request `x-api-key` — a Lovable-hosted app can call the public URL directly.
- The public gate default-denies proxied requests without a key; tailnet/localhost bypass.
- `/api/portfolio` live data was fixed 2026-07-27 (legacy duplicate service without live keys was removed) — the API now reports the real account.

## Deploy target (later)
Option A: keep using Lovable hosting (app calls the public API with the key).
Option B: build static bundle from this repo and serve it from `ng_dashboard` `static/` behind the existing key gate (fully self-hosted). Preferred once the design settles.
