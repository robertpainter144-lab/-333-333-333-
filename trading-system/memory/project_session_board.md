---
name: project-session-board
description: "D33 cross-session board — parallel Claude windows share a hook-maintained board so each prompt sees the other windows' tasks; coordination rules injected every prompt"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9b60d91d-5bba-4324-99e2-2a82b8dc902d
  modified: 2026-08-04T20:56:30.604Z
---

Shipped 2026-08-04 after Robert reported parallel Claude windows colliding
(shared-file overwrites, stale state, repeated mistakes).

- `~/.claude/hooks/session_board.py` — runs on UserPromptSubmit, Stop, and
  SessionEnd (wired in `~/.claude/settings.json`). Maintains
  `~/.claude/session_board/board.json` (flock-protected, entries pruned after
  48h idle): per-session cwd, status, first/last prompt, prompt count, and
  last assistant result (extracted from the transcript on Stop).
- Every UserPromptSubmit injects a SESSION BOARD block listing the OTHER
  windows active in the last 6h plus coordination rules: re-read shared files
  (memory/, DIRECTIVES.md, CONTEXT.md, settings.json, VM state) immediately
  before editing; a file changing mid-turn is probably another window — never
  blind-revert; don't duplicate/undo a parallel window's task, flag overlap to
  Robert; all windows share one 5h token budget.
- Directive D33 in [[project-directive-auditor]] ledger (Mac-local CHECK — VM
  auditor can't reach it). Ship-log entry in CONTEXT.md same day.

**Obey the injected board**: when the SESSION BOARD block lists other active
windows, treat their in-flight work as authoritative context, not noise.
