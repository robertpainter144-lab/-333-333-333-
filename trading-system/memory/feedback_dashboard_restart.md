---
name: Never kill -9 the ng_dashboard
description: SIGKILL on ng_dashboard causes Python SIGABRT + macOS crash-report popup ("reopen this app"). Use launchctl kickstart instead.
type: feedback
originSessionId: c777b35a-8784-4ed8-b477-cb1bcb76d2f3
---
The ng_dashboard on macOS is managed by launchd job `com.quarki3.ng-dashboard`. Killing it with `pkill -9 -f "ng_dashboard/main.py"` (SIGKILL) triggers a Python crash on shutdown because uvloop/NiceGUI worker threads are still holding stdout when the main thread tries to `Py_FinalizeEx` → `_enter_buffered_busy` → SIGABRT → macOS pops a crash-report "Reopen" dialog for Robert to dismiss.

**Why:** Robert had to dismiss multiple crash dialogs during a working session because I kept killing the dashboard hard when I needed it to pick up code changes.

**How to apply:** For any restart of the ng_dashboard, use `launchctl kickstart -k gui/501/com.quarki3.ng-dashboard`. This sends SIGTERM, waits gracefully, then launchd relaunches. Zero crash dialogs.

If TERM alone doesn't work in 5s and I truly need force-kill: use `pkill -f "ng_dashboard/main.py"` (default SIGTERM) first, wait 8s, only then escalate. Same for any FastAPI/uvicorn/NiceGUI process on macOS.
