import EquityChart from "../components/EquityChart";
import PageIntro from "../components/PageIntro";
import { Badge, Card, EmptyState, Skeleton, StaleBanner } from "../components/ui";
import { AllocationBars, PositionsTable } from "../components/BookKit";
import { useActivity, useEquity, useGovernor, usePortfolio, useQc, useTransfers, useWhoami } from "../lib/hooks";
import { absTime, arrow, money, moneySigned, pct, relTime, signClass, toEpochMs } from "../lib/format";
import type { TransferPoint } from "../lib/types";

/** Honest baselines per API.md semantics — dates/values are contract, not data. */
const TEST_BASELINE = { value: 48.06, date: "2026-07-27" };
const INCEPTION = { value: 50.04, date: "2026-07-13" };
// D6 (revised 2026-08-04): the review trigger scales with contributed capital
// (Σ deposits − withdrawals = the backend's starting_capital) so a deposit
// can't turn ordinary daily noise into forced reviews. Floors preserve the
// original $5/$2.50 behavior on a small book and on the guest sim.
const DD_TRIGGER_PCT = 0.03;
const DD_AMBER_PCT = 0.02;
const DD_FLOOR_TRIGGER = 5.0;
const DD_FLOOR_AMBER = 2.5;

const LIVE_COLOR = "#ffd60a";

/** Net deposits−withdrawals dated after `date` (ET midnight). Transfers on or
 *  before the baseline date are already inside the baseline value. */
function netTransfersSince(xfers: TransferPoint[], date: string): number {
  const t0 = new Date(`${date}T00:00:00-04:00`).getTime();
  let net = 0;
  for (const x of xfers) {
    const t = toEpochMs(x.ts);
    if (!Number.isNaN(t) && t > t0) net += x.amount;
  }
  return Math.round(net * 100) / 100;
}

function Delta(props: { label: string; base: number; date: string; equity: number; xfers?: TransferPoint[] }) {
  // Robinhood semantics: a deposit after the baseline is not a gain and a
  // withdrawal is not a loss — P/L is measured net of transfers.
  const netX = netTransfersSince(props.xfers ?? [], props.date);
  const d = props.equity - props.base - netX;
  const p = (d / (props.base + Math.max(0, netX))) * 100;
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-muted">
        {props.label} <span className="text-faint">({props.date}, {money(props.base)})</span>
      </div>
      <div className={`num text-sm font-semibold ${signClass(d)}`}>
        {arrow(d)} {moneySigned(d)} <span className="opacity-80">({pct(p)})</span>
      </div>
      {netX !== 0 && (
        <div className="text-[9px] text-faint" title="Deposits/withdrawals since this baseline are excluded from P/L">
          net of {moneySigned(netX)} transfers
        </div>
      )}
    </div>
  );
}

function DrawdownGauge(props: { peak: number; equity: number; contributed: number }) {
  const trigger = Math.max(DD_FLOOR_TRIGGER, props.contributed * DD_TRIGGER_PCT);
  const amber = Math.max(DD_FLOOR_AMBER, props.contributed * DD_AMBER_PCT);
  const dd = Math.max(0, props.peak - props.equity);
  const frac = Math.min(1, dd / trigger);
  const state = dd >= trigger ? "red" : dd >= amber ? "amber" : "ok";
  const barColor = state === "red" ? "#f85149" : state === "amber" ? "#d29922" : "#3fb950";
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between gap-2">
        <span
          className="min-w-0 truncate text-[10px] uppercase tracking-wider text-muted"
          title={`D6: 3% of contributed capital (${money(props.contributed)}), $5 floor`}
        >
          Drawdown vs {money(trigger)} review trigger
        </span>
        <span className={`num shrink-0 whitespace-nowrap text-sm font-bold ${state === "ok" ? "text-fg" : state === "amber" ? "text-amber" : "text-neg"}`}>
          {money(dd)} of {money(trigger)}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded bg-panel2">
        <div className="h-full rounded" style={{ width: `${frac * 100}%`, background: barColor }} />
      </div>
      <div className="num mt-0.5 flex justify-between text-[9px] text-faint">
        <span>$0</span>
        <span className="text-amber">{money(amber)} amber</span>
        <span className="text-neg">{money(trigger)} review</span>
      </div>
      {state === "red" && (
        <div role="alert" className="mt-2 rounded border border-neg/50 bg-neg/10 px-2 py-1.5 text-xs font-bold text-neg">
          ⛔ Drawdown ≥ {money(trigger)} — review / assess / repair now. Holding is not an option.
        </div>
      )}
    </div>
  );
}

export default function Book() {
  // Guests see the same Book UI fed by `qcmirror` — a server-side paper view
  // of the strategy book (account ids stripped, live-only endpoints stay 403).
  // Wait for the role probe so we never fire the wrong source's queries.
  const w = useWhoami();
  if (w.isPending) {
    return (
      <Card title="Book">
        <Skeleton lines={5} />
      </Card>
    );
  }
  const sim = w.data?.role !== "owner";
  return <BookView source={sim ? "qcmirror" : "alpaca_live"} sim={sim} />;
}

function BookView({ source, sim }: { source: string; sim: boolean }) {
  const live = usePortfolio(source);
  const equity = useEquity(source);
  // Owner only: guests (sim) must not hit the owner-gated transfers endpoint.
  const transfers = useTransfers(source, !sim);
  const activity = useActivity(source, 30);
  const qc = useQc();
  const gov = useGovernor();

  const p = live.data;
  const stale = p?.stale === true;

  return (
    <div className="grid min-w-0 gap-3 lg:grid-cols-3">
      <div className="lg:col-span-3"><PageIntro icon="📒" title="The Book — the money page.">
        {sim
          ? "You are viewing the paper mirror of the live strategy book: same composition, simulated balance."
          : "Your real Alpaca account. Live equity, the drawdown review trigger (3% of contributed capital), and the drift between what the book holds and the governor-blend target it is steering toward. Every other page is paper research feeding this one."}
      </PageIntro></div>
      {/* min-w-0: grid items default to min-width:auto, so any wide inner row
          (stat chips) blows the card past narrow viewports (Flip cover). */}
      <div className="flex min-w-0 flex-col gap-3 lg:col-span-2">
        {/* Hero */}
        <Card
          title={
            <span className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full" style={{ background: sim ? "#bc8cff" : LIVE_COLOR }} />
              {sim ? "Strategy Book — paper mirror (simulated $50)" : "Alpaca Live — real money"}
            </span>
          }
          right={p && <span className="num text-[10px] text-faint" title={absTime(p.ts)}>{relTime(p.ts)}</span>}
        >
          {live.isPending ? (
            <Skeleton lines={3} />
          ) : live.isError ? (
            <EmptyState title="Could not load live portfolio">{String(live.error)}</EmptyState>
          ) : p ? (
            <>
              {sim && (
                <div className="mb-3 rounded border border-bd bg-panel2 px-2 py-1.5 text-[10px] text-muted">
                  🧪 Paper mirror — tracks the governor-blended strategy composition tick-for-tick.
                  Simulated view; not a balance you can trade against.
                </div>
              )}
              {stale && <div className="mb-3"><StaleBanner source={source} ts={p.ts} /></div>}
              {stale ? (
                <div className="num text-4xl font-bold text-faint">—</div>
              ) : (
                <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
                  <div>
                    <div className="num text-4xl font-bold tracking-tight">{money(p.equity)}</div>
                    <div className="num mt-0.5 text-xs text-muted">
                      unrealized {""}
                      <span className={signClass(p.unrealized_pl)}>
                        {arrow(p.unrealized_pl)} {moneySigned(p.unrealized_pl)}
                      </span>
                    </div>
                  </div>
                  <Delta label="vs test baseline" base={TEST_BASELINE.value} date={TEST_BASELINE.date} equity={p.equity} xfers={transfers.data} />
                  <Delta label="vs inception" base={INCEPTION.value} date={INCEPTION.date} equity={p.equity} xfers={transfers.data} />
                </div>
              )}
              {!stale && (
                <div className="mt-4">
                  <DrawdownGauge peak={p.peak_equity} equity={p.equity} contributed={p.starting_capital} />
                </div>
              )}
            </>
          ) : null}
        </Card>

        {/* Equity chart */}
        <Card title={sim ? "Equity — strategy book (sim)" : "Equity — alpaca_live"}>
          {equity.isPending ? (
            <Skeleton lines={5} />
          ) : equity.isError ? (
            <EmptyState title="Equity series unavailable" />
          ) : (
            <>
              {!sim && transfers.isError && (
                <div className="mb-2 rounded border border-amber/50 bg-amber/10 px-2 py-1 text-[10px] text-amber">
                  ⚠ Transfer history unavailable — the line and P/L may temporarily include deposits/withdrawals.
                </div>
              )}
              <EquityChart points={equity.data ?? []} transfers={transfers.data ?? []} color={LIVE_COLOR} />
            </>
          )}
        </Card>

        {/* Allocation + positions */}
        <Card title="Positions & allocation drift">
          {live.isPending ? (
            <Skeleton lines={6} />
          ) : stale ? (
            <StaleBanner source={source} ts={p?.ts} />
          ) : p ? (
            <>
              <AllocationBars
                positions={p.positions}
                equity={p.equity}
                targets={qc.data?.mirror_targets}
                targetLabel="target (governor blend)"
                targetTs={qc.data?.mirror_targets_ts}
              />
              {gov.data?.weights && (
                <p className="num mt-1.5 text-[10px] text-muted">
                  blend:{" "}
                  {Object.entries(gov.data.weights)
                    .sort((a, b) => b[1] - a[1])
                    .filter(([, w]) => w >= 0.005)
                    .map(([s2, w]) => `${s2} ${(w * 100).toFixed(0)}%`)
                    .join(" · ")}{" "}
                  <span className="text-faint">— whose picks the target holds (Governor tab)</span>
                </p>
              )}
              <div className="mt-3">
                <PositionsTable positions={p.positions} />
              </div>
            </>
          ) : null}
        </Card>
      </div>

      <div className="flex flex-col gap-3">
        {/* Cash panel */}
        <Card title="Cash & settlement">
          {live.isPending ? (
            <Skeleton lines={2} />
          ) : p && !stale ? (
            <div className="flex flex-col gap-2">
              <div className="flex items-baseline justify-between">
                <span className="text-xs text-muted">Cash</span>
                <span className="num text-sm font-semibold">{money(p.cash)}</span>
              </div>
              <div className="flex items-baseline justify-between">
                <span className="text-xs text-muted">Settled BP</span>
                <span className="num text-sm font-semibold">
                  {p.settled_bp !== undefined ? money(p.settled_bp) : "—"}
                </span>
              </div>
              {p.settled_bp !== undefined && p.cash > 0.5 && p.settled_bp < p.cash * 0.5 && (
                <div className="flex items-start gap-1.5">
                  <Badge tone="amber">T+1 settling</Badge>
                  <span className="text-[10px] leading-4 text-faint">
                    Settled buying power ≪ cash: pending target buys are deferred, not failed (cash-account T+1).
                  </span>
                </div>
              )}
            </div>
          ) : stale ? (
            <p className="text-xs text-neg">Broker data unavailable — cash figures withheld.</p>
          ) : null}
        </Card>

        {/* How this book decides — plain-language decision loop */}
        <Card title="Decisions — how this book trades">
          <div className="flex flex-col gap-2 text-[11px] leading-5 text-muted">
            <p>
              <b className="text-fg">Target:</b> a governor-weighted <i>blend</i> of every proven
              source's picks — not any single strategy. Each Lab source (qc, patent, congress, …)
              proposes a composition; the governor multiplies each by its earned weight (live
              weights on the Governor tab and under the drift bars above) and the sum is the
              target. Challengers sit at <span className="num">0%</span> until they pass the
              promotion gate
              (<span className="num">n≥30</span> closed trades, expectancy
              <span className="num"> ≥0.05%</span>, Sharpe <span className="num">≥1</span>).
            </p>
            <p>
              <b className="text-fg">Order guards:</b> buys are sized against <i>settled</i> buying
              power only (cash-account T+1); orders queue over weekends instead of firing into a
              closed market; per-symbol caps stop any one name from swallowing the book.
            </p>
            <p>
              <b className="text-fg">Risk protocol:</b> the drawdown gauge above is a hard
              review trigger at 3% of contributed capital ($5 floor) — that far off peak the
              rule is review / assess / repair, never hold. A single name is capped at 35% of
              the book (overflow spills to the composition's other names, not cash).
              Baselines are honest anchors: inception (first funded day) and the test baseline
              (last strategy change), so every delta answers "since when?".
            </p>
            <p className="text-[10px] text-faint">
              Full per-strategy reasoning lives in each Lab card's data center.
            </p>
          </div>
        </Card>

        {/* IBKR card removed 2026-07-28 — bot dark since 07-20, source retired. */}

        {/* Activity feed */}
        <Card title={sim ? "Recent actions — strategy book (sim)" : "Recent actions — alpaca_live"}>
          {activity.isPending ? (
            <Skeleton lines={4} />
          ) : !activity.data || activity.data.length === 0 ? (
            <EmptyState title="No recent activity">
              No fills recorded yet — the mirror trades sparingly by design.
            </EmptyState>
          ) : (
            <ul className="flex flex-col divide-y divide-bd/60">
              {activity.data.slice(0, 20).map((a, i) => (
                <li key={a.order_id ?? i} className="flex flex-col gap-0.5 py-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Badge tone={String(a.side).toUpperCase() === "BUY" ? "green" : "red"}>
                        {String(a.side).toUpperCase()}
                      </Badge>
                      <span className="text-xs font-semibold">{a.symbol}</span>
                      <span className="num text-[10px] text-muted">
                        {money(a.notional)} @ {money(a.price)}
                      </span>
                    </div>
                    <span className="num text-[10px] text-faint" title={absTime(a.ts)}>
                      {relTime(a.ts)}
                    </span>
                  </div>
                  {a.reason && (
                    <p className="pl-1 text-[10px] leading-4 text-muted">{a.reason}</p>
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
