package com.lab333.terminal.ui

import androidx.compose.ui.graphics.Color
import com.lab333.terminal.ui.theme.Bad
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.Good
import com.lab333.terminal.ui.theme.Purple
import com.lab333.terminal.ui.theme.Warn
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.abs

private val nyz = ZoneId.of("America/New_York")
private val tfShort = DateTimeFormatter.ofPattern("MMM dd HH:mm").withZone(nyz)
private val tfTime  = DateTimeFormatter.ofPattern("HH:mm:ss").withZone(nyz)

fun money(v: Double, withSign: Boolean = false): String {
    val sign = when {
        withSign && v > 0 -> "+"
        v < 0             -> "-"
        else              -> ""
    }
    val abs = abs(v)
    return when {
        abs >= 1_000_000 -> "${sign}$${"%.2f".format(abs / 1_000_000)}M"
        abs >= 10_000    -> "${sign}$${"%,.0f".format(abs)}"
        else             -> "${sign}$${"%,.2f".format(abs)}"
    }
}

fun pct(v: Double, withSign: Boolean = true): String {
    val sign = if (withSign && v > 0) "+" else ""
    return "$sign${"%.2f".format(v)}%"
}

fun signColor(v: Double): Color = when {
    v > 0 -> Good
    v < 0 -> Bad
    else  -> FG
}

fun colorFor(hex: String): Color = try {
    val s = hex.removePrefix("#").trim()
    val n = if (s.length == 6) "FF$s" else s
    Color(n.toLong(16))
} catch (_: Throwable) { Purple }

fun warnColorIfReal(realMoney: Boolean): Color = if (realMoney) Warn else FG

fun formatTimestampShort(ts: String): String = try {
    if (ts.all { it.isDigit() }) {
        tfShort.format(Instant.ofEpochSecond(ts.toLong()))
    } else {
        tfShort.format(Instant.parse(ts.let { if (it.contains("T")) it else "${it}T00:00:00Z" }))
    }
} catch (_: Throwable) { ts }

fun formatTime(ts: String): String = try {
    if (ts.all { it.isDigit() }) {
        tfTime.format(Instant.ofEpochSecond(ts.toLong()))
    } else {
        tfTime.format(Instant.parse(ts))
    }
} catch (_: Throwable) { ts }

fun capitalised(s: String): String =
    s.lowercase(Locale.US).replaceFirstChar { it.uppercase(Locale.US) }
