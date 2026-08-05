"""
Loads Alpaca API keys from macOS Keychain (set once via setup_keys.py).
Falls back to environment variables so CI / headless environments still work.
"""
import os

_SVC = "alpaca_trading"


def _get(name: str) -> str:
    val = os.environ.get(f"ALPACA_{name.upper()}")
    if val:
        return val
    # NOTE: Keychain access removed deliberately — it caused repeated GUI prompts.
    # Keys now come exclusively from environment variables (set in ~/.zshenv and the
    # launchd plists). Nothing here ever touches the macOS Keychain anymore.
    raise RuntimeError(
        f"Alpaca key '{name}' not found. Set the ALPACA_{name.upper()} environment variable "
        f"(it's defined in ~/.zshenv and the launchd plists)."
    )


BROKER_KEY    = _get("broker_key")
BROKER_SECRET = _get("broker_secret")
BROKER_ACCT   = _get("broker_acct")
PAPER_KEY     = _get("paper_key")
PAPER_SECRET  = _get("paper_secret")
