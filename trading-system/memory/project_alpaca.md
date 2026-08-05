---
name: Alpaca Paper Trading Integration
description: Alpaca account credentials and integration status for live paper trading
type: project
originSessionId: 2983d522-039d-4c3d-bd33-839f00aba1b9
modified: 2026-08-04T20:32:08.340Z
---
## Paper Trading Account — ACTIVE (app.alpaca.markets visible)

- **Active keys** (PK-prefix, paper API):
  - Key: `PKNEW4EW6GHCKCDDLZAZSUFBQZ`
  - Secret: `9uuqGaFjpbbP9pMzHJm7GFk46iYmMYeGTV2yoq3abrFc`
  - Account ID: `43df0849-2f79-41e8-8ead-b2667150eb5d`
  - Base URL: `https://paper-api.alpaca.markets`
  - Auth: `APCA-API-KEY-ID` / `APCA-API-SECRET-KEY` headers
  - Equity: $100,000 | Cash: $100,000 | Buying power: $200,000
  - Trades visible at app.alpaca.markets ✅

- **Wired in**: `trading_swarm.py` lines 60-61 (PAPER_KEY / PAPER_SECRET defaults)
- **Endpoints used**: `/v2/account`, `/v2/positions`, `/v2/orders`
- **Live orders**: only placed when bot runs with `--live` flag

## Live account (combined_mirror book)
- 2026-08-04: Robert deposited $450 → live equity ~$500 (was $50 since 2026-07-02).
  Deployed same day into INTC/AB/UBER/GOOGL/AMZN/NVDA/AAPL/VST per governor targets.
- combined_mirror.py now deposit-aware: `_net_deposits_since()` sums CSD/CSW
  activities after `baseline_captured_at` (anchored 2026-07-14) so deposits count
  as capital, not P/L. State carries `net_deposits` + `baseline_captured_at`.
- CLOSED 2026-08-04: D6 trigger scaled to max($5, 3% of contributed), amber
  max($4, 2%) ([[feedback_5_drawdown_review]]). Implemented in vm_watchdog +
  directive auditor; the Mac-era stack_watchdog relic (unloaded) patched same
  day after a residual flat-$5 alert fired on an INTC wobble (DD $5.12 at the
  low, below the $10 amber line — review found nothing broken).

## Old broker sandbox (no longer used)
- CK-prefix keys, broker-api.sandbox.alpaca.markets, Basic auth — orders NOT visible in app

## Notes
- `trading_swarm.py` separates data keys (CK-prefix, sandbox data API) from paper order keys (PK-prefix)
- `hdrs()` = data API headers; `paper_hdrs()` = order/account headers
- Shorts skip Alpaca execution — tracked locally only (paper API has shorting limitations)
