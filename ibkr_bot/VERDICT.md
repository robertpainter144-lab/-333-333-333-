# IBKR Stop-Loss System — Verdict

Built 2026-06-17, ~1 hour session. Account `U24971994`, $500 funded.

## The strategy in one line

**Per-position OCA bracket (TRAIL + STOP) sized from adaptive market context,
plus a watcher daemon for time-stop and profit-step rules native orders can't
express.**

## Three-layer defense (all enforced by IBKR server-side or polled every 5min)

### 1. Hard stop — `entry × (1 − min(10%, 2 × ATR%))`
- Static floor; never widens once placed.
- Sized from per-symbol ATR so volatile names get more room (capped 10%).
- Server-side STOP order (GTC, outsideRth). Fires even if Mac is off.

### 2. Adaptive trail — `BASE_K × ATR × VIX_mult × SPY_trend × burst_mult`
- BASE_K = 2.25; clipped to `[2%, 8%]`.
- Server-side TRAIL order using `trailingPercent` (recomputed by IBKR each tick).
- Refresh: watcher modifies the live trail % if regime shifts > 30%.

### 3. OCA group binds (1) + (2)
- First leg to fill cancels the other. IBKR-native — no race conditions, no
  "double-sell" bugs like the PoC10 phantom-position scenario.

## Watcher daemon (`launchd` job, ticks every 5min)

Three rules native orders can't do:

| Rule         | Trigger                                           | Action                                                    |
|--------------|---------------------------------------------------|-----------------------------------------------------------|
| Auto-attach  | Position exists, no bracket on file               | Compute adaptive trail + hard stop, place OCA pair        |
| Profit-step  | price ≥ entry × (1 + 2 × ATR%) AND qty ≥ 2 shares | Cancel bracket, sell half, re-attach bracket at break-even|
| Time-stop    | Held ≥ 7 days, price < entry × 1.01               | Cancel bracket, market-sell remaining position            |

## Why this beats simpler alternatives

| Variant                    | Pros                          | Cons                                              |
|----------------------------|-------------------------------|---------------------------------------------------|
| Single fixed-% stop        | Trivial to implement          | Tight stop on AMD = whipsaw; loose on KO = wasted |
| Bot-side stops only        | Total flexibility             | Bot crash = position naked. (PoC10 caught this.)  |
| Native trail only          | Server-side, robust           | Doesn't know about regime; no time/profit rules   |
| **This hybrid**            | Server enforces baseline + bot adds intelligence | More moving parts; needs IB Gateway always logged in |

The hybrid was the choice. If watcher dies, IBKR still has the trail + hard stop.
If IB Gateway disconnects, the GTC orders persist on IBKR's books — only fill
events get delayed until reconnect.

## Current trail % examples (live market context)

```
F     trail= 8.00%  atr=4.88%  → 1 share already queued @ $14.44
SPY   trail= 3.85%  atr=1.37%
AAPL  trail= 7.41%  atr=2.63%
NVDA  trail= 8.00%  atr=4.03% (cap)
AMD   trail= 8.00%  atr=7.27% (cap)
INTC  trail= 8.00%  atr=8.71% (cap)
BAC   trail= 5.74%  atr=2.04%
KO    trail= 6.07%  atr=2.16%
```

VIX 16.4, SPY above 200-SMA — current regime is "loose" (vol burst mult 1.25).

## Buy candidates for tomorrow — your call on each

Ranked screener output (universe = liquid US names ≤ $25, 30-day momentum > 0,
filtered against blow-off-top extension):

| Rank | Symbol | Price   | 30d   | 5d    | 90d   | ATR  | Why                                              |
|------|--------|---------|-------|-------|-------|------|--------------------------------------------------|
| 1    | AAL    | $15.71  | +28%  | +12%  | +52%  | 4.2% | Airlines rotation strongest in the screen        |
| 2    | NCLH   | $20.14  | +26%  |  +9%  |  +3%  | 4.9% | Cruise — confirms travel theme; 90d weak though  |
| 3    | RIVN   | $16.68  | +15%  |  -1%  |  +9%  | 7.0% | EV momentum; high vol (ATR 7%, biggest in screen)|
| 4    | PINS   | $21.33  | +12%  |  -3%  | +18%  | 5.5% | Social/ad — pullback after run                   |
| 5    | F      | $14.41  |  +8%  |  -4%  | +25%  | 4.9% | **Already queued (order id 12)**                 |
| 6    | SNAP   |  $5.71  |  +7%  |  +1%  | +23%  | 5.4% | Cheapest name; small starter position            |
| 7    | SOFI   | $17.13  |  +7%  |  +4%  |  -3%  | 6.1% | Fintech; 90d negative (concerning)               |

**My picks for your approval** (4 names, ~$74 deployed, leaves ~$412 dry powder):

1. **AAL** — strongest momentum, airline sector tailwind
2. **NCLH** — travel sector diversification within momentum theme
3. **PINS** — non-cyclical exposure (digital ads); momentum confirmed by 90d
4. **SNAP** — cheap starter, momentum across timeframes, $5.71 = lowest-blast

I'd skip RIVN (vol too high for stops to be meaningful at this size), SOFI (90d
weakness contradicts 30d strength), NCLH/AAL together might be sector-correlated
(pick one to reduce theme concentration if you'd rather diversify further).

## How to approve

Reply with the tickers you want me to buy, like:

> approve AAL PINS SNAP

I'll fire 1-share BUY limits, GTC, outsideRth=True. Watcher auto-attaches stops
on fill. Reject anything by omitting it.

## Outstanding gaps (next session)

- IBKR positions don't show up on the NiceGUI dashboard yet — purely terminal /
  JSON readout for now.
- No automated signal-driven entries. Every new position is approved by you.
  Reasonable for "mother bird" mode; will revisit when the watcher has earned
  trust over a few weeks.
- Single account, single broker — no cross-broker risk distribution.
