---
name: IB Gateway must be running on port 4001
description: How to detect and restart IB Gateway when watcher/equity-logger are crashing on ConnectionRefused
type: reference
originSessionId: d207120d-c6c4-4118-a720-bca459d749a8
---
The IBKR bot's `watcher.py` and `equity_logger.py` connect to `127.0.0.1:4001` via `ib_insync`. If IB Gateway is not running or not logged in, both launchd jobs (`com.quarki3.ibkr-watcher`, `com.quarki3.ibkr-equity-logger`) repeatedly crash with:

```
ConnectionRefusedError: [Errno 61] Connect call failed ('127.0.0.1', 4001)
```

`launchctl list | grep quarki3.ibkr` will show those jobs with PID `-` and exit code `1`.

**Why:** IB Gateway requires interactive login (username/password + 2FA prompt). It cannot be started fully unattended. Discovered 2026-06-26 — Gateway had been offline since ~Jun 23, leaving the live trading bot blind for 3 days.

**How to apply:**
1. Detect: `lsof -nP -iTCP:4001` — if empty, Gateway is not bound.
2. Detect: `ps aux | grep -iE "ibgateway|jts" | grep -v grep` — empty means no process.
3. Restart: `open "/Users/quarki3/Applications/IB Gateway 10.46/IB Gateway 10.46.app"`
4. User must complete login interactively. After login, port 4001 binds and the launchd jobs self-recover on next tick.
5. Verify recovery: tail `/tmp/ibkr_watcher.err` — no new ConnectionRefused lines means it's healthy.

Any session that opens with a "disappointing IBKR results" complaint should first check whether Gateway is up before diagnosing strategy.
