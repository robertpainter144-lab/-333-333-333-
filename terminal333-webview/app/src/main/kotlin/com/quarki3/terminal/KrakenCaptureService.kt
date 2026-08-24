package com.quarki3.terminal

import android.app.Notification
import android.net.Uri
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * Passive Kraken-activity capture (D62, 2026-08-24). Robert trades his
 * Kraken Funded challenge BY HAND; this listener only mirrors the
 * notifications the Kraken app posts on HIS device (fills, order
 * confirmations) up to the owner-gated /api/funded/event endpoint so the
 * ledger keeps itself honest. It observes — it can never touch the
 * Kraken app, place trades, or press anything.
 *
 * Needs the Notification Access special permission, granted once via
 * Settings > Notification access (or `adb shell cmd notification
 * allow_listener com.lab333.terminal/.KrakenCaptureService`).
 */
class KrakenCaptureService : NotificationListenerService() {

    private val io = Executors.newSingleThreadExecutor()
    private val seen = LinkedHashMap<String, Long>()

    private fun endpointAndKey(): Pair<String, String>? {
        return try {
            val u = Uri.parse(BuildConfig.DEFAULT_TERMINAL_URL)
            val key = u.getQueryParameter("key") ?: return null
            Pair("${u.scheme}://${u.host}/api/funded/event", key)
        } catch (_: Exception) { null }
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val pkg = sbn.packageName ?: return
        if (!pkg.startsWith("com.kraken")) return
        // dedup: the same notification can be re-posted on update
        val k = "${sbn.key}:${sbn.postTime}"
        synchronized(seen) {
            if (seen.containsKey(k)) return
            seen[k] = sbn.postTime
            while (seen.size > 64) seen.remove(seen.keys.first())
        }
        val ex = sbn.notification?.extras ?: return
        val title = ex.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
        val text = ex.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: ""
        val big = ex.getCharSequence(Notification.EXTRA_BIG_TEXT)?.toString() ?: ""
        if (title.isBlank() && text.isBlank() && big.isBlank()) return
        val body = JSONObject()
            .put("pkg", pkg)
            .put("title", title)
            .put("text", if (big.length > text.length) big else text)
            .put("posted", sbn.postTime / 1000.0)
        io.execute { post(body.toString(), retries = 2) }
    }

    private fun post(json: String, retries: Int) {
        val (url, key) = endpointAndKey() ?: return
        for (attempt in 0..retries) {
            try {
                val c = URL(url).openConnection() as HttpURLConnection
                c.requestMethod = "POST"
                c.connectTimeout = 8000
                c.readTimeout = 8000
                c.doOutput = true
                c.setRequestProperty("content-type", "application/json")
                c.setRequestProperty("x-api-key", key)
                c.outputStream.use { it.write(json.toByteArray()) }
                val ok = c.responseCode in 200..299
                c.disconnect()
                if (ok) return
            } catch (_: Exception) { }
            try { Thread.sleep(3000L * (attempt + 1)) } catch (_: Exception) { }
        }
        // dropped after retries — the event still exists in the phone's own
        // notification history; sessions reconcile from Robert's reports
    }
}
