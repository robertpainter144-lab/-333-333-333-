---
name: Tailscale CLI socket on brew Mac
description: Brew tailscale puts socket under /opt/homebrew/var/run/, CLI defaults to /var/run/ — surprises every session
type: reference
originSessionId: 5b2eeb1a-9333-4444-9e94-f675f794190c
---
On this Mac, `tailscaled` is installed via brew and the socket lives at
`/opt/homebrew/var/run/tailscaled.socket`, but the `tailscale` CLI defaults
to `/var/run/tailscaled.socket` and prints a confusing "not running?" error
even when the daemon is healthy.

To talk to the daemon, either:

```bash
/opt/homebrew/opt/tailscale/bin/tailscale --socket=/opt/homebrew/var/run/tailscaled.socket status
```

or set this shell alias once:

```bash
alias tailscale='/opt/homebrew/opt/tailscale/bin/tailscale --socket=/opt/homebrew/var/run/tailscaled.socket'
```

Tailnet IPs as of 2026-06-16:
- `quarki3s-macbook-air` → `100.71.5.12` (online)
- `roberts-z-flip4` → `100.126.150.36` (Android; reconnect required if offline)

User: robert.m.painter@gmail.com, tailnet `tail33de20.ts.net`.
