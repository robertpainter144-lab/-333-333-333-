package com.lab333.terminal.data

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Thin wrapper that recreates the Retrofit client when the backend URL changes.
 */
class TradingRepo(initialBackend: String, private val apiKey: String = "") {

    private val _backend = MutableStateFlow(initialBackend)
    val backend: StateFlow<String> = _backend.asStateFlow()

    @Volatile private var api: TradingApi = ApiFactory.build(initialBackend, apiKey)

    fun setBackend(url: String) {
        if (url == _backend.value) return
        _backend.value = url
        api = ApiFactory.build(url, apiKey)
    }

    suspend fun health(): HealthDto = api.health()
    suspend fun summary(): SummaryDto = api.summary()
    suspend fun portfolio(source: String): PortfolioDto = api.portfolio(source)
    suspend fun activity(source: String, limit: Int = 100): List<ActivityDto> = api.activity(source, limit)
    suspend fun activityAll(limit: Int = 50): List<ActivityDto> = api.activityAll(limit)
    suspend fun bots(): List<BotDto> = api.bots()
    suspend fun equity(source: String, period: String = "1M"): List<EquityPoint> =
        api.equity(source, period)
    suspend fun ticker(symbol: String, period: String = "1Y"): List<TickerPoint> =
        api.ticker(symbol, period)
    suspend fun insider(): InsiderFeedDto = api.insider()
    suspend fun insiderLeaderboard(): InsiderLeaderboardDto = api.insiderLeaderboard()
    suspend fun insiderLedgers(): InsiderLedgersDto = api.insiderLedgers()
    suspend fun qc(): QcStateDto = api.qc()

    suspend fun strategies(): StrategiesListDto = api.strategies()
    suspend fun strategyCreate(name: String, code: String, broker: String, allocationPct: Double) {
        val body = kotlinx.serialization.json.buildJsonObject {
            put("name", kotlinx.serialization.json.JsonPrimitive(name))
            put("code", kotlinx.serialization.json.JsonPrimitive(code))
            put("broker", kotlinx.serialization.json.JsonPrimitive(broker))
            put("allocation_pct", kotlinx.serialization.json.JsonPrimitive(allocationPct))
        }
        api.strategyCreate(body)
    }
    suspend fun strategyUpdate(name: String, allocationPct: Double? = null, enabled: Boolean? = null, broker: String? = null) {
        val body = kotlinx.serialization.json.buildJsonObject {
            allocationPct?.let { put("allocation_pct", kotlinx.serialization.json.JsonPrimitive(it)) }
            enabled?.let       { put("enabled",        kotlinx.serialization.json.JsonPrimitive(it)) }
            broker?.let        { put("broker",         kotlinx.serialization.json.JsonPrimitive(it)) }
        }
        api.strategyUpdate(name, body)
    }
    suspend fun strategyDelete(name: String) { api.strategyDelete(name) }
    suspend fun qcLabProjects(): QcProjectsDto = api.qcLabProjects()
    suspend fun patentCliff(): PatentCliffDto = api.patentCliff()
    suspend fun whales(): WhaleStatusDto = api.whales()
    suspend fun propDesk(): PropDeskStateDto = api.propDesk()
    suspend fun marketPulse(): MarketPulseDto = api.marketPulse()
    suspend fun catalog(): CatalogDto = api.catalog()
}
