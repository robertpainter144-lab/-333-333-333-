---
name: lab-vs-live-divergence-fixes
description: "2026-07-27 root cause + fixes for why $50 Alpaca live lost while QC Lab profited (governor staleness, settled BP, weekend order queue, cap leak)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 20280baf-085c-46dc-b24e-17271cfda81d
  modified: 2026-07-27T15:56:59.210Z
---

2026-07-27: Diagnosed and fixed why the $50 Alpaca live book (run by `~/scanner/sources/combined_mirror.py`) was at −2.5% while the Lab's QC engine was positive. Four interacting defects, all fixed same day:

1. **Governor staleness conflict**: `strategy_governor.py` hysteresis (only rewrite weights on L1 ≥ 0.10) let `governor_weights.json`'s ts go stale; `combined_mirror._governor_weights()` discards files >4h old → the 92.6%-QC recommendation was silently dropped and live ran the equal blend (40% patent, the losing source). Fixed: governor republishes unchanged weights with fresh ts every cycle.
2. **Settled-funds 403 loop**: live is a cash account — `cash` includes unsettled T+1 proceeds; only `non_marginable_buying_power` is spendable. Buys now gated on settled BP with 30-min per-symbol cooldown after a 403.
3. **Weekend queued-order runaway**: day market orders queue while market closed, positions don't update, every 5-min tick re-buys. 530 queued qc_mirror AMD buys (~$17.9k paper) filled at once at Monday open. Fixed: `_market_open()` /v2/clock guard in qc_mirror + combined_mirror; never submit while closed. This is the same bug class as the patent_mirror 6x incident — check any new mirror for it.
4. **Concentration-cap leak**: when QC converges single-winner (all 5 strategies held AAPL), the 15% single-name cap's global redistribution handed QC's excess to 2%-rated sources. `build_target()` rewritten: source-first fill, spill only to sources with governor weight ≥ 0.10 (`SPILL_MIN_SOURCE_WEIGHT`), remainder deliberately held as cash. Later same day, user authorized ("if qc is our leading strategy go ahead") raising `MAX_SINGLE_NAME_PCT` 0.15 → 0.90 so QC's single-winner composition is fully expressible: live targets AAPL 90% + patent/value ~4%. The $5 aggregate-DD review trigger is the remaining backstop.

5. (added later 2026-07-27) **Orphan-position blindness**: the mirror filtered positions to its CURRENT targets, so symbols dropped from targets became invisible and never sold (AMD/SPY leftovers). Fixed: live account scoped as dedicated (see all positions), `tracked_syms` persisted, orphans liquidated BY QTY — notional sells 403 ("insufficient qty available") when price drifts from the fetched market value. Check any new mirror for both patterns.

Notes: scanner modules are imported once by `scanner/main.py` — edits need `launchctl kickstart -k gui/501/com.quarki3.scanner`. Lab metrics caveat: qc_mirror pnl = unrealized/invested and was distorted by runaway positions; edge_ledger's qc +62.9% is the $50k virtual engine, honest forward record ~+1.3%/event. Related: [[project_scanner]], [[project_prop_desk_autonomous]], [[feedback_context_md_discipline]]

**2026-07-30 supersession — settled-BP guard retired, margin-aware sizing in.** The account turned out to be MARGIN (4x max-multiplier agreement on file; sub-$2k = Alpaca "limited margin": unsettled proceeds spendable, GFVs don't exist). AND the PDT rule was repealed (SEC approved 2026-04-14, effective 2026-06-04, FINRA RN 26-10; broker phase-in to 2027-10). combined_mirror.py now sizes buys to min(buying_power, cash) — never borrows (D20) — and defers same-day same-symbol sells as a phase-in house-rule hedge. T+1 two-morning rotation rhythm is GONE; rotations complete same-day. VM is python3.8: no zoneinfo — midnight-ET comes from Alpaca /v2/clock. Backup: combined_mirror.py.bak-margin.
