#!/usr/bin/env python3
"""Composite whale signal → state/whale_signals.json (consumed by sleeve.py's
whale_gate). Two independent legs, equally weighted where both have data:

  LEG 1 — Hyperliquid top-trader positioning (free, no key, refreshed every
  run): top ~40 traders by 30d PnL, their live perp positions on the
  sleeve's coins. The account-value band is a HEURISTIC that trims the
  extremes; the leaderboard API exposes no account-type field, so
  copy-trading vaults / protocol accounts inside the band are
  indistinguishable from individual whales (accepted limitation).
  risk_on = long notional / (long + short). Breadth counters map naturally:
  each trader = a wallet, each position = a trade. Also writes per-coin bias
  (coin_bias) for the UI and future per-symbol gating.

  LEG 2 — Dune smart-money DEX flows (Q03, costs 1 execution): re-run only
  when its data is older than DUNE_TTL_H; otherwise the previous leg is
  reused so an hourly launchd cadence doesn't burn Dune credits.

Empty/failed legs are dropped (never written as risk_on=0); if both fail the
previous file is left untouched and the sleeve stays momentum-only. The gate's
minimum-evidence rule (n>=5 trades, >=3 wallets, >=$50k) applies to the
combined counters. Runs via launchd com.quarki3.crypto-whale-refresh.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "state" / "whale_signals.json"

DUNE_TTL_H = 6
HL_TOP_N = 40
HL_MIN_ACCT = 1_000_000        # below: noise
HL_MAX_ACCT = 500_000_000      # above: protocol vaults / aggregates, not traders
COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX", "LTC", "DOT",
         "XLM", "SUI", "CRV", "ONDO", "PENGU", "VVV", "PEPE", "kPEPE", "AAVE")

# StockTwits crypto symbol convention (verified live 2026-08-09, keyless)
_ST_SYMBOLS = {"BTC": "BTC.X", "ETH": "ETH.X", "SOL": "SOL.X", "XRP": "XRP.X",
               "DOGE": "DOGE.X", "ADA": "ADA.X", "LINK": "LINK.X", "AVAX": "AVAX.X",
               "LTC": "LTC.X", "DOT": "DOT.X", "XLM": "XLM.X", "SUI": "SUI.X",
               "CRV": "CRV.X", "AAVE": "AAVE.X", "PEPE": "PEPE.X"}

STABLES = {"USDC", "USDT", "USDe", "DAI", "FRAX", "LUSD", "GUSD", "TUSD", "PYUSD",
           "USD₮", "USDT0", "USDC.e", "USDm", "USD₮0", "sUSDe", "GHO", "crvUSD", "FDUSD"}
MAJORS = {"ETH", "WETH", "BTC", "WBTC", "stETH", "wstETH", "rETH", "cbETH", "weETH"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def age_h(iso: str) -> float:
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
    except Exception:
        return 1e9


# ── LEG 1: Hyperliquid top-trader positioning ────────────────────────────
def _post(url: str, body: dict):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


def leg_hyperliquid() -> dict | None:
    try:
        with urllib.request.urlopen(
                "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard", timeout=40) as r:
            rows = json.load(r)["leaderboardRows"]
    except Exception as e:
        print(f"HL leaderboard failed: {e!r}")
        return None

    def month_pnl(row):
        for w, p in row.get("windowPerformances", []):
            if w == "month":
                try:
                    return float(p.get("pnl", 0))
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    traders = [r for r in rows
               if HL_MIN_ACCT <= float(r.get("accountValue", 0) or 0) <= HL_MAX_ACCT]
    traders = sorted(traders, key=month_pnl, reverse=True)[:HL_TOP_N]

    long_usd = short_usd = 0.0
    n_pos = fetch_fails = 0
    wallets = set()
    coin_bias: dict[str, dict] = {}
    for t in traders:
        addr = t.get("ethAddress", "")
        try:
            st = _post("https://api.hyperliquid.xyz/info",
                       {"type": "clearinghouseState", "user": addr})
        except Exception:
            fetch_fails += 1
            continue
        for ap in st.get("assetPositions", []):
            p = ap.get("position", {})
            coin = p.get("coin", "")
            if coin not in COINS:
                continue
            try:
                szi = float(p.get("szi", 0))
                ntl = abs(float(p.get("positionValue", 0)))
            except (TypeError, ValueError):
                continue
            if ntl < 10_000 or szi == 0:
                continue
            n_pos += 1
            wallets.add(addr)
            cb = coin_bias.setdefault(coin, {"long_usd": 0.0, "short_usd": 0.0,
                                             "traders_long": 0, "traders_short": 0})
            if szi > 0:
                long_usd += ntl
                cb["long_usd"] += ntl
                cb["traders_long"] += 1
            else:
                short_usd += ntl
                cb["short_usd"] += ntl
                cb["traders_short"] += 1

    total = long_usd + short_usd
    if fetch_fails:
        print(f"HL leg: {fetch_fails}/{len(traders)} position fetches failed")
    if fetch_fails > len(traders) // 2:
        print("HL leg: majority of fetches failed — dropping leg (biased sample)")
        return None
    if total <= 0 or n_pos == 0:
        print("HL leg: no qualifying positions")
        return None
    for cb in coin_bias.values():
        s = cb["long_usd"] + cb["short_usd"]
        cb["net_long_ratio"] = round(cb["long_usd"] / s, 4) if s else None
        cb["long_usd"] = round(cb["long_usd"])
        cb["short_usd"] = round(cb["short_usd"])
    return {"source": "hyperliquid_top_traders", "asof": now_iso(),
            "risk_on": round(long_usd / total, 4),
            "signal_trades": n_pos, "distinct_wallets": len(wallets),
            "signal_usd": round(total), "long_usd": round(long_usd),
            "short_usd": round(short_usd), "coin_bias": coin_bias}


# ── LEG 2: Dune smart-money DEX flows ────────────────────────────────────
def leg_dune(prev: dict | None) -> dict | None:
    prev_leg = (prev or {}).get("legs", {}).get("dune")
    if prev_leg and age_h(prev_leg.get("asof", "")) < DUNE_TTL_H:
        return prev_leg                       # fresh enough — save the credits
    # circuit breaker: each execute attempt costs Dune credits, so after a
    # failure wait 3h before paying for another try
    if prev_leg and age_h(prev_leg.get("failed_at", "1970-01-01T00:00:00+00:00")) < 3:
        return prev_leg
    try:
        sys.path.insert(0, str(HOME / "whale_tracker"))
        import bootstrap
        env = bootstrap.load_env()
        feed = bootstrap.execute_and_fetch(env["DUNE_API_KEY"], int(env["DUNE_Q03_ID"]))
    except Exception as e:
        print(f"Dune leg failed: {e!r}")
        if prev_leg:
            prev_leg["failed_at"] = now_iso()
        return prev_leg                       # stale beats nothing; gate ages it out
    if not feed:
        print("Dune leg: 0 rows")
        return prev_leg
    buys = derisk = 0.0
    trades = 0
    wallets = set()
    for r in feed:
        bought, sold = r.get("bought_symbol", ""), r.get("sold_symbol", "")
        usd = r.get("amount_usd", 0) or 0
        if bought in STABLES and sold in STABLES:
            continue
        if bought in MAJORS and sold in MAJORS:
            continue
        trades += 1
        wallets.add(r.get("wallet", "?"))
        if bought in STABLES:
            derisk += usd
        else:
            buys += usd
    total = buys + derisk
    if total <= 0 or trades == 0:
        return prev_leg

    # token-flow breakdown: reuses the same rows fetched above (zero extra
    # Dune executions) — top 8 non-stablecoin bought_symbols by USD across
    # the 25 tracked whales. Independent of the trades/derisk classification
    # above, so e.g. a major-for-major buy (BTC→ETH) still counts toward
    # ETH's flow even though it's excluded from the risk_on trade tally.
    token_agg: dict[str, dict] = {}
    for r in feed:
        bought = r.get("bought_symbol", "")
        if not bought or bought in STABLES:
            continue
        usd = r.get("amount_usd", 0) or 0
        if usd <= 0:
            continue
        t = token_agg.setdefault(bought, {"buy_usd": 0.0, "buys": 0, "wallets": set()})
        t["buy_usd"] += usd
        t["buys"] += 1
        t["wallets"].add(r.get("wallet", "?"))
    token_flows = sorted(
        ({"symbol": sym, "buy_usd": round(t["buy_usd"]), "buys": t["buys"],
          "wallets": len(t["wallets"])} for sym, t in token_agg.items()),
        key=lambda x: x["buy_usd"], reverse=True)[:8]

    return {"source": "dune_smart_money_dex", "asof": now_iso(),
            "risk_on": round(buys / total, 4), "signal_trades": trades,
            "distinct_wallets": len(wallets), "signal_usd": round(total),
            "token_flows": token_flows}


# ── LEG 3: social/sentiment (mood only — never counts as whale evidence) ─
# Multi-sub sweep: the flagship sub plus the largest per-coin subs. Each is
# one keyless RSS call (see _reddit_hot_titles); polite 1s gap between subs.
SOCIAL_SUBS = ("CryptoCurrency", "Bitcoin", "ethereum", "solana", "SatoshiStreetBets")
_COIN_WORDS = {"BTC": ("btc", "bitcoin"), "ETH": ("eth", "ethereum"),
               "SOL": ("sol", "solana"), "XRP": ("xrp", "ripple"),
               "DOGE": ("doge", "dogecoin"), "ADA": ("ada", "cardano"),
               "LINK": ("link", "chainlink"), "AVAX": ("avax", "avalanche"),
               "LTC": ("ltc", "litecoin"), "DOT": ("dot", "polkadot")}
_BULL = ("buy", "bull", "pump", "ath", "moon", "rally", "breakout", "accumulat", "long")
_BEAR = ("sell", "bear", "crash", "dump", "capitulat", "liquidat", "short", "plunge", "tank")


def _get(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "quarki-sleeve/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "quarki-sleeve/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


def _reddit_hot_titles(sub: str, limit: int = 60) -> list[str]:
    # api.reddit.com/*.json and old.reddit.com/*.json 403 anonymous requests
    # (bot-detection on the JSON API surface, confirmed live 2026-08-09 —
    # both a UA-less and a spoofed-browser-UA request are blocked there).
    # The public RSS surface is NOT behind that wall: www.reddit.com/<sub>/
    # hot/.rss works keyless as long as the request carries *some* plain,
    # honest User-Agent (empty UA -> 429; a spoofed Mozilla UA -> 403; a
    # short custom string like "quarki-sleeve/1.0" -> 200). One retry
    # handles the shared-anonymous-IP rate limit occasionally hit on the
    # first call of a burst; a lone hourly launchd call rarely needs it.
    url = f"https://www.reddit.com/r/{sub}/hot/.rss?limit={limit}"
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            xml_text = _get_text(url)
            break
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 and attempt == 0:
                time.sleep(30)
                continue
            raise
    else:
        raise last_err  # pragma: no cover
    root = ET.fromstring(xml_text)
    titles = []
    for entry in root.findall("a:entry", _ATOM_NS):
        title_el = entry.find("a:title", _ATOM_NS)
        if title_el is not None and title_el.text:
            titles.append(title_el.text)
    return titles


def leg_social() -> dict | None:
    parts = {}
    # Fear & Greed (0=extreme fear, 100=extreme greed)
    try:
        fng = int(_get("https://api.alternative.me/fng/?limit=1")["data"][0]["value"])
        parts["fear_greed"] = fng
    except Exception as e:
        print(f"F&G failed: {e!r}")
    # Reddit hot across SOCIAL_SUBS — coin buzz + crude bull/bear keyword
    # tilt. Via RSS (see _reddit_hot_titles) since the JSON API 403s
    # anonymous traffic. RSS entries carry no ups/score field, so per-post
    # weight is approximated by rank in the hot-sorted feed (index 0 =
    # hottest) in place of the old ups-based weight. A sub that fails is
    # skipped, not fatal to the others.
    bull = bear = 0
    buzz: dict[str, int] = {}
    per_sub: dict[str, dict] = {}
    for si, sub in enumerate(SOCIAL_SUBS):
        try:
            titles = _reddit_hot_titles(sub, limit=60 if si == 0 else 40)
        except Exception as e:
            print(f"reddit r/{sub} failed: {e!r}")
            continue
        s_bull = s_bear = 0
        for i, raw_title in enumerate(titles):
            title = raw_title.lower()
            w = max(1, 6 - i // 10)          # rank-decay proxy for ups
            if any(k in title for k in _BULL):
                s_bull += w
            if any(k in title for k in _BEAR):
                s_bear += w
            for coin, words in _COIN_WORDS.items():
                if any(wd in title for wd in words):
                    buzz[coin] = buzz.get(coin, 0) + w
        bull += s_bull
        bear += s_bear
        per_sub[sub] = {"bull": s_bull, "bear": s_bear, "posts": len(titles)}
        if si < len(SOCIAL_SUBS) - 1:
            time.sleep(10)   # anonymous RSS rate limit trips at shorter gaps
    if per_sub:
        parts["reddit_bull"] = bull
        parts["reddit_bear"] = bear
        parts["reddit_buzz"] = dict(sorted(buzz.items(), key=lambda kv: -kv[1])[:6])
        parts["reddit_subs"] = per_sub
    # CoinGecko trending — what retail is looking at (display only)
    try:
        tr = _get("https://api.coingecko.com/api/v3/search/trending")
        parts["trending"] = [c["item"]["symbol"].upper()
                             for c in tr.get("coins", [])[:7]]
    except Exception as e:
        print(f"coingecko failed: {e!r}")

    scores = []
    if "fear_greed" in parts:
        scores.append(parts["fear_greed"] / 100)
    if parts.get("reddit_bull", 0) + parts.get("reddit_bear", 0) > 0:
        scores.append(parts["reddit_bull"] / (parts["reddit_bull"] + parts["reddit_bear"]))
    if not scores:
        return None
    return {"source": "social_sentiment", "asof": now_iso(),
            "risk_on": round(sum(scores) / len(scores), 4),
            # sentiment is mood, not money: contributes to risk_on but adds
            # ZERO to the gate's evidence counters
            "signal_trades": 0, "distinct_wallets": 0, "signal_usd": 0,
            "detail": parts}


# ── LEG: StockTwits sentiment (mood only — never counts as whale evidence) ─
# Verified live 2026-08-09: keyless public streams, entities.sentiment.basic
# is "Bullish"/"Bearish" (absent on untagged posts). ~30 msgs/symbol/page.
def leg_stocktwits() -> dict | None:
    total_bull = total_bear = 0
    per_coin: dict[str, dict] = {}
    for i, (coin, sym) in enumerate(_ST_SYMBOLS.items()):
        try:
            data = _get(f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json")
            bull = bear = 0
            for m in data.get("messages", []) or []:
                basic = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
                if basic == "Bullish":
                    bull += 1
                elif basic == "Bearish":
                    bear += 1
            per_coin[coin] = {"bullish": bull, "bearish": bear}
            total_bull += bull
            total_bear += bear
        except Exception as e:
            print(f"stocktwits {coin} failed: {e!r}")
        if i < len(_ST_SYMBOLS) - 1:
            time.sleep(0.5)
    total = total_bull + total_bear
    if total <= 0:
        print("stocktwits leg: no sentiment-tagged messages")
        return None
    return {"source": "stocktwits_sentiment", "asof": now_iso(),
            "risk_on": round(total_bull / total, 4),
            "signal_trades": 0, "distinct_wallets": 0, "signal_usd": 0,
            "detail": {"bullish": total_bull, "bearish": total_bear,
                       "per_coin": per_coin}}


# ── LEG: derivatives positioning (display only — zero evidence weight) ───
# Hyperliquid metaAndAssetCtxs: funding rate, open interest, 24h volume and
# price change for every perp, keyless, one POST. Positive funding = longs
# paying shorts = crowded-long. risk_on = OI-weighted share of coins with
# positive funding; signal_* stay 0 so leg_weight() is 0 and the gate
# composite is untouched — this leg exists for the intel card.
def leg_derivatives() -> dict | None:
    try:
        meta, ctxs = _post("https://api.hyperliquid.xyz/info",
                           {"type": "metaAndAssetCtxs"})
    except Exception as e:
        print(f"derivatives leg failed: {e!r}")
        return None
    per_coin: dict[str, dict] = {}
    oi_pos = oi_tot = 0.0
    for asset, ctx in zip(meta.get("universe", []), ctxs):
        coin = asset.get("name", "")
        if coin not in COINS:
            continue
        try:
            mark = float(ctx.get("markPx", 0) or 0)
            prev = float(ctx.get("prevDayPx", 0) or 0)
            funding = float(ctx.get("funding", 0) or 0)      # hourly rate
            oi_usd = float(ctx.get("openInterest", 0) or 0) * mark
            vol24 = float(ctx.get("dayNtlVlm", 0) or 0)
        except (TypeError, ValueError):
            continue
        if mark <= 0:
            continue
        per_coin[coin] = {
            "funding_8h_pct": round(funding * 8 * 100, 5),
            "funding_apr_pct": round(funding * 24 * 365 * 100, 2),
            "oi_usd": round(oi_usd),
            "vol24_usd": round(vol24),
            "chg24_pct": round((mark / prev - 1) * 100, 2) if prev > 0 else None,
            "mark_px": mark,
        }
        oi_tot += oi_usd
        if funding > 0:
            oi_pos += oi_usd
    if not per_coin or oi_tot <= 0:
        print("derivatives leg: no qualifying coins")
        return None
    return {"source": "hyperliquid_derivatives", "asof": now_iso(),
            "risk_on": round(oi_pos / oi_tot, 4),
            "signal_trades": 0, "distinct_wallets": 0, "signal_usd": 0,
            "detail": {"per_coin": per_coin, "oi_total_usd": round(oi_tot)}}


# ── LEG: market breadth (display only — zero evidence weight) ────────────
# CoinGecko /global: total market cap 24h change, BTC/ETH dominance, total
# volume. One keyless call. risk_on = mcap change squashed to [0,1] purely
# for display consistency; signal_* stay 0 → leg_weight 0 → gate untouched.
def leg_market() -> dict | None:
    try:
        # keyless CoinGecko rate-limits per IP and leg_social's trending call
        # runs shortly before this one — retry once after a cooldown
        try:
            g = _get("https://api.coingecko.com/api/v3/global")["data"]
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(20)
            g = _get("https://api.coingecko.com/api/v3/global")["data"]
        chg = float(g["market_cap_change_percentage_24h_usd"])
        dom = g.get("market_cap_percentage", {}) or {}
        detail = {
            "mcap_chg24_pct": round(chg, 2),
            "total_mcap_usd": round(g["total_market_cap"]["usd"]),
            "total_vol24_usd": round(g["total_volume"]["usd"]),
            "btc_dominance_pct": round(float(dom.get("btc", 0)), 1),
            "eth_dominance_pct": round(float(dom.get("eth", 0)), 1),
        }
    except Exception as e:
        print(f"market leg failed: {e!r}")
        return None
    return {"source": "coingecko_global", "asof": now_iso(),
            "risk_on": round(max(0.0, min(1.0, 0.5 + chg / 10)), 4),
            "signal_trades": 0, "distinct_wallets": 0, "signal_usd": 0,
            "detail": detail}


# ── composite ────────────────────────────────────────────────────────────
def main() -> None:
    prev = None
    try:
        prev = json.loads(OUT.read_text())
    except Exception:
        pass

    legs = {}
    hl = leg_hyperliquid()
    if hl:
        legs["hyperliquid"] = hl
    du = leg_dune(prev)
    if du:
        legs["dune"] = du
    so = leg_social()
    if so:
        legs["social"] = so
    stw = leg_stocktwits()
    if stw:
        legs["stocktwits"] = stw
    dv = leg_derivatives()
    if dv:
        legs["derivatives"] = dv
    mk = leg_market()
    if mk:
        legs["market"] = mk

    fresh = [l for l in legs.values() if age_h(l.get("asof", "")) < 24]
    money = [l for l in fresh if l.get("signal_usd", 0) > 0]   # whale evidence
    if not fresh:
        print("no live legs — leaving whale_signals.json untouched")
        return

    # EVIDENCE-WEIGHTED composite (revised after adversarial review): a leg's
    # vote counts in proportion to how much of the gate's own evidence floors
    # it independently carries. A single $17k Dune trade or a fear-index mood
    # must never veto $500M of actual top-trader positioning (that exact
    # failure blocked the gate on 2026-08-09: HL 0.63-long diluted to a 0.31
    # composite by two near-zero-evidence legs). Sentiment gets a fixed small
    # nudge weight and still contributes zero evidence counters.
    def leg_weight(l):
        if l.get("source") in ("social_sentiment", "stocktwits_sentiment"):
            return 0.15
        return (min(1.0, l.get("signal_usd", 0) / 50_000)
                * min(1.0, l.get("distinct_wallets", 0) / 3)
                * min(1.0, l.get("signal_trades", 0) / 5))

    wsum = sum(leg_weight(l) for l in fresh)
    if wsum <= 0:
        print("all legs zero-weight — leaving whale_signals.json untouched")
        return
    risk_on = sum(leg_weight(l) * l["risk_on"] for l in fresh) / wsum

    out = {"asof": now_iso(),
           "risk_on": round(risk_on, 4),
           "leg_weights": {k: round(leg_weight(l), 4) for k, l in legs.items()},
           "signal_trades": sum(l["signal_trades"] for l in money),
           "distinct_wallets": sum(l["distinct_wallets"] for l in money),
           "signal_usd": sum(l["signal_usd"] for l in money),
           "rows": sum(l["signal_trades"] for l in money),   # legacy gate field
           "legs": legs}
    if hl and hl.get("coin_bias"):
        out["coin_bias"] = hl["coin_bias"]
    if du and du.get("token_flows"):
        out["token_flows"] = du["token_flows"]
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=2))
    tmp.replace(OUT)
    print(f"{out['asof']} risk_on={out['risk_on']} legs={list(legs)} "
          f"n={out['signal_trades']} wallets={out['distinct_wallets']} "
          f"vol=${out['signal_usd']:,}")


if __name__ == "__main__":
    main()
