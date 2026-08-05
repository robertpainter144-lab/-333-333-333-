"""Sensor-account bridge: brokerage positions → autopilot_holdings.json.

Autopilot (joinautopilot.com) has no API, but it executes its portfolio's
trades inside a linked brokerage. This feed polls that "sensor" account,
converts its equity positions to normalized weights, and writes
state/autopilot_holdings.json — the file autopilot_mirror and
combined_mirror read. The sensor account's holdings ARE the portfolio
composition.

Primary path — SnapTrade → Robinhood (user's $500 AM account, read-only):
  1. Sign up at snaptrade.com (free individual tier, 5 connections).
     From the dashboard note clientId + consumerKey.
  2. Write state/autopilot_feed_config.json:
       {"broker": "snaptrade", "client_id": "...", "consumer_key": "..."}
  3. Run `python3.10 snaptrade_connect.py` (scanner root) once — it
     registers the SnapTrade user, saves user_secret into the config, and
     prints a connection-portal URL to log into Robinhood.
Free-tier holdings are cached daily by SnapTrade — plenty for a portfolio
that trades a few times a month.

Alternate path — Schwab sensor account (official individual API):
  config {"broker": "schwab", "app_key": ..., "app_secret": ...} plus a
  one-time OAuth via schwab_auth.py → state/schwab_tokens.json. Schwab
  refresh tokens die every 7 days; the feed alerts when re-auth is needed.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

CONFIG   = STATEDIR / "autopilot_feed_config.json"
TOKENS   = STATEDIR / "schwab_tokens.json"
HOLDINGS = STATEDIR / "autopilot_holdings.json"

SCHWAB_API   = "https://api.schwabapi.com"
TOKEN_URL    = f"{SCHWAB_API}/v1/oauth/token"
REFRESH_MAX_AGE_S = 7 * 86400   # Schwab refresh tokens die after 7 days

# Ignore dust and cash-equivalents when deriving weights
MIN_POSITION_USD = 1.00


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _access_token(cfg: dict) -> str | None:
    """Return a valid access token, refreshing if needed. None → not usable."""
    tok = _load(TOKENS)
    if not tok:
        return None
    now = int(time.time())
    if now - int(tok.get("refresh_issued_ts", 0)) > REFRESH_MAX_AGE_S:
        return None  # refresh token expired — needs manual re-auth
    if now < int(tok.get("access_expires_ts", 0)) - 60:
        return tok.get("access_token")
    try:
        r = requests.post(TOKEN_URL, timeout=15,
                          auth=(cfg["app_key"], cfg["app_secret"]),
                          data={"grant_type": "refresh_token",
                                "refresh_token": tok["refresh_token"]})
        if not r.ok:
            return None
        d = r.json()
        tok["access_token"] = d["access_token"]
        tok["access_expires_ts"] = now + int(d.get("expires_in", 1800))
        # Schwab may rotate the refresh token
        if d.get("refresh_token"):
            tok["refresh_token"] = d["refresh_token"]
        TOKENS.write_text(json.dumps(tok, indent=2))
        return tok["access_token"]
    except Exception:
        return None


def _positions(token: str) -> dict[str, float] | None:
    """{symbol: market_value} for equity positions across sensor accounts.
    None on any API failure (fail closed — never write a partial holdings
    file; a wrong composition would steer paper AND, via the governor,
    eventually live money)."""
    try:
        r = requests.get(f"{SCHWAB_API}/trader/v1/accounts",
                         params={"fields": "positions"},
                         headers={"Authorization": f"Bearer {token}"},
                         timeout=15)
        if not r.ok:
            return None
        out: dict[str, float] = {}
        for acct in r.json():
            sec = acct.get("securitiesAccount", {})
            for p in sec.get("positions", []):
                inst = p.get("instrument", {})
                if inst.get("assetType") not in ("EQUITY", "ETF",
                                                 "COLLECTIVE_INVESTMENT"):
                    continue
                sym = inst.get("symbol", "")
                mv = float(p.get("marketValue", 0) or 0)
                if sym and mv >= MIN_POSITION_USD:
                    out[sym] = out.get(sym, 0.0) + mv
        return out
    except Exception:
        return None


def _snaptrade_signed(cfg: dict, method: str, path: str,
                       query_extra: dict | None = None,
                       body: dict | None = None) -> requests.Response:
    """SnapTrade HMAC-signed request. Personal (PERS-) keys authenticate every
    call with clientId+timestamp query params and a Signature header; no
    userSecret exists on this tier (the user is auto-provisioned)."""
    import base64
    import hashlib
    import hmac
    from urllib.parse import quote
    q = {"clientId": cfg["client_id"], "timestamp": str(int(time.time()))}
    if query_extra:
        q.update(query_extra)
    query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in q.items())
    sig_obj = {"content": body, "path": f"/api/v1{path}", "query": query}
    sig_content = json.dumps(sig_obj, separators=(",", ":"), sort_keys=True)
    sig = base64.b64encode(hmac.new(cfg["consumer_key"].encode(),
                                     sig_content.encode(),
                                     hashlib.sha256).digest()).decode()
    return requests.request(method,
                            f"https://api.snaptrade.com/api/v1{path}?{query}",
                            json=body, headers={"Signature": sig}, timeout=15)


def _snaptrade_positions(cfg: dict) -> dict[str, float] | None:
    """{symbol: market_value} via SnapTrade (Robinhood etc.). None on any
    failure or incomplete setup — fail closed, never write partial holdings."""
    user_id = cfg.get("user_id")
    if not user_id:
        log_tick("autopilot_feed", "no user_id in config — idle")
        return None
    try:
        r = _snaptrade_signed(cfg, "GET", "/accounts", {"userId": user_id})
        if not r.ok:
            log_tick("autopilot_feed", f"accounts HTTP {r.status_code}")
            return None
        accounts = r.json()
        if not accounts:
            log_tick("autopilot_feed", "no brokerage connected on SnapTrade yet")
            return None
        out: dict[str, float] = {}
        for acct in accounts:
            aid = acct.get("id")
            if not aid:
                continue
            rp = _snaptrade_signed(cfg, "GET", f"/accounts/{aid}/positions",
                                    {"userId": user_id})
            if not rp.ok:
                return None  # partial read must not produce partial weights
            for p in (rp.json() or []):
                # Universal symbol nests as symbol.symbol.symbol; dig defensively
                node, sym = p.get("symbol"), None
                for _ in range(3):
                    if isinstance(node, str):
                        sym = node
                        break
                    if not isinstance(node, dict):
                        break
                    node = node.get("symbol")
                units = float(p.get("units") or 0)
                price = float(p.get("price") or 0)
                mv = units * price
                if sym and mv >= MIN_POSITION_USD:
                    out[sym] = out.get(sym, 0.0) + mv
        return out
    except Exception as e:
        log_tick("autopilot_feed", f"snaptrade error: {e!r}"[:120])
        return None


def tick() -> None:
    now = int(time.time())
    cfg = _load(CONFIG)
    if not cfg:
        log_tick("autopilot_feed", "no config — idle (see module docstring)")
        return
    broker = cfg.get("broker", "snaptrade")
    if broker == "snaptrade":
        if not cfg.get("client_id"):
            log_tick("autopilot_feed", "no snaptrade credentials — idle")
            return
        mvs = _snaptrade_positions(cfg)
        _publish(now, mvs, "snaptrade sensor account (Autopilot-managed Robinhood)")
        return
    if not cfg.get("app_key"):
        log_tick("autopilot_feed", "no config — idle (see module docstring)")
        return
    token = _access_token(cfg)
    if not token:
        # Alert at most once/hour so a dead token doesn't spam
        tok = _load(TOKENS) or {}
        if now - int(tok.get("last_reauth_alert_ts", 0)) > 3600:
            tok["last_reauth_alert_ts"] = now
            try:
                TOKENS.write_text(json.dumps(tok, indent=2))
            except Exception:
                pass
            emit_live("autopilot_feed",
                      "[AUTH] Schwab token expired/missing — run schwab_auth.py "
                      "to restore the Autopilot holdings feed")
        log_tick("autopilot_feed", "no valid token — idle")
        return
    mvs = _positions(token)
    _publish(now, mvs, "schwab sensor account (Autopilot-managed)")


def _publish(now: int, mvs: dict[str, float] | None, source: str) -> None:
    """Normalize {sym: market_value} → weights and write the holdings file.
    None (fetch failed) or empty (mid-rebalance?) leaves the last-known
    holdings untouched — never zero the composition on bad data."""
    if mvs is None:
        log_tick("autopilot_feed", "positions fetch failed — holdings unchanged")
        return
    if not mvs:
        log_tick("autopilot_feed", "sensor account empty — holdings unchanged")
        return
    total = sum(mvs.values())
    holdings = {s: round(v / total, 4) for s, v in sorted(
        mvs.items(), key=lambda kv: -kv[1])}
    prev = _load(HOLDINGS) or {}
    changed = holdings != (prev.get("holdings") or {})
    HOLDINGS.write_text(json.dumps({
        "updated_ts": now,
        "source": source,
        "sensor_total_usd": round(total, 2),
        "holdings": holdings,
    }, indent=2))
    if changed:
        emit_live("autopilot_feed",
                  f"[feed] holdings updated: {len(holdings)} names, "
                  f"top {next(iter(holdings))} "
                  f"{holdings[next(iter(holdings))]*100:.1f}%")
    log_tick("autopilot_feed",
             f"{len(holdings)} names ${total:.0f} changed={changed}")


if __name__ == "__main__":
    tick()
