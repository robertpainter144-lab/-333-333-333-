package com.lab333.terminal.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.lab333.terminal.TradingViewModel
import com.lab333.terminal.data.EquityPoint
import com.lab333.terminal.ui.Pill
import com.lab333.terminal.ui.money
import com.lab333.terminal.ui.pct
import com.lab333.terminal.ui.signColor
import com.lab333.terminal.ui.theme.Accent
import com.lab333.terminal.ui.theme.BG3
import com.lab333.terminal.ui.theme.BDR
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.FG2
import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonPrimitive
import java.time.Instant
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

/**
 * Live equity chart with pinch-zoom + horizontal pan. Polls /api/equity/{source}
 * every POLL_INTERVAL_MS so the line extends without user intervention.
 *
 * Gestures (via detectTransformGestures):
 *   - pinch out: zoom IN to fewer-and-finer points (max ~8x)
 *   - pinch in : zoom OUT toward full series (down to 1.0x)
 *   - drag    : pan horizontally; clamped so the visible window stays inside data
 *   - double-tap (todo): reset to 1.0x / live tail
 */
private const val POLL_INTERVAL_MS = 15_000L
private const val MIN_ZOOM = 1.0f
private const val MAX_ZOOM = 12.0f

@Composable
fun RealtimeEquityChart(source: String, vm: TradingViewModel, breakeven: Double = 0.0) {
    // Subscribe to vm state
    val all by vm.equity.collectAsState()
    val state = all[source]

    // WebSocket subscription tied to source. For ibkr we use the live
    // server-push stream; other sources fall back to the polling refresh.
    androidx.compose.runtime.DisposableEffect(source) {
        if (source == "ibkr") {
            vm.subscribeEquityStream(source)
        } else {
            vm.refreshEquity(source, period = "1M")
        }
        onDispose {
            if (source == "ibkr") vm.unsubscribeEquityStream(source)
        }
    }

    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(BG3)
            .padding(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Pill("LIVE", Accent)
            Text("Portfolio value", color = FG,
                 style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold))
        }
        Spacer(Modifier.height(6.dp))

        when (state) {
            is TradingViewModel.EquityState.Ok -> {
                val pts = state.data
                if (pts.size < 2) {
                    Text("Building chart… (need ≥2 data points)", color = FG2,
                         style = MaterialTheme.typography.labelSmall)
                } else {
                    LiveLineChart(pts, breakeven)
                }
            }
            is TradingViewModel.EquityState.Err -> {
                Text("Chart error: ${state.msg}", color = FG2,
                     style = MaterialTheme.typography.labelSmall)
            }
            else -> {
                Text("Loading equity series…", color = FG2,
                     style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

/** Parse an EquityPoint's ts as epoch millis. Handles epoch seconds/millis,
 *  ISO instants ("…Z"), offset datetimes ("…+00:00" — what equity_logger.py
 *  and alpaca_paper_arm.py write; Android's Instant.parse rejects that form),
 *  and naive datetimes (assumed UTC). Returns 0 if unparseable. */
private fun tsMillis(p: EquityPoint): Long {
    val s = ((p.ts as? JsonPrimitive)?.content ?: "").trim()
    if (s.isEmpty()) return 0L
    if (s.all { it.isDigit() }) {
        val v = s.toLongOrNull() ?: return 0L
        return if (v > 100_000_000_000L) v else v * 1000L
    }
    return try {
        Instant.parse(s).toEpochMilli()
    } catch (_: Throwable) {
        try {
            OffsetDateTime.parse(s).toInstant().toEpochMilli()
        } catch (_: Throwable) {
            try {
                LocalDateTime.parse(s).atOffset(ZoneOffset.UTC).toInstant().toEpochMilli()
            } catch (_: Throwable) {
                0L
            }
        }
    }
}


private val HHMM = DateTimeFormatter.ofPattern("HH:mm")
private val MMDD_HHMM = DateTimeFormatter.ofPattern("MM-dd HH:mm")

private fun formatTimeAt(p: EquityPoint, spanMs: Long): String {
    val millis = tsMillis(p)
    if (millis == 0L) return ""
    val ldt = LocalDateTime.ofInstant(Instant.ofEpochMilli(millis), ZoneId.systemDefault())
    // If visible span > 24h, include the date; else just HH:mm
    return if (spanMs > 24 * 60 * 60 * 1000L) ldt.format(MMDD_HHMM)
           else ldt.format(HHMM)
}

@Composable
private fun LiveLineChart(rawPoints: List<EquityPoint>, breakeven: Double = 0.0) {
    // Extract numeric equity series. Skip any zero/NaN points.
    val cleaned = rawPoints.filter { it.equity > 0 }
    val values = cleaned.map { it.equity }
    val n = values.size
    if (n < 2) return

    val measurer = rememberTextMeasurer()
    val axisStyle = TextStyle(color = Color(0xFF7d8590), fontSize = 10.sp)

    // Gesture state — survive recomposition
    var zoom by remember { mutableStateOf(1.0f) }
    var panNorm by remember { mutableStateOf(0.0f) }  // 0 = newest tail, 1 = oldest

    // First-load: snap zoom + pan to TODAY's window (local time, midnight onward).
    // Only fires once per LiveLineChart instance; user gestures take over after.
    var initialApplied by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(n) {
        if (initialApplied) return@LaunchedEffect
        val todayStartMs = ZonedDateTime.now(ZoneId.systemDefault())
            .toLocalDate().atStartOfDay(ZoneId.systemDefault())
            .toInstant().toEpochMilli()
        val firstTodayIdx = cleaned.indexOfFirst { tsMillis(it) >= todayStartMs }
        if (firstTodayIdx > 0 && firstTodayIdx < n - 1) {
            val visibleCount = (n - firstTodayIdx).coerceAtLeast(2)
            zoom = (n.toFloat() / visibleCount).coerceIn(MIN_ZOOM, MAX_ZOOM)
            panNorm = 0f  // newest tail visible
        }
        initialApplied = true
    }

    // Current value + delta header.
    // Use breakeven (starting capital) as the reference so this matches the
    // "vs start" delta on the EQUITY card above. Falls back to first-visible
    // when no breakeven is passed (other sources).
    val first = values.first()
    val last = values.last()
    val reference = if (breakeven > 0) breakeven else first
    val delta = last - reference
    val pctDelta = if (reference > 0) (delta / reference) * 100 else 0.0

    Column {
        Row(verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(money(last), color = FG,
                 style = MaterialTheme.typography.headlineSmall.copy(fontWeight = FontWeight.Bold))
            Text(money(delta, withSign = true), color = signColor(delta),
                 style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.SemiBold))
            Text(pct(pctDelta, withSign = true), color = signColor(delta),
                 style = MaterialTheme.typography.bodyMedium)
        }
        Spacer(Modifier.height(2.dp))
        Text("$n points  ·  zoom ${"%.1f".format(zoom)}×  ·  pinch to zoom, drag to pan",
             color = FG2, style = MaterialTheme.typography.labelSmall)
        Spacer(Modifier.height(8.dp))

        Box(
            Modifier
                .fillMaxWidth()
                .height(220.dp)
                .clip(RoundedCornerShape(8.dp))
                .background(Color(0xFF0d1117))
                .pointerInput(Unit) {
                    detectTransformGestures { _, panChange, zoomChange, _ ->
                        zoom = (zoom * zoomChange).coerceIn(MIN_ZOOM, MAX_ZOOM)
                        // Translate horizontal drag into pan along the series
                        val dragFraction = -panChange.x / size.width.toFloat()
                        // Larger zoom -> smaller pan per pixel
                        panNorm = (panNorm + dragFraction / zoom).coerceIn(0f, 1f)
                    }
                },
        ) {
            Canvas(Modifier.fillMaxSize().padding(8.dp)) {
                val totalW = size.width
                val totalH = size.height

                // Determine visible window based on zoom + pan
                val visCount = (n / zoom).toInt().coerceAtLeast(2)
                val maxStart = n - visCount
                val startIdx = (maxStart * (1f - panNorm)).toInt().coerceIn(0, maxStart)
                val endIdx = (startIdx + visCount).coerceAtMost(n)
                val slice = values.subList(startIdx, endIdx)
                val sliceTs = cleaned.subList(startIdx, endIdx)
                if (slice.size < 2) return@Canvas

                val minV = slice.min()
                val maxV = slice.max()
                val rangeV = (maxV - minV).coerceAtLeast(1e-6)

                // Dollar labels: thousands grouping; show cents when the
                // visible range is so tight that whole dollars would repeat.
                val yLayouts = (0..4).map { g ->
                    val value = maxV - (maxV - minV) * (g / 4f)
                    val label = if (maxV - minV < 5.0) "$" + "%,.2f".format(value)
                                else "$" + "%,.0f".format(value)
                    measurer.measure(label, axisStyle)
                }

                // Gutters sized to the rendered labels (density-aware) so
                // nothing clips on high-DPI screens.
                val leftPad = yLayouts.maxOf { it.size.width } + 8.dp.toPx()
                val bottomPad = yLayouts[0].size.height + 6.dp.toPx()
                val w = (totalW - leftPad).coerceAtLeast(1f)
                val h = (totalH - bottomPad).coerceAtLeast(1f)

                // Y gridlines + dollar labels
                val gridColor = Color(0xFF21262d)
                for (g in 0..4) {
                    val frac = g / 4f
                    val y = h * frac
                    drawLine(gridColor,
                        start = androidx.compose.ui.geometry.Offset(leftPad, y),
                        end = androidx.compose.ui.geometry.Offset(totalW, y),
                        strokeWidth = 1f)
                    val layout = yLayouts[g]
                    drawText(
                        layout,
                        topLeft = androidx.compose.ui.geometry.Offset(
                            x = (leftPad - layout.size.width - 4.dp.toPx()).coerceAtLeast(0f),
                            y = (y - layout.size.height / 2f).coerceIn(0f, h - layout.size.height.toFloat())
                        ),
                    )
                }

                // Build line + fill paths inside plot area
                val reference = if (breakeven > 0) breakeven else first
                val lineColor = if (last >= reference) Color(0xFF3fb950) else Color(0xFFf85149)
                val stroke = Path()
                val fill = Path()
                val xStep = w / (slice.size - 1).toFloat()
                slice.forEachIndexed { i, v ->
                    val x = leftPad + i * xStep
                    val y = h - ((v - minV) / rangeV).toFloat() * h
                    if (i == 0) {
                        stroke.moveTo(x, y)
                        fill.moveTo(x, h)
                        fill.lineTo(x, y)
                    } else {
                        stroke.lineTo(x, y)
                        fill.lineTo(x, y)
                    }
                }
                fill.lineTo(leftPad + (slice.size - 1) * xStep, h)
                fill.close()

                drawPath(
                    fill,
                    brush = Brush.verticalGradient(
                        listOf(lineColor.copy(alpha = 0.35f), lineColor.copy(alpha = 0f))
                    )
                )
                drawPath(stroke, color = lineColor, style = Stroke(width = 3.5f))

                // Latest-value dot
                if (endIdx == n) {
                    val lastY = h - ((slice.last() - minV) / rangeV).toFloat() * h
                    val lastX = leftPad + (slice.size - 1) * xStep
                    drawCircle(lineColor,
                        radius = 5f,
                        center = androidx.compose.ui.geometry.Offset(lastX, lastY))
                }

                // X-axis time labels (4 across visible span)
                val spanMs = (tsMillis(sliceTs.last()) - tsMillis(sliceTs.first())).coerceAtLeast(0L)
                val nXLabels = 4
                val xIdxs = (0 until nXLabels).map { i ->
                    ((slice.size - 1) * (i / (nXLabels - 1f))).toInt().coerceIn(0, slice.size - 1)
                }.distinct()
                for (idx in xIdxs) {
                    val label = formatTimeAt(sliceTs[idx], spanMs)
                    if (label.isEmpty()) continue
                    val layout = measurer.measure(label, axisStyle)
                    val tx = (leftPad + idx * xStep - layout.size.width / 2f)
                        .coerceIn(leftPad, (totalW - layout.size.width).coerceAtLeast(leftPad))
                    drawText(
                        layout,
                        topLeft = androidx.compose.ui.geometry.Offset(
                            x = tx, y = h + 3.dp.toPx()
                        ),
                    )
                }
            }
        }
    }
}
