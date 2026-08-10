// owner_zoom.js — pinch-to-zoom detents for chart period bars (owner-only).
// Two-finger pinch over any chart card steps through its period buttons
// (1D/1W/1M/...) like a ratchet: pinch OUT = zoom in = shorter period,
// pinch IN = zoom out = longer period. Faster pinches cross detents faster.
// Each detent click()s the real button (React handles the rest), fires a
// short haptic tick (navigator.vibrate — needs VIBRATE in the APK, v3.3+),
// and pulses the button. Hitting either end gives one longer "clunk" buzz.
// Generic by design: any group of >=3 buttons labelled like periods inside
// a card that also contains a chart (canvas/svg) gets the behavior — Book
// equity chart, crypto pane, catalog charts, future charts, all covered.
(() => {
  'use strict';
  if (window.__qtPinchZoom) return;
  window.__qtPinchZoom = true;

  const PERIOD_RE = /^(1D|5D|1W|2W|1M|3M|6M|9M|1Y|2Y|5Y|YTD|ALL|MAX)$/i;
  const DETENT = 0.32;        // log2 pinch-scale units per step (~25% spread)
  const SPEED_REF = 1.2;      // log2-units/sec where velocity boost kicks in
  const GAIN_MAX = 2.5;       // fastest pinch crosses detents up to 3.5x faster
  const TICK_MS = 9, CLUNK_MS = 28, EDGE_COOLDOWN = 350;

  function buzz(ms) {
    try { navigator.vibrate && navigator.vibrate(ms); } catch (e) {}
  }

  // A period group = one parent whose direct <button> children are mostly
  // period labels. Returns {card, btns} or null.
  function findGroups() {
    const seen = new Set(), out = [];
    for (const b of document.querySelectorAll('button')) {
      const p = b.parentElement;
      if (!p || seen.has(p)) continue;
      seen.add(p);
      const kids = Array.from(p.children).filter(c => c.tagName === 'BUTTON');
      const periods = kids.filter(c => PERIOD_RE.test((c.textContent || '').trim()));
      if (periods.length >= 3) {
        // the chart card = nearest ancestor that also contains a rendered
        // chart surface; fall back to the group's grandparent block
        let card = p, hops = 0;
        while (card.parentElement && hops < 8 &&
               !card.querySelector('canvas,svg,.recharts-wrapper')) {
          card = card.parentElement; hops++;
        }
        out.push({ card, btns: periods });
      }
    }
    return out;
  }

  function activeIndex(btns) {
    // Prefer explicit state markers, else the visual outlier (the selected
    // chip is styled differently from its siblings), else remembered index.
    for (let i = 0; i < btns.length; i++) {
      const b = btns[i];
      if (b.getAttribute('aria-pressed') === 'true' ||
          b.getAttribute('data-state') === 'active' ||
          b.getAttribute('aria-selected') === 'true') return i;
    }
    const sig = btns.map(b => {
      const s = getComputedStyle(b);
      return s.backgroundColor + '|' + s.color + '|' + s.borderColor;
    });
    const counts = {};
    sig.forEach(k => counts[k] = (counts[k] || 0) + 1);
    let best = -1;
    sig.forEach((k, i) => { if (counts[k] === 1) best = best === -1 ? i : best; });
    return best !== -1 ? best : 0;
  }

  const dist = (t) => Math.hypot(
    t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);

  let st = null; // active gesture

  function begin(e) {
    if (e.touches.length !== 2) return;
    const mx = (e.touches[0].clientX + e.touches[1].clientX) / 2;
    const my = (e.touches[0].clientY + e.touches[1].clientY) / 2;
    for (const g of findGroups()) {
      const r = g.card.getBoundingClientRect();
      if (mx >= r.left && mx <= r.right && my >= r.top && my <= r.bottom) {
        st = {
          btns: g.btns,
          idx: activeIndex(g.btns),
          d0: dist(e.touches), lastD: dist(e.touches),
          lastT: e.timeStamp, acc: 0, lastEdge: 0,
        };
        e.preventDefault();
        return;
      }
    }
  }

  function move(e) {
    if (!st) return;
    if (e.touches.length !== 2) return;
    e.preventDefault();
    const d = dist(e.touches);
    if (d <= 0 || st.lastD <= 0) return;
    const dl = Math.log2(d / st.lastD);
    const dt = Math.max(8, e.timeStamp - st.lastT);
    st.lastD = d; st.lastT = e.timeStamp;
    const speed = Math.abs(dl) * 1000 / dt;
    const gain = 1 + Math.min(GAIN_MAX, Math.max(0, speed / SPEED_REF - 1));
    st.acc += dl * gain;
    // fingers apart (dl>0) = zoom IN = step toward index 0 (shortest period)
    let guard = 0;
    while (st.acc >= DETENT && guard++ < 8) { st.acc -= DETENT; step(-1, e.timeStamp); }
    while (st.acc <= -DETENT && guard++ < 8) { st.acc += DETENT; step(+1, e.timeStamp); }
  }

  function step(dir, ts) {
    const i = st.idx + dir;
    if (i < 0 || i >= st.btns.length) {
      if (ts - st.lastEdge > EDGE_COOLDOWN) { st.lastEdge = ts; buzz(CLUNK_MS); }
      st.acc = 0; // don't wind up past the end
      return;
    }
    st.idx = i;
    const b = st.btns[i];
    b.click();
    buzz(TICK_MS);
    try {
      b.animate(
        [{ transform: 'scale(1)' }, { transform: 'scale(1.22)' },
         { transform: 'scale(1)' }],
        { duration: 160, easing: 'ease-out' });
    } catch (e) {}
  }

  function end(e) {
    if (st && e.touches.length < 2) st = null;
  }

  // capture-phase, non-passive: we must be able to preventDefault before the
  // page's own handlers or native scroll/zoom react. Cost when idle is one
  // length check.
  document.addEventListener('touchstart', begin, { passive: false, capture: true });
  document.addEventListener('touchmove', move, { passive: false, capture: true });
  document.addEventListener('touchend', end, { passive: true, capture: true });
  document.addEventListener('touchcancel', end, { passive: true, capture: true });
})();
