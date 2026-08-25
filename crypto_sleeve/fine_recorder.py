#!/usr/bin/env python3
"""10-second fine-resolution equity recorder (Robert 2026-08-22: full fine
detail at every zoom). Samples Kraken public tickers for the held coins,
prices the book (cash from the engine's last mark + live coin marks), and
appends to state/equity_fine.csv. Keeps ~7 days. Read-only against Kraken
(public endpoints, no key); never trades."""
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/ubuntu/lab-stack/crypto_sleeve")
OUT = ROOT / "state" / "equity_fine.csv"
KEEP_ROWS = 61000          # ~7 days at 10s
TRIM_EVERY = 360           # loops (~1h)


def pairs() -> dict:
    try:
        return json.loads((ROOT / "state" / "pair_cache.json").read_text()).get("pair", {})
    except Exception:
        return {}


def px(pair: str) -> float:
    u = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
    with urllib.request.urlopen(u, timeout=6) as r:
        res = json.load(r).get("result", {})
    return float(next(iter(res.values()))["c"][0])


def qty(meta: dict) -> float:
    if meta.get("mv") is not None and meta.get("cur"):
        return float(meta["mv"]) / float(meta["cur"])
    e = float(meta.get("entry_price") or 0)
    return float(meta.get("notional") or 0) / e if e else 0.0


loop = 0
_last_w = None    # last written value (jump-guard baseline)
try:      # seed from the file so RESTARTS are guarded too (2026-08-22:
          # an unguarded first write after restart printed one bad row)
    _last_w = float(OUT.read_text().strip().rsplit(chr(10), 1)[-1].split(',')[1])
except Exception:
    pass
_pend = None      # held-back candidate awaiting confirmation
_snap_row = None  # equity.csv row ts the current snapshot belongs to
_snap = None      # (positions, cash) captured together at a tick boundary
while True:
    t0 = time.time()
    try:
        # positions + cash form ONE snapshot, refreshed only when the
        # engine finishes a tick (a new equity.csv row appears): reading
        # sleeve_state mid-tick skewed 20-50s of samples at a time
        # (2026-08-24 phantom spikes +83/+112/-386 while the market moved
        # 0.1-0.3%) — the single-sample jump-guard can't catch multi-
        # sample skew, so remove the skew source instead.
        last = (ROOT / "state" / "equity.csv").read_text().strip().rsplit("\n", 1)[-1].split(",")
        if _snap is None or _snap_row != last[0]:
            stp = ROOT / "state" / "sleeve_state.json"
            # only adopt a new snapshot once the state file has been quiet
            # a moment (the engine can still be mid-tick right after the
            # equity row appears); until then keep pricing the old one
            if _snap is None or time.time() - stp.stat().st_mtime > 15:
                st = json.loads(stp.read_text())
                _snap = (st.get("positions") or {},
                         float(last[2]) if len(last) >= 3 else 0.0)
                _snap_row = last[0]
        pos, cash = _snap
        pm = pairs()
        total = cash
        ok = True
        for sym, meta in pos.items():
            k = pm.get(sym)
            if not k:
                ok = False
                break
            total += qty(meta) * px(k)
        try:      # D63: count micro-meme carve-out holdings (marked
                  # by its own 10-min engine) so the fine chart is whole
            _mm = json.loads((ROOT / "state" / "micro_meme_state.json").read_text())
            total += sum(float(q.get("mv") or 0)
                         for q in (_mm.get("positions") or {}).values())
        except Exception:
            pass
        if ok and total > 1:
            # confirmation jump-guard (2026-08-22; tightened 2026-08-23:
            # engine-tick cash-vs-positions skew is one trade-chunk,
            # ~3-6%, and slipped under the old 6% bar). A >1.5% step from
            # the last written row must be CONFIRMED by the next sample
            # (within 2%) before it prints; genuine moves confirm in 10s,
            # transient skew never does.
            if _last_w and abs(total - _last_w) / _last_w > 0.015:
                if _pend and abs(total - _pend) / _pend < 0.02:
                    pass          # confirmed — fall through and write
                else:
                    _pend = total  # hold back, wait for confirmation
                    total = None
            else:
                _pend = None
            if total is not None:
                _pend = None
                _last_w = total
                with OUT.open("a") as f:
                    f.write(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')},{total:.4f}\n")
        loop += 1
        if loop % TRIM_EVERY == 0 and OUT.exists():
            lines = OUT.read_text().strip().splitlines()
            if len(lines) > KEEP_ROWS:
                OUT.write_text("\n".join(lines[-KEEP_ROWS:]) + "\n")
    except Exception:
        pass
    time.sleep(max(1.0, 10.0 - (time.time() - t0)))
