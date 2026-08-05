"""Order safety rails.

The orders endpoint is the only path that can move real money out of the
family member's IBKR account. Every call must pass through `preflight`,
which enforces:

  * No market orders (unbounded notional risk).
  * Per-order notional ≤ `per_order_max_notional_usd`.
  * Cumulative notional today ≤ `daily_notional_cap_usd`.
  * Trading mode is "live" — "paper" rejects forwarding to ibeam.

Every preflight call also appends a JSONL audit record under
`data_path/orders-YYYY-MM-DD.jsonl` whether the order was accepted or
rejected, so we can review what was attempted.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status

from .config import settings

# The daily notional cap is a per-TRADING-DAY limit, so the ledger day must roll
# over on the US market calendar, not at UTC midnight. Keying it to UTC reset the
# cap at ~19:00-20:00 ET — mid-session — letting an order spend up to 2x the cap
# in a single US trading day.
_MARKET_TZ = ZoneInfo("America/New_York")


def _trading_day(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.astimezone(_MARKET_TZ).strftime("%Y-%m-%d")


def _log_path(now: datetime | None = None) -> Path:
    settings.data_path.mkdir(parents=True, exist_ok=True)
    return settings.data_path / f"orders-{_trading_day(now)}.jsonl"


def _append(record: dict[str, Any]) -> None:
    path = _log_path()
    with path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def _todays_accepted_notional() -> float:
    path = _log_path()
    if not path.exists():
        return 0.0
    total = 0.0
    with path.open() as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("outcome") == "accepted":
                total += float(rec.get("notional_usd") or 0.0)
    return total


def preflight(order: dict[str, Any]) -> float:
    """Validate an order against the rails. Raise HTTPException on rejection.

    Returns the computed notional for caller bookkeeping.
    """
    now = datetime.now(timezone.utc)
    base_record = {
        "ts": now.isoformat(),
        "order": order,
        "mode": settings.trading_mode,
    }

    def reject(http_status: int, reason: str) -> HTTPException:
        _append({**base_record, "outcome": "rejected", "reason": reason})
        return HTTPException(http_status, reason)

    order_type = order.get("order_type") or order.get("orderType")
    if order_type == "MKT" and settings.block_market_orders:
        raise reject(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "market orders disabled; submit as LMT with a price",
        )

    limit_price = order.get("limit_price") or order.get("price")
    quantity = order.get("quantity")
    if limit_price is None or quantity is None:
        raise reject(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "limit_price + quantity required to enforce notional cap",
        )

    # Must be positive, finite numbers. A negative or zero price/quantity yields a
    # non-positive notional that slips under both caps — and a negative accepted
    # notional would even inflate the remaining daily headroom for later orders.
    try:
        limit_price = float(limit_price)
        quantity = float(quantity)
    except (TypeError, ValueError):
        raise reject(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "limit_price and quantity must be numbers",
        )
    if not (math.isfinite(limit_price) and math.isfinite(quantity)) \
            or limit_price <= 0 or quantity <= 0:
        raise reject(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "limit_price and quantity must be positive",
        )

    notional = limit_price * quantity
    if notional > settings.per_order_max_notional_usd:
        raise reject(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"order notional ${notional:.2f} exceeds per-order cap "
            f"${settings.per_order_max_notional_usd:.2f}",
        )

    used = _todays_accepted_notional()
    if used + notional > settings.daily_notional_cap_usd:
        raise reject(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"daily notional cap ${settings.daily_notional_cap_usd:.2f} would be "
            f"exceeded (${used:.2f} already used, this order ${notional:.2f})",
        )

    _append({**base_record, "outcome": "accepted", "notional_usd": notional})
    return notional


def limits_snapshot() -> dict[str, Any]:
    return {
        "trading_mode": settings.trading_mode,
        "block_market_orders": settings.block_market_orders,
        "per_order_max_notional_usd": settings.per_order_max_notional_usd,
        "daily_notional_cap_usd": settings.daily_notional_cap_usd,
        "used_notional_today_usd": round(_todays_accepted_notional(), 2),
    }
