// owner_zoom.js — pinch-to-zoom detents for chart period bars (owner-only).
// Two-finger pinch over any chart card steps through its period buttons
// (1D/1W/1M/...) like a ratchet: pinch OUT = zoom in = shorter period,
// pinch IN = zoom out = longer period. Faster pinches cross detents faster.
// TWO-TIER SENSITIVITY (Robert 2026-08-11): slow pinches are the chart's own
// fluid zoom — they never advance the detents (sub-threshold frames bleed the
// accumulator). Only deliberately QUICK pinches hop presets, the first hop
// needs an extra-large sweep (ENGAGE), and each hop claims the gesture via
// window.__qtDetentUntil so the chart's fluid zoom stands down while hopping.
// Each detent click()s the real button (React handles the rest), fires a
// short haptic tick (navigator.vibrate — needs VIBRATE in the APK, v3.3+),
// and pulses the button. Hitting either end gives one longer "clunk" buzz.
// Two-finger gestures over a chart card are owned by this engine BY DESIGN
// (native one-finger pan/drag is untouched); a card only qualifies if it
// actually contains a rendered chart surface AND a period button group.
// Hardened 2026-08-10 after an adversarial review fleet: accumulator-windup
// drain, touch-identifier tracking (finger substitution), chart-surface
// requirement (no phantom oversized cards), midpoint-local discovery (no
// full-document scans in the touch path), disabled-button exclusion.
(() => {
  'use strict';
  if (window.__qtPinchZoom) return;
  window.__qtPinchZoom = true;

  const PERIOD_RE = /^(\d{1,2}[HDWMY]|YTD|ALL|MAX)$/i; // 1H..12H, 1D, 3D, 1W, 1M..6M, 1Y, 5Y, ALL…
  // Tuned on Robert's real fingers 2026-08-10: a genuine pinch sweeps far
  // more log-spread than synthetic tests, and sub-10ms buzzes are below what
  // the Flip4's motor renders. Detents ~0.55 log2 units => a full deliberate
  // pinch crosses ~3-4 levels; velocity gain reserved for genuinely fast flicks.
  const DETENT = 0.55;        // log2 pinch-scale units per step (after the first)
  const ENGAGE = 0.95;        // the FIRST hop of a gesture needs this much sweep
  const SPEED_MIN = 1.15;     // log2-units/sec — slower pinches are the fluid-zoom
                              // tier and never advance the detents
  const DECAY = 0.7;          // slow frames bleed accumulated windup toward zero
  const SPEED_REF = 2.6;      // velocity boost starts well above SPEED_MIN
  const GAIN_MAX = 2.0;       // fastest pinch crosses detents up to 3x faster
  const TICK_MS = 22, CLUNK_MS = 50, EDGE_COOLDOWN = 350;
  const MAX_HOPS = 10;

  function buzz(ms) {
    // Native bridge first (Terminal 333 v3.4+): Chromium blocks
    // navigator.vibrate until the page has been TAPPED, and pinches never
    // grant that activation — the bridge has no such rule and renders short
    // ticks as the system's crisp CLICK effect.
    try {
      if (window.QTNative && QTNative.vibrate) { QTNative.vibrate(ms); return; }
    } catch (e) {}
    try { navigator.vibrate && navigator.vibrate(ms); } catch (e) {}
  }

  // The period group inside `root`: one parent whose direct <button> children
  // are mostly period labels. Disabled buttons are not steppable detents.
  function groupIn(root) {
    const seen = new Set();
    for (const b of root.querySelectorAll('button')) {
      const p = b.parentElement;
      if (!p || seen.has(p)) continue;
      seen.add(p);
      const periods = Array.from(p.children).filter(c =>
        c.tagName === 'BUTTON' && !c.disabled &&
        c.getAttribute('aria-disabled') !== 'true' &&
        PERIOD_RE.test((c.textContent || '').trim()));
      if (periods.length >= 3) return periods;
    }
    return null;
  }

  // Walk up from the pinch midpoint to the nearest ancestor that holds BOTH
  // a real chart surface and a period group — that ancestor is the card.
  // Localized to the touch point: no document-wide scans in the touch path.
  function cardAt(x, y) {
    let node = document.elementFromPoint(x, y);
    for (let hops = 0; node && node !== document.body && hops < MAX_HOPS;
         node = node.parentElement, hops++) {
      if (!node.querySelector) continue;
      if (node.querySelector('canvas,svg,.recharts-wrapper')) {
        const btns = groupIn(node);
        if (btns) return btns;
      }
    }
    return null;
  }

  function activeIndex(btns) {
    for (let i = 0; i < btns.length; i++) {
      const b = btns[i];
      if (b.getAttribute('aria-pressed') === 'true' ||
          b.getAttribute('data-state') === 'active' ||
          b.getAttribute('aria-selected') === 'true') return i;
    }
    // Visual outlier: the selected chip is styled unlike its siblings.
    // (Disabled buttons never enter btns, so they can't fake uniqueness.)
    const sig = btns.map(b => {
      const s = getComputedStyle(b);
      return s.backgroundColor + '|' + s.color + '|' + s.borderColor;
    });
    const counts = {};
    sig.forEach(k => counts[k] = (counts[k] || 0) + 1);
    let best = -1;
    sig.forEach((k, i) => { if (counts[k] === 1 && best === -1) best = i; });
    return best !== -1 ? best : 0;
  }

  const dist2 = (a, b) => Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  function byId(list, id) {
    for (const t of list) if (t.identifier === id) return t;
    return null;
  }

  let st = null; // active gesture

  function begin(e) {
    if (e.touches.length !== 2) return;
    const t0 = e.touches[0], t1 = e.touches[1];
    const btns = cardAt((t0.clientX + t1.clientX) / 2,
                        (t0.clientY + t1.clientY) / 2);
    if (!btns) return;
    st = {
      btns,
      idx: activeIndex(btns),
      id0: t0.identifier, id1: t1.identifier,
      lastD: dist2(t0, t1),
      lastT: e.timeStamp, acc: 0, lastEdge: 0, engaged: false,
    };
    e.preventDefault();
  }

  function move(e) {
    if (!st) return;
    if (e.touches.length !== 2) return;
    // Same two physical fingers only — a substituted finger (transient third
    // touch, one lifts) would poison lastD with an unrelated distance.
    const t0 = byId(e.touches, st.id0), t1 = byId(e.touches, st.id1);
    if (!t0 || !t1) { st = null; return; }
    e.preventDefault();
    const d = dist2(t0, t1);
    if (d <= 0 || st.lastD <= 0) return;
    const dl = Math.log2(d / st.lastD);
    const dt = Math.max(8, e.timeStamp - st.lastT);
    st.lastD = d; st.lastT = e.timeStamp;
    const speed = Math.abs(dl) * 1000 / dt;
    // Slow tier: the chart's fluid zoom owns this motion. Bleed windup so a
    // long fluid zoom can never "bank" distance toward an accidental hop.
    if (speed < SPEED_MIN) { st.acc *= DECAY; return; }
    const gain = 1 + Math.min(GAIN_MAX, Math.max(0, speed / SPEED_REF - 1));
    st.acc += dl * gain;
    // fingers apart (dl>0) = zoom IN = step toward index 0 (shortest period)
    const need = st.engaged ? DETENT : ENGAGE;
    let guard = 0;
    while (st.acc >= need && guard++ < 8) { st.acc -= need; step(-1, e.timeStamp); }
    while (st.acc <= -need && guard++ < 8) { st.acc += need; step(+1, e.timeStamp); }
    // If the guard capped this frame, drop the windup — carrying it forward
    // fires phantom detents on later frames after the fingers have stopped.
    if (Math.abs(st.acc) >= need) st.acc = 0;
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
    if (!b.isConnected) { st = null; return; } // React re-render replaced it
    st.engaged = true;
    // Claim the gesture: the chart's fluid zoom stands down while presets
    // hop (rolling window — self-expires, nothing to clean up on touchend).
    try { window.__qtDetentUntil = Date.now() + 450; } catch (e) {}
    b.click();
    buzz(TICK_MS);
    try { b.scrollIntoView({ inline: 'center', block: 'nearest' }); } catch (e) {}
    try {
      b.animate(
        [{ transform: 'scale(1)' }, { transform: 'scale(1.22)' },
         { transform: 'scale(1)' }],
        { duration: 160, easing: 'ease-out' });
    } catch (e) {}
  }

  function end(e) {
    if (!st) return;
    if (e.touches.length < 2 ||
        !byId(e.touches, st.id0) || !byId(e.touches, st.id1)) st = null;
  }

  // capture-phase, non-passive: we must be able to preventDefault before the
  // page's own handlers or native scroll/zoom react. Cost when idle is one
  // length check; discovery work is localized under the pinch midpoint.
  document.addEventListener('touchstart', begin, { passive: false, capture: true });
  document.addEventListener('touchmove', move, { passive: false, capture: true });
  document.addEventListener('touchend', end, { passive: true, capture: true });
  document.addEventListener('touchcancel', end, { passive: true, capture: true });
})();
