from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="")

    companion_api_key: str

    # IBKR via ibeam (free, family member uses their own IBKR account).
    ibeam_base_url: str = "https://ibeam:5000"

    # Alpaca paper (free, family member uses their own keys).
    alpaca_paper_key: str | None = None
    alpaca_paper_secret: str | None = None

    # Regime-signal feed written by the research repo.
    signals_path: Path = Path("./signals/regimes.json")

    # Risk rails for the /orders endpoint.
    per_order_max_notional_usd: float = 100.0
    daily_notional_cap_usd: float = 200.0
    # MKT orders bypass the notional cap (no known price at submit). Default-deny.
    block_market_orders: bool = True
    # "paper" = validate + log but do not forward to ibeam. "live" = forward.
    trading_mode: str = "paper"

    # Persisted local state.
    data_path: Path = Path("./data")


settings = Settings()
