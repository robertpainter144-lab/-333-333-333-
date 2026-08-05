"""
Portfolio state management: strategy tracking, target computation,
stop-loss enforcement, and transaction logging.
"""
import csv
import json
import os
from datetime import datetime, timezone

from config import (
    STATE_FILE, TRANSACTIONS, BASE_DIR, MIN_REBALANCE_DOLLARS, MIN_REBALANCE_DRIFT,
    TRAIL_STOP_PCT, STRATEGY_DD_LIMIT, DD_LADDER, MAX_SINGLE_POSITION,
    MAX_NAME_CONCENTRATION, EQUITY_GATE_DEVIATION_PCT, EQUITY_GATE_HISTORY_LEN,
    PROFIT_RATCHET_TRIGGER, PROFIT_RATCHET_FLOOR_PCT, PROFIT_RATCHET_SELL_FRAC,
    ADAPTIVE_STOPS, ADAPTIVE_LADDER_SCALE,
    HARD_STOP_ATR_MULT, HARD_STOP_FLOOR_PCT,
    TIME_STOP_DAYS, TIME_STOP_PROGRESS_PCT, PROFIT_STEP_ATR_MULT,
    LEVERAGED_TICKERS, LEV_TIME_STOP_DAYS, LEV_MAX_PORTFOLIO_PCT,
    DD_LADDER_COOLDOWN_DAYS,
)
import adaptive_stops
from qc_api import fetch_top_strategies, build_strategy_config, format_stats
from broker import get_close_prices, get_current_prices
from momentum import get_allocation, compute_portfolio_weights

# ── In-memory state (persisted to STATE_FILE) ─────────────────────────────
_state = {
    "strategies":      [],   # list of config dicts for current top 5
    "weights":         [],   # portfolio weights [w1..w5] summing to 1
    "allocations":     {},   # {strategy_id: {symbol: weight}}
    "prev_strategy_ids": [], # IDs in last refresh (detect swaps)
    "peak_equity":     0.0,
    "peak_strat_vals": {},   # {str(id): peak_dollar_value}
    "trail_highs":     {},   # {symbol: highest_price_since_entry}
    "entry_prices":    {},   # {symbol: first_observed_price for hard-stop ref}
    "entry_dates":     {},   # {symbol: ISO date first observed}
    "step_taken":      {},   # {symbol: True if profit-step already triggered}
    "exposure_level":  1.0,  # current ladder exposure (1.0 = fully invested)
    "tiers_fired":     [],   # ladder thresholds already triggered this cycle
    "last_qc_refresh":       None,
    "last_rebalance":        None,
    "session_open_pl":       None,  # P&L at market open (baseline for session gain)
    "session_peak_pl":       0.0,   # highest session P&L seen today
    "ratchet_floor":         None,  # dollar floor; if P&L drops below this → sell
    "ratchet_fired_today":   False, # did ratchet trigger today already
    "ratchet_date":          None,  # date string to reset session state each day
    "equity_history":        [],    # [[unix_ts, equity_value], ...]
    "initial_equity":        0.0,   # first recorded equity (for all-time P&L)
    "accepted_equity_history": [],  # [[iso_ts_str, equity_value], ...] — gate-accepted only
}

GUARD_EVENTS_FILE = os.path.join(BASE_DIR, "guard_events.jsonl")


def _log_guard_event(guard, action, message, **extra):
    """Append one line to guard_events.jsonl (equity gate / name cap activations)."""
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "guard":     guard,
        "action":    action,
        "message":   message,
        **extra,
    }
    try:
        with open(GUARD_EVENTS_FILE, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")
    except Exception as e:
        print(f"[GUARD LOG] error: {e}")


def load_state():
    global _state
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                _state.update(json.load(f))
        except Exception:
            pass


def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(_state, f, indent=2, default=str)
    except Exception as e:
        print(f"[STATE] save error: {e}")


# ── QC leaderboard refresh ─────────────────────────────────────────────────

def refresh_qc(force=False):
    """
    Pull fresh top-5 from QC API, rebuild weights + allocations.
    Returns True if any strategy changed.
    """
    raw = fetch_top_strategies(5)
    if not raw:
        return False

    configs = [build_strategy_config(s) for s in raw]
    weights = compute_portfolio_weights(configs)

    new_ids = [c["id"] for c in configs]
    old_ids = _state.get("prev_strategy_ids", [])
    changed = (new_ids != old_ids)

    # Gather all symbols for one-shot price download
    all_syms = set()
    for cfg in configs:
        all_syms.update(cfg.get("universe", []))
    close_df = get_close_prices(list(all_syms), period="6mo")

    allocations = {}
    for cfg in configs:
        allocations[cfg["id"]] = get_allocation(cfg, close_df)

    _state["strategies"]        = configs
    _state["weights"]           = weights
    _state["allocations"]       = allocations
    _state["prev_strategy_ids"] = new_ids
    _state["last_qc_refresh"]   = datetime.now(timezone.utc).isoformat()
    save_state()
    return changed


# ── Target portfolio computation ──────────────────────────────────────────

def _apply_name_concentration_cap(target, total_equity):
    """
    No single symbol may exceed MAX_NAME_CONCENTRATION of total equity, even
    after cross-strategy aggregation stacks multiple sleeves onto the same
    name (e.g. three single_winner strategies all picking AMD). Excess is
    redistributed pro-rata across the remaining (non-capped) names.
    """
    if total_equity <= 0 or not target:
        return target

    cap = total_equity * MAX_NAME_CONCENTRATION
    fired = False
    capped_names = set()  # permanently excluded from receiving redistribution

    # Bounded iteration: each pass permanently caps at least one more name
    # (capped names never receive redistribution again, even in a later
    # round), so this converges in at most len(target) passes.
    for _ in range(len(target) + 1):
        newly_over = {s for s, v in target.items() if s not in capped_names and v > cap}
        if not newly_over:
            break
        fired = True
        excess = 0.0
        for sym in newly_over:
            excess += target[sym] - cap
            target[sym] = cap
            capped_names.add(sym)

        recipients = {s: v for s, v in target.items() if s not in capped_names and v > 0}
        recipient_total = sum(recipients.values())
        if excess <= 0:
            break
        if recipient_total <= 0:
            # No room to redistribute into — excess simply goes uninvested.
            break
        for sym, v in recipients.items():
            target[sym] += excess * (v / recipient_total)

    if fired:
        msg = (f"per-name cap {MAX_NAME_CONCENTRATION*100:.0f}% of ${total_equity:,.0f} "
               f"(${cap:,.0f}/name) applied; excess redistributed pro-rata")
        print(f"[NAME CAP] {msg}")
        _log_guard_event("name_cap", "applied", msg,
                          total_equity=total_equity, cap_dollars=cap,
                          target_after={s: round(v, 2) for s, v in target.items()})

    return target


def compute_target(total_equity, current_positions=None):
    """
    Build {symbol: target_dollar_amount} from current strategies/weights/allocations.

    Applies, in order:
      1) Per-symbol single-position cap (MAX_SINGLE_POSITION).
      2) Entry veto on leveraged ETFs when vol regime is hostile (refuse to open;
         existing positions still managed by stop logic).
      3) Aggregate cap on leveraged-ETF exposure (LEV_MAX_PORTFOLIO_PCT).

    current_positions: optional list of position dicts (symbol/market_value).
                        Used so the veto can hold existing positions but block additions.
    """
    strategies  = _state.get("strategies", [])
    weights     = _state.get("weights", [])
    allocations = _state.get("allocations", {})

    if not strategies or not weights:
        return {}

    target = {}
    for i, cfg in enumerate(strategies):
        strat_dollars = total_equity * weights[i]
        alloc = allocations.get(cfg["id"], {})
        for sym, w in alloc.items():
            target[sym] = target.get(sym, 0.0) + strat_dollars * w

    # 0) Cross-strategy per-name concentration cap — applied right after sleeve
    #    aggregation, before the legacy per-symbol cap below, so stacked
    #    single_winner sleeves can't pile the same name past this fraction.
    target = _apply_name_concentration_cap(target, total_equity)

    # 1) Per-symbol cap
    cap = total_equity * MAX_SINGLE_POSITION
    for sym in list(target.keys()):
        if target[sym] > cap:
            print(f"[CAP] {sym} ${target[sym]:,.0f} → capped at ${cap:,.0f} "
                  f"({MAX_SINGLE_POSITION*100:.0f}% of ${total_equity:,.0f})")
            target[sym] = cap

    # 2) Entry veto for leveraged ETFs in hostile regime
    ctx = adaptive_stops.get_market_context() if ADAPTIVE_STOPS else None
    current = {p.get("symbol"): float(p.get("market_value", 0))
               for p in (current_positions or [])}
    if ctx is not None:
        for sym in list(target.keys()):
            if sym not in LEVERAGED_TICKERS:
                continue
            allowed, reason = adaptive_stops.entry_allowed(sym, ctx)
            if allowed:
                continue
            # Cap leveraged target at current holding — don't open or add.
            held = current.get(sym, 0.0)
            if target[sym] > held:
                print(f"[VETO] {sym} entry blocked ({reason}); "
                      f"target ${target[sym]:,.0f} → ${held:,.0f}")
                target[sym] = held

    # 3) Aggregate leveraged-ETF cap
    lev_syms = [s for s in target if s in LEVERAGED_TICKERS]
    lev_total = sum(target[s] for s in lev_syms)
    lev_cap   = total_equity * LEV_MAX_PORTFOLIO_PCT
    if lev_total > lev_cap and lev_total > 0:
        scale = lev_cap / lev_total
        print(f"[LEV CAP] total leveraged ${lev_total:,.0f} > cap ${lev_cap:,.0f} "
              f"({LEV_MAX_PORTFOLIO_PCT*100:.0f}%) — scaling × {scale:.3f}")
        for s in lev_syms:
            target[s] *= scale

    return target


def rebalance_orders(current_positions, target_dollars, current_prices):
    """
    Compute buy/sell orders needed to move current → target.
    Returns list of (symbol, qty, side, reason).
    Does NOT execute — caller does.
    """
    current_dollars = {}
    for pos in current_positions:
        sym = pos.get("symbol", "")
        mv  = float(pos.get("market_value", 0))
        current_dollars[sym] = mv

    orders = []
    all_syms = set(list(target_dollars.keys()) + list(current_dollars.keys()))

    for sym in all_syms:
        target  = target_dollars.get(sym, 0.0)
        current = current_dollars.get(sym, 0.0)
        diff    = target - current

        price = current_prices.get(sym)
        if not price or price <= 0:
            continue

        if abs(diff) < MIN_REBALANCE_DOLLARS:
            continue
        if target > 0 and abs(diff) / target < MIN_REBALANCE_DRIFT:
            continue

        qty  = abs(diff) / price
        side = "buy" if diff > 0 else "sell"
        orders.append((sym, round(qty, 4), side, "rebalance"))

    # sells first so we have cash for buys
    orders.sort(key=lambda x: 0 if x[2] == "sell" else 1)
    return orders


# ── Stop-loss enforcement ─────────────────────────────────────────────────

def check_stop_losses(positions, current_prices, total_equity=None):
    """
    Evaluate per-symbol adaptive trailing/hard/time stops, profit step, and
    portfolio-level drawdown ladder. Returns list of (symbol, qty, side, reason).

    Per-symbol trail % is computed via adaptive_stops.effective_trail when
    ADAPTIVE_STOPS is True; otherwise the flat TRAIL_STOP_PCT is used.

    `total_equity` (optional) should be the broker's reported account equity =
    positions + cash. When supplied, the DD ladder tracks peak/drawdown against
    real total equity so re-entry is triggered by a genuine market recovery (not
    just calendar cooldown). When None (legacy callers), falls back to
    sum(market_value) and relies entirely on the cooldown to escape full-cash
    states.
    """
    trail_highs  = _state.get("trail_highs", {})
    entry_prices = _state.get("entry_prices", {})
    entry_dates  = _state.get("entry_dates", {})
    step_taken   = _state.get("step_taken", {})
    today_iso    = datetime.now(timezone.utc).date().isoformat()
    orders       = []
    decisions    = {}   # for logging / debugging

    ctx = adaptive_stops.get_market_context() if ADAPTIVE_STOPS else None

    for pos in positions:
        sym = pos.get("symbol", "")
        qty = float(pos.get("qty", 0))
        if not sym or qty <= 0:
            continue
        price = current_prices.get(sym)
        if not price:
            continue

        # Track entry on first sight (cold-start from broker positions: use avg_entry_price)
        broker_entry = float(pos.get("avg_entry_price", 0)) or price
        if sym not in entry_prices:
            entry_prices[sym] = broker_entry
            entry_dates[sym]  = today_iso
        entry = entry_prices[sym]

        # Trail high-water mark
        if sym not in trail_highs or price > trail_highs[sym]:
            trail_highs[sym] = price
        peak = trail_highs[sym]

        # Per-symbol effective trail
        if ADAPTIVE_STOPS:
            dec = adaptive_stops.effective_trail(sym, ctx, fallback_pct=TRAIL_STOP_PCT)
            trail_pct = dec.trail_pct
            atr_pct   = dec.atr_pct
            decisions[sym] = dec
        else:
            trail_pct = TRAIL_STOP_PCT
            atr_pct   = 0.0

        # 1) Hard stop — entry − min(floor, mult × ATR%)
        if atr_pct > 0:
            hard_drop = min(HARD_STOP_FLOOR_PCT, HARD_STOP_ATR_MULT * atr_pct)
        else:
            hard_drop = HARD_STOP_FLOOR_PCT
        if price <= entry * (1.0 - hard_drop):
            orders.append((sym, qty, "sell", "hard_stop"))
            continue

        # 2) Trail stop
        if trail_pct and price < peak * (1.0 - trail_pct):
            orders.append((sym, qty, "sell", "trail_stop"))
            continue

        # 3) Profit step — at +PROFIT_STEP_ATR_MULT × ATR%, sell half, move trail to BE
        if atr_pct > 0 and not step_taken.get(sym):
            step_trigger = entry * (1.0 + PROFIT_STEP_ATR_MULT * atr_pct)
            if price >= step_trigger:
                half = round(qty * 0.5, 4)
                if half > 0:
                    orders.append((sym, half, "sell", "profit_step"))
                    trail_highs[sym] = max(peak, entry)  # ratchet trail floor to breakeven
                    step_taken[sym] = True
                    continue

        # 4) Time stop — idle > N sessions with <1% progress
        try:
            d_entry = datetime.fromisoformat(entry_dates.get(sym, today_iso)).date()
            today_d = datetime.now(timezone.utc).date()
            held_days = (today_d - d_entry).days
        except Exception:
            held_days = 0
        time_limit = LEV_TIME_STOP_DAYS if sym in LEVERAGED_TICKERS else TIME_STOP_DAYS
        if held_days >= time_limit and price < entry * (1.0 + TIME_STOP_PROGRESS_PCT):
            orders.append((sym, qty, "sell", "time_stop"))
            continue

    # Clear state for any symbol we just fully exited
    flagged = {o[0] for o in orders if o[3] in ("trail_stop", "hard_stop", "time_stop")}
    for sym in flagged:
        trail_highs.pop(sym, None)
        entry_prices.pop(sym, None)
        entry_dates.pop(sym, None)
        step_taken.pop(sym, None)

    _state["trail_highs"]  = trail_highs
    _state["entry_prices"] = entry_prices
    _state["entry_dates"]  = entry_dates
    _state["step_taken"]   = step_taken

    # ── Portfolio-level drawdown ladder (adaptive thresholds) ───────────────
    # Prefer broker-reported total equity (positions + cash) when available so
    # peak/dd tracking survives full-cash liquidations. Fall back to position
    # market value for legacy callers that don't pass equity through.
    total_mktval = sum(float(p.get("market_value", 0)) for p in positions)
    if total_equity is not None and total_equity > 0:
        equity_basis = float(total_equity)
    else:
        equity_basis = total_mktval
    prev_peak    = _state.get("peak_equity", equity_basis)
    peak_eq      = max(prev_peak, equity_basis)
    _state["peak_equity"] = peak_eq

    exposure_level = _state.get("exposure_level", 1.0)
    tiers_fired    = set(_state.get("tiers_fired", []))
    tier_fired_at  = dict(_state.get("tier_fired_at", {}))  # {str(threshold): iso_date}

    # Cooldown sweep: expire tiers whose fired-at date is older than the cooldown.
    # Required because once a tier sells everything to cash, total_mktval drops
    # to 0 and peak_eq can never be exceeded by position-only equity, so the
    # peak-based reset alone leaves the book stuck in cash forever.
    today_d = datetime.now(timezone.utc).date()
    expired = []
    for k, iso in list(tier_fired_at.items()):
        try:
            fired_d = datetime.fromisoformat(iso).date()
            if (today_d - fired_d).days >= DD_LADDER_COOLDOWN_DAYS:
                expired.append(k)
        except Exception:
            expired.append(k)  # bad timestamp — drop it
    for k in expired:
        thr = float(k)
        tiers_fired.discard(thr)
        tier_fired_at.pop(k, None)
        print(f"[LADDER] tier {int(thr*100)}% expired after "
              f"{DD_LADDER_COOLDOWN_DAYS}d cooldown — re-entry permitted")
    if expired:
        # Restore full exposure once all tiers cleared, and re-baseline peak_eq
        # to current equity so a stale pre-DD peak doesn't immediately
        # re-fire the ladder on what's now a zero/depleted position book.
        if not tiers_fired:
            exposure_level = 1.0
            peak_eq = equity_basis
            _state["peak_equity"] = peak_eq
            prev_peak = peak_eq

    if peak_eq > prev_peak:
        tiers_fired.clear()
        tier_fired_at.clear()
        exposure_level = 1.0

    ladder = (adaptive_stops.adaptive_dd_ladder(DD_LADDER, ctx)
              if ADAPTIVE_LADDER_SCALE else list(DD_LADDER))

    if peak_eq > 0:
        dd       = (peak_eq - equity_basis) / peak_eq
        flagged2 = {o[0] for o in orders}

        for threshold, target_exp in sorted(ladder):
            if dd >= threshold and threshold not in tiers_fired and exposure_level > target_exp:
                sell_frac = 1.0 - (target_exp / exposure_level) if exposure_level > 0 else 1.0
                for pos in positions:
                    sym = pos.get("symbol", "")
                    if sym in flagged2:
                        continue
                    qty_sell = float(pos.get("qty", 0)) * sell_frac
                    if qty_sell > 0:
                        orders.append((sym, round(qty_sell, 4), "sell",
                                       f"ladder_{int(threshold*100)}pct"))
                        flagged2.add(sym)
                tiers_fired.add(threshold)
                tier_fired_at[str(threshold)] = today_d.isoformat()
                exposure_level = target_exp
                print(f"[LADDER] {dd*100:.1f}% drawdown — tier {int(threshold*100)}% "
                      f"→ {int(target_exp*100)}% invested  (ctx vix={ctx.vix:.1f})"
                      if ctx else
                      f"[LADDER] {dd*100:.1f}% drawdown — tier {int(threshold*100)}%")

    _state["exposure_level"] = exposure_level
    _state["tiers_fired"]    = list(tiers_fired)
    _state["tier_fired_at"]  = tier_fired_at

    save_state()

    # Stress-regime overlay (informational; engine can choose to act)
    if ctx and ctx.in_stress:
        print(f"[ADAPTIVE] STRESS regime — VIX={ctx.vix:.1f}; consider reducing exposure.")

    return orders


# ── Profit ratchet ────────────────────────────────────────────────────────

def check_profit_ratchet(positions, current_prices):
    """
    Intraday profit-locking ratchet.

    Once session P&L exceeds PROFIT_RATCHET_TRIGGER:
      - Track the session peak P&L
      - Set a floor = peak × (1 - PROFIT_RATCHET_FLOOR_PCT)
      - If current P&L drops below floor → sell PROFIT_RATCHET_SELL_FRAC of each position
      - Ratchet re-arms on new session peak (keeps locking in higher floors)

    Session state resets each calendar day.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _state.get("ratchet_date") != today:
        _state["ratchet_date"]         = today
        _state["session_open_pl"]      = None
        _state["session_peak_pl"]      = 0.0
        _state["ratchet_floor"]        = None
        _state["ratchet_fired_today"]  = False

    # Current unrealised P&L across all positions
    current_pl = sum(
        (current_prices.get(p.get("symbol",""), 0) - float(p.get("avg_entry_price", 0)))
        * float(p.get("qty", 0))
        for p in positions
        if p.get("symbol") in current_prices
    )

    # Anchor session baseline on first call of the day
    if _state["session_open_pl"] is None:
        _state["session_open_pl"] = current_pl

    session_gain = current_pl - _state["session_open_pl"]

    orders = []

    # Update peak
    if session_gain > _state["session_peak_pl"]:
        _state["session_peak_pl"] = session_gain
        # Re-arm floor at new peak (ratchets up automatically)
        if session_gain >= PROFIT_RATCHET_TRIGGER:
            new_floor = _state["session_open_pl"] + session_gain * (1 - PROFIT_RATCHET_FLOOR_PCT)
            if _state["ratchet_floor"] is None or new_floor > _state["ratchet_floor"]:
                _state["ratchet_floor"] = new_floor
                _state["ratchet_fired_today"] = False  # re-arm for higher floor
                print(f"[RATCHET] New peak gain ${session_gain:+.2f} → floor set at "
                      f"${new_floor:+.2f} (give back max "
                      f"${session_gain * PROFIT_RATCHET_FLOOR_PCT:.2f})")

    # Check if we've breached the floor
    floor = _state.get("ratchet_floor")
    if (floor is not None
            and not _state["ratchet_fired_today"]
            and current_pl < floor
            and positions):
        print(f"[RATCHET] Floor breached! P&L ${current_pl:+.2f} < floor ${floor:+.2f} "
              f"— selling {PROFIT_RATCHET_SELL_FRAC*100:.0f}% of positions")
        for pos in positions:
            sym = pos.get("symbol", "")
            qty = float(pos.get("qty", 0)) * PROFIT_RATCHET_SELL_FRAC
            if sym and qty > 0.001:
                orders.append((sym, round(qty, 4), "sell", "profit_ratchet"))
        _state["ratchet_fired_today"] = True

    save_state()
    return orders, session_gain, _state.get("ratchet_floor")


# ── Transaction log ───────────────────────────────────────────────────────

def log_tx(symbol, side, qty, price, strategy_id=None, reason=""):
    """Append one row to transactions.csv."""
    os.makedirs(os.path.dirname(TRANSACTIONS), exist_ok=True)
    write_header = not os.path.exists(TRANSACTIONS)
    with open(TRANSACTIONS, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(["timestamp", "symbol", "side", "qty", "price",
                        "value", "strategy_id", "reason"])
        w.writerow([
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            symbol, side, round(qty, 4), round(price, 4),
            round(qty * price, 2), strategy_id or "", reason,
        ])


def load_transactions(limit=200):
    """Return last `limit` rows of transactions.csv as list of dicts."""
    if not os.path.exists(TRANSACTIONS):
        return []
    rows = []
    with open(TRANSACTIONS, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows[-limit:]


# ── State getters ─────────────────────────────────────────────────────────

def get_state():
    return _state

def get_strategies():
    return _state.get("strategies", [])

def get_weights():
    return _state.get("weights", [])

def get_allocations():
    return _state.get("allocations", {})


# ── Equity sanity gate ──────────────────────────────────────────────────────

def _local_equity_estimate(positions, cash):
    """cash + sum(qty * last known price), from broker-reported position fields."""
    total = float(cash or 0.0)
    for pos in positions:
        mv = pos.get("market_value")
        if mv is not None:
            try:
                total += float(mv)
                continue
            except (TypeError, ValueError):
                pass
        try:
            total += float(pos.get("qty", 0) or 0) * float(pos.get("current_price", 0) or 0)
        except (TypeError, ValueError):
            pass
    return total


def _fills_since(ts_str):
    """True if any transaction was logged strictly after ts_str (same strftime format)."""
    if not ts_str:
        return True  # unknown baseline — don't treat as "no fills"
    txs = load_transactions(limit=50)
    return any(t.get("timestamp", "") > ts_str for t in txs)


def validate_equity(raw_equity, positions, cash=0.0):
    """
    Sanity-gate broker-reported equity before it reaches the DD ladder, peak
    tracker, or governor. Rejects `raw_equity` when it deviates more than
    EQUITY_GATE_DEVIATION_PCT from:
      (a) cash + sum(qty * last_price) computed locally, or
      (b) the median of the last EQUITY_GATE_HISTORY_LEN accepted readings,
          IF no fills were logged since the last accepted reading (a real
          trade explains a jump; a jump with no trade means bad data).

    On rejection: logs loudly to guard_events.jsonl and returns the last
    accepted equity unchanged — the raw value is never recorded into
    accepted history, equity_history, peak_equity, or exposure_level.
    """
    accepted_hist = _state.get("accepted_equity_history", [])
    last_good = accepted_hist[-1][1] if accepted_hist else 0.0

    try:
        raw_equity = float(raw_equity)
    except (TypeError, ValueError):
        raw_equity = 0.0

    if raw_equity <= 0:
        msg = f"non-positive/unreadable equity ({raw_equity}) — reusing last good ${last_good:,.2f}"
        print(f"[EQUITY GATE] REJECTED: {msg}")
        _log_guard_event("equity_gate", "rejected", msg,
                          raw_equity=raw_equity, fallback_equity=last_good)
        return last_good

    reasons = []

    local_est = _local_equity_estimate(positions, cash)
    if local_est > 0:
        dev_local = abs(raw_equity - local_est) / local_est
        if dev_local > EQUITY_GATE_DEVIATION_PCT:
            reasons.append(f"{dev_local*100:.1f}% off local est ${local_est:,.2f} "
                            f"(cash+positions)")

    if accepted_hist:
        vals = sorted(v for _, v in accepted_hist[-EQUITY_GATE_HISTORY_LEN:])
        n = len(vals)
        median = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2
        if median > 0:
            dev_med = abs(raw_equity - median) / median
            if dev_med > EQUITY_GATE_DEVIATION_PCT and not _fills_since(accepted_hist[-1][0]):
                reasons.append(f"{dev_med*100:.1f}% off {n}-reading median ${median:,.2f} "
                                f"with no fills since last reading")

    if reasons:
        msg = f"equity ${raw_equity:,.2f} — " + "; ".join(reasons) + \
              f" — reusing last good ${last_good:,.2f}"
        print(f"[EQUITY GATE] REJECTED: {msg}")
        _log_guard_event("equity_gate", "rejected", msg,
                          raw_equity=raw_equity, local_est=local_est,
                          fallback_equity=last_good)
        return last_good if last_good > 0 else raw_equity

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    accepted_hist.append([now_str, raw_equity])
    if len(accepted_hist) > EQUITY_GATE_HISTORY_LEN:
        accepted_hist = accepted_hist[-EQUITY_GATE_HISTORY_LEN:]
    _state["accepted_equity_history"] = accepted_hist
    save_state()
    return raw_equity


# ── Equity history ────────────────────────────────────────────────────────

def record_equity(equity_value: float):
    """Append an equity snapshot; called after each rebalance/stop check."""
    if equity_value <= 0:
        return
    now_ts = int(datetime.now(timezone.utc).timestamp())

    if not _state.get("initial_equity"):
        _state["initial_equity"] = equity_value

    hist = _state.get("equity_history", [])
    # Deduplicate: only record if ≥5 min since last snapshot
    if not hist or now_ts - hist[-1][0] >= 300:
        hist.append([now_ts, equity_value])
        if len(hist) > 5000:
            hist = hist[-5000:]
    _state["equity_history"] = hist
    save_state()


def get_equity_history():
    """Return {"timestamp": [...], "equity": [...]} for the GUI equity curve."""
    hist = _state.get("equity_history", [])
    if not hist:
        return {}
    return {
        "timestamp": [h[0] for h in hist],
        "equity":    [h[1] for h in hist],
    }


def get_initial_equity() -> float:
    return float(_state.get("initial_equity") or 0.0)
