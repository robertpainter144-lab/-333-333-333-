---
name: feedback-private-gui-meaning
description: "'My private GUI' = owner-mode Quarki333 Terminal (Lovable-spec'd trading-gui SPA via wrapper app), NOT the Kotlin trading_android app; ship private features there via ng_dashboard owner-mode injection"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: b93a4eef-436d-4e05-b714-fd098bc26ae7
  modified: 2026-07-29T23:03:42.867Z
---

**What Robert means by "my private GUI" (2026-07-29):** the **Quarki333 Terminal** — the Lovable-spec'd React SPA (trading-gui) viewed through his WebView wrapper app (`com.quarki3.terminal`) at the tailnet URL, which the backend treats as owner-mode (no X-Forwarded-For). He calls it "the new one we used lovable for." The Kotlin app (trading_android, launcher label "Trading") is the OTHER app and NOT his daily surface.

**Why:** On 2026-07-29 he asked for "today's updates in my private gui" and got pointed at the Kotlin app twice ("you're updating the wrong one"), then said "your context file clearly isn't functioning — make more effort to remember my activity across sessions."

**How to apply:** New private (post-Linus) features must appear in the owner-mode Terminal AND stay out of the shared trading-gui source. Pattern: put the UI in `ng_dashboard/static/*.js` (private repo) + owner-gated route, injected into the SPA shell server-side only for no-XFF requests (reference: `/owner/catalog.js`, see [[feedback-post-linus-private]], [[autopilot-actively-managed-mirror]]). Also: the Mac's `~/ng_dashboard` copy went stale after the VM migration — the VM is source of truth; sync Mac after VM edits or check the VM copy first ([[project_vm_migration]]).
