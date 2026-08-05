#!/usr/bin/env python3
"""
swarm_orchestrator.py — Continuously runs Claude Code agents on trading system tasks.

Two workers run in parallel, each spawning a `claude -p` subprocess for a bounded task.
Results are written by the agent to swarm_results/completed/{task_id}.json.
The dashboard reads swarm_tasks.json and orchestrator.log for live status.

Usage:
    python3 /Users/quarki3/swarm_orchestrator.py

Pause:  touch /Users/quarki3/swarm_results/PAUSE
Resume: rm /Users/quarki3/swarm_results/PAUSE
Stop:   Ctrl-C or SIGTERM
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

# ── Paths & config ────────────────────────────────────────────────────────────
ROOT        = Path("/Users/quarki3")
RESULTS_DIR = ROOT / "swarm_results"
TASKS_FILE  = ROOT / "swarm_tasks.json"
LOG_FILE    = RESULTS_DIR / "orchestrator.log"
PAUSE_FILE  = RESULTS_DIR / "PAUSE"
CLAUDE_BIN  = "/opt/homebrew/bin/claude"

MAX_WORKERS  = 2
TASK_TIMEOUT = 50 * 60   # 50 min hard kill per task
LOOP_SLEEP   = 8         # seconds between queue polls
MAX_RETRIES  = 1

# ── Logging ───────────────────────────────────────────────────────────────────
RESULTS_DIR.mkdir(exist_ok=True)
(RESULTS_DIR / "completed").mkdir(exist_ok=True)
(RESULTS_DIR / "failed").mkdir(exist_ok=True)
(RESULTS_DIR / "logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("swarm")

# ── Task queue ────────────────────────────────────────────────────────────────
_lock = threading.Lock()

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _load() -> dict:
    if TASKS_FILE.exists():
        try:
            return json.loads(TASKS_FILE.read_text())
        except Exception:
            pass
    return {"pending": [], "running": [], "completed": [], "failed": []}

def _save(state: dict):
    TASKS_FILE.write_text(json.dumps(state, indent=2, default=str))

def _task(title, prompt, type_="feature", priority=50) -> dict:
    return {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "type": type_,
        "priority": priority,
        "prompt": prompt,
        "created_at": _now(),
        "started_at": None,
        "completed_at": None,
        "duration_sec": None,
        "status": "pending",
        "result_file": None,
        "worker_id": None,
        "retry_count": 0,
    }

# ── Task prompts ──────────────────────────────────────────────────────────────

T01_WIRE_SWARM = _task(
    title="Wire trading_swarm.py daily trade output → NiceGUI dashboard",
    type_="feature",
    priority=1,
    prompt="""
You are improving the trading system at /Users/quarki3/.

TASK: Wire trading_swarm.py daily trade output into the NiceGUI dashboard.

STEPS:
1. Read /Users/quarki3/trading_swarm.py.
   Find where the final trade decision is output — look for TradeDecision agent,
   place_order() calls, or the final summary print statements.

2. In trading_swarm.py, add code that writes a JSON file after each run:
   Path: /Users/quarki3/swarm_daily_trade.json
   Schema:
   {
     "timestamp": "<ISO datetime>",
     "date": "<YYYY-MM-DD>",
     "symbol": "<ticker>",
     "strategy": "<strategy name>",
     "side": "buy or sell",
     "entry_price": 0.0,
     "stop_price": 0.0,
     "target_price": 0.0,
     "rr_ratio": 0.0,
     "notional": 0.0,
     "score": 0,
     "thesis": "<full reasoning from TradeDecision agent>",
     "mode": "live/paper/scan"
   }
   Place this write in BOTH --live and --scan modes so the dashboard always has fresh data.

3. Read /Users/quarki3/ng_dashboard/main.py.
   Find page_bot() (around line 1930). Add a "SWARM DAILY TRADE" card at the top of
   that page. The card should:
   - Read swarm_daily_trade.json (return empty state if missing)
   - Show symbol in large monospace text, colored by side (green=buy, red=sell)
   - Show: Strategy | Entry $X.XX | Stop $X.XX | Target $X.XX | R:R 1:X.X
   - Show thesis text in a scrollable area (max 8 lines, font-size 0.78rem)
   - Show timestamp in FG2 color at bottom
   - If file missing/old (>24h): show a muted "No trade today — run trading_swarm.py"

4. Match the dark theme: BG="#0d1117", BG2="#161b22", FG="#c9d1d9", GRN="#3fb950", RED="#f85149"

RESULT: Write /Users/quarki3/swarm_results/completed/wire_swarm_trade.json:
{"task": "wire_swarm_trade", "changed_files": ["..."], "summary": "...", "success": true/false}
""",
)

T02_PNL_EXITS = _task(
    title="Add daily P&L target + loss-limit exits to mock_portfolio.py",
    type_="feature",
    priority=2,
    prompt="""
You are improving the trading system at /Users/quarki3/.

TASK: Add daily profit-target and loss-limit exits to mock_portfolio.py.

Background: The system currently closes all positions at 15:45 ET regardless of P&L.
We need "bank the win" and "cut the day short" logic.

STEPS:
1. Read /Users/quarki3/mock_portfolio.py completely.
   Understand: how is day_pnl computed? Where is it tracked? Where does the main loop run?

2. Near the top of MockTrader class (or __init__), add:
   DAILY_PROFIT_TARGET_PCT = 0.025   # close all + halt if day P&L > 2.5% of equity
   DAILY_LOSS_LIMIT_PCT    = -0.012  # close all + halt if day P&L < -1.2% of equity
   And an instance variable: self._day_halted = False

3. In the main run loop, after computing current day P&L, add the check:
   if not self._day_halted:
       day_pct = total_day_pnl / account_equity
       if day_pct >= DAILY_PROFIT_TARGET_PCT:
           # close all positions
           # log "Daily profit target hit: {day_pct:.1%}"
           # set self._day_halted = True, skip all new entries rest of day
       elif day_pct <= DAILY_LOSS_LIMIT_PCT:
           # close all positions
           # log "Daily loss limit hit: {day_pct:.1%}"
           # set self._day_halted = True

4. Reset _day_halted = False at midnight or at the start of each new trading day.

5. Add "profit_target_hit" and "loss_limit_hit" booleans to the daily_review.json output.

RESULT: Write /Users/quarki3/swarm_results/completed/pnl_exits.json:
{"task": "pnl_exits", "changed_files": ["..."], "summary": "...", "success": true/false}
""",
)

T03_REGIME_REFRESH = _task(
    title="Add intraday regime refresh (every 30 min) to mock_portfolio.py",
    type_="feature",
    priority=3,
    prompt="""
You are improving the trading system at /Users/quarki3/.

TASK: Add intraday regime refresh every 30 minutes to mock_portfolio.py.

Background: The system checks SPY regime once at 09:35. If the market reverses mid-day,
the bot keeps holding longs into a bear trend. Fix this.

STEPS:
1. Read /Users/quarki3/mock_portfolio.py.
   Find get_market_regime() or equivalent. Find the main loop's timing logic.

2. Add a last_regime_check timestamp (initialized to 0). In the main loop:
   if time_since(last_regime_check) > 30 * 60:  # every 30 min during market hours
       new_regime = get_market_regime()
       if new_regime != current_regime:
           log(f"Regime changed: {current_regime} → {new_regime}")
           current_regime = new_regime
           if new_regime in ("BEAR", "NEUTRAL"):
               # close all long positions immediately
               # set flag: no new longs until regime flips back
           elif new_regime == "BULL":
               # allow new entries again
       last_regime_check = time.time()

3. Ensure this only runs during market hours (09:30–16:00 ET), not pre-market.

4. Add "regime_changes" list to daily_review.json: each entry = {time, from, to}

RESULT: Write /Users/quarki3/swarm_results/completed/regime_refresh.json:
{"task": "regime_refresh", "changed_files": ["..."], "summary": "...", "success": true/false}
""",
)

T04_UNIVERSE_TRIM = _task(
    title="Trim the 90-symbol universe — cull illiquid/low-Sharpe symbols",
    type_="optimize",
    priority=4,
    prompt="""
You are optimizing the trading system at /Users/quarki3/.

TASK: Trim the mock_portfolio.py symbol universe from ~90 to the highest-quality symbols.

STEPS:
1. Read /Users/quarki3/mock_portfolio.py. Find the UNIVERSE or SYMBOLS list (~90 symbols).
   Extract the full list.

2. Run this analysis script using python3.10:
   import yfinance as yf, numpy as np, pandas as pd
   symbols = [... the list ...]
   results = []
   for sym in symbols:
       try:
           h = yf.download(sym, period="6mo", interval="1d", progress=False, auto_adjust=True)
           if len(h) < 60: continue
           rets = h["Close"].pct_change().dropna()
           vol = h["Volume"] * h["Close"]  # dollar volume
           avg_dvol = vol.mean()
           sharpe = rets.mean() / rets.std() * (252**0.5)
           max_dd = (h["Close"] / h["Close"].cummax() - 1).min()
           results.append({"sym": sym, "sharpe": sharpe, "avg_dvol": avg_dvol/1e6, "max_dd": max_dd})
       except: pass

3. Keep a symbol if ALL of:
   - avg daily dollar volume > $200M  (liquid fills)
   - abs(max_dd) < 0.60  (not a meme stock that crashed 60%)
   - Sharpe > -0.5  (not persistently losing)
   Always keep: SPY, QQQ, TQQQ, SOXL, NVDA, AAPL, MSFT, AMZN, META, TSLA

4. Remove symbols failing 2+ criteria from the UNIVERSE list in mock_portfolio.py.

5. Print and save analysis. Update mock_portfolio.py.

RESULT: Write /Users/quarki3/swarm_results/completed/universe_trim.json:
{"task": "universe_trim", "original_count": N, "new_count": M, "removed": [...],
 "kept": [...], "summary": "...", "success": true/false}
""",
)

T05_CORRELATION_LIMIT = _task(
    title="Add correlation position limit to mock_portfolio.py (max 0.80 corr)",
    type_="feature",
    priority=5,
    prompt="""
You are improving the trading system at /Users/quarki3/.

TASK: Add correlation-based position limiting to mock_portfolio.py.

Background: The system can hold TQQQ + SOXL + NVDA simultaneously — all highly correlated
tech longs. This concentrates risk without diversification benefit.

STEPS:
1. Read /Users/quarki3/mock_portfolio.py.
   Find where new long positions are entered (the buy/enter function).

2. Add a CORRELATION_LIMIT = 0.80 config constant.

3. Before entering any new position, check correlation with open positions:
   a. If fewer than 2 current positions: skip correlation check (nothing to correlate against)
   b. Fetch 20-bar daily close prices for: (new_symbol + all current_positions)
      Use yfinance: yf.download([new_sym] + [p.symbol for p in positions], period="1mo", progress=False)
   c. Compute Pearson correlation of new_sym vs each existing position
   d. If max_corr > CORRELATION_LIMIT: skip entry, log:
      f"Skip {new_sym}: corr {max_corr:.2f} with {most_correlated_sym}"

4. Cache the price history to avoid redundant API calls (store in a dict keyed by symbol+date).

5. The correlation check should add < 200ms overhead (use cached data when available).

RESULT: Write /Users/quarki3/swarm_results/completed/correlation_limit.json:
{"task": "correlation_limit", "changed_files": ["..."], "summary": "...", "success": true/false}
""",
)

T06_SPY_GAP_FILTER = _task(
    title="Add SPY overnight gap filter to mock_portfolio.py pre-market scan",
    type_="feature",
    priority=6,
    prompt="""
You are improving the trading system at /Users/quarki3/.

TASK: Add SPY overnight gap detection to mock_portfolio.py's 08:30 pre-market scan.

Background: When SPY gaps down -3%+ pre-market, gap-up momentum strategies fail badly.
We need to adapt position sizing and entry thresholds to the macro gap environment.

STEPS:
1. Read /Users/quarki3/mock_portfolio.py. Find the pre-market scan logic (~08:30 ET).

2. At the start of the pre-market scan, compute SPY's overnight gap:
   - Fetch SPY pre-market price: use Alpaca's bars API with feed="iex" or "sip",
     timeframe=1min, start=today 04:00 ET — get latest available bar's close
   - Fetch SPY previous close: yf.download("SPY", period="2d")["Close"].iloc[-2]
   - spy_gap_pct = (premarket_price - prev_close) / prev_close * 100

3. Apply adaptive rules based on spy_gap_pct:
   GAP_SURGE  = spy_gap_pct > +3.0  → relax to min_score=38, max_positions=8
   GAP_UP     = +1.0 < spy_gap_pct <= +3.0 → normal operation (current defaults)
   GAP_FLAT   = -1.0 <= spy_gap_pct <= +1.0 → tighten to min_score=42
   GAP_DOWN   = -3.0 <= spy_gap_pct < -1.0 → min_score=48, max_positions=3
   GAP_CRASH  = spy_gap_pct < -3.0 → skip all entries, log "RISK OFF: SPY gap {pct:.1f}%"

4. Store spy_gap_pct and mode in premarket_picks.json under key "spy_gap".

5. If Alpaca pre-market fetch fails: fall back to gap_flat (normal operation), log warning.

RESULT: Write /Users/quarki3/swarm_results/completed/spy_gap_filter.json:
{"task": "spy_gap_filter", "changed_files": ["..."], "summary": "...", "success": true/false}
""",
)

T07_ATR_SWEEP = _task(
    title="ATR trail multiplier sweep → update mock_portfolio.py with best value",
    type_="optimize",
    priority=7,
    prompt="""
You are optimizing the trading system at /Users/quarki3/.

TASK: Find the optimal ATR trail stop multiplier for mock_portfolio.py via backtest sweep.

Background: mock_portfolio.py uses trail = max(2.0 * ATR, 1.2% * price).
The 2.0× may not be optimal. Test 1.25×–3.0× to find the best Sharpe.

STEPS:
1. Read /Users/quarki3/adaptive_universal_backtest.py or /Users/quarki3/spy_dip_pyramid_backtest.py.
   Understand what args they accept and how to run them. Check if they have an --atr-mult flag.
   If not, find the ATR multiplier constant in the file.

2. For the backtest script that exists, run it for 5 symbols: NVDA, AAPL, SPY, TQQQ, AMD
   with ATR multipliers: [1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0]

   Strategy: temporarily edit the atr_mult value in the script, run it, capture Sharpe/return/drawdown,
   repeat for each multiplier. Restore the script to original after.

3. Aggregate results: for each multiplier, compute avg Sharpe across all 5 symbols.
   Best = highest avg Sharpe with max drawdown < 40%.

4. Update the trail stop calculation in mock_portfolio.py:
   old: trail = max(2.0 * atr, 0.012 * price)
   new: trail = max(BEST_MULT * atr, 0.012 * price)
   Add a comment: # optimized by swarm_orchestrator on {date}

5. If no suitable backtest script found: analyze trade log data from strategy_stats.json
   or portfolio_state.json to estimate optimal multiplier from historical trades.

RESULT: Write /Users/quarki3/swarm_results/completed/atr_sweep.json:
{"task": "atr_sweep", "best_mult": X.X, "by_mult": {...}, "symbols_tested": [...],
 "summary": "...", "success": true/false}
""",
)

T08_QC_STOP_SWEEP = _task(
    title="QC portfolio trailing stop % sweep → update config.py with optimal",
    type_="optimize",
    priority=8,
    prompt="""
You are optimizing the trading system at /Users/quarki3/.

TASK: Optimize the trailing stop percentage in qc_portfolio/config.py.

STEPS:
1. Read /Users/quarki3/qc_portfolio/config.py — find TRAIL_STOP_PCT (likely 0.05 = 5%).
2. Read /Users/quarki3/qc_portfolio/backtest.py — understand how to run it.
   Try: python3.10 /Users/quarki3/qc_portfolio/main.py --backtest
   or:  python3.10 /Users/quarki3/qc_portfolio/backtest.py

3. For each TRAIL_STOP_PCT in [0.03, 0.05, 0.07, 0.10, 0.15]:
   a. Temporarily update config.py
   b. Run the backtest (capture stdout)
   c. Parse: total return, max drawdown, number of stop-outs
   d. Restore config.py after each run

4. Pick the value with best total_return that keeps max_drawdown < 30%.
   Prefer fewer stop-outs (frequent stop-outs = whipsaw = friction).

5. Update config.py TRAIL_STOP_PCT to the winner.
   Add comment: # optimized by swarm on {date}

RESULT: Write /Users/quarki3/swarm_results/completed/qc_stop_sweep.json:
{"task": "qc_stop_sweep", "best_pct": X.XX, "results": [...],
 "summary": "...", "success": true/false}
""",
)

T09_IBKR_READINESS = _task(
    title="IBKR ES Mini readiness audit — what's built, what's missing",
    type_="research",
    priority=9,
    prompt="""
You are auditing the trading system at /Users/quarki3/.

TASK: Audit readiness of the Interactive Brokers ES Mini trading integration.

STEPS:
1. Check what IB packages are installed:
   python3.10 -c "from ibapi.client import EClient; print('ibapi installed')" 2>&1
   pip3.10 show ibapi ib_insync 2>&1

2. Read /Users/quarki3/es_backtest.py — what IB connection params does it use?
   What host/port/clientId does it connect to?

3. Read /Users/quarki3/qc_portfolio/ibkr_data.py and ibkr_main.py:
   What order types, contract specs, and account endpoints are implemented?

4. Check if TWS/IB Gateway is running:
   ps aux | grep -iE "tws|gateway|ibgateway" | grep -v grep

5. Try to connect to the default IB port:
   python3.10 -c "
   import socket
   for port in [7497, 7496, 4001, 4002]:
       try:
           s = socket.create_connection(('127.0.0.1', port), timeout=1)
           print(f'Port {port}: OPEN (IB running)')
           s.close()
       except: print(f'Port {port}: closed')
   "

6. Read /Users/quarki3/es_backtest.py and /Users/quarki3/es_mean_rev_5yr.py:
   What's the best ES strategy found in backtests? What were the best params?

7. Document a clear go-live checklist for ES futures.

RESULT: Write /Users/quarki3/swarm_results/completed/ibkr_readiness.json:
{
  "task": "ibkr_readiness",
  "ibapi_installed": true/false,
  "tws_running": true/false,
  "ports_open": [...],
  "built": ["what's already coded"],
  "missing": ["what still needs to be done"],
  "best_es_params": {...},
  "go_live_checklist": ["step 1", "step 2", ...],
  "summary": "...",
  "success": true
}
""",
)

T10_SOXL_OPTIMIZE = _task(
    title="Optimize SOXL bot parameters — profit tier levels and pyramid adds",
    type_="optimize",
    priority=10,
    prompt="""
You are optimizing the trading system at /Users/quarki3/.

TASK: Find optimal profit-taking tier levels and pyramid add thresholds for soxl_improved_bot.py.

Background: soxl_improved_bot.py uses tiered exits:
  33% at +2×ATR, 33% at +4×ATR, trail the rest.
  Pyramid: up to 4 adds at 1×ATR dip intervals.
We want to know if different tiers improve Sharpe.

STEPS:
1. Read /Users/quarki3/soxl_improved_bot.py — find the profit tier constants and pyramid logic.
2. Read /Users/quarki3/soxl_pyramid_backtest.py — understand how to run it.

3. Test these profit tier configurations:
   A. 33%@+1.5ATR / 33%@+3ATR / trail (tighter, takes profit faster)
   B. 33%@+2ATR  / 33%@+4ATR  / trail (current)
   C. 25%@+1ATR  / 25%@+2ATR  / 25%@+3ATR / trail (more granular)
   D. 50%@+2ATR  / trail rest (simpler)

   Run soxl_pyramid_backtest.py for each (or modify the params inline and re-run).

4. Test pyramid add counts: 2, 3, 4 (current), 5 adds.

5. Pick the config with best Sharpe and drawdown < 45% (SOXL is a 3× ETF, expect volatility).

6. Update soxl_improved_bot.py with the winning configuration.
   Add comment: # optimized by swarm on {date}

RESULT: Write /Users/quarki3/swarm_results/completed/soxl_optimize.json:
{"task": "soxl_optimize", "best_config": {...}, "results": {...},
 "summary": "...", "success": true/false}
""",
)

T11_QC_STRATEGY_EXPAND = _task(
    title="Expand QC portfolio from top-5 to top-10 strategies — backtest comparison",
    type_="research",
    priority=11,
    prompt="""
You are improving the trading system at /Users/quarki3/.

TASK: Test whether expanding the QC portfolio from top-5 to top-10 strategies improves risk-adjusted returns.

STEPS:
1. Read /Users/quarki3/qc_portfolio/qc_api.py — understand how strategies are fetched.
2. Read /Users/quarki3/qc_portfolio/config.py — find TOP_N (= 5) and weighting logic.
3. Read /Users/quarki3/qc_portfolio/backtest.py — run the backtest.

4. Run backtest with TOP_N = 5 (current). Record: return, Sharpe, max drawdown.
5. Update config.py to TOP_N = 10. Run backtest again. Record same metrics.
6. Update config.py to TOP_N = 3. Run backtest again.

7. Restore config.py to the TOP_N that gives the best Sharpe / drawdown tradeoff.
   If top-10 is clearly better: update config.py permanently to TOP_N = 10.
   Add comment: # tested by swarm on {date}

RESULT: Write /Users/quarki3/swarm_results/completed/qc_top_n.json:
{"task": "qc_top_n", "best_n": N, "results_by_n": {...},
 "summary": "...", "success": true/false}
""",
)

T12_SPY_ADAPTIVE_BACKTEST = _task(
    title="Backtest spy_adaptive.py with 1Y data — identify best regime filter params",
    type_="research",
    priority=12,
    prompt="""
You are researching the trading system at /Users/quarki3/.

TASK: Run a comprehensive backtest of spy_adaptive.py and find the best regime filter parameters.

STEPS:
1. Read /Users/quarki3/spy_adaptive.py — understand all configurable parameters
   (EMA period, ATR mult, ADX threshold, ADX direction filter, entry times, etc.)

2. Check if it accepts CLI args. If so, run it. If not, identify the 3 most impactful
   parameters and do a small sweep (3 values each = 27 combinations max).

3. Run:  python3.10 /Users/quarki3/spy_adaptive.py 2>&1 | tail -30
   Capture the output — look for Sharpe, CAGR, max drawdown, win rate.

4. Identify the parameter combination with best Sharpe (require Sharpe > 1.0).

5. Document findings: what works, what doesn't, what conditions cause the strategy to fail.

6. If the current params are already near-optimal: run spy_ort_adx_sweep.py instead
   to see if ORT approach beats the adaptive approach.

RESULT: Write /Users/quarki3/swarm_results/completed/spy_adaptive_backtest.json:
{"task": "spy_adaptive_backtest", "best_params": {...}, "best_sharpe": X.X,
 "summary": "...", "success": true/false}
""",
)

T13_MOCK_PORTFOLIO_AUDIT = _task(
    title="Full code audit of mock_portfolio.py — find edge cases and bugs",
    type_="bugfix",
    priority=13,
    prompt="""
You are auditing /Users/quarki3/mock_portfolio.py for bugs and edge cases.

TASK: Find and fix all bugs, race conditions, and edge cases in mock_portfolio.py.

LOOK FOR:
1. Position sizing bugs: does the 20%-of-equity limit actually work? Are there off-by-one errors?
2. ATR stop calculation: could ATR be 0 or NaN? What happens then?
3. Order execution: if an Alpaca API call fails, does the bot get stuck? Are positions tracked incorrectly?
4. End-of-day close: if 15:45 close fails for one symbol, does it retry? Or leave position open overnight?
5. Data staleness: if yfinance or Alpaca data fails mid-day, what happens to existing stops?
6. Multiple fills: if a buy order partially fills, is position size tracked correctly?
7. Pre-market scan: if 08:30 scan runs at 08:29 (timing drift), does it double-scan?
8. Daily state reset: are all counters/flags properly reset at midnight?
9. Logging: are all error paths logged with enough detail to debug?
10. Thread safety: is shared state protected if any async patterns are used?

For EACH bug found:
- Describe the bug
- Show the current code (file:line)
- Fix it
- Explain why the fix is correct

RESULT: Write /Users/quarki3/swarm_results/completed/mock_audit.json:
{"task": "mock_audit", "bugs_found": N, "bugs_fixed": M,
 "issues": [{"line": N, "description": "...", "fixed": true/false}],
 "summary": "...", "success": true/false}
""",
)

T14_TRADING_SWARM_AUDIT = _task(
    title="Audit trading_swarm.py — check agent prompts and scoring logic",
    type_="bugfix",
    priority=14,
    prompt="""
You are auditing /Users/quarki3/trading_swarm.py for improvements.

TASK: Review the 5-agent trading swarm (MarketScanner→CatalystHunter→RegimeAnalyst→RiskManager→TradeDecision)
and identify weaknesses in:

1. SCORING LOGIC: Are the 5 strategies (trend_momentum, gap_continuation, vwap_bounce,
   range_breakout, relative_strength) correctly scored? Do the point weights make sense?
   Could a low-quality signal game the scoring and get through?

2. AGENT PROMPTS: Read each agent's system prompt and tool definitions.
   Are there ambiguities that could cause wrong outputs? Missing error handling?

3. DATA QUALITY: Does the system handle API errors gracefully (Alpaca data down,
   options data unavailable, etc.)? What happens if ADX or ATR returns NaN?

4. UNIVERSE: Is EXPLOSIVE_UNIVERSE (SOXL, TQQQ, UPRO, NVDA, AMD, MSTR, PLTR) still
   appropriate? Any symbols that should be added or removed?

5. RISK GATES: Does the RiskManager properly reject trades in all cases where it should?
   Can you construct an edge case that passes all gates but is actually bad?

Fix any clear bugs. For structural improvements: document them as recommendations.

RESULT: Write /Users/quarki3/swarm_results/completed/swarm_audit.json:
{"task": "swarm_audit", "bugs_fixed": N, "recommendations": [...],
 "universe_changes": {...}, "summary": "...", "success": true/false}
""",
)

T15_DASHBOARD_PNLCHART = _task(
    title="Add live streaming P&L chart to dashboard Portfolio page",
    type_="feature",
    priority=15,
    prompt="""
You are improving the NiceGUI dashboard at /Users/quarki3/ng_dashboard/main.py.

TASK: Add a live-updating intraday P&L chart to the Portfolio page.

STEPS:
1. Read main.py — find page_portfolio() (~line 2097). Understand what data it shows now.

2. Find where the account equity/P&L is fetched (look in _background_loop and _cache).
   The cache likely has "account" → {"equity": ..., "unrealized_pl": ...}.

3. Add a circular buffer in the background thread that records:
   _pnl_history = deque(maxlen=390)  # one entry per minute for 6.5 hours
   Each entry: {"t": "HH:MM", "pl": float, "eq": float}
   Append to it every 60 seconds from the background loop.

4. In page_portfolio(), add a Plotly line chart that plots intraday P&L:
   - X axis: time (HH:MM ET)
   - Y axis: unrealized P&L in dollars
   - Line color: green if P&L > 0, red if < 0 (use one color, fill to zero line)
   - Auto-refresh every 60 seconds via ui.timer

5. Make it responsive: min-height 200px, full width.

6. Store _pnl_history as a module-level deque so it survives page navigations.

RESULT: Write /Users/quarki3/swarm_results/completed/pnl_chart.json:
{"task": "pnl_chart", "changed_files": ["..."], "summary": "...", "success": true/false}
""",
)

# ── Task list ordered by priority ─────────────────────────────────────────────
ALL_INITIAL_TASKS = [
    T01_WIRE_SWARM,
    T02_PNL_EXITS,
    T03_REGIME_REFRESH,
    T04_UNIVERSE_TRIM,
    T05_CORRELATION_LIMIT,
    T06_SPY_GAP_FILTER,
    T07_ATR_SWEEP,
    T08_QC_STOP_SWEEP,
    T09_IBKR_READINESS,
    T10_SOXL_OPTIMIZE,
    T11_QC_STRATEGY_EXPAND,
    T12_SPY_ADAPTIVE_BACKTEST,
    T13_MOCK_PORTFOLIO_AUDIT,
    T14_TRADING_SWARM_AUDIT,
    T15_DASHBOARD_PNLCHART,
]


# ── Follow-up task generator ──────────────────────────────────────────────────
def _generate_followups(completed_task: dict, result: dict) -> list[dict]:
    """Return new tasks to enqueue based on what just finished."""
    followups: list[dict] = []
    tid = completed_task.get("title", "")

    if "ATR trail" in tid:
        best = result.get("best_mult")
        if best and best != 2.0:
            followups.append(_task(
                title=f"Apply ATR {best}× to soxl_improved_bot.py + spy_live_trader.py",
                type_="optimize",
                priority=20,
                prompt=f"""
Apply the validated optimal ATR trail multiplier ({best}×) to:
1. /Users/quarki3/soxl_improved_bot.py  — find the trail stop calculation, update multiplier
2. /Users/quarki3/spy_live_trader.py    — same

Verify the change is consistent (same logic, just new constant).
Add comment: # optimized by swarm (atr_sweep) on {datetime.now().strftime('%Y-%m-%d')}

RESULT: Write /Users/quarki3/swarm_results/completed/atr_apply_siblings.json:
{{"task":"atr_apply_siblings","changed_files":[...],"summary":"...","success":true}}
""",
            ))

    if "universe trim" in tid.lower():
        removed = result.get("removed", [])
        if len(removed) > 3:
            followups.append(_task(
                title="Backtest trimmed universe vs full universe — confirm improvement",
                type_="research",
                priority=25,
                prompt=f"""
The universe_trim task removed {len(removed)} symbols from mock_portfolio.py.
Removed: {removed[:10]}...

TASK: Confirm the trim improved expected performance.
1. Run /Users/quarki3/adaptive_multi_symbol.py or similar with the OLD list vs NEW list
2. Compare: avg Sharpe across universe, total expected daily return, correlation
3. If the trimmed universe is NOT better: revert the change and document why

RESULT: Write /Users/quarki3/swarm_results/completed/universe_trim_verify.json:
{{"task":"universe_trim_verify","old_sharpe":X,"new_sharpe":Y,"verdict":"keep/revert","success":true}}
""",
            ))

    if "IBKR" in tid:
        followups.append(_task(
            title="Set up IB Gateway auto-start and test ES Mini paper trade",
            type_="feature",
            priority=30,
            prompt="""
Based on the IBKR readiness audit, set up what's needed for ES Mini paper trading.

1. Read /Users/quarki3/swarm_results/completed/ibkr_readiness.json for current status
2. If ibapi not installed: pip3.10 install ibapi and verify
3. Check if there's a launchd plist for IB Gateway; if not, document the manual steps
4. Write a minimal ES Mini test script: connect to TWS, request ES front-month contract,
   print bid/ask, place a 1-contract paper limit order 5 ticks below market, cancel it
   Save as /Users/quarki3/es_mini_test.py

RESULT: Write /Users/quarki3/swarm_results/completed/ibkr_setup.json:
{"task":"ibkr_setup","ibapi_installed":true/false,"test_script_created":true/false,
 "summary":"...","success":true/false}
""",
        ))

    # ── Validation rail: chain on-track follow-ups, never new param sweeps ──────
    tl = tid.lower()
    verdict = str(result.get("verdict", "")).lower()

    # Guard: generated follow-ups must NOT spawn more follow-ups (prevents self-recursion —
    # e.g. a "Shelve overfit ..." task whose own title contains "overfit").
    if tid.startswith("Shelve/down-weight") or tid.startswith("Verify proposed drawdown-control"):
        return followups

    # An out-of-sample / overfit check that came back "overfit" → shelve it conservatively
    if ("out-of-sample" in tl or "oos" in tl or "overfit" in tl or "deflated" in tl) \
            and ("overfit" in verdict or result.get("is_overfit") is True
                 or result.get("oos_failed") is True):
        followups.append(_task(
            title=f"Shelve/down-weight overfit strategy from: {tid[:50]}",
            type_="optimize",
            priority=9,
            prompt=f"""
A validation task ("{tid}") concluded a strategy is likely OVERFIT (result file:
{completed_task.get('result_file')}).

TASK (conservative, no new parameter sweeps):
1. Read that result file for which strategy/params failed out-of-sample.
2. In the relevant live config, mark that strategy as NOT live-ready: lower its weight to 0
   or add a clear `# DISABLED: failed OOS validation {datetime.now().strftime('%Y-%m-%d')}` guard
   so it cannot be deployed by accident. Do NOT delete code; just gate it.
3. Do NOT re-sweep parameters to "rescue" it — that is the overfitting trap.

RESULT: Write /Users/quarki3/swarm_results/completed/shelve_{completed_task['id']}.json:
{{"task":"shelve","strategy":"...","action":"...","summary":"...","success":true}}
""",
        ))

    # A drawdown-control proposal → verify it actually reduces MaxDD before trusting it
    if "drawdown" in tl and (result.get("proposed_config") or "propos" in verdict):
        followups.append(_task(
            title=f"Verify proposed drawdown-control config reduces MaxDD: {tid[:40]}",
            type_="research",
            priority=10,
            prompt=f"""
A drawdown task ("{tid}") proposed config changes (result file:
{completed_task.get('result_file')}) to reduce max drawdown.

TASK:
1. Read the proposed config from that result file.
2. Re-run the relevant backtest with OLD vs PROPOSED config on the SAME data window.
3. Report MaxDD and CAGR for both. Keep the change ONLY if MaxDD drops meaningfully
   AND CAGR does not collapse. Otherwise recommend revert and say why.
4. Do not apply to live configs in this task — just validate and recommend.

RESULT: Write /Users/quarki3/swarm_results/completed/dd_verify_{completed_task['id']}.json:
{{"task":"dd_verify","old_maxdd":X,"new_maxdd":Y,"old_cagr":A,"new_cagr":B,
  "verdict":"keep/revert","summary":"...","success":true}}
""",
        ))

    return followups


# ── Worker thread ─────────────────────────────────────────────────────────────
_shutdown = threading.Event()
_active_procs: dict[str, subprocess.Popen] = {}  # task_id → process
_procs_lock = threading.Lock()


def _run_task(task: dict, worker_id: int):
    tid = task["id"]
    log.info(f"[W{worker_id}] START  {tid}  \"{task['title']}\"")

    with _lock:
        state = _load()
        for t in state["running"]:
            if t["id"] == tid:
                t["started_at"] = _now()
                t["worker_id"] = worker_id
                break
        _save(state)

    task_log = RESULTS_DIR / "logs" / f"{tid}.log"
    prompt = task["prompt"].strip()

    cmd = [
        CLAUDE_BIN, "-p", prompt,
        "--dangerously-skip-permissions",
        "--model", "sonnet",
        "--output-format", "text",
    ]

    t_start = time.time()
    success = False
    try:
        with open(task_log, "w") as fout:
            proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=fout,
                stderr=subprocess.STDOUT,
                env={**os.environ, "TERM": "dumb"},
            )
        with _procs_lock:
            _active_procs[tid] = proc

        try:
            proc.wait(timeout=TASK_TIMEOUT)
        except subprocess.TimeoutExpired:
            log.warning(f"[W{worker_id}] TIMEOUT {tid} after {TASK_TIMEOUT//60} min — killing")
            proc.kill()
            proc.wait()

        duration = int(time.time() - t_start)
        rc = proc.returncode

        # Check for result file written by the agent
        result_glob = list((RESULTS_DIR / "completed").glob(f"*{tid}*")) + \
                      list((RESULTS_DIR / "completed").glob("*.json"))
        # Find any result written after task started (most recent)
        result_file = None
        for p in sorted(result_glob, key=lambda x: x.stat().st_mtime, reverse=True):
            if p.stat().st_mtime >= t_start:
                result_file = p
                break

        result_data = {}
        if result_file:
            try:
                result_data = json.loads(result_file.read_text())
            except Exception:
                pass

        success = rc == 0 and (result_file is not None or duration < TASK_TIMEOUT)
        log.info(
            f"[W{worker_id}] {'DONE' if success else 'FAIL'}  {tid}  "
            f"rc={rc}  dur={duration}s  result={result_file.name if result_file else 'none'}"
        )

        with _lock:
            state = _load()
            task_done = None
            state["running"] = [t for t in state["running"] if t["id"] != tid or (task_done := t) is None]
            if task_done is None:
                for t in state["running"]:
                    if t["id"] == tid:
                        task_done = t
                state["running"] = [t for t in state["running"] if t["id"] != tid]

            # Reconstruct task record
            final = deepcopy(task)
            final["completed_at"] = _now()
            final["duration_sec"] = duration
            final["status"] = "completed" if success else "failed"
            final["result_file"] = str(result_file) if result_file else None

            if success:
                state["completed"].insert(0, final)
                state["completed"] = state["completed"][:50]  # keep last 50
                # Generate follow-ups
                new_tasks = _generate_followups(task, result_data)
                for nt in new_tasks:
                    state["pending"].append(nt)
                    log.info(f"  -> queued follow-up: \"{nt['title']}\"")
            else:
                if task["retry_count"] < MAX_RETRIES:
                    retry = deepcopy(task)
                    retry["retry_count"] += 1
                    retry["status"] = "pending"
                    retry["started_at"] = None
                    state["pending"].insert(0, retry)
                    log.info(f"[W{worker_id}] RETRY  {tid}  (attempt {retry['retry_count']})")
                else:
                    state["failed"].insert(0, final)
                    state["failed"] = state["failed"][:20]

            _save(state)

    except Exception as exc:
        log.exception(f"[W{worker_id}] EXCEPTION in task {tid}: {exc}")
        with _lock:
            state = _load()
            state["running"] = [t for t in state["running"] if t["id"] != tid]
            final = deepcopy(task)
            final["status"] = "failed"
            final["completed_at"] = _now()
            state["failed"].insert(0, final)
            _save(state)
    finally:
        with _procs_lock:
            _active_procs.pop(tid, None)


def _worker(worker_id: int):
    log.info(f"[W{worker_id}] Worker started")
    while not _shutdown.is_set():
        if PAUSE_FILE.exists():
            log.info(f"[W{worker_id}] PAUSED (delete {PAUSE_FILE} to resume)")
            _shutdown.wait(30)
            continue

        task = None
        with _lock:
            state = _load()
            running_count = sum(1 for t in state["running"] if t.get("worker_id") is not None)
            if state["pending"] and running_count < MAX_WORKERS:
                state["pending"].sort(key=lambda t: t.get("priority", 99))
                task = state["pending"].pop(0)
                task["status"] = "running"
                state["running"].append(task)
                _save(state)

        if task:
            _run_task(task, worker_id)
        else:
            _shutdown.wait(LOOP_SLEEP)


# ── Init task queue ───────────────────────────────────────────────────────────
def _init_queue():
    state = _load()
    # Only seed if the queue is completely empty
    total = len(state["pending"]) + len(state["running"]) + len(state["completed"]) + len(state["failed"])
    if total == 0:
        log.info("Initializing task queue with 15 tasks...")
        state["pending"] = ALL_INITIAL_TASKS
        _save(state)
        log.info(f"Queue initialized: {len(state['pending'])} tasks pending")
    else:
        pending = len(state["pending"])
        running = len(state["running"])
        completed = len(state["completed"])
        failed = len(state["failed"])
        log.info(f"Resuming: {pending} pending, {running} running, {completed} completed, {failed} failed")
        # Move any "running" back to pending (crashed mid-task)
        if state["running"]:
            log.info(f"Recovering {len(state['running'])} tasks stuck in running state")
            state["pending"] = state["running"] + state["pending"]
            for t in state["pending"]:
                t["status"] = "pending"
                t["started_at"] = None
            state["running"] = []
            _save(state)


# ── Signal handling ───────────────────────────────────────────────────────────
def _shutdown_handler(signum, frame):
    log.info(f"Signal {signum} received — shutting down gracefully")
    _shutdown.set()
    with _procs_lock:
        for tid, proc in list(_active_procs.items()):
            log.info(f"Terminating task {tid}")
            try:
                proc.terminate()
            except Exception:
                pass

signal.signal(signal.SIGINT,  _shutdown_handler)
signal.signal(signal.SIGTERM, _shutdown_handler)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("=" * 60)
    log.info("  Swarm Orchestrator starting")
    log.info(f"  Workers : {MAX_WORKERS}")
    log.info(f"  Timeout : {TASK_TIMEOUT // 60} min/task")
    log.info(f"  Tasks   : {TASKS_FILE}")
    log.info(f"  Results : {RESULTS_DIR}")
    log.info(f"  Pause   : touch {PAUSE_FILE}")
    log.info("=" * 60)

    _init_queue()

    # Launch worker threads (one per max worker)
    threads = []
    for i in range(MAX_WORKERS):
        t = threading.Thread(target=_worker, args=(i + 1,), daemon=True, name=f"worker-{i+1}")
        t.start()
        threads.append(t)
        time.sleep(2)  # stagger starts

    # Keep main thread alive
    while not _shutdown.is_set():
        with _lock:
            state = _load()
        p = len(state["pending"])
        r = len(state["running"])
        c = len(state["completed"])
        f = len(state["failed"])
        log.info(f"Queue: {p} pending | {r} running | {c} done | {f} failed")
        if p == 0 and r == 0:
            log.info("All tasks complete — orchestrator idle (will pick up new tasks if added)")
        _shutdown.wait(120)  # heartbeat every 2 min

    for t in threads:
        t.join(timeout=5)
    log.info("Swarm Orchestrator stopped.")
