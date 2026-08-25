import { useEffect, useMemo, useRef, useState } from "react";
import type { EquityPoint, TransferPoint } from "../lib/types";
import { toEpochMs, money, moneySigned, pct, signClass, arrow } from "../lib/format";
import { Tabs } from "./ui";

const RANGES = [
  { id: "1H", label: "1H", ms: 3600e3 },
  { id: "3H", label: "3H", ms: 3 * 3600e3 },
  { id: "6H", label: "6H", ms: 6 * 3600e3 },
  { id: "12H", label: "12H", ms: 12 * 3600e3 },
  { id: "1D", label: "1D", ms: 24 * 3600e3 },
  { id: "3D", label: "3D", ms: 3 * 24 * 3600e3 },
  { id: "1W", label: "1W", ms: 7 * 24 * 3600e3 },
  { id: "2W", label: "2W", ms: 14 * 24 * 3600e3 },
  { id: "3W", label: "3W", ms: 21 * 24 * 3600e3 },
  { id: "1M", label: "1M", ms: 31 * 24 * 3600e3 },
  { id: "3M", label: "3M", ms: 92 * 24 * 3600e3 },
  { id: "6M", label: "6M", ms: 183 * 24 * 3600e3 },
  { id: "1Y", label: "1Y", ms: 366 * 24 * 3600e3 },
  { id: "5Y", label: "5Y", ms: 5 * 366 * 24 * 3600e3 },
  { id: "ALL", label: "ALL", ms: Infinity },
];

/** Baseline markers: honest test window start + inception (per API.md semantics). */
const MARKERS = [
  { ts: new Date("2026-07-27T00:00:00-04:00").getTime(), label: "test $48.06" },
  { ts: new Date("2026-07-13T00:00:00-04:00").getTime(), label: "inception $50.04" },
];

const PAD_L = 47;   // fits "$1000.00" at the 9px tick font — never clip a digit
const PAD_B = 20;
const PAD_T = 8;

/** Add-on series painted over the chart by external modules with no
 *  build-time coupling: entries pushed into window.__qtOverlaySeries and
 *  tagged with a channel are drawn by the chart instance whose
 *  `overlayChannel` prop matches — on their own y-scale, with a right-edge
 *  value axis in the series color and an optional top time axis. Dispatch a
 *  "qt-overlay-series" window event after changing the registry. */
export type OverlaySeries = {
  channel: string;
  color: string;
  topAxisColor?: string;
  /** Draw the shared bottom time axis in this color (first overlay that
   *  sets it wins) — lets a channel re-brand the bottom clock. */
  bottomAxisColor?: string;
  /** When any active overlay sets this, ONLY such overlays draw right-edge
   *  value ticks — the others' are suppressed so one series can own the
   *  right gutter outright. */
  rightAxisExclusive?: boolean;
  /** Draw a dashed flat segment at the first point's value across any
   *  visible region BEFORE the series begins, so a young series still reads
   *  as a full line (dashed = pre-history, solid = real data). */
  leadIn?: boolean;
  /** 0–1: reserve this fraction of the plot height as a dedicated lane at
   *  the TOP for this series; the main line compresses into the remainder.
   *  Stacked lanes share the time axis but never visually tangle. */
  lane?: number;
  points: { t: number; v: number }[];
};
declare global {
  interface Window {
    __qtOverlaySeries?: OverlaySeries[];
    /** Trade dots on the overlay line (Robert 2026-08-22): red=buy, blue=sell,
     *  tap for symbol + $ value. Keyed by channel like the series registry. */
    __qtOverlayMarks?: { channel: string; t: number; side: string; sym: string; usd: number; pid?: string; pl?: number;
      /** anchor the dot to the overlay series with this id (default: first drawn) */
      id?: string;
      /** explicit value on that series' scale (skips time-interpolation) */
      v?: number;
      /** per-mark fill/outline colors + radius; fall back to side defaults */
      color?: string; stroke?: string; r?: number }[];
    /** When true the chart skips its own base series (line+fill) so a
     *  registered overlay can be viewed alone. Neutral hook like the
     *  overlay registry above — nothing sets it in this repo. */
    __qtBaseHidden?: boolean;
    /** Set by owner_zoom.js while a FAST pinch is hopping range presets —
     *  the chart's fluid zoom stands down until it expires. */
    __qtDetentUntil?: number;
  }
}
const MIN_SPAN_MS = 10 * 60e3; // can't zoom tighter than 10 minutes
const WKND_W = 0.15;                       // weekend renders at 15% width
const COMPRESS_MIN_SPAN = 48 * 3600e3;     // linear clock under 2 days
const POS_C = "#3fb950";
const NEG_C = "#f85149";
const LS_NET = "qt.equityNet";

type Pt = { t: number; equity: number; adj: number };
type Xfer = { t: number; amount: number; type: string };

/**
 * Canvas equity chart that OWNS its gestures (touch-action: none):
 *  - one finger drags the time window without fighting page scroll
 *  - two fingers pinch-zoom around the gesture midpoint
 *  - double-tap (or the ⟲ button) resets to the selected range
 *  - mouse: drag pans, wheel zooms (page scroll suppressed), hover shows a readout
 *
 * Transfer-aware (Robinhood semantics): deposits/withdrawals never count as
 * P/L. The header P/L for the visible window subtracts net transfers, transfer
 * events are marked on the chart, and the "net" view plots the equity curve
 * with transfer steps removed so the line IS the P/L shape.
 */
export default function EquityChart(props: {
  points: EquityPoint[];
  transfers?: TransferPoint[];
  color?: string;
  overlayChannel?: string;
  /** Range tab selected on mount (default "ALL"). */
  defaultRange?: string;
}) {
  const [range, setRange] = useState(props.defaultRange ?? "ALL");
  const overlayChannel = props.overlayChannel;
  // Bumped by the "qt-overlay-series" event so registry changes redraw.
  const [overlayRev, setOverlayRev] = useState(0);
  useEffect(() => {
    if (!overlayChannel) return;
    const bump = () => setOverlayRev((r) => r + 1);
    window.addEventListener("qt-overlay-series", bump);
    return () => window.removeEventListener("qt-overlay-series", bump);
  }, [overlayChannel]);
  // DEFAULT COLLAPSED: P/L is made by trading, not by moving money in or out,
  // so the line ships with transfer steps removed. "raw" opts into the
  // account-value view (axis rescales across deposits) and is remembered.
  // Default to the REAL account-value axis ("$"): Robert reads the yellow
  // numbers as the actual balance ($499.xx), and the net view's $49.xx axis
  // looks "one digit too short". P/L shape stays one tap away and sticks.
  const [net, setNet] = useState(() => localStorage.getItem(LS_NET) === "1");
  const color = props.color ?? "#ffd60a";

  const rawRows = useMemo(
    () =>
      props.points
        .map((p) => ({ t: toEpochMs(p.ts), equity: p.equity, adj: p.equity }))
        .filter((p) => !Number.isNaN(p.t))
        .sort((a, b) => a.t - b.t),
    [props.points],
  );

  // Alpaca reports transfers date-only (midnight UTC) but the cash actually
  // lands mid-day. Snap each transfer to the equity step on that day that
  // best matches its amount, so markers and window P/L line up with the real
  // jump instead of splitting a day where P/L reads ±deposit for a few hours.
  const xfers: Xfer[] = useMemo(() => {
    const parsed = (props.transfers ?? [])
      .map((x) => ({ t: toEpochMs(x.ts), amount: x.amount, type: x.type, dateOnly: String(x.ts).length <= 10 }))
      .filter((x) => !Number.isNaN(x.t) && x.amount !== 0)
      .sort((a, b) => a.t - b.t);
    for (const x of parsed) {
      if (!x.dateOnly) continue;
      const lo = x.t - 6 * 3600e3, hi = x.t + 30 * 3600e3;
      let bestT = 0, bestErr = Infinity;
      for (let i = 1; i < rawRows.length; i++) {
        const t = rawRows[i].t;
        if (t < lo) continue;
        if (t > hi) break;
        const step = rawRows[i].equity - rawRows[i - 1].equity;
        const err = Math.abs(step - x.amount);
        if (err < bestErr) { bestErr = err; bestT = t; }
      }
      // Only snap when the step genuinely looks like this transfer.
      if (bestT && bestErr < Math.abs(x.amount) / 2) x.t = bestT;
    }
    return parsed.map(({ t, amount, type }) => ({ t, amount, type }));
  }, [props.transfers, rawRows]);

  // Full series with the transfer-adjusted value precomputed BEFORE range
  // filtering, so the adjusted line is continuous no matter the window.
  // adj(t) = equity(t) − transfers since the first point (funding before the
  // series began is part of the baseline, not a "gain").
  const allRows: Pt[] = useMemo(() => {
    const rows = rawRows.map((p) => ({ ...p }));
    if (rows.length && xfers.length) {
      let cum = 0;
      let i = 0;
      // transfers at/before the first equity point fold into the baseline
      while (i < xfers.length && xfers[i].t <= rows[0].t) i++;
      let r = 0;
      for (const row of rows) {
        while (i < xfers.length && xfers[i].t <= row.t) cum += xfers[i++].amount;
        rows[r++].adj = row.equity - cum;
      }
    }
    return rows;
  }, [rawRows, xfers]);

  // Range tabs pick the default VIEW WINDOW, they no longer filter the data:
  // the full history stays loaded behind every tab, so one-finger pan (and
  // the scrubber bar) can travel back past the window's left edge at the
  // SAME zoom level instead of hitting a wall — switching tabs is a choice,
  // never a side effect of navigating (Robert, 2026-08-11).
  const data: Pt[] = allRows;
  const rangeMs = RANGES.find((r) => r.id === range)?.ms ?? Infinity;

  const val = (p: Pt) => (net ? p.adj : p.equity);

  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Visible time domain [t0, t1]; null = fit all data in range.
  const [domain, setDomain] = useState<[number, number] | null>(null);
  const [readout, setReadout] = useState<Pt | null>(null);
  const markHits = useRef<{ x: number; y: number; m: { side: string; sym: string; usd: number; t: number; pid?: string; pl?: number } }[]>([]);
  const [markSel, setMarkSel] = useState<{ side: string; sym: string; usd: number; t: number; pid?: string; pl?: number } | null>(null);

  // Overlay extent (within the selected range) folds into the pannable
  // domain, so a series that starts after — or runs past — the main data is
  // still reachable. Also decides the right-gutter width: overlays get a
  // dedicated value-axis gutter mirroring the left one, instead of painting
  // ticks over the plot.
  const ovExtent = useMemo(() => {
    if (!overlayChannel || !Array.isArray(window.__qtOverlaySeries)) return null;
    let mn = Infinity, mx = -Infinity, n = 0;
    for (const o of window.__qtOverlaySeries) {
      if (!o || o.channel !== overlayChannel || !Array.isArray(o.points)) continue;
      for (const p of o.points) {
        if (!Number.isFinite(p.t) || !Number.isFinite(p.v)) continue;
        n++;
        if (p.t < mn) mn = p.t;
        if (p.t > mx) mx = p.t;
      }
    }
    // Match the draw pass: a single valid point still gets a gutter (it now
    // draws as a flat line), so reserve space whenever anything will
    // actually be drawn instead of requiring 2 raw points up front.
    return n >= 1 ? { mn, mx } : null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [overlayChannel, overlayRev]);
  const padR = ovExtent ? 34 : 6;
  const padRRef = useRef(padR);
  padRRef.current = padR;

  // Overlay-only mode is SCOPED to charts with an overlay channel — the
  // page-wide flag must not blank unrelated EquityChart instances.
  const baseHidden = !!(typeof window !== "undefined" && window.__qtBaseHidden && overlayChannel);
  // Rows backing the header P/L + tap readout: the VISIBLE series. Overlay
  // points are pre-transfer-adjusted by their injector, so adj === equity.
  const activeRows = useMemo<Pt[]>(() => {
    if (!baseHidden || !Array.isArray(window.__qtOverlaySeries)) return data;
    const o = window.__qtOverlaySeries.find(
      (q) => q && q.channel === overlayChannel && Array.isArray(q.points) && q.points.length >= 2);
    if (!o) return data;
    return o.points.map((q) => ({ t: q.t, equity: q.v, adj: q.v }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, baseHidden, overlayChannel, overlayRev]);

  let dataMin = data.length ? data[0].t : ovExtent ? ovExtent.mn : 0;
  let dataMax = data.length ? data[data.length - 1].t : ovExtent ? ovExtent.mx : 1;
  if (ovExtent) {
    dataMin = Math.min(dataMin, ovExtent.mn);
    dataMax = Math.max(dataMax, ovExtent.mx);
  }
  // Default window = the selected range's span, right-anchored at the newest
  // point. A custom domain (pan/zoom/scrub) overrides it until reset.
  const fitD0 = rangeMs === Infinity ? dataMin : Math.max(dataMin, dataMax - rangeMs);
  const d0 = domain ? domain[0] : fitD0;
  const d1 = domain ? domain[1] : dataMax;

  // Refs mirror the live domain/extent so native listeners and mid-gesture
  // math never read a stale render closure (the 20 s poll appends points).
  const domRef = useRef({ d0, d1, dataMin, dataMax, fitD0 });
  domRef.current = { d0, d1, dataMin, dataMax, fitD0 };

  // Reset the custom domain ONLY when the user picks a different range tab.
  // Data refreshes must NOT reset it — that was the 24H jank: every poll
  // appended a fresh tail point, changed the data extent, and wiped the
  // user's zoom/pan mid-gesture.
  useEffect(() => setDomain(null), [range]);
  // If a data swap leaves the domain with no overlap at all (source change),
  // fall back to fit rather than rendering an empty window.
  useEffect(() => {
    setDomain((cur) => (cur && (cur[1] < dataMin || cur[0] > dataMax) ? null : cur));
  }, [dataMin, dataMax]);

  const clampDomain = (a: number, b: number): [number, number] => {
    const { dataMin: mn, dataMax: mx } = domRef.current;
    let span = Math.max(b - a, MIN_SPAN_MS);
    span = Math.min(span, mx - mn || MIN_SPAN_MS);
    let lo = a;
    if (lo < mn) lo = mn;
    if (lo + span > mx) lo = mx - span;
    return [lo, lo + span];
  };

  // ── Compressed clock ──
  // Weekend dead time (Fri 18:00 → Sun 18:00 local ≈ the span with no
  // US-equity marks) renders at WKND_W of its real width on multi-day
  // windows, so the line reads continuous the way broker charts draw it —
  // WITHOUT deleting the hours where a 24/7 overlay series still trades.
  // Sub-2-day windows keep a true linear clock. All gestures map through
  // the same curve, so pan/zoom stays pixel-accurate.
  const clock = useMemo(() => {
    const spans: [number, number][] = [];
    const d = new Date(dataMin - 30 * 86400e3);
    d.setHours(18, 0, 0, 0);
    while (d.getDay() !== 5) d.setDate(d.getDate() - 1);
    for (let ws = d.getTime(); ws < dataMax + 30 * 86400e3; ) {
      spans.push([ws, ws + 48 * 3600e3]);
      const n = new Date(ws);
      n.setDate(n.getDate() + 7);
      ws = n.getTime();
    }
    const u = (t: number) => {
      let rem = 0;
      for (const [ws, we] of spans) {
        if (t <= ws) break;
        rem += (Math.min(t, we) - ws) * (1 - WKND_W);
      }
      return t - rem;
    };
    const inv = (uu: number) => {
      let rem = 0;
      for (const [ws, we] of spans) {
        const uWs = ws - rem;
        if (uu <= uWs) return uu + rem;
        const uWe = uWs + (we - ws) * WKND_W;
        if (uu < uWe) return ws + (uu - uWs) / WKND_W;
        rem += (we - ws) * (1 - WKND_W);
      }
      return uu + rem;
    };
    return { u, inv };
  }, [dataMin, dataMax]);
  const LINEAR = { u: (t: number) => t, inv: (u: number) => u };
  const activeClock = !ovExtent && d1 - d0 > COMPRESS_MIN_SPAN ? clock : LINEAR;
  const clockRef = useRef(activeClock);
  clockRef.current = activeClock;

  // ── Gesture state (refs — no re-render churn mid-gesture) ──
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gestureStart = useRef<{ d0: number; d1: number; x: number; dist: number; mid: number } | null>(null);
  const lastTap = useRef(0);

  const xToT = (x: number, w: number) => {
    const { d0: a, d1: b } = domRef.current;
    const ck = clockRef.current;
    const ua = ck.u(a), ub = ck.u(b);
    return ck.inv(ua + ((x - PAD_L) / Math.max(w - PAD_L - padRRef.current, 1)) * (ub - ua));
  };

  function onPointerDown(e: React.PointerEvent) {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pts = [...pointers.current.values()];
    if (pts.length === 1) {
      // Trade-dot hit-test first: a tap within 11px of a mark shows its
      // symbol/$ tooltip and consumes the tap (no scrub/readout).
      const wrapEl = wrapRef.current;
      if (wrapEl) {
        const rc = wrapEl.getBoundingClientRect();
        const lx = pts[0].x - rc.left, ly = pts[0].y - rc.top;
        let hit = null, hb = 13;
        for (const h of markHits.current) {
          const d = Math.hypot(h.x - lx, h.y - ly);
          if (d < hb) { hb = d; hit = h.m; }
        }
        if (hit) { setMarkSel(hit); return; }
        setMarkSel(null);
      }
      // Double-tap reset — and start the follow-on gesture from the RESET
      // domain, not the pre-reset one, so tap-tap-drag doesn't jump.
      const now = Date.now();
      const doubleTap = now - lastTap.current < 300;
      if (doubleTap) setDomain(null);
      lastTap.current = now;
      const { d0: a, d1: b, dataMax: mx, fitD0: f0 } = domRef.current;
      // Reset lands on the RANGE's window (right-anchored), not the full
      // history — so the follow-on drag starts from what reset shows.
      gestureStart.current = doubleTap
        ? { d0: f0, d1: mx, x: pts[0].x, dist: 0, mid: pts[0].x }
        : { d0: a, d1: b, x: pts[0].x, dist: 0, mid: pts[0].x };
    } else if (pts.length === 2) {
      const dist = Math.abs(pts[0].x - pts[1].x);
      const { d0: a, d1: b } = domRef.current;
      gestureStart.current = { d0: a, d1: b, x: 0, dist: Math.max(dist, 1), mid: (pts[0].x + pts[1].x) / 2 };
    }
  }

  function onPointerMove(e: React.PointerEvent) {
    const wrap = wrapRef.current;
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    if (!pointers.current.has(e.pointerId)) {
      // Mouse hover readout (no buttons pressed)
      if (e.pointerType === "mouse" && data.length) {
        const t = xToT(e.clientX - rect.left, rect.width);
        setReadout(nearest(activeRows, t));
      }
      return;
    }
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const g = gestureStart.current;
    if (!g) return;
    const pts = [...pointers.current.values()];
    const w = Math.max(rect.width - PAD_L - padRRef.current, 1);

    const ck = clockRef.current;
    if (pts.length === 1) {
      // PAN: pixel delta → compressed-time delta on the start domain
      const gu0 = ck.u(g.d0), gu1 = ck.u(g.d1);
      const dxU = ((pts[0].x - g.x) / w) * (gu1 - gu0);
      setDomain(clampDomain(ck.inv(gu0 - dxU), ck.inv(gu1 - dxU)));
      if (activeRows.length) setReadout(nearest(activeRows, xToT(pts[0].x - rect.left, rect.width)));
    } else if (pts.length >= 2) {
      // Two-tier zoom: a FAST pinch belongs to the preset-detent engine
      // (owner_zoom.js) — once it claims the gesture the fluid zoom stands
      // down, so a range hop never tugs-of-war with a mid-flight domain
      // change. Slow pinches never trip the detents and stay fully fluid.
      if ((window.__qtDetentUntil ?? 0) > Date.now()) {
        gestureStart.current = { d0: domRef.current.d0, d1: domRef.current.d1, x: 0, dist: Math.max(Math.abs(pts[0].x - pts[1].x), 1), mid: (pts[0].x + pts[1].x) / 2 };
        return;
      }
      // PINCH: scale the start-domain around the pinch midpoint (u-space)
      const dist = Math.max(Math.abs(pts[0].x - pts[1].x), 1);
      const scale = g.dist / dist;
      const gu0 = ck.u(g.d0), gu1 = ck.u(g.d1);
      const midU = gu0 + (((g.mid - rect.left) - PAD_L) / w) * (gu1 - gu0);
      setDomain(clampDomain(
        ck.inv(midU - (midU - gu0) * scale),
        ck.inv(midU + (gu1 - midU) * scale)));
    }
  }

  function onPointerUp(e: React.PointerEvent) {
    pointers.current.delete(e.pointerId);
    const pts = [...pointers.current.values()];
    // 2 fingers → 1: restart the pan from the CURRENT domain (ref, not the
    // render closure — state may lag the last pinch setDomain).
    const { d0: a, d1: b } = domRef.current;
    gestureStart.current =
      pts.length === 1 ? { d0: a, d1: b, x: pts[0].x, dist: 0, mid: pts[0].x } : null;
    if (pts.length === 0 && e.pointerType !== "mouse") setReadout(null);
  }

  // Wheel zoom must preventDefault or the page scrolls under the chart —
  // React's onWheel is passive, so attach a native non-passive listener.
  const hasData = data.length > 0;
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheelNative = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const w = Math.max(rect.width - PAD_L - padRRef.current, 1);
      const { d0: a, d1: b } = domRef.current;
      const ck = clockRef.current;
      const ua = ck.u(a), ub = ck.u(b);
      const midU = ua + (((e.clientX - rect.left) - PAD_L) / w) * (ub - ua);
      const scale = e.deltaY > 0 ? 1.15 : 0.87;
      setDomain(clampDomain(
        ck.inv(midU - (midU - ua) * scale),
        ck.inv(midU + (ub - midU) * scale)));
    };
    el.addEventListener("wheel", onWheelNative, { passive: false });
    return () => el.removeEventListener("wheel", onWheelNative);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasData]);

  // ── Scrubber bar: mirrors where the visible window sits inside the full
  // history and slides along as you pan the chart; grab it to pan directly
  // at the EXACT current zoom (the span never changes), or tap the track to
  // jump-center the window there. ──
  const scrubRef = useRef<HTMLDivElement>(null);
  const scrubGrab = useRef<{ px: number; d0: number; d1: number } | null>(null);

  const uAll0 = activeClock.u(dataMin), uAll1 = activeClock.u(dataMax);
  const uAllSpan = Math.max(uAll1 - uAll0, 1);
  const thumbL = ((activeClock.u(d0) - uAll0) / uAllSpan) * 100;
  const thumbW = Math.max(((activeClock.u(d1) - activeClock.u(d0)) / uAllSpan) * 100, 1.5);

  /** Domain centered at track position `clientX`, span preserved. */
  const scrubCenterDomain = (clientX: number): [number, number] => {
    const rect = scrubRef.current!.getBoundingClientRect();
    const ck = clockRef.current;
    const { d0: a, d1: b, dataMin: mn, dataMax: mx } = domRef.current;
    const half = (ck.u(b) - ck.u(a)) / 2;
    const uc = ck.u(mn) +
      ((clientX - rect.left) / Math.max(rect.width, 1)) * Math.max(ck.u(mx) - ck.u(mn), 1);
    return clampDomain(ck.inv(uc - half), ck.inv(uc + half));
  };

  function onScrubDown(e: React.PointerEvent) {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    const rect = scrubRef.current!.getBoundingClientRect();
    const fracPct = ((e.clientX - rect.left) / Math.max(rect.width, 1)) * 100;
    let a = domRef.current.d0, b = domRef.current.d1;
    // Tap outside the thumb jump-centers first; the same drag continues
    // from the jumped-to window so down-and-slide feels like one motion.
    if (fracPct < thumbL || fracPct > thumbL + thumbW) {
      const nd = scrubCenterDomain(e.clientX);
      setDomain(nd);
      a = nd[0]; b = nd[1];
    }
    scrubGrab.current = { px: e.clientX, d0: a, d1: b };
  }
  function onScrubMove(e: React.PointerEvent) {
    const g = scrubGrab.current;
    const el = scrubRef.current;
    if (!g || !el) return;
    const rect = el.getBoundingClientRect();
    const ck = clockRef.current;
    const { dataMin: mn, dataMax: mx } = domRef.current;
    // Track px → history-fraction: the thumb tracks the finger 1:1.
    const du = ((e.clientX - g.px) / Math.max(rect.width, 1)) *
      Math.max(ck.u(mx) - ck.u(mn), 1);
    setDomain(clampDomain(ck.inv(ck.u(g.d0) + du), ck.inv(ck.u(g.d1) + du)));
  }
  const onScrubUp = () => { scrubGrab.current = null; };

  // ── Robinhood-style window P/L: equity change net of transfers ──
  const windowPL = useMemo(() => {
    const vis = activeRows.filter((p) => p.t >= d0 && p.t <= d1);
    if (vis.length < 2 && !(vis.length === 1 && readout)) return null;
    const start = vis[0];
    const end = readout && readout.t >= start.t ? readout : vis[vis.length - 1];
    let netX = 0;
    if (!baseHidden)   // overlay rows are already transfer-adjusted
      for (const x of xfers) if (x.t > start.t && x.t <= end.t) netX += x.amount;
    const pl = end.equity - start.equity - netX;
    const base = start.equity + Math.max(0, netX);
    return { pl, pct: base > 0 ? (pl / base) * 100 : 0, netX };
  }, [activeRows, baseHidden, d0, d1, readout, xfers]);

  // ── Draw ──
  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap || data.length === 0) return;
    const rect = wrap.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    const W = rect.width;
    const H = rect.height;
    // Overlay series registered for this chart's channel (drawn after the
    // main line). When one wants a top time axis, reserve headroom for it.
    const overlays =
      overlayChannel && Array.isArray(window.__qtOverlaySeries)
        ? window.__qtOverlaySeries.filter(
            (o) => o && o.channel === overlayChannel && Array.isArray(o.points) && o.points.length >= 1,
          )
        : [];
    const padT = overlays.some((o) => o.topAxisColor) ? 18 : PAD_T;
    const plotW = W - PAD_L - padR;
    const plotH = H - padT - PAD_B;
    // Stacked lanes: an overlay may claim the top slice of the plot; the
    // main series draws in what remains, so the two never overlap.
    const laneFrac = Math.min(0.6, overlays.reduce((m, o) => Math.max(m, o.lane ?? 0), 0));
    const laneGap = laneFrac > 0 ? 5 : 0;
    const laneH = plotH * laneFrac;
    const mainTop = padT + laneH + laneGap;
    const mainH = plotH - laneH - laneGap;

    // Visible slice PLUS one bracketing point each side, so the line always
    // reaches the plot edges while panning instead of clipping/jumping.
    let first = 0;
    while (first < data.length && data[first].t < d0) first++;
    let lastEx = first;
    while (lastEx < data.length && data[lastEx].t <= d1) lastEx++;
    let s = Math.max(0, first - 1);
    let e = Math.min(data.length, lastEx + 1);
    // Sparse window (0–1 points visible): widen to the nearest pair so we
    // draw the segment AROUND the window, never an unrelated tail slice.
    if (e - s < 2) {
      s = Math.max(0, e - 2);
      e = Math.min(data.length, s + 2);
    }
    const seg = data.slice(s, e);

    let mn = Infinity, mx = -Infinity;
    for (const p of seg) { const v = val(p); if (v < mn) mn = v; if (v > mx) mx = v; }
    // Small floor so zooming into a flat stretch lets the y-axis tighten far
    // enough to reveal 5¢/1¢ ticks; still gives a line some breathing room.
    const pad = Math.max((mx - mn) * 0.12, 0.02);
    const lo = mn - pad, hi = mx + pad;

    // All x-positions go through the session clock: weekends render
    // compressed on multi-day windows (see makeClock above).
    const ck = activeClock;
    const u0 = ck.u(d0), u1 = ck.u(d1);
    const X = (t: number) => PAD_L + ((ck.u(t) - u0) / Math.max(u1 - u0, 1)) * plotW;
    const Y = (v: number) => mainTop + (1 - (v - lo) / Math.max(hi - lo, 1e-9)) * mainH;

    ctx.clearRect(0, 0, W, H);
    const css = getComputedStyle(document.documentElement);
    const gridC = css.getPropertyValue("--bd").trim() || "#30363d";
    const mutedC = css.getPropertyValue("--muted").trim() || "#8b949e";

    const bHid = !!(window.__qtBaseHidden && overlayChannel);
    ctx.strokeStyle = gridC; ctx.fillStyle = mutedC;
    ctx.font = "9px ui-monospace, Menlo, monospace";
    ctx.setLineDash([3, 3]); ctx.lineWidth = 1;

    // Pick the smallest "nice" step so tick labels are round numbers and get
    // finer as you zoom in (~`target` ticks across the visible range).
    const niceStep = (range: number, cands: number[], target: number) =>
      cands.find((c) => c >= range / target) ?? cands[cands.length - 1];

    // ── Y grid + $ ticks — snapped to …1¢,2¢,5¢,10¢…$1,$2,$2.50,$5… with a
    // pixel-adaptive tick count, so each zoom level picks its own natural
    // spacing (tight ranges get fine ¢ graduations, wide ones stay uncluttered).
    const yTarget = Math.max(4, Math.min(9, Math.floor(mainH / 42)));
    const yStep = niceStep(hi - lo,
      [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500],
      yTarget);
    // Always dollars-and-cents: a $499.59 book must never read "$499.6".
    const yDec = 2;
    // With an overlay on, each side's numbers take its line's color — left
    // in the main series color, right in the overlay's — so the two axes
    // read as a matched, color-coded pair.
    if (overlays.length) ctx.fillStyle = color;
    // Overlay-only view: skip the base grid+labels entirely — the overlay
    // axis pass draws grid and $-labels at ITS OWN scale positions.
    if (!bHid)
      for (let v = Math.ceil(lo / yStep) * yStep, n = 0; v <= hi && n < 14; v += yStep, n++) {
        const y = Y(v);
        if (y < padT - 1 || y > padT + plotH + 1) continue;
        ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - padR, y); ctx.stroke();
        ctx.fillText(`$${v.toFixed(yDec)}`, 2, y + 3);
      }
    // A channel overlay may re-brand the bottom clock (bottomAxisColor); with
    // none claiming it, an overlay-paired chart falls back to its OWN base
    // color instead of losing its color the moment that overlay toggles off.
    const bottomAxisC = overlays.find((o) => o.bottomAxisColor)?.bottomAxisColor;
    ctx.fillStyle = bottomAxisC ?? (overlayChannel ? color : mutedC);

    // ── X time ticks — nice time step aligned to clean boundaries: dates at
    // week+ zoom, hours at day zoom, minutes as you pinch further in. ──
    const spanMs = d1 - d0;
    const MIN = 60e3, HR = 3600e3, DAY = 24 * HR;
    // Sub-day views get ~12 tick targets (Robert 2026-08-22: hourly markers
    // on 12H instead of 3-hourly); wider views keep the sparse 4. The 40px
    // spacing guard below thins any overflow on narrow screens.
    // Sub-day views target one tick per hour (1D = all 24, 12H = 12, 6H = 6
    // — Robert 2026-08-22); wider views keep the sparse 4. Bare-hour labels
    // are narrow, so the spacing guard relaxes for them.
    const tStep = niceStep(spanMs,
      [5 * MIN, 15 * MIN, 30 * MIN, HR, 2 * HR, 3 * HR, 6 * HR, 12 * HR, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY],
      spanMs <= DAY + MIN ? Math.min(24, Math.max(4, Math.round(spanMs / HR))) : 4);
    const labGap = spanMs <= DAY + MIN && tStep >= HR ? 22 : 40;
    const subDay = tStep < DAY;
    let tStart: number;
    if (subDay) {
      tStart = Math.ceil(d0 / tStep) * tStep;   // epoch-aligned → on the hour/5-min
    } else {
      const d = new Date(d0); d.setHours(0, 0, 0, 0);
      while (d.getTime() < d0) d.setDate(d.getDate() + 1);
      tStart = d.getTime();
    }
    let lastLabX = -1e9;
    for (let t = tStart, n = 0; t <= d1 && n < 40; t += tStep, n++) {
      const x = X(t);
      if (x < PAD_L - 1 || x > W - padR - 14) continue;
      // Compressed weekends bunch tick positions — keep labels legible.
      if (x - lastLabX < labGap) continue;
      lastLabX = x;
      const dt = new Date(t);
      let label: string;
      if (!subDay) {
        label = dt.toLocaleDateString([], { month: "short", day: "numeric" });
      } else {
        // Dates live in their own line above the range chips now — the axis
        // shows pure hour numbers (or HH:MM when zoomed under hour-steps).
        label = tStep >= HR
          ? String(dt.getHours())
          : dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
      }
      if (!(bHid && overlays.some((o) => o.topAxisColor)))
        ctx.fillText(label, Math.min(Math.max(x - 12, PAD_L), W - padR - 38), H - 6);
    }
    // Lane divider between the overlay band and the main series
    if (laneFrac > 0) {
      ctx.setLineDash([3, 3]);
      const dy = mainTop - laneGap / 2;
      ctx.beginPath(); ctx.moveTo(PAD_L, dy); ctx.lineTo(W - padR, dy); ctx.stroke();
    }
    // Baseline markers (kept inside the main lane so the overlay stays clean)
    ctx.setLineDash([4, 4]);
    for (const m of MARKERS) {
      if (m.ts < d0 || m.ts > d1) continue;
      const x = X(m.ts);
      ctx.beginPath(); ctx.moveTo(x, mainTop); ctx.lineTo(x, H - PAD_B); ctx.stroke();
      ctx.fillText(m.label, Math.min(x + 3, W - 90), mainTop + 9);
    }
    // Transfer markers: green = money in, red = money out. Drawn in both
    // views — in the raw view they explain the step; in the net view they
    // mark where a step was removed.
    for (const x of xfers) {
      if (x.t < d0 || x.t > d1) continue;
      const px = X(x.t);
      const c = x.amount >= 0 ? POS_C : NEG_C;
      ctx.strokeStyle = c; ctx.fillStyle = c;
      ctx.setLineDash([2, 4]);
      ctx.beginPath(); ctx.moveTo(px, mainTop); ctx.lineTo(px, H - PAD_B); ctx.stroke();
      ctx.setLineDash([]);
      const yb = H - PAD_B - 4;
      ctx.beginPath();
      if (x.amount >= 0) { ctx.moveTo(px, yb - 6); ctx.lineTo(px - 4, yb); ctx.lineTo(px + 4, yb); }
      else { ctx.moveTo(px, yb); ctx.lineTo(px - 4, yb - 6); ctx.lineTo(px + 4, yb - 6); }
      ctx.closePath(); ctx.fill();
      ctx.fillText(moneySigned(x.amount, 0), Math.min(px + 6, W - padR - 38), yb - 2);
    }
    ctx.strokeStyle = gridC; ctx.fillStyle = mutedC;
    ctx.setLineDash([]);

    // ── Overlay pass 1: shading + line painted BEFORE (under) the main
    // series — the primary line stays predominant, its translucent fill
    // blends with the overlay's where they overlap (olive where yellow
    // crosses green) instead of one series scribbling over the other. ──
    const oDrawn: {
      o: OverlaySeries;
      oseg: { t: number; v: number }[];
      OY: (v: number) => number;
      olo: number;
      ohi: number;
    }[] = [];
    for (const o of overlays) {
      const opts = o.points
        .filter((p) => Number.isFinite(p.t) && Number.isFinite(p.v))
        .sort((a, b) => a.t - b.t);
      // A lone valid point still reads as a line: synthesize a flat sibling
      // across the visible window (mirrors the Funded card's own
      // single-point handling) instead of requiring 2 raw points to draw.
      let oseg: { t: number; v: number }[];
      if (opts.length === 1) {
        oseg = [{ t: Math.min(opts[0].t, d0), v: opts[0].v }, { t: Math.max(opts[0].t, d1), v: opts[0].v }];
      } else {
        // Visible slice + one bracketing point each side (same policy as the
        // main line) so the overlay reaches the plot edges while panning.
        let of = 0;
        while (of < opts.length && opts[of].t < d0) of++;
        let ol = of;
        while (ol < opts.length && opts[ol].t <= d1) ol++;
        let os = Math.max(0, of - 1);
        let oe = Math.min(opts.length, ol + 1);
        if (oe - os < 2) { os = Math.max(0, oe - 2); oe = Math.min(opts.length, os + 2); }
        oseg = opts.slice(os, oe);
      }
      if (oseg.length < 2) continue;
      let omn = Infinity, omx = -Infinity;
      for (const p of oseg) { if (p.v < omn) omn = p.v; if (p.v > omx) omx = p.v; }
      const opad = Math.max((omx - omn) * 0.12, 0.02);
      const olo = omn - opad, ohi = omx + opad;
      const oH = laneFrac > 0 ? laneH : plotH;
      const oBot = padT + oH;
      const OY = (v: number) => padT + (1 - (v - olo) / Math.max(ohi - olo, 1e-9)) * oH;
      // Flat lead-in over the pre-history region, drawn SOLID and shaded as
      // part of the main path (owner preference: a young series reads as one
      // continuous first-class curve across the full window).
      const oplot = (o.leadIn && oseg[0].t > d0)
        ? [{ t: d0, v: oseg[0].v }, ...oseg]
        : oseg;
      ctx.save();
      ctx.beginPath(); ctx.rect(PAD_L, padT, plotW, plotH); ctx.clip();
      const ograd = ctx.createLinearGradient(0, padT, 0, oBot);
      ograd.addColorStop(0, o.color + "22"); ograd.addColorStop(1, o.color + "04");
      ctx.beginPath();
      ctx.moveTo(X(oplot[0].t), OY(oplot[0].v));
      for (const p of oplot) ctx.lineTo(X(p.t), OY(p.v));
      ctx.lineTo(X(oplot[oplot.length - 1].t), oBot);
      ctx.lineTo(X(oplot[0].t), oBot);
      ctx.closePath(); ctx.fillStyle = ograd; ctx.fill();
      ctx.beginPath();
      ctx.moveTo(X(oplot[0].t), OY(oplot[0].v));
      for (const p of oplot) ctx.lineTo(X(p.t), OY(p.v));
      ctx.strokeStyle = o.color; ctx.lineWidth = 1.8; ctx.stroke();
      // Live-end dot so a young series is findable even when it spans only
      // a sliver of the window.
      const oLast = oseg[oseg.length - 1];
      if (oLast.t >= d0 && oLast.t <= d1) {
        ctx.beginPath(); ctx.arc(X(oLast.t), OY(oLast.v), 3, 0, Math.PI * 2);
        ctx.fillStyle = o.color; ctx.fill();
      }
      ctx.restore();
      oDrawn.push({ o, oseg, OY, olo, ohi });
    }

    // Main area fill + line — painted OVER the overlays (predominant).
    // __qtBaseHidden (see the Window decl) lets an injector show an
    // overlay series alone.
    if (seg.length >= 2 && !bHid) {
      ctx.save();
      ctx.beginPath(); ctx.rect(PAD_L, padT, plotW, plotH); ctx.clip();
      const grad = ctx.createLinearGradient(0, mainTop, 0, H - PAD_B);
      grad.addColorStop(0, color + "40"); grad.addColorStop(1, color + "05");
      ctx.beginPath();
      ctx.moveTo(X(seg[0].t), Y(val(seg[0])));
      for (const p of seg) ctx.lineTo(X(p.t), Y(val(p)));
      ctx.lineTo(X(seg[seg.length - 1].t), H - PAD_B);
      ctx.lineTo(X(seg[0].t), H - PAD_B);
      ctx.closePath(); ctx.fillStyle = grad; ctx.fill();
      ctx.beginPath();
      ctx.moveTo(X(seg[0].t), Y(val(seg[0])));
      for (const p of seg) ctx.lineTo(X(p.t), Y(val(p)));
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();
      ctx.restore();
    }

    // ── Overlay pass 2: axes — right-edge value ticks + top clock, drawn
    // last so the small colored labels stay crisp above both fills. ──
    const rightExclusive = oDrawn.some(({ o }) => o.rightAxisExclusive);
    for (const { o, OY, olo, ohi } of oDrawn) {
      // One overlay may own the right gutter outright (rightAxisExclusive):
      // suppress the others' value ticks; top-clock rendering is unaffected.
      const skipRightTicks = rightExclusive && !o.rightAxisExclusive;
      const oStep = niceStep(ohi - olo,
        [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500],
        Math.max(3, Math.min(7, Math.floor(mainH / 56))));
      // Match the main axis: full dollars-and-cents, never rounded away.
      const oDec = 2;
      ctx.fillStyle = o.color;
      // Overlay axes render a step smaller than the main axes, right-aligned
      // hard against the screen edge — mirrors the left gutter's flushness.
      ctx.font = "8px ui-monospace, Menlo, monospace";
      ctx.textAlign = "right";
      for (let v = Math.ceil(olo / oStep) * oStep, n = 0; v <= ohi && n < 12; v += oStep, n++) {
        const y = OY(v);
        if (y < padT - 1 || y > padT + plotH + 1) continue;
        // rightAxisExclusive elsewhere in the channel: this overlay yields
        // the RIGHT gutter only — its left-gutter labels (bHid branch
        // below) keep rendering. Robert's layout: left/top = portfolio
        // green, right/bottom = funded yellow.
        if (!skipRightTicks) ctx.fillText(`$${v.toFixed(oDec)}`, W - 2, y + 3);
        // The hidden-base LEFT duplicate is for the series that owns the
        // left gutter — a rightAxisExclusive overlay (Funded) must never
        // print there too, or both scales interleave into garble
        // (Robert's screenshot 2026-08-25 06:54).
        if (bHid && !o.rightAxisExclusive) {
          ctx.strokeStyle = gridC; ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - padR, y); ctx.stroke();
          ctx.setLineDash([]);
          ctx.textAlign = "left";
          ctx.fillText(`$${v.toFixed(oDec)}`, 2, y + 3);
          ctx.textAlign = "right";
        }
      }
      ctx.textAlign = "left";
      // Top time axis in the overlay's topAxisColor
      if (o.topAxisColor) {
        ctx.fillStyle = o.topAxisColor;
        let lastTopX = -1e9;
        for (let t = tStart, n = 0; t <= d1 && n < 40; t += tStep, n++) {
          const x = X(t);
          if (x < PAD_L - 1 || x > W - padR - 14) continue;
          if (x - lastTopX < labGap) continue;
          lastTopX = x;
          const dt = new Date(t);
          let label: string;
          if (!subDay) {
            label = dt.toLocaleDateString([], { month: "short", day: "numeric" });
          } else {
            label = tStep >= HR
              ? String(dt.getHours())
              : dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
          }
          ctx.fillText(label, Math.min(Math.max(x - 12, PAD_L), W - padR - 38), 12);
        }
      }
      ctx.font = "9px ui-monospace, Menlo, monospace";
    }

    // ── Trade dots (Robert 2026-08-22): red=buy, blue=sell, on the crypto
    // overlay line; tap shows symbol + $ value. Y is interpolated onto the
    // overlay so each dot rides the line at its trade time. ──
    markHits.current = [];
    const _marksAll = (typeof window !== "undefined" && Array.isArray(window.__qtOverlayMarks))
      ? window.__qtOverlayMarks.filter((m) => m && m.channel === overlayChannel) : [];
    if (_marksAll.length && oDrawn.length) {
      // A mark may anchor to a specific overlay series (m.id) and carry its
      // own value/color/radius — falls back to the first drawn series and
      // the classic side colors, so legacy marks render unchanged.
      const yAtOn = (od2: typeof oDrawn[0], t: number, v?: number) => {
        if (v != null) return od2.OY(v);
        const seg = od2.oseg;
        if (t <= seg[0].t) return od2.OY(seg[0].v);
        if (t >= seg[seg.length - 1].t) return od2.OY(seg[seg.length - 1].v);
        for (let i = 1; i < seg.length; i++) {
          if (seg[i].t >= t) {
            const a = seg[i - 1], b = seg[i];
            const f = (t - a.t) / Math.max(b.t - a.t, 1);
            return od2.OY(a.v + (b.v - a.v) * f);
          }
        }
        return od2.OY(seg[seg.length - 1].v);
      };
      const od = oDrawn[0];
      const yAt = (t: number) => yAtOn(od, t);
      const BUY_C = "#f85149", SELL_C = "#4d9dff";
      for (const m of _marksAll) {
        if (m.t < d0 || m.t > d1) continue;
        // Un-anchored (no m.id) marks are only unambiguous when exactly one
        // overlay is drawn — with several, don't guess which one it belongs
        // to (better hidden than plotted at a nonsensical Y on the wrong line).
        if (!m.id && oDrawn.length !== 1) continue;
        const mod = (m.id && oDrawn.find((d) => (d.o as { id?: string }).id === m.id)) || od;
        if (m.id && (mod === od) && (od.o as { id?: string }).id !== m.id) continue; // anchored series not visible
        const mx2 = X(m.t), my2 = yAtOn(mod, m.t, m.v);
        const isSel = markSel && markSel.t === m.t && markSel.sym === m.sym && markSel.side === m.side;
        ctx.beginPath(); ctx.arc(mx2, my2, isSel ? 5 : (m.r ?? 3.2), 0, Math.PI * 2);
        ctx.fillStyle = m.color ?? (m.side === "buy" ? BUY_C : SELL_C);
        ctx.fill();
        ctx.lineWidth = 1; ctx.strokeStyle = m.stroke ?? "rgba(0,0,0,0.55)"; ctx.stroke();
        markHits.current.push({ x: mx2, y: my2, m: { side: m.side, sym: m.sym, usd: m.usd, t: m.t, pid: m.pid, pl: m.pl } });
      }
      // Pair line (Robert 2026-08-22): tapping a dot draws the trade's arc
      // from entry to exit, green if the round-trip won, red if it lost.
      if (markSel && markSel.pid) {
        const partner = _marksAll.find((mm) => mm.pid === markSel.pid && mm.side !== markSel.side);
        if (partner) {
          const x1 = X(markSel.t), y1 = yAt(markSel.t);
          const x2 = X(partner.t), y2 = yAt(partner.t);
          const won = (markSel.pl ?? partner.pl ?? 0) >= 0;
          ctx.save();
          ctx.beginPath(); ctx.rect(PAD_L, padT, plotW, plotH); ctx.clip();
          ctx.setLineDash([5, 4]); ctx.lineWidth = 1.6;
          ctx.strokeStyle = won ? "#3fb950" : "#f85149";
          ctx.beginPath(); ctx.moveTo(x2, y2); ctx.lineTo(x1, y1); ctx.stroke();
          ctx.setLineDash([]);
          ctx.beginPath(); ctx.arc(x2, y2, 4.5, 0, Math.PI * 2);
          ctx.fillStyle = partner.side === "buy" ? "#f85149" : "#4d9dff"; ctx.fill();
          ctx.lineWidth = 1.4; ctx.strokeStyle = won ? "#3fb950" : "#f85149"; ctx.stroke();
          ctx.restore();
        }
      }
      // Selected-dot tooltip
      if (markSel && markSel.t >= d0 && markSel.t <= d1) {
        const tx = X(markSel.t), ty = yAt(markSel.t);
        const label = (markSel.side === "buy" ? "BUY " : "SELL ") + markSel.sym +
          "  $" + Math.abs(markSel.usd).toFixed(2) +
          (markSel.pid && markSel.pl != null
            ? "  (" + (markSel.pl >= 0 ? "+" : "") + markSel.pl.toFixed(2) + ")" : "");
        ctx.font = "10px ui-monospace, Menlo, monospace";
        const tw = ctx.measureText(label).width + 12;
        let bx = tx + 8; if (bx + tw > W - 4) bx = tx - 8 - tw;
        const by = Math.max(padT + 2, ty - 26);
        ctx.fillStyle = "rgba(20,22,28,0.94)";
        ctx.strokeStyle = markSel.side === "buy" ? "#f85149" : "#4d9dff";
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.rect(bx, by, tw, 18); ctx.fill(); ctx.stroke();
        ctx.fillStyle = "#e6edf3"; ctx.textAlign = "left";
        ctx.fillText(label, bx + 6, by + 12);
        ctx.font = "9px ui-monospace, Menlo, monospace";
      }
    }
    // Readout crosshair
    if (readout && readout.t >= d0 && readout.t <= d1) {
      const useO = bHid && oDrawn.length > 0;
      const x = X(readout.t);
      const y = useO ? oDrawn[0].OY(val(readout)) : Y(val(readout));
      ctx.strokeStyle = mutedC; ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, H - PAD_B); ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = useO ? oDrawn[0].o.color : color; ctx.fill();
    }
  }, [data, d0, d1, color, readout, xfers, net, overlayChannel, overlayRev, markSel]);

  const zoomed = domain !== null;
  // Visible-window date(s) — shown above the range chips at EVERY span
  // (Robert 2026-08-22: wanted the date range on 1W+ views too)
  const dateLine = (() => {
    const f = (t: number) => new Date(t).toLocaleDateString([], { month: "short", day: "numeric" });
    const a = f(d0), b = f(d1);
    return a === b ? a : `${a} – ${b}`;
  })();
  const hasXfers = xfers.length > 0;

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-y-1 px-2 md:px-0">
        <span className="text-[10px] uppercase tracking-wider text-muted">
          Equity
          {windowPL && (
            <span className={`num ml-2 normal-case tracking-normal font-semibold ${signClass(windowPL.pl)}`}>
              {arrow(windowPL.pl)} {moneySigned(windowPL.pl)}{" "}
              <span className="opacity-80">({pct(windowPL.pct)})</span>
              {windowPL.netX !== 0 && (
                <span className="ml-1 font-normal text-faint" title="Deposits/withdrawals in this window are excluded from P/L">
                  net of {moneySigned(windowPL.netX)} transfers
                </span>
              )}
            </span>
          )}
          {readout && (
            <span className="num ml-2 normal-case tracking-normal text-fg">
              {money(val(readout))}
              {net && Math.abs(readout.adj - readout.equity) > 0.005 && (
                <span className="text-faint"> (acct {money(readout.equity)})</span>
              )}{" "}
              <span className="text-faint">{new Date(readout.t).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })}</span>
            </span>
          )}
        </span>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-1">
          {hasXfers && (
            <span title="P/L: deposits/withdrawals collapsed out of the line — only trading moves it. $: raw account value.">
              <Tabs
                small
                tabs={[{ id: "pl", label: "P/L" }, { id: "raw", label: "$" }]}
                active={net ? "pl" : "raw"}
                onChange={(id) => {
                  const v = id === "pl";
                  localStorage.setItem(LS_NET, v ? "1" : "0");
                  setNet(v);
                }}
              />
            </span>
          )}
          {zoomed && (
            <button
              onClick={() => setDomain(null)}
              className="rounded border border-bd px-1.5 py-0.5 text-[10px] text-muted"
              title="Reset zoom (or double-tap the chart)"
            >
              ⟲ reset
            </button>
          )}
          {dateLine && (
            <div className="num w-full text-left text-[10px] text-muted">{dateLine}</div>
          )}
          {/* 13 ranges don't fit a phone row — the strip scrolls sideways */}
          <div className="max-w-full overflow-x-auto">
            <Tabs small tabs={RANGES.map((r) => ({ id: r.id, label: r.label }))} active={range} onChange={setRange} />
          </div>
        </div>
      </div>
      {data.length === 0 ? (
        <p className="py-8 text-center text-xs text-faint">No equity points in this range.</p>
      ) : (
        <>
          <div
            ref={wrapRef}
            className="relative h-32 w-full select-none sm:h-44"
            style={{ touchAction: "none" }}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onPointerLeave={(e) => { if (e.pointerType === "mouse" && pointers.current.size === 0) setReadout(null); }}
          >
            <canvas ref={canvasRef} className="h-full w-full" />
          </div>
          <div
            ref={scrubRef}
            className="relative mt-1 h-2 w-full cursor-ew-resize touch-none select-none overflow-hidden rounded-full border border-bd"
            title="Slide to pan the window at the current zoom"
            onPointerDown={onScrubDown}
            onPointerMove={onScrubMove}
            onPointerUp={onScrubUp}
            onPointerCancel={onScrubUp}
          >
            <div
              className="absolute top-0 h-full rounded-full"
              style={{ left: `${thumbL}%`, width: `${thumbW}%`, background: color, opacity: 0.6, minWidth: 8 }}
            />
          </div>
        </>
      )}
      {/* gesture hint costs a text row on phones — the whole chart must fit
          above the fold there, and the gestures are discoverable by now */}
      <p className="mt-1 hidden px-2 text-[9px] text-faint md:block md:px-0">
        drag chart or slide the bar to pan (zoom stays) · slow pinch/scroll = fluid zoom · quick pinch = hop range presets · double-tap to reset
        {hasXfers && <> · ▲▼ deposits/withdrawals{net ? " (collapsed — line is trading P/L only)" : " (raw account value)"}</>}
      </p>
    </div>
  );
}

function nearest(data: Pt[], t: number): Pt {
  let best = data[0];
  let bd = Infinity;
  for (const p of data) {
    const d = Math.abs(p.t - t);
    if (d < bd) { bd = d; best = p; }
  }
  return best;
}
