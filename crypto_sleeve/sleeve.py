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
from datetime import datetime, timedelta, timezone
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
        # Check BOTH maps (2026-08-22: a cache with a 'pair' entry but no
        # matching 'asset' entry left new rotations unreconcilable — the
        # engine dropped ZEC/HYPE from the book and mismarked equity. A
        # half-populated symbol must re-discover, not be treated as known.)
        missing = [s for s in CFG["universe"]
                   if s not in self.pair or s not in self.asset]
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
        params["nonce"] = str(int(time.time() * 1000000))  # us-nonce (2026-08-23: ms watermark poisoned)
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
        # held symbols stay visible even when the auto-universe rebuild
        # fails back to the static seed (2026-08-23: an SSL timeout hid
        # screened-in ZEC from this list and reverse reconciliation booked
        # a phantom close while 0.97 ZEC sat on Kraken)
        syms = list(dict.fromkeys(
            list(CFG["universe"]) + list((st.get("positions") or {}).keys())))
        for sym in syms:
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

    def place_stop(self, symbol: str, vol: float, price: float) -> str:
        """Resting stop-loss SELL at the exchange — survives this machine
        (2026-08-21 power-outage postmortem). Returns the order txid.
        Kraken enforces per-pair price decimals (ETH 2, SOL 2, AVAX 3,
        XRP 5...); on an 'up to N decimals' reject, retry rounded to N."""
        px = f"{price:.6g}"
        for _ in range(2):
            try:
                r = self._priv("/0/private/AddOrder",
                               {"pair": self.pair[symbol], "type": "sell",
                                "ordertype": "stop-loss", "price": px,
                                "volume": f"{vol:.8f}"})
                return (r.get("txid") or [""])[0]
            except RuntimeError as e:
                import re as _re
                m = _re.search(r"up to (\d+) decimals", str(e))
                if not m:
                    raise
                px = f"{price:.{int(m.group(1))}f}"
        raise RuntimeError(f"{symbol}: stop price {px} still rejected")

    def cancel_order(self, txid: str) -> None:
        if not txid:
            return
        try:
            self._priv("/0/private/CancelOrder", {"txid": txid})
        except Exception as e:
            if "Unknown order" not in str(e):   # already gone = fine
                raise


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


def is_meme(symbol: str) -> bool:
    base = str(symbol).split("/")[0].upper()
    return base in {str(x).upper() for x in CFG.get("meme_blocklist", [])}


def meme_room(st: dict) -> tuple:
    """(usd_headroom, slots_open) under the meme exposure cap (Robert
    2026-08-22: 'just limiting them in order to mitigate downside is
    smart' — no exclusion, a budget: max_meme_positions slots and
    meme_cap_pct of the cap in total meme notional)."""
    cap = float(CFG.get("cap_total_usd", 0)) * float(CFG.get("meme_cap_pct", 0.25))
    used = 0.0
    count = 0
    for sym, meta in (st.get("positions") or {}).items():
        if is_meme(sym):
            used += float(meta.get("notional") or 0)
            count += 1
    return max(0.0, cap - used), max(0, int(CFG.get("max_meme_positions", 1)) - count)


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
    # keep the display mark self-consistent IMMEDIATELY (review 2026-08-22:
    # mv/cur lagging a fresh add made the fine recorder undercount the leg
    # until the next 5-min mark — visible chart dips during rotations)
    row["mv"] = round(float(row.get("mv") or old_n) + add_usd, 2)
    row["cur"] = price
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


def trail_width(broker, symbol: str, st: dict | None = None) -> float:
    """Per-asset trailing-stop width from ITS OWN recent volatility (Robert
    2026-08-20: 'a safe trailing stop that makes sense for each individual
    holding analyzed separately'): stdev of the last 48 hourly returns,
    scaled to a day, times a multiplier, clamped. Data failure = widest
    (safest against whipsaw exits).

    POST-SPIKE TIGHTENING (Robert 2026-08-22 00:26, after the 05:05Z blow-
    off): a 3-sigma rip inflates the vol estimate and WIDENS the trail at
    exactly the moment reversal odds peak — backwards. For spike_tighten_h
    hours after a spike-harvest fires on this coin, the width is cut by
    spike_tighten_factor (still floored at trail_min). Pass st to enable."""
    try:
        closes = broker.closes(symbol)[-49:]
        rets = [closes[i + 1] / closes[i] - 1
                for i in range(len(closes) - 1) if closes[i]]
        if len(rets) < 12:
            return float(CFG.get("trail_max", 0.18))
        import statistics
        w = (float(CFG.get("trail_vol_mult", 2.5))
             * statistics.pstdev(rets) * (24 ** 0.5))
        w = max(float(CFG.get("trail_min", 0.05)),
                min(float(CFG.get("trail_max", 0.18)), w))
        if st:
            try:
                if st.get("sentinel_until") and datetime.fromisoformat(
                        st["sentinel_until"]) > datetime.now(timezone.utc):
                    return float(CFG.get("trail_min", 0.05))
            except Exception:
                pass
            _sts = (st.get("spike_ts") or {}).get(symbol)
            if _sts:
                _age_h = (datetime.now(timezone.utc)
                          - datetime.fromisoformat(_sts)).total_seconds() / 3600
                if _age_h <= float(CFG.get("spike_tighten_h", 2.0)):
                    w = max(float(CFG.get("trail_min", 0.05)),
                            w * float(CFG.get("spike_tighten_factor", 0.5)))
        return w
    except Exception:
        return float(CFG.get("trail_max", 0.18))


def sync_exchange_stops(broker, st: dict, still_open) -> None:
    """Dead-man protection (2026-08-21 power-outage postmortem: the Mac was
    dark 4h16m with $3.1k soft-stopped only): keep a REAL resting stop-loss
    order at Kraken for every open position, tracking the engine's effective
    stop (hard -8%, or the armed trail if higher). If this machine dies, the
    EXCHANGE enforces the exits; reverse reconciliation books any fills on
    revival. The engine cancels a position's resting stop before its own
    market exits (a working sell order reserves the balance)."""
    if not hasattr(broker, "place_stop"):
        return
    ex = st.setdefault("ex_stops", {})
    open_syms = {p.symbol for p in still_open}
    for sym in list(ex.keys()):
        if sym not in open_syms:
            try:
                broker.cancel_order(ex[sym].get("txid"))
            except Exception:
                pass
            ex.pop(sym, None)
    for p in still_open:
        try:
            eff = p.entry_price * (1 + float(CFG["stop_pct"]))
            tr = (st.get("trails") or {}).get(p.symbol) or {}
            if tr.get("armed") and tr.get("stop_px"):
                eff = max(eff, float(tr["stop_px"]))
            vol = p.qty * 0.9995
            cur = ex.get(p.symbol)
            if cur and abs(eff - float(cur["stop_px"])) / max(eff, 1e-9) < 0.004 \
                    and abs(vol - float(cur["vol"])) / max(vol, 1e-9) < 0.01:
                continue   # close enough — don't churn cancel/replace
            if cur:
                try:
                    broker.cancel_order(cur.get("txid"))
                except Exception:
                    pass
            txid = broker.place_stop(p.symbol, vol, eff)
            ex[p.symbol] = {"txid": txid, "stop_px": round(eff, 8),
                            "vol": round(vol, 8)}
            log(f"EXCH STOP {p.symbol}: resting stop-loss @ {eff:.6g} "
                f"(vol {vol:.6g}) — enforced by Kraken, survives this machine")
        except Exception as e:
            log(f"exch stop {p.symbol} FAIL {e!r}")
    save_state(st)
    if ex and not st.get("ex_stops_announced"):
        st["ex_stops_announced"] = True
        save_state(st)
        notify("info", "Crypto book now protected AT the exchange",
               "Every open position carries a real resting stop-loss order at "
               "Kraken, tracking the engine's levels. If the Mac loses power "
               "again, Kraken itself enforces the exits — the book is no "
               "longer dependent on this machine for downside protection.")


def note_stop_close(st: dict, reason: str) -> None:
    """Track stop-family exits for CASCADE detection (Robert 2026-08-22:
    'if you sold everything else at that point we would've made a killing'
    — multiple stops firing within minutes is itself the exit signal)."""
    # reconciled_close EXCLUDED (2026-08-22 incident: reconciliation
    # mismatches are bookkeeping, not market crashes — counting them let a
    # mapping bug fire a false cascade that flattened the whole book)
    if not (reason.startswith("trail_stop") or reason == "stop"):
        return
    lst = st.setdefault("recent_stop_closes", [])
    lst.append(datetime.now(timezone.utc).isoformat(timespec="seconds"))
    del lst[:-8]


def close_position(broker, pos: Position, st: dict, reason: str) -> None:
    # cancel the resting exchange stop FIRST — its open sell order reserves
    # the balance and would make the market close reject
    _ex = (st.get("ex_stops") or {}).pop(pos.symbol, None)
    if _ex and hasattr(broker, "cancel_order"):
        try:
            broker.cancel_order(_ex.get("txid"))
        except Exception as e:
            log(f"cancel resting stop {pos.symbol} FAIL {e!r}")
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
    note_stop_close(st, reason)
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


def auto_universe(broker, st: dict) -> None:
    """Exchange-wide spike detection (Robert 2026-08-22: "not missing any
    spikes across the whole crypto industry"). Batch Ticker calls screen
    EVERY online Kraken USD pair; this tick's working universe becomes
    seed-config + held + top 24h gainers + top dollar-volume, all above the
    min_dollar_vol_24h floor. Detection is whole-exchange while the
    per-symbol OHLC momentum scan stays bounded (universe_max). Any failure
    leaves the static seed universe - never blocks the tick."""
    if not CFG.get("universe_auto") or not hasattr(broker, "_pub"):
        return
    try:
        seed = list(CFG.get("_universe_seed") or CFG["universe"])
        CFG["_universe_seed"] = seed   # adoption guard reads this (F5)
        cache_f = ROOT / "state" / "assetpairs_cache.json"
        codes = None
        try:
            c = json.loads(cache_f.read_text())
            if time.time() - c["ts"] < float(CFG.get("universe_refresh_h", 6)) * 3600:
                codes = c["codes"]
        except Exception:
            codes = None
        if codes is None:
            reg = broker._pub("/0/public/AssetPairs", {})
            codes = {}
            # Legacy aliases (XBT=BTC, XDG=DOGE) would double-adopt coins
            # the seed already manages (bit us live 2026-08-22: XDG/USD
            # phantom-adopted the DOGE balance); fiat/stables are high-dvol
            # noise that can never "spike".
            _skip = {"XBT", "XDG", "EUR", "GBP", "AUD", "CAD", "CHF", "JPY",
                     "USDT", "USDC", "DAI", "TUSD", "PYUSD", "EURT", "USDS",
                     "USDG", "EURR", "RLUSD", "USDQ", "USTS", "EURQ"}
            for code, meta in reg.items():
                ws = meta.get("wsname") or ""
                if (meta.get("status") == "online" and ws.endswith("/USD")
                        and meta.get("quote") in ("ZUSD", "USD")
                        and "." not in code
                        and ws.split("/")[0] not in _skip):
                    codes[ws] = code
            cache_f.write_text(json.dumps({"ts": time.time(), "codes": codes}))
        if not codes:
            return
        # belt-and-suspenders: apply the same skip to CACHED code lists
        _skip2 = {"XBT", "XDG", "EUR", "GBP", "AUD", "CAD", "CHF", "JPY",
                  "USDT", "USDC", "DAI", "TUSD", "PYUSD", "EURT", "USDS",
                  "USDG", "EURR", "RLUSD", "USDQ", "USTS", "EURQ"}
        codes = {w: c for w, c in codes.items() if w.split("/")[0] not in _skip2}
        res = {}
        vals = list(codes.values())
        for i in range(0, len(vals), 100):
            res.update(broker._pub("/0/public/Ticker",
                                   {"pair": ",".join(vals[i:i + 100])}))
        floor = float(CFG.get("min_dollar_vol_24h", 2e7))
        rows = []
        for ws, code in codes.items():
            t = res.get(code)
            if not t:
                continue
            try:
                last = float(t["c"][0])
                op = float(t["o"])
                dvol = float(t["v"][1]) * float(t["p"][1])
            except Exception:
                continue
            if dvol < floor or last <= 0 or op <= 0:
                continue
            rows.append((last / op - 1.0, dvol, ws))
        gain = [ws for _, _, ws in sorted(rows, key=lambda r: -r[0])
                [:int(CFG.get("universe_top_gainers", 15))]]
        big = [ws for _, _, ws in sorted(rows, key=lambda r: -r[1])
               [:int(CFG.get("universe_top_dvol", 8))]]
        held = list((st.get("positions") or {}).keys())
        merged, seen = [], set()
        for ws in seed + held + gain + big:  # seed+held first: never truncated
            if ws not in seen:
                seen.add(ws)
                merged.append(ws)
        cap = int(CFG.get("universe_max", 42))
        CFG["universe"] = merged[:max(cap, len(seed) + len(held))]
        # Screener Radar (Robert 2026-08-22 "watch for profit opportunities
        # across the whole industry"): persist the top movers this screen
        # found so the app can show what the machine is watching — owning
        # dust adds nothing, seeing the radar adds everything.
        try:
            _top = sorted(rows, key=lambda r: -r[0])[:12]
            (ROOT / "state" / "radar.json").write_text(json.dumps({
                "ts": int(time.time()),
                "rows": [{"sym": ws, "gain": round(g * 100, 2),
                          "dvol_m": round(d / 1e6, 1),
                          "watched": ws in set(CFG["universe"]),
                          "held": ws in held} for g, d, ws in _top]}))
        except Exception:
            pass
        added = [x for x in CFG["universe"] if x not in seed]
        if added:
            log("auto-universe: %d syms (+%d screened in: %s%s)" % (
                len(CFG["universe"]), len(added), ", ".join(added[:8]),
                "..." if len(added) > 8 else ""))
        if hasattr(broker, "_discover_pairs"):
            broker._discover_pairs()
            # persist the enriched maps: fine_recorder + /api/crypto/spark
            # read pair_cache.json, and a cache missing auto-screened
            # symbols froze the recorder + phantom-marked the book on
            # 2026-08-22. pair+asset are written TOGETHER, atomically.
            try:
                broker._cache_f.write_text(json.dumps(
                    {"pair": broker.pair,
                     "asset": {k: list(v) for k, v in broker.asset.items()}}))
            except Exception:
                pass
    except Exception as e:
        log(f"auto-universe failed ({e!r}); static universe this tick")


def main() -> None:
    # Leader gate (D59, 2026-08-21 "no part of this depends on the Mac"):
    # config.json names the engine host; every other machine running this
    # file is a standby viewer. Hostname-keyed on BOTH sides, so two hosts
    # can never trade the same book even if both jobs fire.
    _eh = str(CFG.get("engine_host", "")).lower()
    if _eh:
        import platform as _plat
        _on_gcp = "quarki-stack" in _plat.node().lower()
        if (_eh == "gcp") != _on_gcp:
            log(f"standby: engine_host={_eh}, this host only displays")
            return
    if not acquire_lock():
        log("skip: previous run still active (lock held)")
        return
    st = load_state()
    broker = KrakenBroker() if CFG.get("broker", "kraken") == "kraken" else AlpacaBroker()
    auto_universe(broker, st)

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
    _seed_set = set(CFG.get("_universe_seed") or CFG["universe"])
    adopted = [] if st.get("frozen") else \
        [p for p in positions
         if p.symbol not in st["positions"] and p.symbol in _seed_set]
    for p in positions:
        if p.symbol not in st["positions"] and p.symbol not in _seed_set:
            log(f"stray balance {p.symbol} (~${p.market_value:.2f}) NOT "
                "adopted — auto-screened symbol, not in the seed universe")
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
            # never book a close off a derived list alone (2026-08-23
            # phantom ZEC close): re-verify against a fresh Balance read,
            # and treat unverifiable as KEEP — a stale entry is
            # recoverable, a phantom close of a live position is not.
            try:
                _al = getattr(broker, "asset", {}).get(_sym) or []
                if not _al:
                    log(f"reconcile: no asset mapping for {_sym} — "
                        "keeping position (unverifiable)")
                    continue
                _b = broker._priv("/0/private/Balance")
                _q = sum(float(_b.get(a, 0)) for a in _al)
                if _q * broker.price(_sym) >= 2:
                    log(f"reconcile: {_sym} missing from tracked set but "
                        f"balance {_q:.8f} still on exchange — keeping")
                    continue
            except Exception as _e:
                log(f"reconcile: balance re-check failed for {_sym} ({_e}) "
                    "— skipping close this tick (fail-safe)")
                continue
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

    # CASCADE SENTINEL (Robert 2026-08-22 "would a governor help the
    # drawdowns?"): BTC leads liquidation cascades (8/21: BTC -3.5% while
    # alts fell 15-21%). A >2% BTC drop inside ~15 min flips the whole book
    # defensive for 45 min: every trail clamps to trail_min and ALL buying
    # (adds, dip-rebuys, new names) pauses. Exits and floors stay live.
    try:
        _btc = broker.price("BTC/USD")
        _now = datetime.now(timezone.utc)
        bh = st.setdefault("btc_hist", [])
        bh.append([_now.isoformat(timespec="seconds"), _btc])
        del bh[:-24]
        _base = None
        for _bts, _bpx in reversed(bh[:-1]):
            _base = float(_bpx)
            if (_now - datetime.fromisoformat(_bts)).total_seconds() >= 14 * 60:
                break
        if _base and _btc / _base - 1 <= -float(CFG.get("sentinel_btc_drop", 0.02)):
            _prev_active = False
            try:
                _prev_active = bool(st.get("sentinel_until")) and \
                    datetime.fromisoformat(st["sentinel_until"]) > _now
            except Exception:
                pass
            st["sentinel_until"] = (_now + timedelta(minutes=45)).isoformat(timespec="seconds")
            save_state(st)
            if not _prev_active:
                notify("action", "Cascade sentinel tripped - book defensive",
                       f"BTC dropped {(_btc / _base - 1) * 100:.1f}% in ~15 minutes - the "
                       "liquidation-cascade signature. All trails are clamped to their "
                       "tightest and buying is paused for 45 minutes. Exits stay live.")
    except Exception as e:
        log(f"sentinel FAIL {e!r}")
    _sentinel = False
    try:
        _sentinel = bool(st.get("sentinel_until")) and \
            datetime.fromisoformat(st["sentinel_until"]) > datetime.now(timezone.utc)
    except Exception:
        pass
    if _sentinel:
        log("sentinel ACTIVE: trails clamped to minimum, all buying paused")

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

    # "Alert me if anything big happens" (Robert 2026-08-21 15:15): exits and
    # skims already push, but a big move that trips NOTHING (a rip, or a
    # slide still inside the trails) was silent. Page each ±$ pl_alert_step
    # leg from the last alerted level, either direction.
    _step = float(CFG.get("pl_alert_step", 75.0))
    _anchor = st.get("pl_alert_anchor")
    if _anchor is None:
        st["pl_alert_anchor"] = sleeve_pl
    elif abs(sleeve_pl - float(_anchor)) >= _step:
        _dirn = "up" if sleeve_pl > float(_anchor) else "down"
        notify("action", f"Crypto book moved ${abs(sleeve_pl - float(_anchor)):.0f} {_dirn}",
               f"Sleeve P/L is {sleeve_pl:+.2f} (was {float(_anchor):+.2f} at the last "
               f"alert). No exit rule has triggered — the {_dirn}-move is inside the "
               "normal rails. Nothing you need to do; this is just the heads-up you "
               "asked for on big moves.")
        st["pl_alert_anchor"] = sleeve_pl
        save_state(st)

    # PROFIT-RATCHET FLOOR (Robert 2026-08-22 01:30 "protect the profit not
    # just the deposit"): the catastrophe rail RISES with the P/L high-water
    # mark, trailing by ratchet_giveback_usd — deliberately WIDE (~15% of
    # book) so it sits outside everything trails/stops/skims handle (the
    # floor-width sim: wide floors cost ~zero profit, tight ones sell
    # bottoms). Once P/L has touched +$500, breakeven is locked for good.
    _hwm = max(float(st.get("pl_hwm") or 0.0), sleeve_pl)
    st["pl_hwm"] = round(_hwm, 2)
    _floor = max(float(CFG["freeze_pnl_usd"]),
                 _hwm - float(CFG.get("ratchet_giveback_usd", 500.0)))
    st["effective_floor"] = round(_floor, 2)
    if sleeve_pl <= _floor:
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
                   f"Sleeve P/L hit {sleeve_pl:+.2f} (ratcheted floor {_floor:+.0f}). "
                   f"Liquidation FAILED for: {', '.join(failed)} — likely Kraken Earn "
                   "(.F) balances a market order cannot touch. Sell or unstake these "
                   "on kraken.com NOW; the sleeve is halted and cannot protect them.")
        else:
            notify("action", "Crypto sleeve froze at its loss rail",
                   f"Sleeve P/L hit {sleeve_pl:+.2f} (ratcheted floor {_floor:+.0f}). "
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
            trail = trail_width(broker, p.symbol, st)
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
    for _d in ("trails", "trail_peaks", "px_hist", "spike_ts"):
        if isinstance(st.get(_d), dict):
            for _s in list(st[_d]):
                if _s not in _open_syms:
                    st[_d].pop(_s, None)

    # SPIKE HARVEST (Robert 2026-08-21 03:17 after the 03:00 $84 round-trip:
    # "be more on top of taking profit at peaks"): sell a SLICE into abnormal
    # upward acceleration instead of waiting for the trail to confirm the
    # turn. Trigger = a coin in profit moving >max(2%, trail/3) in ~15 min —
    # roughly 3 sigma of its own short-window vol, so normal hours never
    # fire. 75% keeps riding; proceeds join the dip-rebuy reserve (skim=True
    # records ADD BACK to the still-held coin on a retrace). One live record
    # per coin at a time = natural cooldown.
    _hist = st.setdefault("px_hist", {})
    _now_dt = datetime.now(timezone.utc)
    for p in still_open:
        try:
            h = _hist.setdefault(p.symbol, [])
            h.append([_now_dt.isoformat(timespec="seconds"), p.current_price])
            del h[:-24]                                   # ~2h of 5-min marks
            if not hasattr(broker, "_priv"):
                continue
            _r0 = (st.get("reentry") or {}).get(p.symbol)
            if _r0 is not None:
                # ESCALATING SKIM (Robert 2026-08-22 00:28 "take profit
                # before the top ... still as much as possible"): a spike
                # still making higher highs 15+ min after the last skim gets
                # skimmed AGAIN — harvest depth adapts to each spike's real
                # duration instead of guessing an average length.
                try:
                    _r_age = (_now_dt - datetime.fromisoformat(_r0["ts"])).total_seconds()
                except Exception:
                    _r_age = 0
                if not (_r0.get("skim") and _r_age >= 900
                        and p.current_price > float(_r0.get("exit_px", 1e18))):
                    continue
            base_px = None
            for _ts, _hpx in reversed(h[:-1]):            # newest mark >=14min old
                _age = (_now_dt - datetime.fromisoformat(_ts)).total_seconds()
                if 14 * 60 <= _age <= 40 * 60:
                    base_px = float(_hpx)
                    break
            if not base_px:
                continue
            spike = p.current_price / base_px - 1
            thr = max(0.02, trail_width(broker, p.symbol) / 3.0)
            if spike < thr or p.current_price <= p.entry_price:
                continue
            frac = float(CFG.get("spike_harvest_frac", 0.25))
            skim_usd = round(p.market_value * frac, 2)
            if skim_usd < CFG["min_order_usd"] * 2:
                continue
            vol = p.qty * frac * 0.9995
            # the resting exchange stop reserves the balance — clear it, the
            # post-harvest sync re-places it at the reduced size
            _exst = (st.get("ex_stops") or {}).pop(p.symbol, None)
            if _exst:
                try:
                    broker.cancel_order(_exst.get("txid"))
                except Exception:
                    pass
            broker._priv("/0/private/AddOrder",
                         {"pair": broker.pair[p.symbol], "type": "sell",
                          "ordertype": "market", "volume": f"{vol:.8f}"})
            _sold_pl = (p.market_value - p.notional) * frac
            row = st["positions"].get(p.symbol, {})
            row["notional"] = round(float(row.get("notional", p.notional)) * (1 - frac), 4)
            st["positions"][p.symbol] = row
            append_ledger({"closed_at": _now_dt.isoformat(timespec="seconds"),
                           "symbol": p.symbol, "entry_time": row.get("opened", ""),
                           "entry_price": f"{p.entry_price:.4f}",
                           "exit_price": f"{p.current_price:.4f}",
                           "notional": f"{p.notional * frac:.2f}",
                           "realized_pl": f"{_sold_pl:.4f}",
                           "pl_pct": f"{_sold_pl / (p.notional * frac):.4f}" if p.notional else "0",
                           "reason": f"spike_harvest({spike * 100:.1f}%_in_15m)",
                           "signal_mode": row.get("signal_mode", "?")})
            _prev_usd = float(_r0.get("usd", 0)) if _r0 else 0.0
            st.setdefault("reentry", {})[p.symbol] = {
                "exit_px": p.current_price, "usd": round(skim_usd + _prev_usd, 2),
                "ts": _now_dt.isoformat(timespec="seconds"), "skim": True}
            st.setdefault("spike_ts", {})[p.symbol] = _now_dt.isoformat(timespec="seconds")
            # keep the in-memory Position consistent for the accounting below
            p.qty *= (1 - frac)
            p.market_value *= (1 - frac)
            p.notional = row["notional"]
            p.unrealized_pl = p.market_value - p.notional
            save_state(st)
            log(f"SPIKE HARVEST {p.symbol}: sold ${skim_usd:.2f} ({frac * 100:.0f}%) "
                f"into a {spike * 100:.1f}%/15min rip, pl {_sold_pl:+.2f}; "
                f"reserve targets the retrace")
            notify("action", f"Spike harvest: {p.symbol.replace('/USD','')} {_sold_pl:+.2f}",
                   f"Sold ${skim_usd:.2f} ({frac * 100:.0f}% of the position) INTO a "
                   f"{spike * 100:.1f}% fifteen-minute spike, locking {_sold_pl:+.2f}. "
                   "The rest keeps riding; the proceeds wait to buy the retrace "
                   "back cheaper. Peak-taking working as you asked.")
        except Exception as e:
            log(f"spike check {p.symbol} FAIL {e!r}")

    # MEME TRIM (fleet 2026-08-22): the 25% meme budget is enforced at buy
    # time, but a pre-cap position (DOGE $800 vs $782.50) or any clamp gap
    # would otherwise stay silently over budget. Reconcile each tick: sell
    # the overage from the largest meme holding (skip dust < min_order).
    try:
        _mcap = float(CFG.get("cap_total_usd", 0)) * float(CFG.get("meme_cap_pct", 0.25))
        _memes = [p for p in still_open if is_meme(p.symbol)]
        _mused = sum(float((st["positions"].get(p.symbol) or {}).get("notional") or 0) for p in _memes)
        _over = _mused - _mcap
        if _over >= CFG["min_order_usd"] and _memes and hasattr(broker, "_priv"):
            _mt = max(_memes, key=lambda q: q.market_value)
            _exm = (st.get("ex_stops") or {}).pop(_mt.symbol, None)
            if _exm:
                try:
                    broker.cancel_order(_exm.get("txid"))
                except Exception:
                    pass
            # cap at what the position actually holds (review 2026-08-22:
            # aggregate overage can exceed the largest position; the old
            # formula requested an impossible volume, Kraken rejected it,
            # and the overage was never corrected). Remainder trims next
            # tick from the then-largest meme.
            _take = min(_over, _mt.market_value * 0.95)
            _tvol = (_take / _mt.current_price) * 0.9995
            broker._priv("/0/private/AddOrder",
                         {"pair": broker.pair[_mt.symbol], "type": "sell",
                          "ordertype": "market", "volume": f"{_tvol:.8f}"})
            _trow = st["positions"].get(_mt.symbol, {})
            _tfrac = min(0.99, _take / max(_mt.market_value, 1e-9))
            _tpl = (_mt.market_value - _mt.notional) * _tfrac
            # cost basis shrinks by the SOLD FRACTION, not the raw dollars
            _trow["notional"] = round(float(_trow.get("notional", _mt.notional)) * (1 - _tfrac), 4)
            st["positions"][_mt.symbol] = _trow
            append_ledger({"closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "symbol": _mt.symbol, "entry_time": _trow.get("opened", ""),
                           "entry_price": f"{_mt.entry_price:.4f}",
                           "exit_price": f"{_mt.current_price:.4f}",
                           "notional": f"{_take:.2f}", "realized_pl": f"{_tpl:.4f}",
                           "pl_pct": f"{_tpl / _take:.4f}",
                           "reason": "meme_trim(25pct_budget)",
                           "signal_mode": _trow.get("signal_mode", "?")})
            _mt.qty *= (1 - _tfrac)
            _mt.market_value *= (1 - _tfrac)
            _mt.notional = _trow["notional"]
            _mt.unrealized_pl = _mt.market_value - _mt.notional
            save_state(st)
            log(f"MEME TRIM {_mt.symbol}: sold ${_take:.2f} back toward the 25% budget")
    except Exception as e:
        log(f"meme trim FAIL {e!r}")

    # CASCADE EXIT COORDINATION (Robert 2026-08-22): if 2+ stop-family
    # exits fired within cascade_window_min, the remaining legs exit too and
    # ALL buying pauses cascade_pause_h via the sentinel — captured crash
    # profits stay captured instead of being instantly redeployed into the
    # fade (8/21 lesson: rebuys after the 05:10Z triple-stop cost ~$290).
    try:
        _now2 = datetime.now(timezone.utc)
        _win = float(CFG.get("cascade_window_min", 20)) * 60
        _recent = [t for t in st.get("recent_stop_closes", [])
                   if (_now2 - datetime.fromisoformat(t)).total_seconds() <= _win]
        if len(_recent) >= int(CFG.get("cascade_min_closes", 2)) and still_open:
            _names = [p.symbol for p in still_open]
            for p in list(still_open):
                try:
                    close_position(broker, p, st, "cascade_exit")
                    still_open.remove(p)
                    st.get("trail_peaks", {}).pop(p.symbol, None)
                    st.get("trails", {}).pop(p.symbol, None)
                except Exception as e:
                    log(f"cascade exit {p.symbol} FAIL {e!r}")
            st["sentinel_until"] = (_now2 + timedelta(
                hours=float(CFG.get("cascade_pause_h", 2.0)))).isoformat(timespec="seconds")
            _sentinel = True   # review 2026-08-22: the pause must bind THIS
            #                    tick's entry gate, not just the next run
            st["recent_stop_closes"] = []
            for _rs in list((st.get("reentry") or {}).keys()):
                st["reentry"].pop(_rs, None)   # no dip-buying the cascade
            save_state(st)
            notify("action", "Cascade exit — whole book to cash",
                   f"Multiple stops fired within {int(_win/60)} minutes, so the "
                   f"remaining positions ({', '.join(n.replace('/USD','') for n in _names)}) "
                   f"were sold too and ALL buying is paused for "
                   f"{CFG.get('cascade_pause_h', 2.0):g}h. Crash profits stay banked; "
                   "the engine re-enters when genuine momentum returns.")
    except Exception as e:
        log(f"cascade check FAIL {e!r}")

    # MILESTONE BANKING (Robert 2026-08-22 "automatic profit taking every
    # $100, make it the new floor"): each NEW +$100 of P/L high-water sells
    # $100 from the largest holding and PERMANENTLY retires that cash from
    # play (banked_usd is subtracted from spendable cash forever) — a floor
    # made of cash instead of a tripwire, so nothing can ever trip at a
    # bottom. Starts from today's high-water; history is not retro-sold.
    try:
        _mstep = float(CFG.get("milestone_step", 100.0))
        _mhwm = float(st.get("pl_hwm") or 0)
        _mbase = st.get("milestone_base")
        if _mbase is None:
            st["milestone_base"] = _mhwm
            save_state(st)
        elif _mhwm >= float(_mbase) + _mstep and still_open:
            _take = float(int((_mhwm - float(_mbase)) // _mstep) * _mstep)
            _big = max(still_open, key=lambda q: q.market_value)
            if _big.market_value > _take * 1.5 and hasattr(broker, "_priv"):
                _exb = (st.get("ex_stops") or {}).pop(_big.symbol, None)
                if _exb:
                    try:
                        broker.cancel_order(_exb.get("txid"))
                    except Exception:
                        pass
                _mvol = (_take / _big.current_price) * 0.9995
                broker._priv("/0/private/AddOrder",
                             {"pair": broker.pair[_big.symbol], "type": "sell",
                              "ordertype": "market", "volume": f"{_mvol:.8f}"})
                _mrow = st["positions"].get(_big.symbol, {})
                _mfrac = _take / _big.market_value
                _mpl = (_big.market_value - _big.notional) * _mfrac
                _mrow["notional"] = round(float(_mrow.get("notional", _big.notional)) * (1 - _mfrac), 4)
                st["positions"][_big.symbol] = _mrow
                append_ledger({"closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "symbol": _big.symbol, "entry_time": _mrow.get("opened", ""),
                               "entry_price": f"{_big.entry_price:.4f}",
                               "exit_price": f"{_big.current_price:.4f}",
                               "notional": f"{_take:.2f}", "realized_pl": f"{_mpl:.4f}",
                               "pl_pct": f"{_mpl / _take:.4f}",
                               "reason": f"milestone_bank(hwm_{_mhwm:.0f})",
                               "signal_mode": _mrow.get("signal_mode", "?")})
                _big.qty *= (1 - _mfrac)
                _big.market_value *= (1 - _mfrac)
                _big.notional = _mrow["notional"]
                _big.unrealized_pl = _big.market_value - _big.notional
                st["milestone_base"] = float(_mbase) + _take
                st["banked_usd"] = round(float(st.get("banked_usd") or 0) + _take, 2)
                save_state(st)
                log(f"MILESTONE BANK: sold ${_take:.0f} of {_big.symbol} at P/L "
                    f"high-water {_mhwm:.0f} — banked_usd now ${st['banked_usd']:.0f}")
                notify("action", f"Profit banked: ${_take:.0f} off the table",
                       f"P/L made a new high (+{_mhwm:.0f}), so ${_take:.0f} of "
                       f"{_big.symbol.replace('/USD','')} was sold and permanently "
                       f"retired from trading. Total banked and untouchable: "
                       f"${st['banked_usd']:.0f}. The rest keeps riding.")
    except Exception as e:
        log(f"milestone bank FAIL {e!r}")

    sync_exchange_stops(broker, st, still_open)

    # Cap accounting on COST BASIS, not market value: a drawdown must not
    # free up "room" to buy more (that would let total spend exceed the cap).
    deployed = sum(p.notional for p in still_open)
    held = {p.symbol for p in still_open}

    # ---- entries ----
    allowed, mode = whale_gate()
    if _sentinel:
        allowed = False
        mode = 'sentinel-defensive' 
    try:
        cash_avail = broker.cash()
    except Exception as e:
        log(f"cash check FAIL {e!r} — skipping entries this tick")
        save_state(st)
        return
    _banked = float(st.get("banked_usd") or 0)
    if _banked > 0:
        cash_avail -= _banked   # banked profit is permanently off the table
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

    bought = 0.0       # concentrated adds + fresh opens
    dip_bought = 0.0   # dip-rebuys tracked separately: a rebuy must not veto
    _max_pos = int(CFG.get("max_positions", 999))  # the fresh-name loop (verify-fleet 2026-08-21)

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
        # skim records stay live while the coin is held (they add BACK to
        # it); full-exit records die if the coin somehow got re-held
        if _age_h > float(CFG.get("reentry_ttl_h", 24)) or \
                (_sym in held and not r.get("skim")):
            _reentry.pop(_sym)
            save_state(st)
            continue
        try:
            _cur = broker.price(_sym)
            _dip = max(0.015, trail_width(broker, _sym) / 3.0)
            # Entries fail CLOSED on missing data, everywhere in this file:
            # a data gap defers the rebuy to next tick, never green-lights it
            # (verify-fleet 2026-08-21; `is True`, not `is not False`).
            _mom = momentum_ok(broker.closes(_sym))
        except Exception:
            reserved += float(r.get("usd") or 0)
            continue
        _is_skim = bool(r.get("skim")) and _sym in st.get("positions", {})
        _want = round(min(float(r.get("usd") or 0), room, cash_avail - 0.5), 2)
        _meme_ok = True
        if is_meme(_sym):
            _mr, _ms = meme_room(st)
            # skims add BACK to a held coin (no new slot needed) but the
            # 25% dollar budget clamps them too (review 2026-08-22: skims
            # silently bypassed the cap)
            _meme_ok = (_ms > 0 or _is_skim) and _mr >= CFG["min_order_usd"]
            if _meme_ok:
                _want = round(min(_want, _mr), 2)
        if (allowed
                and _meme_ok
                and (len(st.get("positions") or {}) < _max_pos or _is_skim)
                and _cur <= float(r["exit_px"]) * (1 - _dip)
                and _want >= CFG["min_order_usd"]
                and _mom is True):
            try:
                _px = broker.buy(_sym, _want)
            except Exception as e:
                log(f"DIPBUY {_sym} ${_want:.2f} FAIL {e!r}")
                reserved += float(r.get("usd") or 0)
                continue
            if _is_skim:
                add_to_position(st, _sym, _want, _px)   # grow the held coin back
            else:
                st["positions"][_sym] = {
                    "entry_price": _px, "notional": _want,
                    "opened": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "signal_mode": "dip_rebuy"}
                st.setdefault("trail_peaks", {})[_sym] = _px
            _reentry.pop(_sym)
            save_state(st)
            room -= _want
            cash_avail -= _want
            dip_bought += _want
            held.add(_sym)
            # Cap room is cost-basis, proceeds include profit — a near-cap
            # book can only partially refill. Say so instead of silently
            # keeping the "bought it all back" story (verify-fleet).
            _short = round(float(r.get("usd") or 0) - _want, 2)
            log(f"DIPBUY {_sym} ${_want:.2f} @ ~{_px} "
                f"({(1 - _px / float(r['exit_px'])) * 100:.1f}% below the exit)"
                + (f" — ${_short:.2f} of the reserve didn't fit under the cap,"
                   " freed to winner-adds" if _short >= CFG["min_order_usd"] else ""))
            notify("action", f"Dip rebuy: {_sym.replace('/USD','')}",
                   f"Bought back ${_want:.2f} of {_sym} at about {_px} — "
                   f"{(1 - _px / float(r['exit_px'])) * 100:.1f}% cheaper than we "
                   "sold it."
                   + (f" (${_short:.2f} of the proceeds didn't fit under the cap "
                      "and grows the other winners instead.)" if _short >= CFG["min_order_usd"] else ""))
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
            if is_meme(sym):
                _mroom2, _ = meme_room(st)
                size = round(min(size, _mroom2), 2)
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
            if is_meme(sym):
                _mroom, _mslots = meme_room(st)
                if _mslots <= 0 or _mroom < CFG["min_order_usd"]:
                    log(f"BUY {sym} skipped: meme cap (headroom ${_mroom:.0f}, slots {_mslots})")
                    continue
                size = round(min(size, _mroom), 2)
                if size < CFG["min_order_usd"]:
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
    # Mark-to-EXCHANGE at tick end (2026-08-22: the old incremental
    # cash+mv+bought arithmetic double-counted a position whose Kraken-side
    # stop filled mid-tick — proceeds in cash while the balance still
    # showed — printing a phantom $4,170 candle). One fresh Balance read is
    # authoritative through any rotation.
    try:
        cash_avail = broker.cash()
        _mv_end = sum(p.market_value for p in broker.positions(st))
        equity = cash_avail + _mv_end
    except Exception:
        equity = cash_avail + sum(p.market_value for p in still_open) + bought + dip_bought
    new_f = not EQUITY_F.exists()
    with EQUITY_F.open("a") as f:
        if new_f:
            f.write("ts,equity,cash,positions_mv,realized\n")
        f.write("%s,%.4f,%.4f,%.4f,%.4f\n"
                % (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   equity, cash_avail, equity - cash_avail,
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
