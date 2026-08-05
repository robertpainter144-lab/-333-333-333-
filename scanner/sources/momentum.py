"""Momentum tracker for Alpaca-tradeable crypto.

Every 60s:
  1. Pull latest price + 24h volume for every Alpaca-tradeable coin (CoinGecko batch).
  2. Append to rolling per-token time series in state/prices/<sym>.jsonl.
  3. Compute velocity (5m/15m %chg), acceleration (delta of velocity), volume trend.
  4. Emit ranked entry/exit signals.

Entry:  5m% > +0.4  AND  accel > 0  AND  5m% > 15m% (getting stronger, not fading)
Exit :  we mark all previous ENTRY tokens; emit EXIT when 5m% turns negative or accel goes deeply negative.

Signals include `entry_score` / `exit_score` in raw for downstream sizing.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit, log_tick, emit_live, STATEDIR

PRICE_DIR = STATEDIR / "prices"
ENTRY_STATE = STATEDIR / "momentum_entries.json"
LEDGER = STATEDIR / "momentum_ledger.jsonl"
CONFIG_FILE = STATEDIR / "momentum_config.json"
WINDOW_ROWS = 60          # keep ~60 minutes of 1-min samples
CG_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"

# Alpaca-tradeable USD pairs → CoinGecko id.
# Stablecoins deliberately excluded.
ALPACA_UNIVERSE: dict[str, str] = {
    "BTC":    "bitcoin",
    "ETH":    "ethereum",
    "SOL":    "solana",
    "DOGE":   "dogecoin",
    "AVAX":   "avalanche-2",
    "LTC":    "litecoin",
    "LINK":   "chainlink",
    "UNI":    "uniswap",
    "AAVE":   "aave",
    "BCH":    "bitcoin-cash",
    "DOT":    "polkadot",
    "XRP":    "ripple",
    "MKR":    "maker",
    "YFI":    "yearn-finance",
    "SHIB":   "shiba-inu",
    "BAT":    "basic-attention-token",
    "GRT":    "the-graph",
    "ADA":    "cardano",
    "ARB":    "arbitrum",
    "BONK":   "bonk",
    "CRV":    "curve-dao-token",
    "FIL":    "filecoin",
    "HYPE":   "hyperliquid",
    "LDO":    "lido-dao",
    "ONDO":   "ondo-finance",
    "PAXG":   "pax-gold",
    "PEPE":   "pepe",
    "POL":    "polygon-ecosystem-token",
    "RENDER": "render-token",
    "SKY":    "sky",
    "SUSHI":  "sushi",
    "TRUMP":  "official-trump",
    "WIF":    "dogwifcoin",
    "XTZ":    "tezos",
}

# Default config — the retuner writes state/momentum_config.json to override.
# Time-scales are FAST (2m/4m/6m) so we react in seconds, not minutes.
DEFAULT_CONFIG = {
    "version":         1,
    "entry_5m_pct":    0.15,    # 2m window: +0.15% is a real spike on majors
    "entry_accel":     0.0,
    "exit_5m_pct":     -0.10,
    "exit_accel_deep": -0.25,
    "exit_drawdown":   -0.7,
    "require_v5m_gte_v15m": True,
    "min_24h_vol_usd": 0,
    "notes":           "v1 fast — 2m/4m windows, 0.15% entry",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            out = dict(DEFAULT_CONFIG)
            out.update(data)
            return out
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def _log_closed_trade(sym: str, entry_ts: int, entry_px: float,
                      exit_ts: int, exit_px: float, pnl_pct: float,
                      reason: str, cfg_version: int) -> None:
    STATEDIR.mkdir(exist_ok=True)
    row = {"sym": sym, "entry_ts": entry_ts, "entry_px": entry_px,
           "exit_ts": exit_ts, "exit_px": exit_px, "pnl_pct": round(pnl_pct, 4),
           "reason": reason, "cfg_version": cfg_version}
    with LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")


def _get(url: str, params: dict, timeout: int = 15) -> dict:
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _load_series(sym: str) -> list[dict]:
    p = PRICE_DIR / f"{sym}.jsonl"
    if not p.exists():
        return []
    try:
        lines = p.read_text().splitlines()[-WINDOW_ROWS:]
        return [json.loads(ln) for ln in lines]
    except Exception:
        return []


def _append_series(sym: str, row: dict) -> None:
    PRICE_DIR.mkdir(parents=True, exist_ok=True)
    p = PRICE_DIR / f"{sym}.jsonl"
    existing = []
    if p.exists():
        try:
            existing = p.read_text().splitlines()[-(WINDOW_ROWS - 1):]
        except Exception:
            existing = []
    existing.append(json.dumps(row))
    p.write_text("\n".join(existing) + "\n")


def _pct(a: float, b: float) -> float:
    """Percent change b→a."""
    if not b:
        return 0.0
    return (a - b) / b * 100.0


def _price_at_offset(series: list[dict], now_ts: int, offset_s: int) -> float | None:
    """Find the price closest to (now_ts - offset_s) within a ±30s tolerance."""
    target = now_ts - offset_s
    best   = None
    best_d = 45   # widen to 45s so we're forgiving of skipped ticks
    for r in series:
        d = abs(r["ts"] - target)
        if d <= best_d:
            best   = r
            best_d = d
    return best["price"] if best else None


def _load_entries() -> dict:
    if not ENTRY_STATE.exists():
        return {}
    try:
        return json.loads(ENTRY_STATE.read_text())
    except Exception:
        return {}


def _save_entries(d: dict) -> None:
    ENTRY_STATE.parent.mkdir(exist_ok=True)
    tmp = ENTRY_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True))
    tmp.replace(ENTRY_STATE)


def fetch_prices() -> dict[str, dict]:
    """One batched call to CoinGecko for the whole universe."""
    ids = ",".join(ALPACA_UNIVERSE.values())
    try:
        j = _get(CG_PRICE_URL, {
            "ids": ids,
            "vs_currencies": "usd",
            "include_24hr_vol":    "true",
            "include_24hr_change": "true",
            "include_last_updated_at": "true",
        })
    except Exception as e:
        return {}
    return j


def tick() -> None:
    now = int(time.time())
    cfg = load_config()
    prices = fetch_prices()
    if not prices:
        log_tick("momentum", f"coingecko fetch failed (cfg v{cfg['version']})")
        return

    # 1. append every symbol's latest tick
    for sym, cg_id in ALPACA_UNIVERSE.items():
        p = prices.get(cg_id)
        if not p:
            continue
        _append_series(sym, {
            "ts":      now,
            "price":   float(p.get("usd", 0)),
            "vol24h":  float(p.get("usd_24h_vol", 0) or 0),
            "chg24h":  float(p.get("usd_24h_change", 0) or 0),
        })

    # 2. score every symbol
    entries = _load_entries()
    entry_signals: list[dict] = []
    exit_signals:  list[dict] = []
    watching:      list[dict] = []
    scanned = 0

    for sym in ALPACA_UNIVERSE:
        series = _load_series(sym)
        if len(series) < 6:
            continue     # need at least ~5 minutes of history
        cur    = series[-1]
        px_now = cur["price"]

        # Fast windows: 2m / 4m / 6m so we react to short-term momentum.
        px_5m   = _price_at_offset(series, now, 120)
        px_10m  = _price_at_offset(series, now, 240)
        px_15m  = _price_at_offset(series, now, 360)
        if px_5m is None or px_10m is None:
            continue

        v_5m    = _pct(px_now, px_5m)         # 2m velocity
        v_10m5m = _pct(px_5m, px_10m)         # prior 2m velocity
        accel   = v_5m - v_10m5m              # 2nd derivative
        v_15m   = _pct(px_now, px_15m) if px_15m is not None else v_5m

        scanned += 1
        held = sym in entries
        watching.append({"sym": sym, "v_5m": v_5m, "accel": accel, "v_15m": v_15m,
                         "px": px_now, "held": held})

        # ── Entry logic ─────────────────────────────────────────
        cond = (v_5m >= cfg["entry_5m_pct"] and accel > cfg["entry_accel"])
        if cfg.get("require_v5m_gte_v15m", True):
            cond = cond and (v_5m >= v_15m)
        if cfg.get("min_24h_vol_usd", 0) > 0:
            cond = cond and (cur.get("vol24h", 0) >= cfg["min_24h_vol_usd"])
        if not held and cond:
            score = round(v_5m + accel + max(0, v_15m), 3)
            note  = f"5m:{v_5m:+.2f}%  accel:{accel:+.2f}pp  15m:{v_15m:+.2f}%  px:${px_now:.4f}  cfg=v{cfg['version']}"
            entry_signals.append({
                "sym": sym, "score": score, "px": px_now, "v_5m": v_5m,
                "accel": accel, "v_15m": v_15m, "note": note,
            })
            entries[sym] = {"entered_ts": now, "entry_px": px_now, "peak_px": px_now,
                            "score_at_entry": score, "cfg_version": cfg["version"]}

        # ── Track peak & exit logic ─────────────────────────────
        if held:
            entries[sym]["peak_px"] = max(entries[sym].get("peak_px", 0), px_now)
            entry_px = entries[sym]["entry_px"]
            peak_px  = entries[sym]["peak_px"]
            pnl_pct  = _pct(px_now, entry_px)
            drawdown = _pct(px_now, peak_px)     # negative = falling from peak
            entered_ts_ = entries[sym].get("entered_ts", now)
            cfg_ver_    = entries[sym].get("cfg_version", cfg["version"])

            do_exit = False
            reason  = ""
            if v_5m <= cfg["exit_5m_pct"]:
                do_exit = True; reason = f"5m turned {v_5m:.2f}%"
            elif accel <= cfg["exit_accel_deep"]:
                do_exit = True; reason = f"accel {accel:.2f}pp"
            elif drawdown <= cfg["exit_drawdown"]:
                do_exit = True; reason = f"gave back {drawdown:.2f}% from peak"

            if do_exit:
                score = round(abs(v_5m) + abs(accel) + max(0, -drawdown), 3)
                note = (f"EXIT {sym}: entry ${entry_px:.4f} → ${px_now:.4f}  "
                        f"pnl {pnl_pct:+.2f}%  peak {peak_px:.4f}  ({reason})")
                exit_signals.append({
                    "sym": sym, "score": score, "px": px_now, "pnl_pct": pnl_pct,
                    "reason": reason, "note": note,
                })
                _log_closed_trade(sym, entered_ts_, entry_px, now, px_now,
                                  pnl_pct, reason, cfg_ver_)
                del entries[sym]

    _save_entries(entries)

    # 3. emit — sorted so top of feed = highest-conviction
    entry_signals.sort(key=lambda s: s["score"], reverse=True)
    exit_signals.sort(key=lambda s: s["score"], reverse=True)

    for s in entry_signals[:8]:
        emit("momentum", "ENTRY", s["sym"], "alpaca",
             size_usd=0.0, direction="BUY",
             dedup_key=f"mo_ent:{s['sym']}:{now // 60}",
             note=f"ENTER {s['sym']}  score={s['score']}  {s['note']}",
             url=f"https://app.alpaca.markets/crypto/{s['sym']}USD",
             raw={"score": s["score"], "px": s["px"], "v_5m": s["v_5m"],
                  "accel": s["accel"], "v_15m": s["v_15m"]})

    for s in exit_signals[:8]:
        emit("momentum", "EXIT", s["sym"], "alpaca",
             size_usd=0.0, direction="SELL",
             dedup_key=f"mo_exit:{s['sym']}:{now // 60}",
             note=s["note"],
             url=f"https://app.alpaca.markets/crypto/{s['sym']}USD",
             raw={"score": s["score"], "px": s["px"], "pnl_pct": s["pnl_pct"],
                  "reason": s["reason"]})

    # 4. HEARTBEAT — always emit top-3 by momentum score (v_5m + accel) so the app
    # shows "what am I watching right now" every tick, not just when a signal fires.
    if watching:
        watching.sort(key=lambda w: (w["v_5m"] + w["accel"]), reverse=True)
        top     = watching[:3]
        bottom  = watching[-3:]
        held_syms = ",".join(sorted(entries.keys())) or "none"
        top_str = "  ".join(
            f"{w['sym']}:{w['v_5m']:+.2f}/{w['accel']:+.2f}" for w in top)
        bot_str = "  ".join(
            f"{w['sym']}:{w['v_5m']:+.2f}/{w['accel']:+.2f}" for w in bottom)
        emit_live("momentum",
                  f"scan cfg v{cfg['version']} — scanned {scanned} coins | "
                  f"held: {held_syms} | "
                  f"TOP: {top_str}  BOT: {bot_str}",
                  top=top, bottom=bottom, held=list(entries.keys()),
                  cfg_version=cfg["version"])

    log_tick("momentum",
             f"cfg v{cfg['version']} scanned={scanned} held={len(entries)} "
             f"entries={len(entry_signals)} exits={len(exit_signals)}")


if __name__ == "__main__":
    tick()
