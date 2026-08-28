#!/usr/bin/env bash
# Idempotent dev-environment bootstrap for the -333-333-333- trading monorepo.
# Sets up a single shared Python virtualenv for the demonstrable Python apps /
# CLIs and installs Node dependencies for the web apps. Safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# The default image ships python3 without ensurepip/venv support.
if ! python3 -c 'import ensurepip' >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3-venv
fi

# Shared virtualenv used by lab333-api, the trading-companion backend, and the
# secret-free Python CLIs (trading_barbell, statarb_harness, patent_cliff).
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet \
  -r lab333-api/requirements.txt \
  -r trading-companion/backend/requirements.txt \
  -r trading_barbell/requirements.txt \
  -r patent_cliff/requirements.txt \
  plotly

# The companion backend reads config from a .env at import time and requires
# COMPANION_API_KEY. Provide a dev key locally (this file is git-ignored). Real
# deployments supply their own secrets.
if [ ! -f trading-companion/backend/.env ]; then
  printf 'COMPANION_API_KEY=dev-local-key\nTRADING_MODE=paper\n' \
    > trading-companion/backend/.env
fi

# Node dependencies for the web apps.
for d in crypto-trading-dashboard trading-gui trading-companion/web; do
  ( cd "$d" && npm install )
done

echo "Dev environment setup complete."
