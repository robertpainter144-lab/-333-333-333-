# Working the project from Android (Mac off)

The entire stack + Claude Code + your Claude memory/global rules live on the
GCP VM **quarki-stack** (e2-micro, project `astute-pride-206918`, zone
`us-central1-a`, user `ubuntu`, stack at `/home/ubuntu/lab-stack` — a clone of
the lab repo; deployed 2026-08-09). The old Oracle VM (129.153.96.201) is DEAD
since 2026-08-06 — do not use it. Working mobile = opening a Claude session ON
quarki-stack from the phone. The Mac is not involved.

Addresses:
- **Tailnet IP (stable, preferred): `100.94.213.49`**
- Public IP `34.66.251.16` is EPHEMERAL — it changes if the VM is ever
  stopped/started. Don't hardcode anything against it.

## One-time setup

1. Install **Termux** from F-Droid (the Play Store build is outdated).
2. In Termux:
   ```
   pkg update && pkg install openssh tmux rsync
   ```
3. Have the **Tailscale app** on the phone connected (device
   `roberts-z-flip4`). `road_prep.sh` (already run on the VM) enabled
   Tailscale SSH on quarki-stack, so the phone authenticates by tailnet
   identity — **no key needed**.
4. Test: `ssh ubuntu@100.94.213.49 hostname` → should print `quarki-stack`.
5. Claude auth on the VM (once): run `claude setup-token` inside the ssh
   session — it's interactive; open the URL it prints in any browser, paste
   the code back.

## Daily use — just run the helper

Termux has a helper script `~/mac` (installed over adb 2026-08-09) that tries
each host in order and lands you in the persistent tmux session **inside the
333 repo checkout** on whichever host answers:

1. quarki-stack tailnet SSH → starts in `/home/ubuntu/lab-stack`
2. quarki-stack public IP + `~/.ssh/oracle_trading` key → same
3. Mac user-sshd `100.71.5.12:2222` → starts in `/Users/quarki3/lab333/upstream333`

```
./mac
claude
```

(The latest version of the script also sits at `/sdcard/Download/mac333`; to
refresh: `cp /storage/emulated/0/Download/mac333 mac && chmod 700 mac`.)

## Primary path — tailnet SSH, no key

```
ssh -t ubuntu@100.94.213.49 'tmux new -A -s mobile'
claude
```

> **Status 2026-08-09 18:20 MDT:** tailnet port 22 on 100.94.213.49 was
> CLOSED when verified from the Mac (tailscale ping pongs, ssh times out) —
> Tailscale SSH is not serving yet. Until that's fixed, the helper falls
> through to the public-IP+key path automatically.

- `tmux new -A -s mobile` attaches your persistent session (creates it the
  first time). Claude keeps its conversation across phone disconnects,
  network drops, screen off — reattach and continue.
- Detach without killing anything: `Ctrl-b` then `d`.
- The VM session loads the same global rules (~/.claude/CLAUDE.md) and memory
  — copied over by `road_prep.sh` (bundle Taildropped from the Mac
  2026-08-09). The Mac remains memory-canonical and re-syncs whenever it's
  next on.

## Fallback: key-based SSH

The phone's existing key from the 2026-08-04 setup (`~/.ssh/oracle_trading`
in Termux) is also authorized on quarki-stack by `road_prep.sh`:

```
ssh -t -i ~/.ssh/oracle_trading ubuntu@100.94.213.49 'tmux new -A -s mobile'
```

## Fallback 2: Mac direct (primary host, port 2222)

The Mac (100.71.5.12) runs the six interactive Claude windows and is
memory-canonical, so a mobile session there continues "these" sessions most
directly. A user-level sshd (no root) is staged: config
`~/.ssh/mobile_sshd_config`, launchd agent
`~/Library/LaunchAgents/com.quarki3.mobile-sshd.plist` (auto-loads at next
login), pubkey-only, bound to the tailnet IP, and the phone's
`oracle_trading` key is authorized. To arm it immediately without a reboot,
run on the Mac:

```
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.quarki3.mobile-sshd.plist
```

Then from Termux: `./mac` (it probes :2222 as its third target), or directly

```
ssh -t -p 2222 -i ~/.ssh/oracle_trading quarki3@100.71.5.12 'tmux new -A -s mobile -c /Users/quarki3/lab333/upstream333'
```

## Break-glass: Cloud Shell (browser)

If the tailnet is down, use Google Cloud Shell from any browser
(ide.cloud.google.com or the GCP console):

```
gcloud compute ssh quarki-stack --zone=us-central1-a
```

## Alternative: claude.ai app / browser

Inside the tmux session run `claude --remote-control` — the session then
appears in claude.ai/code, and you can drive it from the Claude app or any
phone browser with a nicer UI than Termux. Termux+tmux is the reliable
baseline; remote-control is the comfortable layer on top.

## What runs where (post 2026-08-09)

- quarki-stack (Mac can be off): the whole stack — systemd quarki-*
  services/timers — plus Claude CLI + memory.
- Mac only (correctly pauses when Mac is off): consumption meter, session
  governor, request harvester, 15-min lab-repo autopush. None of these are
  needed for the stack to trade/monitor/notify — per D38 the VM pushes
  directly to the lab repo when the Mac is off.
