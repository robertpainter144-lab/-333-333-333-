package com.lab333.terminal.data

import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query
import java.util.concurrent.TimeUnit

interface TradingApi {
    @GET("api/health")
    suspend fun health(): HealthDto

    @GET("api/sources")
    suspend fun sources(): List<SourceDto>

    @GET("api/portfolio/{source}")
    suspend fun portfolio(@Path("source") source: String): PortfolioDto

    @GET("api/activity/{source}")
    suspend fun activity(@Path("source") source: String, @Query("limit") limit: Int = 100): List<ActivityDto>

    @GET("api/activity")
    suspend fun activityAll(@Query("limit") limit: Int = 50): List<ActivityDto>

    @GET("api/bots")
    suspend fun bots(): List<BotDto>

    @GET("api/equity/{source}")
    suspend fun equity(
        @Path("source") source: String,
        @Query("period") period: String = "1M",
    ): List<EquityPoint>

    @GET("api/ticker/{symbol}")
    suspend fun ticker(
        @Path("symbol") symbol: String,
        @Query("period") period: String = "1Y",
    ): List<TickerPoint>

    @GET("api/summary")
    suspend fun summary(): SummaryDto

    @GET("api/insider")
    suspend fun insider(): InsiderFeedDto

    @GET("api/insider/leaderboard")
    suspend fun insiderLeaderboard(): InsiderLeaderboardDto

    @GET("api/insider/ledgers")
    suspend fun insiderLedgers(): InsiderLedgersDto

    @GET("api/qc")
    suspend fun qc(): QcStateDto

    @GET("api/strategies")
    suspend fun strategies(): StrategiesListDto

    @POST("api/strategies")
    suspend fun strategyCreate(@Body req: kotlinx.serialization.json.JsonObject): kotlinx.serialization.json.JsonObject

    @POST("api/strategies/{name}/config")
    suspend fun strategyUpdate(
        @Path("name") name: String,
        @Body req: kotlinx.serialization.json.JsonObject,
    ): kotlinx.serialization.json.JsonObject

    @DELETE("api/strategies/{name}")
    suspend fun strategyDelete(@Path("name") name: String): kotlinx.serialization.json.JsonObject

    @GET("api/qc_lab/projects")
    suspend fun qcLabProjects(): QcProjectsDto

    @GET("api/patent-cliff")
    suspend fun patentCliff(): PatentCliffDto

    @GET("api/whales")
    suspend fun whales(): WhaleStatusDto

    @GET("api/prop_desk/state")
    suspend fun propDesk(): PropDeskStateDto

    @GET("api/market_pulse")
    suspend fun marketPulse(): MarketPulseDto

    // Owner-private (not in the shared Terminal). Reached over tailnet as owner.
    @GET("api/catalog")
    suspend fun catalog(): CatalogDto
}

object ApiFactory {
    val json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
        isLenient = true
    }

    fun build(baseUrl: String, apiKey: String = ""): TradingApi {
        val normalised = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"
        val http = OkHttpClient.Builder()
            .connectTimeout(6, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .apply {
                // Owner key: required when requests arrive via the public
                // HTTPS proxy (X-Forwarded-For present); harmless over
                // direct tailnet where the gate is bypassed anyway.
                if (apiKey.isNotBlank()) addInterceptor { chain ->
                    chain.proceed(
                        chain.request().newBuilder()
                            .header("x-api-key", apiKey)
                            .build()
                    )
                }
            }
            .addInterceptor(HttpLoggingInterceptor().apply {
                level = HttpLoggingInterceptor.Level.BASIC
            })
            .build()
        return Retrofit.Builder()
            .baseUrl(normalised)
            .client(http)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(TradingApi::class.java)
    }
}
