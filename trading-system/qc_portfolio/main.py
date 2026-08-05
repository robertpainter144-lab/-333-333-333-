"""
QC Portfolio Manager — entry point.

Usage:
    python3.10 main.py              # GUI + background daily rebalancer
    python3.10 main.py --daemon     # headless scheduler only (no GUI)
    python3.10 main.py --rebalance  # force one rebalance cycle then exit
    python3.10 main.py --qc-refresh # force QC leaderboard refresh then exit
"""
import sys
import os
import time
import threading
import logging
from datetime import datetime, timedelta

import pytz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import portfolio_engine as pe
import broker as md
from config import INTRADAY_CHECK_MINS, INTRADAY_OPEN_CHECK_MINS, REBALANCE_INTERVAL_DAYS

ET = pytz.timezone("America/New_York")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("qc_portfolio")


# ── Core rebalance logic ───────────────────────────────────────────────────

def do_rebalance():
    """Refresh QC rankings, enforce stop-losses, rebalance to target."""
    log.info("=== REBALANCE START ===")
    pe.load_state()

    changed = pe.refresh_qc()
    if changed:
        log.info("Top-5 strategies changed — positions will be updated.")

    account   = md.get_account()
    positions = md.get_positions()
    raw_equity = float(account.get("equity", 0) or 0)
    cash       = float(account.get("cash", 0) or 0)
    equity     = pe.validate_equity(raw_equity, positions, cash)

    if equity <= 0:
        log.error("Could not retrieve account equity — aborting rebalance.")
        return

    pe.record_equity(equity)

    log.info(f"Account equity: ${equity:,.2f}  |  {len(positions)} open positions")

    # Collect all symbols
    all_syms = set()
    for cfg in pe.get_strategies():
        all_syms.update(cfg.get("universe", []))
    for pos in positions:
        all_syms.add(pos.get("symbol", ""))
    all_syms.discard("")

    prices = md.get_current_prices(list(all_syms))

    # Stop-loss pass
    stop_orders = pe.check_stop_losses(positions, prices, total_equity=equity)
    for sym, qty, side, reason in stop_orders:
        result = md.place_order(sym, qty, side)
        if result:
            pe.log_tx(sym, side, qty, prices.get(sym, 0), reason=reason)
            log.info(f"  STOP  {side.upper():4s} {qty:.4f} {sym}  [{reason}]")

    # Rebalance pass
    # Refresh positions after stops
    positions = md.get_positions()
    target    = pe.compute_target(equity, current_positions=positions)
    orders    = pe.rebalance_orders(positions, target, prices)

    for sym, qty, side, reason in orders:
        result = md.place_order(sym, qty, side)
        if result:
            pe.log_tx(sym, side, qty, prices.get(sym, 0), reason=reason)
            log.info(f"  ORDER {side.upper():4s} {qty:.4f} {sym}  [{reason}]")

    pe.save_state()
    log.info(f"=== REBALANCE DONE — {len(orders)} orders ===")


# ── Intraday stop-check ───────────────────────────────────────────────────

def _is_market_hours():
    """Return True if current ET time is within regular market hours (Mon–Fri 9:30–16:00)."""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return t >= now.replace(hour=9, minute=30, second=0).time() \
        and t < now.replace(hour=16, minute=0, second=0).time()


def do_stop_check():
    """Pull live Alpaca position prices, enforce stops, DD ladder, and profit ratchet."""
    log.info("--- INTRADAY STOP CHECK ---")
    pe.load_state()
    positions = md.get_positions()

    account    = md.get_account()
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
        log.warning("No live prices from positions — skipping stop check.")
        return

    all_orders = []

    # DD ladder + trailing stops
    stop_orders = pe.check_stop_losses(positions, prices, total_equity=equity)
    all_orders.extend(stop_orders)

    # Profit ratchet
    ratchet_orders, session_gain, floor = pe.check_profit_ratchet(positions, prices)
    all_orders.extend(ratchet_orders)
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
    log.info(f"--- STOP CHECK DONE — {len(all_orders)} orders ---")


def do_eod_log():
    """Append end-of-day summary for qc_portfolio to track_record.jsonl (logging only)."""
    _parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _parent not in sys.path:
        sys.path.insert(0, _parent)
    from track_record_logger import append_daily_record

    pe.load_state()
    now_et    = datetime.now(ET)
    today_str = now_et.strftime("%Y-%m-%d")

    account    = md.get_account()
    positions  = md.get_positions()
    raw_equity = float(account.get("equity", 0) or 0)
    cash       = float(account.get("cash", 0) or 0)
    equity     = pe.validate_equity(raw_equity, positions, cash)

    # Day P&L: use session_open_pl state set by profit-ratchet logic
    state           = pe.get_state()
    initial_equity  = state.get("initial_equity") or 0.0
    session_open_pl = state.get("session_open_pl")  # cumulative P&L at open

    if session_open_pl is not None and equity > 0 and initial_equity > 0:
        current_pl = equity - initial_equity
        day_pnl    = current_pl - session_open_pl
    else:
        # Fall back: first equity snapshot of today in equity_history
        from datetime import timezone as _tz
        hist = state.get("equity_history", [])
        today_start_ts = now_et.replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(_tz.utc).timestamp()
        today_vals = [h[1] for h in hist if h[0] >= today_start_ts]
        day_pnl    = (equity - today_vals[0]) if (today_vals and equity > 0) else 0.0

    # Today's fills from transactions.csv (price = pre-trade reference, no true slippage)
    txs        = pe.load_transactions(limit=500)
    today_txs  = [t for t in txs if t.get("timestamp", "").startswith(today_str)]
    num_trades = len(today_txs)
    fills = [
        {
            "symbol":            t.get("symbol", ""),
            "side":              t.get("side", ""),
            "qty":               float(t.get("qty", 0)),
            "actual_fill_price": float(t.get("price", 0)),
            "reason":            t.get("reason", ""),
        }
        for t in today_txs
    ] or None

    append_daily_record(
        "qc_portfolio",
        equity=equity,
        day_pnl=day_pnl,
        num_trades=num_trades,
        date_str=today_str,
        fills=fills,
    )
    log.info(
        f"[track_record] qc_portfolio logged: equity=${equity:,.2f} "
        f"day_pnl={day_pnl:+.2f} trades={num_trades}"
    )


def _intraday_loop(stop_event):
    """
    Intraday check cadence:
      - First 60 min after open: every INTRADAY_OPEN_CHECK_MINS (5 min)
      - Rest of session: every INTRADAY_CHECK_MINS (30 min)
    EOD: fires do_eod_log() once on the first tick after market close.
    """
    tick = 60  # sleep granularity
    elapsed = 0
    session_open_time = None
    eod_logged_date   = None  # track-record date already written today

    while not stop_event.is_set():
        time.sleep(tick)

        if not _is_market_hours():
            # First tick after close: write EOD track record (once per calendar day)
            if session_open_time is not None:
                today_str = datetime.now(ET).strftime("%Y-%m-%d")
                if eod_logged_date != today_str:
                    eod_logged_date = today_str
                    try:
                        do_eod_log()
                    except Exception as e:
                        log.error(f"EOD log error: {e}")
            session_open_time = None  # reset when market closes
            elapsed = 0
            continue

        # Mark session open time on first tick during market hours
        if session_open_time is None:
            session_open_time = datetime.now(ET)
            elapsed = 0  # fire immediately at open
            log.info("[INTRADAY] Market open — switching to 5-min ratchet checks.")

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


# ── Scheduler ────────────────────────────────────────────────────────────

def _next_rebalance_dt():
    """Return the next 9:31 ET datetime (today if not yet passed, else tomorrow)."""
    now_et   = datetime.now(ET)
    target   = now_et.replace(hour=9, minute=31, second=0, microsecond=0)
    if now_et >= target:
        target += timedelta(days=1)
    # Skip Saturday (5) and Sunday (6)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def _scheduler_loop(stop_event):
    """
    Background thread: wake at 9:31 ET each trading day, rebalance every
    REBALANCE_INTERVAL_DAYS trading days, skip otherwise.
    """
    trading_days_since_reb = REBALANCE_INTERVAL_DAYS  # fire on first eligible open

    while not stop_event.is_set():
        next_dt   = _next_rebalance_dt()
        now_et    = datetime.now(ET)
        wait_secs = (next_dt - now_et).total_seconds()
        log.info(f"Next market open: {next_dt.strftime('%Y-%m-%d %H:%M ET')}  "
                 f"(in {wait_secs/3600:.1f}h)  "
                 f"[{trading_days_since_reb}/{REBALANCE_INTERVAL_DAYS} days since last rebalance]")

        elapsed = 0
        while elapsed < wait_secs and not stop_event.is_set():
            time.sleep(min(60, wait_secs - elapsed))
            elapsed += 60

        if stop_event.is_set():
            break

        now_et = datetime.now(ET)
        delta  = abs((now_et - next_dt).total_seconds())
        if delta >= 120:
            log.warning(f"Scheduler drift detected ({delta:.0f}s) — skipping fire")
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
    """Launch the daily rebalance scheduler in a daemon thread."""
    if stop_event is None:
        stop_event = threading.Event()
    t = threading.Thread(target=_scheduler_loop, args=(stop_event,), daemon=True)
    t.start()
    log.info("Daily rebalance scheduler started.")
    return stop_event


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if "--rebalance" in args:
        do_rebalance()
        return

    if "--qc-refresh" in args:
        pe.load_state()
        changed = pe.refresh_qc(force=True)
        strats  = pe.get_strategies()
        weights = pe.get_weights()
        print(f"\nTop {len(strats)} QC Strategies by OOS 3M Return:")
        for i, (cfg, w) in enumerate(zip(strats, weights)):
            stats  = cfg.get("statistics", {})
            r3m    = stats.get("oos 3m return", 0)
            sharpe = stats.get("oos 3m sharpe", 0)
            print(f"  {i+1}. [{cfg['id']:4d}] {cfg.get('name',''):<40} "
                  f"3M: {r3m:>7.2f}%  Sharpe: {sharpe:>6.2f}  "
                  f"Weight: {w*100:>5.1f}%")
        print(f"\n{'Rankings changed' if changed else 'No change in top 5'}")
        return

    if "--daemon" in args:
        # Lock file — prevent duplicate daemon instances
        lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".daemon.lock")
        if os.path.exists(lock_path):
            try:
                existing_pid = int(open(lock_path).read().strip())
                if existing_pid != os.getpid():
                    try:
                        os.kill(existing_pid, 0)
                        log.warning(f"Daemon already running (PID {existing_pid}). Exiting.")
                        return
                    except OSError:
                        pass  # stale lock, overwrite
            except Exception:
                pass
        with open(lock_path, "w") as f:
            f.write(str(os.getpid()))

        log.info("Running in daemon (headless) mode.")
        stop_event = threading.Event()
        start_scheduler(stop_event)
        intraday_t = threading.Thread(
            target=_intraday_loop, args=(stop_event,), daemon=True)
        intraday_t.start()
        log.info(f"Intraday stop-check loop started (every {INTRADAY_CHECK_MINS} min during market hours).")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            log.info("Shutting down.")
            stop_event.set()
        finally:
            try: os.remove(lock_path)
            except: pass
            from config import ACTIVE_BROKER
            if ACTIVE_BROKER == "ibkr":
                try:
                    from ibkr_data import disconnect
                    disconnect()
                except Exception:
                    pass
        return

    # Default: GUI + background scheduler
    stop_event = start_scheduler()
    from gui import launch
    launch()
    stop_event.set()   # clean up scheduler when GUI closes


if __name__ == "__main__":
    main()
