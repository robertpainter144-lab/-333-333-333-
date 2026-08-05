#!/usr/bin/env python3
"""
Unified Trading Dashboard — NiceGUI
  • Permanent left sidebar, dark terminal theme
  • QC Portfolio → Alpaca is the main view (default page)
  • All buttons execute real actions
  • Serves on 0.0.0.0:8080 — Android via Tailscale

Launch:  python3.10 ng_dashboard/main.py
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import requests
from nicegui import ui, app as _app

# ── Root paths ────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).parent.parent
QC_DIR   = ROOT / "qc_portfolio"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(QC_DIR))

from alpaca_keys import PAPER_KEY, PAPER_SECRET, BROKER_KEY, BROKER_SECRET

# Shadow engine = A/B test bot running adaptive + leveraged-ETF stop rules
# in a virtual portfolio, parallel to the production qc_portfolio bot.
sys.path.insert(0, str(ROOT / "qc_portfolio_test"))
try:
    from engine import ShadowEngine
    _shadow = ShadowEngine()
except Exception as _e:
    print(f"[SHADOW] init failed: {_e}")
    _shadow = None

def _shadow_tick_loop():
    """Tick the shadow engine every 60s in the background."""
    while True:
        try:
            if _shadow is not None:
                _shadow.tick()
        except Exception as e:
            print(f"[SHADOW] tick err: {e}")
        time.sleep(60)

if _shadow is not None:
    threading.Thread(target=_shadow_tick_loop, daemon=True).start()

# Mount the JSON REST API used by the Android app (deferred until module
# bottom — see api.mount() at the end of file).

PAPER_BASE  = "https://paper-api.alpaca.markets"
PAPER_HDR   = {"APCA-API-KEY-ID": PAPER_KEY, "APCA-API-SECRET-KEY": PAPER_SECRET}

BROKER_BASE = "https://broker-api.sandbox.alpaca.markets"
BROKER_HDR  = {
    "Authorization": "Basic " + base64.b64encode(f"{BROKER_KEY}:{BROKER_SECRET}".encode()).decode(),
    "Content-Type": "application/json",
}

# Active trading account — fresh $50k broker sandbox account
ACTIVE_ACCT  = "b6ee7e43-9361-4618-845d-3b97bccc9d4c"
ACTIVE_BASE  = f"{BROKER_BASE}/v1/trading/accounts/{ACTIVE_ACCT}"
ACTIVE_HDR   = BROKER_HDR

# Reference-only accounts (closed, shown for context)
OTHER_ACCOUNTS = {
    "e7f4c828-9a09-4eb1-a939-58985613e6a0": "Account #2  (CLOSED)",
    "457113cf-ad32-4b37-b58a-e7d768e319e0": "Account #1  (CLOSED)",
}

# Paper account shown as legacy reference
SHOW_PAPER = True

ET = ZoneInfo("America/New_York")

# ── Colour palette ────────────────────────────────────────────────────────────
BG   = "#0d1117"
BG2  = "#161b22"
BG3  = "#21262d"
BDR  = "#30363d"
FG   = "#c9d1d9"
FG2  = "#8b949e"
GRN  = "#3fb950"
RED  = "#f85149"
YLW  = "#d29922"
BLU  = "#58a6ff"
PRP  = "#bc8cff"

STRAT_COLORS = ["#00d4ff", "#00ff88", "#ff9f43", "#a29bfe", "#fd79a8"]

# ── Tooltip style ─────────────────────────────────────────────────────────────
_TS = (f"background:{BG3};color:{FG};border:1px solid {BDR};"
       f"font-family:monospace;font-size:0.75rem;max-width:340px;"
       f"padding:10px 12px;border-radius:8px;white-space:pre-wrap;line-height:1.5")

# ── Decision log ──────────────────────────────────────────────────────────────
_DLOG: list[dict] = []

def _log(action: str, detail: str = "", result: str = ""):
    _DLOG.insert(0, {"ts": datetime.now(ET).strftime("%H:%M:%S"),
                     "action": action, "detail": detail, "result": result})
    del _DLOG[100:]

# ── Global settings (persists within session) ─────────────────────────────────
_SETTINGS: dict = {"tooltips": True}

# ── Strategy explanations ─────────────────────────────────────────────────────
STRAT_INFO: dict = {
    245: dict(short="Rotates 100% into highest-momentum tech name weekly.",
              universe="AMD · TSLA · AMZN · AAPL · SPXL",
              alloc="Single winner — full concentration",
              edge="Momentum in tech mega-caps persists over 20–120 day windows.",
              risk="High. SPXL is 3× leveraged. Amplified downside."),
    46:  dict(short="Rotates into the single best-performing 3× leveraged ETF.",
              universe="TQQQ · UPRO · SOXL · TECL · LABU · FAS · FNGU · TNA",
              alloc="Single winner — full concentration in one leveraged ETF",
              edge="Leveraged ETF momentum is explosive in sector bull runs.",
              risk="Very high. Volatility decay hurts in choppy markets."),
    123: dict(short="Competes 8 mega-caps vs QQQ; holds the single top name.",
              universe="AAPL · AMZN · GOOGL · META · MSFT · NVDA · TSLA · AMD · QQQ",
              alloc="Single winner — QQQ as diversification escape valve",
              edge="QQQ fallback prevents concentration when no clear leader.",
              risk="Moderate-high. Single stock concentration risk."),
    60:  dict(short="Momentum-ranks 8 leveraged bull ETFs; goes 100% into the top.",
              universe="TQQQ · UPRO · SOXL · TECL · SPXL · FNGU · TNA · UDOW",
              alloc="Single winner",
              edge="Rotates leverage to whichever sector is leading the market.",
              risk="Very high. Similar risk profile to strategy #2."),
    345: dict(short="Equal-weight top-5 momentum names from S&P mega-caps.",
              universe="AAPL · MSFT · NVDA · AMZN · GOOGL · META · TSLA · AVGO · AMD · JPM",
              alloc="Top-5 equal weight — most diversified strategy",
              edge="Spreads momentum exposure across 5 names, reducing single-stock risk.",
              risk="Moderate. No leverage. 5-stock diversification."),
    382: dict(short="Rotates into top AI/data center momentum name.",
              universe="NVDA · AMD · AVGO · SMCI · DELL · HPE · ANET · MSFT · ORCL · CRM",
              alloc="Single winner",
              edge="Concentrated exposure to AI infrastructure buildout theme.",
              risk="High. Sector-concentrated in AI/semis."),
    252: dict(short="Risk-off rotation into safe-haven assets.",
              universe="TLT · GLD · SHV · VIXY · UUP · IAU · AGG · BND · TIPS · IGSB",
              alloc="Top-3 equal weight",
              edge="Defensive capital preservation when market regime turns bearish.",
              risk="Low. Bonds and gold. May lag in bull markets."),
    364: dict(short="Long/short momentum across top US mega-caps.",
              universe="AAPL · MSFT · NVDA · AMZN · GOOGL · META · TSLA · AMD · AVGO · JPM · LLY · V",
              alloc="Top-5 equal weight",
              edge="Broad momentum across the largest US equities.",
              risk="Moderate. Diversified large-cap exposure."),
    446: dict(short="Tail-risk protected long-only portfolio.",
              universe="SPY · QQQ · IWM · EFA · GLD · TLT · AAPL · MSFT · NVDA · AMZN",
              alloc="Top-3 equal weight",
              edge="Mixes equities with tail-risk hedges (GLD, TLT) for downside protection.",
              risk="Low-moderate. Built-in hedges limit upside and downside."),
    315: dict(short="Volatility-clustering options strategy (equity proxy).",
              universe="AAPL · MSFT · NVDA · GOOGL · META · AMZN · AMD · TSLA",
              alloc="Top-3 equal weight",
              edge="Exploits volatility mean-reversion patterns.",
              risk="Moderate-high. Volatility timing can fail in trending markets."),
}

# ── Button tooltips ───────────────────────────────────────────────────────────
BTN_TIP = {
    "deploy":    ("Deploy QC Strategies\n\n"
                  "Full pipeline:\n"
                  "  1. Fetch QC top-5 leaderboard rankings\n"
                  "  2. Apply 2× weight boost to #1 strategy\n"
                  "  3. Run momentum engine → pick winners\n"
                  "  4. Compute order sizes (target − held)\n"
                  "  5. Place market orders via Alpaca\n\n"
                  "Best run at 9:31 AM ET on trading days."),
    "refresh_qc":("Refresh QC Rankings\n\n"
                  "Fetches QuantConnect public leaderboard\n"
                  "sorted by out-of-sample 3M return.\n"
                  "Updates strategy list and weights.\n"
                  "No orders are placed."),
    "close_all": ("Close All Positions\n\n"
                  "Sends SELL market orders for every\n"
                  "open position → 100% cash.\n\n"
                  "⚠  Cannot be undone during market hours."),
    "rebalance": ("Execute Rebalance\n\n"
                  "Computes drift vs QC targets then places\n"
                  "the minimum orders to realign.\n"
                  "Skips positions within 5% of target."),
    "refresh_data":("Refresh Account Data\n\n"
                  "Pulls fresh account balance, positions,\n"
                  "and order history from Alpaca right now\n"
                  "(does not wait for the 5s auto-scan)."),
    "start_bot": ("Start Portfolio Bot\n\n"
                  "Runs mock_portfolio.py in the background.\n"
                  "Manages 5% trailing stop-losses and\n"
                  "auto-rebalances at 9:31 AM ET daily."),
    "stop_bot":  ("Stop Portfolio Bot\n\n"
                  "Terminates automated management.\n"
                  "Open positions remain open."),
}

# ── Metric tooltips ───────────────────────────────────────────────────────────
MET_TIP = {
    "start":     "Starting capital — the initial $50,000 funded at account open.",
    "equity":    "Total account value\n= cash + market value of all open positions",
    "total_ret": "All-time return since the $50,000 starting capital.\n= (Equity − $50,000) / $50,000",
    "cash":      "Uninvested cash available to deploy.\nQC rebalance buys into this.",
    "bp":        "Buying power — max capital for new orders.\nMay exceed equity on margin accounts.",
    "pl":        "Unrealized P&L\nGain/loss on open positions not yet locked in by selling.",
    "positions": "Number of distinct symbols currently held.",
    "orders":    "Orders submitted but not yet filled.",
    "oos3m":     "Out-of-sample 3M return\nLive performance on data the strategy was NOT trained on.\nPrimary QC ranking criterion.",
    "oos1y":     "Out-of-sample 1Y return\nSustained live performance over a longer horizon.",
    "sharpe":    "Sharpe ratio = (return − risk-free) ÷ volatility\n>1.0 = good  |  >2.0 = excellent",
    "maxdd":     "Maximum Drawdown\nLargest peak-to-trough loss in the period.\nMeasures worst-case downside risk.",
    "weight":    "Portfolio weight after 2× boost for top-ranked strategy.\nTop strategy gets 2 raw votes vs 1 for others,\nthen normalised to sum 100%.",
    "aligned":   "Position within 5% of target (or within $50).\nNo rebalance needed for this strategy.",
    "drift":     "Position deviates >5% or >$50 from target.\nRebalance would buy/sell to correct this.",
    "winner":    "Highest-momentum symbol in this strategy's universe,\nselected by composite 20/60/120-day momentum score.",
    "target":    "Target dollar value\n= total equity × strategy weight × symbol allocation.",
    "mktval":    "Current market value of held position\n= quantity × current price.",
    "unrlpl":    "Unrealized P&L on this specific position.\nPositive = profit; Negative = loss.",
    "dd_ladder": ("Drawdown Ladder — tiered progressive liquidation\n\n"
                  "−10% → reduce to 75% invested (sell 25%)\n"
                  "−15% → reduce to 50% invested (sell another 33%)\n"
                  "−20% → reduce to 25% invested (sell another 50%)\n"
                  "−25% → 100% cash (sell everything)\n\n"
                  "Each tier fires ONCE per drawdown cycle.\n"
                  "Resets automatically when portfolio hits a new peak.\n"
                  "Daily rebalance re-enters after stabilisation → systematic buy-the-dip."),
    "signal":    ("Momentum Signal: vol-adjusted composite\n\n"
                  "Score = (0.4×ROC₂₀ + 0.4×ROC₆₀ + 0.2×ROC₁₂₀) ÷ σ₂₀\n\n"
                  "ROC₂₀  = 20-day rate of change (short momentum)\n"
                  "ROC₆₀  = 60-day rate of change (medium momentum)\n"
                  "ROC₁₂₀ = 120-day rate of change (trend confirmation)\n"
                  "σ₂₀    = 20-day realised volatility (normalises across assets)\n\n"
                  "Filter: price < SMA50 → score × 0.75\n"
                  "Filter: RSI outside 25–75 → score × 0.85\n\n"
                  "Winner = symbol with highest score in universe\n"
                  "Backtested Sharpe: 1.08–1.13 over 14 years"),
    "regime":    ("Market Regime Score (0–3 bullish signals)\n\n"
                  "+1  SPY > 20-day SMA\n"
                  "+1  SPY > 200-day SMA\n"
                  "+1  SPY 20-day return > 0\n"
                  "−2  VIX > 25\n"
                  "−1  VIX > 32\n\n"
                  "BULL ≥3  |  NEUTRAL 1–2  |  CAUTION 0  |  RISK-OFF <0\n\n"
                  "Regime affects strategy-fit scores but does NOT\n"
                  "automatically halt trading — the DD ladder handles that."),
    "exposure":  ("Current exposure level from DD ladder\n\n"
                  "1.00 = 100% invested (no ladder tier active)\n"
                  "0.75 = 75% invested (−10% tier fired)\n"
                  "0.50 = 50% invested (−15% tier fired)\n"
                  "0.25 = 25% invested (−20% tier fired)\n"
                  "0.00 = 100% cash (−25% tier fired)\n\n"
                  "Exposure resets to 1.00 when equity makes a new peak."),
    "peak":      ("Peak equity — high-water mark for drawdown calculation\n\n"
                  "Drawdown = (peak − current) ÷ peak\n\n"
                  "Used to determine if DD ladder tiers should fire.\n"
                  "Resets upward whenever equity exceeds the previous peak.\n"
                  "DD ladder tiers clear (un-fire) on each new peak."),
    "rebalance": ("Daily rebalance at 9:31 AM ET\n\n"
                  "1. Fetch QC leaderboard — top-5 by OOS 3M return\n"
                  "2. Apply 2× weight boost to #1 ranked strategy\n"
                  "3. Run momentum engine on each strategy's universe\n"
                  "4. Compute target $/symbol = equity × weight × alloc\n"
                  "5. Place minimum orders to close drift gaps\n\n"
                  "Skips if drift < $50 or < 1% of target.\n"
                  "Daily cadence chosen from 14-year backtest optimisation\n"
                  "(daily rebalance: Sharpe 1.08 vs 3-day: 0.94)."),
}

# ── Runtime cache (refreshed in background) ───────────────────────────────────
_cache: dict = {
    "account":         {},    # paper /v2/account
    "positions":       [],    # paper /v2/positions
    "orders":          [],    # paper /v2/orders (last 50)
    "port_hist":       {},    # paper /v2/account/portfolio/history
    "strategies":      [],    # QC top-5 configs
    "weights":         [],    # portfolio weights
    "allocations":     {},    # {strat_id: {sym: weight}}
    "prices":          {},    # {sym: price}
    "broker_accounts": [],    # [{id, label, status, account, positions, orders}]
    "regime":          {},    # SPY/VIX regime detection signals
    "deploy_status":   {"running": False, "msg": ""},
    "qc_proc":         None,
    "last_refresh":    None,
    "refreshing":      False,
    "bot_proc":        None,
}

# One entry per minute (HH:MM, unrealized_pl, equity) — survives page navigations
_pnl_history: deque = deque(maxlen=390)

# ── Alpaca helpers ────────────────────────────────────────────────────────────

def _alp(method: str, path: str, payload: dict | None = None) -> dict | list:
    """Hit the active broker account's trading endpoint."""
    url = f"{ACTIVE_BASE}{path}"
    try:
        r = (requests.get(url, headers=ACTIVE_HDR, timeout=10)    if method == "GET"    else
             requests.post(url, headers=ACTIVE_HDR, json=payload, timeout=10) if method == "POST" else
             requests.delete(url, headers=ACTIVE_HDR, timeout=10))
        if r.status_code in (200, 201, 207):
            return r.json() if r.content else {}
    except Exception as e:
        print(f"[ACTIVE] {method} {path}: {e}")
    return {} if path.endswith(("account", "history")) else []


def _paper(method: str, path: str) -> dict | list:
    """Hit the legacy paper account (read-only reference)."""
    url = f"{PAPER_BASE}{path}"
    try:
        r = requests.get(url, headers=PAPER_HDR, timeout=10)
        if r.status_code == 200:
            return r.json() if r.content else {}
    except Exception:
        pass
    return {} if path.endswith("account") else []


def _place_order(symbol: str, qty: float, side: str) -> dict:
    return _alp("POST", "/orders", {
        "symbol": symbol, "qty": str(round(qty, 4)),
        "side": side, "type": "market", "time_in_force": "day",
    })


def _broker(method: str, path: str, payload: dict | None = None) -> dict | list:
    url = f"{BROKER_BASE}{path}"
    try:
        r = (requests.get(url, headers=BROKER_HDR, timeout=10)    if method == "GET"  else
             requests.post(url, headers=BROKER_HDR, json=payload, timeout=10) if method == "POST" else
             requests.delete(url, headers=BROKER_HDR, timeout=10))
        if r.status_code in (200, 201, 207):
            return r.json() if r.content else {}
    except Exception as e:
        print(f"[BROKER] {method} {path}: {e}")
    return {} if path.endswith("account") else []


def _refresh_broker_accounts():
    """Fetch reference-only (closed) broker accounts + legacy paper account."""
    results = []

    # Closed broker accounts for reference
    for acct_id, label in OTHER_ACCOUNTS.items():
        entry = {"id": acct_id, "label": label, "account": {}, "positions": [], "orders": []}
        try:
            entry["account"]   = _broker("GET", f"/v1/trading/accounts/{acct_id}/account") or {}
            entry["positions"] = _broker("GET", f"/v1/trading/accounts/{acct_id}/positions") or []
            if entry["account"].get("unrealized_pl") is None:
                entry["account"]["unrealized_pl"] = sum(
                    _f(p.get("unrealized_pl")) for p in entry["positions"])
        except Exception as e:
            print(f"[BROKER] {acct_id[:8]}: {e}")
        results.append(entry)

    # Legacy paper account
    if SHOW_PAPER:
        try:
            p_acct = _paper("GET", "/v2/account")
            p_pos  = _paper("GET", "/v2/positions")
            if p_acct.get("unrealized_pl") is None:
                p_acct["unrealized_pl"] = sum(_f(p.get("unrealized_pl")) for p in p_pos)
            results.append({
                "id": "paper", "label": "Paper Account  (LEGACY)",
                "account": p_acct, "positions": p_pos, "orders": [],
            })
        except Exception as e:
            print(f"[PAPER] {e}")

    _cache["broker_accounts"] = results


# ── Background data refresh ───────────────────────────────────────────────────

def _refresh_all():
    if _cache["refreshing"]:
        return
    _cache["refreshing"] = True
    try:
        # Primary: fresh broker account
        _cache["account"]   = _alp("GET", "/account")
        _cache["positions"] = _alp("GET", "/positions") or []
        _cache["orders"]    = _alp("GET", "/orders?status=all&limit=50") or []

        # Broker API doesn't provide portfolio history — build from what we have
        _cache["port_hist"] = {}

        # Prices from positions
        for p in _cache["positions"]:
            px = p.get("current_price")
            if px is not None:
                try:
                    _cache["prices"][p["symbol"]] = float(px)
                except (TypeError, ValueError):
                    pass

        # Patch None unrealized_pl
        if _cache["account"].get("unrealized_pl") is None:
            _cache["account"]["unrealized_pl"] = sum(
                _f(p.get("unrealized_pl")) for p in _cache["positions"])

        _cache["last_refresh"] = datetime.now(ET)
    finally:
        _cache["refreshing"] = False


def _refresh_qc():
    """Load QC state from portfolio_state.json (written by qc_portfolio/main.py)."""
    state_path = QC_DIR / "portfolio_state.json"
    if state_path.exists():
        try:
            s = json.loads(state_path.read_text())
            strats = s.get("strategies", [])
            weights = s.get("weights", [])
            allocs  = s.get("allocations", {})
            if strats:
                _cache["strategies"]  = strats
                _cache["weights"]     = weights
                _cache["allocations"] = allocs
        except Exception:
            pass

    # Fall back to live QC API fetch if no cached data
    if not _cache["strategies"]:
        try:
            import importlib
            qc_api = importlib.import_module("qc_api")
            pe     = importlib.import_module("portfolio_engine")
            pe.load_state()
            changed = pe.refresh_qc()
            st = pe.get_state()
            _cache["strategies"]  = st.get("strategies", [])
            _cache["weights"]     = st.get("weights", [])
            _cache["allocations"] = st.get("allocations", {})
        except Exception as e:
            print(f"[QC] refresh error: {e}")


def _compute_regime():
    """Detect market regime: BULL / NEUTRAL / CAUTION / RISK-OFF via SPY + VIX."""
    try:
        import yfinance as yf
        spy = yf.download("SPY", period="1y", interval="1d",
                          progress=False, auto_adjust=True)
        vix = yf.download("^VIX", period="5d", interval="1d",
                          progress=False, auto_adjust=True)
        if spy.empty:
            return
        close = spy["Close"]
        if hasattr(close, "squeeze"):
            close = close.squeeze()
        close = close.dropna()
        if len(close) < 20:
            return
        sma20  = float(close.rolling(20).mean().iloc[-1])
        sma200 = float(close.rolling(min(200, len(close))).mean().iloc[-1])
        price  = float(close.iloc[-1])
        ret20  = float((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21]) \
                 if len(close) >= 21 else 0.0

        vix_level = 0.0
        if not vix.empty:
            vc = vix["Close"]
            if hasattr(vc, "squeeze"):
                vc = vc.squeeze()
            vc = vc.dropna()
            if not vc.empty:
                vix_level = float(vc.iloc[-1])

        score = 0
        if price > sma20:    score += 1
        if price > sma200:   score += 1
        if ret20  > 0:       score += 1
        if vix_level > 25:   score -= 2
        if vix_level > 32:   score -= 1

        if score >= 3:
            label, col = "BULL", GRN
        elif score >= 1:
            label, col = "NEUTRAL", YLW
        elif score == 0:
            label, col = "CAUTION", YLW
        else:
            label, col = "RISK-OFF", RED

        _cache["regime"] = {
            "label": label, "color": col,
            "spy_price": price, "sma20": sma20, "sma200": sma200,
            "vix": vix_level, "ret20": ret20, "score": score,
            "updated": datetime.now(ET).strftime("%H:%M:%S ET"),
        }
    except Exception as e:
        print(f"[REGIME] {e}")


def _background_loop():
    _refresh_qc()
    _refresh_all()
    _refresh_broker_accounts()
    _compute_regime()
    _cycle = 0
    while True:
        time.sleep(5)
        _refresh_all()
        _refresh_broker_accounts()
        _cycle += 1
        if _cycle % 12 == 0:   # QC check every 60s
            _refresh_qc()
            _acct = _cache.get("account", {})
            _pl_raw = _acct.get("unrealized_pl")
            _pnl_history.append({
                "t":  datetime.now(ET).strftime("%H:%M"),
                "pl": float(_pl_raw) if _pl_raw is not None else 0.0,
                "eq": float(_acct.get("equity") or 0),
            })
        if _cycle % 72 == 0:   # regime check every ~6 min
            _compute_regime()


# ── Chart builders ────────────────────────────────────────────────────────────

_LAY = dict(
    paper_bgcolor=BG2, plot_bgcolor=BG2,
    font=dict(color=FG, size=11, family="monospace"),
    margin=dict(l=48, r=16, t=40, b=36),
    xaxis=dict(gridcolor=BDR, linecolor=BDR, tickfont=dict(size=10)),
    yaxis=dict(gridcolor=BDR, linecolor=BDR, tickfont=dict(size=10)),
)


def _equity_chart() -> go.Figure:
    hist  = _cache.get("port_hist", {})
    ts    = hist.get("timestamp", [])
    eq    = hist.get("equity", [])
    pairs = [(datetime.fromtimestamp(t, tz=ET), float(e))
             for t, e in zip(ts, eq) if e is not None and e > 0]

    fig = go.Figure(layout=go.Layout(title="Alpaca Account Equity", **_LAY))
    if not pairs:
        fig.add_annotation(text="Fetching equity history…", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False,
                           font=dict(color=FG2, size=13))
        return fig

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    base = ys[0]
    last = ys[-1]
    pct  = (last - base) / base * 100

    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="lines", name="Equity",
        line=dict(color=BLU, width=2.5),
        fill="tozeroy", fillcolor="rgba(88,166,255,0.09)",
    ))
    fig.add_hline(y=base, line_dash="dash", line_color=FG2, opacity=0.5)
    fig.add_annotation(
        x=xs[-1], y=last,
        text=f"  ${last:,.0f}  ({pct:+.1f}%)",
        showarrow=False, xanchor="left",
        font=dict(color=GRN if last >= base else RED, size=12, family="monospace"),
    )
    fig.update_layout(hovermode="x unified", showlegend=False)
    return fig


def _pnl_chart() -> go.Figure:
    fig = go.Figure(layout=go.Layout(title="Intraday P&L", **_LAY))
    if not _pnl_history:
        fig.add_annotation(text="Collecting P&L data… (updates every 60s)",
                           x=0.5, y=0.5, xref="paper", yref="paper",
                           showarrow=False, font=dict(color=FG2, size=13))
        return fig

    xs  = [e["t"]  for e in _pnl_history]
    ys  = [e["pl"] for e in _pnl_history]
    last_pl = ys[-1]
    color   = GRN if last_pl >= 0 else RED
    fill_c  = "rgba(63,185,80,0.12)" if last_pl >= 0 else "rgba(248,81,73,0.12)"

    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="lines", name="P&L",
        line=dict(color=color, width=2),
        fill="tozeroy", fillcolor=fill_c,
        hovertemplate="%{x}  $%{y:+,.2f}<extra></extra>",
    ))
    fig.add_hline(y=0, line_dash="dash", line_color=FG2, opacity=0.4)
    fig.add_annotation(
        x=xs[-1], y=last_pl,
        text=f"  ${last_pl:+,.2f}",
        showarrow=False, xanchor="left",
        font=dict(color=color, size=12, family="monospace"),
    )
    fig.update_layout(
        hovermode="x unified", showlegend=False,
        xaxis_title="Time (ET)", yaxis_title="Unrealized P&L ($)",
        yaxis=dict(tickprefix="$", gridcolor=BDR, linecolor=BDR, tickfont=dict(size=10)),
    )
    return fig


def _alloc_donut(strategies: list, weights: list, positions: list) -> go.Figure:
    pos_map = {p["symbol"]: _f(p.get("market_value", 0)) for p in positions}
    account = _cache.get("account", {})
    cash    = _f(account.get("cash", 0))

    labels, values, colors = [], [], []
    for i, (cfg, w) in enumerate(zip(strategies, weights)):
        alloc = _cache["allocations"].get(cfg.get("id", ""), {})
        winner = next(iter(alloc), "—")
        val = pos_map.get(winner, 0)
        labels.append(f"#{i+1} {cfg.get('name','?')[:22]}")
        values.append(max(val, 0.01))
        colors.append(STRAT_COLORS[i % len(STRAT_COLORS)])

    if cash > 0:
        labels.append("CASH"); values.append(cash); colors.append(FG2)

    fig = go.Figure()
    fig.add_trace(go.Pie(
        labels=labels, values=values,
        hole=0.56,
        marker=dict(colors=colors, line=dict(color=BG, width=2)),
        textfont=dict(size=11, family="monospace"),
        hovertemplate="<b>%{label}</b><br>$%{value:,.0f} (%{percent})<extra></extra>",
    ))
    total = sum(v for v in values if v > 0.01)
    fig.update_layout(
        paper_bgcolor=BG2, plot_bgcolor=BG2,
        font=dict(color=FG, family="monospace"),
        margin=dict(l=10, r=10, t=40, b=10),
        title=dict(text="Allocation", font=dict(size=13, color=FG2)),
        showlegend=True,
        legend=dict(font=dict(size=10, color=FG2), bgcolor=BG2),
        annotations=[dict(
            text=f"${total:,.0f}", x=0.5, y=0.5,
            font=dict(size=14, color=FG, family="monospace"),
            showarrow=False,
        )],
    )
    return fig


def _sankey_chart() -> go.Figure:
    """Capital flow: Portfolio → Strategies → Holdings."""
    strategies = _cache.get("strategies", [])
    weights    = _cache.get("weights", [])
    allocs     = _cache.get("allocations", {})
    account    = _cache.get("account", {})
    positions  = _cache.get("positions", [])
    equity = float(account.get("equity", 0) or 0)
    cash   = float(account.get("cash", 0) or 0)

    fig = go.Figure(layout=go.Layout(
        paper_bgcolor=BG2, plot_bgcolor=BG2,
        font=dict(color=FG, family="monospace", size=10),
        margin=dict(l=10, r=10, t=36, b=10),
        title=dict(text="Capital Flow", font=dict(size=12, color=FG2)),
    ))
    if not strategies or equity <= 0:
        fig.add_annotation(text="Awaiting data…", x=0.5, y=0.5,
                           xref="paper", yref="paper", showarrow=False,
                           font=dict(color=FG2, size=12))
        return fig

    pos_map = {p["symbol"]: float(p.get("market_value", 0) or 0) for p in positions}

    node_labels, node_colors, node_tips = [], [], []
    node_labels.append(f"Portfolio\n${equity:,.0f}")
    node_colors.append(BLU)
    deployed = equity - cash
    node_tips.append(
        f"Total equity: ${equity:,.2f}\n"
        f"Deployed: ${deployed:,.0f} ({deployed/equity*100:.0f}%)\n"
        f"Cash: ${cash:,.0f} ({cash/equity*100:.0f}%)\n"
        f"Signal: vol-adj composite momentum (20/60/120d)\n"
        f"Rebalance: daily at 9:31 ET"
    )

    strat_start = 1
    for i, cfg in enumerate(strategies):
        w = weights[i] if i < len(weights) else 0
        sv = equity * w
        stats = cfg.get("statistics", {})
        def _gs(k, *a, _s=stats):
            return float(next((_s.get(x) for x in (k,)+a if _s.get(x) is not None), 0))
        r3m = _gs("oos 3m return", "oos_3m_return")
        node_labels.append(f"#{i+1} {cfg.get('name','?')[:16]}")
        node_colors.append(STRAT_COLORS[i % len(STRAT_COLORS)])
        info = STRAT_INFO.get(int(cfg.get("id",0)) if str(cfg.get("id","")).isdigit() else 0, {})
        node_tips.append(
            f"Strategy #{i+1}: {cfg.get('name','?')}\n"
            f"Portfolio weight: {w*100:.1f}%  (#{1} gets 2× raw votes)\n"
            f"Allocated: ${sv:,.0f}\n"
            f"QC OOS 3M return: {r3m*100:+.0f}%\n"
            f"Alloc type: {cfg.get('alloc','?').replace('_',' ')}\n"
            f"Edge: {info.get('edge','—')}\n"
            f"Risk: {info.get('risk','—')}"
        )

    sym_idx = {}
    for cfg in strategies:
        sid = cfg.get("id", "")
        for sym in allocs.get(str(sid), allocs.get(sid, {})):
            if sym not in sym_idx:
                mv = pos_map.get(sym, 0)
                sym_idx[sym] = len(node_labels)
                node_labels.append(sym)
                node_colors.append(FG)
                node_tips.append(
                    f"Symbol: {sym}\n"
                    f"Held value: ${mv:,.0f}\n"
                    f"Picked by: highest composite momentum score\n"
                    f"Signal: 0.4×ROC20 + 0.4×ROC60 + 0.2×ROC120, ÷ vol20\n"
                    f"Filter: price > SMA50 (×0.75 penalty if below)\n"
                    f"RSI 25–75 zone preferred (×0.85 outside)"
                )

    cash_idx = len(node_labels)
    node_labels.append(f"Cash\n${cash:,.0f}")
    node_colors.append(FG2)
    node_tips.append(
        f"Uninvested cash: ${cash:,.0f}\n"
        f"= {cash/equity*100:.1f}% of portfolio\n"
        f"DD ladder may hold cash after drawdown triggers\n"
        f"Re-deployed at next daily rebalance"
    )

    srcs, tgts, vals, ltips, lcols = [], [], [], [], []
    for i, (cfg, w) in enumerate(zip(strategies, weights)):
        sv = equity * w
        hx = STRAT_COLORS[i % len(STRAT_COLORS)]
        r, g, b = int(hx[1:3],16), int(hx[3:5],16), int(hx[5:7],16)
        srcs.append(0); tgts.append(strat_start+i)
        vals.append(max(sv,1)); ltips.append(f"${sv:,.0f} ({w*100:.1f}%)")
        lcols.append(f"rgba({r},{g},{b},0.25)")
    for i, (cfg, w) in enumerate(zip(strategies, weights)):
        sid = cfg.get("id", "")
        hx = STRAT_COLORS[i % len(STRAT_COLORS)]
        r, g, b = int(hx[1:3],16), int(hx[3:5],16), int(hx[5:7],16)
        for sym, sw in allocs.get(str(sid), allocs.get(sid, {})).items():
            sv2 = equity * w * sw
            srcs.append(strat_start+i); tgts.append(sym_idx.get(sym, cash_idx))
            vals.append(max(sv2,1)); ltips.append(f"${sv2:,.0f}")
            lcols.append(f"rgba({r},{g},{b},0.45)")
    if cash > 200:
        srcs.append(0); tgts.append(cash_idx)
        vals.append(max(cash,1)); ltips.append(f"${cash:,.0f} uninvested")
        lcols.append("rgba(139,148,158,0.2)")

    fig.add_trace(go.Sankey(
        arrangement="snap",
        node=dict(pad=12, thickness=16,
                  line=dict(color=BG, width=0.5),
                  label=node_labels, color=node_colors,
                  customdata=node_tips,
                  hovertemplate="%{customdata}<extra></extra>"),
        link=dict(source=srcs, target=tgts, value=vals,
                  label=ltips, color=lcols,
                  hovertemplate="Flow: %{label}<extra></extra>"),
    ))
    return fig


def _dd_ladder_gauge() -> go.Figure:
    """Horizontal gauge: current portfolio drawdown vs DD ladder thresholds."""
    state_path = QC_DIR / "portfolio_state.json"
    peak_eq = 0.0; exposure = 1.0; tiers_fired = []
    if state_path.exists():
        try:
            s = json.loads(state_path.read_text())
            peak_eq     = float(s.get("peak_equity", 0) or 0)
            exposure    = float(s.get("exposure_level", 1.0) or 1.0)
            tiers_fired = [float(x) for x in (s.get("tiers_fired") or [])]
        except Exception:
            pass
    account = _cache.get("account", {})
    equity  = float(account.get("equity", 0) or 0)
    if peak_eq <= 0: peak_eq = equity
    cur_dd = max(0.0, (peak_eq - equity) / peak_eq * 100) if peak_eq > 0 else 0.0

    THRESHOLDS = [10, 15, 20, 25]
    TARGETS    = [75, 50, 25,  0]
    MAX_X      = 30.0

    fig = go.Figure()
    zones = [
        (0,  10, f"{GRN}22"),
        (10, 15, f"{YLW}33"),
        (15, 20, "#ff9f4344"),
        (20, 25, f"{RED}55"),
        (25, MAX_X, f"{RED}88"),
    ]
    for x0, x1, fill in zones:
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=0, y1=1,
                      xref="x", yref="paper", fillcolor=fill, line_width=0, layer="below")

    for thr, tgt in zip(THRESHOLDS, TARGETS):
        fired = thr/100 in tiers_fired
        lc = RED if fired else YLW
        fig.add_shape(type="line", x0=thr, x1=thr, y0=0, y1=1,
                      xref="x", yref="paper",
                      line=dict(color=lc, width=1.5, dash="dash"))
        fig.add_annotation(x=thr, y=1.08, xref="x", yref="paper",
                           text=f"−{thr}%→{tgt}% inv",
                           showarrow=False, xanchor="center",
                           font=dict(size=8, color=lc, family="monospace"))

    dd_col = GRN if cur_dd < 10 else (YLW if cur_dd < 15 else (RED if cur_dd < 20 else "#ff0000"))
    fig.add_shape(type="line", x0=cur_dd, x1=cur_dd, y0=0, y1=1,
                  xref="x", yref="paper",
                  line=dict(color=dd_col, width=3))
    fig.add_annotation(x=cur_dd, y=0.5, xref="x", yref="paper",
                       text=f"  {cur_dd:.1f}%",
                       showarrow=False, xanchor="left",
                       font=dict(size=11, color=dd_col, family="monospace", weight="bold"))

    fig.add_trace(go.Scatter(
        x=[cur_dd], y=[0.5], mode="markers",
        marker=dict(size=14, color=dd_col, symbol="diamond",
                    line=dict(color=BG, width=2)),
        hovertemplate=(
            f"<b>Current drawdown: {cur_dd:.2f}%</b><br>"
            f"Peak equity: ${peak_eq:,.0f}<br>"
            f"Current equity: ${equity:,.0f}<br>"
            f"Exposure level: {exposure*100:.0f}% invested<br>"
            f"Tiers fired: {[f'{t*100:.0f}%' for t in tiers_fired] if tiers_fired else 'None'}<br>"
            f"<br>Ladder thresholds:<br>"
            f"  −10% → reduce to 75% invested<br>"
            f"  −15% → reduce to 50% invested<br>"
            f"  −20% → reduce to 25% invested<br>"
            f"  −25% → 100% cash<br>"
            f"Resets when portfolio hits new peak"
            "<extra></extra>"
        ),
        showlegend=False,
    ))

    fig.update_xaxis(range=[0, MAX_X], ticksuffix="%",
                     gridcolor=BDR, linecolor=BDR,
                     tickfont=dict(size=9, family="monospace"),
                     title_text="Drawdown from Peak")
    fig.update_yaxis(visible=False, range=[0, 1.3])
    next_tier = next((t for t in THRESHOLDS if cur_dd < t), None)
    headroom  = f"  {next_tier - cur_dd:.1f}pp to next trigger" if next_tier else "  FULLY TRIGGERED"
    fig.update_layout(
        paper_bgcolor=BG2, plot_bgcolor=BG2,
        font=dict(color=FG, family="monospace", size=10),
        margin=dict(l=10, r=16, t=44, b=44),
        title=dict(
            text=f"DD Ladder  ·  {exposure*100:.0f}% invested  ·  Peak ${peak_eq:,.0f}{headroom}",
            font=dict(size=10, color=FG2)),
        showlegend=False, hovermode="closest",
    )
    return fig


# ── Colour helpers ────────────────────────────────────────────────────────────

def _f(v):  return float(v) if v is not None else 0.0
def _pc(v): return GRN if _f(v) >= 0 else RED
def _fs(v): s = "+" if _f(v) > 0 else ""; return f"{s}{_f(v):,.2f}"
def _fd(v): s = "+" if _f(v) > 0 else ""; return f"${s}{_f(v):,.2f}"

def _tip(el, text: str):
    """Attach a styled tooltip; respects _SETTINGS['tooltips'] toggle."""
    if _SETTINGS.get("tooltips", True):
        with el:
            ui.tooltip(text).style(_TS)
    return el


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: QC PORTFOLIO  (default)
# ══════════════════════════════════════════════════════════════════════════════

@ui.refreshable
def page_qc():
    account   = _cache.get("account", {})
    positions = _cache.get("positions", [])
    orders    = _cache.get("orders", [])
    strategies= _cache.get("strategies", [])
    weights   = _cache.get("weights", [])
    allocs    = _cache.get("allocations", {})
    prices    = _cache.get("prices", {})
    last_ref  = _cache.get("last_refresh")

    equity  = _f(account.get("equity",         0))
    cash    = _f(account.get("cash",            0))
    _pl_raw = account.get("unrealized_pl")
    pl_day  = _f(_pl_raw) if _pl_raw is not None else sum(_f(p.get("unrealized_pl")) for p in positions)
    buying  = _f(account.get("buying_power",    0))
    pos_map = {p["symbol"]: p for p in positions}
    cost_basis = equity - pl_day
    pl_pct = (pl_day / cost_basis * 100) if cost_basis > 0 else 0.0
    pl_str = f"{_fd(pl_day)}  ({pl_pct:+.2f}%)"

    # ── Account header bar ────────────────────────────────────────────────────
    with ui.row().classes("w-full items-center gap-lg q-px-lg q-pt-md q-pb-sm flex-wrap").style(
            f"background:{BG2};border-bottom:1px solid {BDR}"):
        ui.label("QC → ALPACA").style(
            f"color:{BLU};font-size:1.3rem;font-weight:700;font-family:monospace")
        ui.separator().props("vertical")
        for label, val, col, tip_k in [
            ("Equity",       f"${equity:,.2f}",       FG,          "equity"),
            ("Cash",         f"${cash:,.2f}",          FG2,         "cash"),
            ("Unrealized P&L", pl_str,                 _pc(pl_day), "pl"),
            ("Buying Power", f"${buying:,.2f}",        FG2,         "bp"),
            ("Positions",    str(len(positions)),       BLU,         "positions"),
        ]:
            with ui.column().classes("items-start gap-none"):
                ui.label(label).style(f"color:{FG2};font-size:0.7rem;font-family:monospace")
                _tip(ui.label(val).style(
                    f"color:{col};font-size:1.15rem;font-weight:700;font-family:monospace"),
                    MET_TIP[tip_k])

        ui.space()
        ts = last_ref.strftime("Updated %H:%M:%S ET") if last_ref else "Loading…"
        ui.label(ts).style(f"color:{FG2};font-size:0.75rem;font-family:monospace")

    # ── Action bar ────────────────────────────────────────────────────────────
    with ui.row().classes("w-full items-center gap-md q-px-lg q-py-sm flex-wrap").style(
            f"background:{BG3};border-bottom:1px solid {BDR}"):

        def _do_refresh_qc():
            _log("QC REFRESH", "Fetching QuantConnect leaderboard")
            ui.notify("Refreshing QC rankings…", color="info", timeout=2000)
            def _run():
                try:
                    subprocess.run(
                        [sys.executable, str(QC_DIR / "main.py"), "--qc-refresh"],
                        cwd=str(ROOT), timeout=60)
                    _refresh_qc()
                    _log("QC REFRESH", "Done", f"{len(_cache.get('strategies',[]))} strategies")
                except Exception as e:
                    print(f"[QC REFRESH] {e}")
            threading.Thread(target=_run, daemon=True).start()

        def _do_rebalance():
            _log("REBALANCE", "Computing drift and placing orders")
            ui.notify("Executing rebalance…", color="warning", timeout=3000)
            def _run():
                try:
                    subprocess.run(
                        [sys.executable, str(QC_DIR / "main.py"), "--rebalance"],
                        cwd=str(ROOT), timeout=120)
                    _refresh_all()
                    _log("REBALANCE", "Done")
                except Exception as e:
                    print(f"[REBALANCE] {e}")
            threading.Thread(target=_run, daemon=True).start()

        def _do_close_all():
            _log("CLOSE ALL", "Liquidating all positions", "market orders sent")
            def _run():
                _alp("DELETE", "/positions")
                time.sleep(1)
                _refresh_all()
            threading.Thread(target=_run, daemon=True).start()

        def _do_data_refresh():
            _log("DATA REFRESH", "Pulling Alpaca account/positions/orders")
            ui.notify("Refreshing account data…", color="info", timeout=1500)
            threading.Thread(target=_refresh_all, daemon=True).start()

        _tip(ui.button("⟳  Refresh QC Rankings", on_click=_do_refresh_qc).props(
            "unelevated no-caps").style(
            f"background:{BG2};color:{BLU};border:1px solid {BDR};font-family:monospace;font-weight:700"),
            BTN_TIP["refresh_qc"])
        _tip(ui.button("⚖  Rebalance Now", on_click=_do_rebalance).props(
            "unelevated no-caps").style(
            f"background:{GRN};color:#000;font-family:monospace;font-weight:700"),
            MET_TIP["rebalance"])
        _tip(ui.button("↺  Refresh Data", on_click=_do_data_refresh).props(
            "unelevated no-caps").style(
            f"background:{BG2};color:{FG2};border:1px solid {BDR};font-family:monospace"),
            BTN_TIP["refresh_data"])
        _tip(ui.button("✕  Close All Positions", on_click=_do_close_all).props(
            "unelevated no-caps").style(
            f"background:{BG2};color:{RED};border:1px solid {BDR};font-family:monospace"),
            BTN_TIP["close_all"])
        ui.button("⬡  Trade Flow", on_click=lambda: ui.navigate.to(
            "http://100.71.5.12:5050/flow", new_tab=True)).props(
            "unelevated no-caps").style(
            f"background:{BG2};color:#00ff41;border:1px solid #00ff41;font-family:monospace")

    # ── Regime + DD Ladder status bar ────────────────────────────────────────
    regime = _cache.get("regime", {})
    r_label = regime.get("label", "—")
    r_col   = regime.get("color", FG2)

    state_path_qc = QC_DIR / "portfolio_state.json"
    _exp = 1.0; _peak2 = 0.0; _tiers2 = []
    if state_path_qc.exists():
        try:
            _qs = json.loads(state_path_qc.read_text())
            _exp    = float(_qs.get("exposure_level", 1.0) or 1.0)
            _peak2  = float(_qs.get("peak_equity", 0) or 0)
            _tiers2 = _qs.get("tiers_fired", [])
        except Exception:
            pass
    _cur_dd2 = max(0.0, (_peak2 - equity) / _peak2 * 100) if _peak2 > 0 else 0.0
    _dd_col2 = GRN if _cur_dd2 < 10 else (YLW if _cur_dd2 < 15 else RED)

    with ui.row().classes("w-full q-px-lg q-pt-sm q-pb-xs items-center gap-md flex-wrap").style(
            f"background:{BG3};border-bottom:1px solid {BDR}"):
        _tip(ui.badge(f"Regime: {r_label}").style(
            f"background:{r_col}22;color:{r_col};border:1px solid {r_col}55;"
            f"font-family:monospace;font-size:0.72rem;padding:3px 8px"),
            MET_TIP["regime"])
        ui.separator().props("vertical")
        _tip(ui.label(f"DD: {_cur_dd2:.1f}%").style(
            f"color:{_dd_col2};font-family:monospace;font-size:0.78rem;font-weight:700"),
            MET_TIP["dd_ladder"])
        _tip(ui.label(f"Exposure: {_exp*100:.0f}%").style(
            f"color:{_dd_col2};font-family:monospace;font-size:0.78rem;font-weight:700"),
            MET_TIP["exposure"])
        _tip(ui.label(f"Peak: ${_peak2:,.0f}").style(
            f"color:{FG2};font-family:monospace;font-size:0.75rem"),
            MET_TIP["peak"])
        if _tiers2:
            ui.label(f"Tiers fired: {', '.join([str(int(float(t)*100))+'%' for t in _tiers2])}").style(
                f"color:{RED};font-family:monospace;font-size:0.72rem")
        ui.space()
        _tip(ui.label("Signal: vol-adj momentum (20/60/120d ÷ σ₂₀)").style(
            f"color:{FG2};font-family:monospace;font-size:0.72rem"),
            MET_TIP["signal"])

    # ── Strategy cards ────────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md gap-md flex-wrap"):
        for i, (cfg, w) in enumerate(zip(strategies, weights)):
            sid     = cfg.get("id", "?")
            name    = cfg.get("name", f"Strategy {sid}")
            stats   = cfg.get("statistics", {})
            def _sf(key, *alt): return float(next((stats.get(k) for k in (key,)+alt if stats.get(k) is not None), 0))
            ret3m   = _sf("oos 3m return",  "oos_3m_return")
            ret1y   = _sf("oos 1y return",  "oos_1y_return")
            sharpe  = _sf("oos 3m sharpe",  "oos_3m_sharpe")
            dd5y    = _sf("5y drawdown",     "5y_drawdown")
            alloc   = allocs.get(str(sid), allocs.get(sid, {}))
            winner  = next(iter(alloc), "—")
            col     = STRAT_COLORS[i % len(STRAT_COLORS)]

            strat_dollars = equity * w if equity else 0
            pos = pos_map.get(winner, {})
            pos_val  = _f(pos.get("market_value", 0))
            pos_pl   = _f(pos.get("unrealized_pl", 0))
            pos_plpc = _f(pos.get("unrealized_plpc", 0)) * 100
            pos_qty  = _f(pos.get("qty", 0))
            pos_px   = _f(pos.get("current_price", prices.get(winner, 0)))
            target_val = strat_dollars * alloc.get(winner, 1.0) if alloc else 0
            drift   = pos_val - target_val
            aligned = abs(drift) < max(target_val * 0.05, 50)  # within 5% or $50

            with ui.card().classes("q-pa-md").style(
                    f"background:{BG2};border-left:4px solid {col};min-width:260px;flex:1"):
                # ── Strategy header ──────────────────────────────────────────
                with ui.row().classes("w-full justify-between items-start"):
                    with ui.column().classes("gap-none"):
                        ui.label(f"#{i+1}  {name}").style(
                            f"color:{col};font-weight:700;font-family:monospace;font-size:0.95rem")
                        ui.label(f"QC ID {sid}").style(
                            f"color:{FG2};font-size:0.72rem;font-family:monospace")
                    with ui.column().classes("items-end gap-none"):
                        _tip(ui.label(f"{w*100:.1f}%").style(
                            f"color:{col};font-size:1.4rem;font-weight:700;font-family:monospace"),
                            MET_TIP["weight"])
                        ui.label("portfolio weight").style(
                            f"color:{FG2};font-size:0.68rem;font-family:monospace")

                info = STRAT_INFO.get(int(sid) if str(sid).isdigit() else sid, {})
                if info:
                    with ui.expansion(f"ℹ  {info.get('short','Strategy details')}", icon="").classes("w-full").style(
                            f"color:{FG2};font-size:0.72rem;font-family:monospace"):
                        with ui.column().classes("gap-xs q-pa-xs"):
                            for k, v in [("Universe", info.get("universe","")),
                                          ("Allocation", info.get("alloc","")),
                                          ("Edge", info.get("edge","")),
                                          ("Risk", info.get("risk",""))]:
                                with ui.row().classes("w-full gap-sm"):
                                    ui.label(f"{k}:").style(
                                        f"color:{FG2};font-size:0.7rem;font-family:monospace;min-width:72px;flex-shrink:0")
                                    ui.label(v).style(f"color:{FG};font-size:0.7rem;font-family:monospace")

                ui.separator().style(f"background:{BDR};margin:6px 0")

                # ── Performance row ──────────────────────────────────────────
                with ui.row().classes("w-full gap-md"):
                    for lbl, val, vc, tip_key in [
                        ("OOS 3M",  f"{ret3m*100:+.0f}%",  _pc(ret3m), "oos3m"),
                        ("OOS 1Y",  f"{ret1y*100:+.0f}%",  _pc(ret1y), "oos1y"),
                        ("Sharpe",  f"{sharpe:.2f}",         BLU,        "sharpe"),
                        ("Max DD",  f"{dd5y*100:.0f}%",     RED if dd5y else FG2, "maxdd"),
                    ]:
                        with ui.column().classes("items-center gap-none"):
                            _tip(ui.label(lbl).style(
                                f"color:{FG2};font-size:0.68rem;font-family:monospace"),
                                MET_TIP[tip_key])
                            ui.label(val).style(
                                f"color:{vc};font-weight:700;font-family:monospace;font-size:0.85rem")

                ui.separator().style(f"background:{BDR};margin:6px 0")

                # ── Current holding ──────────────────────────────────────────
                ui.label("CURRENT HOLDING").style(
                    f"color:{FG2};font-size:0.68rem;font-family:monospace")

                with ui.row().classes("w-full justify-between items-center q-mt-xs"):
                    _winner_tip = (
                        f"Winner: {winner}\n\n"
                        f"Selected by: highest composite momentum score\n"
                        f"in {cfg.get('name','?')}'s universe\n\n"
                        f"Score = (0.4×ROC₂₀ + 0.4×ROC₆₀ + 0.2×ROC₁₂₀) ÷ σ₂₀\n"
                        f"+ SMA50 filter  +  RSI penalty (outside 25–75)\n\n"
                        f"Universe ({len(cfg.get('universe',[]))} symbols):\n"
                        f"{' · '.join(cfg.get('universe', []))}\n\n"
                        f"Rebalanced daily. Rotates whenever another\n"
                        f"symbol overtakes the current leader."
                    )
                    _tip(ui.label(winner).style(
                        f"color:{FG};font-size:1.3rem;font-weight:700;font-family:monospace;"
                        f"cursor:help;border-bottom:1px dotted {FG2}"),
                        _winner_tip)
                    if pos_px:
                        ui.label(f"${pos_px:,.2f}").style(
                            f"color:{FG};font-family:monospace;font-size:0.9rem")

                # P&L and value
                if pos_val > 0:
                    with ui.row().classes("w-full gap-md q-mt-xs"):
                        with ui.column().classes("gap-none"):
                            _tip(ui.label("Alpaca Value").style(
                                f"color:{FG2};font-size:0.68rem;font-family:monospace"),
                                MET_TIP["mktval"])
                            ui.label(f"${pos_val:,.2f}").style(
                                f"color:{FG};font-weight:700;font-family:monospace")
                        with ui.column().classes("gap-none"):
                            _tip(ui.label("Unrealized P&L").style(
                                f"color:{FG2};font-size:0.68rem;font-family:monospace"),
                                MET_TIP["unrlpl"])
                            ui.label(f"{_fd(pos_pl)}  ({pos_plpc:+.2f}%)").style(
                                f"color:{_pc(pos_pl)};font-weight:700;font-family:monospace")
                        with ui.column().classes("gap-none"):
                            _tip(ui.label("Target").style(
                                f"color:{FG2};font-size:0.68rem;font-family:monospace"),
                                MET_TIP["target"])
                            ui.label(f"${target_val:,.2f}").style(
                                f"color:{FG2};font-family:monospace")
                else:
                    with ui.row().classes("w-full gap-md q-mt-xs"):
                        with ui.column().classes("gap-none"):
                            ui.label("Target Allocation").style(
                                f"color:{FG2};font-size:0.68rem;font-family:monospace")
                            ui.label(f"${target_val:,.2f}").style(
                                f"color:{YLW};font-weight:700;font-family:monospace")
                        with ui.column().classes("gap-none"):
                            ui.label("Alpaca Position").style(
                                f"color:{FG2};font-size:0.68rem;font-family:monospace")
                            ui.label("Not held").style(
                                f"color:{RED};font-family:monospace")

                ui.separator().style(f"background:{BDR};margin:6px 0")

                # ── Status badge + quick order ────────────────────────────────
                with ui.row().classes("w-full justify-between items-center"):
                    if aligned:
                        _tip(ui.badge("✓ ALIGNED", color="positive").style(
                            "font-family:monospace;font-size:0.75rem"),
                            MET_TIP["aligned"])
                    else:
                        drift_s = f"${abs(drift):,.0f} {'over' if drift > 0 else 'under'}"
                        _tip(ui.badge(f"⚠ DRIFT {drift_s}", color="warning").style(
                            "font-family:monospace;font-size:0.75rem"),
                            MET_TIP["drift"])

                    # Universe chip
                    univ = cfg.get("universe", [])
                    ui.label(f"{cfg.get('alloc','?').replace('_',' ')}  •  {len(univ)} symbols").style(
                        f"color:{FG2};font-size:0.68rem;font-family:monospace")

    # ── Charts: Sankey (full width) ──────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md"):
        with ui.card().classes("w-full q-pa-sm").style(f"background:{BG2}"):
            ui.plotly(_sankey_chart()).style("height:340px;width:100%")

    # ── Charts: equity curve + DD gauge ──────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-sm gap-md flex-wrap"):
        with ui.card().classes("q-pa-sm").style(f"background:{BG2};flex:1;min-width:280px"):
            ui.plotly(_equity_chart()).style("height:240px")
        with ui.card().classes("q-pa-sm").style(f"background:{BG2};flex:1;min-width:280px"):
            ui.plotly(_dd_ladder_gauge()).style("height:240px")

    # ── Alpaca positions table ────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            ui.label("ALPACA POSITIONS").style(
                f"color:{FG2};font-size:0.8rem;font-weight:700;font-family:monospace;margin-bottom:8px")

            if positions:
                pcols = [
                    {"name": "symbol",    "label": "Symbol",    "field": "symbol",    "align": "left",   "sortable": True},
                    {"name": "qty",       "label": "Qty",       "field": "qty",       "align": "right"},
                    {"name": "avg_entry", "label": "Avg Entry", "field": "avg_entry", "align": "right"},
                    {"name": "price",     "label": "Price",     "field": "price",     "align": "right"},
                    {"name": "value",     "label": "Mkt Value", "field": "value",     "align": "right",  "sortable": True},
                    {"name": "pl",        "label": "Unrl P&L",  "field": "pl",        "align": "right",  "sortable": True},
                    {"name": "pl_pct",    "label": "P&L %",     "field": "pl_pct",    "align": "right",  "sortable": True},
                    {"name": "strategy",  "label": "Strategy",  "field": "strategy",  "align": "left"},
                ]

                # Map position to strategy
                winner_map = {}
                for i, (cfg, w) in enumerate(zip(strategies, weights)):
                    alloc = allocs.get(str(cfg.get("id","")), allocs.get(cfg.get("id",""), {}))
                    for sym in alloc:
                        winner_map[sym] = f"#{i+1} {cfg.get('name','?')[:20]}"

                prows = []
                for p in sorted(positions, key=lambda x: -abs(float(x.get("market_value", 0)))):
                    sym   = p["symbol"]
                    qty   = _f(p.get("qty", 0))
                    entry = _f(p.get("avg_entry_price", 0))
                    price = _f(p.get("current_price", prices.get(sym, 0)))
                    val   = _f(p.get("market_value", 0))
                    pl    = _f(p.get("unrealized_pl", 0))
                    pl_p  = _f(p.get("unrealized_plpc", 0)) * 100
                    prows.append({
                        "symbol":    sym,
                        "qty":       f"{qty:.4f}",
                        "avg_entry": f"${entry:.2f}",
                        "price":     f"${price:.2f}",
                        "value":     f"${val:,.2f}",
                        "pl":        _fd(pl),
                        "pl_pct":    f"{pl_p:+.2f}%",
                        "strategy":  winner_map.get(sym, "—"),
                    })
                t = ui.table(columns=pcols, rows=prows, row_key="symbol").classes("w-full").props(
                    "dense flat bordered")
                t.add_slot("header-cell-qty",      '<q-th :props="props"><span title="Number of shares/units held (fractional shares supported)">{{ props.col.label }}</span></q-th>')
                t.add_slot("header-cell-avg_entry", '<q-th :props="props"><span title="Average price paid per share across all fills">{{ props.col.label }}</span></q-th>')
                t.add_slot("header-cell-price",    '<q-th :props="props"><span title="Current live market price">{{ props.col.label }}</span></q-th>')
                t.add_slot("header-cell-value",    '<q-th :props="props"><span title="Market value = qty × current price">{{ props.col.label }}</span></q-th>')
                t.add_slot("header-cell-pl",       '<q-th :props="props"><span title="Unrealized P&L = (current price − avg entry) × qty\nNot locked in until sold">{{ props.col.label }}</span></q-th>')
                t.add_slot("header-cell-pl_pct",   '<q-th :props="props"><span title="Unrealized return % = P&L ÷ cost basis">{{ props.col.label }}</span></q-th>')
                t.add_slot("header-cell-strategy", '<q-th :props="props"><span title="Which QC strategy selected this symbol via momentum ranking">{{ props.col.label }}</span></q-th>')
                t.add_slot("body-cell-pl", """
                    <q-td :props="props" :style="'color:' + (props.value.startsWith('$-') || props.value.startsWith('$+') && parseFloat(props.value.replace(/[$,+]/g,'')) < 0 ? '#f85149' : '#3fb950')">
                        {{ props.value }}
                    </q-td>""")
            else:
                ui.label("No open positions in Alpaca account").style(
                    f"color:{FG2};font-family:monospace;padding:12px")

    # ── Recent orders ─────────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-lg"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            ui.label("RECENT ORDERS").style(
                f"color:{FG2};font-size:0.8rem;font-weight:700;font-family:monospace;margin-bottom:8px")

            filled = [o for o in orders if o.get("status") in ("filled", "partially_filled")][:20]
            if filled:
                ocols = [
                    {"name": "submitted",  "label": "Time",    "field": "submitted",  "align": "left"},
                    {"name": "symbol",     "label": "Symbol",  "field": "symbol",     "align": "left", "sortable": True},
                    {"name": "side",       "label": "Side",    "field": "side"},
                    {"name": "qty",        "label": "Qty",     "field": "qty",        "align": "right"},
                    {"name": "filled_px",  "label": "Fill $",  "field": "filled_px",  "align": "right"},
                    {"name": "status",     "label": "Status",  "field": "status"},
                ]
                orows = []
                for o in filled:
                    submitted = o.get("submitted_at", "")[:16].replace("T", " ")
                    orows.append({
                        "submitted": submitted,
                        "symbol":    o.get("symbol", "—"),
                        "side":      o.get("side", "—").upper(),
                        "qty":       f"{float(o.get('qty') or 0):.4f}",
                        "filled_px": f"${float(o.get('filled_avg_price') or 0):.2f}",
                        "status":    o.get("status", "—"),
                    })
                ui.table(columns=ocols, rows=orows, row_key="submitted").classes("w-full").props(
                    "dense flat bordered")
            else:
                ui.label("No filled orders yet").style(
                    f"color:{FG2};font-family:monospace;padding:12px")



# ══════════════════════════════════════════════════════════════════════════════
# PAGE: A/B TEST — PRODUCTION vs IMPROVED STOPS
# ══════════════════════════════════════════════════════════════════════════════

@ui.refreshable
def page_compare():
    """Side-by-side: live Alpaca QC bot vs virtual ShadowEngine (adaptive+lev stops)."""
    account   = _cache.get("account", {})
    positions = _cache.get("positions", [])

    prod_equity = _f(account.get("equity", 0))
    prod_cash   = _f(account.get("cash", 0))
    prod_pl_raw = account.get("unrealized_pl")
    prod_pl     = (_f(prod_pl_raw) if prod_pl_raw is not None
                   else sum(_f(p.get("unrealized_pl")) for p in positions))

    # Pull prod peak/dd from qc_portfolio state for fair drawdown comparison
    prod_peak = 0.0
    state_path_qc = QC_DIR / "portfolio_state.json"
    if state_path_qc.exists():
        try:
            prod_peak = float(json.loads(state_path_qc.read_text()).get("peak_equity") or 0)
        except Exception:
            pass
    prod_dd = (prod_peak - prod_equity) / prod_peak * 100 if prod_peak > 0 else 0.0

    # Shadow state
    test_state = {}
    test_eq = test_cash = test_peak = test_dd = 0.0
    test_positions = {}
    test_ctx = {}
    test_exit_reasons = {}
    test_last_tick = None
    if _shadow is not None:
        try:
            sp = ROOT / "qc_portfolio_test" / "state.json"
            if sp.exists():
                test_state = json.loads(sp.read_text())
                test_cash  = _f(test_state.get("cash"))
                test_peak  = _f(test_state.get("peak_equity"))
                test_positions = test_state.get("positions", {}) or {}
                test_exit_reasons = test_state.get("exit_reasons", {}) or {}
                test_last_tick = test_state.get("last_tick")
                # Recompute equity at current prices via cache
                px = _cache.get("prices", {}) or {}
                held = sum(p["qty"] * px.get(s, p["cost"]) for s, p in test_positions.items())
                test_eq = test_cash + held
                test_dd = ((test_peak - test_eq) / test_peak * 100) if test_peak > 0 else 0.0
                # last context snapshot from engine output (may be stale)
                test_ctx = test_state.get("context", {}) or {}
        except Exception as e:
            print(f"[COMPARE] shadow read err: {e}")

    # ── Header ───────────────────────────────────────────────────────────────
    with ui.row().classes("w-full items-center gap-lg q-px-lg q-pt-md q-pb-sm flex-wrap").style(
            f"background:{BG2};border-bottom:1px solid {BDR}"):
        ui.label("A/B TEST  ·  PROD vs ADAPTIVE+LEV").style(
            f"color:{BLU};font-size:1.3rem;font-weight:700;font-family:monospace")
        ui.separator().props("vertical")
        ui.label(f"VIX {test_ctx.get('vix','—'):.1f}" if isinstance(test_ctx.get('vix'), (int, float))
                 else "VIX —").style(f"color:{FG2};font-family:monospace;font-size:0.85rem")
        rv_burst = test_ctx.get("rv5_over_rv60")
        if isinstance(rv_burst, (int, float)):
            ui.label(f"vol burst {rv_burst:.2f}").style(
                f"color:{YLW if rv_burst>=1.5 else FG2};font-family:monospace;font-size:0.85rem")
        if test_ctx.get("in_stress"):
            ui.badge("STRESS").style(
                f"background:{RED}22;color:{RED};border:1px solid {RED}55;"
                f"font-family:monospace;font-size:0.7rem")
        ui.space()
        if test_last_tick:
            ui.label(f"shadow tick {test_last_tick[11:19]} UTC").style(
                f"color:{FG2};font-family:monospace;font-size:0.75rem")

    # ── Action bar ───────────────────────────────────────────────────────────
    with ui.row().classes("w-full items-center gap-md q-px-lg q-py-sm flex-wrap").style(
            f"background:{BG3};border-bottom:1px solid {BDR}"):
        def _do_test_tick():
            if _shadow is None:
                ui.notify("Shadow engine unavailable", color="negative"); return
            ui.notify("Ticking shadow engine…", color="info", timeout=1500)
            def _run():
                try:
                    res = _shadow.tick()
                    print(f"[SHADOW manual tick] {res}")
                    page_compare.refresh()
                except Exception as e:
                    print(f"[SHADOW tick err] {e}")
            threading.Thread(target=_run, daemon=True).start()

        def _do_test_reset():
            if _shadow is None:
                return
            sp = ROOT / "qc_portfolio_test" / "state.json"
            tp = ROOT / "qc_portfolio_test" / "transactions.csv"
            for p in (sp, tp):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            # Re-init engine
            globals()["_shadow"] = ShadowEngine()
            ui.notify("Shadow engine reset to $50k", color="warning", timeout=2000)
            page_compare.refresh()

        ui.button("▶  Tick Shadow Now", on_click=_do_test_tick).props("unelevated no-caps").style(
            f"background:{GRN};color:#000;font-family:monospace;font-weight:700")
        ui.button("↻  Reset Shadow", on_click=_do_test_reset).props("unelevated no-caps").style(
            f"background:{BG2};color:{YLW};border:1px solid {BDR};font-family:monospace")

    # ── KPI cards ────────────────────────────────────────────────────────────
    def _kpi_col(title, eq, cash, pl, dd, peak, pos_count, color):
        with ui.card().classes("w-full q-pa-md").style(
                f"background:{BG2};border:1px solid {color}33"):
            ui.label(title).style(
                f"color:{color};font-family:monospace;font-weight:700;font-size:0.9rem;letter-spacing:1px")
            with ui.row().classes("w-full gap-lg flex-wrap q-mt-sm"):
                for label, val, col in [
                    ("Equity",      f"${eq:,.2f}",        FG),
                    ("Cash",        f"${cash:,.2f}",      FG2),
                    ("Unreal P&L",  f"{pl:+,.2f}",        _pc(pl)),
                    ("Peak",        f"${peak:,.0f}",      FG2),
                    ("DD",          f"{dd:.2f}%",         GRN if dd<10 else (YLW if dd<15 else RED)),
                    ("Positions",   str(pos_count),       BLU),
                ]:
                    with ui.column().classes("items-start gap-none"):
                        ui.label(label).style(f"color:{FG2};font-size:0.66rem;font-family:monospace")
                        ui.label(val).style(
                            f"color:{col};font-size:1.05rem;font-weight:700;font-family:monospace")

    with ui.row().classes("w-full gap-md q-pa-md flex-wrap"):
        with ui.column().classes("col-grow"):
            _kpi_col("◉ PROD — Alpaca live  (current QC bot)",
                     prod_equity, prod_cash, prod_pl, prod_dd, prod_peak,
                     len(positions), BLU)
        with ui.column().classes("col-grow"):
            test_pl = test_eq - INITIAL_TEST_CAPITAL
            _kpi_col("◎ TEST — Shadow  (adaptive+lev stops, virtual)",
                     test_eq, test_cash, test_pl, test_dd, test_peak,
                     len(test_positions), GRN)

    # ── Positions tables ─────────────────────────────────────────────────────
    with ui.row().classes("w-full gap-md q-px-md q-pb-md").style("align-items:flex-start"):
        # Prod positions
        with ui.column().classes("col-grow"):
            ui.label("PROD POSITIONS").style(
                f"color:{BLU};font-family:monospace;font-weight:700;font-size:0.82rem;margin-bottom:6px")
            if positions:
                rows = [{
                    "symbol": p["symbol"],
                    "qty":    f"{_f(p.get('qty')):.3f}",
                    "mv":     f"${_f(p.get('market_value')):,.2f}",
                    "pl":     f"{_f(p.get('unrealized_pl')):+,.2f}",
                } for p in positions]
                ui.table(columns=[
                    {"name":"symbol","label":"SYM","field":"symbol","align":"left"},
                    {"name":"qty","label":"QTY","field":"qty","align":"right"},
                    {"name":"mv","label":"MV","field":"mv","align":"right"},
                    {"name":"pl","label":"P&L","field":"pl","align":"right"},
                ], rows=rows, row_key="symbol").classes("w-full").props("dense flat bordered")
            else:
                ui.label("No positions").style(f"color:{FG2};font-family:monospace;padding:8px")

        # Test positions
        with ui.column().classes("col-grow"):
            ui.label("TEST POSITIONS").style(
                f"color:{GRN};font-family:monospace;font-weight:700;font-size:0.82rem;margin-bottom:6px")
            if test_positions:
                px = _cache.get("prices", {}) or {}
                rows = [{
                    "symbol": s,
                    "qty":    f"{p['qty']:.3f}",
                    "cost":   f"${p['cost']:.2f}",
                    "mv":     f"${p['qty'] * px.get(s, p['cost']):,.2f}",
                    "pl":     f"{(px.get(s, p['cost']) - p['cost']) * p['qty']:+,.2f}",
                } for s, p in test_positions.items()]
                ui.table(columns=[
                    {"name":"symbol","label":"SYM","field":"symbol","align":"left"},
                    {"name":"qty","label":"QTY","field":"qty","align":"right"},
                    {"name":"cost","label":"COST","field":"cost","align":"right"},
                    {"name":"mv","label":"MV","field":"mv","align":"right"},
                    {"name":"pl","label":"P&L","field":"pl","align":"right"},
                ], rows=rows, row_key="symbol").classes("w-full").props("dense flat bordered")
            else:
                ui.label("No shadow positions").style(f"color:{FG2};font-family:monospace;padding:8px")

    # ── Exit-reason breakdown for the shadow engine ──────────────────────────
    if test_exit_reasons:
        with ui.row().classes("w-full q-px-md q-pb-md gap-md flex-wrap"):
            ui.label("SHADOW EXITS").style(
                f"color:{GRN};font-family:monospace;font-weight:700;font-size:0.78rem")
            for reason, count in sorted(test_exit_reasons.items(), key=lambda x: -x[1]):
                ui.badge(f"{reason}: {count}").style(
                    f"background:{BG3};color:{FG2};border:1px solid {BDR};"
                    f"font-family:monospace;font-size:0.7rem")


INITIAL_TEST_CAPITAL = 50_000.0   # matches qc_portfolio_test.engine.INITIAL_CAPITAL


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: ALL ACCOUNTS
# ══════════════════════════════════════════════════════════════════════════════

@ui.refreshable
def page_accounts():
    other_accts  = _cache.get("broker_accounts", [])   # closed + paper (reference only)
    active_acct  = _cache.get("account", {})            # fresh $50k broker account
    active_pos   = _cache.get("positions", [])
    active_orders = _cache.get("orders", [])
    last_ref     = _cache.get("last_refresh")
    # alias for backward compat in this function
    paper_acct = active_acct
    paper_pos  = active_pos

    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-sm items-center gap-md").style(
            f"background:{BG2};border-bottom:1px solid {BDR}"):
        ui.label("ALL ACCOUNTS").style(
            f"color:{BLU};font-size:1.3rem;font-weight:700;font-family:monospace")
        ui.space()
        ts = last_ref.strftime("Updated %H:%M:%S ET") if last_ref else "Loading…"
        ui.label(ts).style(f"color:{FG2};font-size:0.75rem;font-family:monospace")

    # ── Active account hero card (full width) ────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md"):
        act_eq   = _f(active_acct.get("equity", 0))
        act_cash = _f(active_acct.get("cash", 0))
        act_bp   = _f(active_acct.get("buying_power", 0))
        act_pl   = _f(active_acct.get("unrealized_pl", 0))
        act_cost = act_eq - act_pl
        act_pl_pct = (act_pl / act_cost * 100) if act_cost > 0 else 0.0
        act_pl_str = f"{_fd(act_pl)}  ({act_pl_pct:+.2f}%)"
        act_start  = 50_000.0
        act_alltime_pnl = act_eq - act_start
        act_alltime_pct = (act_alltime_pnl / act_start * 100) if act_start > 0 else 0.0
        act_ret_str = f"{act_alltime_pct:+.2f}%"

        with ui.card().classes("w-full q-pa-lg").style(
                f"background:{BG2};border:2px solid {GRN}"):
            with ui.row().classes("w-full justify-between items-start flex-wrap gap-md"):
                with ui.column().classes("gap-none"):
                    ui.label("▶  ACTIVE TRADING ACCOUNT").style(
                        f"color:{GRN};font-size:1.1rem;font-weight:700;font-family:monospace")
                    ui.label(f"broker-api.sandbox.alpaca.markets  •  {ACTIVE_ACCT[:18]}…").style(
                        f"color:{FG2};font-size:0.72rem;font-family:monospace")
                ui.badge("LIVE  $50k  QC STRATEGIES", color="positive").style(
                    "font-family:monospace;font-size:0.8rem")

            ui.separator().style(f"background:{GRN}44;margin:12px 0")

            with ui.row().classes("w-full gap-xl flex-wrap"):
                for lbl, val, col, tip_key in [
                    ("Starting Capital", f"${act_start:,.2f}",  FG2,                     "start"),
                    ("Equity",           f"${act_eq:,.2f}",     FG,                      "equity"),
                    ("Total Return",     act_ret_str,            _pc(act_alltime_pnl),    "total_ret"),
                    ("Cash",             f"${act_cash:,.2f}",   FG2,                     "cash"),
                    ("Buying Power",     f"${act_bp:,.2f}",     FG2,                     "bp"),
                    ("Unrealized P&L",   act_pl_str,             _pc(act_pl),             "pl"),
                    ("Positions",        str(len(active_pos)),   BLU,                     "positions"),
                    ("Open Orders",      str(len([o for o in active_orders if o.get("status") in ("new","accepted","pending_new","partially_filled")])), YLW, "orders"),
                ]:
                    with ui.column().classes("gap-none items-center q-pa-sm").style(
                            f"background:{BG3};border-radius:8px;min-width:100px"):
                        ui.label(lbl).style(f"color:{FG2};font-size:0.65rem;font-family:monospace;text-align:center")
                        _tip(ui.label(val).style(f"color:{col};font-weight:700;font-family:monospace;font-size:1.1rem"),
                             MET_TIP[tip_key])

            if active_pos:
                ui.separator().style(f"background:{BDR};margin:12px 0")
                ui.label("POSITIONS").style(f"color:{FG2};font-size:0.75rem;font-weight:700;font-family:monospace;margin-bottom:6px")
                pcols = [
                    {"name":"sym",   "label":"Symbol",      "field":"sym",   "align":"left",  "sortable":True},
                    {"name":"qty",   "label":"Qty",          "field":"qty",   "align":"right"},
                    {"name":"px",    "label":"Price",        "field":"px",    "align":"right"},
                    {"name":"val",   "label":"Market Value", "field":"val",   "align":"right", "sortable":True},
                    {"name":"pl",    "label":"Unrl P&L",     "field":"pl",    "align":"right", "sortable":True},
                    {"name":"plpct", "label":"P&L %",        "field":"plpct", "align":"right"},
                ]
                prows = [{
                    "sym":   p["symbol"],
                    "qty":   f"{_f(p.get('qty')):.4f}",
                    "px":    f"${_f(p.get('current_price')):,.2f}",
                    "val":   f"${_f(p.get('market_value')):,.2f}",
                    "pl":    _fd(_f(p.get("unrealized_pl"))),
                    "plpct": f"{_f(p.get('unrealized_plpc'))*100:+.2f}%",
                } for p in sorted(active_pos, key=lambda x: -abs(_f(x.get("market_value",0))))]
                ui.table(columns=pcols, rows=prows, row_key="sym").classes("w-full").props("dense flat bordered")
            else:
                with ui.row().classes("w-full items-center gap-sm q-mt-sm").style(
                        f"color:{BLU};font-family:monospace;font-size:0.9rem"):
                    ui.label("No positions yet — QC rebalance will deploy capital")

            ui.separator().style(f"background:{BDR};margin:12px 0")
            with ui.row().classes("w-full gap-sm"):
                def _do_rebalance_active():
                    ui.notify("Deploying QC strategy allocations…", color="info", timeout=3000)
                    def _r():
                        _log("REBALANCE START", "Fetching QC rankings + computing allocations")
                        ui.notify("Step 1/4 — Refreshing QC rankings…", type="info", timeout=4000)
                        subprocess.run([sys.executable, str(QC_DIR/"main.py"), "--qc-refresh"],
                                       cwd=str(ROOT), timeout=60)
                        _log("QC FETCH", "QuantConnect leaderboard updated")
                        ui.notify("Step 2/4 — Running momentum engine…", type="info", timeout=4000)
                        time.sleep(0.3)
                        ui.notify("Step 3/4 — Computing order sizes…", type="info", timeout=4000)
                        result = subprocess.run([sys.executable, str(QC_DIR/"main.py"), "--rebalance"],
                                                cwd=str(ROOT), timeout=120, capture_output=True, text=True)
                        _refresh_all()
                        n_pos = len(_cache.get("positions", []))
                        _log("REBALANCE DONE", f"{n_pos} positions active", "orders placed")
                    threading.Thread(target=_r, daemon=True).start()

                def _do_refresh_qc_accts():
                    ui.notify("Refreshing QC rankings…", color="info", timeout=2000)
                    def _r():
                        subprocess.run([sys.executable, str(QC_DIR/"main.py"), "--qc-refresh"],
                                       cwd=str(ROOT), timeout=60)
                        _refresh_qc()
                    threading.Thread(target=_r, daemon=True).start()

                def _do_close_all_accts():
                    ui.notify("Closing all positions…", color="warning", timeout=2000)
                    def _r():
                        _alp("DELETE", "/positions")
                        time.sleep(1)
                        _refresh_all()
                    threading.Thread(target=_r, daemon=True).start()

                _tip(ui.button("⚖  Deploy QC Strategies", on_click=_do_rebalance_active).props(
                    "unelevated no-caps").style(
                    f"background:{GRN};color:#000;font-family:monospace;font-weight:700"),
                    BTN_TIP["deploy"])
                _tip(ui.button("⟳  Refresh QC Rankings", on_click=_do_refresh_qc_accts).props(
                    "unelevated no-caps").style(
                    f"background:{BG3};color:{BLU};border:1px solid {BDR};font-family:monospace"),
                    BTN_TIP["refresh_qc"])
                _tip(ui.button("✕  Close All Positions", on_click=_do_close_all_accts).props(
                    "unelevated no-caps").style(
                    f"background:{BG3};color:{RED};border:1px solid {BDR};font-family:monospace"),
                    BTN_TIP["close_all"])

    # ── Reference accounts (collapsed) ───────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md gap-md flex-wrap"):

        # ── Reference accounts (closed / legacy) ─────────────────────────────
        for entry in other_accts:
            aid    = entry["id"]
            label  = entry["label"]
            acct   = entry.get("account", {})
            pos    = entry.get("positions", [])
            orders = entry.get("orders", [])
            eq     = _f(acct.get("equity", 0))
            cash   = _f(acct.get("cash", 0))
            pl     = _f(acct.get("unrealized_pl", 0))
            bp     = _f(acct.get("buying_power", 0))

            is_active = "ACTIVE" in label
            is_closed = "CLOSED" in label
            border_col = GRN if is_active else (RED if is_closed else YLW)
            status_txt = "ACTIVE" if is_active else ("CLOSED" if is_closed else "PENDING")
            badge_col  = "positive" if is_active else ("negative" if is_closed else "warning")

            with ui.card().classes("q-pa-lg").style(
                    f"background:{BG2};border-left:4px solid {border_col};min-width:300px;flex:1"):
                with ui.row().classes("w-full justify-between items-start"):
                    with ui.column().classes("gap-none"):
                        ui.label(label.split("  (")[0]).style(
                            f"color:{border_col};font-size:1rem;font-weight:700;font-family:monospace")
                        ui.label(f"ID: {aid[:18]}…").style(
                            f"color:{FG2};font-size:0.68rem;font-family:monospace")
                    ui.badge(status_txt, color=badge_col).style("font-family:monospace")

                ui.separator().style(f"background:{BDR};margin:10px 0")

                if eq == 0 and cash == 0 and is_active:
                    with ui.row().classes("w-full items-center gap-sm q-pa-sm").style(
                            f"background:{YLW}22;border-radius:6px"):
                        ui.label("⚠").style(f"color:{YLW};font-size:1rem")
                        with ui.column().classes("gap-none"):
                            ui.label("UNFUNDED — Ready for $50k").style(
                                f"color:{YLW};font-weight:700;font-family:monospace;font-size:0.85rem")
                            ui.label("Fund via broker-app.alpaca.markets → Accounts → Fund").style(
                                f"color:{FG2};font-size:0.72rem;font-family:monospace")
                    ui.separator().style(f"background:{BDR};margin:10px 0")
                else:
                    with ui.row().classes("w-full gap-lg"):
                        for lbl, val, col in [
                            ("Equity",       f"${eq:,.2f}",   FG),
                            ("Cash",         f"${cash:,.2f}", FG2),
                            ("P&L",          _fd(pl),          _pc(pl)),
                            ("Positions",    str(len(pos)),    BLU),
                        ]:
                            with ui.column().classes("gap-none items-center"):
                                ui.label(lbl).style(f"color:{FG2};font-size:0.68rem;font-family:monospace")
                                ui.label(val).style(f"color:{col};font-weight:700;font-family:monospace;font-size:0.95rem")
                    ui.separator().style(f"background:{BDR};margin:10px 0")

                # Positions table
                if pos:
                    ui.label("POSITIONS").style(f"color:{FG2};font-size:0.68rem;font-family:monospace;margin-bottom:4px")
                    pcols2 = [
                        {"name":"sym","label":"Symbol","field":"sym","align":"left"},
                        {"name":"val","label":"Value","field":"val","align":"right"},
                        {"name":"pl","label":"P&L","field":"pl","align":"right"},
                    ]
                    prows2 = [{"sym":p["symbol"],
                               "val":f"${_f(p.get('market_value')):,.0f}",
                               "pl":_fd(_f(p.get("unrealized_pl")))}
                              for p in sorted(pos, key=lambda x: -abs(_f(x.get("market_value",0))))]
                    ui.table(columns=pcols2, rows=prows2, row_key="sym").classes("w-full").props("dense flat bordered")
                elif is_active:
                    ui.label("No open positions").style(f"color:{FG2};font-family:monospace;font-size:0.82rem")

                # Recent orders
                filled_orders = [o for o in orders if o.get("status") in ("filled", "partially_filled")][:5]
                if filled_orders:
                    ui.separator().style(f"background:{BDR};margin:10px 0")
                    ui.label("RECENT ORDERS").style(f"color:{FG2};font-size:0.68rem;font-family:monospace;margin-bottom:4px")
                    ocols2 = [
                        {"name":"sym","label":"Symbol","field":"sym","align":"left"},
                        {"name":"side","label":"Side","field":"side"},
                        {"name":"qty","label":"Qty","field":"qty","align":"right"},
                        {"name":"px","label":"Fill $","field":"px","align":"right"},
                        {"name":"time","label":"Time","field":"time","align":"left"},
                    ]
                    orows2 = [{"sym":o.get("symbol","?"),
                               "side":o.get("side","?").upper(),
                               "qty":f"{float(o.get('qty') or 0):.2f}",
                               "px":f"${float(o.get('filled_avg_price') or 0):.2f}",
                               "time":o.get("submitted_at","")[:10]}
                              for o in filled_orders]
                    ui.table(columns=ocols2, rows=orows2, row_key="time").classes("w-full").props("dense flat bordered")

    with ui.row().classes("w-full q-px-lg q-pt-md"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            with ui.row().classes("w-full items-center q-mb-xs"):
                ui.label("DECISION LOG").style(
                    f"color:{FG2};font-size:0.75rem;font-weight:700;font-family:monospace")
                ui.space()
                ui.label("Real-time activity from all actions").style(
                    f"color:{FG2};font-size:0.65rem;font-family:monospace")
            if _DLOG:
                log_cols = [
                    {"name":"ts",     "label":"Time",   "field":"ts",     "align":"left"},
                    {"name":"action", "label":"Action", "field":"action", "align":"left"},
                    {"name":"detail", "label":"Detail", "field":"detail", "align":"left"},
                    {"name":"result", "label":"Result", "field":"result", "align":"left"},
                ]
                ui.table(columns=log_cols, rows=list(_DLOG[:25]), row_key="ts").classes("w-full").props("dense flat bordered")
            else:
                ui.label("No actions logged yet — click Deploy QC Strategies to start").style(
                    f"color:{FG2};font-family:monospace;font-size:0.82rem;padding:8px 0")

    # Totals row
    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-lg"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            total_eq = _f(paper_acct.get("equity", 0)) + sum(
                _f(e.get("account", {}).get("equity", 0)) for e in other_accts)
            ui.label(f"TOTAL ACROSS ALL ACCOUNTS: ${total_eq:,.2f}").style(
                f"color:{FG};font-size:1rem;font-weight:700;font-family:monospace")


def _close_paper_positions():
    def _run():
        _alp("DELETE", "/positions")
        time.sleep(1)
        _refresh_all()
        _log("CLOSE ALL", "Positions closed")
    threading.Thread(target=_run, daemon=True).start()


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DEPLOY — Live $50k Deployment Visualizer
# ══════════════════════════════════════════════════════════════════════════════

@ui.refreshable
def page_deploy():
    account    = _cache.get("account", {})
    positions  = _cache.get("positions", [])
    strategies = _cache.get("strategies", [])
    weights    = _cache.get("weights", [])
    allocs     = _cache.get("allocations", {})
    regime     = _cache.get("regime", {})
    ds         = _cache.get("deploy_status", {"running": False, "msg": ""})

    equity  = _f(account.get("equity", 0))
    cash    = _f(account.get("cash",   0))
    pos_map = {p["symbol"]: p for p in positions}

    r_label = regime.get("label", "—")
    r_col   = regime.get("color", FG2)

    # ── Header ────────────────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-sm items-center gap-lg").style(
            f"background:{BG2};border-bottom:1px solid {BDR}"):
        ui.label("🚀  DEPLOY  —  $50k QC SYSTEM").style(
            f"color:{GRN};font-size:1.3rem;font-weight:700;font-family:monospace")
        ui.space()
        if equity:
            ui.label(f"${equity:,.0f}  equity  •  ${cash:,.0f}  cash").style(
                f"color:{FG2};font-family:monospace;font-size:0.85rem")
        ui.badge(f"Regime: {r_label}").style(
            f"background:{r_col}22;color:{r_col};border:1px solid {r_col}55;"
            f"font-family:monospace;font-size:0.75rem;padding:4px 10px")

    # ── Action bar ────────────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-py-sm items-center gap-md flex-wrap").style(
            f"background:{BG3};border-bottom:1px solid {BDR}"):

        def _do_full_deploy():
            if _cache.get("deploy_status", {}).get("running"):
                return
            _cache["deploy_status"] = {"running": True, "msg": "Initializing…"}
            page_deploy.refresh()
            def _run():
                try:
                    _log("DEPLOY START", "$50k QC system deployment initiated")
                    _cache["deploy_status"]["msg"] = "Step 1/4 — Refreshing QC rankings…"
                    subprocess.run([sys.executable, str(QC_DIR/"main.py"), "--qc-refresh"],
                                   cwd=str(ROOT), timeout=60)
                    _log("QC FETCH", "QuantConnect leaderboard updated")
                    _refresh_qc()
                    _cache["deploy_status"]["msg"] = "Step 2/4 — Computing momentum scores…"
                    time.sleep(0.3)
                    _cache["deploy_status"]["msg"] = "Step 3/4 — Placing orders…"
                    result = subprocess.run(
                        [sys.executable, str(QC_DIR/"main.py"), "--rebalance"],
                        cwd=str(ROOT), timeout=120, capture_output=True, text=True)
                    _cache["deploy_status"]["msg"] = "Step 4/4 — Verifying fills…"
                    time.sleep(1.5)
                    _refresh_all()
                    n = len(_cache.get("positions", []))
                    _log("DEPLOY DONE", f"{n} positions active", "✓")
                    _cache["deploy_status"] = {"running": False,
                                               "msg": f"✓ Deployed — {n} positions active"}
                    # UI refresh handled by _auto_refresh timer (every 5s)
                except Exception as e:
                    _log("DEPLOY ERROR", str(e), "failed")
                    _cache["deploy_status"] = {"running": False, "msg": f"✗ Error: {e}"}
            threading.Thread(target=_run, daemon=True).start()

        running = ds.get("running", False)
        msg     = ds.get("msg", "")
        msg_col = GRN if msg.startswith("✓") else (RED if msg.startswith("✗") else YLW)

        _tip(ui.button(
            "⟳  Deploying…" if running else "🚀  Deploy QC Strategies",
            on_click=_do_full_deploy,
        ).props("unelevated no-caps").style(
            f"background:{YLW if running else GRN};color:#000;"
            f"font-family:monospace;font-weight:700;min-width:200px"),
            BTN_TIP["deploy"])

        if msg:
            ui.label(msg).style(
                f"color:{msg_col};font-family:monospace;font-size:0.85rem")

        ui.space()
        _tip(ui.button("↺  Refresh Data", on_click=lambda: threading.Thread(
            target=_refresh_all, daemon=True).start()
        ).props("unelevated no-caps").style(
            f"background:{BG2};color:{FG2};border:1px solid {BDR};font-family:monospace"),
            BTN_TIP["refresh_data"])

    # ── Capital overview ───────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md gap-md"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            total_deployed = sum(_f(p.get("market_value", 0)) for p in positions)
            pct_deployed   = (total_deployed / equity * 100) if equity > 0 else 0.0

            with ui.row().classes("w-full gap-xl flex-wrap items-center q-mb-sm"):
                for lbl, val, col, tip_k in [
                    ("Total Equity",   f"${equity:,.2f}",        FG,  "equity"),
                    ("Deployed",       f"${total_deployed:,.2f}", GRN, "mktval"),
                    ("Cash Reserve",   f"${cash:,.2f}",            YLW, "cash"),
                    ("Deploy %",       f"{pct_deployed:.1f}%",    BLU, "weight"),
                ]:
                    with ui.column().classes("gap-none items-center q-pa-sm").style(
                            f"background:{BG3};border-radius:8px;min-width:120px"):
                        ui.label(lbl).style(
                            f"color:{FG2};font-size:0.65rem;font-family:monospace")
                        _tip(ui.label(val).style(
                            f"color:{col};font-weight:700;font-family:monospace;font-size:1.05rem"),
                            MET_TIP[tip_k])

            # Progress bar
            with ui.element("div").style(
                    f"width:100%;height:12px;background:{BG3};"
                    f"border-radius:6px;overflow:hidden"):
                pct_s = max(0.0, min(100.0, pct_deployed))
                bar_c = GRN if pct_deployed >= 80 else (YLW if pct_deployed > 0 else BDR)
                ui.element("div").style(
                    f"width:{pct_s}%;height:100%;background:{bar_c};"
                    f"border-radius:6px;transition:width 0.4s")

    # ── Strategy deployment cards ─────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md gap-md flex-wrap"):
        if strategies:
            for i, (cfg, w) in enumerate(zip(strategies, weights)):
                sid    = cfg.get("id", "?")
                name   = cfg.get("name", f"Strategy {sid}")
                alloc  = allocs.get(str(sid), allocs.get(sid, {}))
                winner = next(iter(alloc), "—")
                col    = STRAT_COLORS[i % len(STRAT_COLORS)]
                stats  = cfg.get("statistics", {})
                def _sf(key, *alt, _s=stats):
                    return float(next(
                        (_s.get(k) for k in (key,)+alt if _s.get(k) is not None), 0))
                ret3m   = _sf("oos 3m return", "oos_3m_return")

                target_total = equity * w
                pos      = pos_map.get(winner, {})
                pos_val  = _f(pos.get("market_value", 0))
                pos_pl   = _f(pos.get("unrealized_pl", 0))
                pos_qty  = _f(pos.get("qty", 0))
                fill_pct = min((pos_val / target_total * 100) if target_total > 0 else 0, 100)
                drift    = pos_val - target_total

                with ui.card().classes("q-pa-md").style(
                        f"background:{BG2};border-left:4px solid {col};"
                        f"min-width:220px;flex:1 1 220px"):
                    with ui.row().classes("w-full justify-between items-start"):
                        with ui.column().classes("gap-none"):
                            ui.label(f"#{i+1}  {name[:20]}").style(
                                f"color:{col};font-weight:700;font-family:monospace;font-size:0.88rem")
                            ui.label(f"QC {sid}  •  OOS 3M: {ret3m*100:+.0f}%").style(
                                f"color:{FG2};font-size:0.68rem;font-family:monospace")
                        _tip(ui.label(f"{w*100:.1f}%").style(
                            f"color:{col};font-size:1.3rem;font-weight:700;font-family:monospace"),
                            MET_TIP["weight"])

                    ui.separator().style(f"background:{BDR};margin:6px 0")

                    with ui.row().classes("w-full items-center justify-between"):
                        ui.label(winner).style(
                            f"color:{FG};font-size:1.25rem;font-weight:700;font-family:monospace")
                        if pos_pl != 0:
                            ui.label(_fd(pos_pl)).style(
                                f"color:{_pc(pos_pl)};font-family:monospace;font-weight:700")

                    with ui.row().classes("w-full gap-md q-mt-xs"):
                        with ui.column().classes("gap-none"):
                            _tip(ui.label("Target").style(
                                f"color:{FG2};font-size:0.65rem;font-family:monospace"),
                                MET_TIP["target"])
                            ui.label(f"${target_total:,.0f}").style(
                                f"color:{FG2};font-family:monospace;font-weight:700")
                        with ui.column().classes("gap-none"):
                            _tip(ui.label("Held").style(
                                f"color:{FG2};font-size:0.65rem;font-family:monospace"),
                                MET_TIP["mktval"])
                            ui.label(f"${pos_val:,.0f}").style(
                                f"color:{FG if pos_val > 0 else FG2};"
                                f"font-family:monospace;font-weight:700")
                        if pos_qty:
                            with ui.column().classes("gap-none"):
                                ui.label("Qty").style(
                                    f"color:{FG2};font-size:0.65rem;font-family:monospace")
                                ui.label(f"{pos_qty:.3f}").style(
                                    f"color:{FG2};font-family:monospace;font-size:0.85rem")

                    with ui.element("div").style(
                            f"width:100%;height:6px;background:{BG3};border-radius:3px;"
                            f"overflow:hidden;margin-top:8px;margin-bottom:6px"):
                        pct_s = max(0.0, min(100.0, fill_pct))
                        b_col = GRN if fill_pct >= 90 else (YLW if fill_pct > 0 else BDR)
                        ui.element("div").style(
                            f"width:{pct_s}%;height:100%;background:{b_col};border-radius:3px")

                    with ui.row().classes("w-full justify-between items-center"):
                        if pos_val == 0:
                            ui.badge("NOT DEPLOYED", color="warning").style(
                                "font-family:monospace;font-size:0.68rem")
                        elif fill_pct >= 90:
                            ui.badge("✓ DEPLOYED", color="positive").style(
                                "font-family:monospace;font-size:0.68rem")
                        else:
                            drift_s = f"${abs(drift):,.0f} {'over' if drift > 0 else 'short'}"
                            _tip(ui.badge(f"DRIFT {drift_s}", color="warning").style(
                                "font-family:monospace;font-size:0.68rem"),
                                MET_TIP["drift"])
                        ui.label(f"{fill_pct:.0f}%").style(
                            f"color:{col if fill_pct >= 90 else YLW};"
                            f"font-family:monospace;font-size:0.8rem;font-weight:700")
        else:
            with ui.card().classes("w-full q-pa-xl").style(
                    f"background:{BG2};text-align:center"):
                ui.label("No QC strategies loaded").style(
                    f"color:{FG2};font-family:monospace;font-size:1rem;padding:16px")
                ui.label("Click '🚀 Deploy QC Strategies' to fetch rankings and deploy").style(
                    f"color:{FG2};font-family:monospace;font-size:0.82rem")

    # ── Deploy log ────────────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-lg"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            ui.label("DEPLOY LOG").style(
                f"color:{FG2};font-size:0.8rem;font-weight:700;font-family:monospace;"
                f"margin-bottom:8px")
            entries = [d for d in _DLOG if any(
                k in d.get("action", "")
                for k in ("DEPLOY", "REBALANCE", "QC", "ORDER", "CLOSE"))][:20]
            if entries:
                log_cols = [
                    {"name":"ts",     "label":"Time",   "field":"ts",     "align":"left"},
                    {"name":"action", "label":"Action", "field":"action", "align":"left"},
                    {"name":"detail", "label":"Detail", "field":"detail", "align":"left"},
                    {"name":"result", "label":"Result", "field":"result", "align":"left"},
                ]
                ui.table(columns=log_cols, rows=entries, row_key="ts").classes(
                    "w-full").props("dense flat bordered")
            else:
                ui.label("No deploy actions yet — click 🚀 Deploy to start").style(
                    f"color:{FG2};font-family:monospace;font-size:0.82rem")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: REGIME — Market Regime Detection
# ══════════════════════════════════════════════════════════════════════════════

@ui.refreshable
def page_regime():
    regime     = _cache.get("regime", {})
    strategies = _cache.get("strategies", [])
    weights    = _cache.get("weights", [])

    r_label  = regime.get("label",     "Detecting…")
    r_col    = regime.get("color",     FG2)
    r_spy    = regime.get("spy_price", 0.0)
    r_sma20  = regime.get("sma20",     0.0)
    r_sma200 = regime.get("sma200",    0.0)
    r_vix    = regime.get("vix",       0.0)
    r_ret20  = regime.get("ret20",     0.0)
    r_score  = regime.get("score",     0)
    r_upd    = regime.get("updated",   "—")

    # Find regime key for lookup tables
    r_key = "BULL"
    for k in ("RISK-OFF", "BEAR", "CAUTION", "NEUTRAL", "BULL"):
        if k in r_label:
            r_key = k
            break

    # ── Header ────────────────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-sm items-center gap-lg").style(
            f"background:{BG2};border-bottom:1px solid {BDR}"):
        ui.label("📈  MARKET REGIME").style(
            f"color:{PRP};font-size:1.3rem;font-weight:700;font-family:monospace")
        ui.space()
        ui.button("⟳  Refresh Regime", on_click=lambda: threading.Thread(
            target=_compute_regime, daemon=True).start()
        ).props("unelevated no-caps flat").style(
            f"color:{PRP};font-family:monospace;font-size:0.8rem")
        ui.label(f"Updated: {r_upd}").style(
            f"color:{FG2};font-family:monospace;font-size:0.8rem")

    # ── Regime hero + signals ─────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md gap-md flex-wrap"):
        # Big regime card
        with ui.card().classes("q-pa-lg").style(
                f"background:{BG2};border:2px solid {r_col};"
                f"min-width:180px;flex:0 0 auto;align-self:flex-start"):
            ui.label("CURRENT REGIME").style(
                f"color:{FG2};font-size:0.72rem;font-family:monospace")
            ui.label(r_label).style(
                f"color:{r_col};font-size:2.2rem;font-weight:700;font-family:monospace;"
                f"margin-top:8px")
            with ui.row().classes("gap-xs items-center q-mt-sm"):
                for i in range(3):
                    filled = i < r_score
                    ui.element("div").style(
                        f"width:18px;height:8px;border-radius:4px;"
                        f"background:{r_col if filled else BDR}")
            ui.label(f"Score {r_score}/3").style(
                f"color:{FG2};font-family:monospace;font-size:0.78rem;margin-top:4px")

        # Signal cards
        with ui.card().classes("q-pa-md").style(f"background:{BG2};flex:1;min-width:300px"):
            ui.label("REGIME SIGNALS").style(
                f"color:{FG2};font-size:0.72rem;font-weight:700;font-family:monospace;"
                f"letter-spacing:2px;margin-bottom:10px")
            with ui.row().classes("w-full gap-sm flex-wrap"):
                spy_vs_sma20  = r_spy > r_sma20  if r_sma20  else False
                spy_vs_sma200 = r_spy > r_sma200 if r_sma200 else False
                signals = [
                    ("SPY",      f"${r_spy:,.2f}",            "",                                     True),
                    ("SMA 20",   f"${r_sma20:,.2f}",          f"{'above' if spy_vs_sma20 else 'below'} SMA", spy_vs_sma20),
                    ("SMA 200",  f"${r_sma200:,.2f}",         f"{'above' if spy_vs_sma200 else 'below'} SMA", spy_vs_sma200),
                    ("VIX",      f"{r_vix:.1f}",               "< 20 calm  |  > 25 fear",             r_vix < 25),
                    ("20-Day",   f"{r_ret20*100:+.1f}%",       "SPY 20-day return",                   r_ret20 >= 0),
                ]
                for lbl, val, sub, ok in signals:
                    with ui.column().classes("gap-none items-center q-pa-sm").style(
                            f"background:{BG3};border-radius:8px;min-width:90px"):
                        ui.label(lbl).style(
                            f"color:{FG2};font-size:0.62rem;font-family:monospace")
                        sig_col = GRN if ok else RED
                        ui.label(val).style(
                            f"color:{sig_col};font-weight:700;font-family:monospace;"
                            f"font-size:1.05rem")
                        if sub:
                            ui.label(sub).style(
                                f"color:{FG2};font-size:0.58rem;font-family:monospace;"
                                f"text-align:center")

    # ── Strategy-regime alignment ─────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md gap-md"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            ui.label("STRATEGY — REGIME ALIGNMENT").style(
                f"color:{FG2};font-size:0.8rem;font-weight:700;font-family:monospace;"
                f"margin-bottom:10px")

            REGIME_FIT = {
                245:  {"BULL":"STRONG FIT","NEUTRAL":"MODERATE","CAUTION":"CAUTION","RISK-OFF":"AVOID"},
                46:   {"BULL":"STRONG FIT","NEUTRAL":"MODERATE","CAUTION":"AVOID",  "RISK-OFF":"AVOID"},
                123:  {"BULL":"STRONG FIT","NEUTRAL":"GOOD FIT","CAUTION":"CAUTION","RISK-OFF":"AVOID"},
                60:   {"BULL":"STRONG FIT","NEUTRAL":"MODERATE","CAUTION":"AVOID",  "RISK-OFF":"AVOID"},
                345:  {"BULL":"STRONG FIT","NEUTRAL":"GOOD FIT","CAUTION":"MODERATE","RISK-OFF":"CAUTION"},
                382:  {"BULL":"STRONG FIT","NEUTRAL":"GOOD FIT","CAUTION":"CAUTION","RISK-OFF":"AVOID"},
                252:  {"BULL":"CAUTION",   "NEUTRAL":"MODERATE","CAUTION":"GOOD FIT","RISK-OFF":"STRONG FIT"},
                364:  {"BULL":"STRONG FIT","NEUTRAL":"GOOD FIT","CAUTION":"MODERATE","RISK-OFF":"CAUTION"},
                446:  {"BULL":"GOOD FIT",  "NEUTRAL":"GOOD FIT","CAUTION":"MODERATE","RISK-OFF":"MODERATE"},
                315:  {"BULL":"MODERATE",  "NEUTRAL":"GOOD FIT","CAUTION":"MODERATE","RISK-OFF":"MODERATE"},
            }
            REASONS = {
                "STRONG FIT": "Historically outperforms in this regime",
                "GOOD FIT":   "Should perform well with current conditions",
                "MODERATE":   "No strong advantage or disadvantage",
                "CAUTION":    "May underperform — monitor closely",
                "AVOID":      "Historically struggles in this regime",
            }

            if strategies:
                acols = [
                    {"name":"rank",  "label":"#",           "field":"rank",  "align":"left"},
                    {"name":"name",  "label":"Strategy",    "field":"name",  "align":"left"},
                    {"name":"align", "label":"Alignment",   "field":"align", "align":"left"},
                    {"name":"wt",    "label":"Weight",      "field":"wt",    "align":"right"},
                    {"name":"reason","label":"Why",         "field":"reason","align":"left"},
                ]
                arows = []
                for i2, (cfg2, w2) in enumerate(zip(strategies, weights)):
                    sid2 = cfg2.get("id", "?")
                    fit_map = REGIME_FIT.get(
                        int(sid2) if str(sid2).isdigit() else sid2, {})
                    align = fit_map.get(r_key, "MODERATE")
                    arows.append({
                        "rank":   f"#{i2+1}",
                        "name":   cfg2.get("name","?")[:30],
                        "align":  align,
                        "wt":     f"{w2*100:.1f}%",
                        "reason": REASONS.get(align, ""),
                    })
                t = ui.table(columns=acols, rows=arows, row_key="rank").classes(
                    "w-full").props("dense flat bordered")
                t.add_slot("body-cell-align", """
                    <q-td :props="props">
                      <span :style="'font-weight:700;font-family:monospace;font-size:0.8rem;color:' +
                        (props.value === 'STRONG FIT' ? '#3fb950' :
                         props.value === 'GOOD FIT'   ? '#58a6ff' :
                         props.value === 'AVOID'      ? '#f85149' :
                         props.value === 'CAUTION'    ? '#d29922' : '#8b949e')">
                        {{ props.value }}
                      </span>
                    </q-td>""")
            else:
                ui.label("Load QC strategies first to see alignment").style(
                    f"color:{FG2};font-family:monospace")

    # ── Regime playbook ───────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-lg"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            ui.label("REGIME PLAYBOOK").style(
                f"color:{FG2};font-size:0.8rem;font-weight:700;font-family:monospace;"
                f"margin-bottom:8px")
            playbooks = {
                "BULL":     ("✓  Deploy full capital into momentum strategies\n"
                             "✓  Leveraged ETF strategies (#46, #60) are high-conviction\n"
                             "✓  AI/Tech rotation (#245, #382) running hot\n"
                             "◑  Risk-off (#252) will lag in a bull — normal"),
                "NEUTRAL":  ("◑  Partial deployment recommended — watch for direction\n"
                             "✓  Diversified momentum (#345, #364) offers balance\n"
                             "◑  Reduce leveraged ETF exposure (#46, #60)\n"
                             "✓  Tail-risk (#446) provides some downside protection"),
                "CAUTION":  ("⚠  Exercise caution — regime uncertain\n"
                             "◑  Hold 30–50% cash as buffer\n"
                             "✓  Risk-off strategy (#252) worth increasing\n"
                             "◑  Avoid leveraged ETFs until trend clarifies"),
                "RISK-OFF": ("⚠  Defensive posture — reduce equity exposure\n"
                             "✓  Risk-off (#252): TLT, GLD, SHV are your shield\n"
                             "✗  Avoid ALL leveraged long ETF strategies\n"
                             "✓  Hold cash — opportunities will emerge after flush"),
            }
            text = playbooks.get(r_key, "Detecting market regime — will update shortly…")
            ui.label(text).style(
                f"color:{FG};font-family:monospace;font-size:0.88rem;"
                f"white-space:pre-wrap;line-height:1.8")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: BOT CONTROLS
# ══════════════════════════════════════════════════════════════════════════════

@ui.refreshable
def page_bot():
    PORT_STATE = ROOT / "portfolio_state.json"
    PORT_LOG   = ROOT / "portfolio.log"
    SPY_LOG    = ROOT / "spy_trader.log"

    with ui.row().classes("w-full q-px-lg q-pt-md"):
        ui.label("BOT CONTROLS").style(
            f"color:{BLU};font-size:1.3rem;font-weight:700;font-family:monospace")

    # ── SWARM DAILY TRADE card ────────────────────────────────────────────────
    _swt_path = ROOT / "swarm_daily_trade.json"
    _swt: dict | None = None
    _swt_stale = True
    if _swt_path.exists():
        try:
            _swt = json.loads(_swt_path.read_text())
            _swt_stale = (time.time() - _swt_path.stat().st_mtime) > 86400
        except Exception:
            pass

    with ui.row().classes("w-full q-px-lg q-pt-sm"):
        with ui.card().classes("q-pa-lg w-full").style(f"background:{BG2}"):
            with ui.row().classes("w-full justify-between items-center"):
                ui.label("SWARM DAILY TRADE").style(
                    f"color:{BLU};font-size:1.1rem;font-weight:700;font-family:monospace")
                if _swt:
                    _swt_mode_col = GRN if _swt.get("mode") == "live" else YLW
                    ui.label(_swt.get("mode", "scan").upper()).style(
                        f"color:{_swt_mode_col};font-family:monospace;"
                        f"font-size:0.8rem;font-weight:700")
            if not _swt or _swt_stale:
                ui.label("No trade today — run trading_swarm.py").style(
                    f"color:{FG2};font-family:monospace;font-size:0.9rem;"
                    f"font-style:italic;margin-top:8px")
            else:
                _swt_side    = _swt.get("side", "buy")
                _swt_sym_col = GRN if _swt_side == "buy" else RED
                with ui.row().classes("items-baseline gap-md q-mt-sm"):
                    ui.label(_swt.get("symbol", "—")).style(
                        f"color:{_swt_sym_col};font-size:2rem;"
                        f"font-weight:700;font-family:monospace")
                    ui.label(_swt_side.upper()).style(
                        f"color:{_swt_sym_col};font-size:1rem;"
                        f"font-weight:700;font-family:monospace")
                    ui.label(f"score {_swt.get('score', 0)}").style(
                        f"color:{FG2};font-family:monospace;font-size:0.85rem")
                ui.label(
                    f"{_swt.get('strategy','—')}  |  "
                    f"Entry ${_swt.get('entry_price', 0):.2f}  |  "
                    f"Stop ${_swt.get('stop_price', 0):.2f}  |  "
                    f"Target ${_swt.get('target_price', 0):.2f}  |  "
                    f"R:R 1:{_swt.get('rr_ratio', 0):.1f}"
                ).style(
                    f"color:{FG};font-family:monospace;font-size:0.88rem;margin-top:4px")
                with ui.element("div").style(
                    f"max-height:8.5rem;overflow-y:auto;margin-top:8px;"
                    f"background:{BG3};border:1px solid {BDR};border-radius:6px;"
                    f"padding:8px 10px"
                ):
                    ui.label(_swt.get("thesis", "")).style(
                        f"color:{FG};font-family:monospace;font-size:0.78rem;"
                        f"white-space:pre-wrap;line-height:1.6")
                ui.label(
                    f"Generated {_swt.get('timestamp','')[:16].replace('T',' ')}"
                ).style(
                    f"color:{FG2};font-family:monospace;font-size:0.75rem;margin-top:6px")

    bot_running = _cache["bot_proc"] and _cache["bot_proc"].poll() is None
    hb_text, hb_col = "—", FG2
    if PORT_STATE.exists():
        age = time.time() - PORT_STATE.stat().st_mtime
        hb_text = f"♥ {int(age)}s ago" if age < 30 else f"{int(age)}s ago"
        hb_col  = GRN if age < 30 else (YLW if age < 120 else RED)

    with ui.row().classes("w-full q-px-lg q-pt-sm gap-md flex-wrap"):
        # Portfolio bot
        with ui.card().classes("q-pa-lg").style(f"background:{BG2};min-width:280px;flex:1"):
            with ui.row().classes("w-full justify-between items-center"):
                ui.label("Portfolio Bot").style(
                    f"color:{FG};font-size:1.1rem;font-weight:700;font-family:monospace")
                ui.label("● RUNNING" if bot_running else "● STOPPED").style(
                    f"color:{GRN if bot_running else RED};font-weight:700;font-family:monospace")
            ui.label("mock_portfolio.py").style(
                f"color:{FG2};font-size:0.8rem;font-family:monospace")
            ui.label(f"Heartbeat: {hb_text}").style(
                f"color:{hb_col};font-family:monospace;font-size:0.85rem;margin-top:4px")
            with ui.row().classes("gap-sm q-mt-md"):
                def _start_bot():
                    if not (_cache["bot_proc"] and _cache["bot_proc"].poll() is None):
                        _cache["bot_proc"] = subprocess.Popen(
                            [sys.executable, str(ROOT / "mock_portfolio.py")],
                            cwd=str(ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        ui.notify("Portfolio bot started", color="positive")
                        page_bot.refresh()
                def _stop_bot():
                    if _cache["bot_proc"] and _cache["bot_proc"].poll() is None:
                        _cache["bot_proc"].terminate()
                        ui.notify("Portfolio bot stopped", color="warning")
                        page_bot.refresh()
                _tip(ui.button("▶  Start", on_click=_start_bot).props("unelevated no-caps").style(
                    f"background:{GRN};color:#000;font-family:monospace;font-weight:700"),
                    BTN_TIP["start_bot"])
                _tip(ui.button("■  Stop", on_click=_stop_bot).props("unelevated no-caps").style(
                    f"background:{RED};color:#fff;font-family:monospace;font-weight:700"),
                    BTN_TIP["stop_bot"])

        # QC Portfolio Daemon
        with ui.card().classes("q-pa-lg").style(f"background:{BG2};min-width:280px;flex:1"):
            qc_running = bool(_cache["qc_proc"] and _cache["qc_proc"].poll() is None)

            # Next 9:31 ET weekday
            from datetime import timedelta as _td
            _now_et = datetime.now(ET)
            _next_et = _now_et.replace(hour=9, minute=31, second=0, microsecond=0)
            if _now_et >= _next_et:
                _next_et += _td(days=1)
            while _next_et.weekday() >= 5:
                _next_et += _td(days=1)

            # Last rebalance from state file
            _ps_path = QC_DIR / "portfolio_state.json"
            _last_rebal = "Never"
            if _ps_path.exists():
                try:
                    _ps = json.loads(_ps_path.read_text())
                    _lr = _ps.get("last_rebalance")
                    if _lr:
                        _last_rebal = _lr[:16].replace("T", " ") + " UTC"
                except Exception:
                    pass

            with ui.row().classes("w-full justify-between items-center"):
                ui.label("QC Portfolio Daemon").style(
                    f"color:{FG};font-size:1.1rem;font-weight:700;font-family:monospace")
                ui.label("● RUNNING" if qc_running else "● STOPPED").style(
                    f"color:{GRN if qc_running else RED};font-weight:700;font-family:monospace")
            ui.label("qc_portfolio/main.py --daemon").style(
                f"color:{FG2};font-size:0.78rem;font-family:monospace")
            with ui.column().classes("gap-none q-mt-xs"):
                ui.label(f"Next fire:  {_next_et.strftime('%a %b %-d @ 09:31 ET')}").style(
                    f"color:{BLU};font-family:monospace;font-size:0.82rem")
                ui.label(f"Last rebal: {_last_rebal}").style(
                    f"color:{FG2};font-family:monospace;font-size:0.78rem")
                ui.label("Holds positions overnight & weekends").style(
                    f"color:{GRN};font-family:monospace;font-size:0.72rem")

            with ui.row().classes("gap-sm q-mt-md flex-wrap"):
                def _start_qc():
                    if not (_cache["qc_proc"] and _cache["qc_proc"].poll() is None):
                        lf = open("/tmp/qc_portfolio.log", "a")
                        _cache["qc_proc"] = subprocess.Popen(
                            [sys.executable, str(QC_DIR/"main.py"), "--daemon"],
                            cwd=str(ROOT), stdout=lf, stderr=lf)
                        _log("QC DAEMON", "Started — fires at 09:31 ET weekdays")
                        ui.notify("QC daemon started — next fire: " +
                                  _next_et.strftime("%a %b %-d @ 09:31 ET"),
                                  color="positive", timeout=5000)
                        page_bot.refresh()
                def _stop_qc():
                    if _cache["qc_proc"] and _cache["qc_proc"].poll() is None:
                        _cache["qc_proc"].terminate()
                        _log("QC DAEMON", "Stopped by user")
                        ui.notify("QC daemon stopped", color="warning")
                        page_bot.refresh()
                def _rebal_now():
                    ui.notify("Running immediate rebalance…", color="info", timeout=3000)
                    def _r():
                        subprocess.run([sys.executable, str(QC_DIR/"main.py"), "--rebalance"],
                                       cwd=str(ROOT), timeout=120)
                        _refresh_all()
                        _log("REBALANCE", "Manual fire from Bot Controls")
                    threading.Thread(target=_r, daemon=True).start()
                def _qcref_now():
                    ui.notify("Refreshing QC rankings…", color="info")
                    def _r():
                        subprocess.run([sys.executable, str(QC_DIR/"main.py"), "--qc-refresh"],
                                       cwd=str(ROOT), timeout=60)
                        _refresh_qc()
                    threading.Thread(target=_r, daemon=True).start()

                _tip(ui.button("▶  Start Daemon", on_click=_start_qc).props(
                    "unelevated no-caps").style(
                    f"background:{GRN};color:#000;font-family:monospace;font-weight:700"),
                    "Start the QC scheduler daemon.\nSleeps until 09:31 ET each weekday,\nthen runs a full rebalance cycle.")
                _tip(ui.button("■  Stop", on_click=_stop_qc).props(
                    "unelevated no-caps").style(
                    f"background:{RED};color:#fff;font-family:monospace"),
                    "Stop the daemon process.\nOpen positions remain open.")
                _tip(ui.button("⚡  Rebalance Now", on_click=_rebal_now).props(
                    "unelevated no-caps").style(
                    f"background:{BG3};color:{YLW};border:1px solid {BDR};font-family:monospace"),
                    "Force an immediate rebalance cycle\n(doesn't wait for 09:31 ET).\nUseful for initial deployment.")
                ui.button("⟳  Refresh QC", on_click=_qcref_now).props(
                    "unelevated no-caps").style(
                    f"background:{BG3};color:{BLU};border:1px solid {BDR};font-family:monospace")

    # Log tails
    QC_LOG = Path("/tmp/qc_portfolio.log")
    with ui.row().classes("w-full q-px-lg q-pt-md gap-md flex-wrap"):
        for title, log_path, col in [
            ("QC Daemon Log",   QC_LOG,  PRP),
            ("Portfolio Log",   PORT_LOG, GRN),
            ("SPY Trader Log",  SPY_LOG,  BLU),
        ]:
            with ui.card().classes("q-pa-md").style(f"background:{BG2};flex:1;min-width:300px"):
                ui.label(title).style(
                    f"color:{col};font-size:0.85rem;font-weight:700;font-family:monospace;margin-bottom:6px")
                lines = []
                if log_path.exists():
                    lines = log_path.read_text().splitlines()[-30:]
                with ui.scroll_area().style(
                        f"height:250px;background:{BG};font-family:monospace;font-size:0.78rem"):
                    for line in reversed(lines):
                        lc = (GRN if "ENTRY" in line else YLW if "EXIT" in line else
                              RED  if "ERROR" in line else FG2)
                        ui.label(line).style(f"color:{lc}")



# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PORTFOLIO OVERVIEW (Alpaca raw view)
# ══════════════════════════════════════════════════════════════════════════════

@ui.refreshable
def page_portfolio():
    positions = _cache.get("positions", [])
    account   = _cache.get("account", {})
    equity      = _f(account.get("equity", 0))
    cash        = _f(account.get("cash", 0))
    last_equity = _f(account.get("last_equity", 0))
    day_change  = equity - last_equity if last_equity > 0 else 0.0
    day_pct     = (day_change / last_equity * 100) if last_equity > 0 else 0.0

    upl_total = sum(_f(p.get("unrealized_pl")) for p in positions)
    cost_basis = sum(_f(p.get("cost_basis")) or (_f(p.get("qty")) * _f(p.get("avg_entry_price"))) for p in positions)
    upl_pct    = (upl_total / cost_basis * 100) if cost_basis > 0 else 0.0

    last_ref = _cache.get("last_refresh")
    age_s    = int((datetime.now(ET) - last_ref).total_seconds()) if last_ref else -1

    with ui.row().classes("w-full q-px-lg q-pt-md items-center justify-between"):
        ui.label("ALPACA PORTFOLIO").style(
            f"color:{BLU};font-size:1.3rem;font-weight:700;font-family:monospace")
        age_col = GRN if 0 <= age_s < 10 else (YLW if age_s < 30 else RED)
        age_txt = "—" if age_s < 0 else f"updated {age_s}s ago"
        ui.label(age_txt).style(
            f"color:{age_col};font-size:0.72rem;font-family:monospace;letter-spacing:1px")

    # KPI grid — 2×2 on mobile, single row on desktop
    with ui.row().classes("w-full q-px-lg q-pt-sm gap-sm").style("flex-wrap:wrap"):
        _kpi_cards = [
            ("EQUITY",     f"${equity:,.2f}",                                    FG,   None),
            ("CASH",       f"${cash:,.2f}",                                      FG2,  f"{(cash/equity*100 if equity else 0):.1f}% of equity"),
            ("DAY P&L",    f"{_fd(day_change)}  ({day_pct:+.2f}%)",              _pc(day_change), "vs. prior close"),
            ("UNREALIZED", f"{_fd(upl_total)}  ({upl_pct:+.2f}%)",               _pc(upl_total),  f"on ${cost_basis:,.0f} cost basis"),
            ("POSITIONS",  str(len(positions)),                                  BLU,  None),
        ]
        for label, val, col, sub in _kpi_cards:
            with ui.card().classes("q-pa-md").style(
                    f"background:{BG2};border-left:3px solid {col};flex:1 1 180px;min-width:160px"):
                ui.label(label).style(
                    f"color:{FG2};font-size:0.68rem;font-family:monospace;letter-spacing:1.5px")
                ui.label(val).style(
                    f"color:{col};font-size:1.15rem;font-weight:700;font-family:monospace;line-height:1.4")
                if sub:
                    ui.label(sub).style(
                        f"color:{FG2};font-size:0.65rem;font-family:monospace;opacity:0.75")

    with ui.row().classes("w-full q-px-lg q-pt-md gap-md").style("flex-wrap:wrap"):
        with ui.card().classes("q-pa-sm").style(
                f"background:{BG2};flex:1 1 300px;min-width:280px"):
            ui.plotly(_alloc_donut(
                _cache["strategies"], _cache["weights"], positions)).style("height:280px")
        with ui.card().classes("q-pa-sm").style(
                f"background:{BG2};flex:2 1 400px;min-width:280px"):
            ui.plotly(_equity_chart()).style("height:280px")

    with ui.row().classes("w-full q-px-lg q-pt-md"):
        with ui.card().classes("w-full q-pa-sm").style(f"background:{BG2};min-height:200px"):
            ui.plotly(_pnl_chart()).classes("w-full").style("min-height:200px")

    ui.timer(60, page_portfolio.refresh)

    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-lg"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            ui.label("POSITIONS").style(
                f"color:{FG2};font-size:0.8rem;font-weight:700;font-family:monospace;letter-spacing:1.5px;margin-bottom:8px")
            if not positions:
                with ui.column().classes("w-full items-center q-py-lg"):
                    ui.label("○ No open positions").style(
                        f"color:{FG2};font-size:0.9rem;font-family:monospace;opacity:0.6")
                    ui.label("Portfolio bot picks will appear here once fills post").style(
                        f"color:{FG2};font-size:0.72rem;font-family:monospace;opacity:0.4;margin-top:4px")
            else:
                pcols = [
                    {"name":"symbol", "label":"Symbol",  "field":"symbol", "align":"left", "sortable":True},
                    {"name":"qty",    "label":"Qty",     "field":"qty",    "align":"right"},
                    {"name":"price",  "label":"Price",   "field":"price",  "align":"right"},
                    {"name":"value",  "label":"Value",   "field":"value",  "align":"right","sortable":True},
                    {"name":"weight", "label":"% Port",  "field":"weight", "align":"right","sortable":True},
                    {"name":"pl",     "label":"P&L",     "field":"pl",     "align":"right","sortable":True},
                    {"name":"plpct",  "label":"P&L %",   "field":"plpct",  "align":"right","sortable":True},
                ]
                prows = []
                for p in sorted(positions, key=lambda x: -abs(float(x.get("market_value", 0)))):
                    mv    = _f(p.get("market_value", 0))
                    upl   = _f(p.get("unrealized_pl", 0))
                    uplpc = _f(p.get("unrealized_plpc", 0)) * 100
                    prows.append({
                        "symbol":  p["symbol"],
                        "qty":     f"{_f(p.get('qty', 0)):.4f}",
                        "price":   f"${_f(p.get('current_price', 0)):,.2f}",
                        "value":   f"${mv:,.2f}",
                        "weight":  f"{(mv/equity*100 if equity else 0):.1f}%",
                        "pl":      _fd(upl),
                        "plpct":   f"{uplpc:+.2f}%",
                        "_pl_col": GRN if upl >= 0 else RED,
                    })
                tbl = ui.table(columns=pcols, rows=prows, row_key="symbol").classes(
                    "w-full").props("dense flat bordered")
                # Colour the P&L cells green/red
                tbl.add_slot("body-cell-pl", """
                    <q-td :props="props" :style="'color:'+props.row._pl_col+';font-weight:700'">
                        {{ props.value }}
                    </q-td>
                """)
                tbl.add_slot("body-cell-plpct", """
                    <q-td :props="props" :style="'color:'+props.row._pl_col+';font-weight:700'">
                        {{ props.value }}
                    </q-td>
                """)



# ══════════════════════════════════════════════════════════════════════════════
# PAGE: DAILY REVIEW
# ══════════════════════════════════════════════════════════════════════════════

@ui.refreshable
def page_review():
    DAILY_REVIEW = ROOT / "daily_review.json"
    PREMARKET    = ROOT / "premarket_picks.json"
    PORT_STATE   = ROOT / "portfolio_state.json"

    def _rj(p):
        try: return json.loads(p.read_text()) if p.exists() else {}
        except: return {}

    review  = _rj(DAILY_REVIEW)
    premark = _rj(PREMARKET)
    ps      = _rj(PORT_STATE)
    perf    = review.get("performance", {})
    total_pnl = ps.get("total_pnl", 0)

    with ui.row().classes("w-full q-px-lg q-pt-md"):
        ui.label("DAILY REVIEW").style(
            f"color:{GRN};font-size:1.3rem;font-weight:700;font-family:monospace")

    with ui.row().classes("w-full q-px-lg q-pt-sm gap-md flex-wrap"):
        for label, val, col in [
            ("Date",      review.get("date","—"),                FG),
            ("Trades",    str(perf.get("trades", 0)),            FG),
            ("Wins",      str(perf.get("wins",   0)),            GRN),
            ("Losses",    str(perf.get("losses", 0)),            RED),
            ("Win Rate",  f"{perf.get('win_rate',0)*100:.0f}%" if perf.get("win_rate") else "—", BLU),
            ("Day P&L",   _fd(perf.get("day_pnl",0)),           _pc(perf.get("day_pnl",0))),
            ("Total P&L", _fd(total_pnl),                       _pc(total_pnl)),
        ]:
            with ui.card().classes("q-px-lg q-py-xs").style(f"background:{BG2}"):
                ui.label(label).style(f"color:{FG2};font-size:0.72rem;font-family:monospace")
                ui.label(val).style(f"color:{col};font-size:1.2rem;font-weight:700;font-family:monospace")

    with ui.row().classes("w-full q-px-lg q-pt-md gap-md flex-wrap q-pb-lg"):
        # Left: movers + misses
        with ui.column().classes("gap-md").style("flex:1;min-width:300px"):
            with ui.card().classes("q-pa-md w-full").style(f"background:{BG2}"):
                ui.label("TODAY'S TOP MOVERS").style(
                    f"color:{BLU};font-size:0.8rem;font-weight:700;font-family:monospace;margin-bottom:8px")
                movers = review.get("top_movers", [])
                if movers:
                    mcols = [{"name":"sym","label":"Symbol","field":"sym","align":"left"},
                             {"name":"pct","label":"Day %","field":"pct"},
                             {"name":"open","label":"Open","field":"open"},
                             {"name":"close","label":"Close","field":"close"}]
                    mrows = [{"sym":m.get("symbol",""), "pct":f"{m.get('day_chg',0):+.2f}%",
                              "open":f"${m.get('open',0):.2f}", "close":f"${m.get('close',0):.2f}"}
                             for m in movers[:15]]
                    ui.table(columns=mcols, rows=mrows, row_key="sym").classes("w-full").props(
                        "dense flat bordered")
                else:
                    ui.label("Runs at 16:05 ET").style(f"color:{FG2};font-family:monospace")

            with ui.card().classes("q-pa-md w-full").style(f"background:{BG2}"):
                ui.label("MISSED OPPORTUNITIES").style(
                    f"color:{YLW};font-size:0.8rem;font-weight:700;font-family:monospace;margin-bottom:8px")
                missed = review.get("missed_opps", [])
                if missed:
                    miscols = [{"name":"sym","label":"Symbol","field":"sym","align":"left"},
                               {"name":"pct","label":"Day %","field":"pct"},
                               {"name":"reason","label":"Reason","field":"reason","align":"left"}]
                    misrows = [{"sym":m.get("symbol",""), "pct":f"{m.get('day_chg',0):+.2f}%",
                                "reason":m.get("reason","—")} for m in missed[:10]]
                    ui.table(columns=miscols, rows=misrows, row_key="sym").classes("w-full").props(
                        "dense flat bordered")

        # Right: suggestions + premarket
        with ui.column().classes("gap-md").style("flex:1;min-width:300px"):
            with ui.card().classes("q-pa-md w-full").style(f"background:{BG2}"):
                ui.label("SUGGESTIONS / GAME PLAN").style(
                    f"color:{GRN};font-size:0.8rem;font-weight:700;font-family:monospace;margin-bottom:8px")
                suggestions = review.get("suggestions", "No review yet — runs at 16:05 ET.")
                ui.label(str(suggestions)).style(
                    f"color:{FG};font-family:monospace;font-size:0.85rem;white-space:pre-wrap")

            picks = premark.get("picks", []) if isinstance(premark, dict) else []
            if picks:
                with ui.card().classes("q-pa-md w-full").style(f"background:{BG2}"):
                    ui.label("PRE-MARKET PICKS  (09:35 entries)").style(
                        f"color:{PRP};font-size:0.8rem;font-weight:700;font-family:monospace;margin-bottom:8px")
                    pcols = [{"name":"rank","label":"#","field":"rank"},
                             {"name":"sym","label":"Symbol","field":"sym","align":"left"},
                             {"name":"score","label":"Score","field":"score"},
                             {"name":"gap","label":"Gap%","field":"gap"},
                             {"name":"adx","label":"ADX","field":"adx"},
                             {"name":"dir","label":"Dir","field":"dir"}]
                    prows = [{"rank":i+1,"sym":p.get("symbol",""),"score":p.get("score",0),
                              "gap":f"{p.get('gap_pct',0):+.2f}%","adx":f"{p.get('adx',0):.0f}",
                              "dir":p.get("direction","—")} for i,p in enumerate(picks[:15])]
                    ui.table(columns=pcols, rows=prows, row_key="sym").classes("w-full").props(
                        "dense flat bordered")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SWARM
# ══════════════════════════════════════════════════════════════════════════════

SWARM_TASKS_FILE = ROOT / "swarm_tasks.json"
SWARM_LOG_FILE   = ROOT / "swarm_results" / "orchestrator.log"
SWARM_PAUSE_FILE = ROOT / "swarm_results" / "PAUSE"

@ui.refreshable
def page_swarm():
    # ── Header ────────────────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-sm items-center justify-between").style(
            f"background:{BG2};border-bottom:1px solid {BDR}"):
        ui.label("🐝  SWARM ORCHESTRATOR").style(
            f"color:{FG};font-size:1.3rem;font-weight:700;font-family:monospace")
        paused = SWARM_PAUSE_FILE.exists()
        def _toggle_pause():
            if SWARM_PAUSE_FILE.exists():
                SWARM_PAUSE_FILE.unlink()
                ui.notify("Swarm resumed", color="positive", timeout=2000)
            else:
                SWARM_PAUSE_FILE.touch()
                ui.notify("Swarm paused", color="warning", timeout=2000)
            page_swarm.refresh()
        ui.button("⏸ Pause" if not paused else "▶ Resume",
                  on_click=_toggle_pause).props(
            f"flat dense color={'warning' if not paused else 'positive'}")

    # ── Load state ────────────────────────────────────────────────────────────
    state = {}
    if SWARM_TASKS_FILE.exists():
        try:
            state = json.loads(SWARM_TASKS_FILE.read_text())
        except Exception:
            pass

    pending   = state.get("pending",   [])
    running   = state.get("running",   [])
    completed = state.get("completed", [])
    failed    = state.get("failed",    [])

    # ── Status bar ────────────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md gap-md"):
        for lbl, val, col in [
            ("PENDING",   str(len(pending)),   YLW),
            ("RUNNING",   str(len(running)),   BLU),
            ("COMPLETED", str(len(completed)), GRN),
            ("FAILED",    str(len(failed)),    RED),
        ]:
            with ui.card().classes("q-px-lg q-py-sm").style(f"background:{BG2}"):
                ui.label(lbl).style(f"color:{FG2};font-size:0.65rem;font-family:monospace")
                ui.label(val).style(f"color:{col};font-size:1.6rem;font-weight:700;font-family:monospace")

    paused_badge = ""
    if paused:
        with ui.row().classes("w-full q-px-lg q-pt-xs"):
            ui.badge("⏸  PAUSED", color="warning").style("font-family:monospace;font-size:0.85rem")

    # ── Running tasks ─────────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md gap-md flex-wrap q-pb-sm"):
        with ui.column().classes("gap-sm").style("flex:1;min-width:340px"):
            ui.label("RUNNING").style(
                f"color:{BLU};font-size:0.72rem;font-weight:700;font-family:monospace;"
                f"letter-spacing:2px;margin-bottom:4px")

            if running:
                for t in running:
                    with ui.card().classes("q-pa-md w-full").style(
                            f"background:{BG2};border-left:3px solid {BLU}"):
                        with ui.row().classes("w-full items-center justify-between"):
                            ui.label(t.get("title","?")).style(
                                f"color:{FG};font-family:monospace;font-size:0.82rem;font-weight:700")
                            ui.badge(f"W{t.get('worker_id','?')}", color="info").style("font-family:monospace")
                        started = t.get("started_at","")
                        if started:
                            try:
                                from datetime import timezone as _tz
                                dt = datetime.fromisoformat(started)
                                elapsed = int((datetime.now(_tz.utc) - dt).total_seconds())
                                elapsed_str = f"{elapsed//60}m {elapsed%60}s"
                            except Exception:
                                elapsed_str = "?"
                            ui.label(f"⏱ {elapsed_str} elapsed").style(
                                f"color:{FG2};font-family:monospace;font-size:0.72rem")
                        ui.label(f"#{t.get('id','?')}  •  priority {t.get('priority','?')}  •  {t.get('type','?')}").style(
                            f"color:{FG2};font-family:monospace;font-size:0.68rem")
            else:
                ui.label("No tasks running" + (" (paused)" if paused else "")).style(
                    f"color:{FG2};font-family:monospace;font-size:0.82rem")

        # ── Pending queue ─────────────────────────────────────────────────────
        with ui.column().classes("gap-sm").style("flex:1;min-width:340px"):
            ui.label(f"QUEUE  ({len(pending)} tasks)").style(
                f"color:{YLW};font-size:0.72rem;font-weight:700;font-family:monospace;"
                f"letter-spacing:2px;margin-bottom:4px")

            sorted_pending = sorted(pending, key=lambda t: t.get("priority", 99))
            for t in sorted_pending[:8]:
                type_col = {
                    "feature": BLU, "optimize": GRN, "bugfix": RED, "research": PRP
                }.get(t.get("type",""), FG2)
                with ui.row().classes("w-full items-center gap-sm q-py-xs").style(
                        f"border-bottom:1px solid {BDR}22"):
                    ui.label(f"#{t.get('priority','?'):>3}").style(
                        f"color:{FG2};font-family:monospace;font-size:0.72rem;min-width:30px")
                    ui.badge(t.get("type","?"), color="grey").style(
                        f"color:{type_col};font-family:monospace;font-size:0.65rem")
                    ui.label(t.get("title","?")).style(
                        f"color:{FG};font-family:monospace;font-size:0.78rem")

            if len(sorted_pending) > 8:
                ui.label(f"+ {len(sorted_pending)-8} more in queue").style(
                    f"color:{FG2};font-family:monospace;font-size:0.72rem;margin-top:4px")

    # ── Completed tasks ───────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-sm gap-md flex-wrap q-pb-lg"):
        with ui.column().classes("w-full gap-sm"):
            ui.label("RECENTLY COMPLETED").style(
                f"color:{GRN};font-size:0.72rem;font-weight:700;font-family:monospace;"
                f"letter-spacing:2px;margin-bottom:4px")

            for t in (completed[:8] + failed[:3]):
                ok = t.get("status") == "completed"
                col = GRN if ok else RED
                icon = "✓" if ok else "✗"
                dur = t.get("duration_sec")
                dur_str = f"{dur//60}m {dur%60}s" if dur else "?"

                # Try to load result summary
                summary = ""
                rf = t.get("result_file")
                if rf:
                    try:
                        rdata = json.loads(Path(rf).read_text())
                        summary = rdata.get("summary", "")[:120]
                    except Exception:
                        pass

                with ui.card().classes("q-pa-md w-full").style(
                        f"background:{BG2};border-left:3px solid {col}"):
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.row().classes("items-center gap-sm"):
                            ui.label(icon).style(f"color:{col};font-size:1.0rem;font-weight:700")
                            ui.label(t.get("title","?")).style(
                                f"color:{FG};font-family:monospace;font-size:0.82rem;font-weight:700")
                        ui.label(dur_str).style(
                            f"color:{FG2};font-family:monospace;font-size:0.72rem")
                    if summary:
                        ui.label(summary).style(
                            f"color:{FG2};font-family:monospace;font-size:0.72rem;"
                            f"margin-top:4px;white-space:pre-wrap")
                    completed_at = t.get("completed_at","")
                    if completed_at:
                        try:
                            dt = datetime.fromisoformat(completed_at)
                            ts = dt.strftime("%Y-%m-%d %H:%M UTC")
                        except Exception:
                            ts = completed_at[:19]
                        ui.label(ts).style(f"color:{BDR};font-family:monospace;font-size:0.65rem;margin-top:2px")

    # ── Live log tail ─────────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pb-lg"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            ui.label("ORCHESTRATOR LOG  (last 20 lines)").style(
                f"color:{FG2};font-size:0.65rem;font-weight:700;font-family:monospace;"
                f"letter-spacing:2px;margin-bottom:8px")
            log_lines = []
            if SWARM_LOG_FILE.exists():
                try:
                    all_lines = SWARM_LOG_FILE.read_text().splitlines()
                    log_lines = all_lines[-20:]
                except Exception:
                    pass
            log_text = "\n".join(log_lines) if log_lines else "Orchestrator not started yet."
            ui.label(log_text).style(
                f"color:{FG};font-family:monospace;font-size:0.72rem;"
                f"white-space:pre-wrap;line-height:1.5")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

@ui.refreshable
def page_settings():
    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-sm items-center").style(
            f"background:{BG2};border-bottom:1px solid {BDR}"):
        ui.label("⚙  SETTINGS").style(
            f"color:{FG};font-size:1.3rem;font-weight:700;font-family:monospace")

    with ui.row().classes("w-full q-px-lg q-pt-md gap-md flex-wrap"):
        # ── Display settings ───────────────────────────────────────────────────
        with ui.card().classes("q-pa-lg").style(
                f"background:{BG2};min-width:300px;flex:1"):
            ui.label("DISPLAY").style(
                f"color:{FG2};font-size:0.7rem;font-weight:700;font-family:monospace;"
                f"letter-spacing:2px;margin-bottom:14px")

            with ui.row().classes("w-full items-center justify-between q-py-sm").style(
                    f"border-bottom:1px solid {BDR}"):
                with ui.column().classes("gap-none"):
                    ui.label("Hover Explanations").style(
                        f"color:{FG};font-family:monospace;font-size:0.9rem")
                    ui.label("Tooltips on all buttons, metrics and strategy cards").style(
                        f"color:{FG2};font-family:monospace;font-size:0.72rem")
                def _toggle_tips(e):
                    _SETTINGS["tooltips"] = e.value
                    ui.notify(
                        f"Tooltips {'enabled' if e.value else 'disabled'}",
                        color="info", timeout=1500)
                ui.switch("", value=_SETTINGS.get("tooltips", True),
                          on_change=_toggle_tips).props("color=positive")

            with ui.row().classes("w-full items-center justify-between q-py-sm").style(
                    f"border-bottom:1px solid {BDR}"):
                with ui.column().classes("gap-none"):
                    ui.label("Auto-Refresh Interval").style(
                        f"color:{FG};font-family:monospace;font-size:0.9rem")
                    ui.label("Dashboard polls Alpaca every 5 seconds").style(
                        f"color:{FG2};font-family:monospace;font-size:0.72rem")
                ui.badge("5 s", color="info").style("font-family:monospace;font-size:0.82rem")

            with ui.row().classes("w-full items-center justify-between q-py-sm").style(
                    f"border-bottom:1px solid {BDR}"):
                with ui.column().classes("gap-none"):
                    ui.label("QC Rankings Refresh").style(
                        f"color:{FG};font-family:monospace;font-size:0.9rem")
                    ui.label("QuantConnect leaderboard polled every 60 s").style(
                        f"color:{FG2};font-family:monospace;font-size:0.72rem")
                ui.badge("60 s", color="info").style("font-family:monospace;font-size:0.82rem")

            with ui.row().classes("w-full items-center justify-between q-py-sm"):
                with ui.column().classes("gap-none"):
                    ui.label("Regime Detection Refresh").style(
                        f"color:{FG};font-family:monospace;font-size:0.9rem")
                    ui.label("SPY/VIX regime recalculated every ~6 min").style(
                        f"color:{FG2};font-family:monospace;font-size:0.72rem")
                ui.badge("6 min", color="info").style("font-family:monospace;font-size:0.82rem")

        # ── Active account ──────────────────────────────────────────────────────
        with ui.card().classes("q-pa-lg").style(
                f"background:{BG2};min-width:300px;flex:1"):
            ui.label("ACTIVE ACCOUNT").style(
                f"color:{FG2};font-size:0.7rem;font-weight:700;font-family:monospace;"
                f"letter-spacing:2px;margin-bottom:14px")
            for lbl, val in [
                ("Account ID",   ACTIVE_ACCT),
                ("Endpoint",     "broker-api.sandbox.alpaca.markets"),
                ("Auth Method",  "Basic (BROKER_KEY:BROKER_SECRET)"),
                ("Key Storage",  "macOS Keychain (never in source)"),
            ]:
                with ui.row().classes("w-full items-start gap-md q-py-xs").style(
                        f"border-bottom:1px solid {BDR}22"):
                    ui.label(lbl).style(
                        f"color:{FG2};font-family:monospace;font-size:0.75rem;"
                        f"min-width:110px;flex-shrink:0")
                    ui.label(val).style(
                        f"color:{FG};font-family:monospace;font-size:0.75rem;"
                        f"word-break:break-all")

    # ── QC config summary ─────────────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md gap-md q-pb-lg"):
        with ui.card().classes("w-full q-pa-lg").style(f"background:{BG2}"):
            ui.label("QC PORTFOLIO CONFIG").style(
                f"color:{FG2};font-size:0.7rem;font-weight:700;font-family:monospace;"
                f"letter-spacing:2px;margin-bottom:14px")
            with ui.row().classes("w-full gap-sm flex-wrap"):
                for lbl, val in [
                    ("Top N Strategies",   "5"),
                    ("Rebalance Time",     "09:31 ET"),
                    ("Trailing Stop",      "5%"),
                    ("Strategy DD Limit",  "10%"),
                    ("Portfolio DD Limit", "15%"),
                    ("Min Rebalance $",    "$50"),
                    ("Min Drift",          "1%"),
                    ("Dashboard Version",  "2.0.0"),
                ]:
                    with ui.column().classes("gap-none items-center q-pa-sm").style(
                            f"background:{BG3};border-radius:8px;min-width:110px"):
                        ui.label(lbl).style(
                            f"color:{FG2};font-size:0.62rem;font-family:monospace;text-align:center")
                        ui.label(val).style(
                            f"color:{FG};font-weight:700;font-family:monospace;font-size:0.9rem")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: WHALE SIGNALS  (on-chain smart-money accumulation feed)
# ══════════════════════════════════════════════════════════════════════════════
_WHALE_DIR   = Path("/Users/quarki3/whale_tracker")
_WHALE_STATE = _WHALE_DIR / "state"
_SCANNER_STATE = Path("/Users/quarki3/scanner/state")


def _scanner_signals(limit: int = 60) -> list[str]:
    """Read scanner/state/signals.jsonl + live_activity.jsonl and format as
    log-style lines the app already knows how to render.

    Ranked:
      1. ENTRY signals (fresh action)
      2. EXIT signals
      3. LIVE activity (per-tick pulse of what each source is doing)
      4. Other signals (funding, gainers, whale trades, etc.)
    """
    feed = _SCANNER_STATE / "signals.jsonl"
    live = _SCANNER_STATE / "live_activity.jsonl"
    import time as _t

    entries, exits, others = [], [], []
    if feed.exists():
        try:
            lines = feed.read_text().splitlines()[-limit * 8:]
        except Exception:
            lines = []
        for ln in reversed(lines):
            try:
                row = json.loads(ln)
            except Exception:
                continue
            ts_str = _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime(row.get("ts", 0)))
            sym    = row.get("symbol", "?")
            chain  = row.get("chain", "?")
            stype  = row.get("signal_type", "?")
            src    = row.get("source", "?")
            usd    = row.get("size_usd", 0) or 0
            note   = row.get("note", "")
            line = f"[{ts_str}] alert: {sym} {chain} wallets={row.get('wallets', 0)} agg_usd={usd} | {src}/{stype} — {note}"
            if stype == "ENTRY":
                entries.append(line)
            elif stype == "EXIT":
                exits.append(line)
            else:
                others.append(line)

    live_lines = []
    if live.exists():
        try:
            rows = live.read_text().splitlines()[-40:]
        except Exception:
            rows = []
        for ln in reversed(rows):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            ts_str = _t.strftime("%Y-%m-%d %H:%M:%S", _t.gmtime(r.get("ts", 0)))
            src    = r.get("source", "?")
            act    = r.get("activity", "")
            # Reuse the same alert-line shape so the Android UI renders it.
            live_lines.append(
                f"[{ts_str}] alert: LIVE {src} wallets=0 agg_usd=0 | {src}/LIVE — {act}")

    return (entries + exits + live_lines + others)[:limit]


def _whale_status() -> dict:
    """Read runner state without hitting Dune. Cheap — all local file I/O.
    Now folds in scanner/state/signals.jsonl so the app shows every source."""
    seen_file   = _WHALE_STATE / "seen.json"
    log_file    = _WHALE_STATE / "runner.log"
    dune_env    = _WHALE_DIR / "dune.env"
    ether_env   = _WHALE_DIR / "etherscan.env"

    seen_count = 0
    try:
        seen_count = len(json.loads(seen_file.read_text())) if seen_file.exists() else 0
    except Exception:
        pass

    last_tick, last_line, alerts = "", "", []
    if log_file.exists():
        try:
            lines = log_file.read_text().splitlines()[-500:]
            for ln in reversed(lines):
                if not last_tick and "tick:" in ln:
                    last_tick = ln
                if ln.startswith("[") and "alert:" in ln:
                    alerts.append(ln)
                if last_tick and len(alerts) >= 30:
                    break
            last_line = lines[-1] if lines else ""
        except Exception:
            pass

    # Fold in scanner signals — they use the same alert-line shape
    scanner_alerts = _scanner_signals(limit=60)
    # Scanner-seen count = number of distinct dedup keys tracked
    scanner_seen = _SCANNER_STATE / "seen.json"
    scanner_seen_count = 0
    try:
        if scanner_seen.exists():
            scanner_seen_count = len(json.loads(scanner_seen.read_text()))
    except Exception:
        pass

    return {
        "seen_count":   seen_count + scanner_seen_count,
        "last_tick":    (scanner_alerts[0] if scanner_alerts else last_tick),
        "last_line":    last_line,
        # Scanner alerts first (fresher/broader), then Q01 alerts
        "alerts":       (scanner_alerts + alerts)[:60],
        "dune_configured":      dune_env.exists()  and "DUNE_API_KEY" in dune_env.read_text(),
        "etherscan_configured": ether_env.exists() and "ETHERSCAN_API_KEY" in ether_env.read_text(),
    }


def _run_etherscan_feed_bg(output_holder: dict, refresh_fn) -> None:
    """Run etherscan_feed.py in a subprocess, stream output into the holder."""
    output_holder["running"] = True
    output_holder["lines"]   = ["$ python3 etherscan_feed.py --hours 6 --limit 40 --sort size", ""]
    refresh_fn()
    try:
        proc = subprocess.Popen(
            ["python3", str(_WHALE_DIR / "etherscan_feed.py"),
             "--hours", "6", "--limit", "40", "--sort", "size"],
            cwd=str(_WHALE_DIR),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        for ln in iter(proc.stdout.readline, ""):
            output_holder["lines"].append(ln.rstrip())
            output_holder["lines"] = output_holder["lines"][-600:]
        proc.wait()
        output_holder["lines"].append(f"\n[exit code {proc.returncode}]")
    except Exception as e:
        output_holder["lines"].append(f"[error] {e}")
    finally:
        output_holder["running"] = False
        refresh_fn()


_WHALE_FEED_OUT = {"running": False, "lines": []}


@ui.refreshable
def page_whales():
    st = _whale_status()

    with ui.row().classes("w-full q-px-lg q-pt-md items-center gap-md"):
        ui.label("🐳  WHALE SIGNALS").style(
            f"color:{PRP};font-size:1.3rem;font-weight:700;font-family:monospace")
        ui.space()
        ui.label("On-chain smart-money accumulation").style(
            f"color:{FG2};font-size:0.72rem;font-family:monospace;letter-spacing:1px")

    # ── Config / status strip ───────────────────────────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-sm gap-sm").style("flex-wrap:wrap"):
        _dune_col = GRN if st["dune_configured"] else RED
        _eth_col  = GRN if st["etherscan_configured"] else RED
        for label, val, col, sub in [
            ("CUMULATIVE HITS", str(st["seen_count"]),                       BLU,      "unique tokens flagged"),
            ("DUNE API",        "wired" if st["dune_configured"] else "not set",     _dune_col, "Q01 coordinated-accum poll"),
            ("ETHERSCAN API",   "wired" if st["etherscan_configured"] else "not set", _eth_col,  "curated-wallet transfer feed"),
        ]:
            with ui.card().classes("q-pa-md").style(
                    f"background:{BG2};border-left:3px solid {col};flex:1 1 200px;min-width:180px"):
                ui.label(label).style(
                    f"color:{FG2};font-size:0.65rem;font-family:monospace;letter-spacing:1.5px")
                ui.label(val).style(
                    f"color:{col};font-size:1.1rem;font-weight:700;font-family:monospace")
                ui.label(sub).style(
                    f"color:{FG2};font-size:0.62rem;font-family:monospace;opacity:0.7")

    # ── Recent coordinated-accumulation alerts (Q01) ────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            with ui.row().classes("w-full items-center"):
                ui.label("Q01 · COORDINATED ACCUMULATION ALERTS").style(
                    f"color:{FG2};font-size:0.75rem;font-weight:700;font-family:monospace;letter-spacing:1.5px")
                ui.space()
                if st["last_tick"]:
                    ui.label(st["last_tick"][:19].strip("[]")).style(
                        f"color:{FG2};font-size:0.65rem;font-family:monospace;opacity:0.7")
            if not st["alerts"]:
                with ui.column().classes("w-full items-center q-py-md"):
                    ui.label("○ No alerts yet").style(
                        f"color:{FG2};font-size:0.9rem;font-family:monospace;opacity:0.6")
                    ui.label(st["last_line"] or "Runner hasn't posted a tick yet").style(
                        f"color:{FG2};font-size:0.68rem;font-family:monospace;opacity:0.4;margin-top:4px")
            else:
                for line in st["alerts"]:
                    ui.label(line).style(
                        f"color:{FG};font-family:monospace;font-size:0.75rem;"
                        f"padding:3px 8px;border-left:2px solid {PRP};margin-top:2px")

    # ── Etherscan curated-wallet feed (on demand) ───────────────────────────
    with ui.row().classes("w-full q-px-lg q-pt-md"):
        with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
            with ui.row().classes("w-full items-center"):
                ui.label("CURATED-WALLET LIVE FEED (ETHERSCAN)").style(
                    f"color:{FG2};font-size:0.75rem;font-weight:700;font-family:monospace;letter-spacing:1.5px")
                ui.space()

                def _kick_feed():
                    if _WHALE_FEED_OUT["running"]:
                        return
                    threading.Thread(
                        target=_run_etherscan_feed_bg,
                        args=(_WHALE_FEED_OUT, page_whales.refresh),
                        daemon=True,
                    ).start()
                    page_whales.refresh()

                btn_label = "Running…" if _WHALE_FEED_OUT["running"] else "▶ Run feed (6h)"
                run_btn = ui.button(btn_label, on_click=_kick_feed).props(
                    "flat dense color=primary")
                if _WHALE_FEED_OUT["running"]:
                    run_btn.disable()

            if _WHALE_FEED_OUT["lines"]:
                out_text = "\n".join(_WHALE_FEED_OUT["lines"])
                ui.code(out_text, language="text").classes("w-full").style(
                    f"background:{BG};color:{FG};font-size:0.68rem;max-height:340px;"
                    f"overflow-y:auto;padding:8px;border-radius:4px")
            else:
                ui.label(
                    "Runs etherscan_feed.py against the 6 curated MM/VC wallets "
                    "(Wintermute · Jump · a16z · Justin Sun · Vitalik ×2), "
                    "aggregates by token, flags recurring counterparties."
                ).style(f"color:{FG2};font-size:0.72rem;font-family:monospace;"
                        f"opacity:0.7;line-height:1.6")

    # ── Alpaca crypto auto-trade hook (stub for parallel session) ───────────
    with ui.row().classes("w-full q-px-lg q-pt-md q-pb-lg"):
        with ui.card().classes("w-full q-pa-md").style(
                f"background:{BG2};border-left:3px solid {YLW}"):
            ui.label("↔  ALPACA CRYPTO AUTO-TRADE").style(
                f"color:{YLW};font-size:0.75rem;font-weight:700;font-family:monospace;letter-spacing:1.5px")
            ui.label(
                "Signal → order routing will wire in here once the Alpaca "
                "crypto integration lands. For now the buttons below are stubs."
            ).style(f"color:{FG2};font-size:0.72rem;font-family:monospace;"
                    f"opacity:0.75;line-height:1.5;margin-top:4px")
            with ui.row().classes("q-mt-sm gap-sm"):
                auto = ui.button("Enable auto-buy on Q01 hits").props(
                    "flat dense color=warning")
                auto.disable()
                _tip(auto, "Waiting on Alpaca crypto integration (other session)")
                man = ui.button("Manual: buy top Q01 token").props(
                    "flat dense color=warning")
                man.disable()
                _tip(man, "Waiting on Alpaca crypto integration (other session)")

    ui.timer(30, page_whales.refresh)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN APP
# ══════════════════════════════════════════════════════════════════════════════

def _build_nav_item(icon: str, label: str, key: str, current: dict, on_nav=None):
    active = current["page"] == key
    col    = BLU  if active else FG2
    bg     = f"{BLU}18" if active else "transparent"
    bdr    = f"border-left:3px solid {BLU}" if active else f"border-left:3px solid transparent"
    wt     = "700" if active else "400"
    def _go():
        current["page"] = key
        if on_nav:
            on_nav()
    with ui.row().classes("w-full items-center gap-sm q-px-md q-py-sm").style(
            f"background:{bg};{bdr};border-radius:4px;margin:1px 4px;"
            f"cursor:pointer;transition:background 0.12s").on("click", _go):
        ui.label(icon).style("font-size:1.0rem;min-width:20px;line-height:1")
        ui.label(label).style(
            f"color:{col};font-family:monospace;font-size:0.88rem;font-weight:{wt}")


@ui.page("/")
def main():
    # ── PWA manifest + static assets ─────────────────────────────────────────
    _app.add_static_files("/static", Path(__file__).parent / "static")
    ui.add_head_html("""
    <link rel="manifest" href="/static/manifest.json">
    <link rel="apple-touch-icon" href="/static/icon-192.png">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="Trading">
    <meta name="theme-color" content="#00d4ff">
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
    """)

    ui.add_head_html(f"""
    <style>
      body, .q-page {{ background:{BG} !important; }}
      .q-layout__shadow {{ display:none !important; }}
      .q-header   {{ background:{BG2} !important; border-bottom:1px solid {BDR}; }}
      .q-drawer   {{ background:{BG2} !important; border-right:1px solid {BDR}; }}
      .q-table    {{ background:{BG2} !important; }}
      .q-table th {{ background:{BG3} !important; color:{FG2} !important;
                     font-family:monospace; font-size:0.78rem; font-weight:700;
                     border-bottom:1px solid {BDR} !important; }}
      .q-table td {{ color:{FG} !important; font-family:monospace; font-size:0.82rem;
                     border-bottom:1px solid {BDR}22 !important; }}
      .q-table tbody tr:hover {{ background:{BG3} !important; }}
      .q-scrollarea__content {{ background:{BG} !important; }}
      .q-card      {{ border:1px solid {BDR} !important; border-radius:8px !important; }}
      .q-separator {{ background:{BDR} !important; }}
      .nicegui-content {{ padding:0 !important; background:{BG} !important; }}
      ::-webkit-scrollbar       {{ width:6px; height:6px; }}
      ::-webkit-scrollbar-track {{ background:{BG2}; }}
      ::-webkit-scrollbar-thumb {{ background:{BDR}; border-radius:3px; }}

      /* ── Mobile responsive ─────────────────────────────────────────────── */
      @media (max-width: 768px) {{
        .q-px-lg {{ padding-left: 8px !important; padding-right: 8px !important; }}
        .q-px-md {{ padding-left: 6px !important; padding-right: 6px !important; }}
        .q-pt-md {{ padding-top: 8px !important; }}
        .gap-md   {{ gap: 8px !important; }}
        .gap-lg   {{ gap: 10px !important; }}
        .q-card {{ min-width: 0 !important; }}
        .q-table__container {{ overflow-x: auto !important; -webkit-overflow-scrolling: touch; }}
        .q-table {{ min-width: 420px; }}
        .q-table td, .q-table th {{ font-size: 0.72rem !important; padding: 4px 6px !important; }}
        .js-plotly-plot {{ touch-action: pan-y !important; }}
      }}
      @media (max-width: 480px) {{
        .q-table {{ min-width: 360px; }}
        .q-table td, .q-table th {{ font-size: 0.65rem !important; }}
      }}
    </style>
    """)

    current = {"page": "accounts"}

    # ── Header ────────────────────────────────────────────────────────────────
    with ui.header(elevated=False).classes("items-center q-px-md").style(
            f"background:{BG2};border-bottom:1px solid {BDR};min-height:48px"):
        ui.button(icon="menu", on_click=lambda: drawer.toggle()).props(
            "flat dense round color=grey-5")
        ui.label("📊  Trading Dashboard").style(
            f"color:{BLU};font-size:1rem;font-weight:700;font-family:monospace;margin-left:8px")
        ui.space()

        # Live account equity in header
        eq_lbl    = ui.label("$—").style(
            f"color:{FG};font-family:monospace;font-weight:700;margin-right:8px")
        pl_lbl    = ui.label("—").style(
            f"color:{FG2};font-family:monospace;font-size:0.85rem")
        ui.separator().props("vertical").style("margin:0 8px")
        clock_lbl = ui.label("").style(f"color:{FG2};font-family:monospace;font-size:0.85rem")
        scan_lbl  = ui.label("◉ SCANNING").style(
            f"color:{GRN};font-family:monospace;font-size:0.72rem;margin-left:6px")

        _tick_state = {"frame": 0}

        def _tick():
            now = datetime.now(ET)
            clock_lbl.set_text(now.strftime("%H:%M:%S ET"))

            # Equity / P&L from cache (updated every 5s by background thread)
            acct2  = _cache.get("account", {})
            eq     = _f(acct2.get("equity", 0))
            pl_raw = acct2.get("unrealized_pl")
            pl     = _f(pl_raw) if pl_raw is not None else sum(
                _f(p.get("unrealized_pl")) for p in _cache.get("positions", []))
            eq_lbl.set_text(f"${eq:,.2f}")
            pl_lbl.set_text(_fd(pl))
            pl_lbl.style(f"color:{_pc(pl)};font-family:monospace;font-size:0.85rem")

            # Live scan pulse: rotates through symbols to show it's alive
            _tick_state["frame"] += 1
            pulse = ["◉", "◎", "◉", "◎"][_tick_state["frame"] % 4]
            last  = _cache.get("last_refresh")
            if last:
                age = (now - last).total_seconds()
                scan_lbl.set_text(f"{pulse} {age:.0f}s ago")
                col = GRN if age < 10 else (YLW if age < 20 else RED)
            else:
                scan_lbl.set_text(f"{pulse} SCANNING")
                col = BLU
            scan_lbl.style(f"color:{col};font-family:monospace;font-size:0.72rem;margin-left:6px")

        ui.timer(1.0, _tick)

    # ── Left drawer — always open on desktop ─────────────────────────────────
    with ui.left_drawer(value=False, bordered=True, fixed=True).style(
            f"background:{BG2};border-right:1px solid {BDR};width:200px !important;"
            ).props(':breakpoint="768"') as drawer:

        ui.label("TRADING").style(
            f"color:{FG2};font-size:0.65rem;font-family:monospace;letter-spacing:2px;"
            f"margin:16px 16px 8px")

        NAV = [
            ("🏦", "All Accounts",  "accounts"),
            ("🏆", "QC → Alpaca",   "qc"),
            ("🧪", "A/B Test",      "compare"),
            ("🚀", "Deploy",        "deploy"),
            ("📈", "Regime",        "regime"),
            ("📊", "Portfolio",     "portfolio"),
            ("🤖", "Bot Controls",  "bot"),
            ("📋", "Daily Review",  "review"),
            ("🐝", "Swarm",         "swarm"),
            ("🐳", "Whales",        "whales"),
        ]
        for icon, label, key in NAV:
            _build_nav_item(icon, label, key, current,
                            on_nav=lambda: _main_content.refresh())

        ui.separator().style(f"background:{BDR};margin:8px 8px")
        _build_nav_item("⚙", "Settings", "settings", current,
                        on_nav=lambda: _main_content.refresh())

        ui.separator().style(f"background:{BDR};margin:12px 8px")
        ui.label("ACCOUNT").style(
            f"color:{FG2};font-size:0.65rem;font-family:monospace;letter-spacing:2px;margin:4px 16px 8px")

        # Live mini stats in drawer
        @ui.refreshable
        def _drawer_stats():
            acct = _cache.get("account", {})
            positions = _cache.get("positions", [])
            eq  = _f(acct.get("equity", 0))
            pl  = _f(acct.get("unrealized_pl", 0))
            with ui.column().classes("w-full q-px-md gap-none"):
                for lbl, val, col in [
                    ("Equity",   f"${eq:,.0f}",         FG),
                    ("P&L",      _fd(pl),               _pc(pl)),
                    ("Positions",str(len(positions)),    BLU),
                ]:
                    with ui.row().classes("w-full justify-between"):
                        ui.label(lbl).style(f"color:{FG2};font-size:0.75rem;font-family:monospace")
                        ui.label(val).style(f"color:{col};font-size:0.8rem;font-weight:700;font-family:monospace")

        _drawer_stats()
        ui.timer(15.0, _drawer_stats.refresh)

        ui.separator().style(f"background:{BDR};margin:12px 8px")
        ui.label("VERSION  1.0").style(
            f"color:{BDR};font-size:0.6rem;font-family:monospace;margin:4px 16px")

    # ── Main content ──────────────────────────────────────────────────────────
    @ui.refreshable
    def _main_content():
        with ui.scroll_area().classes("w-full").style(
                f"background:{BG};height:calc(100vh - 48px)"):
            p = current["page"]
            if   p == "accounts":  page_accounts()
            elif p == "qc":        page_qc()
            elif p == "compare":   page_compare()
            elif p == "deploy":    page_deploy()
            elif p == "regime":    page_regime()
            elif p == "portfolio": page_portfolio()
            elif p == "bot":       page_bot()
            elif p == "review":    page_review()
            elif p == "swarm":     page_swarm()
            elif p == "whales":    page_whales()
            elif p == "settings":  page_settings()

    _main_content()

    # ── Global page-refresh timers (outside all refreshables) ─────────────────
    def _auto_refresh():
        p = current["page"]
        if   p == "accounts":  page_accounts.refresh()
        elif p == "qc":        page_qc.refresh()
        elif p == "compare":   page_compare.refresh()
        elif p == "deploy":    page_deploy.refresh()
        elif p == "regime":    page_regime.refresh()
        elif p == "portfolio": page_portfolio.refresh()
        elif p == "bot":       page_bot.refresh()
        elif p == "review":    page_review.refresh()
        elif p == "swarm":     page_swarm.refresh()
        elif p == "whales":    page_whales.refresh()
        elif p == "settings":  page_settings.refresh()

    ui.timer(2.0, _auto_refresh, once=True)    # immediate first render after data loads
    ui.timer(5.0, _auto_refresh)               # live scan every 5 seconds


# ── JSON REST API for the Android app ────────────────────────────────────────
# Mount after every helper in this module is defined so api.py can reach them
# (_alp, _paper, _shadow, _cache, etc) via the imported module reference.
import sys as _sys
try:
    import api as _api          # type: ignore
    _api.mount(_app, _sys.modules[__name__])
    print("[API] /api/* mounted")
except Exception as _e:
    print(f"[API] mount failed: {_e}")

# ── Whale Tracker endpoint (mounted directly on _app to avoid collision with
# whichever session is editing api.py). Android app polls this for the Whales
# screen. Read-only — reads local whale_tracker state files, no external calls.
try:
    def _api_whales():
        st = _whale_status()

        parsed_alerts = []
        for ln in st["alerts"]:
            # Log lines look like: "[2026-07-02 15:04:26] alert: SYM chain wallets=N agg_usd=X"
            try:
                ts_end = ln.index("]")
                ts = ln[1:ts_end]
                body = ln[ts_end + 1:].strip()
                after_alert = body.split("alert:", 1)[1].strip() if "alert:" in body else body
                parts = after_alert.split()
                sym = parts[0] if parts else "?"
                chain = parts[1] if len(parts) > 1 else "?"
                wallets = 0
                agg_usd = 0.0
                for tok in parts[2:]:
                    if tok.startswith("wallets="):
                        try:    wallets = int(tok.split("=", 1)[1])
                        except: pass
                    elif tok.startswith("agg_usd="):
                        try:    agg_usd = float(tok.split("=", 1)[1])
                        except: pass
                parsed_alerts.append({
                    "ts":       ts,
                    "symbol":   sym,
                    "chain":    chain,
                    "wallets":  wallets,
                    "agg_usd":  agg_usd,
                    "raw":      ln,
                })
            except Exception:
                parsed_alerts.append({
                    "ts": "", "symbol": "?", "chain": "?", "wallets": 0,
                    "agg_usd": 0.0, "raw": ln,
                })

        # Whale realized P&L — pull whale_primary strategy rows from prop_ledger
        whale_pnl = None
        whale_started_ts = None
        try:
            ledger_p = Path("/Users/quarki3/scanner/state/prop_ledger.jsonl")
            if ledger_p.exists():
                total = 0.0; n = 0
                for ln in ledger_p.read_text().splitlines():
                    try:
                        r = json.loads(ln)
                        if r.get("strategy") == "whale_primary":
                            total += r.get("pnl_pct", 0); n += 1
                            if whale_started_ts is None or r.get("entry_ts", 0) < whale_started_ts:
                                whale_started_ts = r.get("entry_ts")
                    except Exception:
                        pass
                whale_pnl = round(total, 2) if n > 0 else 0.0
            # Fall back to the whale runner's own start if no trades yet
            if whale_started_ts is None:
                whale_state = Path("/Users/quarki3/whale_tracker/state/runner.log")
                if whale_state.exists():
                    import os
                    whale_started_ts = int(whale_state.stat().st_mtime)
        except Exception:
            pass

        return {
            "ts":                    datetime.now(ET).isoformat(),
            "seen_count":            st["seen_count"],
            "dune_configured":       st["dune_configured"],
            "etherscan_configured":  st["etherscan_configured"],
            "last_tick":             st["last_tick"],
            "last_line":             st["last_line"],
            "alerts":                parsed_alerts,
            "pnl_pct":               whale_pnl,
            "started_ts":            whale_started_ts,
        }
    _app.get("/api/whales")(_api_whales)
    print("[API] /api/whales mounted")
except Exception as _e:
    print(f"[API] whales mount failed: {_e}")


# ── Prop-Desk Live endpoint ──────────────────────────────────────────────────
try:
    _PROP_STATE = Path("/Users/quarki3/scanner/state/prop_state.json")
    _PROP_LEDGER = Path("/Users/quarki3/scanner/state/prop_ledger.jsonl")

    def _api_prop_desk():
        st = {"error": "no state yet"}
        try:
            if _PROP_STATE.exists():
                st = json.loads(_PROP_STATE.read_text())
        except Exception as e:
            st = {"error": str(e)}
        # Ledger summary
        n_trades = 0; total = 0.0
        by_strat: dict = {}
        try:
            if _PROP_LEDGER.exists():
                for ln in _PROP_LEDGER.read_text().splitlines():
                    r = json.loads(ln)
                    n_trades += 1
                    total += r.get("pnl_pct", 0)
                    s = r.get("strategy", "?")
                    by_strat.setdefault(s, [0, 0.0])
                    by_strat[s][0] += 1
                    by_strat[s][1] += r.get("pnl_pct", 0)
        except Exception:
            pass
        st["ledger"] = {
            "n_trades":  n_trades,
            "total_pnl": round(total, 2),
            "mean_pnl":  round(total / n_trades, 3) if n_trades else 0.0,
            "by_strategy": {k: {"n": v[0], "total": round(v[1], 2)}
                             for k, v in by_strat.items()},
        }
        return st

    _app.get("/api/prop_desk/state")(_api_prop_desk)
    print("[API] /api/prop_desk/state mounted")
except Exception as _e:
    print(f"[API] prop_desk mount failed: {_e}")


# ── Market pulse (SPY 1D change — used by the Charts Lab card) ─────────────
try:
    import os as _os
    def _api_market_pulse():
        import urllib.request as _ur, urllib.parse as _up
        pk = _os.environ.get("ALPACA_PAPER_KEY", "")
        sk = _os.environ.get("ALPACA_PAPER_SECRET", "")
        if not pk:
            return {"error": "no alpaca key", "pnl_pct": None}
        try:
            url = "https://data.alpaca.markets/v2/stocks/SPY/snapshot"
            req = _ur.Request(url, headers={
                "APCA-API-KEY-ID": pk, "APCA-API-SECRET-KEY": sk,
            })
            r = _ur.urlopen(req, timeout=6)
            j = json.loads(r.read().decode())
            snap = j.get("snapshot") or j
            bar = snap.get("dailyBar") or {}
            prev = snap.get("prevDailyBar") or {}
            close = float(bar.get("c") or 0)
            prev_close = float(prev.get("c") or 0)
            pnl_pct = ((close - prev_close) / prev_close * 100) if prev_close else 0.0
            # Session started at today's open (t field is ISO ts of the daily bar)
            started_ts = None
            try:
                from datetime import datetime as _dt
                t = bar.get("t")
                if t:
                    started_ts = int(_dt.fromisoformat(t.replace("Z", "+00:00")).timestamp())
            except Exception:
                pass
            return {
                "symbol": "SPY",
                "close": round(close, 2),
                "prev_close": round(prev_close, 2),
                "pnl_pct": round(pnl_pct, 3),
                "started_ts": started_ts,
            }
        except Exception as _e:
            return {"error": str(_e), "pnl_pct": None}
    _app.get("/api/market_pulse")(_api_market_pulse)
    print("[API] /api/market_pulse mounted")
except Exception as _e:
    print(f"[API] market_pulse mount failed: {_e}")


# ── Company profile (yfinance) ──────────────────────────────────────────────
try:
    def _api_company(ticker: str):
        try:
            import yfinance as yf
        except Exception as e:
            return {"error": f"yfinance not installed: {e}"}
        t = yf.Ticker(ticker.upper())
        try:
            info = t.info or {}
        except Exception:
            info = {}
        # News: normalize yfinance's new nested "content" shape
        news_out = []
        try:
            for n in (t.news or [])[:15]:
                c = n.get("content") or n
                url = ((c.get("canonicalUrl") or {}).get("url")
                       or (c.get("clickThroughUrl") or {}).get("url")
                       or c.get("link", ""))
                thumb = None
                thumb_o = c.get("thumbnail") or {}
                if isinstance(thumb_o, dict):
                    res = thumb_o.get("resolutions") or []
                    if res:
                        thumb = (res[0] or {}).get("url")
                news_out.append({
                    "title":       c.get("title") or n.get("title", ""),
                    "publisher":   c.get("publisher") or (c.get("provider") or {}).get("displayName", ""),
                    "url":         url,
                    "pub_date":    c.get("pubDate") or c.get("providerPublishTime", ""),
                    "thumbnail":   thumb,
                })
        except Exception:
            pass
        # Chart data: last 30 days daily close
        chart_out = []
        try:
            hist = t.history(period="1mo", interval="1d")
            for ts, row in hist.iterrows():
                chart_out.append({
                    "ts":    int(ts.timestamp()),
                    "close": round(float(row["Close"]), 4),
                    "high":  round(float(row["High"]), 4),
                    "low":   round(float(row["Low"]), 4),
                    "volume": int(row["Volume"]),
                })
        except Exception:
            pass
        # Extract key stats
        def _g(k, d=None):
            v = info.get(k, d)
            if v is None or (isinstance(v, float) and v != v):  # NaN check
                return d
            return v
        return {
            "ticker":     ticker.upper(),
            "name":       _g("longName") or _g("shortName") or ticker.upper(),
            "sector":     _g("sector"),
            "industry":   _g("industry"),
            "country":    _g("country"),
            "website":    _g("website"),
            "summary":    _g("longBusinessSummary"),
            "employees":  _g("fullTimeEmployees"),
            # Price / trading
            "price":      _g("currentPrice") or _g("regularMarketPrice"),
            "prev_close": _g("previousClose") or _g("regularMarketPreviousClose"),
            "day_high":   _g("dayHigh") or _g("regularMarketDayHigh"),
            "day_low":    _g("dayLow")  or _g("regularMarketDayLow"),
            "avg_volume": _g("averageVolume"),
            "volume":     _g("volume") or _g("regularMarketVolume"),
            # Valuation
            "market_cap":   _g("marketCap"),
            "pe":           _g("trailingPE"),
            "forward_pe":   _g("forwardPE"),
            "peg":          _g("pegRatio"),
            "ps":           _g("priceToSalesTrailing12Months"),
            "pb":           _g("priceToBook"),
            "beta":         _g("beta"),
            "eps":          _g("trailingEps"),
            # Range
            "high_52w":     _g("fiftyTwoWeekHigh"),
            "low_52w":      _g("fiftyTwoWeekLow"),
            "sma_50":       _g("fiftyDayAverage"),
            "sma_200":      _g("twoHundredDayAverage"),
            # Dividend / yield
            "dividend_yield":     _g("dividendYield"),
            "dividend_rate":      _g("dividendRate"),
            "ex_dividend_date":   _g("exDividendDate"),
            # Growth
            "revenue_ttm":    _g("totalRevenue"),
            "gross_margin":   _g("grossMargins"),
            "op_margin":      _g("operatingMargins"),
            "profit_margin":  _g("profitMargins"),
            "roe":            _g("returnOnEquity"),
            "roa":            _g("returnOnAssets"),
            # Analyst
            "target_mean":    _g("targetMeanPrice"),
            "target_high":    _g("targetHighPrice"),
            "target_low":     _g("targetLowPrice"),
            "recommendation": _g("recommendationKey"),
            "analyst_count":  _g("numberOfAnalystOpinions"),
            # Chart + news arrays
            "chart":  chart_out,
            "news":   news_out,
        }

    _app.get("/api/company/{ticker}")(_api_company)
    print("[API] /api/company/{ticker} mounted")
except Exception as _e:
    print(f"[API] company mount failed: {_e}")


# ── Prop-Desk Live NiceGUI page ──────────────────────────────────────────────
@ui.page("/prop_desk")
def prop_desk_page():
    from datetime import datetime as _dt
    ui.add_head_html("<meta name='viewport' content='width=device-width, initial-scale=1.0'>")

    @ui.refreshable
    def _render():
        # Read state
        st = {}
        try:
            if Path("/Users/quarki3/scanner/state/prop_state.json").exists():
                st = json.loads(Path("/Users/quarki3/scanner/state/prop_state.json").read_text())
        except Exception as e:
            ui.label(f"error: {e}"); return

        # Ledger summary
        led = {"n_trades": 0, "total_pnl": 0, "by_strategy": {}}
        try:
            trades = []
            if Path("/Users/quarki3/scanner/state/prop_ledger.jsonl").exists():
                for ln in Path("/Users/quarki3/scanner/state/prop_ledger.jsonl").read_text().splitlines():
                    trades.append(json.loads(ln))
            led["n_trades"] = len(trades)
            led["total_pnl"] = round(sum(t.get("pnl_pct", 0) for t in trades), 2)
            bs: dict = {}
            for t in trades:
                s = t.get("strategy", "?")
                bs.setdefault(s, [0, 0.0])
                bs[s][0] += 1
                bs[s][1] += t.get("pnl_pct", 0)
            led["by_strategy"] = {k: {"n": v[0], "total": round(v[1], 2)} for k, v in bs.items()}
        except Exception:
            pass

        regime_col = {"trending": GRN, "mixed": BLU, "chop": YLW, "unknown": FG2}.get(
            st.get("regime", "?"), FG2)
        age = int(_dt.now().timestamp()) - st.get("ts", 0)

        # HEADER
        with ui.row().classes("w-full q-px-md q-pt-md items-center"):
            ui.label("⚡ PROP-DESK LIVE").style(
                f"color:{PRP};font-size:1.2rem;font-weight:800;font-family:monospace")
            ui.space()
            ui.label(f"tick age: {age}s").style(
                f"color:{FG2};font-size:0.7rem;font-family:monospace")

        # REGIME + BTC + LEDGER SUMMARY strip
        with ui.row().classes("w-full q-px-md q-pt-sm gap-sm").style("flex-wrap:wrap"):
            for label, val, col in [
                ("REGIME",   st.get("regime", "?").upper(), regime_col),
                ("BTC 1h VOL", f"{st.get('btc_vol_1h', 0):.2f}%", FG),
                ("BTC",      f"${st.get('btc_price', 0):,.0f}",  FG),
                ("HELD",     str(len(st.get("held", []))),        BLU),
                ("MODE",     st.get("alpaca_mode", "?").upper(),   YLW),
                ("TRADES",   str(led["n_trades"]),                FG),
                ("TOTAL P&L", f"{led['total_pnl']:+.2f}%",
                 GRN if led["total_pnl"] > 0 else (RED if led["total_pnl"] < 0 else FG2)),
            ]:
                with ui.card().classes("q-pa-sm").style(
                        f"background:{BG2};border-left:3px solid {col};flex:1 1 130px;min-width:120px"):
                    ui.label(label).style(
                        f"color:{FG2};font-size:0.60rem;font-family:monospace;letter-spacing:1.2px")
                    ui.label(val).style(
                        f"color:{col};font-size:1.05rem;font-weight:700;font-family:monospace")

        # HELD POSITIONS
        with ui.row().classes("w-full q-px-md q-pt-md"):
            with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
                ui.label("● HELD POSITIONS").style(
                    f"color:{FG2};font-size:0.7rem;font-weight:700;font-family:monospace;letter-spacing:1.5px")
                held = st.get("held", [])
                if not held:
                    ui.label("(no positions)").style(
                        f"color:{FG2};font-size:0.85rem;font-family:monospace;opacity:0.5")
                else:
                    for h in held:
                        pnl_col = GRN if h["pnl_pct"] > 0 else (RED if h["pnl_pct"] < 0 else FG)
                        with ui.row().classes("w-full items-center gap-sm q-py-xs").style(
                                f"border-left:2px solid {pnl_col};padding-left:6px"):
                            ui.label(h["sym"]).style(
                                f"color:{FG};font-weight:700;font-family:monospace;min-width:56px")
                            ui.label(f"{h['pnl_pct']:+.2f}%").style(
                                f"color:{pnl_col};font-family:monospace;min-width:72px")
                            ui.label(f"dd {h['drawdown']:+.2f}%").style(
                                f"color:{FG2};font-family:monospace;font-size:0.75rem;min-width:80px")
                            ui.label(f"${h['size_usd']}").style(
                                f"color:{FG2};font-family:monospace;font-size:0.75rem")
                            ui.label(f"strat={h['strategy']}").style(
                                f"color:{FG2};font-family:monospace;font-size:0.75rem")
                            if h.get("whale_score", 0):
                                ui.label(f"whale {h['whale_score']:+.1f}").style(
                                    f"color:{PRP};font-family:monospace;font-size:0.75rem")
                            ui.label(f"{h['age_s']//60}m {h['age_s']%60}s").style(
                                f"color:{FG2};font-family:monospace;font-size:0.7rem;opacity:0.6")

        # STRATEGIES pulse
        strats = st.get("strategies", {})
        with ui.row().classes("w-full q-px-md q-pt-md gap-sm").style("flex-wrap:wrap"):
            for name in ("momentum", "mean_reversion", "breakout"):
                s = strats.get(name, {})
                if not s:
                    continue
                with ui.card().classes("q-pa-md").style(
                        f"background:{BG2};flex:1 1 200px;min-width:180px"):
                    ui.label(name.upper()).style(
                        f"color:{FG2};font-size:0.65rem;font-weight:700;font-family:monospace;letter-spacing:1.2px")
                    ui.label(f"BUYs {s.get('buys', 0)}  SELLs {s.get('sells', 0)}").style(
                        f"color:{FG};font-family:monospace")
                    for t in s.get("top", [])[:3]:
                        act_col = GRN if t["action"] == "BUY" else RED
                        ui.label(f"  {t['sym']:<7} {t['action']} {t['score']:+.2f}").style(
                            f"color:{act_col};font-size:0.75rem;font-family:monospace")

        # MOVERS
        movers = st.get("movers", [])
        if movers:
            with ui.row().classes("w-full q-px-md q-pt-md"):
                with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
                    ui.label("↕ TOP MOVERS (last 2m)").style(
                        f"color:{FG2};font-size:0.7rem;font-weight:700;font-family:monospace;letter-spacing:1.5px")
                    for m in movers[:8]:
                        col = GRN if m["v_2m_pct"] > 0 else RED
                        with ui.row().classes("w-full items-center gap-sm q-py-xs"):
                            ui.label(m["sym"]).style(
                                f"color:{FG};font-weight:700;font-family:monospace;min-width:56px")
                            ui.label(f"{m['v_2m_pct']:+.2f}%").style(
                                f"color:{col};font-family:monospace;min-width:72px")
                            ui.label(f"{m['sigma']:+.2f}σ").style(
                                f"color:{FG2};font-family:monospace;min-width:70px")
                            ui.label(f"typ_2m {m['typ_2m']:.2f}%").style(
                                f"color:{FG2};font-family:monospace;font-size:0.75rem;opacity:0.7")

        # WHALES
        whales = st.get("whales", [])
        if whales:
            with ui.row().classes("w-full q-px-md q-pt-md"):
                with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
                    ui.label("🐳 WHALE CONFLUENCE (last 30m)").style(
                        f"color:{PRP};font-size:0.7rem;font-weight:700;font-family:monospace;letter-spacing:1.5px")
                    for w in whales[:8]:
                        col = GRN if w["score"] > 0 else RED
                        hits = ", ".join(w.get("hit_types", []))[:80]
                        with ui.row().classes("w-full items-center gap-sm q-py-xs"):
                            ui.label(w["sym"]).style(
                                f"color:{FG};font-weight:700;font-family:monospace;min-width:56px")
                            ui.label(f"{w['score']:+.1f}").style(
                                f"color:{col};font-family:monospace;min-width:60px")
                            ui.label(f"{w['hits']} hits").style(
                                f"color:{FG2};font-family:monospace;font-size:0.75rem;min-width:65px")
                            ui.label(hits).style(
                                f"color:{FG2};font-family:monospace;font-size:0.7rem;opacity:0.7")

        # GATED
        gated = st.get("gated", [])
        if gated:
            with ui.row().classes("w-full q-px-md q-pt-md"):
                with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
                    ui.label("⊘ GATED THIS TICK").style(
                        f"color:{YLW};font-size:0.7rem;font-weight:700;font-family:monospace;letter-spacing:1.5px")
                    for g in gated[:8]:
                        ui.label(f"  {g['sym']:<7}  {g['strategy']:<15}  {g['reason']}  — {g.get('note','')}").style(
                            f"color:{FG2};font-family:monospace;font-size:0.75rem")

        # LEDGER by strategy
        with ui.row().classes("w-full q-px-md q-pt-md q-pb-md"):
            with ui.card().classes("w-full q-pa-md").style(f"background:{BG2}"):
                ui.label("Σ  LEDGER BY STRATEGY").style(
                    f"color:{FG2};font-size:0.7rem;font-weight:700;font-family:monospace;letter-spacing:1.5px")
                bs = led.get("by_strategy", {})
                if not bs:
                    ui.label("(no closed trades yet)").style(
                        f"color:{FG2};font-family:monospace;opacity:0.5")
                for k, v in bs.items():
                    col = GRN if v["total"] > 0 else RED
                    ui.label(f"  {k:<15}  n={v['n']:<4}  total={v['total']:+.2f}%").style(
                        f"color:{col};font-family:monospace;font-size:0.85rem")

    _render()
    ui.timer(2.0, _render.refresh)


# ── Launch ────────────────────────────────────────────────────────────────────
if __name__ in {"__main__", "__mp_main__"}:
    threading.Thread(target=_background_loop, daemon=True).start()

    # Resolve Tailscale IP at startup
    try:
        import subprocess as _sp
        _ts_ip = _sp.check_output(
            ["/opt/homebrew/bin/tailscale",
             "--socket=/opt/homebrew/var/run/tailscaled.socket", "ip", "-4"],
            text=True, stderr=_sp.DEVNULL).strip()
    except Exception:
        _ts_ip = "<tailscale-ip>"

    print("=" * 60)
    print("  Trading Dashboard  →  http://0.0.0.0:8080")
    print(f"  Local      :  http://192.168.0.5:8080")
    print(f"  Tailscale  :  http://{_ts_ip}:8080")
    print("=" * 60)

    ui.run(
        host="0.0.0.0",
        port=8080,
        title="Trading Dashboard",
        dark=True,
        reload=False,  # was True — file watcher was reloading on every change in $HOME, killing service
        show=False,
        favicon="📊",
    )
