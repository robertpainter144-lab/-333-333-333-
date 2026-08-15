// owner_crypto_account.js — crypto-sleeve account card for the Book page
// (owner-only, D1 pattern: private/ dir, key-gated /owner/crypto-account.js,
// injected only into owner-mode shells). Fills the SPA's
// data-qt-slot="book-crypto-account" mount with a twin of the Alpaca hero:
// portfolio value, unrealized P/L, vs test baseline, vs inception (net of
// the PEPE adoption, treated as a transfer per Robinhood semantics), and a
// drawdown gauge against the D39 −$15 freeze rail. Separate bundle on
// purpose — parallel edits to owner_crypto.js never conflict with this.
(() => {
  'use strict';
  if (window.__qtCryptoAccount) return;
  window.__qtCryptoAccount = true;

  const GREEN = '#00E5A0';
  const POLL_MS = 30000;

  // Honest anchors — dates/values are contract, not data (same doctrine as
  // the SPA's Book baselines). Sleeve went live 2026-08-09 with $10.01; the
  // 7.1M PEPE adoption that evening (+$20.4289 at its 2.878e-06 baseline) is
  // capital IN, not P/L. Test baseline = the 18-pair/4-leg expansion.
  // `ts` is the precise baseline moment — transfer netting must cut there,
  // not at midnight, or a same-day adoption already inside the baseline
  // value gets subtracted twice (shipped bug: "vs test baseline −$20.41").
  const INCEPTION = { value: 10.01, date: '2026-08-09',
                      ts: Date.parse('2026-08-09T00:00:00-04:00') };
  const TEST_BASELINE = { value: 30.43, date: '2026-08-09', label: 'expansion',
                          ts: Date.parse('2026-08-09T23:55:00Z') };
  const TRANSFERS = [
    { ts: Date.parse('2026-08-09T23:51:45Z'), amount: 20.4289 },
    // 2026-08-14 eve: Robert's $10 Kraken cash deposit (flagged per D41,
    // proceeded — within the $50 D39 cap; capital in, not P/L)
    { ts: Date.parse('2026-08-15T03:07:00Z'), amount: 10.0 },
  ];
  // D39 rails: freeze all sleeve trading at −$15 off peak; amber at −$10.
  const DD_FREEZE = 15.0, DD_AMBER = 10.0;

  const $ = (v) => '$' + Math.abs(v).toFixed(2);
  const signed = (v) => (v < 0 ? '−$' : '+$') + Math.abs(v).toFixed(2);
  const arrow = (v) => (v >= 0 ? '▲' : '▼');
  const cls = (v) => (v >= 0 ? 'text-pos' : 'text-neg');

  function netTransfersSince(t0) {
    let net = 0;
    for (const x of TRANSFERS) if (x.ts > t0) net += x.amount;
    return net;
  }

  function deltaBlock(label, base, dateStr, equity, baseTs) {
    const netX = netTransfersSince(baseTs);
    const d = equity - base - netX;
    const p = (d / (base + Math.max(0, netX))) * 100;
    return (
      '<div>' +
      '<div class="text-[10px] uppercase tracking-wider text-muted">' + label +
      ' <span class="text-faint">(' + dateStr + ', ' + $(base) + ')</span></div>' +
      // No whitespace-nowrap here: in the two-column heroes row the card is
      // ~half the phone width, and nowrap made the (%) clip off the edge.
      '<div class="num text-sm font-semibold ' + cls(d) + '">' + arrow(d) + ' ' +
      signed(d) + ' <span class="opacity-80">(' + (p >= 0 ? '+' : '') + p.toFixed(2) + '%)</span></div>' +
      (netX !== 0
        ? '<div class="text-[9px] text-faint" title="Adoptions and cash deposits are capital in, not P/L">net of +$' + netX.toFixed(2) + ' transfers</div>'
        : '') +
      '</div>'
    );
  }

  function gauge(peak, equity) {
    const dd = Math.max(0, peak - equity);
    const frac = Math.min(1, dd / DD_FREEZE);
    const state = dd >= DD_FREEZE ? 'red' : dd >= DD_AMBER ? 'amber' : 'ok';
    const barColor = state === 'red' ? '#f85149' : state === 'amber' ? '#d29922' : '#3fb950';
    return (
      '<div class="mt-2">' +
      '<div class="mb-1 flex items-baseline justify-between gap-2">' +
      '<span class="min-w-0 truncate text-[10px] uppercase tracking-wider text-muted" ' +
      'title="D39: sleeve freezes all trading at $' + DD_FREEZE.toFixed(2) + ' off peak">' +
      'Drawdown vs ' + $(DD_FREEZE) + ' freeze rail</span>' +
      '<span class="num shrink-0 whitespace-nowrap text-sm font-bold ' +
      (state === 'ok' ? 'text-fg' : state === 'amber' ? 'text-amber' : 'text-neg') + '">' +
      $(dd) + ' of ' + $(DD_FREEZE) + '</span></div>' +
      '<div class="h-2 w-full overflow-hidden rounded bg-panel2">' +
      '<div class="h-full rounded" style="width:' + (frac * 100) + '%;background:' + barColor + '"></div></div>' +
      '<div class="num mt-0.5 flex justify-between gap-1 text-[9px] text-faint">' +
      '<span>$0</span><span class="text-amber">$' + DD_AMBER.toFixed(0) + ' amber</span>' +
      '<span class="text-neg">$' + DD_FREEZE.toFixed(0) + ' freeze</span></div>' +
      (state === 'red'
        ? '<div role="alert" class="mt-2 rounded border border-neg/50 bg-neg/10 px-2 py-1.5 text-xs font-bold text-neg">⛔ Sleeve frozen — D39 rail hit. No new crypto orders until reviewed.</div>'
        : '') +
      '</div>'
    );
  }

  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  function render(slot, sum) {
    const eq = sum.equity || [];
    if (!eq.length) return;
    const last = eq[eq.length - 1];
    const equity = last.equity;
    let peak = 0;
    for (const r of eq) if (r.equity > peak) peak = r.equity;
    let unreal = 0;
    const pos = (sum.state && sum.state.positions) || {};
    for (const k in pos) {
      const p = pos[k];
      if (typeof p.mv === 'number' && typeof p.notional === 'number') unreal += p.mv - p.notional;
    }
    const ageS = Math.max(0, Math.round(Date.now() / 1000 - (sum.ts || 0)));
    slot.innerHTML =
      // Header + padding mirror the SPA Card primitive (ui.tsx) exactly —
      // uppercase text-xs title, px-3 py-2 header, p-3 body — so the two
      // hero sections read as identical twins.
      '<section class="h-full rounded-lg border border-bd bg-panel">' +
      '<header class="flex items-center justify-between gap-2 border-b border-bd px-3 py-2">' +
      '<span class="flex min-w-0 items-center gap-2 truncate text-xs font-semibold uppercase tracking-wider text-muted">' +
      '<span class="h-2 w-2 shrink-0 rounded-full" style="background:' + GREEN + '"></span>' +
      '<span class="truncate">Crypto — Kraken</span></span>' +
      '<span class="num shrink-0 text-[10px] text-faint">' + (ageS < 90 ? ageS + 's' : Math.round(ageS / 60) + 'm') + '</span></header>' +
      '<div class="p-3">' +
      '<div><div class="num text-4xl font-bold tracking-tight" style="color:' + GREEN + '">' + $(equity) + '</div>' +
      '<div class="num mt-0.5 text-xs text-muted">unrealized <span class="' + cls(unreal) + '">' +
      arrow(unreal) + ' ' + signed(unreal) + '</span></div></div>' +
      '<div class="mt-1.5 flex flex-col gap-1.5">' +
      deltaBlock('vs test baseline', TEST_BASELINE.value, TEST_BASELINE.date, equity, TEST_BASELINE.ts) +
      deltaBlock('vs inception', INCEPTION.value, INCEPTION.date, equity, INCEPTION.ts) +
      '</div>' + gauge(peak, equity) + '</div></section>';
    // Sit beside the Alpaca hero, not below it: flip the wrapper row to two
    // columns only now that the card exists (guests keep a single column).
    // Robert 2026-08-13: EQUAL columns — the 1.25fr/1fr split plus the
    // Alpaca card's mobile bleed (-mx-3, borderless) made the two value
    // sections visibly uneven. evenHeroes() re-frames the Alpaca card to a
    // normal bordered card inside this row so both halves match.
    const row = slot.closest('[data-qt-row="book-heroes"]');
    if (row) {
      row.style.gridTemplateColumns = 'minmax(0,1fr) minmax(0,1fr)';
      row.style.alignItems = 'stretch';
      evenHeroes();
    }
  }

  // Neutralize the SPA Card's mobile "bleed" styling for the Alpaca hero
  // while it sits in the owner two-column row: no negative margins, framed
  // border like its Kraken twin, horizontal padding restored (bleed mode
  // drops px on mobile), and a one-line ellipsized title so both headers are
  // the same height and the two big numbers sit level. Stylesheet (not
  // inline) so it survives every SPA re-render; guests never load this
  // bundle, so their single-column bleed layout is untouched.
  function evenHeroes() {
    if (document.getElementById('qt-even-heroes')) return;
    const st = document.createElement('style');
    st.id = 'qt-even-heroes';
    st.textContent =
      '[data-qt-row="book-heroes"]>section:first-child{' +
      'margin-left:0!important;margin-right:0!important;' +
      'border:1px solid var(--bd);border-radius:0.5rem}' +
      '[data-qt-row="book-heroes"]>section:first-child>div{' +
      'padding-left:0.75rem;padding-right:0.75rem}' +
      '[data-qt-row="book-heroes"]>section:first-child header h2{' +
      'white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
      // The title's inner span is a flex row (dot + text); flatten it to
      // inline so the h2's ellipsis actually renders instead of hard-clipping
      // mid-glyph, and keep the status dot visible as inline-block.
      '[data-qt-row="book-heroes"]>section:first-child header h2>span{' +
      'display:inline}' +
      '[data-qt-row="book-heroes"]>section:first-child header h2>span>span:first-child{' +
      'display:inline-block;margin-right:0.4rem}' +
      // Keep the "N s ago" stamp on one line — when it wraps, the Alpaca
      // header grows two lines taller and the two big numbers sit unlevel.
      '[data-qt-row="book-heroes"]>section:first-child header .num{' +
      'white-space:nowrap}';
    document.head.appendChild(st);
  }

  // Robert 2026-08-10: the top balance trio is redundant now that both hero
  // cards sit right below it — keep only the combined TOTAL. The trio is
  // rendered by owner_crypto.js (another window's file); slimming it here
  // from the DOM avoids cross-window edit collisions and re-applies if that
  // bundle re-renders. Idempotent via the hidden style itself.
  function slimTrio() {
    // owner_crypto.js's poll rewrites the trio bar's innerHTML every few
    // seconds, so inline hides get resurrected. A stylesheet rule survives
    // every rewrite: the bar's tiles are its only children, in fixed order
    // Alpaca(1) / Crypto(2) / Total(3) — hide the first two for good.
    if (document.getElementById('qt-slim-trio')) return;
    const st = document.createElement('style');
    st.id = 'qt-slim-trio';
    st.textContent =
      'div:has(> .qtcy-tri-label):nth-child(-n+2){display:none!important}';
    document.head.appendChild(st);
  }

  let timer = null;
  async function tick() {
    slimTrio();
    const slot = document.querySelector('[data-qt-slot="book-crypto-account"]');
    if (!slot) return; // not on the Book page right now
    try {
      const r = await fetch('/api/crypto/summary', { credentials: 'same-origin' });
      if (!r.ok) return;
      render(slot, await r.json());
    } catch (e) { /* transient — next tick retries */ }
  }

  // The SPA renders the slot whenever the Book page mounts; watch for it and
  // keep the card fresh while it exists.
  const mo = new MutationObserver(() => {
    const slot = document.querySelector('[data-qt-slot="book-crypto-account"]');
    if (slot && !slot.hasChildNodes()) tick();
  });
  mo.observe(document.body, { childList: true, subtree: true });
  timer = setInterval(tick, POLL_MS);
  tick();
})();
