---
name: SPY Adaptive Regime Backtest Project
description: Full state of the SPY open-range trend-follow backtest — files, best configs, 5-year results, and next steps
type: project
originSessionId: ccb05ad4-58b5-46c7-a51f-023d0fe056f0
---
## What this project is

Iterative SPY 5-min open-range trend-follow backtest with adaptive regime filtering. Uses Alpaca for 5-year historical data. Strategy: enter long (or short in bear regime) when close crosses EMA during 09:30–11:00 ET window; exit via trailing ATR stop.

## Working directory

`/Users/quarki3/`

## Data

- `spy_5min_bars.csv` — 97,334 bars, May 7 2021 → May 6 2026 (RTH only, 5-year)
- Fetched via Alpaca sandbox API: `https://data.sandbox.alpaca.markets/v2/stocks/bars`
- Keys: `ALPACA_KEY="CK4HXKGM3HWRKLXWRU3CDLB6LX"`, `ALPACA_SECRET="7i1mhApwTyzqWprLyvGvbzuDJbNSquMRTCnoiXfJSEWk"`

## Files

| File | Purpose | Key output |
|------|---------|-----------|
| `spy_ort_backtest.py` | Original backtest, Alpaca data fetch, EMA34 baseline | Sharpe +0.35, +$37,247, DD -24.2% |
| `spy_ort_sweep.py` | Sweep: EMA × trail × entry window × EOD close | Best: EMA25, 3.0×, eod=N → Sharpe +0.68, +$168,495, DD -18.2% |
| `spy_ort_adx_sweep.py` | Sweep: ADX max/period/short side | Best: adx<35, short=Y, adxper=20 → Sharpe +0.86, +$135,787, DD -10.3% |
| `spy_adaptive.py` | **CURRENT BEST** — Full adaptive regime system | Sharpe +0.90, +$138,777, DD -10.7% |

## Current best configuration

**Script**: `spy_adaptive.py`

**Fixed parameters**:
- EMA(25), ATR(14), ADX(20), long trail = 3.0×ATR
- ADX threshold = 35 (direction-aware)
- Entry window: 09:30–11:00 ET
- Initial equity: $100k, leverage 2:1, commission $0.005/share

**Sweep winner** (gap=off, eodvol=off, strl=3.0×, eod=N):
- Sharpe: +0.90
- Net PnL: +$138,777
- Max DD: -10.7%
- n = 1,046 trades over 5 years

**Year-by-year**:
- 2021: +$20,847  DD -4.4%
- 2022: +$3,254   DD -18.0%  (was -$19,854 at baseline — fixed by direction-aware ADX)
- 2023: +$21,975  DD -14.3%
- 2024: +$18,944  DD -12.0%
- 2025: +$53,746  DD -18.4%  (tariff shock April 2025 — still profitable year)
- 2026: +$20,012  DD -12.8%  (partial year)

## Regime logic (direction-aware ADX)

```python
def classify_regime(adx, pdi, ndi):
    if adx <= 35:
        return "ranging"
    return "bull_trend" if pdi >= ndi else "bear_trend"
```

- `ranging` / `bull_trend`: enter long when close > EMA
- `bear_trend`: enter short when close >= EMA (rally into EMA in downtrend)
- Shorts: always force-close at EOD (15:50), short trail = 3.0×ATR
- Longs: hold until trailing stop hit

## Key learnings

1. **Direction-aware ADX is the critical fix**: old ADX<35 blocked longs even in bull trends (killed 2023/2024 performance). New logic: only block longs when ADX>35 AND NDI>PDI. This turned 2022 from -$19,854 to +$3,254 with minimal impact on other years.
2. **Gap filter and vol filter counterproductive**: both hurt Sharpe significantly by blocking too many good trades.
3. **EOD vol-close doesn't fix April 2025**: the tariff shock was a pre-announcement surprise — ATR wasn't elevated before entry. No day-of filter can catch it. Fully intraday mode (close all at EOD) costs ~0.06 Sharpe.
4. **Short trail 3.0× beats 2.5×**: bumping short trail from 2.5× to 3.0× consistently improved Sharpe across all configs.
5. **Short regime adds trades but modest PnL**: ~10-12 short trades/year in bear_trend periods; directional filter prevents shorting bull trends.

## Progress history

| Round | Sharpe | Net PnL | Max DD |
|---|---|---|---|
| EMA34 baseline | +0.35 | +$37,247 | −24.2% |
| Param sweep (EMA25, 3×) | +0.68 | +$168,495 | −18.2% |
| ADX filter | +0.86 | +$135,787 | −10.3% |
| Adaptive v1 | +0.88 | +$133,060 | −11.4% |
| Adaptive v2 (current) | +0.90 | +$138,777 | −10.7% |

## What to explore next

- Minimum account for 1 MES contract: ~$1,285 IBKR margin; multiply SPY share sizing by $5/point to estimate ES/MES equivalents
- Run fully intraday sweep (EOD close all) to quantify the Sharpe tradeoff explicitly
- Consider dynamic ADX threshold (25 vs 35 vs 40) as a separate sweep — current 35 is fixed
- APril 2025-style shock protection: the only clean defense is fully intraday; decide if the Sharpe cost (~0.06) is worth the protection

**Why:** Goal is a systematic SPY/ES strategy for live trading through IBKR. 5-year backtest Sharpe +0.90 with reasonable DD and year-over-year consistency.
