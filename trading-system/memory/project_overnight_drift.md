---
name: project-overnight-drift
description: "Overnight Drift Harvester — paper-only D7 lane on VM (buy 15:58 ET / sell 09:31 ET, IWM+QQQ); the sole survivor-mechanism of the 2026-08-02 ideas-from-Claude sweep; INSTALLED & armed 2026-08-03, first buy Tue 08-04 15:58 ET"
metadata: 
  node_type: memory
  type: project
  originSessionId: c477f569-7019-4982-97b2-77613c42dbda
  modified: 2026-08-04T01:22:26.820Z
---

Overnight Drift Harvester, built 2026-08-02 late night — the only mechanism
from the 12-candidate "ideas from Claude" sweep whose signal fully replicated
under adversarial verification (overnight anomaly: IWM +6.2bp/night ann.
Sharpe 1.13, QQQ +5.0bp/0.94, SOXL +26.6bp/1.01, n=1505 nights 2020–2026,
reproduced independently by the kill agent from unadjusted OHLC). Killed
as-submitted only for (a) claiming D23 live-eligibility it doesn't qualify
for (in-house backtest, no external forward record — D23 is for mirrored
proven records) and (b) an uncharacterized fill gap: Alpaca fractional orders
have no MOC/OPG, so real fills are continuous-session prices, not the auction
prints the backtest assumes. Rebuilt on the honest D7 route.

- Code: VM `~/scanner/sources/overnight_drift.py` — STRUCTURALLY paper-only
  (paper-api hardcoded, only ALPACA_PAPER_* keys read; going live requires new
  code after the gate). $25 IWM + $25 QQQ market-buys ~15:58 ET, closes
  ~09:31 ET. Ledger `state/overnight_drift_ledger.jsonl` logs buy/sell fills
  vs IEX close/open prints → per-night realized bp, buy/sell/RT slippage bp,
  sleeve A/B parity (future settled-cash two-sleeve design). Errors page P4
  via quarki_notify; clean skips when market closed / legs already open.
  Dry-run verified 2026-08-02 (auth + clock OK, both legs skip correctly).
- Timers: INSTALLED 2026-08-03 night (units in /etc/systemd/system/, both
  enabled; script copied /tmp → ~/scanner/sources/, ast-checked). First
  fires: sell Tue 08-04 09:31 ET (verified clean no-op via "no open legs"
  guard), first real buy Tue 08-04 15:58 ET.
- Promotion bar (D7 + kill-panel addendum): n≥30 nights, realized mean
  ≥0.05%/trade, Sharpe≥1, AND round-trip slippage ≤5bp (the whole point of
  the paper phase is characterizing that slippage). SOXL sub-sleeve only if
  IWM/QQQ pass first. Review via Monday shadow-review push / edge ledger.
- Rest of the sweep for the record: 11 other candidates killed (5 false
  premises about stack data, 2 empirically dead when replicated — ex-div
  capture t=0.09, turn-of-month ≈ buy-hold; 2 broken data paths, 2 weak
  evidence). Nothing else entered any lane.
  Related: [[project-value-screen-v2]] (CLOSED same night),
  [[feedback-live-eligible-winners]], [[feedback-paper-vs-real]].
