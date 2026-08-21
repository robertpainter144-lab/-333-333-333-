#!/usr/bin/env python3
"""D39 live crypto sleeve — whale-gated momentum, broker-pluggable (Kraken/Alpaca).

Rails (DIRECTIVES.md D39/D58): cap + freeze thresholds live in config.json
(cap_total_usd; freeze + liquidate at sleeve P/L <= freeze_pnl_usd —
catastrophe-only, per-holding vol trails are the primary defense), per-trade
ledger in state/closed_trades.csv (D7-scoreable).
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
EQUITY_F = ROOT / "state" / "equity.csv"
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
    sleeve state; an untracked balance inherits current price as entry.

    Pair/asset codes for the configured universe are discovered from Kraken's
    public AssetPairs registry at startup, so widening the universe is a
    config-only change. The static seed map is the offline fallback and always
    wins for the symbols it covers (BTC/ETH/SOL keep working even if the
    registry fetch fails mid-outage)."""
    PAIR = {"BTC/USD": "XBTUSD", "ETH/USD": "ETHUSD", "SOL/USD": "SOLUSD"}
    ASSET = {"BTC/USD": ("XXBT", "XBT.F"), "ETH/USD": ("XETH", "ETH.F"),
             "SOL/USD": ("SOL", "SOL.F")}
    BASE = "https://api.kraken.com"

    def __init__(self):
        env = read_env(CFG.get("kraken_env_file", "~/kraken.env"))
        self.key = env.get("KRAKEN_API_KEY", "")
        self.sec = env.get("KRAKEN_API_SECRET", "")
        self.pair = dict(self.PAIR)
        self.asset = dict(self.ASSET)
        # Last-known-good registry from disk FIRST, so one slow AssetPairs
        # call can't forget symbols we already manage (ultra-audit 2026-08-21:
        # a discovery timeout on 8/18 KeyError-crashed the tick on XRP/USD
        # with half the book in discovered pairs).
        self._cache_f = ROOT / "state" / "pair_cache.json"
        try:
            c = json.loads(self._cache_f.read_text())
            self.pair.update(c.get("pair", {}))
            self.asset.update({k: tuple(v) for k, v in c.get("asset", {}).items()})
        except Exception:
            pass
        self._discover_pairs()
        try:
            self._cache_f.write_text(json.dumps(
                {"pair": self.pair, "asset": {k: list(v) for k, v in self.asset.items()}}))
        except Exception:
            pass

    def _discover_pairs(self) -> None:
        """Fill self.pair/self.asset for universe symbols the seed map doesn't
        cover, from Kraken's own registry. Failure leaves the seed map — the
        sleeve keeps managing whatever it already knows, never crashes here."""
        missing = [s for s in CFG["universe"] if s not in self.pair]
        if not missing:
            return
        try:
            reg = self._pub("/0/public/AssetPairs", {})
            by_alt = {v["altname"]: v["base"] for v in reg.values()
                      if v.get("status") == "online" and v.get("quote") in ("ZUSD", "USD")}
            for sym in missing:
                alt = sym.replace("/", "")            # "XRP/USD" -> "XRPUSD"
                if alt not in by_alt and alt == "DOGEUSD":
                    alt = "XDGUSD"                    # kraken legacy doge name
                base = by_alt.get(alt)
                if base is None:
                    log(f"universe symbol {sym}: no online Kraken USD pair — skipped")
                    continue
                # legacy X-prefix (XXBT/XETH/XXRP/XLTC/XXDG) -> clean code for the
                # '.F' auto-earn balance suffix; base itself stays as-is
                clean = base[1:] if len(base) > 3 and base[0] == "X" else base
                self.pair[sym] = alt
                self.asset[sym] = (base, f"{clean}.F")
        except Exception as e:
            log(f"pair discovery failed ({e!r}); static universe only")

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

    def transfers(self) -> list[dict]:
        """Deposit/withdrawal ledger entries (Robert 2026-08-20: the chart
        must transfer-adjust so deposits don't drown out trading movement).
        Returns [{ts_epoch, usd(+dep/-wd), refid}]; USD/ZUSD/USD.F assets
        only. Read-only Ledgers query — works on any key with Query funds."""
        try:
            res = self._priv("/0/private/Ledgers",
                             {"type": "deposit", "ofs": 0})
            res2 = self._priv("/0/private/Ledgers",
                              {"type": "withdrawal", "ofs": 0})
        except Exception as e:
            log(f"ledgers fetch FAIL {e!r}")
            return []
        out = []
        for res_d in (res, res2):
            for lid, e in (res_d.get("ledger") or {}).items():
                if e.get("asset") not in ("ZUSD", "USD", "USD.F"):
                    continue
                try:
                    amt = float(e.get("amount", 0))     # +deposit / -withdrawal
                    ts = float(e.get("time", 0))
                except (TypeError, ValueError):
                    continue
                if amt == 0 or ts == 0:
                    continue
                out.append({"ts": ts, "usd": round(amt, 2),
                            "refid": e.get("refid", lid)})
        out.sort(key=lambda x: x["ts"])
        return out

    def price(self, symbol: str) -> float:
        t = self._pub("/0/public/Ticker", {"pair": self.pair[symbol]})
        return float(next(iter(t.values()))["c"][0])

    def closes(self, symbol: str) -> list[float]:
        r = self._pub("/0/public/OHLC", {"pair": self.pair[symbol], "interval": 60})
        rows = next(v for k, v in r.items() if k != "last")
        return [float(x[4]) for x in rows]

    def cash(self) -> float:
        bal = self._priv("/0/private/Balance")
        return sum(float(bal.get(a, 0)) for a in ("ZUSD", "USD", "USD.F"))

    def positions(self, st: dict) -> list[Position]:
        bal = self._priv("/0/private/Balance")
        out = []
        for sym in CFG["universe"]:
            aliases = self.asset.get(sym)
            if aliases is None:   # unresolvable symbol degrades, never crashes
                log(f"positions: no Kraken mapping for {sym} this tick — skipped")
                continue
            qty = sum(float(bal.get(a, 0)) for a in aliases)
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
        r = self._priv("/0/private/AddOrder",
                       {"pair": self.pair[symbol], "type": "buy",
                        "ordertype": "market", "volume": f"{vol:.8f}"})
        # Prefer the ACTUAL average fill over the pre-trade quote — every
        # stop/trail/freeze downstream keys off this basis, and slippage on a
        # $500+ market order is no longer noise (ultra-audit 2026-08-21).
        try:
            txid = (r.get("txid") or [None])[0]
            if txid:
                time.sleep(1)
                o = (self._priv("/0/private/QueryOrders", {"txid": txid})
                     .get(txid) or {})
                fp = float(o.get("price") or 0)
                if fp > 0:
                    return fp
        except Exception:
            pass
        return price

    def close(self, pos: Position) -> None:
        # Sell only the SPOT-available balance: positions() sums spot + .F
        # (earn) aliases, but a market order can only touch spot — ordering
        # the summed qty rejects with EOrder:Insufficient funds and leaves
        # the position UNPROTECTED (found live 2026-08-20: ETH/SOL take-
        # profits failing every tick). Any Earn remainder is logged for a
        # manual unstake.
        try:
            bal = self._priv("/0/private/Balance")
            spot = float(bal.get(self.asset[pos.symbol][0], 0))
        except Exception:
            spot = pos.qty
        vol = min(pos.qty, spot) * 0.9995
        if vol * pos.current_price < 2:
            raise RuntimeError(
                f"{pos.symbol}: spot balance {spot:.8f} too small to sell — "
                f"remainder likely in Kraken Earn (.F); unstake to free it")
        self._priv("/0/private/AddOrder",
                   {"pair": self.pair[pos.symbol], "type": "sell",
                    "ordertype": "market", "volume": f"{vol:.8f}"})
        if spot < pos.qty * 0.99:
            log(f"{pos.symbol}: sold spot {vol:.8f}; ~{pos.qty - spot:.8f} "
                f"remains in Earn (.F) — unstake to fully exit")


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


def momentum_strength(closes: list[float]) -> float | None:
    """Leader score for tilt-weighted sizing (Robert 2026-08-20): trend
    (EMA-fast/slow spread) + recent 24h return. Higher = stronger leader.
    None when momentum_ok isn't a clean True, so only genuine leaders are
    sized up; a data outage never inflates a bet."""
    if momentum_ok(closes) is not True:
        return None
    es = ema(closes, CFG["ema_slow"])
    trend = (ema(closes, CFG["ema_fast"]) / es - 1) if es else 0.0
    day_ago = closes[-25] if len(closes) >= 25 else closes[0]
    recent = (closes[-1] / day_ago - 1) if day_ago else 0.0
    return max(0.0, trend + recent)


def tilt_size(strength: float, strongest: float) -> float:
    """Base per_trade scaled 0.5x (weakest passing) → 2.5x-ish (the
    strongest leader), clamped to [min_order, tilt_max_usd]."""
    base = CFG["per_trade_usd"]
    mult = 0.5 + 2.0 * (strength / strongest if strongest > 0 else 1.0)
    return min(CFG.get("tilt_max_usd", base * 2), base * mult)


def dollar_vol_24h(broker, symbol: str) -> float:
    """Kraken 24h $-volume for the pair (base volume x vwap). Returns 0 on
    any failure — the NEW-entry screen fails CLOSED (Robert 2026-08-21: 'be
    more careful with low volume stuff like pepe'); held positions are never
    screened, only fresh names."""
    try:
        r = broker._pub("/0/public/Ticker", {"pair": broker.pair[symbol]})
        t = next(iter(r.values()))
        return float(t["v"][1]) * float(t["p"][1])
    except Exception:
        return 0.0


def add_to_position(st: dict, symbol: str, add_usd: float, price: float) -> float:
    """Grow a HELD position by add_usd at `price`, blending the entry price
    on cost basis (Robert 2026-08-20 concentrated_add). Returns new notional."""
    row = st["positions"].get(symbol, {})
    old_n = float(row.get("notional", 0.0))
    old_e = float(row.get("entry_price", price)) or price
    qty_old = old_n / old_e if old_e else 0.0
    qty_new = add_usd / price if price else 0.0
    new_n = round(old_n + add_usd, 4)
    new_e = round(new_n / (qty_old + qty_new), 8) if (qty_old + qty_new) else price
    row["notional"] = new_n
    row["entry_price"] = new_e
    st["positions"][symbol] = row
    return new_n


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


def trail_width(broker, symbol: str) -> float:
    """Per-asset trailing-stop width from ITS OWN recent volatility (Robert
    2026-08-20: 'a safe trailing stop that makes sense for each individual
    holding analyzed separately'): stdev of the last 48 hourly returns,
    scaled to a day, times a multiplier, clamped. BTC lands ~5-7%; high-beta
    alts 12-18%. Data failure = widest (safest against whipsaw exits)."""
    try:
        closes = broker.closes(symbol)[-49:]
        rets = [closes[i + 1] / closes[i] - 1
                for i in range(len(closes) - 1) if closes[i]]
        if len(rets) < 12:
            return float(CFG.get("trail_max", 0.18))
        import statistics
        w = (float(CFG.get("trail_vol_mult", 2.5))
             * statistics.pstdev(rets) * (24 ** 0.5))
        return max(float(CFG.get("trail_min", 0.05)),
                   min(float(CFG.get("trail_max", 0.18)), w))
    except Exception:
        return float(CFG.get("trail_max", 0.18))


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
    # Exits page in plain English (Robert 2026-08-21 "let me know if anything
    # changes overnight") — a stop rotating a coin out IS the change he means.
    _why = {"trail_stop": "its trailing stop fired (profit-protecting rotation)",
            "stop": "its hard -8% stop hit",
            "take_profit": "it hit the take-profit level",
            "momentum_flip": "its momentum turned down",
            "freeze_liquidation": "the book-level freeze rail liquidated it"}
    _r = next((v for k, v in _why.items() if reason.startswith(k)), reason)
    notify("action", f"Crypto exit: {pos.symbol.replace('/USD','')} "
           f"{pos.unrealized_pl:+.2f}",
           f"Sold {pos.symbol} for {pos.unrealized_pl:+.2f} because {_r}. "
           "Freed cash redeploys into the remaining winners automatically. "
           "Nothing you need to do.")


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

    # Refresh the deposit/withdrawal ledger for the chart's transfer
    # adjustment (Robert 2026-08-20). Best-effort — a ledger hiccup never
    # blocks trading. Written to state/transfers.json for /api/crypto/summary.
    if hasattr(broker, "transfers"):
        try:
            tfs = broker.transfers()
            if tfs:
                (ROOT / "state" / "transfers.json").write_text(
                    json.dumps({"ts": int(time.time()), "transfers": tfs}))
        except Exception as e:
            log(f"transfers refresh FAIL {e!r}")

    if not st.get("seen_active"):
        st["seen_active"] = True
        notify("info", "Crypto sleeve is LIVE",
               f"The sleeve is connected to {CFG.get('broker', 'kraken')} and active. "
               "It buys when momentum/whale signals line up and cash is available; "
               "entries get logged silently, rails page you.")

    try:
        positions = broker.positions(st)
        if st.pop("unprotected_ticks", None):
            save_state(st)
    except Exception as e:
        # A dead positions fetch means NO stop/trail/freeze ran this tick.
        # Track the streak and escalate in plain English once it's real
        # (ultra-audit 2026-08-21: four outage windows on 8/16 paged nothing).
        st["unprotected_ticks"] = int(st.get("unprotected_ticks", 0)) + 1
        n = st["unprotected_ticks"]
        if n >= 3:
            suppressed_notify(st, "unprotected", "action",
                              "Crypto positions UNPROTECTED — Kraken unreachable",
                              f"The sleeve has failed to read balances for {n} straight "
                              f"ticks (~{n*5} min). No stops or freeze checks are running "
                              "on the crypto book. If Kraken is up for you, check the Mac's "
                              "network; otherwise watch the positions by hand.", hours=2)
        save_state(st)
        log(f"positions fetch FAIL ({n} straight): {e!r}")
        return

    # Persist adopted positions (balances with no state entry — from a prior
    # state loss or coins bought outside the sleeve). Without this the entry
    # price re-baselines to the current price EVERY tick, which permanently
    # disables the stop/take checks for that position.
    adopted = [] if st.get("frozen") else \
        [p for p in positions if p.symbol not in st["positions"]]
    for p in adopted:
        st["positions"][p.symbol] = {
            "entry_price": p.entry_price, "notional": p.market_value,
            "opened": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "signal_mode": "adopted"}
        log(f"ADOPTED {p.symbol} qty={p.qty} @ {p.entry_price} (baseline set)")
    if adopted:
        save_state(st)

    # Reverse reconciliation (ultra-audit 2026-08-21): if state tracks a
    # position the exchange no longer shows (a close whose HTTP response was
    # lost after the fill, or a balance drained outside the sleeve), record
    # it as closed instead of leaving a phantom entry that concentrated_add
    # could blend fresh money against at a stale cost basis.
    _live_syms = {p.symbol for p in positions}
    for _sym in list(st["positions"].keys()):
        if _sym not in _live_syms:
            _meta = st["positions"].pop(_sym)
            try:
                _xp = broker.price(_sym)
            except Exception:
                _xp = float(_meta.get("entry_price") or 0)
            _n = float(_meta.get("notional") or 0)
            _e = float(_meta.get("entry_price") or 0) or _xp
            _pl = (_xp / _e - 1) * _n if _e else 0.0
            append_ledger({"closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "symbol": _sym, "entry_time": _meta.get("opened", ""),
                           "entry_price": f"{_e:.4f}", "exit_price": f"{_xp:.4f}",
                           "notional": f"{_n:.2f}", "realized_pl": f"{_pl:.4f}",
                           "pl_pct": f"{_pl / _n:.4f}" if _n else "0",
                           "reason": "reconciled_close",
                           "signal_mode": _meta.get("signal_mode", "?")})
            st.get("trail_peaks", {}).pop(_sym, None)
            st.get("trails", {}).pop(_sym, None)
            save_state(st)
            log(f"RECONCILED {_sym}: state had a position, exchange shows none "
                f"— recorded as closed (~{_pl:+.2f})")

    if st.get("frozen"):
        suppressed_notify(st, "frozen", "action", "Crypto sleeve is FROZEN",
                          f"The crypto sleeve hit its {CFG['freeze_pnl_usd']:+.0f} freeze "
                          "rail and is halted. It will not trade again until you review "
                          "the ledger (~/crypto_sleeve/state/closed_trades.csv) and delete "
                          "the frozen flag in sleeve_state.json.")
        save_state(st)
        log("idle: frozen")
        return

    sleeve_pl = realized_total() + sum(p.unrealized_pl for p in positions)
    if sleeve_pl <= CFG["freeze_pnl_usd"]:
        # Fault-isolated liquidation (ultra-audit 2026-08-21: a single failed
        # close — e.g. an Earn-locked remainder — must not abort the halt,
        # leave 'frozen' unset, and strand the rest of the book unprotected).
        failed = []
        for p in positions:
            try:
                close_position(broker, p, st, "freeze_liquidation")
            except Exception as e:
                failed.append(p.symbol)
                log(f"freeze close {p.symbol} FAIL {e!r}")
        st["frozen"] = True
        st["trail_peaks"] = {}
        st["trails"] = {}
        save_state(st)
        if failed:
            notify("critical", "Crypto sleeve froze — SOME POSITIONS DID NOT SELL",
                   f"Sleeve P/L hit {sleeve_pl:+.2f} (rail {CFG['freeze_pnl_usd']:+.0f}). "
                   f"Liquidation FAILED for: {', '.join(failed)} — likely Kraken Earn "
                   "(.F) balances a market order cannot touch. Sell or unstake these "
                   "on kraken.com NOW; the sleeve is halted and cannot protect them.")
        else:
            notify("action", "Crypto sleeve froze at its loss rail",
                   f"Sleeve P/L hit {sleeve_pl:+.2f} (rail {CFG['freeze_pnl_usd']:+.0f}). "
                   "All sleeve positions were liquidated and it is halted until you review "
                   "it. Nothing else was touched.")
        return

    # ---- exits ----
    # Per-holding TRAILING stops (Robert 2026-08-20, D41 amendment: keep the
    # upward momentum, protect each winner by its own volatility) — replaces
    # the fixed take_profit as the primary upside exit; the hard stop_pct
    # still guards below-entry moves before the trail arms.
    peaks = st.setdefault("trail_peaks", {})
    still_open = []
    for p in positions:
        try:
            cur = p.current_price
            peaks[p.symbol] = max(peaks.get(p.symbol, 0.0), cur, p.entry_price)
            peak = peaks[p.symbol]
            chg = cur / p.entry_price - 1
            trail = trail_width(broker, p.symbol)
            armed = peak >= p.entry_price * (1 + float(CFG.get("trail_arm_pct", 0.02)))
            if chg <= CFG["stop_pct"]:
                close_position(broker, p, st, "stop")
                peaks.pop(p.symbol, None)
            elif armed and cur <= peak * (1 - trail):
                close_position(broker, p, st,
                               f"trail_stop({trail * 100:.1f}%_off_{peak:.6g})")
                # Peak-sell → dip-rebuy (Robert 2026-08-21: "exiting before
                # the dips ... targeting dips for buys"): reserve these
                # proceeds for a cheaper re-entry on the SAME coin.
                st.setdefault("reentry", {})[p.symbol] = {
                    "exit_px": cur, "usd": round(p.market_value, 2),
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                peaks.pop(p.symbol, None)
            elif chg >= CFG["take_pct"]:
                close_position(broker, p, st, "take_profit")
                st.setdefault("reentry", {})[p.symbol] = {
                    "exit_px": cur, "usd": round(p.market_value, 2),
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                peaks.pop(p.symbol, None)
            elif momentum_ok(broker.closes(p.symbol)) is False:
                close_position(broker, p, st, "momentum_flip")
                peaks.pop(p.symbol, None)
            else:
                still_open.append(p)
                # Surface the case-by-case protection (Robert 2026-08-21:
                # per-holding vol-adaptive stops ARE the defense, not a stiff
                # floor) so the app can show each coin's live trail + stop px.
                st.setdefault("trails", {})[p.symbol] = {
                    "width_pct": round(trail * 100, 2),
                    "peak": peak,
                    "stop_px": round(peak * (1 - trail), 8),
                    "armed": bool(armed),
                }
        except Exception as e:
            # One symbol's API hiccup must not block the other exits or the
            # rails. Treat it as still open; next tick re-reads real balances
            # (the adoption design), so even a half-completed close reconciles.
            log(f"exit check {p.symbol} FAIL {e!r}")
            still_open.append(p)
    # prune trails AND trail_peaks for anything no longer open — a stale
    # peak surviving a close causes an instant false stop on re-entry
    # (ultra-audit 2026-08-21: 9 orphaned peaks found live)
    _open_syms = {p.symbol for p in still_open}
    for _d in ("trails", "trail_peaks"):
        if isinstance(st.get(_d), dict):
            for _s in list(st[_d]):
                if _s not in _open_syms:
                    st[_d].pop(_s, None)

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

    # Display-only: live marks per position + what the engine WANTS this
    # tick (kept positions + momentum picks up to the cap, ignoring cash)
    # — feeds the Terminal's allocation bars, mirroring the Book page's
    # holdings-vs-target pair. Never read back by trading logic.
    try:
        for p in still_open:
            row = st["positions"].get(p.symbol)
            if row is not None:
                row["mv"] = round(p.market_value, 2)
                row["cur"] = p.current_price
        tgt = {p.symbol: round(p.notional, 2) for p in still_open}
        t_room = CFG["cap_total_usd"] - sum(tgt.values())
        if allowed:
            # mirror the momentum-weighted buy order for the target bar
            _scored = []
            for sym in CFG["universe"]:
                if sym in tgt:
                    continue
                try:
                    s = momentum_strength(broker.closes(sym))
                except Exception:
                    s = None
                if s is not None and s > 0:
                    _scored.append((s, sym))
            _scored.sort(reverse=True)
            _strong = _scored[0][0] if _scored else 1.0
            for s, sym in _scored:
                if t_room < CFG["min_order_usd"]:
                    break
                want = round(min(tilt_size(s, _strong) if CFG.get("momentum_weighted")
                                 else CFG["per_trade_usd"], t_room), 2)
                if want < CFG["min_order_usd"]:
                    continue
                tgt[sym] = want
                t_room -= want
        st["targets"] = tgt
        st["targets_ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        st["targets_gate"] = mode
        save_state(st)
    except Exception as e:
        log(f"targets calc FAIL {e!r}")

    bought = 0.0
    _max_pos = int(CFG.get("max_positions", 999))

    # DIP-BUY RE-ENTRIES (Robert 2026-08-21: "targeting dips for buys").
    # A trail/take exit sold near a peak; its proceeds are RESERVED for up
    # to reentry_ttl_h, waiting for that same coin to dip a vol-scaled
    # discount below the exit price. Dip fills -> rebuy cheaper than we
    # sold. No dip before the TTL (price ran away) -> reserve releases to
    # the concentrated adds below. Trend-break exits (hard stop / momentum
    # flip) never dip-buy — they wait for fresh momentum like any new name.
    reserved = 0.0
    _reentry = st.setdefault("reentry", {})
    for _sym in list(_reentry.keys()):
        r = _reentry[_sym]
        try:
            _age_h = (datetime.now(timezone.utc)
                      - datetime.fromisoformat(r["ts"])).total_seconds() / 3600
        except Exception:
            _age_h = 999
        if _sym in held or _age_h > float(CFG.get("reentry_ttl_h", 24)):
            _reentry.pop(_sym)
            save_state(st)
            continue
        try:
            _cur = broker.price(_sym)
            _dip = max(0.015, trail_width(broker, _sym) / 3.0)
        except Exception:
            reserved += float(r.get("usd") or 0)
            continue
        _want = round(min(float(r.get("usd") or 0), room, cash_avail - 0.5), 2)
        if (allowed and len(st.get("positions") or {}) < _max_pos
                and _cur <= float(r["exit_px"]) * (1 - _dip)
                and _want >= CFG["min_order_usd"]
                and momentum_ok(broker.closes(_sym)) is not False):
            try:
                _px = broker.buy(_sym, _want)
            except Exception as e:
                log(f"DIPBUY {_sym} ${_want:.2f} FAIL {e!r}")
                reserved += float(r.get("usd") or 0)
                continue
            st["positions"][_sym] = {
                "entry_price": _px, "notional": _want,
                "opened": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "signal_mode": "dip_rebuy"}
            st.setdefault("trail_peaks", {})[_sym] = _px
            _reentry.pop(_sym)
            save_state(st)
            room -= _want
            cash_avail -= _want
            bought += _want
            held.add(_sym)
            log(f"DIPBUY {_sym} ${_want:.2f} @ ~{_px} "
                f"({(1 - _px / float(r['exit_px'])) * 100:.1f}% below the exit)")
            notify("action", f"Dip rebuy: {_sym.replace('/USD','')}",
                   f"Bought back ${_want:.2f} of {_sym} at about {_px} — "
                   f"{(1 - _px / float(r['exit_px'])) * 100:.1f}% cheaper than we "
                   "sold it. Sell-the-peak, buy-the-dip rotation working as asked.")
        else:
            reserved += float(r.get("usd") or 0)
    if reserved > 0:
        cash_avail -= reserved   # dip reserves are not spendable this tick
        log(f"dip-buy reserve holding ${reserved:.2f} "
            f"for {', '.join(_reentry.keys())}")

    # CONCENTRATED ADD (Robert 2026-08-20 "redeploy freed cash into winners"):
    # when the book already holds winners, grow THEM with any freed cash —
    # tilt-weighted by momentum, up to each position's tilt_max — instead of
    # opening a new (possibly meme) name. Keeps the concentration intact when
    # a trailing stop frees capital. Falls through to the (liquidity-screened)
    # open-new loop only when a slot is free AND the adds deployed nothing —
    # so freed cash can't strand once the survivors hit their tilt caps.
    if (allowed and CFG.get("concentrated_add") and st.get("positions")
            and cash_avail - 0.5 >= CFG["min_order_usd"]):
        pos_cap = CFG.get("tilt_max_usd", CFG["per_trade_usd"] * 2)
        held_scored = []
        for sym in list(st["positions"].keys()):
            try:
                s = momentum_strength(broker.closes(sym))
            except Exception:
                s = None
            # None = data gap (momentum-flip exits already culled true losers
            # this tick) — skip rather than default to max-multiplier sizing
            if s is not None and s > 0:
                held_scored.append((s, sym))
        held_scored.sort(reverse=True)
        strongest = held_scored[0][0] if held_scored else 1.0
        for s, sym in held_scored:
            if room < CFG["min_order_usd"] or cash_avail - 0.5 < CFG["min_order_usd"]:
                break
            cur_n = float(st["positions"][sym].get("notional", 0.0))
            headroom = pos_cap - cur_n
            if headroom < CFG["min_order_usd"]:
                continue
            size = round(min(tilt_size(s, strongest), headroom,
                             room, cash_avail - 0.5), 2)
            if size < CFG["min_order_usd"]:
                continue
            try:
                price = broker.buy(sym, size)
            except Exception as e:
                log(f"ADD {sym} ${size:.2f} FAIL {e!r}")
                continue
            new_n = add_to_position(st, sym, size, price)
            save_state(st)
            room -= size
            cash_avail -= size
            bought += size
            log(f"ADD {sym} ${size:.2f} @ ~{price} -> ${new_n:.2f} [concentrated]")
            notify("routine", "Crypto sleeve — grew a winner",
                   f"Added ${size:.2f} to {sym} (freed cash redeployed into a "
                   f"held winner, now ${new_n:.2f}). Momentum-weighted.")
        if bought > 0 or len(st.get("positions") or {}) >= _max_pos:
            allowed = False   # grew winners (or book full) — no new names

    # Position cap (Robert 2026-08-20): a concentrated winners book must not
    # be re-sprayed into new names every time cash frees — once the sleeve
    # holds max_positions coins, the buy loop opens no new ones (freed cash
    # sits until a slot opens or is manually deployed into a held winner).
    if allowed and len(st.get("positions") or {}) >= _max_pos:
        allowed = False
        log(f"position cap: holding {len(st['positions'])}/{_max_pos} — no new entries")
    if allowed:
        # Momentum-weighted (Robert 2026-08-20 "tilt toward the leaders"):
        # rank eligible non-held coins by strength, fund the STRONGEST first
        # and LARGER. Falls back to flat equal-weight in universe order when
        # momentum_weighted is off.
        if CFG.get("momentum_weighted"):
            scored = []
            for sym in CFG["universe"]:
                if sym in held:
                    continue
                try:
                    s = momentum_strength(broker.closes(sym))
                except Exception:
                    s = None
                if s is not None and s > 0:
                    scored.append((s, sym))
            scored.sort(reverse=True)   # strongest leader first
            strongest = scored[0][0] if scored else 1.0
            order = [(sym, tilt_size(s, strongest)) for s, sym in scored]
        else:
            order = [(sym, CFG["per_trade_usd"]) for sym in CFG["universe"]
                     if sym not in held]

        _min_dvol = float(CFG.get("min_dollar_vol_24h", 20e6))
        for sym, want in order:
            # Running cap check (ultra-audit 2026-08-21): a cold-start tick
            # must not open more than max_positions names in one pass.
            if len(st.get("positions") or {}) >= _max_pos:
                log(f"position cap reached mid-tick ({_max_pos}) — done opening")
                break
            size = round(min(want, room, cash_avail - 0.5), 2)
            if size < CFG["min_order_usd"]:
                # room/cash exhausted, or this leader's slot is too small —
                # in ranked mode the strong ones came first, so break
                if room < CFG["min_order_usd"] or cash_avail - 0.5 < CFG["min_order_usd"]:
                    break
                continue
            # Liquidity screen for NEW names only (Robert 2026-08-21: "be
            # more careful with low volume stuff like pepe") — fails closed.
            dvol = dollar_vol_24h(broker, sym)
            if dvol < _min_dvol:
                log(f"BUY {sym} skipped: 24h $vol ${dvol/1e6:.1f}M < "
                    f"${_min_dvol/1e6:.0f}M liquidity floor")
                continue
            try:
                # momentum already confirmed for ranked mode; re-check in flat
                if not CFG.get("momentum_weighted") and \
                        momentum_ok(broker.closes(sym)) is not True:
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
            # Fresh entry = fresh peak. A stale trail_peak from a PRIOR hold
            # of this symbol would arm instantly and stop the new position
            # out on tick one (ultra-audit 2026-08-21: 9 orphaned peaks live).
            st.setdefault("trail_peaks", {})[sym] = price
            save_state(st)   # persist before touching the next symbol — a
            room -= size     # later failure must not orphan this filled buy
            cash_avail -= size
            bought += size
            log(f"BUY {sym} ${size:.2f} @ ~{price} [{mode}]")
            notify("routine", "Crypto sleeve entry",
                   f"Bought ${size:.2f} of {sym} at about {price} ({mode}). "
                   f"Sleeve deployed ${deployed + size:.2f} of ${CFG['cap_total_usd']:.0f} cap.")

    # Equity curve for the dashboard (owner-only /api/equity/crypto_sleeve).
    # New buys are valued at notional this tick; next tick reprices them.
    equity = cash_avail + sum(p.market_value for p in still_open) + bought
    new_f = not EQUITY_F.exists()
    with EQUITY_F.open("a") as f:
        if new_f:
            f.write("ts,equity,cash,positions_mv,realized\n")
        f.write("%s,%.4f,%.4f,%.4f,%.4f\n"
                % (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   equity, cash_avail,
                   sum(p.market_value for p in still_open) + bought,
                   realized_total()))

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
