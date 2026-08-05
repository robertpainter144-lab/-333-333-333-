"""
Combines cliff_scan + first_filer_opportunities + generic_exposure + sec_signals
into a single ranked ticker list, sizes positions, and (optionally) places paper
orders via Alpaca.

Per Robert's "paper for testing, IBKR for deployment" rule:
  - Default mode is `--dry-run` (print picks, place no orders).
  - `--paper`  fires Alpaca paper orders. Safe; reversible.
  - `--live`   IS DISABLED by default. To enable IBKR live trading you must
               pass BOTH `--live` AND `--strategy-proven-on-paper`. The latter
               flag enforces Robert's rule that real money is never for
               experimenting — only deploy after paper proves the strategy.
               Even then, the aggregate IBKR exposure is capped at $20 per the
               feedback_trade_threshold memory.

Run:
    python3.10 portfolio.py                       # dry-run: print picks
    python3.10 portfolio.py --paper               # fire Alpaca paper orders
    python3.10 portfolio.py --paper --budget 5000
    python3.10 portfolio.py --top 8 --paper
    python3.10 portfolio.py --live --strategy-proven-on-paper   # gated, capped
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
SCORING_STATUS_PATH = OUTPUT / "scoring_status.json"

IBKR_LIVE_AGGREGATE_CAP_USD = 20.0


def _latest(glob: str) -> Path | None:
    files = sorted(OUTPUT.glob(glob))
    return files[-1] if files else None


def _safe_read(path: Path | None) -> pd.DataFrame:
    if not path or not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def load_inputs() -> dict:
    cliff_path = _latest("cliff_scan_*.csv")
    ff_path = _latest("first_filer_opportunities_*.csv")
    exp_path = _latest("generic_exposure_*.csv")
    sec_path = DATA / "sec_signals.csv"
    if not (cliff_path and exp_path):
        raise SystemExit("Missing output/cliff_scan_*.csv or output/generic_exposure_*.csv. Run scan.py first.")
    return {
        "cliff": _safe_read(cliff_path),
        "first_filer": _safe_read(ff_path),
        "exposure": _safe_read(exp_path),
        "sec": _safe_read(sec_path),
    }


def _write_scoring_status(exp: pd.DataFrame) -> None:
    """combined_score is base_score (raw cliff exposure) + catalyst bonuses.
    If every row's catalyst bonus is exactly zero, combined_score degenerates
    to a pure cliff-count ranking without anyone being told — that's what
    happened for weeks while data/sec_signals.csv sat empty. Surface it loudly
    instead of letting picks look catalyst-informed when they aren't."""
    ff_all_zero = bool((exp["first_filer_bonus"] == 0).all())
    sec_all_zero = bool((exp["sec_signal_score"] == 0).all())
    degenerate = ff_all_zero and sec_all_zero
    status = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "degenerate_raw_cliff_ranking": degenerate,
        "first_filer_bonus_all_zero": ff_all_zero,
        "sec_signal_score_all_zero": sec_all_zero,
    }
    if degenerate:
        msg = ("WARNING: first_filer_bonus and sec_signal_score are 0.0 for every "
               "ticker — combined_score is currently just raw cliff exposure "
               "(base_score). Check data/sec_signals.csv (run sec_signals.py with "
               "ANTHROPIC_API_KEY set) and the first-filer input.")
        status["warning"] = msg
        print(f"\n{msg}")
    OUTPUT.mkdir(exist_ok=True)
    SCORING_STATUS_PATH.write_text(json.dumps(status, indent=2))


def build_picks(inputs: dict) -> pd.DataFrame:
    exp = inputs["exposure"].copy()
    if exp.empty:
        return exp

    # Base score = total_cliff_exposure (already cumulative cliff score for each
    # ticker)
    exp = exp.rename(columns={"total_cliff_exposure": "base_score"})

    # First-filer concentration boost — if this ticker is on the first-filer
    # opportunities list as a likely filer, add a multiplicative bonus.
    ff = inputs["first_filer"]
    if not ff.empty:
        # The first_filer CSV doesn't list filer tickers directly; we infer by
        # looking up which tickers in `exposure` show those brand names in their
        # top_brands.
        ff_brands = set(ff["Trade_Name"].dropna().astype(str).str.upper())
        def _ff_overlap(brands_str):
            if not isinstance(brands_str, str): return 0
            brands = {b.strip().upper() for b in brands_str.split(",")}
            return len(brands & ff_brands)
        exp["first_filer_overlap"] = exp["top_brands"].map(_ff_overlap)
        exp["first_filer_bonus"] = exp["first_filer_overlap"].clip(upper=3) * 0.5  # cap bonus
    else:
        exp["first_filer_overlap"] = 0
        exp["first_filer_bonus"] = 0.0

    # SEC signal boost — sum confidence × sentiment over per-ticker signals.
    sec = inputs["sec"]
    if not sec.empty:
        sec = sec.copy()
        # positive=+1, neutral=0, negative=-1
        sent_map = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}
        sec["sent_sign"] = sec["sentiment"].map(sent_map).fillna(0.0)
        sec_agg = (sec.assign(weighted=sec["sent_sign"] * sec["confidence"])
                   .groupby("ticker", as_index=False)
                   .agg(sec_signal_score=("weighted", "sum"),
                        sec_signal_count=("ticker", "size")))
        exp = exp.merge(sec_agg, on="ticker", how="left")
        exp["sec_signal_score"] = exp["sec_signal_score"].fillna(0.0)
        exp["sec_signal_count"] = exp["sec_signal_count"].fillna(0).astype(int)
    else:
        exp["sec_signal_score"] = 0.0
        exp["sec_signal_count"] = 0

    # Combined score — keep simple and inspectable
    exp["combined_score"] = (exp["base_score"]
                             + exp["first_filer_bonus"]
                             + exp["sec_signal_score"])

    _write_scoring_status(exp)

    # Filter out tickers we can't trade easily on Alpaca paper
    # (US-listed only; .NS, .L, .KS, .DE etc. aren't Alpaca-tradable)
    import re
    us_listed = exp["ticker"].astype(str).str.match(r"^[A-Z]{1,5}$")
    exp["us_listed_alpaca_tradable"] = us_listed
    exp = exp.sort_values("combined_score", ascending=False).reset_index(drop=True)
    return exp


def size_positions(picks: pd.DataFrame, top_n: int, budget_usd: float) -> pd.DataFrame:
    tradable = picks[picks["us_listed_alpaca_tradable"]].head(top_n).copy()
    if tradable.empty:
        return tradable
    # Equal-weight against budget. Score-weighting amplifies noise on small N.
    per_ticker_usd = budget_usd / len(tradable)
    tradable["target_usd"] = round(per_ticker_usd, 2)
    return tradable


# ── Alpaca paper trading ──────────────────────────────────────────────

def _alpaca_paper_client():
    from alpaca.trading.client import TradingClient
    return TradingClient(
        api_key=os.environ["ALPACA_PAPER_KEY"],
        secret_key=os.environ["ALPACA_PAPER_SECRET"],
        paper=True,
    )


def fire_alpaca_paper_orders(sized: pd.DataFrame) -> pd.DataFrame:
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.enums import OrderSide, TimeInForce

    client = _alpaca_paper_client()
    acct = client.get_account()
    print(f"\nAlpaca paper account equity ${float(acct.equity):,.2f}  "
          f"cash ${float(acct.cash):,.2f}")

    existing = {p.symbol: float(p.market_value) for p in client.get_all_positions()}
    rows = []
    for _, r in sized.iterrows():
        sym = r["ticker"]
        target = float(r["target_usd"])
        held = existing.get(sym, 0.0)
        delta = target - held
        if delta < 1.0:
            rows.append({**r, "action": "skip_already_at_target", "delta_usd": delta})
            continue
        # Alpaca paper supports fractional via notional
        order = MarketOrderRequest(
            symbol=sym,
            notional=round(delta, 2),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        try:
            resp = client.submit_order(order)
            rows.append({**r, "action": "submitted", "delta_usd": delta,
                         "order_id": str(resp.id), "order_status": str(resp.status)})
        except Exception as e:
            rows.append({**r, "action": "error", "delta_usd": delta, "error": str(e)})
    return pd.DataFrame(rows)


# ── IBKR live (gated, $20 cap) ────────────────────────────────────────

def fire_ibkr_live_orders(sized: pd.DataFrame, proven_on_paper: bool) -> pd.DataFrame:
    if not proven_on_paper:
        raise SystemExit(
            "REFUSED: --live requires --strategy-proven-on-paper. Per the "
            "'paper for testing, IBKR for deployment' rule, real money is never "
            "for experimenting. Run with --paper first and only pass "
            "--strategy-proven-on-paper once the strategy has demonstrated edge "
            "over a meaningful period."
        )
    # Aggregate cap enforcement
    total = float(sized["target_usd"].sum())
    if total > IBKR_LIVE_AGGREGATE_CAP_USD:
        raise SystemExit(
            f"REFUSED: aggregate IBKR exposure ${total:.2f} exceeds the "
            f"hardcoded ${IBKR_LIVE_AGGREGATE_CAP_USD} cap. Reduce --budget or --top "
            f"so the total fits, or update feedback_trade_threshold.md and the "
            f"constant in this file if the cap should change."
        )
    print(f"REFUSED: IBKR live wiring is intentionally not implemented in this "
          f"file. Use the existing ~/ibkr_bot/ entrypoint (with manual approval "
          f"per the 'mother bird' pattern) for live orders.")
    return pd.DataFrame()


def _print_picks(picks: pd.DataFrame, sized: pd.DataFrame, n_show: int = 15):
    print("\n=== Ranked tickers ===")
    cols = ["ticker", "combined_score", "base_score", "first_filer_bonus",
            "sec_signal_score", "sec_signal_count", "cliff_count", "top_brands"]
    show = picks[[c for c in cols if c in picks.columns]].head(n_show).copy()
    for col in ("combined_score", "base_score", "first_filer_bonus", "sec_signal_score"):
        if col in show.columns:
            show[col] = show[col].round(2)
    print(show.to_string(index=False))
    if not sized.empty:
        print("\n=== Sized for trade ===")
        cols2 = ["ticker", "combined_score", "target_usd"]
        s = sized[[c for c in cols2 if c in sized.columns]].copy()
        s["combined_score"] = s["combined_score"].round(2)
        print(s.to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=5, help="Number of top tickers to size")
    ap.add_argument("--budget", type=float, default=1000.0, help="Total notional to deploy")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--paper", action="store_true", help="Fire Alpaca paper orders")
    mode.add_argument("--live", action="store_true", help="GATED: IBKR live (see flags)")
    ap.add_argument("--strategy-proven-on-paper", action="store_true",
                    help="Gate for --live. Asserts paper has shown edge.")
    args = ap.parse_args()

    inputs = load_inputs()
    picks = build_picks(inputs)
    if picks.empty:
        print("No picks available. Run scan.py first.")
        return
    sized = size_positions(picks, args.top, args.budget)
    _print_picks(picks, sized)

    # Save the run for audit
    ts = datetime.now().strftime("%Y-%m-%d_%H%M")
    sized_path = OUTPUT / f"portfolio_picks_{ts}.csv"
    sized.to_csv(sized_path, index=False)
    print(f"\nWrote sized picks to {sized_path}")

    if args.paper:
        result = fire_alpaca_paper_orders(sized)
        result_path = OUTPUT / f"alpaca_orders_{ts}.csv"
        result.to_csv(result_path, index=False)
        print(f"\nAlpaca paper orders → {result_path}")
        print(result[["ticker", "action", "delta_usd"]].to_string(index=False))
    elif args.live:
        fire_ibkr_live_orders(sized, args.strategy_proven_on_paper)
    else:
        print("\n(dry-run — no orders placed; use --paper to fire on Alpaca paper)")


if __name__ == "__main__":
    main()
