---
name: Prop-Desk-Lite Multi-Strategy
description: Multi-strategy crypto framework at ~/scanner/ — momentum + mean-rev + breakout with vol-normalization, cluster caps, regime detection, Kelly-lite sizing. Signal-only.
type: project
originSessionId: c777b35a-8784-4ed8-b477-cb1bcb76d2f3
---
Built 2026-07-08 as pivot from single-strategy retune loop. Robert asked "what if we just emulate top prop desks" — I flagged the retune was only optimizing one lever and proposed multi-strategy blend. He said "yes."

**Location:** `/Users/quarki3/scanner/`

**Architecture:**
- `strategies/base.py` — Signal dataclass, realized-vol calc, RSI/ATR/high helpers, cost model (0.35% roundtrip Alpaca crypto), UNIVERSE (34 Alpaca-tradeable coins), CLUSTERS (majors/l1s/memes/defi/aidepin/other).
- `strategies/momentum.py` — enter when 2m velocity ≥ 1.5σ of 1h vol AND accelerating (accel ≥ 0.02pp). Exit on sigma reversal (<-0.5σ) or -1σ drawdown from peak.
- `strategies/mean_reversion.py` — majors-liq only (BTC/ETH/SOL/XRP/DOGE). RSI15 < 25 = BUY, > 75 = SELL. Target = vol%; stop = -2× vol.
- `strategies/breakout.py` — new 15m rolling high AND 2m step > 1.2× ATR15m. Exit on failed breakout or 1× ATR trail-back.
- `portfolio.py` — regime detector (BTC 1h vol: >0.6=trending, <0.25=chop, mid=all), cluster cap (1 open per cluster), cost gate (skip if expected_move < 0.70%), Kelly-lite (base $3 × min(2.0, score/2)), ensemble bump (×1.5 if ≥2 strategies BUY same sym).
- `sources/prop_desk.py` — orchestrates: fetch prices, append per-token series, call portfolio.manage(), emit ACT_ENTRY/ACT_EXIT signals + heartbeat.
- `evaluate.py` — reads state/prop_ledger.jsonl. Reports cumulative P&L. Verdict: PROFITABLE (≥5 trades, +total, +mean) / UNPROFITABLE (≥5 trades, ≤ −0.5%) / BUILDING.
- `state/prop_hold.json` — open positions (entry_px, peak_px, size, reasons, cfg_ver).
- `state/prop_ledger.jsonl` — closed trades: sym, strategy, entry/exit ts+px, pnl_pct.

**Paper-execution phase (as of 2026-07-10).** No longer signal-only — sources/prop_desk.py now submits real orders to Alpaca via env var `ALPACA_MODE` (paper|live|signal_only, default paper). Reads `ALPACA_PAPER_KEY`/`ALPACA_PAPER_SECRET` from env. Notional min $10 (Alpaca crypto minimum). First closed real trade: 2026-07-10 PEPE momentum, −0.72% net. Whale-primary ETH $10 open. Signals stream to app via existing /api/whales as `prop_desk/ACT_ENTRY` and `prop_desk/ACT_EXIT` alerts.

**Vol scaling fix (2026-07-10):** realized_vol_pct(series, horizon_s) now uses proper √T scaling — was returning per-step std (mis-treated as hourly vol, ~15× too small at 15s cadence). All strategies now use horizon_s in seconds (typ_2m=120, typ_15m=900, hourly=3600) so expected_move targets are realistic and cost gate is passable. Regime thresholds lowered accordingly: HIGH=0.4% (was 0.6), LOW=0.10% (was 0.25) — a chop BTC 1h vol of 0.15% now puts us in MIXED regime so all three strategies are eligible.

**Whale confluence layer (2026-07-10):** portfolio.py::load_whale_confluence() reads last 30min of state/signals.jsonl (non-prop_desk sources), aggregates per-Alpaca-symbol weighted score:
- Bullish: coordinated_accumulation +3.0, whale_trade +2.0, cex_withdrawal +1.5, extreme_funding +1.2, large_transfer +1.0, dex_momentum +1.0
- Bearish: cex_deposit −1.5, big_loser_24h −0.6
Amplifier: BUY size × (1 + score × 0.3), capped 3×.
Whale-primary: if score ≥3.5 and no algo signal, open $10 position anyway with strategy="whale_primary".
Heartbeat shows `[whales 30m] SYM:+score(hits) ...` every tick.

**Retirees:**
- `sources/momentum.py` + `retune.py` still exist but replaced by prop_desk in main.py's SOURCES list. Ledger `momentum_ledger.jsonl` is frozen at prior state (6 trades, -0.59% total on v1).

**Why:** single-strategy retune loop was optimizing thresholds on one edge in a chop market. Prop desks don't tune one strategy — they run orthogonal strategies gated by regime, vol-normalize entries so BTC/SHIB comparable, cap correlated exposure, and size by conviction. Explicitly not perfect but the RIGHT frame.

**How to apply:**
- Signal-only until user approves execution. When approving, wire `sources/prop_desk.py:tick()` to place orders through the existing Alpaca live keys (`ALPACA_LIVE_KEY` / `ALPACA_LIVE_SECRET` env, isolated from paper).
- Adding a strategy = new `strategies/<name>.py` with `evaluate(sym, held) -> Signal | None`, register in `portfolio.STRATEGIES`.
- 10-min autonomous P&L check loop is running via ScheduleWakeup. Verdict UNPROFITABLE will pause and ask user before proceeding.
