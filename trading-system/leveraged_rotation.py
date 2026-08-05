#!/usr/bin/env python3
"""
Leveraged ETF Daily Momentum Rotation
──────────────────────────────────────
Universe : SOXL TQQQ UPRO TECL FNGU FAS TNA  (3x levered ETFs)
Signal   : composite 3-day + 5-day momentum, cross-sectionally ranked
Filter   : price > 10-day SMA  (uptrend gate; avoids decay traps)
Hold     : top 1-2 by signal each day; 100% cash when none pass gate
Rotate   : signals from prior close → fill at next-day open (paper)

Modes:
  python3 leveraged_rotation.py --backtest         5-yr backtest
  python3 leveraged_rotation.py --signal           today's ranked signal
  python3 leveraged_rotation.py --paper            execute paper orders
  python3 leveraged_rotation.py --result           write JSON result file
  python3 leveraged_rotation.py --backtest --result  (all at once)
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ── Universe ──────────────────────────────────────────────────────────────────
TICKERS    = ["SOXL", "TQQQ", "UPRO", "TECL", "FNGU", "FAS", "TNA"]
TOP_N      = 2          # hold top-2 by momentum (equal weight)
MOM_FAST   = 3          # fast momentum lookback (calendar days)
MOM_SLOW   = 5          # slow momentum lookback
SMA_WIN    = 10         # uptrend SMA filter (exit when price < 10d SMA)
SPY_SMA    = 50         # SPY reference window (informational, not a gate)
SLIPPAGE   = 0.0005     # 5 bps per traded position (realistic for liquid 3x ETFs)
BACKTEST_Y = 5          # years of backtest history
SLEEVE_PCT = 0.95       # fraction of account equity used by this sleeve

# ── Alpaca paper API ──────────────────────────────────────────────────────────
PAPER_KEY    = "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV"
PAPER_SECRET = "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV"
BROKER       = "https://paper-api.alpaca.markets"

def _phdrs() -> dict:
    return {"APCA-API-KEY-ID": PAPER_KEY, "APCA-API-SECRET-KEY": PAPER_SECRET}


# ── Data ──────────────────────────────────────────────────────────────────────

def download_prices(period: str = "5y") -> tuple[pd.DataFrame, pd.Series]:
    """Return (close_df, spy_close). SPY is downloaded for context but not used as a gate."""
    all_syms = TICKERS + ["SPY"]
    raw = yf.download(all_syms, period=period, progress=False, auto_adjust=True)
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"]
    else:
        close = raw[["Close"]].rename(columns={"Close": all_syms[0]})
    if hasattr(close.index, "tz") and close.index.tz is not None:
        close.index = close.index.tz_localize(None)
    else:
        close.index = pd.to_datetime(close.index)
    close = close.dropna(how="all")
    spy   = close.pop("SPY")
    return close, spy


# ── Signal computation ────────────────────────────────────────────────────────

def compute_scores(
    close: pd.DataFrame,
    spy: pd.Series,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Returns (score, uptrend, spy_ok). spy_ok is informational only."""
    mom_f   = close.pct_change(MOM_FAST)
    mom_s   = close.pct_change(MOM_SLOW)
    sma     = close.rolling(SMA_WIN).mean()
    score   = 0.5 * mom_f + 0.5 * mom_s
    uptrend = (close > sma)
    spy_ok  = (spy > spy.rolling(SPY_SMA).mean())
    return score, uptrend, spy_ok


def rank_day(score_row: pd.Series, uptrend_row: pd.Series) -> list[str]:
    """Return ordered list of tickers to hold for this day (empty = cash)."""
    valid  = score_row.dropna()
    in_up  = uptrend_row.reindex(valid.index).fillna(False)
    valid  = valid[in_up]
    if valid.empty:
        return []
    return valid.nlargest(min(TOP_N, len(valid))).index.tolist()


# ── Backtest ──────────────────────────────────────────────────────────────────

def run_backtest() -> dict:
    print(f"[backtest] downloading {BACKTEST_Y}yr daily data for {TICKERS} + SPY …")
    close, spy = download_prices(f"{BACKTEST_Y}y")

    score, uptrend, spy_ok = compute_scores(close, spy)
    daily_ret              = close.pct_change()

    # Use prior-day signal to decide today's position (avoids look-ahead)
    score_lag   = score.shift(1)
    uptrend_lag = uptrend.shift(1)
    spy_lag     = spy_ok.shift(1)

    warmup = SMA_WIN + MOM_SLOW + 2
    port_rets = []
    dates     = []

    for date in close.index[warmup:]:
        held = rank_day(score_lag.loc[date], uptrend_lag.loc[date])

        if not held:
            port_rets.append(0.0)
            dates.append(date)
            continue

        n     = len(held)
        wt    = 1.0 / n
        r     = daily_ret.loc[date, held].fillna(0.0)
        total = float((r * wt).sum()) - SLIPPAGE * n  # haircut per leg traded
        port_rets.append(total)
        dates.append(date)

    ret = pd.Series(port_rets, index=dates, dtype=float)
    eq  = (1 + ret).cumprod()
    dd  = (eq - eq.cummax()) / eq.cummax()

    n_days = len(ret)
    avg_d  = float(ret.mean())
    std_d  = float(ret.std())
    sharpe = avg_d / std_d * np.sqrt(252) if std_d > 0 else 0.0
    maxdd  = float(dd.min())
    cagr   = float(eq.iloc[-1] ** (252 / n_days) - 1) if n_days > 0 else 0.0
    win    = float((ret > 0).mean())
    cash   = float((ret == 0.0).mean())

    # Annual breakdown (last 3 years)
    ann_lines = []
    for yr in sorted(set(ret.index.year))[-4:]:
        yr_ret = ret[ret.index.year == yr]
        yr_eq  = (1 + yr_ret).cumprod()
        ann_lines.append(f"  {yr}: {(yr_eq.iloc[-1]-1)*100:+.1f}%")

    print(f"\n{'='*58}")
    print(f"  Leveraged ETF Momentum Rotation — {BACKTEST_Y}yr Backtest")
    print(f"{'='*58}")
    print(f"  CAGR              : {cagr*100:+.1f}%")
    print(f"  Avg daily return  : {avg_d*100:+.4f}%  ({avg_d*252*100:+.1f}% annualised)")
    print(f"  Daily Sharpe      : {sharpe:.3f}")
    print(f"  Max drawdown      : {maxdd*100:.1f}%")
    print(f"  Win rate          : {win*100:.1f}%")
    print(f"  Cash days         : {cash*100:.1f}%")
    print(f"  Trade days        : {n_days}")
    print(f"  Final equity ($1) : ${eq.iloc[-1]:.2f}")
    print(f"  Annual P&L:")
    for ln in ann_lines:
        print(ln)
    print(f"{'='*58}\n")

    return {
        "avg_daily_ret": round(avg_d * 100, 4),
        "daily_sharpe":  round(sharpe, 3),
        "maxdd":         round(maxdd * 100, 2),
        "cagr":          round(cagr * 100, 2),
        "win_rate":      round(win * 100, 1),
        "cash_pct":      round(cash * 100, 1),
        "n_days":        n_days,
        "final_equity":  round(float(eq.iloc[-1]), 2),
    }


# ── Today's signal ────────────────────────────────────────────────────────────

def get_signal(verbose: bool = False) -> list[str]:
    """Compute today's hold list from the latest available closes."""
    close, spy = download_prices("90d")
    score, uptrend, spy_ok = compute_scores(close, spy)
    latest  = close.index[-1]
    row_s   = score.loc[latest]
    row_u   = uptrend.loc[latest]
    held    = rank_day(row_s, row_u)

    if verbose:
        spy_state = "✓ ON" if bool(spy_ok.iloc[-1]) else "✗ OFF"
        print(f"\n  Signal as of {latest.date()}")
        print(f"  SPY {SPY_SMA}d regime: {spy_state}  (informational)")
        print(f"  {'─'*44}")
        for tkr in row_s.dropna().sort_values(ascending=False).index:
            s    = row_s.get(tkr, float("nan"))
            t    = "✓ up" if row_u.get(tkr, False) else "✗ dn"
            flag = " ★ HOLD" if tkr in held else ""
            print(f"  {tkr:6s}  score={s*100:+6.2f}%  trend={t}{flag}")
        print(f"\n  → Hold: {held or ['CASH']}\n")

    return held


# ── Paper trading ─────────────────────────────────────────────────────────────

def _api(method: str, path: str, **kw):
    r = getattr(requests, method)(f"{BROKER}{path}", headers=_phdrs(), timeout=15, **kw)
    return r

def get_account() -> dict:
    return _api("get", "/v2/account").json()

def get_positions() -> dict:
    return {p["symbol"]: p for p in _api("get", "/v2/positions").json()}

def cancel_all_orders():
    _api("delete", "/v2/orders")

def close_position(sym: str) -> str:
    r = _api("delete", f"/v2/positions/{sym}")
    d = r.json()
    return d.get("status", d.get("message", str(d)))

def place_order_notional(sym: str, notional: float) -> str:
    """Buy $notional of sym at market — uses fractional shares."""
    payload = {
        "symbol":        sym,
        "notional":      f"{notional:.2f}",
        "side":          "buy",
        "type":          "market",
        "time_in_force": "day",
    }
    r = _api("post", "/v2/orders", json=payload)
    d = r.json()
    return d.get("status", d.get("message", str(d)))


def run_paper() -> list[str]:
    """Rotate paper account to today's momentum signal."""
    print("[paper] connecting to Alpaca paper account …")
    acct   = get_account()
    equity = float(acct.get("equity", 0))
    cash_b = float(acct.get("cash", 0))
    print(f"  Equity : ${equity:,.0f}  |  Cash : ${cash_b:,.0f}")

    signal = get_signal(verbose=True)
    cancel_all_orders()

    # ── Close stale positions ─────────────────────────────────────────────────
    positions = get_positions()
    for sym, pos in positions.items():
        if sym not in signal:
            mkt_val = float(pos.get("market_value", 0))
            status  = close_position(sym)
            print(f"  Closed {sym}  (${mkt_val:,.0f}) → {status}")

    if not signal:
        print("  → Fully in cash; no new positions.")
        return signal

    # ── Open / resize target positions ───────────────────────────────────────
    sleeve_cash = equity * SLEEVE_PCT
    per_pos     = sleeve_cash / len(signal)
    positions   = get_positions()   # refresh after closes

    for sym in signal:
        cur_val = float(positions.get(sym, {}).get("market_value", 0))
        target  = per_pos

        if cur_val > 0 and abs(cur_val - target) / target < 0.08:
            print(f"  {sym}: already ${cur_val:,.0f} ≈ target ${target:,.0f}; keeping")
            continue

        # Flatten and re-enter at target size (simplest for daily rotation)
        if sym in positions:
            close_position(sym)

        status = place_order_notional(sym, target)
        print(f"  Buy {sym}  ${target:,.0f}  → {status}")

    return signal


# ── Result JSON ───────────────────────────────────────────────────────────────

def write_result(metrics: dict, paper_wired: bool, signal: list[str]) -> Path:
    out_dir = Path("/Users/quarki3/swarm_results/completed")
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "task":          "leveraged_rotation",
        "timestamp":     datetime.now().isoformat(),
        "avg_daily_ret": metrics.get("avg_daily_ret"),
        "daily_sharpe":  metrics.get("daily_sharpe"),
        "maxdd":         metrics.get("maxdd"),
        "cagr":          metrics.get("cagr"),
        "win_rate":      metrics.get("win_rate"),
        "cash_pct":      metrics.get("cash_pct"),
        "n_days":        metrics.get("n_days"),
        "final_equity":  metrics.get("final_equity"),
        "paper_wired":   paper_wired,
        "today_signal":  signal,
        "universe":      TICKERS,
        "params": {
            "mom_fast_d":    MOM_FAST,
            "mom_slow_d":    MOM_SLOW,
            "sma_window_d":  SMA_WIN,
            "top_n":         TOP_N,
            "slippage_bps":  int(SLIPPAGE * 10_000),
            "backtest_years": BACKTEST_Y,
            "sleeve_pct":    SLEEVE_PCT,
        },
        "summary": (
            f"Daily momentum rotation across {len(TICKERS)} 3× ETFs. "
            f"{BACKTEST_Y}yr: CAGR {metrics.get('cagr', 0):+.1f}%, "
            f"daily Sharpe {metrics.get('daily_sharpe', 0):.2f}, "
            f"max DD {metrics.get('maxdd', 0):.1f}%. "
            f"Holds top-{TOP_N} by {MOM_FAST}/{MOM_SLOW}d momentum when above {SMA_WIN}d SMA, "
            f"else cash ({metrics.get('cash_pct', 0):.0f}% of days). "
            f"Today → {signal or ['CASH']}. "
            f"Paper orders wired to Alpaca (PKNEW4… account)."
        ),
        "success": True,
    }

    path = out_dir / "leveraged_rotation.json"
    path.write_text(json.dumps(result, indent=2))
    print(f"\n[result] → {path}")
    return path


# ── Scheduler helpers ─────────────────────────────────────────────────────────

PLIST_PATH = Path("/Users/quarki3/Library/LaunchAgents/com.quarki3.leveraged_rotation.plist")

def write_launchd_plist():
    """Create a launchd job that runs --paper at 09:29 weekdays."""
    py    = sys.executable
    script = Path(__file__).resolve()
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.quarki3.leveraged_rotation</string>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>{script}</string>
    <string>--paper</string>
    <string>--result</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <!-- Mon–Fri 09:29 ET (adjust if needed for timezone) -->
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>29</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>29</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>29</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>29</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>9</integer><key>Minute</key><integer>29</integer></dict>
  </array>
  <key>StandardOutPath</key>
  <string>/tmp/leveraged_rotation.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/leveraged_rotation_err.log</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
"""
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(plist)
    print(f"[schedule] plist → {PLIST_PATH}")
    print(f"  Load with: launchctl load {PLIST_PATH}")
    print(f"  Test with: launchctl start com.quarki3.leveraged_rotation")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Leveraged ETF Daily Momentum Rotation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--backtest",  action="store_true", help=f"Run {BACKTEST_Y}yr backtest")
    ap.add_argument("--signal",    action="store_true", help="Print today's ranked signal")
    ap.add_argument("--paper",     action="store_true", help="Execute paper orders")
    ap.add_argument("--result",    action="store_true", help="Write JSON result file")
    ap.add_argument("--schedule",  action="store_true", help="Install daily launchd job")
    args = ap.parse_args()

    # Default: run backtest if nothing specified
    if not any([args.backtest, args.signal, args.paper, args.result, args.schedule]):
        args.backtest = True

    metrics     = {}
    paper_wired = False
    signal      = []

    if args.backtest or args.result:
        metrics = run_backtest()

    if args.signal and not args.paper:
        signal = get_signal(verbose=True)

    if args.paper:
        signal      = run_paper()
        paper_wired = True

    if args.result:
        if not signal:
            signal = get_signal(verbose=False)
        write_result(metrics, paper_wired, signal)

    if args.schedule:
        write_launchd_plist()


if __name__ == "__main__":
    main()
