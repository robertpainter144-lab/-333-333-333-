"""
Shadow engine: runs the same QC top-5 signal as the production bot, but uses
the adaptive-stops + leveraged-ETF rules and tracks a virtual portfolio.

No orders are sent to Alpaca. We read the production state file to inherit
strategy picks, then simulate fills at live prices. State is written to our
own files so the dashboard can show prod vs test side-by-side.

Tick from anywhere:
    from qc_portfolio_test.engine import ShadowEngine
    eng = ShadowEngine()
    eng.tick()
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Reach the production qc_portfolio modules
QC_DIR = Path("/Users/quarki3/qc_portfolio")
if str(QC_DIR) not in sys.path:
    sys.path.insert(0, str(QC_DIR))

from market_data import get_current_prices                        # noqa: E402
import adaptive_stops as A                                         # noqa: E402
from config import (                                               # noqa: E402
    DD_LADDER, LEVERAGED_TICKERS, LEV_TIME_STOP_DAYS,
    LEV_NO_ENTRY_VIX, LEV_NO_ENTRY_BURST, LEV_MAX_PORTFOLIO_PCT,
    HARD_STOP_ATR_MULT, HARD_STOP_FLOOR_PCT,
    TIME_STOP_DAYS, TIME_STOP_PROGRESS_PCT, PROFIT_STEP_ATR_MULT,
    MAX_SINGLE_POSITION,
)

TEST_DIR     = Path(__file__).parent
STATE_FILE   = TEST_DIR / "state.json"
TRANSACTIONS = TEST_DIR / "transactions.csv"
PROD_STATE   = QC_DIR / "portfolio_state.json"

INITIAL_CAPITAL = 50_000.0

# ── Next-gen knobs (shadow runs MORE aggressive than prod) ────────────────────
# These override the values imported from config above for the shadow only.
# If shadow outperforms prod, these get promoted into config.py.
SHADOW_BASE_K            = 1.85   # tighter than prod's 2.25 — quicker exits
SHADOW_LEV_TIME_STOP     = 1      # leveraged ETFs: 1-day max hold (vs 2 in prod)
SHADOW_TIME_STOP_DAYS    = 5      # non-leveraged: 5d max idle (vs 7 in prod)
SHADOW_HARD_FLOOR_PCT    = 0.06   # tighter hard stop (vs 8% in prod)
SHADOW_CAP_PCT           = 0.07   # never trail wider than 7% (vs 8.5% in prod)
SHADOW_PROFIT_STEP_MULT  = 1.5    # take profit earlier (+1.5×ATR vs +2×ATR)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _today_iso():
    return datetime.now(timezone.utc).date().isoformat()


class ShadowEngine:
    """Virtual-portfolio twin of qc_portfolio with adaptive+leveraged stops."""

    def __init__(self):
        self.state = self._load()

    # ── persistence ─────────────────────────────────────────────────────────
    def _load(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text())
            except Exception:
                pass
        return {
            "cash":          INITIAL_CAPITAL,
            "initial_cash":  INITIAL_CAPITAL,
            "positions":     {},   # sym -> {qty, cost, peak, entry_date, step_taken}
            "peak_equity":   INITIAL_CAPITAL,
            "exposure_level": 1.0,
            "tiers_fired":   [],
            "equity_history": [],   # [[ts, value], ...]
            "last_tick":     None,
            "trade_count":   0,
            "exit_reasons":  {},
        }

    def _save(self):
        STATE_FILE.write_text(json.dumps(self.state, indent=2, default=str))

    # ── transaction log ─────────────────────────────────────────────────────
    def _log(self, sym, side, qty, price, reason):
        write_header = not TRANSACTIONS.exists()
        with TRANSACTIONS.open("a", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["timestamp", "symbol", "side", "qty", "price",
                            "value", "reason"])
            w.writerow([
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                sym, side, round(qty, 4), round(price, 4),
                round(qty * price, 2), reason,
            ])
        self.state["trade_count"] = self.state.get("trade_count", 0) + 1
        if side == "sell":
            r = self.state.setdefault("exit_reasons", {})
            r[reason] = r.get(reason, 0) + 1

    # ── derived helpers ─────────────────────────────────────────────────────
    def equity(self, prices: dict) -> float:
        held = 0.0
        for sym, h in self.state["positions"].items():
            held += h["qty"] * prices.get(sym, h["cost"])
        return self.state["cash"] + held

    # ── core tick ───────────────────────────────────────────────────────────
    def tick(self, force_rebalance: bool = False) -> dict:
        if not PROD_STATE.exists():
            return {"error": "production state file missing"}
        prod = json.loads(PROD_STATE.read_text())
        strategies  = prod.get("strategies", [])
        weights     = prod.get("weights", [])
        allocations = prod.get("allocations", {})
        if not strategies or not weights:
            return {"error": "no strategy data in production state"}

        # Universe = SPY + every symbol in any allocation
        syms = {"SPY"}
        for cfg in strategies:
            syms.update(cfg.get("universe", []))
        for alloc in allocations.values():
            syms.update(alloc.keys())
        prices = get_current_prices(list(syms)) or {}
        if not prices:
            return {"error": "no live prices"}

        ctx = A.get_market_context()

        # 1) Run stops on existing positions
        stop_orders = self._evaluate_stops(prices, ctx)
        for sym, qty, reason in stop_orders:
            self._execute(sym, qty, "sell", prices.get(sym, 0), reason)

        # 2) Rebalance to current targets
        equity = self.equity(prices)
        target = self._compute_target(equity, strategies, weights,
                                       allocations, prices, ctx)
        for sym, side, qty in self._rebalance_orders(target, prices):
            self._execute(sym, qty, side, prices.get(sym, 0), "rebalance")

        # 3) Update peak / exposure / record snapshot
        eq = self.equity(prices)
        self.state["peak_equity"] = max(self.state.get("peak_equity", eq), eq)
        ts = int(time.time())
        hist = self.state.setdefault("equity_history", [])
        hist.append([ts, round(eq, 2)])
        if len(hist) > 2880:           # cap at ~2 days of 1-min ticks
            del hist[:-2880]
        self.state["last_tick"] = _now_iso()
        self._save()

        return {
            "equity": round(eq, 2),
            "cash":   round(self.state["cash"], 2),
            "positions": len(self.state["positions"]),
            "peak_equity": round(self.state["peak_equity"], 2),
            "drawdown_pct": round(
                (self.state["peak_equity"] - eq) / self.state["peak_equity"] * 100, 2),
            "trade_count": self.state["trade_count"],
            "context": {
                "vix":         ctx.vix if ctx else None,
                "spy_above":   ctx.spy_above_200sma if ctx else None,
                "rv5_over_rv60": (ctx.realized_vol_5d / ctx.realized_vol_60d)
                                  if ctx and ctx.realized_vol_60d else None,
                "in_stress":   ctx.in_stress if ctx else None,
            },
        }

    # ── stop evaluation ─────────────────────────────────────────────────────
    def _evaluate_stops(self, prices, ctx):
        orders, kept = [], {}
        today = datetime.now(timezone.utc).date()
        for sym, h in self.state["positions"].items():
            price = prices.get(sym)
            if not price:
                kept[sym] = h
                continue
            entry = h["cost"]
            peak  = max(h.get("peak", entry), price)
            h["peak"] = peak

            leveraged = sym in LEVERAGED_TICKERS
            dec   = A.effective_trail(sym, ctx) if ctx else None
            atr   = dec.atr_pct if dec else 0.0

            # Shadow recomputes trail with its own (tighter) BASE_K + CAP, ignoring
            # adaptive_stops.effective_trail's defaults. Same multipliers, different scalars.
            if dec and ctx:
                lev_m = (0.85 if leveraged else 1.0)
                raw = (SHADOW_BASE_K * atr * dec.vix_mult * dec.spy_mult
                       * dec.burst_mult * dec.event_mult * lev_m)
                trail = max(A.FLOOR_PCT, min(SHADOW_CAP_PCT, raw))
            else:
                trail = 0.03

            if atr > 0:
                hard_drop = min(SHADOW_HARD_FLOOR_PCT, HARD_STOP_ATR_MULT * atr)
            else:
                hard_drop = SHADOW_HARD_FLOOR_PCT

            # Hard stop
            if price <= entry * (1.0 - hard_drop):
                orders.append((sym, h["qty"], "hard_stop"))
                continue
            # Trail stop
            if trail and price < peak * (1.0 - trail):
                orders.append((sym, h["qty"], "trail_stop"))
                continue
            # Profit step (shadow takes profit earlier)
            if atr > 0 and not h.get("step_taken"):
                if price >= entry * (1.0 + SHADOW_PROFIT_STEP_MULT * atr):
                    half = round(h["qty"] * 0.5, 6)
                    if half > 0:
                        orders.append((sym, half, "profit_step"))
                        h["qty"] -= half
                        h["peak"]   = max(peak, entry)
                        h["step_taken"] = True
                        kept[sym] = h
                        continue
            # Time stop
            try:
                d_entry = datetime.fromisoformat(h.get("entry_date", _today_iso())).date()
                held_days = (today - d_entry).days
            except Exception:
                held_days = 0
            limit = SHADOW_LEV_TIME_STOP if leveraged else SHADOW_TIME_STOP_DAYS
            if held_days >= limit and price < entry * (1.0 + TIME_STOP_PROGRESS_PCT):
                orders.append((sym, h["qty"], "time_stop"))
                continue

            kept[sym] = h

        # Remove fully-exited positions from state
        full_exits = {sym for sym, _, _ in orders} - {sym for sym in kept}
        for sym in list(self.state["positions"]):
            if sym in full_exits:
                self.state["positions"].pop(sym, None)
        self.state["positions"].update(kept)
        return orders

    # ── target computation (mirrors compute_target with lev rules) ──────────
    def _compute_target(self, equity, strategies, weights, allocations, prices, ctx):
        target = {}
        for i, cfg in enumerate(strategies):
            strat_dollars = equity * weights[i]
            alloc = allocations.get(str(cfg["id"]), allocations.get(cfg["id"], {}))
            for sym, w in alloc.items():
                target[sym] = target.get(sym, 0.0) + strat_dollars * w

        # Per-symbol single-position cap
        cap = equity * MAX_SINGLE_POSITION
        for sym in list(target):
            if target[sym] > cap:
                target[sym] = cap

        # Leveraged entry veto (block adds; existing holding can stay)
        if ctx is not None:
            for sym in list(target):
                if sym not in LEVERAGED_TICKERS:
                    continue
                allowed, _ = A.entry_allowed(sym, ctx)
                if allowed:
                    continue
                held_val = (self.state["positions"].get(sym, {}).get("qty", 0)
                            * prices.get(sym, 0))
                if target[sym] > held_val:
                    target[sym] = held_val

        # Aggregate leveraged cap
        lev_total = sum(target[s] for s in target if s in LEVERAGED_TICKERS)
        lev_cap   = equity * LEV_MAX_PORTFOLIO_PCT
        if lev_total > lev_cap and lev_total > 0:
            scale = lev_cap / lev_total
            for s in list(target):
                if s in LEVERAGED_TICKERS:
                    target[s] *= scale

        return target

    def _rebalance_orders(self, target_dollars, prices):
        out = []
        # Sells first (close positions not in target)
        for sym in list(self.state["positions"]):
            if sym not in target_dollars and sym in prices:
                qty = self.state["positions"][sym]["qty"]
                if qty > 0:
                    out.append((sym, "sell", qty))
        # Adjust to targets
        for sym, tgt_val in target_dollars.items():
            p = prices.get(sym, 0)
            if p <= 0:
                continue
            held_qty = self.state["positions"].get(sym, {}).get("qty", 0.0)
            held_val = held_qty * p
            diff = tgt_val - held_val
            if abs(diff) < 50:
                continue
            qty = abs(diff) / p
            if diff > 0:
                out.append((sym, "buy", qty))
            else:
                out.append((sym, "sell", min(qty, held_qty)))
        # Sells first so we free cash
        out.sort(key=lambda x: 0 if x[1] == "sell" else 1)
        return out

    # ── execution (virtual) ─────────────────────────────────────────────────
    def _execute(self, sym, qty, side, price, reason):
        if price <= 0 or qty <= 0:
            return
        if side == "buy":
            cost = price * qty
            if cost > self.state["cash"]:
                return
            self.state["cash"] -= cost
            pos = self.state["positions"].get(sym)
            if pos:
                new_q = pos["qty"] + qty
                pos["cost"] = (pos["cost"] * pos["qty"] + price * qty) / new_q
                pos["qty"]  = new_q
                pos["peak"] = max(pos.get("peak", price), price)
            else:
                self.state["positions"][sym] = {
                    "qty": qty, "cost": price, "peak": price,
                    "entry_date": _today_iso(),
                    "step_taken": False,
                }
        else:
            pos = self.state["positions"].get(sym)
            if not pos:
                return
            sell_qty = min(qty, pos["qty"])
            self.state["cash"] += sell_qty * price
            pos["qty"] -= sell_qty
            if pos["qty"] <= 1e-6:
                del self.state["positions"][sym]
        self._log(sym, side, qty, price, reason)


if __name__ == "__main__":
    eng = ShadowEngine()
    result = eng.tick()
    print(json.dumps(result, indent=2, default=str))
