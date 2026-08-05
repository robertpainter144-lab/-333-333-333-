package com.quarki3.trading.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.quarki3.trading.TradingViewModel
import com.quarki3.trading.data.CatalogCurveDto
import com.quarki3.trading.data.CatalogMetaDto
import com.quarki3.trading.data.EdgeStrategyDto
import com.quarki3.trading.ui.theme.Accent
import com.quarki3.trading.ui.theme.BDR
import com.quarki3.trading.ui.theme.BG
import com.quarki3.trading.ui.theme.BG2
import com.quarki3.trading.ui.theme.FG
import com.quarki3.trading.ui.theme.FG2
import com.quarki3.trading.ui.theme.Good
import com.quarki3.trading.ui.theme.Bad
import com.quarki3.trading.ui.theme.Purple
import com.quarki3.trading.ui.theme.Warn

/**
 * The Catalog Realm — OWNER-PRIVATE, this app only.
 *
 * Autopilot strategy-catalog benchmarks (hand-entered + public-filing
 * portfolios) tracked forward from public prices and compared head-to-head
 * with the QuantConnect engine. Served by /api/catalog, which is never exposed
 * on the shared Terminal. This is Robert's private optimisation lens.
 */
@Composable
fun CatalogScreen(vm: TradingViewModel) {
    val state by vm.catalog.collectAsState()

    LaunchedEffect(Unit) {
        vm.refreshCatalog()
        while (true) {
            kotlinx.coroutines.delay(30000)
            vm.refreshCatalog()
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .background(BG)
            .padding(horizontal = 12.dp),
    ) {
        Spacer(Modifier.height(12.dp))
        Text(
            "Strategy Catalog",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
            color = FG,
        )
        Text(
            "Public-source portfolios, marked to market vs. QuantConnect. Private to you.",
            style = MaterialTheme.typography.bodySmall,
            color = FG2,
        )
        Spacer(Modifier.height(10.dp))

        when (val s = state) {
            is TradingViewModel.CatalogState.Loading ->
                Centered("Loading catalog…", FG2)
            is TradingViewModel.CatalogState.Err ->
                Centered("Couldn't reach /api/catalog — ${s.msg}", Bad)
            is TradingViewModel.CatalogState.Ok -> {
                val d = s.data
                if (d.pilots.isEmpty()) {
                    Centered(
                        "No pilots yet. Add holdings to autopilot_catalog.json " +
                            "or run the 13F / congress fetchers.",
                        FG2,
                    )
                } else {
                    // Rank pilots by total P/L so the best benchmark floats up.
                    val ranked = d.pilots.sortedByDescending { it.totalPnlPct ?: -1e9 }
                    LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        d.qcReference?.let { qc ->
                            item { QcReferenceCard(qc) }
                        }
                        items(ranked, key = { it.name }) { p ->
                            PilotCard(p, d.curves[pilotKey(p.name)], d.meta[pilotKey(p.name)])
                        }
                        item { Spacer(Modifier.height(24.dp)) }
                    }
                }
            }
        }
    }
}

/** "autopilot_cat:Point72" -> "Point72" to look up the curve map. */
private fun pilotKey(name: String): String =
    name.removePrefix("autopilot_cat:")

private fun prettyName(name: String): String =
    pilotKey(name).replace('_', ' ')

@Composable
private fun QcReferenceCard(qc: EdgeStrategyDto) {
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(BG2)
            .border(1.dp, Accent.copy(alpha = 0.5f), RoundedCornerShape(10.dp))
            .padding(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("QuantConnect (reference)", color = Accent, fontWeight = FontWeight.SemiBold)
                Text(
                    "your engine — every catalog line is measured against this",
                    style = MaterialTheme.typography.bodySmall,
                    color = FG2,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                PnlText(qc.totalPnlPct, big = true)
                Text("Sharpe ${fmt(qc.sharpe)}", style = MaterialTheme.typography.bodySmall, color = FG2)
            }
        }
    }
}

@Composable
private fun PilotCard(p: EdgeStrategyDto, curve: CatalogCurveDto?, meta: CatalogMetaDto? = null) {
    var open by remember { mutableStateOf(false) }
    val days = curve?.curve?.size ?: p.nClosedTrades
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(BG2)
            .border(1.dp, BDR, RoundedCornerShape(10.dp))
            .clickable { open = !open }
            .padding(12.dp),
    ) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(prettyName(p.name), color = FG, fontWeight = FontWeight.SemiBold)
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        VerdictPill(p.verdict)
                        Pill("BENCHMARK", Purple)
                        Text(
                            "$days ${if (days == 1) "day" else "days"}",
                            style = MaterialTheme.typography.bodySmall,
                            color = FG2,
                        )
                    }
                }
                Column(horizontalAlignment = Alignment.End) {
                    PnlText(p.totalPnlPct, big = true)
                    Text("Sharpe ${fmt(p.sharpe)}", style = MaterialTheme.typography.bodySmall, color = FG2)
                }
            }
            meta?.blurb?.let {
                Spacer(Modifier.height(6.dp))
                Text(it, style = MaterialTheme.typography.bodySmall, color = FG2)
            }
            Text(
                if (open) "collapse ▴" else "tap for full dossier ›",
                style = MaterialTheme.typography.labelSmall,
                color = FG2,
                modifier = Modifier.padding(top = 4.dp),
            )
            if (open) {
                Spacer(Modifier.height(10.dp))
                meta?.detail?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = FG)
                    Spacer(Modifier.height(8.dp))
                }
                BusRow("CONSUMES", meta?.consumes)
                BusRow("EMITS", meta?.emits)
                BusRow("CADENCE", meta?.cadence)
                if (meta?.consumes != null || meta?.emits != null) Spacer(Modifier.height(8.dp))
                curve?.let { c ->
                    val holdings = c.weights.entries.sortedByDescending { it.value }.take(8)
                    if (holdings.isNotEmpty()) {
                        Text("HOLDINGS", style = MaterialTheme.typography.labelSmall, color = FG2)
                        Spacer(Modifier.height(4.dp))
                        holdings.forEach { (ticker, w) ->
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                Text(ticker, color = FG, fontFamily = FontFamily.Monospace,
                                    style = MaterialTheme.typography.bodySmall)
                                Text("${fmt(w * 100)}%", color = FG2,
                                    fontFamily = FontFamily.Monospace,
                                    style = MaterialTheme.typography.bodySmall)
                            }
                        }
                    }
                    c.inception?.let {
                        Spacer(Modifier.height(6.dp))
                        Text("tracking since $it (index base 100)",
                            style = MaterialTheme.typography.bodySmall, color = FG2)
                    }
                }
                (meta?.note)?.let {
                    Spacer(Modifier.height(6.dp))
                    Text(it, style = MaterialTheme.typography.labelSmall, color = FG2)
                }
            }
        }
    }
}

@Composable
private fun BusRow(label: String, value: String?) {
    if (value == null) return
    Row(Modifier.fillMaxWidth().padding(vertical = 1.dp)) {
        Text(label, style = MaterialTheme.typography.labelSmall, color = FG2,
            modifier = Modifier.padding(end = 8.dp))
        Text(value, style = MaterialTheme.typography.bodySmall, color = FG)
    }
}

@Composable
private fun VerdictPill(verdict: String) {
    val color = when (verdict) {
        "PROVEN" -> Good
        "PROMISING" -> Accent
        "LOSING" -> Bad
        else -> FG2
    }
    Pill(verdict, color)
}

@Composable
private fun Pill(text: String, color: Color) {
    Box(
        Modifier
            .clip(RoundedCornerShape(4.dp))
            .background(color.copy(alpha = 0.15f))
            .padding(horizontal = 6.dp, vertical = 1.dp),
    ) {
        Text(text, color = color, style = MaterialTheme.typography.labelSmall, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
private fun PnlText(pct: Double?, big: Boolean = false) {
    val v = pct ?: 0.0
    val color = if (v >= 0) Good else Bad
    val sign = if (v >= 0) "+" else ""
    Text(
        "$sign${fmt(pct)}%",
        color = color,
        fontWeight = FontWeight.Bold,
        fontFamily = FontFamily.Monospace,
        style = if (big) MaterialTheme.typography.titleMedium else MaterialTheme.typography.bodyMedium,
    )
}

@Composable
private fun Centered(text: String, color: Color) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(text, color = color, style = MaterialTheme.typography.bodyMedium)
    }
}

private fun fmt(v: Double?): String =
    if (v == null) "—" else String.format("%.2f", v)
