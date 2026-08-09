package com.lab333.terminal.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.ExperimentalMaterialApi
import androidx.compose.material.pullrefresh.PullRefreshIndicator
import androidx.compose.material.pullrefresh.pullRefresh
import androidx.compose.material.pullrefresh.rememberPullRefreshState
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.lab333.terminal.TradingViewModel
import com.lab333.terminal.data.PatentCliffDto
import com.lab333.terminal.ui.Card
import com.lab333.terminal.ui.Pill
import com.lab333.terminal.ui.StatLabel
import com.lab333.terminal.ui.StatValue
import com.lab333.terminal.ui.theme.Accent
import com.lab333.terminal.ui.theme.BG
import com.lab333.terminal.ui.theme.BG2
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.FG2
import com.lab333.terminal.ui.theme.Good
import com.lab333.terminal.ui.theme.Warn

@OptIn(ExperimentalMaterialApi::class)
@Composable
fun PatentCliffScreen(vm: TradingViewModel) {
    val state = vm.patentCliff.collectAsState().value
    LaunchedEffect(Unit) { vm.refreshPatentCliff() }

    val refreshing = state is TradingViewModel.PatentCliffState.Loading
    val pull = rememberPullRefreshState(refreshing, { vm.refreshPatentCliff() })

    Box(Modifier.fillMaxSize().background(BG).pullRefresh(pull)) {
        Column {
            Box(
                Modifier.fillMaxWidth().background(BG2)
                    .padding(horizontal = 16.dp, vertical = 14.dp),
            ) {
                Text("Patent Cliff", color = Accent, style = MaterialTheme.typography.titleMedium)
            }
            when (state) {
                is TradingViewModel.PatentCliffState.Loading ->
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text("Loading…", color = FG2)
                    }
                is TradingViewModel.PatentCliffState.Err ->
                    ErrorBanner(state.msg) { vm.refreshPatentCliff() }
                is TradingViewModel.PatentCliffState.Ok ->
                    PatentCliffBody(state.data)
            }
        }
        PullRefreshIndicator(
            refreshing = refreshing, state = pull,
            modifier = Modifier.align(Alignment.TopCenter),
            backgroundColor = BG2, contentColor = Accent,
        )
    }
}

@Composable
private fun PatentCliffBody(d: PatentCliffDto) {
    LazyColumn(
        verticalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 8.dp, bottom = 80.dp),
    ) {
        item { StatsCard(d) }
        item {
            SectionHeader(
                "Top cliffs",
                "${d.cliffs.size} of ${d.stats.cliffCount} in window"
            )
        }
        items(d.cliffs.take(15), key = { it.cliffKey() }) { c -> CliffCard(c) }

        if (d.firstFiler.isNotEmpty()) {
            item {
                SectionHeader(
                    "First-filer opportunities",
                    "${d.stats.firstFilerCount} Eligible + low-ANDA"
                )
            }
            items(d.firstFiler, key = { it.cliffKey() }) { f -> FirstFilerCard(f) }
        }

        if (d.exposure.isNotEmpty()) {
            item { SectionHeader("Generic-maker exposure", "${d.stats.exposureCount} tickers") }
            items(d.exposure.take(12), key = { it["ticker"].orEmpty() }) { e -> ExposureCard(e) }
        }

        if (d.picks.isNotEmpty()) {
            item { SectionHeader("Sized portfolio picks", "${d.stats.picksCount} positions") }
            items(d.picks, key = { it["ticker"].orEmpty() }) { p -> PickCard(p) }
        }

        if (d.secSignals.isNotEmpty()) {
            item { SectionHeader("Forward-looking SEC signals", "${d.stats.secSignalCount} extracted") }
            items(d.secSignals, key = { it.cliffKey() }) { s -> SecSignalCard(s) }
        }
    }
}

private fun Map<String, String>.cliffKey(): String =
    listOf("Trade_Name", "Ingredient", "ticker", "drug_mentioned", "applicant").joinToString("|") {
        (this[it] ?: "")
    }.hashCode().toString()

@Composable
private fun StatsCard(d: PatentCliffDto) {
    Card(Modifier.fillMaxWidth()) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Pill("READ-ONLY RESEARCH", Accent)
                if (d.stats.secSignalCount > 0) Pill("LLM ENRICHED", Good)
            }
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                Column { StatLabel("CLIFFS"); StatValue("${d.stats.cliffCount}", FG) }
                Column { StatLabel("1ST-FILER"); StatValue("${d.stats.firstFilerCount}", FG) }
                Column { StatLabel("TICKERS"); StatValue("${d.stats.exposureCount}", FG) }
                Column { StatLabel("PICKS"); StatValue("${d.stats.picksCount}", FG) }
            }
            Spacer(Modifier.height(6.dp))
            d.generated.cliffScan?.let {
                Text(
                    "Latest scan: ${it.take(19)}",
                    color = FG2, style = MaterialTheme.typography.labelSmall,
                )
            }
        }
    }
}

@Composable
private fun SectionHeader(title: String, subtitle: String) {
    Column(Modifier.padding(top = 8.dp, bottom = 2.dp)) {
        Text(title, color = Accent,
             style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold))
        Text(subtitle, color = FG2, style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun CliffCard(c: Map<String, String>) {
    val brand = c["Trade_Name"].orEmpty().ifBlank { c["Ingredient"].orEmpty() }
    val source = c["source"].orEmpty()
    val loe = c["loe_date"].orEmpty().take(10)
    val months = c["months_to_loe"]?.toDoubleOrNull()
    val sales = c["sales_usd_b"]?.toDoubleOrNull()
    val score = c["score"]?.toDoubleOrNull() ?: 0.0
    val applicant = c["Applicant_Full_Name"].orEmpty()

    Card(Modifier.fillMaxWidth()) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Pill(if (source == "purple_book") "BIO" else "SMALL", Accent)
                Text(brand, color = FG,
                     style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold))
            }
            Spacer(Modifier.height(2.dp))
            Text(applicant.take(40), color = FG2, style = MaterialTheme.typography.labelSmall)
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                Column { StatLabel("LOE"); StatValue(loe, FG) }
                months?.let { Column { StatLabel("MONTHS"); StatValue("%.1f".format(it), FG) } }
                sales?.let { Column { StatLabel("SALES \$B"); StatValue("%.1f".format(it), FG) } }
                Column {
                    StatLabel("SCORE")
                    StatValue("%.2f".format(score),
                              if (score >= 1.0) Good else FG)
                }
            }
        }
    }
}

@Composable
private fun FirstFilerCard(f: Map<String, String>) {
    val brand = f["Trade_Name"].orEmpty()
    val andas = f["andas_min"].orEmpty()
    val status = f["iv_status"].orEmpty()
    val sales = f["sales_usd_b"]?.toDoubleOrNull()
    val ffs = f["first_filer_score"]?.toDoubleOrNull() ?: 0.0
    val loe = f["loe_date"].orEmpty().take(10)

    Card(Modifier.fillMaxWidth()) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Pill("$andas ANDA${if (andas == "1") "" else "s"}",
                     if (andas == "1") Good else Warn)
                Text(brand, color = FG,
                     style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold))
            }
            Spacer(Modifier.height(2.dp))
            Text(status, color = FG2, style = MaterialTheme.typography.labelSmall)
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                Column { StatLabel("LOE"); StatValue(loe, FG) }
                sales?.let { Column { StatLabel("SALES \$B"); StatValue("%.1f".format(it), FG) } }
                Column {
                    StatLabel("FF SCORE")
                    StatValue("%.2f".format(ffs),
                              if (ffs >= 2.0) Good else FG)
                }
            }
        }
    }
}

@Composable
private fun ExposureCard(e: Map<String, String>) {
    val ticker = e["ticker"].orEmpty()
    val score = e["total_cliff_exposure"]?.toDoubleOrNull() ?: 0.0
    val cliffCount = e["cliff_count"]?.toIntOrNull() ?: 0
    val top = e["top_brands"].orEmpty().split(",").take(3).joinToString(", ")

    Card(Modifier.fillMaxWidth()) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill(ticker, if (score >= 5.0) Good else Accent)
                Spacer(Modifier.size(8.dp))
                Text("%.2f exposure · %d cliffs".format(score, cliffCount),
                     color = FG, style = MaterialTheme.typography.labelMedium)
            }
            Spacer(Modifier.height(4.dp))
            Text(top, color = FG2, style = MaterialTheme.typography.labelSmall)
        }
    }
}

@Composable
private fun PickCard(p: Map<String, String>) {
    val ticker = p["ticker"].orEmpty()
    val target = p["target_usd"]?.toDoubleOrNull() ?: 0.0
    val score = p["combined_score"]?.toDoubleOrNull() ?: 0.0

    Card(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Pill(ticker, Accent)
            Column {
                Text("Target \$%.2f".format(target),
                     color = FG, style = MaterialTheme.typography.labelMedium)
                Text("Score %.2f".format(score),
                     color = FG2, style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

@Composable
private fun SecSignalCard(s: Map<String, String>) {
    val ticker = s["ticker"].orEmpty()
    val drug = s["drug_mentioned"].orEmpty()
    val type = s["signal_type"].orEmpty().replace("_", " ")
    val sentiment = s["sentiment"].orEmpty()
    val confidence = s["confidence"]?.toDoubleOrNull() ?: 0.0
    val window = s["target_event_window"].orEmpty()
    val quote = s["evidence_quote"].orEmpty()
    val sentimentColor = when (sentiment) { "positive" -> Good; "negative" -> Warn; else -> FG2 }

    Card(Modifier.fillMaxWidth()) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Pill(ticker, Accent)
                Pill(sentiment.uppercase(), sentimentColor)
                Text("%.0f%%".format(confidence * 100),
                     color = FG, style = MaterialTheme.typography.labelMedium)
            }
            Spacer(Modifier.height(4.dp))
            Text("$drug · $type", color = FG,
                 style = MaterialTheme.typography.labelMedium)
            if (window.isNotBlank()) {
                Text("Window: $window", color = FG2,
                     style = MaterialTheme.typography.labelSmall)
            }
            if (quote.isNotBlank()) {
                Spacer(Modifier.height(2.dp))
                Text("\"$quote\"", color = FG2,
                     style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}
