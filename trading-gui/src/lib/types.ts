// Typed API contract — derived from API.md + live response shapes verified 2026-07-27.

export interface Health {
  ok: boolean;
  ts: string;
  host: string;
  tailscale_ip?: string;
  version: number;
}

export type SourceKind = "live" | "paper" | "test";

export interface SourceInfo {
  id: string;
  label: string;
  kind: SourceKind;
  real_money: boolean;
  color: string;
}

export interface Position {
  symbol: string;
  qty: number;
  avg_cost: number;
  market_price: number;
  market_value: number;
  unrealized_pl: number;
  unrealized_pl_pct: number;
  // insider-portfolio extras
  entry_date?: string;
  signal_date?: string;
  quality?: string;
}

export interface Portfolio {
  source: string;
  equity: number;
  cash: number;
  bp: number;
  /** T+1 settled buying power — may be ~$0 while cash is positive. */
  settled_bp?: number;
  unrealized_pl: number;
  starting_capital: number;
  peak_equity: number;
  drawdown_pct: number;
  positions: Position[];
  position_count: number;
  /** True when the broker feed is unavailable and numbers are placeholders — NEVER render as live. */
  stale?: boolean;
  ts: string;
}

export interface EquityPoint {
  /** Epoch seconds — arrives as a string from the CSV-backed series. */
  ts: string | number;
  equity: number;
}

export interface TransferPoint {
  ts: string;
  /** Signed: positive = deposit, negative = withdrawal. */
  amount: number;
  type: "deposit" | "withdrawal";
}

export interface Activity {
  source: string;
  ts: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  notional: number;
  status: string;
  type?: string;
  tif?: string;
  order_id?: string;
  reason?: string;
}

export interface QcStrategy {
  id: number;
  name: string;
  universe: string[];
  alloc_mode: string;
  weight: number;
  holdings: Record<string, number>;
  psr_5y: number;
  cagr_5y: number;
  sharpe_1y: number;
  return_3m: number;
  drawdown_5y: number;
}

export interface QcState {
  ts: string;
  initial_equity: number;
  peak_equity: number;
  qc_started_ts?: number;
  exposure_level: number;
  tiers_fired: unknown[];
  last_qc_refresh?: string;
  last_rebalance?: string | null;
  session_open_pl: number;
  session_peak_pl?: number;
  ratchet_floor: number | null;
  ratchet_fired_today?: boolean;
  strategies: QcStrategy[];
  mirror_pnl_pct: number;
  mirror_mode: string;
  mirror_bankroll: number;
  /** Target allocation percentages by symbol for the live mirror (server-
   *  capped at the D6 35% single-name limit; overflow rolls into "cash"). */
  mirror_targets: Record<string, number>;
  /** Epoch seconds the mirror composition was last computed — feeds the
   *  Book page's "stale target" caveat when the governor hasn't refreshed. */
  mirror_targets_ts?: number;
}

export type EdgeMode = "paper" | "live" | "backtest-only";
export type Verdict = "PROVEN" | "PROMISING" | "LOSING" | "UNPROVEN";

export interface EdgeStrategy {
  name: string;
  mode: EdgeMode;
  n_closed_trades: number;
  win_rate: number | null;
  expectancy_pct: number | null;
  sharpe: number | null;
  total_pnl_pct: number | null;
  last_trade_ts: number | null;
  verdict: Verdict;
  note?: string;
}

export interface EdgeResponse {
  ts: number;
  strategies: EdgeStrategy[];
}

export interface Bot {
  id: string;
  name: string;
  host: string;
  running: boolean;
  pid: number | null;
}

export interface VersionInfo {
  version_code: number;
  version_name: string;
  url?: string;
  sha256?: string;
  notes?: string;
}

export interface TickerPoint {
  ts: number;
  price: number;
}

export interface WhaleAlert {
  ts: string;
  symbol: string;
  chain: string;
  wallets: number;
  agg_usd: number;
  raw: string;
}

export interface WhalesResponse {
  ts: string;
  seen_count: number;
  dune_configured?: boolean;
  etherscan_configured?: boolean;
  last_tick?: string;
  last_line?: string;
  alerts: WhaleAlert[];
}

/** Patent-cliff rows arrive as loosely-typed CSV-ish string records. */
export type PatentCliffRow = Record<string, string | number | null | undefined>;

export interface PatentCliffResponse {
  ts: string;
  generated?: Record<string, string>;
  stats?: Record<string, number>;
  cliffs: PatentCliffRow[];
}

export interface InsiderLeader {
  rank: number;
  config_hash: string;
  config: Record<string, number | boolean>;
  n_folds: number;
  mean_sharpe: number;
  mean_alpha_pct: number;
  worst_dd_pct: number;
  mean_total_ret_pct: number;
  mean_trades_per_fold: number;
  composite_score: number;
  beats_spy_every_fold: boolean;
  last_evaluated: string;
}

export interface InsiderLeaderboard {
  ts: string;
  n_configs_tested: number;
  top_k: number;
  strict_beats_spy?: boolean;
  leaders: InsiderLeader[];
}

export interface InsiderPortfolio {
  initial_cash: number;
  cash: number;
  equity: number;
  n_positions: number;
  positions: Position[];
}

/** Governor state — endpoint not exposed yet (404 as of 2026-07-27); typed for when it lands. */
export interface GovernorState {
  weights?: Record<string, number>;
  reason?: string;
  rolling_pnl?: Record<string, number>;
  n_obs?: number;
  snapshot?: Record<string, number>;
  leader?: string;
  consecutive_leads?: Record<string, number>;
  recent_alerts?: { ts: string; message: string }[];
}

export interface CongressState {
  ts?: number;
  mode?: string;
  targets?: Record<string, number>;
  positions?: Record<string, { qty: number; mv: number; avg_entry: number; unrealized_pl: number }>;
  tracked_syms?: string[];
  source?: string;
  politicians?: string[];
  filings_parsed?: number;
  holdings?: Record<string, number>;
  holdings_updated_ts?: number;
}

export interface PropDeskTrade {
  sym: string;
  strategy: string;
  pnl_pct: number;
  exit_ts: number;
}

export interface PropDeskState {
  ts?: number;
  regime?: string;
  active_strats?: string[];
  auto_tune?: { enabled_strategies?: string[] };
  recent_trades?: PropDeskTrade[];
  counters?: { started_ts?: number };
}

/** Per-strategy data center payload (/api/labbook/{name}, owner-only). */
export interface LabTrade {
  ts: number | null;
  symbol: string;
  side: string | null;
  amount: number | null;
  pnl_pct?: number | null;
  note?: string | null;
}
export interface LabBook {
  name: string;
  positions: Position[];
  targets: Record<string, number>;
  trades: LabTrade[];
  curve: EquityPoint[];
  bankroll: number | null;
  pnl_pct: number | null;
  ts: number | null;
}

/** One row of the governor's full audit trail (/api/governor/history). */
export interface GovernorEvent {
  ts: number | null;
  kind: string; // published | held | kill_switch | alert:* | trade
  reason?: string | null;
  weights?: Record<string, number>;
  l1?: number;
  leader?: string;
  tempo?: string;
  symbol?: string;
  side?: string;
  amount?: number;
  challenger?: string;
}
export interface GovernorHistory {
  ts: string;
  events: GovernorEvent[];
}
