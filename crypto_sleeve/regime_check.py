#!/usr/bin/env python3
"""Price-based market-regime opinion logged NEXT TO the live whale gate.

READ-ONLY / SHADOW ONLY: this script never trades, never touches a broker
or private API, and imports nothing beyond stdlib. It hits Kraken's PUBLIC
OHLC endpoint only. Purpose is a 2-week side-by-side: does a plain BTC-led
price-breadth regime call (50d EMA trend + universe breadth) agree with the
live whale-flow gate (state/whale_signals.json "risk_on")? Logged to
state/regime_log.jsonl hourly via systemd oneshot so we can later score who
was right.

Universe + Kraken pair codes come from the live sleeve's own read-only cache
(state/pair_cache.json) so this never invents its own symbol list.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LEDGER_F = STATE_DIR / "regime_log.jsonl"
ERR_LOG_F = LOG_DIR / "regime_check.log"

CFG = {
    "universe_max": 20,       # cap on non-BTC coins sampled for breadth
    "ema_len": 50,            # 50-day EMA
    "ohlc_interval": 1440,    # daily candles
    "pace_s": 0.3,            # politeness delay between public calls
    "timeout_s": 12,
    "breadth_threshold": 0.5,
    "whale_pass_threshold": 0.5,
}

# Candidate state files that might hold the live whale gate's risk_on float.
# whale_signals.json is where it actually lives today; the rest are kept as
# fallbacks in case the gate's home file ever gets renamed/moved.
WHALE_STATE_CANDIDATES = [
    "whale_signals.json",
    "radar.json",
    "whale.json",
    "whale_state.json",
]


def log_err(msg: str) -> None:
    """Append a line to the error log. Never raises."""
    try:
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
        with open(ERR_LOG_F, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def pub_ohlc(pair_code: str) -> list:
    """Fetch daily OHLC candles for a Kraken pair code. Public endpoint only."""
    url = ("https://api.kraken.com/0/public/OHLC?" +
           urllib.parse.urlencode({"pair": pair_code, "interval": CFG["ohlc_interval"]}))
    with urllib.request.urlopen(url, timeout=CFG["timeout_s"]) as r:
        j = json.load(r)
    if j.get("error"):
        raise RuntimeError(str(j["error"])[:120])
    result = j.get("result", {})
    rows = next((v for k, v in result.items() if k != "last"), None)
    if not rows:
        raise RuntimeError("no OHLC rows in response")
    return rows


def ema(vals: list, n: int) -> float:
    if not vals:
        return 0.0
    k = 2.0 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def load_universe() -> dict:
    """ws-sym -> kraken pair code, from the live sleeve's read-only cache."""
    try:
        pc = json.loads((STATE_DIR / "pair_cache.json").read_text())
        pairs = pc.get("pair") or {}
        if pairs:
            return pairs
    except Exception as e:
        log_err(f"pair_cache.json unreadable: {e!r}")
    # fail closed to a small majors list so the script still produces a reading
    return {"BTC/USD": "XXBTZUSD", "ETH/USD": "XETHZUSD",
            "SOL/USD": "SOLUSD", "XRP/USD": "XXRPZUSD"}


def load_whale_risk_on() -> float | None:
    """First json candidate with a top-level "risk_on" float, else None."""
    for name in WHALE_STATE_CANDIDATES:
        f = STATE_DIR / name
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text())
            val = d.get("risk_on")
            if isinstance(val, (int, float)):
                return float(val)
        except Exception as e:
            log_err(f"{name} unreadable: {e!r}")
    return None


def coin_above_ema50(pair_code: str) -> tuple[bool, float, float] | None:
    """Returns (above, last_close, ema50) for one pair, or None on any failure."""
    rows = pub_ohlc(pair_code)
    closes = [float(x[4]) for x in rows]
    if len(closes) < CFG["ema_len"]:
        return None
    last_close = closes[-1]
    e50 = ema(closes[-CFG["ema_len"]:], CFG["ema_len"])
    if last_close <= 0 or e50 <= 0:
        return None
    return (last_close > e50, last_close, e50)


def append_ledger(row: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LEDGER_F, "a") as f:
        f.write(json.dumps(row) + "\n")


def main() -> None:
    universe = load_universe()

    # BTC drives the trend leg of the verdict.
    btc_code = universe.get("BTC/USD")
    if not btc_code:
        raise RuntimeError("no BTC/USD pair in pair_cache.json")

    btc_close = None
    btc_ema50 = None
    btc_above_50d = None
    try:
        res = coin_above_ema50(btc_code)
        if res is not None:
            btc_above_50d, btc_close, btc_ema50 = res
    except Exception as e:
        log_err(f"BTC OHLC fetch FAIL {e!r}")
    time.sleep(CFG["pace_s"])

    # Breadth leg: fraction of up-to-20 universe coins above their own 50d EMA.
    above_count = 0
    sampled = 0
    for ws_sym, code in list(universe.items())[: CFG["universe_max"]]:
        try:
            res = coin_above_ema50(code)
            if res is not None:
                sampled += 1
                if res[0]:
                    above_count += 1
        except Exception as e:
            log_err(f"{ws_sym} OHLC fetch FAIL {e!r}")
        time.sleep(CFG["pace_s"])

    breadth = (above_count / sampled) if sampled > 0 else None

    if btc_above_50d is not None and breadth is not None:
        price_risk_on = bool(btc_above_50d and breadth >= CFG["breadth_threshold"])
    else:
        price_risk_on = None

    whale_risk_on = load_whale_risk_on()
    whale_gate_pass = (whale_risk_on >= CFG["whale_pass_threshold"]
                        if whale_risk_on is not None else None)

    agree = (price_risk_on == whale_gate_pass
             if price_risk_on is not None and whale_gate_pass is not None else None)

    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "btc_close": btc_close,
        "btc_ema50": btc_ema50,
        "btc_above_50d": btc_above_50d,
        "breadth": round(breadth, 4) if breadth is not None else None,
        "n_coins": sampled,
        "price_risk_on": price_risk_on,
        "whale_risk_on": whale_risk_on,
        "whale_gate_pass": whale_gate_pass,
        "agree": agree,
    }
    append_ledger(row)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log_err(f"ERROR {e!r}")
        # never raise nonzero unless truly broken (e.g. can't even write logs)
