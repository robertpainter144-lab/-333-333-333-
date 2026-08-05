"""Market data: price history via yfinance, account/positions via Alpaca."""
import yfinance as yf
import pandas as pd
import requests
from config import ALPACA_BASE, ALPACA_HDR

# ── Price data ─────────────────────────────────────────────────────────────

def _fix_crypto(sym):
    """Convert Alpaca crypto format LINKUSD → LINK-USD for yfinance."""
    known = {"BTCUSD","ETHUSD","LINKUSD","DOGEUSD","DOTUSD","ADAUSD",
             "SOLUSD","MATICUSD","AVAXUSD","UNIUSD","LTCUSD","BCHUSD"}
    if sym in known:
        return sym[:-3] + "-USD"
    return sym

def get_close_prices(symbols, period="6mo"):
    """Return DataFrame of daily closing prices (date × symbol)."""
    if not symbols:
        return pd.DataFrame()
    syms = list(dict.fromkeys(_fix_crypto(s) for s in symbols))  # deduplicate preserving order
    try:
        raw = yf.download(syms, period=period, interval="1d",
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
        print(f"[DATA] close price error: {e}")
        return pd.DataFrame()

def get_current_prices(symbols):
    """Return {symbol: latest_price} for a list of symbols."""
    if not symbols:
        return {}
    syms = list(dict.fromkeys(_fix_crypto(s) for s in symbols))
    try:
        raw = yf.download(syms, period="2d", interval="1d",
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
        print(f"[DATA] current price error: {e}")
        return {}

def get_ohlcv(symbol, period="1mo"):
    """Return OHLCV DataFrame for a single symbol."""
    try:
        df = yf.download(symbol, period=period, interval="1d",
                         progress=False, auto_adjust=True)
        df.columns = [str(c) for c in df.columns]
        return df
    except Exception as e:
        print(f"[DATA] ohlcv error {symbol}: {e}")
        return pd.DataFrame()

# ── Alpaca REST helpers ────────────────────────────────────────────────────

def _get(path, params=None):
    try:
        r = requests.get(f"{ALPACA_BASE}{path}", headers=ALPACA_HDR,
                         params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[ALPACA GET] {path}: {e}")
        return {} if path.endswith("account") else []

def _delete(path):
    try:
        r = requests.delete(f"{ALPACA_BASE}{path}", headers=ALPACA_HDR, timeout=10)
        r.raise_for_status()
        return r.json() if r.content else {}
    except Exception as e:
        print(f"[ALPACA DELETE] {path}: {e}")
        return None

def _post(path, payload):
    try:
        r = requests.post(f"{ALPACA_BASE}{path}", headers=ALPACA_HDR,
                          json=payload, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[ALPACA POST] {path}: {e}")
        return None

def get_account():    return _get("/account")
def get_positions():  return _get("/positions") or []
def get_orders(status="all", limit=100):
    return _get("/orders", {"status": status, "limit": limit}) or []

def place_order(symbol, qty, side, order_type="market", tif="day"):
    if qty <= 0:
        return None
    payload = {"symbol": symbol, "qty": str(round(qty, 4)),
               "side": side, "type": order_type, "time_in_force": tif}
    result = _post("/orders", payload)
    if result:
        print(f"[ORDER] {side.upper():4s} {qty:.4f} {symbol}")
    return result

def close_all_positions():
    return _delete("/positions")

def portfolio_history(period="1M", timeframe="1D"):
    # Portfolio history is not available via broker trading API — return empty
    return {}
