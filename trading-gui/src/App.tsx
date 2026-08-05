import { useEffect, useRef, useState } from "react";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import Layout from "./components/Layout";
import Book from "./pages/Book";
import Lab from "./pages/Lab";
import Governor from "./pages/Governor";
import Markets from "./pages/Markets";
import HealthPage from "./pages/HealthPage";
import Settings from "./pages/Settings";
import { registerAuthErrorHandler } from "./lib/api";

/**
 * OTA build check. The Android app / installed PWA keeps this SPA alive for
 * days, so `cache-control: no-cache` on the shell never gets a chance to
 * deliver a new deploy. Poll the shell (5 min + every return to foreground),
 * compare the hashed bundle name, and when it changes: reload silently the
 * moment the app is backgrounded, or offer a one-tap banner while visible.
 */
function useAutoUpdate(): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const current =
      document.querySelector<HTMLScriptElement>('script[src*="assets/index-"]')?.src ?? "";
    if (!current) return;
    let stopped = false;
    const check = async () => {
      try {
        const res = await fetch(import.meta.env.BASE_URL, { cache: "no-store" });
        const m = (await res.text()).match(/assets\/index-[\w-]+\.js/);
        if (!stopped && m && !current.includes(m[0])) setReady(true);
      } catch {
        /* offline / server restarting — try again next tick */
      }
    };
    const iv = setInterval(check, 5 * 60_000);
    const onVis = () => { if (document.visibilityState === "visible") check(); };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      stopped = true;
      clearInterval(iv);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);
  useEffect(() => {
    if (!ready) return;
    const onHide = () => { if (document.visibilityState === "hidden") location.reload(); };
    document.addEventListener("visibilitychange", onHide);
    return () => document.removeEventListener("visibilitychange", onHide);
  }, [ready]);
  return ready;
}

export default function App() {
  const navigate = useNavigate();
  const loc = useLocation();
  const locRef = useRef(loc.pathname);
  locRef.current = loc.pathname;

  useEffect(() => {
    // 401/403 → route to Settings with a message; guard so we never loop.
    registerAuthErrorHandler(() => {
      if (locRef.current !== "/settings") navigate("/settings?auth=failed");
    });
  }, [navigate]);

  const updateReady = useAutoUpdate();

  return (
    <>
      {updateReady && (
        <button
          onClick={() => location.reload()}
          className="fixed bottom-16 left-1/2 z-50 -translate-x-1/2 rounded-full border border-gold bg-panel px-3 py-1.5 text-xs font-semibold text-gold shadow-lg"
        >
          ⬆ Update ready — tap to reload
        </button>
      )}
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Book />} />
          <Route path="lab" element={<Lab />} />
          <Route path="governor" element={<Governor />} />
          <Route path="markets" element={<Markets />} />
          <Route path="health" element={<HealthPage />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Book />} />
        </Route>
      </Routes>
    </>
  );
}
