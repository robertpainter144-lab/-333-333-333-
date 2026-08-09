"""Lab333 API — fresh owner-only backend for Terminal 333.

Written from scratch for the lab333 identity: no code shared with any
earlier dashboard. Serves the endpoints the Terminal 333 Android app
consumes, reading the broker data files the loggers already write.

Run:  LAB333_BIND=100.x.y.z uvicorn main:app --host $LAB333_BIND --port 8334
Trust model: bind to the tailnet (or localhost) only — same owner-only
posture as the rest of the stack. No public exposure, no API keys to leak.
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

HOME = Path.home()


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default))).expanduser()


# Per-source data files (env-overridable so the backend can run on any host
# that has the logger outputs synced to it).
ALPACA_EQUITY_CSV = _env_path("LAB333_ALPACA_EQUITY_CSV",
                              HOME / "alpaca_live_arm_state" / "equity.csv")
IBKR_EQUITY_CSV = _env_path("LAB333_IBKR_EQUITY_CSV",
                            HOME / "ibkr_bot" / "equity_history.csv")
IBKR_POSITIONS_JSON = _env_path("LAB333_IBKR_POSITIONS_JSON",
                                HOME / "ibkr_bot" / "positions_snapshot.json")
IBKR_TRANSACTIONS_CSV = _env_path("LAB333_IBKR_TRANSACTIONS_CSV",
                                  HOME / "ibkr_bot" / "transactions.csv")

SOURCES = {
    "alpaca_live": {
        "id": "alpaca_live", "label": "ALPACA · live", "kind": "broker",
        "real_money": True, "color": "#3fb950",
        "starting_capital": float(os.environ.get("LAB333_ALPACA_SEED", "50")),
    },
    "ibkr": {
        "id": "ibkr", "label": "IBKR · live", "kind": "broker",
        "real_money": True, "color": "#58a6ff",
        "starting_capital": float(os.environ.get("LAB333_IBKR_SEED", "500")),
    },
}

WS_POLL_SECONDS = 5.0

app = FastAPI(title="lab333-api", docs_url=None, redoc_url=None)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_z(raw: str) -> str:
    """Normalise a logger timestamp to ISO-8601 with a Z suffix."""
    return raw.strip().replace("+00:00", "Z")


def read_equity_rows(source: str) -> list[dict]:
    path = ALPACA_EQUITY_CSV if source == "alpaca_live" else IBKR_EQUITY_CSV
    ts_col = "ts" if source == "alpaca_live" else "timestamp"
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        with path.open() as f:
            for row in csv.DictReader(f):
                try:
                    eq = float(row.get("equity") or 0)
                except ValueError:
                    continue
                if eq <= 0:
                    continue
                rows.append({
                    "ts": iso_z(row.get(ts_col, "")),
                    "equity": round(eq, 2),
                    "cash": float(row.get("cash") or 0),
                })
    except Exception:
        return []
    return rows


def require_source(source: str) -> dict:
    cfg = SOURCES.get(source)
    if cfg is None:
        raise HTTPException(404, f"unknown source '{source}'")
    return cfg


@app.get("/api/health")
def health():
    tailscale_ip = None
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addr = info[4][0]
            if addr.startswith("100."):
                tailscale_ip = addr
                break
    except OSError:
        pass
    return {"ok": True, "ts": now_iso(), "host": socket.gethostname(),
            "tailscale_ip": tailscale_ip, "version": 1}


@app.get("/api/sources")
def sources():
    return [{k: v for k, v in cfg.items() if k != "starting_capital"}
            for cfg in SOURCES.values()]


@app.get("/api/equity/{source}")
def equity(source: str, period: str = "1M", limit: int = 5000):
    require_source(source)
    rows = read_equity_rows(source)[-limit:]
    return [{"ts": r["ts"], "equity": r["equity"]} for r in rows]


def _positions(source: str) -> list[dict]:
    if source != "ibkr" or not IBKR_POSITIONS_JSON.exists():
        return []
    try:
        snap = json.loads(IBKR_POSITIONS_JSON.read_text())
    except Exception:
        return []
    out = []
    for p in snap.get("positions", []):
        qty = float(p.get("qty") or p.get("position") or 0)
        avg = float(p.get("avg_cost") or 0)
        px = float(p.get("market_price") or 0)
        mv = float(p.get("market_value") or qty * px)
        upl = float(p.get("unrealized_pl") or (mv - qty * avg))
        out.append({
            "symbol": p.get("symbol", "?"), "qty": qty, "avg_cost": avg,
            "market_price": px, "market_value": round(mv, 2),
            "unrealized_pl": round(upl, 2),
            "unrealized_pl_pct": round(upl / (qty * avg) * 100, 2) if qty * avg else 0.0,
        })
    return out


def _portfolio(source: str) -> dict:
    cfg = require_source(source)
    rows = read_equity_rows(source)
    equity_now = rows[-1]["equity"] if rows else 0.0
    cash_now = rows[-1]["cash"] if rows else 0.0
    peak = max((r["equity"] for r in rows), default=equity_now)
    positions = _positions(source)
    return {
        "source": source, "account": None,
        "equity": equity_now, "cash": cash_now, "bp": cash_now,
        "unrealized_pl": round(sum(p["unrealized_pl"] for p in positions), 2),
        "starting_capital": cfg["starting_capital"],
        "peak_equity": peak,
        "drawdown_pct": round((peak - equity_now) / peak * 100, 2) if peak else 0.0,
        "positions": positions, "position_count": len(positions),
        "ts": now_iso(),
    }


@app.get("/api/portfolio/{source}")
def portfolio(source: str):
    return _portfolio(source)


def _activity(source: str, limit: int) -> list[dict]:
    if source != "ibkr" or not IBKR_TRANSACTIONS_CSV.exists():
        return []
    out = []
    try:
        with IBKR_TRANSACTIONS_CSV.open() as f:
            for row in csv.DictReader(f):
                qty = float(row.get("qty") or row.get("quantity") or 0)
                px = float(row.get("price") or 0)
                out.append({
                    "source": source, "ts": iso_z(row.get("ts") or row.get("timestamp") or ""),
                    "symbol": row.get("symbol", "?"),
                    "side": (row.get("side") or "?").lower(),
                    "qty": qty, "price": px, "notional": round(qty * px, 2),
                    "status": row.get("status", "filled"), "type": row.get("type", "market"),
                    "tif": row.get("tif", "day"), "order_id": row.get("order_id"),
                    "reason": row.get("reason", ""),
                })
    except Exception:
        return []
    return out[-limit:][::-1]


@app.get("/api/activity/{source}")
def activity(source: str, limit: int = 100):
    require_source(source)
    return _activity(source, limit)


@app.get("/api/activity")
def activity_all(limit: int = 100):
    merged = [a for s in SOURCES for a in _activity(s, limit)]
    merged.sort(key=lambda a: a["ts"], reverse=True)
    return merged[:limit]


@app.get("/api/summary")
def summary():
    out = []
    for source, cfg in SOURCES.items():
        p = _portfolio(source)
        p.update({k: cfg[k] for k in ("id", "label", "kind", "real_money", "color")})
        out.append(p)
    return {"ts": now_iso(), "sources": out, "bots": []}


# Screens not yet backed by the lab333 stack get empty-but-valid payloads
# so the app renders "no data" instead of erroring.
for route in ("bots", "insider", "whales", "strategies", "qc_lab/projects"):
    app.get(f"/api/{route}")(lambda route=route: [])
for route in ("insider/leaderboard", "insider/ledgers", "qc", "patent-cliff",
              "prop_desk/state", "market_pulse", "catalog"):
    app.get(f"/api/{route}")(lambda route=route: {})


@app.websocket("/ws/equity/{source}")
async def ws_equity(ws: WebSocket, source: str):
    await ws.accept()
    if source not in SOURCES:
        await ws.send_text(json.dumps(
            {"type": "error", "data": [], "msg": f"unknown source '{source}'"}))
        await ws.close()
        return
    rows = read_equity_rows(source)
    await ws.send_text(json.dumps({
        "type": "init",
        "data": [{"ts": r["ts"], "equity": r["equity"]} for r in rows[-2000:]],
    }))
    sent = len(rows)
    try:
        while True:
            await asyncio.sleep(WS_POLL_SECONDS)
            rows = read_equity_rows(source)
            if len(rows) > sent:
                fresh = rows[sent:]
                await ws.send_text(json.dumps({
                    "type": "append",
                    "data": [{"ts": r["ts"], "equity": r["equity"]} for r in fresh],
                }))
                sent = len(rows)
            elif len(rows) < sent:   # file rotated/truncated — resync
                sent = len(rows)
    except WebSocketDisconnect:
        pass
