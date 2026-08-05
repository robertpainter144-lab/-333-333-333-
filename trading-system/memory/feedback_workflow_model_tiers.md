---
name: feedback-workflow-model-tiers
description: "D27: workflow/subagent stages must pin cheap model tiers (haiku mechanical, sonnet analysis); session model only for hard judge/verify"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: dcc2b6f7-a6b1-4e33-80ab-a6e450055134
  modified: 2026-08-03T00:19:19.099Z
---

2026-08-02: Robert approved tier-defaulting after /usage showed 65% of all
burn was `workflow-subagent` and 77% occurred with 4+ parallel sessions.

**Why:** Fable/Opus cost ~5x Sonnet and ~25x Haiku per unit in the [[project-consumption-manager]] weighting; workflow stages inherit the session model unless overridden, so sweeps silently ran everything at the most expensive tier.

**How to apply:** In every Workflow `agent()` call and Agent-tool spawn, pass `model`/`effort` explicitly — `haiku` + `effort:'low'` for mechanical stages (grep/fetch/transcribe), `sonnet` for standard analysis, session model only for judge/verify/synthesis that genuinely needs it (prefer sonnet even there). Canonical rule in `~/.claude/CLAUDE.md` (D27); ledger entry in trading-stack/DIRECTIVES.md.
