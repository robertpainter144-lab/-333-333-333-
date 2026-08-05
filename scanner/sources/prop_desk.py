"""Prop-desk-lite tick source.

Every 30s:
  1. Fetch prices (batched CoinGecko).
  2. Append to state/prices/<SYM>.jsonl (same file the old momentum source used).
  3. Portfolio manager runs all strategies, applies constraints.
  4. Emit ACT_ENTRY / ACT_EXIT signals to the unified feed.
  5. Log closed trades to state/prop_ledger.jsonl.
  6. Heartbeat to live_activity: regime, held, top candidates by strategy.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit, log_tick, emit_live, STATEDIR
from strategies.base import UNIVERSE, ROUNDTRIP_COST_PCT, pct
from portfolio import manage

PRICE_DIR   = STATEDIR / "prices"
HOLD_STATE  = STATEDIR / "prop_hold.json"
LEDGER      = STATEDIR / "prop_ledger.jsonl"
LIVE_STATE  = STATEDIR / "prop_state.json"    # written every tick for the UI
COUNTERS    = STATEDIR / "prop_counters.json"


def _load_counters() -> dict:
    if COUNTERS.exists():
        try:
            return json.loads(COUNTERS.read_text())
        except Exception:
            pass
    return {
        "started_ts":         int(time.time()),
        "ticks":              0,
        "symbols_scanned":    0,
        "signals_fired":      {"momentum": 0, "mean_reversion": 0, "breakout": 0, "whale_primary": 0},
        "gated_by_reason":    {"cost_gate": 0, "cluster_cap": 0},
        "entries_executed":   {"momentum": 0, "mean_reversion": 0, "breakout": 0, "whale_primary": 0},
        "exits_by_outcome":   {"win": 0, "loss": 0, "wash": 0},
    }


def _save_counters(c: dict) -> None:
    STATEDIR.mkdir(exist_ok=True)
    tmp = COUNTERS.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=2, sort_keys=True))
    tmp.replace(COUNTERS)

CG_URL = "https://api.coingecko.com/api/v3/simple/price"

# ─── Alpaca execution (paper first) ────────────────────────────────────
# Set ALPACA_MODE=live in env to promote to live account. Default is paper.
import os
ALPACA_MODE     = os.environ.get("ALPACA_MODE", "paper")   # paper | live | signal_only
ALPACA_PAPER    = "https://paper-api.alpaca.markets"
ALPACA_LIVE     = "https://api.alpaca.markets"
ALPACA_BASE     = ALPACA_PAPER if ALPACA_MODE == "paper" else ALPACA_LIVE
ALPACA_KEY      = (os.environ.get("ALPACA_PAPER_KEY") if ALPACA_MODE == "paper"
                   else os.environ.get("ALPACA_LIVE_KEY", ""))
ALPACA_SECRET   = (os.environ.get("ALPACA_PAPER_SECRET") if ALPACA_MODE == "paper"
                   else os.environ.get("ALPACA_LIVE_SECRET", ""))

def _alpaca_headers() -> dict:
    return {
        "APCA-API-KEY-ID":     ALPACA_KEY or "",
        "APCA-API-SECRET-KEY": ALPACA_SECRET or "",
        "Content-Type":        "application/json",
    }


def _live_promoted() -> bool:
    """True only if the auto-tuner has promoted a strategy to live (ready_for_live).
    Fails closed: any error reading the config blocks live trading."""
    try:
        import auto_tune
        return bool(auto_tune.load_config().get("ready_for_live"))
    except Exception:
        return False


def _submit_order(sym: str, side: str, notional_usd: float | None = None,
                   qty: float | None = None) -> dict:
    """POST /v2/orders. Uses `notional` for BUY (dollar-in), `qty` for SELL
    (dollar-out via qty from position). Returns the order dict or {"error": ...}.
    """
    if ALPACA_MODE == "signal_only" or not ALPACA_KEY:
        return {"skipped": True, "mode": ALPACA_MODE}
    # Promotion gate: live orders require a strategy the auto-tuner has cleared
    # (n>=30, mean>=0.05%, Sharpe>=1, late-mean>=0.02%). Without it, prop-desk
    # stays signal-only regardless of ALPACA_MODE. Paper trading is unaffected.
    if ALPACA_MODE == "live" and not _live_promoted():
        return {"skipped": True, "mode": "live_blocked_unpromoted"}
    body: dict = {
        "symbol":         f"{sym}/USD",
        "side":           side.lower(),
        "type":           "market",
        "time_in_force":  "gtc",
    }
    if notional_usd is not None:
        body["notional"] = f"{notional_usd:.2f}"
    if qty is not None:
        body["qty"] = f"{qty:.10f}".rstrip("0").rstrip(".")
    try:
        r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=_alpaca_headers(),
                          json=body, timeout=15)
        if not r.ok:
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        return r.json()
    except Exception as e:
        return {"error": repr(e)}


def _get_position(sym: str) -> dict | None:
    if not ALPACA_KEY:
        return None
    try:
        # Alpaca crypto symbol in URL is with slash encoded as %2F
        alpaca_sym = f"{sym}USD"
        r = requests.get(f"{ALPACA_BASE}/v2/positions/{alpaca_sym}",
                         headers=_alpaca_headers(), timeout=10)
        if r.status_code == 404:
            return None
        if not r.ok:
            return None
        return r.json()
    except Exception:
        return None


def _get(url, params, timeout=15):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def fetch_prices() -> dict:
    ids = ",".join(cg for cg, _, _ in UNIVERSE.values())
    try:
        return _get(CG_URL, {
            "ids": ids, "vs_currencies": "usd",
            "include_24hr_vol":    "true",
            "include_24hr_change": "true",
        })
    except Exception:
        return {}


def _append_series(sym: str, row: dict) -> None:
    PRICE_DIR.mkdir(parents=True, exist_ok=True)
    p = PRICE_DIR / f"{sym}.jsonl"
    existing = []
    if p.exists():
        try:
            existing = p.read_text().splitlines()[-119:]
        except Exception:
            existing = []
    existing.append(json.dumps(row))
    p.write_text("\n".join(existing) + "\n")


def _load_holds() -> dict:
    if not HOLD_STATE.exists():
        return {}
    try:
        return json.loads(HOLD_STATE.read_text())
    except Exception:
        return {}


def _save_holds(d: dict) -> None:
    STATEDIR.mkdir(exist_ok=True)
    tmp = HOLD_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True))
    tmp.replace(HOLD_STATE)


def _log_closed(row: dict) -> None:
    STATEDIR.mkdir(exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")


def tick() -> None:
    now = int(time.time())
    prices = fetch_prices()
    price_fetch_ok = bool(prices)
    if not price_fetch_ok:
        log_tick("prop_desk", "coingecko fetch failed (using cached series)")
        # Don't return — we still update state / run strategies from cached prices.
        prices = {}
    else:
        # 1. Append prices to per-token series
        for sym, (cg, _, _) in UNIVERSE.items():
            p = prices.get(cg)
            if not p:
                continue
            _append_series(sym, {
                "ts":     now,
                "price":  float(p.get("usd", 0)),
                "vol24h": float(p.get("usd_24h_vol", 0) or 0),
            })

    # 2. Portfolio manager runs strategies + constraints
    holds = _load_holds()
    acts, regime, per_sym_agg, rejections, per_strat = manage(holds)
    _save_holds(holds)

    # 2b. Cumulative counters for the Sankey
    counters = _load_counters()
    counters["ticks"] += 1
    counters["symbols_scanned"] += len(UNIVERSE)
    for strat_name, sigs in per_strat.items():
        counters["signals_fired"].setdefault(strat_name, 0)
        counters["signals_fired"][strat_name] += sum(1 for s in sigs if s.action == "BUY")
    for r in rejections:
        counters["gated_by_reason"].setdefault(r["reason"], 0)
        counters["gated_by_reason"][r["reason"]] += 1
    for a in acts:
        if a["action"] == "BUY":
            counters["entries_executed"].setdefault(a["strategy"], 0)
            counters["entries_executed"][a["strategy"]] += 1

    # 2a. Whale confluence (also read here so the heartbeat can display top wallets)
    from portfolio import load_whale_confluence
    whale_agg = load_whale_confluence(now)

    entries_n = exits_n = 0
    for a in acts:
        if a["action"] == "BUY":
            entries_n += 1
            # ─── Execute on Alpaca (paper by default) ───────────────
            order_res = _submit_order(a["sym"], "buy", notional_usd=a["size_usd"])
            order_note = "signal_only"
            if "error" in order_res:
                order_note = f"ALPACA_ERR: {order_res['error']}"
            elif "id" in order_res:
                order_note = f"alpaca_{ALPACA_MODE}_order={order_res['id'][:8]}"
            elif order_res.get("skipped"):
                order_note = f"skipped_{order_res['mode']}"
            a["alpaca_order"] = order_res

            wscore = a.get("whale_score", 0)
            wmult  = a.get("whale_mult", 1.0)
            whits  = a.get("whale_hits", [])
            whale_str = f"whale_score={wscore:+.1f} ×{wmult:.1f}"
            if whits:
                whale_str += f" hits={','.join(sorted(set(whits))[:4])}"
            emit("prop_desk", "ACT_ENTRY", a["sym"], "alpaca",
                 size_usd=a["size_usd"], direction="BUY",
                 dedup_key=f"prop_ent:{a['sym']}:{now // 30}",
                 note=(f"ENTER {a['sym']}  ${a['size_usd']}  strat={a['strategy']} "
                       f"ensemble={a.get('ensemble',1)}  "
                       f"expected={a['expected_move']:.2f}%  score={a['score']}  "
                       f"regime={regime}  {whale_str}  [{order_note}]  {a['note']}"),
                 url=f"https://app.alpaca.markets/crypto/{a['sym']}USD",
                 raw=a)
        else:
            exits_n += 1
            # For SELL: get current Alpaca position quantity if execution is on
            qty = None
            pos = _get_position(a["sym"])
            if pos:
                try:
                    qty = float(pos.get("qty", 0))
                except Exception:
                    qty = None
            order_res = _submit_order(a["sym"], "sell", qty=qty) if qty else {"skipped": True, "mode": "no_pos"}
            order_note = "signal_only"
            if "error" in order_res:
                order_note = f"ALPACA_ERR: {order_res['error']}"
            elif "id" in order_res:
                order_note = f"alpaca_{ALPACA_MODE}_order={order_res['id'][:8]}"
            elif order_res.get("skipped"):
                order_note = f"skipped_{order_res.get('mode', 'unknown')}"
            a["alpaca_order"] = order_res

            emit("prop_desk", "ACT_EXIT", a["sym"], "alpaca",
                 size_usd=a["size_usd"], direction="SELL",
                 dedup_key=f"prop_exit:{a['sym']}:{now // 30}",
                 note=(f"EXIT {a['sym']}  pnl_est {a['pnl_pct_est']:+.2f}% (net of "
                       f"{ROUNDTRIP_COST_PCT:.2f}% cost)  [{order_note}]  {a['note']}"),
                 url=f"https://app.alpaca.markets/crypto/{a['sym']}USD",
                 raw=a)
            _log_closed({
                "sym": a["sym"], "strategy": a["strategy"],
                "entry_ts": now, "entry_px": a["entry_px"],
                "exit_ts":  now, "exit_px":  a["px"],
                "pnl_pct":  a["pnl_pct_est"],
                "size_usd": a["size_usd"],
                "note":     a["note"],
                "alpaca_mode": ALPACA_MODE,
            })

    # 3. RICH per-tick heartbeat — multiple live rows so the app feels alive.
    #    Also compute per-strategy movers so user sees WHY nothing is trading.
    from strategies.base import load_series, realized_vol_pct, compute_rsi, pct, price_at_offset

    # a) Regime + BTC context (with execution mode)
    btc_series = load_series("BTC", n=120)
    btc_vol    = realized_vol_pct(btc_series, horizon_s=3600) if btc_series else 0
    btc_px     = btc_series[-1]["price"] if btc_series else 0
    active_strats = {"trending": ["momentum", "breakout"], "chop": ["mean_reversion"],
                     "mixed": ["momentum", "mean_reversion", "breakout"],
                     "unknown": []}.get(regime, [])
    mode_tag = f"MODE={ALPACA_MODE.upper()}"
    if ALPACA_MODE in ("paper", "live") and not ALPACA_KEY:
        mode_tag = f"MODE={ALPACA_MODE.upper()}-NO_KEY"
    emit_live("prop_desk",
              f"[regime] {regime.upper()}  BTC 1h vol={btc_vol:.2f}%  BTC=${btc_px:,.0f}  "
              f"active_strats={','.join(active_strats) or 'none'}  held={len(holds)}  {mode_tag}",
              regime=regime, btc_vol=btc_vol, alpaca_mode=ALPACA_MODE)

    # b) Held positions with live P&L
    if holds:
        pnl_bits = []
        for s in sorted(holds.keys()):
            pr = prices.get(UNIVERSE[s][0], {})
            cur_px = float(pr.get("usd", 0)) if pr else holds[s]["entry_px"]
            pnl = pct(cur_px, holds[s]["entry_px"])
            pnl_bits.append(f"{s}:{pnl:+.2f}%")
        emit_live("prop_desk", "[held] " + "  ".join(pnl_bits), held=list(holds.keys()))

    # c) Top movers by |sigma| — show market pulse regardless of signals
    sigma_rows = []
    for sym in UNIVERSE:
        ser = load_series(sym, n=60)
        if len(ser) < 4:
            continue
        now_ts = ser[-1]["ts"]
        px_2m = price_at_offset(ser, now_ts, 120)
        if px_2m is None:
            continue
        v_2m = pct(ser[-1]["price"], px_2m)
        typ_2m = realized_vol_pct(ser, horizon_s=120)
        sig  = v_2m / typ_2m if typ_2m > 0 else 0
        sigma_rows.append((sym, v_2m, sig, typ_2m))
    sigma_rows.sort(key=lambda r: abs(r[2]), reverse=True)
    if sigma_rows:
        movers_str = "  ".join(
            f"{s}:{v:+.2f}%({sig:+.1f}σ)" for s, v, sig, _ in sigma_rows[:6])
        emit_live("prop_desk", f"[movers 2m] {movers_str}")

    # d) Per-strategy trace — show what each strategy is doing this tick
    for strat_name, sigs in per_strat.items():
        buys  = [s for s in sigs if s.action == "BUY"]
        sells = [s for s in sigs if s.action == "SELL"]
        top   = sorted(sigs, key=lambda s: s.score, reverse=True)[:3] if sigs else []
        top_str = ", ".join(f"{s.sym}:{s.score:+.2f}" for s in top) or "-"
        emit_live("prop_desk",
                  f"[strat_{strat_name[:3]}] BUYs={len(buys)} SELLs={len(sells)}  top: {top_str}")

    # d2) Per-hold decision trace — show WHY we're still holding vs exiting
    if holds:
        hold_traces = []
        for sym, h in holds.items():
            ser = load_series(sym, n=20)
            if not ser:
                continue
            px_now = ser[-1]["price"]
            pnl = pct(px_now, h["entry_px"])
            peak = h.get("peak_px", h["entry_px"])
            drawdown = pct(px_now, peak)
            hold_traces.append(f"{sym}(pnl {pnl:+.2f}%, dd {drawdown:+.2f}%, strat={h.get('strategy','?')[:3]})")
        emit_live("prop_desk", f"[eval_holds] " + " | ".join(hold_traces))

    # e) Rejections — cost gate / cluster cap surface
    if rejections:
        rej_str = "  ".join(
            f"{r['sym']}({r['strategy'][:3]}:{r['reason']})" for r in rejections[:6])
        emit_live("prop_desk", f"[gated] {rej_str}")

    # e) Signal-fire summary
    hot = sorted(
        [(sym, agg) for sym, agg in per_sym_agg.items() if agg["buys"] or agg["sells"]],
        key=lambda kv: (kv[1]["buys"] + kv[1]["sells"]), reverse=True,
    )[:6]
    hot_str = "  ".join(f"{sym}(B{a['buys']}/S{a['sells']})" for sym, a in hot) or "quiet"
    emit_live("prop_desk",
              f"[signals] fired: {hot_str}  ->  entries={entries_n}  exits={exits_n}")

    # f) Whale confluence — always show, so user can see what big money is doing
    if whale_agg:
        top_whales = sorted(whale_agg.items(), key=lambda kv: kv[1]["score"], reverse=True)[:6]
        w_str = "  ".join(
            f"{sym}:{d['score']:+.1f}({len(d['hits'])})" for sym, d in top_whales)
        emit_live("prop_desk", f"[whales 30m] {w_str}")
    else:
        emit_live("prop_desk", "[whales 30m] (no confluence hits in window)")

    log_tick("prop_desk", f"regime={regime} held={len(holds)} act={entries_n+exits_n}")

    # ─── Write structured live state for the UI (Android browser dashboard) ─
    held_out = []
    for s in sorted(holds.keys()):
        pr = prices.get(UNIVERSE[s][0], {})
        cur_px = float(pr.get("usd", 0)) if pr else holds[s]["entry_px"]
        pnl = pct(cur_px, holds[s]["entry_px"])
        peak = holds[s].get("peak_px", holds[s]["entry_px"])
        held_out.append({
            "sym":         s,
            "entry_px":    holds[s]["entry_px"],
            "cur_px":      cur_px,
            "pnl_pct":     round(pnl, 3),
            "peak_px":     peak,
            "drawdown":    round(pct(cur_px, peak), 3),
            "strategy":    holds[s].get("strategy", "?"),
            "size_usd":    holds[s].get("size_usd", 0),
            "whale_score": holds[s].get("whale_score", 0),
            "age_s":       now - holds[s].get("entry_ts", now),
        })
    strat_out = {}
    for name, sigs in per_strat.items():
        buys = [s for s in sigs if s.action == "BUY"]
        sells = [s for s in sigs if s.action == "SELL"]
        top = sorted(sigs, key=lambda s: s.score, reverse=True)[:5]
        strat_out[name] = {
            "buys":  len(buys), "sells": len(sells),
            "top":   [{"sym": s.sym, "score": round(s.score, 3),
                        "action": s.action, "note": s.note[:80]} for s in top],
        }
    # Auto-tune snapshot + recent decisions for the UI's "brain" panel
    tune_cfg = {}
    recent_decisions: list = []
    strat_health: dict = {}
    try:
        import auto_tune
        tune_cfg = auto_tune.load_config()
        recent_decisions = auto_tune.last_decisions(n=30)
        strat_health = auto_tune.strategy_health(auto_tune.load_ledger())
    except Exception:
        pass

    live_state = {
        "ts":            now,
        "regime":        regime,
        "btc_vol_1h":    round(btc_vol, 3),
        "btc_price":     round(btc_px, 2),
        "active_strats": active_strats,
        "alpaca_mode":   ALPACA_MODE,
        "auto_tune":     {
            "enabled_strategies": [k for k, v in tune_cfg.get("strategies", {}).items()
                                    if v.get("enabled")],
            "min_expected_pct":   tune_cfg.get("min_expected_pct", 0.7),
            "kelly_boosts":       {k: v.get("kelly_mult", 1.0)
                                    for k, v in tune_cfg.get("strategies", {}).items()},
            "ready_for_live":     tune_cfg.get("ready_for_live", False),
            "version":            tune_cfg.get("version", 0),
        },
        "strat_health":  strat_health,
        "decisions":     recent_decisions[:20],   # last 20 for the timeline
        # ─── Charts data ────────────────────────────────────────────
        "counters":      counters,
        # Trade waterfall: last 40 closed trades with pnl for a bar chart
        "recent_trades": [],
        # Rolling per-strategy edge curve (cumulative pnl over trade #)
        "edge_series":   {},
        "held":          held_out,
        "movers":        [{"sym": s, "v_2m_pct": round(v, 3),
                            "sigma": round(sig, 2), "typ_2m": round(t, 3)}
                           for s, v, sig, t in sigma_rows[:10]],
        "strategies":    strat_out,
        "gated":         [{"sym": r["sym"], "strategy": r["strategy"],
                            "reason": r["reason"], "note": r.get("note", "")}
                          for r in rejections[:10]],
        "whales":        [{"sym": s, "score": d["score"],
                            "hits": len(d["hits"]),
                            "hit_types": sorted(set(d["hits"]))}
                          for s, d in sorted(whale_agg.items(),
                                              key=lambda kv: kv[1]["score"],
                                              reverse=True)[:10]],
        "last_tick_acts": {
            "entries": entries_n, "exits": exits_n,
            "acts": [{"sym": a["sym"], "action": a["action"],
                       "strategy": a.get("strategy","?"),
                       "size_usd": a.get("size_usd", 0),
                       "note": a.get("note","")[:80]} for a in acts],
        },
    }
    # Charts payload: recent trades + rolling per-strategy edge
    try:
        trades = []
        if LEDGER.exists():
            for ln in LEDGER.read_text().splitlines():
                trades.append(json.loads(ln))
        # Waterfall: last 40 trades
        recent = trades[-40:]
        live_state["recent_trades"] = [{
            "sym": t.get("sym", "?"),
            "strategy": t.get("strategy", "?"),
            "pnl_pct":  round(t.get("pnl_pct", 0), 3),
            "exit_ts":  t.get("exit_ts", 0),
        } for t in recent]
        # Edge series per strategy (cumulative pnl over trade index)
        cum = {}
        for t in trades:
            s = t.get("strategy", "?")
            arr = cum.setdefault(s, [])
            prev = arr[-1] if arr else 0.0
            arr.append(round(prev + t.get("pnl_pct", 0), 3))
        # keep only last 60 points per strategy
        live_state["edge_series"] = {k: v[-60:] for k, v in cum.items()}

        # Also update exit-outcome counters from ledger tail (rewind since counters
        # was seeded but exits only added here)
        counters["exits_by_outcome"] = {"win": 0, "loss": 0, "wash": 0}
        for t in trades:
            p = t.get("pnl_pct", 0)
            if p > 0.05:
                counters["exits_by_outcome"]["win"] += 1
            elif p < -0.05:
                counters["exits_by_outcome"]["loss"] += 1
            else:
                counters["exits_by_outcome"]["wash"] += 1
        _save_counters(counters)
        live_state["counters"] = counters
    except Exception:
        pass

    try:
        tmp = LIVE_STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(live_state, indent=2, default=float))
        tmp.replace(LIVE_STATE)
    except Exception as _e:
        emit_live("prop_desk", f"[state_write_err] {_e!r}")


if __name__ == "__main__":
    tick()
