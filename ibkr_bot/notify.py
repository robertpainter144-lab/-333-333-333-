"""
ntfy.sh notification client (replaced Pushover 2026-07-28 — trial expired).

Reads NTFY_TOPIC from env (or ~/ntfy.env if present). The topic name is the
only secret — treat it like a password.
Silent no-op if the topic is missing so the bot keeps working in dev mode.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

KEYFILE = Path.home() / "ntfy.env"

AUTO_FIRE_USD_CAP = 20.0   # Aggregate budget: bot may fire while TOTAL IBKR exposure ≤ this


def _load_topic() -> str:
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic and KEYFILE.exists():
        for line in KEYFILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            if k == "NTFY_TOPIC" and not topic:
                topic = v.strip().strip('"\'')
    return topic


def push(title: str, message: str, priority: int = 0, url: str = "") -> bool:
    """Returns True on success, False on missing topic or transport failure.

    priority keeps the old Pushover scale (-2..2) and maps to ntfy 1..5.
    """
    topic = _load_topic()
    if not topic:
        return False
    headers = {
        # ntfy headers must be latin-1 safe; message body is UTF-8 and unrestricted
        "Title":    title[:250].encode("ascii", "replace").decode(),
        "Priority": str(min(5, max(1, priority + 3))),
    }
    if url:
        headers["Click"] = url
    try:
        r = requests.post(f"https://ntfy.sh/{topic}",
                          data=message[:4000].encode("utf-8"),
                          headers=headers, timeout=8)
        return r.ok
    except Exception:
        return False


def needs_approval(projected_total_usd: float) -> bool:
    """projected_total_usd = sum of all open IBKR exposure (positions + pending + new trade)."""
    return projected_total_usd > AUTO_FIRE_USD_CAP


def propose_trade(symbol: str, side: str, qty: float, est_price: float,
                  projected_total_usd: float, reason: str = "") -> bool:
    """If projected_total > AUTO_FIRE_USD_CAP, send an ntfy approval proposal.

    Returns True ONLY if the notification was actually delivered. A False return
    when approval is required means the operator was NOT reached — callers must
    still block the trade (gate on needs_approval, not on this return value).
    """
    notional = qty * est_price
    if not needs_approval(projected_total_usd):
        return False
    return push(
        title    = f"Trade approval needed: {side} {qty} {symbol}",
        message  = (f"{side} {qty} {symbol} @ ~${est_price:.2f}  notional ${notional:.2f}\n"
                    f"Projected total IBKR exposure: ${projected_total_usd:.2f} "
                    f"(ceiling ${AUTO_FIRE_USD_CAP:.2f})\n"
                    f"{reason}\nReply 'approve {symbol}' in chat to fire."),
        priority = 1,
    )


# ── Per-event notifications (always-on for now) ───────────────────────

def notify_order_placed(symbol: str, side: str, qty: float, limit_price: float,
                        order_id: int, total_exposure_after: float) -> bool:
    return push(
        title   = f"Order placed: {side} {qty} {symbol}",
        message = (f"id={order_id}  {side} {qty} {symbol} @ ${limit_price:.2f} LMT GTC\n"
                   f"Total IBKR exposure now: ${total_exposure_after:.2f} / ${AUTO_FIRE_USD_CAP:.2f}"),
        priority = 0,
    )


def notify_fill(symbol: str, side: str, qty: float, fill_price: float,
                pnl: float | None = None) -> bool:
    msg = f"{side} {qty} {symbol} @ ${fill_price:.4f}"
    if pnl is not None:
        msg += f"\nP&L on this position: ${pnl:+.2f}"
    return push(
        title   = f"Fill: {side} {qty} {symbol} @ ${fill_price:.2f}",
        message = msg,
        priority = 0,
    )


def notify_stop_attached(symbol: str, trail_pct: float, hard_stop: float,
                         entry_price: float) -> bool:
    return push(
        title   = f"Stops attached: {symbol}",
        message = (f"{symbol} entry ${entry_price:.2f}\n"
                   f"trail {trail_pct*100:.2f}%  hard stop ${hard_stop:.2f}"),
        priority = 0,
    )


def notify_cancelled(symbol: str, order_id: int, reason: str = "") -> bool:
    msg = f"id={order_id}  {symbol}"
    if reason:
        msg += f"\n{reason}"
    return push(
        title   = f"Order cancelled: {symbol}",
        message = msg,
        priority = -1,
    )


if __name__ == "__main__":
    # Smoke test
    import sys
    topic = _load_topic()
    print(f"topic loaded: {'***' if topic else 'MISSING'}")
    if len(sys.argv) > 1 and sys.argv[1] == "ping":
        ok = push("trading-bot test", "If you see this, ntfy works ✓", priority=0)
        print(f"push result: {'sent' if ok else 'FAILED (check ~/ntfy.env)'}")
