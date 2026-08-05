---
name: guest-no-real-money-data
description: "D31 — guest/shared surfaces must never serve real-money account data, even relabeled; sim ledgers only"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9e5724f4-ca08-45f0-bab9-54d4190bc0c0
  modified: 2026-08-04T18:38:55.301Z
---

2026-08-04, Robert (upset, correctly): the guest "QC Mirror (sim)" Terminal view was his real alpaca_live book relabeled — Linus and his dad could see the real $500 deposit, cash, and positions. He demanded the sharing stop immediately.

**Why:** Real-money balances/positions ARE the private IP boundary, independent of the D1 code/repo boundary. "Scrubbed" (account id stripped) or relabeled live data is still live data; dollar figures reveal deposits, composition reveals live strategy rotation (which now includes post-boundary D23 winners).

**How to apply:** Any surface reachable by a non-owner key or shared repo serves only genuine sim/paper ledgers (e.g. scanner's qc_mirror paper ledger). Never proxy, rescale, or relabel a real-money source for guests. Standing order D31 in [[project-github-sharing]] context; DIRECTIVES.md carries the machine CHECK. Guest key rotation cuts both Linus and dad (they share it); the post-rotation key lives only in VM dashboard_keys.json and is not distributed without Robert's explicit go-ahead. Related: [[post-linus-private]], [[feedback-permissions]] does NOT extend to handing out guest keys.
