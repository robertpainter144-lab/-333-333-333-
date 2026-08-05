---
name: project_moonshot_screener
description: "Dad's moonshot-hunter screener prototype (5-10x asymmetric bets) + biotech catalyst calendar; ~/moonshot/ on VM, prototype only"
metadata: 
  node_type: memory
  type: project
  originSessionId: 93618a0e-11f8-4e42-95b5-d57004a7ac37
  modified: 2026-08-05T07:36:24.234Z
---

**Robert's dad's strategy = MOONSHOT HUNTER, not value.** He wants names that can go
up 5-10x+ from purchase: small/micro-cap, often pre-profit, catalyst/theme-driven,
asymmetric. This is the OPPOSITE of classic value/GARP — a P/E filter rejects exactly
the names he wants. (Correction made 2026-08-03: the scanner's old `value_screen`
source is a GARP/quality screen — low P/E, steady growth, ORCL/MSFT-type compounders —
NOT dad's thesis. It's wired to combined_mirror but effectively $0 allocated, and
STRUCTURALLY can't earn governor weight because value_screen_state.json has no pnl_pct
for the governor to rank. See [[project_value_screen_v2]] [[project_dip_buy]] — dad's
dip-buy was shelved; SPY Overlay is the surviving live form.)

**Built 2026-08-03 (PROTOTYPE — no money, no lane, no schedule):** `~/moonshot/` on the VM
- `moonshot_screen.py` — ranks a themed speculative universe on 4 axes:
  composite = 0.25*cap + 0.30*momentum + 0.20*base + 0.25*catalyst
  (cap=smaller better; momentum=early breakout/RS/volume; base=beaten-down from 52wH;
  catalyst=real for biotech, theme-proxy otherwise). Run:
  `/home/ubuntu/quarki-venv/bin/python ~/moonshot/moonshot_screen.py`
- `catalyst_calendar.py` — REAL biotech catalyst timing from clinicaltrials.gov v2 API
  (nearest upcoming Phase 2/3 primary-completion = readout per lead sponsor; sooner +
  later-phase = higher; 24h disk cache in ~/moonshot/cache/). Wired for biotech via a
  BIOTECH_SPONSOR ticker->sponsor map. Demoted RXST #1->#13 (RxSight = commercial device
  co, no readout); NTLA carries real Phase 3 readout ~2027-07 (331d). PDUFA dates aren't
  in a free feed — trial primary-completion is the best free proxy.
- Universe = 75-name curated themed seed (ai_infra, nuclear, space, quantum,
  defense_drone, robotics, biotech, ev_energy, crypto_eq, evtol_mob).

**IMPORTANT — not version-controlled.** ~/moonshot is a loose dir, NOT in the vm-autopush
repo list, so it is NOT backed up to GitHub. Add it to a private repo to avoid loss.

**Known limits:** yfinance batch download is flaky (scores 53-71 of 75 per run); universe
is a hand seed (expand / make data-driven); no crypto low-cap sub-screen yet; catalyst is
real only for biotech (nuclear/space/etc. still theme-proxy). It's a SCREEN, not proven
edge — paper-first before any money ([[feedback_paper_vs_real]]).

**Next steps (Robert's chosen order a→b):** (a) weight catalyst higher + add a hard boost
for a Phase 2/3 readout inside ~90 days so imminent-catalyst names lead regardless of
cap/trend; (b) re-run clean for full 75-name list; (c) expand universe + crypto sub-screen;
(d) stand up a paper lane to start a track record. Open question for Robert: does he want
"near catalyst regardless of cap/trend" or "small+cheap+near catalyst" to dominate.
