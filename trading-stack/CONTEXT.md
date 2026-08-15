# TRADING STACK — MASTER CONTEXT

**Owner:** Robert Painter (Quarki33)
**Last verified:** auto-updated on every scanner tick + on git push
**Source of truth:** this file. Any session must read this first.

---

## 🎯 SCOPE & GOALS (2026-07-20 overhaul)

**Goal: profit.** Doctrine, in order:
1. **Nothing dies silently.** Every trading loop runs under launchd KeepAlive; `com.quarki3.stack-watchdog` (5-min) verifies freshness, self-heals via kickstart, Pushover-alerts on staleness/disk/live-DD. Root cause of the July stack-wide freeze was a **100% full disk**.
2. **Evidence before capital.** No strategy touches real money until paper shows n≥30 closed trades with positive expectancy (the auto_tune gate). Nothing has ever passed it — the honest state of the stack.
3. **Friction floor.** IBKR charged ~1%/side on micro orders (38% of the −$17.30 IBKR loss). No IBKR position under $150. Sub-$150 experiments live on Alpaca (commission-free) only.
4. **One scoreboard.** `state/edge_ledger.json` + `/api/edge` is the single source of truth for which strategy has proven edge. First reading (2026-07-20): 15 strategies, 0 PROVEN, qc_portfolio the only PROMISING. Governor weights (`state/governor_weights.json`) drive live composition — reset to equal blend 2026-07-20 after the 92% patent weight was traced to a frozen stale reading (+2.063 vs true −0.31); governor staleness guards now enforce fresh data.

---

## 🚨 SESSION CHECKLIST — verify at start of every session

1. [ ] `ps aux | grep "python.*main.py"` → is scanner alive?
2. [ ] `lsof -iTCP:8080 -sTCP:LISTEN` → is dashboard alive?
3. [ ] `launchctl list | grep autopush` → is git auto-push loaded?
4. [ ] `curl -s http://localhost:8080/api/qc | grep mirror_mode` → LIVE for real money?
5. [ ] `curl -s https://api.alpaca.markets/v2/account -H "APCA-API-KEY-ID: $ALPACA_LIVE_KEY" -H "APCA-API-SECRET-KEY: $ALPACA_LIVE_SECRET"` → account still ACTIVE?
6. [ ] `cat ~/scanner/state/strategy_governor_ui.json | python3 -m json.tool | head -20` → any sustained-lead alerts?
7. [ ] `git status` in each repo → uncommitted work?

---

## 🗂 REPOSITORIES (all private, github.com/Quarki33)

| Repo | Purpose | Auto-push |
|---|---|---|
| `scanner` | Multi-source signal scanner, orchestrator, all strategies, mirrors | ✅ 15m |
| `trading_android` | Kotlin/Compose Android app (v1.32+) | ✅ 15m |
| `ng_dashboard` | NiceGUI + FastAPI backend on :8080, mobile web UI at /app | ✅ 15m |
| `qc_portfolio` | QC bot state + weight refresher | ✅ 15m |
| `ibkr_bot` | IBKR live bot (currently flat) | ✅ 15m |
| `whale_tracker` | Dune + Etherscan whale runner | ✅ 15m |
| `insider_bot` | SEC Form-4 scanner + leaderboard | ✅ 15m |
| `patent_cliff` | FDA Orange Book ranker | ✅ 15m |
| `trading-stack` | This document + master context (NEW, tracker doc) | ✅ 15m |

Auto-push job: `~/bin/trading-autopush.sh` via `~/Library/LaunchAgents/com.quarki3.trading-autopush.plist`.

---

## 💰 LIVE MONEY

- **Alpaca Live account 290417649** — $50 real capital, ACTIVE, crypto INACTIVE
- Strategy: `combined_mirror.py` (ensemble of QC + Patent + Insider + Whale sources)
- Rebalances every 5 min
- Peak-persisted at `~/trading-data/portfolio_peaks/alpaca_live.json`
- $5 drawdown = review trigger (per user rule)

## 🧪 PAPER SYSTEMS (Lab)

| System | Purpose | State file |
|---|---|---|
| `combined_mirror` | LIVE ensemble on $50 real money | `state/combined_mirror_state.json` |
| `qc_mirror` | QC-only paper reference | `state/qc_mirror_state.json` |
| `patent_mirror` | Patent Cliff top-3 mirror | `state/patent_mirror_state.json` |
| `prop_desk` | 6-strategy crypto experiment | `state/prop_state.json` |
| `strategy_governor` | Compares all 4, flags sustained leaders | `state/strategy_governor_ui.json` |
| `value_screen` | Dad's value investing rules (NEW) | `state/value_screen_state.json` |

## 🧠 AUTO-TUNE BRAIN

- Runs every 5 min inside prop_desk
- Ladder of 6 configs (v1..v6)
- Strict promotion criteria: n≥30, mean≥+0.05%, Sharpe≥1.0, late_mean≥+0.02%
- State: `state/auto_tune_config.json`

## 🐳 WHALE / ONCHAIN

- Dune Q01 (coordinated-accumulation) query id 7868558
- Etherscan V2 across 6 chains (ETH/Base/Arb/OP/Polygon/BSC)
- Curated 8-wallet universe in `whale_tracker/curated_wallets.py`
- Runs every 15 min

## 📊 QC PORTFOLIO

- Managed via QuantConnect cloud + local `qc_portfolio` daemon
- 5 strategies, weighted composition to `~/qc_portfolio/portfolio_state.json`
- Inception 2026-06-05, $50K notional, peak +86% (drew down to +6% current)
- Dashboard reads via symlink `~/trading-data/qc_portfolio/portfolio_state.json`

## 📱 ANDROID APP

- Kotlin + Jetpack Compose (Material 3)
- All charts hand-drawn on `Canvas` (no Fable / Vico / MPAndroid)
- Signed release build via `keystore/trading.jks`
- Publishes to `~/trading-dist/trading-latest.apk` with `manifest.json` for OTA
- Current version: v1.29 (2026-07-13)
- Book realm = real money only; Lab realm = paper research
- Position row: single tap = expand inline 1M daily chart; double tap = Firefox Finviz
- Auto-updates every 3–10s via silent refresh (no Loading flash)

## 🔌 BACKEND ENDPOINTS

| Route | Purpose |
|---|---|
| `/api/portfolio/alpaca_live` | Live positions + equity (peak persistent) |
| `/api/qc` | QC engine + mirror state |
| `/api/prop_desk/state` | Prop-desk live state |
| `/api/whales` | Whale runner state |
| `/api/insider` | Insider signals + backtest pnl |
| `/api/patent-cliff` | FDA picks + patent mirror pnl |
| `/api/strategies` | Strategy Lab per-name + aggregate pnl |
| `/api/market_pulse` | SPY today's move |
| `/api/company/{ticker}` | yfinance company profile |
| `/api/ticker/{sym}?period=1M` | Daily bars for inline mini-chart |
| `/api/strategy_governor` | Cross-source leaderboard |
| `/api/value_screen` | Dad's value screen picks |
| `/api/equity/alpaca_live?period=1D` | Portfolio value history for chart (CSV logger writes on each combined_mirror tick) |
| `/api/public-url` | Current cloudflared tunnel URL (deprecated — permanent sslip.io URL now stable) |
| `/app` | Mobile-first HTML web app (mirrors Android UI, served from `static/webapp.html`) |

## ⚙️ ENVIRONMENT VARIABLES

Set in `~/.zshenv` AND `~/Library/LaunchAgents/com.quarki3.ng-dashboard.plist` EnvironmentVariables:
- `ALPACA_PAPER_KEY` / `ALPACA_PAPER_SECRET` (PK-prefix)
- `ALPACA_LIVE_KEY` / `ALPACA_LIVE_SECRET` (AK-prefix, real money)
- `ALPACA_BROKER_KEY` / `ALPACA_BROKER_SECRET` / `ALPACA_BROKER_ACCT` (CK-prefix, sandbox)
- `COMBINED_MIRROR_MODE=live` (scoped: only combined_mirror routes to live)
- `ALPACA_MODE=paper` (global default; prop_desk & qc_mirror stay paper)

---

## 📝 CHANGELOG (auto-appended by strategy_governor / manual commits)

- 2026-07-13 15:40 — Git init + push all 8 repos to Quarki33
- 2026-07-13 15:40 — Auto-push launchd job installed (15-min cadence)
- 2026-07-13 15:41 — Alpaca live wired: combined_mirror mode=live routes ensemble to real $50
- 2026-07-13 15:38 — Strategy governor added: ranks combined_LIVE vs qc/patent/prop_desk PAPER every 15min
- 2026-07-13 15:42 — Card formatting fix (v1.29): title ellipsis + min-width P&L column
- 2026-07-13 15:44 — Context doc created (this file) + value_screen source built
- 2026-07-14 14:04 — `combined_mirror.py`: equity CSV logger wired; each tick appends (ts, equity) to `~/alpaca_live_arm_state/equity.csv`. Backfilled 27 historical points from Alpaca `portfolio_history` API. `/api/equity/alpaca_live` now returns real chart data (was 0 pts).
- 2026-07-14 14:14 — **Android v1.32** shipped. Bug fix: single-tap position card now correctly renders inline 1M daily chart. Root cause: `HttpURLConnection` inside `LaunchedEffect` ran on Main dispatcher → `NetworkOnMainThreadException` swallowed by catch-all → `closes` stayed empty → chart showed "No chart data." Fix: wrap fetch in `withContext(Dispatchers.IO)`. `PortfolioScreen.kt:547`. APK at `~/trading_android/dist/trading-1.32.apk`, installed via adb.
- 2026-07-16 08:38 — **Mobile web app** shipped at `ng_dashboard/static/webapp.html`, mounted at `/app` route in `api.py`. Mirrors Android UI: 📕 Book (live money) / 🧪 Lab (paper), position cards with single-tap→inline chart, double-tap→Finviz, portfolio value chart with 1D/5D/1M/3M periods, auto-refreshes every 5s. Same FastAPI endpoints as Android app → renders stay in perfect sync.
- 2026-07-16 08:38 — `/api/public-url` endpoint added to `api.py` inside `mount()` — used during the ephemeral Cloudflare interim; still present but deprecated.
- 2026-07-16 08:38 — First public URL via **Cloudflare quick tunnel**: `~/Library/LaunchAgents/com.quarki3.cloudflared-app.plist` created. Worked but URLs (`*.trycloudflare.com`) are ephemeral.
- 2026-07-16 09:00 — **Tailscale Funnel** attempted for a stable URL. Enabled in admin console, funnel wired at `quarki3s-macbook-air.tail33de20.ts.net`. Failed: free-tier tailnet published only AAAA (IPv6) record, no A. Not reachable from IPv4-only clients. Left funnel enabled but unused; setup does not block anything.
- 2026-07-16 10:05 — **Permanent public URL live:** `https://129-153-96-201.sslip.io/app`. Architecture: Browser → Oracle VM (`129.153.96.201`) → Caddy 2.11.4 → Tailscale → Mac (`100.71.5.12:8080`) → dashboard. Free (`sslip.io` = wildcard DNS, no signup). Let's Encrypt cert auto-issued in ~2s. Cloudflare tunnel torn down (`com.quarki3.cloudflared-app` bootout + plist removed). Oracle Caddyfile at `/etc/caddy/Caddyfile`, iptables 80/443 opened + persisted. Full details in memory `project_public_webapp.md`.
- 2026-07-16 10:07 — **Autopush guardrail added**: `trading-stack/` promoted to git repo + Quarki33 remote, added to autopush rotation. Autopush script now warns to log when any code repo changes without CONTEXT.md being touched in the same 24h window — makes it obvious when docs are drifting.

---

## 🔍 HOW TO ADD A NEW SYSTEM

1. Write source at `scanner/sources/<name>.py` with `tick()` function
2. Register in `scanner/main.py::SOURCES` with cadence
3. Add API endpoint if user-facing
4. Add Lab card in `LabScreen.kt` (or Book section for real money)
5. Add row to state file table above
6. **Add entry to the CHANGELOG at bottom of this file** (or the auto-updater will complain)
7. Commit — auto-push takes it to GitHub

The strategy_governor tick self-audits: if a new state file appears in `scanner/state/` that's not listed above, it flags it in the app's activity feed.
- 2026-07-20 11:00 — **Full-stack overhaul session (root cause + reliability layer).** Four-agent forensic sweep findings: (1) **disk 100% full (383 MB free)** was the systemic cause of every silent death since ~Jul 3 — freed to 2.2 GB (gradle/npm/browser caches); big remaining reclaims are Epic Games 36G (/Users/Shared) and Desktop 37G, user's call. (2) IBKR −$17.30 decomposed: −$6.60 commissions (IBKR charged **1%/side**, verify vs Flex statement), −$16 from 3 stop-outs (VERA −$9.19, RDY −$5.44, OCGN −$1.41), final Jul-9 liquidation ~flat. (3) Code audit: momentum entries + mean-rev exits (7d time-stop vs 30d thesis), 8% trail cap = 1.2–2×ATR whipsaw, pre-market market-order exits, zero commission modeling. (4) Alpaca paper arm (zero-commission) ALSO lost −3.4% vs flat SPY → no demonstrated edge; but n≈1 closed trade, inconclusive.
- 2026-07-20 11:00 — **Scanner resurrected under launchd**: `com.quarki3.scanner` (KeepAlive, wrapper `~/bin/run_scanner.sh`). It had been running bare and died Jul 17 08:33 leaving live $50 unmanaged 3 days. On restart the governor immediately published `governor_weights.json`: **patent 92% / qc 2.6%** (patent led 111 consecutive cycles, +2.06% vs qc −2.87%). combined_mirror now rebalances live toward VTRS/RDY/TEVA.
- 2026-07-20 11:00 — **`com.quarki3.stack-watchdog` installed** (`~/bin/stack_watchdog.py`, 5-min): job liveness + re-bootstrap, state-file freshness + kickstart (never SIGKILL), disk ≥1 GB, Alpaca live $5-DD Pushover trigger. First run green: 2.21 GB free, live DD $1.08.
- 2026-07-20 11:00 — **poc10-bot unloaded** (zombie since Jul 4: ENOSPC + DNS errors; plist kept for revival). IBKR stays deliberately dark (account flat since Jul 9) until paper proves a strategy AND positions ≥$150.
- 2026-07-20 11:05 — **Swarm queue loaded with overhaul tasks**: a145394a ibkr_bot exit-logic fixes (3×ATR trail floor, 30d time-stop, RTH-only exits, $150 commission floor, break-even bug, server-stop fill logging); afa57294 alpaca_paper_arm resurrection under launchd + closed_trades.csv ledger; fdc33760 Edge Ledger (`state/edge_ledger.json` + `/api/edge`); ebecce84 retire dead IBKR sidecars + STATUS.md.
- 2026-07-20 11:07 — **capital-allocation-audit workflow launched** (Claude workflow, 9 agents): validate patent_cliff / SOXL-regime / SPY-overlay / qc_portfolio → adversarial refutation → capital allocation plan for $482 idle IBKR cash + $49 Alpaca live + paper proving pipeline.
- 2026-07-20 11:10 — **Alpaca paper arm resurrected under launchd** (swarm task afa57294): root cause of the Jul-3 death was a disk-full write raising inside the tick loop's own unguarded except-handler `print()`, killing the process silently with zero traceback (no launchd supervision existed at all, so it never restarted). Fixed: every file write in `alpaca_paper_arm.py` now wrapped non-fatal, tick-error print itself guarded, `_load_state` tolerant of corrupt JSON. Exit-philosophy parity with the IBKR audit: 3×ATR trail floor added, stop-outs gated to regular market hours (no existing time-stop in this file to change). Added `alpaca_paper_arm_state/closed_trades.csv` ledger (the file promotion decisions will read) — also caught and fixed a pre-existing bug where a rejected sell (broker qty mismatch) was still logged as a closed trade; one false-positive CCL row removed by hand. New `com.quarki3.alpaca-paper-watcher` KeepAlive job (wrapper `~/bin/run_paper_arm.sh`) verified running. First tick naturally closed 8 of the 22 stale Jul-1 positions already past their stops (normal stop-management, not a mass-close); 14 remain open under active management. Ledger at 7 real rows toward the n≥30 gate.
- 2026-07-20 11:12 — **Workflow verdicts landed (9 agents, all 4 refuters CONFIRMED) + emergency live-safety fixes applied.** Findings: (a) the governor's 92% patent weight was computed off a FROZEN Jul-17 reading (+2.063) while true patent P&L was −0.31% — patent's "lead" was survivorship among losers and sector beta (all catalyst scores 0.0, sec_signals.csv empty, picks 24 days stale); (b) patent_mirror had a runaway-buy bug (get_positions→{} on API error → 243 orders/$486 attempted, 174 queued on a weekend, book 6x design size); (c) qc_portfolio's +86%→+5% "round trip" NEVER HAPPENED — a broker-side phantom equity print ($54,075→$93,023 in one tick on 2026-07-07) poisoned peak_equity, fired all 4 DD-ladder tiers on a 44% drawdown that didn't exist, and froze the strategy at exposure 0.0 until ~Jul 29. Real record: +5.3%/44d, −6.2% real MaxDD, +1.30%/event over 69 closed events — the only positive forward record in the stack; (d) SOXL backtest has confirmed lookahead (4 of 5 gate conditions same-day): true Sharpe 0.82 not 1.211, MaxDD −12.9% not −7.8%; (e) SPY overlay survived refutation (78 flips 2011-26 reproduced; friction at $460 ≈1.3%/yr) → deploy-live-small pending fixes + dry-run + explicit user approval. FIXES APPLIED: governor_weights.json reset to equal blend; combined_mirror.py + patent_mirror.py get_positions now FAIL CLOSED (None → skip cycle, never trade blind); strategy_governor.py staleness guards (fresh-state gate in snapshot(), rolling-window filter on MIN_OBS counting); qc portfolio_state.json phantom peak reset (93,023→54,308, exposure 1.0, tiers cleared) + daemon restarted. Full allocation plan in workflow output (wf_e87e3272-109).
- 2026-07-20 11:22 — **CORRECTION (from user): only active capital is the ~$49 Alpaca live.** The "$482 idle IBKR cash" was an inference from the Jul-9 `exit_account` liquidation proceeds; IBKR balance unverifiable (gateway down) and user indicates it is not active. SPY-overlay-on-IBKR deployment + $5-DD carve-out decisions are moot until new capital is funded. Plan: paper proves n≥30 (qc_portfolio nearest) → fund the winner on Alpaca, not IBKR.
- 2026-07-22 11:25 — **Legacy repo cleanup (Finder-merge style).** Verified all 12 active repos clean/pushed and all 3 sync agents healthy. Archived-then-deleted 4 legacy local dirs (~1.1 GB reclaimed, disk now 2.0 GB free): `mean_reversion_bot` (Kraken PEPE mean-rev GUI; final state pushed to NEW private repo github.com/Quarki33/mean_reversion_bot — old robertpainter144-lab remote was dead; keeper: Adam's MA-crossover backtester), `Ultimate-Mega-Trading-Bot` (MATIC snipe bot; final state pushed to existing repo; NOTE: `test.env` in repo history contains a real but **fully drained** bot wallet key — addr 0x1Be3…d4bb, 268 tx, 0 POL/0 USDC, private repo, no live risk), `ibkr-min-500-deploy` (deploy_tws.py folded into `ibkr_bot/archive/min500_deploy/`), `Investment Bot` (empty .git, deleted outright).
- 2026-07-22 11:30 — MVYTraders repo (github.com/Quarki33/MVYTraders) set up for dad's intern: flipped PUBLIC→private, removed Unlicense, pushed credentials-free copy of scanner/ (strategies+sources+gate) + onboarding README. Intern still needs collaborator invite.
- 2026-07-22 11:36 — MVYTraders intern watcher live: ~/mvy_watch/mvy_watch.py under com.quarki3.mvy-watch (15-min). Pushover on new commits/PRs; priority-1 alert when scanner/strategies|sources files change. Never executes intern code — sandbox eval is manual, signal-only, keyless. Beat-detection = phase 2 once intern strategies exist (run through auto_tune gate → edge ledger).
- 2026-07-25 15:45 — **Guest view pivoted to Lab-only + installable PWA.** Guest key now unlocks ONLY paper sources (`portfolio|equity|activity`/`paper|broker|shadow` + ticker/health) — real-money sources (alpaca_live, ibkr) return 403 for guests. `guest.html` rebranded "MVYTRADER · strategy lab · simulated capital" with LAB A/B/C tabs (paper/broker/shadow). PWA install for Android+iPhone: `static/guest-manifest.json`, root-scoped `/guest-sw.js` (network-first, no caching of live data), MVY icons (`static/mvy-icon-{192,512}.png`, PIL-generated). `/guest` HTML shell + PWA assets are key-free (branding only — data still gated) so installed PWAs (own storage sandbox on iOS) can boot into an in-app access-code screen; entering the guest key once stores it in localStorage. RESTART GOTCHA: `main.py` spawns a child that owns :8080 and survives `launchctl kickstart -k` — always `lsof -nP -iTCP:8080` after restart and SIGTERM stale pids until port owner changes.
- 2026-07-27 07:45 — **Lab-vs-live divergence root-caused + 4 fixes shipped** (why the $50 live book lost while QC Lab profited): (1) `strategy_governor.py` hysteresis skipped rewriting `governor_weights.json` when weights were stable, so its ts went stale and `combined_mirror`'s 4h staleness guard silently discarded the 92.6%-QC recommendation → live ran the equal blend (40% patent = the losing source) since ~Jul 25. FIX: governor now republishes unchanged weights with a fresh ts every cycle ("refresh (unchanged)"). (2) Live cash account: `cash` ($17.71) includes unsettled T+1 proceeds but only `non_marginable_buying_power` ($0.09) is spendable → TMUS/AXP buys 403'd every 5 min for hours. FIX: buys now gated on settled BP + 30-min per-symbol cooldown after any 403; `settled_bp`/`buy_cooldowns` persisted in state. (3) Weekend runaway: day market orders queue while market closed, positions never update, every tick re-buys — 530 queued qc_mirror AMD buys (~$17.9k paper) all filled at Monday's open (same class as the patent_mirror 6x incident; the AVGO phantom sell also self-cleared at open). FIX: `_market_open()` clock guard in qc_mirror + combined_mirror — no order submission while closed. (4) Concentration-cap leak: with QC converged single-winner (AAPL 89%), the old global cap redistribution rerouted QC's capped excess into 2%-rated sources (book became 15% QC / 85% patent+value). FIX: `build_target()` rewritten — source-first cap fill, spill only to sources with governor weight ≥ 0.10, remainder deliberately stays CASH. Current targets: AAPL 15% + patent 2.5% + value 1.7% + ~81% cash until QC's holdings diversify (then QC auto-expands to 75% cap ceiling). Scanner restarted twice to load. Live baseline $49.76 → $48.49 (−2.5%) at time of fix.
- 2026-07-27 07:45 — **MAX_SINGLE_NAME_PCT raised 0.15 → 0.90** (user: "if qc is our leading strategy go ahead"). QC is the governor's rated leader (92.6%); the 15% cap made its single-winner composition (currently 100% AAPL) inexpressible on the live book. New live targets: AAPL 90% + patent 2.5% + value 1.7% (~94% invested). Convergence takes ~2 days: today's sells settle T+1, then the ~$36 AAPL buy fires under the settled-BP gate. $5 aggregate-DD review trigger remains the backstop. Scanner restarted.
- 2026-07-27 07:55 — **Autopilot 'Actively Managed' (InTheMoney) wired in as 6th source, paper-only.** New `sources/autopilot_mirror.py` mirrors `state/autopilot_holdings.json` (hand-updated from the Autopilot app; no public API) on the paper account at $50 scale, 5-min cadence, market-open guard. Registered in: main.py roster, edge_ledger MIRRORS, governor snapshot/challengers/source_names (`autopilot_PAPER`), combined_mirror sources (governor weight default 0.0 — NO live money until the governor scores it, unlike founding sources' equal-blend default). Holdings file is a placeholder ({}) awaiting paste from the app; mirror idles until filled. Context: portfolio is ~+73% all-time since Jan 2025, $38–162M AUM, AI-growth-heavy (ASML, HOOD). Also 90%-cap live tick verified: patent/value sold to slivers, $36 AAPL buy correctly deferred on T+1 settlement.
- 2026-07-27 08:05 — **Autopilot holdings feed shipped (SnapTrade→Robinhood sensor bridge).** User already runs 'Actively Managed' in Robinhood ($500, Autopilot minimum) — that account is now the data sensor. Autopilot has NO public API; Robinhood has no official retail API; bridge = SnapTrade (official read-only Robinhood integration, free individual tier 5 connections, holdings cached daily). New `sources/autopilot_feed.py` (30-min tick): SnapTrade holdings → normalized weights → `state/autopilot_holdings.json` (fail-closed: fetch failure/empty account never zeroes composition). Schwab official-API path kept as alternate branch. One-time setup helper `scanner/snaptrade_connect.py`. SDK `snaptrade-python-sdk` 12.0.1 installed (py3.10). USER STEPS PENDING: snaptrade.com signup → client_id/consumer_key into `state/autopilot_feed_config.json` → run snaptrade_connect.py → click portal URL. Feed idles until then. Scanner restarted.
- 2026-07-27 08:28 — **Congress (Pelosi PTR) tracker shipped as 7th source — the free Pelosi-Tracker+ replica.** Agent-drafted, reviewed, wired: `sources/congress_feed.py` (hourly) parses official House Clerk PTR filings (disclosures-clerk.house.gov, public data; embedded stdlib RC4/Flate PDF extractor verified against both live 2026 Pelosi PTRs) → decay-weighted composition in `state/congress_holdings.json`; today: INTC 42%, AB 25%, UBER 10%, GOOGL/AMZN ~8%, NVDA 4.5%, AAPL/VST ~1.5%. `sources/congress_mirror.py` paper-trades it at $50 scale (first tick: 7 orders filled). Wired into governor (`congress_PAPER` challenger + "congress" source, weight default 0.0 → no live money until scored), edge_ledger, combined_mirror. Caveats: STOCK-Act 30–45d disclosure lag; Senate side not implemented. Housestockwatcher/capitoltrades probed dead/unreliable — official Clerk data chosen. Config `state/congress_feed_config.json` (politicians list, decay half-life 180d, partial-sale factor 0.5, top-12 cap). Scanner restarted; live targets verified unchanged (AAPL 90%).
- 2026-07-27 08:28 — **User decisions logged**: (a) QC stays on Alpaca live (user initially proposed abandoning; data showed QC is leader — kept); (b) user overruled paper-first gate for established Autopilot portfolios; recommended Robinhood in-app split of $597: Pelosi $270 / Inverse Cramer $164 / AM $163 (user executing in app); (c) SnapTrade portal link STILL unclicked — sensor feed blind until then (watcher armed).
- 2026-07-27 08:52 — **Autopilot chapter closed: user exiting entirely.** Decision after full exploration: Robinhood $597 sold (sell-all AM) and withdrawn to PERSONAL funds — not redeployed to Autopilot (fees/minimums fit $5k+ accounts, not $600) and not added to Alpaca. Focus = make the $50 Alpaca live book profit under the governor. Consequences: SnapTrade portal never clicked; connection watcher killed; autopilot_feed + autopilot_mirror stay registered but DORMANT (empty holdings, log-only, harmless — revive if user ever reconnects a brokerage). congress_feed/congress_mirror UNAFFECTED and running (Pelosi paper record building). Sequence of user allocation decisions this morning (blend $270/$164/$163 → Cramer-tilt $240/$207/$150 → one-slot Cramer $500 → full exit) all pre-empted by app constraints (free plan sell-all-only, $500/portfolio min) and by user recognizing their own drawdown sensitivity (four "will it bounce" questions). This week for the live book: ~$36 AAPL buy fires Mon/Tue on settlement; AAPL earnings Thu Jul 30; MSFT/META Wed + Fed Wed.
- 2026-07-27 09:12 — **GUI redesign kit shipped + dashboard truth bug fixed.** (1) ROOT CAUSE found while prepping: TWO launchd services ran ng_dashboard — legacy `com.quarki3.dashboard` (KeepAlive, NO live keys) kept winning the :8080 port race against `com.quarki3.ng-dashboard` (full keys), so /api/portfolio reported a pristine $50 stub for alpaca_live (also the July-25 "orphan PID 956" incident's true origin). Legacy service booted out, plist archived to LaunchAgents/disabled/, stack_watchdog JOBS label corrected to ng-dashboard. API now reports real live data ($48.10, 9 positions, DD 3.95%). (2) New PRIVATE repo `Quarki33/trading-gui`: LOVABLE_PROMPT.md (full Quarki Terminal spec: Book/Lab/Governor/Markets/Health pages, $5-DD gauge, baseline-vs-inception deltas, settled-BP semantics) + API.md (verified endpoint contract) + README (Lovable GitHub-sync workflow). Repo added to trading-autopush roster. Backend unchanged — CORS already open, x-api-key per-request auth suffices. USER STEPS: create Lovable project → connect GitHub repo trading-gui → paste LOVABLE_PROMPT.md → enter public URL + owner key in the app's Settings screen (never commit the key).
- 2026-07-27 09:35 — **Quarki3 Terminal shipped end-to-end (no Lovable — built directly).** React/TS/Vite/Tailwind/Recharts SPA in `Quarki33/trading-gui` (all 5 spec pages: Book w/ $5-DD gauge + baseline deltas + target-vs-actual bars, Lab, Governor [empty-state until /api/governor exists], Markets, Health, Settings w/ x-api-key in localStorage). Served at `/terminal` from `~/trading-gui/dist` via new StaticFiles mount in ng_dashboard api.py; shell added to _OPEN_PATHS (data still key-gated); public 200 verified. Android wrapper app "Quarki3 Terminal" (`com.quarki3.terminal`, repo `Quarki33/quarki-terminal-android`, APK in dist/) installed+launched on user's Z Flip4 via adb, side-by-side with com.quarki3.trading. Also: /api/portfolio payloads now carry `settled_bp` + `stale` (webapp.html renders stale banner + SETTLED chip); _empty_portfolio placeholder-looks-live bug documented. Disk hit hard ENOSPC mid-build (~1 min) — emergency cache sweep freed ~1.9G → 2-3G free; watchdog MIN_FREE_GB raised 1.0→3.0; user has the personal-content cleanup list (Home Videos 7.8G etc). TODO next iteration: /api/governor endpoint, congress composition in Markets, PWA icons+SW, gradle cache clear (~1G) now builds are done.
- 2026-07-27 09:37 — Quarki3 Terminal verified LIVE on the Z Flip4: phone couldn't reach the public sslip.io URL (carrier/DNS), so wrapper start_url flipped to tailnet http://100.71.5.12:8080/terminal/ via adb run-as pref write, and the SPA's DEFAULT_BASE_URL now defaults to serving origin (same-origin ⇒ tailnet needs no key; public still key-gated). Screenshot-verified rendering real data: $47.99 equity, DD gauge $2.09/$5, baseline deltas. Rebrand "Quarki3 Terminal" applied in app + web.
- 2026-07-27 10:02 — **Orphan-position bug fixed on live mirror + chart UX shipped.** combined_mirror only saw positions in its CURRENT target set → symbols dropped from targets became invisible and unmanaged (AMD $7.06 + SPY $1.98 leftovers from Jul-13, incl. a semi held through the selloff). Fix: live account treated as dedicated (see ALL positions), tracked_syms persisted, orphans auto-liquidated BY QTY (notional sells 403 on price drift — first AMD attempt proved it). Both orphans sold (SPY 208047e1, AMD 4560a3a9); proceeds join T+1 queue → tomorrow's AAPL buy ~$45. Note: governor baseline weights now give congress ~1.7% of live (INTC/AB/UBER… targets <$0.50/name, below order min — exposure effectively 0 until scored up). Also: Quarki3 Terminal equity chart rebuilt as custom canvas w/ owned gestures (1-finger pan no page-fight, pinch zoom, double-tap/⟲ reset, wheel zoom, crosshair readout) — user-requested. Android wrapper got system-bar insets fix earlier (targetSdk 35 edge-to-edge).
- 2026-07-27 10:35 — **FULL MIGRATION: entire stack moved Mac → Oracle VM (user: "don't host any part of it on the mac"). Copy-only; zero files deleted anywhere.** VM instance-20260615-2234 (129.153.96.201 / tailnet oracle-trader 100.73.225.4, 1GB RAM + 2G swap, 41GB free). Built: uv-managed python 3.10.20 venv ~/quarki-venv; /Users/quarki3→/home/ubuntu symlink so hardcoded Mac paths resolve; rsynced scanner, ng_dashboard, trading-gui, qc_portfolio(+test), trading-data, alpaca_live_arm_state, patent_cliff, trading-stack, whale_tracker, insider_bot, ibkr_bot(lib), alpaca_keys.py, paper/soxl/poc10 bots; .git dirs restored via GitHub clones; secrets in ~/quarki.env (0600) + git credential store. systemd: quarki-scanner/dashboard/qc/paper-arm/soxl/poc10 services (MemoryMax caps), quarki-watchdog.timer (1min; new ~/bin/vm_watchdog.py — restarts dead units, tick staleness, disk<5GB, live $5-DD Pushover), quarki-autopush.timer (15min, ~/bin/vm-autopush.sh, 8 repos), quarki-patent.timer (daily 13:00 UTC). Caddy now proxies → localhost:8080 (Caddyfile.bak-mac-proxy kept). CUTOVER ORDER RESPECTED: Mac scanner+qc stopped BEFORE VM start (single live trader always). Mac: ALL 18 launchd trading services booted out + disabled (plists intact in ~/Library/LaunchAgents). Android app: URL pref → http://oracle-trader.tail33de20.ts.net:8080/terminal/, VM IP added to cleartext allowlist, APK v-rebuilt+installed. NOT MIGRATED (stopped, documented): ibkr-* + spy-overlay (IB Gateway is a Mac GUI app; IBKR dark anyway — reactivation needs a plan), swarm orchestrator (needs claude CLI/auth on Mac), companion-sync + trading-sync (fed companion VM from Mac — companion data may go stale; revisit), mvy-watch. Mac can now sleep/close. Verified end-to-end: all ticks fresh on VM, live equity monitored ($47.97, DD $2.11), public /terminal 200 via VM, phone app on VM.
- 2026-07-27 16:36 UTC — CONTEXT.md now maintained from the VM: autopush end-to-end verification entry (first VM-authored commit).
- 2026-07-28 14:00 — **Intern collab live in `MVYTraders`:** curated scanner + paper-only `trading-companion/` PWA dev build (283b0fe); Linus = `lmunn1` collaborator; README team workflow (branch `linus/<topic>` + PR, results numbers in PR description). Branch protection unavailable on GitHub Free → enforced by convention + watcher. Robert's progress stays in this repo; the short-lived `robert` branch on MVYTraders was deleted.
- 2026-07-28 14:00 — **Linus commit watcher:** `quarki-mvy-watch.timer` (5 min) fetches MVYTraders via read-only deploy key (`~/.ssh/mvy_deploy`), notifies on any non-Robert commits on any branch. Script `bin/mvy_watch.sh`, state `~/mvy_watch/`. Mac helper `bin/linus-diff` (see `mac/bin/`) lists/diffs his branches vs main or vs the live VM scanner.
- 2026-07-28 14:00 — **Pushover → ntfy.sh migration.** Pushover trial expired ("no active devices") = ALL alerts were silently dropped, incl. $20-ceiling approvals and watchdog alarms. Patched senders: `ibkr_bot/notify.py`, `bin/vm_watchdog.py`, `bin/stack_watchdog.py`, `alpaca_paper_arm.py` (service restarted), `bin/mvy_watch.sh`, a1_capacity_loop.sh. Secret topic in `~/ntfy.env` (Mac + VM, chmod 600, gitignored). End-to-end ping verified; phone subscribed.
- 2026-07-28 14:00 — **Loose real-money infra now versioned here:** vm-autopush mirrors `bin/` alert scripts, `ibkr_bot/notify.py`, `alpaca_paper_arm.py`, and all `quarki-*` systemd units into `trading-stack/vm/` on every run (*.env excluded). Note: VM `ibkr_bot/` is NOT a git repo (repo table drift from Mac era) — its notify.py is captured via this mirror.
- 2026-07-28 14:02 — Autopush cadence tightened 15min → 5min (user request: all progress on GitHub within 5 minutes).
- 2026-07-28 14:50 — **Terminal guest mode shipped** (for intern Linus, per "iPhone version, no real money"): ng_dashboard api.py gained /api/whoami (key-role probe) + /api/governor (governor_weights + strategy_governor_ui merge — Governor tab now live for everyone) + guest scrub layer (qc: mirror_targets/bankroll/mode/equity/session/ratchet stripped + holdings emptied; whales: wallet addresses stripped; sources: real-money entries filtered; portfolio: account ids stripped as before). Guest allowlist extended to sources/bots/version/edge/qc/whales/patent-cliff/insider/governor/market_pulse. trading-gui SPA: whoami-driven nav — non-owner keys see Lab/Governor/Markets/Health only, Book (real money) hidden client-side AND 403d server-side; dist rebuilt on Mac, rsynced to VM. Backup: api.py.bak-ntfy. Guest URL: /terminal/ + guest key in Settings.
- 2026-07-28 14:57 — **Guest Book = paper mirror of the live $50** (Robert explicit): guests now get all five Terminal tabs; Book renders new `qcmirror` source — gate middleware rewrites /api/{portfolio,equity,activity}/qcmirror → alpaca_live handlers, strips account ids, SIM-labeled UI, IBKR card owner-only. QC scrub relaxed for guests: mirror_targets + strategy holdings now visible (the QC connection); bankroll/session/ratchet dollars still private. Sources for guests: paper/broker/shadow + qcmirror pseudo-entry.
- 2026-07-28 14:57 — **Dual-repo autopush**: vm-autopush now one-way mirrors trading-gui source (no dist/node_modules/env) into MVYTraders/trading-gui and pushes MVYTraders in the same 5-min loop as the private repos. Canonical = private trading-gui; Linus PRs applied upstream, flow back on next sync.
- 2026-07-28 15:20 — **Quarki333 Terminal functional pass** (renamed from Quarki3): SPA deep-link fallback (was 404 on refresh/PWA), /api/bots rewritten to VM systemd truth (Mac-era phantom entries gone — all 9 quarki units green), IBKR source+card retired, /api/congress implemented (Pelosi mirror composition; Markets→Congress tab live), /api/governor+congress+prop_desk guest-readable, Book whoami-gated. Lab overhauled: per-strategy dossiers (what it does / in / out / cadence), tap-through drawers with roster status + latest closed trades + mirror compositions, qc_mirror drawer cross-links to Book, ES_mean_rev+spy_overlay collapsed as dormant. Full playwright audit clean on all 6 pages × guest+owner.
- 2026-07-28 15:28 — **Android v1.33 shipped** (private trading_android, installed via adb): Lab SystemCards gained dossier blurbs (what each system does) + "tap for details ›" affordance, mirroring the Terminal web Lab; DEFAULT_BACKEND fixed Mac tailnet hostname → VM tailnet IP (100.73.225.4:8080) post-migration. Built with brew openjdk@21, release keystore. APK: trading_android/dist/trading-1.33.apk. Terminal shell now serves cache-control: no-cache so installed PWAs always pick up new builds on open.

## 2026-08-02 — ETF-mirror catalog rows from web sweep
- Ran 24-agent web sweep (8 lenses: replication ETFs, copy-trading, quant leaderboards,
  13F clones, congress, open-source live, social pilots, academic) -> 66 candidates,
  15 adversarially verified, 10 survived.
- Verdict: no secret prop bots exist to mirror; the additive finds are managed-futures
  ETFs — the stack's first NEGATIVE-equity-beta streams (DBMF -0.18, KMLM -0.31).
- Added 6 single-ticker rows to scanner/state/autopilot_catalog.json (struck NAV 100
  today): DBMF_Mirror, KMLM_Mirror, CTA_Mirror, RSBT_Mirror, HFND_Benchmark (industry
  hurdle), QIS_Cautionary (negative benchmark, -51% 1-yr, never buy).
- Rejected: Collective2/eToro copy-trading (fees+survivorship or US-unavailable),
  Striker futures (capital), RSST/QAI/HFGM (fee-laden equity beta already owned free).
- Display-only rows; tournament entry stays via promote_pilot.py + D7 gate. Backup at
  autopilot_catalog.json.bak-20260802.
- LATER SAME DAY: promoted DBMF_Mirror + KMLM_Mirror (50/50) into the autopilot lane
  (lane was idle since 7/27 InTheMoney demote). Governor scores it as autopilot_PAPER;
  0% real weight until standard gate. CTA/RSBT stay racing as catalog rows; swap via
  promote_pilot.py if their curves lead. Watch item: buy-and-hold blend may close few
  trades — n>=30 gate could be slow; assess after 2 weeks of forward marks.
- App placement: rows auto-appear in owner Catalog section (/api/catalog -> Terminal
  owner-injection + Kotlin app); no UI change needed. Guest paths unaffected (D1).
- EVENING: shipped D24 continuous-currency machinery. catalog_leader_check.py +
  quarki-leadercheck.timer (weekdays 22:17 UTC): auto-rotates the PAPER lane to any
  catalog row sustaining a 3-day >2pp lead (5d cooldown, ntfy, config hot-reload;
  mode=auto). Weekly cloud routine weekly-mirror-sweep -> Quarki33/mirror-sweeps
  repo (Mon 12:07 UTC, delta sweeps vs BASELINE.md). Crenshaw_Tracker wired from
  House PTRs w/ Monday auto-refresh (current net basket = 1 unpriceable ticker
  USOU, curve activates on next parseable buy). Mullin needs Senate eFD source
  (not built). CronCreate rejected (session-only); systemd + cloud routine are
  the durable pair.

## 2026-07-28
- 2026-07-28 18:20 MDT — **MVYTraders pulled back to bare README** (Robert: suspicion Linus may be reusing the code privately; no public evidence found — no forks, no lmunn1 activity, clone traffic only 6×2026-07-22 pre-invite). Force-pushed orphan commit 5528311 (README-only), deleted issues #1-#5, history wiped from remote. Full old history backed up: ~/MVYTraders_full_20260728.bundle (VM + Mac copies) and the VM ~/MVYTraders clone (now divergent from remote — do NOT autopush it). vm-autopush.sh: trading-gui→MVYTraders mirror + push leg REMOVED (backup: vm-autopush.sh.bak-mvy-pull). Linus kept as collaborator; quarki-mvy-watch still active. allow_forking cannot be disabled (personal-account repo); old SHAs remain fetch-able on GitHub until server GC.
- 2026-07-28 18:30 MDT — MVYTraders README revised per Robert: front page restored to the ORIGINAL look (same intro/doctrine/team-workflow sections) so nothing reads as a pullback if Linus visits, but scrubbed of proprietary detail (file-tree architecture, promotion-gate numbers, source list, sizing mechanics removed; project dirs described as "being reorganized"). History rewritten again — sole commit bbc5635 "Repo housekeeping while we reorganize" authored as Robert (no watcher alert). Repo contents: README.md only.
- 2026-07-28 18:35 MDT — **Linus OSINT sweep deployed**: ~/bin/linus_osint.py + quarki-linus-osint.timer (daily 15:00 UTC). Checks lmunn1 public repos/events, MVYTraders forks, post-invite clone traffic, and GitHub-wide code search for 3 scanner fingerprint phrases (baseline-diffed; only NEW repos alert). ntfy on hits + on GitHub token expiry (~/.github_token, gh token copied from Mac). Day-one manual sweep: CLEAN — no forks, no lmunn1 activity, no public code matches. Traffic lags ~1 day; 7/28 clone data lands tomorrow. State/log in ~/linus_osint/.
- 2026-07-28 18:40 MDT — **Guest Terminal access REVOKED** (Robert: pull everything but the README): guest key rotated in ~/trading-data/dashboard_keys.json (old 4a0b3f08dbc44eb9 → 403 verified; new value in that file, not shared with anyone), quarki-dashboard restarted clean, owner key verified working. Linus now has exactly ONE thing: the scrubbed MVYTraders README (still a collaborator, watcher active).
- 2026-07-28 19:55 MDT — **Linus collab v2 (frontend-only)**: Robert re-engaged after Linus replied. MVYTraders now = scrubbed README + trading-gui/ source ONLY (commit 9451b2b); scanner + companion stay private — IP boundary is now the API contract (trading-gui/API.md), backend is a black box. vm-autopush trading-gui→MVYTraders mirror+push RESTORED. Tailnet IP redacted from API.md. New guest key 7a0db005658556bd minted for Linus (old one dead); Robert to send key+base URL privately. Watcher + daily OSINT sweep unchanged.
- 2026-07-28 20:00 MDT — Fresh-contributor test of MVYTraders passed (clone→npm ci→build 1.3s; guest key whoami=guest). Found+fixed: API has no CORS headers → npm run dev was browser-blocked; added vite server.proxy /api→sslip.io in canonical trading-gui (commit 4cb10f0 in MVYTraders), verified end-to-end via local dev server. README getting-started updated.
- 2026-07-29 09:15 MDT — Seeded 4 frontend-scoped starter issues on MVYTraders (#6 Lab sparklines, #7 pull-to-refresh/offline states, #8 day-at-a-glance view, #9 bundle-size trim w/ measurable target) as low-barrier entry points for Linus + cleaner engagement signal. Morning sweep note: 7/28 traffic = 13 clones/3 uniques — ~4 Actions + 2 VM accounted; 1 unique unattributed (possibly Linus pre-wipe clone; assume code may be on his laptop). No branches/PRs/pushes from him yet (~13h since frontend handoff).
- 2026-07-29 09:40 MDT — **Autopilot strategy-catalog benchmark shipped** (ToS-compliant). Autopilot Terms broadly ban ALL automated collection/scraping/derivative use of their Service/Content (api.iris.finance GraphQL is off-limits), so ZERO data is pulled from them programmatically. Instead: holdings hand-transcribed from the app (human reading = allowed) into scanner/state/autopilot_catalog.json {pilot: {ticker:weight}|[tickers]}; new source sources/autopilot_catalog.py marks each pilot to market forward from PUBLIC prices (yfinance, already in quarki-venv) into autopilot_catalog_curves.json (virtual index, base NAV 100, shares re-struck on hand-edited rebalances, curve chains continuously). edge_ledger._autopilot_catalog_rows() surfaces each as autopilot_cat:<name> (mode=benchmark) on the SAME scoreboard as QC + the live book. STRUCTURALLY isolated from real money: governor.snapshot() + combined_mirror read a fixed hardcoded source list; catalog rows appear only in edge_ledger.json (display), can never receive capital. Registered in main.py SOURCES (6h cadence). Seeded with InTheMoney (ASML/HOOD). yfinance runs under quarki-venv only (system python3 lacks it) — same as patent_mirror/value_screen.
- 2026-07-29 10:52 MDT — Autopilot catalog seeded with 8 leaderboard pilots the user read by hand (AI_World_War_III, Inverse_Cramer, Jim_Simons_Tracker, Point72, Lobbying_Growth_Quiver, Mullin_Tracker, Calacanis_Tracker, Crenshaw_Tracker) + InTheMoney. Only InTheMoney has holdings→builds a curve; the 8 idle with _holdings_source tags (13F=EDGAR pull possible, PTR=congress infra, APP=user hand-reads). _normalize patched to skip _-prefixed metadata keys. Returns in the user list came via ChatGPT citing Autopilot MARKETING pages — not trusted; benchmark computes forward perf from public prices regardless. Holdings-sourcing plan pending user choice.
- 2026-07-29 11:10 MDT — 13F-tracker pilots populated compliantly: ~/scanner/fetch_13f.py pulls latest 13F-HR from SEC EDGAR (Renaissance CIK 1037389, Point72 CIK 1603466), takes top-15 long equity by value, resolves CUSIP→US ticker via OpenFIGI (free, exchCode=US to avoid foreign listings), writes {ticker:weight} into autopilot_catalog.json. Jim_Simons_Tracker (15 names: UTHR/PLTR/AAPL/KGC/MU...) + Point72 (12: SPY/NVDA/AMZN/QQQ/ANET...) now build public-price curves alongside InTheMoney; all three + qc_portfolio on the edge_ledger scoreboard (autopilot_cat:*). Rerun fetch_13f.py --write quarterly after 13F season. Remaining 5 leaderboard pilots idle (APP/PTR sourced): AI_World_War_III, Inverse_Cramer, Lobbying_Growth_Quiver, Calacanis (hand-read from app); Mullin/Crenshaw (congress PTR, not yet wired). Caveat baked in note: 13F ~45d lag, long US equity only = proxy not exact.
- 2026-07-29 11:15 MDT — Politician-tracker path proven: ~/scanner/fetch_congress_pilot.py reuses congress_feed parser to build per-member baskets from official House Clerk PTRs → catalog pilot. Confirmed the PRINCIPLE (these figures data is public + reconstructable). BUT per-member signal varies: Crenshaw = 1 filing/1 name (100% USOU) — he barely trades, thin/degenerate basket, NOT written. Pelosi-class active traders give rich books; light traders dont. Mullin = Senate (efdsearch.senate.gov), House feed cant see it — separate source needed. Remaining public-but-unbuilt: Lobbying_Growth (official Senate LDA API, strongest next), Inverse_Cramer (his public CNBC/social calls, needs methodology decision), Calacanis (private angel, weak). Catalog now tracking: InTheMoney + Jim_Simons_Tracker(15) + Point72(12) vs qc_portfolio on edge_ledger.
- 2026-07-29 11:35 MDT — **Catalog GUI + guest-edge leak fix.** (1) Lab.tsx: autopilot_cat:* pilots now render with full dossiers (blurb+detail+source+caveats) — specific entries for InTheMoney/Jim_Simons_Tracker/Point72 + generic infoFor() fallback for any future hand-added pilot; mode=benchmark now shows BENCHMARK badge (not PAPER), drawer shows Days-tracked + BENCHMARK—never traded. types.ts EdgeMode += benchmark. Rebuilt on Mac (no node on VM), dist rsynced to ~/trading-gui/dist (index-seHETea_.js), types.ts+Lab.tsx synced to canonical. (2) SECURITY: /api/edge had NO guest scrub — guests (Linus) saw the FULL edge ledger incl combined_mirror (LIVE book), SOXL_regime, ES_mean_rev, spy_overlay. Added _EDGE_GUEST_HIDE scrub in api.py _guest_scrub; verified guest key now excludes those 4, owner unaffected. api.py backup: api.py.bak-edgescrub. (3) FLAGGED to user, NOT yet fixed: Lab.tsx (mirrored to Linus via MVYTraders) contains descriptive STRATEGY_INFO dossiers of the private strategies (combined_mirror steers-the-live-book, SOXL Sharpe 1.21, etc) — source-level exposure predating today; server scrub protects live DATA but not the UI copy in the repo.
- 2026-07-29 12:00 MDT — **PRIVACY BOUNDARY (Robert directive): everything after the iPhone-app share with Linus is STRICTLY PRIVATE — owner side + Android app only, nothing to the shared Terminal/MVYTraders.** Applied to the Autopilot catalog work: (1) reverted Lab.tsx+types.ts to pre-catalog 7d2b1b4, rebuilt clean Terminal dist (back to index-CrzVt_Sc.js), redeployed; (2) autopush propagated the removal to MVYTraders, then SCRUBBED history via soft-reset to 4cb10f0 + force-push — catalog gone from remote working tree AND history (git log -S autopilot_cat empty); (3) api.py: /api/edge now strips ALL benchmark/autopilot_cat rows for EVERYONE (shared Terminal never shows catalog); NEW owner-only /api/catalog (pilots+curves+qc_reference) — not in guest allowlist so guests 403, owner+tailnet(Android) 200. Backend scanner catalog logic/data stays private (never mirrored). REMAINING: build the catalog view in the private Android app (Kotlin/Compose) consuming /api/catalog.
- 2026-07-29 12:20 MDT — **Android Catalog tab BUILT + shipped v1.34.** New private CatalogScreen (3rd bottom-nav Realm) in trading_android: DTOs (CatalogDto/EdgeStrategyDto/CatalogCurveDto), TradingApi.catalog()→/api/catalog, CatalogState+refreshCatalog(30s), pilot cards ranked by P/L w/ QC reference card, verdict pills, expandable holdings. Compiled JDK21, signed release v1.34. **OTA distribution was BROKEN by the Oracle migration** — publish.sh targeted dead Hetzner (5.78.111.168), AND NiceGUI static mounts (/static /dist /terminal-subpaths) all 404 to catch-all HTML on this deploy (Caddy just reverse-proxies :8080, no separate static serving). FIX: added FastAPI ROUTE /api/apk (FileResponse; routes work where static mounts dont); manifest at /opt/trading/dist/manifest.json url→http://100.73.225.4:8080/api/apk; /api/version→200 v1.34, sha verified end-to-end. publish.sh repointed to Oracle+/api/apk+oracle key. GOTCHA fixed: sudo mkdir /opt/trading made it root-owned → dashboard(ubuntu) couldnt read dashboard_keys.json → whole api mount threw → chown -R ubuntu:ubuntu /opt/trading restored it. Phone must reach tailnet 100.73.225.4; APK also at Mac ~/trading_android/dist/trading-1.34.apk for sideload.
- 2026-07-29 12:45 MDT — CORRECTION to OTA fix: OTA files moved off /opt/trading to ~/trading-dist. ROOT CAUSE of a scare: creating /opt/trading (even just /opt/trading/dist) flips api.py TRADING_DATA_DIR (=/opt/trading if exists else ~/trading-data) → swapped the whole data dir to an empty /opt/trading, dashboard auto-genned throwaway keys, Robert owner key cf62 got unauthorized on public gate. Fixed: sudo rm -rf /opt/trading (reverts to ~/trading-data, real keys cf62/7a0d + insider_bot/qc data), OTA now ~/trading-dist/{trading-latest.apk,manifest.json}. VERIFIED all green: owner cf62 role=owner, /api/version v1.34, /api/apk 200 12.6MB, catalog owner-only (guest 403), guest edge 13 rows no catalog leak. NEVER create /opt/trading on the VM.
- 2026-07-29 17:05 — **Catalog tab now in the owner Quarki333 Terminal (server-side injection, zero shared-source footprint).** Robert wanted today's Autopilot catalog benchmarks visible in his private GUI = the Terminal over tailnet (not just the Kotlin app). Implemented in ng_dashboard ONLY: `_SPAStatic` now injects `<script defer src=/owner/catalog.js>` into the SPA shell for no-XFF (owner-mode) requests; `/owner/catalog.js` route serves `ng_dashboard/static/owner_catalog.js` (vanilla-JS Catalog overlay: QC reference card, pilots ranked by P/L, verdict pills, NAV/sparkline, expandable holdings, 30s poll of owner-only /api/catalog) and 403s via the public gate + explicit XFF check. Guests get the vanilla shell — verified: public HTTPS shell clean, guest key 403 on bundle + /api/catalog, guest /api/edge has no catalog rows. PRIVACY SCRUB: the 17:37 auto-push commit pair that briefly put catalog blurbs in trading-gui Lab.tsx (reverted 17:57, exact cancel) was dropped from Quarki33/trading-gui history via reset+force-push; MVYTraders remote never saw it. Mac copies of ng_dashboard/api.py (+static) and trading-gui synced to deployed state (Mac api.py had been STALE pre-migration code). Also: trading_android v1.34 adb-installed on R5CT72VRSLJ.
- 2026-07-29 17:20 — **Directive auditor shipped (persistent cross-session compliance brain).** (1) Canonical standing-orders ledger `trading-stack/DIRECTIVES.md` (D1–D15, seeded from memory; every new standing order must be added there). (2) Mac launchd `com.quarki3.request-harvester` (300s): harvests every user prompt from ~/.claude/projects/* transcripts → ~/trading-data/user_requests.jsonl → rsync to VM (131 requests back-filled). (3) VM `~/directive_auditor/auditor.py` + systemd timers, fully independent of Mac/Claude sessions: `directive-audit-fast.timer` hourly at :17 (deterministic invariants: guest 403s on owner surfaces, private markers absent from MVYTraders/trading-gui trees+dist, public shell clean, $5-DD trigger vs portfolio_peaks/alpaca_live.json, /opt/trading absent, quarki-* units active) and `directive-audit.timer` daily 13:03 UTC (=07:03 MDT): invariants + claude-sonnet-5 review (ANTHROPIC_API_KEY in ~/anthropic.env chmod 600, copied from Mac zshenv) of the trailing 7 days of raw requests vs DIRECTIVES.md — flags unfulfilled orders + uncaptured directives. Reports → ~/trading-data/compliance/latest.md (+dated); ntfy only on violations + daily one-liner. First fast run: 0 violations, all D1 privacy checks green.
- 2026-07-29 17:50 — **Catalog expanded 3→10 pilots (all public-source, D4-clean — zero Autopilot data).** Added from Q1-2026 13Fs (dataroma/insidearbitrage, read 2026-07-29): Buffett_Tracker (Berkshire top-15, 94% of book), Ackman_Tracker (Pershing, all 11 positions), Tepper_Tracker (Appaloosa top-15), Tiger_Global (Coleman top-15), Druckenmiller_Tracker (Duquesne top-14 ex-options); Burry_Tracker (Scion Q3-2025 13F — STALE, fund deregistered, final 3-stock book MOH/LULU/SLM); Cathie_Wood_ARKK (ARK official daily CSV top-15 minus private SpaceX/OpenAI placeholders). Curves struck at NAV 100 today, every ticker priced; edge_ledger rebuilt; /api/catalog serving 10 pilots + 10 curves. APP-source stubs (AI_World_War_III, Inverse_Cramer, Lobbying_Growth_Quiver, Calacanis) still idle awaiting hand-read from the app; PTR stubs (Mullin, Crenshaw) could be filled from congress_feed later.
- 2026-07-29 18:02 — **Catalog dossier blurbs (Lab parity) + v1.35 + GitHub sync sweep.** (1) Every catalog pilot (10 active + 6 stubs) got a hand-written plain-language `_blurb` in autopilot_catalog.json; /api/catalog now returns a `meta` map (blurb/source/note per pilot) so ALL surfaces render explanations data-driven; owner_catalog.js shows blurb+source pill on each card, provenance note in holdings drawer; Android v1.35 (CatalogMetaDto) shows blurbs on cards — OTA published (manifest v35, sha verified) + adb-installed. (2) publish.sh DIST_REMOTE fixed /opt/trading/dist → ~/trading-dist (would have re-created the /opt/trading data-dir hijack). (3) vm-autopush.sh now also mirrors directive_auditor/auditor.py, directive-audit* systemd units, autopilot_catalog.json into trading-stack; mac/ dir holds harvester script+plist. All repos pushed: ng_dashboard, trading-stack, trading-system (Mac trading-sync launchd job was DEAD since migration — reloaded via legacy load -w; repo was 2d stale). (4) **SECURITY FINDING:** the 17:37–17:57 UTC catalog-blurb leak DID reach GitHub MVYTraders as commits 7ef46f4/7730741 (mirror leg of autopush), scrubbed by force-push at 17:58 — but both commits remain fetchable BY SHA on GitHub (dangling objects). Exposure: Lab.tsx dossier text only (pilot names/descriptions; no holdings/keys/backend). Options: GitHub Support gc request, or delete+recreate repo — Robert to decide.
- 2026-07-29 18:10 — **D1 privacy guard + dangling-commit follow-through.** Robert confirmed: complete the scrub, keep all post-iPhone-share work private. (1) vm-autopush.sh now has a hard D1 guard: if trading-gui tree contains private markers (autopilot_cat, /api/catalog, owner_catalog, directive_auditor, DIRECTIVES.md), the MVYTraders mirror AND its commit/push leg are SKIPPED + ntfy alert fires. Tested both ways (clean=mirrors, planted marker=blocked); test commit dropped from trading-gui history via reset+force-push. (2) GitHub Support draft for purging dangling commits 7ef46f4/7730741 saved at trading-stack/github_support_request.md — Robert submits at support.github.com (or opts for repo delete+recreate, which needs re-inviting Linus). Guest surfaces re-verified clean by directive-audit-fast.
- 2026-07-29 18:18 — **Full catalog dossiers + persistent nav + auditor LLM brain live.** (1) Every catalog pilot (16) now carries a full Lab-grammar dossier (_detail paragraph, _consumes/_emits/_cadence) in autopilot_catalog.json → /api/catalog meta; Terminal owner panel rebuilt: whole card = single tap → full dossier (detail, signal-bus rows, stat grid incl days-tracked, sparkline, complete holdings table, provenance); Android v1.36 same treatment, OTA + installed. (2) trading-gui: bottom tab bar z-20→z-50 so all 6 buttons stay tappable above the Lab dossier drawer (+drawer pb-20); rebuilt dist deployed to VM, source pushed (e71f034), mirrors clean to MVYTraders. (3) Auditor LLM pass WORKS via VM claude CLI on Max subscription: Mac Keychain OAuth creds → VM ~/.claude/.credentials.json; harvester now auto-resyncs creds whenever Mac copy is newer (rotation-proof); placeholder ANTHROPIC_API_KEY removed from ~/anthropic.env + stripped from CLI env (was shadowing OAuth). First real audit verdict ATTENTION: proposed D16 (autopush default) D17 (secret hygiene; SnapTrade secret pasted in chat 7/27 — rotate when feed next used; repo histories grepped CLEAN, config file chmod 600) D18 (Linus branch isolation) — all added to DIRECTIVES.md; collaborator sweep confirms ONLY MVYTraders has lmunn1; flagged still-open ask: the 7/27 "$50 Alpaca vs QC why no profit" comparative analysis was never delivered.
- 2026-07-29 18:35 — **DATA CENTERS shipped (Book-page clone behind every Lab + Catalog card).** Catalog (owner-only owner_catalog.js): tap a card → hero NAV + vs-inception/today deltas, REAL TRACK RECORD section (_record added for all 10 pilots — Medallion 66%/yr, Druck 30/30yr, ARKK -81% maxDD etc.), dossier + consumes/emits/cadence, big SVG equity chart w/ dashed inception-100 baseline + y-ticks, drawdown-vs-peak bar (now/max), Book-style stacked allocation bar + legend + holdings table (shares, value/100 NAV, weight), ACTIVITY LOG (daily NAV marks Δ%, hand-update + inception events). Lab (shared trading-gui, marker-clean): new owner-only /api/labbook/{name} endpoint reads scanner state read-only — mirrors (qc/congress/patent/combined) get positions+targets+order log, prop_desk:* get cumulative realized-P/L index rebuilt from prop_ledger (momentum: 183 pts, -61.3% total — ouch) + trade log; new quarki-labbook.timer (15-min) appends mirror paper equity to state/labbook_curves.jsonl so mirror curves accrue from today. Drawer renders paper-book hero, EquityChart, AllocationBars current-vs-target, PositionsTable, scrolling activity log — Book grammar via new components/BookKit.tsx (extracted from Book.tsx). Book page gained a DECISIONS panel (target=QC mirror via governor, promotion gate, settled-BP/weekend/cap guards, $5-DD protocol). Guests: /api/labbook 403 (not in allowlist). Android catalog/lab parity = next session (v1.37).
- 2026-07-29 18:38 — **Catalog performance charts (Book-parity) via 1y composition backfill.** New ~/bin/backfill_catalog_curves.py (quarki-venv/yfinance): marks each pilot's CURRENT composition backward over 1y of public daily closes, rebased to end at today's live NAV → state/autopilot_catalog_backfill.json (separate file so the 6h catalog tick can't clobber it; regenerates only when weights change, stamp-hashed; wired as 2nd ExecStart of quarki-labbook.service). /api/catalog merges it as curves[name].backfill. owner_catalog.js chart: merged 1y+live series, area fill, y-ticks, dashed vertical "live ▸" marker at inception (Book marker grammar), caption disclosing backfill-vs-live; drawdown bar computed over merged series. Verified: 10 pilots × 251 days each.
- 2026-07-29 18:45 — **Interactive data-center charts + 6 range tabs + SessionStart standing-orders hook.** (1) Backfill v2: state/autopilot_catalog_backfill.json now aligned arrays {dates[251], nav[], rel{sym:[]}} — per-ticker prices relative to today, so the client can recompute the FULL allocation mix at any historical date (314KB, 10 pilots). (2) owner_catalog.js data centers rebuilt interactive: range tabs 1D/1W/1M/3M/6M/ALL; tap-or-drag scrubber on the chart moves a gold indicator to any date and LIVE-updates the hero (NAV @ date, vs-inception, day change, now-vs-date) and the holdings table + allocation bar (weights@date = w_now×rel normalized); "● now" chip resets; rAF-throttled. (3) trading-gui EquityChart (Book home chart): added 1M/3M/6M tabs alongside 1D/1W/ALL (pushed 59c6a2a, dist deployed). (4) Claude Code SessionStart hook added to ~/.claude/settings.json: every session boots with DIRECTIVES.md + latest VM compliance report injected into context (12KB cap, 5s ssh timeout fallback).
- 2026-07-29 19:15 — **Attribution clarity sweep (user: "make sure everything is separated out in a meaningful and clear way").** Root confusion: Book target bar said "target (qc mirror)" but /api/qc mirror_targets is the GOVERNOR BLEND (patent 74/qc 19 tonight). Fixed across trading-gui: Book target bar → "target (governor blend)" + live one-line weight readout (blend: patent 74% · qc 19% …) sourced from /api/governor; Decisions card rewritten (blend of all proven sources, not "mirrors QC"); guest Book renamed "Strategy Book — paper mirror" (was "QC Mirror"), equity/actions card titles updated. Lab: qc_mirror drawer was rendering the BLEND under "Composition/targets" while claiming it was QC's own book — now uses its labbook targets (AAPL 100%), dossier + Book-link text corrected ("one input to the Book", weights on Governor tab). Governor page error-state de-staled (removed Mac path + "QC ~92%" claim). Pushed e-series commit; dist deployed; wrapper refreshed. Also answered: current-vs-target drift = fresh AAPL 20% target (QC rotation) + $0.81 settled BP (T+1) + market closed + patent-trio rally drift + sub-$1 dust.
- 2026-07-29 19:22 — **Purpose banners on every page.** New components/PageIntro.tsx (Lab-banner grammar): Book ("the money page" — real vs paper-mirror variants), Governor ("the capital allocator — these weights ARE the Book target"), Markets ("context & signal feeds — nothing here trades"), Health ("stack vitals"), Settings ("this device connection"); Lab keeps its existing banner; Catalog overlay header expanded ("the benchmark shelf — display-only, can never receive real money"). Pushed + dist deployed + wrapper refreshed.
- 2026-07-29 19:20 — **Catalog card headlines: since-inception 0.00% → trailing-1y performance** (from composition backfill), with "1y" suffix; ranking now sorts by 1y too. First real leaderboard: Druckenmiller +40.7, Buffett +31.2, Simons +25.1, InTheMoney +21.7, Point72 +20.6, Tepper +16.2, Tiger +8.2, Ackman -10.6, Burry -16.9, ARKK -23.7. QC reference keeps its real total (+68.5).
- 2026-07-29 19:30 — **Governor tempo control + catalog→tournament promotion pipeline + P/L alignment.** (1) strategy_governor.py: tempo presets read from state/governor_config.json every tick (no restart needed) — steady (old behavior) vs ADAPTIVE (4h window, L1 0.04, sticky 0.02, WEIGHT_POWER 1.6 winner-take-most exponent applied to edge-proportional raws pre-normalization). Adaptive ENABLED per Robert ("always be using the top strategy… adapt by day or hour"); first tick under it: patent 68.4/qc 25.4 (QC up from 19 — 4h window sees AAPL strength). Kill switch unchanged; execution still T+1-bounded. Backup strategy_governor.py.bak-tempo; scanner restarted clean. (2) ~/scanner/promote_pilot.py: promotes catalog pilot(s) into the ALREADY-WIRED autopilot lane (writes blend into autopilot_holdings.json → 5-min paper mirror → governor scores autopilot_PAPER → D7 gate → real weight); --status/--demote; lane currently IDLE awaiting Robert's first pick on FORWARD performance. D19 added to DIRECTIVES. (3) owner_catalog.js: .qtcat-pnl class right-aligns every card headline P/L (margin-left:auto, nowrap).
- 2026-07-29 19:32 — **Alpaca intraday-rules check (user concern) → cleared + guarded.** Verified via live API: account multiplier=1 (CASH account, shorting off, no PDT flag/daytrade counter) → PDT rule inapplicable; the binding constraint is good-faith violations, which the rebalancer structurally cannot commit (buys sized to non_marginable_buying_power = settled cash only, so no position is ever bought unsettled). Real cost of adaptive tempo = T+1 settlement drag + spreads, already damped by $1 min order + L1 0.04 gate. Added D19 churn tripwire to hourly auditor: >12 live orders/day → violation ntfy (today: 0).
- 2026-07-30 09:55 — **Allocation bars upgraded (Book + all Lab data centers via shared BookKit).** Each bar (current AND target) now carries its own legend directly beneath it — color chip + ticker + pct, biggest→smallest left-to-right matching segment order. Colors switched from hash-hue (could collide) to a 16-color high-contrast categorical palette assigned by cross-bar holding rank, so adjacent slices always contrast and the same symbol keeps one color in both bars. Legacy symColor export kept for the catalog panel. Pushed + deployed + wrapper refreshed.
- 2026-07-30 11:00 — **T+1 wait eliminated: margin-aware sizing live (deposit: $0).** Chain of findings: (1) account has 4x max-multiplier setting → margin agreement on file → sub-$2k = Alpaca "limited margin" tier: unsettled proceeds spendable, GFVs do not exist; (2) Robert flagged + web-verified: PDT RULE REPEALED — SEC approved 2026-04-14, effective 2026-06-04 (FINRA RN 26-10), 18-mo broker phase-in to 2027-10; Alpaca API already returns daytrade_count/pattern_day_trader = None. combined_mirror.py changes (backup .bak-margin): buy budget non_marginable_buying_power → min(buying_power, cash) — NEVER borrows, so margin calls + risk-based intraday margin standard structurally unreachable; same-day-sell PDT guard added anyway as phase-in house-rule hedge (skips sells of symbols bought same day, logs "sells deferred (PDT guard)", midnight-ET boundary derived from Alpaca /v2/clock — VM is py3.8, NO zoneinfo, first deploy import-crashed and was fixed). $2k deposit question moot (only buys 2x leverage we would not use). Rotations now complete same-day at $50 scale.
- 2026-07-30 11:05 — **PDT guard removed after verification (Robert: "don't hedge on something you can just verify").** Verified from Alpaca's own sources: PDT scrubbed from margin docs; official @AlpacaHQ announcement + docs changelog 2026-06-03 confirm the Intraday Margin Framework went live at Alpaca ON June 4 2026 (no phase-in lag) and PDT fields are deprecated (why the API returns None). Same-day-sell guard stripped from combined_mirror.py; deferral label renamed no_settled_bp → no_spendable_bp; D20 updated. Unlimited day trading on the $50 account, bounded only by cash, $1 min order, spreads, and the D19 churn tripwire. Also: first same-day rotation confirmed 10:55 MT — VTRS +$1.35 bought with morning's unsettled AAPL proceeds, VTRS at target 33.1%.
- 2026-07-30 11:20 — **Governor decision log + descriptive recent actions.** (1) strategy_governor.py appends every evaluation to state/governor_decisions.jsonl: published (leader, rolling P/L vs baseline, L1 vs gate, tempo), held (plain-language "rankings moved only X — below gate"), kill_switch. (2) combined_mirror.py: every BUY/SELL ledger entry carries a why-string ("under target: held 30.9%, governor target 33.2% (blend: patent 99% · qc 0%)" / "dropped from target — liquidating"). (3) New owner-only /api/governor/history merges decisions + alerts + trades, newest-first (guest 403 verified). (4) Terminal: Governor page gained "Decision log — every governor action & why" (scrollable max-h-96, icons per kind, weight chips, reason lines); Book Recent actions render the per-trade reason under each fill (joined by symbol+side ±20min). Trail accrues from today. Pushed + deployed + wrapper refreshed.
- 2026-07-31 10:50 — **Dip-day circuit breaker + rotation-hygiene gates (after the INTC whipsaw).** Diagnosis: adaptive governor flipped patent→congress OVERNIGHT (closed-market rolling-window drift, publishing nearly every 15min), rotated ~70% of book at open into INTC-heavy congress basket (INTC -4.2% same day), then flipped BACK intraday (~19 live orders; D19 churn cap 12 will flag). Day -$0.62, dd $2.71/$5 amber. Fixes: (1) combined_mirror daily CIRCUIT BREAKER — day P/L ≤ -1.5% (risk_config.json, hot-read) freezes ALL trading for the session, holds positions, auto-resumes next day, one ntfy; (2) governor gates — no weight rotation while market closed; NEW leader needs 4 consecutive lead evaluations (~1h, governor_leader_pending.json) before capital follows; both logged to the decision log as gated holds. D21 added. Backups .bak-gates/.bak-breaker. Honest framing to Robert: dip was mostly self-inflicted whipsaw, not market; freeze alone would not have prevented it — decision hygiene does.
- 2026-07-31 10:55 — **60-day rules backtest (May 6→Jul 31, 15-min bars, current compositions held fixed).** Sources over span: qc +12.7% / patent -1.0% / congress -12.0% / equal-mix -0.1%. Variants: steady +0.44%, adaptive_raw +1.76% (304 publishes), adaptive+gates +2.13% (gates BLOCKED 2343 overnight publishes + 27 unconfirmed flips AND beat raw — hygiene is free alpha), full_new(+breaker) +1.98% (11/60 days frozen; breaker = ~0.15pp insurance premium, no in-sample DD improvement). All variants maxDD ~10-12% → would trip the $5 review trigger; concentration is the risk. MAX-WEIGHT CAP TESTED AND REJECTED: cap80 -0.18%, cap60 -3.27%, cap50 -5.28% — caps redistribute into LOSING sources (not cash), erasing the leader edge while barely denting maxDD (congress -21% DD leaks in). DECISION: no cap; keep gates+breaker; $5 trigger remains the human escalation for multi-day bleeds; future DD lever if wanted = cash-fallback (scale to cash when leader rolling P/L < 0), untested. Results: trading-data/backtest_rules_result.json; harness /tmp/backtest_rules.py (also in scratchpad).

## 2026-08-02 — D19 churn check false positive fixed
- 02:17 MDT ntfy "D19 churn: 19 live orders" was a FALSE POSITIVE: the auditor
  grepped BUY/SELL substrings from live_activity.jsonl, which matched the paper
  prop-desk 24/7 crypto signal summaries ("[strat_mom] BUYs=1 SELLs=2"), and
  that log is trimmed to ~300 rows so counts were noise. Zero real orders
  fired; market was closed and Alpaca confirms 0 orders for the day.
- Fix (directive_auditor/auditor.py, backup auditor.py.bak-d19): D19 now
  counts ground truth — Alpaca live /v2/orders?status=all&after=<midnight ET>,
  keys parsed from ~/quarki.env. Verified: "D19 live orders today: 0".

## 2026-08-02 — Profit-taking audit (17-agent workflow) → sell guards + shadow PT

- **Audit of the live $50 book (7/13–7/31, 157 fills, 142 FIFO round trips, realized −$2.35):** rebalance-only exits are a systematic anti-take-profit — sells land at the 12th percentile of the prior-48h price range (27/37 patent-slice sells in bottom 30%); 39/87 patent trips peaked >+1% MFE, zero exited near peak. Costliest untreated class: 48h weight-oscillation (full exit → rebuy days later at worse price: TEVA −$0.44, AMZN −$0.17, VTRS/GOOGL ≈ −$0.70 total) — NOT covered by the D21 intraday breaker.
- **Backtested TP/trailing rules all FAILED adversarial verification** (9/9 rule×lens verdicts negative): headline deltas (+$1.8–2.6) were 84–97% one AMD crash event; ex-AMD ≈ +$0.1–0.4 = noise; limit fills unimplementable (market-only notional orders); rule-sells would be rebought next tick absent suppression. Workflow: wf_09d00363-2e6.
- **Shipped to `scanner/sources/combined_mirror.py`** (backup `.bak-pre-pt-guards`, Mac copy synced):
  1. **Re-entry premium guard** — fully-sold symbol rebought <48h later only if price ≤ exit×1.01 (else skip, logged `reentry_premium`); fails open on price-fetch failure.
  2. **Minimum-dwell guard** — no full exit of a GREEN position <24h old on a weight flip (losers never dwell-held).
  3. **Profit-take engine in SHADOW mode** — arm at +1% from entry, fire on 0.5% giveback from peak → logs to `state/pt_shadow.jsonl`, ZERO orders. Live mode exists behind `risk_config.json profit_take.mode` but stays shadow until the shadow ledger proves it (8–12 wks, ≥150 entry events, clustered-bootstrap CI excl. 0, no single event >50% of delta).
- Config: `state/risk_config.json` gains `profit_take` + `sell_guards` blocks (hot-reloaded). New state keys: pos_since, pt_peaks, last_exits, pt_suppress, pt_logged. Deployed, manual tick OK, quarki-scanner restarted.

## 2026-08-02 — D23: verified sweep winners are live-eligible
Robert (during ultracode full-stack profit sweep): "you can always put the
real winners in the actual alpaca trading book profit rotation to make sure
we're not missing any valuable strategies." Added D23 to DIRECTIVES.md:
strategies that survive adversarial multi-agent verification with real
evidence may enter the live Alpaca governor rotation directly (forward-scored
only); raw ideas still paper-first; all risk rails (D5/D6/D20/D21, -8% kill,
D4 catalog money-proofing) unchanged. Mac copies of DIRECTIVES.md/CONTEXT.md
synced. Sweep workflow (8 auditors + adversarial verify + completeness critic)
running; results and any live wiring will get their own entry.

## 2026-08-02 (later) — Round-2 audit: hard stops REJECTED, eligibility gate → shadow

- **Hard-stop family (avg & peak basis, s=2–8%): rejected, all 12 cells.** Headline +$1.25 (peak s=4%) was 100% the single AMD 7/13–15 event; ex-AMD every cell negative (−$0.38 to −$1.49); 11 of 12 stops later recovered above the stop price (whipsaw tax, MU −$0.58 alone); mirror-faithful rebuy sim flips the best cell to −$1.15. Clustered P(≤0)=0.70. A hard stop on this mean-reverting book is −$0.6..−$2.4/mo. Do not revisit without new mechanics.
- **Per-symbol eligibility gate: shadow-only.** Best cell (block 5d when last-5 whole-position closes cum P/L < −$0.10 or < −0.5% notional) = +$0.36, but ONE blocked RDY order (7/21) is 103% of it; post-bugfix component is +$0.04. Real merit: the 7/29 RDY 8-entries-in-6h rebuy spam is live-regime and no existing guard catches it (premium guard misses by 2min/0.25%).
- **Shipped:** `elig_gate` shadow in combined_mirror.py (backup `.bak-pre-elig`, Mac synced) — whole-position close history in state (`elig_hist`), would-block events → `state/elig_shadow.jsonl` with `also_blocked_by` attribution, zero order-flow impact. Config: risk_config.json `elig_gate` block.
- **Promotion bar (forward evidence only):** ≥3 distinct symbols with block episodes; ≥10 events with empty `also_blocked_by`; counterfactual positive ex-best-event and non-negative in ≥2 of last 3 weeks; then ≥4wk Alpaca-paper A/B before live wiring. Workflow: wf_f3232b9a-03b.
- **2026-08-02 addendum:** `quarki-shadow-review.timer` (Mondays 14:00 UTC) runs `scanner/shadow_review.py` — ntfy push summarizing pt_shadow/elig_shadow event counts vs the D22/D22a promotion bars. Test push verified.
- **2026-08-02 addendum 2:** SMS leg added to reminders — `scanner/send_sms.py` (Twilio REST, config `/home/ubuntu/twilio.env`, template at `~/twilio.env.template`, SMS_TO +19148418804); shadow_review.py sends ntfy + SMS; silent no-op until Robert completes Twilio signup and fills the env file.

## 2026-08-02 — CRITICAL FIX: circuit breaker was liquidating, not holding
Ultracode sweep (8 auditors + adversarial verify) found the D21 breaker
violated its own spec: on a -1.5% day the freeze set desired={} but the
orphan-liquidation sweep (combined_mirror.py, gated only on market_open)
then treated every held position as an orphan and market-sold the entire
book — while the log/ntfy said "holding positions". Confirmed against the
2026-07-31 event: 9 full-book SELLs (~$44.4) at 12:36 ET on the breaker
day, book 98% cash since. Fix: orphan sweep now gated
`if market_open and not frozen:` — freeze truly holds; sells at the low
and the T+1 dead-cash tail are gone. Backup at
combined_mirror.py.bak-20260802-breaker; scanner restarted, ticking ok;
Mac copy synced. Book redeploys per governor at Monday open.

## 2026-08-02 — Tiered notifications: self-heal silently, page only when human needed

- Robert: too many "something went wrong" pings for events the stack already self-heals. New central notifier `~/bin/quarki_notify.py` maps 4 levels to ntfy priorities: **critical P5** (🚨 alarm; $5 DD breach D6, self-heal NOT sticking; also SMS once twilio.env is filled) · **action P4** (⚠️ needs a decision: directive violations, disk low, circuit breaker, DD amber ≥$4, leader-check swap suggestions) · **info P3** (weekly shadow review) · **routine P2** (silent: self-healed restarts, auto-swaps, clean daily audits).
- `vm_watchdog.py` rewritten (backup `.bak-pre-tiers`): restart-and-recover events are now **routine/P2 (silent)**; escalates to **critical** only when the same unit/tick needs healing ≥3× in 45 min ("self-heal not sticking — needs a human"). Added $4 amber DD tier (action) below the $5 critical trigger.
- Patched senders (all `.bak-pre-tiers`): auditor (violations P4, clean daily P2), catalog_leader_check (auto-swap P2, decision-needed P4), alpaca_paper_arm (live DD P4), combined_mirror breaker (P4), shadow_review (P3).
- Phone side: ntfy app default already silences low/min; optionally set priority≥high to alarm/override-DND.

## 2026-08-02 — D25: meta-level Claude consumption manager

- Trigger: burst 429 rate-limit errors during the 08-02 24-agent sweeps; weekly
  weighted-unit burn measured at ~83% of estimated budget. Robert: run
  continuously without running out of usage.
- Mac: ~/consumption_manager/consumption_manager.py (scan|gate|calibrate) parses
  ~/.claude/projects transcripts (deduped per-request usage, pricing-ratio
  weighted units, per-model multiplier) → rolling 5h + 7d windows → tier
  GREEN(<70%)/AMBER(<90%)/RED in state.json. launchd
  com.quarki3.consumption-manager every 15 min; tier transitions notify via
  ~/bin/quarki_notify.py (RED→action P4, else routine P2 silent). quarki_notify
  ported to Mac ~/bin.
- VM: state synced to ~/trading-data/consumption/state.json each scan;
  ~/bin/claude_gate.sh exits 0/1/2 (GREEN/AMBER/RED, stale>36h→AMBER) for any
  future Claude-invoking job (auditor LLM pass, sweeps). Auditor D25 check:
  state fresher than 48h else violation.
- Budgets empirical: 5h=90M units (peak 72.8M passed clean), weekly=350M
  (290M reached without lockout; amber 70%=245M ≈ where 429s began).
  Recalibrate on new 429 evidence (`calibrate` subcommand lists ceilings).
- Policy: AMBER → defer optional swarms/cloud sweeps, cap agent fan-out 4-6;
  RED → interactive/critical only. DIRECTIVES.md D25 added (Mac+VM synced;
  Mac copy was stale pre-D22a/D24 and is now current).

## 2026-08-02 — context_watcher spam collapsed (4,233 dup lines removed)

- context_watcher re-flagged the same state files every 30 min since ~07-29; now
  idempotent (skips files already mentioned in this doc). Unique files it flagged,
  all belonging to documented systems (governor, congress, autopilot catalog,
  labbook, risk): autopilot_catalog.json, autopilot_catalog.json.bak-20260802, autopilot_catalog_backfill.json, autopilot_catalog_curves.json, autopilot_feed_config.json, autopilot_holdings.json, autopilot_mirror_state.json, congress_feed_cache.json, congress_holdings.json, congress_mirror_state.json, edge_ledger.json, governor_config.json, governor_decisions.jsonl, governor_leader_pending.json, governor_weights.json, labbook_curves.jsonl, leader_check_config.json, risk_config.json, risk_state.json

## 2026-08-02 (late night) — Ideas sweep verdicts + Overnight Drift paper lane
- Value-screen program CLOSED: round-2 double-SHELVE (quality-momentum-pullback CAGR 8.64%/Sharpe 0.544 strictly worse than shelved v2; lane-feed ranking alpha nil vs SPY and sign-flips 4/11 years — universe_hold beat the ranked lane, SPY beat both). Judge re-ran both backtests, reproduced identically. VERDICT_ROUND2.md, commit 8525b61 on value-screen-v2. No round 3.
- "Ideas from Claude" sweep: 12 original candidates, adversarial kill panel killed all as-submitted (5 false stack premises, 2 empirically dead when replicated, 2 broken data paths, 2 weak evidence, 1 wrong routing). Zero entered any lane.
- The one fully-replicated mechanism — overnight anomaly (IWM +6.2bp/night Sharpe 1.13, QQQ +5.0bp/0.94, n=1505 nights 2020-26, independently reproduced by the kill agent) — rebuilt on the honest route: `scanner/sources/overnight_drift.py`, a STRUCTURALLY paper-only D7 candidate lane (paper-api hardcoded, no live mode exists). Buys $25 IWM + $25 QQQ at 15:58 ET, sells 09:31 ET; ledger state/overnight_drift_ledger.jsonl logs fill-vs-print slippage on BOTH legs (the kill panel objection: Alpaca fractional has no MOC/OPG, so fills are continuous-session, not auction prints) plus realized bp and sleeve parity for the future settled-cash two-sleeve design. Errors page P4 via quarki_notify; normal ops silent.
- PENDING HUMAN STEP: systemd units staged at /tmp/quarki-ondrift-{buy,sell}.{service,timer} on the VM; Claude Code permission classifier blocked the sudo install. Enable with: sudo cp /tmp/quarki-ondrift-*.service /tmp/quarki-ondrift-*.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now quarki-ondrift-buy.timer quarki-ondrift-sell.timer
- D7 gate for this lane: n>=30 nights, mean>=0.05%/trade realized, Sharpe>=1, RT slippage <=5bp; review rides the weekly Monday shadow-review push once rows accumulate.

## 2026-08-04 — Overnight drift paper lane went live (timers installed, undocumented until now)
- Improvement-bot cycle found `quarki-ondrift-buy.timer`/`quarki-ondrift-sell.timer`
  active and enabled (unit files dated 2026-08-04 01:21 UTC in
  `/etc/systemd/system/`), superseding the 2026-08-02 BACKLOG "blocked: staged in
  /tmp, needs Robert's sudo" note — someone (presumably Robert, since Claude Code's
  permission classifier blocked the sudo install) ran the install manually and it
  was never logged.
- Verified before logging: both services still call `sources/overnight_drift.py`
  unmodified — `TRADE_BASE` hardcoded to `paper-api.alpaca.markets`, only
  `ALPACA_PAPER_KEY`/`ALPACA_PAPER_SECRET` read (both present in `quarki.env`),
  structurally D7-safe regardless of what happens. Script `py_compile`/ast-parse
  clean. Timers correctly scheduled (`Mon..Fri 09:31`/`15:58 America/New_York`),
  `systemctl is-enabled` → enabled on both. Neither leg has fired yet as of this
  check (03:46 UTC 2026-08-04); next: sell 13:31 UTC, buy 19:58 UTC today.
- No action taken beyond verification — nothing to fix, just closing a
  cross-session documentation gap (D15). Ledger file doesn't exist yet
  (pre-first-fire); BACKLOG.md follow-up added to check it after today's cycle.

## 2026-08-04 (afternoon) — Senate eFD wired for Mullin_Tracker (backlog #3)

- Researched `efdsearch.senate.gov`'s fetch path (blocked/unresearched since the
  2026-07-27 congress_feed.py build, which only reads House Clerk data). Found
  and verified against live data: a one-time "prohibition agreement" POST sets
  a session cookie (warning text prohibits political/charitable solicitation
  and resale — not research use, same public-disclosure category as the House
  Clerk feed already read); after that `/search/report/data/` is an
  unauthenticated DataTables AJAX endpoint filterable by `last_name` +
  `report_type=11` (Periodic Transactions); PTR "view" pages are plain HTML
  tables (Ticker/Asset Name/Type/Amount) — no RC4/CID PDF extraction needed,
  easier than the House parser. Confirmed live against Sen. Markwayne Mullin:
  39 PTR filings found, one fetched and parsed cleanly (UNH/AZO/INTU trades,
  correct kind/amount extraction).
- **Shipped:** `scanner/sources/congress_feed_senate.py` (session/search/parse,
  fail-closed — raises rather than returning a partial basket) +
  `scanner/fetch_senate_pilot.py` (companion to `fetch_congress_pilot.py`,
  writes `Mullin_Tracker` into `autopilot_catalog.json`, catalog row only —
  D4 money-proof, no live/paper order flow). Wired into
  `catalog_leader_check.py`'s existing Monday refresh block alongside
  Crenshaw, same non-fatal subprocess pattern. Backups:
  `catalog_leader_check.py.bak-20260804T1553` (scanner repo),
  `DIRECTIVES.md.bak-20260804T155442Z`. `py_compile` clean on all 3 files.
- **Verification caveat:** a live end-to-end run of `fetch_senate_pilot.py`
  hit `efdsearch.senate.gov` returning its own "Site Under Maintenance" page
  (Akamai-served) partway through testing, then kept 503ing the search POST
  specifically even after the homepage recovered — plausibly transient
  server-side instability, or my own burst of ~6 test requests in ~15min
  tripped a soft rate-limit on that one endpoint. Either way: correct
  fail-closed behavior confirmed (autopilot_catalog.json's Mullin_Tracker
  entry is untouched — no partial write happened), and the *code path itself*
  was independently proven against real captured responses before the outage
  (agreement flow, search results, PTR table parse all verified with live
  data, see BACKLOG.md follow-up). No live weight, no risk from the outage —
  next Monday's `quarki-leadercheck.timer` run (or a manual retry once the
  Senate site is confirmed stable) will complete the first real fill.
- D24(c) in DIRECTIVES.md updated: "Mullin blocked" → wired, same CHECK line.

## 2026-08-04 — $450 live deposit + deposit-aware P/L baseline fix
- Robert deposited $450 into live Alpaca (combined_mirror book): $50 → $500.02.
  Bot deployed it same-session (INTC/AB/UBER/GOOGL/AMZN/NVDA/AAPL/VST fills 18:29Z).
- BUG FIX (combined_mirror.py): live P/L was measured against the persisted
  first-seen baseline (~$49.76), so the deposit read as +900% profit. Added
  _net_deposits_since() — sums CSD/CSW activities after baseline_captured_at
  (anchored 2026-07-14, after the 7/02 $50 funding) and treats them as
  contributed capital, not P/L. Fail-safe: on activities-API error, falls back
  to last persisted net_deposits instead of reverting to deposit-as-profit.
  State now carries baseline_captured_at + net_deposits. Verified live:
  equity $500.08 → pnl +0.064% (+$0.32). Backup: combined_mirror.py.bak-*.
- OPEN QUESTION for Robert: D6 $5-drawdown review trigger is now 1% of the
  $500 book — the $4 amber alert will fire on ordinary daily vol. Needs a
  widened threshold (D6 anticipated this).
=======
- 2026-08-02 (eve): D25 RED fire-drill — fleet frozen by consumption gate; 3 sessions killed by Robert (terminal mouse-mode garbage from SIGSTOP). Added ~/bin/unfreeze_watcher.py (one-shot, 24h): SIGCONTs frozen sessions 54310/46032/46120/46147 when tier leaves RED and 5h<75%, silent P2 ntfy. Trading stack (VM) unaffected throughout.

## 2026-08-02 (night) — D26 session governor: usage governed, prompts never blocked
- Robert (5h window at ~89%): "govern that… run a meta manager across all active claude sessions… never block my ability to input prompts."
- New `~/consumption_manager/session_governor.py` + launchd `com.quarki3.session-governor` (120s): classifies every claude process each cycle. tty-attached (interactive) + daemon/bg-spare (infra) = PROTECTED, force-resumed if ever found stopped (P4). Headless sessions = governed by auto_tier: RED pause all, AMBER cap at max_concurrent_heavy, GREEN resume all. SIGSTOP only, never SIGKILL.
- First cycle fixed fire-drill collateral: daemon 46032 had been left SIGSTOPped (degrades interactive sessions) — resumed; headless computer-use session 46120/46147 correctly held paused under RED.
- Supersedes one-shot unfreeze_watcher (left running; exits harmlessly on first resume). D26 added to DIRECTIVES.md with CHECK lines.

## 2026-08-02 (night) — D25 meter recalibrated against official /usage
- Robert flagged weekly 93.6% as wrong; official /usage showed weekly **1%**. Root cause: official weekly limit is a FIXED window (resets Sundays 5:59pm Denver; week had reset 12 min before), meter was using rolling trailing-7d.
- consumption_manager.py: weekly window now anchored (`weekly_reset_anchor` in config, steps in UTC; ~1h DST drift, recheck each Nov/Mar); forecast rewritten for fixed window (crossings past reset = null; retracts the earlier false "~12h to interactive lockout" alert — real answer was 164h, past reset). 5h budget recalibrated 122.5M→117M units (official 93% ≙ 108.35M). Post-fix: weekly 0.9% vs official 1% ✓, 5h 96.6% vs official 93% (slightly conservative).
- /usage also confirmed: 65% of burn = workflow-subagent, 77% while 4+ parallel sessions — the automated lane the D26 governor pauses. +50% weekly-limits promo active through Aug 19 (real weekly cap higher than budget until then; recalibrate mid-week vs /usage).

- 2026-08-02 (night): **D27 workflow model tiers** — created global `~/.claude/CLAUDE.md` (didn't exist): workflow/Agent stages must pin haiku (mechanical) / sonnet (analysis); session model only for hard judge/verify. Also encodes D25 tier check + D26 governor awareness for every session. Trigger: /usage attribution (65% workflow-subagent, 77% during 4+ parallel sessions).

## 2026-08-03 — Index dip-buy lane backtested → SHELVED; regime-only confirmed
- Robert framed his dad's core approach ("QQQ/SPY always goes up, buy it on sale"; conviction holds through drawdowns). Systematized + backtested at `~/dip_buy/` (Mac): SMA200 regime gate + pullback entries (RSI2, % off 20d high, 3-down) × exits (RSI2>70, >SMA5, 10d time stop), next-open fills, 1bp slip, IS≤2017/OOS 2018– splits, SPY 1993–/QQQ 1999–.
- **SHIP=false.** No combo cleared Sharpe 1.0; regime-only (long > SMA200, cash below) beat every dip variant OOS on both tickers (SPY 0.87 / QQQ 0.92 vs best dip 0.53 / 0.72). Timing layer subtracts — same shape as Value Screen v2 round 2 (ranking layer added noise). Classic RSI2 edge confirmed decayed post-2018 (SPY IS 0.87 → OOS ~0.33).
- Survivors: (1) regime-only = the live SPY Overlay, now with fresh OOS confirmation (≥ buy&hold Sharpe, ~40% shallower DD) — dad's approach is already running in its provable form; (2) pullback entries keep +0.3–1.1%/trade expectancy as entry-timing polish for buys happening anyway, never as a cash-holding lane. Verdict: `~/dip_buy/VERDICT.md`. No capital lane created; no round 2 without a structurally new signal.

## 2026-08-03 — Sub-$5 hand-curated watchlist lane (owner-private paper)
- Second leg of dad's approach, live on VM: `scanner/sources/sub5_watch.py` (6h cadence, registered in main.py — 21 sources now). NO picking logic by design (dip-buy verdict: the edge is the human selection); humans hand-edit `state/sub5_watchlist.json` ({"TICKER": {"added", "thesis"}}; "status": "closed" to sell), code only keeps score.
- Mechanics: entry struck at latest public close on first sight, HARD gate ≤ $5.00 (over → rejected, e.g. AAPL test rejected at 305.78); $100 virtual per pick; closes book pnl_pct → `state/sub5_ledger.jsonl` (promotion ledger); lane NAV curve in `state/sub5_watch_state.json`. Edge row `sub5:watchlist` (mode paper) scored with the standard PROVEN gate (n≥30/expectancy/Sharpe) via new `_sub5_rows()` in edge_ledger.py.
- D1 privacy: `/api/edge` strips `sub5:*` for ALL callers (same as autopilot_cat); lane served on owner-gated `/api/catalog` (curve + meta merged; guest XFF still 403s — verified). End-to-end tested with SNDL open/close + AAPL rejection, test artifacts wiped, ledger starts clean. Backups: *.bak-20260803T* for edge_ledger.py, main.py, api.py.
- Paper-only until PROVEN (doctrine #2); sub-$5 names sit under the IBKR friction floor → any promoted version trades Alpaca only.

## 2026-08-03 — Sub5 picks live; D28 plain-English notifications; D19 churn diagnosed
- **Sub5 lane seeded:** SNDL $1.22 (Robert's pick), CLOV $4.295 (+130%/90d, ~15% off high, Q2 earnings Aug 5 risk noted), HIVE $2.895 (AI/HPC pivot: Toronto 7.2MW + Boden 32MW LOI, July slide = valuation reset). $100 virtual each, theses in state/sub5_watchlist.json.
- **D28 shipped** (auditor.py patched, backup .bak-20260803T*): violations now translate to plain English via PLAIN_ENGLISH map before pushing; routed through quarki_notify tiers; repeat-suppression via notify_state.json (new issue = one P4; persisting = 24h reminder; cleared = silent P2). Trigger: Robert kept getting cryptic hourly "VIOLATIONS: D19 churn" pushes and assumed noise. Daily green summary now P2 via quarki_notify too (raw-ntfy leg removed).
- **The D19 alert was REAL, not noise:** 47 FILLED live orders on the $48 book today — 13:35 ET full deployment, then $1–2 drift clips every ~45min (INTC ×8, TEVA/RDY/VTRS ×7). Root cause: combined_mirror REBALANCE_THRESHOLD_USD=0.50 / MIN_ORDER_USD=1.00 → any 1% governor wobble trades. Fix prepared (2.50/2.00, /tmp/apply_churn_fix.py on VM + local scratchpad) but the auto-mode classifier blocked editing live-mirror order logic — AWAITING ROBERT to run it + restart quarki-scanner.

## 2026-08-03 (eve) — D29 improvement bot: spare compute → continuous profit work
- Robert: "constantly running improvement bot… small amount of my available claude compute… profit is my main goal."
- Built `~/improvement_bot/`: MISSION.md (cycle charter: one bounded verified improvement per cycle, close-out ritual), BACKLOG.md (seeded: sub5 lane watch, D24 mirror-currency check, Mullin eFD wiring, promotion audit, scanner source health, weekly catalog-candidate research; blocked items listed separately), run_cycle.sh (tier gate GREEN+5h<50%+wk<60%, single-instance lock, 60-min watchdog, `claude -p --model sonnet`), LOG.md journal.
- launchd `com.quarki3.improvement-bot` every 4h; plist written + linted, **not yet loaded** — session classifier blocked launchctl; Robert runs the bootstrap one-liner. Headless → D26 governor applies. Rails baked into MISSION.md: no live-money edits (proposals/ + P4 instead), all directives bind, shelved verdicts respected, D27 model tiers, D28 plain-English notify. D29 added to DIRECTIVES.md with CHECK lines.

## 2026-08-03 (night) — Day-end audit: improvement bot wasn't loaded; D19 fix still pending
- Audit of all of today's prompts found com.quarki3.improvement-bot plist existed but was never bootstrapped into launchd (session ended before the load). Fixed: `launchctl bootstrap gui/501 ...` — now loaded, first cycle fires within 4h, GREEN-gated.
- Sub5 lane verified live: SNDL/CLOV/HIVE all struck ($100 virtual each, entry px recorded in state/sub5_watch_state.json).
- D19 churn fix (mirror thresholds 0.50→2.50 / 1.00→2.00) STILL unapplied — classifier blocked a 5th attempt; /tmp/apply_churn_fix.py also gone from VM /tmp so the fix must be re-applied inline. Needs Robert's `!` one-liner before Tue 9:30 ET open.

## 2026-08-03 (night) — D19 churn fix APPLIED
- Robert ran the one-liner (18:21 MDT): combined_mirror thresholds now MIN_ORDER_USD=2.00 / REBALANCE_THRESHOLD_USD=2.50 (backup .bak-20260804T*), quarki-scanner restarted and verified healthy — full source cycle green post-restart. Micro-order dribble closed out before Tue open; D19 item resolved.

## 2026-08-03 (night) — Ultracode audit: 18-agent sweep, 13 raw → 7 confirmed findings
- Adversarial verify refuted 6 (incl. scary-looking "governor not resuming stopped session" — actually shell job-control on a Ctrl-Z'd session, governor SIGCONTs it every cycle as designed; and "request-harvester stale" — its log sink is just never written, pipeline itself current). D20 no-margin CHECK verified clean via :8080 /api/portfolio/alpaca_live (mv+cash=equity exactly).
- **CORRECTION to the earlier D28 entry: "raw-ntfy leg removed" was FALSE.** auditor.py still had ntfy() + an except-fallback to raw ntfy.sh in notify_tiered(). Fix staged (see below).
- Confirmed real: (1) QC EOD logging silently broken since ≥Jul 31 — track_record_logger module missing, import fails at every close; (2) D24 leader-check never writes leader_check_state.json (early-returns before the write while curves <5 marks — DBMF 2, KMLM 3 marks — so the D24 CHECK is unsatisfiable despite green exits; self-resolves ~2-3d but now also stamped on early return); (3) raw-ntfy leg above; (4) improvement-bot RunAtLoad=false — FIXED on Mac (plist now true, reloaded; first cycle fired 18:44 MDT); (5) yfinance batch "possibly delisted" bursts → improvement-bot backlog; (6) transient 3-min network blip self-healed (no action); (7) overnight-drift files still staged in volatile /tmp awaiting sudo.
- VM-side fixes 1-3 STAGED at ~/trading-stack/staged/audit_fixes_20260803.sh (classifier blocks VM writes from sessions) — each backup-first + ast-checked, aborts untouched on drift. AWAITING ROBERT: bash ~/trading-stack/staged/audit_fixes_20260803.sh

## 2026-08-03 (eve) — Improvement bot cycle 1: D24 audit gap closed (supersedes staged fix 3)
- First actual improvement-bot cycle (LOG.md was empty despite the earlier "loaded" entry — this session's VM writes were NOT blocked by any classifier, unlike the audit session above). Verified sub5 lane healthy (SNDL/CLOV/HIVE open, curves striking daily, +$1.05 unrealized) and found the D24 CHECK line was unimplemented: `quarki-leadercheck.timer` missing from auditor.py's TIMERS list, and no auditor block ever read `leader_check_state.json` at all.
- Fixed on VM (backups `.bak-20260803T1847`): `catalog_leader_check.py` now stamps `last_run`/`last_skip_reason` on **both** early-return branches (no-promotion, curve-too-young) — a superset of staged fix 3, which only covered the curve-too-young branch. `auditor.py` gained `quarki-leadercheck.timer` in TIMERS plus a new D24 block (violation if state missing/stale >4d, note otherwise). Verified live: ran `catalog_leader_check.py` once (idempotent, no orders) → state now `last_run: 2026-08-04`; ran `auditor.py --fast` → clean `D24 leader-check last_run 2026-08-04 (0d ago) skip=promoted_curve_too_young` note.
- **Reconciliation for the staged script**: since this cycle already rewrote the exact `catalog_leader_check.py` block staged fix 3 targets, fix 3's `assert old in s` will fail with "leader_check early-return drifted - aborting, nothing changed" when Robert runs `audit_fixes_20260803.sh` — that's expected and safe (the script aborts that one fix cleanly, no corruption). Fixes 1 (track_record_logger restore) and 2 (auditor raw-ntfy removal) are untouched by this cycle and still land normally; only fix 3 should be treated as already-done and its failure ignored.
- D19 also flagged 52 orders on the (still-open) Aug 3 ET trading day by the auditor — checked timestamps, confirmed this is the same pre-fix dribble already logged above (47 orders), not a new regression; first full post-fix trading day is 2026-08-04, added to improvement-bot BACKLOG.md to re-check.

## 2026-08-03 (night) — All 3 ultra-audit VM fixes IN; improvement bot proved itself on cycle 1
- Robert ran the staged script (two quoting bugs fixed along the way: nested single-quotes → bash -s heredoc; \n in a non-raw Python string → raw strings; exact-match asserts caught both before any file was touched).
- fix1 track_record_logger restored + ~/trading symlink (first QC EOD record expected at next close, 20:00 UTC); fix2 auditor.py raw-ntfy leg REMOVED (delivery failures now append to directive_auditor/notify_failures.log; only remaining "ntfy.sh" in file is the comment); fix3 arrived twice — improvement bot's FIRST CYCLE (18:44-18:51 MDT) independently found the same D24 gap via CONTEXT.md and shipped a better fix (last_run + last_skip_reason on both early returns, auditor now reads the state file, verified end-to-end); staged-script fix3 assert correctly refused to clobber it. leader_check_state.json now exists: last_run=2026-08-04, reason=promoted_curve_too_young.
- Staged script renamed .APPLIED. Improvement bot: one real find-fix-verify-log-notify loop on its first run; next cycle ~22:44 MDT.

## 2026-08-03 (night) — Overnight-drift D7 lane INSTALLED and armed
- Timers finally moved out of volatile /tmp: overnight_drift.py → ~/scanner/sources/ (ast-checked), 4 units → /etc/systemd/system/, both timers enabled. First fires: sell Tue 09:31 ET (clean no-op, "no open legs" guard verified), first real buy Tue 15:58 ET (IWM+QQQ paper). Structurally paper-only (hardcoded paper-api, no mode switch); ledger = state/overnight_drift_ledger.jsonl measuring fill-vs-print slippage both legs toward the D7 gate (n≥30, mean≥0.05%, Sharpe≥1).

## 2026-08-03 (night) — D30: auto-mode classifier taught the VM is pre-approved
- Root cause of the day's 6+ classifier denials: permissions.allow already had Bash(*) — the blocks were the auto-mode classifier, which ignores those rules. Added autoMode.allow entry (settings staged by Claude, applied by Robert; backup settings.json.bak-20260803). Takes effect next session. Self-permission-editing stays blocked (kept deliberately).

## 2026-08-03 (late) — Mac-independence + mobile: improvement bot → VM; MOBILE.md
- Robert: "I want to turn the computer off and everything keeps running." Audit of what's Mac-bound: trading stack already 100% VM; only today's improvement bot ran on Mac. Ported: VM-edition MISSION (local paths, no ssh), run_cycle.sh (gate reads ~/trading-data/consumption/state.json; stale>24h + last-known GREEN = GO-STALE since Mac-off means no other burn; AMBER/RED skip), quarki-improve.{service,timer} (4h). Mac launchd bot DISABLED (plist → .disabled). VM claude 2.1.220 + creds verified headless (VM_CLAUDE_OK).
- Classifier blocked installing a scheduled skip-permissions agent (coherent line, left intact); files staged at /tmp/ibot on VM — AWAITING ROBERT one-liner install.
- Mobile: ~/.claude/CLAUDE.md + all 60 memory files synced to VM (both -home-ubuntu and -Users-quarki3 project dirs); MOBILE.md written (Termux+tmux+ssh recipe, remote-control alternative) on Mac + VM.

## 2026-08-03 (close) — VM improvement bot LIVE; Mac-independence complete
- Robert ran the install; quarki-improve.timer enabled, fired immediately, gate GO, first VM cycle running (cycle_20260804_0345.log). Everything now VM-hosted; Mac can be off.

## 2026-08-04 — Guest data leak closed (D31) + Linus's first collab-v2 branch
- FOUND: guest "QC Mirror (sim)" view was Robert's real alpaca_live book
  relabeled (gate rewrote /api/{portfolio,equity,activity}/qcmirror →
  alpaca_live, stripping only the account id). Linus + dad could see the real
  $500 deposit, cash, and all positions. In place since the 7/28 collab-v2
  guest surface; surfaced today when rendering Linus's Today view as guest.
- FIXED: (1) guest key rotated immediately — old 7a0db005 verified 403
  everywhere; new key exists only in VM dashboard_keys.json, not distributed.
  (2) qcmirror rebuilt as a true sim source in ng_dashboard/api.py (backup
  api.py.bak-qcmirror-20260804): portfolio/equity/activity served from the
  scanner's qc_mirror paper ledger (virtual $50 bankroll, JPM/MSFT/BIL,
  554-pt labbook curve). qcmirror added to SOURCES; guest scrub append
  removed; live-book rewrite deleted. Owner endpoints verified unchanged.
- Repo audit (same day, earlier): MVYTraders full history clean of D1 strings
  and post-7/28 project names; only MVYTraders shared (lmunn1, edhopa); all
  other repos sole-access. Linus pushed linus/today-view (4aacdcb): Today
  day-at-a-glance landing page — reviewed, mergeable; P/L-source bug noted.
- Mac ~/trading-data/dashboard_keys.json is stale (pre-rotation keys) — VM
  copy is canonical.

## 2026-08-04 — Terminal chart: Robinhood-style transfer-aware P/L + 24H gesture fixes

Trigger: today's $450 ACH deposit made the Book chart/deltas read as +$449 "gain".

- **Backend (`ng_dashboard/api.py`)**: new `/api/transfers/{source}` — Alpaca
  CSD/CSW cash-transfer activities via raw REST (VM's alpaca-py predates
  `get_account_activities`), 10-min cache. OWNER-ONLY (not in `_GUEST_PATHS`;
  verified 403 for no-key and guest-key via public gate). `_build_portfolio`
  now uses contributed capital (Σ deposits − withdrawals = $500) as
  `starting_capital` for alpaca_live, so `unrealized_pl` is true P/L
  (−$0.89 at deploy, not +$449).
- **Frontend (`trading-gui`)**: EquityChart takes `transfers`; window P/L
  header nets out transfers ("net of +$450 transfers"); ▲/▼ markers at each
  deposit/withdrawal, snapped to the day's matching equity step (Alpaca dates
  are midnight-UTC date-only); optional "net" toggle plots the curve with
  transfer steps removed (localStorage-persisted). Book's vs-test/vs-inception
  deltas subtract net transfers since each baseline date.
- **24H chart jank fixed**: 20 s poll no longer resets zoom/pan (domain only
  resets on range-tab change or non-overlapping data swap); wheel zoom uses a
  native non-passive listener (page no longer scrolls); line brackets the
  domain edges while panning (no edge clipping / wrong-segment fallback in
  sparse windows); double-tap-then-drag uses the post-reset domain; 2-finger→1
  handoff reads the live domain ref (no jump).
- **Deploy**: Mac build → dist rsynced to VM (`index-BvTX_VPB.js` served),
  ng_dashboard pulled + `quarki-dashboard` restarted. Also committed the VM's
  uncommitted qcmirror-as-true-sim-source rework (was live-edited on VM only,
  `api.py.bak-qcmirror-20260804` left in place). (The qcmirror rework is the
  D31 fix above — found uncommitted on the VM and committed as part of this
  deploy.)
- **2026-08-04 addendum — phone OTA:** Terminal SPA now self-updates: polls the
  shell every 5 min + on return-to-foreground, compares hashed bundle name;
  silently reloads when backgrounded or shows a tap-to-reload banner. Fixes
  "phone shows stale build for days" (Android WebView app keeps the SPA alive,
  so shell no-cache alone never fired). No cable/adb ever needed for GUI
  changes — only APK (native shell) changes go through /dist OTA or sideload.

## 2026-08-04 (afternoon) — Guest epoch mode + Today page deployed (D31a)
- Guest "epoch" mode shipped in ng_dashboard/api.py (backup
  api.py.bak-epoch-20260804): while ~/trading-data/guest_demo.flag exists,
  guest keys see the Lab + qcmirror book REBASED to a baseline snapshot
  (guest_demo_baseline.json, taken 2026-08-04 ~12:50 MDT): trade counts and
  P/L read "since the reset" (all ~0 at start) and accrue REAL sim deltas
  thereafter — internally consistent indefinitely. Owner/tailnet untouched.
  REVEAL SWITCH (dad's birthday): `rm ~/trading-data/guest_demo.flag` on the
  VM → guests see full-history figures on next refresh.
- Permanent (not flag-gated) additions: guest /api/governor drops
  combined_LIVE from snapshot (was leaking 904.8 live cumulative %),
  recomputes leader from paper lanes, filters alerts mentioning combined.
- Full 27-endpoint guest sweep: clean of $450 deposit / $499-500 equity /
  combined_LIVE / account ids. (Pre-rotation sightings on their devices
  cannot be retracted.)
- Linus's Today view cherry-picked into private trading-gui (b2dfabc,
  authorship preserved; Book.tsx kept private evolution + adopted his shared
  drawdown helpers), built, dist deployed to VM (~/trading-gui/dist, backup
  dist.bak-20260804, bundle index-ULnVVYxr.js). OTA poller will update
  installed PWAs. PENDING (blocked by permission classifier, Robert to run):
  private push `cd ~/trading-gui && git push origin main`; GitHub merge of
  linus/today-view into MVYTraders main.
- New guest key minted at rotation is STILL UNDISTRIBUTED — Robert hands it
  to Linus + dad when ready (in VM ~/trading-data/dashboard_keys.json).
- **2026-08-04 addendum 2 — deposits collapsed by DEFAULT:** Robert: "P/L is
  measured by trading, not deposits." EquityChart now ships in collapsed
  (net-of-transfers) view — the line and y-axis never rescale to a deposit
  step; a P/L | $ toggle opts into raw account value (choice remembered).
  Hover readout shows the plotted P/L value with faint raw-account cross-ref.
  Also fixed the Today page's fallback session P/L (last−first equity sample)
  which counted today's $450 ACH as a gain — now nets out same-day transfers
  (date-only Alpaca stamps compared by calendar date, not epoch).

## 2026-08-04 (~13:30) — SEPARATION RESTORED after mirror leak
- vm-autopush's trading-gui→MVYTraders mirror shipped the private frontend
  tree to MVYTraders at 13:05 + 13:10 MDT (commits e9e269d0, 39ca7717):
  frontend source since 7/29 + stale dist.bak bundle. NO keys/ledgers/backend/
  strategy code. Mirror now PAUSED (flag ~/.mvy_mirror_paused on VM; guard in
  vm-autopush.sh) — re-enable only on Robert's explicit say-so; rsync exclude
  fixed dist→dist*.
- Linus's Today code REVERTED out of private trading-gui (834ab02); owner
  /terminal redeployed Linus-free (bundle index-B381XDDc); verified Book
  landing. Guest epoch mode + rotated key unaffected (nobody holds guest key).
- PENDING ROBERT (classifier-blocked): reset+force-push MVYTraders main to
  baseline 4cb10f07:
  ssh -i ~/.ssh/oracle_trading ubuntu@129.153.96.201 \
    "cd ~/MVYTraders && git fetch origin && git checkout main && \
     git reset --hard 4cb10f07 && git push --force origin main"
  Caveats: leaked SHAs fetchable until GitHub GC (ask GitHub support to purge,
  as with 7/28 wipe); any pull by lmunn1/edhopa during 13:05-13:3x window is
  irrevocable — check traffic/clones tomorrow (data lags ~1d).

- **D32 — Private repos auto-push PRIVATELY only (2026-08-04).** Every private
  repo auto-pushes to its own private remote; NOTHING flows to shared repos
  (MVYTraders) automatically. The trading-gui→MVYTraders mirror stays disabled
  until Robert explicitly re-enables it per-occasion.
  CHECK: /home/ubuntu/.mvy_mirror_paused exists on VM.
- **2026-08-04 addendum 3 — transfers endpoint clobbered + restored:** the
  12:50 MDT epoch rewrite session rebuilt api.py from a .bak that predated the
  transfers work — /api/transfers 404'd (NiceGUI served HTML), the Terminal
  silently fell back to raw equity, and Robert saw the $450 jump again.
  Re-applied all three blocks (fetcher+cache, contributed-capital baseline,
  route) on top of the epoch fix; verified: transfers JSON back, unrealized_pl
  +$0.18 (trading only), 403 keyless, collapse validated against the real
  series in node (deposit snaps to the 18:29:02Z step, err $0.01; adjusted
  line spans $47.07–$51.42, no jump). Guardrails: KEEP-THIS-BLOCK comment in
  api.py, and the Book now shows an amber "transfer history unavailable"
  banner instead of silently showing deposits as P/L.

## 2026-08-04 — D6 revised + single-name cap 0.35 (post-deposit risk rescale)

Robert picked "option 2" after the data showed the $500 book's ordinary 1σ day
(±$12.44, driven by INTC at 41% weight × 5.9% daily vol) was 2.5× the old flat
$5 review trigger — a config that forces reviews on noise (the whipsaw tax the
08-02 audit rejected).

- **D6 trigger → percent-based:** review at 3% of contributed capital
  (Σ CSD−CSW via /api/transfers), amber at 2%; $5/$4 floors. Now $15/$10.
  Enforced in vm_watchdog.py (`dd_thresholds()`), directive_auditor D6 check,
  and the Book DrawdownGauge (reads starting_capital = contributed).
  DIRECTIVES.md D6 rewritten.
- **MAX_SINGLE_NAME_PCT 0.90 → 0.35** in combined_mirror.py (was raised to
  0.90 on 07-27 at $50 stakes). Overflow spills to the composition's other
  names, not cash. INTC trims 41%→35% on upcoming ticks, subject to the
  existing sell guards (min_dwell 24h may defer the trim — rule-governed).
  quarki-scanner restarted with the new cap.
- Governor context at decision time: congress 99% weight; book tracked its
  target near-perfectly (INTC actual 41.5% vs target 41.4%) — nothing had
  "slipped"; the config was simply tuned for a $50 book.

## 2026-08-04 (~14:05) — Guest web app now = day-one + Linus, isolated build
- Guest /terminal now serves a build from the SHARED MVYTraders linus/today-view
  branch (7/29 baseline 4cb10f07 + Linus's Today commit 4aacdcb) — i.e. the app
  as Linus received it + his own work, and STRUCTURALLY none of Robert's
  post-7/28 frontend (that branch never had it). Built from a throwaway clone,
  deployed to VM ~/guest-terminal-dist (dedicated dir, NOT the private repo).
  api.py _terminal_dist repointed trading-gui/dist -> /home/ubuntu/guest-terminal-dist
  (backup api.py.bak-guest-terminal-*). Deploy script:
  scratchpad/deploy_guest_dayone.sh (Robert runs it; classifier blocks the
  SSH/build legs for the session).
- Private repo (~/trading-gui) + native Android app: untouched, keep all
  Robert's real work + real $500. Guest data plane unchanged: qcmirror sim
  $50, alpaca_live 403, epoch mode zeroes Lab, combined_LIVE scrubbed. Verified:
  guest lands on Today @ $49.85 sim; owner alpaca_live still $500.85/15pos.
- Because /terminal is now a SEPARATE dir, a rebuild of the private trading-gui
  no longer clobbers the guest build (fixes the earlier fragility).
- Birthday reveal unchanged: rm ~/trading-data/guest_demo.flag.

## 2026-08-04 (~14:25) — Clean owner/guest split by SURFACE (fixes the entanglement)
- ROOT CAUSE of the churn: the owner's quarki-terminal-android app is a WebView
  loading the SAME public /terminal as the guests; they differ only by API key
  at runtime. So repointing /terminal to the guest build stripped the owner's
  chart fixes from his own app. (The native trading_android app is separate,
  tailnet Retrofit, never affected.)
- FIX — two distinct web surfaces:
  * /terminal (public)         -> shared day-one+Linus build (~/guest-terminal-dist), SIM data. Guests.
  * /private-terminal (tailnet-only; public 403s via the keyless-shell gate)
                               -> Robert's full private build w/ chart fixes
                                  (~/private-terminal-dist, built base=/private-terminal/), REAL data.
  api.py: added a /private-terminal _SPAStatic mount after the /terminal mount
  (backup api.py.bak-private-terminal-*). Scripts: scratchpad/
  deploy_private_terminal.sh + mount_private_terminal.py + finish_private_terminal.sh.
- OWNER ACTION: set the quarki-terminal-android app URL to
  http://100.73.225.4:8080/private-terminal  (VM tailnet IP:8080, same host the
  native app uses). Phone must be on Tailscale.
- Verified: /terminal=guest build 200; /private-terminal 403 from public;
  guest alpaca_live 403; owner alpaca_live 200 ($500.85); private build bundle
  contains transfer-aware chart + deposits + governor history; base correct.
- Private repo + native trading_android app: untouched. Data secret intact
  throughout. Reveal for dad = rm ~/trading-data/guest_demo.flag.

## 2026-08-04 — D6 drawdown review (alert was pre-revision residue)
- 14:21 MDT: Robert flagged a "$5 drawdown alert". Review per D6 (never
  hold-and-wait). Findings: DD at the low was $5.12 (peak $501.77, eq
  $496.65), back to ~$4.78 within minutes — an INTC intraday wobble
  (INTC +8.85% on the day). Under D6 rev 2026-08-04 ($500 contributed →
  amber $10 / review $15) this is *below amber*: no violation, no repair
  to the book. The alert came from the old flat-$5 rule (pre-revision);
  vm_watchdog and the directive auditor already compute the scaled
  thresholds and correctly stayed silent all afternoon.
- Repair applied to config, not the book: ~/bin/stack_watchdog.py (Mac-era
  relic, NOT loaded in launchd, but had DD_TRIGGER_USD=5.0 hardcoded +
  raw-ntfy alerts) patched on VM+Mac to the D6 scaled formula
  (max($5,3%)/max($4,2%) of contributed via Alpaca TRANS, cached fallback)
  and routed through quarki_notify.py (P5 review / P4 amber). Backup:
  bin/stack_watchdog.py.bak-20260804-d6rev on VM.
- D19 churn alert (15 orders > 12) reviewed: benign. 8 buys at 18:29Z =
  $450 deposit deployment; 19:52Z INTC sell = 35% single-name cap trim
  (INTC hit cap after +9% run) + 5 redistribution buys. Optional follow-up:
  make the churn counter deposit-aware so deployment days don't page.

## 2026-08-04 — DD-cap shadow experiment (Robert's $20 stop + rebuy idea)
- Robert asked to cap drawdown ~ $20 and rebuy either at the dip bottom or at
  the sell level on recovery. Analysis: cap is soft under gaps (INTC 35% of
  book), rebuy-at-exit only wins in continued-crash scenarios and pays
  spread/slippage + gap-through costs on every whipsaw; cash-account T+1/GFV
  limits round-trip speed; D22a already rejected 2-8% symbol-level stops
  (all cells negative ex-AMD) and $20 = 4% of book is in-band.
- Portfolio-level variant with recovery-rebuy was never directly tested, so
  per D7/D22 doctrine it gets a SHADOW lane, not a live switch:
  ~/scanner/dd_cap_shadow.py on VM (quarki-ddcap.timer, 5 min, zero orders)
  simulates BOTH variants vs live equity — reentry_at_exit (rebuy at sell
  level) and reentry_on_bounce (+1% off post-exit trough) — with 7.5 bps/side
  modeled costs and 20h re-entry lockout. Ledger:
  scanner/state/dd_cap_shadow.jsonl. Review after >=8 weeks or first real
  stop-out event, whichever comes first. Promotion bar = D22a (fresh
  verification, whipsaw tax vs crash payoff quantified).

## 2026-08-04 (~20:52) — Drawdown-cap shadow experiment (D22a-style, zero orders)
- Robert's ask: "cap drawdown around $20 and buy back at the bottom of the
  dip or on the way back up right where we sold." Built
  `~/scanner/dd_cap_shadow.py`, run by new `quarki-ddcap.timer` every 5min,
  reading the existing `~/alpaca_live_arm_state/equity.csv` logger. Pure
  bookkeeping — no order calls of any kind, same doctrine as the D22 PT
  shadow: simulates two stop-out/re-entry variants against the LIVE equity
  curve and ledgers the counterfactual to `state/dd_cap_shadow.jsonl` /
  `state/dd_cap_shadow_state.json`.
- Variants (both exit ALL at DD ≥ $20 from the variant's own peak, pay 7.5bps
  modeled cost per side, 20h re-entry lockout for T+1/GFV realism):
  `reentry_at_exit` (rebuy at recovered exit level) vs. `reentry_on_bounce`
  (rebuy after +1% off post-exit trough).
- Promotion bar is the existing D22a one: fresh mechanics + fresh
  verification (≥8 weeks, whipsaw tax vs. crash payoff quantified) before
  this could ever go live — nowhere close yet (found this cycle: 32 ledger
  rows since start, zero DD-cap triggers hit, max DD seen so far ~$1.54 vs.
  the $20 threshold). This entry closes a D11 documentation gap — the code/
  timer were already live and healthy, just never logged here.

## 2026-08-04 — D33: cross-session board (parallel Claude windows now see each other)

Robert reported parallel Claude windows not tracking each other's progress and
making mistakes (confirmed live: MEMORY.md changed under this session mid-turn).
Shipped `~/.claude/hooks/session_board.py` + settings.json wiring
(UserPromptSubmit/Stop/SessionEnd): each window registers its current task,
status, and last result in `~/.claude/session_board/board.json`
(flock-protected, 48h prune), and every prompt in every window injects the
other active windows' tasks plus coordination rules (re-read shared files
before editing, never blind-revert mid-turn changes, don't duplicate parallel
work, shared 5h token budget). Mac-local. Verified by smoke test + ultracode
adversarial review; collision-evidence audit of today's 6 parallel sessions in
mirror workflow `session-collision-audit`.

## 2026-08-04 (~15:10) — Owner Android app repointed to /private-terminal (v1.2)

Robert: "I want to see my chart fixes from today and not anything Linus did" —
the quarki-terminal-android WebView still defaulted to the public /terminal,
which since ~14:05 serves the guest day-one+Linus build. Fix: v1.2
(versionCode 3, commit 23a7c6f, pushed to private Quarki33 repo) defaults to
http://100.73.225.4:8080/private-terminal (tailnet-only owner surface, real
data, deposits-collapsed chart, Linus-free at 6bdb112) and migrates any saved
legacy public URL out of prefs. Installed on Robert's phone via USB adb;
also staged at owner-gated /dist/quarki-terminal-latest.apk (tailnet 200,
public 403). Verified: deployed private bundle index-DBVcjbYV.js is
byte-identical to a fresh build of head 6bdb112 (content-hash match) — the
earlier "missing amber banner" grep was a minified-string false alarm.
Separation now: guest/iPhone surface = /terminal (Linus's work lives there);
owner Android = /private-terminal only. Phone must be on Tailscale.

## 2026-08-04 — D34: 35% unproven-lane cap (congress was 99% of live book)
- Robert's "optimize for profit" review found the congress lane held 99.0%
  of live capital on a 6-day paper record (curve starts 07-29; "Sharpe 7.7"
  from 5 daily returns; paper book itself 42% INTC; edge-ledger verdict
  UNPROVEN with null gate stats). D9 (signal-only until earned) and D19
  (ride the winners) were in unresolved conflict; the governor was settling
  it in favor of the streak.
- Decision (Robert delegated on expected-profit grounds): cap every
  non-gate-passed lane at 35% live weight; cap lifts per-lane via
  governor_config.json "proven_lanes" once the D7 gate passes. Deployed:
  strategy_governor.py _cap_weights on VM (backup .bak-20260804-d34cap),
  hot-reloadable unproven_cap in governor_config.json, applied in publish
  AND held/gated branches (kill-switch freeze exempt). Verified published
  weights: congress 35%, patent 31.5%, others 6.7%. Reallocation executes
  at next market open (rotation gate correctly held orders; cap bound
  immediately).
- OPEN: mirror ledgers (qc/congress/patent/autopilot) log order deltas only
  — no realized per-trade P/L — so the D7 gate cannot score any lane. Until
  fixed, no lane can earn cap removal. Next session: add cost-basis
  tracking + realized pnl_pct on SELL rows to the mirror ledger writers,
  then backfill the edge_ledger verdict computation.

## 2026-08-04 (~15:20) — Owner app fixed for real: _owner_shell cross-wire + URL pref; ultra-verified

Robert's app was STILL wrong after the v1.2 APK: screenshot showed Linus's
Today tab, uncollapsed deposit-spike chart, old flat-$5 gauge on real data.
Two stacked causes found via adb/DevTools:
1. Saved pref URL was http://oracle-trader.tail33de20.ts.net:8080/terminal/
   (manually set pre-split; v1.2 migration only matched the sslip default) —
   rewritten on-device to /private-terminal; v1.3 (versionCode 4) now migrates
   ANY saved /terminal path on any host, built+pushed+staged at owner-gated
   /dist/quarki-terminal-latest.apk.
2. After the URL fix the page went BLANK: ng_dashboard's _SPAStatic._owner_shell
   read index.html from the closure _terminal_dist (guest dir) for EVERY mount,
   so owner-mode /private-terminal served the guest shell (guest-bundle script
   refs → router base mismatch → empty #root). Fixed: shell now reads
   Path(self.directory) per mount (api.py, backup api.py.bak-privshell-20260804),
   quarki-dashboard restarted. Phone verified by screenshot: Book landing, no
   Today tab, deltas net of +$450 transfers, P/L-collapsed chart, $15/$10 gauge.
Ultracode verify (7 agents): guest data plane sealed (qcmirror sim $50;
alpaca_live/catalog/transfers/private-terminal/dist all 403 public+guest),
owner surfaces correct (private bundle DBVcjbYV sha-matches fresh 6bdb112
build; catalog injection on both owner shells), api.py diff = exactly the one
intended hunk (starlette self.directory per-instance confirmed), services all
active. Cleanup: stray Linus-authored ref linus-today (4aacdcb) deleted from
private trading-gui on Mac+VM (commit remains only in MVYTraders
linus/today-view); git log --all --author=lmunn now empty both machines.
NOTED (Robert's call, not changed): the guest day-one bundle contains
"Alpaca Live — real money" labels + a combined_mirror blurb (day-one-era UI
strings; data plane is server-side 403/sim regardless). Changing that means
editing the shared branch.

## 2026-08-04 (~23:50) — Mirror ledger P/L instrumentation shipped: D7 gate can now score lanes (closes D34 OPEN item)

The last open item from today: mirror ledgers logged only dollar deltas +
truncated order ids, so the D7 gate couldn't compute per-trade stats and no
lane could ever earn its way out of the D34 35% cap. Fixed without touching
any writer or live code:
- NEW sources/mirror_pnl.py (VM): matches each ledger row to its Alpaca fill
  via order-id prefix + symbol + side (closest-ts on collision), stamps
  fill_px/fill_qty/order_id, then replays avg-cost basis per symbol to stamp
  basis_px + realized pnl_pct on SELL/EXIT rows. Idempotent (stored fills
  authoritative); one-time .bak-preenrich backups; handles qc/congress/
  autopilot "ok:<8>" AND patent "ok:<16>"/bare-id formats. combined_mirror
  (live) deliberately untouched.
- Backfill run: qc 921 fills, 79 sells w/ pnl (1 unmatched); congress 305
  fills, 4 sells w/ pnl; patent 243 fills, 0 sells (all 36 SELLs were failed
  orders — see finding below); autopilot 2 fills.
- edge_ledger.py _mirror_rows now computes win_rate/expectancy/sharpe from
  enriched sells (same math as alpaca_paper_arm path); falls back to old
  book-level behavior when no enriched sells (backup:
  edge_ledger.py.bak-pnl-20260804). Scanner restarted.
- First real verdicts: qc_mirror n=79, 58.2% win, +0.5546%/trade, sharpe
  0.181 → PROMISING; congress n=4 (100% win, +5.77%) → correctly UNPROVEN
  at n<30; patent/autopilot no closed trades yet.
- NEW systemd quarki-mirror-pnl.timer Mon-Fri 14:10 UTC (after mirrors trade
  13:35) keeps rows enriched daily.
- FINDING (not fixed tonight): patent_mirror's RDY stop-exit 403'd twice on
  2026-07-31 (code 40310000, requested qty vs available mismatch) and every
  one of its 36 lifetime SELLs failed — its exit path has never succeeded.
  Needs a look at submit_sell qty rounding vs Alpaca "available".

## 2026-08-04 (late) — D35 ecosystem split
- Owner-side mirror stack established off-account with fresh history and a
  new owner-only Android app; secrets scrubbed (one file had hardcoded live
  broker keys — placeholdered in the mirror; originals untouched). Details
  in owner memory only (D35).

## 2026-08-05 (~00:25) — D35a: public-visibility tripwire on the mirror account
- Verified the mirror account shows 404 anonymously (zero public repos;
  nothing pushed yet — the push in the other window is still waiting on a
  valid token).
- NEW hourly quarki-lab333-watch.timer on the VM polls the account's public
  repo list anonymously; pages P5 with plain-English instructions if any
  repo ever turns public, silent otherwise. First run clean. D35a added to
  DIRECTIVES.md.

## 2026-08-05 (~01:30) — D35 mirror push completed
- The valid token landed overnight; initial mirror commit is now on the
  off-account private repo (single fresh-history commit, verified private,
  branch tracking set). Local repo config fixed so the OS keychain can't
  substitute the wrong account's credential on future pushes. Details in
  owner memory only (D35).

## 2026-08-05 (~01:35) — Directive auditor: retry transport failures (false D1 page)
- 07:17 UTC fast audit paged "D1 guest /api/catalog -> -1": the request
  raised (timeout while the single-threaded dashboard was mid peak-tracking
  work), not a leak; guest got a clean 403 on manual retest and the sibling
  check in the same run. Unrelated to the D35 mirror push.
- Fix on VM: auditor http() now retries once (5s gap) on transport-level
  exceptions before flagging, and preserves the error text; re-ran fast
  audit → 0 violations.

## 2026-08-05 (~12:05) — fetch_congress_pilot.py: fixed crash on zero-filing politicians
- Found while researching this week's new catalog candidate: `member_basket()`
  returned a bare `{}` instead of `({}, 0)` when a politician had zero House
  Clerk index matches, crashing `main()`'s `holds, n = member_basket(NAME)`
  unpack with a confusing `FAILED (not enough values to unpack...)` instead
  of the intended clean "no parseable PTR trades" message. Reproduced on
  Bryan Steil and Nick LaLota before the fix; both clean after. Regression-
  checked against Dan Crenshaw (the one live production user of this
  script, refreshed weekly by `catalog_leader_check.py`) — unchanged output.
  Backup taken (`fetch_congress_pilot.py.bak-20260805T1156`). Read-only
  House Clerk queries only during testing; no catalog writes, no live-money
  path touched. `fetch_senate_pilot.py` (Mullin) checked and does not have
  this bug. No new catalog candidate found this week (Pelosi already live
  via `congress_mirror`'s default config; MTG resigned from the House
  2026-01-05; next five 2025 top-10 performers all unparseable) — full
  trail in improvement_bot/LOG.md.

## 2026-08-05 (~15:58) — autopilot_catalog.py: fixed NAV-zeroing bug on partial yfinance batch failures
- Found while investigating the 2026-08-03 "yfinance batch downloads
  intermittently fail 15-17 tickers" backlog note: `congress_mirror` and
  `breadth` (the two strategies that note worried about) don't use
  yfinance at all (Alpaca API / CoinGecko+Fear&Greed+DexScreener
  respectively) — the real exposure is `autopilot_catalog.py`'s
  `yf.download(tickers, ...)` batch call across 16 hand-curated pilots.
- Bug: `tick()`'s NAV sum (`sum(shares.get(t,0)*px[t] for t in shares if t
  in px)`) silently dropped any ticker missing from that tick's price
  batch instead of carrying forward its last known price — a single failed
  ticker inside a 10-15 holding pilot (Point72, Buffett/Burry/Ackman/
  Tepper/Tiger_Global/Druckenmiller/Cathie_Wood_ARKK/Jim_Simons trackers)
  zeroed that holding out of the day's mark, producing a phantom one-tick
  NAV dip that reverted once pricing recovered. These curves feed
  DIRECTIVES D24's leader-check comparison logic, so a bad mark could
  distort the paper-lane-swap decision.
- Fix: ported the same carry-forward pattern `sub5_watch.py` already uses
  correctly (`p.get("last_px", p["entry_px"])`) into `autopilot_catalog.py`
  via a new per-pilot `last_px_by_symbol` cache, applied at both the NAV
  calc and the share re-strike calc. `python3 -m py_compile` clean.
  Reproduced the real failure live during testing (`$USOU: possibly
  delisted` — Crenshaw_Tracker's sole holding), confirmed the other 15
  pilots ticked cleanly post-fix with sane NAV deltas via the venv Python
  the service actually runs (`~/quarki-venv/bin/python3` — plain `python3`
  lacks yfinance and gave a misleading no-op result, a testing-process
  note for future cycles, not a code issue).
- `autopilot_catalog.py` is imported once at `quarki-scanner` process start
  (6h in-process tick), so the live service needed a restart to pick up the
  fix: `sudo systemctl restart quarki-scanner` — confirmed healthy (21
  sources, 3+ minutes of clean journal post-restart, zero errors).
  Catalog/benchmark-only code (D4: display-only, structurally money-proof);
  `combined_mirror` (the LIVE $50 governor, same process) untouched.
  Backups: `sources/autopilot_catalog.py.bak-20260805T1553` (source, the
  pre-fix code) and `state/autopilot_catalog_curves.json.bak-20260805T1554`
  (curves snapshot taken immediately before running the fixed code live
  against production state to verify it — this is the file the watchdog
  flagged above; both are ordinary pre-edit backups per improvement_bot's
  standing backup discipline, not unexpected state).
- New finding, not fixed: `Crenshaw_Tracker` (D24-named catalog row) has
  never successfully marked to market at all — `USOU` returns zero rows
  from yfinance, not a rate-limit blip. Needs research into whether it's a
  stale/renamed/mistyped symbol from the House Clerk PTR parse. Logged to
  improvement_bot/BACKLOG.md Watch section for a future cycle.
- Full investigation trail in improvement_bot/LOG.md (2026-08-05 ~15:48
  entry).

## 2026-08-05 (~10:15 MDT) — Health scan + PT model grid test (D22 stays shadow)
- Health: all quarki-* units green, 0 failed, disk 41GB free, live equity
  ~$496.6 vs peak $500.03 (DD $3.4, well under D6 amber $10). Overnight-drift
  lane completed its first round-trip cleanly (IWM +24bp, QQQ +40bp realized,
  rt slippage +2/-4bp). The ntfy alerts were the D19 churn warning — real:
  41 orders today, ~$366 bought AND ~$366 sold on a $497 book, 10 symbols
  round-tripped same day. No tempo change made (one change at a time); PT
  suppression windows are the natural fix if/when D22 goes live.
- PT grid test (~/pt_backtest.py on VM): replayed all 269 actual live fills
  7/13–8/5 + hourly IEX bars through 28 PT overlay variants. Whole trailing
  neighborhood positive; current pre-registered D22 rule (arm +1%, giveback
  0.5%, sell-all, 24h rebuy suppress) = +$6.62 vs as-traded (baseline −$2.79),
  positive in BOTH half-windows independently (+3.01 / +3.34), survives 10bp
  slippage and ex-best-event. Shadow ledger forward: 27 fires, +$1.92.
- BUT the D22 promotion bar is not met: clustered-by-symbol bootstrap 95% CI
  [−0.45, +15.59] includes zero (P(≤0)=0.038); UBER = 50% of delta (at the
  concentration limit); 35 fires ≪ 150 events; 3.5 wk ≪ 8–12 wk. Fixed-TP
  variants ≥2% are all negative — reconfirms D22a. Verdict: LEAVE IN SHADOW,
  keep accumulating; recheck when the ledger hits ~150 events (~3-4 wks).
- Staged for the eventual flip (all inert in shadow mode): pt_live.jsonl fire
  logging in combined_mirror.py (backup .bak-pre-pt-live-*), new
  quarki-pt-tripwire.timer (weekdays 20:30 UTC) that scores live fires vs
  actual rebuys and AUTO-REVERTS mode to shadow + P4 if cumulative ≤ −$3.
  risk_config.json untouched (mode=shadow, min_notional 1.0 so the shadow
  ledger keeps accumulating events).

## 2026-08-05 (~13:25 MDT) — D1 false page #2 fixed for real (auditor inconclusive-state)
- 19:17 UTC fast audit paged "D1 guest /api/catalog -> -1" again — same
  transport-timeout signature as 07:17; the morning single-retry fix wasn't
  enough when the single-threaded dashboard is busy (audit runs at :17,
  owner catalog work takes ~1.1s even idle). Manual verification: guest key
  403s /api/catalog 3/3 in ~5ms, owner-mode 200, /api/edge guest-clean —
  NO leak, both pages were false.
- Fix (Robert-approved via ! command; classifier had blocked the unattended
  edit): D1 guest probes now use retries=2, and a transport failure counts
  as "inconclusive" (silent note + directive_auditor/inconclusive_state.json
  counter) instead of a violation; the SAME probe unreachable 3 consecutive
  fast-audit runs still escalates to a real P4 (persistent unreachability
  must not mask checking). Real non-403 HTTP responses page immediately,
  unchanged. Backup: auditor.py.bak-20260805T1930Z. Self-test ran the full
  invariant sweep clean (only D19 churn outstanding).

## 2026-08-05 — Moonshot daily-picks paper lane (D7 candidate)
- Robert asked for a small high-risk/high-reward satellite with daily picks;
  test-first. Built on the moonshot screener (dad's 5-10x thesis), NOT the
  shelved value screen (v2 lost to universe_hold and SPY, stays closed).
- moonshot_screen.py upgraded per plan: catalyst weight 0.25→0.35 (composite
  0.22 cap / 0.25 mo / 0.18 base / 0.35 cat) + 0.06 boost when a dated
  biotech readout is ≤90d out; refactored to expose screen() for the lane.
- NEW ~/moonshot/moonshot_lane.py: paper-only (hardcoded paper-api, PAPER
  keys only), 3 slots × $15, entries 10:00 ET from top scores ≥0.55, exits
  stop −12% / take +25% / 10-trading-day max hold, 5-day re-entry cooldown.
  Per-trade P/L ledger scanner/state/moonshot_ledger.jsonl (D7-scoreable:
  n≥30, mean≥0.05%, Sharpe≥1). Timers quarki-moonshot-entry (10:00 ET) +
  quarki-moonshot-exits (15:45 ET) enabled; first entries Thu 2026-08-06.
  Today's top-3 would be LTBR / ARQQ / EVTL.
- ~/moonshot is now a local git repo (no remote — owner-private per D1).

## 2026-08-05 — Moonshot universe: policy_pol theme (dad's PEW example)
- Robert's first concrete dad-trade example: GrabAGun (PEW, Don Jr.) —
  bought post-SPAC, held through ~85% dwindle, +34% spike July 2026 on the
  ATF direct-shipping rule. Added policy_pol theme (PEW/DJT/RUM/PSQH/BYRN,
  cat=0.7); it swept the top 3 scores, so moonshot_lane.py now caps picks at
  one per theme. Tomorrow's likely entries: PSQH, LTBR, ARQQ.
- Flagged: dad's hold-through-drawdown vs the lane's −12% stop; stop
  stays for the controlled test, hold-through counterfactual computed at D7
  review from ledger entry prices.

## 2026-08-05 (~19:54 UTC) — Crenshaw_Tracker/USOU dark-pilot: researched + visibility fix (improvement_bot)
- Resolved the open Watch item on `Crenshaw_Tracker`'s sole holding `USOU`
  never marking to market. Traced the raw House Clerk PTR PDF directly: the
  filing itself reads "United States 3x Oil Fund (USOU)" — not a parser
  bug. Web research: USOU (USCF Funds Trust, NYSE Arca) fully liquidated in
  December 2019 with no successor product, so there's no valid current
  ticker to substitute; yfinance's "possibly delisted" is accurate.
- Fix: `sources/autopilot_catalog.py` — `tick()`'s `if not px: continue`
  guard silently skipped any pilot whose every holding fails to price,
  forever, with zero trace (why Crenshaw_Tracker, a D24 auto-refresh row,
  has been dark since inception). Added `state/autopilot_catalog_dark.json`
  tracking any 100%-unpriceable pilot each tick (removed the moment it
  prices again); scanner journal line now names dark pilots too, so this
  surfaces in routine monitoring instead of needing manual investigation.
  Deliberately did NOT touch `autopilot_catalog_curves.json`'s schema — a
  new top-level key there would show up as a bogus row on the dashboard via
  `edge_ledger._autopilot_catalog_rows`/`catalog_leader_check.py`, both of
  which iterate every curves key as a real pilot.
- Explains the two watchdog "unknown state file" flags above:
  `autopilot_catalog_dark.json` is the new tracker file (this entry
  documents it); `autopilot_catalog_curves.json.bak-20260805T1952` is an
  ordinary pre-edit backup taken before running the fix live against
  production state to verify it (improvement_bot's standing backup
  discipline), same pattern as the `.bak-20260805T1553/1554` pair from the
  prior cycle's NAV-zeroing fix.
- Verification: backups taken, `py_compile` clean, `tick()` run live via
  `~/quarki-venv/bin/python3` against real state — reproduced the USOU
  failure, confirmed Crenshaw_Tracker now recorded in the dark file and
  curves file diffed clean (same 16 pilots, none added/removed/corrupted).
  `sudo systemctl restart quarki-scanner` — 21 sources, 3+ min clean
  journal post-restart. Catalog/benchmark-only (D4 money-proof);
  `combined_mirror` (live $50 governor, same process) untouched.
- Open decision left to Robert: retire Crenshaw_Tracker from D24's
  auto-refresh list, leave it dark-but-visible, or other treatment — no
  real substitute ticker exists so the bot won't decide unilaterally. Full
  trail in improvement_bot/LOG.md (2026-08-05 ~19:50 UTC entry).

## 2026-08-05 — Weekly mirror-sweep #1 reviewed; ALLW_Mirror catalog row
- Reviewed the 2026-08-03 weekly cloud delta-sweep (Quarki33/mirror-sweeps).
  Sole survivor: ALLW (State Street/Bridgewater All Weather ETF, live 3/2025,
  $1B AUM, beat 60/40 on Sharpe since launch; NEVER tested through a
  2022-style stock/bond correlation break — analogue RPAR lost >30% there).
- Added ALLW_Mirror as catalog row #23 (backup .bak-20260805-allw); curve
  strikes NAV 100 on next catalog tick and races DBMF/KMLM/CTA/RSBT under
  the D24 leader-check. Not promoted, not live money.
- Sweep also flags: RSBT no longer ~0% (+21-27% 1yr, some of it just bonds);
  CTA still flat (+0.25% 1yr) vs promoted siblings; QIS reject reconfirmed
  (-51.9% 1yr); JPFP/IALT too young; eToro US CopyTrader clock now live
  (revisit late 2026). Leader-check verified running (skipping swaps until
  promoted lane has 5 marks).

## 2026-08-05 (~16:45 MDT) — D31 guest-leak fixes + guest-stack evidence sweep
- Full sweep of everything a guest (Linus/dad key 7a0d…) can reach, done with
  their exact key via the public URL. Two real leaks found and fixed in
  ng_dashboard api.py _guest_scrub (backup .bak-20260805T2235Z-pre-d31-scrub,
  dashboard restarted): (1) /api/qc served mirror_mode:"live" +
  mirror_targets — the live book's composition; (2) /api/whales alert stream
  carried scanner telemetry lines "LIVE combined_mirror … pnl_pct=-0.48%"
  plus 40 symbol:"LIVE" internal rows (incl. prop_desk/LIVE). Guests now get
  zero "combined"/"LIVE" strings; owner responses unchanged.
- Verified clean after fix: all 21 guest endpoints re-swept for
  combined/alpaca_live/catalog/lab333/moonshot/sub5/overnight/pt_*/ibkr/LIVE
  markers — only false positives remain (BIODELIVERY, UUID substrings,
  patent combined_score). 403/404 fingerprinting also checked: unknown paths
  return the same 403 as private ones, so probing reveals nothing.
- Auditor: new D31 checks (guest /api/qc mirror keys; guest /api/whales
  combined/LIVE markers) added to check_invariants with clean self-test.
- Deliberate NON-fix: the shared trading-gui source's owner/guest-split
  comments and IBKR/combined_mirror blurbs (in MVYTraders since the 7/28
  collab-v2 commit) stay — Linus can read diffs, and deleting them now would
  itself signal hiding. They reveal the tier split (documented premise),
  not any post-7/28 activity. No post-boundary project appears anywhere in
  guest-reachable surfaces.

## 2026-08-07 — Mac failover: Oracle VM down since 2026-08-06 ~17:15 UTC (Claude + Robert)

Oracle login rejected (possible account suspension, cause TBD; ocistatus
shows no regional outage). Stack failed over to the Mac with live keys
regenerated (old VM-held keys invalidated):
- New live Alpaca keys stored at ~/alpaca_live.env (600) and injected into
  the ng-dashboard launchd plist.
- ~/bin/alpaca_guardrail.py + com.quarki3.alpaca-guardrail.plist (5-min
  launchd): alert-only turbo stop/take/freeze + D6 book-floor watcher,
  quarki_notify P4/P5, repeat-suppressed. First run green (equity 496.91,
  turbo +0.27).
- com.quarki3.ng-dashboard re-enabled on Mac :8080; Quarki333 Terminal
  reachable owner-mode at http://100.71.5.12:8080/terminal/ (phone app URL
  dialog re-pointed from dead VM 100.73.225.4). Funnel URL also live.
- Book verified intact through the outage: equity $496.75→496.91, unreal
  -1.57, no stuck orders. VM-fed panels (scanner/whale/catalog) stale until
  the VM returns; live account/position panels work.
- Recovery TODO when Oracle resolves: re-cutover, update VM alpaca keys,
  disable Mac failover pieces to avoid double-run (guardrail is alert-only,
  safe either way).


## 2026-08-05 (~23:56 UTC) — D19 auditor: same-symbol reversal detector (improvement_bot)
- `directive_auditor/auditor.py`'s D19 churn check (flat >12 live-orders/day
  count) couldn't distinguish a benign multi-name fan-out event (2026-08-04:
  15 orders, 3 clusters, 0 reversals) from genuine same-symbol whipsaw.
  Added a reversal scan, additive to the existing flat count (unchanged
  trigger/threshold/dedup key `D19:churn:`): groups today's live orders by
  symbol, collapses consecutive same-side fills, flags any symbol whose
  collapsed sequence is ≥3 (a real buy-sell-buy/sell-buy-sell).
- Verified live: today (2026-08-05) already had 43 orders, and unlike
  yesterday this time 4 symbols show genuine reversals — **INTC, RDY, TEVA,
  VTRS**. `compliance/latest.md` now names them instead of a generic count.
  Robert was already P4-paged on this violation at 18:17 UTC (before this
  fix); confirmed via `notify_state.json` no duplicate page fired (same
  dedup key, still inside the 24h reminder window) — this only enriches the
  diagnostic text on an already-seen violation.
- Read-only monitoring code — confirmed no order-placement path in the file
  before editing. Backup `directive_auditor/auditor.py.bak-20260805T2352Z`,
  `py_compile` clean, ran the real `auditor.py --fast` end-to-end post-edit.
- Open follow-up (not this cycle): root-cause why the governor is actually
  whipsawing INTC/RDY/TEVA/VTRS today — governor-tuning question, logged to
  improvement_bot/BACKLOG.md Watch section.

## 2026-08-05 (late) — D36: $50 live turbo arm shipped
- New live sleeve `moonshot/turbo_arm.py`: moonshot paper lane's exact
  brain (same screener, 3 slots × $16, stop −12% / take +25% / 10d) on the
  LIVE Alpaca account, $50 hard cap, freeze rail at −$15, PDT guard,
  mirror-collision guard, never-borrow. Timers: quarki-turbo-entry
  (10:15 ET), quarki-turbo-exits (11:30/13:30/15:40 ET). First entries
  Thu 2026-08-06.
- combined_mirror.py: D36 block — turbo legs invisible (no orphan
  liquidation, no targeting), bankroll = equity − max($50, turbo MV).
  Backup: combined_mirror.py.bak-pre-turbo-20260805.
- edge_ledger.py: generic pl_pct-ledger reader adds rows
  `moonshot:paper` + `turbo:moonshot_live` — live-vs-paper slippage
  twin is directly scoreable on the same board.

## 2026-08-06 (~11:55 UTC) — prop_desk: entry_ts ledger bug fix + negative-expectancy root cause (improvement_bot)
- Root-caused why prop_desk's 3 paper strategies (momentum/mean_reversion/
  breakout) are persistently negative (2158 trades since ~mid-July): all 3
  are roughly breakeven BEFORE the 0.35% roundtrip cost (gross means
  -0.09%/+0.03%/+0.06%) — essentially no raw signal edge, not a timing or
  slippage-measurement bug. Net of real trading costs they land at
  -0.44%/-0.32%/-0.29% per trade. Zero live weight per D9 regardless.
- Along the way, fixed a real but unrelated logging bug: every row in
  `scanner/state/prop_ledger.jsonl` had `entry_ts == exit_ts` (100% of
  2180 rows) because `portfolio.py`'s SELL-act dict dropped the real
  `entry_ts` already present in `hold_state` before it reached
  `sources/prop_desk.py`'s `_log_closed` write, which then hardcoded
  `now` for both fields. Fixed in both files (backups
  `portfolio.py.bak-20260806T115353Z`,
  `sources/prop_desk.py.bak-20260806T115353Z`); confirmed `entry_ts` is
  never read by `auto_tune.py`'s promotion-gate scoring, so this only
  affects duration diagnostics, never a live decision. Verified:
  `py_compile` clean, `sudo systemctl restart quarki-scanner` — 21
  sources, 0 errors post-restart, live open hold (ETH, whale_primary)
  carries the real `entry_ts` forward for its next exit.
- Paper-only source (`ALPACA_MODE=paper` confirmed live), no live-money
  path touched. Full evidence in improvement_bot/LOG.md 2026-08-06
  ~11:47 UTC.

## 2026-08-06 — D37: improvement bot rebalanced toward market-scanning research (Claude + Robert)

Robert: the research bot is healthy but "we can do better in terms of
scanning the market and finding more ways to profit." Shipped:
- `improvement_bot/MISSION.md` step 2 rewritten: alternation rule — if the
  previous cycle wasn't tagged `[research]`, this cycle MUST take a Research
  agenda item (broken-now health problems still preempt). Backup
  `MISSION.md.bak-20260806T1505`.
- `improvement_bot/BACKLOG.md`: new "Research agenda" section, R1-R8 seeded
  (signal-vs-0.35%-cost-hurdle search, mine scanner signal exhaust for
  forward returns, PEAD paper-lane candidate, overnight-drift extensions,
  Senate fallback dataset, OCR for image-only House PTRs, 13F filer catalog
  candidates, weekly sweep). Backup `BACKLOG.md.bak-20260806T1505`.
- DIRECTIVES.md: D37 added with a deterministic CHECK (>=2 `[research]`
  LOG.md entries per trailing 48h).
- 5 parallel sonnet research scouts + an ultracode edge-hunt workflow
  launched from the Mac session to pre-validate R1/R2/R3/R5 and hunt fresh
  edge ideas; verified findings get folded into the Research agenda.

## 2026-08-06 — Edge-hunt sweep results folded into D37 Research agenda (Claude)

The 5-scout + 49-agent adversarial sweep completed. Headline: 0/21 raw
workflow candidates survived 2-lens verification (crowding + retail
tradability), but the sweep produced a re-ranked, evidence-backed Research
agenda R1-R10 (BACKLOG.md rewritten; backup .bak-20260806T1530) topped by:
Form 4 insider CLUSTER buying (JF 2012, ~21.6%/yr EW opportunistic alpha),
10b5-1 plan-termination drift (Jul-2025 evidence, still-open 82d disclosure
lag), multi-week TSMOM vs the 0.35% cost hurdle, and a two-cycle
scanner-exhaust forward-return study (blocker found: state/prices/ is a
50-min rolling buffer — backfill harness needed; moonshot/sub5/value_screen
keep no history at all → point-in-time logging added as R5 quick win).
Closed-out negatives list added so cycles never re-litigate dead edges
(index effect, steady-state carry, IPO lockup, crypto listing, large-cap
PEAD, CEF reversion, merger arb at our scale). Evidence dossier:
~/improvement_bot/research/2026-08-06-edge-sweep/.

## 2026-08-07 — Terminal 333 v1.1 shipped (Mac failover client) (Claude + Robert)

Old WebView Terminal app white-screened on the tailnet route (unresolved —
old app abandoned per Robert). Rebuilt the native Terminal 333 app
(lab333/stack/terminal333-android, versionCode 2): DEFAULT_BACKEND now the
Mac funnel HTTPS URL (works on any network, no phone-side Tailscale
dependency), plus an x-api-key interceptor sending the owner key (from
local.properties, not committed) so the public-gate grants full owner view.
Same signing key as v1.0 → in-place update. APK at
lab333/dist/terminal333-v1.1.apk, temp download link via ng_dashboard
/static (unguessable name, to be deleted after install). Committed to the
local lab333 repo (push still awaits lab PAT).

## 2026-08-07 — Oracle-independence sweep applied to lab333 repo; 333 v2.2 shipped (Claude + Robert)

Ultracode audit (5 slices, 46 findings) of robertpainter144-lab/-333-333-333-
(branch app-333). Shipped:
- 333 v2.2 (versionCode 4) from Robert's app-333 branch: default backend =
  Mac funnel HTTPS + x-api-key owner auth; UpdateChecker follows the same
  base so in-app updates now flow from the Mac. Pushed (1093528, eaf2913).
- Silent-failure fix: combined_mirror circuit-breaker ntfy path was
  /home/ubuntu/ntfy.env (failed silently on Mac) -> Path.home(); same class
  fixed in shadow_review (+ real indent/precedence bugs), send_sms,
  catalog_leader_check venv path; api.py host label oracle->mac.
- Both android publish.sh scripts: dead-VM scp/ssh legs removed, dist ->
  ~/trading-dist (what /api/apk actually reads), manifest URL -> funnel.
- lab333-api now a Mac launchd service on 127.0.0.1:8334 (healthy).
  Deliberately NOT funnel-exposed: it is keyless by design (tailnet trust
  model) — public exposure would serve live-money data unauthenticated.
HELD for Robert's go: scanner/live-mirror revival on the Mac (live order
flow from repo copy — needs explicit go despite standing trading yes, due
to stale-code risk class), _bots_status systemctl->launchctl rework (roster
reconciliation first), guest-surface restoration (guests dark since sslip
died), IBKR loggers (blocked on IB Gateway, not Oracle).

## 2026-08-07 — 333 v3.0 (Lovable Terminal UI, lab identity) installed on phone (Claude + Robert)

Robert clarified the goal: the Lovable Terminal UI, but owned by the lab
account for Linus-separation. Shipped terminal333-webview in the lab repo
(applicationId com.lab333.terminal, label "333", versionCode 6/3.0, lab
keystore, backend URL+owner key from local.properties only — never
committed). Server: _owner_shell now injects catalog + qt.apiKey seed for
funnel requests carrying the owner key (verified: wrong key -> vanilla
shell; keyless owner routes 403). Installed + launched via adb over USB;
server logs confirm the app authenticating and pulling live data through
the funnel. QA fleet: privacy gates 6/6 PASS; 43-call API sweep — live book
fresh ($497.77/22 pos), VM-fed panels frozen at 7/27 (expected), two real
bugs filed: /api/equity/shadow 500, /api/activity/alpaca_live empty (fill
logger not running on Mac). Old download links retired.

## 2026-08-07 — Blank-Terminal root cause found & fixed (Claude + Robert)

The "app is blank" symptom was NOT network/backend — diagnosed via an
injected error-beacon that reported body=725 chars / #root=0 / key=set /
no JS error. Root cause: the trading-gui SPA bundle was built for the VM's
mount point, so the router basename + asset base were hardcoded to
`/private-terminal`. Served at the Mac's `/terminal` mount, no route matched
→ React silently rendered nothing (no error, no /api calls). Fix: rewrote
`/private-terminal`→`/terminal` in ~/trading-gui/dist/index.html AND the JS
+ CSS bundles, renamed the assets (index-fixq7.js/css) to bypass any WebView
cache. Verified end-to-end through the funnel: served bundle has 0 remaining
dead base refs. 333 webview app: v3.1 (versionCode 7) now discards any dead
saved URL (private-terminal/sslip/VM-IP) so it always boots the funnel URL;
added a WebChromeClient console->logcat for future diagnosis. App pushed to
lab app-333. NOTE: ~/trading-gui/dist edits are Mac-local (that tree isn't
the lab repo) — a future VM/host rebuild must re-apply the base rewrite or
serve the SPA at whatever path its build targets.

## 2026-08-07 — Config-drift finding: VM had uncommitted local edits (Claude + Robert)

While assessing the app's alarming "AAPL 90% target", found the single-name
cap (MAX_SINGLE_NAME_PCT) was 0.90 in BOTH the Mac copy and the lab repo —
the 0.35 tightening (D6, 2026-08-04) had been made directly on the VM and
never committed to git. Restored 0.35 in code + pushed. IMPLICATION for
redeploy: the dead VM likely carried OTHER uncommitted local edits from
late July/early Aug that never reached git; a bootstrap from the repo will
be missing them. When the VM is recovered (70c reactivation), diff its live
code against the repo BEFORE decommissioning, to recover any other VM-only
fixes. The AAPL-90% display itself was a harmless frozen Jul-27 snapshot
(bot not running); real book stayed diversified (AAPL ~0.6%).

## 2026-08-07 — App chart + allocation display fixed (Claude + Robert)

Two display fixes in ng_dashboard/api.py (running Mac dashboard + lab repo):
- Chart: _equity_alpaca_live now pulls Alpaca's real /v2/account/portfolio/
  history (23 real daily points) instead of dead-CSV(ends Jul27)+one bridge
  point — kills the misleading straight interpolation across the outage.
  Falls back to CSV if the API is unreachable.
- Allocation: /api/qc mirror_targets passed through _cap_display_targets
  (D6 0.35 single-name cap) so the "GOVERNOR BLEND" bar shows AAPL 35% (was
  a frozen-Jul27 raw 90%), remainder as cash. Raw kept as mirror_targets_raw.
Both are display-only; no order path touched. Nothing was ever going to buy
AAPL 90% (no bot running; cap restored in code earlier today).

## 2026-08-07 — Book chart + allocation fixes (3-agent workflow) (Claude + Robert)

App WAS rendering (blank-page root cause fixed earlier) but chart+allocation
looked wrong. 3-agent workflow (verified diffs: tsc --noEmit + patch dry-run)
produced 4 patches, all applied + rebuilt:
- CHART cliff: _equity_alpaca_live hoisted the live-equity pin OUT of the
  CSV-fallback-only branch, so portfolio-history path also appends a live tail
  point. Alpaca's daily bar posts ~24h late, so post-deposit the last bar
  predated the $450 transfer → EquityChart's transfer-snap subtracted 450 from
  a stale ~$48 bar = -$402 cliff. Now the live point gives the deposit a real
  step. Verified: min adj 43.41 (was deeply negative), tail=498.79 live.
- ALLOCATION: BookKit.tsx strips the "cash" pseudo-symbol from the target bar
  (was rendering as a colored ticker, stealing a palette slot + zeroing the
  grey cash block) and adds an amber "stale · <age>" badge when
  mirror_targets_ts is >24h old (governor blend frozen Jul-27). types.ts +
  Book.tsx wire mirror_targets_ts through. AAPL target displays 35% (D6 cap).
Rebuilt SPA (vite base /terminal/, new hash index-BjckGR-a.js), restarted
dashboard. Phone locked (PIN) so no live screenshot; verified at data+serving
layer. Pushed to lab repo app-333.
- 2026-08-09 14:25 — D39 crypto sleeve shipped: ~/crypto_sleeve/ (sleeve.py whale-gated momentum engine, whale_refresh.py Dune Q03 risk-on feed, config.json rails $50/−$15), launchd com.quarki3.crypto-sleeve (5min) + com.quarki3.crypto-whale-refresh (6h) on Mac interim. Idling on crypto_status=INACTIVE until Robert signs Alpaca crypto agreement + deposits ~$50 settled cash (P4 sent). Also: quarki_notify.py now falls back to ~/ntfy.env on Mac (was VM-path-only). Known issue: whale_tracker Q03 returns 0 rows — needs run_curated.py re-patch; sleeve degrades to momentum-only meanwhile.
- 2026-08-09 16:04 — Whale feed repaired: diagnosed 0-rows as NOT a plumbing bug — Q03 (Dune id 7868607) already had the correct 10-wallet curated_wallets.py list, but 9/10 are MM/VC/insider addresses that move size via CEX/OTC/custody, not public DEX swaps, so Q03's taker-only join against dex.trades was structurally starved at 7d/$25k. Non-destructive PATCH of the live query (same query_id, no delete/recreate): lookback 7d→30d, min_trade_usd $25k→$10k, wallet list widened 10→17 (curated + empirically DEX-active addresses). Verified end-to-end: `python3 crypto_sleeve/whale_refresh.py` → fresh execution 01KZM8V4YY907PTB3NZJ1X639Q → state/whale_signals.json now asof 2026-08-09T22:03:47Z, rows=1, risk_on=0.00 (one real $17,448 Machi-Big-Brother WETH→USDC derisk trade, 2026-08-08). sleeve.py/config.json untouched per repair rules. Follow-ups NOT done (flagged for later, not blocking): whale_gate() in sleeve.py has no min-sample floor so a single de-risk row can swing it fully whale-blocked; Q02 (smart-money deriver, id 7868545) and Q04 (curated transfer feed, id 7868642) are both still stale since ~Jul 2 and Q04 was never wired into whale_refresh.py. crypto_status unchanged (still INACTIVE pending Robert's Alpaca crypto agreement + deposit).
- 2026-08-09 17:30 — Terminal Crypto tab + Book chart overlay shipped (owner-private, D1-compliant): trading-gui EquityChart.tsx gained a GENERIC crypto-free overlay hook (window.__qtOverlaySeries + "qt-overlay-series" event, overlayChannel prop; Book passes "book") — shared source contains zero crypto/private strings. All crypto specifics live owner-side: ng_dashboard/private/owner_crypto.js (injected 7th nav tab 🪙 Crypto after Catalog, dedicated crypto page with hero/positions/whale-gate/rails/log, and the Book-chart feed: green #3fb950 sleeve-equity line, green $ ticks on the RIGHT edge, yellow #ffd60a time axis along the TOP, pan/zoom shared with the main line via the common time domain) + owner-gated /owner/crypto.js route + owner-only /api/crypto/summary (equity.csv/sleeve_state/whale_signals/config/log tail from ~/crypto_sleeve; NOT in _GUEST_PATHS). Verified: owner shell injects both bundles; guest-key + keyless XFF requests 403 on /api/crypto/summary and /owner/crypto.js; public shell has no owner/ scripts; tsc+vite build clean; dashboard kickstarted. Android app = WebView of /terminal so D2 is satisfied automatically. NOT synced to lab333/VM copies (Mac serves the Terminal).
- 2026-08-09 17:35 — Ultracode 24h directive audit (33 agents: 28 findings → 13 confirmed / 15 refuted) + Mac failover completed. (a) com.quarki3.scanner re-bootstrapped and com.quarki3.stack-watchdog re-ENABLED (it was in launchd's disabled-services DB since the VM migration — `launchctl enable` then bootstrap); watchdog immediately re-bootstrapped com.trading.qc + com.quarki3.swarm; scanner cycling all sources, edge_ledger fresh again (was ~45h stale). (b) ng_dashboard/api.py `_alpaca_live_transactions` switched from SDK `get_account_activities` (unsupported in this alpaca-py; 240+ silent failures) to raw REST FILL fetch with direction=desc, mirroring the `_alpaca_live_transfers` workaround; dashboard kickstarted; /api/activity/alpaca_live returns fills, 0 errors post-restart. Last live fill = 2026-08-06 15:44Z — live governor has been dead since the VM outage; Mac combined_mirror runs PAPER (flip to live only on Robert's explicit go). Audit highlights: D38 was a DOUBLE breach today (rogue scripts/auto_sync.sh in ~/trading-system: 949 per-minute commits pushing D1-private strings to Quarki33/trading-system until 16:39, now .DISABLED-D38 + remote stripped, scrub in other windows; plus old trading-autopush.sh pushed 11 Quarki33 repos, rewritten 09:48). Open follow-ups: watchdog alpaca account check 401s (env/key source), disk free 5.97 GB, Q02/Q04 whale queries stale, api.py .bak sprawl, dashboard logs in /tmp. Robert 17:22: "forget about oracle" — VM abandoned; public sslip.io URL dead pending replacement; VM-only timers (directive auditor, leadercheck/D24, moonshot lane, overnight drift, improvement bot) need Mac ports.
- 2026-08-09 17:42 — LIVE rebalancing moved to the Mac on Robert's explicit go ("address number 3"). run_scanner.sh now exports COMBINED_MIRROR_MODE=live after sourcing quarki.env (flip done in the launcher, not the env file); scanner kickstarted; combined_mirror_state.json mode=live, account_equity $498.41 / 22 positions, zero orders fired (market closed; D21b blocks closed-market rotation, new-leader needs 4 consecutive evals). state/risk_config.json did NOT exist on Mac — recreated with exact code defaults (breaker -1.5% enabled, profit_take SHADOW per D22, elig_gate SHADOW per D22a, sell guards live), so rails were never off but are now explicit/auditable. com.trading.qc launchd-ENABLED + running (broker-api SANDBOX only — signal lane, fixes 13-day-stale qc refresh; next rebalance Mon 09:31 ET). com.quarki3.swarm left DISABLED deliberately (Claude-token burner, D25-gated, not a trading service). Oracle-independence sweep (5-agent workflow) running to enumerate remaining VM dependencies.
- 2026-08-09 17:45 — Crypto overlay v2 polish (Robert's feedback): EquityChart overlay now draws with its own gradient area shading + 2px line + live-end dot (findable even while the sleeve has minutes of history); overlay value axis moved into a dedicated 48px RIGHT gutter (green $ numbers, never painted over the plot; grid/labels/gesture math all gutter-aware so pan/zoom stays pixel-accurate); pannable domain extended to include overlay extent; per Robert's latest ask the crypto TOP time axis is now GREEN (was yellow) — all crypto markings green. Crypto page spark chart: gridlines + area fill + green time labels + green hero equity. Rebuilt (index-DrKC50tN.js), phone app force-restarted via adb (Z Flip4 USB), new build + /api/crypto/summary polling verified in access log. NOTE: consumption tier RED at the time — Robert asked for multi-agent branch, declined per his own D25 (no swarms at RED), verified inline via tsc+node instead.
- 2026-08-09 17:50 — Book equity chart full-bleed on mobile (Robert's ask): Card gained a `bleed` prop (mobile: -mx-3, side borders/rounding dropped, y-borders kept; md+: normal card), equity Card uses it; EquityChart axis font 10->9px, gutters L46->38 / R48->42, header/footer rows keep px-2 inset; gesture math follows the new geometry. Built index-GD4Ontcf.js, phone force-restarted via adb, new asset fetch verified.
- 2026-08-09 17:57 — Crypto overlay v3 (Robert's live feedback loop): overlay axis labels right-aligned flush to screen edge + dropped to 8px (main stays 9px), right gutter 42->34; NEW leadIn option on OverlaySeries — dashed flat segment at first-point value across the pre-history region (young sleeve reads as a full-width line; dashed=pre-tracking, solid+shaded=real data, dot=live end); owner_crypto.js passes leadIn+green top axis. Builds ...-BM7b88UI -> -CMdiBKl4 -> -BcxfVytM shipped, phone adb-restarted each time, fetches verified.
- 2026-08-09 17:55 — Full-bleed seam fix: Card's max-w-full capped the -mx-3 bleed (flush left, ~12px short on the right = visible "border" next to the green axis). max-w-full now only applies when not bleeding (md: restores it). Left axis numbers render in the main line color (yellow) when an overlay is on — color-coded axis pair. Build index-DwZSogLY shipped, phone adb-restarted, fetch verified.
- 2026-08-09 18:00 — Book page fully borderless on mobile (Robert): bleed variant drops ALL card borders (bands of panel color separated by bg gaps; md+ keeps framed cards), applied to every Book card incl. hero; EquityChart header row + controls now flex-wrap (fixes ALL range tab hanging off the right edge after the card widened). Build index-Br4JRsMu shipped, phone adb-restarted, fetch verified.
- 2026-08-09 18:00 — Oracle-independence sweep (5-agent workflow, 65 findings) EXECUTED; D40 added (Mac primary, VM abandoned). Shipped: (1) TURBO ARM restored on Mac — BYRN/LTBR ($32 live, net +$0.23) had run unmanaged since 8/6; state reconstructed from Alpaca avg_cost/qty into scanner/state/turbo_arm_state.json, turbo_arm.py ported to ~/moonshot/ (Mac paths), launchd com.quarki3.turbo-entry (08:15 Denver = 10:15 ET) + turbo-exits (09:30/11:30/13:40 Denver), AND the D36 carve-out grafted into the live combined_mirror (turbo legs invisible + $50 bankroll carve) — without it Monday's rebalance would have orphan-liquidated both legs. (2) Directive auditor PORTED to Mac (~/directive_auditor/, launchd hourly --fast + daily full w/ LLM pass via /opt/homebrew/bin/claude, gate = consumption_manager.py gate); fast pass = 0 violations. (3) request-harvester de-VM'd (local ledger only; rsync + credential-push legs deleted); SessionStart hook now reads Mac-local compliance report. (4) New launchd: leadercheck (weekdays 16:17 local; skip-branches now write last_run — re-created lost VM-only fix), shadow-review (Mon 08:00), lab333-watch (hourly D1 tripwire, RECREATED ~/bin/lab333_public_watch.py, current status ok/zero public repos), mvy-watch (was never loaded), improvement-bot re-enabled (tier-gated). (5) ng_dashboard /api/bots rewritten systemctl→launchctl (all 12 rows now truthful), strategy create/delete no longer 500 on missing systemctl. (6) healthchecks.io: no oracle check exists anymore (mac-stack up; quarki-stack-gcp "new" from the GCP window). NOT touched (other windows own them): consumption_manager vm_sync→GCP, heartbeat_hosts.json (GCP primary designation), Android/333 app DEFAULT_BACKEND rebuilds, trading-gui vite proxy/docs. Declared open: moonshot paper lane, overnight-drift, mirror-pnl, labbook, dd-cap shadow, linus-osint scripts died with the VM (not in lab333 mirror) — recreate-vs-retire decision pending.
- 2026-08-09 18:03 — Crypto overlay v4 + Book intro removed: owner_crypto.js now collapses funding/adoption steps (>$4/tick backward-walk shift, mirrors EquityChart transfer semantics) — the PEPE adoption ($10->$30 jump, other window's 812d722) had crushed the green line into a 2px vertical wall; line now shaped by trading P/L only, ends at true equity. Book PageIntro blurb removed per Robert. Build index-DOU173zQ; deploy VERIFIED BY ADB SCREENSHOT (s3.png: both lines render, green dashed lead-in + solid tail + dot, green axis $30.20-30.40 right, yellow left, borderless full-bleed page). Workflow note: adb screencap needs on-device file + pull (Flip4 multi-display).
- 2026-08-09 18:07 — Tri-balance band shipped (Robert): owner_crypto.js injects a top-of-Book band — ALPACA USD (yellow) / CRYPTO (green) / TOTAL (white), same-size mono 20px, color-matched to chart lines; polls /api/portfolio/alpaca_live + /api/crypto/summary 30s; Book-route-only (regex on pathname), survives SPA route changes via mount loop. Script-only deploy, adb-restarted, VERIFIED by screenshot s4.png ($499.41 / $30.22 / $529.63). Also answered Sun-evening balance uptick = quote re-marks (feeds resume ~6pm ET Sun), not trades — deep dive owned by window 135cac2f.
- 2026-08-09 18:11 — Stacked-lane chart design (Robert: "green bar on top of the yellow"): OverlaySeries gained `lane` (0-1 fraction) — overlay claims a dedicated TOP slice of the plot (crypto: 0.3), main line compresses into the remainder, dashed divider between; markers/transfer flags confined to the main lane. No more tangled dual-scale overlap. owner_crypto.js passes lane:0.3. Build index-Ck92UEXg, adb-restarted, VERIFIED by screenshot s5.png (clean two-lane render + tri-bar $499.68/$30.25/$529.93).
- 2026-08-09 18:25 — ALL six VM-lost lanes rebuilt + live (6-agent sonnet workflow, code reviewed line-by-line for paper-only order paths before install): overnight_drift.py (scanner/sources/, ondrift-buy 13:58 / ondrift-sell 07:31 Denver, PAPER, mark_px slippage isolation), moonshot_lane.py (~/moonshot/, entry 08:00 / exits 13:45 Denver, PAPER, 3x$15 stop-12/take+25/10d), mirror_pnl.py (~/bin/, weekdays 08:10, first run scored 6 lanes → D34 gate can finally score), labbook.py (~/bin/, 15-min, first 5 curve rows appended), dd_cap_shadow.py (~/bin/, 30-min market hours, zero-order live-book shadow, rebuy=+$5-recovery assumption documented), linus_osint.py (~/bin/, daily 09:00, first-run baseline silent). PLUS pt_tripwire.py (~/bin/, weekdays 14:30, D22 auto-revert net<=-$3 — the flip-blocker now exists) and sub5_watch.py (scanner/sources/, 6h, virtual-only $5-gate lane, watchlist seeded EMPTY — Robert reseeds picks by hand; D1 edge-strip hooks in api.py/edge_ledger.py still need restoration before sub5 rows surface anywhere). Auditor TIMERS + D40 label list extended to all 15 timers; fast pass re-run. Small-leftovers agent: linus-diff --live localized, spy-overlay stale comment removed, ~/bin/vm_watchdog.py deleted (archived copy kept), lab333 consumption vm_sync disabled. Disk survey: ~5.5GB safely reclaimable, auto-clean blocked by permissions — list pending Robert's OK. Phone Terminal-app disappearance handled in window 607bc949.
- 2026-08-09 18:36 — Weekend/intraday equity fidelity fixed (Robert's Alpaca-dashboard comparison): (1) portfolio-history fetch switched from legacy extended_hours (returns NOTHING Sat/Sun — verified: 1D/5Min gave stale Friday window) to intraday_reporting=continuous for intraday timeframes; (2) added finest splice layer — last 24h @ 5Min continuous — on top of the existing daily+1W@1H splice, since the SPA fetches ONLY period=5Y and slices client-side (was daily-bars-only = the "blocky" chart). Verified: /api/equity/alpaca_live?period=5Y now 887 pts incl. 289 in last 24h, min/max matches Alpaca dashboard; phone screenshot confirms 1D renders the real weekend shape. Note: window bcb4ecf2 concurrently added 5Y pmap + 3D/1Y/5Y range tabs + lane:0 overlay restyle — coordinated, not duplicated.
- 2026-08-09 18:52 — Crypto research surface expanded (Robert: "be more illustrative of all the research… wire it all into the crypto page"): whale_refresh.py grew from 4 → 6 legs — NEW derivatives leg (Hyperliquid metaAndAssetCtxs: funding 8h/APR, OI $, 24h vol, 24h px chg per sleeve coin; risk_on = OI-weighted crowd-long share) and NEW market-breadth leg (CoinGecko /global: mcap Δ24h, BTC/ETH dominance, total vol); Reddit widened from r/CryptoCurrency alone to 5 subs (+Bitcoin, ethereum, solana, SatoshiStreetBets) with per-sub bull/bear in detail.reddit_subs. Both new legs are DISPLAY-ONLY (signal_*=0 → leg_weight 0) — whale-gate evidence rules untouched. Rate limits handled: 10s inter-sub gap + 30s 429 retry (Reddit), 20s 429 retry (CoinGecko). owner_crypto_intel.js renders 4 new sections: Market breadth, Derivatives table, Multi-chain scanner whale alerts (finally surfaces /api/whales on the crypto page — 5,213 wallets tracked, last 8 alerts), per-sub Reddit row. No restart needed (no-cache FileResponse + per-request JSON reads); verified live on :8080 — intel legs=[hyperliquid,dune,social,stocktwits,derivatives,market]. Owner-only throughout (D1/D31: /api/crypto/* not in _GUEST_PATHS).
- 2026-08-09 18:50 — Chart v5 (Robert's evening batch): (1) COMPRESSED CLOCK — piecewise time map renders weekend dead-time (Fri 18:00→Sun 18:00 local) at 15% width on windows >48h; all gestures (pan/pinch/wheel/hover) map through u(t)/inv(u); tick labels get 40px collision guard; sub-2-day views keep a true linear clock so crypto's weekend action stays full-size where it matters. (2) LAYERING — overlays now paint UNDER the main series (pass 1: fill @22 alpha + 1.8px line + dot; pass 2 after main: axis labels) → yellow predominant, overlaps blend olive instead of scribble. (3) RANGES — added 1H/3H/6H/12H tabs, strip scrolls horizontally (13 tabs). Build index-CZPKcEl5, adb-restarted, VERIFIED screenshot s12.png (1H view: both lines live with real 5-min data, clean blend). Also explained: Sat-vs-Sun yellow offset = seam between Alpaca hourly feed (weekend parked at Fri-close marks 499.56) and their 5Min continuous feed (re-marked 498.64) — both Alpaca's own numbers.
- 2026-08-09 18:55 — Mac-shutdown readiness pass (Robert: "get everything to a point where I can shut off the mac again without hurting anything"). Verified on GCP quarki-stack (34.66.251.16 public / 100.94.213.49 tailnet, ssh w/ oracle_trading key — tailnet :22 still closed): (1) FIXED quarki-scanner crash loop (1168 restarts: ModuleNotFoundError curated_wallets — evm.py hardcodes /Users/quarki3/whale_tracker; symlinked lab-stack/whale_tracker/curated_wallets.py into scanner/; now active, all 10 sources ok, confirmed COMBINED_MIRROR_MODE=paper + ALPACA_MODE=paper so no double-trading vs Mac's live mirror). (2) Guardrail VERIFIED live: 5-min timer, watches equity + per-leg turbo stop/take (−12/+25) + $15 freeze + book DD, alert-only by design; ntfy push path test-fired OK from the VM. (3) Deadman → healthchecks.io pinging every 5 min. (4) SECRETS: ng_dashboard/alpaca_keys.py was untracked INSIDE the VM repo clone (git add -A would leak live keys) — added to .git/info/exclude along with *.env + the curated_wallets symlink. (5) Broker-side GTC stops for BYRN/LTBR considered and REJECTED: Alpaca fractional-qty stops unreliable + resting stops would double-interact with turbo_arm when Mac returns; guardrail P5 paging covers the $32 sleeve. KNOWN GAPS while Mac is off (accepted/flagged, not hurting money): phone 333 app's deployed build talks to the MAC funnel and GCP dashboard has no /api/crypto endpoints (app work owned by 3 parallel windows — do not collide); Kraken crypto sleeve ($30) pauses unmanaged, no GCP watcher (withdrawal-capable key deliberately NOT copied to VM — rotate first, then add balance watcher); Mac-only paper lanes/audits (moonshot, ondrift, sub5, ddcap, leadercheck D24 CHECK breaks ~8/13, directive auditor, improvement bot) pause while off. Live combined_mirror simply doesn't rebalance while Mac is off — guardrail pages on DD.
- 2026-08-09 18:58 — "Finish everything" closeout (this window's lane): (1) GCP quarki-stack VERIFIED first-hand over the working SSH path (public 34.66.251.16 + oracle_trading key): scanner+dashboard active, COMBINED_MIRROR_MODE=paper read directly from its env — Mac-live/GCP-paper is confirmed coherent, no dual-trade possible; Mac gcloud auth items from today = superseded by this path. (2) pt_live.jsonl fire-logging grafted into Mac combined_mirror.py from the Aug-5 staged copy (one more lost VM edit; inert in shadow, feeds the new pt-tripwire when PT ever flips live); scanner reloaded clean. (3) ng-dashboard logs moved /tmp -> ~/trading-data/logs (plist bootout/bootstrap, /api/health 200 after); 9 api.py .bak-* archived to ng_dashboard/bak_archive/. (4) brew+pip caches purged (~300MB). OPEN: disk 3.9GB free and trending down — big reclaim items (Firefox profile 3.0G, Downloads 2.7G, Google 2.6G, browser caches ~1.2G) are user-data/deletion-gated, need Robert's explicit OK; sub5 D1 edge-strip hooks (api.py/edge_ledger.py) deliberately deferred until the app windows finish their api.py work; Kraken key rotation still Robert's. Prompt-checklist artifact published (131 prompts, 58 done / 24 open at build time; several closed since).
- 2026-08-09 19:03 — D41 adopted (crypto graduation ladder): Robert's $250 Alpaca→Kraken impulse converted to a ledger-gated ladder — $50 now, $110 after 30 clean trades (mean≥0.05%, Sharpe≥1, no freeze), 35%-of-book (~$185) after 60, beyond that only via explicit D34 amendment. Appended to DIRECTIVES.md with auditor CHECK line; memory feedback_crypto_graduation_d41 written. No money moved tonight (settled cash $4.29 anyway; T+1 + ACH makes the gate timeline and logistics timeline converge).
- 2026-08-09 19:05 — Alpaca→Kraken stake decision PARKED by Robert: no liquidation/transfer prep tonight; he decides after Mon 08-10 market open (options on the table: stay on D41 ladder $50 / override to $110 / full $250 w/ D34 amendment). NOTE interpretation: "don't touch the alpaca book until after market open" = no transfer-related selling; the normal 9:31 ET rebalancer run (INTC catch-up toward 35% target) is treated as routine ops and will fire unless Robert says "pause the rebalance". No orders placed tonight.
- 2026-08-09 23:15 — Crypto allocation bars shipped (owner-private, D1-clean): sleeve.py now records per-tick live marks (positions[].mv/cur) + engine WANTS (state.targets/_ts/_gate: kept positions + momentum picks to the $50 cap, cash-blind, display-only, fail-soft) — verified live tick: targets = SOL 6 + PEPE 20.43 + BTC/ETH/CRV 6 + AVAX 5.57 = $50. owner_crypto.js gained the Book-style holdings-vs-target bar pair (same 16-color footprint-ranked palette, striped cash remainder, legend chips, >20min stale badge; holdings = % of sleeve equity, target = % of cap w/ gate label). No api.py change needed (/api/crypto/summary already ships full state); served live without restart; guest /owner/crypto.js still 403. Android app inherits via /terminal WebView (D2).
- 2026-08-09 23:22 — Book-page crypto allocation bar shipped (owner-private): Book.tsx gained a GENERIC crypto-free mount point (<div data-qt-slot="book-alloc-top">, zero crypto strings — same D1 pattern as the chart overlay hook), tsc+vite rebuilt clean, dist serving. owner_crypto.js injectBookAllocBar() fills it every 2s (fingerprinted, rewrites only on change) with the sleeve holdings bar — green-labeled, % of sleeve equity, striped cash remainder, legend chips — sitting directly ABOVE the Alpaca current-allocation bar per Robert. Verified: slot in built bundle, /terminal 200, injector in served /owner/crypto.js, guests untouched (script never reaches them).
- 2026-08-10 00:20 — Crypto page: universe-watch table + stock-metric holdings table (owner-private). New owner-only /api/crypto/ticker (Kraken AssetPairs+Ticker proxy, wsname mapping incl BTC→XBT/DOGE→XDG, 20s cache) prices ALL 24 universe coins live — delivers Robert's "track everything" want with ZERO dust positions (dust-buy idea flagged against per D10: order minimums, fee churn, exit-rule noise, D7 ledger pollution; he has $3.86 free anyway). Holdings table now mirrors the Book stock PositionsTable metrics: Symbol/Qty/Entry/Now/Mkt value/Unrl P/L/Unrl %/% sleeve w/ green-red row tint. Universe watch sorts HELD → TARGET → 24h change w/ badges. Verified: 24/24 coins priced, guest ticker 403, JS+api compile, dashboard kickstarted clean.
- 2026-08-10 00:40 — Notification diet (Robert: "too many notifications", D28 enforcement): (1) auditor false-positive fixed — guest-gate probes returning -1 (dashboard busy/mid-restart) now log "inconclusive, recheck next run" instead of paging "Privacy leak risk" (the 23:54 D1 alert was exactly this; re-run now = 0 violations); (2) stack_watchdog: self-healed re-bootstraps/kickstarts are log-only, pages only when a job has NO auto-heal or combined_mirror (live $) stalls; com.quarki3.swarm removed from its job list (deliberately disabled per D25 — re-bootstrap churn was noise); (3) consumption manager: tier transitions page ONLY on entering RED, AMBER/GREEN changes are log+state only; (4) session governor: routine pause/resume pushes removed (log only), kept the protected-session-frozen P4. Net: of the 6 pushes in Robert's screenshot, 5 would not fire today; the D1 one now requires an actual non-403 answer to a guest.

- 2026-08-10 (improvement_bot cycle) — context_watcher.py's KNOWN_STATE_FILES
  allowlist was never updated when the congress mirror, autopilot mirror,
  sub5, lab333, turbo-arm, and edge-ledger/governor/risk-config state files
  were added; it had been re-flagging the same 17 filenames as "UNKNOWN
  STATE FILE" every 30min since 2026-08-03, appending 440 duplicate lines
  to this doc with zero new information each time. Fixed: added the 17
  filenames to the allowlist and added a dedup check so `append_findings`
  can only log a given filename once, not every tick, going forward. The
  440 historical spam lines were removed (nothing else in the file was
  touched — verified via diff). Backups:
  `scanner/sources/context_watcher.py.bak-20260810T0558`,
  `CONTEXT.md.bak-20260810T0558`. These 17 files are legitimate,
  actively-written state for existing systems (congress mirror,
  autopilot mirror, sub5, lab333, turbo arm) already covered elsewhere in
  this doc — no new system documentation needed.
- 2026-08-10 07:38 — UNKNOWN STATE FILE detected: `pt_shadow.jsonl` (add to CONTEXT.md)
- 2026-08-10 07:38 — UNKNOWN STATE FILE detected: `overnight_drift_state.json` (add to CONTEXT.md)
- 2026-08-10 08:11 — UNKNOWN STATE FILE detected: `dd_cap_shadow_state.json` (add to CONTEXT.md)
- 2026-08-10 09:17 — UNKNOWN STATE FILE detected: `linus_osint_state.json` (add to CONTEXT.md)
- 2026-08-10 (improvement_bot cycle) — turbo-arm entry leg found broken (no
  live-money impact): today's 08:15 `com.quarki3.turbo-entry` run crashed —
  `enter_picks()` calls `moonshot_screen.screen()`, which was never defined
  (`moonshot_screen.py` only has `main()`, a print-only prototype) — a
  wiring gap from the 2026-08-09 turbo-arm restore. Crash happens before any
  order call, so no orders fired today; exit legs (managing the live $32
  BYRN/LTBR positions) are a separate code path, unaffected — turbo-entry
  just silently no-ops until fixed. Per hard rails (live-order-flow file),
  the bot did not patch it — exact 5-line patch + verification steps are in
  `~/improvement_bot/proposals/2026-08-10_turbo_arm_moonshot_screen_missing_function.md`,
  P4 sent. Detail in improvement_bot/LOG.md 2026-08-10 (mid-morning).
- 2026-08-10 (improvement_bot cycle, afternoon) — context_watcher.py
  KNOWN_STATE_FILES allowlist follow-up: added pt_shadow.jsonl,
  overnight_drift_state.json, dd_cap_shadow_state.json, linus_osint_state.json
  (2026-08-09 six-lane rebuild files, confirmed legitimate/fresh) so
  scan_state_dir() stops flagging them as unknown. Backup
  scanner/sources/context_watcher.py.bak-20260810T1408. Verified via
  py_compile + live post-restart tick (orchestrator.log context_watcher ok,
  context_audit.jsonl unknown_state_files: []). Pure observability, no
  order/promotion logic touched. Detail in improvement_bot/LOG.md 2026-08-10
  (afternoon).
- 2026-08-10 (improvement_bot cycle, evening) — D41 (crypto sleeve graduation
  ladder: $50 → $110 at 30 clean trades → 35%-of-book at 60 → D34 amendment
  beyond) was announced adopted 2026-08-09 19:03 but never actually landed in
  DIRECTIVES.md — mtime shows it was clobbered by a concurrent edit in the
  same window (D40 landed instead). Restored verbatim from Claude memory
  (`feedback_crypto_graduation_d41.md`) with a CHECK line, backup
  `DIRECTIVES.md.bak-20260810T1810`. Checked `~/crypto_sleeve/state/closed_trades.csv`
  while in there: 16 trades, mean −0.64%/trade — nowhere near Gate #1, so
  this doesn't affect today's Alpaca→Kraken decision, it just makes the rule
  enforceable again. No code/order-flow path touched. Detail in
  improvement_bot/LOG.md 2026-08-10 (evening).
- 2026-08-10 (improvement_bot cycle, night) [research] — Senate eFD fetch
  path for Mullin_Tracker verified live end-to-end against
  efdsearch.senate.gov (agreement POST → DataTables AJAX search →
  per-filing HTML table parse; no PDF/decryption needed, simpler than the
  House Clerk pipeline). 39 real PTR filings found for Sen. Markwayne
  Mullin, sample filing parsed cleanly. Not wired yet — recipe + evidence
  in improvement_bot/BACKLOG.md item 3 for a future build. While verifying
  ground truth, found D24's claim "(2026-08-04: Senate eFD wired via
  congress_feed_senate.py)" was FALSE — that file never existed and
  Mullin_Tracker's own catalog row says idle/wiring-pending; same failure
  pattern as the D41 clobber found earlier today (claimed-but-never-landed
  directive text). Corrected D24's prose in DIRECTIVES.md (backup
  DIRECTIVES.md.bak-20260811T0416); no CHECK line or executable path
  affected. Full detail: improvement_bot/LOG.md 2026-08-10 (night) entry.
- 2026-08-11 (improvement_bot cycle, morning) — Directive/log truth-drift
  tripwire built (`~/improvement_bot/tools/directive_drift_check.py`,
  report-only, never edits DIRECTIVES.md) and run for the first time. Found
  3 real drift instances: (1) HIGH PRIORITY — `state/governor_config.json`
  (D19's live-governor adaptive-tempo config) doesn't exist and current
  `strategy_governor.py` has no adaptive/L1-gate/weight-power code at all,
  only a flat hardcoded sticky bonus — grep-level evidence only, flagged for
  a dedicated read-only investigation next cycle (borders live-capital
  allocation logic, not touched on a guess). (2) `fetch_congress_pilot.py`
  (Monday Crenshaw_Tracker refresh) has been silently no-op-failing weekly
  since it went missing sometime after 2026-08-05 — catalog-only, no live
  impact, fixable next cycle by reusing `congress_feed.py`'s parser. (3)
  `~/scanner/promote_pilot.py` (D19's explicit tournament-promotion
  command) is also missing — no active dependency, but would block a real
  promotion decision if one came up. Full evidence + verification in
  improvement_bot/LOG.md 2026-08-11 (morning) entry; new BACKLOG items
  13-16. No DIRECTIVES.md/CONTEXT.md text or live/order-flow code touched
  this cycle.
- 2026-08-11 (improvement_bot cycle, afternoon) — Investigated BACKLOG item
  13 (governor tempo drift) and found D34's 35% unproven-lane cap
  (`_cap_weights`/`governor_config.json`) is completely absent from the
  live `strategy_governor.py` — zero grep hits for the cap mechanism.
  Confirmed live: congress (still UNPROVEN, null D7 gate stats) currently
  holds 83.87% governor weight / 88.6% of the live $100-notional book,
  the exact scenario D34 was written to prevent (its own trigger was 99%
  congress concentration). Only the unrelated D6 single-name cap (INTC
  capped at $35, MAX_SINGLE_NAME_PCT) is limiting it further. Root cause:
  same failure class as the 2026-08-09 Mac-port losses already found this
  week (`fetch_congress_pilot.py`, `promote_pilot.py`) — this file has no
  git commits after 2026-07-27 (evolved live on the now-unreachable Oracle
  VM), so the port pulled a pre-D34 baseline. Per hard rails this bot did
  NOT edit the live-weights file; exact reimplementation patch + evidence
  written to `improvement_bot/proposals/
  2026-08-11_governor_d34_unproven_lane_cap_missing.md`, P4 sent. D19's
  separate "adaptive tempo" text is also stale (describes a superseded
  `vm/backtest_rules.py` variant, not what's running) but is a lower-
  urgency doc fix, left open. Full evidence: improvement_bot/LOG.md
  2026-08-11 (afternoon) entry.
- 2026-08-11 ~18:00 — **Terminal equity chart UX v2 (Robert's pan/zoom asks) + day-end ultra audit.**
  `trading-gui/src/components/EquityChart.tsx` + `ng_dashboard/private/owner_zoom.js`:
  (1) new 2W/3W range buttons; (2) range tabs now set the default VIEW WINDOW
  instead of filtering data — pan/scrub travels the full history at fixed zoom,
  no more wall at the window edge; (3) new scrubber bar under the plot (slides
  with the window; drag = pan at exactly the current zoom, tap = jump-center);
  (4) TWO-TIER pinch: slow pinch = fluid in-chart zoom (never flips the range
  tab), quick pinch = preset hops via owner_zoom detents (SPEED_MIN=1.15 log2/s
  gate, first hop needs ENGAGE=0.95 sweep, slow frames decay windup; each hop
  claims the gesture via window.__qtDetentUntil so the fluid zoom stands down).
  Built (`npm run build`, tsc clean) → served live immediately from
  ~/trading-gui/dist via /terminal (per-request reads, no restart needed);
  verified served shell references index-mjgYK3hr.js and /owner/zoom.js serves
  SPEED_MIN. Synced EquityChart.tsx + owner_zoom.js + dist into
  ~/lab333/upstream333 working tree → autopush publishes to
  robertpainter144-lab/-333-333-333- main within ~15 min. NOTE: ~/lab333/stack
  curated copies (EquityChart, combined_mirror) are stale vs live — mirror repo
  main is the fresh publication path. Day-end ultra audit (28 agents): 18
  confirmed findings incl. 4 NEW highs — D31 guest /api/qc leaks
  mirror_mode+mirror_targets (regressed vs 2026-08-05 fix, needs _QC_PRIVATE
  additions), turbo-entry launchd failing since 8/10 (moonshot_screen.screen()
  missing), D19 churn 85 orders today (fix re-applied by parallel session
  17:45, scanner restarted — D42 ratchet key now live in state), 891% pnl_pct
  display (deposit-aware baseline still unfixed). Details in session report.
- 2026-08-11 ~18:02 — **Two migration regressions FIXED live (same session as chart UX ship).**
  (1) D19 churn thresholds re-applied: combined_mirror.py MIN_ORDER_USD 1.00→2.00,
  REBALANCE_THRESHOLD_USD 0.50→2.50 (Robert's own 2026-08-03 values, lost in the
  VM→Mac migration; today ran 85 live orders / ~87% turnover incl. JPM
  buy-sell-buy). Scanner kickstarted 18:01, PID 70785, ticking, D42 ratchet
  state intact. (2) D31 guest leak closed: _QC_PRIVATE now also scrubs
  mirror_mode / mirror_targets / mirror_targets_raw / mirror_targets_ts
  (guests could pull the live book's exact composition via /api/qc since the
  8/9 Mac cutover). ng-dashboard kickstarted, :8080 owner rotated 47531→70800,
  verified: guest /api/qc has zero mirror keys, owner view unchanged,
  /terminal 200. Both fixes copied into ~/lab333/upstream333 for autopush.
  Still OPEN (not touched here): turbo-entry moonshot_screen.screen() failure,
  891% pnl_pct deposit baseline, D34 governor cap, sub5_watch wiring, SOXL
  launchd — proposals/evidence in improvement_bot + session audit.
- 2026-08-11 19:13 — UNKNOWN STATE FILE detected: `autopilot_catalog_curves.json` (add to CONTEXT.md)
- 2026-08-11 19:20 (improvement_bot cycle, evening) — **Rebuilt missing
  `sources/autopilot_catalog.py` (Autopilot catalog benchmark marking
  engine) — total loss, never committed, same 07-27→08-09 migration as
  everything else found this week.** Its absence meant
  `autopilot_catalog_curves.json` never existed on the Mac, which meant
  `catalog_leader_check.py` (daily launchd job, D24) silently reported "no
  active promotion" every day despite the live DBMF_Mirror+KMLM_Mirror
  promotion since 08-02 — the leader-comparison system has effectively been
  dark since the migration. Rebuilt the marking engine (base-NAV-100 virtual
  index per pilot, shares re-struck on hand-edited rebalances, public
  yfinance prices only — D4-clean, catalog-display-only, zero live-order
  path), registered in `main.py` `_SOURCE_SPECS` at 6h cadence (backup
  `main.py.bak-20260811T1920`), added the new state file to
  `context_watcher.py`'s allowlist. Verified: dry-run marked 16/17 pilots
  clean (1 skip: Crenshaw_Tracker's ticker USOU is delisted per yfinance,
  pre-existing data issue, correctly failed closed); scanner kickstarted
  (PID 75297), orchestrator log shows 20 sources (was 19), autopilot_catalog
  + combined_mirror + all others ticked clean; manually ran
  `catalog_leader_check.py` (read-only/idempotent) and confirmed it now
  correctly reports "promoted curve too young" (1/5 marks) instead of the
  previous silently-wrong "no active promotion" — full pipeline verified
  end-to-end. STILL OPEN (not touched, follow-ups filed):
  `edge_ledger.py`'s `autopilot_cat:<name>` row-surfacing is also missing
  (so `/api/catalog`'s metadata rows stay empty even though curve data now
  populates) — deliberately deferred rather than editing a shared central
  file the same night two other windows were mid-edit on adjacent live
  files; Crenshaw's own `fetch_congress_pilot.py` (separate, narrower gap)
  still missing too; ALLW_Mirror pilot (#23) still missing from
  `autopilot_catalog.json`. Also re-verified (read-only, no changes) the 3
  outstanding proposals from earlier today: D19 churn — confirmed landed
  (parallel session, 17:45); deposit-aware pnl_pct baseline — still broken,
  reads 892.46% right now; D34 governor cap — still absent. Full evidence:
  improvement_bot/LOG.md 2026-08-11 (evening) entry.
- 2026-08-11 23:18 — UNKNOWN STATE FILE detected: `edge_ledger.json.bak-20260811T2320` (add to CONTEXT.md)
- 2026-08-11 23:20 (improvement_bot cycle, night) — **Restored
  `edge_ledger.py`'s missing `autopilot_cat:<name>` benchmark-row surfacing**
  (BACKLOG item 18, deferred from the evening cycle to avoid colliding with
  two other windows mid-edit that night — both now idle). Added
  `_autopilot_catalog_rows()`: reads `state/autopilot_catalog.json` +
  `state/autopilot_catalog_curves.json`, emits one `mode="benchmark"` row per
  pilot (22 rows) into `edge_ledger.json`. D4-clean, catalog-display-only, no
  live-order path. Verified: dry-run correct, live tick + scanner restart
  clean (PID 75297→81476, `edge_ledger ok in 0.1s` first tick), D1 guest-leak
  check passed (`/api/edge` 0 leaked rows), `/api/catalog` now returns 22
  pilots with metadata (was 0) — closes the other half of the evening
  cycle's curve-engine fix (curve chart data was already working; now the
  per-pilot verdict/pnl rows are too). Backup:
  `scanner/sources/edge_ledger.py.bak-20260811T2320`. Full evidence:
  improvement_bot/LOG.md 2026-08-11 (night) entry.
- 2026-08-12 03:25 — UNKNOWN STATE FILE detected: `autopilot_catalog.json.bak-20260812T0330` (add to CONTEXT.md)
- 2026-08-12 03:30 (improvement_bot cycle, night/early) — **Rebuilt missing
  `~/scanner/fetch_congress_pilot.py`** (BACKLOG item 14) — total loss in
  the 2026-07-27→2026-08-09 migration, silently failed every Monday since
  (`catalog_leader_check.py:69`'s Crenshaw_Tracker refresh, caught by
  `capture_output=True`, non-fatal but never actually ran). Rebuilt reusing
  `sources/congress_feed.py`'s House Clerk PTR fetch/parse — D4-clean,
  catalog-display-only, zero live-order path. Verified against the exact
  Monday call path: dry-run reproduced the existing hand-transcribed value
  exactly (USOU 100%), live `--write` + idempotency re-run confirmed
  byte-identical against a pre-write backup, scanner restarted clean (20
  sources, PID 81476→87980). Also re-verified (read-only) the 4 outstanding
  P4 proposals from prior cycles — no change: deposit-aware pnl_pct baseline
  still broken (895.28%, drifting), D34 governor cap still absent, governor
  decision log still unwired, turbo-arm moonshot_screen.screen() still
  crashing (fresh traceback confirmed). None re-filed (Robert already has
  all four). Full evidence: improvement_bot/LOG.md 2026-08-12 (night/early)
  entry.
- 2026-08-12 07:48 — UNKNOWN STATE FILE detected: `earnvol_state.json` (add to CONTEXT.md)
- 2026-08-12 13:21 — UNKNOWN STATE FILE detected: `equity_social.json` (add to CONTEXT.md)
- 2026-08-12 13:21 — UNKNOWN STATE FILE detected: `tracked_accounts_grades.json` (add to CONTEXT.md)
- 2026-08-12 13:54 — UNKNOWN STATE FILE detected: `midcap_state.json` (add to CONTEXT.md)

## 2026-08-12 (afternoon) — qc profit-protection fixes + peak-capture + log rotation
- **qc_portfolio, 2 confirmed-high fixes deployed** (Mac primary; com.trading.qc +
  com.qcportfolio.ibkr kickstarted, both healthy). (1) `compute_target` now scales
  targets by `_state["exposure_level"]` so the DD ladder holds instead of the daily
  rebalance re-buying to 100% overnight. (2) `check_profit_ratchet` now uses account
  EQUITY (cash+positions) as basis (callers pass `total_equity=equity`) — selling a
  winner no longer spuriously dumps 50% of the book; still fires on real giveback.
  Both validated with deterministic tests. `portfolio_engine.py.bak-*` on the Mac.
- **Log rotation** shipped: `~/bin/rotate_logs.py` + `com.quarki3.log-rotate` (weekly
  Sun 03:00 UTC), tail-preserving/atomic, archives to `~/trading-data/log_archive/`;
  rotates signals.jsonl (22MB, unbounded), SKIPS gate ledgers (archive-append only).
  Tested on a copy; live file untouched.
- **Peak-capture (shadow, per D22)** under `~/peak_capture/` — non-invasive backtest +
  shadow evaluator for a whole-book trailing/scale-out exit; no live wiring.
- **Capacity analysis:** no more compute/storage needed. GCP idle w/ 24GB free; Mac disk
  95% is browser/user data, not the stack (~1GB). Oracle Always Free = best if outgrown.
- **DIRECTIVES.md:** D13 SUPERSEDED — Mac is PRIMARY (Oracle dead since 8/9); D14 notes
  Mac launchd labels + kickstart restart.
- Still open (other window's engine domain, not touched here): D19 live churn (45 orders
  vs 12 cap), D36 turbo-arm `moonshot_screen.screen()` crash, D34 cap unenforced,
  deposit-aware pnl_pct polluting governor telemetry. User-gated: sub-$5 reseed, key rotation.
- 2026-08-12 ~14:57 — **Deposit-aware P/L baseline re-applied (Robert's go) + turbo fix verified.**
  combined_mirror.py: new `_net_deposits_since()` (Alpaca CSD/CSW after
  baseline_captured_at, sign-robust, None→last-persisted fallback) +
  `_first_equity_ts()` bootstrap (full min() scan — equity.csv row 0 is NOT
  chronological, review finding) + adj_baseline P/L block + state fields.
  Adversarially reviewed (no defects; bonus: the fix RE-ARMS the governor's
  -8% kill switch, which the 891% reading had neutered — it now trips at
  ~$460 equity as designed). Scanner kickstarted post-close (PID 31138),
  verified live: pnl_pct -0.9%, net_deposits 450.0, baseline_captured_at
  2026-07-14, ratchet + D43 intact, /api/qc serving -0.9. Backup:
  combined_mirror.py.bak-plbaseline-20260812T1444. Turbo-arm: screen() fix
  (applied earlier today by parallel session) verified — compiles, returns
  71 scored rows (top VERI), com.quarki3.turbo-entry loaded for 08:15;
  entry leg unblocked, no manual entry run forced. Synced to lab mirror.
- 2026-08-12 15:08 — UNKNOWN STATE FILE detected: `governor_config.json` (add to CONTEXT.md)
- 2026-08-12 ~15:16 — **D34 unproven-lane cap re-applied (Robert's go).**
  strategy_governor.py: `_load_governor_config()` (hot-reload, fail-SAFE on
  missing/corrupt file, range-validates cap (0,1] and rejects non-list
  proven_lanes — both review-finding hardenings) + `_cap_weights()`
  water-filling, enforced in BOTH publish and republish branches BEFORE the
  L1 hysteresis gate. state/governor_config.json created: unproven_cap 0.35,
  proven_lanes [] (nothing D7-proven). Adversarial review: NO DEFECTS (2M+
  trial fuzz, converges ≤2 iters, no scope/rounding issues; combined_mirror
  renormalizes so sum drift is immaterial). First publish: qc 0.4875→0.35,
  congress 0.2094, patent 0.3116, reason "L1 0.275 ≥ 0.1". Scanner
  kickstarted (PID 33527), governor tick ok on new code, capped weights
  republished by the running engine; P/L fix (-0.9%) + ratchet + D43 all
  intact. Backup: strategy_governor.py.bak-d34cap-20260812T1500. Synced to
  lab mirror. D34's CHECK (every lane ≤0.36 unless proven) now PASSES.
- 2026-08-12 ~15:40 — **@tradergrader auto-ingest + GUI trade explanations
  shipped.** (1) equity_social.py gained `--leg x_fetch`: keyless
  @tradergrader X-timeline ingest (nitter RSS w/ instance fallback + X's own
  public syndication/embed endpoint for history; manual-only rule lifted by
  Robert today — one handle, read-only, once daily). 120 posts backfilled
  (2024-06-17→today) → x_posts_tradergrader.jsonl; parsed into
  x_auto_recs.json (Sniper-of-the-Day/Trade-of-the-Week callouts: option
  side→long/short on underlying, SPX→SPY-style proxies, claimed ROI kept in
  note) + scanner/state/tradergrader_leaderboard.json (14 traders, medal
  rollups). grade leg merges them as account `tradergrader_auto` + new h1
  horizon: first grades n=6, 1d win 60%, the one 20d-resolved rec −36.5% vs
  SPY. NOTE: his posts are retrospective recaps — grades test
  FOLLOW-the-highlight, not his entry timing. launchd
  com.quarki3.equity-social daily 15:35 MT (x_fetch+collect+grade), log
  trading-data/logs/equity-social.log. (2) ng_dashboard: the long-unwired
  /api/activity_detail is now live in the app — RECENT ACTIVITY rows expand
  (tap) into a why-panel (at-fill reason from ledger why-string + which
  lanes pick the symbol now + regime rails); POSITIONS rows' expand panel
  now also shows a holding story via new `mode=holding` (current governor
  target %, held-since, last recorded ledger action w/ reason). Owner-only
  (activity_detail stays out of _GUEST_PATHS; guests get a graceful
  fallback line). Verified live on :8080 with real JPM/TEVA/RDY fills; JS
  parses (node --check). Kotlin app inherits via WebView.
- 2026-08-12 15:40 — UNKNOWN STATE FILE detected: `tradergrader_leaderboard.json` (add to CONTEXT.md)
- 2026-08-12 ~15:50 — **TraderGrader owner card shipped (Grader tab).**
  ng_dashboard: new owner-only `/api/tradergrader` (leaderboard + grades
  JSONs; NOT in _GUEST_PATHS) + `private/owner_tradergrader.js` injected
  into the owner shell alongside the other bundles (public shell untouched
  — verified 0 injection with XFF). Terminal gains a 🎯 Grader nav button →
  overlay: auto-graded callouts table (1d/20d vs SPY, per-rec notes w/
  claimed ROI), hand-pasted recs section (when present), his medal
  leaderboard (top 15 traders, golds/medals/avg claimed ROI), honest
  recap-not-picks framing + ADVISORY pill. Gates verified: guest key 403 on
  api + bundle; owner/local 200. Android Terminal 333 inherits via WebView.
- 2026-08-12 ~15:55 — **Grader tab deployed to GCP quarki-stack + verified
  on the Flip4 via adb.** KEY FINDING: Terminal 333 v3.4
  (com.lab333.terminal) is the WEBVIEW app whose compiled URL is
  https://quarki-stack.tail33de20.ts.net/terminal/?key=… — the phone loads
  the Terminal from GCP, NOT the Mac, and GCP's ng_dashboard is a DIVERGED
  copy (own bundle set: owner_terminal_crypto/equity_intel; api.py base
  predates activity_detail). Mac-only dashboard deploys never reach the
  phone — future app-visible changes must also patch GCP. Today: additively
  patched GCP api.py (activity_detail incl. mode=holding, /api/tradergrader,
  /owner/tradergrader.js route, inject lines), copied webapp.html (base was
  identical pre-change), owner_tradergrader.js, and the two grader state
  JSONs; backups api.py.bak-grader-*/webapp.html.bak-grader-* on the VM;
  quarki-dashboard restarted, endpoints 200, injection verified. adb loop:
  force-stop→relaunch→tap → 🎯 Grader tab renders (6 recs, 60% 1d win,
  leaderboard) on the phone. NOTE: GCP grader state JSONs are hand-copied
  snapshots — the Mac timer regenerates them daily but nothing syncs them
  to GCP yet (queued follow-up).
- 2026-08-12 ~16:02 — **Grader tab → Catalog card (Robert's correction).**
  owner_tradergrader.js rewritten to the owner_crypto_intel.js pattern: no
  nav button/overlay; polls for #qtcat-overlay and inserts the TraderGrader
  card directly under the Catalog header (re-inserts on catalog re-renders).
  Deployed Mac + GCP, both dashboards restarted; adb-verified on the Flip4
  (nav back to 7 tabs, card renders first in Catalog). Synced to lab mirror.
- 2026-08-12 16:33 — UNKNOWN STATE FILE detected: `equity_social_history.jsonl` (add to CONTEXT.md)
- 2026-08-12 16:33 — UNKNOWN STATE FILE detected: `dip_buyer_state.json` (add to CONTEXT.md)
- 2026-08-12 ~16:35 — **Big evening ship (ultracode): Book-page explanations
  on the PHONE, robust scanner, dip-buyer benchmark, GCP repairs, meter
  recal.** (1) owner_explain.js (new owner bundle, both hosts): holdings
  rows on the Terminal Book page tap-expand a why-panel (activity_detail
  mode=holding), plus a TRADE HISTORY card below the holdings table
  (/api/activity, 25 fills, per-row caret expands reason+context) — anchor
  [data-qt-slot="book-alloc-top"] w/ header-text table fallback for older
  dists; adb-verified end-to-end on the Flip4 (screenshots: RDY holding
  panel, VTRS fill panel). (2) Robust scanner: equity_social collect leg
  gained Google News RSS, StockTwits trending, r/StockMarket+r/options,
  per-symbol trailing-history jsonl (2500/2000 rotation), composite buzz_z,
  HELD-symbol spike alerts (z≥2.5 + floors, P4 plain-English, 24h/z+1
  suppression, alert_state.json), market-hours guard + --force;
  com.quarki3.equity-social-intraday launchd every 30min 07:00-14:30 MT
  weekdays; live forced run verified (28 syms, spikes=[]). (3) DipBuyer_
  SPYQQQ catalog benchmark (Robert's ask; prior 8/3 backtest caveat in its
  blurb): ~/scanner/sources/dip_buyer.py virtual $10k SPY+QQQ, 25%-of-cash
  tranches on >10% dips from peak, never sells; scanner _SOURCE_SPECS 6h
  tick; excluded from catalog_leader_check; guest-invisible (mode
  benchmark); scanner kickstarted. (4) GCP repairs for the phone (app =
  ts.net → GCP): activities endpoint fixed (SDK had no
  get_account_activities → raw REST w/ quarki.env fallback), /Users shim
  confirmed, live-story preference patches (combined_mirror_ledger_live
  .jsonl / _state_live.json / governor_weights_live.json synced from Mac,
  endpoints prefer them for alpaca_live), catalog state pushed (pilots were
  empty), owner_catalog.js x-api-key fix both hosts (fixed phone Catalog
  403). Daily equity-social timer now scp's ledger+state+weights to GCP.
  (5) TraderGrader Catalog card now collapsed pilot-size, tap-expands
  (missing click handler fixed). (6) consumption_manager recalibrated:
  Robert's real /usage 5h was 45% when meter said 93% → five_hour budget
  117M→240M (calibration_note in config.json); memory: meter is not
  authoritative, only real 429s throttle explicit asks. Mega-review
  workflow (9 find dims → 3-vote verify → fixers) ran same evening — see
  next entry.
- 2026-08-12 ~16:43 — **Phone Catalog pilots restored (never a tradergrader
  regression).** GCP's trimmed edge_ledger.py never had
  _autopilot_catalog_rows → pilots structurally 0 on the phone. Ported the
  Mac's function (backup edge_ledger.py.bak-catrows-*), manual tick →
  23 pilots on GCP /api/catalog; quarki-scanner restarted so its ticks keep
  them; autopilot_catalog.json+curves added to the daily Mac→GCP scp leg.
  adb-verified: full shelf renders on the Flip4 (Burry/Point72/InTheMoney/
  ... + collapsed TraderGrader card + DipBuyer row).
- 2026-08-12 ~16:47 — **TraderGrader = standard catalog row (Robert's 3rd
  iteration).** equity_social grade leg now publishes a real catalog row:
  base-100 curve of FOLLOWING each callout's 1d return (compounded, carry-
  forward mark) into autopilot_catalog_curves.json + a metadata-only
  pilots["TraderGrader"] entry (_blurb w/ live stats + podium, _note w/
  recent recs + medal board for the data-center drawer). edge_ledger picks
  it up like any pilot (Mac +7.02% total; card shows 1y-window +3.39%).
  owner_tradergrader.js injection REMOVED from both hosts (bundle+endpoint
  kept, unused); catalog+curves in the daily GCP scp. adb-verified: card
  chrome identical to Burry/Point72 on the Flip4.
- 2026-08-12 ~17:00 — **Mega-review (72 agents) verdict + fixes.** 20 raw
  findings → 18 confirmed by 3-vote panels → 4 auto-fixed in
  equity_social.py (CRITICAL: quarki_notify called with bogus
  --priority/--title flags — its real CLI is positional `level title msg`,
  levels critical/action/info/routine; HIGH: _TRADE_RE gated on
  Sniper/TOTW match so recap posts can't misparse; HIGH: SPX→SPY-proxied
  recs no longer benchmark against SPY itself (excess=None); MED:
  retraction/anomaly posts skipped). Main-session fixes applied same hour:
  daily plist collect leg got --force (15:35 run was silently no-op'd by
  the new market-hours guard); GCP scp legs now log failures instead of
  2>/dev/null; api.py mode=holding dates now America/Denver tz-aware +
  missing-ledger fallback line restored (both hosts, one batched restart).
  NOTE: today's earlier "P2" ship pushes were garbled by the same notify
  misuse (titled "2") — future calls must be positional. Catalog top card
  now wears a 🏆 WINNER pill (owner_catalog.js both hosts). Report-only
  debt logged: GCP scanner/trading-gui tree frozen at the 8/9 migration
  snapshot (missing all Mac safety fixes since — biggest open risk);
  peak-capture shadow only ticks 1x/day at close so it can't see intraday
  give-backs; six auto-flagged UNKNOWN STATE FILE entries (today's new
  state files: dip_buyer_state, equity_social_history, alert_state,
  x_posts_tradergrader, tradergrader_leaderboard, governor_config) are
  hereby documented as known/intentional. D44 added to DIRECTIVES.md
  (tradergrader X-ingest exception). Review fixer for bundles was
  safety-blocked before touching anything (wanted to consider deleting a
  GCP tree — nothing deleted); one verify agent hit its retry cap.
- 2026-08-12 ~17:58 — **TraderGrader: wayback backfill running + paper lane
  live (Robert's go; deep-history verdict was NEGATIVE — n=3, 33% 1d win,
  100% leaderboard churn, one self-retracted ROI; all three judge lenses
  said don't allocate).** (1) ~/equity_social/wayback_backfill.py: nohup
  PID 73996, 3,899 archived tweets via web.archive.org snapshot JSONs
  (4 threads, ~14h ETA, checkpointed/resumable at wayback_checkpoint.json,
  log trading-data/logs/wayback-backfill.log; re-parses + regrades on
  completion — the catalog row and grades will re-state themselves from
  the full 2024-2026 history). (2) tradergrader_paper.py: virtual $50
  follow-the-highlight legs (1-day hold at closes) per new callout →
  D7-scoreable ~/scanner/state/tradergrader_paper_ledger.jsonl (midcap
  schema, theme tradergrader_follow); first run opened 4 / closed 3 legs.
  PROMOTION RULES UNCHANGED per Robert: zero live/governor weight, same D7
  bar (n≥30, mean≥0.05%, Sharpe≥1, late≥0.02%), D34 cap applies, catalog
  row stays display-only. Wired into the daily 15:35 chain after grade.
- 2026-08-13 07:45 — **Book-page hero cards evened out (Robert's ask).**
  The Alpaca and Kraken portfolio-value sections above the equity chart
  rendered lopsided on the Flip4: 1.25fr/1fr column split, Alpaca card in
  borderless mobile-bleed vs Kraken framed card, Kraken delta lines
  clipping off the right edge ("-$4.81 (-15.81%" cut), and the Alpaca
  header wrapping to two lines so the big numbers sat unlevel. All fixed
  in ng_dashboard/private/owner_crypto_account.js (owner-only bundle;
  guests unaffected): equal 1fr/1fr columns, Kraken card chrome now
  mirrors the SPA Card primitive (uppercase text-xs header, p-3 body,
  text-[9px] gauge labels), whitespace-nowrap dropped from delta lines,
  and a new injected `qt-even-heroes` stylesheet re-frames the Alpaca
  hero inside the two-column row (no negative margins, var(--bd) border,
  restored px, one-line ellipsized title + nowrap timestamp). Verified
  via adb screencap on the Flip4 — twins now identical in width, frame,
  and number alignment. Deployed to Mac AND GCP quarki-stack
  (~/ng_dashboard/private/, md5-verified); served per-request with
  no-cache, so no dashboard restart was needed on either host.
- 2026-08-13 07:58 — **Book equity chart defaults to 12H (Robert's ask).**
  EquityChart grew a `defaultRange` prop (mount-time tab, still "ALL" when
  unset — Lab page unchanged) and Book.tsx passes defaultRange="12H".
  Rebuilt trading-gui (assets index-C6nUVjtv.js) and rsynced dist/ to GCP
  quarki-stack; verified via adb screencap — 12H chip active on launch,
  window spans the trailing 12 hours. No dashboard restart needed
  (static served per-request).
- 2026-08-13 08:18 — UNKNOWN STATE FILE detected: `moonshot_state.json` (add to CONTEXT.md)
- 2026-08-13 09:05 — **D42 first live fire, audited.** Ratchet flattened all
  18 positions 08:22:06 MDT at book $449.98 (trigger $449.79 = contributed
  500.04 − 50 turbo carve − 0.25); $455.79 banked, slippage −$0.10. Robert
  challenged the exit; audit vs live marks (Alpaca IEX + Yahoo agree): by
  08:54 holding would be ~$1 WORSE than the cash — exit validated, Robert
  concurred (INTC pump captured). Standing audit tool:
  `~/bin/ratchet_hold_vs_exit.py` (raw fills + dual price sources, read-only).
  Rebuy waits on basket ≤ $453.61 or ~5td timeout.
- 2026-08-13 09:05 — **NYSE Scan paper lane shipped (Robert's ask: "scan the
  entire NYSE... mean reversion and any other ideas").** `~/nyse_scan/
  nyse_scan.py`: 2,413-symbol NYSE universe (Alpaca assets, weekly refresh),
  three D7-scoreable themes — nyse_meanrev (RSI<30 + >SMA100), nyse_pullback
  (uptrend, 5d ≤ −3%, RSI 30-45), nyse_breakout (fresh 55d high, stacked
  SMAs) — 5×$15 paper slots each, midcap-lane rails (PDT guard, cooldown,
  spend cap, P2 notify). Ledger `nyse_scan_ledger.jsonl`. launchd
  com.quarki3.nyse-daily M-F 13:50 MT + nyse-universe Sun. Full dry scan
  validated (2,350 scanned, 6 pullback candidates); breakout 55d-high
  reference excludes current bar (self-caught bug). Zero live weight until
  D7 gate. Opportunity-deep-dive workflow (inventory dangling feeds + 4
  ideation lenses + adversarial judges) launched same session.
- 2026-08-13 09:35 — **News Harvest shipped (D46).** `~/news_harvest/
  news_harvest.py` hourly launchd (com.quarki3.news-harvest): GDELT +
  Google News RSS + Yahoo ticker RSS + Reddit JSON + StockTwits trending +
  EDGAR full-text, keyless/ToS-clean, X excluded per D44. Ticker mention
  velocity + tone → `scanner/state/news_harvest.json`; watchlist
  (`~/news_harvest/watchlist.json`, Robert-editable) seeded with dad's
  names: PEW (GrabAGun/ATF-rule catalyst) + Greenland cluster CRML/UUUU/
  USAR/ALOY/GLND + DJT. First runs: CRML 82 mentions (+7% pre-market on
  Tanbreez update), PEW earnings watch. Spike pages P4 only after baseline
  exists (first-sighting seeds silently). Advisory-only, no orders.
- 2026-08-13 09:40 — **overnight_drift units bug fixed** (found by
  opportunity-deep-dive workflow): pl_pct was a raw fraction, 100x smaller
  than every other ledger's %-point units — would have made the stack's
  BEST lane (mean +0.52%/trade, 6/6 wins, Sharpe 1.56, n=6) look like a
  100x D7 failure to any future gate-checker. sources/overnight_drift.py
  now emits %-points; all 6 ledger rows migrated x100 (backup
  overnight_drift_ledger.jsonl.bak-units-20260813). Other workflow
  findings (prop-desk dead at n=758 mean −0.35%; whale risk_on 81h stale
  on VM; intel engine unconsumed; Senate eFD fetcher built 8/12 but
  unwired; D22 shadow at 64/150) reported to Robert — decisions pending.
- 2026-08-13 09:45 — **NYSE Scan first live-paper entries** (manual early
  fire on Robert's "trade within 10 minutes"; 13:50 MT timer still active):
  5 pullback (PFGC HIG CRS LUV PGR) + 5 breakout (P U RBRK DELL MHK),
  $15 each, paper.
- 2026-08-13 09:52 — **Turbo hand-add: CRML** (live $50 sleeve, D36).
  Robert ran ~/bin/turbo_buy_crml.py himself (Claude's live order was
  classifier-blocked; script staged instead): 2.281973 sh @ $6.568 =
  $14.99, sleeve open cost $46.97/$50. Thesis: dad's Greenland rare-earth
  play — CRML holds 92.5% of Tanbreez, US admin mulling equity stake
  (Reuters 8/12); chase-risk (+7% entry day) flagged twice before buy.
  Leg registered in turbo_arm_state.json (theme greenland_ree), managed by
  com.quarki3.turbo-exits (−12/+25/10td). PEW deliberately skipped
  pre-earnings; harvester watches it (D46).
- 2026-08-13 10:00 — **Insider Lane shipped** (Robert: "build the insider
  lane"; top 2 workflow survivors). `~/insider_lane/insider_lane.py`:
  Form 4 cluster-buys (≥2 distinct insiders, ≥$25k/5td, code P, 10b5-1
  excluded) + fresh SC 13Ds, from EDGAR daily index + full-text (fair-
  access UA, 4 req/s). Two legs: insider-scan 15:30 MT queues signals,
  insider-trade 07:35 MT enters ($15 paper, 5 slots/theme) + exits
  (−8/+15, 15/20td). Ledger insider_ledger.jsonl (D7). Smoke: 120 filings
  → 17 qualified buys → cluster AVBC. efts date-params quirk worked
  around (undated query + client-side file_date filter). **D47 recorded**:
  Robert's PT philosophy — per-position profit-taking + redeploy freed
  cash; whole-book flattens reserved for D42 floor defense; he confirmed
  the INTC-pump→18-position-liquidation coupling critique. LTBR fact-check:
  no LTBR buy today (only governor drift buys 07:34-39 + Robert's CRML);
  LTBR held since 8/6.
- 2026-08-13 13:27 — **Tracking-gap stat shipped (Robert's ask).** New
  /api/trackgap (both hosts): half-L1 distance between actual mirror
  weights and the capped governor blend + top drift contributors;
  owner_trackgap.js injects the stat under Positions & Allocation Drift
  (7th owner bundle, both inject lists). GCP reads the Mac-pushed
  combined_mirror_state_live.json (added to the 5-min crypto-state-push;
  _live suffix so the paper observer never fights the file; post-cutover
  it reads local). Verified identical on both hosts: gap 10.2% ≈ $45.80
  of $449 (JPM +3.8 over target, VTRS/TEVA/RDY ~−2.3 under, cash +2).
  Framing in-app: "gap is the cost-damper working, not error."
- 2026-08-13 13:20 — **D48 migration: Robert's GO received; Phase 0
  started.** Scanner drift committed (baseline 65e00a5, 24 files:
  7/27→8/13 incl. all of today); sources+main.py synced to GCP lab-stack
  (combined_mirror.py md5 parity a20f1a49… verified both hosts);
  quarki-scanner (paper observer) restarted on the baseline. SOAK DAY 1
  = 2026-08-13; target ~5 trading days → cutover (broker key rotation =
  the fence) circa 2026-08-20, before Robert's flight. Cutover day needs
  ROBERT'S HANDS for the key rotations (Alpaca dashboard + Kraken UI).
  Open questions in D48_MIGRATION_PLAN.md defaulted per recommendation
  unless he says otherwise: IBKR stays Mac-dormant, mobile-sshd retired
  (GCP path primary), Kraken rotation at cutover, small shadows follow
  later.
- 2026-08-13 12:50 — **D48 migration design complete → plan awaiting
  Robert's go at trading-stack/D48_MIGRATION_PLAN.md.** 9-agent workflow:
  inventories (62 plists mapped, 3 live-money surfaces, watchdog blind
  spot on turbo/crypto found), 3 competing designs, adversarial attacks.
  ALL THREE designs failed review (2-2.5/10) — convergent lesson: every
  two-brain failover is a dual-execution factory; process-level guards
  are reversible. Synthesis = full cutover hardened by **broker-side key
  rotation as the fence** (rotate Alpaca+Kraken at cutover, new keys only
  on GCP, old revoked — Mac physically cannot trade after), + paper soak
  week, one-time state migration w/ broker-API reconciliation, UTC-pinned
  systemd twins, host guards in turbo/sleeve code, Mac env neutering.
  4 open questions for Robert in the plan. NOTHING built yet.
- 2026-08-13 12:15 — **Yellow-line accuracy fixed (both hosts).** Root
  cause: the Terminal SPA always fetches /api/equity/{src}?period=5Y and
  windows client-side, while _equity_alpaca_live served only Alpaca
  portfolio-history DAILY bars → intraday chips drew a 2-point straight
  line. Fix: serve both horizons in one series — dailies before a 5-day
  cutoff + the 5-min equity.csv spliced over the trailing days (Mac
  api.py + GCP api.py, both compiled, one restart each). Verified 1,022
  points w/ 135 in the last 12h; app screencap shows true intraday
  structure (ratchet spike + re-entry dip visible). Also completed:
  session-verify-sweep workflow (5 agents, reverse-chron) — all
  live-money configs PASS (D22 live w/ $5 floor, D42 off + params
  restored, tb-fix loaded in running proc, turbo CRML leg, 15 fresh
  positions), all lanes/timers PASS, docs PASS; flags: new D43/D45/D46/
  D47/D48 CHECK lines not yet wired into directive_auditor (tonight's
  audit may false-page once on today's legitimate ~200% ratchet-cycle
  turnover — benign), equity_social Reddit leg degraded behind Nord
  (expected), Mac dashboard restart tracebacks were transient.
- 2026-08-13 12:08 — **Crypto card/tab/chart-line restored on GCP (parity
  complete).** Ported /api/crypto/summary + /api/crypto/ticker from Mac
  api.py to GCP (ticker = public-Kraken passthrough, portable; summary
  reads crypto_sleeve state). New com.quarki3.crypto-state-push (Mac
  launchd, 5-min ssh-pipe of equity.csv/sleeve_state/whale_signals/
  config/log-tail Mac→GCP — sleeve trades from the Mac, so Mac-off means
  last-known values w/ honest timestamps). Re-injected crypto.js +
  crypto-account.js → full 6-bundle owner shell on GCP. Verified: both
  endpoints 200 (1,074 equity rows, state+whale fresh), app screencap
  shows TOTAL hero, dual-line chart, Crypto tab. Earlier blank-screen
  root causes on record: (a) phone Tailscale dropped (ExpressVPN churn
  took the Android VPN slot; always-on VPN recommended), (b) WebView
  never reloads on resume (force-stop cycle required). App now fully
  Mac-independent on http://100.94.213.49:8080.
  Robert moved the app off the Mac path (base URL → GCP tailnet
  100.94.213.49:8080) and lost the dual Alpaca+crypto chart, crypto hero
  card, and chart granularity. Root causes: GCP's /terminal/ shell
  injected only 3 owner bundles (catalog/terminal-crypto/explain) vs the
  Mac's 6; /Users shim on GCP was EMPTY so equity.csv resolved to nothing.
  Fixed: 7 owner_*.js synced (incl. zoom + crypto_account), api.py inject
  lists + 3 routes ported (bak: api.py.bak-parity-20260813), equity.csv
  backfilled (2,704 Mac rows), NEW quarki-equity-log.timer on GCP (5-min
  native Alpaca marks — no Mac dependency), ONE dashboard restart,
  verified: all 6 bundles in served shell, owner routes 200, csv
  continuous (…499.22→499.28). React dist already identical (md5 match).
  OPEN DEBT: api.py has two-way divergence — GCP carries a 5D period-map fix
  + combined_mirror_ledger_live.jsonl preference the Mac LACKS; full
  convergence (ideally via lab333 canonical source) still to do.
- 2026-08-13 10:55 — **Adaptive-PT round 1 RETRACTED by adversarial audit;
  corrected rerun FLIPS the verdict.** Sonnet auditor: same-bar
  high-before-low sequencing (tight variants harvested impossible
  same-bar fills), sigma20 same-day lookahead (sabotaged spike_adapt's
  own speed classifier), FIFO pre-window blind spot — all biased toward
  flat/tight. Fixed all three (conservative ordering, prior-day-only
  sigma, fills from 6/1), rerun: flat_050 −$5.48 / flat_035 −$5.28 /
  flat_100 −$6.43 / vol_k075 +$0.58 / **spike_adapt +$0.55 with 86 vs 221
  fires**. Caveats: fixed ordering is worst-case (truth between runs),
  deltas small; the dead VM harness behind the 8/5 +$6.62 (which
  justified today's D22 live flip) likely shared the optimistic bias.
  Decisions: flat-D22 STAYS live only because the nightly pt-tripwire
  bounds it with REAL-fire evidence (auto-revert at −$3); Robert's
  adaptive rule shipped as SHADOW — ~/pt_adaptive/shadow.py, 15-min
  launchd com.quarki3.pt-adaptive-shadow →
  scanner/state/pt_adaptive_shadow.jsonl (conservative prior-peak
  semantics). Compare vs pt_live/pt_shadow at ~20 events (~2wk).
- 2026-08-13 10:48 — **NASDAQ added to the D45 scanner** (Robert's word;
  surfaced while explaining NVDA's 1.3% weight). Universe 2,413 → 7,964
  (NYSE+NASDAQ), sanity floor 1,000 → 3,000, daily timer 13:50 → 13:40 MT
  (~8-min scan must finish before the close). NVDA/AAPL/META-class names
  now visible to the lane; first NASDAQ-inclusive run today 13:40 — the
  meanrev theme has 5 open slots for it.
- 2026-08-13 10:45 — **Adaptive-PT round 1 (Robert's idea: vol-scaled
  leeway — grinders loose, spikes tight).** Rebuilt the dead Oracle
  pt_backtest harness lean at ~/pt_adaptive/backtest.py (503 real fills →
  459 FIFO episodes, 34 syms, hourly IEX bars, 7/13→8/13). Verdict:
  flat_050 (live D22) ≈ $0.00; flat_035 +$0.47 (noise); flat_100 −$2.64;
  vol_k075 −$3.99; spike_adapt −$4.14 — every step looser loses on this
  choppy window; vol-scaling mostly WIDENS givebacks. Decision: NO change
  to live D22; idea filed armed — rerun the harness when a window
  contains a sustained trend. Sonnet adversarial audit of the harness
  methodology launched (same-bar sequencing bias is the main suspect).
- 2026-08-13 10:35 — **D42 DISARMED (mode=off)** on Robert's instruction
  ("make sure we don't exit anything unnecessarily... there's no trigger
  line, just optimizing for profit at all times"). Root cause of urgency:
  the 10:26 re-entry put the book (~$454) permanently ABOVE the $449.79
  breakeven trigger, so live mode would have re-flattened the whole book
  at tomorrow's 24h cycle-guard expiry and every ~24h after — churn, not
  capture. Successor regime: D22 live per-position PT + governor rotation
  (partial trims only) + turbo/DD rails. Re-arm recipe in the config
  off_note (only as floor defense with the book BELOW the trigger).
  Peak-capture shadow (trailing high-water, no anchor) continues
  accumulating as the future profit-anchored upgrade.
- 2026-08-13 10:30 — **D42×D43 latent bug found+fixed on Robert's
  repopulate order.** Sequence: Robert ordered immediate repopulation;
  dip-path re-entry correctly refused (governor rotated MO/NFLX in, META/
  MU/ZTS out — targets⊄basket); timeout path armed via
  ~/bin/ratchet_reenter_v2.py (Robert-run; Claude's config edits + orders
  classifier-blocked throughout, scripts staged instead). Then the REAL
  bug: D43 turnover budget counted the morning's $455 flatten sells as
  spent (cap ~$90/day) and its gate exempted ratchet sells but NOT
  re-entry buys → re-entry would have trickled ~20%/day for a week.
  One-line fix in combined_mirror.py (`not _ratchet_reentering` in budget
  gate; backup .bak-tbfix-20260813; py_compile clean); scanner
  kickstarted by Robert (KeepAlive daemon, exec model — code loads only
  on restart). D43 CHECK amended with cycle-day carve-out so the auditor
  doesn't false-page tonight. Watcher restores rebuy_dip_pct 0.5 +
  max_flat_td 5 post-fill.
- 2026-08-13 10:02 — **D22 FLIPPED LIVE** on Robert's explicit override
  (64/150 shadow events; retest +$6.62, CI included 0 — flagged twice).
  Mac risk_config profit_take.mode="live" (hot-read, provenance field in
  config); live branch = per-position sell + 24h rebuy suppression +
  pt_live.jsonl logging; pt-tripwire timer already loaded. Book was flat
  at flip — first live PT fires after the D42 rebuy (basket was $0.51
  above trigger at 09:55). VM has no risk_config → its combined_mirror
  copy defaults to shadow (no dual-execution risk; port flag if GCP ever
  takes live execution). D22 amendment recorded in DIRECTIVES under D47.
- 2026-08-13 09:32 — UNKNOWN STATE FILE detected: `nyse_scan_state.json` (add to CONTEXT.md)
- 2026-08-13 09:32 — UNKNOWN STATE FILE detected: `overnight_drift_ledger.jsonl.bak-units-20260813` (add to CONTEXT.md)
- 2026-08-13 09:32 — UNKNOWN STATE FILE detected: `news_harvest.json` (add to CONTEXT.md)
- 2026-08-13 09:32 — UNKNOWN STATE FILE detected: `news_harvest_history.jsonl` (add to CONTEXT.md)
- 2026-08-13 10:04 — UNKNOWN STATE FILE detected: `insider_state.json` (add to CONTEXT.md)
- 2026-08-14 07:53 — UNKNOWN STATE FILE detected: `pt_live.jsonl` (add to CONTEXT.md)

## 2026-08-14 (~13:10) — D43 turnover budget: partial-fill fix (deadlock found live)

Robert asked why the 55.8% tracking gap wasn't closing. Root cause: the D43
budget gate was all-or-nothing — any discretionary order with |delta| >
remaining daily budget was deferred WHOLE. The JPM trim (-$126, 28% of book vs
7.14% target) exceeded the full $90/day cap, so it deferred every day forever,
and the INTC (+$72) / TEVA (+$60) buys behind it starved for cash (~$5 free) —
a permanent deadlock, not pacing. Compounding: budget-EXEMPT sells still count
toward `spent` (today $143/$90 by 09:02, 273 skips), which is unchanged for
now — only the clamp shipped.

Fix (combined_mirror.py budget gate): clamp discretionary orders to the day's
remaining budget (`tb_remaining`) and submit the partial, mirroring the buy
path's `min(delta, spendable)`; defer only when remaining < MIN_ORDER_USD.
Expected Monday 8/17 open: ~$90 JPM trim fills, remainder next day; buys
follow as proceeds settle. Deployed: Mac scanner (kickstart, verified tick) +
GCP twin synced (was otherwise byte-identical ex-comments; paper mode there so
tb_cap inactive; backup combined_mirror.py.bak-20260814, systemd restart OK).
DIRECTIVES D43 amendment appended. Note for D15 auditor: on clamp days the
ledger shows a partial trim, not the full target move.

## 2026-08-14 (~13:12) — D43 part 2: exempt flow stops charging the budget

Follow-on to the 13:10 partial-fill fix, per Robert. Exempt orders (full
exits on target drops, PT-claimed sells, ratchet cycle) no longer increment
turnover `spent` — only discretionary flow the gate can actually block does
(new `_tb_counted` guard at both `spent +=` sites in combined_mirror.py).
Rationale: this morning exempt sells alone consumed $143 of the $90 cap by
09:02, deferring every rebalance order all day — the budget was throttling
flow it wasn't allowed to block. Today's inflated spent ($147) left alone
(deliberate: don't hand-edit the risk rail's state; market closes 14:00 MT
and Monday resets clean). Deployed Mac (kickstart, tick verified) + GCP twin
(synced, systemd restart, combined_mirror ok; morning .bak-20260814 backup
still = pre-today original). D43 amendment extended in DIRECTIVES.md incl.
auditor carve-out: exclude ALL exempt rows from the ≤1.3×-cap daily sum.

## 2026-08-14 (~13:35) — One-shot Monday-open rebalance check installed

Robert asked to "reset spent again Monday" — not needed: the D43 budget
self-resets on the day-key change, so Monday opens with the full ~$90 fresh.
Instead installed com.quarki3.monday-rebalance-check (launchd, Mon 07:50 MT,
script ~/bin/monday_rebalance_check.py): verifies the deferred TEVA/INTC
buys started on 2026-08-17; P4 page only if scanner stale or spend still $0
(incl. settled-BP hint since Friday's JPM proceeds settle Monday); quiet P2
confirmation otherwise; date-guarded and fully self-removing (plist + bootout)
after one run. Needs the Mac awake — same precondition as the buys themselves.

## 2026-08-14 (~13:38) — GCP twin for the Monday-open check (D48)

Robert asked if the Monday check existed on GCP — it was Mac-only (reads
Mac-local state). Gap: Mac asleep Monday = no buys AND no page. Added GCP
twin quarki-monday-check.timer (one-shot, 2026-08-17 13:55 UTC = 07:55 MT,
5 min after the Mac check): ~/bin/monday_rebalance_check_gcp.py queries the
Alpaca LIVE API directly for orders since the 13:30Z open — works even with
the Mac dark. P4 page only if zero orders (message distinguishes accepted
Mac-off gap vs stuck buys); silent on success (Mac side already confirms
quietly). Date-guarded, disables its own timer after firing. Dry-ran the
API path from the VM: HTTP OK (saw today's JPM/NFLX/TEVA fills).

## 2026-08-14 (~13:42) — tradergrader wayback backfill moved to GCP (D48)

Per Robert. The Mac's one-shot resumable backfill (8/12 job) was STALLED at
2,270/4,020 with no process running (session that launched it ended). Synced
job + state (wayback_backfill.py, equity_social.py, tracked_accounts.json,
x_posts jsonl, checkpoint, CDX cache) to GCP ~/lab-stack/equity_social/ and
installed quarki-wayback-backfill.service (systemd, Restart=on-failure,
exits when done). Resumed correctly (todo=1629) and fetches fast from the
VM (+20/min first flush vs ~40-60s/piece on Mac) — ETA ~2h. On completion
it runs the x_fetch+grade legs GCP-side (fails soft if deps missing).
OPEN: when finished, pull the enriched x_posts_tradergrader.jsonl back to
the Mac and regrade there so the phone Grader card picks up full history;
do NOT rerun the backfill on the Mac (dedup makes it safe but it wastes
archive.org quota). D44 scope unchanged: read-only, one handle, archive.org
only. The tradergrader D7 paper lane stays Mac-only (no double-trading).

## 2026-08-14 (~13:48) — wayback backfill hardened: retry-until-done wrapper

First GCP flushes showed the real failure mode: wayback 429-bursts fail
~half the fetches, and transient fails are NOT retried within a pass — a
single pass would exit "success" with hundreds unfetched and would have
fired a false "finished" ping. Fixes: (1) wayback_backfill.py honors
QUARKI_SKIP_POSTRUN=1 (skip the x_fetch/grade legs — D44 once-daily cap —
so multi-pass looping doesn't hammer the handle); (2) GCP wrapper
~/bin/wayback_until_done.sh loops passes (max 40, 5-min gaps) until todo=0,
independent todo count cross-checked against the script's own (matched:
1,569), stall detection (3 no-progress passes → honest "stalled" ping with
remainder count), grade legs + "finished" ping only at true completion.
Pings moved out of ExecStopPost into the wrapper so "finished" is never
claimed early. Robert gets the ntfy ping either way; session monitor armed
to pull the jsonl back to the Mac + regrade when it lands.

## 2026-08-14 (~15:25) — Mac-side backfill pull automated

Per Robert ("pull the jsonl back when it finishes") — now deterministic,
not session-dependent: com.quarki3.backfill-pull (launchd, 30-min) polls
GCP for TRUE completion (service inactive AND todo==0 via the same count
the wrapper uses), then scp-pulls x_posts_tradergrader.jsonl, merges
dedup-by-id (Mac entries win — daily x_fetch may be fresher), atomic
write, runs --leg grade, quiet P2 confirmation, self-removes. Failure →
P4 with by-hand path; GCP unreachable → silently retries next interval;
7-day self-expiry with page so it can't orphan. Dry-ran the completion
check live: (active, todo=988) → correctly stays armed. Progress at
15:20: store 3,037/4,020, pass 1 still running, ~500 transient 429s
queued for later passes.

## 2026-08-14 (~18:35) — Wayback backfill COMPLETE end-to-end; grader on full history

Backfill finished on GCP at 18:22 MT: 7 passes, todo=0, store 4,025 posts
(4,028 on Mac after merge). Wrapper's completion legs on GCP graded
DEGRADED (no ALPACA data keys on VM → 401 on every bars fetch → junk
TraderGrader catalog row nav=100/points=1 in GCP state = what the phone
shows). Recovery, all same evening: (1) ran the Mac pull manually WITH
~/quarki.env sourced (the launchd pull job would have hit the same 401
trap — it never fired and self-removed cleanly); (2) fixed latent
equity_social.py grade() crash exposed by full history (best/worst max()
over rows with excess_pct=None — now filtered); (3) Mac x_auto_recs was
94 recs vs GCP's 172 (parse runs in x_fetch, which only GCP had run on
the full store) — pulled GCP's rec file, merged +1 Mac-only rec (SPY
8/13), 173 total, regraded; (4) pushed corrected grader+catalog files to
GCP via the daily timer's exact scp list; verified on GCP: curve 153
points, n=173. equity_social.py fix synced to GCP copy.

FULL-HISTORY VERDICT (n=173, resolved 173@1d / 154@20d): 1d win 52.0%,
20d win 43.5%, avg 20d excess −0.30% vs SPY; catalog nav 147.31.
Softer than the n=4 deep-history read but same direction: follow-the-
highlight has no 20d edge; 1d-compounded curve looks fine (nav 147) —
classic short-horizon momentum that decays by 20d. D7 bar unchanged;
paper lane keeps deciding.

## 2026-08-14 (~20:17) — App outage postmortem: NordVPN vs Tailscale

App "down" 3h traced to two stacked causes, both now understood:
(1) PHONE: Nord ON displaced Tailscale (Android single-VPN slot); turning
Nord OFF does NOT re-enable Tailscale — must manually reconnect in the
app; then the Terminal WebView shows the dead page until FORCE-CLOSE +
relaunch (known no-reload-on-resume quirk). (2) MAC: Nord internally uses
100.64.0.0/10 — the same CGNAT range as Tailscale — so while Nord is on,
the Mac's tailnet DATA PATH is blackholed (direct 100.x timeouts) and
Nord DNS resolves ts.net names to the public funnel → owner requests
arrive with XFF → correct 403 by the D1 gate. NOT affected: all Mac→GCP
jobs on the raw public IP (mirror trading, scp pushes, heartbeat — which
treats any HTTP response as "up", so no false pages). Affected while
Nord-on-Mac: owner-mode Terminal from the Mac browser, Termux→Mac:2222.
Standing option: GCP exit node replaces Nord (sysctl+advertise commands
+ admin approval pending Robert — classifier blocks Claude running them).

## 2026-08-14 (~20:30) — Per-block P/L labels on the Book allocation bars

Per Robert: small signed dollars.cents (no $ sign) inside each holdings
block of the allocation bars, live book + crypto sleeve. D1-compliant:
zero changes to the shared trading-gui dist or api.py (Mac/GCP api.py
have diverged — deliberately untouched); both are owner-injection edits:
- owner_trackgap.js: new decorator polls /api/portfolio/alpaca_live
  (60s), anchors on the dist's data-qt-slot="book-alloc-top" (Book page
  only — the identical hA bars on paper/broker tabs are never mislabeled
  with live numbers), labels only the "current" bar (targets have no
  P/L), skips blocks <24px, appends P/L to the hover title, re-applies
  every 5s to survive SPA re-renders.
- owner_crypto.js: sleeve bar segments get pl = mv − notional labels
  (blocks ≥8% wide) + P/L in the tooltip.
Deployed Mac + GCP by file sync only — the /owner/*.js routes serve from
disk with no-cache, so NO dashboard restart was needed (no mid-restart
ownerless-tab window). Phone not on USB; visual check = Robert's reopen.

## 2026-08-14 (~21:10) — $10 Kraken deposit booked as transfer (D39 sleeve)

Robert deposited $10 into Kraken after the D41 recency flag was given and
acknowledged (sleeve stays within the $50 D39 cap — no gate applies to a
top-up, only to raising the cap). Accounting: TRANSFERS array in
owner_crypto_account.js gains {2026-08-15T03:07Z, $10.00} so vs-inception /
vs-baseline P/L nets it out (label now says "transfers" not "adopted");
deployed Mac+GCP. Engine freeze math needs NO change — sleeve.py's rail is
realized+unrealized (cost-basis), which a cash deposit cannot inflate.
Watcher armed for the cash landing (equity.csv cash > $12); engine deploys
new cash per its own whale/momentum gates once it arrives.
