import { useEffect, useMemo, useRef, useState } from "react";
import type { EquityPoint, TransferPoint } from "../lib/types";
import { toEpochMs, money, moneySigned, pct, signClass, arrow } from "../lib/format";
import { Tabs } from "./ui";

const RANGES = [
  { id: "1D", label: "1D", ms: 24 * 3600e3 },
  { id: "1W", label: "1W", ms: 7 * 24 * 3600e3 },
  { id: "1M", label: "1M", ms: 31 * 24 * 3600e3 },
  { id: "3M", label: "3M", ms: 92 * 24 * 3600e3 },
  { id: "6M", label: "6M", ms: 183 * 24 * 3600e3 },
  { id: "ALL", label: "ALL", ms: Infinity },
];

/** Baseline markers: honest test window start + inception (per API.md semantics). */
const MARKERS = [
  { ts: new Date("2026-07-27T00:00:00-04:00").getTime(), label: "test $48.06" },
  { ts: new Date("2026-07-13T00:00:00-04:00").getTime(), label: "inception $50.04" },
];

const PAD_L = 46;
const PAD_B = 20;
const PAD_T = 8;
const MIN_SPAN_MS = 10 * 60e3; // can't zoom tighter than 10 minutes
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
}) {
  const [range, setRange] = useState("ALL");
  // DEFAULT COLLAPSED: P/L is made by trading, not by moving money in or out,
  // so the line ships with transfer steps removed. "raw" opts into the
  // account-value view (axis rescales across deposits) and is remembered.
  const [net, setNet] = useState(() => localStorage.getItem(LS_NET) !== "0");
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

  const data: Pt[] = useMemo(() => {
    const win = RANGES.find((r) => r.id === range)?.ms ?? Infinity;
    if (win === Infinity) return allRows;
    const cutoff = Date.now() - win;
    return allRows.filter((p) => p.t >= cutoff);
  }, [allRows, range]);

  const val = (p: Pt) => (net ? p.adj : p.equity);

  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  // Visible time domain [t0, t1]; null = fit all data in range.
  const [domain, setDomain] = useState<[number, number] | null>(null);
  const [readout, setReadout] = useState<Pt | null>(null);

  const dataMin = data.length ? data[0].t : 0;
  const dataMax = data.length ? data[data.length - 1].t : 1;
  const d0 = domain ? domain[0] : dataMin;
  const d1 = domain ? domain[1] : dataMax;

  // Refs mirror the live domain/extent so native listeners and mid-gesture
  // math never read a stale render closure (the 20 s poll appends points).
  const domRef = useRef({ d0, d1, dataMin, dataMax });
  domRef.current = { d0, d1, dataMin, dataMax };

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

  // ── Gesture state (refs — no re-render churn mid-gesture) ──
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gestureStart = useRef<{ d0: number; d1: number; x: number; dist: number; mid: number } | null>(null);
  const lastTap = useRef(0);

  const xToT = (x: number, w: number) => {
    const { d0: a, d1: b } = domRef.current;
    return a + ((x - PAD_L) / Math.max(w - PAD_L - 6, 1)) * (b - a);
  };

  function onPointerDown(e: React.PointerEvent) {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pts = [...pointers.current.values()];
    if (pts.length === 1) {
      // Double-tap reset — and start the follow-on gesture from the RESET
      // domain, not the pre-reset one, so tap-tap-drag doesn't jump.
      const now = Date.now();
      const doubleTap = now - lastTap.current < 300;
      if (doubleTap) setDomain(null);
      lastTap.current = now;
      const { d0: a, d1: b, dataMin: mn, dataMax: mx } = domRef.current;
      gestureStart.current = doubleTap
        ? { d0: mn, d1: mx, x: pts[0].x, dist: 0, mid: pts[0].x }
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
        setReadout(nearest(data, t));
      }
      return;
    }
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const g = gestureStart.current;
    if (!g) return;
    const pts = [...pointers.current.values()];
    const w = Math.max(rect.width - PAD_L - 6, 1);

    if (pts.length === 1) {
      // PAN: pixel delta → time delta on the domain captured at gesture start
      const dxT = ((pts[0].x - g.x) / w) * (g.d1 - g.d0);
      setDomain(clampDomain(g.d0 - dxT, g.d1 - dxT));
      if (data.length) setReadout(nearest(data, xToT(pts[0].x - rect.left, rect.width)));
    } else if (pts.length >= 2) {
      // PINCH: scale the start-domain around the pinch midpoint
      const dist = Math.max(Math.abs(pts[0].x - pts[1].x), 1);
      const scale = g.dist / dist;
      const midT = g.d0 + (((g.mid - rect.left) - PAD_L) / w) * (g.d1 - g.d0);
      const lo = midT - (midT - g.d0) * scale;
      const hi = midT + (g.d1 - midT) * scale;
      setDomain(clampDomain(lo, hi));
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
      const w = Math.max(rect.width - PAD_L - 6, 1);
      const { d0: a, d1: b } = domRef.current;
      const midT = a + (((e.clientX - rect.left) - PAD_L) / w) * (b - a);
      const scale = e.deltaY > 0 ? 1.15 : 0.87;
      setDomain(clampDomain(midT - (midT - a) * scale, midT + (b - midT) * scale));
    };
    el.addEventListener("wheel", onWheelNative, { passive: false });
    return () => el.removeEventListener("wheel", onWheelNative);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasData]);

  // ── Robinhood-style window P/L: equity change net of transfers ──
  const windowPL = useMemo(() => {
    const vis = data.filter((p) => p.t >= d0 && p.t <= d1);
    if (vis.length < 2 && !(vis.length === 1 && readout)) return null;
    const start = vis[0];
    const end = readout && readout.t >= start.t ? readout : vis[vis.length - 1];
    let netX = 0;
    for (const x of xfers) if (x.t > start.t && x.t <= end.t) netX += x.amount;
    const pl = end.equity - start.equity - netX;
    const base = start.equity + Math.max(0, netX);
    return { pl, pct: base > 0 ? (pl / base) * 100 : 0, netX };
  }, [data, d0, d1, readout, xfers]);

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
    const plotW = W - PAD_L - 6;
    const plotH = H - PAD_T - PAD_B;

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

    const X = (t: number) => PAD_L + ((t - d0) / Math.max(d1 - d0, 1)) * plotW;
    const Y = (v: number) => PAD_T + (1 - (v - lo) / Math.max(hi - lo, 1e-9)) * plotH;

    ctx.clearRect(0, 0, W, H);
    const css = getComputedStyle(document.documentElement);
    const gridC = css.getPropertyValue("--bd").trim() || "#30363d";
    const mutedC = css.getPropertyValue("--muted").trim() || "#8b949e";

    ctx.strokeStyle = gridC; ctx.fillStyle = mutedC;
    ctx.font = "10px ui-monospace, Menlo, monospace";
    ctx.setLineDash([3, 3]); ctx.lineWidth = 1;

    // Pick the smallest "nice" step so tick labels are round numbers and get
    // finer as you zoom in (~`target` ticks across the visible range).
    const niceStep = (range: number, cands: number[], target: number) =>
      cands.find((c) => c >= range / target) ?? cands[cands.length - 1];

    // ── Y grid + $ ticks — snapped to …1¢,5¢,10¢,25¢,50¢,$1,$2.50,$5… ──
    const yStep = niceStep(hi - lo, [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 25, 50, 100, 250, 500], 5);
    const yDec = yStep < 1 ? 2 : Number.isInteger(yStep) ? 0 : 1;
    for (let v = Math.ceil(lo / yStep) * yStep, n = 0; v <= hi && n < 14; v += yStep, n++) {
      const y = Y(v);
      if (y < PAD_T - 1 || y > PAD_T + plotH + 1) continue;
      ctx.beginPath(); ctx.moveTo(PAD_L, y); ctx.lineTo(W - 6, y); ctx.stroke();
      ctx.fillText(`$${v.toFixed(yDec)}`, 2, y + 3);
    }

    // ── X time ticks — nice time step aligned to clean boundaries: dates at
    // week+ zoom, hours at day zoom, minutes as you pinch further in. ──
    const spanMs = d1 - d0;
    const MIN = 60e3, HR = 3600e3, DAY = 24 * HR;
    const tStep = niceStep(spanMs, [5 * MIN, 15 * MIN, 30 * MIN, HR, 2 * HR, 3 * HR, 6 * HR, 12 * HR, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY], 4);
    const subDay = tStep < DAY;
    let tStart: number;
    if (subDay) {
      tStart = Math.ceil(d0 / tStep) * tStep;   // epoch-aligned → on the hour/5-min
    } else {
      const d = new Date(d0); d.setHours(0, 0, 0, 0);
      while (d.getTime() < d0) d.setDate(d.getDate() + 1);
      tStart = d.getTime();
    }
    let prevDay = "";
    for (let t = tStart, n = 0; t <= d1 && n < 40; t += tStep, n++) {
      const x = X(t);
      if (x < PAD_L - 1 || x > W - 20) continue;
      const dt = new Date(t);
      let label: string;
      if (!subDay) {
        label = dt.toLocaleDateString([], { month: "short", day: "numeric" });
      } else {
        const dayKey = dt.toDateString();
        // First tick of each visible day shows the date; the rest show time.
        label = dayKey !== prevDay
          ? dt.toLocaleDateString([], { month: "short", day: "numeric" })
          : dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
        prevDay = dayKey;
      }
      ctx.fillText(label, Math.min(Math.max(x - 12, PAD_L), W - 44), H - 6);
    }
    // Baseline markers
    ctx.setLineDash([4, 4]);
    for (const m of MARKERS) {
      if (m.ts < d0 || m.ts > d1) continue;
      const x = X(m.ts);
      ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, H - PAD_B); ctx.stroke();
      ctx.fillText(m.label, Math.min(x + 3, W - 90), PAD_T + 9);
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
      ctx.beginPath(); ctx.moveTo(px, PAD_T); ctx.lineTo(px, H - PAD_B); ctx.stroke();
      ctx.setLineDash([]);
      const yb = H - PAD_B - 4;
      ctx.beginPath();
      if (x.amount >= 0) { ctx.moveTo(px, yb - 6); ctx.lineTo(px - 4, yb); ctx.lineTo(px + 4, yb); }
      else { ctx.moveTo(px, yb); ctx.lineTo(px - 4, yb - 6); ctx.lineTo(px + 4, yb - 6); }
      ctx.closePath(); ctx.fill();
      ctx.fillText(moneySigned(x.amount, 0), Math.min(px + 6, W - 44), yb - 2);
    }
    ctx.strokeStyle = gridC; ctx.fillStyle = mutedC;
    ctx.setLineDash([]);

    // Area fill + line (clipped to the plot so bracketing points don't spill)
    if (seg.length >= 2) {
      ctx.save();
      ctx.beginPath(); ctx.rect(PAD_L, PAD_T, plotW, plotH); ctx.clip();
      const grad = ctx.createLinearGradient(0, PAD_T, 0, H - PAD_B);
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
    // Readout crosshair
    if (readout && readout.t >= d0 && readout.t <= d1) {
      const x = X(readout.t), y = Y(val(readout));
      ctx.strokeStyle = mutedC; ctx.setLineDash([2, 3]);
      ctx.beginPath(); ctx.moveTo(x, PAD_T); ctx.lineTo(x, H - PAD_B); ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.arc(x, y, 3.5, 0, Math.PI * 2);
      ctx.fillStyle = color; ctx.fill();
    }
  }, [data, d0, d1, color, readout, xfers, net]);

  const zoomed = domain !== null;
  const hasXfers = xfers.length > 0;

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
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
              <span className="text-faint">{new Date(readout.t).toLocaleString()}</span>
            </span>
          )}
        </span>
        <div className="flex items-center gap-1">
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
          <Tabs small tabs={RANGES.map((r) => ({ id: r.id, label: r.label }))} active={range} onChange={setRange} />
        </div>
      </div>
      {data.length === 0 ? (
        <p className="py-8 text-center text-xs text-faint">No equity points in this range.</p>
      ) : (
        <div
          ref={wrapRef}
          className="relative h-48 w-full select-none"
          style={{ touchAction: "none" }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onPointerLeave={(e) => { if (e.pointerType === "mouse" && pointers.current.size === 0) setReadout(null); }}
        >
          <canvas ref={canvasRef} className="h-full w-full" />
        </div>
      )}
      <p className="mt-1 text-[9px] text-faint">
        drag to pan · pinch or scroll to zoom · double-tap to reset
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
