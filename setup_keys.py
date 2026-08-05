#!/usr/bin/env python3
"""
One-time script to store Alpaca API keys in macOS Keychain.
Run once: python3.10 setup_keys.py
Keys are then loaded at runtime — nothing stored in source files.
"""
import keyring

SERVICE = "alpaca_trading"

keys = {
    "broker_key":    "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV",
    "broker_secret": "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV",
    "broker_acct":   "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV",
    "paper_key":     "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV",
    "paper_secret":  "REDACTED_LOADS_FROM_KEYCHAIN_OR_ENV",
}

for name, value in keys.items():
    keyring.set_password(SERVICE, name, value)
    print(f"  ✓ stored {name}")

print("\nAll keys saved to macOS Keychain under service 'alpaca_trading'.")
print("You can verify in Keychain Access.app — search for 'alpaca_trading'.")
