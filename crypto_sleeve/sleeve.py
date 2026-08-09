#!/usr/bin/env python3
"""D39 live crypto sleeve — whale-gated momentum on Alpaca crypto (BTC/ETH/SOL).

Rails (DIRECTIVES.md D39): $50 hard cap, freeze + liquidate at sleeve P/L
<= -$15, per-trade ledger in state/closed_trades.csv (D7-scoreable).
Entries need EMA momentum AND (whale risk-on gate OR stale whale feed).
While the account's crypto_status is INACTIVE the loop idles and pings
Robert (action, repeat-suppressed 24h) to sign the Alpaca crypto agreement.

Runs every 5 min via launchd com.quarki3.crypto-sleeve (Mac interim host;
migrate to VM alongside the rest of the stack when SSH access returns).
"""
from __future__ import annotations

import csv
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
LEDGER_F = ROOT / "state" / "closed_trades.csv"
WHALE_F = ROOT / "state" / "whale_signals.json"
CFG = json.loads((ROOT / "config.json").read_text())

DATA_BASE = "https://data.alpaca.markets"

sys.path.insert(0, str(HOME / "bin"))
try:
    from quarki_notify import notify
except Exception:
    def notify(level, title, msg):  # noqa: ARG001
        return False


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}", flush=True)


def load_env() -> dict:
    env = {}
    for line in Path(CFG["env_file"]).expanduser().read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip().replace("export ", "")] = v.strip().strip('"').strip("'")
    key = next(v for k, v in env.items() if "KEY" in k.upper() and "SECRET" not in k.upper())
    sec = next(v for k, v in env.items() if "SECRET" in k.upper())
    base = next((v for k, v in env.items() if "URL" in k.upper() or "BASE" in k.upper()),
                "https://api.alpaca.markets")
    return {"key": key, "sec": sec, "base": base.rstrip("/")}


ENV = load_env()
HDR = {"APCA-API-KEY-ID": ENV["key"], "APCA-API-SECRET-KEY": ENV["sec"],
       "Content-Type": "application/json"}


def api(path: str, method: str = "GET", body: dict | None = None, base: str | None = None):
    url = (base or ENV["base"]) + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HDR, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


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
    last = st["last_notified"].get(key, 0)
    if now - last >= hours * 3600:
        notify(level, title, msg)
        st["last_notified"][key] = now


def bars(symbol: str, limit: int = 80) -> list[dict]:
    q = urllib.parse.urlencode({"symbols": symbol, "timeframe": CFG["bar_timeframe"],
                                "limit": limit, "sort": "desc"})
    out = api(f"/v1beta3/crypto/us/bars?{q}", base=DATA_BASE)
    rows = out.get("bars", {}).get(symbol, [])
    rows.reverse()  # oldest first
    return rows


def ema(vals: list[float], n: int) -> float:
    k = 2 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def momentum_signal(symbol: str) -> tuple[bool, float]:
    rows = bars(symbol)
    if len(rows) < CFG["ema_slow"] + 2:
        return False, 0.0
    closes = [float(r["c"]) for r in rows]
    fast, slow = ema(closes, CFG["ema_fast"]), ema(closes, CFG["ema_slow"])
    day_ago = closes[-25] if len(closes) >= 25 else closes[0]
    ok = fast > slow and closes[-1] > day_ago
    return ok, closes[-1]


def whale_gate() -> tuple[bool, str]:
    """(entries_allowed, mode). Stale/no feed -> momentum-only fallback."""
    try:
        w = json.loads(WHALE_F.read_text())
        asof = datetime.fromisoformat(w["asof"])
        age_h = (datetime.now(timezone.utc) - asof).total_seconds() / 3600
        if age_h > CFG["whale_max_age_hours"]:
            return True, "momentum-only (whale feed stale %.0fh)" % age_h
        if not w.get("rows"):
            return True, "momentum-only (whale feed empty)"
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


def close_position(pos: dict, st: dict, reason: str) -> None:
    sym_flat = pos["symbol"]                       # e.g. BTCUSD
    api(f"/v2/positions/{sym_flat}", method="DELETE")
    meta = st["positions"].pop(sym_flat, {})
    entry = float(meta.get("entry_price") or pos["avg_entry_price"])
    exit_p = float(pos["current_price"])
    notional = float(meta.get("notional") or pos["cost_basis"])
    pl = float(pos["unrealized_pl"])
    append_ledger({"closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "symbol": sym_flat, "entry_time": meta.get("opened", ""),
                   "entry_price": f"{entry:.4f}", "exit_price": f"{exit_p:.4f}",
                   "notional": f"{notional:.2f}", "realized_pl": f"{pl:.4f}",
                   "pl_pct": f"{pl / notional:.4f}" if notional else "0",
                   "reason": reason, "signal_mode": meta.get("signal_mode", "?")})
    log(f"CLOSED {sym_flat} reason={reason} pl={pl:+.2f}")


def main() -> None:
    st = load_state()

    acct = api("/v2/account")
    if acct.get("crypto_status") != "ACTIVE":
        suppressed_notify(st, "crypto_inactive", "action", "Crypto sleeve waiting on you",
                          "The crypto sleeve is installed and ready, but the Alpaca account "
                          "can't trade crypto until you sign the crypto agreement. Open the "
                          "Alpaca web dashboard > Account and accept the crypto agreement. "
                          "The sleeve starts trading automatically once it's active.")
        save_state(st)
        log(f"idle: crypto_status={acct.get('crypto_status')}")
        return

    if not st.get("seen_active"):
        st["seen_active"] = True
        notify("info", "Crypto sleeve is LIVE",
               "The Alpaca crypto agreement is signed and the sleeve is now active. "
               "It will start buying as soon as settled cash is available and "
               "momentum/whale signals line up — entries get logged silently.")

    positions = [p for p in api("/v2/positions") if p.get("asset_class") == "crypto"]

    if st.get("frozen"):
        suppressed_notify(st, "frozen", "action", "Crypto sleeve is FROZEN",
                          "The crypto sleeve hit its -$15 freeze rail and is halted flat. "
                          "It will not trade again until you review the ledger "
                          "(~/crypto_sleeve/state/closed_trades.csv) and delete the frozen "
                          "flag in sleeve_state.json.")
        save_state(st)
        log("idle: frozen")
        return

    unreal = sum(float(p["unrealized_pl"]) for p in positions)
    sleeve_pl = realized_total() + unreal
    if sleeve_pl <= CFG["freeze_pnl_usd"]:
        for p in positions:
            close_position(p, st, "freeze_liquidation")
        st["frozen"] = True
        notify("action", "Crypto sleeve froze at its loss rail",
               f"Sleeve P/L hit {sleeve_pl:+.2f} (rail {CFG['freeze_pnl_usd']:+.0f}). "
               "All crypto positions were liquidated and the sleeve is halted until "
               "you review it. Nothing else in the account was touched.")
        save_state(st)
        return

    # ---- exits on open positions ----
    for p in positions:
        sym_flat = p["symbol"]
        meta = st["positions"].get(sym_flat, {})
        entry = float(meta.get("entry_price") or p["avg_entry_price"])
        cur = float(p["current_price"])
        chg = cur / entry - 1
        slash_sym = meta.get("slash_symbol") or f"{sym_flat[:-3]}/{sym_flat[-3:]}"
        mom_ok, _ = momentum_signal(slash_sym)
        if chg <= CFG["stop_pct"]:
            close_position(p, st, "stop")
        elif chg >= CFG["take_pct"]:
            close_position(p, st, "take_profit")
        elif not mom_ok:
            close_position(p, st, "momentum_flip")

    # refresh after exits
    positions = [p for p in api("/v2/positions") if p.get("asset_class") == "crypto"]
    deployed = sum(float(p["market_value"]) for p in positions)
    held_flat = {p["symbol"] for p in positions}

    # ---- entries ----
    allowed, mode = whale_gate()
    cash_avail = float(acct.get("non_marginable_buying_power") or acct.get("cash") or 0)
    room = CFG["cap_total_usd"] - deployed
    log(f"pl={sleeve_pl:+.2f} deployed={deployed:.2f} room={room:.2f} "
        f"cash={cash_avail:.2f} gate={mode}")

    if allowed:
        for sym in CFG["universe"]:
            flat = sym.replace("/", "")
            if flat in held_flat:
                continue
            size = min(CFG["per_trade_usd"], room, cash_avail - 0.5)
            if size < CFG["min_order_usd"]:
                break
            mom_ok, price = momentum_signal(sym)
            if not mom_ok:
                continue
            api("/v2/orders", method="POST",
                body={"symbol": sym, "notional": f"{size:.2f}", "side": "buy",
                      "type": "market", "time_in_force": "gtc"})
            st["positions"][flat] = {
                "slash_symbol": sym, "entry_price": price, "notional": size,
                "opened": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "signal_mode": mode}
            room -= size
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
