#!/bin/bash
# Mac failover launcher for the trading scanner (self-healing via launchd).
# Reads keys + mode from ~/quarki.env. combined_mirror is in PAPER mode
# (COMBINED_MIRROR_MODE=paper) — refreshes the governor/targets and runs all
# 19 sources for constant activity, but places NO real-money orders. Flip to
# live only on Robert's explicit go.
set -a
[ -f "$HOME/quarki.env" ] && source "$HOME/quarki.env"
set +a
cd "$HOME/scanner" && exec /opt/homebrew/bin/python3 main.py
