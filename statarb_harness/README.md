# Stat-Arb Harness (rebuild)

Rebuilt from scratch 2026-08-12 to the spec of the sandbox harness Robert's
dad built on claude.ai (his files pending). Five modules: `data.py` (frame
contracts + synthetic panel), `signals.py` (illiquidity-scaled reversal +
SUE-decay PEAD), `portfolio.py` (hysteresis, dead-band, beta hedge, trend
overlay, downside-dev targeter), `execution.py` (T+1, sqrt-impact costs,
delist handling), `backtest.py` (metrics, ablations, 20-random noise floor).

Run: `python3 backtest.py` (full, writes results.json) or `--quick`.

## Reproduction status vs the reference harness

| Finding | Reference | This rebuild |
|---|---|---|
| Naive re-ranking dies from costs | 104x, instant death | 96x, −3.7%/yr |
| Hysteresis makes it survivable | 48x, +7%, Sortino 1.70 | 45.7x, +8.0%, Sortino 1.33 |
| Cost drag stays binding | ~20%/yr | 17.9%/yr |
| Hedged DD vs index | −9% vs −53% | −10.5% vs −56% |
| Trend overlay on a hedged book cuts alpha, not tail | 10.4% vs 7.2% off/on | 8.0% vs 5.2% off/on |
| Noise floor | 0.90 | −4.1 (random books churn ~90x through full costs) |

Numbers moved DOWN when a 14-agent adversarial review fixed five defects
(hedge P&L timing, vol-of-|r| understating impact, delist exit costed at
pre-crash weight, dead-band disabled whenever gross≠1, plus calibration) —
the honest direction. Synthetic magnitudes prove machinery, not edge.

## Go/no-go checklist before believing ANY real-data number

1. Point-in-time universe incl. delisted names (no survivorship); delist
   terminal prints realized, not dropped.
2. Earnings/SUE timestamped at first tradeable moment, not filing date.
3. Spreads/ADV measured from the same era being backtested.
4. Walk-forward split with the deflated Sortino (module not yet built);
   parameter choices frozen on train, reported on test only.
5. Turnover × measured cost per leg reconciled against a real fill sample.
6. Beat the same-machinery random noise floor by a wide margin.
7. Capacity sanity: strategy $ scaled to ADV participation ≤ 1%.

## Next steps (from the reference author, unchanged)

- Wire a real vendor (Alpaca daily bars cover the reversal sleeve today;
  PEAD needs point-in-time earnings — Sharadar or accumulated Nasdaq
  calendar snapshots).
- Walk-forward / deflated-Sortino validation module.
- Residual-momentum third sleeve to dilute the reversal book's turnover.

Not a deployment recommendation. This strategy class needs a margin account
and materially more capital than the current live book.
