package com.quarki3.trading.updater

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import com.quarki3.trading.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * Polls the backend's `/api/version` for a newer APK and hands off to the system
 * package installer. The user still has to tap "Install" — Android doesn't allow
 * silent install without root or device-owner — but no USB cable is ever needed.
 */
@Serializable
data class VersionManifest(
    val version_code: Int,
    val version_name: String,
    val url: String,
    val sha256: String,
    val notes: String = "",
)

object UpdateChecker {
    private val json = Json { ignoreUnknownKeys = true }
    private val http = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    suspend fun check(backendUrl: String): VersionManifest? = withContext(Dispatchers.IO) {
        val base = backendUrl.trimEnd('/')
        try {
            val req = Request.Builder().url("$base/api/version").build()
            http.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val body = resp.body?.string() ?: return@withContext null
                val manifest = json.decodeFromString<VersionManifest>(body)
                if (manifest.version_code > BuildConfig.VERSION_CODE) manifest else null
            }
        } catch (_: Throwable) {
            null
        }
    }

    suspend fun downloadAndInstall(ctx: Context, manifest: VersionManifest) = withContext(Dispatchers.IO) {
        val cacheDir = File(ctx.cacheDir, "updates").apply { mkdirs() }
        val apkFile = File(cacheDir, "trading-${manifest.version_name}.apk")

        // Download
        val req = Request.Builder().url(manifest.url).build()
        http.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) throw RuntimeException("download failed: ${resp.code}")
            val body = resp.body ?: throw RuntimeException("empty body")
            apkFile.outputStream().use { out -> body.byteStream().copyTo(out) }
        }

        // Verify sha256 (skip if manifest sha is empty)
        if (manifest.sha256.isNotEmpty()) {
            val actual = sha256(apkFile)
            if (!actual.equals(manifest.sha256, ignoreCase = true)) {
                apkFile.delete()
                throw RuntimeException("sha256 mismatch")
            }
        }

        // Fire system installer intent
        val authority = "${ctx.packageName}.updater"
        val uri: Uri = FileProvider.getUriForFile(ctx, authority, apkFile)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, "application/vnd.android.package-archive")
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_GRANT_READ_URI_PERMISSION
        }
        withContext(Dispatchers.Main) { ctx.startActivity(intent) }
    }

    private fun sha256(file: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(8192)
            while (true) {
                val n = input.read(buf)
                if (n <= 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}
