# DIRECTIVES.md — Robert's standing orders (canonical ledger)

The directive auditor (`~/directive_auditor/` on the VM, daily + hourly timers)
checks the live system and the trailing week of Robert's typed requests
against this file. Every session that receives a new standing order MUST add
it here (and to Claude memory). Machine-checkable directives carry a CHECK
line the auditor enforces deterministically.

Last full revision: 2026-07-29.

## Privacy / collaboration

- **D1 — Post-Linus privacy boundary (2026-07-28).** Everything built after
  sharing the iPhone/Terminal app with intern Linus is owner-private: never in
  trading-gui source, dist, or git history; never in MVYTraders. Private data
  goes on owner-only endpoints (not in `_GUEST_PATHS`); private UI is served
  from ng_dashboard via owner-mode (no-XFF) injection.
  CHECK: MVYTraders + trading-gui trees/dist contain no `autopilot_cat`,
  `/api/catalog`, `owner_catalog` strings; guest key 403s `/api/catalog` and
  `/owner/catalog.js`; public shell has no injection; guest `/api/edge` has no
  catalog rows.
- **D2 — "My private GUI" = owner-mode Quarki333 Terminal** (tailnet, no XFF).
  New private features must be visible there (ng_dashboard injection pattern)
  and in the Kotlin Android app — not only one of them.
- **D3 — Never invite anyone to any repo without explicit instruction.**
  MVYTRADER (no "s") stays an empty decoy.
- **D4 — No Autopilot scraping.** Their ToS bans automated collection; pilot
  holdings are hand-transcribed by Robert only. Catalog rows are display-only
  benchmarks and STRUCTURALLY must never receive real money.

## Money / risk

- **D5 — $20 aggregate IBKR auto-fire ceiling.** Total (not per-trade) IBKR
  exposure above $20 requires push (ntfy) approval first.
- **D6 — Aggregate live-account drawdown = mandatory review (rev 2026-08-04).**
  Trigger scales with the book: 3% of contributed capital (Σ deposits −
  withdrawals via /api/transfers/alpaca_live), $5 floor; amber at 2% / $4
  floor. At the current $500 contributed: review $15, amber $10. Never
  "hold and wait". Revised with the deposit-scaling decision (option 2, with
  the 35% single-name cap in combined_mirror.py — was 0.90) because at $500
  the book's ordinary 1σ day (±$12, INTC-driven) exceeded the old $5 trigger.
  CHECK: alpaca_live equity vs peak; alert at ≥max($4, 2%) (amber) and
  ≥max($5, 3%) (violation).
- **D7 — Paper proves, IBKR deploys.** Real money is never for experimenting;
  strategies earn live weight only through the promotion gate
  (n≥30, mean≥0.05%, Sharpe≥1, late-mean≥0.02%). Exception Robert granted:
  Autopilot portfolios with public records are pre-proven (Alpaca-stack gate
  unchanged).
- **D8 — IBKR: never sell same-day** (PDT 28-day window constraint).
- **D9 — Congress + prop-desk strategies stay at 0 live weight until earned.**
  Signal-only until promoted.
- **D10 — Rule-governed allocation.** Flag recency-driven reversals with
  data; never recommend willpower-holds.

## Ops discipline

- **D11 — CONTEXT.md changelog discipline.** Every meaningful shipped change
  gets a dated entry in trading-stack/CONTEXT.md the same day.
- **D12 — Never `kill -9` the dashboard.** VM: `sudo systemctl restart
  quarki-dashboard.service`. (Mac-era: `launchctl kickstart -k`.)
- **D13 — VM is source of truth post-migration (2026-07-27).** The Mac hosts
  nothing; sync Mac copies after VM edits. Never create `/opt/trading` on the
  VM (flips TRADING_DATA_DIR off ~/trading-data).
  CHECK: `/opt/trading` does not exist.
- **D14 — Core services stay up.** CHECK: quarki-scanner, quarki-dashboard,
  quarki-qc, quarki-poc10, quarki-soxl active; autopush +
  watchdog timers active.
  (quarki-paper-arm retired 2026-08-02 — superseded by D23 live-eligible winners;
  it was a hung, redundant research feeder well below the n>=30 promotion gate.)
- **D15 — Cross-session consistency (2026-07-29).** Claude must keep memory,
  CONTEXT.md, and this ledger in sync with Robert's actual instructions; the
  auditor's daily LLM pass reviews the week's raw requests for anything
  dropped, and its report is the escalation path (ntfy on violations).

## Added by directive audit 2026-07-29 (from Robert's own words in transcripts)

- **D16 — Autopush everything every 5 minutes** unless Robert specifies
  otherwise or the material is designated private under D1 (2026-07-28:
  "keep auto pushing everything every 5 minutes unless i specify otherwise
  or if we've already determined it's something i'm keeping to myself").
- **D17 — Secret hygiene.** No raw API secrets/tokens in chat, repos, or
  logs; anything exposed gets rotated. OPEN ITEM: the SnapTrade user secret
  pasted in chat 2026-07-27 should be rotated at snaptrade.com when the
  Robinhood feed is next used (feed currently dormant).
  CHECK: repos grepped for the exposed string.
- **D18 — Linus collaboration is branch/repo-isolated.** His work and
  Robert's stay separate; nothing auto-merges across the boundary
  (2026-07-28: "see all his progress while simultaneously branching my own").
- **D19 — Always ride the winners (2026-07-29).** The governor runs in
  ADAPTIVE tempo by default (state/governor_config.json {"mode":"adaptive"}:
  4h rolling window, L1 gate 0.04, sticky 0.02, concentration power 1.6) so
  capital chases current leadership at intraday speed, bounded by cash-account
  T+1 settlement. Catalog pilots enter the tournament ONLY via
  `python3 ~/scanner/promote_pilot.py <Pilot>` (autopilot lane), on FORWARD
  performance — never backfill numbers — and face the standard gate (D7)
  before real weight. Kill switch (-8% live) still freezes everything.
- **D20 — Never borrow on Alpaca (2026-07-30).** The live rebalancer spends
  min(buying_power, cash) — positions never exceed equity, so leverage,
  margin calls, and the post-PDT risk-based intraday margin standard are
  structurally unreachable. PDT rule is repealed (eff. 2026-06-04) and
  VERIFIED implemented by Alpaca day one (their announcement + changelog
  deprecating PDT fields) — same-day-sell guard removed 2026-07-30 per
  Robert ("don't hedge on something you can just verify"); no self-imposed
  day-trade limits remain. CHECK: alpaca_live positions market value ≤ equity.
- **D21 — Dip-day circuit breaker + rotation hygiene (2026-07-31).** (a) If
  the live book's day P/L breaches -1.5% (state/risk_config.json,
  hot-reloaded), ALL trading freezes for the session — positions held, never
  dumped at the low; auto-resumes next market day; ntfy fires once.
  (b) The governor may not rotate capital while the market is closed (frozen
  prices can't re-rank), and a NEW leader must hold its lead 4 consecutive
  evaluations (~1h) before weights follow. Root cause: 2026-07-31 whipsaw —
  overnight patent→congress flip bought INTC at open (-4.2%), flipped back
  same day; ~19 live orders. CHECK: churn cap (D19) + freeze honored.

## Added 2026-08-02 (profit-taking audit)

- **D22 — Profit-taking is shadow-first (2026-08-02).** From Robert's "find a
  way to take profits more efficiently": the PT engine in combined_mirror
  (arm +1%, 0.5% giveback from peak) runs in SHADOW mode
  (state/pt_shadow.jsonl, zero orders) until the shadow ledger proves it per
  D7 discipline (8–12 weeks, >=150 independent entry events, clustered
  bootstrap 95% CI excluding zero, no single event >50% of the delta).
  The structural sell guards (48h re-entry premium 1%, 24h min-dwell on
  green full-exits) ARE live — they only re-time orders the rebalancer
  would place anyway and never dwell-hold a loser (D6/D10 intact).
  CHECK: risk_config.json profit_take.mode == "shadow" unless Robert
  explicitly flips it.
  *2026-08-05 interim scorecard (fills-replay backtest, 7/13–8/5, 269 live
  fills):* current rule +$6.62 vs as-traded (−$2.79 baseline), positive in
  both half-windows, survives 10bp slippage; shadow ledger 27 events / +$1.92
  forward. BUT clustered-by-symbol bootstrap 95% CI [−0.45, +15.59] includes
  zero (P(≤0)=0.038) and UBER is 50% of the delta — promotion bar NOT met;
  rule stays SHADOW. Staged for the eventual flip: pt_live.jsonl fire logging
  in combined_mirror.py (inert in shadow) and quarki-pt-tripwire.timer
  (weekdays 20:30 UTC, auto-reverts mode to shadow + P4 if live fires net
  ≤ −$3 vs holding). Scoring harness: ~/pt_backtest.py on the VM.

## Added 2026-08-02 (ultracode profit sweep)

- **D23 — Verified sweep winners are live-eligible (2026-08-02).** Robert:
  "you can always put the real winners in the actual alpaca trading book
  profit rotation to make sure we're not missing any valuable strategies."
  This extends the D7 exception lane: a strategy that survives an
  adversarial multi-agent audit with REAL supporting evidence (public
  forward track record, existing paper-ledger edge, or verified data — not
  a raw idea or backtest-only claim) may be entered into the live Alpaca
  governor rotation directly, without first completing the full
  n>=30/mean/Sharpe paper gate. It enters at tournament weight like any
  lane and is scored on FORWARD performance only (never backfilled).
  All risk rails unchanged and non-negotiable: D5 ($20 IBKR ceiling),
  D6 ($5 DD review), D20 (no margin), D21 (breaker + rotation hygiene),
  -8% kill switch, D4 (Autopilot catalog rows still STRUCTURALLY
  money-proof — a live sleeve trading a public ETF ticker is a separate
  lane, not a catalog row). Raw/unevidenced ideas still go paper-first.

- **D24 — Continuous mirror currency (2026-08-02).** Stay on the current best
  mirror at all times, not quarterly. Machinery: (a) quarki-leadercheck.timer
  (weekdays 22:17 UTC) compares all catalog curves vs the promoted autopilot
  lane; in AUTO mode (state/leader_check_config.json {"mode":"auto"}) it
  swaps the PAPER lane itself after a 3-day sustained >2pp lead, 5-day
  cooldown, ntfy on every swap — real weight still requires the D7 gate;
  (b) weekly cloud routine weekly-mirror-sweep (Mon 12:07 UTC) delta-sweeps
  the web and commits to private repo Quarki33/mirror-sweeps — review its
  report in-session each week; (c) Crenshaw_Tracker (House) and Mullin_Tracker
  (Senate) both auto-refresh from official PTR filings each Monday
  (2026-08-04: Senate eFD wired via congress_feed_senate.py — efdsearch.senate.gov
  agreement+AJAX search+HTML table parse, same public-disclosure category as
  the House Clerk feed; catalog rows only, D4 money-proof).
  CHECK: quarki-leadercheck.timer active; leader_check_state.json last_run
  within 4 days.
- **D22a — Round-2 addendum (2026-08-02).** Hard stops (MAE exits, any basis
  2–8%) are REJECTED for this book — all cells ex-AMD negative, whipsaw tax
  on mean-reverting names; do not deploy without new mechanics + fresh
  verification. The per-symbol eligibility gate runs SHADOW-ONLY
  (state/elig_shadow.jsonl); promotion requires >=3 distinct symbols,
  >=10 events incremental over existing guards (empty also_blocked_by),
  positive counterfactual ex-best-event, then a >=4-week paper A/B (D7).
  CHECK: risk_config.json elig_gate.mode == "shadow" unless Robert
  explicitly flips it.

## Added 2026-08-02 (consumption governance)

- **D25 — Claude usage consumption governance (2026-08-02).** From Robert's
  "meta-level consumption manager ... ensure we run continuously without
  running out of usage": ~/consumption_manager/ on the Mac meters all Claude
  Code usage (transcript-derived weighted units) into rolling 5-hour and
  7-day windows and publishes GREEN/AMBER/RED to state.json (launchd
  com.quarki3.consumption-manager, 15-min cadence), synced to VM
  ~/trading-data/consumption/state.json. Automated/optional Claude jobs
  (auditor LLM pass, cloud sweeps, swarms) must pass ~/bin/claude_gate.sh
  (VM) or `consumption_manager.py gate` (Mac) first: RED = interactive/
  critical work only; AMBER = defer optional swarms/sweeps and cap agent
  fan-out at 4-6 (burst 429s during the 08-02 24-agent sweeps were the
  trigger). Budgets are empirical estimates (5h: 90M units, weekly: 350M) —
  recalibrate whenever new 429 evidence lands. Alerts via quarki_notify
  tiers (RED -> action P4, else routine P2).
  CHECK: VM ~/trading-data/consumption/state.json fresher than 48h.
- **D23 — Tiered notifications; self-healed = silent (2026-08-02).** From
  Robert: too many pings about problems the stack fixes itself. All alerts
  route through ~/bin/quarki_notify.py: critical P5 (human needed NOW: D6
  breach, self-heal not sticking >=3x/45min; + SMS when twilio.env exists),
  action P4 (decision soon: violations, disk, breaker, $4 amber DD), info P3
  (scheduled summaries), routine P2 (silent — anything auto-healed).
  New alert code must never post raw ntfy at default/high priority for a
  self-handled event; design self-healing first, silent log second, page
  last. CHECK: vm_watchdog.py imports quarki_notify; no sender posts
  self-heal events above Priority 2.

- **D31 — Guest surfaces never serve real-money data (2026-08-04).** No guest
  or shared surface (guest key, shared repo, shared app) may serve real-money
  account data — balances, cash, positions, fills — even relabeled, rescaled,
  or "scrubbed". Sim/paper ledgers only. Found 2026-08-04: the guest
  "qcmirror" source was a gate-level rewrite to alpaca_live, so Linus + dad's
  Terminal showed Robert's real $500 book. Fixed at the gate, and qcmirror
  is now a genuine sim source fed by scanner/state/qc_mirror_state.json +
  qc_mirror_ledger.jsonl + labbook_curves.jsonl (virtual $50 bankroll).
  *Key policy (2026-08-05, Robert):* the ORIGINAL guest key (7a0d…) stays
  live — Robert briefly rotated it 8/4 then restored it the same evening.
  Never rotate or redistribute it, and never make guest-visible changes:
  Linus/dad must see zero signal that anything owner-side is being hidden.
  Privacy is enforced server-side only (allowlist + scrubbing).
  *2026-08-05 leak fixes:* guest /api/qc was serving mirror_mode:"live" +
  mirror_targets (live composition); guest /api/whales alert stream carried
  "LIVE combined_mirror … pnl_pct" telemetry lines. Both scrubbed server-side
  (api.py _guest_scrub, backup .bak-20260805T2235Z-pre-d31-scrub). The
  shared trading-gui source keeps its 7/28 owner/guest-split comments ON
  PURPOSE — Linus can read git diffs, and retroactive scrubbing is itself
  the signal; the split has been the documented collab premise since 7/28.
  CHECK: guest /api/qc has no mirror_mode/mirror_targets/mirror_bankroll/
  mirror_pnl_pct keys; guest /api/whales contains no "combined" and no
  "LIVE" (auditor enforces both since 2026-08-05).
  CHECK: guest key 403s /api/portfolio/alpaca_live; guest
  /api/portfolio/qcmirror returns source=="qcmirror" with
  starting_capital==50.0 and equity differing from owner alpaca_live equity;
  api.py contains no rewrite of qcmirror paths to alpaca_live.

- **D31a — Guest epoch mode (2026-08-04).** While
  ~/trading-data/guest_demo.flag exists on the VM, guest keys see
  epoch-rebased research figures (baseline guest_demo_baseline.json) —
  "since the 2026-08-04 reset" semantics with real deltas accruing.
  Owner/tailnet views untouched. Deleting the flag is the reveal (dad's
  birthday gift). The combined_LIVE governor scrub is permanent, NOT
  flag-gated. CHECK: if guest_demo.flag exists, guest /api/edge
  qc_portfolio row has n_closed_trades < 30; guest /api/governor snapshot
  never contains combined_LIVE (flag or no flag).
- **D32 — Private repos auto-push PRIVATELY only (2026-08-04).** Every private
  repo auto-pushes to its own private remote; NOTHING flows to shared repos
  (MVYTraders) automatically. The trading-gui→MVYTraders mirror stays disabled
  until Robert explicitly re-enables it per-occasion.
  CHECK: /home/ubuntu/.mvy_mirror_paused exists on VM.
- **D33 — Cross-session board (2026-08-04).** Robert runs several Claude
  windows in parallel and they were colliding (shared-file overwrites, stale
  state, duplicated work). Every window's hooks now maintain
  `~/.claude/session_board/board.json` (Mac-local, flock-protected) via
  `~/.claude/hooks/session_board.py`, and every prompt injects the other
  active windows' tasks plus coordination rules: re-read shared files
  (memory/, DIRECTIVES.md, CONTEXT.md, settings.json, VM state) immediately
  before editing; never blind-revert a mid-turn file change (it is probably
  another window); don't duplicate or undo a parallel window's task — flag
  the overlap to Robert instead; remember all windows share one 5h token
  budget. CHECK (Mac-local — VM auditor cannot reach it):
  ~/.claude/hooks/session_board.py exists and ~/.claude/settings.json wires
  it on UserPromptSubmit, Stop, and SessionEnd.
- **D34 — Unproven-lane cap: 35% of live weight (2026-08-04).** From the
  congress-lane review (99% of live capital on a 6-day paper record, verdict
  UNPROVEN): no governor lane may exceed 35% of live weight until it PASSES
  the D7 promotion gate on scored trades. Resolves D9-vs-D19: winners are
  ridden (D19) but only up to the cap until proven (D9 spirit). Enforced in
  strategy_governor._cap_weights on every publish branch (held/gated
  included; kill-switch freeze exempt — frozen means frozen). Passed lanes
  are listed in governor_config.json "proven_lanes" (currently empty);
  Robert's "optimize for profit" delegation 2026-08-04 chose expected
  compound growth over streak-chasing. OPEN ITEM: mirror ledgers log order
  deltas only (no per-trade P/L), so the D7 gate cannot score any lane yet —
  instrumentation fix required before any lane can earn cap removal.
  CHECK: governor_config.json has unproven_cap 0.35; every lane in
  governor_weights.json weights ≤ 0.36 unless listed in proven_lanes
  (frozen:true snapshots exempt).
- **D35 — Ecosystem split: owner mirror stack (2026-08-04).** The owner
  maintains a complete off-account mirror of the stack plus an owner-only
  Android client ("Terminal 333"); location, account, and credentials are
  recorded in owner memory ONLY — never in any repo, doc, dist, or guest
  surface of the original ecosystem. Nothing from the mirror stack is ever
  pushed or referenced back here; commits there never use this ecosystem's
  identity or tokens. Guest-visible surfaces (MVYTraders, trading-gui,
  iPhone app) stay frozen as handed over. CHECK: no `lab333`,
  `terminal333`, or `com.lab333` strings anywhere in MVYTraders,
  trading-gui tree/dist, or guest-reachable endpoints.
- **D35a — Mirror account must never be publicly visible (2026-08-05).**
  The mirror account's repos must show 404 to anonymous GitHub at all
  times. CHECK: quarki-lab333-watch.timer (VM, hourly) polls the account's
  public repo list anonymously and pages P5 the moment anything appears;
  state in scanner/state/lab333_watch_state.json.

- **D36 — $50 live turbo arm (2026-08-05).** Robert authorizes ONE live
  aggressive sleeve on the Alpaca account: `moonshot/turbo_arm.py`, the
  moonshot lane's exact rules on real money, hard-capped at $50 aggregate
  cost basis. This is a narrow, explicit exception to the paper-first gate;
  every other lane still proves on paper first. The cap is the total risk
  budget for the "exciting" experiment — raising it (or adding a second live
  experimental arm) requires Robert saying so in writing.
  Rails: freeze entries at −$15 cumulative (P4 page, sticky until a human
  clears `frozen`); no same-day round trips (PDT); never a symbol
  combined_mirror tracks; spend capped at cash (never borrow); mirror carves
  max($50, turbo MV) off its bankroll and never touches turbo legs.
  CHECK: sum of open-leg cost basis in scanner/state/turbo_arm_state.json
  ≤ $50; turbo_ledger.jsonl rows carry pl_usd; combined_mirror state
  `bankroll` ≤ account equity − $50.

- **D37 — Improvement bot alternates research cycles (2026-08-06).** Robert
  wants the D29 improvement bot biased toward FINDING NEW WAYS TO PROFIT,
  not just stack maintenance. At least every other 4h cycle must be a
  market-scanning/new-edge research cycle drawn from the "Research agenda"
  section of `~/improvement_bot/BACKLOG.md`; only a broken-right-now health
  problem may preempt a due research cycle. Research cycles tag their
  `~/improvement_bot/LOG.md` entry heading with `[research]` and must end in
  a falsifiable written verdict with evidence. All existing rails unchanged
  (paper/shadow only, PROVEN gate, shelved verdicts stay shelved, D27 model
  tiers).
  CHECK: at least 2 LOG.md entries whose heading contains `[research]` in
  any trailing 48h of improvement-bot cycles (skip the check if the timer
  itself was down, which D-health checks catch separately).

- **D38 — All git work goes to the lab repo ONLY (2026-08-09).** Every
  commit/push from Mac and VM targets
  `robertpainter144-lab/-333-333-333-` main and nothing else. NO pushes to
  any Quarki33/* remote (trading-stack, scanner, ng_dashboard, qc_portfolio,
  ibkr_bot, whale_tracker, insider_bot, patent_cliff, trading-gui,
  trading_android, quarki-terminal-android, mirror-sweeps) — those repos are
  frozen archives. Supersedes the D24 push leg to Quarki33/mirror-sweeps
  (sweep artifacts now land in the lab repo or stay local). Mac autopush
  (`~/bin/trading-autopush.sh`) pushes only `~/lab333/upstream333` with a
  hard remote+branch guard; canonical checkout is `~/lab333/upstream333`
  (`~/lab333/stack` is deprecated, reset to origin/main).
  CHECK: ~/bin/trading-autopush.sh contains
  `robertpainter144-lab/-333-333-333-` and no `Quarki33` push loop; no VM
  timer/script pushes to a Quarki33 remote.
  **Reaffirmed EXCLUSIVE 2026-08-09 18:30 (Robert: "make sure this is the
  only repo we are working with until I say otherwise").** Enforcement sweep
  same evening: every remaining live push URL on the Mac neutered to
  `DISABLED-BY-D38-USE-LAB-REPO` — insider_bot, MVYTraders,
  quarki-terminal-android, trading-companion, lab333/terminal333, and the 4
  nested trading-system/* checkouts (fetch URLs kept for reference).
  Includes MVYTraders and robertpainter144-lab/terminal333: no pushes
  ANYWHERE except `-333-333-333-` main until Robert lifts this.
  companion-sync auto_sync.sh pushes are dead (plist unloaded + URL
  neutered); mvy_watch stays (read-only tripwire).
  CHECK: `git -C <repo> remote -v` shows no live push URL other than
  `robertpainter144-lab/-333-333-333-` for every git checkout under ~.

- **D39 — Live crypto sleeve, $50 cap (2026-08-09).** Robert's second
  sanctioned live experiment sleeve (turbo-arm pattern): whale-gated
  momentum on crypto spot (BTC/ETH/SOL), broker-pluggable — PRIMARY VENUE
  KRAKEN (Robert's existing account; Alpaca leg dormant, blocked on their
  missing crypto agreement UI as of 2026-08-09), engine at `~/crypto_sleeve/`
  (Mac interim host, launchd `com.quarki3.crypto-sleeve` 5-min +
  `com.quarki3.crypto-whale-refresh` 6h; migrate to VM with the stack).
  Rails: $50 aggregate hard cap, $16/position, stop −8% / take +15%,
  sleeve freeze + full liquidation at −$15 cumulative P/L; per-trade
  ledger `state/closed_trades.csv` (D7-scoreable). Whale gate from
  whale_tracker Q03 risk-on ratio; stale/empty feed degrades to
  momentum-only entries, never blocks exits or rails. Dormant until
  Robert signs the Alpaca crypto agreement (crypto_status ACTIVE) and
  funds ~$50 of settled cash; activates automatically after that.
  CHECK: `~/crypto_sleeve/config.json` has cap_total_usd ≤ 50 and
  freeze_pnl_usd = −15; launchd job loaded; no crypto position outside
  the sleeve's ledger accounting.

- **D40 — Persistent intel engine (2026-08-09).** Robert: a portion of the
  Terminal must "stay on and scrape the entire web" for news + social
  sentiment feeding both equities and crypto decisions. Implementation:
  `intel_engine/collect.py` on quarki-stack (quarki-intel.timer, 30 min,
  Mac-off independent) collecting PUBLIC KEYLESS sources only — Reddit RSS
  (CryptoCurrency/wallstreetbets/stocks), StockTwits streams, alternative.me
  Fear&Greed, CoinGecko global, CoinDesk/CoinTelegraph RSS, Google News per
  held symbol — lexicon-scored into risk_on_social (0..1). Served owner-only
  at /api/crypto/intel (merged with the whale composite the Terminal card
  already renders) and /api/equity/intel. No login scraping, no ToS-breaking
  collection (D4 discipline); sources degrade fail-soft. Intel is ADVISORY
  — it feeds displays and research, never places orders directly (D7 gate
  unchanged for any strategy built on it).
  CHECK: quarki-intel.timer active on VM; intel_engine/state/intel.json
  asof < 2h old; /api/crypto/intel 403s guest keys.

- **D42 — Breakeven Ratchet on the live book (2026-08-11).** From Robert's
  marked-up chart ("if you had actually taken profit at those points"):
  when alpaca_live equity recovers to contributed capital − $0.25, the
  rebalancer banks the WHOLE book to cash and stays flat until the sold
  basket is 0.5% cheaper (dip-triggered rebuy — validated on the 8/4–8/11
  5-min replay: +$5 vs staying invested; time-triggered rebuy lost money),
  with a max-5-trading-day fallback re-entry and ≥1 trading day between
  cycles (cash-account GFV guard). Never evaluated while the D21 breaker is
  frozen. contributed_usd lives in state/risk_config.json breakeven_ratchet
  and MUST be updated on any deposit/withdrawal.
  Same session: fixed a latent D21 violation — orphan liquidation had no
  `frozen` guard, so a breaker day would have dumped every position at the
  low; sells are now suppressed while frozen.
  CHECK: risk_config.json breakeven_ratchet.mode == "live" and
  contributed_usd within $1 of Σ deposits−withdrawals from
  /api/transfers/alpaca_live; combined_mirror orphan-liquidation block
  contains `not frozen`.

- **D43 — Daily turnover budget on the live book (2026-08-12).** From the
  sandbox-harness finding Robert shared (costs are the binding constraint:
  104x/yr turnover = instant death even with 32% gross alpha) and this
  book's own measurement (150-230x annualized on 8/10-8/12): the mirror may
  trade at most daily_notional_pct (20%) of the book per day in
  DISCRETIONARY flow — largest target-drifts fill first; full exits and
  ratchet/PT sells are never budget-blocked, so risk can always come off.
  Config: risk_config.json turnover_budget (hot-read); per-day spend in
  combined_mirror_state.turnover. 20%/day ≈ 50x/yr ≈ 1.5-2.5%/yr spread
  drag at large-cap spreads — the harness's survivable zone.
  CHECK: turnover_budget.mode == "live"; combined_mirror_state.turnover.day
  == today on trading days; ledger daily |delta_usd| sum ≤ 1.3× the cap.
