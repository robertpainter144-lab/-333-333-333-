package com.quarki3.trading.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.ExperimentalMaterialApi
import androidx.compose.material.pullrefresh.PullRefreshIndicator
import androidx.compose.material.pullrefresh.pullRefresh
import androidx.compose.material.pullrefresh.rememberPullRefreshState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.quarki3.trading.TradingViewModel
import com.quarki3.trading.data.PositionDto
import com.quarki3.trading.data.SummarySourceDto
import com.quarki3.trading.ui.BlinkingDot
import com.quarki3.trading.ui.Card
import com.quarki3.trading.ui.Pill
import com.quarki3.trading.ui.SectionTitle
import com.quarki3.trading.ui.SegmentedTabs
import com.quarki3.trading.ui.StatLabel
import com.quarki3.trading.ui.StatValue
import com.quarki3.trading.ui.colorFor
import com.quarki3.trading.ui.money
import com.quarki3.trading.ui.pct
import com.quarki3.trading.ui.signColor
import com.quarki3.trading.ui.theme.Accent
import com.quarki3.trading.ui.theme.BDR
import com.quarki3.trading.ui.theme.BG
import com.quarki3.trading.ui.theme.BG2
import com.quarki3.trading.ui.theme.FG
import com.quarki3.trading.ui.theme.FG2
import com.quarki3.trading.ui.theme.Good
import com.quarki3.trading.ui.theme.Warn

@OptIn(ExperimentalMaterialApi::class)
@Composable
fun PortfolioScreen(vm: TradingViewModel, pinnedSource: String? = null) {
    val state = vm.summary.collectAsState().value
    val refreshing = vm.refreshing.collectAsState().value
    // When pinnedSource is set (e.g. dedicated Alpaca tab), skip source pills entirely.
    var selected by rememberSaveable { mutableStateOf(pinnedSource ?: "ibkr") }
    var showSettings by remember { mutableStateOf(false) }

    val pull = rememberPullRefreshState(refreshing, { vm.refreshSummary() })

    Box(Modifier.fillMaxSize().background(BG).pullRefresh(pull)) {
        Column(Modifier.fillMaxSize()) {
            TopHeader(state, onSettings = { showSettings = true })

            when (state) {
                is TradingViewModel.SummaryState.Loading -> Loader("Loading portfolio…")
                is TradingViewModel.SummaryState.Err -> ErrorBanner(state.msg) { vm.refreshSummary() }
                is TradingViewModel.SummaryState.Ok -> {
                    if (pinnedSource != null) {
                        val src = state.data.sources.firstOrNull { it.id == pinnedSource }
                        if (src == null) {
                            Loader("Source '$pinnedSource' not available yet…")
                        } else {
                            SourceBody(src, vm)
                        }
                    } else {
                        // IBKR Live (real money) always leftmost so the most-important
                        // account is the first thing the user sees.
                        val sources = state.data.sources.sortedBy { if (it.id == "ibkr") 0 else 1 }
                        val current = sources.firstOrNull { it.id == selected } ?: sources.firstOrNull()

                        SegmentedTabs(
                            items = sources.map { it.id to it.label },
                            selected = current?.id ?: "",
                            onSelect = { selected = it },
                            accent = { id -> colorFor(sources.firstOrNull { s -> s.id == id }?.color ?: "#58a6ff") },
                        )
                        Spacer(Modifier.height(10.dp))

                        current?.let { src -> SourceBody(src, vm) }
                    }
                }
            }
        }

        PullRefreshIndicator(
            refreshing = refreshing,
            state = pull,
            modifier = Modifier.align(Alignment.TopCenter),
            backgroundColor = BG2,
            contentColor = Accent,
        )
    }

    if (showSettings) {
        SettingsDialog(vm, onDismiss = { showSettings = false })
    }
}

@Composable
private fun TopHeader(state: TradingViewModel.SummaryState, onSettings: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .background(BG2)
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        BlinkingDot(
            color = if (state is TradingViewModel.SummaryState.Err) Color.Red else Good,
        )
        Spacer(Modifier.size(8.dp))
        Text("Trading", color = Accent, style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.size(8.dp))
        val ts = (state as? TradingViewModel.SummaryState.Ok)?.data?.ts ?: ""
        if (ts.isNotEmpty()) {
            val ageLabel = summaryAgeLabel(ts)
            val ageColor = summaryAgeColor(ts)
            Text(ts.takeLast(8), color = FG2, style = MaterialTheme.typography.labelSmall)
            Spacer(Modifier.size(6.dp))
            Text(ageLabel, color = ageColor,
                 style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold))
        }

        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
            IconButton(onClick = onSettings) {
                Icon(Icons.Outlined.Settings, contentDescription = "Settings", tint = FG2)
            }
        }
    }
}

private fun summaryAgeSeconds(ts: String): Long {
    return try {
        val parsed = java.time.OffsetDateTime.parse(ts)
        java.time.Duration.between(parsed.toInstant(), java.time.Instant.now()).seconds
    } catch (_: Throwable) {
        -1L
    }
}

private fun summaryAgeLabel(ts: String): String {
    val s = summaryAgeSeconds(ts)
    return when {
        s < 0     -> "—"
        s < 60    -> "${s}s ago"
        s < 3600  -> "${s / 60}m ago"
        else      -> "${s / 3600}h ago"
    }
}

private fun summaryAgeColor(ts: String): Color {
    val s = summaryAgeSeconds(ts)
    return when {
        s < 0    -> FG2
        s < 15   -> Good
        s < 60   -> Warn
        else     -> Color(0xFFF85149)
    }
}

@Composable
private fun SourceBody(src: SummarySourceDto, vm: com.quarki3.trading.TradingViewModel) {
    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 12.dp),
    ) {
        EquityCard(src)
        Spacer(Modifier.height(10.dp))
        StatsGrid(src)
        Spacer(Modifier.height(10.dp))
        CollapsibleChart {
            RealtimeEquityChart(source = src.id, vm = vm, breakeven = src.startingCapital)
        }
        SectionTitle("Positions  (${src.positionCount})")
        if (src.positions.isEmpty()) {
            Card(Modifier.fillMaxWidth()) {
                Column {
                    Text("○  No open positions", color = FG2,
                         style = MaterialTheme.typography.titleSmall)
                    Text("Filled orders will show up here as they post.",
                         color = FG2, style = MaterialTheme.typography.labelSmall)
                }
            }
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                src.positions.forEach { p -> PositionRow(p, src.equity) }
            }
        }
        // Real-money-only: always show recent broker activity (auto-recorded).
        // Paper/prop-desk stays out of the Book realm entirely.
        if (src.id == "alpaca_live") {
            Spacer(Modifier.height(18.dp))
            QcMirrorBlock(vm)
        }
        if (src.id == "alpaca_live" || src.id == "ibkr") {
            Spacer(Modifier.height(18.dp))
            RecentRealFillsBlock(vm, source = src.id)
        }
        Spacer(Modifier.height(80.dp))
    }
}

@Composable
private fun QcMirrorBlock(vm: TradingViewModel) {
    val qc = vm.qc.collectAsState().value
    LaunchedEffect(Unit) {
        vm.refreshQc()
        while (true) {
            kotlinx.coroutines.delay(15000)
            vm.refreshQc()
        }
    }
    val ok = qc as? TradingViewModel.QcState.Ok ?: return
    val d = ok.data
    // Book is real-money-only. If the mirror is not routing to LIVE, don't
    // render the block here at all — it belongs in Lab.
    if (d.mirrorMode != "live") return
    val mirrorPnl = d.mirrorPnlPct ?: return

    Column(Modifier.fillMaxWidth().padding(horizontal = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("📈", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.size(6.dp))
            Text("QC MIRROR", color = FG,
                 style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
            Spacer(Modifier.size(8.dp))
            Text("mirrors QC daily target composition on this account",
                 color = FG2, style = MaterialTheme.typography.labelSmall)
        }
        Spacer(Modifier.height(6.dp))
        Card(Modifier.fillMaxWidth()) {
            Column {
                Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                    Column {
                        Text("BANKROLL", color = FG2,
                             style = MaterialTheme.typography.labelSmall)
                        Text("$${d.mirrorBankroll ?: 0.0}", color = FG,
                             style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold))
                    }
                    Column {
                        Text("P&L", color = FG2,
                             style = MaterialTheme.typography.labelSmall)
                        val col = if (mirrorPnl > 0) Good
                                  else if (mirrorPnl < 0) Color(0xFFF85149) else FG
                        Text("${"%+.2f".format(mirrorPnl)}%", color = col,
                             style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold))
                    }
                    Column {
                        Text("MODE", color = FG2,
                             style = MaterialTheme.typography.labelSmall)
                        Text("LIVE", color = Good,
                             style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold))
                    }
                }
                Spacer(Modifier.height(6.dp))
                Text(
                    "Signal source: QC Engine in Lab. Rebalances every 5 min against " +
                    "QC's daily target weights.",
                    color = FG2, style = MaterialTheme.typography.labelSmall,
                )
            }
        }
    }
}

@Composable
private fun RecentRealFillsBlock(vm: TradingViewModel, source: String) {
    val allActivities = vm.activities.collectAsState().value
    val state = allActivities[source]

    LaunchedEffect(source) {
        vm.refreshActivity(source)
        // Auto-refresh every 15s so any new fill shows up without user action
        while (true) {
            kotlinx.coroutines.delay(15000)
            vm.refreshActivity(source)
        }
    }

    Column(Modifier.fillMaxWidth().padding(horizontal = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("💵", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.size(6.dp))
            Text("RECENT ACTIVITY", color = FG,
                 style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
            Spacer(Modifier.size(8.dp))
            Text("auto-recorded from broker", color = FG2,
                 style = MaterialTheme.typography.labelSmall)
        }
        Spacer(Modifier.height(6.dp))
        when (state) {
            null,
            is TradingViewModel.ActivityState.Loading -> {
                Text("Loading…", color = FG2, style = MaterialTheme.typography.labelSmall)
            }
            is TradingViewModel.ActivityState.Err -> {
                Text("Error: ${state.msg}", color = Warn, style = MaterialTheme.typography.labelSmall)
            }
            is TradingViewModel.ActivityState.Ok -> {
                if (state.rows.isEmpty()) {
                    Card(Modifier.fillMaxWidth()) {
                        Text("○  No activity yet", color = FG2,
                             style = MaterialTheme.typography.titleSmall)
                    }
                } else {
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        state.rows.take(15).forEach { a ->
                            Card(Modifier.fillMaxWidth()) {
                                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                    val sideCol = if (a.side.equals("SELL", true)) Warn
                                                  else if (a.side.equals("BUY", true)) Good
                                                  else FG2
                                    Text(a.side.uppercase().take(4), color = sideCol,
                                         style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                                         modifier = Modifier.padding(end = 6.dp))
                                    Text(a.symbol, color = FG,
                                         style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Bold),
                                         modifier = Modifier.padding(end = 8.dp))
                                    Text("${String.format("%.2f", a.qty)} @ ${money(a.price)}",
                                         color = FG2, style = MaterialTheme.typography.labelSmall,
                                         modifier = Modifier.padding(end = 6.dp))
                                    Text(a.status, color = if (a.status.equals("Filled", true)) Good else FG2,
                                         style = MaterialTheme.typography.labelSmall,
                                         modifier = Modifier.padding(end = 6.dp))
                                    Spacer(Modifier.weight(1f))
                                    Text(a.ts.take(19).replace("T", " "), color = FG2,
                                         style = MaterialTheme.typography.labelSmall)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun AlpacaWhaleHeader() {
    Box(Modifier.fillMaxWidth().padding(horizontal = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("🐳", style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.size(8.dp))
            Text(
                "WHALE SIGNALS",
                color = Color(0xFFBC8CFF),
                style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold),
            )
            Spacer(Modifier.size(10.dp))
            Text(
                "on-chain smart-money accumulation",
                color = FG2,
                style = MaterialTheme.typography.labelSmall,
            )
        }
    }
}

@Composable
private fun EquityCard(src: SummarySourceDto) {
    val accent = colorFor(src.color)
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(BG2)
            .padding(16.dp),
    ) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill(src.kind.uppercase(), accent)
                Spacer(Modifier.size(6.dp))
                if (src.realMoney) Pill("REAL MONEY", Warn)
                Spacer(Modifier.size(6.dp))
                Text(src.label, color = FG, style = MaterialTheme.typography.titleSmall)
            }
            Spacer(Modifier.height(10.dp))
            Text("EQUITY", color = FG2, style = MaterialTheme.typography.labelSmall)
            Text(money(src.equity), color = FG, style = MaterialTheme.typography.titleLarge)
            Spacer(Modifier.height(4.dp))
            Row {
                val totalReturn = if (src.startingCapital > 0)
                    (src.equity - src.startingCapital) / src.startingCapital * 100 else 0.0
                Text("vs start: ", color = FG2, style = MaterialTheme.typography.labelMedium)
                Text(money(src.equity - src.startingCapital, withSign = true),
                     color = signColor(totalReturn),
                     style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
                Text("  •  ", color = FG2, style = MaterialTheme.typography.labelMedium)
                Text(pct(totalReturn), color = signColor(totalReturn),
                     style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
            }
        }
    }
}

@Composable
private fun StatsGrid(src: SummarySourceDto) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        StatChip(Modifier.weight(1f), "CASH",     money(src.cash),             FG)
        StatChip(Modifier.weight(1f), "BP",       money(src.bp),               FG)
        StatChip(Modifier.weight(1f), "UNREAL",   money(src.unrealizedPl, true), signColor(src.unrealizedPl))
    }
    Spacer(Modifier.height(8.dp))
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        StatChip(Modifier.weight(1f), "PEAK",  money(src.peakEquity), FG)
        StatChip(Modifier.weight(1f), "DD",    pct(-src.drawdownPct, withSign = false),
                 if (src.drawdownPct > 0.0001) Color(0xFFF85149) else FG)
        StatChip(Modifier.weight(1f), "START", money(src.startingCapital), FG2)
    }
}

@Composable
private fun StatChip(modifier: Modifier, label: String, value: String, color: Color) {
    Box(
        modifier
            .clip(RoundedCornerShape(10.dp))
            .background(BG2)
            .padding(horizontal = 10.dp, vertical = 8.dp),
    ) {
        Column {
            StatLabel(label)
            StatValue(value, color)
        }
    }
}

@Composable
private fun PositionRow(p: PositionDto, portEquity: Double) {
    val stripe = signColor(p.unrealizedPl)
    val weight = if (portEquity > 0) p.marketValue / portEquity * 100 else 0.0
    val context = androidx.compose.ui.platform.LocalContext.current
    var expanded by remember { mutableStateOf(false) }
    val openFinviz = {
        val url = "https://finviz.com/quote.ashx?t=${p.symbol}"
        val intent = android.content.Intent(
            android.content.Intent.ACTION_VIEW,
            android.net.Uri.parse(url),
        )
        intent.setPackage("org.mozilla.firefox")
        try {
            context.startActivity(intent)
        } catch (_: android.content.ActivityNotFoundException) {
            intent.setPackage(null)
            try { context.startActivity(intent) } catch (_: Exception) {}
        }
    }
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(BG2)
            .pointerInput(p.symbol) {
                detectTapGestures(
                    onTap = { expanded = !expanded },
                    onDoubleTap = { openFinviz() },
                )
            },
    ) {
    Row(Modifier.fillMaxWidth()) {
        Box(
            Modifier
                .size(width = 4.dp, height = 60.dp)
                .background(stripe),
        )
        Column(Modifier.padding(horizontal = 12.dp, vertical = 10.dp).fillMaxWidth()) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(p.symbol, color = FG, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.size(8.dp))
                Text("× ${"%.4f".format(p.qty).trimEnd('0').trimEnd('.')}",
                     color = FG2, style = MaterialTheme.typography.labelMedium)
                Spacer(Modifier.size(8.dp))
                Text("${"%.1f".format(weight)}% port", color = FG2,
                     style = MaterialTheme.typography.labelSmall)
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                    Text(money(p.marketValue), color = FG,
                         style = MaterialTheme.typography.titleSmall)
                }
            }
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("avg ${money(p.avgCost)}  •  px ${money(p.marketPrice)}",
                     color = FG2, style = MaterialTheme.typography.labelMedium)
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                    Row {
                        Text(money(p.unrealizedPl, withSign = true),
                             color = stripe,
                             style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
                        Text("  ${pct(p.unrealizedPlPct)}", color = stripe,
                             style = MaterialTheme.typography.labelMedium)
                    }
                }
            }
        }
    }
    // ─── Inline daily chart when expanded ────────────────────────────
    if (expanded) {
        Column(Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp)) {
            TickerMiniChart(symbol = p.symbol, vm = null)
        }
    }
    }
}

// Simple line chart for a ticker's recent daily closes. Fetches from
// /api/ticker/{symbol}?period=1M via yfinance.
@Composable
private fun TickerMiniChart(symbol: String, vm: TradingViewModel?) {
    var closes by remember(symbol) { mutableStateOf<List<Double>>(emptyList()) }
    var loading by remember(symbol) { mutableStateOf(true) }

    LaunchedEffect(symbol) {
        loading = true
        // MUST switch to IO — HttpURLConnection on Main throws
        // NetworkOnMainThreadException, which was silently swallowed and left
        // the chart with no data.
        try {
            val list = kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                val backend = com.quarki3.trading.BuildConfig.DEFAULT_BACKEND
                val url = java.net.URL("$backend/api/ticker/$symbol?period=1M")
                val conn = url.openConnection() as java.net.HttpURLConnection
                conn.connectTimeout = 6000; conn.readTimeout = 10000
                val text = conn.inputStream.bufferedReader().use { it.readText() }
                // /api/ticker returns a JSON array of {ts, price}
                val root = kotlinx.serialization.json.Json.parseToJsonElement(text)
                (root as? kotlinx.serialization.json.JsonArray)?.mapNotNull {
                    (it as? kotlinx.serialization.json.JsonObject)
                        ?.get("price")?.toString()?.trim('"')?.toDoubleOrNull()
                } ?: emptyList()
            }
            closes = list
        } catch (_: Throwable) { }
        loading = false
    }

    if (loading && closes.isEmpty()) {
        Text("Loading chart…", color = FG2, style = MaterialTheme.typography.labelSmall)
        return
    }
    if (closes.size < 2) {
        Text("No chart data.", color = FG2, style = MaterialTheme.typography.labelSmall)
        return
    }
    val minV = closes.min(); val maxV = closes.max(); val range = (maxV - minV).coerceAtLeast(0.01)
    val gain = closes.last() >= closes.first()
    val col = if (gain) com.quarki3.trading.ui.theme.Good else com.quarki3.trading.ui.theme.Bad
    androidx.compose.foundation.Canvas(Modifier.fillMaxWidth().height(90.dp)) {
        val w = size.width; val h = size.height
        val path = androidx.compose.ui.graphics.Path()
        closes.forEachIndexed { i, v ->
            val x = i.toFloat() / (closes.size - 1) * w
            val y = h - ((v - minV) / range * h).toFloat()
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        drawPath(path, color = col,
                 style = androidx.compose.ui.graphics.drawscope.Stroke(width = 3f))
    }
    Row(Modifier.fillMaxWidth().padding(top = 2.dp)) {
        Text("1M daily", color = FG2, style = MaterialTheme.typography.labelSmall)
        Spacer(Modifier.size(8.dp))
        val chg = (closes.last() - closes.first()) / closes.first() * 100
        Text("${"%+.2f".format(chg)}% (1M)", color = col,
             style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold))
        Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
            Text("double-tap → Finviz", color = FG2, style = MaterialTheme.typography.labelSmall)
        }
    }
}

// Collapsible wrapper for the portfolio equity chart. Prominent button-styled
// header so it's obviously interactive.
@Composable
private fun CollapsibleChart(content: @Composable () -> Unit) {
    var expanded by rememberSaveable { mutableStateOf(true) }
    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp))
                .background(BG2)
                .clickable { expanded = !expanded }
                .padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(if (expanded) "▼" else "▶", color = Accent,
                 style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.size(8.dp))
            Text("PORTFOLIO VALUE  (tap to " + (if (expanded) "collapse" else "expand") + ")",
                 color = FG, style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                Text("pinch = zoom · drag = pan", color = FG2,
                     style = MaterialTheme.typography.labelSmall)
            }
        }
        if (expanded) {
            Spacer(Modifier.size(4.dp))
            content()
        }
    }
}

@Composable
private fun Loader(text: String) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(text, color = FG2)
        }
    }
}

@Composable
fun ErrorBanner(msg: String, onRetry: () -> Unit) {
    Box(Modifier.fillMaxWidth().padding(12.dp)) {
        Card(Modifier.fillMaxWidth()) {
            Column {
                Text("Cannot reach backend", color = Color(0xFFF85149),
                     style = MaterialTheme.typography.titleSmall)
                Spacer(Modifier.size(4.dp))
                Text(msg, color = FG2, style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.size(8.dp))
                TextButton(onClick = onRetry) { Text("Retry") }
            }
        }
    }
}

@Composable
private fun SettingsDialog(vm: TradingViewModel, onDismiss: () -> Unit) {
    val current = vm.backendUrl.collectAsState(initial = "").value
    var url by remember(current) { mutableStateOf(current) }

    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = BG2,
        textContentColor = FG,
        titleContentColor = FG,
        title = { Text("Backend") },
        text = {
            Column {
                Text("Base URL of the dashboard server. Use the Mac's Tailscale IP.",
                     color = FG2, style = MaterialTheme.typography.bodySmall)
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = url,
                    onValueChange = { url = it },
                    singleLine = true,
                    label = { Text("http://100.x.x.x:8080") },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { vm.setBackend(url); onDismiss() }) { Text("Save") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}
