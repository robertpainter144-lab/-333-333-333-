# Architecture notes

## Trust boundary

The backend is **trusted infrastructure** Robert operates. The PWA holds only:

- a single bearer token to call the backend (in `localStorage`)
- the IBKR session lives server-side in `ibeam`, not on the phone
- Alpaca keys live in `backend.env`, not on the phone

Losing the phone never exposes IBKR or Alpaca credentials.

## Why no QuantConnect

An earlier iteration of this repo proxied QC's API to deploy and mirror
strategies. We dropped that path because QC requires a paid org tier
(cheapest is $84/mo) to use the API. For a single-family-member app, that's
not viable.

Strategy authorship stays in the research repo (`trading-system`). The
companion just publishes the regime outputs via `backend/signals/regimes.json`
and exposes a UI to follow them.

## Why ibeam for IBKR

IBKR's Client Portal Web API requires a long-running gateway (`ibeam`)
holding the session. Browsers can't run that. `ibeam` wraps the official
IBKR CP Gateway in Docker and handles 2FA + keepalive.

Session lifetime: IBKR kills CP Gateway sessions every 6–24 hours. The app
prompts the user to tap "Connect IBKR" again when
`/auth/ibkr/status` returns `authenticated: false`.

## Alpaca

Alpaca paper trading is a direct REST integration — no proxy gateway needed.
Family member supplies their own paper key/secret via `backend.env`.

## Signals feed contract

The research repo writes `backend/signals/regimes.json`. Backend serves it
through `GET /signals` (auth required). Schema:

```json
{
  "as_of": "ISO-8601",
  "regimes": [
    {
      "id": "string",
      "name": "string",
      "status": "active|rebalance|warning|inactive",
      "rationale": "string|null",
      "action": {
        "symbol": "string",
        "side": "BUY|SELL",
        "size_pct_of_buying_power": number|null,
        "hold_days": number|null
      } | null
    }
  ]
}
```

If the research repo's schema diverges, update `web/src/lib/types.ts`.

## Order safety rails (TODO)

`POST /orders` is currently a thin proxy. Before pointing it at a live IBKR
account, layer on:

- per-day notional cap (default `DAILY_NOTIONAL_CAP_USD=50`)
- explicit two-tap confirmation in the PWA for live mode
- rate limiting on `POST /orders`
- backend-side veto if `trading_mode=paper` but the gateway is connected to
  a live account
