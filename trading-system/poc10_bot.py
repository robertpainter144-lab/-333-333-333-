#!/usr/bin/env python3
"""
poc10_bot.py — $10 proof-of-concept reliability test on Alpaca PAPER.

Sleeves:
  crypto    $4.00  BTC/USD  ETH/USD  SOL/USD  — 24/7, short-window momentum + trailing stop
  equity    $3.00  QQQ  SPY                    — RTH only, momentum gate + hard stop
  defensive $3.00  GLD                         — RTH only, always-hold + hard stop

Design goals:
  • Fractional / notional sizing so $1-4 allocations are placeable
  • Isolated accounting — snapshot legacy positions at startup, never touch them
  • Hard stop (−3% from entry) on every position
  • Trailing stop (−2% from peak) on crypto positions
  • Swappable broker layer: Broker ABC → AlpacaBroker now, IBKRBroker later
  • Every event appended to /Users/quarki3/track_record.jsonl tagged 'poc10'
  • /Users/quarki3/swarm_results/completed/poc10.json written after first tick
"""

from __future__ import annotations

import json
import logging
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ─── Config ───────────────────────────────────────────────────────────────────

TOTAL_BUDGET = 10.00
# NOTE: Alpaca paper requires $10 minimum per direct crypto order (BTC/USD etc.).
# With a $4 total crypto sleeve we substitute crypto-tracking equity ETFs (IBIT/ETHA)
# which carry $1 fractional minimums.  These trade during RTH only, not 24/7.
SLEEVE_CFG = {
    "crypto": {
        "budget": 4.00,
        # IBIT = iShares Bitcoin Trust, ETHA = iShares Ethereum Trust
        "symbols": ["IBIT", "ETHA"],
        "always_on": False,   # equity ETFs trade RTH only
        "momentum_gate": True,
        "trailing_stop": True,
    },
    "equity": {
        "budget": 3.00,
        "symbols": ["QQQ", "SPY"],
        "always_on": False,
        "momentum_gate": True,
        "trailing_stop": False,
    },
    "defensive": {
        "budget": 3.00,
        "symbols": ["GLD"],
        "always_on": False,
        "momentum_gate": False,  # hold unconditionally when market open
        "trailing_stop": False,
    },
}

HARD_STOP_PCT = 0.030   # exit if down 3% from entry
TRAIL_PCT     = 0.020   # crypto: exit if down 2% from peak
MOM_BARS      = 6       # bars for short-window momentum check (5-min bars)
TICK_SECS     = 60      # main loop cadence

ET         = ZoneInfo("America/New_York")
PREFIX     = "poc10_"
TRACK_FILE = Path("/Users/quarki3/track_record.jsonl")
RESULT_FILE = Path("/Users/quarki3/swarm_results/completed/poc10.json")

log = logging.getLogger("poc10")


# ─── Broker abstraction (swap AlpacaBroker → IBKRBroker to go live) ──────────

class Broker(ABC):

    @abstractmethod
    def account(self) -> dict: ...

    @abstractmethod
    def positions(self) -> list[dict]: ...

    @abstractmethod
    def order_buy_notional(
        self, symbol: str, notional: float, client_order_id: str, asset_class: str
    ) -> dict: ...

    @abstractmethod
    def order_sell_qty(
        self, symbol: str, qty: float, client_order_id: str, asset_class: str
    ) -> dict: ...

    @abstractmethod
    def position_qty(self, symbol: str) -> float | None: ...

    @abstractmethod
    def price(self, symbol: str, asset_class: str) -> float | None: ...

    @abstractmethod
    def recent_closes(self, symbol: str, asset_class: str, n: int) -> list[float]: ...

    @abstractmethod
    def clock(self) -> dict: ...


class AlpacaBroker(Broker):
    _PAPER = "https://paper-api.alpaca.markets"
    _DATA  = "https://data.alpaca.markets"

    def __init__(self, key: str, secret: str) -> None:
        self._h = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}

    def _get(self, base: str, path: str, **params) -> dict:
        r = requests.get(
            f"{base}{path}", headers=self._h,
            params={k: v for k, v in params.items() if v is not None},
            timeout=12,
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = requests.post(f"{self._PAPER}{path}", headers=self._h, json=body, timeout=12)
        r.raise_for_status()
        return r.json()

    def account(self) -> dict:
        return self._get(self._PAPER, "/v2/account")

    def positions(self) -> list[dict]:
        return self._get(self._PAPER, "/v2/positions")

    def order_buy_notional(
        self, symbol: str, notional: float, client_order_id: str, asset_class: str
    ) -> dict:
        tif = "gtc" if asset_class == "crypto" else "day"
        return self._post("/v2/orders", {
            "symbol": symbol,
            "side": "buy",
            "type": "market",
            "time_in_force": tif,
            "notional": str(round(notional, 2)),
            "client_order_id": client_order_id,
        })

    def order_sell_qty(
        self, symbol: str, qty: float, client_order_id: str, asset_class: str
    ) -> dict:
        tif = "gtc" if asset_class == "crypto" else "day"
        # Format: 8 decimal places for crypto, 6 for equity
        fmt = ".8f" if asset_class == "crypto" else ".6f"
        return self._post("/v2/orders", {
            "symbol": symbol,
            "side": "sell",
            "type": "market",
            "time_in_force": tif,
            "qty": format(qty, fmt),
            "client_order_id": client_order_id,
        })

    def position_qty(self, symbol: str) -> float | None:
        try:
            sym_enc = symbol.replace("/", "%2F")
            d = self._get(self._PAPER, f"/v2/positions/{sym_enc}")
            return float(d.get("qty", 0))
        except Exception:
            return None

    def price(self, symbol: str, asset_class: str) -> float | None:
        # Try Alpaca data API first
        try:
            if asset_class == "crypto":
                d = self._get(self._DATA, "/v1beta3/crypto/us/latest/bars", symbols=symbol)
                bars = d.get("bars", {})
                data = bars.get(symbol) or bars.get(symbol.replace("/", ""))
                if data:
                    return float(data["c"])
            else:
                d = self._get(self._DATA, "/v2/stocks/quotes/latest",
                              symbols=symbol, feed="iex")
                q = d.get("quotes", {}).get(symbol, {})
                ap, bp = float(q.get("ap") or 0), float(q.get("bp") or 0)
                if ap and bp:
                    return (ap + bp) / 2.0
                if ap:
                    return ap
                if bp:
                    return bp
        except Exception as e:
            log.debug("alpaca price(%s): %s", symbol, e)

        # Fallback: yfinance
        try:
            import yfinance as yf
            sym_yf = symbol.replace("/", "-") if asset_class == "crypto" else symbol
            hist = yf.Ticker(sym_yf).history(period="1d", interval="5m")
            if not hist.empty:
                return float(hist["Close"].iloc[-1])
        except Exception as e2:
            log.debug("yfinance price(%s): %s", symbol, e2)

        return None

    def recent_closes(self, symbol: str, asset_class: str, n: int = 6) -> list[float]:
        try:
            if asset_class == "crypto":
                d = self._get(self._DATA, "/v1beta3/crypto/us/bars",
                              symbols=symbol, timeframe="5Min", limit=n)
                bars = d.get("bars", {})
                data = bars.get(symbol) or bars.get(symbol.replace("/", ""))
                if data:
                    return [float(b["c"]) for b in data]
            else:
                d = self._get(self._DATA, "/v2/stocks/bars",
                              symbols=symbol, timeframe="5Min", limit=n, feed="iex")
                data = d.get("bars", {}).get(symbol, [])
                if data:
                    return [float(b["c"]) for b in data]
        except Exception as e:
            log.debug("recent_closes(%s): %s", symbol, e)

        # Fallback: yfinance intraday
        try:
            import yfinance as yf
            sym_yf = symbol.replace("/", "-") if asset_class == "crypto" else symbol
            hist = yf.Ticker(sym_yf).history(period="1d", interval="5m")
            if not hist.empty:
                return [float(c) for c in hist["Close"].tail(n).tolist()]
        except Exception as e2:
            log.debug("yfinance closes(%s): %s", symbol, e2)

        return []

    def clock(self) -> dict:
        return self._get(self._PAPER, "/v2/clock")


# ─── Sub-ledger: isolated $10 book ───────────────────────────────────────────

class SubLedger:

    def __init__(self) -> None:
        self.positions: dict[str, dict] = {}
        self.closed_pnl: float = 0.0
        self.all_orders: list[dict] = []

    def open_position(
        self,
        symbol: str,
        sleeve: str,
        notional: float,
        entry_price: float,
        order_resp: dict,
    ) -> None:
        qty_est = (notional / entry_price) if entry_price else 0.0
        self.positions[symbol] = {
            "sleeve": sleeve,
            "entry_price": entry_price,
            "notional": notional,
            "qty_est": qty_est,
            "peak": entry_price,
            "order_id": order_resp.get("id", ""),
            "status": order_resp.get("status", ""),
            "opened_at": datetime.now(ET).isoformat(),
        }
        self.all_orders.append({
            "symbol": symbol,
            "side": "buy",
            "sleeve": sleeve,
            "notional": notional,
            "est_entry_price": entry_price,
            "order_id": order_resp.get("id", ""),
            "status": order_resp.get("status", ""),
            "ts": datetime.now(ET).isoformat(),
        })

    def close_position(self, symbol: str, exit_price: float, reason: str) -> float:
        pos = self.positions.pop(symbol, None)
        if not pos:
            return 0.0
        pnl = (exit_price / pos["entry_price"] - 1.0) * pos["notional"] if pos["entry_price"] else 0.0
        self.closed_pnl += pnl
        self.all_orders.append({
            "symbol": symbol,
            "side": "sell",
            "sleeve": pos["sleeve"],
            "exit_price": exit_price,
            "pnl": round(pnl, 6),
            "reason": reason,
            "ts": datetime.now(ET).isoformat(),
        })
        return pnl

    def update_peak(self, symbol: str, price: float) -> None:
        if symbol in self.positions and price > self.positions[symbol]["peak"]:
            self.positions[symbol]["peak"] = price

    def unrealized_pnl(self, prices: dict[str, float]) -> float:
        total = 0.0
        for sym, pos in self.positions.items():
            if sym in prices and pos["entry_price"]:
                total += (prices[sym] / pos["entry_price"] - 1.0) * pos["notional"]
        return total


# ─── Bot ──────────────────────────────────────────────────────────────────────

class PoC10Bot:

    def __init__(self, broker: Broker) -> None:
        self.broker = broker
        self.ledger = SubLedger()
        self.legacy_syms: set[str] = set()
        self.errors: list[str] = []

    # ── event logging ─────────────────────────────────────────────────────────

    def _emit(self, event: str, **kw) -> None:
        entry = {"ts": datetime.now(ET).isoformat(), "tag": "poc10", "event": event, **kw}
        try:
            with TRACK_FILE.open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
        except Exception as e:
            self.errors.append(f"log_write:{e}")
        log.info("%s", json.dumps(entry))

    # ── initialisation ────────────────────────────────────────────────────────

    def init(self) -> None:
        try:
            existing = self.broker.positions()
            self.legacy_syms = {p["symbol"] for p in existing}
            log.info("Legacy positions (will NOT touch): %s", self.legacy_syms or "none")
        except Exception as e:
            self.errors.append(f"init_positions:{e}")
            log.error("Could not fetch existing positions: %s", e)
        self._emit("bot_start", total_budget=TOTAL_BUDGET,
                   sleeves=list(SLEEVE_CFG.keys()), legacy=sorted(self.legacy_syms))

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _asset_class(symbol: str) -> str:
        return "crypto" if "/" in symbol else "equity"

    def _momentum_ok(self, symbol: str, cls: str) -> bool:
        closes = self.broker.recent_closes(symbol, cls, MOM_BARS)
        if len(closes) < 2:
            # no bars data → allow entry (this is a PoC, never block on missing data)
            log.debug("No bars for %s — momentum defaulting to True", symbol)
            return True
        sma = sum(closes[:-1]) / (len(closes) - 1)
        ok = closes[-1] >= sma
        log.debug("%s momentum: last=%.4f sma(n=%d)=%.4f → %s",
                  symbol, closes[-1], len(closes) - 1, sma, "OK" if ok else "SKIP")
        return ok

    # ── entry / exit ──────────────────────────────────────────────────────────

    def _enter(self, symbol: str, notional: float, sleeve: str) -> bool:
        cls = self._asset_class(symbol)
        coid = f"{PREFIX}{sleeve}_{symbol.replace('/', '')}_{int(time.time())}"
        try:
            resp = self.broker.order_buy_notional(symbol, notional, coid, cls)
            price = self.broker.price(symbol, cls) or 0.0
            self.ledger.open_position(symbol, sleeve, notional, price, resp)
            self._emit("entry", symbol=symbol, sleeve=sleeve, notional=notional,
                       est_price=price, order_id=resp.get("id"), status=resp.get("status"))
            log.info("ENTRY %-10s sleeve=%-10s notional=$%.2f @%.4f id=%s",
                     symbol, sleeve, notional, price, resp.get("id", "?"))
            return True
        except Exception as e:
            self.errors.append(f"entry:{symbol}:{e}")
            self._emit("entry_error", symbol=symbol, sleeve=sleeve,
                       notional=notional, error=str(e))
            log.error("Entry FAILED %s: %s", symbol, e)
            return False

    def _exit(self, symbol: str, reason: str) -> None:
        cls = self._asset_class(symbol)
        pos = self.ledger.positions.get(symbol)
        if not pos:
            return
        # Prefer actual Alpaca qty over our estimate
        qty = self.broker.position_qty(symbol) or pos["qty_est"]
        if qty <= 0:
            log.warning("Zero qty for %s — skipping exit", symbol)
            return
        coid = f"{PREFIX}exit_{symbol.replace('/', '')}_{int(time.time())}"
        try:
            resp = self.broker.order_sell_qty(symbol, qty, coid, cls)
            price = self.broker.price(symbol, cls) or pos["entry_price"]
            pnl = self.ledger.close_position(symbol, price, reason)
            self._emit("exit", symbol=symbol, reason=reason, qty=qty,
                       est_price=price, pnl=round(pnl, 6), order_id=resp.get("id"))
            log.info("EXIT  %-10s reason=%-12s @%.4f pnl=$%.6f id=%s",
                     symbol, reason, price, pnl, resp.get("id", "?"))
        except Exception as e:
            self.errors.append(f"exit:{symbol}:{e}")
            self._emit("exit_error", symbol=symbol, reason=reason, error=str(e))
            log.error("Exit FAILED %s: %s", symbol, e)

    # ── per-tick logic ────────────────────────────────────────────────────────

    def _check_stops(self) -> None:
        for symbol in list(self.ledger.positions):
            cls = self._asset_class(symbol)
            price = self.broker.price(symbol, cls)
            if price is None:
                continue
            self.ledger.update_peak(symbol, price)
            pos = self.ledger.positions[symbol]
            entry = pos["entry_price"]
            if not entry:
                continue
            # hard stop: down 3% from entry
            if price < entry * (1.0 - HARD_STOP_PCT):
                log.info("HARD STOP %s price=%.4f entry=%.4f", symbol, price, entry)
                self._exit(symbol, "hard_stop")
                continue
            # trailing stop: crypto only, down 2% from peak
            cfg = SLEEVE_CFG.get(pos["sleeve"], {})
            if cfg.get("trailing_stop") and price < pos["peak"] * (1.0 - TRAIL_PCT):
                log.info("TRAIL STOP %s price=%.4f peak=%.4f", symbol, price, pos["peak"])
                self._exit(symbol, "trail_stop")

    def _run_sleeve(self, sleeve: str, cfg: dict, market_open: bool) -> None:
        if not cfg["always_on"] and not market_open:
            return  # equity/defensive: skip when market closed
        per_sym = cfg["budget"] / len(cfg["symbols"])
        for symbol in cfg["symbols"]:
            if symbol in self.ledger.positions:
                continue  # already holding
            if symbol in self.legacy_syms:
                log.warning("Skip %s — legacy position exists, will not commingle", symbol)
                continue
            if cfg["momentum_gate"] and not self._momentum_ok(symbol, self._asset_class(symbol)):
                log.info("Skip %s — short-window momentum negative", symbol)
                continue
            self._enter(symbol, per_sym, sleeve)

    def tick(self) -> dict:
        clk: dict = {}
        market_open = False
        try:
            clk = self.broker.clock()
            market_open = bool(clk.get("is_open", False))
        except Exception as e:
            self.errors.append(f"clock:{e}")
            log.error("Clock fetch failed: %s", e)

        self._check_stops()
        for sleeve, cfg in SLEEVE_CFG.items():
            self._run_sleeve(sleeve, cfg, market_open)

        return {"market_open": market_open, "clock": clk}

    # ── result reporting ──────────────────────────────────────────────────────

    def write_result(self, launched: bool) -> dict:
        buy_orders = [o for o in self.ledger.all_orders if o["side"] == "buy"]
        result = {
            "task": "poc10",
            "launched": launched,
            "sleeves": {
                k: {"budget": v["budget"], "symbols": v["symbols"]}
                for k, v in SLEEVE_CFG.items()
            },
            "crypto_pct": round(SLEEVE_CFG["crypto"]["budget"] / TOTAL_BUDGET * 100, 1),
            "first_orders": self.ledger.all_orders,
            "mechanics_ok": len(self.errors) == 0,
            "errors": self.errors,
            "summary": (
                f"Placed {len(buy_orders)} buy orders across "
                f"{len(self.ledger.positions)} open positions. "
                f"Errors: {len(self.errors)}."
            ),
            "success": launched and len(buy_orders) > 0 and len(self.errors) == 0,
        }
        RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESULT_FILE.write_text(json.dumps(result, indent=2))
        return result


# ─── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("/tmp/poc10_bot.log"),
        ],
    )

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from alpaca_keys import PAPER_KEY, PAPER_SECRET
    except Exception as e:
        log.error("Cannot load Alpaca paper keys: %s", e)
        sys.exit(1)

    broker = AlpacaBroker(PAPER_KEY, PAPER_SECRET)

    # Verify connectivity
    try:
        acct = broker.account()
        log.info(
            "Alpaca PAPER — equity=$%s  cash=$%s  buying_power=$%s",
            acct.get("equity"), acct.get("cash"), acct.get("buying_power"),
        )
    except Exception as e:
        log.error("Cannot reach Alpaca paper API: %s", e)
        sys.exit(1)

    bot = PoC10Bot(broker)
    bot.init()

    # ── First tick (places initial orders) ───────────────────────────────────
    log.info("=== First tick — placing initial positions ===")
    tick_info = bot.tick()
    result = bot.write_result(launched=True)

    log.info(
        "First-tick summary: %d buy orders | %d open positions | market_open=%s | errors=%d",
        sum(1 for o in result["first_orders"] if o["side"] == "buy"),
        len(bot.ledger.positions),
        tick_info["market_open"],
        len(result["errors"]),
    )

    # ── Continuous loop ───────────────────────────────────────────────────────
    log.info("Entering main loop (tick every %ds) — Ctrl-C to stop", TICK_SECS)
    try:
        while True:
            time.sleep(TICK_SECS)
            try:
                bot.tick()
                bot.write_result(launched=True)
            except Exception as e:
                bot.errors.append(f"loop_tick:{e}")
                log.error("Loop tick error: %s", e)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
        bot.write_result(launched=True)


if __name__ == "__main__":
    main()
