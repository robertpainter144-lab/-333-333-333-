import { useEffect, useState } from "react";
import PageIntro from "../components/PageIntro";
import { Badge, Card, EmptyState, Skeleton } from "../components/ui";
import { useBots, useHealth, useVersion } from "../lib/hooks";
import { getEndpointFreshness } from "../lib/api";
import { relTime } from "../lib/format";

const STALE_THRESHOLD_MS = 90_000;

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export default function HealthPage() {
  const health = useHealth();
  const bots = useBots();
  const version = useVersion();
  useNow(5000);

  const freshness = getEndpointFreshness();
  const staleEndpoints = freshness.filter((f) => f.ageMs > STALE_THRESHOLD_MS);

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <div className="lg:col-span-2"><PageIntro icon="🫀" title="Health — stack vitals.">
        Is the machinery alive? API reachability, data freshness per endpoint, and running bots. If a number
        elsewhere looks stale or frozen, this page says why.
      </PageIntro></div>
      <Card title="API health">
        {health.isPending ? (
          <Skeleton lines={3} />
        ) : health.isError ? (
          <EmptyState title="/api/health unreachable">{String(health.error)}</EmptyState>
        ) : health.data ? (
          <div className="flex flex-col gap-1.5 text-xs">
            <div className="flex items-center gap-2">
              <span className={`h-2.5 w-2.5 rounded-full ${health.data.ok ? "bg-pos" : "bg-neg"}`} />
              <span className="font-semibold">{health.data.ok ? "OK" : "NOT OK"}</span>
              <span className="num text-faint">v{health.data.version}</span>
            </div>
            <div className="text-muted">
              host <span className="num text-fg">{health.data.host}</span>
            </div>
            {health.data.tailscale_ip && (
              <div className="text-muted">
                tailscale <span className="num text-fg">{health.data.tailscale_ip}</span>
              </div>
            )}
            <div className="num text-faint">server ts {health.data.ts}</div>
            {version.data && (
              <div className="mt-1 border-t border-bd pt-1.5 text-muted">
                app version <span className="num text-fg">{version.data.version_name}</span>
                {version.data.notes && <p className="mt-0.5 text-[10px] text-faint">{version.data.notes}</p>}
              </div>
            )}
          </div>
        ) : null}
      </Card>

      <Card title={`Poll staleness self-check (${">"}90 s)`}>
        {freshness.length === 0 ? (
          <EmptyState title="No polls recorded yet this session" />
        ) : staleEndpoints.length === 0 ? (
          <p className="flex items-center gap-2 text-xs text-pos">
            <span className="h-2 w-2 rounded-full bg-pos" /> All {freshness.length} polled endpoints fresh.
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {staleEndpoints.map((f) => (
              <li key={f.path} className="flex items-center justify-between text-xs">
                <span className="num text-neg">{f.path}</span>
                <Badge tone="red">last ok {relTime(Date.now() - f.ageMs)}</Badge>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-2 text-[10px] text-faint">
          Endpoints polled this session, flagged when their last successful fetch is older than 90 s.
        </p>
      </Card>

      <Card title="Services" className="lg:col-span-2">
        {bots.isPending ? (
          <Skeleton lines={4} />
        ) : bots.isError || !bots.data ? (
          <EmptyState title="/api/bots unavailable" />
        ) : (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {bots.data.map((b) => (
              <div key={b.id} className="flex items-center justify-between rounded border border-bd bg-panel2 px-3 py-2">
                <div>
                  <div className="text-xs font-semibold">{b.name}</div>
                  <div className="num text-[10px] text-faint">
                    {b.host}
                    {b.pid ? ` · pid ${b.pid}` : ""}
                  </div>
                </div>
                <Badge tone={b.running ? "green" : "gray"}>{b.running ? "running" : "stopped"}</Badge>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Watchdog & disk" className="lg:col-span-2">
        <p className="text-xs text-muted">
          The self-healing watchdog alerts via <strong>Pushover</strong> when a service dies or the scanner goes
          silent. Disk warnings mean <strong className="text-neg">act now</strong> — a full disk has previously
          taken down the equity logger and every bot on this machine.
        </p>
      </Card>
    </div>
  );
}
