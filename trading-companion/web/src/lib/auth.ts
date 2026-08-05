import { writable, type Writable } from 'svelte/store';

const isBrowser = typeof window !== 'undefined';

function persisted(key: string, initial: string | null): Writable<string | null> {
  const stored = isBrowser ? window.localStorage.getItem(key) : null;
  const store = writable<string | null>(stored ?? initial);
  if (isBrowser) {
    store.subscribe((v) => {
      if (v === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, v);
    });
  }
  return store;
}

export const backendUrl = persisted('backend_url', null);
export const apiToken = persisted('api_token', null);

export function isOnboarded(url: string | null, token: string | null): boolean {
  return !!url && !!token;
}
