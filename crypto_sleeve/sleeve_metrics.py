#!/usr/bin/env python3
"""Weekly performance report for the LIVE crypto sleeve (read-only).

Reads state/closed_trades.csv, state/equity.csv and state/transfers.json,
computes honest trade + risk-adjusted-return stats over two windows
(trailing 7 days, and "since the churn fix" starting 2026-08-25T19:00
America/Denver == 2026-08-26T01:00 UTC, when Robert reverted momentum_flip
to its pre-churn 8/20 behavior), writes state/metrics_weekly.json, appends
one line to logs/metrics.log, and pushes a plain-English summary via
quarki_notify.py. Never trades, never writes to any state file the live
engine reads. Deployed as a weekly systemd oneshot, Mon 13:00 UTC.

Bookkeeping rows (reason starting with "reconciled" or "milestone") are
excluded from trade stats -- they're ledger housekeeping, not trades.

Daily returns are transfer-adjusted: each UTC calendar day's equity is the
last equity.csv row seen that day; daily_pl = equity(day) - equity(prev
known day) - net transfers that day; daily_ret = daily_pl / prev equity.
"Prev known day" is the previous day present in the series, not
necessarily the calendar day before (equity.csv is 5-min data so gaps
should be rare, but the fallback is defensive). Sharpe/Sortino annualize
with sqrt(365); Sortino and profit factor report null (never inf) when
there's no loss/downside data to divide by. Max drawdown is computed on
the compounded transfer-adjusted daily-return curve.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
TRADES_F = STATE_DIR / "closed_trades.csv"
EQUITY_F = STATE_DIR / "equity.csv"
TRANSFERS_F = STATE_DIR / "transfers.json"
METRICS_F = STATE_DIR / "metrics_weekly.json"
LOG_F = ROOT / "logs" / "metrics.log"
NOTIFIER = Path.home() / "bin" / "quarki_notify.py"

# 2026-08-25 19:00 America/Denver (MDT, UTC-6) == 2026-08-26 01:00 UTC.
CHURN_FIX_START = datetime.fromisoformat("2026-08-25T19:00:00-06:00")

BOOKKEEPING_PREFIXES = ("reconciled", "milestone")


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line)
    try:
        LOG_F.parent.mkdir(parents=True, exist_ok=True)
        with LOG_F.open("a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def notify(level: str, title: str, msg: str) -> None:
    try:
        subprocess.run(["python3", str(NOTIFIER), level, title, msg],
                        timeout=30, capture_output=True, check=False)
    except Exception as e:
        log(f"notify FAIL {e!r}")


def fmt_pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def fmt_num(x) -> str:
    return "n/a" if x is None else f"{x:.2f}"


# --------------------------------------------------------------------------
# Loaders -- every one is defensive: missing file, empty file, or malformed
# rows all degrade to "no data" instead of raising.
# --------------------------------------------------------------------------

def load_closed_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                reason = (row.get("reason") or "").strip().lower()
                if reason.startswith(BOOKKEEPING_PREFIXES):
                    continue
                try:
                    closed_at = datetime.fromisoformat(row["closed_at"])
                except Exception:
                    continue
                if closed_at.tzinfo is None:
                    closed_at = closed_at.replace(tzinfo=timezone.utc)
                try:
                    pl = float(row.get("realized_pl") or 0.0)
                except (TypeError, ValueError):
                    continue
                out.append({"closed_at": closed_at, "pl": pl,
                            "symbol": row.get("symbol"),
                            "reason": row.get("reason")})
    except Exception as e:
        log(f"closed_trades read FAIL {e!r}")
        return []
    return out


def load_equity(path: Path) -> list[tuple[datetime, float]]:
    if not path.exists():
        return []
    out = []
    try:
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                try:
                    ts = datetime.fromisoformat(row["ts"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    eq = float(row["equity"])
                except (KeyError, TypeError, ValueError):
                    continue
                out.append((ts, eq))
    except Exception as e:
        log(f"equity read FAIL {e!r}")
        return []
    out.sort(key=lambda r: r[0])
    return out


def load_transfers(path: Path) -> list[tuple[datetime, float]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        log(f"transfers read FAIL {e!r}")
        return []
    entries = data if isinstance(data, list) else (data.get("transfers") or [])
    out = []
    for e in entries:
        try:
            ts = datetime.fromtimestamp(float(e["ts"]), tz=timezone.utc)
            usd = float(e["usd"])
        except Exception:
            continue
        out.append((ts, usd))
    return out


# --------------------------------------------------------------------------
# Daily bucketing + transfer-adjusted returns
# --------------------------------------------------------------------------

def daily_equity_series(rows: list[tuple[datetime, float]]) -> dict:
    """Last equity value seen per UTC calendar day."""
    by_day: dict = {}
    for ts, eq in rows:
        d = ts.date()
        if d not in by_day or ts >= by_day[d][0]:
            by_day[d] = (ts, eq)
    return {d: eq for d, (ts, eq) in sorted(by_day.items())}


def transfers_by_day(rows: list[tuple[datetime, float]]) -> dict:
    by_day: dict = {}
    for ts, usd in rows:
        d = ts.date()
        by_day[d] = by_day.get(d, 0.0) + usd
    return by_day


def compute_daily_returns(eq_by_day: dict, tr_by_day: dict) -> list[dict]:
    days = sorted(eq_by_day.keys())
    out = []
    for i in range(1, len(days)):
        d_prev, d = days[i - 1], days[i]
        prev_eq, eq = eq_by_day[d_prev], eq_by_day[d]
        if not prev_eq:
            continue
        net_transfer = tr_by_day.get(d, 0.0)
        daily_pl = eq - prev_eq - net_transfer
        daily_ret = daily_pl / prev_eq
        out.append({"date": d, "ret": daily_ret, "pl": daily_pl})
    return out


def filter_daily(rows: list[dict], start: datetime, end: datetime) -> list[dict]:
    # .date() reads the naive calendar fields of a datetime, ignoring
    # tzinfo -- CHURN_FIX_START carries a -06:00 offset, so it must be
    # normalized to UTC first or this silently pulls in the wrong day.
    start_d = start.astimezone(timezone.utc).date()
    end_d = end.astimezone(timezone.utc).date()
    return [r for r in rows if start_d <= r["date"] <= end_d]


# --------------------------------------------------------------------------
# Stats
# --------------------------------------------------------------------------

def trade_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_win_usd": None,
                "avg_loss_usd": None, "profit_factor": None,
                "largest_loss_usd": None}
    wins = [t["pl"] for t in trades if t["pl"] > 0]
    losses = [t["pl"] for t in trades if t["pl"] < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    return {
        "n": n,
        "win_rate": round(len(wins) / n, 4),
        "avg_win_usd": round(statistics.mean(wins), 4) if wins else None,
        "avg_loss_usd": round(statistics.mean(losses), 4) if losses else None,
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "largest_loss_usd": round(min(losses), 4) if losses else None,
    }


def return_stats(daily_rows: list[dict]) -> dict:
    n = len(daily_rows)
    if n < 2:
        return {"n_days": n, "sharpe": None, "sortino": None,
                "max_drawdown_pct": None, "best_day": None, "worst_day": None,
                "mean_daily_ret_pct": None, "note": "not enough data yet"}

    rets = [r["ret"] for r in daily_rows]
    mean_r = statistics.mean(rets)

    try:
        std_r = statistics.stdev(rets)
    except statistics.StatisticsError:
        std_r = 0.0
    sharpe = (mean_r / std_r * math.sqrt(365)) if std_r > 0 else None

    downside = [r for r in rets if r < 0]
    sortino = None
    if len(downside) >= 2:
        down_std = statistics.stdev(downside)
        if down_std > 0:
            sortino = mean_r / down_std * math.sqrt(365)
    # len(downside) in (0, 1): not enough spread to define a downside std
    # -> sortino stays None (never inf), per spec.

    cum = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in rets:
        cum *= (1 + r)
        peak = max(peak, cum)
        max_dd = min(max_dd, cum / peak - 1.0)

    best = max(daily_rows, key=lambda r: r["ret"])
    worst = min(daily_rows, key=lambda r: r["ret"])

    return {
        "n_days": n,
        "sharpe": round(sharpe, 3) if sharpe is not None else None,
        "sortino": round(sortino, 3) if sortino is not None else None,
        "max_drawdown_pct": round(max_dd * 100, 3),
        "best_day": {"date": best["date"].isoformat(), "ret_pct": round(best["ret"] * 100, 3)},
        "worst_day": {"date": worst["date"].isoformat(), "ret_pct": round(worst["ret"] * 100, 3)},
        "mean_daily_ret_pct": round(mean_r * 100, 4),
    }


# --------------------------------------------------------------------------
# Human summary
# --------------------------------------------------------------------------

def build_message(windows_out: dict) -> str:
    w7, wc = windows_out["trailing_7d"], windows_out["since_churn_fix"]
    t7, r7 = w7["trades"], w7["returns"]
    tc, rc = wc["trades"], wc["returns"]

    if t7["n"] == 0:
        line1 = "The crypto sleeve had no closed trades in the last 7 days."
    else:
        avg_win = t7["avg_win_usd"] or 0.0
        avg_loss = abs(t7["avg_loss_usd"] or 0.0)
        line1 = (f"Over the last 7 days the sleeve closed {t7['n']} trades with a "
                  f"{fmt_pct(t7['win_rate'])} win rate; winners averaged ${avg_win:.2f} "
                  f"and losers averaged ${avg_loss:.2f}, for a profit factor of "
                  f"{fmt_num(t7['profit_factor'])} (above 1 means winners outweigh losers).")

    if r7["n_days"] < 2:
        line2 = "There isn't enough daily equity history yet to compute risk-adjusted return or drawdown for the week."
    else:
        line2 = (f"Risk-adjusted return (Sharpe) was {fmt_num(r7['sharpe'])} and the worst "
                  f"pullback from a peak (max drawdown) was {fmt_num(r7['max_drawdown_pct'])}%; "
                  f"the best day was {fmt_num(r7['best_day']['ret_pct'])}% and the worst day "
                  f"was {fmt_num(r7['worst_day']['ret_pct'])}%.")

    if tc["n"] == 0:
        line3 = "Since Tuesday's churn fix there have been no closed trades yet."
    else:
        sharpe_bit = f", Sharpe {fmt_num(rc['sharpe'])}" if rc["n_days"] >= 2 else ""
        line3 = (f"Since Tuesday's churn fix, {tc['n']} trades have closed with a "
                  f"{fmt_pct(tc['win_rate'])} win rate and a profit factor of "
                  f"{fmt_num(tc['profit_factor'])}{sharpe_bit}.")

    return " ".join([line1, line2, line3])


# --------------------------------------------------------------------------

def main() -> None:
    now = datetime.now(timezone.utc)

    trades = load_closed_trades(TRADES_F)
    equity_rows = load_equity(EQUITY_F)
    transfer_rows = load_transfers(TRANSFERS_F)

    eq_by_day = daily_equity_series(equity_rows)
    tr_by_day = transfers_by_day(transfer_rows)
    all_daily = compute_daily_returns(eq_by_day, tr_by_day)

    window_starts = {
        "trailing_7d": now - timedelta(days=7),
        "since_churn_fix": CHURN_FIX_START,
    }

    windows_out = {}
    log_bits = []
    for name, start in window_starts.items():
        w_trades = [t for t in trades if start <= t["closed_at"] <= now]
        w_daily = filter_daily(all_daily, start, now)
        tstats = trade_stats(w_trades)
        rstats = return_stats(w_daily)
        windows_out[name] = {
            "start": start.isoformat(),
            "end": now.isoformat(),
            "trades": tstats,
            "returns": rstats,
        }
        log_bits.append(
            f"{name}: n={tstats['n']} win={fmt_pct(tstats['win_rate'])} "
            f"pf={fmt_num(tstats['profit_factor'])} sharpe={fmt_num(rstats['sharpe'])} "
            f"maxdd={fmt_num(rstats['max_drawdown_pct'])}%"
        )

    report = {"generated": now.isoformat(), "windows": windows_out}
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        METRICS_F.write_text(json.dumps(report, indent=2))
    except Exception as e:
        log(f"metrics write FAIL {e!r}")

    log("weekly metrics | " + " | ".join(log_bits))

    msg = build_message(windows_out)
    notify("action", "Weekly crypto report", msg)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            log(f"sleeve_metrics FATAL {e!r}")
        except Exception:
            pass
    sys.exit(0)
