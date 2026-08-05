package com.quarki3.trading.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.TimeUnit
import kotlin.math.min

/** WebSocket payload from the backend. */
@Serializable
private data class WsEquityRow(val ts: String, val equity: Double)

@Serializable
private data class WsEquityMessage(
    val type: String,                  // "init" | "append" | "error"
    val data: List<WsEquityRow> = emptyList(),
    val msg: String? = null,
)

/**
 * Connects to /ws/equity/{source} on the backend, parses server-pushed equity
 * rows, and emits them as a list of EquityPoint via the callbacks.
 *
 * Reconnects with exponential backoff if the socket drops.
 *
 *   - onInit(history): called once with the initial history snapshot
 *   - onAppend(newRows): called whenever the backend pushes new rows
 *   - onStatus(status): "connected" / "disconnected" / "reconnecting"
 */
class EquityStreamer(
    private val baseUrl: String,
    private val source: String,
    private val onInit: (List<EquityPoint>) -> Unit,
    private val onAppend: (List<EquityPoint>) -> Unit,
    private val onStatus: (String) -> Unit = {},
) {
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }
    private val client = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS) // server pushes; no idle timeout
        .build()
    @Volatile private var ws: WebSocket? = null
    @Volatile private var closing = false
    @Volatile private var retryAttempt = 0

    fun connect() {
        if (closing) return
        val wsUrl = baseUrl
            .replace("http://", "ws://", ignoreCase = false)
            .replace("https://", "wss://", ignoreCase = false)
            .trimEnd('/') + "/ws/equity/$source"
        val req = Request.Builder().url(wsUrl).build()
        ws = client.newWebSocket(req, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                retryAttempt = 0
                onStatus("connected")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val msg = json.decodeFromString(WsEquityMessage.serializer(), text)
                    val points = msg.data.map {
                        EquityPoint(ts = JsonPrimitive(it.ts), equity = it.equity)
                    }
                    when (msg.type) {
                        "init"   -> onInit(points)
                        "append" -> if (points.isNotEmpty()) onAppend(points)
                        "error"  -> onStatus("error: ${msg.msg}")
                    }
                } catch (t: Throwable) {
                    onStatus("parse error: ${t.message}")
                }
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                onStatus("disconnected")
                if (!closing) scheduleReconnect()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                onStatus("disconnected (${t.message})")
                if (!closing) scheduleReconnect()
            }
        })
    }

    private fun scheduleReconnect() {
        if (closing) return
        retryAttempt += 1
        // Exponential backoff capped at 30s: 1, 2, 4, 8, 16, 30, 30, ...
        val delayMs = (min(30, 1 shl (retryAttempt - 1).coerceAtMost(5))) * 1000L
        onStatus("reconnecting in ${delayMs / 1000}s")
        Thread {
            try { Thread.sleep(delayMs) } catch (_: InterruptedException) { return@Thread }
            if (!closing) connect()
        }.start()
    }

    fun close() {
        closing = true
        ws?.close(1000, "client closing")
        ws = null
    }
}
