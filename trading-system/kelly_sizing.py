"""
Fractional-Kelly position sizing utilities.

Kelly criterion: f* = (p * b - q) / b
  where p = win probability, q = 1-p, b = win/loss ratio (e.g. avg_win / avg_loss)

Half-Kelly (fraction=0.5) is the typical production default — it reduces drawdown
significantly while sacrificing only ~25% of geometric growth rate vs full Kelly.
"""


def kelly_fraction(win_rate: float, win_loss_ratio: float, fraction: float = 0.5) -> float:
    """
    Compute a fractional Kelly bet size as a fraction of equity.

    Formula: kelly_f = fraction * (p*b - q) / b
      p = win_rate, q = 1-p, b = win_loss_ratio

    Args:
        win_rate: Probability of a winning trade, e.g. 0.625 for 62.5%.
        win_loss_ratio: Average win / average loss (must be > 0).
        fraction: Kelly multiplier, e.g. 0.5 for half-Kelly (default).

    Returns:
        Kelly fraction clamped to [0.0, 1.0]. Returns 0.0 for edge-negative setups.
    """
    if win_loss_ratio <= 0:
        raise ValueError("win_loss_ratio must be positive")
    if not (0.0 < win_rate < 1.0):
        raise ValueError("win_rate must be strictly between 0 and 1")
    if not (0.0 < fraction <= 1.0):
        raise ValueError("fraction must be in (0, 1]")

    p = win_rate
    q = 1.0 - win_rate
    b = win_loss_ratio

    full_kelly = (p * b - q) / b
    scaled = fraction * full_kelly

    return max(0.0, min(1.0, scaled))


def position_size(
    equity: float,
    kelly_frac: float,
    price: float,
    max_pct: float = 0.20,
) -> int:
    """
    Convert a Kelly fraction to a whole number of shares, capped by a max position size.

    Args:
        equity: Total account equity in dollars.
        kelly_frac: Output of kelly_fraction(), a value in [0, 1].
        price: Current price per share/contract.
        max_pct: Hard cap on single-position size as fraction of equity (default 20%).

    Returns:
        Number of shares (integer, >= 0). Returns 0 if price is zero or kelly_frac is 0.

    Safety caps applied (both must pass):
      1. kelly_frac is already capped at 1.0 inside kelly_fraction().
      2. max_pct overrides kelly_frac if the Kelly suggestion exceeds the hard cap.
    """
    if price <= 0:
        raise ValueError("price must be positive")
    if equity <= 0:
        raise ValueError("equity must be positive")
    if not (0.0 < max_pct <= 1.0):
        raise ValueError("max_pct must be in (0, 1]")

    capped_frac = min(kelly_frac, max_pct)
    dollar_risk = equity * capped_frac
    shares = int(dollar_risk / price)
    return max(0, shares)


if __name__ == "__main__":
    # ES Mini mean-reversion strategy documented stats
    # Source: ES backtest — win_rate ~62.5%, avg_win/avg_loss ~1.3
    WIN_RATE = 0.625
    WIN_LOSS_RATIO = 1.3
    FRACTION = 0.5          # half-Kelly
    EQUITY = 50_000.0       # Alpaca paper account buying power
    ES_PRICE = 5_850.0      # approximate ES Mini price (per contract point equivalent)
    MAX_PCT = 0.20          # never risk more than 20% on one position

    kf = kelly_fraction(WIN_RATE, WIN_LOSS_RATIO, fraction=FRACTION)
    shares = position_size(EQUITY, kf, ES_PRICE, max_pct=MAX_PCT)

    print("=== Fractional-Kelly Position Sizing Demo ===")
    print(f"Strategy stats  : win_rate={WIN_RATE:.1%}, win/loss ratio={WIN_LOSS_RATIO}")
    print(f"Kelly fraction  : {FRACTION}x Kelly = {kf:.4f}  ({kf:.2%} of equity)")
    print(f"Account equity  : ${EQUITY:,.0f}")
    print(f"Instrument price: ${ES_PRICE:,.2f}")
    print(f"Hard cap (max)  : {MAX_PCT:.0%} of equity")
    print(f"Suggested size  : {shares} share(s) / contract(s)")
    print(f"Dollar exposure : ${shares * ES_PRICE:,.2f}  ({shares * ES_PRICE / EQUITY:.2%} of equity)")
