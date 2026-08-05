// Minimal service worker: exists so Android Chrome treats /guest as an
// installable PWA. Network-first passthrough — live trading data must
// never be served stale from cache.
self.addEventListener("install", (e) => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  e.respondWith(fetch(e.request).catch(() =>
    new Response("offline", { status: 503, statusText: "offline" })
  ));
});
