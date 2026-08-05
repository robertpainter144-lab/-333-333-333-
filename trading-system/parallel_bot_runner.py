#!/usr/bin/env python3
"""
parallel_bot_runner.py — 3 uncorrelated paper-trading bots, one Alpaca paper account

  soxl_regime  $30k  SOXL-only, 5-cond daily regime gate, 9:35–11:00 entry, 3-day max hold
  momentum     $40k  top-2 individual stocks (ADX/vol/mom scan), 9:35 entry, 15:45 EOD close
  levered_etf  $30k  best leveraged ETF (TQQQ/UPRO/LABU/FNGU/TECL, not SOXL), 9:35, 15:45 EOD

Idempotent: fcntl exclusive lock /tmp/parallel_bots.lock; extra launchd KeepAlive instances
  sleep forever rather than exit (prevents restart storm).
Restartable: reconciles existing paper positions on startup before entering new trades.
Daily P&L per bot → /Users/quarki3/track_record.jsonl via track_record_logger.py interface.
Status written to /Users/quarki3/swarm_results/completed/parallel_paper_bots.json.
"""

import fcntl
import json
import os
import sys
import time
import threading
import warnings
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

# ═══════════════════════════════════════════════════════════════════════════════
#  Paths & credentials
# ═══════════════════════════════════════════════════════════════════════════════
ET           = ZoneInfo("America/New_York")
HERE         = Path("/Users/quarki3")
LOCK_FILE    = Path("/tmp/parallel_bots.lock")
TRACK_RECORD = HERE / "track_record.jsonl"
RESULT_FILE  = HERE / "swarm_results/completed/parallel_paper_bots.json"
LOG_FILE     = HERE / "parallel_bots.log"
STATE_FILE   = HERE / "parallel_bots_state.json"

sys.path.insert(0, str(HERE))
from alpaca_keys import PAPER_KEY, PAPER_SECRET, BROKER_KEY, BROKER_SECRET

PAPER_BASE = "https://paper-api.alpaca.markets"
DATA_BASE  = "https://data.sandbox.alpaca.markets"   # CK keys → sandbox data

# ═══════════════════════════════════════════════════════════════════════════════
#  Capital splits (sum = $100k = full paper account)
# ═══════════════════════════════════════════════════════════════════════════════
CAPITAL = {
    "soxl_regime": 30_000.0,
    "momentum":    40_000.0,
    "levered_etf": 30_000.0,
}

# ═══════════════════════════════════════════════════════════════════════════════
#  Market hours (ET minutes from midnight)
# ═══════════════════════════════════════════════════════════════════════════════
PREMARKET_SCAN  = 8*60 + 28   # 08:28 morning scan (before open)
ENTRY_START     = 9*60 + 35   # 09:35 open entries
SOXL_ENTRY_END  = 11*60       # 11:00 last SOXL entry
MOM_ENTRY_END   = 10*60 + 30  # 10:30 last momentum entry
STOP_CHECK_END  = 15*60 + 50  # 15:50 stop monitoring ends
EOD_CLOSE_INTRA = 15*60 + 45  # 15:45 force-close intraday bots
SOXL_EOD_FORCE  = 15*60 + 55  # 15:55 force-close SOXL on day 3
EOD_LOG_TIME    = 16*60 + 5   # 16:05 log daily P&L

HOLIDAYS_2026 = {
    date(2026, 1, 1),  date(2026, 1, 19), date(2026, 2, 16),
    date(2026, 4, 3),  date(2026, 5, 25), date(2026, 7, 3),
    date(2026, 9, 7),  date(2026, 11, 26),date(2026, 12, 25),
}

# ═══════════════════════════════════════════════════════════════════════════════
#  Logging
# ═══════════════════════════════════════════════════════════════════════════════
_log_lock = threading.Lock()

def log(msg: str, bot: str = "runner") -> None:
    ts   = datetime.now(ET).strftime("%H:%M:%S")
    line = f"{ts} [{bot:12s}] {msg}"
    with _log_lock:
        print(line, flush=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")

# ═══════════════════════════════════════════════════════════════════════════════
#  Time helpers
# ═══════════════════════════════════════════════════════════════════════════════
def is_market_day(dt: datetime | None = None) -> bool:
    if dt is None:
        dt = datetime.now(ET)
    d = dt.astimezone(ET).date()
    return d.weekday() < 5 and d not in HOLIDAYS_2026

def et_mins(dt: datetime | None = None) -> int:
    if dt is None:
        dt = datetime.now(ET)
    loc = dt.astimezone(ET)
    return loc.hour * 60 + loc.minute

# ═══════════════════════════════════════════════════════════════════════════════
#  Alpaca paper API — orders & account
# ═══════════════════════════════════════════════════════════════════════════════
_order_lock = threading.Lock()

def _ph() -> dict:
    return {"APCA-API-KEY-ID": PAPER_KEY, "APCA-API-SECRET-KEY": PAPER_SECRET}

def _dh() -> dict:
    return {"APCA-API-KEY-ID": BROKER_KEY, "APCA-API-SECRET-KEY": BROKER_SECRET}

def get_account() -> dict:
    try:
        r = requests.get(f"{PAPER_BASE}/v2/account", headers=_ph(), timeout=8)
        return r.json() if r.ok else {}
    except Exception:
        return {}

def get_paper_positions() -> dict:
    """Returns {symbol: {qty, avg_entry_price, current_price}}."""
    try:
        r = requests.get(f"{PAPER_BASE}/v2/positions", headers=_ph(), timeout=8)
        if not r.ok:
            return {}
        out = {}
        for p in r.json():
            sym = p.get("symbol", "")
            out[sym] = {
                "qty":             float(p.get("qty", 0)),
                "avg_entry_price": float(p.get("avg_entry_price", 0)),
                "current_price":   float(p.get("current_price") or p.get("avg_entry_price", 0)),
                "unrealized_pl":   float(p.get("unrealized_pl", 0)),
            }
        return out
    except Exception:
        return {}

def place_order(symbol: str, qty: int, side: str, client_id: str) -> dict | None:
    if qty < 1:
        return None
    payload = {
        "symbol":          symbol,
        "qty":             str(qty),
        "side":            side,
        "type":            "market",
        "time_in_force":   "day",
        "client_order_id": client_id[:48],  # Alpaca limit 48 chars
    }
    with _order_lock:
        try:
            r = requests.post(f"{PAPER_BASE}/v2/orders", headers=_ph(),
                              json=payload, timeout=10)
            if r.status_code in (200, 201):
                return r.json()
            log(f"Order rejected {symbol} {side} {qty}: {r.status_code} {r.text[:100]}")
            return None
        except Exception as e:
            log(f"Order error {symbol}: {e}")
            return None

def close_all(symbol: str, client_id: str) -> None:
    """Close entire position in symbol."""
    try:
        r = requests.delete(f"{PAPER_BASE}/v2/positions/{symbol}",
                            headers=_ph(), timeout=10)
        if r.ok:
            return
    except Exception:
        pass
    positions = get_paper_positions()
    if symbol in positions:
        qty = int(positions[symbol]["qty"])
        if qty > 0:
            place_order(symbol, qty, "sell", client_id)

# ═══════════════════════════════════════════════════════════════════════════════
#  Data helpers
# ═══════════════════════════════════════════════════════════════════════════════
def _compute_adx(hi: pd.Series, lo: pd.Series, cl: pd.Series,
                 period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    tr   = pd.concat([(hi - lo), (hi - cl.shift(1)).abs(),
                      (lo - cl.shift(1)).abs()], axis=1).max(axis=1)
    up   = hi - hi.shift(1)
    dn   = lo.shift(1) - lo
    pdm  = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=hi.index)
    ndm  = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=hi.index)
    atr_ = tr.ewm(span=period, adjust=False).mean().replace(0, np.nan)
    pdi  = 100 * pdm.ewm(span=period, adjust=False).mean() / atr_
    ndi  = 100 * ndm.ewm(span=period, adjust=False).mean() / atr_
    dx   = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(span=period, adjust=False).mean(), pdi, ndi

def fetch_daily(symbol: str, period: str = "60d") -> pd.DataFrame:
    try:
        df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel(1, axis=1)
        df.columns = [c.lower() for c in df.columns]
        return df.dropna(how="all")
    except Exception:
        return pd.DataFrame()

def fetch_5min(symbol: str, n_bars: int = 60) -> pd.DataFrame:
    """5-min bars: tries Alpaca sandbox data API, falls back to yfinance."""
    try:
        end   = pd.Timestamp.now(tz="UTC")
        start = end - pd.Timedelta(hours=10)
        params = {
            "timeframe": "5Min",
            "start": start.isoformat(),
            "end":   end.isoformat(),
            "limit": n_bars,
            "feed":  "iex",
        }
        r = requests.get(f"{DATA_BASE}/v2/stocks/{symbol}/bars",
                         headers=_dh(), params=params, timeout=10)
        if r.ok and r.json().get("bars"):
            bars = r.json()["bars"]
            df = pd.DataFrame(bars)
            df["t"] = pd.to_datetime(df["t"], utc=True)
            df = df.set_index("t").rename(
                columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
            return df[["open", "high", "low", "close", "volume"]]
    except Exception:
        pass
    try:
        df = yf.download(symbol, period="2d", interval="5m",
                         progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex):
            df = df.droplevel(1, axis=1)
        df.columns = [c.lower() for c in df.columns]
        return df.dropna(how="all").tail(n_bars)
    except Exception:
        return pd.DataFrame()

def current_price(symbol: str) -> float | None:
    try:
        r = requests.get(f"{DATA_BASE}/v2/stocks/{symbol}/bars/latest",
                         headers=_dh(), timeout=6)
        if r.ok:
            bar = r.json().get("bar", {})
            p = float(bar.get("c") or 0)
            if p > 0:
                return p
    except Exception:
        pass
    try:
        df = yf.download(symbol, period="1d", interval="1m",
                         progress=False, auto_adjust=True)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df = df.droplevel(1, axis=1)
            return float(df["Close"].iloc[-1])
    except Exception:
        pass
    return None

# ═══════════════════════════════════════════════════════════════════════════════
#  Track record logger (mirrors track_record_logger.py interface)
# ═══════════════════════════════════════════════════════════════════════════════
def append_track_record(strategy: str, day_pnl: float, capital_used: float,
                        num_trades: int, fills: list | None = None) -> None:
    record: dict = {
        "date":       str(date.today()),
        "strategy":   strategy,
        "equity":     round(capital_used, 2),
        "day_pnl":    round(day_pnl, 2),
        "num_trades": num_trades,
    }
    if fills:
        record["fills"] = fills
    line = json.dumps(record, separators=(",", ":")) + "\n"
    TRACK_RECORD.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACK_RECORD, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    log(f"track_record: pnl={day_pnl:+.2f}  trades={num_trades}", strategy)

# ═══════════════════════════════════════════════════════════════════════════════
#  Bot 1: SOXL Regime
#  Strategy: 5-cond daily gate → 9:35–11:00 5-min entry → ATR trail, max 3 days
# ═══════════════════════════════════════════════════════════════════════════════
class SoxlRegimeBot:
    NAME     = "soxl_regime"
    CAPITAL  = CAPITAL["soxl_regime"]
    RISK_PCT = 0.02   # 2% of capital → stop_dist determines shares
    MAX_NOT  = 0.30   # cap at 30% of capital in notional
    TRAIL    = 1.25   # daily ATR multiplier for trail
    TAKE1    = 2.0    # sell 50% at +2× daily ATR
    HARD_PCT = 0.08   # 8% hard stop below entry
    MAX_DAYS = 3

    def __init__(self):
        self.gate_open    = False
        self.daily_atr    = 0.0
        self._gate_date   = None
        self.position     = None    # {qty, entry, stop, trail_off, high, take1_done, days_held, entry_date}
        self.day_pnl      = 0.0
        self.closed_fills = []
        self._seq         = 0

    def _cid(self) -> str:
        self._seq += 1
        return f"sxr_{date.today().strftime('%m%d')}_{self._seq:03d}"

    # ── Gate check ──────────────────────────────────────────────────────────
    def check_gate(self) -> None:
        if self._gate_date == date.today():
            return
        log("Checking regime gate...", self.NAME)
        try:
            smh  = fetch_daily("SMH",  "90d")
            spy  = fetch_daily("SPY",  "90d")
            soxl = fetch_daily("SOXL", "90d")
            vix  = fetch_daily("^VIX", "90d")
        except Exception as e:
            log(f"Gate fetch error: {e}", self.NAME)
            return
        if smh.empty or spy.empty or soxl.empty or len(smh) < 55:
            log("Gate: insufficient data", self.NAME)
            return
        try:
            sc = smh["close"]
            e20s = sc.ewm(span=20, adjust=False).mean()
            e50s = sc.ewm(span=50, adjust=False).mean()
            adx_s, _, _ = _compute_adx(smh["high"], smh["low"], sc, 14)
            spy_c  = spy["close"]
            sma200 = spy_c.rolling(200, min_periods=150).mean()
            sx = soxl["close"]
            e20x = sx.ewm(span=20, adjust=False).mean()
            e50x = sx.ewm(span=50, adjust=False).mean()
            tr = pd.concat([
                (soxl["high"] - soxl["low"]),
                (soxl["high"] - sx.shift(1)).abs(),
                (soxl["low"]  - sx.shift(1)).abs(),
            ], axis=1).max(axis=1)
            atr14 = tr.ewm(span=14, adjust=False).mean()

            i = -2  # previous day's bar (yesterday's closed)
            c1 = bool(sc.iloc[i] > e50s.iloc[i] and e20s.iloc[i] > e50s.iloc[i])
            c2 = bool(adx_s.iloc[i] > 20)
            c3 = bool(spy_c.iloc[i] > sma200.iloc[i])
            c4 = True if vix.empty else bool(vix["close"].iloc[i] < 25)
            c5 = bool(e20x.iloc[i] > e50x.iloc[i] and sx.iloc[i] > e50x.iloc[i])

            self.gate_open  = c1 and c2 and c3 and c4 and c5
            self.daily_atr  = float(atr14.iloc[i])
            self._gate_date = date.today()
            status = "OPEN" if self.gate_open else "CLOSED"
            log(f"Gate {status}  c1={c1} c2={c2} c3={c3} c4={c4} c5={c5} "
                f"ATR={self.daily_atr:.2f}", self.NAME)
        except Exception as e:
            log(f"Gate calc error: {e}", self.NAME)

    # ── Entry signal ────────────────────────────────────────────────────────
    def check_entry(self) -> bool:
        try:
            bars = fetch_5min("SOXL", 55)
            if bars.empty or len(bars) < 6:
                return False
            cl     = bars["close"]
            ema25  = cl.ewm(span=25, adjust=False).mean()
            prev_d = fetch_daily("SOXL", "5d")
            if prev_d.empty:
                return False
            prev_day_close = float(prev_d["close"].iloc[-2])
            signal = (
                float(cl.iloc[-1]) > float(ema25.iloc[-1])
                and float(cl.iloc[-1]) > float(cl.iloc[-2])
                and float(cl.iloc[-1]) > prev_day_close
            )
            return signal
        except Exception as e:
            log(f"Entry signal error: {e}", self.NAME)
            return False

    # ── Position open ───────────────────────────────────────────────────────
    def enter(self, price: float) -> None:
        if self.daily_atr <= 0:
            log("daily_atr=0, cannot size; skip entry", self.NAME)
            return
        trail_dist  = self.daily_atr * self.TRAIL
        risk_shares = int((self.CAPITAL * self.RISK_PCT) / trail_dist)
        max_shares  = int((self.CAPITAL * self.MAX_NOT)  / price)
        qty         = max(1, min(risk_shares, max_shares))
        cid = self._cid()
        order = place_order("SOXL", qty, "buy", cid)
        if not order:
            return
        stop_raw  = price - trail_dist
        hard_stop = price * (1 - self.HARD_PCT)
        stop      = max(stop_raw, hard_stop)          # tightest of the two
        take1     = price + self.daily_atr * self.TAKE1
        self.position = {
            "qty": qty, "entry": price, "stop": stop,
            "trail_off": trail_dist, "high": price,
            "take1_target": take1, "take1_done": False,
            "days_held": 0, "entry_date": str(date.today()), "cid": cid,
        }
        log(f"BUY {qty}sh @ {price:.2f}  stop={stop:.2f}  take1={take1:.2f}", self.NAME)

    # ── Intraday management ─────────────────────────────────────────────────
    def manage(self, price: float) -> None:
        p = self.position
        if not p:
            return

        # Advance trailing high
        if price > p["high"]:
            p["high"] = price
            candidate = price - p["trail_off"]
            if candidate > p["stop"]:
                p["stop"] = candidate

        # Tiered take-profit: sell 50% at +2×ATR
        if not p["take1_done"] and price >= p["take1_target"]:
            sell_qty = max(1, p["qty"] // 2)
            cid = self._cid()
            place_order("SOXL", sell_qty, "sell", cid)
            pnl = (price - p["entry"]) * sell_qty
            self.day_pnl += pnl
            self.closed_fills.append({
                "symbol": "SOXL", "qty": sell_qty,
                "entry_price": p["entry"], "exit_price": price,
                "pnl": round(pnl, 2), "reason": "take1",
            })
            p["qty"]      -= sell_qty
            p["take1_done"] = True
            p["stop"]      = max(p["stop"], p["entry"])  # raise stop to breakeven
            log(f"TAKE1 sell {sell_qty}sh @ {price:.2f}  pnl={pnl:+.2f}", self.NAME)
            if p["qty"] < 1:
                self.position = None
            return

        # Stop hit
        if price <= p["stop"]:
            pnl = (price - p["entry"]) * p["qty"]
            self.day_pnl += pnl
            self.closed_fills.append({
                "symbol": "SOXL", "qty": p["qty"],
                "entry_price": p["entry"], "exit_price": price,
                "pnl": round(pnl, 2), "reason": "stop",
            })
            close_all("SOXL", self._cid())
            log(f"STOP {p['qty']}sh @ {price:.2f}  pnl={pnl:+.2f}", self.NAME)
            self.position = None

    def force_close(self, price: float, reason: str = "eod") -> None:
        if not self.position:
            return
        p   = self.position
        pnl = (price - p["entry"]) * p["qty"]
        self.day_pnl += pnl
        self.closed_fills.append({
            "symbol": "SOXL", "qty": p["qty"],
            "entry_price": p["entry"], "exit_price": price,
            "pnl": round(pnl, 2), "reason": reason,
        })
        close_all("SOXL", self._cid())
        log(f"FORCE_CLOSE({reason}) {p['qty']}sh @ {price:.2f}  pnl={pnl:+.2f}", self.NAME)
        self.position = None

    # ── Reconcile existing positions on startup ──────────────────────────────
    def reconcile(self) -> None:
        positions = get_paper_positions()
        if "SOXL" not in positions:
            return
        p     = positions["SOXL"]
        entry = p["avg_entry_price"]
        atr   = self.daily_atr if self.daily_atr > 0 else entry * 0.035
        stop  = max(entry - atr * self.TRAIL, entry * (1 - self.HARD_PCT))
        self.position = {
            "qty": int(p["qty"]), "entry": entry,
            "stop": stop, "trail_off": atr * self.TRAIL,
            "high": p["current_price"], "take1_target": entry + atr * self.TAKE1,
            "take1_done": False, "days_held": 0,
            "entry_date": str(date.today()), "cid": "reconciled",
        }
        log(f"Reconciled {int(p['qty'])}sh @ {entry:.2f}  stop={stop:.2f}", self.NAME)

    def _log_pnl(self) -> None:
        append_track_record(self.NAME, self.day_pnl, self.CAPITAL,
                            len(self.closed_fills), self.closed_fills)
        self.day_pnl      = 0.0
        self.closed_fills = []

    # ── Main event loop ──────────────────────────────────────────────────────
    def run(self) -> None:
        log("Bot started", self.NAME)
        self.check_gate()
        self.reconcile()
        today_entered = None
        pnl_logged    = None

        while True:
            try:
                now  = datetime.now(ET)
                mins = et_mins(now)
                tod  = now.date()

                if not is_market_day(now):
                    time.sleep(300)
                    continue

                # ── Daily gate at pre-market ───────────────────────────────
                if mins >= PREMARKET_SCAN:
                    self.check_gate()

                # ── Entry window ───────────────────────────────────────────
                if (ENTRY_START <= mins <= SOXL_ENTRY_END
                        and self.gate_open
                        and self.position is None
                        and today_entered != tod):
                    if self.check_entry():
                        price = current_price("SOXL")
                        if price:
                            self.enter(price)
                            today_entered = tod

                # ── Increment days_held on new calendar day ────────────────
                if self.position and mins == ENTRY_START:
                    if self.position.get("entry_date") != str(tod):
                        self.position["days_held"] = self.position.get("days_held", 0) + 1

                # ── Intraday stop management ───────────────────────────────
                if self.position and ENTRY_START <= mins <= STOP_CHECK_END:
                    price = current_price("SOXL")
                    if price:
                        self.manage(price)

                # ── Force EOD close: day 3, OR gate closed and past 15:45 ──
                if self.position:
                    days  = self.position.get("days_held", 0)
                    force = (days >= 2 and mins >= SOXL_EOD_FORCE)
                    if not self.gate_open and mins >= EOD_CLOSE_INTRA:
                        force = True
                    if force:
                        price = current_price("SOXL") or self.position["entry"]
                        self.force_close(price, "eod_day3" if days >= 2 else "gate_closed")

                # ── Log P&L once per day after close ──────────────────────
                if mins >= EOD_LOG_TIME and pnl_logged != tod:
                    self._log_pnl()
                    pnl_logged = tod

                time.sleep(60)

            except Exception as e:
                log(f"Loop error: {e}", self.NAME)
                time.sleep(60)


# ═══════════════════════════════════════════════════════════════════════════════
#  Bot 2: Momentum Stocks
#  Strategy: ADX/vol/mom scan → top-2 stocks → 9:35 entry → 15:45 EOD close
# ═══════════════════════════════════════════════════════════════════════════════
MOMENTUM_UNIVERSE = [
    # Large-cap momentum / tech
    "NVDA", "AMD",  "MSFT", "META", "GOOGL", "AAPL", "AMZN", "TSLA", "AVGO",
    "LLY",  "JPM",  "GS",   "MS",   "UNH",   "VRTX", "AMGN", "ISRG",
    # Growth/AI
    "PLTR", "CRWD", "DDOG", "NET",  "APP",  "COIN", "SMCI", "SOFI", "HOOD",
    # ETFs that complement (not leveraged)
    "QQQ",  "IWM",  "XLK",  "XLF",  "GLD",
]

class MomentumStocksBot:
    NAME      = "momentum"
    CAPITAL   = CAPITAL["momentum"]
    MAX_SLOTS = 2
    MAX_PCT   = 0.22    # up to 22% per position (2 slots × 22% = 44%, leaves room)
    TRAIL     = 1.25    # ATR trail multiplier
    MIN_ADX   = 25
    MIN_SCORE = 38

    def __init__(self):
        self.picks      : list  = []   # [(sym, score, dir)]
        self.positions  : dict  = {}   # {sym: {qty, entry, stop, trail_off, high, cid}}
        self.day_pnl    : float = 0.0
        self.closed_fills: list = []
        self._seq        = 0
        self._scan_date  = None

    def _cid(self, sym: str = "") -> str:
        self._seq += 1
        return f"mom_{sym[:4]}_{date.today().strftime('%m%d')}_{self._seq:03d}"

    def morning_scan(self) -> None:
        log("Running morning scan...", self.NAME)
        results = []
        for sym in MOMENTUM_UNIVERSE:
            try:
                df = fetch_daily(sym, "35d")
                if df.empty or len(df) < 22:
                    continue
                cl = df["close"]
                adx, pdi, ndi = _compute_adx(df["high"], df["low"], cl, 14)
                a = float(adx.iloc[-1])
                p = float(pdi.iloc[-1])
                n = float(ndi.iloc[-1])
                if a < self.MIN_ADX:
                    continue
                vol_avg = df["volume"].iloc[-20:-1].mean()
                vol_r   = df["volume"].iloc[-1] / vol_avg if vol_avg > 0 else 1.0
                mom1d   = (float(cl.iloc[-1]) - float(cl.iloc[-2])) / float(cl.iloc[-2])
                mom5d   = (float(cl.iloc[-1]) - float(cl.iloc[-6])) / float(cl.iloc[-6])
                direction = "long" if p > n else "short"
                score  = (40 if a > 50 else 30 if a > 35 else 15)
                score += (20 if vol_r > 2.0 else 10 if vol_r > 1.5 else 3)
                if direction == "long":
                    score += (15 if mom1d > 0.02 else 8 if mom1d > 0.005 else 0)
                    score += (10 if mom5d > 0.05 else 5 if mom5d > 0.01 else 0)
                else:
                    score += (15 if mom1d < -0.02 else 8 if mom1d < -0.005 else 0)
                    score += (10 if mom5d < -0.05 else 5 if mom5d < -0.01 else 0)
                if score >= self.MIN_SCORE:
                    results.append((sym, score, direction))
            except Exception:
                pass
        results.sort(key=lambda x: -x[1])
        self.picks     = results[:self.MAX_SLOTS * 2]  # keep 2× slots as backup
        self._scan_date = date.today()
        top = [(s, int(sc), d) for s, sc, d in self.picks[:4]]
        log(f"Scan complete: top={top}", self.NAME)

    def try_enter(self, sym: str, direction: str, price: float) -> None:
        if sym in self.positions or len(self.positions) >= self.MAX_SLOTS:
            return
        if direction != "long":   # long-only in paper account
            return
        budget = self.CAPITAL * self.MAX_PCT
        qty    = int(budget / price)
        if qty < 1:
            return
        df = fetch_daily(sym, "30d")
        if df.empty:
            return
        tr = pd.concat([
            (df["high"] - df["low"]),
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"]  - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr       = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])
        trail_off = atr * self.TRAIL
        stop      = price - trail_off
        cid = self._cid(sym)
        order = place_order(sym, qty, "buy", cid)
        if order:
            self.positions[sym] = {
                "qty": qty, "entry": price, "stop": stop,
                "trail_off": trail_off, "high": price, "cid": cid,
            }
            log(f"BUY {sym} {qty}sh @ {price:.2f}  stop={stop:.2f}", self.NAME)

    def check_stops(self) -> None:
        to_del = []
        for sym, p in self.positions.items():
            price = current_price(sym)
            if not price:
                continue
            if price > p["high"]:
                p["high"] = price
                cand = price - p["trail_off"]
                if cand > p["stop"]:
                    p["stop"] = cand
            if price <= p["stop"]:
                pnl = (price - p["entry"]) * p["qty"]
                self.day_pnl += pnl
                self.closed_fills.append({
                    "symbol": sym, "qty": p["qty"],
                    "entry_price": p["entry"], "exit_price": price,
                    "pnl": round(pnl, 2), "reason": "stop",
                })
                close_all(sym, self._cid(sym))
                log(f"STOP {sym} {p['qty']}sh @ {price:.2f}  pnl={pnl:+.2f}", self.NAME)
                to_del.append(sym)
        for sym in to_del:
            del self.positions[sym]

    def eod_close_all(self) -> None:
        for sym, p in list(self.positions.items()):
            price = current_price(sym) or p["entry"]
            pnl   = (price - p["entry"]) * p["qty"]
            self.day_pnl += pnl
            self.closed_fills.append({
                "symbol": sym, "qty": p["qty"],
                "entry_price": p["entry"], "exit_price": price,
                "pnl": round(pnl, 2), "reason": "eod",
            })
            close_all(sym, self._cid(sym))
            log(f"EOD {sym} {p['qty']}sh @ {price:.2f}  pnl={pnl:+.2f}", self.NAME)
        self.positions.clear()

    def reconcile(self) -> None:
        positions = get_paper_positions()
        for sym in list(positions):
            if sym in ("SOXL",):
                continue   # belongs to soxl_regime bot
            if sym in [e for e in ("TQQQ","UPRO","LABU","FNGU","TECL","TNA","SPXL","UDOW","NAIL","DPST")]:
                continue   # belongs to levered_etf bot
            p     = positions[sym]
            entry = p["avg_entry_price"]
            atr   = entry * 0.02   # 2% fallback ATR estimate
            self.positions[sym] = {
                "qty": int(p["qty"]), "entry": entry,
                "stop": entry - atr * self.TRAIL, "trail_off": atr * self.TRAIL,
                "high": p["current_price"], "cid": "reconciled",
            }
            log(f"Reconciled {sym} {int(p['qty'])}sh @ {entry:.2f}", self.NAME)

    def _log_pnl(self) -> None:
        append_track_record(self.NAME, self.day_pnl, self.CAPITAL,
                            len(self.closed_fills), self.closed_fills)
        self.day_pnl       = 0.0
        self.closed_fills  = []
        self.picks         = []

    def run(self) -> None:
        log("Bot started", self.NAME)
        self.reconcile()
        entered_today: set = set()
        pnl_logged         = None

        while True:
            try:
                now  = datetime.now(ET)
                mins = et_mins(now)
                tod  = now.date()

                if not is_market_day(now):
                    time.sleep(300)
                    continue

                # ── Morning scan ───────────────────────────────────────────
                if mins >= PREMARKET_SCAN and self._scan_date != tod:
                    self.morning_scan()

                # ── Open entries ───────────────────────────────────────────
                if (ENTRY_START <= mins <= MOM_ENTRY_END
                        and self.picks and len(self.positions) < self.MAX_SLOTS):
                    for sym, score, direction in self.picks:
                        if sym not in entered_today and sym not in self.positions:
                            price = current_price(sym)
                            if price:
                                self.try_enter(sym, direction, price)
                                entered_today.add(sym)
                            if len(self.positions) >= self.MAX_SLOTS:
                                break

                # ── Stop management ────────────────────────────────────────
                if self.positions and ENTRY_START <= mins <= STOP_CHECK_END:
                    self.check_stops()

                # ── EOD force-close ────────────────────────────────────────
                if mins >= EOD_CLOSE_INTRA and self.positions:
                    self.eod_close_all()

                # ── Log P&L ────────────────────────────────────────────────
                if mins >= EOD_LOG_TIME and pnl_logged != tod:
                    self._log_pnl()
                    pnl_logged    = tod
                    entered_today = set()

                time.sleep(60)

            except Exception as e:
                log(f"Loop error: {e}", self.NAME)
                time.sleep(60)


# ═══════════════════════════════════════════════════════════════════════════════
#  Bot 3: Levered ETF Scanner
#  Strategy: scan TQQQ/UPRO/LABU/FNGU/TECL/TNA/SPXL (not SOXL), enter best ADX → 15:45 EOD
# ═══════════════════════════════════════════════════════════════════════════════
LEVERED_UNIVERSE = [
    "TQQQ", "UPRO", "LABU", "FNGU", "TECL",
    "TNA",  "SPXL", "UDOW", "NAIL", "DPST",
]

class LeveredEtfBot:
    NAME     = "levered_etf"
    CAPITAL  = CAPITAL["levered_etf"]
    MAX_PCT  = 0.45    # concentrate up to 45% in one ETF
    TRAIL    = 1.5     # wider trail for leveraged volatility
    MIN_ADX  = 30

    def __init__(self):
        self.pick       : tuple | None = None    # (symbol, direction)
        self.position   : dict | None  = None    # {symbol, qty, entry, stop, trail_off, high, cid}
        self.day_pnl    : float        = 0.0
        self.closed_fills: list        = []
        self._seq        = 0
        self._scan_date  = None

    def _cid(self, sym: str = "") -> str:
        self._seq += 1
        return f"etf_{sym[:4]}_{date.today().strftime('%m%d')}_{self._seq:03d}"

    def morning_scan(self) -> None:
        log("Scanning leveraged ETFs...", self.NAME)
        best_score  = -1.0
        best_sym    = None
        best_dir    = None
        for sym in LEVERED_UNIVERSE:
            try:
                df = fetch_daily(sym, "35d")
                if df.empty or len(df) < 22:
                    continue
                cl = df["close"]
                adx, pdi, ndi = _compute_adx(df["high"], df["low"], cl, 14)
                a = float(adx.iloc[-1])
                p = float(pdi.iloc[-1])
                n = float(ndi.iloc[-1])
                if a < self.MIN_ADX:
                    continue
                direction = "long" if p > n else "short"
                if direction != "long":
                    continue   # long-only
                mom1d = (float(cl.iloc[-1]) - float(cl.iloc[-2])) / float(cl.iloc[-2])
                mom5d = (float(cl.iloc[-1]) - float(cl.iloc[-6])) / float(cl.iloc[-6])
                score  = a
                score += (20 if mom1d > 0.02 else 10 if mom1d > 0.005 else 0)
                score += (15 if mom5d > 0.05 else 8  if mom5d > 0.01  else 0)
                if score > best_score:
                    best_score = score
                    best_sym   = sym
                    best_dir   = direction
            except Exception:
                pass
        self.pick      = (best_sym, best_dir) if best_sym else None
        self._scan_date = date.today()
        log(f"Best ETF: {self.pick}  score={best_score:.0f}", self.NAME)

    def try_enter(self) -> None:
        if not self.pick or self.position:
            return
        sym, direction = self.pick
        if direction != "long":
            return
        price = current_price(sym)
        if not price:
            return
        budget = self.CAPITAL * self.MAX_PCT
        qty    = int(budget / price)
        if qty < 1:
            return
        df = fetch_daily(sym, "30d")
        if df.empty:
            return
        tr = pd.concat([
            (df["high"] - df["low"]),
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"]  - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr       = float(tr.ewm(span=14, adjust=False).mean().iloc[-1])
        trail_off = atr * self.TRAIL
        stop      = price - trail_off
        cid = self._cid(sym)
        order = place_order(sym, qty, "buy", cid)
        if order:
            self.position = {
                "symbol": sym, "qty": qty, "entry": price,
                "stop": stop, "trail_off": trail_off, "high": price, "cid": cid,
            }
            log(f"BUY {sym} {qty}sh @ {price:.2f}  stop={stop:.2f}", self.NAME)

    def check_stop(self) -> None:
        if not self.position:
            return
        p   = self.position
        sym = p["symbol"]
        price = current_price(sym)
        if not price:
            return
        if price > p["high"]:
            p["high"] = price
            cand = price - p["trail_off"]
            if cand > p["stop"]:
                p["stop"] = cand
        if price <= p["stop"]:
            pnl = (price - p["entry"]) * p["qty"]
            self.day_pnl += pnl
            self.closed_fills.append({
                "symbol": sym, "qty": p["qty"],
                "entry_price": p["entry"], "exit_price": price,
                "pnl": round(pnl, 2), "reason": "stop",
            })
            close_all(sym, self._cid(sym))
            log(f"STOP {sym} {p['qty']}sh @ {price:.2f}  pnl={pnl:+.2f}", self.NAME)
            self.position = None

    def eod_close(self) -> None:
        if not self.position:
            return
        p     = self.position
        sym   = p["symbol"]
        price = current_price(sym) or p["entry"]
        pnl   = (price - p["entry"]) * p["qty"]
        self.day_pnl += pnl
        self.closed_fills.append({
            "symbol": sym, "qty": p["qty"],
            "entry_price": p["entry"], "exit_price": price,
            "pnl": round(pnl, 2), "reason": "eod",
        })
        close_all(sym, self._cid(sym))
        log(f"EOD {sym} {p['qty']}sh @ {price:.2f}  pnl={pnl:+.2f}", self.NAME)
        self.position = None

    def reconcile(self) -> None:
        positions = get_paper_positions()
        for sym in LEVERED_UNIVERSE:
            if sym not in positions:
                continue
            p     = positions[sym]
            entry = p["avg_entry_price"]
            atr   = entry * 0.03
            self.position = {
                "symbol": sym, "qty": int(p["qty"]), "entry": entry,
                "stop": entry - atr * self.TRAIL, "trail_off": atr * self.TRAIL,
                "high": p["current_price"], "cid": "reconciled",
            }
            log(f"Reconciled {sym} {int(p['qty'])}sh @ {entry:.2f}", self.NAME)
            break   # one position only

    def _log_pnl(self) -> None:
        append_track_record(self.NAME, self.day_pnl, self.CAPITAL,
                            len(self.closed_fills), self.closed_fills)
        self.day_pnl       = 0.0
        self.closed_fills  = []
        self.pick          = None

    def run(self) -> None:
        log("Bot started", self.NAME)
        self.reconcile()
        entered_today = False
        pnl_logged    = None

        while True:
            try:
                now  = datetime.now(ET)
                mins = et_mins(now)
                tod  = now.date()

                if not is_market_day(now):
                    time.sleep(300)
                    continue

                # ── Morning scan ───────────────────────────────────────────
                if mins >= PREMARKET_SCAN and self._scan_date != tod:
                    self.morning_scan()

                # ── Enter at market open ───────────────────────────────────
                if mins >= ENTRY_START and not entered_today and self.pick and not self.position:
                    self.try_enter()
                    entered_today = True

                # ── Stop management ────────────────────────────────────────
                if self.position and ENTRY_START <= mins <= STOP_CHECK_END:
                    self.check_stop()

                # ── EOD force-close ────────────────────────────────────────
                if mins >= EOD_CLOSE_INTRA and self.position:
                    self.eod_close()

                # ── Log P&L ────────────────────────────────────────────────
                if mins >= EOD_LOG_TIME and pnl_logged != tod:
                    self._log_pnl()
                    pnl_logged    = tod
                    entered_today = False

                time.sleep(60)

            except Exception as e:
                log(f"Loop error: {e}", self.NAME)
                time.sleep(60)


# ═══════════════════════════════════════════════════════════════════════════════
#  Idempotency: exclusive file lock (safe under launchd KeepAlive)
# ═══════════════════════════════════════════════════════════════════════════════
_lock_fh = None

def acquire_lock() -> bool:
    global _lock_fh
    _lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh.write(str(os.getpid()))
        _lock_fh.flush()
        return True
    except (IOError, OSError):
        return False

# ═══════════════════════════════════════════════════════════════════════════════
#  Status JSON
# ═══════════════════════════════════════════════════════════════════════════════
def write_result(bot_names: list, success: bool = True) -> None:
    result = {
        "task":          "parallel_paper_bots",
        "bots_running":  bot_names,
        "capital_split": {k: int(v) for k, v in CAPITAL.items()},
        "summary": (
            f"{len(bot_names)} uncorrelated bots running on Alpaca paper account. "
            f"soxl_regime ($30k): SOXL-only, 5-condition regime gate, 9:35–11:00 entry, "
            f"1.25×daily-ATR trail, max 3-day hold — Sharpe 1.211 in backtest. "
            f"momentum ($40k): top-2 individual stocks by ADX+vol+momentum scan, "
            f"9:35 entry, 15:45 EOD close, 1.25×ATR trail. "
            f"levered_etf ($30k): best ADX-ranked leveraged ETF from TQQQ/UPRO/LABU/FNGU/TECL "
            f"(excl SOXL — handled by Bot 1), 9:35 entry, 15:45 EOD close. "
            f"Strategies are low-correlation: different universe (SOXL vs stocks vs non-SOXL ETFs), "
            f"different hold periods (1–3d vs intraday), different entry triggers (regime gate vs "
            f"momentum scan vs ADX score). Daily P&L per bot logged to track_record.jsonl. "
            f"PID {os.getpid()} | lock {LOCK_FILE}"
        ),
        "success":  success,
        "started":  datetime.now(ET).isoformat(),
        "pid":      os.getpid(),
        "log":      str(LOG_FILE),
        "track_record": str(TRACK_RECORD),
    }
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULT_FILE, "w") as f:
        json.dump(result, f, indent=2)
    log(f"Result → {RESULT_FILE}")


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════
def main() -> None:
    if not acquire_lock():
        # Another instance is already running — sleep forever so launchd
        # KeepAlive doesn't loop (process stays alive, no restart triggered).
        log("Lock held by another instance — sleeping (launchd won't restart).")
        while True:
            time.sleep(3600)

    bots = [
        SoxlRegimeBot(),
        MomentumStocksBot(),
        LeveredEtfBot(),
    ]

    threads = []
    for bot in bots:
        t = threading.Thread(target=bot.run, name=bot.NAME, daemon=True)
        t.start()
        threads.append((t, bot))
        log(f"Thread started: {bot.NAME}  capital=${CAPITAL[bot.NAME]:,.0f}")

    write_result([b.NAME for b in bots])

    log(f"All {len(bots)} bots running — PID {os.getpid()}", "runner")

    while True:
        for i, (t, bot) in enumerate(threads):
            if not t.is_alive():
                log(f"Thread {t.name} died — restarting", "runner")
                new_t = threading.Thread(target=bot.run, name=t.name, daemon=True)
                new_t.start()
                threads[i] = (new_t, bot)
        time.sleep(30)


if __name__ == "__main__":
    main()
