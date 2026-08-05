---
name: QC Portfolio Manager
description: New trading program — top-5 QC leaderboard strategies, 2x winner weighting, daily rebalance, full GUI
type: project
originSessionId: b287fa6f-7470-4542-883a-fbd1ab9f33a4
---
## Location
`/Users/quarki3/qc_portfolio/`

## Files
| File | Purpose |
|------|---------|
| `main.py` | Entry point. `--qc-refresh`, `--rebalance`, or GUI |
| `config.py` | All constants: Alpaca keys, strategy universes, stop-loss params |
| `qc_api.py` | Polls `quantconnect.com/api/v2/strategies/list`, sorts by `oos 3m return` |
| `market_data.py` | yfinance price history + Alpaca REST helpers |
| `momentum.py` | Vol-adj composite momentum, strategy allocation |
| `portfolio_engine.py` | State management, target computation, stop-loss, CSV logging |
| `backtest.py` | Walk-forward daily backtest, stop-loss sweep |
| `gui.py` | Tkinter+matplotlib GUI — 7 tabs |

## Run
```bash
python3.10 /Users/quarki3/qc_portfolio/main.py              # launch GUI
python3.10 /Users/quarki3/qc_portfolio/main.py --qc-refresh # re-rank strategies
python3.10 /Users/quarki3/qc_portfolio/main.py --rebalance  # force rebalance
```

## QC API
- URL: `https://www.quantconnect.com/api/v2/strategies/list` (public, no auth)
- Stats field names use spaces: `'oos 3m return'`, `'oos 3m sharpe'`, `'5y drawdown'`
- 385 public strategies; top 5 today: 245 (166%), 46 (122%), 315 (85%), 123 (84%), 60 (81%)

## Weighting formula
- Base: proportional to `oos 3m return`
- Winner: ×2 then renormalize → winner gets ~47% today

## Current top 5 weights (2026-06-04)
1. Tech Momentum Winner Rotation [245]: 47.3%
2. TheOmniscientParadox [46]: 17.3%
3. Mandelbrotian Vol Clustering [315]: 12.0%
4. High-Conviction Mega-Cap vs QQQ [123]: 11.9%
5. Leveraged ETF Momentum Allocator [60]: 11.4%

## Alpaca
- Same paper keys as trading_swarm (PKNEW4EW6GHCKCDDLZAZSUFBQZ)
- Swap to live keys in config.py when real account approved

## Stop-loss
- Position: 5% trailing stop
- Portfolio: 15% drawdown → liquidate to 50% cash

## Backtest results (1Y, strategies 245+46+123)
- CAGR: 152.5%, Sharpe: 2.02, Max DD: 35.3%, Win Rate: 66%

## GUI tabs
1. Overview: wallet balance, master pie, equity curve, transaction log
2-6. Strategy 1-5: per-strategy pie, price charts for each universe asset
7. Backtest: controls + equity curve vs SPY + drawdown chart

## TODO
- Add automated daily scheduler (cron or schedule library) for 9:31 ET rebalance
- Wire --rebalance into launchd/crontab
- Swap Alpaca keys when live account approved
