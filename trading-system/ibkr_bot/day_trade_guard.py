"""
Day-trade safeguard.

IBKR account U24971994 is below PDT threshold; same-day buy+sell of any symbol
counts as a day trade. With only 1 day trade left in the rolling 5-business-day
window, ANY same-day sell could trigger the 30-day closing-only lockout.

This module exposes a single check: `has_buy_today(ib, symbol)`.

Callers (watcher, manual scripts, any future automation) MUST gate every sell-
or stop-attach action through this check. If True, the action is refused.

Fail-safe: if execution times can't be parsed for any reason, defaults to True
(refuse to sell). False is only returned when we positively confirm no BUY today.
"""
from __future__ import annotations

from datetime import datetime
import pytz

US_EASTERN = pytz.timezone("America/New_York")

DAILY_DAY_TRADE_CAP = 1   # hard ceiling: at most 1 round-trip per ET day, ever


def _today_et_date() -> str:
    return datetime.now(US_EASTERN).strftime("%Y-%m-%d")


def count_round_trips_today(ib) -> int:
    """Number of symbols with BOTH a BOT and SLD execution today (ET). Each
    completed round trip counts as one day-trade. Fail-safe: on any error,
    returns DAILY_DAY_TRADE_CAP so callers treat the cap as already hit."""
    today = _today_et_date()
    try:
        fills = ib.fills()
    except Exception:
        return DAILY_DAY_TRADE_CAP
    bought_today: set[str] = set()
    sold_today:   set[str] = set()
    for f in fills:
        try:
            t = f.execution.time
            if t is None:
                return DAILY_DAY_TRADE_CAP
            if t.astimezone(US_EASTERN).strftime("%Y-%m-%d") != today:
                continue
            sym = f.contract.symbol
            if f.execution.side == "BOT":
                bought_today.add(sym)
            elif f.execution.side == "SLD":
                sold_today.add(sym)
        except Exception:
            return DAILY_DAY_TRADE_CAP
    return len(bought_today & sold_today)


def under_daily_cap(ib) -> bool:
    return count_round_trips_today(ib) < DAILY_DAY_TRADE_CAP


def has_buy_today(ib, symbol: str) -> bool:
    """Returns True if there's any BUY execution for `symbol` whose execution
    timestamp falls on today's US Eastern date."""
    today = _today_et_date()
    try:
        fills = ib.fills()
    except Exception:
        return True  # Can't verify → assume same-day → refuse to sell
    for f in fills:
        try:
            if f.contract.symbol != symbol:
                continue
            if f.execution.side != "BOT":  # BOT = bought
                continue
            t = f.execution.time
            if t is None:
                return True
            et_date = t.astimezone(US_EASTERN).strftime("%Y-%m-%d")
            if et_date == today:
                return True
        except Exception:
            return True  # Defensive: malformed fill → refuse
    return False


def safe_to_sell(ib, symbol: str) -> tuple[bool, str]:
    """Convenience wrapper. Returns (ok, reason).

    Two gates, both must pass:
      1) Per-symbol: refuse if `symbol` was bought today.
      2) Global cap: refuse if today's round-trip count is already at the cap.
         This is a defense against a 2nd unintended day-trade after the 1st has
         happened (e.g., a server-side stop fired on a same-day buy)."""
    if has_buy_today(ib, symbol):
        return False, f"day_trade_guard: {symbol} has BUY today → cannot sell same-day"
    if not under_daily_cap(ib):
        return False, (f"day_trade_guard: daily cap of {DAILY_DAY_TRADE_CAP} round-trip(s) "
                       f"already reached today → freezing all sells")
    return True, ""


def assert_safe_to_sell(ib, symbol: str) -> None:
    """Raise if not safe."""
    ok, reason = safe_to_sell(ib, symbol)
    if not ok:
        raise DayTradeBlocked(reason)


class DayTradeBlocked(Exception):
    pass
