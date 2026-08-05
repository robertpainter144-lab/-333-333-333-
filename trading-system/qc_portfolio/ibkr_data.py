"""
Interactive Brokers data module — drop-in replacement for market_data.py.

Requires IB Gateway or TWS running on localhost.
  IB Gateway live:  port 4001
  IB Gateway paper: port 4002
  TWS live:         port 7496
  TWS paper:        port 7497

pip install ib_insync
"""
import time
import threading
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

from config import IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID, IBKR_ACCOUNT

try:
    import nest_asyncio as _nest_asyncio
    _nest_asyncio.apply()                          # allow ib_insync inside Flask threads
    import logging as _logging
    _logging.getLogger("ib_insync").setLevel(_logging.CRITICAL)
    from ib_insync import IB, Stock, MarketOrder, LimitOrder, Future
    _IB_AVAILABLE = True
except ImportError:
    _IB_AVAILABLE = False

# ── Connection pool (singleton per thread, reconnects on demand) ──────────

_ib: "IB | None" = None
_ib_lock = threading.Lock()


def _connect() -> "IB":
    """Return a connected IB instance, reconnecting if needed."""
    global _ib
    if not _IB_AVAILABLE:
        raise RuntimeError("ib_insync not installed — run: pip install ib_insync")
    with _ib_lock:
        try:
            if _ib is not None and _ib.isConnected():
                return _ib
        except Exception:
            pass
        ib = IB()
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID,
                   readonly=False, timeout=10)
        ib.sleep(1)   # allow account data to arrive
        _ib = ib
        return _ib


def _safe_call(fn, default):
    """Execute fn(ib) with automatic reconnect; return default on failure."""
    for attempt in range(2):
        try:
            ib = _connect()
            return fn(ib)
        except Exception as e:
            print(f"[IBKR] attempt {attempt+1} failed: {e}")
            global _ib
            _ib = None
            time.sleep(1)
    return default


# ── ES front-month contract qualification ────────────────────────────────

def get_front_month() -> str:
    """Return YYYYMM for the current ES front-month contract.
    ES quarterly cycle: Mar(3) Jun(6) Sep(9) Dec(12).
    Rolls after the 14th of the contract month (near CME roll date).
    """
    now = datetime.now()
    quarters = [3, 6, 9, 12]
    year, month, day = now.year, now.month, now.day
    for q in quarters:
        if q > month or (q == month and day <= 14):
            return f"{year}{q:02d}"
    return f"{year + 1}03"


def qualify_es_contract():
    """Return a qualified IB Future contract for ES front-month, or None if unavailable."""
    if not _IB_AVAILABLE:
        return None
    def _fn(ib):
        expiry = get_front_month()
        contract = Future(
            symbol="ES",
            exchange="CME",
            currency="USD",
            lastTradeDateOrContractMonth=expiry,
        )
        [contract] = ib.qualifyContracts(contract)
        return contract
    return _safe_call(_fn, None)


# ── Account ───────────────────────────────────────────────────────────────

def get_account() -> dict:
    """Return dict with equity, cash, last_equity (mirrors Alpaca format)."""
    def _fn(ib):
        acct = IBKR_ACCOUNT or (ib.managedAccounts()[0] if ib.managedAccounts() else "")
        summary = {s.tag: s.value for s in ib.accountSummary(acct)}

        net_liq   = float(summary.get("NetLiquidation", 0))
        cash      = float(summary.get("TotalCashValue",  0))
        prev_eod  = float(summary.get("PreviousDayEquityWithLoanValue", net_liq))

        return {
            "equity":      net_liq,
            "cash":        cash,
            "last_equity": prev_eod,
            "account_id":  acct,
        }
    return _safe_call(_fn, {"equity": 0, "cash": 0, "last_equity": 0})


# ── Positions ─────────────────────────────────────────────────────────────

def get_positions() -> list:
    """Return list of position dicts (mirrors Alpaca format)."""
    def _fn(ib):
        acct = IBKR_ACCOUNT or (ib.managedAccounts()[0] if ib.managedAccounts() else "")
        rows = []

        # Pull current market data for all positions in one batch
        portfolio = [p for p in ib.portfolio(acct) if p.position != 0]
        if not portfolio:
            return []

        contracts = [p.contract for p in portfolio]
        tickers   = ib.reqTickers(*contracts)
        price_map = {}
        for t in tickers:
            sym = t.contract.symbol
            mid = t.midpoint()
            last = t.last
            price_map[sym] = mid if mid and mid > 0 else (last if last and last > 0 else 0)

        for item in portfolio:
            sym      = item.contract.symbol
            qty      = float(item.position)
            avg_cost = float(item.averageCost)
            cur_px   = price_map.get(sym, avg_cost)
            mktval   = qty * cur_px
            unpl     = (cur_px - avg_cost) * qty
            unplpc   = (cur_px / avg_cost - 1) if avg_cost > 0 else 0.0

            rows.append({
                "symbol":               sym,
                "side":                 "long" if qty > 0 else "short",
                "qty":                  abs(qty),
                "avg_entry_price":      avg_cost,
                "current_price":        cur_px,
                "market_value":         mktval,
                "unrealized_pl":        unpl,
                "unrealized_plpc":      unplpc,
                "unrealized_intraday_pl": unpl,  # approximation; IBKR doesn't expose intraday easily
            })
        return rows
    return _safe_call(_fn, [])


# ── Orders ────────────────────────────────────────────────────────────────

def get_orders(status: str = "all", limit: int = 100) -> list:
    """Return list of open/recent orders."""
    def _fn(ib):
        ib.reqAllOpenOrders()
        ib.sleep(0.5)
        orders = ib.openOrders()
        result = []
        for o in orders[:limit]:
            result.append({
                "id":     o.orderId,
                "symbol": o.contract.symbol if hasattr(o, "contract") else "",
                "side":   o.action.lower() if hasattr(o, "action") else "",
                "qty":    o.totalQuantity,
                "status": o.orderState.status if hasattr(o, "orderState") else "",
            })
        return result
    return _safe_call(_fn, [])


# ── Place order ───────────────────────────────────────────────────────────

def place_order(symbol: str, qty: float, side: str,
                order_type: str = "market", tif: str = "day"):
    """Place a market order. Returns order result dict or None on failure."""
    if qty <= 0:
        return None

    def _fn(ib):
        contract = Stock(symbol, "SMART", "USD")
        ib.qualifyContracts(contract)

        action = side.upper()  # "BUY" or "SELL"
        if order_type == "market":
            order = MarketOrder(action, round(qty, 4))
            order.tif = tif.upper()
        else:
            # Fallback: market order
            order = MarketOrder(action, round(qty, 4))

        trade = ib.placeOrder(contract, order)
        ib.sleep(0.5)   # allow IBKR to acknowledge
        print(f"[IBKR ORDER] {side.upper():4s} {qty:.4f} {symbol}  "
              f"status={trade.orderStatus.status}")
        return {
            "id":     trade.order.orderId,
            "symbol": symbol,
            "side":   side,
            "qty":    qty,
            "status": trade.orderStatus.status,
        }
    return _safe_call(_fn, None)


def close_all_positions():
    """Close every open position at market."""
    def _fn(ib):
        acct     = IBKR_ACCOUNT or (ib.managedAccounts()[0] if ib.managedAccounts() else "")
        portfolio = [p for p in ib.portfolio(acct) if p.position != 0]
        results  = []
        for item in portfolio:
            sym  = item.contract.symbol
            qty  = abs(float(item.position))
            side = "sell" if item.position > 0 else "buy"
            r    = place_order(sym, qty, side)
            if r:
                results.append(r)
        return results
    return _safe_call(_fn, [])


# ── Price data (yfinance — same as market_data.py) ────────────────────────

def _fix_crypto(sym: str) -> str:
    known = {"BTCUSD","ETHUSD","LINKUSD","DOGEUSD","DOTUSD","ADAUSD",
             "SOLUSD","MATICUSD","AVAXUSD","UNIUSD","LTCUSD","BCHUSD"}
    return (sym[:-3] + "-USD") if sym in known else sym


def get_close_prices(symbols: list, period: str = "6mo") -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    syms = list(dict.fromkeys(_fix_crypto(s) for s in symbols))
    try:
        raw   = yf.download(syms, period=period, interval="1d",
                            progress=False, auto_adjust=True, threads=True)
        if raw.empty:
            return pd.DataFrame()
        close = raw["Close"] if "Close" in raw else raw
        if isinstance(close, pd.Series):
            close = close.to_frame(syms[0])
        close = close.dropna(how="all")
        close.columns = [str(c) for c in close.columns]
        return close
    except Exception as e:
        print(f"[IBKR/DATA] close price error: {e}")
        return pd.DataFrame()


def get_current_prices(symbols: list) -> dict:
    if not symbols:
        return {}
    syms = list(dict.fromkeys(_fix_crypto(s) for s in symbols))
    try:
        raw   = yf.download(syms, period="2d", interval="1d",
                            progress=False, auto_adjust=True, threads=True)
        if raw.empty:
            return {}
        close = raw["Close"] if "Close" in raw else raw
        if isinstance(close, pd.Series):
            close = close.to_frame(syms[0])
        result = {}
        for sym in syms:
            col = str(sym)
            if col in close.columns:
                last = close[col].dropna()
                if not last.empty:
                    result[sym] = float(last.iloc[-1])
        return result
    except Exception as e:
        print(f"[IBKR/DATA] current price error: {e}")
        return {}


def get_ohlcv(symbol: str, period: str = "1mo") -> pd.DataFrame:
    try:
        df = yf.download(symbol, period=period, interval="1d",
                         progress=False, auto_adjust=True)
        df.columns = [str(c) for c in df.columns]
        return df
    except Exception as e:
        print(f"[IBKR/DATA] ohlcv error {symbol}: {e}")
        return pd.DataFrame()


def portfolio_history(period: str = "1M", timeframe: str = "1D") -> dict:
    # Served from locally tracked equity_history in portfolio_engine
    return {}


def disconnect():
    """Cleanly disconnect from IB Gateway / TWS."""
    global _ib
    if _ib and _ib.isConnected():
        _ib.disconnect()
    _ib = None
