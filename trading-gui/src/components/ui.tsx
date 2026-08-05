// Hand-rolled primitives (Card, Badge, Tabs, Table, Skeleton, ProgressRing, EmptyState).
// Deliberately small — no component library.

import type { ReactNode } from "react";

export function Card(props: {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  pad?: boolean;
}) {
  const { title, right, children, className = "", pad = true } = props;
  return (
    <section className={`min-w-0 max-w-full overflow-hidden rounded-lg border border-bd bg-panel ${className}`}>
      {(title || right) && (
        <header className="flex items-center justify-between gap-2 border-b border-bd px-3 py-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted">{title}</h2>
          {right && <div className="flex items-center gap-2">{right}</div>}
        </header>
      )}
      <div className={pad ? "p-3" : ""}>{children}</div>
    </section>
  );
}

export type BadgeTone =
  | "green"
  | "red"
  | "blue"
  | "amber"
  | "gray"
  | "gold"
  | "purple";

const badgeTones: Record<BadgeTone, string> = {
  green: "bg-pos/15 text-pos border-pos/40",
  red: "bg-neg/15 text-neg border-neg/40",
  blue: "bg-blue/15 text-blue border-blue/40",
  amber: "bg-amber/15 text-amber border-amber/40",
  gray: "bg-muted/10 text-muted border-bd",
  gold: "bg-gold/15 text-gold border-gold/40",
  purple: "bg-purple/15 text-purple border-purple/40",
};

export function Badge(props: { tone?: BadgeTone; children: ReactNode; title?: string }) {
  const tone = props.tone ?? "gray";
  return (
    <span
      title={props.title}
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${badgeTones[tone]}`}
    >
      {props.children}
    </span>
  );
}

export function Tabs(props: {
  tabs: { id: string; label: ReactNode }[];
  active: string;
  onChange: (id: string) => void;
  small?: boolean;
}) {
  return (
    <div
      role="tablist"
      className={`inline-flex rounded-md border border-bd bg-panel2 p-0.5 ${props.small ? "text-[11px]" : "text-xs"}`}
    >
      {props.tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={props.active === t.id}
          onClick={() => props.onChange(t.id)}
          className={`rounded px-2.5 py-1 font-medium transition-colors ${
            props.active === t.id
              ? "bg-panel text-fg shadow-sm border border-bd"
              : "text-muted hover:text-fg"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Th(props: {
  children?: ReactNode;
  right?: boolean;
  onClick?: () => void;
  active?: boolean;
  dir?: "asc" | "desc";
}) {
  return (
    <th
      onClick={props.onClick}
      className={`whitespace-nowrap px-2 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted ${
        props.right ? "text-right" : "text-left"
      } ${props.onClick ? "cursor-pointer select-none hover:text-fg" : ""}`}
    >
      {props.children}
      {props.active && <span className="ml-0.5">{props.dir === "asc" ? "↑" : "↓"}</span>}
    </th>
  );
}

export function Td(props: { children?: ReactNode; right?: boolean; className?: string; title?: string }) {
  return (
    <td
      title={props.title}
      className={`whitespace-nowrap px-2 py-1.5 text-xs ${props.right ? "text-right" : "text-left"} ${props.className ?? ""}`}
    >
      {props.children}
    </td>
  );
}

export function Table(props: { head: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={`scroll-x ${props.className ?? ""}`}>
      <table className="w-full border-collapse">
        <thead className="border-b border-bd">
          <tr>{props.head}</tr>
        </thead>
        <tbody className="divide-y divide-bd/60">{props.children}</tbody>
      </table>
    </div>
  );
}

export function Skeleton(props: { className?: string; lines?: number }) {
  const lines = props.lines ?? 1;
  return (
    <div className={props.className}>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton mb-2 h-4 w-full last:mb-0" />
      ))}
    </div>
  );
}

export function EmptyState(props: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-bd p-6 text-center">
      <p className="text-sm font-medium text-muted">{props.title}</p>
      {props.children && <div className="mt-2 text-xs text-faint">{props.children}</div>}
    </div>
  );
}

/** SVG progress ring, e.g. toward the n≥30 closed-trade gate. */
export function ProgressRing(props: {
  value: number;
  max: number;
  size?: number;
  color?: string;
  label?: string;
}) {
  const size = props.size ?? 40;
  const stroke = 4;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const frac = Math.max(0, Math.min(1, props.max > 0 ? props.value / props.max : 0));
  return (
    <svg width={size} height={size} role="img" aria-label={`${props.value} of ${props.max}`}>
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke="var(--bd)"
        strokeWidth={stroke}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={r}
        fill="none"
        stroke={props.color ?? "#58a6ff"}
        strokeWidth={stroke}
        strokeDasharray={`${c * frac} ${c * (1 - frac)}`}
        strokeDashoffset={c / 4}
        strokeLinecap="round"
      />
      <text
        x="50%"
        y="50%"
        dominantBaseline="central"
        textAnchor="middle"
        className="num"
        style={{ fill: "var(--fg)", fontSize: size / 3.6 }}
      >
        {props.label ?? props.value}
      </text>
    </svg>
  );
}

/** Red banner for stale broker data — spec: never render placeholder numbers as live. */
export function StaleBanner(props: { source: string; ts?: string }) {
  return (
    <div
      role="alert"
      className="flex items-center gap-2 rounded-md border border-neg/50 bg-neg/10 px-3 py-2 text-xs font-semibold text-neg"
    >
      <span aria-hidden>⛔</span>
      <span>
        Broker data unavailable for {props.source} — figures below are placeholders, not live.
        {props.ts ? ` Last payload ${props.ts}.` : ""}
      </span>
    </div>
  );
}
