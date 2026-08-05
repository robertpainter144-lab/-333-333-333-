#!/usr/bin/env python3
"""
SOXL Regime Paper Bot — Alpaca Paper Account
============================================

STATUS: DISABLED
  OOS verdict = 'shelve'
  Reason: block-bootstrap p95 MaxDD = -84.6% on $100 (near-wipeout risk).
  Flag:   "p95 MaxDD=-84.6% exceeds -60% on $100 (near-wipeout risk)"
  Set LIVE_PAPER_ENABLED = True ONLY after manual re-review of OOS results
  at /Users/quarki3/swarm_results/completed/soxl_regime_oos.json.

HARD RAILS (non-negotiable, enforced in code):
  1. Max exposure = $100 notional. No pyramiding beyond the cap.
  2. Per-trade hard stop: min(8% of entry, 2 × prev-day daily ATR).
  3. Daily loss cutoff: halt entries for the day if daily_pnl ≤ -$8 (-8%).
  4. Kill-switch: halt ALL trading if allocated equity ≤ $80 (-20%), until
     manually reviewed (state persists across restarts in soxl_bot_state.json).
  5. No overnight positions: force-close by 15:55 ET (avoid 3× gap risk).
  6. Track record: every fill (intended vs actual price) + daily summary
     appended to /Users/quarki3/track_record.jsonl.
"""

import json
import sys
import time
import warnings
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

# ───────────────────────────────────────────────────────────────────────────────
# MASTER SWITCH — set True only after manual review of OOS flags
# OOS verdict: "shelve"
# Reason: p95 block-bootstrap MaxDD = -84.6% on $100 (near-wipeout risk)
# Review: /Users/quarki3/swarm_results/completed/soxl_regime_oos.json
# ───────────────────────────────────────────────────────────────────────────────
LIVE_PAPER_ENABLED = False
_OOS_VERDICT       = "shelve"
_OOS_FLAGS         = ["p95 MaxDD=-84.6% exceeds -60% on $100 (near-wipeout risk)"]

# ── Alpaca Paper credentials ──────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from alpaca_keys import PAPER_KEY, PAPER_SECRET  # type: ignore

PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE  = "https://data.alpaca.markets/v2"
SYMBOL     = "SOXL"

# ── Hard Rails ────────────────────────────────────────────────────────────────
MAX_EXPOSURE      = 100.0    # max $100 notional; no pyramiding beyond this
DAILY_LOSS_LIMIT  = -8.0     # halt today at -$8 (-8% of $100)
KILL_EQUITY_MIN   = 80.0     # halt until manual review if allocated equity ≤ $80
EOD_CLOSE_MINS    = 15*60+55 # force-close by 15:55 ET (no overnight)

# ── Strategy parameters (mirrors soxl_regime_strategy.py) ────────────────────
TRAIL_MULT       = 1.25
TAKE1_MULT       = 2.0
TAKE1_FRAC       = 0.50
HARD_STOP_PCT    = 0.08   # 8% of entry
HARD_STOP_MULT   = 2.0    # 2 × prev-day daily ATR
RISK_PER_TRADE   = 0.02   # 2% of allocated equity
ENTRY_EMA        = 25
ENTRY_START_MINS = 9*60+35
ENTRY_END_MINS   = 11*60
GATE_EMA_FAST    = 20
GATE_EMA_SLOW    = 50
GATE_ADX_WIN     = 14
GATE_ADX_THR     = 20.0
GATE_SPY_SMA     = 200
GATE_VIX_MAX     = 25.0

# ── Paths ─────────────────────────────────────────────────────────────────────
TRACK_RECORD = Path("/Users/quarki3/track_record.jsonl")
BOT_LOG      = Path("/Users/quarki3/soxl_paper_bot.log")
STATE_FILE   = Path("/Users/quarki3/soxl_bot_state.json")
ET           = ZoneInfo("America/New_York")


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str):
    ts   = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {msg}"
    print(line, flush=True)
    with open(BOT_LOG, "a") as f:
        f.write(line + "\n")


def append_record(record: dict):
    with open(TRACK_RECORD, "a") as f:
        f.write(json.dumps(record) + "\n")


# ── Alpaca Paper Broker ───────────────────────────────────────────────────────

class PaperAlpaca:

    def __init__(self):
        self._hdrs = {
            "APCA-API-KEY-ID":     PAPER_KEY,
            "APCA-API-SECRET-KEY": PAPER_SECRET,
            "Content-Type":        "application/json",
        }
        self._data_hdrs = {
            "APCA-API-KEY-ID":     PAPER_KEY,
            "APCA-API-SECRET-KEY": PAPER_SECRET,
        }

    def account(self) -> dict:
        try:
            r = requests.get(f"{PAPER_BASE}/v2/account",
                             headers=self._hdrs, timeout=8)
            return r.json() if r.ok else {}
        except Exception as e:
            log(f"account() error: {e}")
            return {}

    def positions(self) -> dict:
        try:
            r = requests.get(f"{PAPER_BASE}/v2/positions",
                             headers=self._hdrs, timeout=8)
            data = r.json() if r.ok else []
            return {p["symbol"]: p for p in (data or [])} if isinstance(data, list) else {}
        except Exception as e:
            log(f"positions() error: {e}")
            return {}

    def place_order(self, symbol: str, qty: float, side: str) -> dict | None:
        try:
            body = {
                "symbol":        symbol,
                "qty":           f"{qty:.4f}",
                "side":          side,
                "type":          "market",
                "time_in_force": "day",
            }
            r = requests.post(f"{PAPER_BASE}/v2/orders",
                              headers=self._hdrs, json=body, timeout=10)
            if r.status_code in (200, 201):
                d = r.json()
                log(f"ORDER {side.upper()} {qty:.4f} {symbol}  id={d.get('id','')[:8]}")
                return d
            log(f"order rejected {symbol} {side}: {r.status_code} {r.text[:120]}")
            return None
        except Exception as e:
            log(f"place_order error: {e}")
            return None

    def get_filled_price(self, order_id: str) -> float | None:
        if not order_id:
            return None
        try:
            r = requests.get(f"{PAPER_BASE}/v2/orders/{order_id}",
                             headers=self._hdrs, timeout=8)
            if r.ok:
                fp = r.json().get("filled_avg_price")
                return float(fp) if fp else None
        except Exception:
            pass
        return None

    def get_bars_5min(self, symbol: str, limit: int = 80) -> pd.DataFrame:
        try:
            r = requests.get(
                f"{DATA_BASE}/stocks/{symbol}/bars",
                headers=self._data_hdrs,
                params={
                    "timeframe": "5Min",
                    "limit":     limit,
                    "adjustment": "raw",
                    "feed":      "iex",
                },
                timeout=10,
            )
            if not r.ok:
                log(f"get_bars_5min {r.status_code}: {r.text[:80]}")
                return pd.DataFrame()
            bars = r.json().get("bars", [])
            if not bars:
                return pd.DataFrame()
            df = pd.DataFrame(bars)
            df["t"] = pd.to_datetime(df["t"])
            df = df.set_index("t").rename(
                columns={"o": "open", "h": "high", "l": "low",
                         "c": "close", "v": "volume"}
            )
            df.index = df.index.tz_convert("America/New_York")
            return df[["open", "high", "low", "close", "volume"]]
        except Exception as e:
            log(f"get_bars_5min error: {e}")
            return pd.DataFrame()


# ── Daily regime gate ─────────────────────────────────────────────────────────

def _daily_adx(df: pd.DataFrame, period: int) -> pd.Series:
    hi, lo, cl = df["High"], df["Low"], df["Close"]
    tr   = pd.concat([(hi - lo), (hi - cl.shift(1)).abs(),
                      (lo - cl.shift(1)).abs()], axis=1).max(axis=1)
    up   = hi - hi.shift(1)
    dn   = lo.shift(1) - lo
    pdm  = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    ndm  = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    atr_e = tr.ewm(span=period, adjust=False).mean()
    pdi  = 100 * pdm.ewm(span=period, adjust=False).mean() / atr_e
    ndi  = 100 * ndm.ewm(span=period, adjust=False).mean() / atr_e
    dx   = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(span=period, adjust=False).mean()


def check_regime_gate() -> tuple[bool, dict]:
    """
    Computes the 5-condition regime gate using previous day's closed bars.
    Mirrors soxl_regime_strategy.py logic exactly (no look-ahead).
    Returns (gate_open, signals_dict).
    """
    try:
        end_dt   = datetime.now(ET).date()
        start_dt = (datetime.now(ET) - timedelta(days=400)).date()

        def flat(df: pd.DataFrame) -> pd.DataFrame:
            if isinstance(df.columns, pd.MultiIndex):
                df = df.droplevel(1, axis=1)
            df.index = pd.to_datetime(df.index).normalize()
            return df

        smh  = flat(yf.download("SMH",  start=str(start_dt), end=str(end_dt),
                                  progress=False, auto_adjust=True))
        spy  = flat(yf.download("SPY",  start=str(start_dt), end=str(end_dt),
                                  progress=False, auto_adjust=True))
        soxl = flat(yf.download("SOXL", start=str(start_dt), end=str(end_dt),
                                  progress=False, auto_adjust=True))
        try:
            vix     = flat(yf.download("^VIX", start=str(start_dt), end=str(end_dt),
                                        progress=False, auto_adjust=True))
            has_vix = len(vix) > 10
        except Exception:
            vix     = pd.DataFrame()
            has_vix = False

        if len(smh) < 55 or len(spy) < 205 or len(soxl) < 55:
            log("Regime gate: insufficient history — gate closed by default")
            return False, {"error": "insufficient_history"}

        # SMH signals
        smh_c    = smh["Close"]
        ema_fast = smh_c.ewm(span=GATE_EMA_FAST, adjust=False).mean()
        ema_slow = smh_c.ewm(span=GATE_EMA_SLOW, adjust=False).mean()
        adx      = _daily_adx(smh, GATE_ADX_WIN)

        # SPY
        spy_c    = spy["Close"]
        spy_s200 = spy_c.rolling(GATE_SPY_SMA, min_periods=GATE_SPY_SMA).mean()

        # SOXL daily
        soxl_c   = soxl["Close"]
        soxl_hi  = soxl["High"]
        soxl_lo  = soxl["Low"]
        soxl_ema20 = soxl_c.ewm(span=20, adjust=False).mean()
        soxl_ema50 = soxl_c.ewm(span=50, adjust=False).mean()
        soxl_tr  = pd.concat(
            [(soxl_hi - soxl_lo),
             (soxl_hi - soxl_c.shift(1)).abs(),
             (soxl_lo - soxl_c.shift(1)).abs()], axis=1
        ).max(axis=1)
        soxl_atr = soxl_tr.ewm(span=14, adjust=False).mean()

        # Use iloc[-2] = last fully-closed day (iloc[-1] may be today's partial bar)
        def prev(s: pd.Series) -> float:
            return float(s.iloc[-2]) if len(s) >= 2 else float(s.iloc[-1])

        c_smh_trend = (prev(smh_c) > prev(ema_slow)) and (prev(ema_fast) > prev(ema_slow))
        c_adx       = prev(adx) > GATE_ADX_THR
        c_spy       = prev(spy_c) > prev(spy_s200)
        c_soxl_tr   = ((prev(soxl_ema20) > prev(soxl_ema50))
                       and (prev(soxl_c) > prev(soxl_ema50)))
        c_vix       = True
        vix_val     = None
        if has_vix:
            vix_val = prev(vix["Close"])
            c_vix   = vix_val < GATE_VIX_MAX

        gate = c_smh_trend and c_adx and c_spy and c_vix and c_soxl_tr

        signals: dict = {
            "cond_smh_trend":  c_smh_trend,
            "cond_adx":        c_adx,
            "adx_val":         round(prev(adx), 2),
            "cond_spy":        c_spy,
            "cond_vix":        c_vix,
            "vix_val":         round(vix_val, 2) if vix_val is not None else None,
            "cond_soxl_trend": c_soxl_tr,
            "gate_open":       gate,
            "prev_soxl_close": round(prev(soxl_c), 4),
            "soxl_daily_atr":  round(prev(soxl_atr), 4),
            "soxl_ema20":      round(prev(soxl_ema20), 4),
            "soxl_ema50":      round(prev(soxl_ema50), 4),
        }
        log(f"Regime gate={'OPEN' if gate else 'CLOSED'}  "
            f"smh_trend={c_smh_trend} adx={c_adx}({signals['adx_val']:.1f}) "
            f"spy={c_spy} vix={c_vix}({vix_val or 'N/A'}) soxl_trend={c_soxl_tr}")
        return gate, signals

    except Exception as e:
        log(f"check_regime_gate error: {e}")
        return False, {"error": str(e)}


# ── Persistent bot state ──────────────────────────────────────────────────────

class BotState:
    """
    Persists total_pnl and kill_triggered to STATE_FILE so the kill-switch
    survives bot restarts.
    """

    def __init__(self):
        self._load_persistent()
        self.today          = date.today()
        self.gate_open      = False
        self.gate_signals: dict = {}
        self.entered_today  = False
        self.daily_pnl      = 0.0
        self.halted_today   = False
        self.pos: dict | None = None

    def _load_persistent(self):
        if STATE_FILE.exists():
            try:
                d = json.loads(STATE_FILE.read_text())
                self.total_pnl      = float(d.get("total_pnl", 0.0))
                self.kill_triggered = bool(d.get("kill_triggered", False))
                log(f"State loaded  total_pnl=${self.total_pnl:.2f}  "
                    f"kill_triggered={self.kill_triggered}")
                return
            except Exception:
                pass
        self.total_pnl      = 0.0
        self.kill_triggered = False

    def _save_persistent(self):
        STATE_FILE.write_text(json.dumps({
            "total_pnl":      round(self.total_pnl, 4),
            "kill_triggered": self.kill_triggered,
            "updated":        datetime.now(ET).isoformat(),
        }, indent=2))

    def allocated_equity(self) -> float:
        return MAX_EXPOSURE + self.total_pnl

    def reset_day(self):
        today = date.today()
        if today != self.today:
            self.today         = today
            self.entered_today = False
            self.daily_pnl     = 0.0
            self.halted_today  = False

    def check_daily_loss(self) -> bool:
        if self.daily_pnl <= DAILY_LOSS_LIMIT:
            if not self.halted_today:
                log(f"DAILY LOSS CUTOFF  daily_pnl=${self.daily_pnl:.2f} "
                    f"≤ limit=${DAILY_LOSS_LIMIT:.2f}  halting entries for today")
                self.halted_today = True
            return True
        return False

    def check_kill_switch(self) -> bool:
        eq = self.allocated_equity()
        if eq <= KILL_EQUITY_MIN:
            if not self.kill_triggered:
                log(f"KILL SWITCH TRIGGERED  allocated_equity=${eq:.2f} "
                    f"≤ ${KILL_EQUITY_MIN:.2f}  halting ALL trading until manual review")
                log(f"To re-enable: review track_record.jsonl, then set "
                    f"kill_triggered=false in {STATE_FILE} and restart.")
                self.kill_triggered = True
                self._save_persistent()
            return True
        return False

    def add_pnl(self, pnl: float):
        self.daily_pnl += pnl
        self.total_pnl += pnl
        self._save_persistent()

    def record_fill(self, fill_type: str, side: str, qty: float,
                    intended_price: float, actual_price: float,
                    pnl: float | None = None, metadata: dict | None = None):
        record: dict = {
            "type":             fill_type,
            "ts":               datetime.now(ET).isoformat(),
            "date":             str(date.today()),
            "symbol":           SYMBOL,
            "side":             side,
            "qty":              round(qty, 4),
            "intended_price":   round(intended_price, 4),
            "actual_price":     round(actual_price, 4),
            "slippage":         round(actual_price - intended_price, 4),
            "pnl":              round(pnl, 4) if pnl is not None else None,
            "daily_pnl":        round(self.daily_pnl, 4),
            "total_pnl":        round(self.total_pnl, 4),
            "allocated_equity": round(self.allocated_equity(), 4),
        }
        if metadata:
            record.update(metadata)
        append_record(record)

    def record_daily_summary(self):
        append_record({
            "type":             "daily_summary",
            "date":             str(self.today),
            "ts":               datetime.now(ET).isoformat(),
            "daily_pnl":        round(self.daily_pnl, 4),
            "total_pnl":        round(self.total_pnl, 4),
            "allocated_equity": round(self.allocated_equity(), 4),
            "gate_open":        self.gate_open,
            "entered":          self.entered_today,
            "halted_daily":     self.halted_today,
            "kill_triggered":   self.kill_triggered,
        })


# ── Entry signal ──────────────────────────────────────────────────────────────

def compute_entry_signal(bars: pd.DataFrame, prev_day_close: float) -> tuple[bool, float]:
    """
    close > EMA25(5-min)  AND  close > prev 5-min close  AND  close > prev_day_close
    Returns (signal, current_close).
    """
    if len(bars) < 30:
        return False, 0.0
    df = bars.copy()
    df["ema"] = df["close"].ewm(span=ENTRY_EMA, adjust=False).mean()
    last  = df.iloc[-1]
    prev5 = df.iloc[-2]
    close = float(last["close"])
    ema   = float(last["ema"])
    p5    = float(prev5["close"])
    sig   = (close > ema) and (close > p5) and (close > prev_day_close)
    return sig, close


# ── Trading actions ───────────────────────────────────────────────────────────

def _try_entry(broker: PaperAlpaca, state: BotState):
    bars = broker.get_bars_5min(SYMBOL, limit=80)
    if bars.empty:
        return

    sig            = state.gate_signals
    prev_day_close = sig.get("prev_soxl_close", 0.0)
    daily_atr      = sig.get("soxl_daily_atr", 0.0)

    if daily_atr <= 0:
        log("Entry skip: daily_atr=0 (no valid ATR in gate signals)")
        return

    signal, close = compute_entry_signal(bars, prev_day_close)
    if not signal or close <= 0:
        return

    alloc_eq    = state.allocated_equity()
    risk_usd    = alloc_eq * RISK_PER_TRADE
    trail_dist  = TRAIL_MULT * daily_atr
    hard_dist   = min(close * HARD_STOP_PCT, HARD_STOP_MULT * daily_atr)
    stop_dist   = min(trail_dist, hard_dist)
    shares_risk = risk_usd / max(stop_dist, 0.01)
    shares_cap  = MAX_EXPOSURE / close
    qty         = min(shares_risk, shares_cap)

    if qty * close < 0.50:
        log(f"Entry skip: notional ${qty*close:.2f} < $0.50")
        return

    # Enforce hard notional cap — no pyramiding
    existing = broker.positions().get(SYMBOL)
    if existing:
        held_val = abs(float(existing.get("market_value", 0)))
        if held_val >= MAX_EXPOSURE * 0.95:
            log(f"Entry skip: already holding ${held_val:.2f} ≥ cap ${MAX_EXPOSURE:.2f}")
            return

    intended_price = close
    order = broker.place_order(SYMBOL, qty, "buy")
    if not order:
        return

    time.sleep(2)
    actual_price = broker.get_filled_price(order.get("id", "")) or intended_price

    trail_stop = actual_price - trail_dist
    hard_stop  = actual_price - min(actual_price * HARD_STOP_PCT, HARD_STOP_MULT * daily_atr)

    state.pos = {
        "entry_price":    actual_price,
        "qty":            qty,
        "trail_stop":     trail_stop,
        "hard_stop":      hard_stop,
        "daily_atr":      daily_atr,
        "partial_taken":  False,
        "entry_time":     datetime.now(ET).isoformat(),
        "order_id":       order.get("id", ""),
    }
    state.entered_today = True

    state.record_fill(
        "entry", "buy", qty, intended_price, actual_price,
        metadata={
            "trail_stop":    round(trail_stop, 4),
            "hard_stop":     round(hard_stop, 4),
            "daily_atr":     round(daily_atr, 4),
            "gate_signals":  sig,
        },
    )
    log(f"ENTRY  qty={qty:.4f}  intended=${intended_price:.2f}  "
        f"actual=${actual_price:.2f}  "
        f"trail_stop=${trail_stop:.2f}  hard_stop=${hard_stop:.2f}")


def _manage_position(broker: PaperAlpaca, state: BotState):
    pos = state.pos
    if pos is None:
        return

    bars = broker.get_bars_5min(SYMBOL, limit=10)
    if bars.empty:
        return

    close     = float(bars.iloc[-1]["close"])
    daily_atr = state.gate_signals.get("soxl_daily_atr") or pos["daily_atr"]
    if daily_atr <= 0:
        daily_atr = pos["daily_atr"]

    # Ratchet trailing stop upward
    new_trail = close - TRAIL_MULT * daily_atr
    if new_trail > pos["trail_stop"]:
        pos["trail_stop"] = new_trail

    # Tiered partial: sell 50% at +2 × daily ATR
    if (not pos["partial_taken"]
            and close >= pos["entry_price"] + TAKE1_MULT * pos["daily_atr"]
            and pos["qty"] > 0.001):
        sell_qty = pos["qty"] * TAKE1_FRAC
        order    = broker.place_order(SYMBOL, sell_qty, "sell")
        if order:
            time.sleep(2)
            act_price = broker.get_filled_price(order.get("id", "")) or close
            pnl       = (act_price - pos["entry_price"]) * sell_qty
            state.add_pnl(pnl)
            pos["qty"]          -= sell_qty
            pos["partial_taken"] = True
            state.record_fill(
                "partial_exit", "sell", sell_qty, close, act_price, pnl=pnl,
                metadata={"reason": "TAKE1", "remaining_qty": round(pos["qty"], 4)},
            )
            log(f"PARTIAL TAKE1  qty={sell_qty:.4f}  intended=${close:.2f}  "
                f"actual=${act_price:.2f}  pnl=${pnl:.2f}")
        return  # re-evaluate trail/hard on next tick

    # Exit triggers
    if close <= pos["hard_stop"]:
        _close_position(broker, state, "HARD_STOP", close)
    elif close <= pos["trail_stop"]:
        _close_position(broker, state, "TRAIL_STOP", close)


def _eod_close(broker: PaperAlpaca, state: BotState):
    log("EOD force-close — no overnight positions")
    bars  = broker.get_bars_5min(SYMBOL, limit=5)
    close = float(bars.iloc[-1]["close"]) if not bars.empty else 0.0
    _close_position(broker, state, "EOD", close)


def _close_position(broker: PaperAlpaca, state: BotState, reason: str, market_price: float):
    pos = state.pos
    if pos is None:
        return

    qty   = pos["qty"]
    order = broker.place_order(SYMBOL, qty, "sell")

    actual_price = market_price
    if order:
        time.sleep(2)
        fp = broker.get_filled_price(order.get("id", ""))
        if fp:
            actual_price = fp

    pnl = (actual_price - pos["entry_price"]) * qty
    state.add_pnl(pnl)

    state.record_fill(
        "exit", "sell", qty,
        intended_price=market_price,
        actual_price=actual_price,
        pnl=pnl,
        metadata={
            "reason":      reason,
            "entry_price": round(pos["entry_price"], 4),
            "trail_stop":  round(pos["trail_stop"], 4),
            "hard_stop":   round(pos["hard_stop"], 4),
        },
    )
    log(f"EXIT {reason}  qty={qty:.4f}  intended=${market_price:.2f}  "
        f"actual=${actual_price:.2f}  pnl=${pnl:.2f}  "
        f"daily_pnl=${state.daily_pnl:.2f}  total_pnl=${state.total_pnl:.2f}")
    state.pos = None

    # Re-check rails after close
    state.check_daily_loss()
    state.check_kill_switch()


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_bot():
    log("=" * 70)
    log(f"SOXL Paper Bot  LIVE_PAPER_ENABLED={LIVE_PAPER_ENABLED}")

    if not LIVE_PAPER_ENABLED:
        log(f"DISABLED — OOS verdict='{_OOS_VERDICT}'")
        for flag in _OOS_FLAGS:
            log(f"  FLAG: {flag}")
        log(f"Review: /Users/quarki3/swarm_results/completed/soxl_regime_oos.json")
        log("Set LIVE_PAPER_ENABLED=True after manual review to activate paper trading.")
        log("=" * 70)
        return

    broker = PaperAlpaca()

    # Connectivity check
    acct = broker.account()
    if not acct:
        log("Cannot reach Alpaca paper account — check PAPER_KEY / PAPER_SECRET")
        return
    log(f"Connected  equity=${float(acct.get('equity', 0)):,.2f}  "
        f"cash=${float(acct.get('cash', 0)):,.2f}")

    # Warn if existing SOXL position found at startup
    existing = broker.positions().get(SYMBOL)
    if existing:
        log(f"WARNING: existing {SYMBOL} position found at startup "
            f"(qty={existing.get('qty')} mkt_val=${existing.get('market_value')}). "
            "State not restored — monitor manually.")

    state            = BotState()
    last_gate_date: date | None  = None
    last_eod_logged: date | None = None

    log("Bot running. Hard rails active: "
        f"max_exposure=${MAX_EXPOSURE}  "
        f"daily_loss_limit=${DAILY_LOSS_LIMIT}  "
        f"kill_equity=${KILL_EQUITY_MIN}  "
        f"eod_close={EOD_CLOSE_MINS//60}:{EOD_CLOSE_MINS%60:02d} ET")

    while True:
        now   = datetime.now(ET)
        mins  = now.hour * 60 + now.minute
        today = now.date()

        state.reset_day()

        # Compute regime gate once per day at/after 9:30
        if last_gate_date != today and mins >= 9*60+30:
            state.gate_open, state.gate_signals = check_regime_gate()
            last_gate_date = today

        kill   = state.check_kill_switch()
        halted = state.halted_today or state.check_daily_loss()

        # Always manage and close open positions even when halted/killed
        if state.pos is not None:
            _manage_position(broker, state)

        if mins >= EOD_CLOSE_MINS and state.pos is not None:
            _eod_close(broker, state)

        # Entry: only when gate open, not halted, not killed, in window, flat
        if (not kill
                and not halted
                and state.gate_open
                and not state.entered_today
                and state.pos is None
                and ENTRY_START_MINS <= mins < ENTRY_END_MINS):
            _try_entry(broker, state)

        # EOD daily summary (once, after 16:10)
        if mins >= 16*60+10 and last_eod_logged != today:
            state.record_daily_summary()
            last_eod_logged = today
            log(f"Daily summary  daily_pnl=${state.daily_pnl:.2f}  "
                f"total_pnl=${state.total_pnl:.2f}  "
                f"allocated_equity=${state.allocated_equity():.2f}")

        time.sleep(30)


if __name__ == "__main__":
    run_bot()
