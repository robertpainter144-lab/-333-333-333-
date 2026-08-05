"""
IBKR position watcher daemon.

Runs every WATCH_INTERVAL_SEC, does five things:

  1. Auto-attach stops to any open position that doesn't have a bracket yet.
  2. Profit-step: if price ≥ entry × (1 + PROFIT_STEP_MULT × ATR%), sell half and
     ratchet the bracket's stop floor to break-even.
  3. Time-stop: if held > TIME_STOP_DAYS with < TIME_STOP_PROGRESS_PCT gain,
     close the position (cancel bracket, market sell).
  4. Trail refresh: if the live trail % has drifted > REFRESH_THRESHOLD from the
     current adaptive value (regime shift), modify the live TRAIL order.
  5. Fill reconciliation: any SELL execution (server-side TRAIL/STOP hits
     included) missing from transactions.csv is appended with reason='server_stop'.

Designed to run as a launchd / systemd service. State in stop_state.json.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ib_insync import IB, MarketOrder

GATEWAY_HOST = os.environ.get("IBKR_GATEWAY_HOST", "127.0.0.1")
GATEWAY_PORT = int(os.environ.get("IBKR_GATEWAY_PORT", "4001"))

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import stop_manager as SM
import main as MAIN
try:
    import notify
except Exception:
    notify = None

import day_trade_guard as DTG
try:
    import arms as ARMS
except Exception:
    ARMS = None

SEEN_EXEC_FILE = ROOT / "seen_executions.json"

# ── Tunables ──────────────────────────────────────────────────────────
WATCH_INTERVAL_SEC      = 300     # 5 min
PROFIT_STEP_MULT        = 2.0     # at +2 × ATR% from entry, take half
TIME_STOP_DAYS          = 30
TIME_STOP_PROGRESS_PCT  = 0.01
REFRESH_THRESHOLD       = 0.30    # modify live trail if |new - current| / current > 30%
MIN_QTY_FOR_PROFIT_STEP = 2       # need ≥2 shares to sell half


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_positions(ib: IB) -> dict:
    """{symbol: {"qty": float, "avgCost": float}}"""
    accts = ib.managedAccounts()
    if not accts:
        # Gateway dropped — reconnect once before raising
        try: ib.disconnect()
        except Exception: pass
        ib.connect(GATEWAY_HOST, GATEWAY_PORT, clientId=29, timeout=15)
        accts = ib.managedAccounts()
        if not accts:
            raise RuntimeError("watcher: managed accounts empty after reconnect")
    acct = accts[0]
    return {p.contract.symbol: {"qty": float(p.position), "avgCost": float(p.avgCost),
                                "contract": p.contract}
            for p in ib.positions(acct) if abs(float(p.position)) > 0}


def _live_price(ib: IB, contract) -> float | None:
    ib.reqMarketDataType(3)  # delayed
    t = ib.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
    for _ in range(15):
        ib.sleep(0.4)
        for fld in (t.last, t.close, t.bid, t.ask):
            try:
                v = float(fld)
                if v > 0:
                    ib.cancelMktData(contract)
                    return v
            except Exception:
                pass
    ib.cancelMktData(contract)
    # yfinance fallback
    try:
        import yfinance as yf
        h = yf.Ticker(contract.symbol).history(period="1d", interval="5m")
        if not h.empty:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return None


def _load_seen() -> set:
    if SEEN_EXEC_FILE.exists():
        try:
            import json
            return set(json.loads(SEEN_EXEC_FILE.read_text()))
        except Exception:
            return set()
    return set()


def _save_seen(s: set) -> None:
    import json
    SEEN_EXEC_FILE.write_text(json.dumps(sorted(s)))


def _push_new_fills(ib: IB) -> int:
    """Pull all executions, push notify on any execId we haven't seen before."""
    if notify is None:
        return 0
    seen = _load_seen()
    fresh = 0
    try:
        fills = ib.fills()
    except Exception:
        return 0
    for f in fills:
        eid = f.execution.execId
        if not eid or eid in seen:
            continue
        sym  = f.contract.symbol
        side = f.execution.side  # "BOT" or "SLD"
        qty  = float(f.execution.shares or 0)
        px   = float(f.execution.price or 0)
        side_label = "BUY" if side == "BOT" else "SELL"
        try:
            notify.notify_fill(sym, side_label, qty, px)
        except Exception as e:
            print(f"[notify] fill err: {e}", file=sys.stderr)
        seen.add(eid)
        fresh += 1
    if fresh:
        _save_seen(seen)
    return fresh


def _logged_order_ids() -> set:
    """orderIds already present in transactions.csv."""
    if not MAIN.TXNS.exists():
        return set()
    ids = set()
    with MAIN.TXNS.open() as f:
        for row in csv.DictReader(f):
            oid = row.get("orderId")
            if oid:
                try:
                    ids.add(int(oid))
                except ValueError:
                    pass
    return ids


def _reconcile_server_fills(ib: IB) -> int:
    """Append any SELL fill missing from transactions.csv — covers server-side
    TRAIL/STOP hits that IBKR executes without this bot placing an order."""
    try:
        fills = ib.fills()
    except Exception:
        return 0
    logged = _logged_order_ids()
    added = 0
    for f in fills:
        if f.execution.side != "SLD":
            continue
        oid = f.execution.orderId
        if not oid or oid in logged:
            continue
        MAIN._log_txn({
            "ts":            _now().isoformat(),
            "action":        "SELL",
            "symbol":        f.contract.symbol,
            "cashQty":       0,
            "totalQuantity": float(f.execution.shares or 0),
            "limitPrice":    "",
            "status":        "Filled",
            "orderId":       oid,
            "permId":        getattr(f.execution, "permId", 0),
            "filled":        float(f.execution.shares or 0),
            "avgFillPrice":  float(f.execution.price or 0),
            "reason":        "server_stop",
        })
        logged.add(oid)
        added += 1
    return added


def watch_once(ib: IB) -> dict:
    state    = SM._load_state()
    brackets = state["brackets"]
    positions = _get_positions(ib)
    actions   = []

    # New fills since last tick (covers server-side stop hits too)
    nf = _push_new_fills(ib)
    if nf:
        actions.append(f"new_fills_notified={nf}")

    nr = _reconcile_server_fills(ib)
    if nr:
        actions.append(f"server_fills_logged={nr}")

    # 1. Auto-attach for any uncovered position — BUT NOT IF SAME-DAY BUY
    for sym, pos in positions.items():
        if sym not in brackets and pos["qty"] > 0:
            ok, why = DTG.safe_to_sell(ib, sym)
            if not ok:
                actions.append(f"skip_attach_stops {sym} (day_trade_guard)")
                continue
            try:
                exch = pos["contract"].primaryExchange or "SMART"
                br = SM.attach_stops(ib, sym, pos["qty"], pos["avgCost"], exch)
                actions.append(f"attach_stops {sym}")
            except Exception as e:
                actions.append(f"attach_stops_err {sym}: {e}")

    # Re-read state after attaches
    state    = SM._load_state()
    brackets = state["brackets"]

    # 2/3/4. Per-position rules
    for sym, br in list(brackets.items()):
        pos = positions.get(sym)
        if not pos or pos["qty"] <= 0:
            # Position closed (stop hit or external close) — drop the bracket record
            state["brackets"].pop(sym, None)
            actions.append(f"bracket_clear {sym} (no live position)")
            continue

        price = _live_price(ib, pos["contract"])
        if price is None:
            continue

        entry  = br["entry_price"]
        qty    = pos["qty"]
        atr    = br.get("components", {}).get("atr_pct", 0)

        # Time stop
        try:
            entry_dt = datetime.fromisoformat(br["entry_date"])
            held = (_now() - entry_dt).days
        except Exception:
            held = 0
        if held >= TIME_STOP_DAYS and price < entry * (1 + TIME_STOP_PROGRESS_PCT):
            ok, why = DTG.safe_to_sell(ib, sym)
            if not ok:
                actions.append(f"skip_time_stop {sym} (day_trade_guard)")
                continue
            # Cancel bracket, market-sell the whole position
            SM.cancel_bracket(ib, sym)
            mkt = MarketOrder("SELL", qty)
            mkt.tif = "DAY"
            mkt.outsideRth = False
            ib.placeOrder(pos["contract"], mkt)
            actions.append(f"time_stop {sym} ({held}d, price ${price:.2f} ≤ entry ${entry:.2f})")
            continue

        # Profit step (per-arm gate)
        arm = ARMS.get(sym) if ARMS is not None else None
        if arm is not None and not arm.allow_profit_step:
            pass  # arm opted out of profit-step (e.g. armB AAL "let winners run")
        elif atr > 0 and qty >= MIN_QTY_FOR_PROFIT_STEP and not br.get("step_taken"):
            trigger = entry * (1 + PROFIT_STEP_MULT * atr)
            if price >= trigger:
                ok, why = DTG.safe_to_sell(ib, sym)
                if not ok:
                    actions.append(f"skip_profit_step {sym} (day_trade_guard)")
                    continue
                half = int(qty // 2)
                if half > 0:
                    SM.cancel_bracket(ib, sym)
                    mkt = MarketOrder("SELL", half)
                    mkt.tif = "DAY"
                    mkt.outsideRth = False
                    ib.placeOrder(pos["contract"], mkt)
                    ib.sleep(2)
                    # Re-attach bracket on the remaining qty; floor the hard stop
                    # at the original entry so this is a true break-even stop.
                    SM.attach_stops(ib, sym, qty - half, entry,
                                    br.get("components", {}).get("primary_exch", "SMART"),
                                    min_hard_stop_price=entry)
                    # Mark step taken
                    st = SM._load_state()
                    if sym in st["brackets"]:
                        st["brackets"][sym]["step_taken"] = True
                        SM._save_state(st)
                    actions.append(f"profit_step {sym}: sold {half}/{qty} @ ${price:.2f}")
                    continue

        # Trail refresh (regime shift)
        new_trail, comp = SM.compute_trail_pct(sym)
        cur_trail = br["trail_pct"]
        if cur_trail > 0 and abs(new_trail - cur_trail) / cur_trail > REFRESH_THRESHOLD:
            SM.modify_trail(ib, sym, new_trail)
            actions.append(f"trail_refresh {sym}: {cur_trail*100:.2f}% → {new_trail*100:.2f}%")

    SM._save_state(state)
    return {"timestamp": _now().isoformat(), "actions": actions,
            "positions": len(positions), "brackets": len(brackets)}


def main() -> None:
    daemon = "--daemon" in sys.argv
    ib = IB()
    ib.connect(GATEWAY_HOST, GATEWAY_PORT, clientId=29, timeout=15)
    print(f"watcher connected: accounts={ib.managedAccounts()} via {GATEWAY_HOST}:{GATEWAY_PORT}")
    try:
        if not daemon:
            print(json.dumps(watch_once(ib), indent=2))
            return
        while True:
            try:
                r = watch_once(ib)
                if r["actions"]:
                    print(f"[{r['timestamp'][11:19]}] actions: {r['actions']}")
                else:
                    print(f"[{r['timestamp'][11:19]}] idle  pos={r['positions']} brackets={r['brackets']}")
            except Exception as e:
                print(f"[watch err] {e}", file=sys.stderr)
            time.sleep(WATCH_INTERVAL_SEC)
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
