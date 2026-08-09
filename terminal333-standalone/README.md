# Terminal 333

Owner-only trading terminal under the lab333 identity. Fully separate from
any earlier shared project: fresh git history, `com.lab333.terminal`
packaging, and a from-scratch backend.

## Layout

- `app/` — Kotlin/Compose Android app ("Terminal 333"). Build with JDK 21:

  ```sh
  cd app
  JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home ./gradlew assembleRelease
  ```

- `backend/` — lab333-api, a from-scratch FastAPI backend serving the
  endpoints the app consumes (health, sources, equity, portfolio, activity,
  summary, `/ws/equity/{source}` push stream; owner-only screens not yet
  backed return empty payloads). Reads the broker logger files
  (`equity.csv`, `equity_history.csv`, `positions_snapshot.json`,
  `transactions.csv`); paths overridable via `LAB333_*` env vars.

  ```sh
  cd backend
  pip install -r requirements.txt
  uvicorn main:app --host <tailnet-ip> --port 8334
  ```

  Trust model: bind to the tailnet or localhost only — same owner-only
  posture as the rest of the stack (no public exposure).

Point the app's server setting at `http://<tailnet-ip>:8334`.

Signing keystore + passwords: `~/lab333/secrets/` (never in this repo).
