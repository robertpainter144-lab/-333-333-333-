import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { getTheme, setApiKey } from "./lib/api";
import "./index.css";

// Deep-link onboarding: /terminal/?key=… stores the key and scrubs it from
// the address bar, so a shared link is all a new device needs.
{
  const params = new URLSearchParams(window.location.search);
  const k = params.get("key");
  if (k) {
    setApiKey(k);
    params.delete("key");
    const qs = params.toString();
    window.history.replaceState(
      null,
      "",
      window.location.pathname + (qs ? `?${qs}` : "") + window.location.hash
    );
  }
}

// Dark-first: apply persisted theme before first paint.
document.documentElement.classList.toggle("dark", getTheme() === "dark");

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: true,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
