#!/usr/bin/env python3
"""intel_engine — persistent news/social/sentiment collector (D40).

Runs on quarki-stack via quarki-intel.timer (30 min), Mac-off independent.
Public keyless sources only (official APIs + RSS; no login scraping):
  Reddit hot JSON, StockTwits symbol streams, alternative.me Fear&Greed,
  CoinGecko global, CoinDesk/CoinTelegraph RSS, Google News RSS per symbol.
Output: intel_engine/state/intel.json (+ rolling intel_history.jsonl) served
owner-only at /api/crypto/intel (merged with whale composite) and
/api/equity/intel. Stdlib only — the VM has 1GB RAM.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
STATE.mkdir(exist_ok=True)
UA = {"User-Agent": "quarki-intel/1.0 (personal research; contact robert.m.painter@gmail.com)"}
DASH = "http://127.0.0.1:8080"

CRYPTO_SYMS = ["BTC", "ETH", "SOL"]
EQUITY_FALLBACK = ["SPY", "QQQ", "INTC", "UBER", "SCHX"]

BULL = {"moon", "bull", "bullish", "rally", "surge", "soar", "breakout", "pump",
        "buy", "long", "calls", "upgrade", "beat", "beats", "record", "high",
        "gain", "gains", "up", "green", "ath", "adoption", "approval", "win"}
BEAR = {"crash", "bear", "bearish", "dump", "plunge", "tank", "collapse",
        "sell", "short", "puts", "downgrade", "miss", "misses", "low", "loss",
        "losses", "down", "red", "fear", "lawsuit", "hack", "ban", "fraud",
        "bankruptcy", "recession", "warning", "sec"}


def _get(url: str, timeout: int = 10):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_json(url: str, timeout: int = 10):
    return json.loads(_get(url, timeout))


def lexicon_score(texts: list[str]) -> float | None:
    """Crude bull-bear ratio over titles: 0..1, None if no signal words."""
    b = s = 0
    for t in texts:
        words = set(re.findall(r"[a-z']+", t.lower()))
        b += len(words & BULL)
        s += len(words & BEAR)
    return round(b / (b + s), 3) if (b + s) else None


def _rss_titles(url: str, limit: int = 15) -> list[dict]:
    root = ET.fromstring(_get(url))
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if title:
            out.append({"title": title[:200], "link": link[:300]})
        if len(out) >= limit:
            break
    return out


def held_equity_symbols() -> list[str]:
    try:
        d = _get_json(f"{DASH}/api/live/alpaca_live")
        syms = [p.get("symbol") for p in d.get("positions", []) if p.get("symbol")]
        return sorted(set(syms))[:15] or EQUITY_FALLBACK
    except Exception:
        return EQUITY_FALLBACK


def reddit(sub: str) -> dict:
    # .json is 403-blocked for anonymous clients; the Atom feed is open.
    # Space out calls — 3 quick hits from one IP earn a 429.
    time.sleep(10)
    root = ET.fromstring(_get(f"https://www.reddit.com/r/{sub}/hot.rss"))
    ns = "{http://www.w3.org/2005/Atom}"
    if root.tag != f"{ns}feed":
        # A WAF/interstitial page that happens to be valid XML would
        # otherwise read as a silent zero-post result.
        raise ValueError(f"unexpected feed root {root.tag!r}")
    entries = []
    for e in root.iter(f"{ns}entry"):
        t = (e.findtext(f"{ns}title") or "").strip()
        ln = e.find(f"{ns}link")
        if t:
            entries.append({"title": t[:200],
                            "link": (ln.get("href", "") if ln is not None else "")[:300]})
    entries = entries[:25]
    titles = [x["title"] for x in entries]
    # symbols people are talking about (cashtags + bare tickers in caps)
    mentions: dict[str, int] = {}
    for t in titles:
        for m in re.findall(r"\$([A-Z]{2,5})\b|\b(BTC|ETH|SOL|DOGE|XRP|PEPE)\b", t):
            sym = m[0] or m[1]
            mentions[sym] = mentions.get(sym, 0) + 1
    return {
        "sub": sub,
        "posts": len(titles),
        "sentiment": lexicon_score(titles),
        "top_mentions": dict(sorted(mentions.items(), key=lambda kv: -kv[1])[:8]),
        "top_titles": [{"title": x["title"][:160], "link": x["link"]}
                       for x in entries[:8]],
    }


def stocktwits(sym: str) -> dict:
    d = _get_json(f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json")
    msgs = d.get("messages", [])
    bull = sum(1 for m in msgs
               if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bullish")
    bear = sum(1 for m in msgs
               if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bearish")
    samples = [{"body": (m.get("body") or "")[:140],
                "sentiment": ((m.get("entities", {}).get("sentiment") or {})
                              .get("basic") or "")}
               for m in msgs[:5] if m.get("body")]
    return {"symbol": sym, "messages": len(msgs), "bullish": bull, "bearish": bear,
            "ratio": round(bull / (bull + bear), 3) if (bull + bear) else None,
            "samples": samples}


def fear_greed() -> dict:
    d = _get_json("https://api.alternative.me/fng/?limit=2")["data"]
    return {"value": int(d[0]["value"]), "label": d[0]["value_classification"],
            "yesterday": int(d[1]["value"]) if len(d) > 1 else None}


def coingecko_global() -> dict:
    d = _get_json("https://api.coingecko.com/api/v3/global")["data"]
    return {
        "mcap_change_24h_pct": round(d.get("market_cap_change_percentage_24h_usd", 0), 2),
        "btc_dominance": round(d.get("market_cap_percentage", {}).get("btc", 0), 1),
        "eth_dominance": round(d.get("market_cap_percentage", {}).get("eth", 0), 1),
    }


def google_news(query: str, limit: int = 8) -> list[dict]:
    from urllib.parse import quote
    q = quote(query)
    return _rss_titles(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en", limit)


def collect() -> dict:
    doc: dict = {"asof": datetime.now(timezone.utc).isoformat(), "errors": {}}

    def leg(name, fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            doc["errors"][name] = f"{type(e).__name__}: {e}"[:120]
            return None

    # ---- crypto side -------------------------------------------------
    crypto: dict = {}
    crypto["fear_greed"] = leg("fear_greed", fear_greed)
    crypto["global"] = leg("coingecko", coingecko_global)
    crypto["reddit"] = leg("reddit_cc", reddit, "CryptoCurrency")
    crypto["stocktwits"] = [x for x in
                            (leg(f"st_{s}", stocktwits, f"{s}.X") for s in CRYPTO_SYMS) if x]
    news = leg("coindesk", _rss_titles, "https://www.coindesk.com/arc/outboundfeeds/rss/", 10) or []
    news += leg("cointelegraph", _rss_titles, "https://cointelegraph.com/rss", 10) or []
    crypto["headlines"] = news[:15]
    crypto["news_sentiment"] = lexicon_score([h["title"] for h in news]) if news else None
    crypto["news_by_symbol"] = {
        s: leg(f"gnews_c_{s}", google_news, q, 4) or []
        for s, q in [("BTC", "bitcoin"), ("ETH", "ethereum"), ("SOL", "solana")]}

    # ---- equities side ----------------------------------------------
    held = held_equity_symbols()
    eq: dict = {"held": held}
    eq["reddit_wsb"] = leg("reddit_wsb", reddit, "wallstreetbets")
    eq["reddit_stocks"] = leg("reddit_stocks", reddit, "stocks")
    eq["stocktwits"] = [x for x in (leg(f"st_{s}", stocktwits, s) for s in held[:8]) if x]
    eq["news_by_symbol"] = {s: leg(f"gnews_{s}", google_news, f"{s} stock", 5) or []
                            for s in held[:6]}

    # ---- composite social risk-on (0..1) ----------------------------
    parts = []
    fg = crypto.get("fear_greed") or {}
    if fg.get("value") is not None:
        parts.append(fg["value"] / 100)
    for block in (crypto.get("reddit"), eq.get("reddit_wsb"), eq.get("reddit_stocks")):
        if block and block.get("sentiment") is not None:
            parts.append(block["sentiment"])
    st_ratios = [s["ratio"] for s in crypto.get("stocktwits", []) + eq.get("stocktwits", [])
                 if s.get("ratio") is not None]
    if st_ratios:
        parts.append(sum(st_ratios) / len(st_ratios))
    if crypto.get("news_sentiment") is not None:
        parts.append(crypto["news_sentiment"])
    doc["risk_on_social"] = round(sum(parts) / len(parts), 3) if parts else None
    doc["crypto"] = crypto
    doc["equities"] = eq
    return doc


def main():
    doc = collect()
    tmp = STATE / "intel.json.tmp"
    tmp.write_text(json.dumps(doc, indent=1))
    tmp.replace(STATE / "intel.json")
    hist = STATE / "intel_history.jsonl"
    with hist.open("a") as f:
        f.write(json.dumps({"ts": int(time.time()),
                            "risk_on_social": doc.get("risk_on_social"),
                            "fear_greed": (doc["crypto"].get("fear_greed") or {}).get("value"),
                            "errors": len(doc["errors"])}) + "\n")
    # Rotate: cap at ~30k rows (~1.7 years at 48/day) via atomic rewrite.
    lines = hist.read_text().splitlines()
    if len(lines) > 30_000:
        htmp = STATE / "intel_history.jsonl.tmp"
        htmp.write_text("\n".join(lines[-30_000:]) + "\n")
        htmp.replace(hist)
    ok = sum(1 for k in ("fear_greed", "reddit_cc", "reddit_wsb") if k not in doc["errors"])
    print(f"intel ok: risk_on_social={doc.get('risk_on_social')} "
          f"errors={len(doc['errors'])} core_legs_ok={ok}/3")


if __name__ == "__main__":
    main()
