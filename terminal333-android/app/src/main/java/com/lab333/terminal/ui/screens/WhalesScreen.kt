package com.lab333.terminal.ui.screens

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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.material.ExperimentalMaterialApi
import androidx.compose.material.pullrefresh.PullRefreshIndicator
import androidx.compose.material.pullrefresh.pullRefresh
import androidx.compose.material.pullrefresh.rememberPullRefreshState
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.lab333.terminal.TradingViewModel
import com.lab333.terminal.data.WhaleAlertDto
import com.lab333.terminal.data.WhaleStatusDto
import com.lab333.terminal.ui.Card
import com.lab333.terminal.ui.Pill
import com.lab333.terminal.ui.SectionTitle
import com.lab333.terminal.ui.money
import com.lab333.terminal.ui.theme.Accent
import com.lab333.terminal.ui.theme.BG
import com.lab333.terminal.ui.theme.BG2
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.FG2
import com.lab333.terminal.ui.theme.Good
import com.lab333.terminal.ui.theme.Warn

private val Whale = Color(0xFFBC8CFF)
private val Bad   = Color(0xFFF85149)

@OptIn(ExperimentalMaterialApi::class)
@Composable
fun WhalesScreen(vm: TradingViewModel) {
    val state = vm.whales.collectAsState().value

    LaunchedEffect(Unit) { vm.refreshWhales() }

    val refreshing = state is TradingViewModel.WhalesState.Loading
    val pull = rememberPullRefreshState(refreshing, { vm.refreshWhales() })

    Box(Modifier.fillMaxSize().background(BG).pullRefresh(pull)) {
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
            TopBar()
            WhaleSignalsBlock(vm, showAlpacaHook = true, showPropDesk = true)
            Spacer(Modifier.height(80.dp))
        }
        PullRefreshIndicator(
            refreshing = refreshing, state = pull,
            modifier = Modifier.align(Alignment.TopCenter),
            backgroundColor = BG2, contentColor = Accent,
        )
    }
}

/**
 * The whole crypto whale scanner as an embeddable block: status strip
 * (HITS · DUNE · ETHERSCAN) + Q01 coordinated-accumulation alerts + the
 * Alpaca auto-trade hook. Kicks off its own refresh on first composition.
 * Safe to drop inside any vertically-scrolling parent.
 */
@Composable
fun WhaleSignalsBlock(vm: TradingViewModel,
                      showAlpacaHook: Boolean = true,
                      showPropDesk: Boolean = false) {
    val state = vm.whales.collectAsState().value

    LaunchedEffect(showPropDesk) {
        vm.refreshWhales()
        if (showPropDesk) {
            vm.refreshPropDesk()
            // Auto-poll prop desk every 3s only when we're rendering it (Lab)
            while (true) {
                kotlinx.coroutines.delay(3000)
                vm.refreshPropDesk()
            }
        }
    }

    Column(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        // ═══ PROP-DESK LIVE section — Lab only (paper testing) ═════════════
        if (showPropDesk) {
            PropDeskLiveBlock(vm)
        }

        // ═══ WHALE Q01 section (existing) ═══════════════════════════════════
        when (state) {
            is TradingViewModel.WhalesState.Loading -> InlineNote("Loading whale signals…", FG2)
            is TradingViewModel.WhalesState.Err     -> InlineNote("Whale backend error: ${state.msg}", Bad)
            is TradingViewModel.WhalesState.Ok      -> {
                StatusStrip(state.data)
                Text(
                    "Q01 · COORDINATED ACCUMULATION",
                    color = FG2,
                    style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold),
                )
                if (state.data.alerts.isEmpty()) {
                    EmptyState(state.data.lastTick, state.data.lastLine)
                } else {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        state.data.alerts.forEach { AlertRow(it) }
                    }
                }
                if (showAlpacaHook) AlpacaHookCard()
            }
        }
    }
}

@Composable
private fun TopBar() {
    Row(
        Modifier.fillMaxWidth().background(BG2).padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("🐳", color = Whale, style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.size(8.dp))
        Text("Whale Signals", color = Whale, style = MaterialTheme.typography.titleMedium.copy(
            fontWeight = FontWeight.Bold))
        Spacer(Modifier.size(10.dp))
        Text("on-chain smart-money accumulation", color = FG2,
             style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun StatusStrip(d: WhaleStatusDto) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        StatusChip(Modifier.weight(1f), "HITS",      d.seenCount.toString(),          Accent)
        StatusChip(Modifier.weight(1f), "DUNE",      if (d.duneConfigured)      "wired" else "off",
                   if (d.duneConfigured)      Good else Bad)
        StatusChip(Modifier.weight(1f), "ETHERSCAN", if (d.etherscanConfigured) "wired" else "off",
                   if (d.etherscanConfigured) Good else Bad)
    }
}

@Composable
private fun StatusChip(modifier: Modifier, label: String, value: String, tint: Color) {
    Box(
        modifier
            .clip(RoundedCornerShape(10.dp))
            .background(BG2)
            .padding(horizontal = 10.dp, vertical = 8.dp),
    ) {
        Column {
            Text(label, color = FG2, style = MaterialTheme.typography.labelSmall)
            Text(value, color = tint,
                 style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold))
        }
    }
}

@Composable
private fun AlertRow(a: WhaleAlertDto) {
    Card(Modifier.fillMaxWidth()) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill(a.chain.uppercase(), Whale)
                Spacer(Modifier.size(8.dp))
                Text(a.symbol, color = FG, style = MaterialTheme.typography.titleMedium.copy(
                    fontWeight = FontWeight.Bold))
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                    Text(money(a.aggUsd), color = Good,
                         style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold))
                }
            }
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${a.wallets} smart wallets", color = FG2,
                     style = MaterialTheme.typography.labelMedium)
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                    Text(a.ts, color = FG2, style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }
}

@Composable
private fun EmptyState(lastTick: String, lastLine: String) {
    Card(Modifier.fillMaxWidth()) {
        Column {
            Text("○  No alerts yet", color = FG2,
                 style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(4.dp))
            val msg = when {
                lastTick.isNotBlank() -> lastTick
                lastLine.isNotBlank() -> lastLine
                else                  -> "Runner hasn't posted a tick yet."
            }
            Text(msg, color = FG2, style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
private fun AlpacaHookCard() {
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(BG2)
            .padding(14.dp),
    ) {
        Column {
            Text("↔  ALPACA CRYPTO AUTO-TRADE", color = Warn,
                 style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
            Spacer(Modifier.height(4.dp))
            Text("Signal → order routing lands here once the Alpaca crypto path is wired.",
                 color = FG2, style = MaterialTheme.typography.labelSmall)
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = { },
                    enabled = false,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = BG,
                        disabledContainerColor = BG,
                        contentColor = Warn,
                        disabledContentColor = Warn.copy(alpha = 0.5f),
                    ),
                ) { Text("Auto-buy Q01", style = MaterialTheme.typography.labelSmall) }
                Button(
                    onClick = { },
                    enabled = false,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = BG,
                        disabledContainerColor = BG,
                        contentColor = Warn,
                        disabledContentColor = Warn.copy(alpha = 0.5f),
                    ),
                ) { Text("Manual buy", style = MaterialTheme.typography.labelSmall) }
            }
        }
    }
}

@Composable
private fun InlineNote(text: String, color: Color = FG2) {
    Card(Modifier.fillMaxWidth()) {
        Text(text, color = color, style = MaterialTheme.typography.bodyMedium)
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// PROP-DESK LIVE — rich real-time view of multi-strategy paper trading
// ═══════════════════════════════════════════════════════════════════════════

@Composable
private fun PropDeskLiveBlock(vm: TradingViewModel) {
    val s = vm.propDesk.collectAsState().value
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("⚡ PROP-DESK LIVE", color = Accent,
                 style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold))
            Spacer(Modifier.size(8.dp))
            Text("multi-strategy · paper", color = FG2,
                 style = MaterialTheme.typography.labelSmall)
        }

        when (s) {
            is TradingViewModel.PropDeskState.Loading ->
                InlineNote("Loading prop-desk state…", FG2)
            is TradingViewModel.PropDeskState.Err ->
                InlineNote("Prop-desk error: ${s.msg}", Bad)
            is TradingViewModel.PropDeskState.Ok ->
                PropDeskContent(s.data)
        }
    }
}

@Composable
private fun PropDeskContent(d: com.lab333.terminal.data.PropDeskStateDto) {
    val ageS = (System.currentTimeMillis() / 1000L - d.ts).coerceAtLeast(0)

    // TOP STRIP: regime | btc vol | btc | held | mode | trades | total P&L
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        val regimeCol = when (d.regime) {
            "trending" -> Good
            "chop"     -> Warn
            "mixed"    -> Accent
            else       -> FG2
        }
        StatChip(Modifier.weight(1f), "REGIME", d.regime.uppercase(), regimeCol)
        StatChip(Modifier.weight(1f), "BTC 1H", String.format("%.2f%%", d.btcVol1h), FG)
        StatChip(Modifier.weight(1f), "BTC",    "$${String.format("%,.0f", d.btcPrice)}", FG)
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        StatChip(Modifier.weight(1f), "HELD",   d.held.size.toString(), Accent)
        StatChip(Modifier.weight(1f), "MODE",   d.alpacaMode.uppercase(), Warn)
        StatChip(Modifier.weight(1f), "TRADES", d.ledger.nTrades.toString(), FG)
        val pnlCol = if (d.ledger.totalPnl > 0) Good else if (d.ledger.totalPnl < 0) Bad else FG2
        StatChip(Modifier.weight(1f), "P&L", "${String.format("%+.1f", d.ledger.totalPnl)}%", pnlCol)
    }
    Text("tick age: ${ageS}s", color = FG2,
         style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Normal))

    // 🧠 AUTO-TUNE BRAIN — this is what the bot is thinking right now
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("🧠", color = Whale, style = MaterialTheme.typography.bodyLarge,
                     modifier = Modifier.padding(end = 6.dp))
                Text("AUTO-TUNE BRAIN", color = Whale,
                     style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
                Spacer(Modifier.size(8.dp))
                Text("cfg v${d.autoTune.version} · gate ${String.format("%.2f", d.autoTune.minExpectedPct)}%",
                     color = FG2, style = MaterialTheme.typography.labelSmall)
                if (d.autoTune.readyForLive) {
                    Spacer(Modifier.size(6.dp))
                    Text(" READY_FOR_LIVE ", color = Good,
                         style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                         modifier = Modifier.background(Good.copy(alpha = 0.15f)).padding(horizontal = 4.dp))
                }
            }
            // Strategy health pills — big colored badges
            Row(Modifier.fillMaxWidth().padding(top = 4.dp),
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("momentum", "mean_reversion", "breakout").forEach { name ->
                    val h  = d.stratHealth[name]
                    val on = name in d.autoTune.enabledStrategies
                    val boost = d.autoTune.kellyBoosts[name] ?: 1.0
                    val col = when {
                        !on -> Bad
                        (h?.mean ?: 0.0) > 0 -> Good
                        else -> FG2
                    }
                    Column(
                        Modifier.weight(1f).clip(RoundedCornerShape(4.dp))
                                .background(col.copy(alpha = 0.12f)).padding(6.dp)
                    ) {
                        Text(name.take(4).uppercase(), color = col,
                             style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold))
                        Text(if (on) "ON" else "OFF", color = col,
                             style = MaterialTheme.typography.labelSmall)
                        if (h != null && h.n > 0) {
                            Text("n=${h.n}  ${String.format("%+.2f", h.mean)}%",
                                 color = FG2, style = MaterialTheme.typography.labelSmall)
                        }
                        if (boost != 1.0) {
                            Text("×${String.format("%.1f", boost)}", color = Good,
                                 style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }
            // Decision timeline — last N decisions
            if (d.decisions.isNotEmpty()) {
                Spacer(Modifier.size(6.dp))
                Text("DECISIONS (latest first)", color = FG2,
                     style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold))
                d.decisions.take(6).forEach { dec ->
                    val col = when (dec.type) {
                        "disable_strategy" -> Bad
                        "boost_strategy", "ready_for_live", "reenable_strategy" -> Good
                        "tighten_cost_gate", "loosen_cost_gate", "reset_kelly" -> Warn
                        else -> FG2
                    }
                    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                        Text("▸", color = col,
                             style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 6.dp))
                        Text(dec.type.replace("_", " "), color = col,
                             style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                             modifier = Modifier.padding(end = 6.dp))
                        if (dec.strategy.isNotEmpty()) {
                            Text(dec.strategy, color = Accent,
                                 style = MaterialTheme.typography.labelSmall,
                                 modifier = Modifier.padding(end = 6.dp))
                        }
                        Text(dec.reason.take(80), color = FG2,
                             style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
    }

    // 📊 VISUAL: Sankey — scan → strategies → gates → outcomes
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            SectionTitle("📊 SCAN FLOW (Sankey)")
            SankeyChart(d, heldCount = d.held.size)
        }
    }

    // 📈 VISUAL: Strategy edge over trades (cumulative pnl)
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            SectionTitle("📈 STRATEGY EDGE (cumulative %)")
            StrategyEdgeChart(d.edgeSeries)
        }
    }

    // 💧 VISUAL: Trade waterfall
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            SectionTitle("💧 RECENT TRADES WATERFALL")
            TradeWaterfall(d.recentTrades)
        }
    }

    // HELD POSITIONS
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            SectionTitle("● HELD POSITIONS")
            if (d.held.isEmpty()) {
                Text("(no positions)", color = FG2, style = MaterialTheme.typography.bodySmall)
            } else {
                d.held.forEach { h ->
                    val col = if (h.pnlPct > 0) Good else if (h.pnlPct < 0) Bad else FG
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(h.sym, color = FG,
                             style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Bold),
                             modifier = Modifier.padding(end = 8.dp))
                        Text("${String.format("%+.2f", h.pnlPct)}%", color = col,
                             style = MaterialTheme.typography.bodyMedium,
                             modifier = Modifier.padding(end = 8.dp))
                        Text("dd ${String.format("%+.2f", h.drawdown)}%", color = FG2,
                             style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 8.dp))
                        Text("$${String.format("%.0f", h.sizeUsd)}", color = FG2,
                             style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 8.dp))
                        Text(h.strategy, color = Accent,
                             style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 8.dp))
                        if (h.whaleScore != 0.0) {
                            Text("🐳${String.format("%+.1f", h.whaleScore)}", color = Whale,
                                 style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }
        }
    }

    // STRATEGIES ROW
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            SectionTitle("STRATEGIES THIS TICK")
            listOf("momentum", "mean_reversion", "breakout").forEach { name ->
                val s = d.strategies[name]
                if (s != null) {
                    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                        Text(name.uppercase().take(8), color = FG2,
                             style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                             modifier = Modifier.padding(end = 10.dp))
                        Text("B${s.buys}·S${s.sells}", color = FG,
                             style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 10.dp))
                        val topStr = s.top.take(3).joinToString("  ") {
                            "${it.sym}:${String.format("%+.2f", it.score)}"
                        }.ifEmpty { "-" }
                        Text(topStr, color = Accent,
                             style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
    }

    // TOP MOVERS
    if (d.movers.isNotEmpty()) {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                SectionTitle("↕ TOP MOVERS (2m)")
                d.movers.take(8).forEach { m ->
                    val col = if (m.v2mPct > 0) Good else Bad
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(m.sym, color = FG,
                             style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                             modifier = Modifier.padding(end = 8.dp))
                        Text("${String.format("%+.2f", m.v2mPct)}%", color = col,
                             style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 8.dp))
                        Text("${String.format("%+.2f", m.sigma)}σ", color = FG2,
                             style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 8.dp))
                        Text("typ ${String.format("%.2f", m.typ2m)}%", color = FG2,
                             style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
    }

    // WHALE CONFLUENCE
    if (d.whales.isNotEmpty()) {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                SectionTitle("🐳 WHALE CONFLUENCE (30m)")
                d.whales.take(8).forEach { w ->
                    val col = if (w.score > 0) Good else Bad
                    Row(Modifier.fillMaxWidth()) {
                        Text(w.sym, color = FG,
                             style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                             modifier = Modifier.padding(end = 8.dp))
                        Text(String.format("%+.1f", w.score), color = col,
                             style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 8.dp))
                        Text("${w.hits} hits", color = FG2,
                             style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 8.dp))
                        Text(w.hitTypes.take(3).joinToString(", "), color = FG2,
                             style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
    }

    // GATED
    if (d.gated.isNotEmpty()) {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                SectionTitle("⊘ GATED THIS TICK")
                d.gated.take(6).forEach { g ->
                    Text("${g.sym} · ${g.strategy} · ${g.reason}",
                         color = Warn, style = MaterialTheme.typography.labelSmall)
                }
            }
        }
    }

    // LEDGER BY STRATEGY
    if (d.ledger.byStrategy.isNotEmpty()) {
        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                SectionTitle("Σ  LEDGER BY STRATEGY")
                d.ledger.byStrategy.forEach { (k, v) ->
                    val col = if (v.total > 0) Good else Bad
                    Row(Modifier.fillMaxWidth()) {
                        Text(k, color = FG, style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 10.dp).weight(1f))
                        Text("n=${v.n}", color = FG2,
                             style = MaterialTheme.typography.labelSmall,
                             modifier = Modifier.padding(end = 10.dp))
                        Text("${String.format("%+.2f", v.total)}%", color = col,
                             style = MaterialTheme.typography.labelSmall)
                    }
                }
            }
        }
    }
}

@Composable
private fun StatChip(modifier: Modifier, label: String, value: String, tint: Color) {
    Column(
        modifier.clip(RoundedCornerShape(6.dp)).background(BG2).padding(8.dp),
        verticalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Text(label, color = FG2, style = MaterialTheme.typography.labelSmall)
        Text(value, color = tint,
             style = MaterialTheme.typography.bodyMedium.copy(fontWeight = FontWeight.Bold))
    }
}


// ═══════════════════════════════════════════════════════════════════════════
// VISUAL CHARTS — Sankey, edge line, trade waterfall
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Sankey diagram: 4 columns showing scan flow.
 * [Universe scanned] → [Strategy fires] → [Gated / Executed] → [Outcomes: Win/Loss/Wash/Held]
 */
@Composable
private fun SankeyChart(d: com.lab333.terminal.data.PropDeskStateDto, heldCount: Int) {
    val c = d.counters
    // Nodes
    val col1 = listOf("SCAN" to (c.symbolsScanned / 100.0).coerceAtLeast(1.0))
    val stratFires = listOf(
        "MOM" to (c.signalsFired["momentum"] ?: 0).toDouble(),
        "MEA" to (c.signalsFired["mean_reversion"] ?: 0).toDouble(),
        "BRK" to (c.signalsFired["breakout"] ?: 0).toDouble(),
        "WHA" to (c.signalsFired["whale_primary"] ?: 0).toDouble(),
    ).filter { it.second > 0 }
    val gateCol = listOf(
        "COST" to (c.gatedByReason["cost_gate"] ?: 0).toDouble(),
        "CLUS" to (c.gatedByReason["cluster_cap"] ?: 0).toDouble(),
        "EXEC" to (c.entriesExecuted.values.sum()).toDouble(),
    ).filter { it.second > 0 }
    val outcomeCol = listOf(
        "WIN"  to (c.exitsByOutcome["win"] ?: 0).toDouble(),
        "LOSS" to (c.exitsByOutcome["loss"] ?: 0).toDouble(),
        "WASH" to (c.exitsByOutcome["wash"] ?: 0).toDouble(),
        "HELD" to heldCount.toDouble(),
    ).filter { it.second > 0 }

    val colColors = mapOf(
        "SCAN" to FG,
        "MOM" to Accent, "MEA" to Warn, "BRK" to Good, "WHA" to Whale,
        "COST" to Warn, "CLUS" to Warn, "EXEC" to Good,
        "WIN" to Good, "LOSS" to Bad, "WASH" to FG2, "HELD" to Accent,
    )

    val chartHeight = 200.dp
    Canvas(Modifier.fillMaxWidth().height(chartHeight).background(BG2).padding(8.dp)) {
        val w = size.width
        val h = size.height
        val columns = listOf(col1, stratFires, gateCol, outcomeCol)
        val colXCenters = List(4) { (it + 0.5f) * (w / 4f) }
        val nodeW = 26f

        // Sum per column
        val colSums = columns.map { it.sumOf { p -> p.second }.coerceAtLeast(0.0001) }

        // Draw each column of nodes
        val nodeRects = mutableListOf<MutableList<Triple<Float, Float, String>>>()
        for ((i, colNodes) in columns.withIndex()) {
            val totalV = colSums[i]
            val gap    = 6f
            val avail  = h - gap * (colNodes.size - 1).coerceAtLeast(0)
            var y = 0f
            val rects = mutableListOf<Triple<Float, Float, String>>()
            for ((name, v) in colNodes) {
                val nh = ((v / totalV) * avail).toFloat().coerceAtLeast(4f)
                drawRect(
                    color = colColors[name] ?: FG,
                    topLeft = Offset(colXCenters[i] - nodeW / 2f, y),
                    size = androidx.compose.ui.geometry.Size(nodeW, nh),
                )
                rects += Triple(y, nh, name)
                y += nh + gap
            }
            nodeRects += rects
        }

        // Draw flows between adjacent columns.
        // Distribute each source node's flow to targets proportional to target size.
        fun drawFlows(fromCol: Int, toCol: Int) {
            val fromRects = nodeRects[fromCol]
            val toRects   = nodeRects[toCol]
            val toTotal = toRects.sumOf { it.second.toDouble() }.coerceAtLeast(0.0001)
            var srcYCursor = fromRects.map { it.first }.toMutableList()
            val srcHeights = fromRects.map { it.second }.toMutableList()

            // simple: each source contributes to each target proportional to target's size
            for ((si, src) in fromRects.withIndex()) {
                val sHeight = src.second
                var dstYCursor = toRects.map { it.first }.toMutableList()
                for ((di, dst) in toRects.withIndex()) {
                    val portion = (dst.second / toTotal).toFloat().coerceIn(0f, 1f)
                    val flowH = sHeight * portion
                    if (flowH < 0.5f) continue

                    val sx = colXCenters[fromCol] + nodeW / 2f
                    val ex = colXCenters[toCol] - nodeW / 2f
                    val sy1 = srcYCursor[si]
                    val sy2 = sy1 + flowH
                    val ey1 = dstYCursor[di]
                    val ey2 = ey1 + flowH

                    val path = Path().apply {
                        moveTo(sx, sy1)
                        cubicTo((sx + ex) / 2f, sy1, (sx + ex) / 2f, ey1, ex, ey1)
                        lineTo(ex, ey2)
                        cubicTo((sx + ex) / 2f, ey2, (sx + ex) / 2f, sy2, sx, sy2)
                        close()
                    }
                    val col = colColors[src.third] ?: FG
                    drawPath(path, color = col.copy(alpha = 0.35f))

                    srcYCursor[si] = sy2
                    dstYCursor[di] = ey2
                }
            }
        }
        drawFlows(0, 1)
        drawFlows(1, 2)
        drawFlows(2, 3)
    }

    // Node labels row
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
        listOf("scan", "fires", "gate", "exit").forEach { label ->
            Text(label, color = FG2,
                 style = MaterialTheme.typography.labelSmall,
                 modifier = Modifier.weight(1f))
        }
    }
}

/**
 * Line chart: cumulative pnl per strategy over trade index.
 * Multiple lines overlaid.
 */
@Composable
private fun StrategyEdgeChart(edgeSeries: Map<String, List<Double>>) {
    if (edgeSeries.isEmpty() || edgeSeries.values.all { it.isEmpty() }) {
        Text("(no trades yet)", color = FG2,
             style = MaterialTheme.typography.labelSmall)
        return
    }
    val colors = mapOf(
        "momentum" to Accent, "mean_reversion" to Warn, "breakout" to Good,
        "whale_primary" to Whale,
    )
    val allPoints = edgeSeries.values.flatten()
    val yMin = (allPoints.minOrNull() ?: 0.0).coerceAtMost(0.0)
    val yMax = (allPoints.maxOrNull() ?: 0.0).coerceAtLeast(0.0)
    val range = (yMax - yMin).coerceAtLeast(0.5)
    val maxLen = edgeSeries.values.maxOf { it.size }.coerceAtLeast(2)

    Canvas(Modifier.fillMaxWidth().height(140.dp).background(BG2).padding(6.dp)) {
        val w = size.width; val h = size.height
        val zeroY = h - ((0.0 - yMin) / range * h).toFloat()
        // Zero line
        drawLine(FG2.copy(alpha = 0.35f),
                 Offset(0f, zeroY), Offset(w, zeroY),
                 pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f)))

        for ((strat, series) in edgeSeries) {
            if (series.size < 2) continue
            val col = colors[strat] ?: FG
            val path = Path()
            for ((i, v) in series.withIndex()) {
                val x = i.toFloat() / (maxLen - 1) * w
                val y = h - ((v - yMin) / range * h).toFloat()
                if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            drawPath(path, color = col, style = Stroke(width = 3f))
        }
    }
    // Legend
    Row(Modifier.fillMaxWidth().padding(top = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        edgeSeries.forEach { (strat, series) ->
            val col = colors[strat] ?: FG
            val last = series.lastOrNull() ?: 0.0
            Text("${strat.take(3)} ${String.format("%+.1f", last)}%",
                 color = col, style = MaterialTheme.typography.labelSmall)
        }
    }
}

/**
 * Waterfall: bar per trade, green if pnl > 0, red if < 0.
 * Cumulative line overlaid.
 */
@Composable
private fun TradeWaterfall(trades: List<com.lab333.terminal.data.RecentTradeDto>) {
    if (trades.isEmpty()) {
        Text("(no closed trades yet)", color = FG2,
             style = MaterialTheme.typography.labelSmall)
        return
    }
    val pnls = trades.map { it.pnlPct }
    val cum = mutableListOf<Double>()
    var acc = 0.0
    for (p in pnls) { acc += p; cum += acc }
    val yMin = (pnls.min().coerceAtMost(cum.min())).coerceAtMost(0.0)
    val yMax = (pnls.max().coerceAtLeast(cum.max())).coerceAtLeast(0.0)
    val range = (yMax - yMin).coerceAtLeast(0.5)
    val n = trades.size

    Canvas(Modifier.fillMaxWidth().height(140.dp).background(BG2).padding(6.dp)) {
        val w = size.width; val h = size.height
        val zeroY = h - ((0.0 - yMin) / range * h).toFloat()
        val slot = w / n

        drawLine(FG2.copy(alpha = 0.35f),
                 Offset(0f, zeroY), Offset(w, zeroY),
                 pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f)))

        // Bars
        for ((i, p) in pnls.withIndex()) {
            val x = i * slot
            val barTop = h - ((p.coerceAtLeast(0.0) - yMin) / range * h).toFloat()
            val barBot = h - ((p.coerceAtMost(0.0) - yMin) / range * h).toFloat()
            val col = if (p > 0) Good else if (p < 0) Bad else FG2
            drawRect(color = col.copy(alpha = 0.75f),
                     topLeft = Offset(x + 1f, minOf(barTop, barBot)),
                     size = androidx.compose.ui.geometry.Size(slot - 2f, kotlin.math.abs(barBot - barTop).coerceAtLeast(1f)))
        }
        // Cumulative line
        val path = Path()
        for ((i, v) in cum.withIndex()) {
            val x = i * slot + slot / 2f
            val y = h - ((v - yMin) / range * h).toFloat()
            if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
        }
        drawPath(path, color = Accent, style = Stroke(width = 3f))
    }
    Row(Modifier.fillMaxWidth().padding(top = 4.dp),
        horizontalArrangement = Arrangement.SpaceBetween) {
        Text("last ${trades.size} trades", color = FG2,
             style = MaterialTheme.typography.labelSmall)
        val finalCum = cum.lastOrNull() ?: 0.0
        val col = if (finalCum > 0) Good else if (finalCum < 0) Bad else FG2
        Text("cum ${String.format("%+.2f", finalCum)}%", color = col,
             style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold))
    }
}
