---
name: project-lab333-mirror
description: Owner-only mirror stack + Terminal 333 Android app on the robertpainter144-lab GitHub account (D35 ecosystem split)
metadata: 
  node_type: memory
  type: project
  originSessionId: 70f09dd0-7d59-4701-8a1e-057ba9499cc2
  modified: 2026-08-05T07:27:53.799Z
---

**D35 ecosystem split (2026-08-04).** Robert's second GitHub account
`robertpainter144-lab` (user id 269261449) holds the private repo
`-333-333-333-` — a complete owner-only mirror of the trading stack, fully
separated from Quarki33 / MVYTraders / the frozen iPhone app Linus (`lmunn1`)
and dad (`edhopa`) have. They must never see any post-handover activity.

- Local repo: `~/lab333/stack` — fresh orphan history, single "Initial mirror"
  commit `3d1d41e`; identity `robertpainter144-lab
  <269261449+robertpainter144-lab@users.noreply.github.com>` (repo-local).
  Remote set to `https://github.com/robertpainter144-lab/-333-333-333-.git`.
- Contents: working-tree mirrors (no old histories) of trading-stack,
  trading-system, scanner, qc_portfolio, ibkr_bot, whale_tracker,
  patent_cliff, trading-companion, ng_dashboard, consumption_manager,
  trading-gui, trading-data, trading_barbell, crypto-trading-dashboard, plus
  `terminal333-android/`.
- **Terminal 333 app**: package `com.lab333.terminal`, exists only in this
  stack; installs alongside the old app; APK `~/lab333/dist/terminal333-v1.0.apk`;
  signing keystore `~/lab333/secrets/terminal333.jks` (random pw in
  `keystore.properties` there); build with JDK 21
  (`/opt/homebrew/opt/openjdk@21`). Default backend unchanged
  (`http://100.73.225.4:8080`, runtime-overridable).
- **Secrets**: `~/trading-system/setup_keys.py` had LIVE Alpaca broker
  key+secret hardcoded — placeholdered in the mirror copy only; NOTE the
  original is also in Quarki33/trading-system on GitHub (pre-existing
  exposure, flagged to Robert). All env/keystore files excluded from mirror.
- **Push auth**: fine-grained PAT from robertpainter144-lab (repo
  `-333-333-333-` only, Contents R/W) at `~/lab333/secrets/github_token`.
  NEVER push with Quarki33 gh auth; never cross-add collaborators.
  **PUSHED 2026-08-05 ~01:27**: `main` (=`3d1d41e`) live on the remote, repo
  private. Gotcha: macOS keychain served the wrong account's credential
  ("Repository not found") — fixed with repo-local `credential.helper ""` +
  `core.askPass ~/lab333/secrets/askpass.sh` (askpass.sh reads the token),
  so plain `git push` from `~/lab333/stack` now works.
- Isolation rules in repo's `ISOLATION.md`; vague D35 entries in
  DIRECTIVES.md/CONTEXT.md (account name kept out of old-ecosystem files).
- **D35a tripwire (2026-08-05)**: hourly `quarki-lab333-watch.timer` on the
  VM anonymously polls the account's public repo list; pages P5 if anything
  ever shows public, silent when clean (verified 404/zero-public at install).
  Related: [[project-github-sharing]], [[feedback-post-linus-private]].
