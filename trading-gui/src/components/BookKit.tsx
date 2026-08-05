// Shared "Book kit" — the Book page's visual grammar (allocation drift bars,
// sortable positions table, symbol color scale) extracted so every data
// center (Lab drawers, etc.) renders books the exact same way.
import { useMemo, useState } from "react";
import { EmptyState, Table, Td, Th } from "./ui";
import { arrow, money, moneySigned, pct, pctPlain, signClass } from "../lib/format";
import type { Position } from "../lib/types";

/** High-contrast categorical palette for allocation bars — assigned by holding
 *  rank (biggest first) so neighbors are always maximally distinct. The same
 *  symbol gets the same color in both bars via a shared rank map. */
const ALLOC_PALETTE = [
  "#ffd60a", // gold
  "#58a6ff", // blue
  "#f85149", // red
  "#3fb950", // green
  "#bc8cff", // purple
  "#f78166", // orange
  "#39d2c0", // teal
  "#ff7eb6", // pink
  "#d2a8ff", // lavender
  "#9ccc2c", // lime
  "#e3b341", // amber
  "#79c0ff", // sky
  "#ffa657", // tangerine
  "#56d364", // mint
  "#ff9bce", // rose
  "#a5d6ff", // ice
];

/** Legacy export (hash → hue) kept for callers outside the Book kit. */
export function symColor(sym: string): string {
  let h = 0;
  for (const c of sym) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 55% 55%)`;
}

export function AllocationBars(props: {
  positions: Position[];
  equity: number;
  targets?: Record<string, number>;
  targetLabel?: string;
}) {
  const current = props.positions
    .map((p) => ({ sym: p.symbol, pct: props.equity > 0 ? (p.market_value / props.equity) * 100 : 0 }))
    .filter((x) => x.pct > 0.01)
    .sort((a, b) => b.pct - a.pct);
  const cash = Math.max(0, 100 - current.reduce((s, x) => s + x.pct, 0));

  const targets = Object.entries(props.targets ?? {})
    .map(([sym, p]) => ({ sym, pct: p }))
    .filter((x) => x.pct > 0.01)
    .sort((a, b) => b.pct - a.pct);
  const tCash = Math.max(0, 100 - targets.reduce((s, x) => s + x.pct, 0));

  // One color per symbol, assigned by its biggest footprint across both bars,
  // walking the palette in order — adjacent slices get contrasting colors.
  const rank = new Map<string, number>();
  for (const x of [...current, ...targets]) {
    rank.set(x.sym, Math.max(rank.get(x.sym) ?? 0, x.pct));
  }
  const colorOf = new Map<string, string>();
  [...rank.entries()]
    .sort((a, b) => b[1] - a[1])
    .forEach(([sym], i) => colorOf.set(sym, ALLOC_PALETTE[i % ALLOC_PALETTE.length]));

  const Bar = (rows: { sym: string; pct: number }[], cashPct: number, label: string) => (
    <div className="mb-2.5">
      <div className="mb-0.5 text-[9px] uppercase tracking-wider text-faint">{label}</div>
      <div className="flex h-4 w-full gap-[2px] overflow-hidden rounded">
        {rows.map((r) => (
          <div
            key={r.sym}
            title={`${r.sym} ${pctPlain(r.pct)}`}
            style={{ width: `${r.pct}%`, background: colorOf.get(r.sym), minWidth: 2 }}
            className="h-full"
          />
        ))}
        {cashPct > 0.5 && (
          <div title={`cash ${pctPlain(cashPct)}`} style={{ width: `${cashPct}%` }} className="h-full bg-panel2" />
        )}
      </div>
      {/* Per-bar legend: this bar's own holdings, biggest → smallest. */}
      <div className="mt-1 flex flex-wrap gap-x-2.5 gap-y-0.5">
        {rows.map((r) => (
          <span key={r.sym} className="num flex items-center gap-1 text-[10px] text-muted">
            <span className="h-2 w-2 rounded-sm" style={{ background: colorOf.get(r.sym) }} />
            {r.sym} <span className="text-faint">{r.pct.toFixed(1)}%</span>
          </span>
        ))}
        {cashPct > 0.5 && (
          <span className="num flex items-center gap-1 text-[10px] text-muted">
            <span className="h-2 w-2 rounded-sm bg-panel2" /> cash{" "}
            <span className="text-faint">{cashPct.toFixed(1)}%</span>
          </span>
        )}
      </div>
    </div>
  );

  return (
    <div>
      {Bar(current, cash, "current")}
      {targets.length > 0 ? (
        Bar(targets, tCash, props.targetLabel ?? "target")
      ) : (
        <p className="text-[10px] text-faint">No targets available.</p>
      )}
    </div>
  );
}

type SortKey = "symbol" | "qty" | "market_value" | "unrealized_pl" | "unrealized_pl_pct";

export function PositionsTable(props: { positions: Position[] }) {
  const [key, setKey] = useState<SortKey>("market_value");
  const [dir, setDir] = useState<"asc" | "desc">("desc");
  const rows = useMemo(() => {
    const r = [...props.positions];
    r.sort((a, b) => {
      const av = a[key];
      const bv = b[key];
      const cmp = typeof av === "string" ? String(av).localeCompare(String(bv)) : Number(av) - Number(bv);
      return dir === "asc" ? cmp : -cmp;
    });
    return r;
  }, [props.positions, key, dir]);

  const sortBy = (k: SortKey) => {
    if (k === key) setDir(dir === "asc" ? "desc" : "asc");
    else {
      setKey(k);
      setDir(k === "symbol" ? "asc" : "desc");
    }
  };
  const th = (k: SortKey, label: string, right = true) => (
    <Th right={right} onClick={() => sortBy(k)} active={key === k} dir={dir}>
      {label}
    </Th>
  );

  if (rows.length === 0) return <EmptyState title="No open positions" />;
  return (
    <Table
      head={
        <>
          {th("symbol", "Symbol", false)}
          {th("qty", "Qty")}
          {th("market_value", "Mkt value")}
          {th("unrealized_pl", "Unrl P/L")}
          {th("unrealized_pl_pct", "Unrl %")}
        </>
      }
    >
      {rows.map((p) => (
        <tr
          key={p.symbol}
          className={p.unrealized_pl > 0 ? "bg-pos/5" : p.unrealized_pl < 0 ? "bg-neg/5" : ""}
        >
          <Td className="font-semibold">{p.symbol}</Td>
          <Td right className="num text-muted">{p.qty.toFixed(4)}</Td>
          <Td right className="num">{money(p.market_value)}</Td>
          <Td right className={`num ${signClass(p.unrealized_pl)}`}>
            {arrow(p.unrealized_pl)} {moneySigned(p.unrealized_pl)}
          </Td>
          <Td right className={`num ${signClass(p.unrealized_pl_pct)}`}>{pct(p.unrealized_pl_pct)}</Td>
        </tr>
      ))}
    </Table>
  );
}
