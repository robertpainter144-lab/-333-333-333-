# PROMPTS_COMPILED.md — Robert's asks, compiled (through 2026-08-28)

One document any session or agent can read to know what Robert wants,
compiled from every recorded prompt in this stack. Verbatim quotes are
Robert's own words; D-numbers reference `DIRECTIVES.md` (canonical rules
ledger — this file summarizes intent, that file governs).

Sources: `trading-data/user_requests.jsonl` (251 substantive typed prompts,
2026-07-27 → 08-05) · `trading-stack/DIRECTIVES.md` (D1–D49) ·
`trading-stack/CONTEXT.md` changelog · verbatim quotes preserved in configs
and code comments (2026-08-15 → 08-27, the window after the request log
stops). Compiled 2026-08-28.

## 1. The prime directive: profit

- "nope, just optimize for profit" (8/4) · "idk? what will maximize profit?
  that's the goal..." (8/4) · "we are always looking for any and all
  opportunities to profit" (8/2). The improvement bot's standing mission:
  "Robert's main goal is PROFIT" (8/4, D29/D37).
- Ride the winners, always: "always staying on top of trying to mirror the
  winners" (7/30, D19) · "quarterly sweeps sound way less effective than
  being more on top of using the cutting edge strategy at all times" (8/2,
  D24) · "the goal is to design something that can easily beat our current
  winners" (8/2).
- Take profits per-position and redeploy — never empty the book: "exiting
  because we're taking profits is great but it doesn't mean the whole book
  should be empty after that… take profits on individual opportunities when
  we can and then rebalance that money into something better" (8/13, D47).
- Don't exit without reason: "make sure we don't exit anything
  unnecessarily... there's no trigger line, just optimizing for profit at
  all times" (8/13 — D42 ratchet retired as a general mechanism).
- Capture spikes: "a true big spike should sell all of the holding as close
  to the top of the spike as possible" (8/25 — megaspike full-exit trail).
- Bank gains before givebacks: "you should've exited trading and taken
  profit... deploy the winning rule" (8/26 — ratchet giveback $150, D69).
- Evidence before live money: paper proves, live deploys (D7), with the
  verified-winner fast lane (D23) and Robert's explicit overrides only
  (e.g. D22 flipped live 8/13 on his word).
- Hard risk rails are non-negotiable once set: $20 IBKR ceiling (D5), DD
  review triggers (D6), never borrow (D20), dip-day breaker + rotation
  hygiene (D21), 35% unproven-lane cap (D34), sleeve caps + freeze rails
  (D36/D39/D41), turnover budget (D43), funding ladder outside the trading
  loop (D49).

## 2. Money-display honesty (the chart / P/L contract)

- Transfer-adjusted P/L everywhere, Robinhood semantics: "make it adapt to
  incoming and outgoing deposits in a dynamic way, similar to how robinhood
  still shows total p/l regardless of your deposits and withdrawals" (8/4) ·
  "the deposits and withdrawals should not reflect on the p/l chart" (8/4) ·
  "technically p/l is not measured by deposits and withdrawals it's measured
  by trading" (8/4) · "green line is still showing the withdrawal not the
  overall p/l" (8/25 → P/L-collapsed view is the default).
- Deposits/adoptions are capital IN, not P/L — the PEPE adoption (8/9) and
  every cash top-up are netted out of every P/L figure.
- The server computes every money number; the client renders server strings
  verbatim (Book core law, 8/27). No client-side money math.
- Honest anchors are contract, not data: inception / test-baseline values,
  per-day "since 8/20". Change label and math together.
- Staleness must announce itself: a frozen/lagging chart source gets an
  explicit banner; the headline value comes from live Kraken, not a frozen
  recorder. While the engine is frozen, ledger cash moves must be applied
  onto the recorder tail (8/27–8/28 — the $536.25 withdrawal lesson).
- No fake or placeholder numbers: "I don't want the catalog cards to have
  zero portrayed" (7/30); zero NAV ticks are failed polls, never chartable
  values; omit stats (e.g. unrealized while flat) rather than showing ±$0.
- No duplicated or mislabeled figures: "fix the locations on the page where
  the portfolio value number repeats several times in differently labeled
  boxes" (7/27) · "make sure everything is separated out in a meaningful and
  clear way… no other weird errors like this" (7/30) · P/L percentages
  aligned consistently (7/30).

## 3. Terminal app UX

- Chart gestures: one-finger pan with no page-fight, two-finger pinch zoom
  (7/27), zero horizontal page scroll (7/27), smooth pan/zoom at 24H (8/4).
- "always see the chart in whatever timespan I want — basically just the
  portfolio value chart and a minimized version of all the chart timespan
  option buttons" (8/19 → PiP bubble + panel; Android PiP shows chart-only,
  no fake-tappable buttons, 8/22).
- Every span has data: 1D must never be empty while the engine is frozen
  (8/27 — coarse + fine NAV merge); hourly markers at 12H zoom (8/22).
- Live-updating, no loading flashes; auto-refresh every 3–10s.
- Catalog cards: 1-year performance charts, scrub-to-date holdings +
  allocation that update live with the scrub (7/30); right-aligned P/L.
- Allocation bars: current + target with tickers, size-ordered, clearly
  differentiated colors (7/30); per-block signed P/L labels (8/14); alloc
  bar sits directly under the chart (8/27).
- Action history: "more descriptive… a full scrollable history of all the
  actions that the governor has taken and why" (7/30).
- Book = real money only; Lab = paper research. Book is crypto-only since
  D61 (8/22, equities retired). Owner features must appear in BOTH the
  Terminal web app and the Android app (D2).
- Mobile-first: safe-area fit, equal-height hero cards, no clipped digits,
  value row must never hide behind floating buttons (8/27).

## 4. Architecture & ops

- No single-hardware dependency: "I don't want my system to depend on a
  single piece of hardware to survive… whether I have access to this
  machine or not" (8/13, D48) · "i want to be able to turn the computer off
  and everything keeps running independently" (8/4) · "don't host any part
  of it on the mac" (7/27).
- Everything auto-pushes to git every 5 minutes (D16); since 8/9 ALL git
  work targets `robertpainter144-lab/-333-333-333-` main and nothing else
  (D38 — exclusive until Robert lifts it). This repo is the delivery path:
  fixes land on main, hosts sync from it.
- Ecosystem isolation: the lab stack is one-way separated from the old
  Quarki33 ecosystem (D35, ISOLATION.md). Guests NEVER see real-money data
  (D31); guest surfaces stay frozen — the working version is the surprise
  for dad's birthday (8/4).
- Cross-session consistency: "wire up a context tracker that reviews each
  and every request I've input scanning the past 1 week to make sure all
  orders are being followed to a t" (7/29, D15 auditor) · "go through each
  and every prompt from today and make sure all my instructions are being
  followed" (8/3) · parallel windows must not collide or revert each other
  (8/4, D33).
- Notifications tiered; self-healed = silent (8/2, D23-notify). Usage
  governance so Claude never blocks Robert's prompting (8/2–8/3, D25).
- Never `kill -9` the dashboard — service restarts only (D12); core
  services stay up (D14); secrets never in repos or chat (D17).
- Automate everything; "find another way to do it automatically, i'm lazy"
  (7/30). One-shot helpers self-remove when done (8/14 pattern).

## 5. Crypto sleeve — current rules (as of 8/28)

- Kraken primary (D39). Cap $3,030 after the 8/20–21 scale-up; momentum
  tilt ON: "tilt the sleeve toward the leaders, maximizing profit" (8/20).
- Anti-churn (8/25: "revert the functioning state back to Thursday 8/20,
  keep the features"): flip exits need 4h min hold + 0.15% margin + 2
  confirm ticks; 90-min re-entry cooldown; 1.0x daily turnover cap.
- Megaspike full-exit 1.2% trail off the spike peak (8/25); ordinary spikes
  keep the 25% harvest + tighten.
- Ratchet: $150 giveback off the trade-P/L high-water mark (8/26, D69).
- Risk discipline (8/26): 35% per-coin cap at buy time; loss-streak halves
  entry sizes after 3 straight losers.
- Freeze at −$200 cumulative → flatten + halt for review. HIT 8/26 15:35;
  sleeve is FROZEN awaiting Robert's UNFREEZE decision. While frozen the
  engine cannot see manual restructures — recorder numbers are estimates.
- Graduation ladder for cap raises is ledger-gated (D41); beyond 35% of
  combined equity requires Robert amending D34 in writing.
- Kraken Funded challenge (8/24, hand-entered ledger in funded.json):
  $1,000 start · pass at $1,120 · kill floor $950.
- Whale/intel feeds are advisory only — they inform displays and research,
  never place orders directly (D40).

## 6. Open items awaiting Robert

- UNFREEZE (or manually flatten) the crypto sleeve — engine is halted and
  the leftover holdings are unmanaged until this decision.
- Funded challenge margin: $969.32 vs the $950 kill floor (8/28 16:28Z).
- Mac↔GCP api.py divergence: converge "ideally via lab333 canonical
  source" (8/13 OPEN DEBT) — i.e. make this repo's `ng_dashboard/` the
  single code source both hosts pull.
- Senate (Mullin) tracker still unwired (D24); SnapTrade secret rotation
  still open (D17).

## How to use this file

Any session touching money displays must satisfy §2 in full. Any session
touching strategy or risk code must re-read §1 and §5 and check
`DIRECTIVES.md` for the governing D-numbers before changing anything. New
standing orders from Robert get added to `DIRECTIVES.md` first and
reflected here.
