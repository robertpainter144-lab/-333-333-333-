"""
Append-only daily track-record logger shared by mock_portfolio and qc_portfolio.
One JSON line per (date, strategy) written to track_record.jsonl.
fcntl.flock ensures concurrent-daemon-safe atomic appends.
"""
import fcntl
import json
from datetime import date as _date
from pathlib import Path

TRACK_RECORD = Path("/Users/quarki3/track_record.jsonl")


def append_daily_record(
    strategy: str,
    equity: float,
    day_pnl: float,
    num_trades: int,
    *,
    date_str: str | None = None,
    fills: list | None = None,
) -> None:
    """
    Atomically append one record to track_record.jsonl.

    Args:
        strategy:    Bot name, e.g. "mock_portfolio" or "qc_portfolio"
        equity:      End-of-day total portfolio equity (dollars)
        day_pnl:     Day P&L (closed trades for mock_portfolio;
                     equity delta since market open for qc_portfolio)
        num_trades:  Number of trades/orders executed today
        date_str:    ISO date "YYYY-MM-DD" (default: today in local time)
        fills:       Optional list of per-fill dicts with at minimum
                     {"symbol": ..., "actual_fill_price": ...}
                     and optionally "intended_price" when a pre-trade
                     reference price is available (slippage = actual - intended)
    """
    record: dict = {
        "date":       date_str or str(_date.today()),
        "strategy":   strategy,
        "equity":     round(float(equity), 2),
        "day_pnl":    round(float(day_pnl), 2),
        "num_trades": int(num_trades),
    }
    if fills:
        record["fills"] = fills

    line = json.dumps(record, separators=(",", ":")) + "\n"
    TRACK_RECORD.parent.mkdir(parents=True, exist_ok=True)

    with open(TRACK_RECORD, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
