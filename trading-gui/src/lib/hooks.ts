// react-query data layer: 20 s polling, 15 s staleTime, retry with backoff.

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export const POLL_MS = 20_000;
export const STALE_MS = 15_000;

export const common = {
  refetchInterval: POLL_MS,
  staleTime: STALE_MS,
  retry: 2,
  retryDelay: (attempt: number) => Math.min(1000 * 2 ** attempt, 10_000),
} as const;

export function useHealth() {
  return useQuery({ queryKey: ["health"], queryFn: api.health, ...common, retry: 1 });
}
export function useSources() {
  return useQuery({ queryKey: ["sources"], queryFn: api.sources, ...common });
}
export function usePortfolio(source: string) {
  return useQuery({
    queryKey: ["portfolio", source],
    queryFn: () => api.portfolio(source),
    ...common,
  });
}
export function useEquity(source: string) {
  return useQuery({
    queryKey: ["equity", source],
    queryFn: () => api.equity(source),
    ...common,
  });
}
/** Owner-only funding history (deposits/withdrawals). Enabled must be false
 *  for guests — the endpoint 403s and the auth handler would bounce them to
 *  Settings. Transfers change rarely; poll at 5 min, not the 20 s cadence. */
export function useTransfers(source: string, enabled: boolean) {
  return useQuery({
    queryKey: ["transfers", source],
    queryFn: () => api.transfers(source),
    enabled,
    refetchInterval: 300_000,
    staleTime: 240_000,
    retry: false,
  });
}
export function useActivity(source: string, limit = 50) {
  return useQuery({
    queryKey: ["activity", source, limit],
    queryFn: () => api.activity(source, limit),
    ...common,
  });
}
export function useQc() {
  return useQuery({ queryKey: ["qc"], queryFn: api.qc, ...common });
}
export function useEdge() {
  return useQuery({ queryKey: ["edge"], queryFn: api.edge, ...common });
}
export function useBots() {
  return useQuery({ queryKey: ["bots"], queryFn: api.bots, ...common });
}
export function useVersion() {
  return useQuery({ queryKey: ["version"], queryFn: api.version, ...common });
}
export function useWhales() {
  return useQuery({ queryKey: ["whales"], queryFn: api.whales, ...common });
}
export function usePatentCliff() {
  return useQuery({ queryKey: ["patent-cliff"], queryFn: api.patentCliff, ...common });
}
export function useInsiderLeaderboard() {
  return useQuery({
    queryKey: ["insider-leaderboard"],
    queryFn: api.insiderLeaderboard,
    ...common,
  });
}
export function useInsiderPortfolio() {
  return useQuery({
    queryKey: ["insider-portfolio"],
    queryFn: api.insiderPortfolio,
    ...common,
  });
}
export function useTicker(symbol: string) {
  return useQuery({
    queryKey: ["ticker", symbol],
    queryFn: () => api.ticker(symbol),
    enabled: symbol.length > 0,
    ...common,
  });
}
/** Governor endpoint is not exposed yet — expected to 404; the page renders an empty state. */
export function useGovernor() {
  return useQuery({
    queryKey: ["governor"],
    queryFn: api.governor,
    ...common,
    retry: false,
  });
}

export function useWhoami() {
  return useQuery({
    queryKey: ["whoami"],
    queryFn: api.whoami,
    staleTime: 60_000,
    retry: 1,
  });
}

/** True until the key is proven to be the owner key — guests and unknown
 *  keys both get the Lab-only surface. Server enforcement is the real gate;
 *  this only shapes navigation. */
export function useIsGuest(): boolean {
  const w = useWhoami();
  return w.data?.role !== "owner";
}

export function useCongress() {
  return useQuery({ queryKey: ["congress"], queryFn: api.congress, ...common });
}

export function usePropDesk() {
  return useQuery({ queryKey: ["prop-desk"], queryFn: api.propDesk, ...common });
}

/** Owner-only per-strategy data center; guests 403 → treated as absent. */
export function useLabbook(name: string) {
  return useQuery({
    queryKey: ["labbook", name],
    queryFn: () => api.labbook(name),
    ...common,
    retry: false,
  });
}

export function useGovernorHistory() {
  return useQuery({
    queryKey: ["governor-history"],
    queryFn: api.governorHistory,
    ...common,
    retry: false,
  });
}
