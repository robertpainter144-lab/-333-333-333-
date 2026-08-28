# mac-live-snapshots/

Dated safety copies of code that RUNS live on the Mac but is not otherwise
versioned (the Mac's ~/ng_dashboard has no git remote; the lab repo's
ng_dashboard/ is a deliberately DIFFERENT variant — see the name-collision
warning in memory/project_crypto_sleeve.md).

These are DISASTER-RECOVERY copies, not the canonical source. To restore the
Mac server, copy ng_dashboard/* back to ~/ng_dashboard/. Snapshot refreshed by
the orchestration window 2026-08-10 00:25 MDT; refresh manually (or wire into
autopush) whenever the live Mac server changes.

**Refresh with one command on the Mac** (added 2026-08-28 after an audit
found this snapshot 18 days behind the live api.py — the live-NAV
reconstruction, funded endpoints, and pip.js?v=7 wiring existed only on
the Mac/GCP disks):

```sh
~/lab333/upstream333/bin/refresh_live_snapshots.sh          # Mac live code
~/lab333/upstream333/bin/refresh_live_snapshots.sh --gcp    # + GCP api.py & funded state
```

The `--gcp` form also snapshots the GCP VM's api.py and the funded
ledger's source of truth (funded.json / funded_events.jsonl) into
`gcp-live-snapshots/`. Wire either form into the autopush loop to make
the backup continuous.
