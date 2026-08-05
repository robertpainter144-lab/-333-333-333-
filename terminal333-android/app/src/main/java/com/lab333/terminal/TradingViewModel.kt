package com.lab333.terminal

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.lab333.terminal.data.ActivityDto
import com.lab333.terminal.data.BotDto
import com.lab333.terminal.data.CatalogDto
import com.lab333.terminal.data.EquityPoint
import com.lab333.terminal.data.EquityStreamer
import com.lab333.terminal.data.InsiderFeedDto
import com.lab333.terminal.data.InsiderLeaderboardDto
import com.lab333.terminal.data.InsiderLedgersDto
import com.lab333.terminal.data.TickerPoint
import com.lab333.terminal.data.PatentCliffDto
import com.lab333.terminal.data.WhaleStatusDto
import com.lab333.terminal.data.PropDeskStateDto
import com.lab333.terminal.data.MarketPulseDto
import com.lab333.terminal.data.PortfolioDto
import com.lab333.terminal.data.QcProjectDto
import com.lab333.terminal.data.QcStateDto
import com.lab333.terminal.data.StrategiesListDto
import com.lab333.terminal.data.StrategyDto
import com.lab333.terminal.data.SettingsRepo
import com.lab333.terminal.data.SummaryDto
import com.lab333.terminal.data.TradingRepo
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class TradingViewModel(
    private val repo: TradingRepo,
    private val settings: SettingsRepo,
) : ViewModel() {

    sealed interface SummaryState {
        data object Loading : SummaryState
        data class Ok(val data: SummaryDto) : SummaryState
        data class Err(val msg: String) : SummaryState
    }

    sealed interface PortfolioState {
        data object Loading : PortfolioState
        data class Ok(val data: PortfolioDto) : PortfolioState
        data class Err(val msg: String) : PortfolioState
    }

    sealed interface ActivityState {
        data object Loading : ActivityState
        data class Ok(val rows: List<ActivityDto>) : ActivityState
        data class Err(val msg: String) : ActivityState
    }

    sealed interface EquityState {
        data object Loading : EquityState
        data class Ok(val data: List<EquityPoint>) : EquityState
        data class Err(val msg: String) : EquityState
    }

    sealed interface TickerState {
        data object Loading : TickerState
        data class Ok(val symbol: String, val period: String, val data: List<TickerPoint>) : TickerState
        data class Err(val msg: String) : TickerState
    }

    sealed interface InsiderState {
        data object Loading : InsiderState
        data class Ok(val data: InsiderFeedDto) : InsiderState
        data class Err(val msg: String) : InsiderState
    }

    sealed interface LeaderboardState {
        data object Loading : LeaderboardState
        data class Ok(val leaderboard: InsiderLeaderboardDto, val ledgers: InsiderLedgersDto) : LeaderboardState
        data class Err(val msg: String) : LeaderboardState
    }

    sealed interface QcState {
        data object Loading : QcState
        data class Ok(val data: QcStateDto) : QcState
        data class Err(val msg: String) : QcState
    }

    sealed interface StrategiesState {
        data object Loading : StrategiesState
        data class Ok(val data: StrategiesListDto) : StrategiesState
        data class Err(val msg: String) : StrategiesState
    }

    sealed interface PatentCliffState {
        data object Loading : PatentCliffState
        data class Ok(val data: PatentCliffDto) : PatentCliffState
        data class Err(val msg: String) : PatentCliffState
    }

    sealed interface WhalesState {
        data object Loading : WhalesState
        data class Ok(val data: WhaleStatusDto) : WhalesState
        data class Err(val msg: String) : WhalesState
    }

    sealed interface PropDeskState {
        data object Loading : PropDeskState
        data class Ok(val data: PropDeskStateDto) : PropDeskState
        data class Err(val msg: String) : PropDeskState
    }

    sealed interface CatalogState {
        data object Loading : CatalogState
        data class Ok(val data: CatalogDto) : CatalogState
        data class Err(val msg: String) : CatalogState
    }

    private val _summary = MutableStateFlow<SummaryState>(SummaryState.Loading)
    val summary: StateFlow<SummaryState> = _summary.asStateFlow()

    private val _portfolios = MutableStateFlow<Map<String, PortfolioState>>(emptyMap())
    val portfolios: StateFlow<Map<String, PortfolioState>> = _portfolios.asStateFlow()

    private val _activities = MutableStateFlow<Map<String, ActivityState>>(emptyMap())
    val activities: StateFlow<Map<String, ActivityState>> = _activities.asStateFlow()

    private val _equity = MutableStateFlow<Map<String, EquityState>>(emptyMap())
    val equity: StateFlow<Map<String, EquityState>> = _equity.asStateFlow()

    // (source) -> currently-selected timeframe for the Charts screen
    private val _equityPeriod = MutableStateFlow<Map<String, String>>(emptyMap())
    val equityPeriod: StateFlow<Map<String, String>> = _equityPeriod.asStateFlow()

    private val _ticker = MutableStateFlow<TickerState>(TickerState.Loading)
    val ticker: StateFlow<TickerState> = _ticker.asStateFlow()

    private val _insider = MutableStateFlow<InsiderState>(InsiderState.Loading)
    val insider: StateFlow<InsiderState> = _insider.asStateFlow()

    private val _leaderboard = MutableStateFlow<LeaderboardState>(LeaderboardState.Loading)
    val leaderboard: StateFlow<LeaderboardState> = _leaderboard.asStateFlow()

    private val _qc = MutableStateFlow<QcState>(QcState.Loading)
    val qc: StateFlow<QcState> = _qc.asStateFlow()

    private val _strategies = MutableStateFlow<StrategiesState>(StrategiesState.Loading)
    val strategies: StateFlow<StrategiesState> = _strategies.asStateFlow()

    private val _qcLabProjects = MutableStateFlow<List<QcProjectDto>>(emptyList())
    val qcLabProjects: StateFlow<List<QcProjectDto>> = _qcLabProjects.asStateFlow()

    private val _patentCliff = MutableStateFlow<PatentCliffState>(PatentCliffState.Loading)
    val patentCliff: StateFlow<PatentCliffState> = _patentCliff.asStateFlow()

    private val _whales = MutableStateFlow<WhalesState>(WhalesState.Loading)
    val whales: StateFlow<WhalesState> = _whales.asStateFlow()

    private val _propDesk = MutableStateFlow<PropDeskState>(PropDeskState.Loading)
    val propDesk: StateFlow<PropDeskState> = _propDesk.asStateFlow()

    private val _catalog = MutableStateFlow<CatalogState>(CatalogState.Loading)
    val catalog: StateFlow<CatalogState> = _catalog.asStateFlow()

    private val _marketPulse = MutableStateFlow<MarketPulseDto?>(null)
    val marketPulse: StateFlow<MarketPulseDto?> = _marketPulse.asStateFlow()

    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing.asStateFlow()

    val backendUrl = repo.backend

    init {
        viewModelScope.launch {
            val saved = settings.backendUrl.first()
            repo.setBackend(saved)
            refreshSummary()
            startAutoRefresh()
        }
    }

    private suspend fun startAutoRefresh() {
        while (true) {
            delay(8_000)
            refreshSummary(showSpinner = false)
        }
    }

    fun setBackend(url: String) {
        viewModelScope.launch {
            settings.setBackendUrl(url)
            repo.setBackend(url)
            refreshSummary()
        }
    }

    fun refreshSummary(showSpinner: Boolean = true) {
        viewModelScope.launch {
            if (showSpinner) _refreshing.value = true
            try {
                val data = withContext(Dispatchers.IO) { repo.summary() }
                _summary.value = SummaryState.Ok(data)
            } catch (t: Throwable) {
                _summary.value = SummaryState.Err(t.localisedHttpMessage())
            } finally {
                _refreshing.value = false
            }
        }
    }

    fun refreshPortfolio(source: String) {
        viewModelScope.launch {
            _portfolios.value = _portfolios.value + (source to PortfolioState.Loading)
            try {
                val data = withContext(Dispatchers.IO) { repo.portfolio(source) }
                _portfolios.value = _portfolios.value + (source to PortfolioState.Ok(data))
            } catch (t: Throwable) {
                _portfolios.value = _portfolios.value + (source to PortfolioState.Err(t.localisedHttpMessage()))
            }
        }
    }

    fun refreshActivity(source: String) {
        viewModelScope.launch {
            _activities.value = _activities.value + (source to ActivityState.Loading)
            try {
                val rows = withContext(Dispatchers.IO) {
                    if (source == "all") repo.activityAll(120) else repo.activity(source, 200)
                }
                _activities.value = _activities.value + (source to ActivityState.Ok(rows))
            } catch (t: Throwable) {
                _activities.value = _activities.value + (source to ActivityState.Err(t.localisedHttpMessage()))
            }
        }
    }

    fun refreshStrategies() {
        viewModelScope.launch {
            if (_strategies.value !is StrategiesState.Ok) _strategies.value = StrategiesState.Loading
            try {
                val data = withContext(Dispatchers.IO) { repo.strategies() }
                _strategies.value = StrategiesState.Ok(data)
            } catch (t: Throwable) {
                if (_strategies.value !is StrategiesState.Ok) _strategies.value = StrategiesState.Err(t.localisedHttpMessage())
            }
        }
    }

    fun createStrategy(name: String, code: String, broker: String, allocationPct: Double, onDone: (Throwable?) -> Unit) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    repo.strategyCreate(name, code, broker, allocationPct)
                }
                refreshStrategies()
                onDone(null)
            } catch (t: Throwable) { onDone(t) }
        }
    }

    fun updateStrategy(name: String, allocationPct: Double? = null, enabled: Boolean? = null, broker: String? = null) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    repo.strategyUpdate(name, allocationPct, enabled, broker)
                }
                refreshStrategies()
            } catch (_: Throwable) {}
        }
    }

    fun deleteStrategy(name: String) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { repo.strategyDelete(name) }
                refreshStrategies()
            } catch (_: Throwable) {}
        }
    }

    fun loadQcLabProjects() {
        viewModelScope.launch {
            try {
                val data = withContext(Dispatchers.IO) { repo.qcLabProjects() }
                _qcLabProjects.value = data.projects
            } catch (_: Throwable) {
                _qcLabProjects.value = emptyList()
            }
        }
    }

    fun refreshQc() {
        viewModelScope.launch {
            if (_qc.value !is QcState.Ok) _qc.value = QcState.Loading
            try {
                val data = withContext(Dispatchers.IO) { repo.qc() }
                _qc.value = QcState.Ok(data)
            } catch (t: Throwable) {
                if (_qc.value !is QcState.Ok) _qc.value = QcState.Err(t.localisedHttpMessage())
            }
        }
    }

    fun refreshPatentCliff() {
        viewModelScope.launch {
            if (_patentCliff.value !is PatentCliffState.Ok) _patentCliff.value = PatentCliffState.Loading
            try {
                val data = withContext(Dispatchers.IO) { repo.patentCliff() }
                _patentCliff.value = PatentCliffState.Ok(data)
            } catch (t: Throwable) {
                if (_patentCliff.value !is PatentCliffState.Ok) _patentCliff.value = PatentCliffState.Err(t.localisedHttpMessage())
            }
        }
    }

    fun refreshWhales() {
        viewModelScope.launch {
            // Silent refresh — only show Loading on first fetch, not on subsequent polls
            if (_whales.value !is WhalesState.Ok) {
                _whales.value = WhalesState.Loading
            }
            try {
                val data = withContext(Dispatchers.IO) { repo.whales() }
                _whales.value = WhalesState.Ok(data)
            } catch (t: Throwable) {
                if (_whales.value !is WhalesState.Ok) {
                    _whales.value = WhalesState.Err(t.localisedHttpMessage())
                }
            }
        }
    }

    fun refreshPropDesk() {
        viewModelScope.launch {
            // Don't flash Loading each poll — silent refresh
            try {
                val data = withContext(Dispatchers.IO) { repo.propDesk() }
                _propDesk.value = PropDeskState.Ok(data)
            } catch (t: Throwable) {
                _propDesk.value = PropDeskState.Err(t.localisedHttpMessage())
            }
        }
    }

    fun refreshMarketPulse() {
        viewModelScope.launch {
            try {
                _marketPulse.value = withContext(Dispatchers.IO) { repo.marketPulse() }
            } catch (_: Throwable) { /* keep previous */ }
        }
    }

    fun refreshCatalog() {
        viewModelScope.launch {
            if (_catalog.value !is CatalogState.Ok) _catalog.value = CatalogState.Loading
            try {
                val data = withContext(Dispatchers.IO) { repo.catalog() }
                _catalog.value = CatalogState.Ok(data)
            } catch (t: Throwable) {
                if (_catalog.value !is CatalogState.Ok) _catalog.value = CatalogState.Err(t.localisedHttpMessage())
            }
        }
    }

    fun refreshLeaderboard() {
        viewModelScope.launch {
            _leaderboard.value = LeaderboardState.Loading
            try {
                val lb = withContext(Dispatchers.IO) { repo.insiderLeaderboard() }
                val lg = withContext(Dispatchers.IO) { repo.insiderLedgers() }
                _leaderboard.value = LeaderboardState.Ok(lb, lg)
            } catch (t: Throwable) {
                _leaderboard.value = LeaderboardState.Err(t.localisedHttpMessage())
            }
        }
    }

    fun refreshInsider() {
        viewModelScope.launch {
            if (_insider.value !is InsiderState.Ok) _insider.value = InsiderState.Loading
            try {
                val data = withContext(Dispatchers.IO) { repo.insider() }
                _insider.value = InsiderState.Ok(data)
            } catch (t: Throwable) {
                if (_insider.value !is InsiderState.Ok) _insider.value = InsiderState.Err(t.localisedHttpMessage())
            }
        }
    }

    // ── Live equity WebSocket streamer ──────────────────────────────────
    private val streamers = mutableMapOf<String, EquityStreamer>()

    fun subscribeEquityStream(source: String) {
        if (streamers.containsKey(source)) return
        val streamer = EquityStreamer(
            baseUrl = repo.backend.value,
            source = source,
            onInit = { history ->
                _equity.value = _equity.value + (source to EquityState.Ok(history))
            },
            onAppend = { newRows ->
                val cur = _equity.value[source]
                val merged = if (cur is EquityState.Ok) cur.data + newRows else newRows
                _equity.value = _equity.value + (source to EquityState.Ok(merged))
            },
        )
        streamers[source] = streamer
        streamer.connect()
    }

    fun unsubscribeEquityStream(source: String) {
        streamers.remove(source)?.close()
    }

    fun refreshEquity(source: String, period: String = _equityPeriod.value[source] ?: "1M") {
        _equityPeriod.value = _equityPeriod.value + (source to period)
        viewModelScope.launch {
            // Only show Loading on the FIRST fetch. If we already have data,
            // keep it on-screen while refreshing in the background so the
            // chart doesn't unmount/remount (no more 15s flash).
            val current = _equity.value[source]
            if (current !is EquityState.Ok) {
                _equity.value = _equity.value + (source to EquityState.Loading)
            }
            try {
                val rows = withContext(Dispatchers.IO) { repo.equity(source, period) }
                _equity.value = _equity.value + (source to EquityState.Ok(rows))
            } catch (t: Throwable) {
                // On error during silent refresh, KEEP the last good data —
                // only surface the error if we have nothing to show.
                if (current !is EquityState.Ok) {
                    _equity.value = _equity.value + (source to EquityState.Err(t.localisedHttpMessage()))
                }
            }
        }
    }

    fun fetchTicker(symbol: String, period: String = "1Y") {
        if (symbol.isBlank()) return
        viewModelScope.launch {
            _ticker.value = TickerState.Loading
            try {
                val rows = withContext(Dispatchers.IO) { repo.ticker(symbol.uppercase(), period) }
                _ticker.value = TickerState.Ok(symbol.uppercase(), period, rows)
            } catch (t: Throwable) {
                _ticker.value = TickerState.Err(t.localisedHttpMessage())
            }
        }
    }

    private fun Throwable.localisedHttpMessage(): String =
        message ?: this::class.simpleName ?: "unknown error"
}
