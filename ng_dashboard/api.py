"""
JSON REST API for the Trading Android app.

Mounted on the NiceGUI FastAPI app at /api/*.
Returns plain JSON (no NiceGUI components) so an Android client can call it
over Tailscale without any HTML scraping.

All endpoints are read-only here except /api/bots/* which proxies to the same
helpers the web UI uses.
"""
from __future__ import annotations

import csv
import json
import os
import platform
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
import asyncio
from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

ET = ZoneInfo("America/New_York")

# ── Source registry ───────────────────────────────────────────────────────────
# Each source is one bucket of trading activity the Android app can browse.
SOURCES: list[dict] = [
    {"id": "alpaca_live", "label": "Alpaca Live",  "kind": "live",   "real_money": True,  "color": "#ffd60a"},
    {"id": "paper",  "label": "Alpaca Paper",     "kind": "paper",  "real_money": False, "color": "#58a6ff"},
    {"id": "broker", "label": "Alpaca Broker SB", "kind": "paper",  "real_money": False, "color": "#bc8cff"},
    {"id": "shadow", "label": "Shadow Bot",       "kind": "test",   "real_money": False, "color": "#d29922"},
    {"id": "qcmirror", "label": "QC Mirror (sim)", "kind": "paper", "real_money": False, "color": "#bc8cff"},
]

# True seed values per source — Alpaca's `last_equity` is yesterday's close,
# not the original funding, so the dashboard mislabels returns as ~0% unless
# we override it here.
STARTING_CAPITAL = {
    "paper":  100_000.0,   # Alpaca paper default
    "broker": 50_000.0,    # Sandbox seed
    "alpaca_live": 50.0,   # $50 ACH deposit 2026-07-02
}

IBKR_DIR         = Path("/Users/quarki3/ibkr_bot")
ALPACA_LIVE_DIR  = Path("/Users/quarki3/alpaca_live_arm_state")

# On Hetzner this data lives at /opt/trading; on Mac we mirror it under ~/trading-data
# so QC + insider tabs keep working after we shut Hetzner down.
TRADING_DATA_DIR = (Path("/opt/trading") if Path("/opt/trading").exists()
                    else Path.home() / "trading-data")
QC_DIR    = Path("/Users/quarki3/qc_portfolio")
SHADOW_DIR = Path("/Users/quarki3/qc_portfolio_test")

# Real-money (IBKR) data lives on the DigitalOcean droplet that owns IB Gateway.
# This backend (Hetzner) talks to it over tailnet. Read-only.
IBKR_SIDECAR_URL = "http://100.112.115.66:8082"
IBKR_SIDECAR_TIMEOUT = 4.0


def _ibkr_sidecar_get(path: str) -> dict | list | None:
    """GET <SIDECAR>/<path>; returns None on failure (caller handles empty)."""
    try:
        r = requests.get(f"{IBKR_SIDECAR_URL}{path}", timeout=IBKR_SIDECAR_TIMEOUT)
        if r.ok:
            return r.json()
    except Exception as e:
        print(f"[API] IBKR sidecar {path}: {e}")
    return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _now_iso() -> str:
    return datetime.now(ET).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _read_csv(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open("r", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if limit is not None:
            rows = rows[-limit:]
        return rows
    except Exception:
        return []


def _tailscale_ip() -> str | None:
    try:
        import subprocess
        r = subprocess.run(
            ["/opt/homebrew/opt/tailscale/bin/tailscale",
             "--socket=/opt/homebrew/var/run/tailscaled.socket", "ip", "--4"],
            capture_output=True, text=True, timeout=2,
        )
        ip = r.stdout.strip().splitlines()[0] if r.stdout else None
        return ip or None
    except Exception:
        return None


# ── Data adapters ─────────────────────────────────────────────────────────────
def _portfolio_paper(main_mod) -> dict:
    acct = main_mod._paper("GET", "/v2/account") or {}
    # Empty acct = API failure. Fall through to the stale placeholder instead of
    # letting _build_portfolio synthesize equity=0 / drawdown=100% with
    # stale:False (the 2026-07-27 "placeholder rendered as live data" bug).
    if not acct:
        return _empty_portfolio("paper")
    pos  = main_mod._paper("GET", "/v2/positions") or []
    return _build_portfolio(acct, pos, source="paper")


def _portfolio_broker(main_mod) -> dict:
    acct = main_mod._alp("GET", "/account") or {}
    if not acct:
        return _empty_portfolio("broker")
    pos  = main_mod._alp("GET", "/positions") or []
    return _build_portfolio(acct, pos, source="broker")


def _portfolio_shadow(main_mod) -> dict:
    s = getattr(main_mod, "_shadow", None)
    if s is None:
        return _empty_portfolio("shadow")
    state = s.state
    prices = main_mod._cache.get("prices", {}) or {}
    equity = s.equity(prices)
    held = []
    for sym, h in state.get("positions", {}).items():
        qty = _f(h.get("qty"))
        cost = _f(h.get("cost"))
        px = _f(prices.get(sym, cost / qty if qty else 0))
        mv = qty * px
        avg = cost / qty if qty else 0
        upl = (px - avg) * qty
        held.append({
            "symbol": sym,
            "qty": qty,
            "avg_cost": round(avg, 4),
            "market_price": round(px, 4),
            "market_value": round(mv, 2),
            "unrealized_pl": round(upl, 2),
            "unrealized_pl_pct": round(((px / avg) - 1) * 100, 2) if avg else 0.0,
            "entry_date": h.get("entry_date"),
        })
    cash = _f(state.get("cash"))
    peak = _f(state.get("peak_equity", equity))
    return {
        "source": "shadow",
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "bp": round(cash, 2),
        "unrealized_pl": round(sum(p["unrealized_pl"] for p in held), 2),
        "starting_capital": _f(state.get("initial_cash", 50000)),
        "peak_equity": round(peak, 2),
        "drawdown_pct": round((peak - equity) / peak * 100, 2) if peak else 0.0,
        "positions": held,
        "position_count": len(held),
        "ts": _now_iso(),
    }


def _ibkr_local_snapshot() -> dict | None:
    """Read the Mac-local positions_snapshot.json that equity_logger.py writes.
    Returns None if the file is missing. The remote ibkr-trader sidecar is
    deprecated — Mac's IBKR bot is now the source of truth."""
    snap_path = IBKR_DIR / "positions_snapshot.json"
    if not snap_path.exists():
        return None
    try:
        return json.loads(snap_path.read_text())
    except Exception:
        return None


def _ibkr_local_equity_history(limit: int = 5000) -> list[dict]:
    """Read equity_history.csv written by equity_logger.py."""
    eq_path = IBKR_DIR / "equity_history.csv"
    if not eq_path.exists():
        return []
    rows: list[dict] = []
    try:
        with eq_path.open() as f:
            rdr = csv.DictReader(f)
            for row in rdr:
                rows.append({
                    "ts": row.get("timestamp", ""),
                    "equity": float(row.get("equity") or 0),
                })
    except Exception:
        return []
    return rows[-limit:]


def _ibkr_local_transactions(limit: int = 100) -> list[dict]:
    """Read transactions.csv written by main.py — returns RAW csv dicts so
    _activity_ibkr can transform them consistently (it also fell back to the
    sidecar, which returned raw rows too, so the shapes need to match)."""
    tx_path = IBKR_DIR / "transactions.csv"
    if not tx_path.exists():
        return []
    rows: list[dict] = []
    try:
        with tx_path.open() as f:
            rdr = csv.DictReader(f)
            for row in rdr:
                rows.append(dict(row))
    except Exception:
        return []
    return rows[-limit:][::-1]  # newest first


# ── Alpaca Live (real-money $50 account) ─────────────────────────────────────
_ALPACA_LIVE_CLIENT = None

def _alpaca_live_client():
    """Cached TradingClient for the live Alpaca account. Returns None if
    ALPACA_LIVE_KEY / ALPACA_LIVE_SECRET aren't in the env."""
    global _ALPACA_LIVE_CLIENT
    if _ALPACA_LIVE_CLIENT is not None:
        return _ALPACA_LIVE_CLIENT
    key = os.environ.get("ALPACA_LIVE_KEY")
    sec = os.environ.get("ALPACA_LIVE_SECRET")
    if not (key and sec):
        return None
    try:
        from alpaca.trading.client import TradingClient
        _ALPACA_LIVE_CLIENT = TradingClient(api_key=key, secret_key=sec, paper=False)
        return _ALPACA_LIVE_CLIENT
    except Exception as e:
        print(f"[API] alpaca_live client init: {e}")
        return None


def _alpaca_live_equity_history(limit: int = 5000) -> list[dict]:
    """Read equity.csv written by alpaca_paper_arm.py --env live --watch."""
    eq_path = ALPACA_LIVE_DIR / "equity.csv"
    if not eq_path.exists():
        return []
    rows: list[dict] = []
    try:
        with eq_path.open() as f:
            rdr = csv.DictReader(f)
            for row in rdr:
                rows.append({
                    "ts": row.get("ts", ""),
                    "equity": float(row.get("equity") or 0),
                    "cash":   float(row.get("cash") or 0),
                })
    except Exception:
        return []
    return rows[-limit:]


def _alpaca_live_transactions(limit: int = 100) -> list[dict]:
    """Fetch fills from Alpaca live account activities. Empty on missing keys."""
    key = os.environ.get("ALPACA_LIVE_KEY")
    sec = os.environ.get("ALPACA_LIVE_SECRET")
    if not (key and sec):
        return []
    # Raw REST — this alpaca-py TradingClient predates get_account_activities
    # (same workaround as _alpaca_live_transfers).
    try:
        import requests
        resp = requests.get(
            "https://api.alpaca.markets/v2/account/activities",
            params={"activity_types": "FILL", "direction": "desc",
                    "page_size": min(limit, 100)},
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
            timeout=10)
        resp.raise_for_status()
        acts = resp.json()
    except Exception as e:
        print(f"[API] alpaca_live activities: {e}")
        return []
    rows: list[dict] = []
    for d in acts[:limit]:
        if not isinstance(d, dict):
            continue
        rows.append({
            "ts":       str(d.get("transaction_time") or d.get("date") or ""),
            "symbol":   d.get("symbol", "?"),
            "side":     str(d.get("side", "")).lower(),
            "qty":      _f(d.get("qty")),
            "price":    _f(d.get("price")),
            "notional": _f(d.get("qty")) * _f(d.get("price")),
            "status":   "filled",
            "type":     str(d.get("type", "fill")),
            "tif":      "DAY",
            "order_id": str(d.get("order_id", "")),
            "reason":   "",
            "source":   "alpaca_live",
        })
    # direction=desc already returns newest first
    # Join each fill to the combined_mirror ledger's decision context so the
    # Book's Recent actions can say WHY a trade happened, not just what.
    try:
        from datetime import datetime as _dt
        led = Path("/Users/quarki3/scanner/state/combined_mirror_ledger.jsonl")
        entries = []
        if led.exists():
            for ln in led.read_text().splitlines()[-400:]:
                try:
                    entries.append(json.loads(ln))
                except Exception:
                    pass
        for r in rows:
            if r.get("reason"):
                continue
            try:
                fts = _dt.fromisoformat(str(r["ts"]).replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            best = None
            for e in entries:
                if e.get("sym") != r.get("symbol"):
                    continue
                if str(e.get("side", "")).lower() != str(r.get("side", "")).lower():
                    continue
                dt_s = abs(e.get("ts", 0) - fts)
                if dt_s <= 1200 and (best is None or dt_s < best[0]):
                    best = (dt_s, e)
            if best and best[1].get("why"):
                r["reason"] = best[1]["why"]
    except Exception:
        pass
    return rows


_TRANSFERS_CACHE: dict = {"ts": 0.0, "rows": []}
_TRANSFERS_TTL = 600  # seconds — transfers are rare; don't hammer the activities API


def _alpaca_live_transfers() -> list[dict]:
    """Cash deposits/withdrawals (CSD/CSW) on the live account, oldest first.

    Feeds the Robinhood-style P/L math in the Terminal: the chart subtracts
    net transfers so a deposit never shows up as a "gain". Signed amount:
    positive = money in, negative = money out.
    NB: re-clobbered by the 2026-08-04 epoch rewrite once already — if you are
    refactoring this file from a .bak, KEEP THIS BLOCK (and the contributed-
    capital block in _build_portfolio, and the /api/transfers route).
    """
    now = time.time()
    if now - _TRANSFERS_CACHE["ts"] < _TRANSFERS_TTL:
        return _TRANSFERS_CACHE["rows"]
    key = os.environ.get("ALPACA_LIVE_KEY")
    sec = os.environ.get("ALPACA_LIVE_SECRET")
    if not (key and sec):
        return _TRANSFERS_CACHE["rows"]
    # Raw REST — the VM's alpaca-py TradingClient predates get_account_activities.
    try:
        import requests
        resp = requests.get(
            "https://api.alpaca.markets/v2/account/activities",
            params={"activity_types": "CSD,CSW", "page_size": 100},
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
            timeout=10)
        resp.raise_for_status()
        acts = resp.json()
    except Exception as e:
        print(f"[API] alpaca_live transfers: {e}")
        return _TRANSFERS_CACHE["rows"]
    rows: list[dict] = []
    for d in acts:
        if not isinstance(d, dict):
            continue
        amt = _f(d.get("net_amount"))
        kind = str(d.get("activity_type", ""))
        if "CSW" in kind and amt > 0:
            amt = -amt  # withdrawals arrive positive from some SDK versions
        rows.append({
            "ts":     str(d.get("date") or d.get("transaction_time") or ""),
            "amount": round(amt, 2),
            "type":   "deposit" if amt >= 0 else "withdrawal",
        })
    rows.sort(key=lambda r: r["ts"])
    _TRANSFERS_CACHE.update(ts=now, rows=rows)
    return rows


def _portfolio_alpaca_live() -> dict:
    client = _alpaca_live_client()
    if client is None:
        return _empty_portfolio("alpaca_live")
    try:
        acct = client.get_account().model_dump()
        positions = [p.model_dump() for p in client.get_all_positions()]
    except Exception as e:
        print(f"[API] alpaca_live portfolio: {e}")
        return _empty_portfolio("alpaca_live")
    return _build_portfolio(acct, positions, source="alpaca_live")


def _portfolio_ibkr() -> dict:
    # Prefer Mac-local file (equity_logger writes it every minute); fall back to
    # legacy remote sidecar only if local snapshot missing.
    snap = _ibkr_local_snapshot() or _ibkr_sidecar_get("/positions")
    if not snap:
        return _empty_portfolio("ibkr")
    pos_raw = snap.get("positions", []) or []
    held = []
    for p in pos_raw:
        qty = _f(p.get("qty"))
        avg = _f(p.get("avg_cost"))
        px = _f(p.get("market_price", avg))
        mv = _f(p.get("market_value", qty * px))
        upl = _f(p.get("unrealized_pl", (px - avg) * qty))
        held.append({
            "symbol": p.get("symbol", "?"),
            "qty": qty,
            "avg_cost": round(avg, 4),
            "market_price": round(px, 4),
            "market_value": round(mv, 2),
            "unrealized_pl": round(upl, 2),
            "unrealized_pl_pct": round(((px / avg) - 1) * 100, 2) if avg else 0.0,
        })
    equity = _f(snap.get("equity"))
    cash = _f(snap.get("cash"))
    bp = _f(snap.get("bp", cash))
    # Peak equity from local equity_history.csv (fall back to legacy sidecar)
    peak = equity
    try:
        rows = _ibkr_local_equity_history() or _ibkr_sidecar_get("/equity_history") or []
        if rows:
            peak = max(_f(r.get("equity")) for r in rows) or equity
    except Exception:
        pass
    return {
        "source": "ibkr",
        "account": snap.get("account"),
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "bp": round(bp, 2),
        "unrealized_pl": round(sum(p["unrealized_pl"] for p in held), 2),
        "starting_capital": 500.0,
        "peak_equity": round(peak, 2),
        "drawdown_pct": round((peak - equity) / peak * 100, 2) if peak else 0.0,
        "positions": held,
        "position_count": len(held),
        "ts": snap.get("ts") or _now_iso(),
    }


def _build_portfolio(acct: dict, pos: list, source: str) -> dict:
    held = []
    for p in pos:
        qty = _f(p.get("qty"))
        avg = _f(p.get("avg_entry_price"))
        px = _f(p.get("current_price", avg))
        mv = _f(p.get("market_value", qty * px))
        upl = _f(p.get("unrealized_pl", (px - avg) * qty))
        upl_pct = _f(p.get("unrealized_plpc", 0)) * 100
        if upl_pct == 0 and avg:
            upl_pct = ((px / avg) - 1) * 100
        held.append({
            "symbol": p.get("symbol", "?"),
            "qty": qty,
            "avg_cost": round(avg, 4),
            "market_price": round(px, 4),
            "market_value": round(mv, 2),
            "unrealized_pl": round(upl, 2),
            "unrealized_pl_pct": round(upl_pct, 2),
        })
    equity = _f(acct.get("equity") or acct.get("portfolio_value"))
    cash = _f(acct.get("cash"))
    bp = _f(acct.get("buying_power", cash))
    # Cash accounts: `cash` includes unsettled T+1 proceeds; only
    # non_marginable_buying_power is spendable today. None when broker
    # doesn't report it (paper/margin accounts).
    _settled = acct.get("non_marginable_buying_power")
    settled_bp = round(_f(_settled), 2) if _settled is not None else None
    start = STARTING_CAPITAL.get(source, _f(acct.get("last_equity") or 50000))
    if source == "alpaca_live":
        # Deposit-aware baseline: contributed capital = Σ deposits − withdrawals
        # (CSD/CSW), so a new ACH never reads as unrealized "gain" and a
        # withdrawal never reads as a loss (Robinhood semantics). Falls back to
        # the static seed if the activities API is unavailable.
        _xf = _alpaca_live_transfers()
        _contributed = round(sum(x["amount"] for x in _xf), 2)
        if _contributed > 0:
            start = _contributed
    # PERSISTENT PEAK — one file per source. Every call updates it to the
    # max of prior peak and current equity. So peak is genuinely the highest
    # equity seen since inception, never just a mirror of current equity.
    _peak_dir = TRADING_DATA_DIR / "portfolio_peaks"
    try:
        _peak_dir.mkdir(exist_ok=True)
    except Exception:
        pass
    _peak_file = _peak_dir / f"{source}.json"
    prev_peak = start
    try:
        import sys as _sys
        print(f'[PEAK-DBG] source={source}  file={_peak_file}  exists={_peak_file.exists()}', file=_sys.stderr, flush=True)
        if _peak_file.exists():
            prev_peak = float(json.loads(_peak_file.read_text()).get("peak", start))
            print(f'[PEAK-DBG]   read prev_peak={prev_peak}', file=_sys.stderr, flush=True)
    except Exception:
        pass
    peak = max(prev_peak, equity, start)
    # Always write so the file exists as a persistent record (peak monotone).
    try:
        _peak_file.write_text(json.dumps({"peak": peak, "ts": _now_iso()}))
    except Exception:
        pass
    dd = ((peak - equity) / peak * 100) if peak > 0 else 0.0
    # UNREAL must always equal (equity - starting_capital) so the header value
    # can never drift from the equity number. If we used sum(positions.unrealized_pl)
    # we'd get Alpaca's two-source pricing lag between the account-level equity
    # and per-position marks. Deriving from equity guarantees consistency.
    unreal = round(equity - start, 2)
    return {
        "source": source,
        "equity": round(equity, 2),
        "cash": round(cash, 2),
        "bp": round(bp, 2),
        "settled_bp": settled_bp,
        "unrealized_pl": unreal,
        "starting_capital": round(start, 2),
        "peak_equity": round(peak, 2),
        "drawdown_pct": round(dd, 2),
        "positions": held,
        "position_count": len(held),
        "stale": False,
        "ts": _now_iso(),
    }


def _empty_portfolio(source: str) -> dict:
    # Broker fetch failed. `stale: True` MUST be honored by every frontend —
    # rendering these placeholder numbers as live data made all the stat
    # boxes show the same $50.00 for days (the 2026-07-27 keyless-orphan bug).
    start = STARTING_CAPITAL.get(source, 0.0)
    return {
        "source": source, "equity": start, "cash": start, "bp": start,
        "settled_bp": None,
        "unrealized_pl": 0.0, "starting_capital": start, "peak_equity": start,
        "drawdown_pct": 0.0, "positions": [], "position_count": 0,
        "stale": True,
        "ts": _now_iso(),
    }


# ── Activity adapters ────────────────────────────────────────────────────────
def _activity_paper(main_mod, limit: int) -> list[dict]:
    orders = main_mod._paper("GET", f"/v2/orders?status=all&limit={limit}") or []
    return [_norm_alpaca_order(o, "paper") for o in orders]


def _activity_broker(main_mod, limit: int) -> list[dict]:
    orders = main_mod._alp("GET", f"/orders?status=all&limit={limit}") or []
    return [_norm_alpaca_order(o, "broker") for o in orders]


def _norm_alpaca_order(o: dict, source: str) -> dict:
    ts = o.get("filled_at") or o.get("submitted_at") or o.get("created_at") or ""
    fill_px = _f(o.get("filled_avg_price"))
    qty = _f(o.get("filled_qty") or o.get("qty"))
    notional = qty * fill_px if fill_px else _f(o.get("notional"))
    return {
        "source": source,
        "ts": ts,
        "symbol": o.get("symbol", "?"),
        "side": (o.get("side") or "").upper(),
        "qty": qty,
        "price": fill_px,
        "notional": round(notional, 2),
        "status": o.get("status", "").upper(),
        "type": o.get("type", ""),
        "tif": o.get("time_in_force", ""),
        "order_id": o.get("id"),
        "reason": "",
    }


def _activity_ibkr(limit: int) -> list[dict]:
    # Prefer local Mac transactions.csv; fall back to legacy remote sidecar
    rows = _ibkr_local_transactions(limit) or _ibkr_sidecar_get(f"/transactions?limit={limit}") or []
    out = []
    for r in rows:
        qty = _f(r.get("totalQuantity")) or _f(r.get("filled"))
        fill_px = _f(r.get("avgFillPrice")) or _f(r.get("limitPrice"))
        cash_qty = _f(r.get("cashQty"))
        notional = cash_qty if cash_qty else qty * fill_px
        out.append({
            "source": "ibkr",
            "ts": r.get("ts", ""),
            "symbol": r.get("symbol", "?"),
            "side": (r.get("action") or "").upper(),
            "qty": qty,
            "price": fill_px,
            "notional": round(notional, 2),
            "status": (r.get("status") or "").upper(),
            "type": "LMT" if _f(r.get("limitPrice")) else "MKT",
            "tif": "GTC",
            "order_id": r.get("orderId"),
            "reason": r.get("reason", ""),
        })
    out.reverse()
    return out


def _activity_shadow(main_mod, limit: int) -> list[dict]:
    s = getattr(main_mod, "_shadow", None)
    if s is None:
        return []
    tx_path = SHADOW_DIR / "transactions.csv"
    rows = _read_csv(tx_path)
    out = []
    for r in rows[-limit:]:
        qty = _f(r.get("qty"))
        px = _f(r.get("price"))
        out.append({
            "source": "shadow",
            "ts": r.get("ts", ""),
            "symbol": r.get("symbol", "?"),
            "side": (r.get("side") or "").upper(),
            "qty": qty,
            "price": px,
            "notional": round(qty * px, 2),
            "status": "FILLED",
            "type": "MKT",
            "tif": "DAY",
            "order_id": "",
            "reason": r.get("reason", ""),
        })
    out.reverse()
    return out


# ── Period helpers ────────────────────────────────────────────────────────────
# Periods exposed to the Android client: 1H, 1D, 1W, 1M, 3M, 1Y, 5Y, MAX.
_PERIOD_SECONDS = {
    "1H":  3600,
    "1D":  86_400,
    "1W":  86_400 * 7,
    "1M":  86_400 * 31,
    "3M":  86_400 * 93,
    "1Y":  86_400 * 366,
    "5Y":  86_400 * 366 * 5,
}

_ALPACA_PERIOD_ARGS = {
    "1H":  ("1D",  "5Min"),
    "1D":  ("1D",  "5Min"),
    "1W":  ("1W",  "15Min"),
    "1M":  ("1M",  "1D"),
    "3M":  ("3M",  "1D"),
    "1Y":  ("1A",  "1D"),
    "5Y":  ("5A",  "1D"),
    "MAX": ("20A", "1D"),
}


def _period_cutoff(period: str) -> float | None:
    """Unix-ts threshold; points older than this are filtered. None = MAX (keep all)."""
    secs = _PERIOD_SECONDS.get(period.upper())
    if secs is None:
        return None
    return time.time() - secs


def _ts_to_epoch(ts) -> float | None:
    """Coerce ISO string or epoch into a float epoch second. Returns None on failure."""
    if ts is None or ts == "":
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts)
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _filter_by_period(points: list[dict], period: str) -> list[dict]:
    cutoff = _period_cutoff(period)
    if cutoff is None:
        return points
    out = []
    for p in points:
        e = _ts_to_epoch(p.get("ts"))
        if e is not None and e >= cutoff:
            out.append(p)
    return out


# ── Equity-curve adapters ────────────────────────────────────────────────────
def _equity_paper(main_mod, period: str, timeframe: str) -> list[dict]:
    p, tf = _ALPACA_PERIOD_ARGS.get(period.upper(), (period, timeframe))
    hist = main_mod._paper(
        "GET", f"/v2/account/portfolio/history?period={p}&timeframe={tf}") or {}
    return _alpaca_history_to_points(hist)


def _equity_broker(main_mod, period: str, timeframe: str) -> list[dict]:
    # Broker sandbox doesn't expose portfolio/history; synthesise a flat curve
    # spanning the requested period (10 sample points).
    acct = main_mod._alp("GET", "/account") or {}
    eq = _f(acct.get("equity") or acct.get("portfolio_value"))
    now = int(time.time())
    span = _PERIOD_SECONDS.get(period.upper(), 86_400 * 31)
    step = max(span // 10, 60)
    return [{"ts": now - span + i * step, "equity": eq} for i in range(11)]


def _equity_shadow(main_mod, period: str) -> list[dict]:
    s = getattr(main_mod, "_shadow", None)
    if s is None:
        return []
    out = []
    for row in s.state.get("equity_history", []):
        ts = row.get("ts") or row.get("t")
        eq = _f(row.get("equity") or row.get("eq"))
        if ts and eq:
            out.append({"ts": ts, "equity": eq})
    return _filter_by_period(out, period)


def _equity_ibkr(period: str) -> list[dict]:
    # Prefer local equity_history.csv; the local reader returns {ts, equity} already
    local_rows = _ibkr_local_equity_history()
    if local_rows:
        points = [{"ts": r["ts"], "equity": r["equity"]} for r in local_rows if r["ts"]]
    else:
        rows = _ibkr_sidecar_get("/equity_history") or []
        points = [
            {"ts": r.get("timestamp"), "equity": _f(r.get("equity"))}
            for r in rows if r.get("timestamp")
        ]
    return _filter_by_period(points, period)


def _equity_alpaca_live(period: str) -> list[dict]:
    # Prefer Alpaca's real portfolio history — it has genuine equity values
    # across the VM-outage window, so no straight interpolated line. Fall back
    # to the local CSV + live-bridge approach only if the API is unavailable.
    ph = _alpaca_portfolio_history(period)
    if len(ph) >= 2:
        points = list(ph)  # copy: ph may be the _PH_CACHE list, don't mutate it
    else:
        rows = _alpaca_live_equity_history()
        points = [{"ts": r["ts"], "equity": r["equity"]} for r in rows if r["ts"]]
        points = _filter_by_period(points, period)
    # Pin the last point to LIVE account equity so the chart's tail matches the
    # EquityCard number — and, critically, so a same-day deposit/withdrawal has
    # a real equity step to align against. Alpaca's daily portfolio-history bar
    # for a trading day only appears ~24h after that day closes, so right after
    # a deposit `ph`'s last bar still predates it. Without this live tail point,
    # EquityChart.tsx's transfer-snap finds no matching step and subtracts the
    # deposit from the stale pre-deposit balance — the equity/transfers cliff
    # (e.g. 48.14 − 450 ≈ -402) seen right after a same-day deposit.
    try:
        cli = _alpaca_live_client()
        if cli is not None:
            acc = cli.get_account().model_dump()
            live_eq = float(acc.get("equity", 0))
            if live_eq > 0:
                now_ts = int(time.time())
                last_eq = float(points[-1]["equity"]) if points else 0.0
                if not points or abs(last_eq - live_eq) > 0.005:
                    points.append({"ts": now_ts, "equity": round(live_eq, 4)})
    except Exception:
        pass
    return points


def _cap_display_targets(t: dict, cap_pct: float = 35.0) -> dict:
    """Apply the real single-name cap (D6: 0.35) to the displayed governor-blend
    target so the allocation bar reflects what the bot could ACTUALLY execute,
    not the raw pre-cap blend. The frozen Jul-27 snapshot showed AAPL 90%
    (uncapped); with the cap no name exceeds 35% and the remainder reads as
    cash — the honest picture. Values are percentages (e.g. 90.0)."""
    if not isinstance(t, dict) or not t:
        return t
    out: dict = {}
    noncash = 0.0
    for k, v in t.items():
        if str(k).lower() == "cash":
            continue
        try:
            cv = min(float(v), cap_pct)
        except (TypeError, ValueError):
            continue
        out[k] = round(cv, 2)
        noncash += cv
    out["cash"] = round(max(0.0, 100.0 - noncash), 2)
    return out


def _alpaca_history_to_points(hist: dict) -> list[dict]:
    eq = hist.get("equity") or []
    ts = hist.get("timestamp") or []
    return [{"ts": int(t), "equity": _f(e)} for t, e in zip(ts, eq)]


_PH_CACHE: dict = {}


def _alpaca_portfolio_history(period: str) -> list[dict]:
    """Real account equity series from Alpaca's own portfolio-history API.
    Replaces the dead-CSV + single-bridge-point approach so the chart shows
    actual equity across the 2026-07 VM outage instead of a straight
    interpolated line. Returns [] on missing keys / error (caller falls back).
    """
    key = os.environ.get("ALPACA_LIVE_KEY")
    sec = os.environ.get("ALPACA_LIVE_SECRET")
    if not (key and sec):
        return []
    # Finest timeframe Alpaca allows per period. Alpaca rejects intraday
    # (1H/15Min) for a full month, so 1M/3M/6M are daily — but a daily-only
    # line renders a normal ±1%% day as a jarring single-point spike. Fix: for
    # the medium views, SPLICE a fine-grained recent window (15Min, where the
    # deposit + recent volatility live) onto the daily older data, so the
    # busy recent stretch is smooth and only the quiet older weeks are daily.
    splice = {"1M": "1W", "3M": "1W", "6M": "1W", "1Y": "1W", "5Y": "1W"}
    pmap = {"1D": ("1D", "5Min"), "1W": ("1W", "15Min"), "1M": ("1M", "1D"),
            "3M": ("3M", "1D"), "6M": ("6M", "1D"), "1Y": ("1A", "1D"),
            "5Y": ("5A", "1D")}
    per, tf = pmap.get(period, ("1M", "1D"))
    ck = f"{per}:{tf}:spliced"
    now = time.time()
    c = _PH_CACHE.get(ck)
    if c and now - c["ts"] < 60:
        return c["rows"]

    def _fetch(p, t):
        try:
            import requests
            # Continuous (24/7) reporting for intraday windows — matches
            # Alpaca's own dashboard, which re-marks through nights and
            # weekends. The legacy extended_hours flag returns NOTHING after
            # Friday's close all weekend (verified 2026-08-09: 1D/5Min gave
            # a stale Friday window vs a live rolling 24h with continuous).
            params = {"period": p, "timeframe": t}
            if t != "1D":
                params["intraday_reporting"] = "continuous"
            resp = requests.get(
                "https://api.alpaca.markets/v2/account/portfolio/history",
                params=params,
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
                timeout=12)
            resp.raise_for_status()
            h = resp.json()
        except Exception as e:
            print(f"[API] alpaca portfolio history {p}/{t}: {e}")
            return []
        ts = h.get("timestamp") or []
        eq = h.get("equity") or []
        return [{"ts": int(t2), "equity": round(float(e), 4)}
                for t2, e in zip(ts, eq) if e is not None and float(e) > 0]

    rows = _fetch(per, tf)
    fine_period = splice.get(period)
    if fine_period:
        # Hourly (not 15Min) for the spliced recent window: enough resolution
        # to render the deposit step + trend smoothly, without the intraday
        # noise that makes a $500 book's 15-min marks look jagged/spiky.
        # 15Min continuous to match Alpaca's own dashboard detail (Robert
        # compared side-by-side 2026-08-09: their line shows intraday+overnight
        # wiggles our hourly splice averaged away).
        fine = _fetch(fine_period, "15Min")
        if fine:
            cut = fine[0]["ts"]
            rows = [r for r in rows if r["ts"] < cut] + fine
        # Finest layer: the last 24h at 5Min (continuous) — the same
        # resolution Alpaca's own dashboard renders, so the client's 1D zoom
        # shows real overnight/weekend movement instead of hourly steps.
        finest = _fetch("1D", "5Min")
        if finest:
            cut = finest[0]["ts"]
            rows = [r for r in rows if r["ts"] < cut] + finest
    _PH_CACHE[ck] = {"ts": now, "rows": rows}
    return rows


# ── Ticker bars (per-symbol) ─────────────────────────────────────────────────
# Uses yfinance so MAX walks back to IPO instead of being capped at ~10-20 years
# the way most consumer brokers limit you.
_YF_PERIOD = {
    "1H":  "1d",  "1D": "1d",  "1W": "5d",
    "1M":  "1mo", "3M": "3mo", "1Y": "1y",
    "5Y":  "5y",  "MAX": "max",
}
_YF_INTERVAL = {
    "1H":  "5m",  "1D": "5m",  "1W": "30m",
    "1M":  "1d",  "3M": "1d",  "1Y": "1d",
    "5Y":  "1wk", "MAX": "1mo",
}


def _ticker_bars(symbol: str, period: str) -> list[dict]:
    import yfinance as yf
    p = _YF_PERIOD.get(period.upper(), "1y")
    iv = _YF_INTERVAL.get(period.upper(), "1d")
    df = yf.Ticker(symbol.upper()).history(period=p, interval=iv, auto_adjust=False)
    if df is None or df.empty:
        return []
    col = "Close" if "Close" in df.columns else df.columns[0]
    out = []
    for idx, row in df.iterrows():
        try:
            ts = int(idx.timestamp())
        except Exception:
            continue
        v = float(row[col])
        if v == v:  # filter NaN
            out.append({"ts": ts, "price": v})
    return out


# ── Bot status ───────────────────────────────────────────────────────────────
def _process_running(name_fragments: list[str]) -> dict | None:
    try:
        import subprocess
        out = subprocess.run(["pgrep", "-f", name_fragments[0]],
                             capture_output=True, text=True, timeout=2)
        pids = [p for p in out.stdout.strip().splitlines() if p]
        if not pids:
            return None
        return {"pid": int(pids[0]), "extra_pids": pids[1:]}
    except Exception:
        return None


def _bots_status() -> list[dict]:
    """launchd truth on the Mac (2026-08-09 rewrite: Oracle VM abandoned,
    Mac is the primary host). `launchctl list <label>` exit 0 = loaded —
    running now or waiting on its schedule, which is launchd's "active"."""
    import re as _re
    import subprocess

    jobs = [
        ("com.quarki3.scanner",              "Scanner (mirrors + feeds + governor)"),
        ("com.quarki3.ng-dashboard",         "Dashboard (this API)"),
        ("com.trading.qc",                   "QC Portfolio Engine"),
        ("com.quarki3.turbo-entry",          "Turbo Arm entries (10:15 ET)"),
        ("com.quarki3.turbo-exits",          "Turbo Arm exits (3x daily)"),
        ("com.quarki3.crypto-sleeve",        "Crypto Sleeve (Kraken, 5 min)"),
        ("com.quarki3.stack-watchdog",       "Watchdog (5 min)"),
        ("com.quarki3.leadercheck",          "Leader Check (daily)"),
        ("com.quarki3.directive-audit-fast", "Directive Auditor (hourly)"),
        ("com.quarki3.lab333-watch",         "Lab333 Leak Tripwire (hourly)"),
        ("com.quarki3.mvy-watch",            "MVYTraders Watcher (15 min)"),
    ]
    out = []
    for label, name in jobs:
        running, pid = False, None
        try:
            r = subprocess.run(["launchctl", "list", label],
                               capture_output=True, text=True, timeout=5)
            running = r.returncode == 0
            if running:
                m = _re.search(r'"PID"\s*=\s*(\d+)', r.stdout)
                pid = int(m.group(1)) if m else None
        except Exception:
            running = False
        out.append({"id": label.rsplit(".", 1)[-1], "name": name, "host": "mac",
                    "running": running, "pid": pid})
    out.append({"id": "shadow", "name": "Shadow A/B Engine", "host": "in-process",
                "running": True, "pid": None})
    return out


def _winrate(closed: list[dict]) -> float:
    if not closed:
        return 0.0
    wins = sum(1 for c in closed if _f(c.get("pnl_pct")) > 0)
    return round(wins / len(closed) * 100, 1)


def _probe_tcp(host: str, port: int, timeout: float = 0.5) -> bool:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


# ── Mounting ─────────────────────────────────────────────────────────────────
# ── qcmirror: the scanner's QC-mirror PAPER ledger ───────────────────────────
# This used to be a gate-level rewrite to alpaca_live ("paper view of the live
# book"), which showed guest keys the real account's balances and positions.
# It is now a genuine sim source: virtual bankroll, scanner-maintained
# positions and trade ledger, labbook equity curve. No broker linkage.

_QCMIRROR_STATE   = Path("/Users/quarki3/scanner/state/qc_mirror_state.json")
_QCMIRROR_LEDGER  = Path("/Users/quarki3/scanner/state/qc_mirror_ledger.jsonl")
_LABBOOK_CURVES   = Path("/Users/quarki3/scanner/state/labbook_curves.jsonl")
_QCMIRROR_STALE_S = 3600  # scanner ticks every few minutes; an hour quiet = stale


def _qcmirror_curve() -> list[dict]:
    try:
        lines = _LABBOOK_CURVES.read_text().splitlines()
    except Exception:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get("name") == "qc_mirror" and r.get("equity") is not None:
            out.append({"ts": r["ts"], "equity": _f(r["equity"])})
    return out


def _portfolio_qcmirror() -> dict:
    try:
        st = json.loads(_QCMIRROR_STATE.read_text())
    except Exception:
        return _empty_portfolio("qcmirror")
    held = []
    for sym, p in (st.get("positions") or {}).items():
        qty = _f(p.get("qty"))
        mv = _f(p.get("mv"))
        avg = _f(p.get("avg_entry"))
        upl = _f(p.get("unrealized_pl"))
        cost = qty * avg
        held.append({
            "symbol": sym,
            "qty": qty,
            "avg_cost": round(avg, 4),
            "market_price": round(mv / qty, 4) if qty else 0.0,
            "market_value": round(mv, 2),
            "unrealized_pl": round(upl, 2),
            "unrealized_pl_pct": round(upl / cost * 100, 2) if cost else 0.0,
        })
    bankroll = _f(st.get("bankroll")) or 50.0
    equity = round(bankroll * (1 + _f(st.get("pnl_pct")) / 100), 2)
    mv_total = sum(p["market_value"] for p in held)
    peak = max([pt["equity"] for pt in _qcmirror_curve()] + [equity])
    return {
        "source": "qcmirror",
        "equity": equity,
        "cash": round(equity - mv_total, 2),
        "bp": round(equity - mv_total, 2),
        "unrealized_pl": round(sum(p["unrealized_pl"] for p in held), 2),
        "starting_capital": bankroll,
        "peak_equity": round(peak, 2),
        "drawdown_pct": round((peak - equity) / peak * 100, 2) if peak else 0.0,
        "positions": held,
        "position_count": len(held),
        "stale": (time.time() - _f(st.get("ts"))) > _QCMIRROR_STALE_S,
        "ts": _now_iso(),
    }


def _equity_qcmirror(period: str) -> list[dict]:
    return _filter_by_period(_qcmirror_curve(), period)


def _activity_qcmirror(limit: int) -> list[dict]:
    try:
        lines = _QCMIRROR_LEDGER.read_text().splitlines()
    except Exception:
        return []
    out = []
    for ln in reversed(lines[-limit:]):
        try:
            r = json.loads(ln)
        except Exception:
            continue
        result = str(r.get("result") or "")
        out.append({
            "source": "qcmirror",
            "ts": datetime.fromtimestamp(_f(r.get("ts")), tz=ZoneInfo("UTC")).isoformat(),
            "symbol": r.get("sym", "?"),
            "side": (r.get("side") or "").upper(),
            "qty": 0.0,
            "price": 0.0,
            "notional": round(_f(r.get("delta_usd")), 2),
            "status": "FILLED" if result.startswith("ok") else (result.upper() or "?"),
            "type": "sim",
            "tif": "",
            "order_id": result.split(":", 1)[1] if ":" in result else None,
            "reason": "",
        })
    return out


_PORTFOLIO_DISPATCH = {
    "paper":       lambda m: _portfolio_paper(m),
    "broker":      lambda m: _portfolio_broker(m),
    "shadow":      lambda m: _portfolio_shadow(m),
    "ibkr":        lambda m: _portfolio_ibkr(),
    "alpaca_live": lambda m: _portfolio_alpaca_live(),
    "qcmirror":    lambda m: _portfolio_qcmirror(),
}

_ACTIVITY_DISPATCH = {
    "paper":       lambda m, lim: _activity_paper(m, lim),
    "broker":      lambda m, lim: _activity_broker(m, lim),
    "shadow":      lambda m, lim: _activity_shadow(m, lim),
    "ibkr":        lambda m, lim: _activity_ibkr(lim),
    "alpaca_live": lambda m, lim: _alpaca_live_transactions(lim),
    "qcmirror":    lambda m, lim: _activity_qcmirror(lim),
}

_EQUITY_DISPATCH = {
    "paper":       lambda m, p, tf: _equity_paper(m, p, tf),
    "broker":      lambda m, p, tf: _equity_broker(m, p, tf),
    "shadow":      lambda m, p, tf: _equity_shadow(m, p),
    "ibkr":        lambda m, p, tf: _equity_ibkr(p),
    "alpaca_live": lambda m, p, tf: _equity_alpaca_live(p),
    "qcmirror":    lambda m, p, tf: _equity_qcmirror(p),
}


def _mount_static_dist(fastapi_app):
    """Serve /opt/trading/dist as /dist/* so the Android app can download APKs.
    No-op (logged) if the dir can't be created (e.g. running on Mac dev box)."""
    from fastapi.staticfiles import StaticFiles
    for candidate in (Path("/opt/trading/dist"),
                      Path.home() / "trading-dist"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            fastapi_app.mount("/dist", StaticFiles(directory=str(candidate)), name="dist")
            return
        except PermissionError:
            continue
    print("[API] /dist static not mounted (no writable dir)")


def mount(fastapi_app, main_module) -> None:
    """Attach all /api/* routes to the NiceGUI FastAPI app."""
    # Add CORS so the Android app (or any non-tailnet client) can hit the API.
    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # ── Public-access gate ────────────────────────────────────────────────
    # Requests proxied from the public URL (Oracle Caddy) always carry
    # X-Forwarded-For; direct localhost/tailnet clients (bots, Android over
    # Tailscale) never do, so they bypass the gate entirely. Public requests
    # need a key from ~/trading-data/dashboard_keys.json: "owner" unlocks
    # everything, "guest" unlocks only the read-only consumer surface at
    # /guest (portfolio, equity, activity, ticker — no strategy internals).
    import re as _re
    import secrets as _secrets
    from fastapi.responses import JSONResponse

    _keys_path = TRADING_DATA_DIR / "dashboard_keys.json"
    try:
        _keys = json.loads(_keys_path.read_text())
    except Exception:
        _keys = {"owner": _secrets.token_hex(16), "guest": _secrets.token_hex(8)}
        _keys_path.parent.mkdir(parents=True, exist_ok=True)
        _keys_path.write_text(json.dumps(_keys, indent=2))

    # /guest HTML shell is open (branding only — every data endpoint it
    # calls still requires the guest key); needed so the installed PWA can
    # boot and show its own access-code screen (iOS PWAs don't share
    # Safari's localStorage).
    # /terminal/* is the SPA shell + assets only — its /api/* calls still
    # require a key when they arrive via the public proxy.
    # NB: /static/owner_* is deliberately NOT open — owner-private assets must
    # require a key even though the rest of /static is public (D1 defense-in-depth).
    _OPEN_PATHS  = _re.compile(r"^/(static/(?!owner_)|manifest\.json$|favicon|guest$|guest-sw\.js$|terminal(/|$))")
    # Guests see the Lab only: paper/sandbox/shadow sources. Real-money
    # sources (alpaca_live, ibkr) are owner-key territory.
    _GUEST_PATHS = _re.compile(
        r"^/(guest$|api/(health$|whoami$"
        r"|(portfolio|equity|activity)/(paper|broker|shadow|qcmirror)$"
        r"|sources$|bots$|version$|edge$|qc$|whales$|patent-cliff$|prop_desk/state$"
        r"|insider/(leaderboard|portfolio)$|governor$|market_pulse$|congress$"
        r"|ticker/))"
    )

    # Per-endpoint guest scrubbing: guests get the Lab-only view of shared
    # endpoints — no broker account ids, no live-money composition, no
    # private whale wallets. Server-side so the client can never opt out.
    _QC_PRIVATE = ("mirror_bankroll", "initial_equity", "peak_equity",
                   "session_open_pl", "session_peak_pl", "ratchet_floor",
                   "ratchet_fired_today", "mirror_pnl_pct",
                   # 2026-08-11 D31 re-fix: these four regressed out of the
                   # scrub in the VM→Mac migration — guests could pull the
                   # live book's exact mode + target composition via /api/qc.
                   "mirror_mode", "mirror_targets", "mirror_targets_raw",
                   "mirror_targets_ts")
    # Edge-ledger rows a guest must NOT see: the live real-money ensemble and
    # the private discretionary / research strategies. Guests keep the paper
    # research rows (prop_desk, qc_*, mirrors, paper_arm). The Autopilot
    # catalog (benchmark rows) is owner-private and never enters /api/edge at
    # all — it's served only on the owner-only /api/catalog for the Android app.
    _EDGE_GUEST_HIDE = {"combined_mirror", "SOXL_regime", "ES_mean_rev", "spy_overlay"}

    # ── Guest epoch mode ─────────────────────────────────────────────────
    # While ~/trading-data/guest_demo.flag exists, guest keys see the Lab
    # and qcmirror book rebased to a baseline snapshot taken when the flag
    # was created: counts and P/L read "since the 2026-08-04 reset" and then
    # accrue REAL sim deltas going forward, so the view stays internally
    # consistent indefinitely. The reset itself is genuine (qcmirror source
    # rebuild + key rotation happened that day). Owner keys and tailnet
    # clients are untouched. Delete the flag to end the epoch (the reveal);
    # guests then see full-history figures again.
    _DEMO_FLAG = TRADING_DATA_DIR / "guest_demo.flag"
    _DEMO_BASELINE = TRADING_DATA_DIR / "guest_demo_baseline.json"

    def _demo_on() -> bool:
        return _DEMO_FLAG.exists()

    def _demo_baseline() -> dict:
        """Load the epoch baseline, snapshotting it from live state on first use."""
        try:
            return json.loads(_DEMO_BASELINE.read_text())
        except Exception:
            pass
        base: dict = {"ts": int(time.time()), "edge": {}, "qcmirror": {}}
        try:
            led = json.loads((Path("/Users/quarki3/scanner/state/edge_ledger.json")).read_text())
            for s in led.get("strategies") or []:
                base["edge"][s.get("name")] = {
                    "n": _f(s.get("n_closed_trades")),
                    "pnl": _f(s.get("total_pnl_pct"))}
        except Exception:
            pass
        try:
            st = json.loads(_QCMIRROR_STATE.read_text())
            base["qcmirror"] = {
                "equity0": _f(st.get("bankroll")) * (1 + _f(st.get("pnl_pct")) / 100) or 50.0,
                "upl0": {sym: _f(p.get("unrealized_pl"))
                         for sym, p in (st.get("positions") or {}).items()}}
        except Exception:
            base["qcmirror"] = {"equity0": 50.0, "upl0": {}}
        try:
            _DEMO_BASELINE.write_text(json.dumps(base, indent=2))
        except Exception:
            pass
        return base

    def _demo_rebase_book(p: dict, base: dict) -> dict:
        b = base.get("qcmirror") or {}
        eq0 = _f(b.get("equity0")) or 50.0
        upl0 = b.get("upl0") or {}
        seed = _f(p.get("starting_capital")) or 50.0
        equity = round(seed * (_f(p.get("equity")) / eq0 if eq0 else 1.0), 2)
        held = []
        for pos in p.get("positions") or []:
            cost = _f(pos.get("qty")) * _f(pos.get("avg_cost"))
            upl = round(_f(pos.get("unrealized_pl")) - _f(upl0.get(pos.get("symbol"))), 2)
            mv = round(cost + upl, 2)
            held.append({**pos, "market_value": mv, "unrealized_pl": upl,
                         "market_price": round(mv / _f(pos.get("qty")), 4) if _f(pos.get("qty")) else 0.0,
                         "unrealized_pl_pct": round(upl / cost * 100, 2) if cost else 0.0})
        mv_total = sum(_f(h.get("market_value")) for h in held)
        peak = max(seed, equity)
        return {**p, "equity": equity, "cash": round(equity - mv_total, 2),
                "bp": round(equity - mv_total, 2),
                "unrealized_pl": round(sum(_f(h.get("unrealized_pl")) for h in held), 2),
                "peak_equity": peak,
                "drawdown_pct": round((peak - equity) / peak * 100, 2) if peak else 0.0,
                "positions": held}

    def _demo_edge_row(s: dict, base: dict) -> dict:
        b = (base.get("edge") or {}).get(s.get("name")) or {}
        n = max(int(_f(s.get("n_closed_trades")) - _f(b.get("n"))), 0)
        pnl = round(_f(s.get("total_pnl_pct")) - _f(b.get("pnl")), 3) if n else 0.0
        out = {**s, "n_closed_trades": n, "total_pnl_pct": pnl,
               "expectancy_pct": round(pnl / n, 4) if n else 0.0,
               "sharpe": s.get("sharpe") if n >= 10 else 0.0}
        if not n:
            out["win_rate"] = 0.0
        # Trades from before the epoch don't exist in this view — a
        # last-trade stamp older than the baseline would contradict n=0.
        if _f(s.get("last_trade_ts")) < _f(base.get("ts")):
            out["last_trade_ts"] = None
        out["verdict"] = ("UNPROVEN" if n < 30
                          else "LOSING" if pnl < 0 else "PROMISING")
        return out

    def _guest_scrub(path, data):
        if path.startswith("/api/portfolio"):
            for obj in (data if isinstance(data, list) else [data]):
                if isinstance(obj, dict):
                    obj.pop("account", None)
                    if _demo_on() and obj.get("source") == "qcmirror":
                        obj.update(_demo_rebase_book(obj, _demo_baseline()))
        if path == "/api/equity/qcmirror" and _demo_on() and isinstance(data, list):
            # Curve restarts at the epoch: only post-reset samples, rescaled
            # so the series opens at the $50 seed.
            b = _demo_baseline()
            t0 = _f(b.get("ts"))
            eq0 = _f((b.get("qcmirror") or {}).get("equity0")) or 50.0
            data = [{"ts": pt.get("ts"),
                     "equity": round(50.0 * _f(pt.get("equity")) / eq0, 4)}
                    for pt in data if _f(pt.get("ts")) >= t0]
            return data
        if path == "/api/activity/qcmirror" and _demo_on() and isinstance(data, list):
            t0 = _f(_demo_baseline().get("ts"))
            iso0 = datetime.fromtimestamp(t0, tz=ZoneInfo("UTC")).isoformat()
            return [a for a in data if str(a.get("ts")) >= iso0]
        if path == "/api/governor" and isinstance(data, dict):
            # Permanent (not epoch-gated): the live combined book is real
            # money and never enters a guest response (D31).
            snap = data.get("snapshot")
            if isinstance(snap, dict):
                snap.pop("combined_LIVE", None)
                if data.get("leader") == "combined_LIVE":
                    paper = {k: v for k, v in snap.items()
                             if isinstance(v, (int, float))}
                    data["leader"] = max(paper, key=paper.get) if paper else None
            data["recent_alerts"] = [a for a in data.get("recent_alerts") or []
                                     if "combined" not in json.dumps(a).lower()]
        elif path == "/api/sources" and isinstance(data, list):
            # qcmirror is in SOURCES now, so the real-money filter is the
            # whole scrub — guests keep only the sim/paper sources.
            data = [x for x in data if not (isinstance(x, dict) and x.get("real_money"))]
        elif path == "/api/qc" and isinstance(data, dict):
            for k in _QC_PRIVATE:
                data.pop(k, None)
        elif path == "/api/whales" and isinstance(data, dict):
            for a in data.get("alerts") or []:
                if isinstance(a, dict):
                    a.pop("wallets", None)
                    a.pop("raw", None)
        elif path == "/api/edge" and isinstance(data, dict):
            # Hide the live/private strategies from guests. (Benchmark/catalog
            # rows are already stripped upstream in the /api/edge handler.)
            rows = [s for s in (data.get("strategies") or [])
                    if s.get("name") not in _EDGE_GUEST_HIDE]
            if _demo_on():
                base = _demo_baseline()
                rows = [_demo_edge_row(s, base) for s in rows]
            data["strategies"] = rows
        return data

    @fastapi_app.middleware("http")
    async def _public_gate(request, call_next):
        if "x-forwarded-for" not in request.headers:
            return await call_next(request)  # direct: localhost / tailnet
        path = request.url.path
        if _OPEN_PATHS.match(path):
            return await call_next(request)  # icons/manifest — no data
        key = request.headers.get("x-api-key") or request.query_params.get("key")
        if key == _keys.get("owner"):
            return await call_next(request)
        if key == _keys.get("guest") and _GUEST_PATHS.match(path):
            # qcmirror is a real sim source (scanner's paper ledger) — no
            # rewrite to live handlers. Guest keys must NEVER reach a
            # real-money source, relabeled or otherwise.
            resp = await call_next(request)
            if resp.headers.get("content-type", "").startswith("application/json"):
                body = b"".join([chunk async for chunk in resp.body_iterator])
                try:
                    data = _guest_scrub(path, json.loads(body))
                    return JSONResponse(data, status_code=resp.status_code)
                except Exception:
                    from starlette.responses import Response as _Resp
                    return _Resp(content=body, status_code=resp.status_code,
                                 media_type="application/json")
            return resp
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    @fastapi_app.get("/api/whoami", include_in_schema=False)
    def _whoami(request: Request):
        """Key role probe for the Terminal SPA (guest mode hides the Book)."""
        if "x-forwarded-for" not in request.headers:
            return {"role": "owner"}  # direct localhost / tailnet
        key = request.headers.get("x-api-key") or request.query_params.get("key")
        if key == _keys.get("owner"):
            return {"role": "owner"}
        if key == _keys.get("guest"):
            return {"role": "guest"}
        return {"role": "none"}

    @fastapi_app.get("/api/governor", include_in_schema=False)
    def _governor():
        """Governor weights + scoreboard, file-backed from scanner state."""
        base = Path.home() / "scanner" / "state"
        out = {}
        for fname in ("governor_weights.json", "strategy_governor_ui.json"):
            try:
                out.update(json.loads((base / fname).read_text()))
            except Exception:
                pass
        if not out:
            return JSONResponse({"error": "governor state unavailable"}, status_code=404)
        if isinstance(out.get("leader"), dict):
            out["leader"] = out["leader"].get("name")
        import datetime as _dt
        for a in out.get("recent_alerts") or []:
            if isinstance(a, dict) and isinstance(a.get("ts"), (int, float)):
                a["ts"] = _dt.datetime.fromtimestamp(a["ts"], _dt.timezone.utc).isoformat()
        return out

    @fastapi_app.get("/api/congress", include_in_schema=False)
    def _congress():
        """Pelosi-tracker (congress_mirror) paper composition, file-backed."""
        base = Path.home() / "scanner" / "state"
        out = {}
        try:
            st = json.loads((base / "congress_mirror_state.json").read_text())
            out.update({k: st.get(k) for k in ("ts", "mode", "targets",
                                               "positions", "tracked_syms")})
        except Exception:
            pass
        try:
            h = json.loads((base / "congress_holdings.json").read_text())
            out.update({"source": h.get("source"), "politicians": h.get("politicians"),
                        "filings_parsed": h.get("filings_parsed"),
                        "holdings": h.get("holdings"),
                        "holdings_updated_ts": h.get("updated_ts")})
        except Exception:
            pass
        if not out:
            return JSONResponse({"error": "congress state unavailable"}, status_code=404)
        return out

    # Serve /dist/* for over-the-air APK self-updates.
    _mount_static_dist(fastapi_app)

    # Serve the ng_dashboard/static directory (icons, manifest, webapp.html)
    from fastapi.staticfiles import StaticFiles
    _static_dir = Path(__file__).parent / "static"
    if _static_dir.exists():
        fastapi_app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    # Public mobile-first web app — same endpoints the Android app uses.
    # Exposed at /app so the NiceGUI root ("/") still owns the desktop view.
    from fastapi.responses import FileResponse
    _webapp = _static_dir / "webapp.html"
    if _webapp.exists():
        @fastapi_app.get("/app", include_in_schema=False)
        def _webapp_html():
            return FileResponse(str(_webapp), media_type="text/html")

    # New Quarki Terminal SPA (React, built in ~/trading-gui/dist). Served
    # under /terminal; the shell is open like /app — every data call it makes
    # still needs a key when arriving via the public proxy.
    _terminal_dist = Path.home() / "trading-gui" / "dist"
    if (_terminal_dist / "index.html").exists():
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as _StarHTTPExc

        class _SPAStatic(StaticFiles):
            def _owner_shell(self, scope):
                # Owner-mode shell: inject the private catalog bundle at serve
                # time. Two ways in: (a) direct localhost/tailnet (no proxy
                # XFF), or (b) via the public funnel WITH the correct owner
                # key as ?key= (added 2026-08-07 for the Mac-failover app —
                # server verifies the key BEFORE injecting; guests/wrong keys
                # get the vanilla shell and the bundle route 403s them anyway).
                from urllib.parse import parse_qs
                _qs = parse_qs((scope.get("query_string") or b"").decode())
                _qkey = (_qs.get("key") or [None])[0]
                _owner_ok = _qkey == _keys.get("owner")
                if any(k == b"x-forwarded-for" for k, _ in scope.get("headers") or []) \
                        and not _owner_ok:
                    return None
                try:
                    html = (_terminal_dist / "index.html").read_text()
                except Exception:
                    return None
                inject = ('<script defer src="/owner/catalog.js"></script>'
                          '<script defer src="/owner/crypto.js"></script>'
                          '<script defer src="/owner/crypto-intel.js"></script>'
                          '<script defer src="/owner/zoom.js"></script>'
                          '<script defer src="/owner/crypto-account.js"></script>'
                          '<script defer src="/owner/explain.js"></script>')
                if _owner_ok:
                    # Via the funnel, the browser's <script> loads of the
                    # /owner/*.js bundles ALSO hit the XFF key-gate (they can't
                    # send the header), so pass the key on those URLs too —
                    # otherwise the private bundles 403 and never load.
                    # Then auto-login the SPA and scrub the query string.
                    inject = ('<script defer src="/owner/catalog.js?key='
                              + _qkey + '"></script>'
                              '<script defer src="/owner/crypto.js?key='
                              + _qkey + '"></script>'
                              '<script defer src="/owner/crypto-intel.js?key='
                              + _qkey + '"></script>'
                              '<script defer src="/owner/zoom.js?key='
                              + _qkey + '"></script>'
                              '<script defer src="/owner/crypto-account.js?key='
                              + _qkey + '"></script>'
                              '<script defer src="/owner/explain.js?key='
                              + _qkey + '"></script>')
                    inject = ('<script>try{localStorage.setItem("qt.apiKey",'
                              + json.dumps(_qkey) +
                              ');history.replaceState(null,"",location.pathname);}'
                              'catch(e){}</script>' + inject)
                html = html.replace("</body>", inject + "</body>")
                from starlette.responses import HTMLResponse
                resp = HTMLResponse(html)
                resp.headers["cache-control"] = "no-cache"
                return resp

            async def get_response(self, path, scope):
                # Deep links (/terminal/lab etc.) fall back to the SPA shell;
                # real asset misses (paths with extensions) still 404.
                def _routeish() -> bool:
                    # "" / "." = the mount root serving index.html directly
                    return path in ("", ".") or "." not in path.rsplit("/", 1)[-1]
                if _routeish():
                    owner = self._owner_shell(scope)
                    if owner is not None:
                        return owner
                try:
                    resp = await super().get_response(path, scope)
                except _StarHTTPExc as exc:
                    if exc.status_code == 404 and _routeish():
                        resp = await super().get_response("index.html", scope)
                        resp.headers["cache-control"] = "no-cache"
                        return resp
                    raise
                if resp.status_code == 404 and _routeish():
                    resp = await super().get_response("index.html", scope)
                # The shell must always revalidate or installed PWAs keep
                # showing stale builds; hashed assets stay long-cacheable.
                if _routeish():
                    resp.headers["cache-control"] = "no-cache"
                return resp

        fastapi_app.mount("/terminal", _SPAStatic(directory=str(_terminal_dist),
                                                  html=True), name="terminal")
        print("[API] /terminal mounted (Quarki Terminal)")

        @fastapi_app.middleware("http")
        async def _tmp_access_log(request, call_next):
            resp = await call_next(request)
            try:
                print(f"[ACCESS] {request.client.host} {request.method} "
                      f"{request.url.path} -> {resp.status_code}", flush=True)
            except Exception:
                pass
            return resp
    else:
        print("[API] /terminal NOT mounted — ~/trading-gui/dist/index.html missing")

    # Owner-private Terminal add-on: the Autopilot catalog tab, served from
    # THIS repo (never trading-gui — post-Linus privacy boundary). The bundle
    # lives OUTSIDE the public /static mount so it can only be reached through
    # the owner-key-gated /owner/catalog.js route below — serving it from
    # /static would let anyone with the public URL fetch it keyless (D1 leak).
    # The public gate also 403s /owner/* for guests (not in _GUEST_PATHS).
    _private_dir = Path(__file__).parent / "private"
    _owner_cat_js = _private_dir / "owner_catalog.js"
    if _owner_cat_js.exists():
        @fastapi_app.get("/owner/catalog.js", include_in_schema=False)
        def _owner_catalog_js(request: Request):
            key = request.headers.get("x-api-key") or request.query_params.get("key")
            if "x-forwarded-for" in request.headers and key != _keys.get("owner"):
                raise HTTPException(status_code=403)
            resp = FileResponse(str(_owner_cat_js),
                                media_type="application/javascript")
            resp.headers["cache-control"] = "no-cache"
            return resp

    # Owner-private Crypto tab (D39 Kraken sleeve) — same serving rules as
    # the catalog bundle: outside /static, owner-key-gated via /owner/*.
    _owner_cy_js = _private_dir / "owner_crypto.js"
    if _owner_cy_js.exists():
        @fastapi_app.get("/owner/crypto.js", include_in_schema=False)
        def _owner_crypto_js(request: Request):
            key = request.headers.get("x-api-key") or request.query_params.get("key")
            if "x-forwarded-for" in request.headers and key != _keys.get("owner"):
                raise HTTPException(status_code=403)
            resp = FileResponse(str(_owner_cy_js),
                                media_type="application/javascript")
            resp.headers["cache-control"] = "no-cache"
            return resp

    # Whale/social intel add-on card (separate bundle so parallel edits to
    # owner_crypto.js never conflict) — same owner-key gate.
    _owner_cyintel_js = _private_dir / "owner_crypto_intel.js"
    if _owner_cyintel_js.exists():
        @fastapi_app.get("/owner/crypto-intel.js", include_in_schema=False)
        def _owner_crypto_intel_js(request: Request):
            key = request.headers.get("x-api-key") or request.query_params.get("key")
            if "x-forwarded-for" in request.headers and key != _keys.get("owner"):
                raise HTTPException(status_code=403)
            resp = FileResponse(str(_owner_cyintel_js),
                                media_type="application/javascript")
            resp.headers["cache-control"] = "no-cache"
            return resp

    # Pinch-to-zoom period detents for chart cards — same owner-key gate.
    _owner_zoom_js = _private_dir / "owner_zoom.js"
    if _owner_zoom_js.exists():
        @fastapi_app.get("/owner/zoom.js", include_in_schema=False)
        def _owner_zoom_js_route(request: Request):
            key = request.headers.get("x-api-key") or request.query_params.get("key")
            if "x-forwarded-for" in request.headers and key != _keys.get("owner"):
                raise HTTPException(status_code=403)
            resp = FileResponse(str(_owner_zoom_js),
                                media_type="application/javascript")
            resp.headers["cache-control"] = "no-cache"
            return resp

    # Crypto-sleeve account card (Book page hero twin) — same owner-key gate.
    _owner_cyacct_js = _private_dir / "owner_crypto_account.js"
    if _owner_cyacct_js.exists():
        @fastapi_app.get("/owner/crypto-account.js", include_in_schema=False)
        def _owner_crypto_account_route(request: Request):
            key = request.headers.get("x-api-key") or request.query_params.get("key")
            if "x-forwarded-for" in request.headers and key != _keys.get("owner"):
                raise HTTPException(status_code=403)
            resp = FileResponse(str(_owner_cyacct_js),
                                media_type="application/javascript")
            resp.headers["cache-control"] = "no-cache"
            return resp

    # TraderGrader leaderboard + graded-recs card — same owner-key gate.
    _owner_tg_js = _private_dir / "owner_tradergrader.js"
    if _owner_tg_js.exists():
        @fastapi_app.get("/owner/tradergrader.js", include_in_schema=False)
        def _owner_tradergrader_route(request: Request):
            key = request.headers.get("x-api-key") or request.query_params.get("key")
            if "x-forwarded-for" in request.headers and key != _keys.get("owner"):
                raise HTTPException(status_code=403)
            resp = FileResponse(str(_owner_tg_js),
                                media_type="application/javascript")
            resp.headers["cache-control"] = "no-cache"
            return resp

    # Book-page explain add-on (holdings why-dropdowns + trade history card)
    # — same owner-key gate.
    _owner_explain_js = _private_dir / "owner_explain.js"
    if _owner_explain_js.exists():
        @fastapi_app.get("/owner/explain.js", include_in_schema=False)
        def _owner_explain_route(request: Request):
            key = request.headers.get("x-api-key") or request.query_params.get("key")
            if "x-forwarded-for" in request.headers and key != _keys.get("owner"):
                raise HTTPException(status_code=403)
            resp = FileResponse(str(_owner_explain_js),
                                media_type="application/javascript")
            resp.headers["cache-control"] = "no-cache"
            return resp

    @fastapi_app.get("/api/tradergrader", include_in_schema=False)
    def _tradergrader():
        """@tradergrader leaderboard rollups + graded-recs for the owner
        Grader card. Deliberately NOT in _GUEST_PATHS (post-Linus boundary —
        it names the accounts we track and how we score them)."""
        S = Path.home() / "scanner" / "state"
        out: dict[str, Any] = {"leaderboard": {}, "grades": {}}
        try:
            out["leaderboard"] = json.loads(
                (S / "tradergrader_leaderboard.json").read_text())
        except Exception:
            pass
        try:
            out["grades"] = json.loads(
                (S / "tracked_accounts_grades.json").read_text())
        except Exception:
            pass
        return out

    # ── D39 crypto sleeve data (owner-only). Deliberately NOT in
    # _GUEST_PATHS: real-money Kraken book + post-Linus privacy boundary, so
    # the public gate 403s guest keys; tailnet/owner-key requests pass. ──
    _CRYPTO_DIR = Path.home() / "crypto_sleeve"

    @fastapi_app.get("/api/crypto/intel", include_in_schema=False)
    def _crypto_intel():
        """Full whale/social composite (all legs + token_flows) for the intel
        card. Owner-only via the same gate as the rest of /api/crypto/*."""
        try:
            return json.loads((_CRYPTO_DIR / "state" / "whale_signals.json").read_text())
        except Exception:
            return {"available": False}

    # ── Crypto Book money contract ────────────────────────────────────────
    # The 2026-08-27 crypto-only Book renders SERVER strings verbatim — the
    # client never computes a money number. Core law for every figure below:
    # deposits/withdrawals are capital moves, NOT P/L, so all P/L values are
    # net of the Kraken ledger transfers in state/transfers.json.
    # The per-day stat's anchor is a contract with the Book label
    # ("/day since 8/20" in trading-gui Book.tsx) — change them together.
    _CRYPTO_PL_ANCHOR = "2026-08-20T00:00:00+00:00"

    def _crypto_usd(v: float) -> str:
        """$1234.56 / -$12.34 — glyph-identical to the SPA's money()."""
        return ("-" if v < 0 else "") + "$" + f"{abs(v):.2f}"

    def _crypto_usd_signed(v: float) -> str:
        """+$1.23 / −$1.23 / ±$0.00 — glyph-identical to moneySigned()."""
        sign = "+" if v > 0 else ("\u2212" if v < 0 else "\u00b1")
        return f"{sign}${abs(v):.2f}"

    def _crypto_epoch(iso: str) -> float | None:
        try:
            return datetime.fromisoformat(iso).timestamp()
        except ValueError:
            return None

    @fastapi_app.get("/api/crypto/summary", include_in_schema=False)
    def _crypto_summary():
        out = {"ts": int(time.time()), "equity": []}
        try:
            lines = (_CRYPTO_DIR / "state" / "equity.csv").read_text().strip().splitlines()
            for ln in lines[1:][-5000:]:
                c = ln.split(",")
                if len(c) >= 5:
                    try:
                        out["equity"].append({
                            "ts": c[0], "equity": float(c[1]), "cash": float(c[2]),
                            "positions_mv": float(c[3]), "realized": float(c[4])})
                    except ValueError:
                        pass
        except Exception:
            pass
        # The 5-min logger halts whenever the sleeve freezes (8/26 15:35: the
        # Book chart flat-lined at the freeze while the account kept moving).
        # The 10-second fine logger keeps ticking through freezes, so splice
        # it on past the last coarse row — thinned to a bounded point budget,
        # newest tick always kept. Fine rows only carry (ts, equity); carry
        # cash/realized forward from the last coarse row and preserve the
        # equity = cash + positions_mv identity for consumers that read the
        # last row's fields (owner_crypto.js stat tiles).
        try:
            last = out["equity"][-1] if out["equity"] else None
            last_ts = last["ts"] if last else ""
            cash = last["cash"] if last else 0.0
            realized = last["realized"] if last else 0.0
            fresh = []
            for ln in (_CRYPTO_DIR / "state" / "equity_fine.csv").read_text() \
                    .strip().splitlines():
                c = ln.split(",")
                if len(c) >= 2 and c[0] > last_ts:
                    try:
                        fresh.append((c[0], float(c[1])))
                    except ValueError:
                        pass
            budget = 1500
            stride = max(1, -(-len(fresh) // budget))  # ceil div
            picked = fresh[::stride]
            if fresh and (not picked or picked[-1][0] != fresh[-1][0]):
                picked.append(fresh[-1])
            for ts_, eq_ in picked:
                out["equity"].append({
                    "ts": ts_, "equity": eq_, "cash": cash,
                    "positions_mv": round(eq_ - cash, 4), "realized": realized})
        except Exception:
            pass
        for fname, key in (("sleeve_state.json", "state"),
                           ("whale_signals.json", "whale")):
            try:
                out[key] = json.loads((_CRYPTO_DIR / "state" / fname).read_text())
            except Exception:
                out[key] = None
        try:
            cfg = json.loads((_CRYPTO_DIR / "config.json").read_text())
            cfg.pop("kraken_env_file", None)
            cfg.pop("env_file", None)
            out["config"] = cfg
        except Exception:
            out["config"] = None
        try:
            out["log_tail"] = (_CRYPTO_DIR / "logs" / "sleeve.log").read_text() \
                .strip().splitlines()[-12:]
        except Exception:
            out["log_tail"] = []
        # Kraken ledger transfers — the chart marks them, and every P/L
        # figure below subtracts them so funding never reads as trading.
        # The cash ledger cannot see ON-CHAIN token adoptions, so known
        # non-cash capital events ride along as corrections (same doctrine
        # as the retired account card's hand anchors: dates/values are
        # contract, not data). 2026-08-09 23:51:45Z: the 7.1M PEPE adoption,
        # +$20.4289 at its baseline price — capital in, not P/L.
        _CRYPTO_XFER_CORRECTIONS = [
            {"ts": 1786319505.0, "usd": 20.4289},
        ]
        transfers = []
        try:
            tj = json.loads((_CRYPTO_DIR / "state" / "transfers.json").read_text())
            for t in tj.get("transfers") or []:
                try:
                    transfers.append({"ts": float(t["ts"]), "usd": float(t["usd"])})
                except (KeyError, TypeError, ValueError):
                    pass
            transfers.extend(_CRYPTO_XFER_CORRECTIONS)
            transfers.sort(key=lambda t: t["ts"])
        except Exception:
            pass
        out["transfers"] = transfers
        # Hero figures (value / total P/L / unrealized / per-day) + chart
        # staleness. All server-side; the Book renders the *_str verbatim.
        if out["equity"]:
            lastrow = out["equity"][-1]
            value = float(lastrow["equity"])
            out["value_usd"] = round(value, 2)
            out["value_str"] = _crypto_usd(value)
            # Total P/L, inception-anchored (the stack's established "vs
            # inception, net of transfers since" doctrine — and exactly the
            # chart's ALL-window math, so hero and chart always agree):
            # equity now − first logged equity − net transfers since logging
            # began. Ledger entries at/before the first row (pre-sleeve
            # account history) are baseline capital, not sleeve P/L.
            first_ep = _crypto_epoch(out["equity"][0]["ts"]) or 0.0
            pl_total = (value - float(out["equity"][0]["equity"])
                        - sum(t["usd"] for t in transfers if t["ts"] > first_ep))
            out["pl_total"] = round(pl_total, 2)
            out["pl_total_str"] = _crypto_usd_signed(pl_total)
            # Unrealized: tracked open positions marked at their freshest
            # px_hist tick. Omitted (not zeroed) when flat or unpriceable —
            # the Book hides the chip rather than showing a fake ±$0.00.
            try:
                st = out.get("state") or {}
                poss = st.get("positions") or {}
                px_hist = st.get("px_hist") or {}
                unreal, priced = 0.0, bool(poss)
                for sym, p in poss.items():
                    entry = float(p.get("entry_price") or 0)
                    notional = float(p.get("notional") or 0)
                    hist = px_hist.get(sym) or []
                    if not entry or not hist:
                        priced = False
                        break
                    unreal += notional / entry * float(hist[-1][1]) - notional
                if priced:
                    out["unrealized_usd"] = round(unreal, 2)
                    out["unrealized_str"] = _crypto_usd_signed(unreal)
            except Exception:
                pass
            # P/L per day since the 8/20 scale-up: transfer-adjusted equity
            # gain over calendar days elapsed. Anchored to the last equity
            # tick at/before the anchor (series start if younger than it).
            try:
                anchor_ep = _crypto_epoch(_CRYPTO_PL_ANCHOR)
                eq_anchor = None
                for r in out["equity"]:
                    if r["ts"] <= _CRYPTO_PL_ANCHOR:
                        eq_anchor = float(r["equity"])
                    else:
                        break
                if eq_anchor is None:
                    eq_anchor = float(out["equity"][0]["equity"])
                    first_ep = _crypto_epoch(out["equity"][0]["ts"])
                    if first_ep:
                        anchor_ep = max(anchor_ep or first_ep, first_ep)
                days = (time.time() - anchor_ep) / 86400.0 if anchor_ep else 0.0
                if days > 0:
                    net_since = sum(t["usd"] for t in transfers
                                    if t["ts"] >= anchor_ep)
                    per_day = ((value - eq_anchor) - net_since) / days
                    out["per_day_usd"] = round(per_day, 2)
                    out["per_day_str"] = _crypto_usd_signed(per_day)
            except Exception:
                pass
            # Staleness: no equity tick for >30 min means the loggers are
            # down — flag it so the Book hides the window-P/L stat (a stale
            # line's first-vs-last P/L would contradict pl_total) and shows
            # an honest notice instead of a silently frozen line.
            last_ep = _crypto_epoch(lastrow["ts"])
            if last_ep:
                age_s = max(0.0, time.time() - last_ep)
                out["chart_stale"] = age_s > 1800
                if out["chart_stale"]:
                    ago = (f"{age_s / 3600:.1f} h" if age_s < 172800
                           else f"{age_s / 86400:.1f} d")
                    note = f"chart stale — last equity tick {ago} ago"
                    if (out.get("state") or {}).get("frozen"):
                        note += " · sleeve FROZEN (flat until reviewed)"
                    out["chart_notice_str"] = note
        return out

    # Live Kraken prices for the sleeve's whole universe — feeds the crypto
    # page's universe-watch table (track prices WITHOUT holding dust). One
    # upstream call cached 20s; owner-only via the default-deny guest gate.
    _crypto_tick_cache: dict = {"ts": 0.0, "rows": {}}
    _kraken_ws_by_key: dict = {}   # internal pair key -> wsname
    _kraken_alt_by_ws: dict = {}   # wsname -> altname

    @fastapi_app.get("/api/crypto/ticker", include_in_schema=False)
    def _crypto_ticker():
        import requests as _rq
        now = time.time()
        if now - _crypto_tick_cache["ts"] < 20 and _crypto_tick_cache["rows"]:
            return {"ts": int(_crypto_tick_cache["ts"]),
                    "rows": _crypto_tick_cache["rows"]}
        try:
            uni = json.loads((_CRYPTO_DIR / "config.json").read_text()) \
                .get("universe") or []
        except Exception:
            uni = []
        # Kraken wsnames rename a few bases (BTC->XBT, DOGE->XDG)
        rename = {"BTC": "XBT", "DOGE": "XDG"}
        want = {}
        for pair in uni:
            base = pair.split("/")[0]
            want[f"{rename.get(base, base)}/USD"] = pair
        try:
            if not _kraken_ws_by_key:
                ap = _rq.get("https://api.kraken.com/0/public/AssetPairs",
                             timeout=10).json().get("result") or {}
                for k, v in ap.items():
                    _kraken_ws_by_key[k] = v.get("wsname", "")
                    _kraken_alt_by_ws[v.get("wsname", "")] = v.get("altname", k)
            alts = [_kraken_alt_by_ws[w] for w in want if w in _kraken_alt_by_ws]
            res = _rq.get("https://api.kraken.com/0/public/Ticker",
                          params={"pair": ",".join(alts)}, timeout=10) \
                .json().get("result") or {}
            rows = {}
            for k, v in res.items():
                pair = want.get(_kraken_ws_by_key.get(k, k))
                if not pair:
                    continue
                try:
                    last = float(v["c"][0])
                    op = float(v.get("o") or 0)
                    rows[pair] = {"price": last,
                                  "chg_pct": (last / op - 1) * 100 if op else None}
                except (KeyError, ValueError, TypeError):
                    continue
            if rows:
                _crypto_tick_cache.update(ts=now, rows=rows)
        except Exception as e:
            print(f"[API] crypto ticker: {e}")
        return {"ts": int(_crypto_tick_cache["ts"] or now),
                "rows": _crypto_tick_cache["rows"]}

    # Consumer-facing guest view — stripped Book-only page, no strategy
    # internals. Public access requires the guest key (?key=... once, then
    # stored in localStorage by the page).
    _guestpage = _static_dir / "guest.html"
    if _guestpage.exists():
        @fastapi_app.get("/guest", include_in_schema=False)
        def _guest_html():
            return FileResponse(str(_guestpage), media_type="text/html")

    # Service worker must be served from a root-level path so its scope can
    # cover /guest (a /static/-served worker could only control /static/*).
    _guest_sw = _static_dir / "guest-sw.js"
    if _guest_sw.exists():
        @fastapi_app.get("/guest-sw.js", include_in_schema=False)
        def _guest_sw_js():
            return FileResponse(str(_guest_sw), media_type="application/javascript")

    # Look-up for the current Cloudflare quick tunnel URL. trycloudflare
    # URLs are ephemeral — regenerated on every cloudflared restart. This
    # endpoint scrapes the launchd tunnel log so any tailnet client can
    # always find the current public URL.
    import re
    @fastapi_app.get("/api/public-url", include_in_schema=False)
    def _public_url():
        log = Path.home() / "Library/Logs/cloudflared/quick-tunnel.log"
        if not log.exists():
            return {"url": None, "reason": "no tunnel log"}
        try:
            text = log.read_text(errors="ignore")
            hits = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", text)
            url  = hits[-1] if hits else None
            return {"url": url, "app_url": (url + "/app") if url else None}
        except Exception as e:
            return {"url": None, "reason": str(e)}

    @fastapi_app.get("/api/health")
    def health():
        return {
            "ok": True,
            "ts": _now_iso(),
            "host": platform.node(),
            "tailscale_ip": _tailscale_ip(),
            "version": 1,
        }

    @fastapi_app.get("/api/qc")
    def qc():
        """QC Portfolio bot state: strategies, weights, allocations, exposure."""
        state_path = TRADING_DATA_DIR / "qc_portfolio/portfolio_state.json"
        if not state_path.exists():
            return {"error": "qc state file not found"}
        try:
            s = json.loads(state_path.read_text())
        except Exception as e:
            raise HTTPException(500, f"qc state parse error: {e}")

        strategies = []
        for i, strat in enumerate(s.get("strategies", [])):
            stats = strat.get("statistics", {}) or {}
            strategies.append({
                "id":         strat.get("id"),
                "name":       strat.get("name", ""),
                "universe":   strat.get("universe", []),
                "alloc_mode": strat.get("alloc", ""),
                "weight":     s.get("weights", [0] * 5)[i] if i < len(s.get("weights", [])) else 0,
                "holdings":   s.get("allocations", {}).get(str(strat.get("id")), {}),
                "psr_5y":     stats.get("5y psr"),
                "cagr_5y":    stats.get("5y cagr"),
                "sharpe_1y":  stats.get("1y sharpe"),
                "return_3m":  stats.get("3m return"),
                "drawdown_5y": stats.get("5y drawdown"),
            })

        # Live mirror P&L — prefer the ENSEMBLE combined_mirror; fall back to qc_mirror
        mirror = {}
        try:
            cp = Path("/Users/quarki3/scanner/state/combined_mirror_state.json")
            mp = Path("/Users/quarki3/scanner/state/qc_mirror_state.json")
            if cp.exists():
                mirror = json.loads(cp.read_text())
                mirror["_source"] = "ensemble"
            elif mp.exists():
                mirror = json.loads(mp.read_text())
                mirror["_source"] = "qc_only"
        except Exception:
            pass

        # QC engine inception timestamp (first ts in equity_history)
        qc_started_ts = None
        try:
            eh = s.get("equity_history") or []
            if eh:
                qc_started_ts = int(eh[0][0])
        except Exception:
            pass

        return {
            "ts":              _now_iso(),
            "initial_equity":  s.get("initial_equity"),
            "peak_equity":     s.get("peak_equity"),
            "qc_started_ts":   qc_started_ts,
            "exposure_level":  s.get("exposure_level"),
            "tiers_fired":     s.get("tiers_fired", []),
            "last_qc_refresh": s.get("last_qc_refresh"),
            "last_rebalance":  s.get("last_rebalance"),
            "session_open_pl": s.get("session_open_pl"),
            "session_peak_pl": s.get("session_peak_pl"),
            "ratchet_floor":   s.get("ratchet_floor"),
            "ratchet_fired_today": s.get("ratchet_fired_today"),
            "strategies":      strategies,
            "mirror_pnl_pct":  mirror.get("pnl_pct"),
            "mirror_mode":     mirror.get("mode"),
            "mirror_bankroll": mirror.get("bankroll"),
            "mirror_targets":  _cap_display_targets(mirror.get("targets")),
            "mirror_targets_raw": mirror.get("targets"),
            "mirror_targets_ts":  mirror.get("ts"),
        }

    # ── QC strategy runner ───────────────────────────────────────────────────
    @fastapi_app.get("/api/strategies")
    def strategies_list():
        d = TRADING_DATA_DIR / "qc_user_strategies"
        d.mkdir(parents=True, exist_ok=True)
        out = []
        for sub in sorted(d.iterdir()):
            if not sub.is_dir():
                continue
            cfg_path = sub / "config.json"
            if not cfg_path.exists():
                continue
            try:
                cfg = json.loads(cfg_path.read_text())
            except Exception:
                continue
            equity_path = sub / "equity.csv"
            last_equity = 0.0
            if equity_path.exists():
                lines = equity_path.read_text().strip().splitlines()
                if len(lines) > 1:
                    try:
                        last_equity = float(lines[-1].split(",")[1])
                    except Exception:
                        pass
            # Per-strategy P&L: compare last_equity to first row of equity.csv
            initial_equity = 0.0
            if equity_path.exists():
                lines = equity_path.read_text().strip().splitlines()
                if len(lines) > 1:
                    try:
                        initial_equity = float(lines[1].split(",")[1])
                    except Exception:
                        pass
            pnl_pct = None
            if initial_equity > 0 and last_equity > 0:
                pnl_pct = round((last_equity - initial_equity) / initial_equity * 100, 3)
            out.append({
                "name":           cfg.get("name", sub.name),
                "broker":         cfg.get("broker"),
                "allocation_pct": cfg.get("allocation_pct", 0),
                "enabled":        cfg.get("enabled", True),
                "source":         cfg.get("source", "manual"),
                "qc_project_id":  cfg.get("qc_project_id"),
                "created_at":     cfg.get("created_at"),
                "last_equity":    last_equity,
                "initial_equity": initial_equity,
                "pnl_pct":        pnl_pct,
            })
        # Aggregate pnl across all enabled strategies + earliest created_at
        agg = [s["pnl_pct"] for s in out if s["pnl_pct"] is not None]
        aggregate_pnl_pct = round(sum(agg) / len(agg), 3) if agg else None
        earliest = None
        try:
            from datetime import datetime as _dt
            starts = []
            for s in out:
                if s.get("created_at"):
                    starts.append(int(_dt.fromisoformat(
                        s["created_at"].replace("Z", "+00:00")).timestamp()))
            if starts:
                earliest = min(starts)
        except Exception:
            pass
        return {"ts": _now_iso(), "strategies": out,
                "aggregate_pnl_pct": aggregate_pnl_pct,
                "started_ts": earliest}

    @fastapi_app.get("/api/strategies/{name}")
    def strategy_detail(name: str):
        d = TRADING_DATA_DIR / "qc_user_strategies" / name
        if not d.exists():
            raise HTTPException(404, "no such strategy")
        cfg = json.loads((d / "config.json").read_text())
        equity = []
        if (d / "equity.csv").exists():
            lines = (d / "equity.csv").read_text().splitlines()[1:]
            for line in lines[-200:]:
                parts = line.split(",")
                if len(parts) >= 2:
                    equity.append({"ts": parts[0], "equity": _f(parts[1])})
        # latest log lines
        log_tail = []
        if (d / "run.log").exists():
            log_tail = (d / "run.log").read_text().splitlines()[-30:]
        return {"config": cfg, "equity": equity, "log_tail": log_tail}

    @fastapi_app.post("/api/strategies")
    async def strategy_create(req: dict):
        """req = {name, code, broker, allocation_pct, qc_project_id (optional)}"""
        from fastapi import Body
        name = req.get("name", "").strip()
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            raise HTTPException(400, "name must be alphanumeric (plus - / _)")
        d = TRADING_DATA_DIR / "qc_user_strategies" / name
        if d.exists():
            raise HTTPException(409, "strategy with that name already exists")
        d.mkdir(parents=True)
        (d / "strategy.py").write_text(req.get("code", ""))
        cfg = {
            "name":            name,
            "broker":          req.get("broker", "alpaca_paper"),
            "allocation_pct":  float(req.get("allocation_pct", 10)),
            "enabled":         True,
            "source":          req.get("source", "manual"),
            "qc_project_id":   req.get("qc_project_id"),
            "created_at":      _now_iso(),
        }
        (d / "config.json").write_text(json.dumps(cfg, indent=2))
        # The systemd template instance died with the VM; on the Mac the
        # runner discovers strategies from config.json presence alone.
        import subprocess
        try:
            subprocess.run(["systemctl", "enable", "--now",
                            f"qc-strategy@{name}.service"],
                           capture_output=True, timeout=10)
        except Exception:
            pass
        return {"created": cfg}

    @fastapi_app.post("/api/strategies/{name}/config")
    async def strategy_update(name: str, req: dict):
        """Allow updating allocation_pct, enabled, broker. Hot-reloaded by runner."""
        d = TRADING_DATA_DIR / "qc_user_strategies" / name
        if not d.exists():
            raise HTTPException(404, "no such strategy")
        cfg = json.loads((d / "config.json").read_text())
        for k in ("allocation_pct", "enabled", "broker"):
            if k in req:
                cfg[k] = req[k]
        (d / "config.json").write_text(json.dumps(cfg, indent=2))
        return cfg

    @fastapi_app.delete("/api/strategies/{name}")
    def strategy_delete(name: str):
        d = TRADING_DATA_DIR / "qc_user_strategies" / name
        if not d.exists():
            raise HTTPException(404, "no such strategy")
        import shutil, subprocess
        try:
            subprocess.run(["systemctl", "disable", "--now",
                            f"qc-strategy@{name}.service"],
                           capture_output=True, timeout=10)
        except Exception:
            pass
        shutil.rmtree(d)
        return {"deleted": name}

    @fastapi_app.get("/api/qc_lab/projects")
    def qc_lab_projects():
        try:
            sys.path.insert(0, "/opt/trading")
            from qc_shim import qc_lab_client
            return {"projects": qc_lab_client.list_projects()}
        except Exception as e:
            raise HTTPException(502, f"QC API: {e}")

    @fastapi_app.post("/api/qc_lab/deploy")
    async def qc_lab_deploy(req: dict):
        """req = {project_id, name, broker, allocation_pct}"""
        try:
            sys.path.insert(0, "/opt/trading")
            from qc_shim import qc_lab_client
            code = qc_lab_client.fetch_main_file(int(req["project_id"]))
        except Exception as e:
            raise HTTPException(502, f"QC fetch failed: {e}")
        return await strategy_create({**req, "code": code, "source": "qc_lab"})

    @fastapi_app.get("/api/insider/backfill")
    def insider_backfill():
        """EDGAR backfill progress (resumable service state)."""
        path = TRADING_DATA_DIR / "insider_bot/backfill_state.json"
        if not path.exists():
            return {"running": False, "next_date": None,
                    "earliest_target": None, "rows_inserted": 0,
                    "days_processed": 0, "days_errored": 0}
        try:
            state = json.loads(path.read_text())
        except Exception:
            state = {}
        # Liveness — was state updated in last 90 minutes?
        running = False
        if state.get("last_progress"):
            try:
                last = datetime.fromisoformat(state["last_progress"])
                running = (datetime.now() - last).total_seconds() < 5400
            except Exception:
                pass
        state["running"] = running
        return state

    @fastapi_app.get("/api/insider/leaderboard")
    def insider_leaderboard():
        """Top-K optimizer-ranked insider strategy configs (walk-forward CV).
        Reads leaderboard.json written by leaderboard.py after each optimizer run."""
        path = TRADING_DATA_DIR / "insider_bot/leaderboard.json"
        if not path.exists():
            return {"ts": _now_iso(), "n_configs_tested": 0, "top_k": 0, "leaders": []}
        try:
            return json.loads(path.read_text())
        except Exception as e:
            raise HTTPException(500, f"leaderboard parse error: {e}")

    @fastapi_app.get("/api/insider/ledgers")
    def insider_ledgers():
        """Per-config virtual paper ledgers from the multi-ledger trader."""
        d = TRADING_DATA_DIR / "insider_bot/ledgers"
        if not d.exists():
            return {"ts": _now_iso(), "ledgers": []}
        ledgers = []
        for p in sorted(d.glob("*.json")):
            try:
                L = json.loads(p.read_text())
            except Exception:
                continue
            equity = L.get("cash", 0) + sum(
                pos["qty"] * pos.get("last_price", pos.get("entry_price", 0))
                for pos in L.get("positions", {}).values()
            )
            init = L.get("initial_cash", 1.0) or 1.0
            ledgers.append({
                "config_hash":  L.get("config_hash", p.stem),
                "initial_cash": init,
                "cash":         round(L.get("cash", 0), 2),
                "equity":       round(equity, 2),
                "ret_pct":      round((equity / init - 1) * 100, 2),
                "n_positions":  len(L.get("positions", {})),
                "n_closed":     len(L.get("closed", [])),
                "winrate_pct":  _winrate(L.get("closed", [])),
                "last_run":     L.get("last_run"),
                "started_at":   L.get("started_at"),
            })
        # Sort by current return
        ledgers.sort(key=lambda L: L["ret_pct"], reverse=True)
        return {"ts": _now_iso(), "ledgers": ledgers}

    @fastapi_app.get("/api/insider/portfolio")
    def insider_portfolio():
        """Virtual-ledger portfolio for the SMART signal trader."""
        led_path = TRADING_DATA_DIR / "insider_bot/ledger.json"
        if not led_path.exists():
            return {"initial_cash": 0, "cash": 0, "equity": 0, "positions": [], "closed": []}
        led = json.loads(led_path.read_text())
        equity = led["cash"] + sum(p["qty"] * p["last_price"] for p in led["positions"].values())
        positions = []
        for sym, p in led["positions"].items():
            mv = p["qty"] * p["last_price"]
            positions.append({
                "symbol": sym, "qty": p["qty"], "avg_cost": p["entry_price"],
                "market_price": p["last_price"], "market_value": round(mv, 2),
                "unrealized_pl": round(mv - p["qty"] * p["entry_price"], 2),
                "unrealized_pl_pct": p.get("last_pnl_pct", 0.0),
                "entry_date": p["entry_date"], "signal_date": p.get("signal_date"),
                "quality": p.get("quality"),
            })
        return {
            "initial_cash":  led.get("initial_cash", 0),
            "cash":          round(led.get("cash", 0), 2),
            "equity":        round(equity, 2),
            "n_positions":   len(positions),
            "positions":     positions,
            "n_closed":      len(led.get("closed", [])),
            "closed_winrate_pct": _winrate(led.get("closed", [])),
            "last_run":      led.get("last_run"),
        }

    @fastapi_app.get("/api/insider")
    def insider():
        """Today's insider-purchase signals. Read-only tracker, no auto-trading.
        Powered by insider_bot/scanner.py which runs nightly via systemd timer.
        Includes pnl_pct pulled from the top-ranked backtest config's mean total return."""
        sig_path = TRADING_DATA_DIR / "insider_bot/signals.json"
        lb_path  = TRADING_DATA_DIR / "insider_bot/leaderboard.json"

        # Compute paper P&L from the top-ranked config's backtest mean.
        pnl_pct = None
        started_ts = None
        try:
            if lb_path.exists():
                lb = json.loads(lb_path.read_text())
                leaders = lb.get("leaders") or []
                if leaders:
                    pnl_pct = float(leaders[0].get("mean_total_ret_pct") or 0)
                # Insider tracker started when leaderboard was first evaluated
                started_str = lb.get("ts") or (leaders[0].get("last_evaluated") if leaders else None)
                if started_str:
                    from datetime import datetime as _dt
                    started_ts = int(_dt.fromisoformat(started_str.replace("Z", "+00:00")).timestamp())
        except Exception:
            pass

        if not sig_path.exists():
            return {"ts": _now_iso(), "n_signals": 0, "signals": [],
                    "pnl_pct": pnl_pct, "started_ts": started_ts}
        try:
            data = json.loads(sig_path.read_text())
            data["pnl_pct"] = pnl_pct
            data["started_ts"] = started_ts
            return data
        except Exception as e:
            raise HTTPException(500, f"signals.json parse error: {e}")

    @fastapi_app.get("/api/edge")
    def edge_ledger_endpoint():
        """Edge Ledger: which strategy has proven edge, aggregated across
        prop_desk, qc_portfolio, the qc/patent/combined mirrors, the
        alpaca_paper_arm experiment, and backtest-only claims.
        Read-only passthrough of scanner/state/edge_ledger.json, written every
        15 min by scanner/sources/edge_ledger.py."""
        path = Path("/Users/quarki3/scanner/state/edge_ledger.json")
        if not path.exists():
            return {"ts": _now_iso(), "strategies": []}
        try:
            data = json.loads(path.read_text())
        except Exception as e:
            raise HTTPException(500, f"edge_ledger.json parse error: {e}")
        # The Autopilot catalog (benchmark rows) is owner-private and lives only
        # on the Android app via /api/catalog — never on the shared Terminal's
        # scoreboard. Strip it here for ALL callers of /api/edge.
        data["strategies"] = [s for s in (data.get("strategies") or [])
                              if s.get("mode") != "benchmark"
                              and not str(s.get("name", "")).startswith("autopilot_cat:")
                              and not str(s.get("name", "")).startswith("sub5:")]
        return data

    @fastapi_app.get("/api/catalog")
    def autopilot_catalog_endpoint():
        """Autopilot strategy-catalog benchmark — OWNER-PRIVATE (post-Linus work).

        Not in the guest allowlist, so the public gate 403s any non-owner key;
        only the owner key and direct localhost/tailnet (the Android app) reach
        it. Returns the benchmark rows from the edge ledger plus each pilot's
        marked-to-market index curve. Never served on the shared Terminal."""
        edge = Path("/Users/quarki3/scanner/state/edge_ledger.json")
        curves = Path("/Users/quarki3/scanner/state/autopilot_catalog_curves.json")
        rows, curve_data = [], {}
        if edge.exists():
            try:
                allrows = json.loads(edge.read_text()).get("strategies") or []
                rows = [s for s in allrows if s.get("mode") == "benchmark"
                        or str(s.get("name", "")).startswith("autopilot_cat:")
                        or str(s.get("name", "")).startswith("sub5:")]
            except Exception as e:
                raise HTTPException(500, f"edge_ledger.json parse error: {e}")
        if curves.exists():
            try:
                curve_data = json.loads(curves.read_text())
            except Exception:
                curve_data = {}
        # Merge the 1y composition backfill (separate file so the catalog
        # source's 6h rewrite can't clobber it) into each pilot's curve entry.
        bf = Path("/Users/quarki3/scanner/state/autopilot_catalog_backfill.json")
        if bf.exists():
            try:
                for name, hist in json.loads(bf.read_text()).items():
                    if name in curve_data and isinstance(curve_data[name], dict):
                        curve_data[name]["backfill"] = hist
            except Exception:
                pass
        # Sub-$5 watchlist lane (owner-private paper lane, same surface).
        s5 = Path("/Users/quarki3/scanner/state/sub5_watch_state.json")
        if s5.exists():
            try:
                st = json.loads(s5.read_text())
                lc = st.get("lane_curve") or {}
                curve_data["sub5:watchlist"] = {
                    "inception": min(lc) if lc else None,
                    "curve": lc,
                    "last_nav": list(lc.values())[-1] if lc else None,
                    "last_update": st.get("ts"),
                }
            except Exception:
                pass
        # QC reference so the Android app can show catalog-vs-QC head to head.
        qc_row = None
        if edge.exists():
            try:
                for s in json.loads(edge.read_text()).get("strategies") or []:
                    if s.get("name") == "qc_portfolio":
                        qc_row = s
                        break
            except Exception:
                pass
        # Per-pilot dossier text (blurb/source/note) straight from the
        # hand-maintained catalog file, so every surface renders the same
        # explanations without hardcoding them in any frontend.
        meta = {}
        cat = Path("/Users/quarki3/scanner/state/autopilot_catalog.json")
        if cat.exists():
            try:
                for name, h in (json.loads(cat.read_text())
                                .get("pilots") or {}).items():
                    if isinstance(h, dict):
                        meta[name] = {
                            "blurb": h.get("_blurb"),
                            "source": h.get("_holdings_source"),
                            "note": h.get("_note"),
                            "detail": h.get("_detail"),
                            "consumes": h.get("_consumes"),
                            "emits": h.get("_emits"),
                            "cadence": h.get("_cadence"),
                            "record": h.get("_record"),
                        }
                    else:
                        meta[name] = {}
            except Exception:
                pass
        meta["sub5:watchlist"] = {
            "blurb": ("Hand-curated sub-$5 picks (dad approach) — $100 virtual "
                      "per pick, entry gate <= $5, promotion via standard "
                      "PROVEN gate on closed-trade ledger"),
            "source": "state/sub5_watchlist.json (hand-edited)",
        }
        return {"ts": _now_iso(), "pilots": rows, "curves": curve_data,
                "qc_reference": qc_row, "meta": meta}

    @fastapi_app.get("/api/labbook/{name}")
    def labbook(name: str):
        """Per-strategy DATA CENTER for the Lab drawer — positions, targets,
        trade log, and equity curve, assembled read-only from scanner state.
        Owner-only (not in the guest allowlist). Mirrors get a forward curve
        from state/labbook_curves.jsonl (15-min logger); prop_desk strategies
        get a cumulative realized-P/L index rebuilt from their ledger."""
        ST = Path("/Users/quarki3/scanner/state")
        MIRRORS = {
            "qc_mirror": ("qc_mirror_state.json", "qc_mirror_ledger.jsonl"),
            "congress_mirror": ("congress_mirror_state.json",
                                "congress_mirror_ledger.jsonl"),
            "patent_mirror": ("patent_mirror_state.json",
                              "patent_mirror_ledger.jsonl"),
            "combined_mirror": ("combined_mirror_state.json",
                                "combined_mirror_ledger.jsonl"),
        }

        def _jsonl_tail(p: Path, n: int = 200) -> list:
            try:
                return [json.loads(x) for x in
                        p.read_text().splitlines()[-n:] if x.strip()]
            except Exception:
                return []

        out = {"name": name, "positions": [], "targets": {}, "trades": [],
               "curve": [], "bankroll": None, "pnl_pct": None, "ts": None}

        if name in MIRRORS:
            sfile, lfile = MIRRORS[name]
            try:
                st = json.loads((ST / sfile).read_text())
            except Exception:
                st = {}
            out["targets"] = st.get("targets") or {}
            out["bankroll"] = st.get("bankroll")
            out["pnl_pct"] = st.get("pnl_pct")
            out["ts"] = st.get("ts")
            for sym, pos in (st.get("positions") or {}).items():
                if not isinstance(pos, dict):
                    continue
                mv = pos.get("mv") or 0.0
                pl = pos.get("unrealized_pl") or 0.0
                basis = mv - pl
                out["positions"].append({
                    "symbol": sym, "qty": pos.get("qty") or 0.0,
                    "market_value": mv, "unrealized_pl": pl,
                    "unrealized_pl_pct": (pl / basis * 100) if basis else 0.0,
                    "avg_entry": pos.get("avg_entry")})
            for t in reversed(_jsonl_tail(ST / lfile, 120)):
                out["trades"].append({
                    "ts": t.get("ts"), "symbol": t.get("sym"),
                    "side": t.get("side"), "amount": t.get("delta_usd"),
                    "note": t.get("result")})
        elif name.startswith("prop_desk:"):
            strat = name.split(":", 1)[1]
            rows = [t for t in _jsonl_tail(ST / "prop_ledger.jsonl", 3000)
                    if t.get("strategy") == strat]
            eq = 100.0
            for t in rows:
                eq *= 1 + (t.get("pnl_pct") or 0.0) / 100.0
                out["curve"].append({"ts": t.get("exit_ts") or t.get("entry_ts"),
                                     "equity": round(eq, 4)})
            out["curve"] = out["curve"][-500:]
            out["pnl_pct"] = round(eq - 100.0, 3)
            for t in reversed(rows[-120:]):
                out["trades"].append({
                    "ts": t.get("exit_ts") or t.get("entry_ts"),
                    "symbol": t.get("sym"), "side": "ROUND-TRIP",
                    "amount": t.get("size_usd"), "pnl_pct": t.get("pnl_pct"),
                    "note": t.get("note")})

        # Forward-accruing curve for mirrors (logger appends every 15 min;
        # curves start shallow and deepen from 2026-07-29 onward).
        if not out["curve"]:
            lb = ST / "labbook_curves.jsonl"
            if lb.exists():
                pts = []
                for x in lb.read_text().splitlines():
                    try:
                        r = json.loads(x)
                        if r.get("name") == name:
                            pts.append({"ts": r["ts"], "equity": r["equity"]})
                    except Exception:
                        pass
                out["curve"] = pts[-500:]
        return out

    @fastapi_app.get("/api/governor/history")
    def governor_history(limit: int = 500):
        """Chronological governor audit trail: every weight decision (published /
        held / kill-switch) with its reason, plus alerts and executed trades
        with their why-strings. Newest first. Owner-only via the public gate."""
        ST2 = Path("/Users/quarki3/scanner/state")
        out = []

        def _tail(p: Path, n: int) -> list:
            try:
                return [json.loads(x) for x in
                        p.read_text().splitlines()[-n:] if x.strip()]
            except Exception:
                return []

        for d in _tail(ST2 / "governor_decisions.jsonl", limit):
            out.append({"ts": d.get("ts"), "kind": d.get("decision", "decision"),
                        "reason": d.get("reason"), "weights": d.get("weights"),
                        "l1": d.get("l1"), "leader": d.get("leader"),
                        "rolling_pnl": d.get("rolling_pnl"),
                        "tempo": d.get("tempo")})
        for a in _tail(ST2 / "strategy_governor_alerts.jsonl", 200):
            out.append({"ts": a.get("ts"), "kind": "alert:" + str(a.get("type", "?")),
                        "reason": a.get("message"),
                        "challenger": a.get("challenger")})
        for e in _tail(ST2 / "combined_mirror_ledger.jsonl", 300):
            out.append({"ts": e.get("ts"), "kind": "trade",
                        "symbol": e.get("sym"), "side": e.get("side"),
                        "amount": e.get("delta_usd"),
                        "reason": e.get("why") or e.get("result")})
        out.sort(key=lambda r: r.get("ts") or 0, reverse=True)
        return {"ts": _now_iso(), "events": out[:limit]}

    @fastapi_app.get("/api/version")
    def version():
        """Latest Android APK version available for self-update.

        Reads /opt/trading/dist/manifest.json if present. Otherwise proxies the
        canonical Hetzner backend's manifest, so even users whose backend
        URL points at the Mac still get OTA updates.
        """
        for candidate in (Path("/opt/trading/dist/manifest.json"),
                          Path.home() / "trading-dist/manifest.json"):
            if candidate.exists():
                try:
                    return json.loads(candidate.read_text())
                except Exception as e:
                    raise HTTPException(500, f"manifest parse error: {e}")
        try:
            # Use Hetzner's public IP — Mac userspace Tailscale can't route to 100.x from
            # Python sockets, so the tailnet IP would always time out from Mac.
            r = requests.get("http://5.78.111.168:8080/api/version", timeout=4)
            if r.ok:
                return r.json()
        except Exception:
            pass
        return {"version_code": 0, "version_name": "unknown", "url": "", "sha256": "", "notes": ""}

    @fastapi_app.get("/api/apk")
    def apk_download():
        """Serve the latest release APK as a route (not a static mount).

        NiceGUI's static mounts (/static, /dist) return the catch-all HTML on
        this deployment, so OTA downloads go through this FastAPI route, which
        works. The Android UpdateChecker points its manifest url here."""
        from fastapi.responses import FileResponse
        for p in (Path("/opt/trading/dist/trading-latest.apk"),
                  Path.home() / "trading-dist/trading-latest.apk"):
            if p.exists():
                return FileResponse(str(p), media_type="application/vnd.android.package-archive",
                                    filename="trading-latest.apk")
        raise HTTPException(404, "no APK staged")

    @fastapi_app.get("/api/sources")
    def sources():
        return SOURCES

    @fastapi_app.get("/api/portfolio/{source}")
    def portfolio(source: str):
        fn = _PORTFOLIO_DISPATCH.get(source)
        if not fn:
            raise HTTPException(404, f"unknown source: {source}")
        return fn(main_module)

    @fastapi_app.get("/api/portfolio")
    def portfolio_all():
        return [_PORTFOLIO_DISPATCH[s["id"]](main_module) for s in SOURCES]

    @fastapi_app.get("/api/activity/{source}")
    def activity(source: str, limit: int = 100):
        fn = _ACTIVITY_DISPATCH.get(source)
        if not fn:
            raise HTTPException(404, f"unknown source: {source}")
        return fn(main_module, limit)

    @fastapi_app.get("/api/activity")
    def activity_all(limit: int = 50):
        rows: list[dict] = []
        for s in SOURCES:
            try:
                rows.extend(_ACTIVITY_DISPATCH[s["id"]](main_module, limit))
            except Exception as e:
                print(f"[API] activity {s['id']}: {e}")
        rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
        return rows[: limit * 2]

    @fastapi_app.get("/api/activity_detail/{source}")
    def activity_detail(source: str, symbol: str, side: str = "", reason: str = "",
                        mode: str = ""):
        """Per-trade decision blurb for the Book activity list (owner surface;
        deliberately NOT in _GUEST_PATHS — it names live sources and weights).
        Composes: the at-the-time reason recorded on the fill, each strategy
        source's CURRENT rationale for holding the symbol, and the regime the
        governor is running right now. Plain sentences per D28.
        mode=holding swaps the fill-specific 'how' text for a holding story:
        current governor-blend target, held-since date, and the last ledger
        action recorded for the symbol with a why-string."""
        S = Path.home() / "scanner" / "state"

        def _j(name):
            try:
                return json.loads((S / name).read_text())
            except Exception:
                return {}

        sym = symbol.upper()
        picked_by = []

        cong = _j("congress_holdings.json")
        w = (cong.get("holdings") or {}).get(sym)
        if w:
            picked_by.append(
                f"Congress lane: {sym} is {w * 100:.1f}% of the composition built "
                f"from {cong.get('filings_parsed', '?')} parsed House PTR filings "
                f"across {len(cong.get('politicians') or [])} politicians — the lane "
                "mirrors what disclosing members actually bought.")

        pat = _j("patent_mirror_state.json")
        if sym in (pat.get("picks") or []):
            picked_by.append(
                f"Patent lane: {sym} is a current patent-cliff pick — ranked from FDA "
                "Orange Book exclusivity expiries (generics pressure on rivals / "
                "upside for challengers).")

        qc = _j("qc_mirror_state.json")
        qw = (qc.get("targets") or {}).get(sym)
        if qw:
            picked_by.append(
                f"QC lane: {sym} is {qw:.0f}% of the QuantConnect-leaderboard mirror's "
                "target — it holds what the current top community strategies hold.")

        vs = _j("value_screen_state.json")
        for p in (vs.get("picks") or []):
            if (p.get("symbol") or p.get("t")) == sym:
                picked_by.append(
                    "Value screen: a current corrected-quality pick "
                    f"({p.get('reason') or p.get('industry') or 'ranked entry'}).")
                break

        gov_ui = _j("strategy_governor_ui.json")
        gov_w = _j("governor_weights.json")
        weights = gov_w.get("weights") or gov_ui.get("weights") or {}
        top = sorted(weights.items(), key=lambda kv: -kv[1])[:3]
        leader = gov_ui.get("leader")
        if isinstance(leader, dict):
            leader = leader.get("name")
        risk = {}
        try:
            risk = json.loads((Path.home() / "scanner" / "state" / "risk_config.json").read_text())
        except Exception:
            pass
        regime = []
        if top:
            regime.append(
                "The governor is running its adaptive tempo: capital follows earned "
                "weight, currently " +
                " · ".join(f"{k} {v * 100:.0f}%" for k, v in top if v >= 0.005) +
                (f", leader: {leader}" if leader else "") + ".")
        breaker = (risk.get("circuit_breaker") or {}).get("day_pl_pct") or risk.get("breaker_day_pl_pct")
        regime.append(
            "Standing rails: " +
            (f"session freeze if day P/L breaches {breaker}%; " if breaker else "") +
            "single-name cap 35% of the book; buys sized against settled cash only; "
            "drawdown ≥3% of contributed capital forces a review (D6).")

        how = []
        if mode == "holding":
            cm = _j("combined_mirror_state.json")
            tgt = (cm.get("targets") or {}).get(sym)
            if tgt is not None:
                line = (f"The governor blend currently targets {sym} at "
                        f"{float(tgt):.1f}% of the book — the rebalancer only "
                        "buys or sells to close the gap between held weight and "
                        "that number, never on discretion.")
            else:
                line = (f"{sym} is not in the current governor-blend target — "
                        "either an exit is pending on a coming rebalance tick, "
                        "or this name is held by a sleeve outside the mirror.")
            since = (cm.get("pos_since") or {}).get(sym)
            _mt = ZoneInfo("America/Denver")
            if since:
                try:
                    line += (" Held since "
                             f"{datetime.fromtimestamp(float(since), tz=_mt).strftime('%Y-%m-%d')}.")
                except Exception:
                    pass
            how.append(line)
            last = None
            try:
                ledger = S / "combined_mirror_ledger.jsonl"
                for ln in ledger.read_text().splitlines()[-600:]:
                    try:
                        row = json.loads(ln)
                    except Exception:
                        continue
                    if row.get("sym") == sym and row.get("why"):
                        last = row
            except Exception:
                pass
            if last:
                when = datetime.fromtimestamp(last["ts"], tz=_mt).strftime("%Y-%m-%d %H:%M")
                how.append(f"Last recorded action: {last['side']} "
                           f"${abs(last.get('delta_usd') or 0):.2f} on {when} — "
                           f"\"{last['why']}\"")
            else:
                how.append("No per-order reason is on record for this name yet — "
                           "reason recording began 2026-08-11, so older fills "
                           "are bare in the ledger.")
        elif reason:
            how.append(
                f"Recorded at the moment the order was placed: \"{reason}\". "
                "That is the rebalancer's actual arithmetic — the governor-blend "
                "target for the symbol vs what the book held; the order closes "
                "that gap, it is never a discretionary call.")
        else:
            how.append(
                "This fill predates per-order reason recording (or was a broker-side "
                "event like a partial refill), so the at-time arithmetic wasn't "
                "captured. The sources below explain why the symbol is in the "
                "target composition at all.")

        if not picked_by:
            picked_by.append(
                f"No current source composition includes {sym} — this was likely an "
                "exit/trim (the target dropped the name and the rebalancer sold the "
                "remainder), or the source that held it has since rotated away.")

        return {
            "symbol": sym,
            "side": side.upper(),
            "how": how,
            "picked_by": picked_by,
            "regime": regime,
        }

    @fastapi_app.get("/api/equity/{source}")
    def equity(source: str, period: str = "1M", timeframe: str = "1D"):
        fn = _EQUITY_DISPATCH.get(source)
        if not fn:
            raise HTTPException(404, f"unknown source: {source}")
        return fn(main_module, period, timeframe)

    @fastapi_app.get("/api/transfers/{source}")
    def transfers(source: str):
        """Cash deposit/withdrawal events. Owner-only through the public gate
        (deliberately NOT in _GUEST_PATHS — real-money funding history). Sources
        without transfer records return [] so the Terminal degrades cleanly."""
        if source == "alpaca_live":
            return _alpaca_live_transfers()
        return []

    @fastapi_app.get("/api/ticker/{symbol}")
    def ticker(symbol: str, period: str = "1Y"):
        """OHLC close-price bars for a single symbol; uses yfinance so MAX → IPO."""
        try:
            return _ticker_bars(symbol, period)
        except Exception as e:
            raise HTTPException(502, f"yfinance error for {symbol}: {e}")

    @fastapi_app.get("/api/bots")
    def bots():
        return _bots_status()

    @fastapi_app.websocket("/ws/equity/{source}")
    async def equity_ws(websocket: WebSocket, source: str):
        """Push new equity_history rows to connected clients as they're written.

        Client-side: on connect we send the last ~500 rows so the chart can
        render history. Then we poll the local CSV every 2 seconds and forward
        any new rows. Cheaper than 60s file-poll if equity_logger writes
        sub-minute, immediate when it writes."""
        await websocket.accept()
        # Which local reader supplies this source's rows?
        _WS_READERS = {
            "ibkr":        _ibkr_local_equity_history,
            "alpaca_live": _alpaca_live_equity_history,
        }
        reader = _WS_READERS.get(source)
        try:
            if reader is None:
                await websocket.send_json({"type": "error", "msg": f"source '{source}' not yet supported on ws"})
                await websocket.close(code=4004)
                return

            rows = reader()
            initial = rows[-500:] if rows else []
            await websocket.send_json({"type": "init", "data": initial})
            last_ts = initial[-1]["ts"] if initial else ""

            while True:
                await asyncio.sleep(2)
                rows = reader()
                if not rows:
                    continue
                new_rows = [r for r in rows if r["ts"] > last_ts] if last_ts else []
                if new_rows:
                    await websocket.send_json({"type": "append", "data": new_rows})
                    last_ts = new_rows[-1]["ts"]
        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[ws] equity {source} closed: {e}")

    @fastapi_app.get("/api/summary")
    def summary():
        """One round-trip overview for the Android home screen."""
        per_source = []
        for s in SOURCES:
            try:
                p = _PORTFOLIO_DISPATCH[s["id"]](main_module)
            except Exception as e:
                print(f"[API] summary {s['id']}: {e}")
                p = _empty_portfolio(s["id"])
            per_source.append({**s, **p})
        return {
            "ts": _now_iso(),
            "sources": per_source,
            "bots": _bots_status(),
        }

    @fastapi_app.get("/api/patent-cliff")
    def patent_cliff():
        """Combined patent-cliff payload for the Android Patent screen.
        Reads the latest CSVs from ~/patent_cliff/output/."""
        out_dir = Path("/Users/quarki3/patent_cliff/output")
        data_dir = Path("/Users/quarki3/patent_cliff/data")

        def _latest(glob: str) -> Path | None:
            files = sorted(out_dir.glob(glob))
            return files[-1] if files else None

        def _safe_csv_rows(path: Path | None, limit: int | None = None) -> list[dict]:
            if path is None or not path.exists():
                return []
            try:
                with path.open() as f:
                    rdr = csv.DictReader(f)
                    rows = list(rdr)
            except Exception:
                return []
            if limit is not None:
                rows = rows[:limit]
            return rows

        cliffs = _safe_csv_rows(_latest("cliff_scan_*.csv"), limit=50)
        first_filer = _safe_csv_rows(_latest("first_filer_opportunities_*.csv"))
        exposure = _safe_csv_rows(_latest("generic_exposure_*.csv"))
        picks = _safe_csv_rows(_latest("portfolio_picks_*.csv"))
        sec_path = data_dir / "sec_signals.csv"
        sec_signals = _safe_csv_rows(sec_path, limit=25) if sec_path.exists() else []

        def _ftd(p: Path | None) -> str | None:
            return datetime.fromtimestamp(p.stat().st_mtime).isoformat() if p else None

        # Patent mirror P&L + start ts from scanner/state/patent_mirror_state.json
        mirror_pnl = None
        patent_started_ts = None
        try:
            mp = Path("/Users/quarki3/scanner/state/patent_mirror_state.json")
            if mp.exists():
                mirror = json.loads(mp.read_text())
                mirror_pnl = mirror.get("pnl_pct")
            # First entry in patent_mirror_ledger = when we opened first patent position
            pl = Path("/Users/quarki3/scanner/state/patent_mirror_ledger.jsonl")
            if pl.exists():
                first = pl.read_text().splitlines()
                if first:
                    patent_started_ts = json.loads(first[0]).get("ts")
        except Exception:
            pass

        return {
            "ts": _now_iso(),
            "generated": {
                "cliff_scan":     _ftd(_latest("cliff_scan_*.csv")),
                "first_filer":    _ftd(_latest("first_filer_opportunities_*.csv")),
                "exposure":       _ftd(_latest("generic_exposure_*.csv")),
                "picks":          _ftd(_latest("portfolio_picks_*.csv")),
                "sec_signals":    _ftd(sec_path) if sec_path.exists() else None,
            },
            "stats": {
                "cliff_count": len(cliffs),
                "first_filer_count": len(first_filer),
                "exposure_count": len(exposure),
                "picks_count": len(picks),
                "sec_signal_count": len(sec_signals),
            },
            "cliffs": cliffs,
            "first_filer": first_filer,
            "exposure": exposure,
            "picks": picks,
            "sec_signals": sec_signals,
            "pnl_pct": mirror_pnl,
            "started_ts": patent_started_ts,
        }
