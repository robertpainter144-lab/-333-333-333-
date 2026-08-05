---
name: Live Trading Dashboard & Bot
description: Full state of the NiceGUI unified dashboard, Tkinter legacy, bot files, security, and Android access
type: project
originSessionId: ffc1e014-1722-4f29-92d2-4ccd6f3cf887
modified: 2026-08-04T18:44:55.150Z
---
## 2026-07-27 updates
- **Legacy launchd duplicate removed**: `com.quarki3.dashboard` (no live keys) used to race `com.quarki3.ng-dashboard` for :8080 and win, making /api/portfolio report a $50 stub for alpaca_live. Booted out, plist archived in `~/Library/LaunchAgents/disabled/`; stack_watchdog now watches the `ng-dashboard` label. If the dashboard ever reports live equity exactly $50.00/0 positions again, check for a keyless orphan on :8080 first.
- **GUI redesign in flight via Lovable**: private repo `Quarki33/trading-gui` holds LOVABLE_PROMPT.md + API.md (verified contract). Lovable syncs code into that repo; it's in the autopush roster. Backend API unchanged (CORS `*`, per-request `x-api-key`).

## Current architecture (as of 2026-06-11)

### New: NiceGUI unified dashboard
| File | Purpose |
|------|---------|
| `/Users/quarki3/ng_dashboard/main.py` | NiceGUI web dashboard — 9 pages, serves on 0.0.0.0:8080 |
| `/Users/quarki3/alpaca_keys.py` | Loads all Alpaca keys from macOS Keychain (never hardcoded) |
| `/Users/quarki3/setup_keys.py` | One-time script — stores keys in Keychain under service "alpaca_trading" |
| `/Users/quarki3/TAILSCALE_SETUP.md` | Install guide + launchd config for Android access |

### Legacy Tkinter dashboard (kept, still works)
| File | Purpose |
|------|---------|
| `/Users/quarki3/trading_dashboard.py` | Tkinter GUI: 11 tabs — now loads keys from alpaca_keys.py |
| `/Users/quarki3/mock_portfolio.py` | Trading bot — now loads keys from alpaca_keys.py |
| `/Users/quarki3/qc_portfolio/config.py` | QC portfolio config — now loads paper keys from alpaca_keys.py |

## Running the new dashboard
```bash
nohup python3.10 /Users/quarki3/ng_dashboard/main.py >> /tmp/ng_dashboard.log 2>&1 &
# Access at http://localhost:8080 or http://<tailscale-ip>:8080 from Android
```

## Running the bot
```bash
nohup python3.14 mock_portfolio.py >> /tmp/portfolio_bot.log 2>&1 &
```

## Security
- All Alpaca keys stored in macOS Keychain under service "alpaca_trading"
- Keys: broker_key, broker_secret, broker_acct (broker sandbox), paper_key, paper_secret (paper trading)
- Zero credentials in any source file
- Dashboard bound to 0.0.0.0:8080; use Tailscale for remote access

## Android access
- Install Tailscale on Mac + Android, sign in with same account
- Get Mac IP: `tailscale ip --4` (100.x.x.x)
- Android browser: http://100.x.x.x:8080

## NiceGUI dashboard pages
1. Portfolio — positions, equity chart, P&L chart, activity log
2. Bot Controls — start/stop portfolio bot + SPY trader
3. Global Markets 24/7 — after-hours scanner (forex, crypto, intl ETFs)
4. US Regular Hours — RTH scanner (S&P 100 + momentum)
5. Scanner — manual market scan
6. Daily Review — movers, misses, suggestions, pre-market picks
7. Swarm AI — equity chart, bot cards, regime/fear
8. Backtest — run trading_swarm.py backtest with live chart
9. QC Portfolio — strategy cards, equity curve, rebalance button

## Alpaca accounts
- Broker sandbox: CKQNTXLUDOP3OA7OOEITP2UXD6 / acct 457113cf
- Paper trading: PKNEW4EW6GHCKCDDLZAZSUFBQZ

## Transfer-aware P/L (2026-08-04)
- `/api/transfers/{source}` (ng_dashboard/api.py): Alpaca CSD/CSW via raw REST
  (VM alpaca-py lacks `get_account_activities`), 10-min cache, OWNER-ONLY
  (not in _GUEST_PATHS). `_build_portfolio` alpaca_live starting_capital =
  contributed capital (Σ deposits − withdrawals), so unrealized_pl = true P/L.
- Terminal EquityChart (trading-gui): Robinhood semantics — window P/L nets
  out transfers, ▲/▼ markers snapped to the day's equity step, "net" toggle
  plots deposit-adjusted curve; Book vs-baseline deltas subtract transfers.
- Chart gestures: poll no longer resets zoom/pan; native non-passive wheel;
  edge-bracketed line drawing. Deploy = Mac `npm run build` in trading-gui,
  rsync dist/ to VM (~/trading-gui/dist), `sudo systemctl restart
  quarki-dashboard` (port 8080).

## Still TODO
- Install Tailscale (user action — see TAILSCALE_SETUP.md)
- Test on live markets after Android access is set up
- Swap to live Alpaca keys when real account approved
- Optional: launchd auto-start for dashboard + bot on login
