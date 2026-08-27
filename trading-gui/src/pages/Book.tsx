import { useQuery } from "@tanstack/react-query";
import EquityChart from "../components/EquityChart";
import { Card, EmptyState, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { useWhoami } from "../lib/hooks";
import { signClass } from "../lib/format";

// Crypto-only Book (Robert 2026-08-27). Equities/Alpaca retired (D61).
// OWNER-ONLY: guests 403 on /api/crypto/summary (D31).
// Core law: the client NEVER computes a money number — every figure is a
// server string rendered verbatim.

type CryptoSummary = {
  equity: { ts: string; equity: number }[];
  transfers: { ts: number; usd: number }[];
  value_usd?: number | null;
  value_str?: string | null;
  pl_total?: number | null;
  pl_total_str?: string | null;
  unrealized_usd?: number | null;
  unrealized_str?: string | null;
  per_day_usd?: number | null;
  per_day_str?: string | null;
  chart_stale?: boolean;
  chart_notice_str?: string;
};

function useCryptoSummary() {
  return useQuery<CryptoSummary>({
    queryKey: ["crypto-summary"],
    queryFn: () => api.cryptoSummary(),
    refetchInterval: 30_000,
    retry: false,
  });
}

export default function Book() {
  const w = useWhoami();
  if (w.isPending) {
    return (
      <Card bleed title="Book">
        <Skeleton lines={5} />
      </Card>
    );
  }
  const sim = w.data?.role !== "owner";
  return <BookView sim={sim} />;
}

function BookView({ sim }: { sim: boolean }) {
  return (
    <div className="grid min-w-0 gap-2 lg:grid-cols-3" data-qt-sim={sim}>
      <div className="flex min-w-0 flex-col gap-2 lg:col-span-2">
        {!sim && <CryptoBook />}

        {/* Alloc bar directly under the chart (Robert 2026-08-27: "just put
            it directly under the chart please") — above the holdings list. */}
        <div data-qt-slot="book-alloc-top" />

        {/* Hidden: first-class CryptoBook is the hero+chart. Injection still
            fills lists/alloc below; a second "Crypto — Kraken" card here
            duplicated the headline. */}
        <div data-qt-slot="book-crypto-account" className="hidden" />
        <div className="flex flex-col gap-2">
          <div data-qt-slot="book-crypto-lists" />
        </div>
      </div>

      <div className="flex flex-col gap-3">
        <div data-qt-slot="book-drift-bottom" className="text-[9px] leading-tight text-faint" />
      </div>
    </div>
  );
}

function CryptoBook() {
  const q = useCryptoSummary();
  if (q.isPending) {
    return (
      <Card bleed title="Crypto — Kraken">
        <Skeleton lines={4} />
      </Card>
    );
  }
  if (q.isError || !q.data) {
    return (
      <Card bleed title="Crypto — Kraken">
        <EmptyState title="Could not load crypto book">
          {q.error instanceof Error ? q.error.message : "summary unavailable"}
        </EmptyState>
      </Card>
    );
  }
  const s = q.data;

  const chartPoints = (s.equity ?? []).map((r) => ({ ts: r.ts, equity: Number(r.equity) }));
  const chartTransfers = (s.transfers ?? []).map((x) => ({
    ts: new Date(Number(x.ts) * 1000).toISOString(),
    amount: Number(x.usd),
    type: (Number(x.usd) >= 0 ? "deposit" : "withdrawal") as "deposit" | "withdrawal",
  }));

  return (
    <Card bleed>
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1 px-1 pb-1">
        <span className="text-xs font-bold uppercase tracking-wider text-muted">
          Crypto — Kraken
        </span>
        {/* Top row, not the value row: on narrow phones the value row wraps
            and the wrapped span landed under owner_crypto's floating tab
            button (Robert 2026-08-27 screenshot hid "+$46.12"). */}
        {s.unrealized_str && (
          <span className={`num text-xs font-semibold ${signClass(Number(s.unrealized_usd))}`}>
            {s.unrealized_str}
            <span className="text-faint"> unrealized</span>
          </span>
        )}
        {s.per_day_str && (
          <span className={`num text-xs font-semibold ${signClass(Number(s.per_day_usd))}`}>
            {s.per_day_str}
            <span className="text-faint"> /day since 8/20</span>
          </span>
        )}
      </div>
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-1 px-1 pb-2">
        <span className="num text-3xl font-bold tracking-tight">{s.value_str ?? "—"}</span>
        {s.pl_total_str && (
          <span className={`num text-sm font-semibold ${signClass(Number(s.pl_total))}`}>
            {s.pl_total_str} <span className="text-faint">total P/L</span>
          </span>
        )}
      </div>
      {s.chart_stale && s.chart_notice_str && (
        <div className="mx-1 mb-2 rounded border border-amber/50 bg-amber/10 px-2 py-1 text-[10px] text-amber">
          {s.chart_notice_str}
        </div>
      )}
      {/* overlayChannel="book": owner_crypto.js paints the transfer-collapsed
          green line here. Base still draws if that overlay hasn't landed. */}
      <EquityChart
        points={chartPoints}
        transfers={chartTransfers}
        color="#3fb950"
        defaultRange="1W"
        overlayChannel="book"
        hideWindowPL={!!s.chart_stale}
        plOnly
      />
    </Card>
  );
}
