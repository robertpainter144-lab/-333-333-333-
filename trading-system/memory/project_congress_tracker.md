---
name: congress-pelosi-tracker
description: "Free Pelosi-Tracker+ replica: House Clerk PTR feed + paper mirror, 7th scanner source, governor-scored as congress_PAPER"
metadata: 
  node_type: memory
  type: project
  originSessionId: 20280baf-085c-46dc-b24e-17271cfda81d
  modified: 2026-07-27T14:26:12.553Z
---

2026-07-27: Shipped `~/scanner/sources/congress_feed.py` (hourly) + `congress_mirror.py` (5-min paper mirror, $50 scale) — replicates Autopilot's Pelosi Tracker+ for free from official House Clerk data (disclosures-clerk.house.gov year-index XML + PTR PDFs; embedded stdlib RC4-128/FlateDecode/ToUnicode PDF text extractor, verified on live 2026 Pelosi filings).

Composition heuristic (config `state/congress_feed_config.json`): 365d lookback, buy amount-range midpoints with 180d half-life decay, partial sales ×0.5, full sales drop, options count as underlying, top 12 normalized. First live composition: INTC 42%, AB 25%, UBER 10%, GOOGL/AMZN 8%, NVDA 4.5%, AAPL/VST 1.5%.

Wiring: governor challenger `congress_PAPER`, source key "congress" (combined_mirror default weight 0.0 — earns live allocation only via governor). edge_ledger tracks `congress_mirror`. Fail-closed everywhere; unparseable/image PTRs cached and skipped; >10 new PDFs/tick politeness cap.

Caveats: STOCK Act 30–45 day disclosure lag (June PTR = May trades); Senate (efdsearch) NOT implemented — House members only; housestockwatcher.com dead (S3 AccessDenied), capitoltrades bff 503'd. Related: [[project_autopilot_mirror]], [[lab-vs-live-divergence-fixes]], [[project_scanner]]
