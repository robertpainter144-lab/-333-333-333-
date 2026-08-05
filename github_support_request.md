# GitHub Support request — purge dangling commits (DRAFT, ready to submit)

**Submit at:** https://support.github.com/contact — sign in as `Quarki33`,
choose "Account or repository" → repository `Quarki33/MVYTraders`.

**Subject:** Please remove two unreachable (dangling) commits from a private repository

**Message:**

> Hello,
>
> I force-pushed the `main` branch of my private repository
> `Quarki33/MVYTraders` to remove two commits that were pushed by mistake,
> but the commit objects are still accessible directly by SHA via the web UI
> and API. Please run garbage collection / remove these unreachable objects
> from the repository so they can no longer be fetched:
>
> - `7ef46f48ec22542ebbca5a443198e5c797a3f5ef`
> - `7730741cccc5e33baed3bba19ff494d10af35b90`
>
> The branch itself is already clean — I only need the dangling objects and
> any cached views of them (including any references in the repository's
> events/activity) purged. I am the repository owner.
>
> Thank you!

**Context (not part of the ticket):** the commits contain trading-gui
Lab.tsx dossier text describing autopilot_cat catalog pilots (names +
descriptions only — no holdings, keys, or backend code). They reached GitHub
via the autopush MVYTraders mirror leg during 17:37–17:57 UTC 2026-07-29 and
were force-push-scrubbed at 17:58. A D1 privacy guard now blocks the mirror
whenever private markers are present in trading-gui, so this cannot recur.
Alternative to a support ticket: delete + recreate the repo (purges instantly
but requires re-inviting Linus, owner's call).
