#!/usr/bin/env python3
"""Minimal Mac-side server for the /app webapp + owner crypto GUI (stdlib only).

Interim serving path while the VM stack is unreachable from the Mac: the phone
connects via `adb reverse tcp:8123 tcp:8123` (or tailnet) and gets:
  /app                       webapp shell + owner crypto bundle (always owner
                             mode — this server binds localhost only)
  /owner/crypto.js           the private bundle
  /api/portfolio/alpaca_live live account+positions straight from Alpaca
  /api/equity/alpaca_live    Alpaca portfolio-history
  /api/equity/crypto_sleeve  sleeve equity.csv
  /api/crypto_sleeve         sleeve status (state+ledger+whale gate)
Anything else under /api returns [] so the shell degrades quietly.

Run: python3 ng_dashboard/mac_serve.py   (serves 127.0.0.1:8123)
"""
from __future__ import annotations

import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent          # ng_dashboard/
REPO = ROOT.parent
SLEEVE = REPO / "crypto_sleeve"
PORT = 8123
STARTING_CAPITAL = 500.0                        # Σ deposits (2026-08-04 baseline)

env = {}
for line in (Path.home() / "alpaca_live.env").read_text().splitlines():
    line = line.strip()
    if "=" in line and not line.startswith("#"):
        k, _, v = line.partition("=")
        env[k.strip().replace("export ", "")] = v.strip().strip('"').strip("'")
A_KEY = next(v for k, v in env.items() if "KEY" in k.upper() and "SECRET" not in k.upper())
A_SEC = next(v for k, v in env.items() if "SECRET" in k.upper())
A_HDR = {"APCA-API-KEY-ID": A_KEY, "APCA-API-SECRET-KEY": A_SEC}


def alpaca(path: str):
    req = urllib.request.Request("https://api.alpaca.markets" + path, headers=A_HDR)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


_cache: dict[str, tuple[float, object]] = {}


def cached(key: str, ttl: float, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def portfolio_alpaca():
    acct = alpaca("/v2/account")
    poss = alpaca("/v2/positions")
    eq = float(acct["equity"])
    hist = equity_alpaca("3M")
    peak = max([p["equity"] for p in hist] + [eq]) if hist else eq
    return {
        "equity": eq, "cash": float(acct["cash"]),
        "settled_bp": float(acct.get("non_marginable_buying_power") or acct["cash"]),
        "starting_capital": STARTING_CAPITAL, "peak_equity": peak,
        "positions": [{
            "symbol": p["symbol"], "qty": float(p["qty"]),
            "market_value": float(p["market_value"]),
            "avg_cost": float(p["avg_entry_price"]),
            "market_price": float(p["current_price"]),
            "unrealized_pl": float(p["unrealized_pl"]),
            "unrealized_pl_pct": float(p["unrealized_plpc"]) * 100,
        } for p in sorted(poss, key=lambda x: -float(x["market_value"]))],
    }


_ALPACA_PERIODS = {"1H": ("1D", "5Min"), "1D": ("1D", "5Min"), "5D": ("1W", "1H"),
                   "1W": ("1W", "1H"), "1M": ("1M", "1D"), "3M": ("3M", "1D")}


def equity_alpaca(period: str):
    ap, tf = _ALPACA_PERIODS.get(period, ("1M", "1D"))
    h = alpaca(f"/v2/account/portfolio/history?period={ap}&timeframe={tf}")
    out = [{"ts": t, "equity": e} for t, e in zip(h["timestamp"], h["equity"])
           if e is not None and e > 0]
    return out


_PERIOD_S = {"1H": 3600, "1D": 86400, "5D": 5 * 86400, "1W": 7 * 86400,
             "1M": 31 * 86400, "3M": 93 * 86400}


def equity_crypto(period: str):
    f = SLEEVE / "state" / "equity.csv"
    if not f.exists():
        return []
    pts = []
    with f.open() as fh:
        for r in csv.DictReader(fh):
            try:
                pts.append({"ts": int(datetime.fromisoformat(r["ts"]).timestamp()),
                            "equity": float(r["equity"])})
            except (ValueError, KeyError):
                continue
    cutoff = _PERIOD_S.get(period.upper())
    if cutoff:
        now = time.time()
        pts = [p for p in pts if now - p["ts"] <= cutoff]
    return pts


def crypto_status():
    out = {"available": True, "positions": {}, "frozen": False,
           "realized": 0.0, "gate": None, "config": {}, "closed_trades": []}
    try:
        st = json.loads((SLEEVE / "state" / "sleeve_state.json").read_text())
        out["positions"] = st.get("positions", {})
        out["frozen"] = bool(st.get("frozen"))
    except Exception:
        pass
    try:
        cfg = json.loads((SLEEVE / "config.json").read_text())
        out["config"] = {k: cfg.get(k) for k in
                         ("broker", "universe", "cap_total_usd", "per_trade_usd",
                          "stop_pct", "take_pct", "freeze_pnl_usd")}
    except Exception:
        pass
    try:
        out["gate"] = json.loads((SLEEVE / "state" / "whale_signals.json").read_text())
    except Exception:
        pass
    try:
        with (SLEEVE / "state" / "closed_trades.csv").open() as fh:
            raw = list(csv.DictReader(fh))
        rows, realized = [], 0.0
        for r in raw:
            try:
                realized += float(r["realized_pl"])
                rows.append(r)
            except (ValueError, KeyError, TypeError):
                continue
        out["realized"] = round(realized, 4)
        out["closed_trades"] = rows[-20:]
    except Exception:
        pass
    eq = equity_crypto("ALL")
    if eq:
        out["equity"] = eq[-1]["equity"]
    return out


class H(BaseHTTPRequestHandler):
    def _send(self, body, ctype="application/json", code=200):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("cache-control", "no-cache")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        period = (q.get("period") or ["1M"])[0]
        try:
            if path in ("/app", "/"):
                html = (ROOT / "static" / "webapp.html").read_text()
                html = html.replace("</body>",
                                    '<script defer src="/owner/crypto.js"></script></body>')
                return self._send(html, "text/html")
            if path == "/owner/crypto.js":
                return self._send((ROOT / "private" / "owner_crypto.js").read_text(),
                                  "application/javascript")
            if path == "/api/portfolio/alpaca_live":
                return self._send(cached("port", 10, portfolio_alpaca))
            if path == "/api/equity/alpaca_live":
                return self._send(cached("eq" + period, 30, lambda: equity_alpaca(period)))
            if path == "/api/equity/crypto_sleeve":
                return self._send(equity_crypto(period))
            if path == "/api/crypto_sleeve":
                return self._send(crypto_status())
            if path.startswith("/api/"):
                return self._send([])
            return self._send({"error": "not found"}, code=404)
        except Exception as e:
            return self._send({"error": str(e)}, code=502)


if __name__ == "__main__":
    print(f"mac_serve on 127.0.0.1:{PORT} — adb reverse tcp:{PORT} tcp:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
