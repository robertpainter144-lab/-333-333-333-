#!/usr/bin/env python3
"""
Intraday Paper Trader — redesigned 2026-05-08
═══════════════════════════════════════════════════════════════════════════════
Root-cause fixes from prior version:
  • CX/cemex removed from always-watch — was generating 300+ rebalance-churn
    trades at -$5/cycle × 6 cycles/min = -$30/min hemorrhage
  • Rebalance now requires: 60-min minimum hold, 50-pt score gap (was 25),
    and position must be underwater (never evict a winner)
  • Trail stop floor: max(N×ATR, 1.2% of price) — prevents instant stopouts
    on low-ATR symbols like CX at $13

New features:
  • Pre-market scan at 08:30 ET: full universe ranked by gap%, pre-mkt vol,
    ADX, ATR% → top 50 stored in premarket_picks.json
  • 09:35 ET market-open entry: enter top-ranked picks with regime confirmation
  • 15:45 ET: close all intraday positions (no overnight drift)
  • 16:05 ET: daily review — what moved, what we missed, why, adaptive weights
  • strategy_stats.json: rolling win-rate by ADX/vol/time bucket → auto-tunes
    ADX and volume thresholds after 20+ trades
"""

import base64, json, sys, time, warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import yfinance as yf

warnings.filterwarnings("ignore")

ET             = ZoneInfo("America/New_York")
STATE_FILE     = Path("portfolio_state.json")
LOG_FILE       = Path("portfolio.log")
TRIGGER_FILE   = Path("portfolio_trigger.json")
PREMARKET_FILE = Path("premarket_picks.json")
REVIEW_FILE    = Path("daily_review.json")
STATS_FILE     = Path("strategy_stats.json")

# ── Alpaca credentials (loaded from macOS Keychain via alpaca_keys.py) ────────
import sys as _sys; _sys.path.insert(0, str(Path(__file__).parent))
from alpaca_keys import BROKER_KEY as ALPACA_KEY, BROKER_SECRET as ALPACA_SECRET, BROKER_ACCT as ALPACA_ACCT
ALPACA_BASE   = "https://broker-api.sandbox.alpaca.markets"
_ALPACA_SKIP  = lambda s: s.endswith("=X") or s.endswith("-USD")

# ── Tradier paper-trading credentials ─────────────────────────────────────────
TRADIER_TOKEN = ""
TRADIER_ACCT  = ""
TRADIER_BASE  = "https://sandbox.tradier.com/v1"

# ── IBKR live-trading flag ─────────────────────────────────────────────────────
# Keep False until ALL of these are confirmed:
#   1. IB Gateway running, logged in, API enabled (port 4001 live / 4002 paper)
#   2. ibkr_connection_check.py prints "IBKR READY" with a positive NLV
#   3. You have reviewed risk limits and accept live-money exposure
IBKR_LIVE_ENABLED = False   # ← gate: never flips to True automatically

IBKR_HOST      = "127.0.0.1"
IBKR_PORT      = 4001        # 4001 = IB Gateway live | 4002 = IB Gateway paper
IBKR_CLIENT_ID = 20          # unique clientId for this bot (qc_portfolio uses 1)
IBKR_ACCOUNT   = ""          # blank = auto-select first managed account

# ── Risk / strategy params ────────────────────────────────────────────────────
INITIAL_CASH      = 50_000.0
MAX_POS_PCT       = 0.30     # tuned 2026-06-15: 0.20→0.30
MAX_POSITIONS     = 5        # tuned 2026-06-15: 6→5
TRAIL_MULT        = 1.25     # unchanged (coord-ascent confirmed optimal)
TRAIL_MULT_SHORT  = 1.0
MIN_TRAIL_PCT     = 0.005    # tuned 2026-06-15: 0.012→0.005 (tighter floor, faster stopout)
COMM              = 0.005
CORRELATION_LIMIT = 0.80     # max Pearson corr allowed between new and any open position

# Timing constants (all in minutes from midnight ET unless noted)
EOD_INTRADAY      = 15 * 60 + 45   # close all intraday positions at 15:45
EOD_EVAL          = 15 * 60 + 30   # evaluate overnight holds at 15:30
PREMARKET_HOUR    = 8
PREMARKET_MIN     = 30              # pre-market scan at 08:30
MARKET_OPEN_MIN   = 9 * 60 + 35    # enter picks at 09:35
REVIEW_HOUR       = 16
REVIEW_MIN        = 5               # daily review at 16:05

# Scanner / entry quality
ADX_THRESH        = 25             # minimum ADX for trend (25 catches more setups than 35)
SCAN_MIN_SCORE    = 30             # tuned 2026-06-15: 35→30 (catches more valid setups)
RESCAN_MINS       = 20             # rescan every 20 min during RTH
PM_MIN_SCORE      = 40             # higher bar for pre-market picks (less data, lower liquidity)

# Rebalance guards (prior version had none — this was the bug)
MIN_HOLD_SECS     = 60 * 60        # position must be held 60 min before rebalance-eligible
REBALANCE_GAP_S   = 120 * 60       # max 1 rebalance per 2 hours
REBALANCE_THRESH  = 50             # new score must beat current by 50 pts (was 25)

# Universe: quality-filtered symbols — high-beta, large-cap, ETFs, crypto, forex
# Trimmed 2026-06-14: removed SQ (delisted), CORN/USDMXN=X/USDBRL=X/USDCNY=X (failed dvol+sharpe)
SCANNER_UNIVERSE = [
    # US large-cap / momentum
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","JPM","V",
    "LLY","XOM","MA","BAC","WMT","CVX","AMD","MCD","GE","NFLX","QCOM",
    "GS","MS","SBUX","AMAT","GILD","SYK","ISRG","VRTX","F","UBER",
    "COIN","PLTR","SOFI","MSTR","SMCI","CRWD","SNOW","SHOP","SE","HOOD",
    "APP","MU","ANET","PANW","DKNG","RBLX","TTD","PINS","DUOL",
    "QQQ","IWM","XLK","XLF","XLE","XLV","SOXL","TQQQ",
    # Commodities ETFs
    "GLD","SLV","GDX","USO","UNG","WEAT","SOYB","DBA","PDBC",
    # International / emerging markets
    "EWJ","EWG","EWU","EWC","EWA","FXI","KWEB","EEM","EWZ","INDA",
    "EWH","EWT","EWY","VWO","IEMG","EWS",
    # FOREX (continuous 24/5)
    "EURUSD=X","GBPUSD=X","USDJPY=X","AUDUSD=X","USDCAD=X",
    "USDCHF=X","NZDUSD=X",
    # Crypto (24/7)
    "BTC-USD","ETH-USD","SOL-USD","BNB-USD","XRP-USD",
]

# CX intentionally excluded from ALWAYS_WATCH — it was the #1 loss driver
ALWAYS_WATCH_OVN = {"GLD","GDX","BTC-USD","ETH-USD","EURUSD=X","GBPUSD=X","USDJPY=X"}
ALWAYS_WATCH     = {"SPY"}   # RTH only


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str):
    ts   = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── Alpaca broker wrapper ─────────────────────────────────────────────────────

class AlpacaBroker:

    def __init__(self):
        creds = base64.b64encode(f"{ALPACA_KEY}:{ALPACA_SECRET}".encode()).decode()
        self._hdrs = {
            "Authorization": f"Basic {creds}",
            "Content-Type":  "application/json",
        }
        self._base  = ALPACA_BASE
        self._acct  = ALPACA_ACCT
        self._ready = False
        self._check_ready()

    def _check_ready(self):
        try:
            r = requests.get(
                f"{self._base}/v1/trading/accounts/{self._acct}/account",
                headers=self._hdrs, timeout=8)
            d = r.json()
            bp = float(d.get("buying_power", 0))
            self._ready = bp > 0
            if self._ready:
                log(f"Alpaca ready  equity=${float(d.get('equity',0)):,.0f}"
                    f"  cash=${float(d.get('cash',0)):,.0f}")
            else:
                log(f"Alpaca account active but cash not settled (bp={bp:.0f})")
        except Exception as e:
            log(f"Alpaca connectivity check failed: {e}")

    def account(self) -> dict:
        try:
            r = requests.get(
                f"{self._base}/v1/trading/accounts/{self._acct}/account",
                headers=self._hdrs, timeout=8)
            return r.json()
        except Exception:
            return {}

    def place_order(self, symbol: str, side: str, price: float) -> str | None:
        if _ALPACA_SKIP(symbol):
            return None
        if side == "short":
            return None
        if not self._ready:
            self._check_ready()
            if not self._ready:
                return None
        try:
            acct   = self.account()
            bp     = float(acct.get("buying_power", 0))
            if bp <= 0:
                log(f"Alpaca skip {symbol}: no buying power")
                return None
            budget = min(bp * MAX_POS_PCT, bp * 0.95)
            qty    = int(budget / price) if price > 0 else 0
            if qty < 1:
                return None
            r = requests.post(
                f"{self._base}/v1/trading/accounts/{self._acct}/orders",
                headers=self._hdrs,
                json={
                    "symbol":        symbol,
                    "qty":           str(qty),
                    "side":          "buy" if side == "long" else "sell",
                    "type":          "market",
                    "time_in_force": "day",
                },
                timeout=10)
            if r.status_code in (200, 201):
                oid = r.json().get("id", "")
                log(f"Alpaca ORDER  {symbol} BUY {qty} @ ~${price:.2f}  id={oid[:8]}")
                return oid
            else:
                log(f"Alpaca order rejected {symbol}: {r.status_code} {r.text[:120]}")
                return None
        except Exception as e:
            log(f"Alpaca order error {symbol}: {e}")
            return None

    def close_position(self, symbol: str) -> bool:
        if _ALPACA_SKIP(symbol):
            return False
        try:
            r = requests.delete(
                f"{self._base}/v1/trading/accounts/{self._acct}/positions/{symbol}",
                headers=self._hdrs, timeout=10)
            if r.status_code in (200, 201, 204):
                log(f"Alpaca CLOSE  {symbol}")
                return True
            elif r.status_code == 404:
                return True
            else:
                log(f"Alpaca close error {symbol}: {r.status_code} {r.text[:100]}")
                return False
        except Exception as e:
            log(f"Alpaca close error {symbol}: {e}")
            return False

    def positions(self) -> dict:
        try:
            r = requests.get(
                f"{self._base}/v1/trading/accounts/{self._acct}/positions",
                headers=self._hdrs, timeout=8)
            return {p["symbol"]: p for p in r.json()} if r.ok else {}
        except Exception:
            return {}


class TradierBroker:

    def __init__(self):
        self._ready = bool(TRADIER_TOKEN and TRADIER_ACCT)
        if self._ready:
            self._hdrs = {
                "Authorization": f"Bearer {TRADIER_TOKEN}",
                "Accept":        "application/json",
            }
            self._check_ready()
        else:
            log("Tradier: no credentials yet")

    def _check_ready(self):
        try:
            d = self._balances()
            bp = float(d.get("total_buying_power", 0) or d.get("buying_power", 0))
            self._ready = bp > 0
            if self._ready:
                log(f"Tradier ready  equity=${float(d.get('total_equity',0)):,.0f}")
        except Exception as e:
            log(f"Tradier connectivity check failed: {e}")
            self._ready = False

    def _balances(self) -> dict:
        r = requests.get(f"{TRADIER_BASE}/accounts/{TRADIER_ACCT}/balances",
                         headers=self._hdrs, timeout=8)
        return r.json().get("balances", {}) if r.ok else {}

    def account(self) -> dict:
        try:
            return self._balances()
        except Exception:
            return {}

    def place_order(self, symbol: str, side: str, price: float) -> str | None:
        if not self._ready or _ALPACA_SKIP(symbol):
            return None
        try:
            bal    = self._balances()
            bp     = float(bal.get("total_buying_power", 0) or bal.get("buying_power", 0))
            if bp <= 0:
                return None
            budget = min(bp * MAX_POS_PCT, bp * 0.95)
            qty    = int(budget / price) if price > 0 else 0
            if qty < 1:
                return None
            r = requests.post(
                f"{TRADIER_BASE}/accounts/{TRADIER_ACCT}/orders",
                headers=self._hdrs,
                data={
                    "class":    "equity",
                    "symbol":   symbol,
                    "side":     "buy" if side == "long" else "sell_short",
                    "quantity": str(qty),
                    "type":     "market",
                    "duration": "day",
                },
                timeout=10)
            if r.ok:
                oid = str(r.json().get("order", {}).get("id", ""))
                log(f"Tradier ORDER  {symbol} {'BUY' if side=='long' else 'SELL'} {qty} @ ~${price:.2f}")
                return oid
            else:
                log(f"Tradier order rejected {symbol}: {r.status_code} {r.text[:120]}")
                return None
        except Exception as e:
            log(f"Tradier order error {symbol}: {e}")
            return None

    def close_position(self, symbol: str) -> bool:
        if not self._ready or _ALPACA_SKIP(symbol):
            return False
        try:
            r = requests.get(f"{TRADIER_BASE}/accounts/{TRADIER_ACCT}/positions",
                             headers=self._hdrs, timeout=8)
            positions = r.json().get("positions", {}).get("position", [])
            if isinstance(positions, dict):
                positions = [positions]
            pos = next((p for p in positions if p.get("symbol") == symbol), None)
            if not pos:
                return True
            qty        = abs(int(pos.get("quantity", 0)))
            long       = int(pos.get("quantity", 0)) > 0
            close_side = "sell" if long else "buy_to_cover"
            r2 = requests.post(
                f"{TRADIER_BASE}/accounts/{TRADIER_ACCT}/orders",
                headers=self._hdrs,
                data={
                    "class":    "equity",
                    "symbol":   symbol,
                    "side":     close_side,
                    "quantity": str(qty),
                    "type":     "market",
                    "duration": "day",
                },
                timeout=10)
            if r2.ok:
                log(f"Tradier CLOSE  {symbol}")
                return True
            else:
                log(f"Tradier close error {symbol}: {r2.status_code} {r2.text[:100]}")
                return False
        except Exception as e:
            log(f"Tradier close error {symbol}: {e}")
            return False


# ── IBKR broker wrapper (ib_insync) ──────────────────────────────────────────
# Trades equities and ETFs only; skips crypto (-USD) and forex (=X) because
# IBKR has no spot crypto and the PoC universe uses yfinance symbols for FX.
# All methods return empty/None/False when IBKR_LIVE_ENABLED is False.

_IBKR_SKIP = lambda s: s.endswith("=X") or s.endswith("-USD")

try:
    import nest_asyncio as _nest_asyncio
    _nest_asyncio.apply()
    import logging as _ib_log
    _ib_log.getLogger("ib_insync").setLevel(_ib_log.CRITICAL)
    from ib_insync import IB as _IB, Stock as _Stock, MarketOrder as _MktOrder
    _IB_OK = True
except ImportError:
    _IB_OK = False


class IBKRBroker:

    def __init__(self):
        import threading
        self._enabled = IBKR_LIVE_ENABLED
        self._ib      = None
        self._lock    = threading.Lock()
        self._ready   = False
        if self._enabled:
            self._check_ready()
        else:
            log("IBKR broker: DISABLED (IBKR_LIVE_ENABLED=False)")

    # ── internal helpers ──────────────────────────────────────────────────────

    def _connect(self):
        if not _IB_OK:
            raise RuntimeError("ib_insync not installed — pip install ib_insync")
        with self._lock:
            try:
                if self._ib is not None and self._ib.isConnected():
                    return self._ib
            except Exception:
                pass
            ib = _IB()
            ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID,
                       readonly=False, timeout=8)
            ib.sleep(1)
            self._ib = ib
            return self._ib

    def _safe(self, fn, default):
        import time as _t
        for attempt in range(2):
            try:
                return fn(self._connect())
            except Exception as e:
                log(f"IBKR attempt {attempt + 1} failed: {e}")
                with self._lock:
                    self._ib = None
                _t.sleep(1)
        self._ready = False
        return default

    def _acct_id(self, ib) -> str:
        return IBKR_ACCOUNT or (ib.managedAccounts()[0] if ib.managedAccounts() else "")

    def _check_ready(self):
        if not self._enabled:
            return
        try:
            def _fn(ib):
                summary = {s.tag: s.value for s in ib.accountSummary(self._acct_id(ib))}
                return float(summary.get("NetLiquidation", 0))
            nlv = self._safe(_fn, 0.0)
            self._ready = nlv > 0
            if self._ready:
                log(f"IBKR ready  NLV=${nlv:,.0f}  port={IBKR_PORT}")
            else:
                log("IBKR: connected but NetLiquidation=0 — check account")
        except Exception as e:
            log(f"IBKR connectivity check failed: {e}")
            self._ready = False

    # ── public interface (mirrors AlpacaBroker) ───────────────────────────────

    def account(self) -> dict:
        if not self._enabled or not self._ready:
            return {}
        def _fn(ib):
            summary = {s.tag: s.value for s in ib.accountSummary(self._acct_id(ib))}
            nlv  = float(summary.get("NetLiquidation",  0))
            cash = float(summary.get("TotalCashValue",  0))
            return {"equity": nlv, "cash": cash, "buying_power": cash}
        return self._safe(_fn, {})

    def place_order(self, symbol: str, side: str, price: float) -> str | None:
        if not self._enabled or not self._ready:
            return None
        if _IBKR_SKIP(symbol) or side == "short":
            return None
        try:
            bp = float(self.account().get("buying_power", 0))
            if bp <= 0:
                log(f"IBKR skip {symbol}: no buying power")
                return None
            budget = min(bp * MAX_POS_PCT, bp * 0.95)
            qty    = round(budget / price, 4) if price > 0 else 0
            if qty < 0.001:
                return None
            def _fn(ib):
                contract = _Stock(symbol, "SMART", "USD")
                ib.qualifyContracts(contract)
                order     = _MktOrder("BUY", qty)
                order.tif = "DAY"
                trade     = ib.placeOrder(contract, order)
                ib.sleep(0.5)
                oid = str(trade.order.orderId)
                log(f"IBKR ORDER  {symbol} BUY {qty:.4f} @ ~${price:.2f}  id={oid}")
                return oid
            return self._safe(_fn, None)
        except Exception as e:
            log(f"IBKR order error {symbol}: {e}")
            return None

    def close_position(self, symbol: str) -> bool:
        if not self._enabled or not self._ready:
            return False
        if _IBKR_SKIP(symbol):
            return False
        try:
            def _fn(ib):
                items = [p for p in ib.portfolio(self._acct_id(ib))
                         if p.contract.symbol == symbol and p.position != 0]
                if not items:
                    return True
                item  = items[0]
                qty   = abs(float(item.position))
                side  = "SELL" if item.position > 0 else "BUY"
                order     = _MktOrder(side, qty)
                order.tif = "DAY"
                ib.placeOrder(item.contract, order)
                ib.sleep(0.5)
                log(f"IBKR CLOSE  {symbol} {side} {qty:.4f}")
                return True
            return self._safe(_fn, False)
        except Exception as e:
            log(f"IBKR close error {symbol}: {e}")
            return False

    def positions(self) -> dict:
        if not self._enabled or not self._ready:
            return {}
        def _fn(ib):
            return {
                p.contract.symbol: {
                    "symbol":          p.contract.symbol,
                    "qty":             abs(float(p.position)),
                    "side":            "long" if p.position > 0 else "short",
                    "avg_entry_price": float(p.averageCost),
                }
                for p in ib.portfolio(self._acct_id(ib)) if p.position != 0
            }
        return self._safe(_fn, {})


# ── Indicators ────────────────────────────────────────────────────────────────

def add_indicators(raw: pd.DataFrame) -> pd.DataFrame:
    df   = raw.copy()
    df["ema"] = df["Close"].ewm(span=25, adjust=False).mean()
    hl   = df["High"] - df["Low"]
    hcp  = (df["High"] - df["Close"].shift(1)).abs()
    lcp  = (df["Low"]  - df["Close"].shift(1)).abs()
    tr   = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=14, adjust=False).mean()
    up   = df["High"] - df["High"].shift(1)
    dn   = df["Low"].shift(1) - df["Low"]
    pdm  = np.where((up > dn) & (up > 0), up, 0.0)
    ndm  = np.where((dn > up) & (dn > 0), dn, 0.0)
    aw   = tr.ewm(span=20, adjust=False).mean()
    pdi  = 100 * pd.Series(pdm, index=df.index).ewm(span=20, adjust=False).mean() / aw
    ndi  = 100 * pd.Series(ndm, index=df.index).ewm(span=20, adjust=False).mean() / aw
    dx   = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    df["adx"] = dx.ewm(span=20, adjust=False).mean()
    df["pdi"] = pdi
    df["ndi"] = ndi
    # RSI(14)
    delta     = df["Close"].diff()
    gain      = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    loss      = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
    rs        = gain / loss.replace(0, 1e-10)
    df["rsi"] = 100 - 100 / (1 + rs)
    # 3-bar momentum (% change over last 3 bars)
    df["mom3"] = df["Close"].pct_change(3) * 100
    # VWAP (resets daily — strong intraday reference)
    try:
        df["_d"]   = df.index.date
        df["_tp"]  = (df["High"] + df["Low"] + df["Close"]) / 3
        df["_pv"]  = df["_tp"] * df["Volume"]
        df["vwap"] = (df.groupby("_d")["_pv"].cumsum() /
                      df.groupby("_d")["Volume"].cumsum().replace(0, np.nan))
        df = df.drop(columns=["_d", "_tp", "_pv"])
    except Exception:
        df["vwap"] = df["Close"]
    return df


def regime(adx, pdi, ndi):
    if adx <= ADX_THRESH:
        return "ranging"
    return "bull" if pdi >= ndi else "bear"


_spy_bull_cache: tuple[date, bool] | None = None
_corr_price_cache: dict[tuple, pd.Series | None] = {}

def spy_is_bullish() -> bool:
    global _spy_bull_cache
    today = date.today()
    if _spy_bull_cache and _spy_bull_cache[0] == today:
        return _spy_bull_cache[1]
    try:
        df = yf.Ticker("SPY").history(period="90d", interval="1d", auto_adjust=True)
        if df.empty or len(df) < 50:
            return False
        ema50  = float(df["Close"].ewm(span=50, adjust=False).mean().iloc[-1])
        result = float(df["Close"].iloc[-1]) > ema50
    except Exception:
        result = False
    _spy_bull_cache = (today, result)
    return result


def _corr_closes(sym: str) -> "pd.Series | None":
    """Last 20 daily closes for sym; same-day cache to keep correlation checks fast."""
    key = (sym, date.today())
    if key in _corr_price_cache:
        return _corr_price_cache[key]
    try:
        df = yf.download([sym], period="1mo", progress=False, auto_adjust=True)
        if df.empty or len(df) < 5:
            _corr_price_cache[key] = None
            return None
        closes = df["Close"].squeeze().dropna().iloc[-20:]
        _corr_price_cache[key] = closes
        return closes
    except Exception:
        _corr_price_cache[key] = None
        return None


def get_market_regime() -> str:
    """Fetch SPY 5-min bars and return intraday regime: BULL, BEAR, or NEUTRAL."""
    df = get_bars("SPY", period="5d")
    if df is None or df.empty:
        return "NEUTRAL"
    last = df.iloc[-1]
    reg  = regime(float(last["adx"]), float(last["pdi"]), float(last["ndi"]))
    return {"bull": "BULL", "bear": "BEAR", "ranging": "NEUTRAL"}.get(reg, "NEUTRAL")


def get_bars(sym: str, period: str = "10d", prepost: bool = False) -> pd.DataFrame | None:
    try:
        df = yf.Ticker(sym).history(period=period, interval="5m",
                                     auto_adjust=True, prepost=prepost)
        if df.empty or len(df) < 20:
            return None
        df.index = df.index.tz_convert(ET)
        return add_indicators(df)
    except Exception:
        return None


def get_daily_bars(sym: str, period: str = "60d") -> pd.DataFrame | None:
    try:
        df = yf.Ticker(sym).history(period=period, interval="1d", auto_adjust=True)
        if df.empty or len(df) < 10:
            return None
        return add_indicators(df)
    except Exception:
        return None


# ── Portfolio ─────────────────────────────────────────────────────────────────

class Portfolio:

    def __init__(self):
        self._load()

    def _load(self):
        if STATE_FILE.exists():
            d = json.loads(STATE_FILE.read_text())
        else:
            d = {}
        self.cash      = d.get("cash",      INITIAL_CASH)
        self.positions = d.get("positions", {})
        self.closed    = d.get("closed",    [])
        self.total_pnl = d.get("total_pnl", 0.0)
        self.n_trades  = d.get("n_trades",  0)

    def save(self, alpaca_equity: float = 0.0):
        STATE_FILE.write_text(json.dumps({
            "cash":          self.cash,
            "positions":     self.positions,
            "closed":        self.closed[-200:],
            "total_pnl":     round(self.total_pnl, 2),
            "n_trades":      self.n_trades,
            "equity":        round(self.equity, 2),
            "alpaca_equity": round(alpaca_equity, 2),
            "updated":       str(datetime.now(ET)),
        }, indent=2, default=str))

    @property
    def equity(self) -> float:
        pos_value = sum(
            p.get("last_price", p["entry_price"]) * p["shares"]
            for p in self.positions.values()
        )
        return self.cash + pos_value

    def can_enter(self, price: float) -> int:
        if len(self.positions) >= MAX_POSITIONS:
            return 0
        budget = min(self.equity * MAX_POS_PCT, self.cash * 0.95)
        return max(int(budget / price), 0)

    def open(self, sym: str, side: str, price: float, atr: float, strategy: str) -> int:
        shares = self.can_enter(price)
        if shares < 1:
            return 0
        cost = shares * price + shares * COMM
        if cost > self.cash:
            shares = max(int((self.cash * 0.95) / (price + COMM)), 0)
        if shares < 1:
            return 0

        # Guard against NaN/zero ATR — fall back to 1% of price
        if not np.isfinite(atr) or atr <= 0:
            atr = price * 0.01

        # Trail stop: max(N×ATR, MIN_TRAIL_PCT × price) — floor prevents instant stopouts
        min_dist = price * MIN_TRAIL_PCT
        if side == "long":
            trail = round(price - max(TRAIL_MULT * atr, min_dist), 2)
        else:
            trail = round(price + max(TRAIL_MULT_SHORT * atr, min_dist), 2)

        self.cash -= shares * price + shares * COMM
        self.positions[sym] = {
            "side":        side,
            "entry_price": round(price, 4),
            "shares":      shares,
            "trail_stop":  trail,
            "atr":         round(atr, 4),
            "entry_time":  str(datetime.now(ET)),
            "strategy":    strategy,
            "unrealized":  0.0,
        }
        return shares

    def update(self, sym: str, price: float, atr: float):
        if sym not in self.positions:
            return
        pos  = self.positions[sym]
        side = pos["side"]
        entry = pos["entry_price"]
        if not np.isfinite(atr) or atr <= 0:
            atr = price * 0.01
        min_dist = price * MIN_TRAIL_PCT
        if side == "long":
            ns = price - max(TRAIL_MULT * atr, min_dist)
            if ns > pos["trail_stop"]:
                pos["trail_stop"] = round(ns, 4)
            # Breakeven mechanic: once 1×ATR in profit, stop floor = entry + 0.3×ATR
            if not pos.get("be_set") and price >= entry + 1.0 * atr:
                be_floor = entry + 0.3 * atr
                if be_floor > pos["trail_stop"]:
                    pos["trail_stop"] = round(be_floor, 4)
                    pos["be_set"] = True
            pos["unrealized"] = round((price - entry) * pos["shares"], 2)
        else:
            ns = price + max(TRAIL_MULT_SHORT * atr, min_dist)
            if ns < pos["trail_stop"]:
                pos["trail_stop"] = round(ns, 4)
            if not pos.get("be_set") and price <= entry - 1.0 * atr:
                be_floor = entry - 0.3 * atr
                if be_floor < pos["trail_stop"]:
                    pos["trail_stop"] = round(be_floor, 4)
                    pos["be_set"] = True
            pos["unrealized"] = round((entry - price) * pos["shares"], 2)
        pos["last_price"] = round(price, 4)
        pos["last_atr"]   = round(atr, 4)

    def close(self, sym: str, price: float, reason: str) -> float:
        if sym not in self.positions:
            return 0.0
        pos    = self.positions.pop(sym)
        side   = pos["side"]
        shares = pos["shares"]
        gross  = (price - pos["entry_price"]) * shares if side == "long" \
             else (pos["entry_price"] - price) * shares
        pnl    = gross - shares * COMM * 2
        self.cash      += shares * price - shares * COMM
        self.total_pnl += pnl
        self.n_trades  += 1
        self.closed.append({
            "symbol":     sym,
            "side":       side,
            "entry":      pos["entry_price"],
            "exit":       round(price, 4),
            "shares":     shares,
            "pnl":        round(pnl, 2),
            "reason":     reason,
            "strategy":   pos["strategy"],
            "entry_time": pos["entry_time"],
            "exit_time":  str(datetime.now(ET)),
            "entry_adx":  pos.get("entry_adx", 0),
            "entry_rv":   pos.get("entry_rv", 0),
        })
        return round(pnl, 2)


# ── Strategy stats (adaptive learning) ───────────────────────────────────────

def load_stats() -> dict:
    if STATS_FILE.exists():
        try:
            return json.loads(STATS_FILE.read_text())
        except Exception:
            pass
    return {
        "total_trades": 0,
        "total_wins": 0,
        "by_adx_bucket": {},
        "by_rv_bucket": {},
        "by_time_bucket": {},
        "suggested_adx_min": ADX_THRESH,
        "suggested_rv_min": 1.5,
        "last_updated": str(date.today()),
    }


def update_stats(trade: dict):
    stats = load_stats()
    won   = trade["pnl"] > 0
    stats["total_trades"] = stats.get("total_trades", 0) + 1
    stats["total_wins"]   = stats.get("total_wins", 0) + int(won)

    def bucket_update(key: str, bucket: str):
        b = stats.setdefault(key, {})
        b.setdefault(bucket, {"trades": 0, "wins": 0})
        b[bucket]["trades"] += 1
        b[bucket]["wins"]   += int(won)

    adx = trade.get("entry_adx", 0)
    if adx < 30:     bucket_update("by_adx_bucket", "20-30")
    elif adx < 40:   bucket_update("by_adx_bucket", "30-40")
    elif adx < 50:   bucket_update("by_adx_bucket", "40-50")
    else:            bucket_update("by_adx_bucket", "50+")

    rv = trade.get("entry_rv", 0)
    if rv < 2:       bucket_update("by_rv_bucket", "1.0-2.0")
    elif rv < 4:     bucket_update("by_rv_bucket", "2.0-4.0")
    else:            bucket_update("by_rv_bucket", "4.0+")

    try:
        et_str = trade.get("entry_time", "")
        et     = datetime.fromisoformat(et_str)
        h, m   = et.hour, et.minute
        mins   = h * 60 + m
        if mins < 10 * 60:         bucket_update("by_time_bucket", "0935-1000")
        elif mins < 11 * 60:       bucket_update("by_time_bucket", "1000-1100")
        elif mins < 13 * 60:       bucket_update("by_time_bucket", "1100-1300")
        else:                      bucket_update("by_time_bucket", "1300+")
    except Exception:
        pass

    # Adapt thresholds when we have enough data
    total = stats["total_trades"]
    if total >= 20:
        wr = stats["total_wins"] / total

        # Find best-performing ADX bucket
        best_adx_wr, best_adx_min = 0, ADX_THRESH
        for bkt, v in stats.get("by_adx_bucket", {}).items():
            if v["trades"] >= 5:
                bkt_wr = v["wins"] / v["trades"]
                if bkt_wr > best_adx_wr:
                    best_adx_wr = bkt_wr
                    lo = int(bkt.split("-")[0]) if "-" in bkt else int(bkt.replace("+", ""))
                    best_adx_min = lo

        # Find best-performing rv bucket
        best_rv_wr, best_rv_min = 0, 1.5
        for bkt, v in stats.get("by_rv_bucket", {}).items():
            if v["trades"] >= 5:
                bkt_wr = v["wins"] / v["trades"]
                if bkt_wr > best_rv_wr:
                    best_rv_wr = bkt_wr
                    best_rv_min = float(bkt.split("-")[0].replace("+", ""))

        stats["suggested_adx_min"] = max(best_adx_min, ADX_THRESH)
        stats["suggested_rv_min"]  = max(best_rv_min, 1.0)
        stats["overall_win_rate"]  = round(wr, 3)

    stats["last_updated"] = str(date.today())
    STATS_FILE.write_text(json.dumps(stats, indent=2))


def get_adaptive_params() -> tuple[float, float]:
    """Return (adx_min, rv_min) from learned stats, or defaults."""
    stats = load_stats()
    if stats.get("total_trades", 0) >= 20:
        return (
            float(stats.get("suggested_adx_min", ADX_THRESH)),
            float(stats.get("suggested_rv_min", 1.5)),
        )
    return (float(ADX_THRESH), 1.5)


# ── MockTrader ────────────────────────────────────────────────────────────────

class MockTrader:

    DAILY_PROFIT_TARGET_PCT = 0.015   # tuned 2026-06-15: 0.025→0.015 (lock profits sooner)
    DAILY_LOSS_LIMIT_PCT    = -0.005  # tuned 2026-06-15: -0.012→-0.005 (cut bad days fast)

    def __init__(self):
        self.portfolio          = Portfolio()
        self.broker             = AlpacaBroker()
        self.tradier            = TradierBroker()
        self.ibkr               = IBKRBroker()
        self._running           = False
        self._scan_candidates   = []
        self._last_scan_min     = -999
        self._last_cycle_day    = None
        self._cooldown          = {}
        self._last_rebalance_t  = None   # datetime of last rebalance
        self._premarket_done    = False  # per-day flags
        self._market_open_done  = False
        self._eod_done          = False
        self._review_done       = False
        self._day_halted        = False  # halted after profit target or loss limit
        self._profit_target_hit = False
        self._loss_limit_hit    = False
        self._adx_min, self._rv_min = get_adaptive_params()
        self._current_regime      = "BULL"
        self._last_regime_check   = 0.0
        self._regime_no_new_longs = False
        self._regime_changes: list = []

    def _place_all(self, symbol: str, side: str, price: float):
        self.broker.place_order(symbol, side, price)
        self.tradier.place_order(symbol, side, price)
        self.ibkr.place_order(symbol, side, price)

    def _close_all(self, symbol: str):
        self.broker.close_position(symbol)
        self.tradier.close_position(symbol)
        self.ibkr.close_position(symbol)

    def _corr_ok(self, sym: str) -> bool:
        """Return False (and log) if sym is too correlated with any open position."""
        if len(self.portfolio.positions) < 2:
            return True
        new_closes = _corr_closes(sym)
        if new_closes is None:
            return True
        max_corr, most_corr_sym = 0.0, ""
        for pos_sym in self.portfolio.positions:
            pos_closes = _corr_closes(pos_sym)
            if pos_closes is None:
                continue
            aligned = pd.concat([new_closes, pos_closes], axis=1).dropna()
            if len(aligned) < 10:
                continue
            corr = float(aligned.iloc[:, 0].corr(aligned.iloc[:, 1]))
            if corr > max_corr:
                max_corr, most_corr_sym = corr, pos_sym
        if max_corr > CORRELATION_LIMIT:
            log(f"Skip {sym}: corr {max_corr:.2f} with {most_corr_sym}")
            return False
        return True

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        self._running = True
        self.portfolio.save()
        acct      = self.broker.account()
        alpaca_eq = float(acct.get("equity", 0))
        log("=" * 70)
        log(f"Bot started (redesigned)  |  equity=${self.portfolio.equity:,.0f}"
            + (f"  |  Alpaca=${alpaca_eq:,.0f}" if alpaca_eq else "  |  Alpaca pending"))
        log(f"Positions: {len(self.portfolio.positions)}  |  Cash: ${self.portfolio.cash:,.0f}")
        log(f"Adaptive params: ADX≥{self._adx_min:.0f}  RV≥{self._rv_min:.1f}×")
        log("=" * 70)

        while self._running:
            now  = datetime.now(ET)
            mins = now.hour * 60 + now.minute

            # Reset per-day flags at midnight
            if now.date() != self._last_cycle_day:
                self._scan_candidates  = []
                self._last_scan_min    = -999
                self._last_cycle_day   = now.date()
                self._premarket_done   = False
                self._market_open_done = False
                self._eod_done         = False
                self._review_done      = False
                self._day_halted       = False
                self._profit_target_hit = False
                self._loss_limit_hit   = False
                self._adx_min, self._rv_min = get_adaptive_params()
                self._current_regime      = "BULL"
                self._last_regime_check   = 0.0
                self._regime_no_new_longs = False
                self._regime_changes      = []
                self._last_rebalance_t    = None   # reset so rebalance isn't blocked at open
                log(f"New day {now.date()}  adaptive: ADX≥{self._adx_min:.0f}  RV≥{self._rv_min:.1f}×")

            in_rth = (9 * 60 + 30) <= mins <= (16 * 60)
            in_ext = (4 * 60)      <= mins <= (20 * 60)

            # ── Intraday regime refresh (every 30 min during RTH only) ────────
            if in_rth and (time.time() - self._last_regime_check) > 30 * 60:
                new_regime = get_market_regime()
                if new_regime != self._current_regime:
                    log(f"Regime changed: {self._current_regime} → {new_regime}")
                    self._regime_changes.append({
                        "time": now.strftime("%H:%M"),
                        "from": self._current_regime,
                        "to":   new_regime,
                    })
                    self._current_regime = new_regime
                    if new_regime in ("BEAR", "NEUTRAL"):
                        log("Regime flip to BEAR/NEUTRAL — closing all long positions, blocking new longs")
                        for sym in list(self.portfolio.positions.keys()):
                            pos = self.portfolio.positions.get(sym)
                            if pos and pos["side"] == "long":
                                df_r  = get_bars(sym)
                                price = float(df_r.iloc[-1]["Close"]) if df_r is not None \
                                        else float(pos.get("last_price", pos["entry_price"]))
                                pnl   = self.portfolio.close(sym, price, "regime_flip_refresh")
                                log(f"  CLOSE {sym:<6}  pnl={pnl:>+,.0f}  [regime_flip_refresh]"
                                    f"  equity=${self.portfolio.equity:,.0f}")
                                self.portfolio.save()
                                self._close_all(sym)
                        self._regime_no_new_longs = True
                    elif new_regime == "BULL":
                        log("Regime flip to BULL — allowing new long entries")
                        self._regime_no_new_longs = False
                self._last_regime_check = time.time()

            # ── Timed events ──────────────────────────────────────────────────

            # 08:30 — pre-market scan (also catches restarts between 8:30–9:29)
            is_pm_window = (4 * 60) <= mins < (9 * 60 + 30)
            if is_pm_window and not self._premarket_done:
                try:
                    picks_fresh = (
                        PREMARKET_FILE.exists() and
                        json.loads(PREMARKET_FILE.read_text()).get("date") == str(now.date())
                    )
                except Exception:
                    picks_fresh = False
                if picks_fresh:
                    # Today's scan already written (e.g. bot restarted after 8:30)
                    self._premarket_done = True
                elif mins >= PREMARKET_HOUR * 60 + PREMARKET_MIN:
                    # Past 08:30 and no fresh file — run now
                    self._premarket_scan()
                    self._premarket_done = True
                # else: before 08:30, leave _premarket_done=False so we retry next tick

            # 09:35 — market-open entries from pre-market picks
            if in_rth and mins >= MARKET_OPEN_MIN and not self._market_open_done:
                self._enter_premarket_picks()
                self._market_open_done = True

            # 15:45 — close all intraday positions
            if in_rth and mins >= EOD_INTRADAY and not self._eod_done:
                self._close_all_intraday(now)
                self._eod_done = True

            # 16:05 — daily review
            if mins >= REVIEW_HOUR * 60 + REVIEW_MIN and not self._review_done:
                self._daily_review(now)
                self._review_done = True

            # ── Scan ──────────────────────────────────────────────────────────
            scan_ivl = 15 if in_rth else 30 if in_ext else 60
            if (mins - self._last_scan_min) >= scan_ivl:
                self._scan(in_rth, in_ext)

            # ── Trading cycle ─────────────────────────────────────────────────
            self._trading_cycle(now, mins, in_rth, in_ext)

            # ── Daily P&L guard (profit target / loss limit) ──────────────────
            if in_rth and not self._day_halted:
                today_str_g      = str(now.date())
                closed_today_pnl = sum(
                    t["pnl"] for t in self.portfolio.closed
                    if today_str_g in t.get("exit_time", "")
                )
                unrealized_pnl   = sum(
                    p.get("unrealized", 0) for p in self.portfolio.positions.values()
                )
                total_day_pnl   = closed_today_pnl + unrealized_pnl
                account_equity  = self.portfolio.equity
                if account_equity > 0:
                    day_pct = total_day_pnl / account_equity
                    if day_pct >= self.DAILY_PROFIT_TARGET_PCT:
                        log(f"Daily profit target hit: {day_pct:.1%} — closing all positions")
                        self._close_all_intraday(now)
                        self._day_halted        = True
                        self._profit_target_hit = True
                        self._eod_done          = True
                    elif day_pct <= self.DAILY_LOSS_LIMIT_PCT:
                        log(f"Daily loss limit hit: {day_pct:.1%} — closing all positions")
                        self._close_all_intraday(now)
                        self._day_halted     = True
                        self._loss_limit_hit = True
                        self._eod_done       = True

            # ── Rebalance (with guards) ────────────────────────────────────────
            if (len(self.portfolio.positions) >= MAX_POSITIONS
                    and self._scan_candidates and not self._day_halted):
                self._rebalance(now)

            # ── Dashboard trigger ─────────────────────────────────────────────
            self._check_trigger(in_rth, in_ext)

            # ── Persist Alpaca equity every ~2 min ────────────────────────────
            if int(time.time()) % 120 < 10:
                acct_data = self.broker.account()
                alpaca_eq = float(acct_data.get("equity", 0))
                if alpaca_eq > 0 and not self.broker._ready:
                    self.broker._ready = True
                    log(f"Alpaca cash settled — equity=${alpaca_eq:,.0f}")
                self.portfolio.save(alpaca_equity=alpaca_eq)

            time.sleep(10)

    def stop(self):
        self._running = False
        self.portfolio.save()
        log("Stopped.")

    # ── SPY gap detection ─────────────────────────────────────────────────────

    def _fetch_spy_gap(self) -> tuple[float, str]:
        """Fetch SPY overnight gap vs previous close. Returns (gap_pct, mode). Falls back to GAP_FLAT."""
        try:
            hist = yf.download("SPY", period="2d", progress=False, auto_adjust=True)
            if len(hist) < 2:
                raise ValueError("insufficient SPY history")
            prev_close = float(hist["Close"].iloc[-2])
        except Exception as e:
            log(f"SPY gap: prev_close fetch failed: {e} — defaulting to GAP_FLAT")
            return 0.0, "GAP_FLAT"

        premarket_price = None
        try:
            now_et   = datetime.now(ET)
            start_dt = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
            creds    = base64.b64encode(f"{ALPACA_KEY}:{ALPACA_SECRET}".encode()).decode()
            hdrs     = {"Authorization": f"Basic {creds}"}
            for base_url in [
                "https://data.sandbox.alpaca.markets/v2",
                "https://data.alpaca.markets/v2",
            ]:
                try:
                    r = requests.get(
                        f"{base_url}/stocks/SPY/bars",
                        headers=hdrs,
                        params={
                            "timeframe": "1Min",
                            "start":     start_dt.isoformat(),
                            "feed":      "iex",
                            "limit":     200,
                            "sort":      "asc",
                        },
                        timeout=8,
                    )
                    if r.ok:
                        bars = r.json().get("bars", [])
                        if bars:
                            premarket_price = float(bars[-1]["c"])
                            break
                except Exception:
                    continue
        except Exception as e:
            log(f"SPY gap: Alpaca fetch error: {e}")

        if premarket_price is None:
            log("SPY gap: Alpaca pre-market price unavailable — defaulting to GAP_FLAT")
            return 0.0, "GAP_FLAT"

        gap_pct = (premarket_price - prev_close) / prev_close * 100

        if gap_pct > 3.0:
            mode = "GAP_SURGE"
        elif gap_pct > 1.0:
            mode = "GAP_UP"
        elif gap_pct >= -1.0:
            mode = "GAP_FLAT"
        elif gap_pct >= -3.0:
            mode = "GAP_DOWN"
        else:
            mode = "GAP_CRASH"

        log(f"SPY gap: {gap_pct:+.2f}%  prev_close={prev_close:.2f}"
            f"  premarket={premarket_price:.2f}  mode={mode}")
        return gap_pct, mode

    # ── Pre-market scan ───────────────────────────────────────────────────────

    def _premarket_scan(self):
        spy_gap_pct, spy_gap_mode = self._fetch_spy_gap()

        # Adaptive thresholds based on SPY overnight gap
        if spy_gap_mode == "GAP_SURGE":
            pm_score_thresh, gap_max_pos = 38, 8
        elif spy_gap_mode == "GAP_UP":
            pm_score_thresh, gap_max_pos = PM_MIN_SCORE, MAX_POSITIONS
        elif spy_gap_mode == "GAP_FLAT":
            pm_score_thresh, gap_max_pos = 42, MAX_POSITIONS
        elif spy_gap_mode == "GAP_DOWN":
            pm_score_thresh, gap_max_pos = 48, 3
        else:  # GAP_CRASH
            log(f"RISK OFF: SPY gap {spy_gap_pct:.1f}% — skipping all pre-market entries")
            PREMARKET_FILE.write_text(json.dumps({
                "date":      str(date.today()),
                "generated": str(datetime.now(ET)),
                "picks":     [],
                "spy_gap": {
                    "pct":           round(spy_gap_pct, 2),
                    "mode":          spy_gap_mode,
                    "max_positions": 0,
                },
            }, indent=2))
            return

        log(f"PRE-MARKET SCAN starting ({datetime.now(ET).strftime('%H:%M')} ET)  "
            f"scoring {len(SCANNER_UNIVERSE)} symbols …  "
            f"SPY gap={spy_gap_pct:+.1f}% [{spy_gap_mode}]")
        results = []

        def score_pm(sym):
            try:
                # Daily bars for ADX and previous close
                dd = get_daily_bars(sym, period="30d")
                if dd is None or len(dd) < 10:
                    return None
                prev_close = float(dd["Close"].iloc[-2])
                adx_d      = float(dd["adx"].iloc[-1])
                atr_d      = float(dd["atr"].iloc[-1])

                # 5-min bars with pre-market (prepost=True) for latest price + volume
                df = get_bars(sym, period="2d", prepost=True)
                if df is None:
                    return None

                today = date.today()
                pm_bars = df[
                    (df.index.date == today) &
                    ((df.index.hour < 9) |
                     ((df.index.hour == 9) & (df.index.minute < 30)))
                ]

                latest    = df.iloc[-1]
                cur_price = float(latest["Close"])
                if cur_price <= 0:
                    return None

                # Gap from previous close (use latest price as proxy for pre-market price)
                gap_pct = (cur_price - prev_close) / prev_close * 100

                # Pre-market volume (0 if no pre-mkt bars — stock didn't trade pre-market)
                prev_bars = dd.iloc[:-1]
                avg_daily_vol = float(prev_bars["Volume"].mean()) if not prev_bars.empty else 0
                if len(pm_bars) > 0:
                    pm_vol = float(pm_bars["Volume"].sum())
                    pm_vol_ratio = (pm_vol / avg_daily_vol * (390 / max(len(pm_bars) * 5, 5))
                                    ) if avg_daily_vol > 0 else 0
                else:
                    pm_vol_ratio = 0.0   # no pre-market activity — still scored on gap/ADX/ATR

                # ATR as % of price (ensures the symbol moves enough to profit)
                atr_pct = atr_d / cur_price * 100

                # Composite pre-market score
                gap_score    = min(abs(gap_pct) / 5.0,  1.0) * 30   # up to 30 — capped at 5% gap
                pm_vol_score = min(pm_vol_ratio / 5.0,  1.0) * 25   # up to 25
                adx_score    = min(adx_d / 60.0,        1.0) * 25   # up to 25
                atr_score    = min(atr_pct / 3.0,       1.0) * 20   # up to 20 — need ≥3% ATR

                # Penalise very large gaps (gap-and-crap > 10%)
                if abs(gap_pct) > 10:
                    gap_score *= 0.5

                total = gap_score + pm_vol_score + adx_score + atr_score

                # Direction from gap and daily regime
                last_d = dd.iloc[-1]
                reg_d  = regime(float(last_d["adx"]), float(last_d["pdi"]), float(last_d["ndi"]))
                direction = "long" if gap_pct >= 0 else "short"
                # Align direction with daily regime
                if reg_d == "bull":
                    direction = "long"
                elif reg_d == "bear" and not spy_is_bullish():
                    direction = "short"

                return dict(
                    symbol=sym, score=round(total, 1),
                    gap_pct=round(gap_pct, 2), pm_vol_ratio=round(pm_vol_ratio, 1),
                    adx=round(adx_d, 1), atr_pct=round(atr_pct, 2),
                    price=round(cur_price, 2), direction=direction,
                    regime=reg_d,
                )
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=16) as ex:
            futs = {ex.submit(score_pm, s): s for s in SCANNER_UNIVERSE}
            for f in as_completed(futs):
                r = f.result()
                if r and r["score"] >= pm_score_thresh:
                    results.append(r)

        results.sort(key=lambda x: -x["score"])
        top_picks = results[:gap_max_pos]

        PREMARKET_FILE.write_text(json.dumps({
            "date":      str(date.today()),
            "generated": str(datetime.now(ET)),
            "picks":     top_picks,
            "spy_gap": {
                "pct":           round(spy_gap_pct, 2),
                "mode":          spy_gap_mode,
                "max_positions": gap_max_pos,
            },
        }, indent=2))

        log(f"Pre-market scan done: {len(results)} qualified → top {len(top_picks)} saved  [{spy_gap_mode}]")
        for r in top_picks[:10]:
            log(f"  {r['direction'].upper():<5}  {r['symbol']:<6}  "
                f"score={r['score']:.0f}  gap={r['gap_pct']:+.1f}%  "
                f"pm_vol={r['pm_vol_ratio']:.1f}×  ADX={r['adx']:.0f}")

    # ── 09:35 market-open entry ───────────────────────────────────────────────

    def _enter_premarket_picks(self):
        if not PREMARKET_FILE.exists():
            log("09:35 entry: no pre-market picks file — running live scan instead")
            self._scan(in_rth=True, in_ext=False)
            return

        try:
            data  = json.loads(PREMARKET_FILE.read_text())
            picks = data.get("picks", [])
            today = str(date.today())
            if data.get("date") != today:
                log("09:35 entry: pre-market picks are from a previous day, running live scan")
                self._scan(in_rth=True, in_ext=False)
                return
        except Exception:
            log("09:35 entry: could not read pre-market picks file")
            return

        spy_gap  = data.get("spy_gap", {})
        gap_mode = spy_gap.get("mode", "GAP_FLAT")
        gap_maxp = spy_gap.get("max_positions", MAX_POSITIONS)

        if gap_mode == "GAP_CRASH":
            log(f"09:35 entry: RISK OFF (GAP_CRASH {spy_gap.get('pct', 0):+.1f}%) — skipping all entries")
            return

        log(f"09:35 MARKET OPEN — entering from {len(picks)} pre-market picks … [{gap_mode}  max={gap_maxp}]")
        entered = 0
        for pick in picks:
            if entered >= gap_maxp:
                break
            sym  = pick["symbol"]
            if sym in self.portfolio.positions:
                continue
            if len(self.portfolio.positions) >= gap_maxp:
                break

            # Re-confirm signal now that market is open
            df = get_bars(sym, period="5d")
            if df is None:
                continue
            last  = df.iloc[-1]
            close = float(last["Close"])
            atr   = float(last["atr"])
            adx   = float(last["adx"])
            pdi   = float(last["pdi"])
            ndi   = float(last["ndi"])
            ema   = float(last["ema"])
            reg   = regime(adx, pdi, ndi)

            direction = pick["direction"]
            vwap = float(last.get("vwap", close))
            rsi  = float(last.get("rsi", 50))
            if direction == "long":
                if not (close > ema and reg in ("bull", "ranging")):
                    log(f"  {sym}: pre-mkt pick stale at open (close={close:.2f} ema={ema:.2f} reg={reg})")
                    continue
                if rsi > 78:
                    log(f"  {sym}: overbought at open RSI={rsi:.0f}, skip")
                    continue
                # VWAP check: only skip if significantly below VWAP (>0.5%)
                if vwap > 0 and close < vwap * 0.995:
                    log(f"  {sym}: price below VWAP at open ({close:.2f} vs vwap={vwap:.2f}), skip")
                    continue
            if direction == "short":
                if spy_is_bullish():
                    log(f"  {sym}: SPY bullish — skip short")
                    continue
                if not (close < ema and reg == "bear"):
                    log(f"  {sym}: short setup stale at open")
                    continue

            if not self._corr_ok(sym):
                continue
            shares = self.portfolio.open(sym, direction, close, atr, "premarket")
            if shares:
                pos = self.portfolio.positions[sym]
                pos["entry_adx"] = round(adx, 1)
                pos["entry_rv"]  = pick.get("pm_vol_ratio", 0)
                log(f"  ENTRY {sym:<6}  {direction.upper():<5}  {shares} @ {close:.2f}"
                    f"  stop={pos['trail_stop']:.2f}  score={pick['score']:.0f}"
                    f"  gap={pick['gap_pct']:+.1f}%  [{reg}]"
                    f"  equity=${self.portfolio.equity:,.0f}")
                self.portfolio.save()
                self._place_all(sym, direction, close)
                entered += 1

        log(f"09:35 entry complete: {entered} positions opened")

    # ── 15:45 close all intraday ──────────────────────────────────────────────

    def _close_all_intraday(self, now: datetime):
        # Close all locally tracked positions
        if self.portfolio.positions:
            log(f"15:45 EOD — closing all {len(self.portfolio.positions)} intraday positions")
            for sym in list(self.portfolio.positions.keys()):
                try:
                    pos = self.portfolio.positions.get(sym)
                    if pos is None:
                        continue
                    df    = get_bars(sym)
                    price = float(df.iloc[-1]["Close"]) if df is not None \
                            else float(pos.get("last_price", pos["entry_price"]))
                    pnl   = self.portfolio.close(sym, price, "eod_intraday")
                    log(f"  CLOSE {sym:<6}  pnl={pnl:>+,.0f}  equity=${self.portfolio.equity:,.0f}")
                    self.portfolio.save()
                    self._close_all(sym)
                except Exception as e:
                    log(f"  EOD close error {sym}: {e} — forcing close at last known price")
                    try:
                        pos = self.portfolio.positions.get(sym)
                        if pos:
                            fallback = pos.get("last_price", pos["entry_price"])
                            self.portfolio.close(sym, fallback, "eod_forced")
                            self._close_all(sym)
                            self.portfolio.save()
                    except Exception as e2:
                        log(f"  EOD force-close failed {sym}: {e2}")
        # Also wipe any orphaned Alpaca positions (fills from prior sessions)
        try:
            alpaca_positions = self.broker.positions()
            for sym in list(alpaca_positions.keys()):
                self.broker.close_position(sym)
                log(f"  CLOSE Alpaca orphan: {sym}")
        except Exception as e:
            log(f"  Alpaca orphan cleanup error: {e}")

    # ── Daily review ──────────────────────────────────────────────────────────

    def _daily_review(self, now: datetime):
        log(f"DAILY REVIEW starting ({now.strftime('%H:%M')} ET) …")
        today_str = str(now.date())

        # Trades closed today
        today_trades = [
            t for t in self.portfolio.closed
            if today_str in t.get("exit_time", "")
        ]

        # Update strategy stats for each today trade
        for t in today_trades:
            update_stats(t)

        # Scan full universe for today's biggest movers
        log("  Scanning universe for today's top movers …")
        movers = []

        def get_mover(sym):
            try:
                df = get_bars(sym, period="2d")
                if df is None:
                    return None
                today_bars = df[df.index.date == now.date()]
                if len(today_bars) < 2:
                    return None
                open_p  = float(today_bars.iloc[0]["Open"])
                close_p = float(today_bars.iloc[-1]["Close"])
                if open_p <= 0:
                    return None
                day_chg = (close_p - open_p) / open_p * 100
                return dict(symbol=sym, day_chg=round(day_chg, 2),
                            open=round(open_p, 2), close=round(close_p, 2))
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=16) as ex:
            futs = {ex.submit(get_mover, s): s for s in SCANNER_UNIVERSE}
            for f in as_completed(futs):
                r = f.result()
                if r:
                    movers.append(r)

        movers.sort(key=lambda x: -abs(x["day_chg"]))
        top_movers = movers[:20]

        # Find what we missed and why
        traded_today_syms = {t["symbol"] for t in today_trades}
        currently_held    = set(self.portfolio.positions.keys())
        all_held_today    = traded_today_syms | currently_held

        candidates_to_diagnose = [m for m in top_movers if m["symbol"] not in all_held_today]
        missed = []
        if candidates_to_diagnose:
            with ThreadPoolExecutor(max_workers=10) as ex:
                futs = {ex.submit(_diagnose_miss, m["symbol"], m["day_chg"]): m
                        for m in candidates_to_diagnose}
                for f in as_completed(futs):
                    m = futs[f]
                    reason = f.result()
                    missed.append({**m, "miss_reason": reason})
            missed.sort(key=lambda x: -abs(x.get("day_chg", 0)))

        # Performance summary
        wins   = [t for t in today_trades if t["pnl"] > 0]
        losses = [t for t in today_trades if t["pnl"] <= 0]
        total_pnl_today = sum(t["pnl"] for t in today_trades)

        # Suggestions based on today's misses
        suggestions = _generate_suggestions(missed, today_trades)

        review = {
            "date":            today_str,
            "generated":       str(now),
            "regime_changes":  list(self._regime_changes),
            "performance": {
                "trades":      len(today_trades),
                "wins":        len(wins),
                "losses":      len(losses),
                "win_rate":    round(len(wins) / max(len(today_trades), 1), 3),
                "total_pnl":   round(total_pnl_today, 2),
                "avg_win":     round(sum(t["pnl"] for t in wins) / max(len(wins), 1), 2),
                "avg_loss":    round(sum(t["pnl"] for t in losses) / max(len(losses), 1), 2),
            },
            "profit_target_hit":    self._profit_target_hit,
            "loss_limit_hit":       self._loss_limit_hit,
            "top_movers_today": top_movers[:20],
            "missed_opportunities": missed[:10],
            "what_worked": [t for t in today_trades if t["pnl"] > 0],
            "what_didnt":  [t for t in today_trades if t["pnl"] <= 0],
            "suggestions":  suggestions,
        }
        REVIEW_FILE.write_text(json.dumps(review, indent=2, default=str))

        log(f"  Today: {len(today_trades)} trades  wins={len(wins)}  "
            f"P&L={total_pnl_today:+,.0f}  win_rate={review['performance']['win_rate']:.0%}")
        log(f"  Top movers we missed:")
        for m in missed[:5]:
            log(f"    {m['symbol']:<6}  {m['day_chg']:>+.1f}%  reason={m['miss_reason']}")
        log(f"  Suggestions:")
        for s in suggestions[:3]:
            log(f"    → {s}")
        log(f"  Daily review saved → {REVIEW_FILE}")

        # ── Persistent track record (append-only, logging only) ───────────────
        try:
            from track_record_logger import append_daily_record
            fills = [
                {"symbol": t["symbol"],
                 "actual_fill_price": t["exit"],
                 "entry_price": t["entry"],
                 "side": t["side"],
                 "shares": t["shares"],
                 "pnl": t["pnl"]}
                for t in today_trades
            ] or None
            append_daily_record(
                "mock_portfolio",
                equity=self.portfolio.equity,
                day_pnl=round(total_pnl_today, 2),
                num_trades=len(today_trades),
                date_str=today_str,
                fills=fills,
            )
            log(f"  [track_record] logged: equity=${self.portfolio.equity:,.2f} "
                f"day_pnl={total_pnl_today:+,.2f} trades={len(today_trades)}")
        except Exception as _tr_err:
            log(f"  [track_record] write error (non-fatal): {_tr_err}")

        self._regime_changes = []

    # ── Scanner ───────────────────────────────────────────────────────────────

    def _scan(self, in_rth: bool, in_ext: bool):
        now = datetime.now(ET)
        self._last_scan_min = now.hour * 60 + now.minute
        label    = "RTH" if in_rth else "EXT" if in_ext else "OVN"
        min_rv   = self._rv_min if in_rth else 2.5
        min_score = SCAN_MIN_SCORE if in_rth else SCAN_MIN_SCORE + 15
        log(f"Scan [{label}] @ {now.strftime('%H:%M')} — {len(SCANNER_UNIVERSE)} symbols …")

        results = []
        with ThreadPoolExecutor(max_workers=16) as ex:
            futs = {ex.submit(self._score_symbol, s, min_rv): s
                    for s in SCANNER_UNIVERSE}
            for f in as_completed(futs):
                r = f.result()
                if r and r["signal"] != "watch" and r["score"] >= min_score:
                    results.append(r)

        results.sort(key=lambda x: -x["score"])
        self._scan_candidates = results[:8]
        log(f"Scan done: {len(results)} setups")
        for r in self._scan_candidates[:5]:
            log(f"  {r['signal'].upper():<5}  {r['symbol']:<6}  "
                f"score={r['score']}  adx={r['adx']:.0f}  rv={r['rel_vol']:.1f}×"
                f"  rsi={r.get('rsi',0):.0f}  mom={r.get('mom3',0):+.1f}%"
                f"  vwap={r.get('vwap_pct',0):+.1f}%")

    def _score_symbol(self, sym: str, min_rv: float = 1.5) -> dict | None:
        try:
            df = get_bars(sym, period="10d")
            if df is None or len(df) < 20:
                return None
            last  = df.iloc[-1]
            close = float(last["Close"])
            atr   = float(last["atr"])
            adx   = float(last["adx"])
            pdi   = float(last["pdi"])
            ndi   = float(last["ndi"])
            ema   = float(last["ema"])
            reg   = regime(adx, pdi, ndi)

            last_date = last.name.date()
            day_bars  = df[df.index.date == last_date]
            prev_bars = df[df.index.date < last_date]
            day_vol   = float(day_bars["Volume"].sum())
            prev_adv  = prev_bars.groupby(prev_bars.index.date)["Volume"].sum().mean() \
                        if not prev_bars.empty else 1
            rv        = day_vol / prev_adv if prev_adv > 0 else 1.0

            open_p = float(day_bars.iloc[0]["Open"]) if len(day_bars) > 0 else close
            chg    = (close - open_p) / open_p * 100 if open_p else 0

            rsi  = float(last.get("rsi", 50))
            mom3 = float(last.get("mom3", 0))
            vwap = float(last.get("vwap", close))
            vwap_pct = (close - vwap) / vwap * 100 if vwap > 0 else 0

            rv_ok = (rv >= min_rv) if min_rv > 1.0 else True
            # RSI quality filter: avoid overbought longs (>75) and oversold shorts (<25)
            rsi_ok_long  = rsi < 75
            rsi_ok_short = rsi > 25

            if reg in ("ranging", "bull") and close > ema and rv_ok and rsi_ok_long:
                sig = "long"
            elif reg == "bear" and close < ema and rv_ok and rsi_ok_short and not spy_is_bullish():
                sig = "short"
            else:
                sig = "watch"

            adx_eff = max(adx, self._adx_min)
            # Momentum bonus: 3-bar momentum aligned (up to +8 pts)
            mom_bonus = min(abs(mom3) / 3.0, 1.0) * 8 if (
                (sig == "long" and mom3 > 0) or (sig == "short" and mom3 < 0)) else 0
            # VWAP bonus: price above VWAP for longs (up to +7 pts), below for shorts
            if sig == "long":
                vwap_bonus = max(0, min(vwap_pct / 1.0, 1.0)) * 7
            elif sig == "short":
                vwap_bonus = max(0, min(-vwap_pct / 1.0, 1.0)) * 7
            else:
                vwap_bonus = 0
            score = int(
                min(max(rv, 0.1) / 3.0, 1.0) * 26 +
                min(abs(chg) / 5.0, 1.0) * 30 +
                min(adx_eff / 50.0, 1.0) * 29 +
                mom_bonus + vwap_bonus
            )
            return dict(symbol=sym, signal=sig, score=score, atr=atr,
                        adx=adx, rel_vol=rv, close=close, regime=reg,
                        chg=chg, rsi=round(rsi, 1), mom3=round(mom3, 2),
                        vwap=round(vwap, 2), vwap_pct=round(vwap_pct, 2))
        except Exception as e:
            log(f"Score error {sym}: {e}")
            return None

    # ── Rebalance (guarded) ───────────────────────────────────────────────────

    def _rebalance(self, now: datetime):
        # Enforce time gap between rebalances
        if self._last_rebalance_t is not None:
            elapsed = (now - self._last_rebalance_t).total_seconds()
            if elapsed < REBALANCE_GAP_S:
                return

        new_candidates = [c for c in self._scan_candidates
                          if c["symbol"] not in self.portfolio.positions
                          and c["signal"] != "watch"]
        if not new_candidates:
            return
        best = new_candidates[0]

        # Find worst position — must pass all guards
        worst_sym, worst_score = None, 999
        for sym, pos in self.portfolio.positions.items():
            # Guard 1: minimum hold time
            try:
                entry_dt = datetime.fromisoformat(pos["entry_time"])
                if entry_dt.tzinfo is None:
                    entry_dt = entry_dt.replace(tzinfo=ET)
                held_secs = (now - entry_dt).total_seconds()
            except Exception:
                held_secs = 0
            if held_secs < MIN_HOLD_SECS:
                continue

            # Guard 2: never evict a profitable position
            if pos.get("unrealized", 0) > 0:
                continue

            r = self._score_symbol(sym)
            if r is None:
                continue
            adj = r["score"]
            if pos.get("unrealized", 0) < 0:
                adj -= 20
            side = pos["side"]
            if (side == "long"  and r["regime"] == "bear") or \
               (side == "short" and r["regime"] != "bear"):
                adj -= 30
            if adj < worst_score:
                worst_score = adj
                worst_sym   = sym

        if worst_sym is None:
            return

        if best["score"] > worst_score + REBALANCE_THRESH:
            pos   = self.portfolio.positions[worst_sym]
            df    = get_bars(worst_sym)
            if df is None:
                return
            price = float(df.iloc[-1]["Close"])
            pnl   = self.portfolio.close(worst_sym, price, "rebalance")
            log(f"REBAL close {worst_sym:<6}  pnl={pnl:>+,.0f}  "
                f"→ {best['symbol']}  (score {worst_score}→{best['score']})")
            self.portfolio.save()
            self._last_rebalance_t = now

    # ── Trading cycle ─────────────────────────────────────────────────────────

    def _trading_cycle(self, now: datetime, mins: int, in_rth: bool, in_ext: bool):
        to_check = set(self.portfolio.positions.keys())
        if in_rth or in_ext:
            to_check |= set(ALWAYS_WATCH)
        to_check |= ALWAYS_WATCH_OVN
        to_check |= {r["symbol"] for r in self._scan_candidates}

        for sym in to_check:
            try:
                df = get_bars(sym)
                if df is None:
                    continue
                last  = df.iloc[-1]
                close = float(last["Close"])
                atr   = float(last["atr"])
                adx_v = float(last["adx"])
                pdi_v = float(last["pdi"])
                ndi_v = float(last["ndi"])
                ema   = float(last["ema"])
                reg   = regime(adx_v, pdi_v, ndi_v)

                # ── Manage open position ───────────────────────────────────
                if sym in self.portfolio.positions:
                    pos  = self.portfolio.positions[sym]
                    side = pos["side"]
                    self.portfolio.update(sym, close, atr)

                    exit_reason = None

                    if side == "long"  and close <= pos["trail_stop"]:
                        exit_reason = "trail_stop"
                    elif side == "short" and close >= pos["trail_stop"]:
                        exit_reason = "trail_stop"

                    if not exit_reason:
                        if side == "long"  and reg == "bear":
                            exit_reason = "regime_flip"
                        elif side == "short" and reg in ("ranging", "bull"):
                            exit_reason = "regime_flip"

                    if not exit_reason:
                        if side == "long"  and close < ema * 0.999:
                            exit_reason = "ema_cross"
                        elif side == "short" and close > ema * 1.001:
                            exit_reason = "ema_cross"

                    if not exit_reason and side == "short" and in_rth and mins >= EOD_INTRADAY:
                        exit_reason = "eod_short"

                    if not exit_reason and side == "long" and in_rth and mins >= EOD_EVAL:
                        unrl = pos.get("unrealized", 0)
                        if reg == "bear":
                            exit_reason = "eod_regime"
                        elif close < ema:
                            exit_reason = "eod_ema"
                        elif unrl < -(atr * pos["shares"]):
                            exit_reason = "eod_loss"

                    if exit_reason:
                        shares_held = pos["shares"]
                        pnl = self.portfolio.close(sym, close, exit_reason)
                        log(f"EXIT  {sym:<6}  {side.upper():<5}  "
                            f"{shares_held} @ {close:.2f}"
                            f"  pnl={pnl:>+,.0f}  [{exit_reason}]"
                            f"  equity=${self.portfolio.equity:,.0f}")
                        update_stats(self.portfolio.closed[-1])
                        self.portfolio.save()
                        self._close_all(sym)
                        self._cooldown[sym] = now
                        continue

                # ── Entry logic ────────────────────────────────────────────
                if sym in self.portfolio.positions:
                    continue
                if self._day_halted:
                    continue
                if len(self.portfolio.positions) >= MAX_POSITIONS:
                    continue
                # No new entries after 15:25 ET — any position opened then closes at 15:45 (only 20 min)
                if in_rth and mins >= 15 * 60 + 25:
                    continue
                if sym in self._cooldown:
                    if (now - self._cooldown[sym]).total_seconds() < 15 * 60:
                        continue
                    else:
                        del self._cooldown[sym]

                # Volume filter
                last_date = last.name.date()
                day_bars  = df[df.index.date == last_date]
                prev_bars = df[df.index.date < last_date]
                day_vol   = float(day_bars["Volume"].sum())
                prev_adv  = prev_bars.groupby(prev_bars.index.date)["Volume"].sum().mean() \
                            if not prev_bars.empty else 1
                rv_now    = day_vol / prev_adv if prev_adv > 0 else 1.0

                signal = strategy = None

                if in_rth:
                    if sym == "SPY":
                        if mins < 11 * 60:
                            if reg in ("ranging", "bull") and close > ema and rv_now > 1.0:
                                signal, strategy = "long", "spy_regime"
                            elif reg == "bear" and close < ema and rv_now > 1.0 \
                                    and not spy_is_bullish():
                                signal, strategy = "short", "spy_regime"

                    elif any(c["symbol"] == sym for c in self._scan_candidates):
                        cand = next(c for c in self._scan_candidates if c["symbol"] == sym)
                        # Require adaptive rv_min (learned from stats)
                        if cand["signal"] == "long" and reg in ("ranging", "bull") \
                                and close > ema and rv_now >= self._rv_min:
                            signal, strategy = "long", "scanner"
                        elif cand["signal"] == "short" and reg == "bear" \
                                and close < ema and rv_now >= self._rv_min \
                                and not spy_is_bullish():
                            signal, strategy = "short", "scanner"

                elif in_ext:
                    if any(c["symbol"] == sym for c in self._scan_candidates):
                        cand = next(c for c in self._scan_candidates if c["symbol"] == sym)
                        if cand["signal"] == "long" and reg == "bull" \
                                and close > ema and rv_now > 2.5 and adx_v > 35:
                            signal, strategy = "long", "ext_scanner"
                        elif cand["signal"] == "short" and reg == "bear" \
                                and close < ema and rv_now > 2.5 and adx_v > 35 \
                                and not spy_is_bullish():
                            signal, strategy = "short", "ext_scanner"

                else:
                    cands = [c for c in self._scan_candidates if c["symbol"] == sym]
                    cand  = cands[0] if cands else None
                    if cand and cand["signal"] == "long" and reg == "bull" \
                            and close > ema and adx_v > 40:
                        signal, strategy = "long", "ovn"
                    elif cand and cand["signal"] == "short" and reg == "bear" \
                            and close < ema and adx_v > 40 and not spy_is_bullish():
                        signal, strategy = "short", "ovn"

                if signal == "long" and self._regime_no_new_longs:
                    signal = None

                if signal and not self._corr_ok(sym):
                    signal = None

                if signal:
                    shares = self.portfolio.open(sym, signal, close, atr, strategy)
                    if shares:
                        pos = self.portfolio.positions[sym]
                        pos["entry_adx"] = round(adx_v, 1)
                        pos["entry_rv"]  = round(rv_now, 2)
                        log(f"ENTRY {sym:<6}  {signal.upper():<5}  "
                            f"{shares} @ {close:.2f}"
                            f"  trail={pos['trail_stop']:.2f}"
                            f"  regime={reg}  [{strategy}]"
                            f"  equity=${self.portfolio.equity:,.0f}")
                        self.portfolio.save()
                        self._place_all(sym, signal, close)

            except Exception as e:
                log(f"Trading cycle error {sym}: {e}")

    # ── Dashboard trigger ─────────────────────────────────────────────────────

    def _check_trigger(self, in_rth: bool, in_ext: bool):
        if not TRIGGER_FILE.exists():
            return
        try:
            data = json.loads(TRIGGER_FILE.read_text())
            TRIGGER_FILE.unlink()
        except Exception:
            return
        for entry in data.get("entries", []):
            sym  = entry.get("symbol")
            side = entry.get("side")
            if not sym or not side:
                continue
            if sym in self.portfolio.positions:
                continue
            if len(self.portfolio.positions) >= MAX_POSITIONS:
                log(f"Trigger {sym}: at capacity, skipping")
                break
            df = get_bars(sym, period="10d")
            if df is None:
                continue
            last  = df.iloc[-1]
            close = float(last["Close"])
            atr   = float(last["atr"])
            adx   = float(last["adx"])
            pdi   = float(last["pdi"])
            ndi   = float(last["ndi"])
            ema   = float(last["ema"])
            reg   = regime(adx, pdi, ndi)
            if side == "long" and not (close > ema):
                log(f"Trigger {sym}: EMA condition stale, skip")
                continue
            if side == "short" and not (close < ema):
                log(f"Trigger {sym}: EMA condition stale, skip")
                continue
            if side == "short" and spy_is_bullish():
                log(f"Trigger {sym}: SPY bullish — no shorts, skip")
                continue
            if not self._corr_ok(sym):
                continue
            shares = self.portfolio.open(sym, side, close, atr, "dashboard_trigger")
            if shares:
                pos = self.portfolio.positions[sym]
                pos["entry_adx"] = round(adx, 1)
                pos["entry_rv"]  = 0
                trail = pos["trail_stop"]
                log(f"ENTRY {sym:<6} {side.upper():<5}  {shares} @ {close:.4f}"
                    f"  stop={trail:.4f}  regime={reg}  [trigger]"
                    f"  equity=${self.portfolio.equity:,.0f}")
                self.portfolio.save()
                self._place_all(sym, side, close)


# ── Miss diagnosis helpers ────────────────────────────────────────────────────

def _diagnose_miss(sym: str, day_chg: float) -> str:
    if sym not in SCANNER_UNIVERSE:
        return "not_in_universe"
    if abs(day_chg) < 2.0:
        return "move_too_small"

    # Check if it appeared in today's pre-market picks
    pm_rank = None
    pm_score = None
    if PREMARKET_FILE.exists():
        try:
            pm = json.loads(PREMARKET_FILE.read_text())
            picks = pm.get("picks", [])
            for i, p in enumerate(picks):
                if p["symbol"] == sym:
                    pm_rank  = i + 1
                    pm_score = p["score"]
                    break
        except Exception:
            pass

    if pm_rank is not None:
        if pm_rank <= 6:
            return f"in_pm_picks_rank{pm_rank}_score{pm_score:.0f}_but_signal_stale_at_open"
        elif pm_rank <= 20:
            return f"in_pm_picks_rank{pm_rank}_score{pm_score:.0f}_positions_likely_full"
        else:
            return f"in_pm_picks_rank{pm_rank}_too_low_to_enter"

    try:
        df = get_bars(sym, period="5d")
        if df is None:
            return "no_data"
        last  = df.iloc[-1]
        adx   = float(last["adx"])
        pdi   = float(last["pdi"])
        ndi   = float(last["ndi"])
        ema   = float(last["ema"])
        close = float(last["Close"])
        reg   = regime(adx, pdi, ndi)
        rsi   = float(last.get("rsi", 50))

        if adx < ADX_THRESH:
            return f"low_adx({adx:.0f}<{ADX_THRESH})"
        if rsi > 75 and day_chg > 0:
            return f"overbought_at_scan_time(rsi={rsi:.0f})"
        if day_chg > 0 and close < ema:
            return "price_below_ema_despite_gain"
        if reg == "ranging":
            return "ranging_regime_low_score"
        return "low_volume_at_scan_time_or_late_breakout"
    except Exception:
        return "unknown"


def _generate_suggestions(missed: list, today_trades: list) -> list[str]:
    suggestions = []

    # Missed opportunities that were in our PM picks but signal staled at open
    pm_stale = [m for m in missed if "signal_stale_at_open" in m.get("miss_reason", "")]
    if pm_stale:
        suggestions.append(
            f"{len(pm_stale)} pre-market picks had stale signals at 9:35 — "
            "consider allowing entry window until 10:00 or using daily-regime instead of 5m")

    # Missed because PM rank too low (11-50, positions likely full)
    pm_rank_miss = [m for m in missed if "positions_likely_full" in m.get("miss_reason", "")]
    if pm_rank_miss and abs(sum(m["day_chg"] for m in pm_rank_miss)) > 10:
        suggestions.append(
            f"{len(pm_rank_miss)} picks ranked 7-20 made big moves — "
            "consider raising MAX_POSITIONS to 8 or scaling allocation down to 15%")

    # Not-in-universe misses
    universe_misses = [m for m in missed if m.get("miss_reason") == "not_in_universe"]
    if universe_misses:
        syms = ", ".join(m["symbol"] for m in universe_misses[:5])
        suggestions.append(
            f"Universe gaps: {syms} moved big but not in scanner — "
            "add to SCANNER_UNIVERSE in mock_portfolio.py")

    # Low volume at scan time — pre-market scan would have caught these
    vol_misses = [m for m in missed if "low_volume" in m.get("miss_reason", "")]
    if len(vol_misses) >= 2:
        suggestions.append(
            f"{len(vol_misses)} symbols had low volume at scan time but moved later — "
            "pre-market scan (8:30) should improve this by using gap% instead of rv")

    # Overbought at entry
    rsi_misses = [m for m in missed if "overbought" in m.get("miss_reason", "")]
    if rsi_misses:
        suggestions.append(
            f"{len(rsi_misses)} symbols blocked by RSI>75 filter but still moved up — "
            "consider raising RSI threshold to 80 or removing filter for gap-up days")

    # Loss pattern analysis
    losses = [t for t in today_trades if t["pnl"] <= 0]
    if losses:
        by_reason: dict[str, int] = {}
        for t in losses:
            by_reason[t.get("reason", "?")] = by_reason.get(t.get("reason", "?"), 0) + 1
        top_reason, top_n = max(by_reason.items(), key=lambda x: x[1])
        avg_loss = sum(t["pnl"] for t in losses) / len(losses)
        if top_n >= 2:
            remedies = {
                "trail_stop":   "consider TRAIL_MULT=2.5 or wider MIN_TRAIL_PCT",
                "ema_cross":    "EMA_cross may be too sensitive — try ema*0.998 threshold",
                "regime_flip":  "regime flips quickly on 5m — consider confirming on 2 bars",
                "rebalance":    "rebalance exits should NOT generate losses — check guards",
            }
            remedy = remedies.get(top_reason, "review entry quality for this pattern")
            suggestions.append(
                f"Top loss driver: '{top_reason}' ({top_n}× avg={avg_loss:+.0f}) — {remedy}")

    # Win pattern — reinforce what's working
    wins = [t for t in today_trades if t["pnl"] > 0]
    if wins:
        by_strat: dict[str, list] = {}
        for t in wins:
            by_strat.setdefault(t.get("strategy", "?"), []).append(t["pnl"])
        best_strat = max(by_strat.items(), key=lambda x: sum(x[1]))
        suggestions.append(
            f"Best strategy today: '{best_strat[0]}' "
            f"({len(best_strat[1])} wins, total={sum(best_strat[1]):+.0f}) — keep it")

    if not suggestions:
        suggestions.append("No major structural issues today — maintain current parameters")

    return suggestions


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    trader = MockTrader()
    try:
        trader.run()
    except KeyboardInterrupt:
        trader.stop()

if __name__ == "__main__":
    main()
