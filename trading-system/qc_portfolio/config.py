"""Central configuration for QC Portfolio Manager."""

# ── Alpaca — fresh broker sandbox account (50k, clean start) ──────────────
import sys as _sys, os as _os, base64 as _b64
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
from alpaca_keys import BROKER_KEY, BROKER_SECRET

ACTIVE_ACCOUNT_ID = "b6ee7e43-9361-4618-845d-3b97bccc9d4c"
ALPACA_BASE = (
    f"https://broker-api.sandbox.alpaca.markets"
    f"/v1/trading/accounts/{ACTIVE_ACCOUNT_ID}"
)
ALPACA_HDR = {
    "Authorization": "Basic " + _b64.b64encode(
        f"{BROKER_KEY}:{BROKER_SECRET}".encode()).decode(),
    "Content-Type": "application/json",
}

# ── QuantConnect API ──────────────────────────────────────────────────────
QC_LIST_URL   = "https://www.quantconnect.com/api/v2/strategies/list"
QC_DETAIL_URL = "https://www.quantconnect.com/api/v2/strategies/read"
QC_TOP_N      = 3  # tested by swarm on 2026-06-14

# ── Known strategy universes (auto-detected for unknowns at runtime) ──────
STRATEGY_CONFIGS = {
    245: {
        "name": "Tech Momentum Winner Rotation",
        "universe": ["AMD", "TSLA", "AMZN", "AAPL", "SPXL"],
        "alloc": "single_winner",
    },
    46: {
        "name": "TheOmniscientParadox",
        "universe": ["TQQQ", "UPRO", "SOXL", "TECL", "LABU", "FAS", "FNGU", "TNA"],
        "alloc": "single_winner",
    },
    315: {
        "name": "Mandelbrotian Vol Clustering",
        # Options strategy simplified to underlying equities
        "universe": ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "AMD", "TSLA"],
        "alloc": "top3_equal",
    },
    123: {
        "name": "High-Conviction Mega-Cap vs QQQ",
        "universe": ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA", "AMD", "QQQ"],
        "alloc": "single_winner",
    },
    60: {
        "name": "Leveraged ETF Momentum Allocator",
        "universe": ["TQQQ", "UPRO", "SOXL", "TECL", "SPXL", "FNGU", "TNA", "UDOW"],
        "alloc": "single_winner",
    },
    345: {
        "name": "Large-Cap US Momentum Rotation",
        "universe": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "JPM"],
        "alloc": "top5_equal",
    },
    446: {
        "name": "Long-Only Liquid Equities Tail-Risk",
        "universe": ["SPY", "QQQ", "IWM", "EFA", "GLD", "TLT", "AAPL", "MSFT", "NVDA", "AMZN"],
        "alloc": "top3_equal",
    },
    382: {
        "name": "AI Data Center Momentum Rotation",
        "universe": ["NVDA", "AMD", "AVGO", "SMCI", "DELL", "HPE", "ANET", "MSFT", "ORCL", "CRM"],
        "alloc": "single_winner",
    },
    252: {
        "name": "Risk Off Rocket",
        "universe": ["TLT", "GLD", "SHV", "VIXY", "UUP", "IAU", "AGG", "BND", "TIPS", "IGSB"],
        "alloc": "top3_equal",
    },
    364: {
        "name": "Top-12 US LongShort Momentum",
        "universe": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD",
                     "AVGO", "JPM", "LLY", "V"],
        "alloc": "top5_equal",
    },
}

# Fallback universes for unknown strategy types
LEVERAGED_ETF_UNIVERSE = ["TQQQ", "UPRO", "SOXL", "TECL", "LABU", "FAS", "FNGU", "TNA"]
LARGE_CAP_UNIVERSE     = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "JPM"]

# ── Portfolio mechanics ───────────────────────────────────────────────────
REBALANCE_HOUR          = 9    # ET
REBALANCE_MINUTE        = 31
REBALANCE_INTERVAL_DAYS = 1    # daily rebalance (strategy_compare: best Sharpe)

# Stop-loss thresholds
TRAIL_STOP_PCT     = 0.03   # legacy fallback / floor; only used when ADAPTIVE_STOPS=False
STRATEGY_DD_LIMIT  = 0.10   # 10% strategy drawdown → halve allocation

# Adaptive stops — per-symbol trail % from ATR × VIX × SPY trend × vol burst
ADAPTIVE_STOPS         = True
ADAPTIVE_LADDER_SCALE  = True   # scale DD ladder thresholds by realized-vol regime
HARD_STOP_ATR_MULT     = 2.0    # hard stop = entry − min(8%, mult × ATR%)
HARD_STOP_FLOOR_PCT    = 0.08
TIME_STOP_DAYS         = 7      # close any position idle > N sessions with <1% progress
TIME_STOP_PROGRESS_PCT = 0.01
PROFIT_STEP_ATR_MULT   = 2.0    # at +mult × ATR%, sell half and move trail to BE

# ── Leveraged-ETF–specific rules (volatility-decay defense) ──────────────────
# Daily-reset products compound vol drag, so we hold shorter, trail tighter,
# refuse entry in vol bursts, and cap aggregate exposure.
LEVERAGED_TICKERS = {
    "TQQQ", "UPRO", "SOXL", "TECL", "LABU", "FAS", "SPXL", "FNGU", "TNA",
    "SQQQ", "SPXS", "SOXS", "TECS", "LABD", "FAZ", "TZA", "FNGD",  # inverse
    "UDOW", "SDOW", "URTY", "SRTY", "DRN", "DRV", "NUGT", "DUST", "JNUG", "JDST",
}
LEV_TIME_STOP_DAYS      = 2     # 2 sessions max for leveraged names (vs 7 for normal)
LEV_TRAIL_MULT          = 0.85  # tighten trail on leveraged (decay penalizes long holds)
LEV_NO_ENTRY_VIX        = 22.0  # block new leveraged entries when VIX >= this
LEV_NO_ENTRY_BURST      = 1.5   # block when 5d realized vol / 60d >= this ratio
LEV_MAX_PORTFOLIO_PCT   = 0.30  # leveraged-ETF sum capped at 30% of portfolio

# Drawdown ladder — tiered liquidation (backtest-optimal, beats single trigger)
# Each tier fires once per drawdown cycle; resets when portfolio makes a new peak
# OR when DD_LADDER_COOLDOWN_DAYS elapse since the tier fired (whichever first).
# The cooldown exists because peak_eq is tracked from sum(position market_value);
# once a tier sells everything to cash, that sum can't grow, so a peak-only reset
# would lock the book in cash forever. Cooldown is the escape hatch.
DD_LADDER = [
    (0.10, 0.75),  # -10% → sell down to 75% invested
    (0.15, 0.50),  # -15% → sell down to 50% invested
    (0.20, 0.25),  # -20% → sell down to 25% invested
    (0.25, 0.00),  # -25% → full cash
]
DD_LADDER_COOLDOWN_DAYS = 21    # after N calendar days at a tier, allow re-entry

# Minimum rebalance threshold (skip if drift < this)
MIN_REBALANCE_DOLLARS = 50.0
MIN_REBALANCE_DRIFT   = 0.01   # 1% of target value

# Position sizing cap — no single symbol can exceed this fraction of portfolio
MAX_SINGLE_POSITION = 1.0    # uncapped (strategy_compare: cap hurts single-winner strategies)

# Cross-strategy per-name concentration cap — applied AFTER sleeve aggregation,
# so three single_winner strategies all picking the same name (e.g. AMD) can't
# stack past this fraction of total equity. Excess is redistributed pro-rata
# to other names. Added 2026-07-20 after a 75%-in-AMD concentration incident.
MAX_NAME_CONCENTRATION = 0.35

# ── Equity sanity gate ────────────────────────────────────────────────────
# Broker-reported equity is cross-checked before it's allowed to feed the DD
# ladder, peak tracker, or governor. Added 2026-07-20 after a phantom broker
# equity print ($54,075 -> $93,023 in one tick, no corresponding buys) poisoned
# peak_equity and fired the whole drawdown ladder on a drawdown that never
# happened. See guard_events.jsonl for gate activations.
EQUITY_GATE_DEVIATION_PCT = 0.07   # max allowed deviation before rejection
EQUITY_GATE_HISTORY_LEN   = 5      # size of accepted-reading window for median check

# Intraday stop-check interval during market hours
INTRADAY_CHECK_MINS        = 30   # normal session cadence
INTRADAY_OPEN_CHECK_MINS   = 5    # first 60 min of session: check every 5 min

# Profit ratchet — locks in gains once session P&L exceeds trigger
# Once activated: if P&L gives back more than FLOOR_PCT of the session peak, sell SELL_FRAC
PROFIT_RATCHET_TRIGGER     = 50.0  # activate once session P&L > $50
PROFIT_RATCHET_FLOOR_PCT   = 0.40  # don't give back more than 40% of peak gain
PROFIT_RATCHET_SELL_FRAC   = 0.50  # sell 50% of each position when floor breached

# ── Broker selector ──────────────────────────────────────────────────────
# "alpaca"  → paper/sandbox via Alpaca broker API (current)
# "ibkr"    → live account via IB Gateway / TWS (ib_insync)
# Set env var QC_BROKER=ibkr when launching ibkr_main.py; never change this
# line — the two systems run as separate OS processes simultaneously.
import os as _os
ACTIVE_BROKER = _os.environ.get("QC_BROKER", "alpaca")

# ── IBKR connection ───────────────────────────────────────────────────────
# IB Gateway live: 4001  |  IB Gateway paper: 4002
# TWS live: 7496         |  TWS paper: 7497
IBKR_HOST       = "127.0.0.1"
IBKR_PORT       = 4001          # IB Gateway live
IBKR_CLIENT_ID  = 10
IBKR_ACCOUNT    = "U24971994"

# ── Web dashboard (IBKR) ─────────────────────────────────────────────────
WEB_HOST     = "0.0.0.0"   # bind all interfaces for remote access
WEB_PORT     = 5050
WEB_PASSWORD = "qcibkr"    # change before exposing to internet

# ── Files ────────────────────────────────────────────────────────────────
import os
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
# Alpaca keeps its original filenames; IBKR gets its own separate files
if ACTIVE_BROKER == "ibkr":
    TRANSACTIONS = os.path.join(BASE_DIR, "transactions_ibkr.csv")
    STATE_FILE   = os.path.join(BASE_DIR, "portfolio_state_ibkr.json")
else:
    TRANSACTIONS = os.path.join(BASE_DIR, "transactions.csv")
    STATE_FILE   = os.path.join(BASE_DIR, "portfolio_state.json")

# ── GUI ──────────────────────────────────────────────────────────────────
REFRESH_INTERVAL_MS = 60_000   # price/account refresh every 60s
QC_REFRESH_HOURS    = 6        # re-check QC leaderboard every 6 hours

STRATEGY_COLORS = ["#00d4ff", "#00ff88", "#ff9f43", "#a29bfe", "#fd79a8"]
BG_DARK  = "#0f0f1a"
BG_CARD  = "#1a1a2e"
FG_TEXT  = "#e0e0e0"
FG_DIM   = "#888888"
GREEN    = "#00ff88"
RED      = "#ff4d4d"
