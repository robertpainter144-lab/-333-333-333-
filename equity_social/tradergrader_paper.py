#!/usr/bin/env python3
"""tradergrader_PAPER — virtual follow-the-highlight lane. ZERO live weight.

Promotion doctrine (Robert 2026-08-12: "same promotion rules ... so we
don't make any dumb moves"): this lane is a D7 closed-trade ledger and
NOTHING else. It is not wired into strategy_governor, build_target, or any
order path. It can earn governor consideration only by passing the same
bar every challenger faces — n>=30 closed trades, mean pl_pct >= 0.05%,
Sharpe >= 1, late-window mean >= 0.02% — and even then D34 caps unproven
lanes and Robert must explicitly approve any weight. The catalog
TraderGrader row stays display-only regardless.

Mechanics (mirrors the graded thesis exactly): each NEW auto-parsed
callout in x_auto_recs.json opens a virtual $50 leg on the underlying
(long for calls, short for puts) at the callout date's close; the leg
closes at the next trading day's close (1-day hold — the only horizon the
deep-analysis found even directionally interesting). Closed legs append
D7-scoreable rows (midcap_ledger schema) to
~/scanner/state/tradergrader_paper_ledger.jsonl. Prices: Alpaca daily
bars, feed=iex, paper keys from env. Runs on the daily 15:35 MT
equity-social chain after the grade leg.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import requests

HOME = Path.home()
ROOT = Path(__file__).resolve().parent
RECS = ROOT / "x_auto_recs.json"
STATE = ROOT / "tradergrader_paper_state.json"
LEDGER = HOME / "scanner" / "state" / "tradergrader_paper_ledger.jsonl"

NOTIONAL = 50.0
DATA_BASE = "https://data.alpaca.markets"
TIMEOUT = 15


def _headers() -> dict:
    return {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY", ""),
            "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET", "")}


def _bars(sym: str, start: str) -> list[dict]:
    try:
        r = requests.get(f"{DATA_BASE}/v2/stocks/{sym}/bars", headers=_headers(),
                         params={"timeframe": "1Day", "start": start, "feed": "iex",
                                 "limit": 100, "adjustment": "split"}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("bars", []) or []
    except Exception as e:
        print(f"bars {sym} failed: {e!r}")
        return []


def _load_state() -> dict:
    try:
        st = json.loads(STATE.read_text())
    except Exception:
        st = {}
    st.setdefault("open_legs", {})
    st.setdefault("processed", [])
    return st


def _save_state(st: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(STATE)


def _append_ledger(row: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps({"ts": time.time(), **row}) + "\n")


def main() -> None:
    if not os.environ.get("ALPACA_PAPER_KEY"):
        print("no ALPACA_PAPER_KEY in env — skipping")
        return
    try:
        recs = json.loads(RECS.read_text()).get("recs") or []
    except Exception as e:
        print(f"recs read failed: {e!r}")
        return
    st = _load_state()

    # 1. open virtual legs for new callouts (skip anything older than 10
    #    days — backfill history must not open stale "new" positions)
    cutoff = (date.today() - timedelta(days=10)).isoformat()
    opened = 0
    for r in recs:
        sid = r.get("src_id")
        if not sid or sid in st["processed"] or sid in st["open_legs"]:
            continue
        if str(r.get("ts", ""))[:10] < cutoff:
            st["processed"].append(sid)
            continue
        bars = _bars(r["sym"], r["ts"][:10])
        entry = next((b for b in bars if b["t"][:10] >= r["ts"][:10]), None)
        if entry is None:
            continue   # no bar yet (callout today, market not settled) — retry next run
        st["open_legs"][sid] = {"sym": r["sym"], "side": r["side"],
                                "entry_date": entry["t"][:10],
                                "entry_px": float(entry["c"]),
                                "note": (r.get("note") or "")[:120]}
        opened += 1

    # 2. close legs that have a bar after entry_date
    closed = 0
    for sid in list(st["open_legs"]):
        leg = st["open_legs"][sid]
        bars = _bars(leg["sym"], leg["entry_date"])
        nxt = next((b for b in bars if b["t"][:10] > leg["entry_date"]), None)
        if nxt is None:
            continue
        exit_px = float(nxt["c"])
        raw = exit_px / leg["entry_px"] - 1.0
        pl = raw if leg["side"] != "short" else -raw
        _append_ledger({
            "sym": leg["sym"], "theme": "tradergrader_follow",
            "entry_date": leg["entry_date"], "exit_date": nxt["t"][:10],
            "entry_px": leg["entry_px"], "exit_px": round(exit_px, 4),
            "pl_pct": round(pl * 100, 3), "side": leg["side"],
            "notional": NOTIONAL, "src_id": sid, "note": leg["note"],
        })
        st["processed"].append(sid)
        del st["open_legs"][sid]
        closed += 1

    _save_state(st)
    n = 0
    try:
        n = sum(1 for _ in LEDGER.open())
    except Exception:
        pass
    print(f"tradergrader_paper: opened={opened} closed={closed} "
          f"open_legs={len(st['open_legs'])} ledger_n={n} "
          f"(D7 gate: n>=30, mean>=0.05%, Sharpe>=1 — currently {'UNPROVEN' if n < 30 else 'check gate'})")


if __name__ == "__main__":
    main()
