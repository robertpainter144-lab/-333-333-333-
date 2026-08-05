---
name: Trading Android App
description: Kotlin/Compose Android client for the trading dashboard backend; build setup, JDK quirk, APK staging
type: project
originSessionId: 6d2c613e-6afd-47a4-b3e0-4fe438229e25
modified: 2026-07-30T00:00:39.950Z
---
Native Android app at `/Users/quarki3/trading_android/` that talks to the trading dashboard backend.

**Stack:** Kotlin 2.1.0 + Jetpack Compose (BOM 2024.12.01), Material3, Navigation Compose, Retrofit + kotlinx.serialization, OkHttp, DataStore for settings. Min SDK 26, target/compile SDK 35.

**Screens:** PortfolioScreen, ActivityScreen, BotsScreen, ChartsScreen, WhalesScreen, QcScreen, StrategiesScreen, InsidersScreen, PatentCliffScreen, RealtimeEquityChart, SystemCard, RegimeOverlapCard (under `ui/screens/`). LabScreen + BookScreen live in `ui/` directly.

**Two-realm nav (v1.24, 2026-07-03):** Bottom nav collapsed from 9 tabs to 2 realms — **Book** (Live: IBKR + Alpaca, default home) and **Lab** (Paper: whale/QC/strategies/bots/patent/insiders/charts/activity, deep-dive on tap). Realm switch uses AnimatedContent horizontal slide. `RegimeOverlapCard` at top of Lab is a hardcoded pairwise confluence detector (whales + qc for now) — seed of a QC-ready alpha combiner. `SystemCard` composable enforces shared visual grammar (state pill, blinking dot when emitting, in/out signal-bus labels).

**Build:** `JAVA_HOME=/opt/homebrew/opt/openjdk@21 ./gradlew assembleDebug|assembleRelease`. Gradle 8.11.1.

**Why JDK 21 specifically:** Gradle 8.11.1's bundled Kotlin compiler cannot parse JDK 26's version string ("26.0.1") — fails with `IllegalArgumentException: 26.0.1` from `JavaVersion.parse`. Brew-installed `openjdk` (default) is 26, so always set `JAVA_HOME=/opt/homebrew/opt/openjdk@21` for this project.

**How to apply:** If `./gradlew` fails with cryptic "What went wrong: 26.0.1", that's the JDK mismatch — set JAVA_HOME and retry.

**Keystore:** `/Users/quarki3/trading_android/keystore/trading.jks` (alias `trading`, pw `changeit`, self-signed, 100yr validity). Release builds sign with v2 scheme.

**Default backend:** `http://100.71.5.12:8080` (Mac tailnet IP) baked in as BuildConfig.DEFAULT_BACKEND; user can override at runtime in Settings.

**Built APKs staged at:** `/Users/quarki3/trading_android/dist/trading-1.0.apk` through `trading-1.24.apk` (each release, ~12MB). Old APKs kept for practical rollback — sideload any older version if a new build misbehaves.

**Filesystem backup:** `/Users/quarki3/trading_android_backups/trading_android_pre_realm_20260702_123500.tgz` — source snapshot including keystore (which is NOT git-mirrored).

**Git sync:** No standalone repo. Source is rsync-mirrored every 60s into `trading-system/trading_android/` via `com.quarki3.trading-sync` launchd job → auto-committed and pushed to `github.com/Quarki33/trading-system`. Excludes: `build/`, `.gradle/`, APKs, keystore, `local.properties`.

**Install path:** `adb install dist/trading-X.Y.apk` once phone is connected with USB debugging on, or sideload via file transfer to the Android tailnet device.

## v1.33 (2026-07-28)

Lab SystemCards gained dossier blurbs (plain-language what-it-does) + "tap
for details ›" affordance — parity with the Quarki333 Terminal web Lab.
DEFAULT_BACKEND fixed: Mac tailnet hostname → VM tailnet `100.73.225.4:8080`
(post-migration; runtime Settings override still wins). Build recipe that
works: `JAVA_HOME=/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
./gradlew assembleRelease` (brew openjdk@21; no system JDK exists), release
keystore configured in build.gradle.kts, then `adb install -r
dist/trading-1.33.apk`. Robert's device: `R5CT72VRSLJ` (Samsung, USB).

## v1.34 (2026-07-29) — Catalog tab + OTA distribution fix

Added a 3rd bottom-nav Realm **Catalog** (`ui/screens/CatalogScreen.kt`, icon `Leaderboard`) — OWNER-PRIVATE Autopilot strategy-catalog benchmarks vs QuantConnect (see [[autopilot-actively-managed-mirror]], [[feedback-post-linus-private]]). Consumes owner-only `/api/catalog` (pilots + curves + qc_reference). New DTOs in Models.kt: `CatalogDto`/`EdgeStrategyDto`/`CatalogCurveDto`; `TradingApi.catalog()`; ViewModel `CatalogState`/`refreshCatalog()` (30s poll). Cards ranked by total P/L, QC reference card on top, verdict pills, tap-to-expand holdings.

**OTA distribution was broken by the Oracle migration — fixed 2026-07-29:**
- `publish.sh` targeted the DEAD Hetzner box (`5.78.111.168`) → `scp: Connection closed`. Repointed to Oracle (`ubuntu@129.153.96.201`, `-i ~/.ssh/oracle_trading`), url→`http://100.73.225.4:8080/api/apk`.
- **NiceGUI static mounts are ALL broken on the Oracle deploy**: `/static`, `/dist`, and `/terminal/<file>` subpaths return the NiceGUI catch-all HTML (200/404 `text/html`, ~12.5KB) instead of files. Caddy only reverse-proxies `:8080` — no separate static serving. So the old `/dist/xxx.apk` OTA url can't work.
- **FIX pattern:** serve the APK via a FastAPI **route** not a static mount — added `@fastapi_app.get("/api/apk")` (FileResponse) in `ng_dashboard/api.py`. Routes work where static mounts don't. Manifest at `/opt/trading/dist/manifest.json` (read by `/api/version`), url points at `/api/apk`. Verified end-to-end: version→200, apk download→200 12.6MB, sha256 matches.
- **OTA files live at `~/trading-dist/`** (`trading-latest.apk` + `manifest.json`) on the VM — the fallback both `/api/version` and `/api/apk` check. Do NOT use `/opt/trading` (see gotcha).
- **CRITICAL GOTCHA (hit + fully reverted 2026-07-29): never create `/opt/trading` on the VM.** `api.py` sets `TRADING_DATA_DIR = /opt/trading if it EXISTS else ~/trading-data`. Merely creating `/opt/trading/dist` made `/opt/trading` "exist" → flipped the ENTIRE data dir from `~/trading-data` (real: owner key `cf62…`, guest `7a0d…`, insider_bot, qc_portfolio, portfolio_peaks) to an empty `/opt/trading`, where the dashboard auto-generated throwaway keys → Robert's owner key `cf62…` rejected as unauthorized on the public gate. (Doing the mkdir with sudo ALSO made it root-owned → couldn't read dashboard_keys.json → whole `/api/*` mount threw.) Fix: `sudo rm -rf /opt/trading` (reverts TRADING_DATA_DIR to ~/trading-data), OTA files moved to `~/trading-dist`. The real dashboard data dir on the VM is `~/trading-data`; `/opt/trading` is a Hetzner-era path that must not exist here.
- Phone must reach tailnet `100.73.225.4:8080`. Sideload fallback: `~/trading_android/dist/trading-1.34.apk`, `adb install -r`.

## v1.35 (2026-07-29 eve)

Catalog cards show per-pilot dossier blurbs (Lab parity) from the new `meta`
map on /api/catalog (CatalogMetaDto: blurb/source/note — blurb always visible,
note in expanded drawer). OTA published to ~/trading-dist (manifest v35) +
adb-installed. **publish.sh fixed:** DIST_REMOTE was still /opt/trading/dist —
running it would have re-created the /opt/trading data-dir hijack; now
~/trading-dist. NOTE: publish.sh auto-bumps versionCode — if version was
already bumped by hand, publish manually (scp APK + manifest) instead.
Mac trading-sync launchd job was dead since the VM migration (repo 2d stale);
reloaded with legacy `launchctl load -w` (bootstrap gave I/O error).

## Quarki Terminal wrapper app (learned 2026-07-28)

Robert's daily "Quarki3 Terminal" Android app is NOT trading_android — it's a
separate thin WebView wrapper: `com.quarki3.terminal`, source at
`~/quarki-terminal-android/`, debuggable build, loads
`https://129-153-96-201.sslip.io/terminal` (long-press 1.5s or menu button =
URL dialog; swipe-down = reload). Staleness root cause: WebView HTTP cache
held old SPA bundles. Fixed 2026-07-28: server now sends cache-control:
no-cache on the shell, and the phone's stale cache was cleared once via
`adb shell run-as com.quarki3.terminal rm -rf "cache/WebView/Default/HTTP Cache"`
(localStorage key survives). trading_android (Kotlin UI) is the OTHER app.

Update (same day, later): cache-dir deletion alone did NOT fix staleness —
the WebView still resurrected an ancient bundle (index-BEK6GeZK). Definitive
fix: attach CDP over `adb forward tcp:9223
localabstract:webview_devtools_remote_<pid>` and issue `Page.reload
{ignoreCache:true}` (raw websocket; connectOverCDP doesn't work on WebView).
After that the no-cache-stamped entry persists and launches revalidate.
Wrapper v1.1 shipped: launcher label renamed "Quarki333 Terminal";
repo quarki-terminal-android has a GitHub remote (pushed). Robert's wrapper
points at tailnet URL http://oracle-trader.tail33de20.ts.net:8080/terminal
(oracle-trader = the trading VM, direct :8080, owner-mode via no-XFF).
