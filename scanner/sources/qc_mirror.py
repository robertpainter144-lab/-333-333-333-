"""QC Portfolio → Alpaca Mirror.

Reads the daily-rebalanced weights + per-strategy allocations from
/Users/quarki3/qc_portfolio/portfolio_state.json, computes a target-weighted
ticker composition, then syncs the Alpaca account so it matches.

Uses fractional shares (Alpaca supports $1 min notional).

Env var ALPACA_MODE:
  paper (default) — mirror on paper account
  live            — mirror on live account ($50 real money)
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

QC_STATE = Path("/Users/quarki3/qc_portfolio/portfolio_state.json")
LEDGER   = STATEDIR / "qc_mirror_ledger.jsonl"
STATE    = STATEDIR / "qc_mirror_state.json"

# Total capital allocated to this mirror. On paper we simulate $50 to match
# what the user has in Alpaca live. On live it uses whatever the account has.
PAPER_BANKROLL_USD = 50.00

ALPACA_MODE   = os.environ.get("ALPACA_MODE", "paper")
ALPACA_BASE   = ("https://api.alpaca.markets" if ALPACA_MODE == "live"
                 else "https://paper-api.alpaca.markets")
ALPACA_KEY    = (os.environ.get("ALPACA_LIVE_KEY") if ALPACA_MODE == "live"
                 else os.environ.get("ALPACA_PAPER_KEY", ""))
ALPACA_SECRET = (os.environ.get("ALPACA_LIVE_SECRET") if ALPACA_MODE == "live"
                 else os.environ.get("ALPACA_PAPER_SECRET", ""))

MIN_ORDER_USD = 1.00              # Alpaca fractional min notional
REBALANCE_THRESHOLD_USD = 0.50    # skip a delta smaller than this


def _headers():
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY or "",
        "APCA-API-SECRET-KEY": ALPACA_SECRET or "",
        "Content-Type":        "application/json",
    }


def compute_target_weights() -> dict[str, float]:
    """Read portfolio_state.json weights + allocations → per-ticker % of book."""
    if not QC_STATE.exists():
        return {}
    try:
        d = json.loads(QC_STATE.read_text())
    except Exception:
        return {}
    weights     = d.get("weights", [])
    strategies  = d.get("strategies", [])
    allocations = d.get("allocations", {})   # {strategy_id: {ticker: fraction}}
    target: dict[str, float] = defaultdict(float)
    for i, s in enumerate(strategies):
        w = weights[i] if i < len(weights) else 0.0
        sid = str(s.get("id"))
        allocs = allocations.get(sid, {})
        for ticker, frac in allocs.items():
            target[ticker] += w * frac
    total = sum(target.values())
    if total <= 0:
        return {}
    # Normalize (should be ~1.0 already but be safe).
    return {t: v / total for t, v in target.items()}


def get_positions(target_syms: set[str] | None = None) -> dict[str, dict]:
    """Return {sym: {qty, market_value}} for equity positions.
    If target_syms is provided, ONLY return positions matching those symbols —
    this stops the mirror from touching unrelated equity from other experiments."""
    if not ALPACA_KEY:
        return {}
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/positions",
                         headers=_headers(), timeout=10)
        if not r.ok:
            return {}
        out = {}
        for p in r.json():
            sym = p["symbol"]
            if p.get("asset_class") != "us_equity":
                continue
            if target_syms is not None and sym not in target_syms:
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


def submit_order(sym: str, side: str, notional_usd: float | None = None,
                  qty: float | None = None) -> dict:
    if not ALPACA_KEY:
        return {"skipped": True}
    body = {"symbol": sym, "side": side.lower(), "type": "market",
            "time_in_force": "day"}
    if notional_usd is not None:
        body["notional"] = round(notional_usd, 2)
    if qty is not None:
        body["qty"] = str(round(qty, 6))
    try:
        r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=_headers(),
                          json=body, timeout=12)
        if not r.ok:
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        return r.json()
    except Exception as e:
        return {"error": repr(e)}


def _log_action(row: dict):
    STATEDIR.mkdir(exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")


def _market_open() -> bool:
    """True if US equities are open right now. Defaults False on API failure.
    While the market is closed, day market orders queue and positions never
    update, so every tick re-submits the same delta — 530 queued AMD buys
    (~$17.9k) accumulated over the 2026-07-25/26 weekend and all filled at
    Monday's open. Never submit orders while closed."""
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/clock", headers=_headers(), timeout=8)
        if r.ok:
            return bool(r.json().get("is_open"))
    except Exception:
        pass
    return False


def tick() -> None:
    now = int(time.time())
    targets = compute_target_weights()
    if not targets:
        log_tick("qc_mirror", "no QC targets")
        return
    if not ALPACA_KEY:
        emit_live("qc_mirror", "[skip] no Alpaca key configured")
        log_tick("qc_mirror", "skipped (no key)")
        return

    # ONLY look at positions in target set. Any pre-existing positions in
    # non-target symbols (from other tests) are left completely alone.
    target_syms = set(targets.keys())
    positions = get_positions(target_syms=target_syms)
    total_book = PAPER_BANKROLL_USD if ALPACA_MODE == "paper" else None
    if total_book is None:
        try:
            r = requests.get(f"{ALPACA_BASE}/v2/account",
                              headers=_headers(), timeout=10)
            total_book = float(r.json().get("equity", 0)) if r.ok else 0
        except Exception:
            total_book = 0
    if total_book <= 0:
        log_tick("qc_mirror", "no bankroll")
        return

    # Compute per-ticker desired dollar value
    desired = {t: v * total_book for t, v in targets.items()}
    if not _market_open():
        desired = {}
        log_tick("qc_mirror", "market closed — orders skipped")

    actions = []
    for sym, desired_usd in desired.items():
        current_mv = positions.get(sym, {}).get("mv", 0.0)
        delta = desired_usd - current_mv
        if abs(delta) < REBALANCE_THRESHOLD_USD:
            continue
        if delta > 0 and delta >= MIN_ORDER_USD:
            res = submit_order(sym, "buy", notional_usd=delta)
            actions.append({"sym": sym, "side": "BUY",
                             "delta_usd": round(delta, 2),
                             "result": ("ok:" + res.get("id","?")[:8])
                                       if "id" in res
                                       else res.get("error", "skipped")})
        elif delta < 0 and abs(delta) >= MIN_ORDER_USD:
            # Sell — Alpaca requires qty (not notional) for sells if wholeshare only;
            # for fractional, notional works. Use fractional sell.
            res = submit_order(sym, "sell", notional_usd=abs(delta))
            actions.append({"sym": sym, "side": "SELL",
                             "delta_usd": round(delta, 2),
                             "result": ("ok:" + res.get("id","?")[:8])
                                       if "id" in res
                                       else res.get("error", "skipped")})

    # NOTE: no pruning of non-target positions. Because we only see positions in
    # `target_syms`, we can never accidentally sell an unrelated experiment's
    # holdings. The mirror scope is strictly the target composition.

    # P/L computed only against our $50 mirror bankroll, ignoring other holdings.
    total_unreal = sum(p.get("unrealized_pl", 0) for p in positions.values())
    invested = sum(p.get("mv", 0) for p in positions.values())
    pnl_pct = round(total_unreal / max(invested, 1) * 100, 3) if invested else 0

    STATE.write_text(json.dumps({
        "ts":        now,
        "mode":      ALPACA_MODE,
        "bankroll":  round(total_book, 2),
        "targets":   {t: round(v * 100, 2) for t, v in targets.items()},
        "positions": positions,
        "total_unrealized": round(total_unreal, 2),
        "pnl_pct":   pnl_pct,
        "last_actions": actions,
    }, indent=2, default=float))
    if actions:
        for a in actions:
            _log_action({**a, "ts": now})
        emit_live("qc_mirror",
                  f"[mirror] {len(actions)} orders  pnl_pct={pnl_pct:+.2f}%  " +
                  "  ".join(f"{a['sym']}:{a['side']}${a['delta_usd']:+.2f}"
                            for a in actions[:4]))
    else:
        emit_live("qc_mirror",
                  f"[mirror] balanced  pnl_pct={pnl_pct:+.2f}%  book=${total_book:.2f}")
    log_tick("qc_mirror", f"acts={len(actions)} pnl_pct={pnl_pct:+.2f}%")


if __name__ == "__main__":
    tick()
