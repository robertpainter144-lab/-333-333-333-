"""
Broker abstraction layer.

Set ACTIVE_BROKER in config.py:
  "alpaca" → Alpaca paper/sandbox (default)
  "ibkr"   → Interactive Brokers live via IB Gateway / TWS

All callers import this module as `md` instead of `market_data` directly.
"""
from config import ACTIVE_BROKER

if ACTIVE_BROKER == "ibkr":
    from ibkr_data import (
        get_account,
        get_positions,
        get_orders,
        place_order,
        close_all_positions,
        get_close_prices,
        get_current_prices,
        get_ohlcv,
        portfolio_history,
    )
else:
    from market_data import (
        get_account,
        get_positions,
        get_orders,
        place_order,
        close_all_positions,
        get_close_prices,
        get_current_prices,
        get_ohlcv,
        portfolio_history,
    )
