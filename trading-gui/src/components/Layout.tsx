import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useHealth, useIsGuest } from "../lib/hooks";
import { getTheme, setTheme } from "../lib/api";
import { relTime, absTime } from "../lib/format";

// Same five tabs for everyone — guests' Book renders the qcmirror paper view
// (see Book.tsx); live-only data is 403'd server-side regardless.
const NAV = [
  // Crypto-only nav (Robert 2026-08-27: "focus on the 333 app as it sits
  // today") — Lab/Governor/Markets were Alpaca-era research surfaces (D61).
  { to: "/", label: "Book", icon: "📒" },
  { to: "/health", label: "Health", icon: "🫀" },
] as const;

function useTick(ms: number): number {
  const [, setN] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setN((n) => n + 1), ms);
    return () => clearInterval(id);
  }, [ms]);
  return Date.now();
}

function ThemeToggle() {
  const [theme, setLocal] = useState<"dark" | "light">(getTheme());
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    const meta = document.querySelector('meta[name="theme-color"]');
    meta?.setAttribute("content", theme === "dark" ? "#0d1117" : "#f6f8fa");
  }, [theme]);
  return (
    <button
      onClick={() => {
        const next = theme === "dark" ? "light" : "dark";
        setTheme(next);
        setLocal(next);
      }}
      className="rounded border border-bd px-2 py-1 text-[11px] text-muted hover:text-fg"
      title="Toggle light/dark"
    >
      {theme === "dark" ? "☀ light" : "☾ dark"}
    </button>
  );
}

export default function Layout() {
  const health = useHealth();
  const guest = useIsGuest();
  const nav = NAV;
  const loc = useLocation();
  useTick(5000); // re-render for "last updated" freshness

  const offline = health.isError || (health.data && !health.data.ok);
  const updated = health.dataUpdatedAt;

  return (
    <div className="flex min-h-full">
      {/* Desktop left rail */}
      <nav className="hidden w-44 shrink-0 flex-col border-r border-bd bg-panel md:flex">
        <div className="flex items-center gap-2 border-b border-bd px-4 py-3">
          <span className="text-gold" aria-hidden>▲</span>
          <span className="text-sm font-bold tracking-tight">Quarki333 Terminal</span>
        </div>
        <div className="flex flex-1 flex-col gap-1 p-2">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-2 rounded px-3 py-2 text-sm ${
                  isActive ? "bg-panel2 font-semibold text-fg" : "text-muted hover:text-fg"
                }`
              }
            >
              <span aria-hidden>{n.icon}</span> {n.label}
            </NavLink>
          ))}
        </div>
        <div className="flex items-center justify-between border-t border-bd p-2">
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              `rounded px-2 py-1 text-[11px] ${isActive ? "text-fg" : "text-muted hover:text-fg"}`
            }
          >
            ⚙ Settings
          </NavLink>
          <ThemeToggle />
        </div>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <header className="sticky top-0 z-20 flex items-center justify-between gap-2 border-b border-bd bg-bg/95 px-3 py-2 backdrop-blur">
          <div className="flex items-center gap-2 md:hidden">
            <span className="text-gold" aria-hidden>▲</span>
            <span className="text-sm font-bold">Quarki333 Terminal</span>
          </div>
          <div className="hidden text-xs text-muted md:block">
            {nav.find((n) => (n.to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(n.to)))?.label ??
              "Settings"}
          </div>
          <div className="flex items-center gap-2">
            {guest && (
              <span className="rounded border border-bd bg-panel2 px-1.5 py-0.5 text-[9px] font-bold tracking-wider text-muted">
                SIM
              </span>
            )}
            {updated > 0 && !offline && (
              <span className="num text-[11px] text-faint" title={absTime(updated)}>
                updated {relTime(updated)}
              </span>
            )}
            <span
              className={`h-2 w-2 rounded-full ${offline ? "bg-neg" : health.data ? "bg-pos" : "bg-amber"}`}
              title={offline ? "offline" : "connected"}
            />
            <NavLink to="/settings" className="text-muted hover:text-fg md:hidden" aria-label="Settings">
              ⚙
            </NavLink>
          </div>
        </header>

        {offline && (
          <div
            role="alert"
            className="border-b border-neg/50 bg-neg/10 px-3 py-1.5 text-center text-xs font-semibold text-neg"
          >
            Offline — /api/health unreachable. Data below may be stale. Check base URL / key in Settings.
          </div>
        )}

        <main className="mx-auto w-full max-w-6xl flex-1 p-3 pb-20 md:pb-6">
          <Outlet />
        </main>

        {/* Mobile bottom tabs */}
        {/* z-50: the tab bar must stay on top of every drawer/overlay so
            navigation is always one tap away, even mid-dossier. */}
        <nav className="fixed inset-x-0 bottom-0 z-50 flex border-t border-bd bg-panel pb-[env(safe-area-inset-bottom)] md:hidden">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) =>
                `flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] ${
                  isActive ? "font-semibold text-fg" : "text-muted"
                }`
              }
            >
              <span className="text-base leading-none" aria-hidden>
                {n.icon}
              </span>
              {n.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}
