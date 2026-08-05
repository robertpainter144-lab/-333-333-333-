"""QuantConnect public leaderboard API client."""
import re
import requests
from config import QC_LIST_URL, QC_DETAIL_URL, QC_TOP_N, STRATEGY_CONFIGS

_COMMON_WORDS = {
    "ETF","THE","FOR","AND","OR","IN","ON","OF","US","BE","BY","AT","IS","IT",
    "TO","AS","AN","IF","SO","DO","NO","UP","MA","RSI","ADX","EMA","SMA","ATR",
    "ROC","OOS","USA","API","CEO","CFO","CTO","IPO","ETH","BTC","USD","EUR",
    "GBP","JPY","CAD","AUD","CHF","NZD","HKD","SGD","SEK","NOK","DKK","PLN",
}

def fetch_top_strategies(n=QC_TOP_N):
    """
    Fetch and return the top-N QC strategies sorted by OOS 3M return.
    Each item is the raw strategy dict from the API.
    """
    try:
        resp = requests.get(QC_LIST_URL, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        strategies = data.get("strategies", [])
        valid = [s for s in strategies
                 if s.get("statistics", {}).get("oos 3m return") not in (None, 0)
                 and "Option" not in s.get("assetClasses", [])]
        sorted_s = sorted(valid,
                          key=lambda s: s["statistics"]["oos 3m return"],
                          reverse=True)
        return sorted_s[:n]
    except Exception as e:
        print(f"[QC] fetch error: {e}")
        return []

def fetch_strategy_detail(strategy_id):
    """Get full strategy dict for a single ID."""
    try:
        resp = requests.get(QC_DETAIL_URL,
                            params={"strategyId": strategy_id},
                            timeout=15)
        resp.raise_for_status()
        return resp.json().get("strategy", {})
    except Exception as e:
        print(f"[QC] detail error id={strategy_id}: {e}")
        return {}

def extract_tickers(description):
    """Parse ticker symbols mentioned in a strategy description."""
    if not description:
        return []
    candidates = re.findall(r"\b([A-Z]{2,5}[0-9]?)\b", description)
    seen, result = set(), []
    for t in candidates:
        if t not in _COMMON_WORDS and t not in seen:
            seen.add(t)
            result.append(t)
    return result[:12]

def build_strategy_config(strategy):
    """Return a config dict for a strategy (uses known config or auto-detects)."""
    sid = strategy["id"]
    if sid in STRATEGY_CONFIGS:
        cfg = dict(STRATEGY_CONFIGS[sid])
        cfg["id"] = sid
        cfg["statistics"] = strategy.get("statistics", {})
        return cfg

    from config import LEVERAGED_ETF_UNIVERSE, LARGE_CAP_UNIVERSE
    desc = strategy.get("description", "")
    tags = strategy.get("tags", [])
    tags_str = " ".join(tags).lower()

    if "leveraged" in tags_str or "3x" in desc.lower():
        universe = LEVERAGED_ETF_UNIVERSE
    else:
        tickers = extract_tickers(desc)
        universe = tickers if len(tickers) >= 3 else LARGE_CAP_UNIVERSE

    alloc = "top3_equal" if "long/short" in tags_str or "long-short" in tags_str else "single_winner"

    return {
        "id": sid,
        "name": strategy.get("name", f"Strategy {sid}"),
        "universe": universe,
        "alloc": alloc,
        "statistics": strategy.get("statistics", {}),
    }

def format_stats(stats):
    """Return a human-readable summary dict from raw statistics."""
    return {
        "oos_3m_return": stats.get("oos 3m return", 0),
        "oos_3m_sharpe": stats.get("oos 3m sharpe", 0),
        "oos_1y_return": stats.get("oos 1y return", 0),
        "oos_1y_sharpe": stats.get("oos 1y sharpe", 0),
        "5y_cagr":       stats.get("5y cagr", 0),
        "5y_drawdown":   stats.get("5y drawdown", 0),
        "1y_sharpe":     stats.get("1y sharpe", 0),
    }
