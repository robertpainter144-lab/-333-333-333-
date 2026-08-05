"""
IBKR Portfolio Manager — entry point for Interactive Brokers live trading.
Runs completely independently of the Alpaca paper system.

Usage:
    python3.10 ibkr_main.py              # web dashboard + background scheduler
    python3.10 ibkr_main.py --daemon     # headless scheduler only (no web)
    python3.10 ibkr_main.py --rebalance  # force one rebalance cycle then exit
    python3.10 ibkr_main.py --qc-refresh # refresh QC leaderboard then exit

Web dashboard:  http://localhost:5050   (or your machine's IP for remote access)
"""
import os
os.environ["QC_BROKER"] = "ibkr"   # must be first — before any local imports

import sys
import time
import threading
import logging
from datetime import datetime, timedelta

import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import portfolio_engine as pe
import broker as md
from config import (
    INTRADAY_CHECK_MINS, INTRADAY_OPEN_CHECK_MINS, REBALANCE_INTERVAL_DAYS,
    WEB_HOST, WEB_PORT,
)

ET = pytz.timezone("America/New_York")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  [IBKR]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("ibkr_portfolio")


# ── Core rebalance logic ──────────────────────────────────────────────────

def do_rebalance():
    log.info("=== IBKR REBALANCE START ===")
    pe.load_state()

    changed = pe.refresh_qc()
    if changed:
        log.info("Top-5 strategies changed — positions will be updated.")

    account    = md.get_account()
    positions  = md.get_positions()
    raw_equity = float(account.get("equity", 0) or 0)
    cash       = float(account.get("cash", 0) or 0)
    equity     = pe.validate_equity(raw_equity, positions, cash)

    if equity <= 0:
        log.error("Could not retrieve account equity — aborting rebalance.")
        return

    pe.record_equity(equity)
    log.info(f"Account equity: ${equity:,.2f}  |  {len(positions)} open positions")

    all_syms = set()
    for cfg in pe.get_strategies():
        all_syms.update(cfg.get("universe", []))
    for pos in positions:
        all_syms.add(pos.get("symbol", ""))
    all_syms.discard("")

    prices = md.get_current_prices(list(all_syms))

    stop_orders = pe.check_stop_losses(positions, prices, total_equity=equity)
    for sym, qty, side, reason in stop_orders:
        result = md.place_order(sym, qty, side)
        if result:
            pe.log_tx(sym, side, qty, prices.get(sym, 0), reason=reason)
            log.info(f"  STOP  {side.upper():4s} {qty:.4f} {sym}  [{reason}]")

    positions = md.get_positions()
    target    = pe.compute_target(equity)
    orders    = pe.rebalance_orders(positions, target, prices)

    for sym, qty, side, reason in orders:
        result = md.place_order(sym, qty, side)
        if result:
            pe.log_tx(sym, side, qty, prices.get(sym, 0), reason=reason)
            log.info(f"  ORDER {side.upper():4s} {qty:.4f} {sym}  [{reason}]")

    pe.save_state()
    log.info(f"=== IBKR REBALANCE DONE — {len(orders)} orders ===")


# ── Intraday stop-check ───────────────────────────────────────────────────

def _is_market_hours():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return (t >= now.replace(hour=9,  minute=30, second=0).time()
        and t <  now.replace(hour=16, minute=0,  second=0).time())


def do_stop_check():
    log.info("--- IBKR INTRADAY STOP CHECK ---")
    pe.load_state()

    account    = md.get_account()
    positions  = md.get_positions()
    raw_equity = float(account.get("equity", 0) or 0)
    cash       = float(account.get("cash", 0) or 0)
    equity     = pe.validate_equity(raw_equity, positions, cash)
    if equity > 0:
        pe.record_equity(equity)

    if not positions:
        log.info("No open positions.")
        return

    prices = {}
    for pos in positions:
        sym = pos.get("symbol", "")
        cp  = pos.get("current_price")
        if sym and cp:
            try:
                prices[sym] = float(cp)
            except (TypeError, ValueError):
                pass

    if not prices:
        log.warning("No live prices — skipping stop check.")
        return

    stop_orders = pe.check_stop_losses(positions, prices, total_equity=equity)
    ratchet_orders, session_gain, floor = pe.check_profit_ratchet(positions, prices)
    all_orders = stop_orders + ratchet_orders

    log.info(f"  Session gain: ${session_gain:+.2f}  |  Ratchet floor: "
             f"{'${:.2f}'.format(floor) if floor is not None else 'inactive'}")

    if not all_orders:
        log.info("No stops or ratchet triggered.")
        return

    for sym, qty, side, reason in all_orders:
        result = md.place_order(sym, qty, side)
        if result:
            pe.log_tx(sym, side, qty, prices.get(sym, 0), reason=reason)
            log.info(f"  ORDER {side.upper():4s} {qty:.4f} {sym}  [{reason}]")

    pe.save_state()
    log.info(f"--- IBKR STOP CHECK DONE — {len(all_orders)} orders ---")


def _intraday_loop(stop_event):
    tick = 60
    elapsed = 0
    session_open_time = None
    while not stop_event.is_set():
        time.sleep(tick)
        if not _is_market_hours():
            session_open_time = None
            elapsed = 0
            continue
        if session_open_time is None:
            session_open_time = datetime.now(ET)
            elapsed = 0
            log.info("[INTRADAY] Market open — 5-min ratchet checks active.")
        elapsed += tick
        mins_since_open = (datetime.now(ET) - session_open_time).total_seconds() / 60
        interval = (INTRADAY_OPEN_CHECK_MINS if mins_since_open <= 60
                    else INTRADAY_CHECK_MINS) * 60
        if elapsed < interval:
            continue
        elapsed = 0
        try:
            do_stop_check()
        except Exception as e:
            log.error(f"Intraday check error: {e}")


# ── Scheduler ─────────────────────────────────────────────────────────────

def _next_rebalance_dt():
    now_et = datetime.now(ET)
    target = now_et.replace(hour=9, minute=31, second=0, microsecond=0)
    if now_et >= target:
        target += timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def _scheduler_loop(stop_event):
    trading_days_since_reb = REBALANCE_INTERVAL_DAYS
    while not stop_event.is_set():
        next_dt   = _next_rebalance_dt()
        now_et    = datetime.now(ET)
        wait_secs = (next_dt - now_et).total_seconds()
        log.info(f"Next open: {next_dt.strftime('%Y-%m-%d %H:%M ET')}  "
                 f"(in {wait_secs/3600:.1f}h)  "
                 f"[{trading_days_since_reb}/{REBALANCE_INTERVAL_DAYS} days since rebalance]")

        elapsed = 0
        while elapsed < wait_secs and not stop_event.is_set():
            time.sleep(min(60, wait_secs - elapsed))
            elapsed += 60
        if stop_event.is_set():
            break

        now_et = datetime.now(ET)
        if abs((now_et - next_dt).total_seconds()) >= 120:
            log.warning("Scheduler drift — skipping fire")
            continue

        trading_days_since_reb += 1
        if trading_days_since_reb < REBALANCE_INTERVAL_DAYS:
            log.info(f"Skipping rebalance — day {trading_days_since_reb}/{REBALANCE_INTERVAL_DAYS}")
            continue

        try:
            do_rebalance()
            trading_days_since_reb = 0
        except Exception as e:
            log.error(f"Rebalance error: {e}")


def start_scheduler(stop_event=None):
    if stop_event is None:
        stop_event = threading.Event()
    t = threading.Thread(target=_scheduler_loop, args=(stop_event,), daemon=True)
    t.start()
    log.info("IBKR daily rebalance scheduler started.")
    return stop_event


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if "--rebalance" in args:
        pe.load_state()
        do_rebalance()
        return

    if "--qc-refresh" in args:
        pe.load_state()
        changed = pe.refresh_qc(force=True)
        strats  = pe.get_strategies()
        weights = pe.get_weights()
        print(f"\nTop {len(strats)} QC Strategies (IBKR):")
        for i, (cfg, w) in enumerate(zip(strats, weights)):
            stats  = cfg.get("statistics", {})
            r3m    = stats.get("oos 3m return", 0)
            sharpe = stats.get("oos 3m sharpe", 0)
            print(f"  {i+1}. [{cfg['id']:4d}] {cfg.get('name',''):<40} "
                  f"3M: {r3m:>7.2f}%  Sharpe: {sharpe:>6.2f}  "
                  f"Weight: {w*100:>5.1f}%")
        print(f"\n{'Rankings changed' if changed else 'No change in top 5'}")
        return

    pe.load_state()
    stop_event = start_scheduler()

    intraday_t = threading.Thread(
        target=_intraday_loop, args=(stop_event,), daemon=True)
    intraday_t.start()

    if "--daemon" in args:
        log.info("Running in headless daemon mode (no web GUI).")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            log.info("Shutting down.")
            stop_event.set()
        finally:
            try:
                from ibkr_data import disconnect
                disconnect()
            except Exception:
                pass
        return

    # Default: scheduler + web dashboard
    from web_gui import create_app
    app = create_app(
        rebalance_fn=do_rebalance,
        stop_check_fn=do_stop_check,
    )
    log.info(f"Web dashboard → http://0.0.0.0:{WEB_PORT}  (password protected)")
    try:
        app.run(host=WEB_HOST, port=WEB_PORT, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        try:
            from ibkr_data import disconnect
            disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    main()
