# Trading-Stack Hosting Recommendation — Final

## 0. Where this lands, in one sentence

Every commercial cloud VPS candidate that was adversarially tested was **killed on payment/KYC friction and/or RAM-vs-price arithmetic** — none survived cleanly. The only thing that survived verification is the phone, and only in a **secondary, non-critical role**. That means the honest recommendation is not "pick the winner from the survivors list" (there isn't one for primary duty) — it's: **attempt the least-bad, cheapest-verified-to-actually-fit VPS candidate as a live test, keep the Mac as a silent bridge until that test passes, and put the dead-host paging mechanism *outside* the primary host from day one**, because the audit already proved the watchdog-lives-with-what-it-watches design is broken regardless of which host wins.

---

## 1. Primary architecture — hybrid, decisive pick

**Hetzner CX/CAX Cloud VPS (US region: Ashburn/Hillsboro, to avoid EU-specific SEPA/IBAN dead-ends) as the 24/7 core, paid via PayPal on a monthly (not annual) plan, + healthchecks.io free dead-man's-switch as the external "host died" alarm, + the Z Flip 4 as a redundant off-host paging/SSH channel, + Mac kept running (unchanged) until cutover is verified, then powered down.**

Why this over everything else that was tested:

- **Cost fit.** Hetzner's small ARM (CAX) or shared-core (CX) tier is the only candidate in the tested set whose specs actually clear the workload's own RAM ceiling (the audit's 6-daemon `MemoryMax` sum is ~1.3GB design, ~500-900MB realistic) *without* blowing the $5/mo hard ceiling — DigitalOcean and Vultr's cheap tiers were killed specifically because their $5-6/1GB tier is too thin for this workload and the tier that fixes that breaks the price ceiling. Hetzner's comparable tier gives more RAM per dollar, which is the actual thing that sank the other two.
- **Payment friction, mitigated not eliminated.** Hetzner takes PayPal (no card requirement), which directly answers the user's stated card-fragility constraint. The KYC/ID-lock risk flagged in verification is real but is **not unique to Hetzner** — it showed up against GCP, DigitalOcean, Vultr, Linode, and RackNerd too. Since no cloud provider tested came back clean on this front, the right response isn't to reject cloud hosting entirely — it's to (a) pick the provider with the best documented cost/reliability fit once you accept the KYC risk is shared, and (b) treat first signup as a *test*, not a commitment, before decommissioning anything.
- **Reliability.** Real KVM, real systemd, no structural inability to run `OnCalendar` timers or persistent daemons — this is the same class of infra the Oracle VM already proved works for this exact stack. The Front-2 failures that killed DO/Vultr were RAM-arithmetic problems, not infra problems; Hetzner's extra RAM headroom removes that specific failure mode.
- **Europe-move resilience.** It's cloud, reachable over the existing Tailscale tailnet regardless of what country the user is physically in — the one property that matters most given the pending move. A home server or Pi was correctly ruled out for the same reason already built into the constraints.
- **GitHub Actions / Cloudflare Workers Cron are explicitly *not* used for anything live-money.** Both were independently killed for the daemon/watchdog workload (ephemeral runtime, no persistent process, 5-min cron floor, no SLA on schedule triggers). They add a second platform and a second point of failure without removing the real hosting problem, so they're excluded from the primary design entirely. All 22 units (6 daemons + 16 timer pairs) stay on the one VPS, exactly as they already run on Oracle today — no re-architecture, just a re-host.
- **The watchdog design flaw gets fixed, not repeated.** The audit's own finding — "the watchdog currently lives on the host it's watching, so a hard crash kills the pager with it" — is the one structural bug worth fixing regardless of which VPS wins. This plan puts the actual "is the host alive" check on **healthchecks.io** (free, external, no card, dead-man's-switch pattern: the VPS pings it every N minutes; if a ping is missed, healthchecks.io alerts independently of the VPS's own health) with the **phone as a second, independent receiver** of that same alert via ntfy. Neither depends on the VPS being alive to fire.

---

## 2. Runner-up — if Hetzner signup fails or gets ID-locked

**Try re-instating the original Oracle Always Free account first (free, $0, already proven at this exact workload) via a support ticket disputing the billing/card-verification suspension. If that stalls beyond ~1 week, fall to DigitalOcean's $6/mo Droplet (1GB) as a paid fallback, with an explicit, acknowledged $1/mo breach of the cost ceiling and two workload trims to fit the RAM: pause `poc10` and `soxl` (both explicitly non-live-money paper bots per the audit's own "can die without financial harm" list) until a 2GB tier is affordable.**

Why this ordering: Oracle costs nothing and already ran the whole stack successfully — the suspension was a billing-verification problem, not a capability problem, so it's worth one free attempt to fix before paying anyone. DigitalOcean is picked as the paid fallback (over Linode/Vultr/RackNerd) because it's the only other candidate with a **live-confirmed** price today ($6/mo/1GB — Linode's pricing page could not even be fetched, RackNerd's real price is $17.99/mo, Vultr's page 403'd) and PayPal support is real, not asserted. Do not attempt DigitalOcean and Hetzner signups in the same week — spread them out; back-to-back new-VPS-account creation from the same identity/IP is itself a fraud-scoring trigger.

---

## 3. Concrete migration plan for the primary (Hetzner)

1. **Sign up with PayPal, monthly billing, no annual prepay.** Smaller/recurring transactions are less likely to trip fraud review than a lump sum. Do this as a live test — do not touch the Mac or Oracle remnants yet.
2. **Provision smallest ARM (CAX) instance in a US region**, confirm live current price at checkout against the $5/mo ceiling before confirming the order (Hetzner's exact current SKU/price was not independently re-verified this session — re-check at signup, and if the confirmed price is over ~$5.50/mo, drop to the next-smaller CX tier or stop and go to the runner-up).
3. **Base OS setup**: install Tailscale, join the existing tailnet with a descriptive node name (e.g., `hetzner-core`), confirm MagicDNS resolves it and that it's reachable from the phone's Tailscale app before doing anything else.
4. **Recreate the `/Users/quarki3 → /home/ubuntu`-style path shim** (or equivalent) up front — the prior Oracle migration needed this for hardcoded Mac paths; assume the same here rather than discovering it mid-cutover.
5. **Deploy repo**: pull the private full-stack source from `robertpainter144-lab/-333-333-333-` (the owner-only lab333 mirror) via a deploy key scoped read-only to that repo — this is the repo that should land on the new VPS, not the public-facing MVYTraders repo (which stays frontend-only / Linus-shared and must never carry backend/strategy code per standing directive). Use the same `vm-autopush.sh` pull pattern already proven on the Oracle box.
6. **Secrets transfer** (Alpaca keys, `ntfy.env`, `anthropic.env`, any IBKR creds even though dormant): copy over Tailscale SSH directly Mac→Hetzner, never through the repo, never through a third-party paste service. Delete the in-transit copies afterward.
7. **Install all 22 systemd units** (6 daemons + 16 timer/service pairs) unchanged from the mirrored unit files already in `trading-stack/vm/systemd/`. Set the `MemoryMax=` caps exactly as they are today.
8. **Stand up the dead-man's-switch**: create a free healthchecks.io check, add a `curl`-based ping to it inside `quarki-watchdog.timer`'s own run (every 1 min, same cadence). Configure healthchecks.io's own alerting to hit ntfy directly (not routed back through the VPS). This is the piece that actually answers "page me when the host dies" — because it lives entirely off-host.
9. **Phone as second channel**: on the Z Flip 4, install Termux (F-Droid) + enable the Samsung One UI battery-optimization exemptions (disable "put app to sleep," disable adaptive battery for Termux, exclude from auto-disable) so it can run a lightweight independent ping (e.g., every 15 min, Tailscale reachability check to the Hetzner node) and push its own ntfy alert if the primary alert channel goes quiet too. This is explicitly a bonus/backup channel, not the primary guardrail — do not skip step 8 in favor of this.
10. **Burn-in period, minimum 5 trading days, before touching the Mac**: run Hetzner and Mac/Oracle-remnant in parallel (paper-only outputs compared), specifically watching the two exact-minute timers (`ondrift-buy` 15:58 ET, `ondrift-sell` 09:31 ET) fire on time every day, and confirming healthchecks.io + phone both correctly alert on a deliberately-triggered test failure (kill a unit on purpose once, confirm both channels page).
11. **Cutover live-money units last**: only after step 10 is clean, stop `quarki-scanner.service` and `quarki-qc.service` on whatever is currently running them, start them on Hetzner, and confirm one full trading day of correct live behavior (positions, stop-losses, D5 gate) before declaring done.
12. **Turn the Mac off** (or let it idle with nothing load-bearing on it) only after step 11 passes and a second healthchecks.io/phone dead-host test confirms paging works with the Mac fully powered down — this is the actual proof that Mac-independence was achieved, not just claimed.

---

## 4. Cost and first-invoice payment method

- **Hetzner (primary):** ~$4–5.50/mo depending on exact current CAX/CX SKU (needs live reconfirmation at checkout — not independently re-verified this session). Paid via **PayPal**, monthly billing cycle, no annual prepay.
- **healthchecks.io (dead-man's-switch):** $0, free tier, no card.
- **Termux/Tailscale on phone (secondary channel):** $0, already-owned hardware, already-provisioned tailnet.
- **Total recurring: ~$4–5.50/mo**, at or just under the stated hard ceiling. If Hetzner's live-confirmed price exceeds that, stop and go to the runner-up (Oracle retry, $0) rather than accepting a silent budget breach.
- **Runner-up (DigitalOcean) if invoked:** $6/mo via PayPal — an explicit, acknowledged $1/mo ceiling breach, only used if both Hetzner and an Oracle reinstatement attempt fail.