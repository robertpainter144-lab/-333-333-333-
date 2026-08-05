"""
SPY Overlay (SMA200 tactical, hysteresis band) — IBKR shares-based deployment.

Strategy (backtest 14yr 2011-2026: Sharpe 1.02, DD 20.91%, CAGR 11.80%):
    BULL  (SPY > SMA200 * 1.01)  →  hold SCHX  (Schwab US Large-Cap, S&P 500-equivalent)
    BEAR  (SPY < SMA200 * 0.99)  →  hold SHY   (1-3yr Treasuries, capital preservation)
    inside the +/-1% band       →  hold whatever regime was last active (hysteresis)

Raw SMA200 cross flipped 78x 2011-2026 (15x in 2022 alone) — the 1% band and
flip-only trading below exist to cut that whipsaw down.

Whole-share targeting (IBKR has no fractional shares on this account).
Only manages SCHX + SHY positions — other holdings (F/AAL/etc) untouched.
Day-trade guard enforces no same-day sells.
Only trades on an actual regime FLIP — never rebalances on price drift.

Sizing for the eventual deployment: $460 in SCHX whole shares.

SAFETY: DRY_RUN_LOCKED below hard-locks the script to dry-run (no orders,
IBKR connection is read-only) regardless of CLI flags. It stays True until
the user has reviewed 3-5 clean dry-run sessions and explicitly approves
flipping it off.

Usage:
    python3.10 spy_overlay_ibkr.py                  # dry-run, decisions only
    python3.10 spy_overlay_ibkr.py --execute        # would place orders (blocked while locked)
    python3.10 spy_overlay_ibkr.py --budget 100     # different sizing
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yfinance as yf
from ib_insync import IB, Stock, LimitOrder, MarketOrder

ROOT           = Path(__file__).resolve().parent
STATE_FILE     = ROOT / "spy_overlay_ibkr_state.json"
HYST_STATE_FILE = ROOT / "spy_overlay_hysteresis_state.json"
LOG_FILE       = ROOT / "spy_overlay_ibkr.log"
DRYRUN_LOG_FILE = ROOT / "spy_overlay_dryrun.log"

# Day-trade guard + Pushover alerts, shared with the IBKR bot
sys.path.insert(0, str(ROOT.parent / "ibkr_bot"))
import day_trade_guard as DTG  # noqa: E402
import notify as PN            # noqa: E402

# ── Safety lock ─────────────────────────────────────────────────────────────
# Hard override: while True, the script always runs dry (no orders placed,
# no launchd auto-fire) no matter what CLI flags are passed. Flip to False
# only after: fixes reviewed, 3-5 clean dry-run sessions, explicit user
# approval, and user has 2FA-logged into IB Gateway.
DRY_RUN_LOCKED = True

# ── Strategy params (locked from backtest) ─────────────────────────────────
SMA_WINDOW      = 200
HYSTERESIS_BAND = 0.01    # 1% band around SMA200 to suppress whipsaw flips
SPY_SYMBOL      = "SPY"     # used only for regime signal (yfinance), not held
BULL_TICKER     = "SCHX"    # broad large-cap, >0.99 corr with SPY
BULL_EXCH       = "ARCA"
BEAR_TICKER     = "SHY"     # 1-3y treasuries
BEAR_EXCH       = "ARCA"
STRATEGY_SYMBOLS = {BULL_TICKER, BEAR_TICKER}  # only these are managed

# ── Default budget (matches allocation plan 2026-07-20, pending approval) ──
DEFAULT_BUDGET_USD = 460.00

# ── IBKR ──────────────────────────────────────────────────────────────────
import os as _os
IBKR_HOST = _os.environ.get("IBKR_GATEWAY_HOST", "127.0.0.1")
IBKR_PORT = int(_os.environ.get("IBKR_GATEWAY_PORT", "4001"))
IBKR_CLIENT_ID = 51

# ── Limit-pricing buffer for marketable BUY limit / SELL limit ────────────
LIMIT_BUFFER_BUY  = 1.005   # pay up to +0.5% over reference for fast fill
LIMIT_BUFFER_SELL = 0.995   # accept down to -0.5% under reference


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    line = f"{_now()}  {msg}"
    print(line)
    try:
        LOG_FILE.open("a").write(line + "\n")
    except Exception:
        pass


def _dryrun_log(msg: str) -> None:
    line = f"{_now()}  {msg}"
    try:
        DRYRUN_LOG_FILE.open("a").write(line + "\n")
    except Exception:
        pass


# ─── Regime ────────────────────────────────────────────────────────────────

def fetch_regime() -> dict:
    """Pull SPY 14mo of daily closes from yfinance, derive raw signal + ticker prices."""
    syms = [SPY_SYMBOL, BULL_TICKER, BEAR_TICKER]
    hist = yf.download(syms, period="14mo", progress=False,
                       auto_adjust=True, threads=True)
    close = hist["Close"].dropna(how="all").ffill()
    if close.empty or len(close) < SMA_WINDOW + 1:
        raise RuntimeError("not enough SPY history for 200-SMA")
    spy_last   = float(close[SPY_SYMBOL].iloc[-1])
    spy_sma200 = float(close[SPY_SYMBOL].iloc[-SMA_WINDOW:].mean())
    return {
        "as_of":      close.index[-1].date().isoformat(),
        "spy":        spy_last,
        "spy_sma200": spy_sma200,
        "spy_vs_sma": spy_last / spy_sma200 - 1,
        "ref_prices": {
            BULL_TICKER: float(close[BULL_TICKER].iloc[-1]),
            BEAR_TICKER: float(close[BEAR_TICKER].iloc[-1]),
        },
    }


# ─── Hysteresis state ──────────────────────────────────────────────────────

def load_hyst_state() -> dict:
    if HYST_STATE_FILE.exists():
        return json.loads(HYST_STATE_FILE.read_text())
    return {"regime": None, "updated": None}


def save_hyst_state(regime: str) -> None:
    HYST_STATE_FILE.write_text(json.dumps(
        {"regime": regime, "updated": _now()}, indent=2))


def resolve_regime(spy_last: float, sma200: float, prior_regime: str | None) -> str:
    """1% band around SMA200: only flip on a decisive break, else hold prior state."""
    upper = sma200 * (1 + HYSTERESIS_BAND)
    lower = sma200 * (1 - HYSTERESIS_BAND)
    if spy_last > upper:
        return "BULL"
    if spy_last < lower:
        return "BEAR"
    if prior_regime in ("BULL", "BEAR"):
        return prior_regime
    # No prior state (first run ever) and price is inside the band: fall
    # back to a plain SMA cross to seed an initial regime.
    return "BULL" if spy_last > sma200 else "BEAR"


# ─── IBKR helpers ──────────────────────────────────────────────────────────

def _qualify(ib: IB, symbol: str, exch: str) -> Stock:
    c = Stock(symbol, "SMART", "USD", primaryExchange=exch)
    q = ib.qualifyContracts(c)
    if not q:
        raise RuntimeError(f"could not qualify {symbol}/{exch}")
    return q[0]


def _last_price(ib: IB, contract) -> float:
    """Best-effort: IBKR delayed → fallback to whatever yfinance had."""
    ib.reqMarketDataType(3)
    t = ib.reqMktData(contract, "", snapshot=False, regulatorySnapshot=False)
    px = None
    for _ in range(15):
        ib.sleep(0.4)
        for fld in (t.ask, t.last, t.close, t.bid):
            try:
                v = float(fld)
                if v > 0:
                    px = v
                    break
            except Exception:
                pass
        if px:
            break
    ib.cancelMktData(contract)
    return px or 0.0


def _strategy_positions(ib: IB, acct: str) -> dict:
    """Return only SCHX/SHY positions; ignore everything else (F/AAL/etc)."""
    out = {}
    for p in ib.positions(acct):
        sym = p.contract.symbol
        qty = float(p.position)
        if sym in STRATEGY_SYMBOLS and abs(qty) > 0:
            out[sym] = {"qty": qty, "avg_cost": float(p.avgCost)}
    return out


def _nlv(ib: IB, acct: str) -> float:
    for s in ib.accountSummary(acct):
        if s.tag == "NetLiquidation":
            return float(s.value)
    return 0.0


# ─── Plan + execute ────────────────────────────────────────────────────────

def compute_plan(regime: str, budget: float, ref_prices: dict,
                 positions: dict) -> list:
    """Returns trades = [{symbol, side, qty, ref_px, reason}, ...].
    Only called on an actual regime flip (or first-ever run) — see main()."""
    target_sym = BULL_TICKER if regime == "BULL" else BEAR_TICKER
    target_px  = ref_prices.get(target_sym, 0)
    if target_px <= 0:
        return []
    target_qty = int(budget // target_px)  # whole shares only
    plan = []

    # Exit any strategy-managed positions that aren't the target ticker
    for sym, info in positions.items():
        if sym == target_sym:
            continue
        plan.append({
            "symbol": sym, "side": "SELL",
            "qty": int(info["qty"]),
            "ref_px": ref_prices.get(sym, info["avg_cost"]),
            "reason": f"exit (regime={regime}, target={target_sym})",
        })

    # Adjust target position toward target_qty
    held_qty = int(positions.get(target_sym, {}).get("qty", 0))
    diff = target_qty - held_qty
    if diff > 0:
        plan.append({
            "symbol": target_sym, "side": "BUY", "qty": diff,
            "ref_px": target_px,
            "reason": f"buy_to_target ({held_qty}→{target_qty}, regime={regime})",
        })
    elif diff < 0:
        plan.append({
            "symbol": target_sym, "side": "SELL", "qty": abs(diff),
            "ref_px": target_px,
            "reason": f"trim_to_target ({held_qty}→{target_qty}, regime={regime})",
        })
    return plan


def execute_trade(ib: IB, trade: dict) -> dict:
    sym, side, qty = trade["symbol"], trade["side"], trade["qty"]
    if qty <= 0:
        return {**trade, "status": "SKIP_zero_qty"}

    # Day-trade guard for sells
    if side == "SELL":
        ok, why = DTG.safe_to_sell(ib, sym)
        if not ok:
            return {**trade, "status": f"BLOCKED_DTG ({why})"}

    exch = BULL_EXCH if sym == BULL_TICKER else BEAR_EXCH
    contract = _qualify(ib, sym, exch)

    px = _last_price(ib, contract)
    if px <= 0:
        # Fall back to the snapshot ref
        px = float(trade.get("ref_px") or 0)
    if px <= 0:
        return {**trade, "status": "no_quote"}

    if side == "BUY":
        limit = round(px * LIMIT_BUFFER_BUY, 2)
    else:
        limit = round(px * LIMIT_BUFFER_SELL, 2)

    order = LimitOrder(side, qty, limit)
    order.tif        = "DAY"
    order.outsideRth = True
    order.transmit   = True

    t = ib.placeOrder(contract, order)
    ib.sleep(3.0)
    return {**trade, "order_id": t.order.orderId, "limit": limit,
            "status": str(t.orderStatus.status),
            "filled": float(t.orderStatus.filled or 0),
            "avg_fill": float(t.orderStatus.avgFillPrice or 0)}


# ─── Trade-history state (only written on real executions) ────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"regime": None, "last_run": None, "history": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))


# ─── Crash alerting ─────────────────────────────────────────────────────────

def _crash_alert(exc: BaseException) -> None:
    tb = traceback.format_exc()
    _log(f"CRASH: {type(exc).__name__}: {exc}\n{tb}")
    try:
        PN.push(
            title=f"SPY Overlay CRASHED: {type(exc).__name__}",
            message=f"{exc}\n\n{tb[-800:]}",
            priority=1,
        )
    except Exception:
        pass


# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="actually place orders (blocked while DRY_RUN_LOCKED=True)")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="compute and log intended actions without placing orders (default)")
    ap.add_argument("--budget", type=float, default=DEFAULT_BUDGET_USD,
                    help=f"USD allocated to this strategy (default ${DEFAULT_BUDGET_USD})")
    args = ap.parse_args()

    dry = not args.execute
    if args.execute and DRY_RUN_LOCKED:
        _log("SAFETY LOCK: DRY_RUN_LOCKED=True — ignoring --execute, running dry-run. "
             "Unlock only after user approval.")
        dry = True

    _log(f"=== spy_overlay_ibkr START  budget=${args.budget:.2f}  dry={dry} ===")

    # 1. Signal + hysteresis regime
    snap = fetch_regime()
    hyst = load_hyst_state()
    prior_regime = hyst.get("regime")
    regime = resolve_regime(snap["spy"], snap["spy_sma200"], prior_regime)
    first_run = prior_regime is None
    flipped = (not first_run) and regime != prior_regime
    upper = snap["spy_sma200"] * (1 + HYSTERESIS_BAND)
    lower = snap["spy_sma200"] * (1 - HYSTERESIS_BAND)

    _log(f"signal  as_of={snap['as_of']}  SPY=${snap['spy']:.2f} "
         f"SMA200=${snap['spy_sma200']:.2f} ({snap['spy_vs_sma']*100:+.2f}%) "
         f"band=[${lower:.2f},${upper:.2f}]  prior={prior_regime}  regime={regime}  "
         f"flip={flipped}  first_run={first_run}")
    _log(f"prices  {BULL_TICKER}=${snap['ref_prices'][BULL_TICKER]:.2f}  "
         f"{BEAR_TICKER}=${snap['ref_prices'][BEAR_TICKER]:.2f}")

    # Hysteresis state persists every run regardless of dry/execute — it
    # tracks the market, not whether we actually traded.
    save_hyst_state(regime)

    # 2. Connect IBKR (read-only in dry-run: only used to read positions/NLV)
    ib = IB()
    try:
        ib.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=15)
        acct = ib.managedAccounts()[0]
        nlv  = _nlv(ib, acct)
        positions = _strategy_positions(ib, acct)
        _log(f"ibkr    acct={acct}  NLV=${nlv:.2f}  "
             f"strategy_positions={ {s: int(p['qty']) for s,p in positions.items()} }")

        # 3. Plan — flip-only trading: never rebalance on drift, only on an
        # actual regime flip (or to establish the first-ever position).
        if flipped or first_run:
            plan = compute_plan(regime, args.budget, snap["ref_prices"], positions)
        else:
            plan = []
            _log("plan    no flip — holding position, drift not rebalanced")

        if plan:
            _log(f"plan    {len(plan)} trade(s):")
            for t in plan:
                _log(f"          {t['side']:<4} {t['qty']} {t['symbol']:<5} "
                     f"@ref=${t['ref_px']:.2f}   {t['reason']}")
        elif flipped or first_run:
            _log("plan    no trades needed (already in target)")

        # 4. Execute (or log intended actions in dry-run)
        results = []
        if plan and not dry:
            for t in plan:
                try:
                    r = execute_trade(ib, t)
                    _log(f"  EXEC  {r}")
                    results.append(r)
                except Exception as e:
                    _log(f"  ERR   {t}: {e}")
        elif dry:
            _dryrun_log(f"regime={regime} prior={prior_regime} flip={flipped} "
                        f"first_run={first_run} budget=${args.budget:.2f} "
                        f"SPY=${snap['spy']:.2f} SMA200=${snap['spy_sma200']:.2f} "
                        f"band=[${lower:.2f},${upper:.2f}]")
            if plan:
                for t in plan:
                    _dryrun_log(f"WOULD {t['side']} {t['qty']} {t['symbol']} "
                                f"@ref=${t['ref_px']:.2f}  {t['reason']}")
            else:
                _dryrun_log("WOULD no trades (no flip / already in target)")
            _log("dry-run (pass --execute to fire these orders; blocked while locked)")

        # 5. Persist trade-history state (only on real executions)
        prior_trade_regime = load_state().get("regime")
        if not dry:
            state = load_state()
            state["regime"]   = regime
            state["last_run"] = _now()
            state.setdefault("history", []).append({
                "ts": _now(), "regime": regime, "prior": prior_trade_regime,
                "trades": [{k: v for k, v in r.items() if k != "ref_px"}
                           for r in results],
            })
            state["history"] = state["history"][-200:]
            save_state(state)

        _log(f"=== DONE  regime={regime}  prior={prior_regime}  "
             f"flip={flipped}  trades_placed={len(results)} ===")
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _crash_alert(e)
        sys.exit(1)
