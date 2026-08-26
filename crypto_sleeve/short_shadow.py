#!/usr/bin/env python3
"""Short-side SHADOW lane (Robert 2026-08-25 "yeah build the short shadow
lane") — signal-only. This process CANNOT trade by construction: it imports
no broker, reads no API key, and touches only Kraken PUBLIC endpoints plus
the sleeve's own state files read-only. It logs every short it WOULD take
into a D7-scoreable ledger; the D41-style gate is >=30 closed shadow trades
with positive expectancy NET of modeled margin costs before real money is
even discussed.

Signals (both require the coin to be shortable-liquid: 24h dvol >= $20M):
  A. SPIKE-EXHAUSTION FADE — the mirror of the mega-spike sell-all: a coin
     that ripped >= +5% inside ~15-30min and has now broken >= 1.2% off that
     spike peak gets shorted at the break. Cover at -3% (retrace target),
     stop on a new high above the spike peak +0.3%, time-stop 4h.
  B. BREAKDOWN MOMENTUM — EMA12 < EMA48 by >= 0.5% on 1h candles AND price
     <= 24h-ago -2% AND a fresh 24h low printed within the last hour.
     Cover when the 1h momentum flips back up (fast > slow) on two
     consecutive ticks, stop +3% against, time-stop 24h.

Costs are binding (D43): every open leg accrues a modeled Kraken margin
rollover of 0.02%/4h plus 0.26% taker each side; the ledger P/L is NET.
"""
from __future__ import annotations

import fcntl
import json
import statistics
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_F = STATE_DIR / "short_shadow_state.json"
LEDGER_F = STATE_DIR / "short_shadow.jsonl"
LOCK_F = STATE_DIR / "short_shadow.lock"
LOG_F = ROOT / "logs" / "short_shadow.log"
LOG_F.parent.mkdir(parents=True, exist_ok=True)

CFG = {
    "notional_usd": 100.0,        # paper size per leg
    "max_positions": 3,
    "universe_max": 20,
    "min_dvol_usd": 20e6,         # same liquidity floor as the live sleeve
    "taker_fee": 0.0026,          # per side
    "rollover_per_4h": 0.0002,    # Kraken margin rollover, modeled
    # A: spike-exhaustion fade
    "spike_pct": 0.05,            # the rip (mirrors megaspike_pct)
    "spike_window_s": 1800,       # rip must fit inside ~30min
    "break_pct": 0.012,           # entry: this far off the spike peak
    "fade_target": -0.03,         # cover at -3% from entry
    "fade_stop_above_peak": 0.003,
    "fade_time_stop_h": 4.0,
    # B: breakdown momentum
    "ema_fast": 12, "ema_slow": 48,
    "bd_ema_gap": 0.005,
    "bd_drop_24h": -0.02,
    "bd_low_fresh_s": 3600,
    "bd_stop": 0.03,
    "bd_time_stop_h": 24.0,
    "bd_flip_confirm": 2,
}


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line)
    with open(LOG_F, "a") as f:
        f.write(line + "\n")


def notify(level: str, title: str, msg: str) -> None:
    try:
        subprocess.run(["python3", str(Path.home() / "bin" / "quarki_notify.py"),
                        level, title, msg], timeout=15, capture_output=True)
    except Exception:
        pass


def pub(path: str, params: dict) -> dict:
    url = "https://api.kraken.com" + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=12) as r:
        j = json.load(r)
    if j.get("error"):
        raise RuntimeError(str(j["error"])[:80])
    return j.get("result", {})


def ema(vals, n):
    if not vals:
        return 0.0
    k = 2.0 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def load_state() -> dict:
    try:
        return json.loads(STATE_F.read_text())
    except Exception:
        return {"positions": {}, "flip_pending": {}, "first_notified": False}


def save_state(st: dict) -> None:
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(STATE_F)


def universe() -> list[tuple[str, str]]:
    """(display_sym, kraken_pair_code) from the live sleeve's own caches —
    read-only; fails closed to a majors list if the cache is unreadable."""
    out = []
    try:
        pc = json.loads((STATE_DIR / "pair_cache.json").read_text())
        pairs = pc.get("pair") or {}
        for ws, code in pairs.items():
            out.append((ws, code))
    except Exception:
        out = [("BTC/USD", "XXBTZUSD"), ("ETH/USD", "XETHZUSD"),
               ("SOL/USD", "SOLUSD"), ("XRP/USD", "XXRPZUSD")]
    return out[: CFG["universe_max"]]


def close_leg(st: dict, sym: str, px: float, reason: str) -> None:
    p = st["positions"].pop(sym)
    entry = float(p["entry"])
    notional = float(p["notional"])
    opened = datetime.fromisoformat(p["opened"])
    now = datetime.now(timezone.utc)
    hours = max(0.0, (now - opened).total_seconds() / 3600)
    # short P/L: entry above exit = profit
    gross = notional * (entry - px) / entry
    fees = notional * CFG["taker_fee"] * 2
    roll = notional * CFG["rollover_per_4h"] * (hours / 4.0)
    pl = round(gross - fees - roll, 4)
    row = {"closed_at": now.isoformat(timespec="seconds"), "sym": sym,
           "side": "short", "entry": entry, "exit": px,
           "notional": notional, "opened": p["opened"],
           "signal": p.get("signal"), "reason": reason,
           "gross": round(gross, 4), "fees": round(fees, 4),
           "rollover": round(roll, 4), "pl": pl,
           "pl_pct": round(pl / notional * 100, 2)}
    with open(LEDGER_F, "a") as f:
        f.write(json.dumps(row) + "\n")
    log(f"COVER {sym} @ {px} ({reason}) pl={pl:+.2f}")


def main() -> None:
    lock = open(LOCK_F, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another tick still running — skip")
        return
    st = load_state()
    now = datetime.now(timezone.utc)
    uni = universe()
    for sym, code in uni:
        try:
            time.sleep(0.25)   # public-pool politeness
            o5 = pub("/0/public/OHLC", {"pair": code, "interval": 5})
            rows5 = next(v for k, v in o5.items() if k != "last")
            if len(rows5) < 8:
                continue
            time.sleep(0.25)
            o60 = pub("/0/public/OHLC", {"pair": code, "interval": 60})
            rows60 = next(v for k, v in o60.items() if k != "last")
            closes60 = [float(x[4]) for x in rows60]
            px = float(rows5[-1][4])
            if px <= 0:
                continue
            # 24h dollar-volume liquidity floor (fail closed)
            dvol = sum(float(x[6]) * float(x[4]) for x in rows60[-24:])
            if dvol < CFG["min_dvol_usd"]:
                if sym in st["positions"]:
                    pass          # manage an open leg even if dvol decayed
                else:
                    continue

            pos = st["positions"].get(sym)
            if pos:
                entry = float(pos["entry"])
                chg = px / entry - 1          # + = against the short
                hours = (now - datetime.fromisoformat(pos["opened"])
                         ).total_seconds() / 3600
                if pos["signal"] == "spike_fade":
                    peak = float(pos["peak_ref"])
                    if px >= peak * (1 + CFG["fade_stop_above_peak"]):
                        close_leg(st, sym, px, "stop_new_high")
                    elif chg <= CFG["fade_target"]:
                        close_leg(st, sym, px, "target_retrace")
                    elif hours >= CFG["fade_time_stop_h"]:
                        close_leg(st, sym, px, "time_stop")
                else:  # breakdown
                    fast, slow = ema(closes60, CFG["ema_fast"]), ema(closes60, CFG["ema_slow"])
                    fp = st.setdefault("flip_pending", {})
                    if chg >= CFG["bd_stop"]:
                        close_leg(st, sym, px, "stop")
                        fp.pop(sym, None)
                    elif hours >= CFG["bd_time_stop_h"]:
                        close_leg(st, sym, px, "time_stop")
                        fp.pop(sym, None)
                    elif fast > slow:
                        n = int(fp.get(sym, 0)) + 1
                        fp[sym] = n
                        if n >= CFG["bd_flip_confirm"]:
                            close_leg(st, sym, px, "momentum_flip_up")
                            fp.pop(sym, None)
                    else:
                        fp.pop(sym, None)
                continue

            if len(st["positions"]) >= CFG["max_positions"]:
                continue

            # A: spike-exhaustion fade
            win = [r for r in rows5
                   if now.timestamp() - float(r[0]) <= CFG["spike_window_s"]]
            entered = False
            if len(win) >= 3:
                lo = min(float(r[3]) for r in win)
                hi = max(float(r[2]) for r in win)
                if lo > 0 and hi / lo - 1 >= CFG["spike_pct"] \
                        and px <= hi * (1 - CFG["break_pct"]) \
                        and px > lo:                       # broke, not round-tripped
                    st["positions"][sym] = {
                        "side": "short", "entry": px,
                        "notional": CFG["notional_usd"], "peak_ref": hi,
                        "opened": now.isoformat(timespec="seconds"),
                        "signal": "spike_fade"}
                    log(f"SHORT {sym} @ {px} [spike_fade rip {(hi / lo - 1) * 100:.1f}% "
                        f"peak {hi}]")
                    entered = True
            # B: breakdown momentum
            if not entered and len(closes60) >= CFG["ema_slow"] + 2:
                fast, slow = ema(closes60, CFG["ema_fast"]), ema(closes60, CFG["ema_slow"])
                day_ago = closes60[-25] if len(closes60) >= 25 else closes60[0]
                lows_24h = [float(r[3]) for r in rows60[-24:]]
                low_is_fresh = lows_24h and min(lows_24h) == min(lows_24h[-1:])
                if slow > 0 and day_ago > 0 \
                        and fast < slow * (1 - CFG["bd_ema_gap"]) \
                        and px / day_ago - 1 <= CFG["bd_drop_24h"] \
                        and low_is_fresh:
                    st["positions"][sym] = {
                        "side": "short", "entry": px,
                        "notional": CFG["notional_usd"],
                        "opened": now.isoformat(timespec="seconds"),
                        "signal": "breakdown"}
                    log(f"SHORT {sym} @ {px} [breakdown fast/slow "
                        f"{fast / slow - 1:+.3%} 24h {px / day_ago - 1:+.2%}]")
        except Exception as e:
            log(f"{sym} tick FAIL {e!r}")
    if not st.get("first_notified") and LEDGER_F.exists():
        st["first_notified"] = True
        notify("action", "Short shadow lane: first logged trade",
               "The signal-only short lane just closed its first paper short "
               "(state/short_shadow.jsonl). No real orders are possible from "
               "this lane; it must earn 30+ profitable-net trades before real "
               "money is discussed.")
    save_state(st)
    log(f"tick done: {len(st['positions'])} open shadow shorts")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERROR {e!r}")
        raise
