// Formatting helpers — money to 2 dp with $, percents with explicit +/−, relative timestamps.

export function money(v: number | null | undefined, dp = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}$${Math.abs(v).toFixed(dp)}`;
}

/** Signed money: +$1.23 / −$1.23 */
export function moneySigned(v: number | null | undefined, dp = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "±";
  return `${sign}$${Math.abs(v).toFixed(dp)}`;
}

/** Signed percent: +1.23% / −1.23% */
export function pct(v: number | null | undefined, dp = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "±";
  return `${sign}${Math.abs(v).toFixed(dp)}%`;
}

/** Unsigned percent for weights/allocations. */
export function pctPlain(v: number | null | undefined, dp = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(dp)}%`;
}

export function signClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return "text-muted";
  return v > 0 ? "text-pos" : "text-neg";
}

export function arrow(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v) || v === 0) return "";
  return v > 0 ? "▲" : "▼";
}

export function toEpochMs(ts: string | number): number {
  if (typeof ts === "number") return ts > 1e12 ? ts : ts * 1000;
  const n = Number(ts);
  if (!Number.isNaN(n) && ts.trim() !== "") return n > 1e12 ? n : n * 1000;
  return new Date(ts).getTime();
}

/** Relative time: "3 m ago" — absolute value goes in the title attribute. */
export function relTime(ts: string | number | null | undefined): string {
  if (ts === null || ts === undefined) return "—";
  const ms = toEpochMs(ts);
  if (Number.isNaN(ms)) return "—";
  const diff = Date.now() - ms;
  if (diff < 0) return "now";
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s} s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24);
  return `${d} d ago`;
}

export function absTime(ts: string | number | null | undefined): string {
  if (ts === null || ts === undefined) return "";
  const ms = toEpochMs(ts);
  if (Number.isNaN(ms)) return String(ts);
  return new Date(ms).toLocaleString();
}
