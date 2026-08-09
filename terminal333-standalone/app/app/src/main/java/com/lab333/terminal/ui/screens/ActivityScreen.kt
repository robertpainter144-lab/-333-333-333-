package com.lab333.terminal.ui.screens

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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.lab333.terminal.TradingViewModel
import com.lab333.terminal.data.ActivityDto
import com.lab333.terminal.ui.Card
import com.lab333.terminal.ui.Pill
import com.lab333.terminal.ui.SegmentedTabs
import com.lab333.terminal.ui.colorFor
import com.lab333.terminal.ui.formatTimestampShort
import com.lab333.terminal.ui.money
import com.lab333.terminal.ui.theme.Accent
import com.lab333.terminal.ui.theme.BDR
import com.lab333.terminal.ui.theme.BG
import com.lab333.terminal.ui.theme.BG2
import com.lab333.terminal.ui.theme.Bad
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.FG2
import com.lab333.terminal.ui.theme.Good
import com.lab333.terminal.ui.theme.Purple
import com.lab333.terminal.ui.theme.Warn

@OptIn(ExperimentalMaterialApi::class)
@Composable
fun ActivityScreen(vm: TradingViewModel) {
    val summary = vm.summary.collectAsState().value
    val sources = (summary as? TradingViewModel.SummaryState.Ok)?.data?.sources.orEmpty()

    var selected by rememberSaveable { mutableStateOf("all") }
    val activities = vm.activities.collectAsState().value
    val state = activities[selected]

    LaunchedEffect(selected) { vm.refreshActivity(selected) }

    val refreshing = state is TradingViewModel.ActivityState.Loading
    val pull = rememberPullRefreshState(refreshing, { vm.refreshActivity(selected) })

    Box(Modifier.fillMaxSize().background(BG).pullRefresh(pull)) {
        Column {
            Box(
                Modifier
                    .fillMaxWidth()
                    .background(BG2)
                    .padding(horizontal = 16.dp, vertical = 14.dp),
            ) {
                Text("Activity", color = Accent, style = MaterialTheme.typography.titleMedium)
            }

            val ordered = sources.sortedBy { if (it.id == "ibkr") 0 else 1 }
            val tabs = listOf("all" to "All") + ordered.map { it.id to it.label.removePrefix("Alpaca ") }
            SegmentedTabs(
                items = tabs,
                selected = selected,
                onSelect = { selected = it },
                accent = { id -> if (id == "all") Accent else colorFor(sources.firstOrNull { s -> s.id == id }?.color ?: "#58a6ff") },
            )
            Spacer(Modifier.height(8.dp))

            when (state) {
                null,
                is TradingViewModel.ActivityState.Loading -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text("Loading…", color = FG2)
                    }
                }
                is TradingViewModel.ActivityState.Err -> {
                    ErrorBanner(state.msg) { vm.refreshActivity(selected) }
                }
                is TradingViewModel.ActivityState.Ok -> {
                    if (state.rows.isEmpty()) {
                        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                            Text("No activity yet.", color = FG2)
                        }
                    } else {
                        LazyColumn(
                            verticalArrangement = Arrangement.spacedBy(6.dp),
                            contentPadding = androidx.compose.foundation.layout.PaddingValues(
                                start = 12.dp, end = 12.dp, top = 4.dp, bottom = 80.dp,
                            ),
                        ) {
                            items(state.rows, key = { it.source + (it.orderId?.toString() ?: it.ts) + it.symbol + it.ts }) { row ->
                                ActivityRow(row, sources)
                            }
                        }
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
}

@Composable
private fun ActivityRow(row: ActivityDto, sources: List<com.lab333.terminal.data.SummarySourceDto>) {
    val sourceColor = colorFor(sources.firstOrNull { it.id == row.source }?.color ?: "#58a6ff")
    val sideColor = if (row.side == "BUY") Good else Bad
    val statusColor = when (row.status.uppercase()) {
        "FILLED"  -> Good
        "CANCELED", "CANCELLED", "REJECTED" -> Bad
        "PENDING_SUBMIT", "SUBMITTED", "PENDINGSUBMIT", "PENDING_NEW", "NEW" -> Warn
        else      -> FG2
    }

    Card(Modifier.fillMaxWidth()) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill(row.side, sideColor)
                Spacer(Modifier.size(6.dp))
                Text(row.symbol, color = FG, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.size(8.dp))
                Pill(row.source.uppercase(), sourceColor)
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                    Text(formatTimestampShort(row.ts), color = FG2,
                         style = MaterialTheme.typography.labelSmall)
                }
            }
            Spacer(Modifier.height(6.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("${"%.4f".format(row.qty).trimEnd('0').trimEnd('.')} @ ${money(row.price)}",
                     color = FG, style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.size(8.dp))
                Text("= ${money(row.notional)}", color = FG2,
                     style = MaterialTheme.typography.bodyMedium)
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                    Pill(row.status, statusColor)
                }
            }
            if (row.reason.isNotBlank()) {
                Spacer(Modifier.height(4.dp))
                Text("• ${row.reason}", color = FG2, style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}
