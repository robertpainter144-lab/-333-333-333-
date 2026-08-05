# lab333-api

From-scratch owner-only backend for the Terminal 333 app
(`terminal333-android/`). No code shared with ng_dashboard or any earlier
dashboard — fresh FastAPI implementation under the lab333 identity.

Serves the endpoints the app consumes: `/api/health`, `/api/sources`,
`/api/equity/{source}`, `/api/portfolio/{source}`, `/api/activity[...]`,
`/api/summary`, and the `/ws/equity/{source}` push stream. Screens not yet
backed by the lab333 stack get empty-but-valid payloads so the app renders
"no data" instead of erroring.

Data comes from the broker logger files (`equity.csv`,
`equity_history.csv`, `positions_snapshot.json`, `transactions.csv`);
override paths with `LAB333_*` env vars (see the top of `main.py`).

```sh
pip install -r requirements.txt
uvicorn main:app --host <tailnet-ip> --port 8334
```

Trust model: bind to the tailnet or localhost only — same owner-only
posture as the rest of the stack. Point the app's server setting at
`http://<tailnet-ip>:8334`.
