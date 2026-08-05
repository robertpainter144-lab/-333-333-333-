"""One-time SnapTrade setup for the Autopilot holdings feed.

Prereq: state/autopilot_feed_config.json with
    {"broker": "snaptrade", "client_id": "...", "consumer_key": "..."}
(from the snaptrade.com dashboard, free individual tier).

Run:  python3.10 snaptrade_connect.py
  1. Registers the SnapTrade user (idempotent) and stores user_secret
     into the config file.
  2. Prints a connection-portal URL — open it, pick Robinhood, log in.
     The connection is READ-ONLY; SnapTrade's Robinhood integration
     cannot place trades.
After the portal shows "connected", the autopilot_feed scanner source
starts publishing holdings on its next tick (verify with
  cat state/autopilot_holdings.json).
"""
import json
import sys
import uuid
from pathlib import Path

CONFIG = Path(__file__).parent / "state" / "autopilot_feed_config.json"


def main() -> int:
    try:
        from snaptrade_client import SnapTrade
    except ImportError:
        print("Missing SDK: /opt/homebrew/bin/python3.10 -m pip install snaptrade-python-sdk")
        return 1
    if not CONFIG.exists():
        print(f"Create {CONFIG} first (see docstring).")
        return 1
    cfg = json.loads(CONFIG.read_text())
    if not (cfg.get("client_id") and cfg.get("consumer_key")):
        print("Config needs client_id + consumer_key from the SnapTrade dashboard.")
        return 1

    st = SnapTrade(consumer_key=cfg["consumer_key"], client_id=cfg["client_id"])

    if not cfg.get("user_secret"):
        user_id = cfg.get("user_id") or f"quarki3-{uuid.uuid4().hex[:8]}"
        resp = st.authentication.register_snap_trade_user(body={"userId": user_id})
        cfg["user_id"] = resp.body["userId"]
        cfg["user_secret"] = resp.body["userSecret"]
        CONFIG.write_text(json.dumps(cfg, indent=2))
        print(f"Registered SnapTrade user {cfg['user_id']} (secret saved to config).")
    else:
        print(f"User {cfg['user_id']} already registered — reusing.")

    login = st.authentication.login_snap_trade_user(
        query_params={"userId": cfg["user_id"], "userSecret": cfg["user_secret"]},
        body={"broker": "ROBINHOOD", "connectionType": "read"},
    )
    url = login.body.get("redirectURI") or login.body
    print("\nOpen this URL and log into Robinhood (read-only):\n")
    print(f"  {url}\n")
    print("Then wait for the next autopilot_feed tick (≤30 min) or run:")
    print("  cd ~/scanner && python3.10 -c 'from sources import autopilot_feed; autopilot_feed.tick()'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
