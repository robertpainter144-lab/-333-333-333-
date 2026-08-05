import { useState } from "react";
import PageIntro from "../components/PageIntro";
import { useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Card } from "../components/ui";
import { api, DEFAULT_BASE_URL, getApiKey, getBaseUrl, setApiKey, setBaseUrl } from "../lib/api";

export default function Settings() {
  const qc = useQueryClient();
  const loc = useLocation();
  const authFailed = new URLSearchParams(loc.search).get("auth") === "failed";

  const [url, setUrl] = useState(getBaseUrl());
  const [key, setKey] = useState(getApiKey());
  const [saved, setSaved] = useState(false);
  const [test, setTest] = useState<{ ok: boolean; ms: number; detail: string } | null>(null);
  const [testing, setTesting] = useState(false);

  const save = () => {
    setBaseUrl(url || DEFAULT_BASE_URL);
    setApiKey(key);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
    qc.invalidateQueries();
  };

  const runTest = async () => {
    setTesting(true);
    setTest(null);
    // test with the values as currently entered
    setBaseUrl(url || DEFAULT_BASE_URL);
    setApiKey(key);
    const res = await api.testConnection();
    setTest(res);
    setTesting(false);
  };

  return (
    <div className="mx-auto max-w-lg">
      <div className="mb-3"><PageIntro icon="⚙" title="Settings — this device's connection.">
        Backend URL and API key, stored only in this browser. Test the round-trip here if anything shows
        offline.
      </PageIntro></div>
      {authFailed && (
        <div role="alert" className="mb-3 rounded-md border border-neg/50 bg-neg/10 px-3 py-2 text-xs font-semibold text-neg">
          The API rejected your key (401/403). Enter a valid key below — it is stored only in this browser's
          localStorage.
        </div>
      )}
      <Card title="Connection">
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">API base URL</span>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder={DEFAULT_BASE_URL}
              className="num rounded border border-bd bg-panel2 px-2 py-1.5 text-sm outline-none focus:border-blue"
              autoCorrect="off"
              autoCapitalize="off"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-muted">
              API key <span className="normal-case text-faint">(sent as x-api-key; stored in localStorage only)</span>
            </span>
            <input
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="owner key"
              className="num rounded border border-bd bg-panel2 px-2 py-1.5 text-sm outline-none focus:border-blue"
              autoComplete="off"
            />
          </label>
          <div className="flex items-center gap-2">
            <button
              onClick={save}
              className="rounded bg-blue/20 px-3 py-1.5 text-xs font-bold text-blue hover:bg-blue/30"
            >
              {saved ? "Saved ✓" : "Save"}
            </button>
            <button
              onClick={runTest}
              disabled={testing}
              className="rounded border border-bd px-3 py-1.5 text-xs font-semibold text-muted hover:text-fg disabled:opacity-50"
            >
              {testing ? "Testing…" : "Test connection"}
            </button>
          </div>
          {test && (
            <div
              className={`rounded border px-2 py-1.5 text-xs ${
                test.ok ? "border-pos/40 bg-pos/10 text-pos" : "border-neg/40 bg-neg/10 text-neg"
              }`}
            >
              {test.ok ? "✓ connected" : "✗ failed"} · <span className="num">{test.ms} ms</span> · {test.detail}
            </div>
          )}
          <p className="text-[10px] leading-4 text-faint">
            Public base requires the owner key on every request; tailnet/localhost access bypasses the gate. The
            key never leaves this device except as the x-api-key header to the base URL above.
          </p>
        </div>
      </Card>
    </div>
  );
}
