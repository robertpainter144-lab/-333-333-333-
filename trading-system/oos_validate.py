#!/usr/bin/env python3
"""
oos_validate.py — Out-of-sample / walk-forward validation helpers.

Import this from any backtest in this trading system.

Input convention
----------------
Returns must be fractional daily returns (e.g. 0.01 = 1% gain, -0.005 = -0.5% loss).
For intraday bar-level returns, pass periods_per_year = 252 * bars_per_day.
Sharpe is annualized assuming zero risk-free rate.

Quick-start
-----------
    from oos_validate import split_is_oos, report, walk_forward

    is_df, oos_df = split_is_oos(price_df, oos_frac=0.30)
    metrics = report("MyStrategy", is_df["ret"], oos_df["ret"])
    if metrics["degradation"] < 0.5:
        print("Severe degradation — do not trade live")
"""

import json
import math
from typing import Any, Callable, Tuple

import numpy as np
import pandas as pd


# ── Core split ────────────────────────────────────────────────────────────────

def split_is_oos(
    df: pd.DataFrame,
    oos_frac: float = 0.30,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Split df into (in_sample, out_of_sample) by time.

    OOS is the most recent oos_frac fraction. df must be sorted
    chronologically (oldest row first).

    Args:
        df:       DataFrame with a chronological index.
        oos_frac: Fraction reserved for OOS (default 0.30).

    Returns:
        (in_sample, out_of_sample) — both DataFrames share the original index.
    """
    if not 0 < oos_frac < 1:
        raise ValueError(f"oos_frac must be in (0, 1), got {oos_frac}")
    n = len(df)
    if n < 10:
        raise ValueError(f"DataFrame too short ({n} rows) to split meaningfully")
    split = n - max(1, int(round(n * oos_frac)))
    return df.iloc[:split].copy(), df.iloc[split:].copy()


# ── Metric helpers ────────────────────────────────────────────────────────────

def _sharpe(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Annualized Sharpe ratio (zero risk-free rate, sample std)."""
    if len(returns) < 2:
        return float("nan")
    mu = returns.mean()
    sigma = returns.std(ddof=1)
    if sigma == 0 or math.isnan(sigma):
        return 0.0
    return float(mu / sigma * math.sqrt(periods_per_year))


def _cagr(returns: pd.Series, periods_per_year: int = 252) -> float:
    """Compound annual growth rate from a series of fractional returns."""
    if len(returns) == 0:
        return float("nan")
    total = float((1.0 + returns).prod())
    if total <= 0:
        return -1.0
    years = len(returns) / periods_per_year
    return total ** (1.0 / years) - 1.0


def _max_drawdown(returns: pd.Series) -> float:
    """Maximum drawdown as a negative fraction (e.g. -0.15 = -15%)."""
    if len(returns) == 0:
        return float("nan")
    equity = (1.0 + returns).cumprod()
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())


def _metrics(returns: pd.Series, periods_per_year: int = 252) -> dict:
    return {
        "sharpe": _sharpe(returns, periods_per_year),
        "cagr":   _cagr(returns, periods_per_year),
        "max_dd": _max_drawdown(returns),
        "n":      int(len(returns)),
    }


# ── Report ────────────────────────────────────────────────────────────────────

def report(
    name: str,
    is_returns: pd.Series,
    oos_returns: pd.Series,
    periods_per_year: int = 252,
    verbose: bool = True,
) -> dict:
    """Compare in-sample vs out-of-sample performance and return a metrics dict.

    Args:
        name:            Strategy label shown in the printed table.
        is_returns:      In-sample fractional returns (pd.Series).
        oos_returns:     Out-of-sample fractional returns (pd.Series).
        periods_per_year: 252 for daily; 252*78 for 5-min bars; etc.
        verbose:         Print the comparison table (default True).

    Returns:
        dict with keys:
            name, is_sharpe, is_cagr, is_max_dd, is_n,
            oos_sharpe, oos_cagr, oos_max_dd, oos_n,
            degradation  (OOS_Sharpe / IS_Sharpe; None if IS_Sharpe ≈ 0)

        CAGR and MaxDD values are in percent (e.g. -15.2 means -15.2%).
    """
    is_m  = _metrics(is_returns,  periods_per_year)
    oos_m = _metrics(oos_returns, periods_per_year)

    is_sharpe  = is_m["sharpe"]
    oos_sharpe = oos_m["sharpe"]

    if abs(is_sharpe) < 1e-9 or math.isnan(is_sharpe):
        degradation = None
    else:
        degradation = round(oos_sharpe / is_sharpe, 4)

    result = {
        "name":       name,
        "is_sharpe":  round(is_sharpe,         3),
        "is_cagr":    round(is_m["cagr"] * 100, 2),
        "is_max_dd":  round(is_m["max_dd"] * 100, 2),
        "is_n":       is_m["n"],
        "oos_sharpe": round(oos_sharpe,          3),
        "oos_cagr":   round(oos_m["cagr"] * 100, 2),
        "oos_max_dd": round(oos_m["max_dd"] * 100, 2),
        "oos_n":      oos_m["n"],
        "degradation": degradation,
    }

    if verbose:
        _print_report(result)

    return result


def _print_report(r: dict) -> None:
    deg_str = f"{r['degradation']:.3f}" if r["degradation"] is not None else "N/A"
    d = r["degradation"]
    if d is None:
        flag = "  (IS Sharpe ≈ 0, ratio undefined)"
    elif d < 0:
        flag = "  *** OOS SIGN FLIP — strategy reverses in OOS"
    elif d < 0.50:
        flag = "  *** Severe degradation (< 0.50)"
    elif d < 0.75:
        flag = "  ** Notable degradation (0.50–0.75)"
    else:
        flag = "  (acceptable degradation)"

    print(f"\n{'='*62}")
    print(f"  OOS Validation: {r['name']}")
    print(f"{'='*62}")
    print(f"  {'Metric':<14} {'In-Sample':>13} {'Out-of-Sample':>15}")
    print(f"  {'-'*44}")
    print(f"  {'Sharpe':<14} {r['is_sharpe']:>13.3f} {r['oos_sharpe']:>15.3f}")
    print(f"  {'CAGR %':<14} {r['is_cagr']:>12.2f}% {r['oos_cagr']:>14.2f}%")
    print(f"  {'MaxDD %':<14} {r['is_max_dd']:>12.2f}% {r['oos_max_dd']:>14.2f}%")
    print(f"  {'N periods':<14} {r['is_n']:>13,} {r['oos_n']:>15,}")
    print(f"  {'-'*44}")
    print(f"  Degradation (OOS_Sharpe / IS_Sharpe): {deg_str}{flag}")
    print(f"{'='*62}\n")


# ── Walk-forward ──────────────────────────────────────────────────────────────

def walk_forward(
    df: pd.DataFrame,
    n_folds: int,
    fit_fn: Callable[[pd.DataFrame], Any],
    eval_fn: Callable[[pd.DataFrame, Any], pd.Series],
    min_oos_periods: int = 20,
    periods_per_year: int = 252,
) -> dict:
    """Generic anchored walk-forward validation.

    Uses an *expanding* IS window (anchored at the start). For fold k:
        IS  = df.iloc[ : k * fold_size ]
        OOS = df.iloc[ k * fold_size : (k+1) * fold_size ]

    Rolling (fixed-window) walk-forward can be achieved by slicing inside
    fit_fn itself (e.g. is_df.iloc[-N:]).

    Args:
        df:              Chronologically sorted DataFrame.
        n_folds:         Total number of equal-sized slices (must be >= 2).
        fit_fn:          fit_fn(is_df) -> Any
                         Return whatever your strategy needs (params dict,
                         sklearn model, etc.).
        eval_fn:         eval_fn(oos_df, params) -> pd.Series of fractional returns
                         The Series index does not need to match df; it is
                         concatenated in fold order.
        min_oos_periods: Skip a fold whose OOS slice is shorter than this.
        periods_per_year: Used only for Sharpe/CAGR annualization.

    Returns:
        dict with keys:
            n_folds_run        (actual folds executed, may be < n_folds - 1)
            folds              (list of per-fold dicts)
            combined_oos_sharpe
            combined_oos_cagr   (percent)
            combined_oos_max_dd (percent)
    """
    if n_folds < 2:
        raise ValueError("n_folds must be >= 2 (need at least 1 IS fold + 1 OOS fold)")

    n = len(df)
    fold_size = n // n_folds
    if fold_size < min_oos_periods:
        raise ValueError(
            f"Each fold is only {fold_size} rows but min_oos_periods={min_oos_periods}. "
            f"Reduce n_folds or lower min_oos_periods."
        )

    folds_out   = []
    all_oos_ret = []

    for k in range(1, n_folds):
        is_end    = k * fold_size
        oos_start = is_end
        oos_end   = (k + 1) * fold_size

        is_slice  = df.iloc[:is_end]
        oos_slice = df.iloc[oos_start:oos_end]

        if len(oos_slice) < min_oos_periods:
            continue

        params  = fit_fn(is_slice)
        oos_ret = eval_fn(oos_slice, params)

        if not isinstance(oos_ret, pd.Series):
            oos_ret = pd.Series(oos_ret)

        m = _metrics(oos_ret, periods_per_year)
        folds_out.append({
            "fold":            k,
            "is_rows":         len(is_slice),
            "oos_rows":        len(oos_slice),
            "oos_sharpe":      round(m["sharpe"],      3),
            "oos_cagr_pct":    round(m["cagr"] * 100,  2),
            "oos_max_dd_pct":  round(m["max_dd"] * 100, 2),
        })
        all_oos_ret.append(oos_ret)

    if not all_oos_ret:
        return {
            "n_folds_run":         0,
            "folds":               [],
            "combined_oos_sharpe": float("nan"),
            "combined_oos_cagr":   float("nan"),
            "combined_oos_max_dd": float("nan"),
        }

    combined = pd.concat(all_oos_ret, ignore_index=True)
    cm = _metrics(combined, periods_per_year)

    return {
        "n_folds_run":         len(folds_out),
        "folds":               folds_out,
        "combined_oos_sharpe": round(cm["sharpe"],      3),
        "combined_oos_cagr":   round(cm["cagr"] * 100,  2),
        "combined_oos_max_dd": round(cm["max_dd"] * 100, 2),
    }


# ── Demo ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 62)
    print("  oos_validate.py — self-test & math verification")
    print("=" * 62)

    rng = np.random.default_rng(42)
    DAYS = 1260  # 5 years

    # IS-period returns: mild positive drift (≈ 10% CAGR target)
    # Theoretical Sharpe = 0.0004 / 0.010 * sqrt(252) ≈ 0.635
    n_is = int(DAYS * 0.70)
    is_ret_arr = rng.normal(loc=0.0004, scale=0.010, size=n_is)

    # OOS-period returns: negative drift (regime shift — realistic worst case)
    # Theoretical Sharpe = -0.0001 / 0.013 * sqrt(252) ≈ -0.122
    n_oos = DAYS - n_is
    oos_ret_arr = rng.normal(loc=-0.0001, scale=0.013, size=n_oos)

    dates = pd.bdate_range("2019-01-02", periods=DAYS)
    full_returns = pd.Series(
        np.concatenate([is_ret_arr, oos_ret_arr]), index=dates, name="ret"
    )
    df = pd.DataFrame({"ret": full_returns})

    # ── 1. split_is_oos ──────────────────────────────────────────────────────
    print("\n[1] split_is_oos  (oos_frac=0.30)")
    is_df, oos_df = split_is_oos(df, oos_frac=0.30)
    is_r  = is_df["ret"]
    oos_r = oos_df["ret"]
    print(f"    IS : {len(is_df):4d} days  {is_df.index[0].date()} → {is_df.index[-1].date()}")
    print(f"    OOS: {len(oos_df):4d} days  {oos_df.index[0].date()} → {oos_df.index[-1].date()}")

    # ── 2. Math spot-check ───────────────────────────────────────────────────
    print("\n[2] Sharpe / CAGR / MaxDD spot-checks")

    # Sharpe
    manual_sharpe_is  = is_r.mean()  / is_r.std(ddof=1)  * np.sqrt(252)
    manual_sharpe_oos = oos_r.mean() / oos_r.std(ddof=1) * np.sqrt(252)
    computed_sharpe_is  = _sharpe(is_r)
    computed_sharpe_oos = _sharpe(oos_r)
    assert abs(manual_sharpe_is  - computed_sharpe_is)  < 1e-9, "IS Sharpe mismatch"
    assert abs(manual_sharpe_oos - computed_sharpe_oos) < 1e-9, "OOS Sharpe mismatch"
    print(f"    IS  Sharpe  manual={manual_sharpe_is:.4f}  computed={computed_sharpe_is:.4f}  OK")
    print(f"    OOS Sharpe  manual={manual_sharpe_oos:.4f}  computed={computed_sharpe_oos:.4f}  OK")

    # CAGR
    manual_cagr_is = float((1.0 + is_r).prod() ** (252 / len(is_r)) - 1)
    computed_cagr_is = _cagr(is_r)
    assert abs(manual_cagr_is - computed_cagr_is) < 1e-9, "IS CAGR mismatch"
    print(f"    IS  CAGR    manual={manual_cagr_is*100:.2f}%  computed={computed_cagr_is*100:.2f}%  OK")

    # MaxDD
    equity = (1.0 + is_r).cumprod()
    manual_mdd = float(((equity - equity.cummax()) / equity.cummax()).min())
    computed_mdd = _max_drawdown(is_r)
    assert abs(manual_mdd - computed_mdd) < 1e-9, "MaxDD mismatch"
    print(f"    IS  MaxDD   manual={manual_mdd*100:.2f}%  computed={computed_mdd*100:.2f}%  OK")

    # ── 3. report ────────────────────────────────────────────────────────────
    print("\n[3] report()")
    r = report("Synthetic Strategy (regime shift demo)", is_r, oos_r)

    print(f"    Degradation={r['degradation']}  (expect < 0 since OOS drift is negative)")
    assert r["oos_sharpe"] < r["is_sharpe"], "OOS should be worse than IS in this demo"

    # ── 4. walk_forward ──────────────────────────────────────────────────────
    print("[4] walk_forward (5 folds, buy-and-hold eval_fn)")

    def fit_buyhold(is_df: pd.DataFrame) -> dict:
        # No fitting needed for buy-and-hold; return mean IS return as reference only.
        return {"is_mean_ret": float(is_df["ret"].mean())}

    def eval_buyhold(oos_df: pd.DataFrame, params: dict) -> pd.Series:
        return oos_df["ret"]

    wf = walk_forward(
        df, n_folds=5,
        fit_fn=fit_buyhold,
        eval_fn=eval_buyhold,
    )

    print(f"    Folds run:            {wf['n_folds_run']}")
    print(f"    Combined OOS Sharpe:  {wf['combined_oos_sharpe']:.3f}")
    print(f"    Combined OOS CAGR:    {wf['combined_oos_cagr']:.2f}%")
    print(f"    Combined OOS MaxDD:   {wf['combined_oos_max_dd']:.2f}%")
    print()
    for fold in wf["folds"]:
        print(f"    Fold {fold['fold']}: IS={fold['is_rows']}d  "
              f"OOS={fold['oos_rows']}d  "
              f"Sharpe={fold['oos_sharpe']:+.3f}  "
              f"CAGR={fold['oos_cagr_pct']:+.1f}%  "
              f"MaxDD={fold['oos_max_dd_pct']:.1f}%")

    assert wf["n_folds_run"] == 4, f"Expected 4 OOS folds, got {wf['n_folds_run']}"

    # Combined OOS Sharpe should be lower than full-period mean (regime shift drags it)
    print(f"\n    Theoretical IS Sharpe  ≈ {0.0004/0.010*np.sqrt(252):.3f} (drift=0.04%/day, vol=1.0%)")
    print(f"    Theoretical OOS Sharpe ≈ {-0.0001/0.013*np.sqrt(252):.3f} (drift=-0.01%/day, vol=1.3%)")

    print("\n[PASS] All assertions passed. Math is correct.")
