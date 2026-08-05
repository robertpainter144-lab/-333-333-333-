# Lovable Master Prompt — "Quarki Trading Terminal"

Paste everything below the line into Lovable as the project's first prompt. Keep `API.md` from this repo in the project so Lovable can reference exact shapes.

---

Build a **dark-first, information-dense trading terminal PWA** called **Quarki Terminal** for a personal algorithmic-trading stack. React + TypeScript + Tailwind + shadcn/ui + Recharts. Mobile-first responsive (primary devices: Android phone + MacBook). No backend of your own — this is a pure API client for an existing FastAPI backend documented in `API.md`.

## Connection & auth (build this first)
- Settings screen (also shown on first launch): **API base URL** (default `https://129-153-96-201.sslip.io`) and **API key** (password field). Store both in localStorage. Send the key as `x-api-key` header on every request. A "Test connection" button hits `/api/health` and shows round-trip status.
- Global data layer: poll each endpoint on a 20-second interval with react-query (staleTime 15 s), visible "last updated Xs ago" indicator, automatic retry with backoff, and a global offline banner when `/api/health` fails.
- **Never hardcode any key or secret anywhere in the code.**

## Pages (left nav on desktop, bottom tabs on mobile)

### 1. Book (home) — the real money
- Hero: Alpaca Live equity as a large number, with two deltas side by side: **vs test baseline $48.06 (started 2026-07-27)** and vs inception $50.04 (2026-07-13). Green/red per sign.
- **Drawdown gauge**: absolute drawdown in dollars against the user's hard **$5 review trigger** (from `peak_equity − equity`). Amber ≥ $2.50, red ≥ $5 with a persistent banner.
- Equity area chart from `/api/equity/alpaca_live` with range tabs (1D/1W/ALL) and the baseline dates marked.
- Positions table from `/api/portfolio/alpaca_live`: symbol, qty, market value, unrealized P/L ($ and %), sortable; row tint by sign. Above it, a horizontal stacked bar comparing **current allocation vs target allocation** (targets from `/api/qc` `mirror_targets`) so drift is visible at a glance.
- Cash panel: cash vs settled buying power, with a "T+1 settling" chip when settled << cash (explains why buys are deferred, per API.md semantics).
- Recent actions feed from `/api/activity/alpaca_live`.
- Secondary card: IBKR Live from `/api/portfolio/ibkr` (usually dark/flat — render compactly).

### 2. Lab — paper strategies proving themselves
- Card grid, one per strategy from `/api/edge`: name, mode badge (paper/backtest-only), **verdict badge** (PROVEN green / PROMISING blue / UNPROVEN gray / LOSING red), total P/L %, expectancy, win rate, n trades with a progress ring toward the n≥30 gate, last-trade time. Everything here is clearly watermarked "PAPER".
- Detail drawer per strategy: stats + (where the strategy is one of the mirrors: qc_mirror, congress_mirror, patent_mirror, autopilot_mirror) its composition/targets if available.
- Explainer strip at top: "Strategies must prove n≥30 closed trades, expectancy ≥ 0.05%, Sharpe ≥ 1 before real money."

### 3. Governor — who's driving the live book
- Donut of current source weights (qc/patent/insider/whale/value/autopilot/congress) with the leader called out (e.g., "QC 92%").
- Scoreboard table: combined_LIVE vs each `*_PAPER` challenger (rolling P/L), consecutive-lead counters.
- Alerts timeline (sustained-lead and kill-switch alerts), newest first.
- If a dedicated endpoint is missing for governor state, show a friendly "endpoint not exposed yet" empty state (see API.md note) — do not fabricate.

### 4. Markets — the stack's signal sources
- Tabs: **QC** (strategy table from `/api/qc`: name, weight, 3-month return, Sharpe, holdings chips), **Patent Cliff** (`/api/patent-cliff` ranking table), **Insider** (`/api/insider/leaderboard` + portfolio), **Whales** (`/api/whales`), **Congress** (composition of the Pelosi tracker if exposed; else empty state).
- Ticker lookup: search box → `/api/ticker/{symbol}` mini chart + quote.

### 5. Health — is the machine alive
- Service grid from `/api/bots` (running/stopped, host, pid) + `/api/health` + `/api/version`.
- Poll staleness self-check: if any polled endpoint hasn't refreshed in >90 s, flag it here.
- Disk/watchdog note card (static text): watchdog alerts via Pushover; disk warnings mean act now.

## Design language
- Dark terminal aesthetic: near-black background (#0d1117 family), high-contrast numerals in a tabular mono font for all financial figures, subtle 1px borders, no gradients-for-decoration. Light mode as a toggle, dark default.
- Use the source colors from `/api/sources` consistently everywhere a source appears (alpaca_live #ffd60a, paper #58a6ff, broker #bc8cff, shadow #d29922, ibkr #3fb950).
- Positive/negative: green #3fb950 / red #f85149; never rely on color alone — include sign and arrows.
- Numbers: money to 2 dp with $; percents to 2 dp with explicit +/−; timestamps as relative ("3 m ago") with absolute on hover.
- Loading states: skeletons, never spinners over blank pages. Empty states explain *why* (e.g., "IBKR bot is dark — flat since 2026-07-20").
- Charts: Recharts area/line with muted gridlines; tooltips show exact values + timestamps; no 3-D, no chartjunk.

## Quality bar
- Type every API response (derive interfaces from API.md examples).
- All fetch logic in one typed client module with the base-URL + key injection.
- PWA manifest + icons so it installs on Android ("Quarki Terminal", dark theme color).
- Graceful auth failure: 401/403 → route to Settings with a message, don't loop.
