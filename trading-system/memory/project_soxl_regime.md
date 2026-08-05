---
name: SOXL Regime Strategy
description: Risk-first short-hold SOXL strategy with 5-condition regime gate — file location, key results, design decisions, live paper deployment
type: project
originSessionId: af772715-dae0-4800-b996-4e54437a686a
modified: 2026-07-20T17:17:44.796Z
---
Strategy file: /Users/quarki3/soxl_regime_strategy.py
Paper bot: /Users/quarki3/soxl_paper_bot.py (LIVE_PAPER_ENABLED=True as of 2026-07-20)
Result JSON: /Users/quarki3/swarm_results/completed/soxl_regime_strategy.json
Fix summary: /Users/quarki3/swarm_results/completed/soxl_fixes.json
Daily cache: /Users/quarki3/daily_regime_cache.csv (SMH/SPY/VIX from yfinance)

**2026-07-20 lookahead fix**: the regime gate's SMH/SPY/VIX conditions
(`cond_smh_trend`/`cond_adx`/`cond_spy`/`cond_vix`) were built from
same-day closes while only SOXL's own daily signals were shift(1)'d. Fixed
by shifting all four external conditions by 1 day. The prior "Sharpe 1.211,
MaxDD -7.8%" numbers were inflated by this bug and are superseded below.
`soxl_paper_bot.py`'s live gate (`check_regime_gate()`) was never affected —
it already used `iloc[-2]` (last fully-closed day) for every signal.

**Backtest results (5yr May 2021-May 2026, $100k initial) — corrected, lookahead-free**:
- Sharpe (daily): +0.817, CAGR: +8.31%, MaxDD: -12.90%
- Win rate: 53.1%, Profit Factor: 1.430, 162 trades
- Avg hold: 1.73 trading days, ~30% of time in market
- $100k → $149,040 final equity

**5-condition regime gate** (all prev-day shifted, no look-ahead — now actually true post-fix):
1. SMH close > 50-EMA AND 20-EMA > 50-EMA (semi trend up)
2. SMH ADX(14) > 20 (trending, not chopping)
3. SPY close > 200-SMA (risk-on macro)
4. VIX < 25 (volatility cap)
5. SOXL 20-EMA > 50-EMA AND close > 50-EMA (SOXL self-trend — critical filter that blocks 2022 bear rally false entries; reduces gate from 44% → 27.3% of days)

**Entry**: 5-min close > 25-EMA + close > prior 5-min + close > prior trading day's close. Window 9:35-11:00. ONE entry per gate-open day (no re-entries).

**Exits**:
- Trail: 1.25× DAILY SOXL ATR (NOT 5-min ATR — key design decision)
- Partial: sell 50% at +2× daily ATR
- Hard stop: entry - min(8%, 2× daily ATR)
- EOD close only on day 3 (MAX HOLD = 3 days); days 1-2 let trail work overnight
- Sizing: 2% equity risk per trade, max 30% notional

**Key design insights**:
- 1.25x ATR on 5-min bars is too tight for SOXL (fires on noise in 1-2 bars, Sharpe -0.77). Must use DAILY ATR × 1.25 for a 1-3 day hold.
- Forced EOD close every day kills positive expectancy (winners capped at 1-4% intraday, losers hit full hard stops). Let trail work overnight.
- SOXL self-trend filter (condition 5) is the critical addition vs soxl_pyramid_backtest.py — eliminates bear-rally false entries that old script caught.
- soxl_pyramid_backtest.py is flagged broken: negative Sharpe all configs, SHORT entry condition is inverted (close >= ema instead of <ema in bear_trend).
- Always regenerate `daily_regime_cache.csv` after touching gate-condition logic in `fetch_external_daily()` — it caches computed columns and will silently serve stale/buggy values otherwise.

**Live paper deployment (2026-07-20)**:
- soxl_paper_bot.py originally force-closed every position by 15:55 ET daily, which contradicted the strategy (day-3 EOD is correct; days 1-2 need overnight trailing). Fixed to only force-close on day 3 (MAX_HOLD_DAYS), matching the backtest.
- Position state now persists to soxl_bot_state.json (entry price/qty/stops/entry_date) so a launchd restart mid-hold doesn't orphan an overnight position.
- Deployed via launchd: label `com.quarki3.soxl-paper`, wrapper `/Users/quarki3/bin/run_soxl_paper.sh`, plist `~/Library/LaunchAgents/com.quarki3.soxl-paper.plist`, KeepAlive, log at `/tmp/soxl_paper_launchd.log`.
- Uses existing Alpaca paper keys (ALPACA_PAPER_KEY/SECRET from ~/.zshenv via alpaca_keys.py) — same shared paper account as [[project_alpaca_paper_arm]] and others. $100 notional rail, hard stop, -$8 daily loss cutoff, kill-switch at $80 equity all unchanged.
- The shared paper account had a stray untracked 0.018-share SOXL position (~$2.57) at deploy time from an unrelated earlier test; the bot correctly detected the mismatch (broker has SOXL, bot state doesn't) and left it alone rather than touching it, logging a warning.

**Why:** Robert asked for a risk-first short-hold SOXL tool. The old pyramid backtest showed -Sharpe for all configs due to: 1) the script bug, 2) no regime gate, 3) pyramid adds compounding exposure in bear market.

**How to apply:** When suggesting SOXL trades or evaluating SOXL entries, check the 5-condition gate first. Gate open = trade, gate closed = flat/cash. Use the corrected Sharpe 0.817/MaxDD -12.9% figures, not the pre-fix 1.211/-7.8%. The paper bot is live (not just backtested) — check `/tmp/soxl_paper_launchd.log` and `track_record.jsonl` for real fills before re-deploying or re-tuning.
