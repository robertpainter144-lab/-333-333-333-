"""Congressional PTR composition → Alpaca paper mirror.

RESEARCH DRAFT (2026-07-27) — not registered in main.py.

Mirrors state/congress_holdings.json (written by congress_feed.py from
official House Clerk PTR filings; default roster ["Nancy Pelosi"]) onto the
shared Alpaca paper account. Same shape as autopilot_holdings.json:

    {"updated_ts": 1753600000,
     "source": "House Clerk PTRs — Nancy Pelosi",
     "holdings": {"NVDA": 0.31, "AAPL": 0.22}}

`holdings` may also be a plain list of tickers → equal weight. Weights are
normalized, so relative sizes are enough.

Paper-only by design: PTR disclosures lag trades by up to 30/45 days, so
this source must prove that lagged mirroring still carries edge on the
$50-scale paper book before the governor ever awards it real weight.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

HOLDINGS = STATEDIR / "congress_holdings.json"
LEDGER   = STATEDIR / "congress_mirror_ledger.jsonl"
STATE    = STATEDIR / "congress_mirror_state.json"

PAPER_BANKROLL_USD = 50.00   # match the live book's scale

ALPACA_BASE   = "https://paper-api.alpaca.markets"
ALPACA_KEY    = os.environ.get("ALPACA_PAPER_KEY", "")
ALPACA_SECRET = os.environ.get("ALPACA_PAPER_SECRET", "")

MIN_ORDER_USD = 1.00
REBALANCE_THRESHOLD_USD = 0.50


def _headers():
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY or "",
        "APCA-API-SECRET-KEY": ALPACA_SECRET or "",
        "Content-Type":        "application/json",
    }


def composition() -> dict[str, float]:
    """Normalized {ticker: fraction} from congress_holdings.json."""
    if not HOLDINGS.exists():
        return {}
    try:
        d = json.loads(HOLDINGS.read_text())
    except Exception:
        return {}
    h = d.get("holdings") or {}
    if isinstance(h, list):
        h = {t: 1.0 for t in h}
    h = {str(t).upper(): float(w) for t, w in h.items() if float(w) > 0}
    total = sum(h.values())
    if total <= 0:
        return {}
    return {t: w / total for t, w in h.items()}


def get_positions(target_syms: set[str]) -> dict[str, dict]:
    """Positions in target symbols only — other experiments' holdings on the
    shared paper account are invisible to this mirror."""
    if not ALPACA_KEY:
        return {}
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=_headers(),
                         timeout=10)
        if not r.ok:
            return {}
        out = {}
        for p in r.json():
            sym = p["symbol"]
            if sym not in target_syms or p.get("asset_class") != "us_equity":
                continue
            out[sym] = {
                "qty": float(p.get("qty", 0)),
                "mv":  float(p.get("market_value", 0)),
                "avg_entry": float(p.get("avg_entry_price", 0)),
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
            }
        return out
    except Exception:
        return {}


def submit_order(sym: str, side: str, notional_usd: float) -> dict:
    if not ALPACA_KEY:
        return {"skipped": True}
    body = {"symbol": sym, "side": side.lower(), "type": "market",
            "time_in_force": "day", "notional": round(notional_usd, 2)}
    try:
        r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=_headers(),
                          json=body, timeout=12)
        if not r.ok:
            return {"error": f"HTTP {r.status_code}: {r.text[:150]}"}
        return r.json()
    except Exception as e:
        return {"error": repr(e)}


def _market_open() -> bool:
    """True if US equities are open right now. Defaults False on API failure.
    While the market is closed, day market orders queue and positions never
    update, so every tick re-submits the same delta (the qc_mirror 530-order
    AMD runaway of 2026-07-27). Never submit while closed."""
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/clock", headers=_headers(), timeout=8)
        if r.ok:
            return bool(r.json().get("is_open"))
    except Exception:
        pass
    return False


def tick() -> None:
    now = int(time.time())
    targets = composition()
    if not targets:
        log_tick("congress_mirror", "no holdings file yet — idle")
        return
    if not ALPACA_KEY:
        log_tick("congress_mirror", "skipped (no key)")
        return

    # Include symbols the previous tick targeted so positions dropped from
    # the composition (disclosed sells) remain visible and get liquidated.
    prev_syms: set[str] = set()
    try:
        st = json.loads(STATE.read_text())
        prev_syms = set(st.get("tracked_syms") or st.get("targets", {}))
    except Exception:
        pass
    target_syms = set(targets.keys())
    positions = get_positions(target_syms | prev_syms)

    desired = {t: v * PAPER_BANKROLL_USD for t, v in targets.items()}
    if not _market_open():
        desired = {}
        log_tick("congress_mirror", "market closed — orders skipped")

    actions = []
    for sym, want in desired.items():
        cur = positions.get(sym, {}).get("mv", 0.0)
        delta = want - cur
        if abs(delta) < REBALANCE_THRESHOLD_USD:
            continue
        if delta > 0 and delta >= MIN_ORDER_USD:
            res = submit_order(sym, "buy", delta)
        elif delta < 0 and abs(delta) >= MIN_ORDER_USD:
            res = submit_order(sym, "sell", abs(delta))
        else:
            continue
        actions.append({"sym": sym, "side": "BUY" if delta > 0 else "SELL",
                        "delta_usd": round(delta, 2),
                        "result": ("ok:" + res.get("id", "?")[:8]) if "id" in res
                                  else res.get("error", "skipped")[:60]})

    # Symbols the composition dropped (disclosed sells) but we still hold
    if _market_open():
        for sym, p in positions.items():
            if sym in desired or p.get("mv", 0.0) < MIN_ORDER_USD:
                continue
            res = submit_order(sym, "sell", p["mv"])
            actions.append({"sym": sym, "side": "SELL",
                            "delta_usd": round(-p["mv"], 2),
                            "result": ("ok:" + res.get("id", "?")[:8])
                                      if "id" in res
                                      else res.get("error", "skipped")[:60]})

    total_unreal = sum(p.get("unrealized_pl", 0) for p in positions.values())
    invested = sum(p.get("mv", 0) for p in positions.values())
    pnl_pct = round(total_unreal / max(invested, 1) * 100, 3) if invested else 0.0

    STATE.write_text(json.dumps({
        "ts": now,
        "mode": "paper",
        "bankroll": PAPER_BANKROLL_USD,
        "targets": {t: round(v * 100, 2) for t, v in targets.items()},
        # dropped-but-still-held names stay tracked until fully liquidated
        "tracked_syms": sorted(target_syms | set(positions)),
        "positions": positions,
        "invested": round(invested, 2),
        "total_unrealized": round(total_unreal, 2),
        "pnl_pct": pnl_pct,
        "last_actions": actions,
    }, indent=2, default=float))
    if actions:
        with LEDGER.open("a") as f:
            for a in actions:
                f.write(json.dumps({"ts": now, **a}) + "\n")
        emit_live("congress_mirror",
                  f"[congress] {len(actions)} orders  pnl_pct={pnl_pct:+.2f}%")
    log_tick("congress_mirror", f"acts={len(actions)} pnl_pct={pnl_pct:+.2f}%")


if __name__ == "__main__":
    tick()
