package com.quarki3.trading.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable
data class HealthDto(
    val ok: Boolean,
    val ts: String,
    val host: String? = null,
    @SerialName("tailscale_ip") val tailscaleIp: String? = null,
    val version: Int = 1,
)

@Serializable
data class SourceDto(
    val id: String,
    val label: String,
    val kind: String,
    @SerialName("real_money") val realMoney: Boolean,
    val color: String,
)

@Serializable
data class PositionDto(
    val symbol: String,
    val qty: Double,
    @SerialName("avg_cost") val avgCost: Double,
    @SerialName("market_price") val marketPrice: Double,
    @SerialName("market_value") val marketValue: Double,
    @SerialName("unrealized_pl") val unrealizedPl: Double,
    @SerialName("unrealized_pl_pct") val unrealizedPlPct: Double = 0.0,
    @SerialName("entry_date") val entryDate: String? = null,
)

@Serializable
data class PortfolioDto(
    val source: String,
    val account: String? = null,
    val equity: Double,
    val cash: Double,
    val bp: Double,
    @SerialName("unrealized_pl") val unrealizedPl: Double,
    @SerialName("starting_capital") val startingCapital: Double,
    @SerialName("peak_equity") val peakEquity: Double,
    @SerialName("drawdown_pct") val drawdownPct: Double,
    val positions: List<PositionDto>,
    @SerialName("position_count") val positionCount: Int,
    val ts: String,
)

@Serializable
data class ActivityDto(
    val source: String,
    val ts: String,
    val symbol: String,
    val side: String,
    val qty: Double,
    val price: Double,
    val notional: Double,
    val status: String,
    val type: String,
    val tif: String,
    @SerialName("order_id") val orderId: JsonElement? = null,
    val reason: String = "",
)

@Serializable
data class BotDto(
    val id: String,
    val name: String,
    val host: String,
    val running: Boolean,
    val pid: Int? = null,
)

@Serializable
data class EquityPoint(
    val ts: JsonElement,   // can be epoch-int or ISO-string
    val equity: Double,
)

@Serializable
data class TickerPoint(
    val ts: JsonElement,
    val price: Double,
)

@Serializable
data class SummarySourceDto(
    val id: String,
    val label: String,
    val kind: String,
    @SerialName("real_money") val realMoney: Boolean,
    val color: String,
    val account: String? = null,
    val equity: Double,
    val cash: Double,
    val bp: Double,
    @SerialName("unrealized_pl") val unrealizedPl: Double,
    @SerialName("starting_capital") val startingCapital: Double,
    @SerialName("peak_equity") val peakEquity: Double,
    @SerialName("drawdown_pct") val drawdownPct: Double,
    val positions: List<PositionDto>,
    @SerialName("position_count") val positionCount: Int,
    val ts: String,
)

@Serializable
data class SummaryDto(
    val ts: String,
    val sources: List<SummarySourceDto>,
    val bots: List<BotDto>,
)

@Serializable
data class InsiderPurchaseDto(
    val insider: String,
    val title: String = "",
    @SerialName("tx_date") val txDate: String,
    val qty: Long = 0,
    val price: Double = 0.0,
    val value: Double = 0.0,
)

@Serializable
data class InsiderSignalDto(
    @SerialName("signal_date") val signalDate: String,
    val ticker: String,
    val company: String = "",
    @SerialName("cluster_size") val clusterSize: Int = 0,
    @SerialName("total_value") val totalValue: Double = 0.0,
    @SerialName("largest_buy") val largestBuy: Double = 0.0,
    @SerialName("top_officers") val topOfficers: List<String> = emptyList(),
    @SerialName("other_insiders") val otherInsiders: List<String> = emptyList(),
    val purchases: List<InsiderPurchaseDto> = emptyList(),
    val quality: String = "C",
)

@Serializable
data class InsiderFeedDto(
    val ts: String,
    @SerialName("n_signals") val nSignals: Int = 0,
    val signals: List<InsiderSignalDto> = emptyList(),
    @SerialName("pnl_pct")   val pnlPct: Double? = null,
    @SerialName("started_ts") val startedTs: Long? = null,
)

@Serializable
data class QcStrategyDto(
    val id: Int? = null,
    val name: String,
    val universe: List<String> = emptyList(),
    @SerialName("alloc_mode") val allocMode: String = "",
    val weight: Double = 0.0,
    val holdings: Map<String, Double> = emptyMap(),
    @SerialName("psr_5y")      val psr5y: Double? = null,
    @SerialName("cagr_5y")     val cagr5y: Double? = null,
    @SerialName("sharpe_1y")   val sharpe1y: Double? = null,
    @SerialName("return_3m")   val return3m: Double? = null,
    @SerialName("drawdown_5y") val drawdown5y: Double? = null,
)

@Serializable
data class StrategyDto(
    val name: String,
    val broker: String,
    @SerialName("allocation_pct") val allocationPct: Double = 0.0,
    val enabled: Boolean = true,
    val source: String = "manual",
    @SerialName("qc_project_id") val qcProjectId: Int? = null,
    @SerialName("created_at") val createdAt: String = "",
    @SerialName("last_equity") val lastEquity: Double = 0.0,
    @SerialName("initial_equity") val initialEquity: Double = 0.0,
    @SerialName("pnl_pct") val pnlPct: Double? = null,
)

@Serializable
data class StrategiesListDto(
    val ts: String,
    val strategies: List<StrategyDto> = emptyList(),
    @SerialName("aggregate_pnl_pct") val aggregatePnlPct: Double? = null,
    @SerialName("started_ts") val startedTs: Long? = null,
)

@Serializable
data class QcProjectDto(
    val projectId: Int = 0,
    val name: String = "",
    val description: String = "",
    val language: String = "",
)

@Serializable
data class QcProjectsDto(
    val projects: List<QcProjectDto> = emptyList(),
)

@Serializable
data class InsiderLeaderDto(
    val rank: Int,
    @SerialName("config_hash") val configHash: String,
    val config: Map<String, JsonElement> = emptyMap(),
    @SerialName("mean_sharpe")        val meanSharpe: Double = 0.0,
    @SerialName("mean_alpha_pct")     val meanAlphaPct: Double = 0.0,
    @SerialName("worst_dd_pct")       val worstDdPct: Double = 0.0,
    @SerialName("mean_total_ret_pct") val meanTotalRetPct: Double = 0.0,
    @SerialName("mean_trades_per_fold") val meanTradesPerFold: Double = 0.0,
    @SerialName("composite_score")    val compositeScore: Double = 0.0,
    @SerialName("beats_spy_every_fold") val beatsSpyEveryFold: Boolean = false,
    @SerialName("n_folds") val nFolds: Int = 0,
)

@Serializable
data class InsiderLeaderboardDto(
    val ts: String,
    @SerialName("n_configs_tested") val nConfigsTested: Int = 0,
    @SerialName("top_k") val topK: Int = 0,
    val leaders: List<InsiderLeaderDto> = emptyList(),
)

@Serializable
data class InsiderLedgerDto(
    @SerialName("config_hash") val configHash: String,
    @SerialName("initial_cash") val initialCash: Double = 0.0,
    val cash: Double = 0.0,
    val equity: Double = 0.0,
    @SerialName("ret_pct") val retPct: Double = 0.0,
    @SerialName("n_positions") val nPositions: Int = 0,
    @SerialName("n_closed") val nClosed: Int = 0,
    @SerialName("winrate_pct") val winratePct: Double = 0.0,
)

@Serializable
data class InsiderLedgersDto(
    val ts: String,
    val ledgers: List<InsiderLedgerDto> = emptyList(),
)

// ── Patent Cliff (pharma research) ───────────────────────────────────

@Serializable
data class PatentCliffStatsDto(
    @SerialName("cliff_count")        val cliffCount: Int = 0,
    @SerialName("first_filer_count")  val firstFilerCount: Int = 0,
    @SerialName("exposure_count")     val exposureCount: Int = 0,
    @SerialName("picks_count")        val picksCount: Int = 0,
    @SerialName("sec_signal_count")   val secSignalCount: Int = 0,
)

@Serializable
data class PatentCliffGeneratedDto(
    @SerialName("cliff_scan")    val cliffScan:    String? = null,
    @SerialName("first_filer")   val firstFiler:   String? = null,
    val exposure:                String? = null,
    val picks:                   String? = null,
    @SerialName("sec_signals")   val secSignals:   String? = null,
)

@Serializable
data class PatentCliffDto(
    val ts: String,
    val stats: PatentCliffStatsDto = PatentCliffStatsDto(),
    val generated: PatentCliffGeneratedDto = PatentCliffGeneratedDto(),
    val cliffs:       List<Map<String, String>> = emptyList(),
    @SerialName("first_filer") val firstFiler: List<Map<String, String>> = emptyList(),
    val exposure:     List<Map<String, String>> = emptyList(),
    val picks:        List<Map<String, String>> = emptyList(),
    @SerialName("sec_signals") val secSignals: List<Map<String, String>> = emptyList(),
    @SerialName("pnl_pct") val pnlPct: Double? = null,
    @SerialName("started_ts") val startedTs: Long? = null,
)

// ── Whale Signals (on-chain smart-money accumulation) ───────────────

@Serializable
data class WhaleAlertDto(
    val ts: String = "",
    val symbol: String = "?",
    val chain: String = "?",
    val wallets: Int = 0,
    @SerialName("agg_usd") val aggUsd: Double = 0.0,
    val raw: String = "",
)

@Serializable
data class WhaleStatusDto(
    val ts: String = "",
    @SerialName("seen_count")           val seenCount: Int = 0,
    @SerialName("dune_configured")      val duneConfigured: Boolean = false,
    @SerialName("etherscan_configured") val etherscanConfigured: Boolean = false,
    @SerialName("last_tick")            val lastTick: String = "",
    @SerialName("last_line")            val lastLine: String = "",
    val alerts: List<WhaleAlertDto> = emptyList(),
    @SerialName("pnl_pct")              val pnlPct: Double? = null,
    @SerialName("started_ts")           val startedTs: Long? = null,
)

@Serializable
data class MarketPulseDto(
    val symbol: String = "SPY",
    val close: Double = 0.0,
    @SerialName("prev_close") val prevClose: Double = 0.0,
    @SerialName("pnl_pct") val pnlPct: Double? = null,
    @SerialName("started_ts") val startedTs: Long? = null,
)

// ── Prop Desk Live (multi-strategy paper trading) ───────────────────

@Serializable
data class HeldPosDto(
    val sym: String = "?",
    @SerialName("entry_px") val entryPx: Double = 0.0,
    @SerialName("cur_px")   val curPx: Double = 0.0,
    @SerialName("pnl_pct")  val pnlPct: Double = 0.0,
    @SerialName("peak_px")  val peakPx: Double = 0.0,
    val drawdown: Double = 0.0,
    val strategy: String = "?",
    @SerialName("size_usd") val sizeUsd: Double = 0.0,
    @SerialName("whale_score") val whaleScore: Double = 0.0,
    @SerialName("age_s")    val ageS: Long = 0,
)

@Serializable
data class MoverDto(
    val sym: String = "?",
    @SerialName("v_2m_pct") val v2mPct: Double = 0.0,
    val sigma: Double = 0.0,
    @SerialName("typ_2m")   val typ2m: Double = 0.0,
)

@Serializable
data class StratTopDto(
    val sym: String = "?",
    val score: Double = 0.0,
    val action: String = "?",
    val note: String = "",
)

@Serializable
data class StrategyStatusDto(
    val buys: Int = 0,
    val sells: Int = 0,
    val top: List<StratTopDto> = emptyList(),
)

@Serializable
data class GatedDto(
    val sym: String = "?",
    val strategy: String = "?",
    val reason: String = "?",
    val note: String = "",
)

@Serializable
data class WhaleConfluenceDto(
    val sym: String = "?",
    val score: Double = 0.0,
    val hits: Int = 0,
    @SerialName("hit_types") val hitTypes: List<String> = emptyList(),
)

@Serializable
data class LedgerByStratDto(
    val n: Int = 0,
    val total: Double = 0.0,
)

@Serializable
data class LedgerSummaryDto(
    @SerialName("n_trades")  val nTrades: Int = 0,
    @SerialName("total_pnl") val totalPnl: Double = 0.0,
    @SerialName("mean_pnl")  val meanPnl: Double = 0.0,
    @SerialName("by_strategy") val byStrategy: Map<String, LedgerByStratDto> = emptyMap(),
)

@Serializable
data class AutoTuneDto(
    @SerialName("enabled_strategies") val enabledStrategies: List<String> = emptyList(),
    @SerialName("min_expected_pct")   val minExpectedPct: Double = 0.7,
    @SerialName("kelly_boosts")       val kellyBoosts: Map<String, Double> = emptyMap(),
    @SerialName("ready_for_live")     val readyForLive: Boolean = false,
    val version: Int = 0,
)

@Serializable
data class StratHealthDto(
    val n: Int = 0,
    val mean: Double = 0.0,
    val total: Double = 0.0,
    val wins: Int = 0,
    val losses: Int = 0,
    val confidence: Double = 0.0,
)

@Serializable
data class DecisionDto(
    val ts: Long = 0,
    val type: String = "?",
    val strategy: String = "",
    val reason: String = "",
    // Free-form evidence - we ignore in UI for now.
)

@Serializable
data class CountersDto(
    @SerialName("started_ts")       val startedTs: Long = 0,
    val ticks: Long = 0,
    @SerialName("symbols_scanned")  val symbolsScanned: Long = 0,
    @SerialName("signals_fired")    val signalsFired: Map<String, Int> = emptyMap(),
    @SerialName("gated_by_reason")  val gatedByReason: Map<String, Int> = emptyMap(),
    @SerialName("entries_executed") val entriesExecuted: Map<String, Int> = emptyMap(),
    @SerialName("exits_by_outcome") val exitsByOutcome: Map<String, Int> = emptyMap(),
)

@Serializable
data class RecentTradeDto(
    val sym: String = "?",
    val strategy: String = "?",
    @SerialName("pnl_pct") val pnlPct: Double = 0.0,
    @SerialName("exit_ts") val exitTs: Long = 0,
)

@Serializable
data class PropDeskStateDto(
    val ts: Long = 0,
    val regime: String = "unknown",
    @SerialName("btc_vol_1h") val btcVol1h: Double = 0.0,
    @SerialName("btc_price")  val btcPrice: Double = 0.0,
    @SerialName("active_strats") val activeStrats: List<String> = emptyList(),
    @SerialName("alpaca_mode") val alpacaMode: String = "?",
    val held: List<HeldPosDto> = emptyList(),
    val movers: List<MoverDto> = emptyList(),
    val strategies: Map<String, StrategyStatusDto> = emptyMap(),
    val gated: List<GatedDto> = emptyList(),
    val whales: List<WhaleConfluenceDto> = emptyList(),
    val ledger: LedgerSummaryDto = LedgerSummaryDto(),
    @SerialName("auto_tune")    val autoTune: AutoTuneDto = AutoTuneDto(),
    @SerialName("strat_health") val stratHealth: Map<String, StratHealthDto> = emptyMap(),
    val decisions: List<DecisionDto> = emptyList(),
    val counters: CountersDto = CountersDto(),
    @SerialName("recent_trades") val recentTrades: List<RecentTradeDto> = emptyList(),
    @SerialName("edge_series")   val edgeSeries: Map<String, List<Double>> = emptyMap(),
)

@Serializable
data class QcStateDto(
    val ts: String,
    @SerialName("initial_equity")    val initialEquity: Double = 0.0,
    @SerialName("peak_equity")       val peakEquity: Double = 0.0,
    @SerialName("exposure_level")    val exposureLevel: Double = 1.0,
    @SerialName("tiers_fired")       val tiersFired: List<JsonElement> = emptyList(),
    @SerialName("last_qc_refresh")   val lastQcRefresh: String? = null,
    @SerialName("last_rebalance")    val lastRebalance: String? = null,
    @SerialName("session_open_pl")   val sessionOpenPl: Double? = null,
    @SerialName("session_peak_pl")   val sessionPeakPl: Double? = null,
    @SerialName("ratchet_floor")     val ratchetFloor: Double? = null,
    @SerialName("ratchet_fired_today") val ratchetFiredToday: Boolean = false,
    val strategies: List<QcStrategyDto> = emptyList(),
    @SerialName("mirror_pnl_pct")  val mirrorPnlPct: Double? = null,
    @SerialName("mirror_mode")     val mirrorMode: String? = null,
    @SerialName("mirror_bankroll") val mirrorBankroll: Double? = null,
    @SerialName("qc_started_ts")   val qcStartedTs: Long? = null,
)

// ─── Autopilot strategy-catalog benchmark (owner-private, /api/catalog) ──────
// Post-Linus private work: hand-entered + public-filing portfolios tracked
// forward from public prices, compared head-to-head with the QC engine. Never
// served on the shared Terminal — this app reaches it over tailnet as owner.
@Serializable
data class EdgeStrategyDto(
    val name: String,
    val mode: String = "",
    @SerialName("n_closed_trades") val nClosedTrades: Int = 0,
    @SerialName("win_rate") val winRate: Double? = null,
    @SerialName("expectancy_pct") val expectancyPct: Double? = null,
    val sharpe: Double? = null,
    @SerialName("total_pnl_pct") val totalPnlPct: Double? = null,
    @SerialName("last_trade_ts") val lastTradeTs: Long? = null,
    val verdict: String = "UNPROVEN",
    val note: String? = null,
)

@Serializable
data class CatalogCurveDto(
    val inception: String? = null,
    @SerialName("last_nav") val lastNav: Double? = null,
    @SerialName("last_update") val lastUpdate: String? = null,
    val curve: Map<String, Double> = emptyMap(),
    val weights: Map<String, Double> = emptyMap(),
)

@Serializable
data class CatalogMetaDto(
    val blurb: String? = null,
    val source: String? = null,
    val note: String? = null,
    val detail: String? = null,
    val consumes: String? = null,
    val emits: String? = null,
    val cadence: String? = null,
)

@Serializable
data class CatalogDto(
    val ts: String = "",
    val pilots: List<EdgeStrategyDto> = emptyList(),
    val curves: Map<String, CatalogCurveDto> = emptyMap(),
    @SerialName("qc_reference") val qcReference: EdgeStrategyDto? = null,
    val meta: Map<String, CatalogMetaDto> = emptyMap(),
)
