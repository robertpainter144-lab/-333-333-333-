# Barbell Capital Compounding Trading System

Survival-first, volatility-adjusted barbell system: **core** (80–95% capital) for long-term compounding and **aggression** (5–20%) for volatility expansions and liquidation cascades.

## Structure

| Component | Role |
|-----------|------|
| **CORE (80–95%)** | Volatility-adjusted trend-following, 1–2x max leverage, strict drawdown control |
| **AGGRESSION (5–20%)** | High-vol breakout, regime-scaled (can go to zero), allowed to blow up without affecting core |

## Features

- **Core**: Dual-EMA trend + ATR/vol target position sizing, drawdown scaling, recovery gate
- **Aggression**: Breakout (high/low) + volatility regime classification (low / normal / expansion) to scale or zero
- **Shared**: Volatility regime, funding-rate trim for perps, tail-risk filter (flash-crash pause/reduce)
- **Portfolio**: Barbell allocator, compounding engine
- **Optimization**: Walk-forward (train/test folds, grid over EMAs / vol target)
- **Analytics**: Capital efficiency (Sharpe, Calmar, turnover, leverage)

## Quick Start

```bash
cd trading_barbell
pip install -r requirements.txt
python main.py
```

With your own OHLCV CSV (columns: `open`, `high`, `low`, `close`; optional `funding_rate`):

```bash
python main.py --config config/default.yaml --data path/to/ohlcv.csv --out equity.csv
```

Synthetic data only (no `--data`):

```bash
python main.py --synthetic 504 --out equity.csv
```

## Config

Edit `config/default.yaml` for:

- `barbell`: core vs aggression weights, `aggression_can_zero`
- `core.trend`: fast/slow EMA, vol lookback, vol target
- `core.drawdown`: max DD %, reduction factor, recovery %
- `aggression`: breakout lookback/ATR, regime scales (low / normal / expansion)
- `regime`: lookback, percentiles
- `funding`: enable, lookback, extreme threshold, long trim %
- `tail_risk`: move threshold, pause bars, size reduction
- `walk_forward`: train/test/step bars

## Walk-Forward

```bash
python scripts/run_walk_forward.py
```

Customize the param grid and objective in the script (`sharpe`, `sortino`, `calmar`).

## MVP: Core Only

To run **core only** (no aggression):

- Set `aggression_pct` to 0 and `core_pct` to 1 in config, or
- Use `BacktestConfig(use_aggression=False)` in code.

Aggression sleeve can be added in phase 2 without changing the core engine.

## Layout

```
trading_barbell/
├── config/default.yaml
├── main.py
├── requirements.txt
├── backtest/engine.py      # BarbellBacktest, config loading
├── src/
│   ├── core/               # trend_following, position_sizing, drawdown_control
│   ├── aggression/         # breakout, regime_scaling
│   ├── shared/             # regime, funding, tail_risk
│   ├── portfolio/          # allocator, compounding
│   ├── optimization/       # walk_forward
│   └── analytics/          # capital_efficiency
└── scripts/run_walk_forward.py
```

## License

Use at your own risk. Not financial advice.
