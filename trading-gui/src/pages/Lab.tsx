import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, EmptyState, ProgressRing, Skeleton } from "../components/ui";
import EquityChart from "../components/EquityChart";
import { AllocationBars, PositionsTable } from "../components/BookKit";
import type { BadgeTone } from "../components/ui";
import { useCongress, useEdge, useLabbook, usePropDesk } from "../lib/hooks";
import { absTime, money, moneySigned, arrow, pct, pctPlain, relTime, signClass } from "../lib/format";
import type { EdgeStrategy, Verdict } from "../lib/types";

/** Plain-language dossier per strategy: what it does, what feeds it, what it
 *  emits. Shown on the card (one-liner) and in full in the tap-through drawer. */
const STRATEGY_INFO: Record<
  string,
  { blurb: string; detail: string; consumes: string; emits: string; cadence: string }
> = {
  "prop_desk:momentum": {
    blurb: "Rides continuation: buys names already moving with volume behind them.",
    detail:
      "Scans a vol-normalized universe every tick and enters when short-horizon price momentum exceeds its noise band (σ-scaled). Exits on trail stops or momentum decay. Position sizes are volatility-normalized and cluster-capped so one hot sector can't dominate the book.",
    consumes: "Price/volume ticks · breadth regime filter",
    emits: "Paper entries/exits into the edge ledger",
    cadence: "continuous (scanner loop)",
  },
  "prop_desk:momentum_5m": {
    blurb: "Faster momentum variant on 5-minute bars.",
    detail:
      "Same momentum logic compressed to 5-minute bars — quicker triggers, tighter exits. In the auto-tune roster; hasn't closed trades yet, so its ring sits at 0 until the gate fills.",
    consumes: "5m bars · breadth regime filter",
    emits: "Paper entries/exits into the edge ledger",
    cadence: "every 5 minutes",
  },
  "prop_desk:momentum_15m": {
    blurb: "Momentum variant on 15-minute bars.",
    detail:
      "The 15-minute sibling — slower triggers than the 5m arm, fewer whipsaws. In the auto-tune roster awaiting its first closed trades.",
    consumes: "15m bars · breadth regime filter",
    emits: "Paper entries/exits into the edge ledger",
    cadence: "every 15 minutes",
  },
  "prop_desk:mean_reversion": {
    blurb: "Fades overstretched moves back toward their mean.",
    detail:
      "Waits for a name to stretch multiple σ away from its rolling mean, then takes the other side with a defined invalidation. Sized and clustered like the rest of the desk. Thirty trades in — the numbers you see are its honest record.",
    consumes: "Price ticks · rolling μ/σ bands",
    emits: "Paper entries/exits into the edge ledger",
    cadence: "continuous (scanner loop)",
  },
  "prop_desk:breakout": {
    blurb: "Buys range breaks with volume confirmation.",
    detail:
      "Tracks consolidation ranges and enters when price clears the range on expanding volume. Stops sit just back inside the range. Thirty closed trades — gate-complete, verdict is what it is.",
    consumes: "Price/volume ticks · range detection",
    emits: "Paper entries/exits into the edge ledger",
    cadence: "continuous (scanner loop)",
  },
  "prop_desk:whale_primary": {
    blurb: "Follows coordinated on-chain whale accumulation.",
    detail:
      "Takes entries when the whale tracker flags coordinated accumulation across tracked wallets (Dune + Etherscan feeds). In the roster; the wallet watchlist gates how often it fires.",
    consumes: "Whale tracker signals (Dune, Etherscan)",
    emits: "Paper entries/exits into the edge ledger",
    cadence: "on whale signal",
  },
  qc_portfolio: {
    blurb: "The QC engine: top-5 leaderboard strategies, 2× winner weighting.",
    detail:
      "Runs the QuantConnect portfolio engine — five leaderboard strategies blended with double weight on the current winner, rebalanced daily. This is the brain whose output the mirror (and the live book) follows. 96 closed paper trades and the only PROMISING verdict on the board.",
    consumes: "QuantConnect leaderboard + daily rebalance window",
    emits: "Target weights → qc_mirror",
    cadence: "daily rebalance",
  },
  qc_mirror: {
    blurb: "Paper-trades the QC engine's picks 1:1 — one INPUT to the live book, not the book itself.",
    detail:
      "Takes qc_portfolio's target weights and executes them as paper orders. This is QC's own composition only — the live Alpaca book holds the governor's BLEND of all proven sources (qc, patent, …), each scaled by its earned weight. Compare this drawer's targets with the Book's target bar to see how much of the book QC is currently driving.",
    consumes: "qc_portfolio target weights",
    emits: "Paper fills · one weighted input to the governor blend",
    cadence: "on rebalance + continuous tracking",
  },
  autopilot_mirror: {
    blurb: "Mirrors InTheMoney's 'Actively Managed' public portfolio.",
    detail:
      "Paper-tracks the published Autopilot 'Actively Managed' portfolio; holdings are refreshed from the public record and traded on paper to build a scored track record before any capital decision.",
    consumes: "Published Autopilot holdings",
    emits: "Paper fills into the edge ledger",
    cadence: "on holdings update",
  },
  congress_mirror: {
    blurb: "Pelosi-tracker replica built from House Clerk disclosures.",
    detail:
      "Parses congressional PTR filings (House Clerk), weights the disclosed positions, and paper-trades the resulting basket. 140 closed trades so far. Its current targets and open paper positions are in the drawer — and under Markets → Congress.",
    consumes: "House Clerk PTR filings",
    emits: "Paper fills · Markets → Congress composition",
    cadence: "on new filings",
  },
  patent_mirror: {
    blurb: "Trades the Patent Cliff scanner's generic-drug picks on paper.",
    detail:
      "Follows the FDA Orange Book patent-cliff ranker: drugs losing exclusivity → exposed brand names → generic beneficiaries. Picks and scores are under Markets → Patent Cliff; this arm paper-trades them to earn (or fail) the promotion gate.",
    consumes: "FDA Orange Book · SEC signals",
    emits: "Paper fills into the edge ledger",
    cadence: "daily rescan",
  },
  combined_mirror: {
    blurb: "The governor-weighted ensemble — this is what steers the live book.",
    detail:
      "Blends every scored source using the governor's weights (see the Governor tab pie) into one composition. The live Alpaca book executes this blend; the Book tab is its real-time portrait. Its own ledger entry tracks the blend's paper performance for honest comparison against each pure arm.",
    consumes: "Governor weights × all source compositions",
    emits: "The live book's orders (via the trading loop)",
    cadence: "every governor refresh",
  },
  "alpaca_paper_arm:mirror": {
    blurb: "A/B arm A: mirrors the historical live-bot picks on Alpaca paper.",
    detail:
      "Runs the same picker and stop math as the original live bot, apples-to-apples on Alpaca paper — catalyst-widened trails included. Exists to measure the strategy without commissions or capital.",
    consumes: "Picker + adaptive stops (shared with live-bot lineage)",
    emits: "closed_trades.csv promotion ledger",
    cadence: "1-minute watch loop",
  },
  "alpaca_paper_arm:broad": {
    blurb: "A/B arm B: same logic across a broad 20-name sub-$25 universe.",
    detail:
      "Statistical-power arm — spreads the same entry/stop logic across ~20 cheap names so the ledger fills faster. Compared head-to-head with the mirror arm to separate stock-picking from logic.",
    consumes: "Broad sub-$25 universe screen",
    emits: "closed_trades.csv promotion ledger",
    cadence: "1-minute watch loop",
  },
  SOXL_regime: {
    blurb: "Risk-first short-hold SOXL behind a 5-condition regime gate.",
    detail:
      "Only holds leveraged semis (SOXL) for short windows when all five regime conditions are green — trend, breadth, vol, and two risk filters. Backtest: Sharpe 1.21, max drawdown −7.8% over 5y. The paper bot is live and waiting for its gate to open.",
    consumes: "Regime gate (5 conditions) · SOXL bars",
    emits: "Paper entries/exits into the edge ledger",
    cadence: "daily gate check",
  },
  ES_mean_rev: {
    blurb: "Research: ES-futures mean reversion (backtest study only).",
    detail:
      "A completed backtest study on ES mini futures — parameter sweeps live in the research repo. No live loop runs it; it's here so the ledger stays a complete record.",
    consumes: "Historical ES 5-minute bars",
    emits: "Backtest results only",
    cadence: "dormant",
  },
  spy_overlay: {
    blurb: "SMA200 tactical overlay (SCHX/SHY) — parked while IBKR is dark.",
    detail:
      "Holds SCHX above the 200-day moving average, rotates to SHY below it. Tied to the IBKR account, which has been intentionally dark since 2026-07-20 — so this arm is parked, not failed.",
    consumes: "SPY SMA200 signal (daily)",
    emits: "IBKR orders (when active)",
    cadence: "dormant (daily when active)",
  },
};

const N_GATE = 30;

const verdictTone: Record<Verdict, BadgeTone> = {
  PROVEN: "green",
  PROMISING: "blue",
  UNPROVEN: "gray",
  LOSING: "red",
};

const MIRROR_NAMES = ["qc_mirror", "congress_mirror", "patent_mirror", "autopilot_mirror"];

// Research-only entries with no live loop behind them (ES backtest study,
// IBKR-tied SPY overlay while the bot is dark). Kept in the ledger for
// honesty, tucked away here so the Lab shows what's actually running.
const DORMANT = new Set(["ES_mean_rev", "spy_overlay"]);

function num(v: number | null, suffix = "", dp = 2): string {
  return v === null || v === undefined ? "—" : `${v.toFixed(dp)}${suffix}`;
}

function StrategyCard(props: { s: EdgeStrategy; onOpen: () => void }) {
  const { s } = props;
  return (
    <button
      onClick={props.onOpen}
      className="relative rounded-lg border border-bd bg-panel p-3 text-left transition-colors hover:border-faint"
    >
      <span className="pointer-events-none absolute right-2 top-2 rotate-0 text-[9px] font-bold uppercase tracking-widest text-faint/60">
        {s.mode === "backtest-only" ? "BACKTEST" : "PAPER"}
      </span>
      <div className="flex items-start justify-between gap-2 pr-14">
        <h3 className="break-all text-xs font-semibold leading-4">{s.name}</h3>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <Badge tone={verdictTone[s.verdict]}>{s.verdict}</Badge>
        <Badge tone={s.mode === "backtest-only" ? "purple" : "blue"}>{s.mode}</Badge>
      </div>
      <div className="mt-3 flex items-center gap-3">
        <ProgressRing
          value={Math.min(s.n_closed_trades, N_GATE)}
          max={N_GATE}
          label={String(s.n_closed_trades)}
          color={s.n_closed_trades >= N_GATE ? "#3fb950" : "#58a6ff"}
        />
        <div className="grid flex-1 grid-cols-2 gap-x-2 gap-y-1">
          <div>
            <div className="text-[9px] uppercase text-faint">Total P/L</div>
            <div className={`num text-xs font-semibold ${signClass(s.total_pnl_pct)}`}>{pct(s.total_pnl_pct)}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-faint">Expectancy</div>
            <div className={`num text-xs ${signClass(s.expectancy_pct)}`}>{pct(s.expectancy_pct, 3)}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-faint">Win rate</div>
            <div className="num text-xs">{s.win_rate === null ? "—" : pctPlain(s.win_rate)}</div>
          </div>
          <div>
            <div className="text-[9px] uppercase text-faint">Sharpe</div>
            <div className="num text-xs">{num(s.sharpe)}</div>
          </div>
        </div>
      </div>
      {STRATEGY_INFO[s.name] && (
        <p className="mt-2 line-clamp-2 text-[10px] leading-4 text-muted">{STRATEGY_INFO[s.name].blurb}</p>
      )}
      <div className="mt-2 flex items-center justify-between">
        <span className="num text-[10px] text-faint" title={s.last_trade_ts ? absTime(s.last_trade_ts) : ""}>
          last trade {s.last_trade_ts ? relTime(s.last_trade_ts) : "never"}
        </span>
        <span className="text-[10px] font-semibold text-blue">tap for details ›</span>
      </div>
    </button>
  );
}

function Drawer(props: { s: EdgeStrategy; onClose: () => void }) {
  const { s } = props;
  const congress = useCongress();
  const desk = usePropDesk();
  const lb = useLabbook(s.name);
  const isMirror = MIRROR_NAMES.includes(s.name);
  const targets =
    s.name === "qc_mirror"
      ? lb.data?.targets ?? undefined
      : s.name === "congress_mirror"
        ? congress.data?.targets
        : undefined;
  const info = STRATEGY_INFO[s.name];

  // Prop-desk strategies get their live roster status + latest trades.
  const deskKey = s.name.startsWith("prop_desk:") ? s.name.slice("prop_desk:".length) : null;
  const enabled = deskKey ? (desk.data?.auto_tune?.enabled_strategies ?? []).includes(deskKey) : false;
  const deskTrades = deskKey
    ? (desk.data?.recent_trades ?? []).filter((t) => t.strategy === deskKey).slice(0, 6)
    : [];

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={props.onClose}>
      <aside
        className="h-full w-full max-w-sm overflow-y-auto border-l border-bd bg-panel p-4 pb-20 md:pb-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-start justify-between gap-2">
          <div>
            <h2 className="break-all text-sm font-bold">{s.name}</h2>
            <div className="mt-1 flex flex-wrap gap-1.5">
              <Badge tone={verdictTone[s.verdict]}>{s.verdict}</Badge>
              <Badge tone="blue">{s.mode}</Badge>
              <Badge tone="amber">PAPER — not money</Badge>
              {deskKey && <Badge tone={enabled ? "green" : "gray"}>{enabled ? "IN ROSTER" : "ON BENCH"}</Badge>}
            </div>
          </div>
          <button onClick={props.onClose} className="text-muted hover:text-fg" aria-label="Close">
            ✕
          </button>
        </div>

        {info && (
          <div className="mb-3 rounded border border-bd bg-panel2 p-2.5">
            <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-muted">What it does</h3>
            <p className="text-[11px] leading-5 text-fg">{info.detail}</p>
            <div className="mt-2 grid gap-1 text-[10px] text-muted">
              <span><b className="text-faint">in ▸</b> {info.consumes}</span>
              <span><b className="text-faint">out ▸</b> {info.emits}</span>
              <span><b className="text-faint">cadence ▸</b> {info.cadence}</span>
            </div>
          </div>
        )}

        <dl className="grid grid-cols-2 gap-2">
          {(
            [
              ["Closed trades", `${s.n_closed_trades} / ${N_GATE} gate`],
              ["Win rate", s.win_rate === null ? "—" : pctPlain(s.win_rate)],
              ["Expectancy", pct(s.expectancy_pct, 3)],
              ["Sharpe", num(s.sharpe)],
              ["Total P/L", pct(s.total_pnl_pct)],
              ["Last trade", s.last_trade_ts ? `${relTime(s.last_trade_ts)}` : "never"],
            ] as [string, string][]
          ).map(([k, v]) => (
            <div key={k} className="rounded border border-bd bg-panel2 p-2">
              <dt className="text-[9px] uppercase tracking-wider text-faint">{k}</dt>
              <dd className="num mt-0.5 text-xs font-semibold">{v}</dd>
            </div>
          ))}
        </dl>

        {/* ---- DATA CENTER: this strategy's book, Book-page grammar ---- */}
        {lb.data && (
          <>
            {(lb.data.bankroll != null || lb.data.pnl_pct != null) && (
              <div className="mt-4 rounded border border-bd bg-panel2 p-2.5">
                <div className="text-[9px] uppercase tracking-wider text-faint">Paper book</div>
                <div className="flex flex-wrap items-end gap-x-4 gap-y-1">
                  {lb.data.bankroll != null && lb.data.pnl_pct != null && (
                    <span className="num text-2xl font-bold tracking-tight">
                      {money(lb.data.bankroll * (1 + lb.data.pnl_pct / 100))}
                    </span>
                  )}
                  {lb.data.pnl_pct != null && (
                    <span className={`num text-sm font-semibold ${signClass(lb.data.pnl_pct)}`}>
                      {arrow(lb.data.pnl_pct)} {pct(lb.data.pnl_pct)}
                      <span className="text-[10px] text-faint"> total</span>
                    </span>
                  )}
                </div>
              </div>
            )}

            {lb.data.curve.length > 1 && (
              <div className="mt-4">
                <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
                  Equity — paper book
                </h3>
                <EquityChart points={lb.data.curve} color="#bc8cff" />
                <p className="mt-1 text-[9px] text-faint">
                  Mirrors accrue this curve from 2026-07-29 (15-min marks); desk strategies rebuild it
                  from every closed trade.
                </p>
              </div>
            )}

            {(lb.data.positions.length > 0 || Object.keys(lb.data.targets).length > 0) && (
              <div className="mt-4">
                <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
                  Positions & allocation drift
                </h3>
                <AllocationBars
                  positions={lb.data.positions}
                  equity={lb.data.bankroll != null && lb.data.pnl_pct != null
                    ? lb.data.bankroll * (1 + lb.data.pnl_pct / 100)
                    : lb.data.positions.reduce((s2, x) => s2 + x.market_value, 0)}
                  targets={lb.data.targets}
                  targetLabel="target (mirror composition)"
                />
                {lb.data.positions.length > 0 && (
                  <div className="mt-3">
                    <PositionsTable positions={lb.data.positions} />
                  </div>
                )}
              </div>
            )}

            {lb.data.trades.length > 0 && (
              <div className="mt-4">
                <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
                  Activity log
                </h3>
                <ul className="max-h-64 divide-y divide-bd/60 overflow-y-auto rounded border border-bd">
                  {lb.data.trades.slice(0, 40).map((a, i) => (
                    <li key={`${a.symbol}-${a.ts}-${i}`} className="flex items-center justify-between gap-2 px-2 py-1.5">
                      <div className="flex min-w-0 items-center gap-2">
                        <Badge tone={a.side === "BUY" ? "green" : a.side === "SELL" ? "red" : "blue"}>
                          {a.side ?? "•"}
                        </Badge>
                        <span className="text-xs font-semibold">{a.symbol}</span>
                        {a.amount != null && <span className="num text-[10px] text-muted">{moneySigned(a.amount)}</span>}
                        {a.pnl_pct != null && (
                          <span className={`num text-[10px] ${signClass(a.pnl_pct)}`}>{pct(a.pnl_pct)}</span>
                        )}
                      </div>
                      <span className="num shrink-0 text-[10px] text-faint" title={a.ts ? absTime(a.ts) : undefined}>
                        {a.ts ? relTime(a.ts) : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}

        {s.note && <p className="mt-3 rounded border border-bd bg-panel2 p-2 text-[11px] text-muted">{s.note}</p>}

        {deskKey && deskTrades.length > 0 && (
          <div className="mt-4">
            <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
              Latest closed trades (this strategy)
            </h3>
            <ul className="divide-y divide-bd/60 rounded border border-bd">
              {deskTrades.map((t, i) => (
                <li key={`${t.sym}-${t.exit_ts}-${i}`} className="flex items-center justify-between px-2 py-1.5">
                  <span className="text-xs font-semibold">{t.sym}</span>
                  <span className={`num text-xs ${signClass(t.pnl_pct)}`}>{pct(t.pnl_pct)}</span>
                  <span className="num text-[10px] text-faint" title={absTime(t.exit_ts)}>
                    {relTime(t.exit_ts)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {isMirror && (
          <div className="mt-4">
            <h3 className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
              Composition / targets
            </h3>
            {targets && Object.keys(targets).length > 0 ? (
              <>
                <ul className="divide-y divide-bd/60 rounded border border-bd">
                  {Object.entries(targets)
                    .sort((a, b) => b[1] - a[1])
                    .map(([sym, w]) => (
                      <li key={sym} className="flex items-center justify-between px-2 py-1.5">
                        <span className="text-xs font-semibold">{sym}</span>
                        <span className="num text-xs text-muted">{pctPlain(w, 2)}</span>
                      </li>
                    ))}
                </ul>
                {s.name === "qc_mirror" && (
                  <Link
                    to="/"
                    className="mt-2 block rounded border border-blue/40 bg-blue/10 px-2 py-1.5 text-center text-[11px] font-semibold text-blue"
                    onClick={props.onClose}
                  >
                    One input to the Book: the governor blends this with the other proven sources'
                    picks (weights on the 🎛 Governor tab). Open 📒 Book to see the blended result →
                  </Link>
                )}
                {s.name === "congress_mirror" && (
                  <p className="mt-2 text-[10px] text-faint">
                    Full table with paper positions under Markets → Congress.
                  </p>
                )}
              </>
            ) : (
              <EmptyState title="Composition not exposed">
                Per-mirror state (targets, cooldowns, settled BP) is file-backed in scanner state with no
                API endpoint yet — ask the stack owner to expose it rather than guessing.
              </EmptyState>
            )}
          </div>
        )}
      </aside>
    </div>
  );
}

export default function Lab() {
  const edge = useEdge();
  const [open, setOpen] = useState<string | null>(null);
  const [showDormant, setShowDormant] = useState(false);

  const strategies = edge.data?.strategies ?? [];
  const active = strategies.filter((s) => !DORMANT.has(s.name));
  const dormant = strategies.filter((s) => DORMANT.has(s.name));
  const selected = strategies.find((s) => s.name === open);

  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-md border border-blue/40 bg-blue/10 px-3 py-2 text-xs text-blue">
        🧪 <strong>Paper lab.</strong> Strategies must prove n≥30 closed trades, expectancy ≥ 0.05%, Sharpe ≥ 1
        before real money. Nothing on this page is a live balance. <strong>Tap any card</strong> for the full
        breakdown — what the strategy does, its data feeds, latest trades, and (for mirrors) the exact
        composition it's targeting.
      </div>

      {edge.isPending ? (
        <Card title="Edge ledger">
          <Skeleton lines={6} />
        </Card>
      ) : edge.isError ? (
        <EmptyState title="Edge ledger unavailable">{String(edge.error)}</EmptyState>
      ) : strategies.length === 0 ? (
        <EmptyState title="No strategies in the edge ledger yet" />
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {active.map((s) => (
              <StrategyCard key={s.name} s={s} onOpen={() => setOpen(s.name)} />
            ))}
          </div>
          {dormant.length > 0 && (
            <div>
              <button
                onClick={() => setShowDormant(!showDormant)}
                className="text-[11px] text-faint hover:text-muted"
              >
                {showDormant ? "▾" : "▸"} {dormant.length} dormant (research-only, no live loop)
              </button>
              {showDormant && (
                <div className="mt-2 grid gap-3 opacity-70 sm:grid-cols-2 xl:grid-cols-3">
                  {dormant.map((s) => (
                    <StrategyCard key={s.name} s={s} onOpen={() => setOpen(s.name)} />
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {selected && <Drawer s={selected} onClose={() => setOpen(null)} />}
    </div>
  );
}
