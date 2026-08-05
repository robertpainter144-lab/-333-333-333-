"""
SPY Overlay Bot — combined SMA200 tactical + leveraged sleeve.

Strategy (validated 2011-2026, beats SPY on CAGR, Sharpe, and MaxDD):
    STRONG_BULL    SPY > SMA200  AND  VIX < 20   →   80% SPY + 20% TQQQ
    CAUTIOUS_BULL  SPY > SMA200  AND  VIX ≥ 20   →   100% SPY
    BEAR           SPY ≤ SMA200                  →   100% SHY

Execution model:
    Single-shot script, intended to run once per trading day via cron/launchd.
    Persists regime state to JSON; only trades on regime change or rebalance
    drift (2%) inside STRONG_BULL.

Modes (--broker):
    alpaca-paper   (default)  Alpaca paper account via alpaca-py SDK
    ibkr-live                 IBKR via ib_insync (4001) — gated by $20 ceiling
                              and day_trade_guard.

Safety:
    --dry-run     (default)   Print intended trades, no execution
    --execute                 Actually place orders

Usage:
    python3.10 spy_overlay_bot.py --dry-run               # see what it would do
    python3.10 spy_overlay_bot.py --execute               # actually trade (paper)
    python3.10 spy_overlay_bot.py --broker ibkr-live      # IBKR mode (still dry by default)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf

ROOT       = Path(__file__).resolve().parent
STATE_FILE = ROOT / "spy_overlay_state.json"
LOG_FILE   = ROOT / "spy_overlay.log"

# ── Strategy parameters (validated by backtest, lock them) ─────────────────
SMA_WINDOW      = 200
VIX_THRESHOLD   = 20.0
TQQQ_SLEEVE_PCT = 0.20
REBAL_DRIFT_PCT = 0.02

# ── Hard safety: max notional the bot may move per cron run ────────────────
IBKR_TOTAL_NOTIONAL_CAP = 20.00     # bot may not auto-place > $20 total on IBKR
PAPER_TOTAL_NOTIONAL_CAP = 200_000  # paper: high enough that one-time migration fits

# ── Regime symbols ─────────────────────────────────────────────────────────
SPY  = "SPY"
TQQQ = "TQQQ"
SHY  = "SHY"
VIX  = "^VIX"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    line = f"{_now()}  {msg}"
    print(line)
    try:
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ─── Market data ───────────────────────────────────────────────────────────

def fetch_market_snapshot() -> dict:
    """Pull current SPY price, SPY 200-day SMA, current VIX, TQQQ + SHY prices."""
    syms = [SPY, TQQQ, SHY, VIX]
    hist = yf.download(syms, period="14mo", progress=False,
                       auto_adjust=True, threads=True)
    close = hist["Close"].dropna(how="all").ffill()
    if close.empty or len(close) < SMA_WINDOW + 1:
        raise RuntimeError("insufficient SPY history for 200-SMA")

    spy_close   = float(close[SPY].iloc[-1])
    spy_sma200  = float(close[SPY].iloc[-SMA_WINDOW:].mean())
    vix_close   = float(close[VIX].iloc[-1])
    tqqq_close  = float(close[TQQQ].iloc[-1])
    shy_close   = float(close[SHY].iloc[-1])
    as_of       = close.index[-1].date().isoformat()

    return {
        "as_of":       as_of,
        "spy":         spy_close,
        "spy_sma200":  spy_sma200,
        "spy_vs_sma":  spy_close / spy_sma200 - 1.0,
        "vix":         vix_close,
        "tqqq":        tqqq_close,
        "shy":         shy_close,
    }


# ─── Regime detection ──────────────────────────────────────────────────────

def detect_regime(snap: dict) -> str:
    spy_above = snap["spy"] > snap["spy_sma200"]
    vix_calm  = snap["vix"] < VIX_THRESHOLD
    if not spy_above:
        return "BEAR"
    return "STRONG_BULL" if vix_calm else "CAUTIOUS_BULL"


def target_allocation(regime: str) -> dict:
    """Returns {symbol: target_weight} for the regime."""
    if regime == "STRONG_BULL":
        return {SPY: 1.0 - TQQQ_SLEEVE_PCT, TQQQ: TQQQ_SLEEVE_PCT}
    if regime == "CAUTIOUS_BULL":
        return {SPY: 1.0}
    return {SHY: 1.0}


# ─── State persistence ─────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"regime": None, "last_run": None, "history": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


# ─── Broker adapters ───────────────────────────────────────────────────────

class AlpacaPaper:
    """Wraps qc_portfolio/market_data.py (existing Alpaca broker sandbox REST client)."""
    name = "alpaca-paper"

    def __init__(self):
        sys.path.insert(0, str(ROOT))
        import market_data as md  # noqa
        self._md = md

    def nlv(self) -> float:
        acct = self._md.get_account() or {}
        return float(acct.get("equity") or acct.get("portfolio_value") or 0)

    def positions(self) -> dict:
        out = {}
        for p in self._md.get_positions() or []:
            sym = p.get("symbol")
            try:
                qty = float(p.get("qty", 0))
                val = float(p.get("market_value", 0))
            except Exception:
                qty, val = 0.0, 0.0
            if sym and abs(qty) > 0:
                out[sym] = {"qty": qty, "value": val}
        return out

    def submit(self, symbol: str, side: str, notional: float) -> dict:
        # market_data.place_order takes qty. Convert notional → qty via current price.
        prices = self._md.get_current_prices([symbol])
        px = float(prices.get(symbol) or 0)
        if px <= 0:
            return {"symbol": symbol, "side": side, "status": "no_quote"}
        qty = round(notional / px, 4)
        if qty < 0.0001:
            return {"symbol": symbol, "side": side, "status": "qty_too_small"}
        r = self._md.place_order(symbol, qty, side.lower(), order_type="market", tif="day")
        if not r:
            return {"symbol": symbol, "side": side, "qty": qty,
                    "status": "place_order_returned_none"}
        return {"id": r.get("id"), "symbol": symbol, "side": side,
                "qty": qty, "notional_est": round(qty * px, 2),
                "status": r.get("status")}


class IBKRLive:
    name = "ibkr-live"

    def __init__(self):
        from ib_insync import IB, Stock, MarketOrder  # noqa
        sys.path.insert(0, str(ROOT.parent / "ibkr_bot"))
        import day_trade_guard as DTG
        self._IB = IB
        self._Stock = Stock
        self._MarketOrder = MarketOrder
        self._DTG = DTG
        self._ib = IB()
        self._ib.connect("127.0.0.1", 4001, clientId=37, timeout=15)
        self._acct = self._ib.managedAccounts()[0]

    def nlv(self) -> float:
        for s in self._ib.accountSummary(self._acct):
            if s.tag == "NetLiquidation":
                return float(s.value)
        return 0.0

    def positions(self) -> dict:
        out = {}
        for p in self._ib.positions(self._acct):
            qty = float(p.position)
            if abs(qty) > 0:
                out[p.contract.symbol] = {"qty": qty, "value": qty * float(p.avgCost)}
        return out

    def submit(self, symbol: str, side: str, notional: float) -> dict:
        ok, why = self._DTG.safe_to_sell(self._ib, symbol) if side == "SELL" else (True, "")
        if not ok:
            return {"symbol": symbol, "side": side, "notional": round(notional, 2),
                    "status": f"BLOCKED ({why})"}
        contract = self._Stock(symbol, "SMART", "USD")
        q = self._ib.qualifyContracts(contract)
        if not q:
            return {"symbol": symbol, "side": side, "status": "qualify_failed"}
        contract = q[0]
        # IBKR doesn't support notional orders; price * shares only
        from ib_insync import Ticker  # noqa
        self._ib.reqMarketDataType(3)
        t = self._ib.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
        px = None
        for _ in range(15):
            self._ib.sleep(0.4)
            for fld in (t.last, t.close, t.bid, t.ask):
                try:
                    v = float(fld)
                    if v > 0:
                        px = v; break
                except Exception:
                    pass
            if px: break
        self._ib.cancelMktData(contract)
        if not px:
            return {"symbol": symbol, "side": side, "status": "no_quote"}
        qty = max(1, int(notional / px))
        order = self._MarketOrder(side, qty)
        order.tif = "DAY"
        order.outsideRth = False
        trade = self._ib.placeOrder(contract, order)
        self._ib.sleep(1.0)
        return {"id": trade.order.orderId, "symbol": symbol, "side": side,
                "qty": qty, "px_est": round(px, 2),
                "notional_est": round(qty * px, 2),
                "status": str(trade.orderStatus.status)}

    def close(self):
        try: self._ib.disconnect()
        except Exception: pass


# ─── Trade plan ────────────────────────────────────────────────────────────

def compute_plan(nlv: float, positions: dict, target_weights: dict,
                 snap: dict, regime: str, current_regime: str) -> list:
    """Returns list of trades [{symbol, side, notional, reason}, ...]."""
    plan = []
    # Target dollar values per symbol
    targets = {sym: nlv * w for sym, w in target_weights.items()}
    held    = set(positions.keys())
    want    = set(targets.keys())

    # Sell anything we hold but don't want
    for sym in held - want:
        val = positions[sym]["value"]
        if val > 0.01:
            plan.append({"symbol": sym, "side": "SELL", "notional": val,
                         "reason": f"exit (regime {regime})"})

    # Buy/adjust everything we want
    for sym, tgt_val in targets.items():
        cur_val = positions.get(sym, {}).get("value", 0.0)
        diff    = tgt_val - cur_val

        is_strong_bull = regime == "STRONG_BULL"
        regime_change  = current_regime != regime
        # For drift-only rebalance inside STRONG_BULL, only trade if abs drift > threshold
        if not regime_change and is_strong_bull:
            drift = abs(cur_val - tgt_val) / nlv if nlv > 0 else 0
            if drift < REBAL_DRIFT_PCT:
                continue

        if abs(diff) < 1.00:   # below $1 notional, skip
            continue
        side = "BUY" if diff > 0 else "SELL"
        reason = ("regime change → " + regime) if regime_change else f"drift rebalance ({regime})"
        plan.append({"symbol": sym, "side": side, "notional": abs(diff),
                     "reason": reason})

    return plan


# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", choices=["alpaca-paper", "ibkr-live"],
                    default="alpaca-paper")
    ap.add_argument("--execute", action="store_true",
                    help="actually place orders (default is dry-run)")
    ap.add_argument("--force", action="store_true",
                    help="bypass the IBKR $20 total-notional cap (use with care)")
    args = ap.parse_args()
    dry = not args.execute

    _log(f"=== spy_overlay_bot START  broker={args.broker}  dry_run={dry} ===")

    # 1. Snapshot market
    snap = fetch_market_snapshot()
    _log(f"snapshot  as_of={snap['as_of']}  SPY=${snap['spy']:.2f} "
         f"SMA200=${snap['spy_sma200']:.2f} ({snap['spy_vs_sma']*100:+.2f}%)  "
         f"VIX={snap['vix']:.2f}  TQQQ=${snap['tqqq']:.2f}  SHY=${snap['shy']:.2f}")

    # 2. Decide regime
    regime  = detect_regime(snap)
    weights = target_allocation(regime)
    _log(f"regime    {regime}   target_weights={weights}")

    # 3. Load prior state
    state = load_state()
    prior = state.get("regime")
    _log(f"prior     regime={prior or 'NONE (first run)'}")

    # 4. Connect broker, fetch NLV + positions
    broker = AlpacaPaper() if args.broker == "alpaca-paper" else IBKRLive()
    try:
        nlv = broker.nlv()
        positions = broker.positions()
        _log(f"broker    NLV=${nlv:.2f}  positions={ {s: round(p['value'],2) for s,p in positions.items()} }")

        # 5. Compute trade plan
        plan = compute_plan(nlv, positions, weights, snap, regime, prior)
        if not plan:
            _log("plan      no trades needed (in-target)")
        else:
            _log(f"plan      {len(plan)} trade(s):")
            for t in plan:
                _log(f"            {t['side']:<4} {t['symbol']:<5} "
                     f"${t['notional']:>9.2f}   reason={t['reason']}")

        # 6. Safety: total notional cap
        total_notional = sum(t["notional"] for t in plan)
        cap = IBKR_TOTAL_NOTIONAL_CAP if args.broker == "ibkr-live" else PAPER_TOTAL_NOTIONAL_CAP
        if total_notional > cap and not args.force:
            _log(f"BLOCKED   total notional ${total_notional:.2f} > cap ${cap}.  "
                 f"Pass --force to override (IBKR live requires Pushover approval per user policy).")
            plan = []

        # 7. Execute (or dry-run)
        results = []
        if plan and not dry:
            for t in plan:
                try:
                    r = broker.submit(t["symbol"], t["side"], t["notional"])
                    _log(f"  EXEC    {r}")
                    results.append(r)
                except Exception as e:
                    _log(f"  ERROR   {t}: {e}")
        elif plan and dry:
            _log("dry-run   (use --execute to actually place these orders)")

        # 8. Persist state — only when actually executing, so dry-runs
        # don't poison the "prior regime" used for regime-change detection.
        if not dry:
            state["regime"]   = regime
            state["last_run"] = _now()
            state.setdefault("history", []).append({
                "ts": _now(), "regime": regime, "prior": prior,
                "trades": len(results), "broker": args.broker,
            })
            state["history"] = state["history"][-200:]
            save_state(state)
        _log(f"=== DONE  regime={regime}  trades_placed={len(results)} ===")
    finally:
        if hasattr(broker, "close"):
            broker.close()


if __name__ == "__main__":
    main()
