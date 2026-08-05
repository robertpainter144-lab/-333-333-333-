package com.lab333.terminal.ui.screens

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.unit.dp
import com.lab333.terminal.TradingViewModel
import com.lab333.terminal.data.SummarySourceDto
import com.lab333.terminal.data.TickerPoint
import com.lab333.terminal.ui.Card
import com.lab333.terminal.ui.Pill
import com.lab333.terminal.ui.clickableNoIndication
import com.lab333.terminal.ui.colorFor
import com.lab333.terminal.ui.money
import com.lab333.terminal.ui.pct
import com.lab333.terminal.ui.signColor
import com.lab333.terminal.ui.theme.Accent
import com.lab333.terminal.ui.theme.BDR
import com.lab333.terminal.ui.theme.BG
import com.lab333.terminal.ui.theme.BG2
import com.lab333.terminal.ui.theme.BG3
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.FG2
import com.lab333.terminal.ui.theme.Warn

private val PERIODS = listOf("1H", "1D", "1W", "1M", "3M", "1Y", "5Y", "MAX")

@Composable
fun ChartsScreen(vm: TradingViewModel) {
    val summary = vm.summary.collectAsState().value
    val sources = (summary as? TradingViewModel.SummaryState.Ok)?.data?.sources.orEmpty()
        .sortedBy { if (it.id == "ibkr") 0 else 1 }
    val equityMap = vm.equity.collectAsState().value
    val periodMap = vm.equityPeriod.collectAsState().value

    LaunchedEffect(sources.map { it.id }) {
        sources.forEach { vm.refreshEquity(it.id, periodMap[it.id] ?: "1M") }
    }

    Column(Modifier.fillMaxSize().background(BG).verticalScroll(rememberScrollState())) {
        Box(
            Modifier.fillMaxWidth().background(BG2).padding(horizontal = 16.dp, vertical = 14.dp),
        ) {
            Text("Charts", color = Accent, style = MaterialTheme.typography.titleMedium)
        }
        Spacer(Modifier.height(8.dp))

        sources.forEach { src ->
            val state = equityMap[src.id]
            val period = periodMap[src.id] ?: "1M"
            ChartCard(src, state, period, onPeriod = { p -> vm.refreshEquity(src.id, p) })
            Spacer(Modifier.height(10.dp))
        }

        TickerSearchCard(vm)
        Spacer(Modifier.height(80.dp))
    }
}

@Composable
private fun PeriodPills(
    selected: String,
    onSelect: (String) -> Unit,
    accent: Color = Accent,
) {
    LazyRow(
        Modifier.fillMaxWidth().padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        items(PERIODS) { p ->
            val isSel = p == selected
            Box(
                Modifier
                    .clip(RoundedCornerShape(50))
                    .background(if (isSel) accent.copy(alpha = 0.18f) else BG3)
                    .padding(horizontal = 12.dp, vertical = 6.dp)
                    .clickableNoIndication { onSelect(p) },
            ) {
                Text(p, color = if (isSel) accent else FG2,
                     style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}

@Composable
private fun ChartCard(
    src: SummarySourceDto,
    state: TradingViewModel.EquityState?,
    period: String,
    onPeriod: (String) -> Unit,
) {
    val accent = colorFor(src.color)
    Box(Modifier.fillMaxWidth().padding(horizontal = 12.dp)) {
        Card(Modifier.fillMaxWidth()) {
            Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Pill(src.kind.uppercase(), accent)
                    Spacer(Modifier.size(6.dp))
                    if (src.realMoney) Pill("REAL", Warn)
                    Spacer(Modifier.size(6.dp))
                    Text(src.label, color = FG, style = MaterialTheme.typography.titleSmall)
                }
                Spacer(Modifier.height(6.dp))
                Row {
                    Text(money(src.equity), color = FG, style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.size(8.dp))
                    val delta = src.equity - src.startingCapital
                    val pctV = if (src.startingCapital > 0) delta / src.startingCapital * 100 else 0.0
                    Text(money(delta, withSign = true) + "  " + pct(pctV),
                         color = signColor(delta),
                         style = MaterialTheme.typography.labelMedium)
                }
                Spacer(Modifier.height(8.dp))
                PeriodPills(period, onPeriod, accent)
                Spacer(Modifier.height(6.dp))

                when (state) {
                    null,
                    is TradingViewModel.EquityState.Loading ->
                        Box(Modifier.fillMaxWidth().height(160.dp), contentAlignment = Alignment.Center) {
                            Text("Loading…", color = FG2, style = MaterialTheme.typography.labelSmall)
                        }
                    is TradingViewModel.EquityState.Err ->
                        Box(Modifier.fillMaxWidth().height(160.dp), contentAlignment = Alignment.Center) {
                            Text("No equity history", color = FG2, style = MaterialTheme.typography.labelSmall)
                        }
                    is TradingViewModel.EquityState.Ok -> {
                        if (state.data.size < 2) {
                            Box(Modifier.fillMaxWidth().height(160.dp), contentAlignment = Alignment.Center) {
                                Text("Not enough data yet", color = FG2,
                                     style = MaterialTheme.typography.labelSmall)
                            }
                        } else {
                            Spark(state.data.map { it.equity }, accent)
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun TickerSearchCard(vm: TradingViewModel) {
    var input by remember { mutableStateOf("") }
    var period by remember { mutableStateOf("1Y") }
    val state = vm.ticker.collectAsState().value

    Box(Modifier.fillMaxWidth().padding(horizontal = 12.dp)) {
        Card(Modifier.fillMaxWidth()) {
            Column {
                Text("Ticker", color = Accent, style = MaterialTheme.typography.titleSmall)
                Spacer(Modifier.height(6.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    OutlinedTextField(
                        value = input,
                        onValueChange = { input = it.uppercase().trim() },
                        singleLine = true,
                        placeholder = { Text("AAPL") },
                        modifier = Modifier.weight(1f),
                    )
                    Spacer(Modifier.size(8.dp))
                    TextButton(onClick = { vm.fetchTicker(input, period) }) { Text("Load") }
                }
                Spacer(Modifier.height(4.dp))
                PeriodPills(period, onSelect = {
                    period = it
                    if (input.isNotBlank()) vm.fetchTicker(input, it)
                })
                Spacer(Modifier.height(6.dp))
                when (state) {
                    is TradingViewModel.TickerState.Loading ->
                        Box(Modifier.fillMaxWidth().height(160.dp), contentAlignment = Alignment.Center) {
                            Text("Loading…", color = FG2)
                        }
                    is TradingViewModel.TickerState.Err ->
                        Box(Modifier.fillMaxWidth().height(160.dp), contentAlignment = Alignment.Center) {
                            Text(state.msg.take(80), color = FG2, style = MaterialTheme.typography.labelSmall)
                        }
                    is TradingViewModel.TickerState.Ok -> {
                        if (state.data.size < 2) {
                            Box(Modifier.fillMaxWidth().height(160.dp), contentAlignment = Alignment.Center) {
                                Text("No data", color = FG2)
                            }
                        } else {
                            val firstPx = state.data.first().price
                            val lastPx = state.data.last().price
                            val change = (lastPx / firstPx - 1) * 100
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(state.symbol, color = FG,
                                     style = MaterialTheme.typography.titleSmall)
                                Spacer(Modifier.size(8.dp))
                                Text("${money(lastPx)}  ${pct(change)}",
                                     color = signColor(change),
                                     style = MaterialTheme.typography.labelMedium)
                            }
                            Spacer(Modifier.height(6.dp))
                            Spark(state.data.map { it.price }, signColor(change))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun Spark(points: List<Double>, color: Color) {
    val anim = remember { Animatable(0f) }
    LaunchedEffect(points.size) {
        anim.snapTo(0f); anim.animateTo(1f, animationSpec = tween(700))
    }
    Box(
        Modifier.fillMaxWidth().height(160.dp)
            .clip(RoundedCornerShape(8.dp)).background(BG3),
    ) {
        Canvas(Modifier.fillMaxSize().padding(12.dp)) {
            val n = points.size
            val minV = points.min(); val maxV = points.max()
            val range = (maxV - minV).coerceAtLeast(1e-6)
            val limit = (n * anim.value).toInt().coerceAtLeast(2)
            val pathStroke = Path(); val pathFill = Path()
            for (i in 0 until limit) {
                val x = (i.toFloat() / (n - 1)) * size.width
                val y = size.height - ((points[i] - minV) / range).toFloat() * size.height
                if (i == 0) { pathStroke.moveTo(x, y); pathFill.moveTo(x, size.height); pathFill.lineTo(x, y) }
                else { pathStroke.lineTo(x, y); pathFill.lineTo(x, y) }
            }
            if (limit > 1) {
                val lastX = ((limit - 1).toFloat() / (n - 1)) * size.width
                pathFill.lineTo(lastX, size.height); pathFill.close()
            }
            drawPath(pathFill, brush = Brush.verticalGradient(listOf(color.copy(alpha = 0.35f), color.copy(alpha = 0.0f))))
            drawPath(pathStroke, color = color,
                     style = androidx.compose.ui.graphics.drawscope.Stroke(width = 3.5f))
        }
    }
}

