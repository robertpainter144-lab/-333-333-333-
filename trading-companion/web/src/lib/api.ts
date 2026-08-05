import { get } from 'svelte/store';
import { backendUrl, apiToken } from './auth';
import type {
  IBKRAuthStatus,
  OrderRequest,
  Position,
  SignalsResponse
} from './types';

export class APIError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const base = get(backendUrl);
  const token = get(apiToken);
  if (!base || !token) throw new APIError(0, 'backend not configured');

  const url = new URL(path.replace(/^\//, ''), base.endsWith('/') ? base : base + '/');
  const r = await fetch(url.toString(), {
    ...init,
    headers: {
      ...(init.headers || {}),
      Authorization: `Bearer ${token}`
    }
  });
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new APIError(r.status, `HTTP ${r.status}: ${body}`);
  }
  return r.json() as Promise<T>;
}

export const api = {
  signals: () => call<SignalsResponse>('/signals'),
  positions: () => call<Position[]>('/portfolio/positions'),
  ibkrStatus: () => call<IBKRAuthStatus>('/auth/ibkr/status'),
  placeOrder: (order: OrderRequest) =>
    call<unknown>('/orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(order)
    })
};

export function ibkrConnectURL(base: string): string {
  return new URL('auth/ibkr/start', base.endsWith('/') ? base : base + '/').toString();
}
