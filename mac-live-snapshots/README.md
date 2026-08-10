# mac-live-snapshots/

Dated safety copies of code that RUNS live on the Mac but is not otherwise
versioned (the Mac's ~/ng_dashboard has no git remote; the lab repo's
ng_dashboard/ is a deliberately DIFFERENT variant — see the name-collision
warning in memory/project_crypto_sleeve.md).

These are DISASTER-RECOVERY copies, not the canonical source. To restore the
Mac server, copy ng_dashboard/* back to ~/ng_dashboard/. Snapshot refreshed by
the orchestration window 2026-08-10 00:25 MDT; refresh manually (or wire into
autopush) whenever the live Mac server changes.
