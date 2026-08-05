"""
Equal-Risk-Contribution (ERC) allocator vs. Top-N Momentum backtest.

Sleeves:
  US-momentum  : MTUM  (iShares MSCI USA Momentum Factor ETF)
  Intl/Asia    : EFA   (iShares MSCI EAFE) + EEM (EM) blended 60/40
  Bonds        : TLT   (iShares 20+ Year Treasury)
  Gold         : GLD   (SPDR Gold Trust)

Momentum benchmark:
  Universe of 10 SPDR sector ETFs; pick top-3 by 12-1 month momentum,
  equal-weight, rebalance monthly — mimics the QC top-N book.

Backtest: 2021-01-01 → 2026-06-13  (~5.5 years)
Rebalance: monthly (first trading day of each month)
Max per-sleeve weight: 40 %
Rolling cov window: 60 trading days
"""

import json, os, warnings
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import skew
import yfinance as yf

warnings.filterwarnings("ignore")

# ── Configuration ─────────────────────────────────────────────────────────────
START        = "2021-01-01"
END          = "2026-06-13"          # data cutoff
COV_WINDOW   = 60                    # trading days for rolling cov
MAX_SLEEVE   = 0.40                  # per-sleeve hard cap
INITIAL_CAP  = 100_000.0
RF_ANNUAL    = 0.045                 # risk-free rate for Sharpe

# ERC sleeves (proxy tickers)
ERC_TICKERS = {
    "us_momentum": ["MTUM"],
    "intl_asia":   ["EFA", "EEM"],   # blended 60/40
    "bonds":       ["TLT"],
    "gold":        ["GLD"],
}
INTL_BLEND = {"EFA": 0.60, "EEM": 0.40}

# Momentum universe (SPDR sectors)
MOM_UNIVERSE = ["XLK", "XLY", "XLF", "XLV", "XLE", "XLI", "XLB", "XLU", "XLP", "XLRE"]
MOM_TOP_N    = 3
MOM_LOOKBACK = 252    # 12-month momentum window (trading days)
MOM_SKIP     = 21     # skip last month (Jegadeesh-Titman standard)


# ── Data download ─────────────────────────────────────────────────────────────
def download_prices(tickers: list[str], start: str, end: str) -> pd.DataFrame:
    data = yf.download(tickers, start=start, end=end,
                       auto_adjust=True, progress=False)
    if isinstance(data.columns, pd.MultiIndex):
        prices = data["Close"]
    else:
        prices = data[["Close"]] if "Close" in data.columns else data
        prices.columns = tickers
    prices = prices.dropna(how="all")
    return prices


# ── Build sleeve prices (blended where needed) ────────────────────────────────
def build_sleeve_prices(raw: pd.DataFrame) -> pd.DataFrame:
    """Return one price series per sleeve, using blending for intl/asia."""
    sleeves = {}
    sleeves["us_momentum"] = raw["MTUM"]
    # intl/asia: blend EFA 60% + EEM 40% on normalised basis
    efa = raw["EFA"] / raw["EFA"].iloc[0]
    eem = raw["EEM"] / raw["EEM"].iloc[0]
    sleeves["intl_asia"] = (INTL_BLEND["EFA"] * efa + INTL_BLEND["EEM"] * eem) * 100.0
    sleeves["bonds"]     = raw["TLT"]
    sleeves["gold"]      = raw["GLD"]
    return pd.DataFrame(sleeves).dropna()


# ── ERC solver ────────────────────────────────────────────────────────────────
def erc_weights(cov: np.ndarray, w_prev: np.ndarray | None = None,
                max_w: float = MAX_SLEEVE) -> np.ndarray:
    """Solve Equal-Risk-Contribution weights via convex-ish quadratic proxy."""
    n = cov.shape[0]
    w0 = w_prev if w_prev is not None else np.ones(n) / n

    def risk_contributions(w):
        sigma_sq = w @ cov @ w
        rc = w * (cov @ w) / np.sqrt(sigma_sq)
        return rc

    def objective(w):
        rc = risk_contributions(w)
        rc_mean = rc.mean()
        return np.sum((rc - rc_mean) ** 2)

    def grad(w):
        from scipy.optimize import approx_fprime
        return approx_fprime(w, objective, 1e-7)

    constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    bounds = [(0.0, max_w)] * n

    result = minimize(
        objective, w0,
        method="SLSQP",
        jac=grad,
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 1000},
    )
    w = result.x
    # Normalise (numerical drift)
    w = np.clip(w, 0.0, max_w)
    w /= w.sum()
    return w


# ── Generic backtest engine ───────────────────────────────────────────────────
def backtest(price_df: pd.DataFrame, weight_fn, initial_cap: float = INITIAL_CAP) -> dict:
    """
    Run a monthly-rebalance backtest.

    weight_fn(date, price_df_up_to_date) → dict {ticker: weight}
    Returns daily portfolio value series and trade log.
    """
    rets = price_df.pct_change().dropna()
    monthly_starts = (
        rets.resample("MS").first().index.intersection(rets.index)
    )

    # Merge any month-start date that isn't in the index to the next valid
    actual_rebal = []
    for ms in monthly_starts:
        loc = rets.index.searchsorted(ms)
        if loc < len(rets.index):
            actual_rebal.append(rets.index[loc])
    actual_rebal = sorted(set(actual_rebal))

    portfolio_value = pd.Series(index=rets.index, dtype=float)
    cash = initial_cap
    holdings: dict[str, float] = {}   # ticker → shares (or units)
    weights: dict[str, float] = {}
    last_prices: dict[str, float] = {}

    for i, date in enumerate(rets.index):
        # Mark-to-market
        if holdings:
            port_val = sum(
                holdings[t] * price_df.loc[date, t]
                for t in holdings if t in price_df.columns
            )
            portfolio_value[date] = port_val
        else:
            portfolio_value[date] = cash

        # Rebalance?
        if date in actual_rebal:
            total = portfolio_value[date]
            new_weights = weight_fn(date, price_df.loc[:date])
            if new_weights is None:
                continue
            # Re-allocate
            holdings = {}
            for ticker, w in new_weights.items():
                if ticker not in price_df.columns:
                    continue
                alloc = total * w
                price = price_df.loc[date, ticker]
                if price > 0:
                    holdings[ticker] = alloc / price
            weights = new_weights

    portfolio_value = portfolio_value.dropna()
    return portfolio_value


# ── ERC weight function ───────────────────────────────────────────────────────
_prev_erc_w: np.ndarray | None = None
SLEEVE_ORDER = ["us_momentum", "intl_asia", "bonds", "gold"]

def erc_weight_fn(date: pd.Timestamp, prices_to_date: pd.DataFrame):
    global _prev_erc_w
    rets = prices_to_date.pct_change().dropna()
    if len(rets) < COV_WINDOW + 5:
        return None
    recent = rets.tail(COV_WINDOW)
    cov = recent.cov().values * 252   # annualised
    w = erc_weights(cov, _prev_erc_w)
    _prev_erc_w = w
    return {sl: float(w[i]) for i, sl in enumerate(SLEEVE_ORDER)}


# ── Momentum weight function ──────────────────────────────────────────────────
def momentum_weight_fn(date: pd.Timestamp, prices_to_date: pd.DataFrame):
    if len(prices_to_date) < MOM_LOOKBACK + MOM_SKIP + 5:
        return None
    # 12-1 month momentum: return from 252 days ago to 21 days ago
    p_curr = prices_to_date.iloc[-MOM_SKIP]
    p_prev = prices_to_date.iloc[-(MOM_LOOKBACK)]
    mom = (p_curr / p_prev - 1.0).dropna()
    if mom.empty:
        return None
    top = mom.nlargest(MOM_TOP_N).index.tolist()
    w = 1.0 / len(top)
    return {t: w for t in top}


# ── Performance metrics ───────────────────────────────────────────────────────
def calc_metrics(pv: pd.Series, rf: float = RF_ANNUAL) -> dict:
    if len(pv) < 2:
        return {"cagr": 0.0, "sharpe": 0.0, "maxdd": 0.0}

    years = (pv.index[-1] - pv.index[0]).days / 365.25
    cagr  = (pv.iloc[-1] / pv.iloc[0]) ** (1.0 / years) - 1.0

    daily_rets = pv.pct_change().dropna()
    excess = daily_rets - rf / 252
    sharpe = (excess.mean() / excess.std()) * np.sqrt(252) if excess.std() > 0 else 0.0

    roll_max = pv.cummax()
    dd = (pv - roll_max) / roll_max
    maxdd = dd.min()

    return {
        "cagr":  round(float(cagr)  * 100, 2),
        "sharpe": round(float(sharpe), 3),
        "maxdd":  round(float(maxdd) * 100, 2),
    }


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Downloading price data …")
    all_tickers = ["MTUM", "EFA", "EEM", "TLT", "GLD"] + MOM_UNIVERSE
    raw = download_prices(all_tickers, START, END)

    # Drop tickers that failed
    raw = raw.dropna(axis=1, how="all")
    available = raw.columns.tolist()
    print(f"Available tickers: {available}")

    # ── ERC backtest ──────────────────────────────────────────────────────────
    sleeve_prices = build_sleeve_prices(raw)
    print(f"\nERC sleeve price matrix: {sleeve_prices.shape[0]} days, "
          f"{sleeve_prices.shape[1]} sleeves")

    global _prev_erc_w
    _prev_erc_w = None   # reset warmup state

    pv_erc = backtest(sleeve_prices, erc_weight_fn)
    metrics_erc = calc_metrics(pv_erc)
    print(f"\nERC results: {metrics_erc}")

    # ── Momentum backtest ─────────────────────────────────────────────────────
    mom_tickers = [t for t in MOM_UNIVERSE if t in available]
    mom_prices  = raw[mom_tickers]

    pv_mom = backtest(mom_prices, momentum_weight_fn)
    metrics_mom = calc_metrics(pv_mom)
    print(f"Momentum results: {metrics_mom}")

    # ── Summary ───────────────────────────────────────────────────────────────
    # Both maxdd values are negative; more negative = deeper drawdown.
    # dd_depth_erc > dd_depth_mom means ERC draws down less (shallower).
    sharpe_delta    = metrics_erc["sharpe"] - metrics_mom["sharpe"]
    cagr_delta      = metrics_erc["cagr"]   - metrics_mom["cagr"]
    dd_depth_erc    = abs(metrics_erc["maxdd"])
    dd_depth_mom    = abs(metrics_mom["maxdd"])
    dd_delta_abs    = dd_depth_erc - dd_depth_mom   # positive = ERC draws down MORE

    erc_dd_direction = "deeper" if dd_delta_abs > 0 else "shallower"
    note_2022 = (
        " NOTE: 2021-2026 period included the 2022 rate-shock that caused TLT to fall ~40%,"
        " heavily penalising the bonds sleeve and pushing ERC drawdown beyond the momentum benchmark."
        if dd_delta_abs > 0 else ""
    )

    summary = (
        f"ERC CAGR {metrics_erc['cagr']:.1f}% Sharpe {metrics_erc['sharpe']:.2f} MaxDD {metrics_erc['maxdd']:.1f}% | "
        f"Momentum CAGR {metrics_mom['cagr']:.1f}% Sharpe {metrics_mom['sharpe']:.2f} MaxDD {metrics_mom['maxdd']:.1f}%. "
        f"ERC vs Momentum: CAGR {cagr_delta:+.1f}pp, Sharpe {sharpe_delta:+.2f}, "
        f"MaxDD {abs(dd_delta_abs):.1f}pp {erc_dd_direction} for ERC."
        f"{note_2022}"
    )

    result = {
        "task":     "risk_parity",
        "backtest_period": f"{pv_erc.index[0].date()} to {pv_erc.index[-1].date()}",
        "erc": metrics_erc,
        "momentum": metrics_mom,
        "summary": summary,
        "success": True,
    }

    out_path = "/Users/quarki3/swarm_results/completed/risk_parity.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\nResults written → {out_path}")
    print(f"\nSUMMARY:\n{summary}")
    return result


if __name__ == "__main__":
    main()
