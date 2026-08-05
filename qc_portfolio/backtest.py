"""
Walk-forward daily backtest of the QC portfolio strategy.
Simulates: QC ranking → portfolio weights → daily momentum allocation
→ rebalancing → stop-loss enforcement.
"""
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

from config import STRATEGY_CONFIGS, LARGE_CAP_UNIVERSE, LEVERAGED_ETF_UNIVERSE


def run_backtest(strategy_ids=None, lookback_years=None,
                 trail_stop_pct=0.05, portfolio_dd_limit=0.15,
                 initial_capital=100_000, max_position_cap=1.0,
                 rebalance_days=1, use_intraday_stops=False,
                 dd_ladder=None, min_order_dollars=None):
    """
    Simulate the portfolio over historical daily data.

    Parameters
    ----------
    lookback_years   : float | None — None uses max available history (~14y)
    max_position_cap : float — cap any single symbol at this fraction of portfolio (1.0 = no cap)
    rebalance_days   : int   — bars between rebalances (1=daily, 2=every 2 days, …)
    use_intraday_stops : bool — use daily LOW to simulate intraday stop triggers
    dd_ladder        : list of (threshold, target_exposure) tuples, sorted ascending by threshold.
                       Replaces the single portfolio_dd_limit trigger when provided.
                       e.g. [(0.10, 0.75), (0.15, 0.50), (0.20, 0.25), (0.25, 0.0)]
                       Each tier fires once per drawdown cycle; resets on new equity peak.

    Returns dict:
        equity_curve, drawdown_curve, benchmark, trades, metrics
    """
    if strategy_ids is None:
        strategy_ids = list(STRATEGY_CONFIGS.keys())[:5]

    # ── Gather all symbols ──────────────────────────────────────────────────
    all_syms = {"SPY"}
    for sid in strategy_ids:
        cfg = STRATEGY_CONFIGS.get(sid, {})
        all_syms.update(cfg.get("universe", LARGE_CAP_UNIVERSE))

    end = datetime.now()
    if lookback_years is None:
        start = end - timedelta(days=14 * 365 + 120)   # ~14y — covers TQQQ/SOXL history
    else:
        start = end - timedelta(days=int(lookback_years * 365) + 120)

    print(f"[BT] downloading {len(all_syms)} symbols …")
    raw = yf.download(
        list(all_syms),
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=True,
        threads=True,
    )
    if raw.empty:
        return None

    close = raw["Close"] if "Close" in raw else raw
    if isinstance(close, pd.Series):
        close = close.to_frame(list(all_syms)[0])
    close.columns = [str(c) for c in close.columns]
    close = close.dropna(how="all").ffill()

    # Daily LOW — used only when use_intraday_stops=True
    if use_intraday_stops and "Low" in raw:
        low_df = raw["Low"].copy()
        low_df.columns = [str(c) for c in low_df.columns]
        low_df = low_df.reindex(close.index).ffill()
    else:
        low_df = None

    # Scale minimum order size with capital; default $50 for $100k, proportional otherwise
    min_order = min_order_dollars if min_order_dollars is not None else max(1.0, initial_capital * 0.0005)

    warmup = 120
    trade_dates = close.index[warmup:]

    # ── Compute backtest weights (2x winner, equal-ish otherwise) ──────────
    n = len(strategy_ids)
    base_w = [1.0] * n
    base_w[0] = 2.0
    total_w = sum(base_w)
    bt_weights = [w / total_w for w in base_w]

    # ── Simulation state ────────────────────────────────────────────────────
    cash      = float(initial_capital)
    holdings  = {}   # {sym: {qty, cost, peak}}
    peak_eq   = float(initial_capital)
    last_reb  = None

    # Drawdown ladder state
    exposure_level = 1.0          # current fraction of capital allowed in equities
    tiers_fired    = set()        # thresholds already triggered this drawdown cycle

    equity_curve   = {}
    drawdown_curve = {}
    trades         = []

    for date in trade_dates:
        prices = {sym: float(close.at[date, sym])
                  for sym in all_syms
                  if sym in close.columns and not pd.isna(close.at[date, sym])}

        # Intraday low prices for stop evaluation (falls back to close when not available)
        if low_df is not None:
            lows = {sym: float(low_df.at[date, sym])
                    for sym in all_syms
                    if sym in low_df.columns and not pd.isna(low_df.at[date, sym])}
        else:
            lows = prices

        # ── Mark-to-market ─────────────────────────────────────────────────
        port_val = cash + sum(
            info["qty"] * prices.get(sym, info["cost"])
            for sym, info in holdings.items()
        )

        # ── Trailing stop-loss ─────────────────────────────────────────────
        for sym in list(holdings.keys()):
            if sym not in prices:
                continue
            p_close = prices[sym]
            p_check = lows.get(sym, p_close)   # intraday LOW when available
            peak    = holdings[sym]["peak"]
            stop_px = peak * (1 - trail_stop_pct)
            # Evaluate stop against pre-update peak so today's close gain
            # doesn't raise the bar before checking today's intraday low
            if p_check < stop_px:
                fill_px = min(p_check, stop_px)
                pnl  = (fill_px - holdings[sym]["cost"]) * holdings[sym]["qty"]
                cash += holdings[sym]["qty"] * fill_px
                trades.append({"date": date, "symbol": sym, "side": "sell",
                                "qty": holdings[sym]["qty"], "price": fill_px,
                                "pnl": pnl, "reason": "trail_stop"})
                del holdings[sym]
            else:
                # Only update peak after confirming no stop-out
                if p_close > holdings[sym]["peak"]:
                    holdings[sym]["peak"] = p_close

        # ── Portfolio drawdown protection ──────────────────────────────────
        port_val = cash + sum(
            info["qty"] * prices.get(sym, info["cost"])
            for sym, info in holdings.items()
        )
        prev_peak = peak_eq
        peak_eq   = max(peak_eq, port_val)
        dd        = (peak_eq - port_val) / peak_eq if peak_eq > 0 else 0

        if dd_ladder:
            # Reset ladder tiers when a new equity peak is made
            if peak_eq > prev_peak:
                tiers_fired.clear()
                exposure_level = 1.0

            # Fire any newly crossed tiers
            for threshold, target_exp in sorted(dd_ladder):
                if dd >= threshold and threshold not in tiers_fired and exposure_level > target_exp:
                    sell_frac = 1.0 - (target_exp / exposure_level) if exposure_level > 0 else 1.0
                    for sym in list(holdings.keys()):
                        if sym not in prices:
                            continue
                        qty = holdings[sym]["qty"] * sell_frac
                        if qty <= 0:
                            continue
                        p   = prices[sym]
                        pnl = (p - holdings[sym]["cost"]) * qty
                        cash += qty * p
                        holdings[sym]["qty"] -= qty
                        trades.append({"date": date, "symbol": sym, "side": "sell",
                                        "qty": qty, "price": p, "pnl": pnl,
                                        "reason": f"ladder_{int(threshold*100)}pct"})
                        if holdings[sym]["qty"] <= 0:
                            del holdings[sym]
                    tiers_fired.add(threshold)
                    exposure_level = target_exp
        else:
            if dd > portfolio_dd_limit:
                for sym in list(holdings.keys()):
                    if sym not in prices:
                        continue
                    qty = holdings[sym]["qty"] * 0.5
                    p   = prices[sym]
                    pnl = (p - holdings[sym]["cost"]) * qty
                    cash += qty * p
                    holdings[sym]["qty"] -= qty
                    trades.append({"date": date, "symbol": sym, "side": "sell",
                                    "qty": qty, "price": p, "pnl": pnl, "reason": "port_dd"})
                    if holdings[sym]["qty"] <= 0:
                        del holdings[sym]

        # ── Rebalance (every rebalance_days bars) ──────────────────────────
        should_reb = last_reb is None or (date - last_reb).days >= rebalance_days
        if should_reb:
            hist = close.loc[:date]
            target = {}

            for i, sid in enumerate(strategy_ids):
                cfg       = STRATEGY_CONFIGS.get(sid, {})
                universe  = cfg.get("universe", LARGE_CAP_UNIVERSE)
                alloc_t   = cfg.get("alloc", "single_winner")
                avail     = [s for s in universe if s in close.columns]

                if not avail:
                    continue

                strat_cap = port_val * bt_weights[i]
                scores    = _bt_momentum(avail, hist)
                ranked    = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

                if alloc_t == "single_winner" and ranked:
                    sym = ranked[0]
                    target[sym] = target.get(sym, 0.0) + strat_cap
                elif alloc_t in ("top3_equal", "top5_equal"):
                    k = 3 if alloc_t == "top3_equal" else 5
                    top = ranked[:min(k, len(ranked))]
                    for sym in top:
                        target[sym] = target.get(sym, 0.0) + strat_cap / len(top)

            # Apply max position cap
            if max_position_cap < 1.0:
                cap_val = port_val * max_position_cap
                for sym in list(target.keys()):
                    if target[sym] > cap_val:
                        target[sym] = cap_val

            # Close positions not in target
            for sym in list(holdings.keys()):
                if sym not in target and sym in prices:
                    qty  = holdings[sym]["qty"]
                    p    = prices[sym]
                    pnl  = (p - holdings[sym]["cost"]) * qty
                    cash += qty * p
                    trades.append({"date": date, "symbol": sym, "side": "sell",
                                   "qty": qty, "price": p, "pnl": pnl, "reason": "rebalance"})
                    del holdings[sym]

            # Adjust positions toward target
            for sym, tgt_val in target.items():
                if sym not in prices or prices[sym] <= 0:
                    continue
                p       = prices[sym]
                cur_qty = holdings.get(sym, {}).get("qty", 0.0)
                cur_val = cur_qty * p
                diff    = tgt_val - cur_val

                if abs(diff) < min_order:
                    continue

                qty = abs(diff) / p
                if diff > 0 and cash >= qty * p:
                    cash -= qty * p
                    if sym in holdings:
                        old_q, old_c = holdings[sym]["qty"], holdings[sym]["cost"]
                        new_q = old_q + qty
                        holdings[sym] = {"qty": new_q,
                                         "cost": (old_c * old_q + p * qty) / new_q,
                                         "peak": max(holdings[sym]["peak"], p)}
                    else:
                        holdings[sym] = {"qty": qty, "cost": p, "peak": p}
                    trades.append({"date": date, "symbol": sym, "side": "buy",
                                   "qty": qty, "price": p, "pnl": 0, "reason": "rebalance"})
                elif diff < 0:
                    sell_qty = min(qty, cur_qty)
                    if sell_qty > 0:
                        pnl  = (p - holdings[sym]["cost"]) * sell_qty
                        cash += sell_qty * p
                        holdings[sym]["qty"] -= sell_qty
                        if holdings[sym]["qty"] <= 0:
                            del holdings[sym]
                        trades.append({"date": date, "symbol": sym, "side": "sell",
                                       "qty": sell_qty, "price": p, "pnl": pnl, "reason": "rebalance"})
            last_reb = date

        # ── Record snapshot ────────────────────────────────────────────────
        port_val = cash + sum(
            info["qty"] * prices.get(sym, info["cost"])
            for sym, info in holdings.items()
        )
        equity_curve[date]   = port_val
        dd_pct = (peak_eq - port_val) / peak_eq * 100 if peak_eq > 0 else 0
        drawdown_curve[date] = max(dd_pct, 0)

    ec  = pd.Series(equity_curve)
    ddc = pd.Series(drawdown_curve)

    spy_raw = close["SPY"].iloc[warmup:] if "SPY" in close.columns else pd.Series()
    if not spy_raw.empty and spy_raw.iloc[0] > 0:
        benchmark = (spy_raw / spy_raw.iloc[0]) * initial_capital
    else:
        benchmark = pd.Series()

    metrics = _metrics(ec, trades, initial_capital)

    return {
        "equity_curve":   ec,
        "drawdown_curve": ddc,
        "benchmark":      benchmark,
        "trades":         trades,
        "metrics":        metrics,
    }


def _bt_momentum(symbols, close_df):
    scores = {}
    for sym in symbols:
        if sym not in close_df.columns:
            scores[sym] = 0.0
            continue
        p = close_df[sym].dropna()
        n = len(p)
        if n < 21:
            scores[sym] = 0.0
            continue
        curr   = float(p.iloc[-1])
        roc20  = (curr / float(p.iloc[-20]) - 1)
        roc60  = (curr / float(p.iloc[-60]) - 1) if n >= 61 else roc20
        scores[sym] = 0.5 * roc20 + 0.5 * roc60
    return scores


def _metrics(ec, trades, initial_capital):
    if ec.empty or len(ec) < 5:
        return {}
    final  = float(ec.iloc[-1])
    days   = (ec.index[-1] - ec.index[0]).days
    years  = max(days / 365.25, 0.1)
    cagr   = ((final / initial_capital) ** (1 / years) - 1) * 100
    rets   = ec.pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    rm     = ec.cummax()
    max_dd = float(((ec - rm) / rm).min() * 100)
    sells  = [t for t in trades if t["side"] == "sell" and "pnl" in t]
    wins   = sum(1 for t in sells if t["pnl"] > 0)
    wr     = (wins / len(sells) * 100) if sells else 0
    return {
        "cagr":         round(cagr, 2),
        "sharpe":       round(sharpe, 2),
        "max_dd":       round(abs(max_dd), 2),
        "win_rate":     round(wr, 2),
        "total_trades": len(sells),
        "final_value":  round(final, 2),
    }
