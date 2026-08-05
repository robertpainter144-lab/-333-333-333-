---
name: feedback-post-linus-private
description: "Hard boundary — all work built after sharing the iPhone app with Linus is strictly private (owner API + Android app only), never the shared Terminal/MVYTraders"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b6e4441c-9733-4539-a311-c09841a4ff22
  modified: 2026-07-29T23:03:30.949Z
---

**Directive (Robert, 2026-07-29):** "anything after the sharing of the iphone app with linus is strictly private and should be kept on my side with the android app."

The iPhone/Terminal app was shared with intern Linus on 2026-07-28 (see [[project-github-sharing]]). Everything built AFTER that share is strictly private:
- It must NOT appear in the shared `trading-gui` Terminal frontend (that source mirrors to MVYTraders, which Linus has) — not in the source, not in the built dist, not in git history.
- Its DATA must be owner-gated in `ng_dashboard/api.py` (private repo, not mirrored): put private features on their own endpoint that is NOT in the guest allowlist (`_GUEST_PATHS`), so guest keys 403 and only the owner key + direct localhost/tailnet reach it.
- Robert's private viewing surfaces for this work are (a) his **Android app** (Kotlin/Compose client, see [[project-trading-android]]) and (b) **his owner-mode Quarki333 Terminal** (wrapper app → tailnet URL, no XFF). **CORRECTION 2026-07-29 (Robert was annoyed this was misread):** when Robert says "my private GUI" he means the Quarki333 Terminal he uses daily — private features MUST also show up THERE, not only in the Kotlin app. The compliant pattern (shipped 2026-07-29): serve the feature's UI from ng_dashboard (private repo) — `_SPAStatic` injects `/owner/catalog.js` into the SPA shell ONLY for no-XFF owner-mode requests; the bundle route 403s guests. trading-gui source/dist/history stay clean.

**Why:** the Terminal is the shared collaboration surface with Linus (an intern being evaluated); Robert's own year-plus of IP and any new private strategy work belongs on his side only.

**How to apply:** before adding any post-2026-07-28 feature to the GUI, ask "does this go through trading-gui (shared) or the Android app (private)?" Private → owner-only API endpoint + Android view. If something private already leaked into trading-gui/MVYTraders, revert the source, rebuild the dist, AND scrub git history (soft-reset to the last clean commit + force-push), because Linus can read repo history. Reference implementation: the Autopilot catalog → `/api/catalog` (owner-only), stripped from the shared `/api/edge`. Related: [[autopilot-actively-managed-mirror]], [[feedback-github-sharing-lockdown]].
