# Trading Stack API Contract

Backend: FastAPI inside `ng_dashboard` on the Mac, reachable two ways:

| Access | Base URL | Auth |
|---|---|---|
| Public (anywhere) | `https://129-153-96-201.sslip.io` | **required** — `x-api-key` header (preferred) or `?key=` query param on every request |
| Tailnet/local | `http://100.x.y.z:8080` / `http://localhost:8080` | none (direct clients bypass the gate) |

Keys live in `~/trading-data/dashboard_keys.json` on the Mac: `owner` unlocks everything; `guest` unlocks only `/api/health`, `/api/(portfolio|equity|activity)/(paper|broker|shadow)`, `/api/ticker/*`. **Never hardcode a key in the frontend — prompt the user once and keep it in localStorage.**

CORS: `allow_origins=["*"]`, methods GET/POST — a hosted frontend can call the public base directly.

## Core endpoints (all GET unless noted)

### `/api/health`
`{"ok":true,"ts":"2026-07-27T11:00:46-04:00","host":"...","tailscale_ip":"100.x.y.z","version":1}`

### `/api/sources`
Registry of money sources with display colors:
```json
[{"id":"alpaca_live","label":"Alpaca Live","kind":"live","real_money":true,"color":"#ffd60a"},
 {"id":"paper","label":"Alpaca Paper","kind":"paper","real_money":false,"color":"#58a6ff"},
 {"id":"broker","label":"Alpaca Broker SB","kind":"paper","real_money":false,"color":"#bc8cff"},
 {"id":"shadow","label":"Shadow Bot","kind":"test","real_money":false,"color":"#d29922"},
 {"id":"ibkr","label":"IBKR Live","kind":"live","real_money":true,"color":"#3fb950"}]
```

### `/api/portfolio` and `/api/portfolio/{source}`
Array (or single object) of:
```json
{"source":"alpaca_live","equity":48.10,"cash":17.7,"bp":17.7,"unrealized_pl":-1.2,
 "starting_capital":50.0,"peak_equity":50.08,"drawdown_pct":3.95,
 "positions":[{"symbol":"AAPL","qty":0.021,"avg_cost":337.47,"market_price":338.57,
               "market_value":7.24,"unrealized_pl":0.02,"unrealized_pl_pct":0.32}],
 "position_count":9,"ts":"2026-07-27T11:00:46-04:00"}
```
`alpaca_live` is the real-money $50 account. IMPORTANT: drawdown vs `$5` absolute is the user's review trigger.

### `/api/equity/{source}`
Equity time series for charts. `alpaca_live` reads a CSV logged every ~30 s since 2026-07-13.

### `/api/activity/{source}` (`?limit=100`)
Recent fills/actions, newest first.

### `/api/qc`
QC engine + live mirror state. Notable fields: `initial_equity`, `peak_equity`, `exposure_level`, `tiers_fired`, `session_open_pl`, `ratchet_floor`, `strategies[]` (id, name, universe, alloc_mode, weight, holdings, psr_5y, cagr_5y, sharpe_1y, return_3m, drawdown_5y), `mirror_pnl_pct`, `mirror_mode` ("live"), `mirror_bankroll`, `mirror_targets` {sym: pct}.

### `/api/edge`
The **edge ledger** — the honest scoreboard. `{"ts":..., "strategies":[{"name","mode":"paper|live|backtest-only","n_closed_trades","win_rate","expectancy_pct","sharpe","total_pnl_pct","last_trade_ts","verdict":"PROVEN|PROMISING|LOSING|UNPROVEN","note"?}]}`
Verdict gate: n≥30, expectancy≥0.05%, sharpe≥1 → PROVEN.

### `/api/strategies`, `/api/strategies/{name}`, POST `/api/strategies`, POST `/api/strategies/{name}/config`
Prop-desk strategy roster + per-strategy detail/config.

### `/api/insider`, `/api/insider/leaderboard`, `/api/insider/ledgers`, `/api/insider/portfolio`, `/api/insider/backfill`
Insider-trading source data.

### `/api/patent-cliff`
Patent-cliff scanner ranking.

### `/api/ticker/{symbol}`
Quote/series for a symbol (guest-accessible).

### `/api/bots`
`[{"id":"qc_local","name":"QC Portfolio (Mac)","host":"mac","running":true,"pid":83884}, ...]`

### `/api/summary`
Everything for a single overview render: all sources with equity/positions inline.

### `/api/version`, `/api/public-url`, `/api/whales`, `/api/prop_desk/state`, `/api/market_pulse`, `/api/company/{ticker}`
Ancillary.

## Scanner state files surfaced through the API
The governor/mirror internals are file-backed on the Mac. If an endpoint is missing for something below, ask the stack owner to expose it (5-line FastAPI addition) rather than guessing:
- `governor weights` — current source weights (qc 0.92 as of 2026-07-27), reason, rolling_pnl, n_obs
- `strategy_governor_ui.json` — snapshot {combined_LIVE, qc_PAPER, patent_PAPER, congress_PAPER, autopilot_PAPER}, leader, consecutive_leads, recent_alerts
- per-mirror state: targets, positions, pnl_pct, last_actions, buy_cooldowns, settled_bp

## Semantics that must render truthfully
1. **Live baseline**: the honest live-performance test window starts 2026-07-27 at $48.06. Show P/L vs that baseline prominently, alongside inception (-4.0% since 2026-07-13, $50.04).
2. **Settlement**: `settled_bp` may be ~$0 while `cash` is positive (cash-account T+1). Pending target buys are *deferred, not failed*.
3. **Paper mirrors are evidence, not money**: label Lab data clearly as paper.
4. **The $5 drawdown trigger**: live drawdown ≥ $5 absolute = red alert state everywhere.
