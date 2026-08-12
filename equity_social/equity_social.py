#!/usr/bin/env python3
"""Traditional-markets social & news scanner. Sibling of
~/crypto_sleeve/whale_refresh.py — same keyless-first philosophy, same
try/except-per-leg discipline, ported to equities. Two independent CLI legs:

  --leg collect  ADVISORY ONLY. For the live-book symbol universe (current
  combined_mirror positions) plus the S&P 400 midcap universe's deepest-
  oversold names (if the cache is present), pulls three keyless-or-cheap
  signals per symbol: StockTwits stream sentiment (native Bullish/Bearish
  tags — same keyless streams endpoint whale_refresh.py's leg_stocktwits()
  already uses for crypto, just without the ".X" crypto-symbol suffix),
  Reddit mention counts (r/stocks + r/wallstreetbets + r/investing, via the
  same public-RSS 403 workaround as whale_refresh.py's _reddit_hot_titles —
  the JSON API blocks anonymous traffic, but www.reddit.com/<sub>/hot/.rss
  works with a plain custom User-Agent), and Alpaca news headlines. Writes
  ~/scanner/state/equity_social.json. This feeds NO gate and places NO
  orders — sentiment/buzz/headlines only.

  --leg grade  Tracked-accounts recommendation grader. Manual ingest only:
  X/Twitter scraping is banned (ToS + house rule, same D4 pattern as the
  rest of the stack) — Robert pastes recs into tracked_accounts.json by
  hand. For every rec, computes forward returns vs SPY over the same
  window at +5d/+20d/+60d (Alpaca daily bars, feed=iex) and writes
  ~/scanner/state/tracked_accounts_grades.json with per-account rollups.

Both legs: requests with 15s timeouts, every external fetch wrapped in its
own try/except so one dead leg (a rate-limited subreddit, a missing Alpaca
key, a delisted ticker) never kills the run. No new pip dependencies —
`requests` is already used by ~/midcap/midcap_lane.py in this same stack.

Usage:
    python3 equity_social.py --leg collect
    python3 equity_social.py --leg grade
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

HOME = Path.home()
ROOT = Path(__file__).resolve().parent
SCANNER_STATE = HOME / "scanner" / "state"

BOOK_STATE = SCANNER_STATE / "combined_mirror_state.json"
SP400_CACHE = HOME / "midcap" / "cache" / "sp400.json"
TRACKED = ROOT / "tracked_accounts.json"

OUT_SOCIAL = SCANNER_STATE / "equity_social.json"
OUT_GRADES = SCANNER_STATE / "tracked_accounts_grades.json"

DATA_BASE = "https://data.alpaca.markets"
TIMEOUT = 15
UA = {"User-Agent": "quarki-equity-social/1.0"}

TOP_OVERSOLD_N = 10          # how many sp400 oversold names to add to the universe
MIN_PRICE = 5.0               # same liquidity floor as midcap_lane.py's scan()
MIN_DOLLAR_VOL = 5_000_000.0  # ditto — keeps the oversold leg to tradeable names

REDDIT_SUBS = ("stocks", "wallstreetbets", "investing")
NEWS_CHUNK = 10                # symbols per Alpaca news request
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _alpaca_headers() -> dict:
    return {"APCA-API-KEY-ID": os.environ.get("ALPACA_PAPER_KEY", ""),
            "APCA-API-SECRET-KEY": os.environ.get("ALPACA_PAPER_SECRET", "")}


def _atomic_write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(path)


# ── universe ──────────────────────────────────────────────────────────────
def _book_positions() -> list[str]:
    try:
        d = json.loads(BOOK_STATE.read_text())
        return list((d.get("positions") or {}).keys())
    except Exception as e:
        print(f"book positions read failed: {e!r}")
        return []


def _rsi14(closes: list[float]) -> float | None:
    """Wilder RSI(14) — identical formula to midcap_lane.py's _rsi14()."""
    n = 14
    if len(closes) < n + 1:
        return None
    gains, losses = [], []
    for a, b in zip(closes, closes[1:]):
        d = b - a
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - 100.0 / (1.0 + rs)


def _sp400_bars(syms: list[str], days: int = 40) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    start = (date.today() - timedelta(days=days * 2)).isoformat()
    headers = _alpaca_headers()
    for i in range(0, len(syms), 100):
        batch = syms[i:i + 100]
        page = None
        while True:
            params = {"symbols": ",".join(batch), "timeframe": "1Day", "start": start,
                      "limit": 10000, "feed": "iex", "adjustment": "split"}
            if page:
                params["page_token"] = page
            try:
                r = requests.get(f"{DATA_BASE}/v2/stocks/bars", headers=headers,
                                  params=params, timeout=TIMEOUT)
                r.raise_for_status()
                j = r.json()
            except Exception as e:
                print(f"sp400 bars batch failed: {e!r}")
                break
            for s, bs in (j.get("bars") or {}).items():
                out.setdefault(s, []).extend(bs)
            page = j.get("next_page_token")
            if not page:
                break
    return out


def _top_oversold(n: int = TOP_OVERSOLD_N) -> list[str]:
    """Deepest-RSI(14) names in the S&P 400 cache — sp400.json itself is
    just a flat constituent list (no ranking), so this reuses midcap_lane's
    scan() logic (same RSI + liquidity floor) to rank it. Absent cache,
    absent Alpaca key, or a dead bars call all degrade to an empty list —
    this leg is additive to the universe, never required."""
    try:
        cache = json.loads(SP400_CACHE.read_text())
        syms = cache.get("syms") or []
    except Exception as e:
        print(f"sp400 cache unavailable: {e!r}")
        return []
    if not syms:
        return []
    if not os.environ.get("ALPACA_PAPER_KEY"):
        print("sp400 oversold: no ALPACA_PAPER_KEY in env — skipping")
        return []
    try:
        bars = _sp400_bars(syms)
    except Exception as e:
        print(f"sp400 bars fetch failed: {e!r}")
        return []
    cands = []
    for s, bs in bars.items():
        closes = [float(b["c"]) for b in bs]
        if len(closes) < 21:
            continue
        px = closes[-1]
        rsi = _rsi14(closes)
        if rsi is None or px < MIN_PRICE:
            continue
        dvol = sum(float(b["c"]) * float(b["v"]) for b in bs[-20:]) / min(20, len(bs))
        if dvol < MIN_DOLLAR_VOL:
            continue
        cands.append((rsi, s))
    cands.sort(key=lambda t: t[0])
    return [s for _, s in cands[:n]]


def build_universe() -> list[str]:
    pos = _book_positions()
    oversold: list[str] = []
    try:
        oversold = _top_oversold()
    except Exception as e:
        print(f"sp400 oversold leg failed entirely: {e!r}")
    seen: list[str] = []
    for s in pos + oversold:
        if s and s not in seen:
            seen.append(s)
    return seen


# ── StockTwits leg (mirrors whale_refresh.py's leg_stocktwits) ─────────────
# Equities use the bare ticker on StockTwits, unlike the crypto sibling's
# "<SYM>.X" convention. entities.sentiment.basic is "Bullish"/"Bearish"
# (absent on untagged posts) — same field the crypto leg reads.
def _stocktwits_symbol(sym: str) -> dict | None:
    try:
        r = requests.get(f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json",
                          headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"stocktwits {sym} failed: {e!r}")
        return None
    msgs = data.get("messages") or []
    bull = bear = 0
    times = []
    for m in msgs:
        basic = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
        if basic == "Bullish":
            bull += 1
        elif basic == "Bearish":
            bear += 1
        ca = m.get("created_at")
        if ca:
            try:
                times.append(datetime.fromisoformat(ca.replace("Z", "+00:00")))
            except Exception:
                pass
    n = len(msgs)
    # message rate: page span in hours, floored so a tight burst of messages
    # doesn't blow up to an absurd rate
    rate = float(n)
    if len(times) >= 2:
        span_h = max((max(times) - min(times)).total_seconds() / 3600, 0.1)
        rate = n / span_h
    return {"bull": bull, "bear": bear, "msgs": n, "rate_hr": round(rate, 2)}


# ── Reddit RSS leg (mirrors whale_refresh.py's _reddit_hot_titles) ─────────
# api.reddit.com/*.json 403s anonymous requests (bot-detection); the public
# RSS surface (www.reddit.com/<sub>/hot/.rss) is not behind that wall as
# long as the request carries a plain, honest User-Agent — verified live by
# the crypto sibling on 2026-08-09, confirmed again here 2026-08-12. One
# retry with a 30s cooldown handles the occasional anonymous-IP 429.
def _reddit_hot_titles(sub: str, limit: int = 60) -> list[str]:
    url = f"https://www.reddit.com/r/{sub}/hot/.rss?limit={limit}"
    resp = None
    for attempt in range(2):
        resp = requests.get(url, headers=UA, timeout=TIMEOUT)
        if resp.status_code == 429 and attempt == 0:
            time.sleep(30)
            continue
        break
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    titles = []
    for entry in root.findall("a:entry", _ATOM_NS):
        t = entry.find("a:title", _ATOM_NS)
        if t is not None and t.text:
            titles.append(t.text)
    return titles


def _reddit_mentions(universe: list[str]) -> dict[str, int]:
    """One hot-feed fetch per sub (not per symbol) — same pattern as the
    crypto sibling's coin-buzz sweep — then count per-symbol mentions across
    the combined title corpus. A title counts toward a symbol on either a
    "$SYM" match (case-insensitive — the $ prefix alone disambiguates) or a
    standalone all-caps "SYM" token (case-sensitive, word-bounded — plain
    lowercase/mixed-case matching would false-positive heavily since many
    tickers are ordinary English words, e.g. "AN", "IT", "ALL")."""
    titles: list[str] = []
    for i, sub in enumerate(REDDIT_SUBS):
        try:
            titles.extend(_reddit_hot_titles(sub))
        except Exception as e:
            print(f"reddit r/{sub} failed: {e!r}")
        if i < len(REDDIT_SUBS) - 1:
            time.sleep(10)   # anonymous RSS rate limit trips at shorter gaps
    counts = {s: 0 for s in universe}
    for title in titles:
        for sym in universe:
            esc = re.escape(sym)
            if re.search(rf"\$\s?{esc}\b", title, re.IGNORECASE) or \
               re.search(rf"(?<![A-Za-z]){esc}(?![A-Za-z])", title):
                counts[sym] += 1
    return counts


# ── Alpaca news leg ──────────────────────────────────────────────────────
def _alpaca_news(universe: list[str]) -> dict[str, list[dict]]:
    headers = _alpaca_headers()
    if not headers["APCA-API-KEY-ID"]:
        print("alpaca news: no ALPACA_PAPER_KEY/SECRET in env — skipping")
        return {}
    start_iso = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw: dict[str, list[dict]] = {s: [] for s in universe}
    seen_ids: set = set()
    for i in range(0, len(universe), NEWS_CHUNK):
        chunk = universe[i:i + NEWS_CHUNK]
        page = None
        for _ in range(2):   # cap pagination — advisory feature, not exhaustive
            params = {"symbols": ",".join(chunk), "start": start_iso, "limit": 50}
            if page:
                params["page_token"] = page
            try:
                r = requests.get(f"{DATA_BASE}/v1beta1/news", headers=headers,
                                  params=params, timeout=TIMEOUT)
                r.raise_for_status()
                j = r.json()
            except Exception as e:
                print(f"alpaca news chunk {chunk} failed: {e!r}")
                break
            for art in j.get("news", []) or []:
                aid = art.get("id")
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)
                row = {"ts": art.get("created_at", ""), "title": art.get("headline", ""),
                       "src": art.get("source") or art.get("author") or ""}
                for sym in art.get("symbols", []) or []:
                    if sym in raw:
                        raw[sym].append(row)
            page = j.get("next_page_token")
            if not page:
                break
    return raw


# ── collect ──────────────────────────────────────────────────────────────
def collect() -> None:
    universe = build_universe()
    print(f"universe: {len(universe)} symbols -> {universe}")

    per_symbol: dict[str, dict] = {}
    rate_lookup: dict[str, float] = {}
    for i, sym in enumerate(universe):
        st = None
        try:
            st = _stocktwits_symbol(sym)
        except Exception as e:
            print(f"stocktwits {sym} unexpected failure: {e!r}")
        per_symbol[sym] = {
            "st_bull": (st or {}).get("bull", 0),
            "st_bear": (st or {}).get("bear", 0),
            "st_msgs": (st or {}).get("msgs", 0),
            "st_msg_rate_hr": (st or {}).get("rate_hr", 0.0),
            "reddit_mentions": 0,
            "news_24h": 0,
            "headlines": [],
        }
        if st:
            rate_lookup[sym] = st["rate_hr"]
        if i < len(universe) - 1:
            time.sleep(0.3)

    try:
        for sym, c in _reddit_mentions(universe).items():
            per_symbol[sym]["reddit_mentions"] = c
    except Exception as e:
        print(f"reddit leg failed entirely: {e!r}")

    try:
        news_raw = _alpaca_news(universe)
        for sym, arts in news_raw.items():
            per_symbol[sym]["news_24h"] = len(arts)
            per_symbol[sym]["headlines"] = sorted(arts, key=lambda h: h["ts"], reverse=True)[:3]
    except Exception as e:
        print(f"alpaca news leg failed entirely: {e!r}")

    top_buzz: list[dict] = []
    try:
        if len(rate_lookup) >= 2:
            vals = list(rate_lookup.values())
            mean = statistics.mean(vals)
            std = statistics.pstdev(vals)
            z = {s: ((r - mean) / std if std > 0 else 0.0) for s, r in rate_lookup.items()}
        else:
            z = {s: 0.0 for s in rate_lookup}
        ranked = sorted(z, key=lambda s: z[s], reverse=True)[:10]
        top_buzz = [{"sym": s, "z": round(z[s], 3), "st_msg_rate_hr": rate_lookup[s]}
                    for s in ranked]
    except Exception as e:
        print(f"top_buzz compute failed: {e!r}")

    out = {"ts": now_iso(), "per_symbol": per_symbol, "top_buzz": top_buzz}
    _atomic_write(OUT_SOCIAL, out)
    print(f"wrote {OUT_SOCIAL} symbols={len(universe)} "
          f"top_buzz={[b['sym'] for b in top_buzz]}")


# ── grade ────────────────────────────────────────────────────────────────
def _load_tracked() -> dict:
    try:
        return json.loads(TRACKED.read_text())
    except Exception as e:
        print(f"tracked_accounts.json read failed: {e!r}")
        return {}


def _daily_bars_single(sym: str, start_date: str) -> list[dict]:
    try:
        r = requests.get(f"{DATA_BASE}/v2/stocks/{sym}/bars", headers=_alpaca_headers(),
                          params={"timeframe": "1Day", "start": start_date, "feed": "iex",
                                  "limit": 1000, "adjustment": "split"},
                          timeout=TIMEOUT)
        r.raise_for_status()
        return r.json().get("bars", []) or []
    except Exception as e:
        print(f"bars fetch {sym} failed: {e!r}")
        return []


def _anchor_idx(bars: list[dict], rec_date: str) -> int | None:
    """Index of the most recent bar on or before rec_date — bars are oldest
    first (Alpaca default), same ordering midcap_lane.py assumes."""
    idx = None
    for i, b in enumerate(bars):
        if b["t"][:10] <= rec_date:
            idx = i
        else:
            break
    return idx


def _horizon(bars: list[dict], idx: int | None, px_at_rec: float | None,
             spy_bars: list[dict], spy_idx: int | None, h: int, side: str) -> dict:
    if idx is None or px_at_rec is None:
        return {"ret_pct": None, "spy_ret_pct": None, "excess_pct": None,
                "win": None, "status": "no_anchor"}
    fwd_i = idx + h
    if fwd_i >= len(bars):
        return {"ret_pct": None, "spy_ret_pct": None, "excess_pct": None,
                "win": None, "status": "pending"}
    raw_ret = bars[fwd_i]["c"] / px_at_rec - 1
    signed_ret = raw_ret if side != "short" else -raw_ret
    spy_ret = None
    if spy_idx is not None:
        spy_fwd_i = spy_idx + h
        if spy_fwd_i < len(spy_bars):
            spy_ret = spy_bars[spy_fwd_i]["c"] / spy_bars[spy_idx]["c"] - 1
    excess = (signed_ret - spy_ret) if spy_ret is not None else None
    return {"ret_pct": round(signed_ret * 100, 3),
            "spy_ret_pct": round(spy_ret * 100, 3) if spy_ret is not None else None,
            "excess_pct": round(excess * 100, 3) if excess is not None else None,
            "win": bool(signed_ret > 0), "status": "resolved"}


def _grade_one_rec(rec: dict, spy_cache: dict) -> dict:
    sym = rec["sym"]
    side = rec.get("side", "long")
    ts = rec["ts"]
    rec_date = ts[:10]
    fetch_start = (date.fromisoformat(rec_date) - timedelta(days=10)).isoformat()

    bars = _daily_bars_single(sym, fetch_start)
    if fetch_start not in spy_cache:
        spy_cache[fetch_start] = _daily_bars_single("SPY", fetch_start)
    spy_bars = spy_cache[fetch_start]

    idx = _anchor_idx(bars, rec_date)
    spy_idx = _anchor_idx(spy_bars, rec_date)

    px_given = rec.get("px_at_rec")
    px_at_rec = px_given if px_given is not None else (bars[idx]["c"] if idx is not None else None)

    row = {"ts": ts, "sym": sym, "side": side, "note": rec.get("note"), "px_at_rec": px_at_rec}
    for h, key in ((5, "h5"), (20, "h20"), (60, "h60")):
        row[key] = _horizon(bars, idx, px_at_rec, spy_bars, spy_idx, h, side)
    return row


def grade() -> None:
    tracked = _load_tracked()
    spy_cache: dict[str, list[dict]] = {}
    accounts_out: dict[str, dict] = {}

    for acct_name, acct in tracked.items():
        recs = acct.get("recs") or []
        rec_rows = []
        for rec in recs:
            try:
                rec_rows.append(_grade_one_rec(rec, spy_cache))
            except Exception as e:
                print(f"grade rec failed acct={acct_name} rec={rec}: {e!r}")

        resolved = [r for r in rec_rows if r.get("h20", {}).get("win") is not None]
        win_rate_20d = round(sum(1 for r in resolved if r["h20"]["win"]) / len(resolved), 4) \
            if resolved else None
        excess_vals = [r["h20"]["excess_pct"] for r in resolved
                       if r["h20"].get("excess_pct") is not None]
        avg_excess_20d = round(sum(excess_vals) / len(excess_vals), 3) if excess_vals else None
        best = worst = None
        if excess_vals:
            best_row = max(resolved, key=lambda r: r["h20"]["excess_pct"])
            worst_row = min(resolved, key=lambda r: r["h20"]["excess_pct"])
            best = {"sym": best_row["sym"], "ts": best_row["ts"],
                    "excess_20d_pct": best_row["h20"]["excess_pct"]}
            worst = {"sym": worst_row["sym"], "ts": worst_row["ts"],
                     "excess_20d_pct": worst_row["h20"]["excess_pct"]}

        accounts_out[acct_name] = {
            "source": acct.get("source"), "handle": acct.get("handle"),
            "ingest": acct.get("ingest"),
            "n": len(recs), "n_resolved_20d": len(resolved),
            "win_rate_20d": win_rate_20d, "avg_excess_20d": avg_excess_20d,
            "best": best, "worst": worst,
            "recs": rec_rows,
        }

    out = {"ts": now_iso(), "accounts": accounts_out}
    _atomic_write(OUT_GRADES, out)
    print(f"wrote {OUT_GRADES} accounts={list(accounts_out)}")
    for name, a in accounts_out.items():
        print(f"  {name}: n={a['n']} resolved_20d={a['n_resolved_20d']} "
              f"win_rate_20d={a['win_rate_20d']} avg_excess_20d={a['avg_excess_20d']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leg", choices=["collect", "grade"], required=True)
    args = ap.parse_args()
    if args.leg == "collect":
        collect()
    else:
        grade()


if __name__ == "__main__":
    main()
