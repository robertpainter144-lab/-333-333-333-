#!/usr/bin/env python3
"""Cross-sectional rotation SHADOW lane (Grok-review adoption, Robert-approved
2026-08-26) — signal-only. This process CANNOT trade by construction: it
imports no broker, reads no API key, and touches only Kraken PUBLIC endpoints
plus the sleeve's own state/pair_cache.json read-only. It is a slow-rotation
comparison lane against the live fast-momentum sleeve. Every paper SELL/trim
gets logged to a D7-scoreable ledger; nothing here can ever place a real
order.

Strategy:
  Universe   pair_cache.json ws-sym -> kraken code, capped at 24 names, minus
             any whose 24h dollar-volume (hourly OHLC, last 24 bars,
             sum(vol*close)) is under $20M.
  Data       daily OHLC (interval=1440), >=35 bars required to be scored.
  Score      dual-horizon, vol-adjusted momentum:
               roc7  = c[-1]/c[-8]  - 1
               roc28 = c[-1]/c[-29] - 1
               vol   = stdev(daily returns, last 28 bars)   [guard >1e-6]
               score = 0.5*(roc7/(vol*sqrt(7))) + 0.5*(roc28/(vol*sqrt(28)))
  Eligible   roc28 > 0 AND close > EMA50(daily closes).
  Portfolio  paper equity starts at $1000. Hold top-3 by score among the
             eligible pool, inverse-vol weighted, 10% cash buffer reserved.
  Hysteresis rebalance only if (a) a held name fell out of the top-5, or
             (b) the best non-held name's score beats the worst held name's
             score by >=25%, AND (c) >=48h since the last rebalance. First
             run buys the initial top-3 unconditionally.
  Dip-add    a held name pulled back to within +/-1.5% of its EMA20 (while
             still > EMA50, roc28>0, RSI14<50) gets one +25%-of-target-weight
             add per 7 days, funded from cash.
  Costs      every paper fill (buy, sell, trim, add) costs 0.26% one side;
             cost_basis/entry_avg carry that drag so P/L is net.

Nothing above touches a broker: only urllib GETs to api.kraken.com/0/public
and read-only json.loads of the sleeve's own state files.
"""
from __future__ import annotations

import fcntl
import json
import math
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)
STATE_F = STATE_DIR / "rotation_shadow_state.json"
LEDGER_F = STATE_DIR / "rotation_shadow.jsonl"
LOCK_F = STATE_DIR / "rotation_shadow.lock"
LOG_F = ROOT / "logs" / "rotation_shadow.log"
LOG_F.parent.mkdir(parents=True, exist_ok=True)

CFG = {
    "universe_max": 24,
    "min_dvol_usd": 20e6,          # 24h $ volume floor, hourly OHLC
    "min_daily_bars": 35,          # daily bars required to score a name
    "pace_s": 0.3,                 # public-endpoint pacing
    "paper_equity_start": 1000.0,
    "cash_buffer_pct": 0.10,       # 10% always reserved off the top
    "top_n": 3,                    # target holding count
    "top5_n": 5,                   # hysteresis "still in the running" band
    "score_margin": 0.25,          # non-held must beat worst held by 25%
    "rebalance_min_hours": 48.0,
    "fee_rate": 0.0026,            # one side, every fill
    # dip-add
    "dip_pct": 0.015,              # within +/-1.5% of EMA20
    "dip_add_frac": 0.25,          # +25% of target weight per add
    "dip_add_cooldown_days": 7.0,
    "rsi_period": 14,
    "rsi_dip_max": 50.0,
    "ema20_n": 20,
    "ema50_n": 50,
    "roc_short_n": 7,
    "roc_long_n": 28,
    "vol_window": 28,
    # dust guard: skip a rebalance-trim/add smaller than this (fee churn)
    "min_rebalance_delta_usd": 5.0,
    "min_rebalance_delta_pct": 0.02,
}


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line)
    with open(LOG_F, "a") as f:
        f.write(line + "\n")


def notify(level: str, title: str, msg: str) -> None:
    try:
        subprocess.run(["python3", str(Path.home() / "bin" / "quarki_notify.py"),
                        level, title, msg], timeout=15, capture_output=True)
    except Exception:
        pass


def pub(path: str, params: dict) -> dict:
    url = "https://api.kraken.com" + path + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=12) as r:
        j = json.load(r)
    if j.get("error"):
        raise RuntimeError(str(j["error"])[:80])
    return j.get("result", {})


def ema(vals: list[float], n: int) -> float:
    if not vals:
        return 0.0
    k = 2.0 / (n + 1)
    e = vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def stdev(vals: list[float]) -> float:
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    var = sum((v - m) ** 2 for v in vals) / n
    return var ** 0.5


def rsi(closes: list[float], n: int) -> float | None:
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain, avg_loss = gains / n, losses / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1 + rs)


def load_state() -> dict:
    try:
        st = json.loads(STATE_F.read_text())
    except Exception:
        st = {}
    st.setdefault("positions", {})           # sym -> {qty, entry_avg, cost_basis, opened, weight, last_price}
    st.setdefault("cash", CFG["paper_equity_start"])
    st.setdefault("paper_equity", CFG["paper_equity_start"])
    st.setdefault("last_rebalance_ts", None)
    st.setdefault("last_dip_add", {})         # sym -> iso ts
    st.setdefault("first_notified", False)
    return st


def save_state(st: dict) -> None:
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(STATE_F)


def ledger_append(row: dict) -> None:
    with open(LEDGER_F, "a") as f:
        f.write(json.dumps(row) + "\n")


def load_universe_pairs() -> dict:
    """ws-sym -> kraken code from the live sleeve's own cache, read-only.
    Deliberately does NOT fail over to a hardcoded list: if the cache is
    missing/unreadable the caller logs and exits 0 rather than guessing."""
    pc = json.loads((STATE_DIR / "pair_cache.json").read_text())
    pairs = pc.get("pair") or {}
    if not pairs:
        raise ValueError("pair_cache has no 'pair' map")
    return pairs


def compute_equity(st: dict, data: dict) -> float:
    v = float(st["cash"])
    for sym, pos in st["positions"].items():
        px = data[sym]["px"] if sym in data else float(pos.get("last_price", pos["entry_avg"]))
        v += float(pos["qty"]) * px
    return v


def buy(st: dict, sym: str, px: float, usd_amount: float, now_iso: str) -> None:
    if usd_amount <= 0 or px <= 0:
        return
    fee = usd_amount * CFG["fee_rate"]
    qty = (usd_amount - fee) / px
    if qty <= 0:
        return
    pos = st["positions"].get(sym)
    if pos:
        pos["qty"] = float(pos["qty"]) + qty
        pos["cost_basis"] = float(pos.get("cost_basis", 0.0)) + usd_amount
        pos["entry_avg"] = pos["cost_basis"] / pos["qty"]
        pos["last_price"] = px
    else:
        st["positions"][sym] = {"qty": qty, "cost_basis": usd_amount,
                                 "entry_avg": usd_amount / qty, "opened": now_iso,
                                 "weight": 0.0, "last_price": px}
    st["cash"] -= usd_amount
    log(f"BUY {sym} @ {px:.6g} usd={usd_amount:.2f} qty={qty:.6g} "
        f"cash_left={st['cash']:.2f}")


def sell_full(st: dict, sym: str, px: float, reason: str, score_at_exit,
              now_iso: str) -> None:
    pos = st["positions"].pop(sym)
    qty = float(pos["qty"])
    entry_avg = float(pos["entry_avg"])
    cost_basis = float(pos.get("cost_basis", entry_avg * qty))
    proceeds = qty * px
    fee = proceeds * CFG["fee_rate"]
    net = proceeds - fee
    pl = round(net - cost_basis, 4)
    row = {"closed_at": now_iso, "sym": sym, "side": "rotation",
           "entry_avg": round(entry_avg, 8), "exit": px,
           "notional": round(proceeds, 2), "pl": pl,
           "pl_pct": round(pl / cost_basis * 100, 2) if cost_basis else 0.0,
           "reason": reason, "score_at_exit": score_at_exit}
    ledger_append(row)
    st["cash"] += net
    st.get("last_dip_add", {}).pop(sym, None)
    log(f"SELL {sym} @ {px:.6g} qty={qty:.6g} ({reason}) pl={pl:+.2f}")


def sell_partial(st: dict, sym: str, px: float, qty_sell: float, reason: str,
                  score_at_exit, now_iso: str) -> None:
    pos = st["positions"][sym]
    qty = float(pos["qty"])
    entry_avg = float(pos["entry_avg"])
    qty_sell = min(qty_sell, qty)
    if qty_sell <= 0:
        return
    proceeds = qty_sell * px
    fee = proceeds * CFG["fee_rate"]
    net = proceeds - fee
    cost_basis_sold = entry_avg * qty_sell
    pl = round(net - cost_basis_sold, 4)
    row = {"closed_at": now_iso, "sym": sym, "side": "rotation",
           "entry_avg": round(entry_avg, 8), "exit": px,
           "notional": round(proceeds, 2), "pl": pl,
           "pl_pct": round(pl / cost_basis_sold * 100, 2) if cost_basis_sold else 0.0,
           "reason": reason, "score_at_exit": score_at_exit}
    ledger_append(row)
    st["cash"] += net
    remaining = qty - qty_sell
    if remaining <= 1e-9:
        st["positions"].pop(sym, None)
    else:
        pos["qty"] = remaining
        pos["cost_basis"] = entry_avg * remaining
        pos["last_price"] = px
    log(f"TRIM {sym} @ {px:.6g} qty={qty_sell:.6g} ({reason}) pl={pl:+.2f}")


def fetch_symbol(sym: str, code: str) -> dict:
    """Public-only. Raises on any transient failure — caller catches per
    symbol so one bad name never takes the tick down."""
    time.sleep(CFG["pace_s"])
    o60 = pub("/0/public/OHLC", {"pair": code, "interval": 60})
    rows60 = next(v for k, v in o60.items() if k != "last")
    if len(rows60) < 24:
        raise RuntimeError(f"only {len(rows60)} hourly bars")
    px = float(rows60[-1][4])
    if px <= 0:
        raise RuntimeError("non-positive price")
    dvol = sum(float(r[6]) * float(r[4]) for r in rows60[-24:])

    time.sleep(CFG["pace_s"])
    oD = pub("/0/public/OHLC", {"pair": code, "interval": 1440})
    rowsD = next(v for k, v in oD.items() if k != "last")
    closesD = [float(r[4]) for r in rowsD]

    entry = {"px": px, "dvol": dvol, "eligible_liquid": dvol >= CFG["min_dvol_usd"],
              "roc7": None, "roc28": None, "vol": None, "ema20": None,
              "ema50": None, "rsi14": None, "score": None, "eligible": False}

    if len(closesD) >= CFG["min_daily_bars"]:
        c = closesD
        n7, n28 = CFG["roc_short_n"], CFG["roc_long_n"]
        roc7 = c[-1] / c[-(n7 + 1)] - 1
        roc28 = c[-1] / c[-(n28 + 1)] - 1
        w = CFG["vol_window"]
        rets = [c[i] / c[i - 1] - 1 for i in range(len(c) - w, len(c))]
        vol = stdev(rets)
        ema20v = ema(c, CFG["ema20_n"])
        ema50v = ema(c, CFG["ema50_n"])
        rsi14v = rsi(c, CFG["rsi_period"])
        score = None
        if vol > 1e-6:
            score = 0.5 * (roc7 / (vol * math.sqrt(n7))) + \
                    0.5 * (roc28 / (vol * math.sqrt(n28)))
        eligible = bool(entry["eligible_liquid"] and roc28 > 0
                        and c[-1] > ema50v and score is not None)
        entry.update(roc7=roc7, roc28=roc28, vol=vol, ema20=ema20v,
                     ema50=ema50v, rsi14=rsi14v, score=score, eligible=eligible)
    return entry


def do_rebalance(st: dict, now_iso: str, data: dict, top3: list[str],
                  top5: set) -> None:
    held = list(st["positions"].keys())
    equity = compute_equity(st, data)
    investable = equity * (1 - CFG["cash_buffer_pct"])

    # exits: anything held that isn't in the new target set
    for s in held:
        if s in top3:
            continue
        if s not in data:
            log(f"{s} would exit but no fresh price this tick — holding over")
            continue
        reason = "rank_drop" if s not in top5 else "rotated_out"
        sell_full(st, s, data[s]["px"], reason, data[s].get("score"), now_iso)

    usable = [s for s in top3 if s in data and data[s].get("vol")
              and data[s]["vol"] > 1e-6]
    if not usable:
        log("rebalance triggered but no usable vol data this tick — no buys")
        st["last_rebalance_ts"] = now_iso
        return

    inv = {s: 1.0 / data[s]["vol"] for s in usable}
    tot = sum(inv.values())
    weight = {s: inv[s] / tot for s in usable}

    for s in usable:
        px = data[s]["px"]
        target_usd = weight[s] * investable
        cur_qty = float(st["positions"][s]["qty"]) if s in st["positions"] else 0.0
        cur_val = cur_qty * px
        diff = target_usd - cur_val
        min_delta = max(CFG["min_rebalance_delta_usd"],
                        target_usd * CFG["min_rebalance_delta_pct"])
        if s not in st["positions"]:
            buy(st, s, px, target_usd, now_iso)
        elif diff > min_delta:
            buy(st, s, px, diff, now_iso)
        elif diff < -min_delta:
            sell_partial(st, s, px, (-diff) / px, "rebalance_trim",
                        data[s].get("score"), now_iso)
        if s in st["positions"]:
            st["positions"][s]["weight"] = weight[s]

    st["last_rebalance_ts"] = now_iso
    weights_rounded = {s: round(weight[s], 3) for s in usable}
    log(f"REBALANCE done -> {usable} weights={weights_rounded}")


def dip_add_pass(st: dict, now, now_iso: str, data: dict) -> None:
    equity = compute_equity(st, data)
    investable = equity * (1 - CFG["cash_buffer_pct"])
    for s in list(st["positions"].keys()):
        d = data.get(s)
        if not d or d.get("score") is None:
            continue
        if not (d.get("roc28") is not None and d["roc28"] > 0
                and d.get("ema50") and d["px"] > d["ema50"]):
            continue
        ema20v = d.get("ema20")
        if not ema20v or abs(d["px"] / ema20v - 1) > CFG["dip_pct"]:
            continue
        rsi14v = d.get("rsi14")
        if rsi14v is None or rsi14v >= CFG["rsi_dip_max"]:
            continue
        last_add = st.get("last_dip_add", {}).get(s)
        if last_add:
            days_since = (now - datetime.fromisoformat(last_add)).total_seconds() / 86400
            if days_since < CFG["dip_add_cooldown_days"]:
                continue
        weight = float(st["positions"][s].get("weight", 0.0))
        if weight <= 0:
            continue
        add_usd = CFG["dip_add_frac"] * weight * investable
        if add_usd <= 1.0:
            continue
        if st["cash"] < add_usd:
            log(f"{s} dip-add skipped: cash {st['cash']:.2f} < needed {add_usd:.2f}")
            continue
        buy(st, s, d["px"], add_usd, now_iso)
        st.setdefault("last_dip_add", {})[s] = now_iso
        log(f"DIP_ADD {s} @ {d['px']:.6g} usd={add_usd:.2f} rsi={rsi14v:.1f}")


def main() -> None:
    lock = open(LOCK_F, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("another tick still running — skip")
        return

    st = load_state()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat(timespec="seconds")

    try:
        pairs = load_universe_pairs()
    except Exception as e:
        log(f"pair_cache missing/unreadable — exiting ({e!r})")
        return

    items = list(pairs.items())[: CFG["universe_max"]]
    covered = {s for s, _ in items}
    for s in st["positions"].keys():          # never lose the ability to exit a held name
        if s not in covered and s in pairs:
            items.append((s, pairs[s]))

    data: dict = {}
    for sym, code in items:
        try:
            data[sym] = fetch_symbol(sym, code)
        except Exception as e:
            log(f"{sym} tick FAIL {e!r}")

    eligible = [s for s, d in data.items() if d.get("eligible")]
    ranked = sorted(eligible, key=lambda s: data[s]["score"], reverse=True)
    top5 = set(ranked[: CFG["top5_n"]])
    top3 = ranked[: CFG["top_n"]]

    was_empty = len(st["positions"]) == 0

    if was_empty:
        if top3:
            log(f"first run: buying initial top-{len(top3)} {top3}")
            do_rebalance(st, now_iso, data, top3, top5)
        else:
            log("first run: no eligible names yet — staying in cash")
    else:
        held = list(st["positions"].keys())
        cond_a = any(s not in top5 for s in held if s in data)
        held_scores = [data[s]["score"] for s in held
                       if s in data and data[s].get("score") is not None]
        best_non_held = next((s for s in ranked if s not in held), None)
        cond_b = False
        if best_non_held is not None and held_scores:
            worst = min(held_scores)
            cand = data[best_non_held]["score"]
            if worst > 0 and cand >= worst * (1 + CFG["score_margin"]):
                cond_b = True
            elif worst <= 0 < cand:
                cond_b = True
        last_rb = st.get("last_rebalance_ts")
        hrs = 1e9 if not last_rb else \
            (now - datetime.fromisoformat(last_rb)).total_seconds() / 3600.0
        cond_c = hrs >= CFG["rebalance_min_hours"]
        if (cond_a or cond_b) and cond_c and top3:
            log(f"rebalance trigger: fell_out_of_top5={cond_a} "
                f"better_name_available={cond_b} hrs_since_last={hrs:.1f}")
            do_rebalance(st, now_iso, data, top3, top5)
        elif cond_a or cond_b:
            log(f"hysteresis hold: signal present but only {hrs:.1f}h "
                f"since last rebalance (<{CFG['rebalance_min_hours']}h)")

    dip_add_pass(st, now, now_iso, data)

    equity = compute_equity(st, data)
    holdings_usd = {}
    for sym, pos in st["positions"].items():
        px = data[sym]["px"] if sym in data else float(pos.get("last_price", pos["entry_avg"]))
        holdings_usd[sym] = round(float(pos["qty"]) * px, 2)
    ledger_append({"type": "mark", "ts": now_iso, "paper_equity": round(equity, 2),
                   "holdings": holdings_usd})
    st["paper_equity"] = round(equity, 2)

    if not st.get("first_notified") and was_empty and st["positions"]:
        st["first_notified"] = True
        notify("action", "Rotation shadow lane: first rebalance",
               "The signal-only rotation lane just made its first paper "
               "trade (state/rotation_shadow.jsonl). It starts with $1000 "
               "of paper equity and exists purely to compare a slow "
               "cross-sectional rotation approach against the live "
               "fast-momentum crypto sleeve. It cannot place real orders "
               "and needs weeks of ledger history before its numbers mean "
               "anything.")

    save_state(st)
    log(f"tick done: equity=${equity:.2f} positions={list(st['positions'].keys())}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERROR {e!r}")
        raise
