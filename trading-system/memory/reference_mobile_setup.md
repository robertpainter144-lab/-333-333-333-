---
name: reference-mobile-setup
description: "Android mobile workflow: Termux+tmux+ssh to VM Claude; phone setup COMPLETE 2026-08-04; full recipe in trading-stack/MOBILE.md"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 6e56e98c-9de9-415f-b8b7-069caa80ac31
  modified: 2026-08-04T19:47:04.635Z
---

Robert works mobile by running Claude Code ON the Oracle VM from Android —
the Mac can be off. Full recipe: `~/trading-stack/MOBILE.md` (Mac + VM copies).

- Phone setup DONE 2026-08-04: Termux (F-Droid) + openssh/tmux installed, VM
  key at Termux `~/.ssh/oracle_trading`, VM host key accepted, login verified.
  Daily use:
  `ssh -t -i ~/.ssh/oracle_trading ubuntu@129.153.96.201 'tmux new -A -s mobile'`
  → `claude`. tmux keeps the session alive across disconnects.
  (Key transfer trick used: Mac Remote Login stayed off; ran Termux `sshd`
  (port 8022) and pushed key Mac→phone with expect/scp.)
- VM has claude 2.1.220 at `~/.local/bin/claude`, OAuth creds at
  `~/.claude/.credentials.json`, global rules at `~/.claude/CLAUDE.md`, and
  the full memory dir synced into BOTH `~/.claude/projects/-home-ubuntu/` and
  `-Users-quarki3/` (2026-08-03 snapshot; Mac is still the memory canonical —
  re-sync when meaningful memories change, and beware divergence if VM
  sessions write memories).
- Alternative UI: `claude --remote-control` inside tmux → drive from
  claude.ai on the phone.
Related: [[project-improvement-bot]], [[project-vm-migration]].
