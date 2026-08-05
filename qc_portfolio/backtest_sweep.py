"""
Parameter sweep over:
  - rebalance_days      : 1, 2, 3, 5, 10 bars between rebalances
  - trail_stop_pct      : 0.03, 0.05, 0.08, 0.12, 0.15
  - max_position_cap    : 0.30, 0.40, 0.50, 0.75, 1.0 (1.0 = uncapped)
  - use_intraday_stops  : False (close-only), True (daily LOW triggers stop)

Uses maximum available history (~14 years).
Outputs a results table sorted by Sharpe ratio.

Usage:
    python3.10 backtest_sweep.py
    python3.10 backtest_sweep.py --quick   # smaller grid, faster
"""
import sys
import itertools
import time
from backtest import run_backtest
from config import STRATEGY_CONFIGS

STRATEGY_IDS = list(STRATEGY_CONFIGS.keys())[:5]

# ── Parameter grids ────────────────────────────────────────────────────────

FULL_GRID = {
    "rebalance_days":     [1, 2, 3, 5, 10],
    "trail_stop_pct":     [0.03, 0.05, 0.08, 0.12, 0.15],
    "max_position_cap":   [0.30, 0.40, 0.50, 0.75, 1.0],
    "use_intraday_stops": [False, True],
}

QUICK_GRID = {
    "rebalance_days":     [1, 2, 3, 5],
    "trail_stop_pct":     [0.05, 0.08, 0.12],
    "max_position_cap":   [0.40, 0.60, 1.0],
    "use_intraday_stops": [False, True],
}


def _label(params):
    cap = f"{int(params['max_position_cap']*100)}%" if params['max_position_cap'] < 1.0 else "none"
    iday = "intraday" if params["use_intraday_stops"] else "close"
    return (f"reb={params['rebalance_days']}d  "
            f"stop={int(params['trail_stop_pct']*100)}%  "
            f"cap={cap}  "
            f"stops={iday}")


def run_sweep(grid):
    keys   = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))
    total  = len(combos)
    print(f"\nRunning {total} backtest combinations over max history …\n")

    results = []
    t0 = time.time()

    for i, values in enumerate(combos, 1):
        params = dict(zip(keys, values))
        label  = _label(params)
        try:
            bt = run_backtest(
                strategy_ids      = STRATEGY_IDS,
                lookback_years    = None,
                trail_stop_pct    = params["trail_stop_pct"],
                max_position_cap  = params["max_position_cap"],
                rebalance_days    = params["rebalance_days"],
                use_intraday_stops= params["use_intraday_stops"],
            )
            if bt is None:
                continue
            m = bt["metrics"]
            results.append({**params, **m, "label": label})
        except Exception as e:
            print(f"  [{i}/{total}] ERROR {label}: {e}")
            continue

        elapsed = time.time() - t0
        eta     = (elapsed / i) * (total - i)
        print(f"  [{i:>3}/{total}]  sharpe={m.get('sharpe',0):>5.2f}  "
              f"cagr={m.get('cagr',0):>7.2f}%  "
              f"dd={m.get('max_dd',0):>6.2f}%  "
              f"{label}   ETA {eta/60:.1f}m")

    return results


def print_table(results, top_n=20):
    if not results:
        print("No results.")
        return

    # Sort by Sharpe descending; break ties by CAGR
    results.sort(key=lambda r: (r.get("sharpe", 0), r.get("cagr", 0)), reverse=True)

    header = (f"{'#':>3}  {'Sharpe':>6}  {'CAGR':>7}  {'MaxDD':>6}  "
              f"{'WinRate':>7}  {'Trades':>6}  "
              f"{'Reb':>3}  {'Stop':>4}  {'Cap':>4}  {'Intraday':>8}")
    print("\n" + "=" * len(header))
    print(header)
    print("=" * len(header))

    for rank, r in enumerate(results[:top_n], 1):
        cap_s  = f"{int(r['max_position_cap']*100)}%" if r['max_position_cap'] < 1.0 else "none"
        iday_s = "yes" if r["use_intraday_stops"] else "no"
        print(
            f"{rank:>3}  "
            f"{r.get('sharpe',0):>6.2f}  "
            f"{r.get('cagr',0):>7.2f}%  "
            f"{r.get('max_dd',0):>6.2f}%  "
            f"{r.get('win_rate',0):>7.1f}%  "
            f"{r.get('total_trades',0):>6}  "
            f"{r['rebalance_days']:>3}d  "
            f"{int(r['trail_stop_pct']*100):>3}%  "
            f"{cap_s:>4}  "
            f"{iday_s:>8}"
        )

    print("=" * len(header))
    best = results[0]
    print(f"\nBest config:  {best['label']}")
    print(f"  Sharpe {best.get('sharpe',0):.2f}  |  "
          f"CAGR {best.get('cagr',0):.2f}%  |  "
          f"MaxDD {best.get('max_dd',0):.2f}%  |  "
          f"WinRate {best.get('win_rate',0):.1f}%\n")


if __name__ == "__main__":
    grid = QUICK_GRID if "--quick" in sys.argv else FULL_GRID
    results = run_sweep(grid)
    print_table(results)
