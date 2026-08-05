"""
alpaca_paper_arm.py — Paper A/B test of the IBKR strategy on Alpaca.

Two portfolios, sharing the same picker + stop math as the live IBKR bot:

  mirror  — mirrors today's live IBKR positions apples-to-apples
  broad   — 20 names across a broad sub-$25 universe (statistical power)

Both use adaptive_stops.effective_trail + stop_manager._catalyst_widening
so VERA/AMRX etc. get the same catalyst widening on paper as on live.

Alpaca has no OCA, so stops are enforced in-process on the 1-min tick:
each position tracks a peak; if last <= peak * (1 - trail_pct) OR last <=
entry * (1 - hard_drop_pct), we submit a market-sell.

Usage:
    python alpaca_paper_arm.py --open mirror
    python alpaca_paper_arm.py --open broad
    python alpaca_paper_arm.py --open both
    python alpaca_paper_arm.py --watch                 # 1-min loop
    python alpaca_paper_arm.py --status
    python alpaca_paper_arm.py --close mirror|broad|all
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Import the same stop math the live bot uses
sys.path.insert(0, str(Path("/Users/quarki3/ibkr_bot")))
sys.path.insert(0, str(Path("/Users/quarki3/qc_portfolio")))
import stop_manager  # noqa: E402
import adaptive_stops  # noqa: E402
sys.path.insert(0, str(Path("/Users/quarki3")))
from ibkr_bot import screener  # noqa: E402

ENV = os.environ.get("ALPACA_ARM_ENV", "paper")   # "paper" | "live"
IS_LIVE = ENV == "live"

STATE_DIR = Path.home() / (f"alpaca_{ENV}_arm_state")
STATE_DIR.mkdir(exist_ok=True)
EQUITY_CSV = STATE_DIR / "equity.csv"
STOP_LOG = STATE_DIR / "stop_events.jsonl"
CLOSED_TRADES_CSV = STATE_DIR / "closed_trades.csv"

# Exit-philosophy floors (mirrors the IBKR bot audit):
#   - trail never tighter than 3x the symbol's ATR%
#   - exits (stop-outs) only submitted during regular market hours
TRAIL_MIN_ATR_MULT = 3.0

# Live sizing tuned for a $50 starting account:
#   broad only  → 12 × $3 = $36 deployed, ~$14 cash (28% buffer)
#   both arms   → +8 × $1.50 = $48 deployed, ~$2 cash (tight — use only if IBKR closed)
# NOTE: while IBKR still holds mirror-arm tickers (F, AAL, VERA, AMRX etc.), running
# mirror on Alpaca live doubles exposure — prefer broad-only until IBKR is wound down.
MIRROR_PER_POSITION_USD = 1.5 if IS_LIVE else 10.0
BROAD_TARGET_N = 12 if IS_LIVE else 20
BROAD_PER_POSITION_USD = 3.0 if IS_LIVE else 5.0

# Sub-$5 seed for the broad arm (added on top of screener.CHEAP_UNIVERSE)
SUB5_SEED = [
    "OCGN", "GRAB", "OPEN", "SOFI", "PLUG", "MARA", "RIOT",
    "NIO", "BBAI", "IONQ", "SIRI", "VALE", "XPEV", "LCID",
    "AAL", "F", "PARA", "SNAP", "PINS", "T",
]


# ── State I/O ─────────────────────────────────────────────────────────

def _state_file(arm: str) -> Path:
    return STATE_DIR / f"{arm}.json"


def _safe_write_text(path: Path, content: str) -> bool:
    """Best-effort write. A full disk (or any OS write error) must never crash
    the caller — 2026-07-03 the watcher died with no exception logged at all,
    consistent with a write failing inside the loop's own error handler."""
    try:
        path.write_text(content)
        return True
    except OSError as e:
        print(f"  [warn] write failed for {path}: {e}")
        return False


def _load_state(arm: str) -> dict:
    p = _state_file(arm)
    if not p.exists():
        return {"positions": {}, "opened_at": None}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"  [warn] {p} unreadable ({e}) — treating as empty state")
        return {"positions": {}, "opened_at": None}


def _save_state(arm: str, state: dict) -> None:
    _safe_write_text(_state_file(arm), json.dumps(state, indent=2))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_stop_event(evt: dict) -> None:
    try:
        with STOP_LOG.open("a") as f:
            f.write(json.dumps(evt) + "\n")
    except OSError as e:
        print(f"  [warn] stop_events.jsonl append failed: {e}")


def _log_closed_trade(pos: dict, arm: str, exit_px: float, exit_reason: str) -> None:
    """Append one row per closed position — this is the ledger promotion
    decisions read, so it must accumulate to n>=30 closed trades."""
    try:
        pnl_pct = round((exit_px / pos["entry_px"] - 1) * 100, 4)
    except ZeroDivisionError:
        pnl_pct = 0.0
    row = [
        pos.get("opened_at", ""), _now(), pos["symbol"], arm,
        f"{pos['entry_px']:.4f}", f"{exit_px:.4f}", f"{pnl_pct:.4f}", exit_reason,
    ]
    try:
        write_header = not CLOSED_TRADES_CSV.exists()
        with CLOSED_TRADES_CSV.open("a", newline="") as f:
            w = csv.writer(f)
            if write_header:
                w.writerow(["ts_open", "ts_close", "symbol", "arm", "entry",
                            "exit", "pnl_pct", "exit_reason"])
            w.writerow(row)
    except OSError as e:
        print(f"  [warn] closed_trades.csv append failed: {e}")


def _is_regular_market_hours(now: Optional[datetime] = None) -> bool:
    """True during 9:30-16:00 ET on a weekday. No holiday calendar — fails
    open (returns True) on any tz lookup error so a bug here can't silently
    block every exit forever."""
    try:
        from zoneinfo import ZoneInfo
        et = now or datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return True
    if et.weekday() >= 5:
        return False
    open_t = et.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = et.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= et <= close_t


# ── Alpaca client ─────────────────────────────────────────────────────

def _client():
    from alpaca.trading.client import TradingClient
    if IS_LIVE:
        key = os.environ.get("ALPACA_LIVE_KEY")
        sec = os.environ.get("ALPACA_LIVE_SECRET")
        if not (key and sec):
            raise RuntimeError(
                "ALPACA_LIVE_KEY / ALPACA_LIVE_SECRET not set. "
                "Generate live keys at app.alpaca.markets → Home → Generate New Keys, "
                "add to ~/.zshenv, then `source ~/.zshenv`."
            )
        return TradingClient(api_key=key, secret_key=sec, paper=False)
    return TradingClient(
        api_key=os.environ["ALPACA_PAPER_KEY"],
        secret_key=os.environ["ALPACA_PAPER_SECRET"],
        paper=True,
    )


def _quote(symbol: str) -> Optional[float]:
    """Latest price via yfinance (paper Alpaca data is IEX-only, misses small caps)."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        info = t.fast_info
        px = getattr(info, "last_price", None) or getattr(info, "regular_market_previous_close", None)
        if px is None:
            info2 = t.info
            px = info2.get("regularMarketPrice") or info2.get("currentPrice") or info2.get("previousClose")
        return float(px) if px else None
    except Exception:
        return None


def _submit_buy(client, symbol: str, qty: float) -> Optional[str]:
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    req = MarketOrderRequest(
        symbol=symbol, qty=qty, side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    try:
        resp = client.submit_order(req)
        return str(resp.id)
    except Exception as e:
        print(f"  buy {symbol}: {e}")
        return None


def _submit_sell(client, symbol: str, qty: float) -> Optional[str]:
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce
    req = MarketOrderRequest(
        symbol=symbol, qty=qty, side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    try:
        resp = client.submit_order(req)
        return str(resp.id)
    except Exception as e:
        print(f"  sell {symbol}: {e}")
        return None


# ── Universe builders ────────────────────────────────────────────────

def _mirror_universe() -> list[dict]:
    """Symbols currently held on the live IBKR bot, as {symbol, entry_hint}."""
    snap = Path("/Users/quarki3/ibkr_bot/positions_snapshot.json")
    if not snap.exists():
        return []
    data = json.loads(snap.read_text())
    return [
        {"symbol": p["symbol"], "entry_hint": float(p.get("market_price") or 0)}
        for p in data.get("positions", [])
    ]


def _broad_universe() -> list[str]:
    """Merge screener.CHEAP_UNIVERSE + SUB5_SEED, dedup, filter to <=$25."""
    seed = list(dict.fromkeys(screener.CHEAP_UNIVERSE + SUB5_SEED))
    return seed


# ── Stops ─────────────────────────────────────────────────────────────

def _compute_stops(symbol: str, entry_price: float) -> tuple[float, float, dict]:
    """Returns (trail_pct, hard_drop_pct, meta).  Both include catalyst widening."""
    trail_pct, comp = stop_manager.compute_trail_pct(symbol)
    # Approximate atr_pct back from components; fall back to trail/2 if missing
    atr_pct = comp.get("atr_pct") or (trail_pct / 2)
    # Exit-philosophy floor: never trail tighter than 3x ATR (mirrors IBKR audit).
    min_trail = TRAIL_MIN_ATR_MULT * atr_pct
    if min_trail > trail_pct:
        comp = {**comp, "raw_trail_pre_floor": round(trail_pct, 5),
                "trail_floor_3xatr": round(min_trail, 5)}
        trail_pct = min_trail
    hard_drop = stop_manager.compute_hard_drop(atr_pct, symbol=symbol)
    return trail_pct, hard_drop, comp


# ── Open / close ──────────────────────────────────────────────────────

def open_arm(arm: str) -> None:
    if arm == "both":
        open_arm("mirror")
        open_arm("broad")
        return

    client = _client()
    acct = client.get_account()
    print(f"[{arm}] paper equity=${float(acct.equity):,.2f} cash=${float(acct.cash):,.2f}")

    state = _load_state(arm)
    existing_syms = set(state["positions"].keys())

    if arm == "mirror":
        target_usd = MIRROR_PER_POSITION_USD
        candidates = _mirror_universe()
    else:
        target_usd = BROAD_PER_POSITION_USD
        seed = _broad_universe()
        candidates = [{"symbol": s, "entry_hint": 0.0} for s in seed]

    picks_added = 0
    for cand in candidates:
        sym = cand["symbol"]
        if sym in existing_syms:
            continue
        if arm == "broad" and (len(state["positions"]) + picks_added) >= BROAD_TARGET_N:
            break

        px = _quote(sym)
        if not px or px <= 0.2 or px > 500:
            print(f"  [{arm}] skip {sym}: bad quote {px}")
            continue

        qty = round(target_usd / px, 4)
        if qty * px < 1.0:  # Alpaca fractional min ~$1
            print(f"  [{arm}] skip {sym}: qty*px<$1")
            continue

        order_id = _submit_buy(client, sym, qty)
        if order_id is None:  # Try whole-share fallback for non-fractionable
            whole = int(target_usd / px)
            if whole >= 1:
                order_id = _submit_buy(client, sym, float(whole))
                if order_id:
                    qty = float(whole)
        if not order_id:
            continue

        trail_pct, hard_drop, meta = _compute_stops(sym, px)
        state["positions"][sym] = {
            "symbol": sym,
            "entry_px": px,
            "qty": qty,
            "peak_px": px,
            "trail_pct": round(trail_pct, 5),
            "hard_drop": round(hard_drop, 5),
            "stop_meta": meta,
            "opened_at": _now(),
            "buy_order_id": order_id,
        }
        picks_added += 1
        cat = meta.get("catalyst_type")
        cat_str = f" [{cat} in {meta.get('days_to_catalyst')}d, mult={meta.get('catalyst_mult')}]" if cat else ""
        print(f"  [{arm}] BUY {qty:g} {sym} @ ${px:.2f}  trail={trail_pct*100:.2f}%  hard={hard_drop*100:.2f}%{cat_str}")

    state["opened_at"] = state["opened_at"] or _now()
    _save_state(arm, state)
    print(f"[{arm}] opened {picks_added} new; total held = {len(state['positions'])}")


def close_arm(arm: str) -> None:
    if arm == "all":
        close_arm("mirror")
        close_arm("broad")
        return

    client = _client()
    state = _load_state(arm)
    if not state["positions"]:
        print(f"[{arm}] no positions to close")
        return

    for sym, pos in list(state["positions"].items()):
        oid = _submit_sell(client, sym, pos["qty"])
        if oid:
            print(f"  [{arm}] SELL {pos['qty']:g} {sym}  order {oid}")
            exit_px = _quote(sym) or pos["entry_px"]
            _log_closed_trade(pos, arm, exit_px, "manual_close")
            del state["positions"][sym]
        else:
            print(f"  [{arm}] SELL {sym} FAILED — left in state")

    _save_state(arm, state)


# ── Watch loop (1-min stop enforcement) ───────────────────────────────

def _check_stops(client, arm: str, state: dict) -> int:
    """Returns count of positions closed by stops on this pass."""
    closed = 0
    for sym, pos in list(state["positions"].items()):
        px = _quote(sym)
        if px is None:
            continue

        if px > pos["peak_px"]:
            pos["peak_px"] = px

        trail_stop = pos["peak_px"] * (1 - pos["trail_pct"])
        hard_stop = pos["entry_px"] * (1 - pos["hard_drop"])
        # Use whichever stop is HIGHER (tighter — protects more)
        stop_px = max(trail_stop, hard_stop)

        if px <= stop_px:
            if not _is_regular_market_hours():
                # Triggered, but only exit during RTH — avoid thin-liquidity
                # market sells on stale after-hours quotes.
                continue
            trigger = "trail" if trail_stop >= hard_stop else "hard"
            oid = _submit_sell(client, sym, pos["qty"])
            if oid is None:
                # Sell rejected (e.g. state qty out of sync with broker) — leave
                # the position in state so the next tick retries rather than
                # silently recording a close that never executed.
                print(f"  [{arm}] STOP-OUT {sym} FAILED to submit — retrying next tick")
                continue
            evt = {
                "ts": _now(), "arm": arm, "symbol": sym,
                "trigger": trigger, "px": px, "stop_px": round(stop_px, 4),
                "entry_px": pos["entry_px"], "peak_px": pos["peak_px"],
                "trail_pct": pos["trail_pct"], "hard_drop": pos["hard_drop"],
                "pl_pct": round((px / pos["entry_px"] - 1) * 100, 2),
                "sell_order_id": oid,
            }
            _log_stop_event(evt)
            _log_closed_trade(pos, arm, px, trigger)
            print(f"  [{arm}] STOP-OUT {sym} @ ${px:.2f} ({trigger}) — P/L {evt['pl_pct']:+.2f}%")
            del state["positions"][sym]
            closed += 1

    return closed


DD_ALERT_THRESHOLD_USD = 5.0        # $5 review trigger (per user rule)
DD_ALERT_STATE = STATE_DIR / "dd_alert_state.json"
BASELINE_FILE = STATE_DIR / "baseline_equity.json"


def _pushover(msg: str, title: str | None = None) -> None:
    """Best-effort Pushover notify. Loads creds from ibkr_bot/pushover.env if not in shell.
    No-op in paper mode — ntfy alerts are reserved for real-money activity."""
    if not IS_LIVE:
        return
    if title is None:
        title = f"Alpaca {ENV} arm DD alert"
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        pf = Path("/Users/quarki3/ntfy.env")
        if pf.exists():
            for line in pf.read_text().splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    if k.strip() == "NTFY_TOPIC": topic = v.strip().strip('"\'')
    if not topic:
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://ntfy.sh/{topic}", data=msg.encode(),
            headers={"Title": title.encode("ascii", "replace").decode(),
                     "Priority": "4"})
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _get_or_set_baseline(client) -> float | None:
    """First call: capture current account equity as baseline. Later: return it."""
    if BASELINE_FILE.exists():
        try:
            return float(json.loads(BASELINE_FILE.read_text())["baseline"])
        except Exception:
            pass
    try:
        eq = float(client.get_account().equity)
    except Exception:
        return None
    _safe_write_text(BASELINE_FILE, json.dumps({
        "baseline": eq, "captured_at": _now(), "env": ENV,
    }, indent=2))
    print(f"  [baseline] captured ${eq:.2f} as reference for $5 DD rule")
    return eq


def _dd_check(arm: str, client, state: dict) -> None:
    """Alert if account equity has fallen $5+ from the captured baseline.
    Baseline is captured on first watcher tick and persisted.
    Cooldown: 30 min per arm to prevent spam."""
    baseline = _get_or_set_baseline(client)
    if baseline is None:
        return
    try:
        equity = float(client.get_account().equity)
    except Exception:
        return
    bleed = equity - baseline   # negative when down
    if bleed > -DD_ALERT_THRESHOLD_USD:
        return

    now = datetime.now(timezone.utc)
    st = {}
    if DD_ALERT_STATE.exists():
        try:
            st = json.loads(DD_ALERT_STATE.read_text())
        except Exception:
            st = {}
    last = st.get(arm)
    if last:
        last_ts = datetime.fromisoformat(last)
        if (now - last_ts).total_seconds() < 1800:  # 30-min cooldown
            return
    env_tag = "REAL MONEY" if IS_LIVE else "paper"
    msg = f"[{env_tag}] Account DD ${bleed:+.2f} (baseline ${baseline:.2f} → now ${equity:.2f}) — REVIEW required"
    print(f"  ALERT: {msg}")
    _pushover(msg)
    st[arm] = now.isoformat()
    _safe_write_text(DD_ALERT_STATE, json.dumps(st, indent=2))


def _log_equity(client) -> None:
    """Append current combined paper equity to equity.csv."""
    try:
        acct = client.get_account()
        equity = float(acct.equity)
        cash = float(acct.cash)
    except Exception:
        return
    try:
        write_header = not EQUITY_CSV.exists()
        with EQUITY_CSV.open("a") as f:
            if write_header:
                f.write("ts,equity,cash\n")
            f.write(f"{_now()},{equity:.2f},{cash:.2f}\n")
    except OSError as e:
        print(f"  [warn] equity.csv append failed: {e}")


def _run_tick(client, tick: int) -> None:
    for arm in ("mirror", "broad"):
        state = _load_state(arm)
        if not state["positions"]:
            continue
        closed = _check_stops(client, arm, state)
        _save_state(arm, state)
        if closed:
            print(f"  [{arm}] closed {closed} on this pass; {len(state['positions'])} remain")
        _dd_check(arm, client, state)
    _log_equity(client)
    if tick % 10 == 0:
        print(f"tick {tick} @ {_now()}")


def watch(interval_sec: int = 60) -> None:
    client = _client()
    print(f"watching every {interval_sec}s — Ctrl-C to stop")
    tick = 0
    while True:
        tick += 1
        try:
            _run_tick(client, tick)
        except KeyboardInterrupt:
            print("stopped")
            return
        except Exception as e:
            # Never let a write failure inside this handler itself escape —
            # 2026-07-03 the watcher died with zero exception text logged,
            # consistent with a disk-full print() raising from right here.
            try:
                print(f"tick error: {e}")
            except Exception:
                pass
        time.sleep(interval_sec)


# ── Status ────────────────────────────────────────────────────────────

def status() -> None:
    for arm in ("mirror", "broad"):
        state = _load_state(arm)
        positions = state["positions"]
        if not positions:
            print(f"[{arm}] no positions")
            continue
        print(f"\n[{arm}]  {len(positions)} positions  opened {state.get('opened_at')}")
        print(f"  {'SYM':6s} {'ENTRY':>7s} {'NOW':>7s} {'PEAK':>7s} {'TRAIL%':>7s} {'HARD%':>7s} {'STOP@':>7s} {'P/L%':>7s}")
        print("  " + "-" * 70)
        total_cost = 0.0
        total_now = 0.0
        for sym, p in sorted(positions.items()):
            px = _quote(sym) or p["entry_px"]
            trail_stop = p["peak_px"] * (1 - p["trail_pct"])
            hard_stop = p["entry_px"] * (1 - p["hard_drop"])
            stop_px = max(trail_stop, hard_stop)
            cost = p["entry_px"] * p["qty"]
            val = px * p["qty"]
            total_cost += cost
            total_now += val
            pl_pct = (px / p["entry_px"] - 1) * 100
            print(f"  {sym:6s} ${p['entry_px']:>6.2f} ${px:>6.2f} ${p['peak_px']:>6.2f} "
                  f"{p['trail_pct']*100:>6.2f} {p['hard_drop']*100:>6.2f} ${stop_px:>6.2f} {pl_pct:>+6.2f}")
        tot_pl = total_now - total_cost
        tot_pl_pct = (tot_pl / total_cost * 100) if total_cost > 0 else 0
        print(f"  {'TOTAL':6s} ${total_cost:>6.2f} ${total_now:>6.2f}  {'':>7s} {'':>7s} {'':>7s} {'':>7s} {tot_pl_pct:>+6.2f}")


# ── CLI ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=["paper", "live"], default=None,
                    help="Override ALPACA_ARM_ENV. Default: paper.")
    ap.add_argument("--open", choices=["mirror", "broad", "both"])
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--close", choices=["mirror", "broad", "all"])
    args = ap.parse_args()

    if args.env and args.env != ENV:
        # Re-exec ourselves with the env var set — module-level constants need to re-init.
        env = os.environ.copy()
        env["ALPACA_ARM_ENV"] = args.env
        argv = [sys.executable, __file__] + [a for a in sys.argv[1:] if a not in ("--env", args.env)]
        os.execvpe(sys.executable, argv, env)
    print(f"[env={ENV}] state_dir={STATE_DIR}")

    if args.open:
        open_arm(args.open)
    elif args.watch:
        watch(args.interval)
    elif args.status:
        status()
    elif args.close:
        close_arm(args.close)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
