import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import PageIntro from "../components/PageIntro";
import { Card, EmptyState, Skeleton, Table, Td, Th } from "../components/ui";
import { useGovernor, useGovernorHistory } from "../lib/hooks";
import { pct, pctPlain, relTime, absTime } from "../lib/format";
import { ApiError } from "../lib/api";

/** Source colors from /api/sources design language; governor sources mapped to the family. */
const SOURCE_COLORS: Record<string, string> = {
  qc: "#ffd60a",
  patent: "#bc8cff",
  insider: "#58a6ff",
  whale: "#d29922",
  value: "#3fb950",
  autopilot: "#f85149",
  congress: "#8b949e",
};

function MissingEndpoint() {
  return (
    <EmptyState title="Governor endpoint not exposed yet">
      <p className="mx-auto max-w-md">
        Could not reach <code className="num">/api/governor</code> on the VM. The governor decides how the
        live book's capital is split between strategy sources; its weights are file-backed in scanner state.
        Check the connection or the dashboard service — this page lights up automatically when the endpoint
        responds.
      </p>

    </EmptyState>
  );
}

export default function Governor() {
  const gov = useGovernor();
  const hist = useGovernorHistory();

  if (gov.isPending) {
    return (
      <Card title="Governor">
        <Skeleton lines={5} />
      </Card>
    );
  }

  // 404 (or any failure) → honest empty state, never fabricated data.
  if (gov.isError || !gov.data) {
    const status = gov.error instanceof ApiError ? gov.error.status : undefined;
    return (
      <Card title={`Governor — who's driving the live book${status === 404 ? " (404)" : ""}`}>
        <MissingEndpoint />
      </Card>
    );
  }

  const g = gov.data;
  const weights = Object.entries(g.weights ?? {}).sort((a, b) => b[1] - a[1]);
  const leader = weights[0];

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <div className="lg:col-span-2"><PageIntro icon="🎛" title="The Governor — the capital allocator.">
        Splits the live book's dollars between strategy sources by rolling paper P/L. Proven sources earn
        weight; challengers sit at 0% until the promotion gate (n≥30, expectancy ≥0.05%, Sharpe ≥1). The
        weights on this page ARE the Book's target composition.
      </PageIntro></div>
      <Card title="Source weights">
        {weights.length === 0 ? (
          <MissingEndpoint />
        ) : (
          <div className="flex items-center gap-4">
            <div className="h-44 w-44 shrink-0">
              <ResponsiveContainer>
                <PieChart>
                  <Pie
                    data={weights.map(([name, w]) => ({ name, value: w }))}
                    dataKey="value"
                    nameKey="name"
                    innerRadius="60%"
                    outerRadius="95%"
                    paddingAngle={2}
                    stroke="var(--panel)"
                    strokeWidth={2}
                  >
                    {weights.map(([name]) => (
                      <Cell key={name} fill={SOURCE_COLORS[name] ?? "#6e7681"} />
                    ))}
                  </Pie>
                  <Tooltip
                    content={({ active, payload }) => {
                      if (!active || !payload?.length) return null;
                      const d = payload[0];
                      return (
                        <div className="rounded border border-bd bg-panel px-2 py-1 text-xs">
                          {String(d.name)}: <span className="num">{pctPlain(Number(d.value) * 100)}</span>
                        </div>
                      );
                    }}
                  />
                </PieChart>
              </ResponsiveContainer>
            </div>
            <div className="flex flex-col gap-1">
              {leader && (
                <p className="mb-1 text-sm font-bold">
                  {leader[0].toUpperCase()} {pctPlain(leader[1] * 100, 0)} is driving
                </p>
              )}
              {weights.map(([name, w]) => (
                <span key={name} className="flex items-center gap-1.5 text-xs text-muted">
                  <span className="h-2 w-2 rounded-sm" style={{ background: SOURCE_COLORS[name] ?? "#6e7681" }} />
                  {name} <span className="num text-fg">{pctPlain(w * 100)}</span>
                </span>
              ))}
              {g.reason && <p className="mt-1 text-[10px] text-faint">{g.reason}</p>}
            </div>
          </div>
        )}
      </Card>

      <Card title="Scoreboard — combined_LIVE vs challengers">
        {g.snapshot && Object.keys(g.snapshot).length > 0 ? (
          <Table
            head={
              <>
                <Th>Arm</Th>
                <Th right>Rolling P/L</Th>
                <Th right>Consec. leads</Th>
              </>
            }
          >
            {Object.entries(g.snapshot)
              .sort((a, b) => b[1] - a[1])
              .map(([arm, pl]) => (
                <tr key={arm} className={arm === g.leader ? "bg-gold/5" : ""}>
                  <Td className="font-semibold">
                    {arm}
                    {arm === g.leader ? " 👑" : ""}
                  </Td>
                  <Td right className={`num ${pl > 0 ? "text-pos" : pl < 0 ? "text-neg" : "text-muted"}`}>
                    {pct(pl)}
                  </Td>
                  <Td right className="num text-muted">
                    {g.consecutive_leads?.[arm] ?? "—"}
                  </Td>
                </tr>
              ))}
          </Table>
        ) : (
          <MissingEndpoint />
        )}
      </Card>

      <Card title="Alerts timeline" className="lg:col-span-2">
        {g.recent_alerts && g.recent_alerts.length > 0 ? (
          <ul className="flex flex-col divide-y divide-bd/60">
            {g.recent_alerts.map((a, i) => (
              <li key={i} className="flex items-center justify-between gap-2 py-1.5">
                <span className="text-xs">{a.message}</span>
                <span className="num text-[10px] text-faint" title={absTime(a.ts)}>
                  {relTime(a.ts)}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="No alerts exposed">
            Sustained-lead and kill-switch alerts will appear here once the governor endpoint is exposed.
          </EmptyState>
        )}
      </Card>
      {/* Full decision audit trail — every weight move, hold, alert & trade with its why */}
      <div className="lg:col-span-2">
        <Card
          title="Decision log — every governor action & why"
          right={
            hist.data && (
              <span className="num text-[10px] text-faint">{hist.data.events.length} events</span>
            )
          }
        >
          {hist.isPending ? (
            <Skeleton lines={5} />
          ) : hist.isError || !hist.data ? (
            <EmptyState title="History unavailable">
              /api/governor/history unreachable (owner-only endpoint).
            </EmptyState>
          ) : hist.data.events.length === 0 ? (
            <EmptyState title="No decisions recorded yet">
              The audit trail starts accruing from 2026-07-30 — every 15-min governor
              evaluation (weight shifts AND deliberate holds), alerts, and executed
              trades will appear here with their reasoning.
            </EmptyState>
          ) : (
            <ul className="max-h-96 divide-y divide-bd/60 overflow-y-auto pr-1">
              {hist.data.events.map((e, i) => (
                <li key={`${e.ts}-${e.kind}-${i}`} className="flex flex-col gap-0.5 py-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex min-w-0 items-center gap-1.5">
                      <span aria-hidden className="text-xs">
                        {e.kind === "published" ? "⚖️" : e.kind === "held" ? "⏸" :
                         e.kind === "trade" ? (String(e.side).toUpperCase() === "BUY" ? "🟢" : "🔴") :
                         e.kind === "kill_switch" ? "⛔" : "🔔"}
                      </span>
                      <span className="text-xs font-semibold">
                        {e.kind === "published" ? "Weights shifted" :
                         e.kind === "held" ? "Weights held" :
                         e.kind === "trade" ? `${String(e.side).toUpperCase()} ${e.symbol}` :
                         e.kind === "kill_switch" ? "KILL SWITCH" :
                         e.kind.replace("alert:", "Alert: ")}
                      </span>
                      {e.kind === "published" && e.weights && (
                        <span className="num truncate text-[10px] text-muted">
                          {Object.entries(e.weights)
                            .sort((a, b) => b[1] - a[1])
                            .filter(([, w]) => w >= 0.01)
                            .map(([s, w]) => `${s} ${(w * 100).toFixed(0)}%`)
                            .join(" · ")}
                        </span>
                      )}
                      {e.kind === "trade" && e.amount != null && (
                        <span className="num text-[10px] text-muted">${Math.abs(e.amount).toFixed(2)}</span>
                      )}
                    </div>
                    <span className="num shrink-0 text-[10px] text-faint" title={e.ts ? absTime(e.ts) : ""}>
                      {e.ts ? relTime(e.ts) : ""}
                    </span>
                  </div>
                  {e.reason && (
                    <p className="pl-5 text-[10px] leading-4 text-muted">{e.reason}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
