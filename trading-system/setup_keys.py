#!/usr/bin/env python3
"""
SECRETS SCRUBBED for the mirror repo — real values live in macOS Keychain
(service: alpaca_trading) and in the original ~/trading-system/setup_keys.py.

One-time script to store Alpaca API keys in macOS Keychain.
Run once: python3.10 setup_keys.py
Keys are then loaded at runtime — nothing stored in source files.
"""
import keyring

SERVICE = "alpaca_trading"

keys = {
    "broker_key":    'FILL_ME_IN',
    "broker_secret": 'FILL_ME_IN',
    "broker_acct":   'FILL_ME_IN',
    "paper_key":     'FILL_ME_IN',
    "paper_secret":  'FILL_ME_IN',
}

for name, value in keys.items():
    keyring.set_password(SERVICE, name, value)
    print(f"  ✓ stored {name}")

print("\nAll keys saved to macOS Keychain under service 'alpaca_trading'.")
print("You can verify in Keychain Access.app — search for 'alpaca_trading'.")
