#!/usr/bin/env python3
"""D39 live crypto sleeve — whale-gated momentum, broker-pluggable (Kraken/Alpaca).

Rails (DIRECTIVES.md D39): $50 hard cap, freeze + liquidate at sleeve P/L
<= -$15, per-trade ledger in state/closed_trades.csv (D7-scoreable).
Entries need EMA momentum AND (whale risk-on gate OR stale whale feed).

config.json "broker" selects the venue:
  kraken — needs ~/kraken.env (KRAKEN_API_KEY / KRAKEN_API_SECRET, no
           withdrawal permission). Trades the moment the key exists.
  alpaca — needs live crypto_status ACTIVE (crypto agreement signed).
Missing credentials/agreement -> idle + repeat-suppressed P4 ping.

Runs every 5 min via launchd com.quarki3.crypto-sleeve (Mac interim host;
migrate to VM alongside the rest of the stack when SSH access returns).
"""
from __future__ import annotations

import base64
import csv
import fcntl
import hashlib
import hmac
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent
STATE_F = ROOT / "state" / "sleeve_state.json"
LEDGER_F = ROOT / "state" / "closed_trades.csv"
WHALE_F = ROOT / "state" / "whale_signals.json"
CFG = json.loads((ROOT / "config.json").read_text())

sys.path.insert(0, str(HOME / "bin"))
try:
    from quarki_notify import notify
except Exception:
    def notify(level, title, msg):  # noqa: ARG001
        return False


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", flush=True)


def read_env(path: str) -> dict:
    env = {}
    p = Path(path).expanduser()
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip().replace("export ", "")] = v.strip().strip('"').strip("'")
    return env


def ema(vals: list[float], n: int) -> float:
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


# ---------------------------------------------------------------- brokers
class Position:
    def __init__(self, symbol: str, qty: float, cur: float, entry: float, notional: float):
        self.symbol = symbol          # canonical "BTC/USD"
        self.qty = qty
        self.current_price = cur
        self.entry_price = entry
        self.notional = notional or qty * entry
        self.market_value = qty * cur
        self.unrealized_pl = self.market_value - self.notional


class KrakenBroker:
    """Spot only. Kraken has balances, not positions — entry prices come from
    sleeve state; an untracked balance inherits current price as entry."""
    PAIR = {"BTC/USD": "XBTUSD", "ETH/USD": "ETHUSD", "SOL/USD": "SOLUSD"}
    ASSET = {"BTC/USD": ("XXBT", "XBT.F"), "ETH/USD": ("XETH", "ETH.F"),
             "SOL/USD": ("SOL", "SOL.F")}
    BASE = "https://api.kraken.com"

    def __init__(self):
        env = read_env(CFG.get("kraken_env_file", "~/kraken.env"))
        self.key = env.get("KRAKEN_API_KEY", "")
        self.sec = env.get("KRAKEN_API_SECRET", "")

    def _pub(self, path: str, params: dict) -> dict:
        url = f"{self.BASE}{path}?{urllib.parse.urlencode(params)}"
        with urllib.request.urlopen(url, timeout=20) as r:
            out = json.load(r)
        if out.get("error"):
            raise RuntimeError(f"kraken {path}: {out['error']}")
        return out["result"]

    def _priv(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        params["nonce"] = str(int(time.time() * 1000))
        post = urllib.parse.urlencode(params)
        digest = hashlib.sha256((params["nonce"] + post).encode()).digest()
        mac = hmac.new(base64.b64decode(self.sec), path.encode() + digest, hashlib.sha512)
        req = urllib.request.Request(
            self.BASE + path, data=post.encode(),
            headers={"API-Key": self.key, "API-Sign": base64.b64encode(mac.digest()).decode(),
                     "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=20) as r:
            out = json.load(r)
        if out.get("error"):
            raise RuntimeError(f"kraken {path}: {out['error']}")
        return out["result"]

    def ready(self, st: dict, suppressed) -> bool:
        if not self.key or not self.sec:
            suppressed(st, "kraken_key", "action", "Crypto sleeve waiting on a Kraken API key",
                       "The sleeve is pointed at your Kraken account but ~/kraken.env doesn't "
                       "exist yet. On kraken.com: Settings > API > Create key with permissions "
                       "'Query funds' and 'Create & modify orders' (NOT withdrawals), then save "
                       "it on the Mac as ~/kraken.env with lines KRAKEN_API_KEY=... and "
                       "KRAKEN_API_SECRET=... — it starts trading within 5 minutes.")
            log("idle: no kraken API key (~/kraken.env missing)")
            return False
        return True

    def price(self, symbol: str) -> float:
        t = self._pub("/0/public/Ticker", {"pair": self.PAIR[symbol]})
        return float(next(iter(t.values()))["c"][0])

    def closes(self, symbol: str) -> list[float]:
        r = self._pub("/0/public/OHLC", {"pair": self.PAIR[symbol], "interval": 60})
        rows = next(v for k, v in r.items() if k != "last")
        return [float(x[4]) for x in rows]

    def cash(self) -> float:
        bal = self._priv("/0/private/Balance")
        return sum(float(bal.get(a, 0)) for a in ("ZUSD", "USD", "USD.F"))

    def positions(self, st: dict) -> list[Position]:
        bal = self._priv("/0/private/Balance")
        out = []
        for sym in CFG["universe"]:
            qty = sum(float(bal.get(a, 0)) for a in self.ASSET[sym])
            if qty <= 0:
                continue
            cur = self.price(sym)
            if qty * cur < 2:          # dust
                continue
            meta = st["positions"].get(sym, {})
            entry = float(meta.get("entry_price") or cur)
            out.append(Position(sym, qty, cur, entry, float(meta.get("notional") or 0)))
        return out

    def buy(self, symbol: str, usd: float) -> float:
        price = self.price(symbol)
        # 0.5% haircut: market fill can be worse than the quote; without it the
        # real USD spent could silently exceed the intended size (and the cap).
        vol = round(usd * 0.995 / price, 8)
        self._priv("/0/private/AddOrder",
                   {"pair": self.PAIR[symbol], "type": "buy",
                    "ordertype": "market", "volume": f"{vol:.8f}"})
        return price

    def close(self, pos: Position) -> None:
        self._priv("/0/private/AddOrder",
                   {"pair": self.PAIR[pos.symbol], "type": "sell",
                    "ordertype": "market", "volume": f"{pos.qty:.8f}"})


class AlpacaBroker:
    DATA = "https://data.alpaca.markets"

    def __init__(self):
        env = read_env(CFG.get("env_file", "~/alpaca_live.env"))
        self.key = next(v for k, v in env.items() if "KEY" in k.upper() and "SECRET" not in k.upper())
        self.sec = next(v for k, v in env.items() if "SECRET" in k.upper())
        self.base = next((v for k, v in env.items() if "URL" in k.upper() or "BASE" in k.upper()),
                         "https://api.alpaca.markets").rstrip("/")
        self.hdr = {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.sec,
                    "Content-Type": "application/json"}

    def _req(self, path, method="GET", body=None, base=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request((base or self.base) + path, data=data,
                                     headers=self.hdr, method=method)
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def ready(self, st: dict, suppressed) -> bool:
        acct = self._req("/v2/account")
        if acct.get("crypto_status") != "ACTIVE":
            suppressed(st, "crypto_inactive", "action", "Crypto sleeve waiting on you",
                       "Alpaca still hasn't got crypto enabled (crypto agreement unsigned). "
                       "Sign it in the Alpaca dashboard, or keep using the Kraken leg.")
            log(f"idle: alpaca crypto_status={acct.get('crypto_status')}")
            return False
        self._acct = acct
        return True

    def cash(self) -> float:
        return float(self._acct.get("non_marginable_buying_power") or self._acct.get("cash") or 0)

    def closes(self, symbol: str) -> list[float]:
        q = urllib.parse.urlencode({"symbols": symbol, "timeframe": CFG["bar_timeframe"],
                                    "limit": 80, "sort": "desc"})
        rows = self._req(f"/v1beta3/crypto/us/bars?{q}", base=self.DATA)["bars"].get(symbol, [])
        rows.reverse()
        return [float(r["c"]) for r in rows]

    def positions(self, st: dict) -> list[Position]:
        out = []
        for p in self._req("/v2/positions"):
            if p.get("asset_class") != "crypto":
                continue
            flat = p["symbol"]
            sym = f"{flat[:-3]}/{flat[-3:]}"
            meta = st["positions"].get(sym, {})
            entry = float(meta.get("entry_price") or p["avg_entry_price"])
            out.append(Position(sym, float(p["qty"]), float(p["current_price"]),
                                entry, float(meta.get("notional") or p["cost_basis"])))
        return out

    def buy(self, symbol: str, usd: float) -> float:
        price = self.closes(symbol)[-1]
        self._req("/v2/orders", method="POST",
                  body={"symbol": symbol, "notional": f"{usd:.2f}", "side": "buy",
                        "type": "market", "time_in_force": "gtc"})
        return price

    def close(self, pos: Position) -> None:
        self._req(f"/v2/positions/{pos.symbol.replace('/', '')}", method="DELETE")


# ---------------------------------------------------------------- engine
def load_state() -> dict:
    if STATE_F.exists():
        return json.loads(STATE_F.read_text())
    return {"positions": {}, "frozen": False, "last_notified": {}}


def save_state(st: dict) -> None:
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2))
    tmp.replace(STATE_F)


def suppressed_notify(st: dict, key: str, level: str, title: str, msg: str,
                      hours: float = 24.0) -> None:
    """D28: first occurrence pings once, persisting condition re-pings every `hours`."""
    now = time.time()
    if now - st["last_notified"].get(key, 0) >= hours * 3600:
        notify(level, title, msg)
        st["last_notified"][key] = now


def momentum_ok(closes: list[float]) -> bool | None:
    """True/False = real signal; None = not enough data (a data outage must
    never read as a bearish flip and force a live sell)."""
    if len(closes) < CFG["ema_slow"] + 2:
        return None
    day_ago = closes[-25] if len(closes) >= 25 else closes[0]
    return ema(closes, CFG["ema_fast"]) > ema(closes, CFG["ema_slow"]) and closes[-1] > day_ago


def whale_gate() -> tuple[bool, str]:
    """(entries_allowed, mode). Stale/empty/missing feed -> momentum-only fallback."""
    try:
        w = json.loads(WHALE_F.read_text())
        asof = datetime.fromisoformat(w["asof"])
        age_h = (datetime.now(timezone.utc) - asof).total_seconds() / 3600
        if age_h > CFG["whale_max_age_hours"]:
            return True, "momentum-only (whale feed stale %.0fh)" % age_h
        if not w.get("rows"):
            return True, "momentum-only (whale feed empty)"
        # Minimum-evidence rule: the gate only gets authority over live entries
        # when the signal has breadth. A sample of one wallet's single trade
        # (or a batch of pure stable/wrap shuffles) must never block trading.
        n = w.get("signal_trades", w.get("rows", 0))
        wallets = w.get("distinct_wallets", 1)
        vol = w.get("signal_usd", w.get("real_buys_usd", 0) + w.get("derisk_usd", 0))
        if (n < CFG.get("whale_min_trades", 5)
                or wallets < CFG.get("whale_min_wallets", 3)
                or vol < CFG.get("whale_min_usd", 50000)):
            return True, ("momentum-only (whale signal thin: n=%d wallets=%d vol=$%d)"
                          % (n, wallets, vol))
        if w["risk_on"] >= CFG["whale_gate_min_risk_on"]:
            return True, "whale-confirmed (risk_on=%.2f)" % w["risk_on"]
        return False, "whale-blocked (risk_on=%.2f)" % w["risk_on"]
    except Exception:
        return True, "momentum-only (no whale feed)"


def realized_total() -> float:
    if not LEDGER_F.exists():
        return 0.0
    with LEDGER_F.open() as f:
        return sum(float(r["realized_pl"]) for r in csv.DictReader(f))


def append_ledger(row: dict) -> None:
    new = not LEDGER_F.exists()
    with LEDGER_F.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["closed_at", "symbol", "entry_time",
                                          "entry_price", "exit_price", "notional",
                                          "realized_pl", "pl_pct", "reason", "signal_mode"])
        if new:
            w.writeheader()
        w.writerow(row)


def close_position(broker, pos: Position, st: dict, reason: str) -> None:
    broker.close(pos)
    meta = st["positions"].pop(pos.symbol, {})
    append_ledger({"closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "symbol": pos.symbol, "entry_time": meta.get("opened", ""),
                   "entry_price": f"{pos.entry_price:.4f}",
                   "exit_price": f"{pos.current_price:.4f}",
                   "notional": f"{pos.notional:.2f}",
                   "realized_pl": f"{pos.unrealized_pl:.4f}",
                   "pl_pct": f"{pos.unrealized_pl / pos.notional:.4f}" if pos.notional else "0",
                   "reason": reason, "signal_mode": meta.get("signal_mode", "?")})
    save_state(st)   # persist immediately: a later failure this tick must not
    log(f"CLOSED {pos.symbol} reason={reason} pl={pos.unrealized_pl:+.2f}")  # roll back a close that already executed on the exchange


_LOCK_FH = None


def acquire_lock() -> bool:
    global _LOCK_FH
    _LOCK_FH = open(ROOT / "state" / ".lock", "w")
    try:
        fcntl.flock(_LOCK_FH, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def main() -> None:
    if not acquire_lock():
        log("skip: previous run still active (lock held)")
        return
    st = load_state()
    broker = KrakenBroker() if CFG.get("broker", "kraken") == "kraken" else AlpacaBroker()

    if not broker.ready(st, suppressed_notify):
        save_state(st)
        return

    if not st.get("seen_active"):
        st["seen_active"] = True
        notify("info", "Crypto sleeve is LIVE",
               f"The sleeve is connected to {CFG.get('broker', 'kraken')} and active. "
               "It buys when momentum/whale signals line up and cash is available; "
               "entries get logged silently, rails page you.")

    positions = broker.positions(st)

    # Persist adopted positions (balances with no state entry — from a prior
    # state loss or coins bought outside the sleeve). Without this the entry
    # price re-baselines to the current price EVERY tick, which permanently
    # disables the stop/take checks for that position.
    adopted = [p for p in positions if p.symbol not in st["positions"]]
    for p in adopted:
        st["positions"][p.symbol] = {
            "entry_price": p.entry_price, "notional": p.market_value,
            "opened": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "signal_mode": "adopted"}
        log(f"ADOPTED {p.symbol} qty={p.qty} @ {p.entry_price} (baseline set)")
    if adopted:
        save_state(st)

    if st.get("frozen"):
        suppressed_notify(st, "frozen", "action", "Crypto sleeve is FROZEN",
                          "The crypto sleeve hit its -$15 freeze rail and is halted flat. "
                          "It will not trade again until you review the ledger "
                          "(~/crypto_sleeve/state/closed_trades.csv) and delete the frozen "
                          "flag in sleeve_state.json.")
        save_state(st)
        log("idle: frozen")
        return

    sleeve_pl = realized_total() + sum(p.unrealized_pl for p in positions)
    if sleeve_pl <= CFG["freeze_pnl_usd"]:
        for p in positions:
            close_position(broker, p, st, "freeze_liquidation")
        st["frozen"] = True
        notify("action", "Crypto sleeve froze at its loss rail",
               f"Sleeve P/L hit {sleeve_pl:+.2f} (rail {CFG['freeze_pnl_usd']:+.0f}). "
               "All sleeve positions were liquidated and it is halted until you review "
               "it. Nothing else was touched.")
        save_state(st)
        return

    # ---- exits ----
    still_open = []
    for p in positions:
        try:
            chg = p.current_price / p.entry_price - 1
            if chg <= CFG["stop_pct"]:
                close_position(broker, p, st, "stop")
            elif chg >= CFG["take_pct"]:
                close_position(broker, p, st, "take_profit")
            elif momentum_ok(broker.closes(p.symbol)) is False:
                close_position(broker, p, st, "momentum_flip")
            else:
                still_open.append(p)
        except Exception as e:
            # One symbol's API hiccup must not block the other exits or the
            # rails. Treat it as still open; next tick re-reads real balances
            # (the adoption design), so even a half-completed close reconciles.
            log(f"exit check {p.symbol} FAIL {e!r}")
            still_open.append(p)

    # Cap accounting on COST BASIS, not market value: a drawdown must not
    # free up "room" to buy more (that would let total spend exceed the cap).
    deployed = sum(p.notional for p in still_open)
    held = {p.symbol for p in still_open}

    # ---- entries ----
    allowed, mode = whale_gate()
    try:
        cash_avail = broker.cash()
    except Exception as e:
        log(f"cash check FAIL {e!r} — skipping entries this tick")
        save_state(st)
        return
    room = CFG["cap_total_usd"] - deployed
    log(f"pl={sleeve_pl:+.2f} deployed={deployed:.2f} room={room:.2f} "
        f"cash={cash_avail:.2f} gate={mode}")

    if allowed:
        for sym in CFG["universe"]:
            if sym in held:
                continue
            size = min(CFG["per_trade_usd"], room, cash_avail - 0.5)
            if size < CFG["min_order_usd"]:
                break
            try:
                if momentum_ok(broker.closes(sym)) is not True:
                    continue
                price = broker.buy(sym, size)
            except Exception as e:
                # e.g. Kraken per-pair volume minimum (BTC ~$5.8, ETH ~$8.2)
                # or a funds race — skip this symbol, try the next; one
                # rejected order must never kill the whole tick.
                log(f"BUY {sym} ${size:.2f} FAIL {e!r}")
                continue
            st["positions"][sym] = {
                "entry_price": price, "notional": size,
                "opened": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "signal_mode": mode}
            save_state(st)   # persist before touching the next symbol — a
            room -= size     # later failure must not orphan this filled buy
            cash_avail -= size
            log(f"BUY {sym} ${size:.2f} @ ~{price} [{mode}]")
            notify("routine", "Crypto sleeve entry",
                   f"Bought ${size:.2f} of {sym} at about {price} ({mode}). "
                   f"Sleeve deployed ${deployed + size:.2f} of ${CFG['cap_total_usd']:.0f} cap.")

    save_state(st)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # keep launchd loop alive; surface persistent failures
        st = load_state()
        suppressed_notify(st, "engine_error", "action", "Crypto sleeve error",
                          f"The sleeve loop is failing with: {e}. It retries every 5 "
                          "minutes; if this repeats, check ~/crypto_sleeve/logs/.",
                          hours=24)
        save_state(st)
        log(f"ERROR {e}")
        raise
