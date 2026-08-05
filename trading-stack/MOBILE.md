# Working the project from Android (Mac off)

The entire stack + Claude Code + your Claude memory/global rules live on the
Oracle VM (129.153.96.201). Working mobile = opening a Claude session ON the
VM from the phone. The Mac is not involved.

## One-time setup (~5 min, do while near the Mac once)

1. Install **Termux** from F-Droid (the Play Store build is outdated).
2. In Termux:
   ```
   pkg update && pkg install openssh tmux rsync
   mkdir -p ~/.ssh && chmod 700 ~/.ssh
   ```
3. Get the VM key onto the phone (pick one):
   - Easiest (Mac on, Remote Login enabled, phone on tailnet):
     `scp quarki3@<mac-tailnet-ip>:.ssh/oracle_trading ~/.ssh/`
   - Or Taildrop the file `~/.ssh/oracle_trading` from Mac to phone, then move
     it into Termux's `~/.ssh/` (`termux-setup-storage` first).
   Then: `chmod 600 ~/.ssh/oracle_trading`
4. Test: `ssh -i ~/.ssh/oracle_trading ubuntu@129.153.96.201 hostname`
   (works from anywhere — public IP, no tailnet needed for the VM).

## Daily use

```
ssh -t -i ~/.ssh/oracle_trading ubuntu@129.153.96.201 'tmux new -A -s mobile'
claude
```

- `tmux new -A -s mobile` attaches your persistent session (creates it the
  first time). Claude keeps its conversation across phone disconnects,
  network drops, screen off — reattach and continue.
- Detach without killing anything: `Ctrl-b` then `d`.
- The VM session loads the same global rules (~/.claude/CLAUDE.md) and memory
  (synced from the Mac 2026-08-03; the Mac re-syncs whenever it's on).
- Claude auth on the VM: ~/.claude/.credentials.json (auto-refreshes). If it
  ever expires for good: `claude setup-token` inside the ssh session.

## Alternative: claude.ai app / browser

Inside the tmux session run `claude --remote-control` — the session then
appears in claude.ai/code, and you can drive it from the Claude app or any
phone browser with a nicer UI than Termux. Termux+tmux is the reliable
baseline; remote-control is the comfortable layer on top.

## What runs where (post 2026-08-03)

- VM (Mac can be off): entire trading stack (quarki-* services/timers),
  sub-$5 lane, overnight-drift lane, directive auditor, improvement bot
  (quarki-improve.timer), Claude CLI + memory.
- Mac only (correctly pauses when Mac is off): consumption meter, session
  governor, request harvester, trading-sync/GitHub backup. None of these are
  needed for the stack to trade/monitor/notify.
