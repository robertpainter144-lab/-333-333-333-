# Ecosystem isolation rules

This repo (`robertpainter144-lab/-333-333-333-`) is the owner-only mirror of the
full trading stack. It is completely separated from the original ecosystem
(the Quarki33 account, MVYTraders, and the iPhone/Terminal app shared with
Linus and dad in its frozen state).

## Hard rules

1. **One-way street.** Content may be copied FROM the old stack INTO this repo.
   Nothing created here is ever pushed, referenced, or mirrored back to any
   Quarki33 repo, MVYTraders, trading-gui dist, or any guest-reachable surface.
2. **Identity separation.** All commits here use the
   `robertpainter144-lab` noreply identity. Never push with Quarki33
   credentials or gh CLI auth; this repo uses its own scoped credential store
   (`~/lab333/secrets/git-credentials`). Quarki33 is never added as a
   collaborator here, and robertpainter144-lab is never added to any old repo.
3. **Fresh history.** This repo starts from an orphan initial commit. Old git
   histories (which carry Quarki33 author emails and pre-scrub artifacts) stay
   on the old private repos and local machines only.
4. **No guests, no invites.** No collaborators on this repo, ever, without
   explicit owner instruction (extends D3).
5. **Secrets stay out.** Live keys (Alpaca, IBKR, ntfy, Dune, Etherscan,
   GitHub PAT, signing keystore) live in `~/lab333/secrets/` and the original
   runtime env files — never in this tree. The Android release keystore is
   `~/lab333/secrets/terminal333.jks` (new key, not the old `trading.jks`).
6. **Frozen guest state.** Linus and dad keep the iPhone app exactly as
   handed over (with the funding it had then). Nothing in this repo changes
   what their guest key can reach; guest surfaces continue to serve sim data
   only (D31).

## Layout

Each top-level directory is a full mirror (working tree, no history) of the
corresponding project: trading-stack, trading-system, scanner, qc_portfolio,
ibkr_bot, whale_tracker, patent_cliff, trading-companion, ng_dashboard,
consumption_manager, trading-gui, trading-data, trading_barbell,
crypto-trading-dashboard — plus **terminal333-android**, the new
owner-only Android app (`com.lab333.terminal`, "Terminal 333"), which exists
only in this stack.
