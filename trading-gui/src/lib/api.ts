// Single typed API client. Base URL + key come from localStorage (Settings screen).
// The key is sent as `x-api-key` on every request. Never hardcode any secret here.

import type {
  GovernorHistory,
  LabBook,
  Activity,
  ActivityDetail,
  Bot,
  CongressState,
  EdgeResponse,
  EquityPoint,
  GovernorState,
  Health,
  InsiderLeaderboard,
  InsiderPortfolio,
  PatentCliffResponse,
  Portfolio,
  PropDeskState,
  QcState,
  SourceInfo,
  TickerPoint,
  TransferPoint,
  VersionInfo,
  WhalesResponse,
} from "./types";

// Default to the origin that served this app: over Tailscale that's the Mac
// directly (no key needed — the gate only applies to proxied requests), and
// via the public URL it's the sslip.io host. The hardcoded public URL is only
// the fallback for non-http contexts (file://, previews).
export const DEFAULT_BASE_URL =
  window.location.origin.startsWith("http")
    ? window.location.origin
    : "https://129-153-96-201.sslip.io";

const LS_BASE = "qt.baseUrl";
const LS_KEY = "qt.apiKey";
const LS_THEME = "qt.theme";

export function getBaseUrl(): string {
  return localStorage.getItem(LS_BASE)?.trim().replace(/\/+$/, "") || DEFAULT_BASE_URL;
}
export function setBaseUrl(url: string): void {
  localStorage.setItem(LS_BASE, url.trim().replace(/\/+$/, ""));
}
export function getApiKey(): string {
  return localStorage.getItem(LS_KEY) ?? "";
}
export function setApiKey(key: string): void {
  localStorage.setItem(LS_KEY, key);
}
export function getTheme(): "dark" | "light" {
  return localStorage.getItem(LS_THEME) === "light" ? "light" : "dark";
}
export function setTheme(t: "dark" | "light"): void {
  localStorage.setItem(LS_THEME, t);
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}
export class AuthError extends ApiError {
  constructor(status: number) {
    super(status, "Unauthorized — check your API key in Settings");
  }
}

/** Auth-failure hook: App registers a navigate-to-Settings handler here. */
let onAuthError: (() => void) | null = null;
export function registerAuthErrorHandler(fn: () => void): void {
  onAuthError = fn;
}

/**
 * Poll-staleness registry: records the last successful fetch time per endpoint
 * so the Health page can flag anything that hasn't refreshed in >90 s.
 */
const lastSuccess = new Map<string, number>();
export function getEndpointFreshness(): { path: string; ageMs: number }[] {
  const now = Date.now();
  return [...lastSuccess.entries()]
    .map(([path, t]) => ({ path, ageMs: now - t }))
    .sort((a, b) => b.ageMs - a.ageMs);
}

async function get<T>(path: string): Promise<T> {
  const url = `${getBaseUrl()}${path}`;
  const headers: Record<string, string> = {};
  const key = getApiKey();
  if (key) headers["x-api-key"] = key;

  let res: Response;
  try {
    res = await fetch(url, { headers });
  } catch (e) {
    throw new ApiError(0, `Network error reaching ${url}`);
  }
  if (res.status === 401 || res.status === 403) {
    // Role-gated endpoints 403 routinely for guest keys (owner-only data) —
    // that's a normal response, not a bad key. Only a rejected probe
    // (whoami/health, which every role may call) means the key itself is bad.
    if (path.startsWith("/api/whoami") || path.startsWith("/api/health")) {
      onAuthError?.();
    }
    throw new AuthError(res.status);
  }
  if (!res.ok) {
    throw new ApiError(res.status, `${res.status} ${res.statusText} on ${path}`);
  }
  const data = (await res.json()) as T;
  lastSuccess.set(path, Date.now());
  return data;
}

export const api = {
  health: () => get<Health>("/api/health"),
  /** Key role probe: owner sees everything; guest gets the Lab-only surface
   *  (Book hidden client-side AND live endpoints 403'd server-side). */
  whoami: () => get<{ role: "owner" | "guest" | "none" }>("/api/whoami"),
  sources: () => get<SourceInfo[]>("/api/sources"),
  portfolio: (source: string) => get<Portfolio>(`/api/portfolio/${source}`),
  portfolios: () => get<Portfolio[]>("/api/portfolio"),
  // Full-depth fetch: range buttons window client-side, so ask the server
  // for the longest span once (recent window is fine-grained via splicing).
  equity: (source: string) => get<EquityPoint[]>(`/api/equity/${source}?period=5Y`),
  /** Owner-only; guests must never call it (403 → auth redirect). */
  transfers: (source: string) => get<TransferPoint[]>(`/api/transfers/${source}`),
  activity: (source: string, limit = 50) =>
    get<Activity[]>(`/api/activity/${source}?limit=${limit}`),
  activityDetail: (source: string, symbol: string, side: string, reason: string) =>
    get<ActivityDetail>(
      `/api/activity_detail/${source}?symbol=${encodeURIComponent(symbol)}` +
      `&side=${encodeURIComponent(side)}&reason=${encodeURIComponent(reason)}`),
  qc: () => get<QcState>("/api/qc"),
  edge: () => get<EdgeResponse>("/api/edge"),
  bots: () => get<Bot[]>("/api/bots"),
  version: () => get<VersionInfo>("/api/version"),
  ticker: (symbol: string) =>
    get<TickerPoint[]>(`/api/ticker/${encodeURIComponent(symbol.toUpperCase())}`),
  whales: () => get<WhalesResponse>("/api/whales"),
  patentCliff: () => get<PatentCliffResponse>("/api/patent-cliff"),
  insiderLeaderboard: () => get<InsiderLeaderboard>("/api/insider/leaderboard"),
  insiderPortfolio: () => get<InsiderPortfolio>("/api/insider/portfolio"),
  governor: () => get<GovernorState>("/api/governor"),
  governorHistory: () => get<GovernorHistory>("/api/governor/history?limit=500"),
  congress: () => get<CongressState>("/api/congress"),
  propDesk: () => get<PropDeskState>("/api/prop_desk/state"),
  labbook: (name: string) => get<LabBook>(`/api/labbook/${encodeURIComponent(name)}`),
  /** Manual connection test returning round-trip ms. */
  async testConnection(): Promise<{ ok: boolean; ms: number; detail: string }> {
    const t0 = performance.now();
    try {
      const h = await get<Health>("/api/health");
      const ms = Math.round(performance.now() - t0);
      return { ok: h.ok, ms, detail: `host ${h.host} · v${h.version}` };
    } catch (e) {
      const ms = Math.round(performance.now() - t0);
      const detail = e instanceof Error ? e.message : "unknown error";
      return { ok: false, ms, detail };
    }
  },
};
