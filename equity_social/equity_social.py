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

  --leg grade  Tracked-accounts recommendation grader. Two ingest paths:
  manual (Robert pastes recs into tracked_accounts.json by hand) and the
  x_fetch leg's auto-parsed @tradergrader callouts (merged in as the
  synthetic account "tradergrader_auto"). For every rec, computes forward
  returns vs SPY over the same window at +1d/+5d/+20d/+60d (Alpaca daily
  bars, feed=iex) and writes
  ~/scanner/state/tracked_accounts_grades.json with per-account rollups.

  --leg x_fetch  @tradergrader X-timeline ingester. The original manual-only
  rule was lifted by Robert on 2026-08-12 ("add tradergrader to the social
  media scans"); this stays deliberately low-impact: one handle, read-only,
  once-a-day timer, no login, no crawling. Two keyless sources, each
  try/except-isolated: nitter RSS (instance list with fallback) and X's own
  public embed/syndication widget endpoint (which also provides ~100 posts
  of history for the past-trades backtest). Raw posts dedup into
  x_posts_tradergrader.jsonl by tweet id; the full store is then re-parsed
  every run into (a) x_auto_recs.json — structured trade callouts (Sniper
  of the Day / Trade of the Week: option contract, trader, claimed ROI,
  side inferred from C/P, index underlyings mapped to tradeable ETF
  proxies) — and (b) ~/scanner/state/tradergrader_leaderboard.json — per-
  trader rollups of his daily Top Performers / $100-Budget medal lists.
  NOTE: his posts are retrospective recaps (the ROI already happened), so
  the grade leg is honestly testing the FOLLOW-the-highlight strategy:
  forward returns of the implied direction starting at the callout date.

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
import email.utils
import html as html_mod
import json
import os
import re
import statistics
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

HOME = Path.home()
ROOT = Path(__file__).resolve().parent
SCANNER_STATE = HOME / "scanner" / "state"

BOOK_STATE = SCANNER_STATE / "combined_mirror_state.json"
SP400_CACHE = HOME / "midcap" / "cache" / "sp400.json"
TRACKED = ROOT / "tracked_accounts.json"

OUT_SOCIAL = SCANNER_STATE / "equity_social.json"
OUT_GRADES = SCANNER_STATE / "tracked_accounts_grades.json"
OUT_LEADERBOARD = SCANNER_STATE / "tradergrader_leaderboard.json"

X_POSTS = ROOT / "x_posts_tradergrader.jsonl"
X_AUTO_RECS = ROOT / "x_auto_recs.json"
X_HANDLE = "tradergrader"
# Public nitter mirrors, tried in order until one returns valid RSS.
# nitter.net verified live 2026-08-12; the rest are best-effort fallbacks.
NITTER_INSTANCES = ("nitter.net", "nitter.privacyredirect.com",
                    "nitter.tiekoetter.com", "lightbrd.com")
SYNDICATION_URL = ("https://syndication.twitter.com/srv/timeline-profile/"
                   f"screen-name/{X_HANDLE}")
BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
# His callouts are options on index products; grade the directional call on
# the liquid ETF proxy instead (daily-bar grading only — strikes untouched).
INDEX_PROXY = {"SPX": "SPY", "SPXW": "SPY", "XSP": "SPY", "ES": "SPY",
               "NDX": "QQQ", "NQ": "QQQ", "RUT": "IWM", "DJX": "DIA"}
UNGRADEABLE = {"VIX"}   # no clean 1x ETF proxy — recorded but left ungraded

DATA_BASE = "https://data.alpaca.markets"
TIMEOUT = 15
UA = {"User-Agent": "quarki-equity-social/1.0"}

TOP_OVERSOLD_N = 10          # how many sp400 oversold names to add to the universe
MIN_PRICE = 5.0               # same liquidity floor as midcap_lane.py's scan()
MIN_DOLLAR_VOL = 5_000_000.0  # ditto — keeps the oversold leg to tradeable names

REDDIT_SUBS = ("stocks", "wallstreetbets", "investing", "StockMarket", "options")
NEWS_CHUNK = 10                # symbols per Alpaca news request
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# ── intraday additions (2026-08-12) ─────────────────────────────────────
HISTORY_FILE = SCANNER_STATE / "equity_social_history.jsonl"
HISTORY_MAX_LINES = 2500
HISTORY_KEEP_LINES = 2000
ALERT_STATE = ROOT / "alert_state.json"
SPIKE_Z_THRESHOLD = 2.5
STOCKTWITS_TRENDING_URL = "https://api.stocktwits.com/api/2/trending/symbols.json"
MARKET_TZ = ZoneInfo("America/Denver")
MARKET_GUARD_START = (6, 55)   # 06:55 America/Denver
MARKET_GUARD_END = (14, 40)    # 14:40 America/Denver


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


# ── Google News RSS leg ──────────────────────────────────────────────────
# Keyless — Google's own RSS search surface, no API key needed. RSS 2.0
# (channel/item, not Atom) so this parses differently from the reddit leg.
def _google_news_symbol(sym: str) -> dict | None:
    url = (f"https://news.google.com/rss/search?q=%22{sym}%22%20stock"
           "&hl=en-US&gl=US&ceid=US:en")
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        root = ET.fromstring(r.text)
    except Exception as e:
        print(f"google news {sym} failed: {e!r}")
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = []
    count_24h = 0
    for item in root.findall("./channel/item"):
        title = item.findtext("title") or ""
        pub = item.findtext("pubDate") or ""
        try:
            ts = email.utils.parsedate_to_datetime(pub)
            ts = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if ts >= cutoff:
            count_24h += 1
        if title:
            rows.append((ts, title))
    rows.sort(key=lambda t: t[0], reverse=True)
    return {"count_24h": count_24h, "titles": [t for _, t in rows[:3]]}


def _google_news_leg(universe: list[str], per_symbol: dict) -> None:
    """Adds to the existing news_24h count (Alpaca + Google combined signal)
    and populates the new news_titles field. Whole leg try/except-wrapped —
    a dead Google endpoint should never kill the run."""
    for i, sym in enumerate(universe):
        gn = None
        try:
            gn = _google_news_symbol(sym)
        except Exception as e:
            print(f"google news {sym} unexpected failure: {e!r}")
        if gn:
            per_symbol[sym]["news_24h"] = per_symbol[sym].get("news_24h", 0) + gn["count_24h"]
            per_symbol[sym]["news_titles"] = gn["titles"]
        if i < len(universe) - 1:
            time.sleep(0.5)


# ── StockTwits trending leg ──────────────────────────────────────────────
def _stocktwits_trending() -> set[str]:
    r = requests.get(STOCKTWITS_TRENDING_URL, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return {(s.get("symbol") or "").upper() for s in (data.get("symbols") or [])}


# ── history store + composite spike score ───────────────────────────────
def _load_history() -> list[dict]:
    try:
        lines = HISTORY_FILE.read_text().splitlines()
    except FileNotFoundError:
        return []
    except Exception as e:
        print(f"history read failed: {e!r}")
        return []
    out = []
    for line in lines:
        if line.strip():
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _append_history(per_symbol: dict) -> None:
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        syms = {s: {"rate": d.get("st_msg_rate_hr", 0.0),
                    "reddit": d.get("reddit_mentions", 0),
                    "news": d.get("news_24h", 0)} for s, d in per_symbol.items()}
        row = json.dumps({"ts": now_iso(), "syms": syms})
        with HISTORY_FILE.open("a") as f:
            f.write(row + "\n")
        lines = HISTORY_FILE.read_text().splitlines()
        if len(lines) > HISTORY_MAX_LINES:
            keep = lines[-HISTORY_KEEP_LINES:]
            tmp = HISTORY_FILE.with_suffix(".tmp")
            tmp.write_text("\n".join(keep) + "\n")
            tmp.replace(HISTORY_FILE)
    except Exception as e:
        print(f"history store append failed: {e!r}")


def _zscore(current: float, history_vals: list[float]) -> float:
    if len(history_vals) < 5:
        return 0.0
    mean = statistics.mean(history_vals)
    std = statistics.pstdev(history_vals)
    if std == 0:
        return 0.0
    return (current - mean) / std


def _compute_buzz_z(per_symbol: dict, history: list[dict]) -> None:
    for sym, d in per_symbol.items():
        rates, reddits, news = [], [], []
        for row in history:
            s = (row.get("syms") or {}).get(sym)
            if s:
                rates.append(s.get("rate", 0.0))
                reddits.append(s.get("reddit", 0))
                news.append(s.get("news", 0))
        zs = []
        if len(rates) >= 5:
            zs.append(_zscore(d.get("st_msg_rate_hr", 0.0), rates))
        if len(reddits) >= 5:
            zs.append(_zscore(d.get("reddit_mentions", 0), reddits))
        if len(news) >= 5:
            zs.append(_zscore(d.get("news_24h", 0), news))
        d["buzz_z"] = round(sum(zs) / len(zs), 3) if zs else 0.0


# ── spike alerts (D28 plain-English, 24h repeat-suppressed) ─────────────
def _load_alert_state() -> dict:
    try:
        return json.loads(ALERT_STATE.read_text())
    except Exception:
        return {}


def _should_alert(prev: dict | None, z: float, now: datetime) -> bool:
    if not prev:
        return True
    try:
        last_ts = datetime.fromisoformat(prev.get("last_ts", ""))
        elapsed_h = (now - last_ts).total_seconds() / 3600
    except Exception:
        elapsed_h = 999.0
    last_z = prev.get("last_z", 0.0)
    return elapsed_h >= 24 or z >= last_z + 1.0


def _send_spike_alert(sym: str, z: float, d: dict) -> None:
    reasons = []
    if d.get("st_msgs", 0) >= 10:
        reasons.append(f"{d['st_msgs']} StockTwits messages")
    if d.get("reddit_mentions", 0) >= 3:
        reasons.append(f"{d['reddit_mentions']} Reddit mentions")
    if d.get("news_24h", 0) >= 3:
        reasons.append(f"{d['news_24h']} news items in the last 24h")
    why = "; ".join(reasons) if reasons else "elevated chatter"
    title = f"Buzz spike on {sym} (you hold it)"
    message = (f"{sym} social/news buzz is {z:.1f} standard deviations above its "
               f"normal baseline ({why}). You currently hold this position — "
               f"worth a quick look at what's driving it before it moves the price.")
    try:
        subprocess.run([sys.executable, str(HOME / "bin" / "quarki_notify.py"),
                        "action", title, message],
                       timeout=20)
    except Exception as e:
        print(f"spike alert push failed for {sym}: {e!r}")


def _spike_scoring_and_alerts(per_symbol: dict) -> list[dict]:
    spikes: list[dict] = []
    history = _load_history()
    _compute_buzz_z(per_symbol, history)
    held = set(_book_positions())
    alert_state = _load_alert_state()
    changed = False
    now = datetime.now(timezone.utc)
    for sym, d in per_symbol.items():
        z = d.get("buzz_z", 0.0)
        if z < SPIKE_Z_THRESHOLD:
            continue
        is_held = sym in held
        spikes.append({"sym": sym, "z": z, "held": is_held})
        if not is_held:
            continue
        if not (d.get("st_msgs", 0) >= 10 or d.get("reddit_mentions", 0) >= 3
                or d.get("news_24h", 0) >= 3):
            continue
        if _should_alert(alert_state.get(sym), z, now):
            _send_spike_alert(sym, z, d)
            alert_state[sym] = {"last_ts": now.isoformat(timespec="seconds"), "last_z": z}
            changed = True
    if changed:
        _atomic_write(ALERT_STATE, alert_state)
    return spikes


# ── market-hours guard ────────────────────────────────────────────────────
def _within_market_hours(force: bool) -> bool:
    if force:
        return True
    now = datetime.now(MARKET_TZ)
    if now.weekday() >= 5:   # Sat=5, Sun=6
        return False
    start = now.replace(hour=MARKET_GUARD_START[0], minute=MARKET_GUARD_START[1],
                        second=0, microsecond=0)
    end = now.replace(hour=MARKET_GUARD_END[0], minute=MARKET_GUARD_END[1],
                      second=0, microsecond=0)
    return start <= now <= end


# ── collect ──────────────────────────────────────────────────────────────
def collect(force: bool = False) -> None:
    if not _within_market_hours(force):
        now = datetime.now(MARKET_TZ)
        print(f"collect: outside market-hours guard (06:55-14:40 America/Denver, "
              f"Mon-Fri) and --force not set — skipping. now={now.isoformat()}")
        return
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
            "news_titles": [],
            "st_trending": False,
            "buzz_z": 0.0,
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

    try:
        _google_news_leg(universe, per_symbol)
    except Exception as e:
        print(f"google news leg failed entirely: {e!r}")

    try:
        trending = _stocktwits_trending()
        for sym in universe:
            per_symbol[sym]["st_trending"] = sym in trending
    except Exception as e:
        print(f"stocktwits trending leg failed entirely: {e!r}")

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

    spikes: list[dict] = []
    try:
        spikes = _spike_scoring_and_alerts(per_symbol)
    except Exception as e:
        print(f"spike scoring/alerts failed entirely: {e!r}")

    try:
        _append_history(per_symbol)
    except Exception as e:
        print(f"history store failed entirely: {e!r}")

    out = {"ts": now_iso(), "per_symbol": per_symbol, "top_buzz": top_buzz, "spikes": spikes}
    _atomic_write(OUT_SOCIAL, out)
    print(f"wrote {OUT_SOCIAL} symbols={len(universe)} "
          f"top_buzz={[b['sym'] for b in top_buzz]} spikes={[s['sym'] for s in spikes]}")


# ── x_fetch: @tradergrader timeline ingest ───────────────────────────────
def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _nitter_posts() -> list[dict]:
    """Latest ~20 posts via nitter RSS. Skips retweets (dc:creator differs
    from the tracked handle). Raises only if every instance fails."""
    last_err: Exception | None = None
    for inst in NITTER_INSTANCES:
        try:
            r = requests.get(f"https://{inst}/{X_HANDLE}/rss",
                             headers=BROWSER_UA, timeout=TIMEOUT)
            r.raise_for_status()
            root = ET.fromstring(r.text)
        except Exception as e:
            last_err = e
            print(f"nitter {inst} failed: {e!r}")
            continue
        posts = []
        for it in root.findall(".//item"):
            creator = (it.findtext("{http://purl.org/dc/elements/1.1/}creator")
                       or "").lstrip("@").lower()
            if creator != X_HANDLE:
                continue
            tid = (it.findtext("guid") or "").rsplit("/", 1)[-1].split("#")[0]
            if not tid.isdigit():
                continue
            try:
                ts = email.utils.parsedate_to_datetime(
                    it.findtext("pubDate") or "").astimezone(timezone.utc)
            except Exception:
                continue
            posts.append({"id": tid, "ts": ts.isoformat(timespec="seconds"),
                          "text": _strip_html(it.findtext("description") or "")})
        if posts:
            print(f"nitter {inst}: {len(posts)} posts")
            return posts
    if last_err:
        raise last_err
    return []


def _syndication_posts() -> list[dict]:
    """X's own public embed/syndication widget endpoint — keyless, and the
    only route with real history (~100 entries). Verified live 2026-08-12."""
    r = requests.get(SYNDICATION_URL, headers=BROWSER_UA, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  r.text, re.S)
    if not m:
        raise RuntimeError("syndication page had no __NEXT_DATA__ blob")
    entries = (json.loads(m.group(1))["props"]["pageProps"]
               .get("timeline", {}).get("entries", []))
    posts = []
    for e in entries:
        tw = (e.get("content") or {}).get("tweet") or {}
        if ((tw.get("user") or {}).get("screen_name") or "").lower() != X_HANDLE:
            continue
        tid = tw.get("id_str") or ""
        text = tw.get("full_text") or tw.get("text") or ""
        try:
            ts = datetime.strptime(tw.get("created_at", ""),
                                   "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc)
        except Exception:
            continue
        if tid.isdigit() and text:
            posts.append({"id": tid, "ts": ts.isoformat(timespec="seconds"),
                          "text": _strip_html(text)})
    print(f"syndication: {len(posts)} posts")
    return posts


_TRADE_RE = re.compile(r"Trade:\s*\$?([A-Za-z]{1,6})\s+(\d+(?:\.\d+)?)\s*([CPcp])\b"
                       r"(?:\s+(\d{1,2}/\d{1,2}))?")
_ROI_RE = re.compile(r"Potential ROI:\s*\+?([\d,]+(?:\.\d+)?)\s*%")
_SNIPER_RE = re.compile(r"Sniper of the Day\s*@?(\w+)")
_TOTW_RE = re.compile(r"Trade of the Week[^@]{0,60}@(\w+)")
_MEDAL_RE = re.compile(r"(🥇|🥈|🥉)\s*@?(\w+)\s*[—–-]+\s*\+?(-?[\d,]+(?:\.\d+)?)\s*%")
# TraderGrader's own retraction/correction language ("we found an anomaly
# in this trade's performance, updating our database"). These posts
# re-quote the original callout text verbatim, so without this check
# _TRADE_RE/_ROI_RE would happily re-ingest the disowned figures as a
# fresh, normal callout.
_RETRACTION_RE = re.compile(
    r"\b(anomaly|investigated|updating our database|correction|retract)\w*",
    re.IGNORECASE)


def _rec_from_post(p: dict) -> dict | None:
    """Sniper-of-the-Day / Trade-of-the-Week callout → grader rec. Side is
    the option's direction (call=long, put=short) applied to the underlying
    (index symbols mapped to their ETF proxy)."""
    text = p["text"]
    if _RETRACTION_RE.search(text):
        # A self-flagged correction/anomaly notice, not a genuine callout —
        # skip it rather than re-ingesting the disowned/original figures.
        return None
    trader = None
    m = _SNIPER_RE.search(text) or _TOTW_RE.search(text)
    if m:
        trader = m.group(1)
    if trader is None:
        # Not an actual Sniper-of-the-Day / Trade-of-the-Week callout —
        # bail before _TRADE_RE gets a chance to match an unrelated
        # "Trade:" substring inside recap text about other traders
        # (e.g. "Most Profitable Trade: $CVNA 130p 9/9").
        return None
    tm = _TRADE_RE.search(text)
    if not tm:
        return None
    raw_sym, strike, cp, mmdd = tm.group(1).upper(), tm.group(2), tm.group(3).upper(), tm.group(4)
    post_date = date.fromisoformat(p["ts"][:10])
    rec_date = post_date
    if mmdd:   # the callout names the trade date; the post lands the same evening
        try:
            mth, dy = (int(x) for x in mmdd.split("/"))
            cand = date(post_date.year, mth, dy)
            if cand > post_date:   # e.g. a January post recapping December
                cand = date(post_date.year - 1, mth, dy)
            rec_date = cand
        except ValueError:
            pass
    sym = INDEX_PROXY.get(raw_sym, raw_sym)
    if raw_sym in UNGRADEABLE:
        return None
    rm = _ROI_RE.search(text)
    roi = rm.group(1).replace(",", "") if rm else None
    kind = "sniper" if _SNIPER_RE.search(text) else \
           ("trade_of_week" if _TOTW_RE.search(text) else "callout")
    note = f"{kind} @{trader or '?'}: ${raw_sym} {strike}{cp}" \
           + (f" {mmdd}" if mmdd else "") \
           + (f", claimed ROI {roi}%" if roi else "") \
           + (f" (graded on {sym} proxy)" if sym != raw_sym else "")
    return {"ts": rec_date.isoformat(), "sym": sym,
            "side": "long" if cp == "C" else "short",
            "note": note, "src_id": p["id"], "px_at_rec": None}


def _parse_posts(posts: list[dict]) -> tuple[list[dict], dict]:
    recs: dict[str, dict] = {}
    board: dict[str, dict] = {}
    for p in sorted(posts, key=lambda x: x["ts"]):
        rec = None
        try:
            rec = _rec_from_post(p)
        except Exception as e:
            print(f"rec parse failed id={p['id']}: {e!r}")
        if rec:
            recs[rec["src_id"]] = rec
        for medal, handle, roi in _MEDAL_RE.findall(p["text"]):
            h = handle.lower()
            row = board.setdefault(h, {"handle": handle, "medals": 0, "golds": 0,
                                       "claimed_rois": [], "last_seen": p["ts"]})
            row["medals"] += 1
            row["golds"] += int(medal == "🥇")
            try:
                row["claimed_rois"].append(float(roi.replace(",", "")))
            except ValueError:
                pass
            row["last_seen"] = max(row["last_seen"], p["ts"])
    for row in board.values():
        rois = row.pop("claimed_rois")
        row["avg_claimed_roi"] = round(sum(rois) / len(rois), 1) if rois else None
    ranked = dict(sorted(board.items(),
                         key=lambda kv: (kv[1]["golds"], kv[1]["medals"]), reverse=True))
    return list(recs.values()), ranked


def _load_posts() -> dict[str, dict]:
    posts: dict[str, dict] = {}
    try:
        for line in X_POSTS.read_text().splitlines():
            if line.strip():
                p = json.loads(line)
                posts[p["id"]] = p
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"x posts store read failed: {e!r}")
    return posts


def x_fetch() -> None:
    posts = _load_posts()
    before = len(posts)
    for fetcher in (_nitter_posts, _syndication_posts):
        try:
            for p in fetcher():
                posts.setdefault(p["id"], p)
        except Exception as e:
            print(f"{fetcher.__name__} failed entirely: {e!r}")
    ordered = sorted(posts.values(), key=lambda p: p["ts"])
    tmp = X_POSTS.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(p) + "\n" for p in ordered))
    tmp.replace(X_POSTS)

    recs, board = _parse_posts(ordered)
    _atomic_write(X_AUTO_RECS, {"ts": now_iso(), "handle": X_HANDLE, "recs": recs})
    _atomic_write(OUT_LEADERBOARD, {"ts": now_iso(), "handle": X_HANDLE,
                                    "n_posts": len(ordered), "traders": board})
    print(f"x_fetch: posts {before} -> {len(ordered)} (+{len(ordered) - before}), "
          f"recs={len(recs)}, leaderboard traders={len(board)}")


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
    # If the rec's underlying (after INDEX_PROXY mapping) is itself SPY —
    # the exact instrument used as the market benchmark below — comparing
    # it to "SPY" would be self-referential (excess_pct always 0 for longs,
    # always -2x the raw move for shorts). Only SPX/SPXW/XSP/ES map to SPY;
    # NDX/RUT/DJX map to QQQ/IWM/DIA and stay independently benchmarkable.
    spy_idx = None if sym == "SPY" else _anchor_idx(spy_bars, rec_date)

    px_given = rec.get("px_at_rec")
    px_at_rec = px_given if px_given is not None else (bars[idx]["c"] if idx is not None else None)

    row = {"ts": ts, "sym": sym, "side": side, "note": rec.get("note"), "px_at_rec": px_at_rec}
    for h, key in ((1, "h1"), (5, "h5"), (20, "h20"), (60, "h60")):
        row[key] = _horizon(bars, idx, px_at_rec, spy_bars, spy_idx, h, side)
    return row


def _write_catalog_row(accounts: dict) -> None:
    """Publish TraderGrader as a standard catalog benchmark row (Robert
    2026-08-12: 'exact same layout and data as the rest of the cards').
    Curve = base-100 NAV of FOLLOWING each auto-graded callout's 1-day
    signed return, compounded in ts order — the honest testable version of
    his feed, same {curve, updated_ts} shape autopilot_catalog.py writes.
    The pilots entry is metadata-only (_-prefixed keys, no weights) so the
    catalog engine's holdings marker skips it; edge_ledger's
    _autopilot_catalog_rows() turns it into the card row."""
    acct = accounts.get("tradergrader_auto") or {}
    recs = sorted((acct.get("recs") or []), key=lambda r: str(r.get("ts")))
    nav, curve = 100.0, {}
    for r in recs:
        h1 = r.get("h1") or {}
        if h1.get("ret_pct") is None:
            continue
        nav *= 1.0 + h1["ret_pct"] / 100.0
        curve[str(r["ts"])[:10]] = round(nav, 4)
    today = date.today().isoformat()
    curve[today] = round(nav, 4)   # flat carry-forward so the card has a fresh mark

    curves_path = SCANNER_STATE / "autopilot_catalog_curves.json"
    try:
        curves = json.loads(curves_path.read_text())
    except Exception:
        curves = {}
    curves["TraderGrader"] = {"curve": curve, "updated_ts": now_iso()}
    _atomic_write(curves_path, curves)

    lb = {}
    try:
        lb = json.loads(OUT_LEADERBOARD.read_text()).get("traders") or {}
    except Exception:
        pass
    favs = " ".join("@" + (v.get("handle") or k) for k, v in list(lb.items())[:3])
    wr = acct.get("win_rate_1d")
    blurb = (f"@tradergrader on X — grades other options traders; this row follows "
             f"his Sniper/Trade-of-the-Week callouts at the close they land. "
             f"{acct.get('n', 0)} recs, 1d win {round(wr * 100) if wr is not None else '—'}%, "
             f"avg 1d vs SPY {acct.get('avg_excess_1d', '—')}%. His podium lately: {favs or '—'}. "
             "Retrospective recaps — tests FOLLOWING, not his entry timing. Never real money.")
    note_lines = ["Recent callouts (1d/20d vs SPY):"]
    for r in recs[-5:][::-1]:
        h1, h20 = r.get("h1") or {}, r.get("h20") or {}
        note_lines.append(
            f"{str(r['ts'])[:10]} {r['sym']} {r['side']}: "
            f"1d {h1.get('ret_pct', '…')}% / 20d {h20.get('ret_pct', '…')}% — {r.get('note', '')[:80]}")
    note_lines.append("Medal leaderboard: " + (", ".join(
        f"@{v.get('handle') or k} {v.get('golds', 0)}g/{v.get('medals', 0)}m"
        for k, v in list(lb.items())[:5]) or "none yet"))
    cat_path = SCANNER_STATE / "autopilot_catalog.json"
    try:
        cat = json.loads(cat_path.read_text())
    except Exception:
        return
    pilots = cat.setdefault("pilots", {})
    row = pilots.get("TraderGrader") or {}
    row.update({"_blurb": blurb, "_note": "\n".join(note_lines),
                "_holdings_source": "equity_social.py grade leg (daily 15:35 MT)",
                "_record": "display-only benchmark; STRUCTURALLY never receives money"})
    pilots["TraderGrader"] = row
    _atomic_write(cat_path, cat)
    print(f"catalog row TraderGrader: nav={nav:.2f} points={len(curve)}")


def grade() -> None:
    tracked = _load_tracked()
    # x_fetch's auto-parsed callouts join the manual accounts as their own
    # synthetic account so hand-pasted recs and auto recs never mix.
    try:
        auto = json.loads(X_AUTO_RECS.read_text())
        if auto.get("recs"):
            tracked["tradergrader_auto"] = {
                "source": "x", "handle": auto.get("handle", X_HANDLE),
                "ingest": "auto", "recs": auto["recs"]}
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"x_auto_recs read failed: {e!r}")
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

        resolved_1d = [r for r in rec_rows if r.get("h1", {}).get("win") is not None]
        win_rate_1d = round(sum(1 for r in resolved_1d if r["h1"]["win"]) / len(resolved_1d), 4) \
            if resolved_1d else None
        excess_1d = [r["h1"]["excess_pct"] for r in resolved_1d
                     if r["h1"].get("excess_pct") is not None]
        avg_excess_1d = round(sum(excess_1d) / len(excess_1d), 3) if excess_1d else None

        resolved = [r for r in rec_rows if r.get("h20", {}).get("win") is not None]
        win_rate_20d = round(sum(1 for r in resolved if r["h20"]["win"]) / len(resolved), 4) \
            if resolved else None
        excess_vals = [r["h20"]["excess_pct"] for r in resolved
                       if r["h20"].get("excess_pct") is not None]
        avg_excess_20d = round(sum(excess_vals) / len(excess_vals), 3) if excess_vals else None
        best = worst = None
        if excess_vals:
            scored = [r for r in resolved
                      if r["h20"].get("excess_pct") is not None]
            best_row = max(scored, key=lambda r: r["h20"]["excess_pct"])
            worst_row = min(scored, key=lambda r: r["h20"]["excess_pct"])
            best = {"sym": best_row["sym"], "ts": best_row["ts"],
                    "excess_20d_pct": best_row["h20"]["excess_pct"]}
            worst = {"sym": worst_row["sym"], "ts": worst_row["ts"],
                     "excess_20d_pct": worst_row["h20"]["excess_pct"]}

        accounts_out[acct_name] = {
            "source": acct.get("source"), "handle": acct.get("handle"),
            "ingest": acct.get("ingest"),
            "n": len(recs), "n_resolved_20d": len(resolved),
            "n_resolved_1d": len(resolved_1d),
            "win_rate_1d": win_rate_1d, "avg_excess_1d": avg_excess_1d,
            "win_rate_20d": win_rate_20d, "avg_excess_20d": avg_excess_20d,
            "best": best, "worst": worst,
            "recs": rec_rows,
        }

    out = {"ts": now_iso(), "accounts": accounts_out}
    _atomic_write(OUT_GRADES, out)
    try:
        _write_catalog_row(accounts_out)
    except Exception as e:
        print(f"catalog row write failed: {e!r}")
    print(f"wrote {OUT_GRADES} accounts={list(accounts_out)}")
    for name, a in accounts_out.items():
        print(f"  {name}: n={a['n']} resolved_1d={a['n_resolved_1d']} "
              f"win_rate_1d={a['win_rate_1d']} resolved_20d={a['n_resolved_20d']} "
              f"win_rate_20d={a['win_rate_20d']} avg_excess_20d={a['avg_excess_20d']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--leg", choices=["collect", "grade", "x_fetch"], required=True)
    ap.add_argument("--force", action="store_true",
                    help="bypass the market-hours guard on --leg collect")
    args = ap.parse_args()
    if args.leg == "collect":
        collect(force=args.force)
    elif args.leg == "x_fetch":
        x_fetch()
    else:
        grade()


if __name__ == "__main__":
    main()
