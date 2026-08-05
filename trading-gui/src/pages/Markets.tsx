import { useMemo, useState } from "react";
import PageIntro from "../components/PageIntro";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Card, EmptyState, Skeleton, Table, Tabs, Td, Th } from "../components/ui";
import {
  useCongress,
  useInsiderLeaderboard,
  useInsiderPortfolio,
  usePatentCliff,
  useQc,
  useTicker,
  useWhales,
} from "../lib/hooks";
import { arrow, money, moneySigned, pct, pctPlain, relTime, signClass, absTime } from "../lib/format";

function TickerLookup() {
  const [input, setInput] = useState("");
  const [symbol, setSymbol] = useState("");
  const q = useTicker(symbol);

  const data = useMemo(
    () => (q.data ?? []).map((p) => ({ t: p.ts * 1000, price: p.price })),
    [q.data]
  );
  const last = data.length ? data[data.length - 1] : null;
  const first = data.length ? data[0] : null;
  const chg = last && first ? ((last.price - first.price) / first.price) * 100 : null;

  return (
    <Card title="Ticker lookup">
      <form
        className="mb-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setSymbol(input.trim().toUpperCase());
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Symbol, e.g. AAPL"
          className="num w-40 rounded border border-bd bg-panel2 px-2 py-1.5 text-sm outline-none focus:border-blue"
          autoCapitalize="characters"
          autoCorrect="off"
        />
        <button type="submit" className="rounded border border-bd bg-panel2 px-3 py-1.5 text-xs font-semibold hover:border-faint">
          Look up
        </button>
      </form>
      {!symbol ? (
        <p className="text-xs text-faint">Enter a symbol to fetch its quote and mini chart.</p>
      ) : q.isPending ? (
        <Skeleton lines={4} />
      ) : q.isError || data.length === 0 ? (
        <EmptyState title={`No data for ${symbol}`} />
      ) : (
        <>
          <div className="mb-2 flex items-baseline gap-3">
            <span className="text-sm font-bold">{symbol}</span>
            <span className="num text-xl font-bold">{money(last!.price)}</span>
            <span className={`num text-xs ${signClass(chg)}`}>
              {arrow(chg)} {pct(chg)} <span className="text-faint">over series</span>
            </span>
          </div>
          <div className="h-36 w-full">
            <ResponsiveContainer>
              <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={(t: number) => new Date(t).toLocaleDateString([], { month: "short", day: "numeric" })}
                  tickLine={false}
                  axisLine={false}
                  minTickGap={60}
                />
                <YAxis domain={["auto", "auto"]} tickFormatter={(v: number) => `$${v.toFixed(0)}`} tickLine={false} axisLine={false} width={40} />
                <Tooltip
                  content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const p = payload[0].payload as { t: number; price: number };
                    return (
                      <div className="rounded border border-bd bg-panel px-2 py-1 text-xs">
                        <div className="num font-semibold">{money(p.price)}</div>
                        <div className="text-faint">{new Date(p.t).toLocaleDateString()}</div>
                      </div>
                    );
                  }}
                />
                <Line type="monotone" dataKey="price" stroke="#58a6ff" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </Card>
  );
}

function QcTab() {
  const qc = useQc();
  if (qc.isPending) return <Skeleton lines={6} />;
  if (qc.isError || !qc.data) return <EmptyState title="QC state unavailable" />;
  const s = qc.data;
  return (
    <>
      <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {(
          [
            ["Mirror bankroll", money(s.mirror_bankroll)],
            ["Mirror P/L", pct(s.mirror_pnl_pct)],
            ["Exposure", pctPlain(s.exposure_level * 100, 0)],
            ["Session open P/L", moneySigned(s.session_open_pl)],
          ] as [string, string][]
        ).map(([k, v]) => (
          <div key={k} className="rounded border border-bd bg-panel2 p-2">
            <div className="text-[9px] uppercase tracking-wider text-faint">{k}</div>
            <div className="num mt-0.5 text-sm font-semibold">{v}</div>
          </div>
        ))}
      </div>
      <Table
        head={
          <>
            <Th>Strategy</Th>
            <Th right>Weight</Th>
            <Th right>3M ret</Th>
            <Th right>Sharpe 1y</Th>
            <Th>Holdings</Th>
          </>
        }
      >
        {s.strategies.map((st) => (
          <tr key={st.id}>
            <Td className="max-w-[220px] truncate font-medium" title={st.name}>
              {st.name}
            </Td>
            <Td right className="num">{pctPlain(st.weight * 100)}</Td>
            <Td right className={`num ${signClass(st.return_3m)}`}>{pct(st.return_3m)}</Td>
            <Td right className="num">{st.sharpe_1y.toFixed(2)}</Td>
            <Td>
              <span className="flex flex-wrap gap-1">
                {Object.entries(st.holdings).map(([sym, w]) => (
                  <Badge key={sym} tone="gold" title={pctPlain(w * 100)}>
                    {sym}
                  </Badge>
                ))}
              </span>
            </Td>
          </tr>
        ))}
      </Table>
    </>
  );
}

function PatentTab() {
  const q = usePatentCliff();
  if (q.isPending) return <Skeleton lines={6} />;
  if (q.isError || !q.data) return <EmptyState title="Patent-cliff scanner unavailable" />;
  const rows = (q.data.cliffs ?? []).slice(0, 25);
  if (rows.length === 0) return <EmptyState title="No cliffs ranked" />;
  const f = (v: unknown) => (v === undefined || v === null || v === "" ? "—" : String(v));
  return (
    <Table
      head={
        <>
          <Th>Brand</Th>
          <Th>Ingredient</Th>
          <Th>Applicant</Th>
          <Th right>LOE date</Th>
          <Th right>Months</Th>
          <Th right>Sales $B</Th>
          <Th right>Score</Th>
        </>
      }
    >
      {rows.map((r, i) => (
        <tr key={i}>
          <Td className="font-semibold">{f(r["Trade_Name"])}</Td>
          <Td className="max-w-[160px] truncate text-muted" title={f(r["Ingredient"])}>
            {f(r["Ingredient"])}
          </Td>
          <Td className="max-w-[160px] truncate text-muted" title={f(r["Applicant_Full_Name"])}>
            {f(r["Applicant_Full_Name"])}
          </Td>
          <Td right className="num">{f(r["loe_date"])}</Td>
          <Td right className="num">{r["months_to_loe"] ? Number(r["months_to_loe"]).toFixed(1) : "—"}</Td>
          <Td right className="num">{f(r["sales_usd_b"])}</Td>
          <Td right className="num font-semibold">{r["score"] ? Number(r["score"]).toFixed(2) : "—"}</Td>
        </tr>
      ))}
    </Table>
  );
}

function InsiderTab() {
  const lb = useInsiderLeaderboard();
  const pf = useInsiderPortfolio();
  return (
    <div className="flex flex-col gap-3">
      <PageIntro icon="📈" title="Markets — context & signal feeds.">
        The external inputs the strategies read: tickers, regime, and disclosure feeds like Congress.
        Nothing on this page trades by itself — it is what the Lab strategies consume.
      </PageIntro>
      <div>
        <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
          Config leaderboard {lb.data && <span className="num normal-case text-faint">({lb.data.n_configs_tested} tested)</span>}
        </h3>
        {lb.isPending ? (
          <Skeleton lines={4} />
        ) : lb.isError || !lb.data ? (
          <EmptyState title="Insider leaderboard unavailable" />
        ) : (
          <Table
            head={
              <>
                <Th right>Rank</Th>
                <Th>Config</Th>
                <Th right>Sharpe</Th>
                <Th right>Alpha</Th>
                <Th right>Ret</Th>
                <Th right>Score</Th>
              </>
            }
          >
            {lb.data.leaders.map((l) => (
              <tr key={l.config_hash}>
                <Td right className="num">{l.rank}</Td>
                <Td className="num text-muted">{l.config_hash.slice(0, 8)}</Td>
                <Td right className="num">{l.mean_sharpe.toFixed(2)}</Td>
                <Td right className={`num ${signClass(l.mean_alpha_pct)}`}>{pct(l.mean_alpha_pct)}</Td>
                <Td right className={`num ${signClass(l.mean_total_ret_pct)}`}>{pct(l.mean_total_ret_pct)}</Td>
                <Td right className="num font-semibold">{l.composite_score.toFixed(3)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </div>
      <div>
        <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted">Paper portfolio</h3>
        {pf.isPending ? (
          <Skeleton lines={4} />
        ) : pf.isError || !pf.data ? (
          <EmptyState title="Insider portfolio unavailable" />
        ) : (
          <>
            <p className="num mb-1.5 text-xs text-muted">
              Equity {money(pf.data.equity)} · cash {money(pf.data.cash)} · {pf.data.n_positions} positions
              <Badge tone="blue">PAPER</Badge>
            </p>
            <Table
              head={
                <>
                  <Th>Symbol</Th>
                  <Th right>Value</Th>
                  <Th right>Unrl P/L</Th>
                  <Th right>Quality</Th>
                  <Th right>Entry</Th>
                </>
              }
            >
              {pf.data.positions.map((p) => (
                <tr key={p.symbol}>
                  <Td className="font-semibold">{p.symbol}</Td>
                  <Td right className="num">{money(p.market_value)}</Td>
                  <Td right className={`num ${signClass(p.unrealized_pl)}`}>{moneySigned(p.unrealized_pl)}</Td>
                  <Td right>{p.quality ?? "—"}</Td>
                  <Td right className="num text-muted">{p.entry_date ?? "—"}</Td>
                </tr>
              ))}
            </Table>
          </>
        )}
      </div>
    </div>
  );
}

function WhalesTab() {
  const q = useWhales();
  if (q.isPending) return <Skeleton lines={5} />;
  if (q.isError || !q.data) return <EmptyState title="Whale tracker unavailable" />;
  const w = q.data;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <Badge tone="gray">seen {w.seen_count}</Badge>
        {w.dune_configured && <Badge tone="green">dune ok</Badge>}
        {w.etherscan_configured && <Badge tone="green">etherscan ok</Badge>}
      </div>
      {w.last_tick && <p className="num break-all rounded border border-bd bg-panel2 p-2 text-[10px] text-muted">{w.last_tick}</p>}
      {w.alerts.length === 0 ? (
        <EmptyState title="No whale alerts" />
      ) : (
        <ul className="flex flex-col divide-y divide-bd/60">
          {w.alerts.slice(0, 30).map((a, i) => (
            <li key={i} className="py-1.5">
              <div className="flex items-center gap-2">
                <Badge tone="amber">{a.chain}</Badge>
                <span className="text-xs font-semibold">{a.symbol}</span>
                <span className="num text-[10px] text-muted">
                  {a.wallets} wallets · ${a.agg_usd.toLocaleString()}
                </span>
                <span className="num ml-auto text-[10px] text-faint" title={absTime(a.ts)}>
                  {relTime(a.ts)}
                </span>
              </div>
              {a.raw && <p className="num mt-0.5 break-all text-[10px] text-faint">{a.raw}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CongressTab() {
  const q = useCongress();
  if (q.isPending) return <Skeleton lines={5} />;
  if (q.isError || !q.data) return <EmptyState title="Congress state unavailable" />;
  const d = q.data;
  const targets = Object.entries(d.targets ?? {}).sort((a, b) => b[1] - a[1]);
  if (targets.length === 0) return <EmptyState title="No congress targets yet" />;
  return (
    <div>
      <p className="mb-2 flex flex-wrap items-center gap-1.5 text-xs text-muted">
        <Badge tone="purple">PAPER MIRROR</Badge>
        <span>
          {d.source ?? "House Clerk PTRs"} · {d.filings_parsed ?? 0} filings parsed
        </span>
      </p>
      <Table
        head={
          <>
            <Th>Symbol</Th>
            <Th right>Target</Th>
            <Th right>Qty</Th>
            <Th right>Mkt value</Th>
            <Th right>Unrl P/L</Th>
          </>
        }
      >
        {targets.map(([sym, w]) => {
          const p = d.positions?.[sym];
          return (
            <tr key={sym}>
              <Td className="font-semibold">{sym}</Td>
              <Td right className="num">{pctPlain(w)}</Td>
              <Td right className="num text-muted">{p ? p.qty.toFixed(4) : "—"}</Td>
              <Td right className="num">{p ? money(p.mv) : "—"}</Td>
              <Td right className={`num ${signClass(p ? p.unrealized_pl : null)}`}>
                {p ? moneySigned(p.unrealized_pl) : "—"}
              </Td>
            </tr>
          );
        })}
      </Table>
      <p className="mt-2 text-[10px] text-faint">
        Paper track record lives in the Lab as <code className="num">congress_mirror</code>.
      </p>
    </div>
  );
}

const TABS = [
  { id: "qc", label: "QC" },
  { id: "patent", label: "Patent Cliff" },
  { id: "insider", label: "Insider" },
  { id: "whales", label: "Whales" },
  { id: "congress", label: "Congress" },
];

export default function Markets() {
  const [tab, setTab] = useState("qc");
  return (
    <div className="flex flex-col gap-3">
      <TickerLookup />
      <Card
        title="Signal sources"
        right={<Tabs small tabs={TABS} active={tab} onChange={setTab} />}
      >
        {tab === "qc" && <QcTab />}
        {tab === "patent" && <PatentTab />}
        {tab === "insider" && <InsiderTab />}
        {tab === "whales" && <WhalesTab />}
        {tab === "congress" && <CongressTab />}
      </Card>
    </div>
  );
}
