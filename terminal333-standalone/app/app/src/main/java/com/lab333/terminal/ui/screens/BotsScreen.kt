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
import androidx.compose.runtime.collectAsState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.lab333.terminal.TradingViewModel
import com.lab333.terminal.data.BotDto
import com.lab333.terminal.ui.BlinkingDot
import com.lab333.terminal.ui.Card
import com.lab333.terminal.ui.Pill
import com.lab333.terminal.ui.theme.Accent
import com.lab333.terminal.ui.theme.BG
import com.lab333.terminal.ui.theme.BG2
import com.lab333.terminal.ui.theme.Bad
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.FG2
import com.lab333.terminal.ui.theme.Good
import com.lab333.terminal.ui.theme.Warn

@OptIn(ExperimentalMaterialApi::class)
@Composable
fun BotsScreen(vm: TradingViewModel) {
    val summary = vm.summary.collectAsState().value
    val refreshing = vm.refreshing.collectAsState().value
    val pull = rememberPullRefreshState(refreshing, { vm.refreshSummary() })

    Box(Modifier.fillMaxSize().background(BG).pullRefresh(pull)) {
        Column(Modifier.fillMaxSize()) {
            Box(
                Modifier
                    .fillMaxWidth()
                    .background(BG2)
                    .padding(horizontal = 16.dp, vertical = 14.dp),
            ) {
                Text("Bots", color = Accent, style = MaterialTheme.typography.titleMedium)
            }

            when (summary) {
                is TradingViewModel.SummaryState.Loading -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text("Loading…", color = FG2)
                    }
                }
                is TradingViewModel.SummaryState.Err -> {
                    ErrorBanner(summary.msg) { vm.refreshSummary() }
                }
                is TradingViewModel.SummaryState.Ok -> {
                    LazyColumn(
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(12.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        items(summary.data.bots, key = { it.id }) { b ->
                            BotCard(b)
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
private fun BotCard(b: BotDto) {
    Card(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            BlinkingDot(if (b.running) Good else FG2)
            Spacer(Modifier.size(10.dp))
            Column(Modifier.weight(1f)) {
                Text(b.name, color = FG, style = MaterialTheme.typography.titleSmall)
                Spacer(Modifier.size(2.dp))
                Text(
                    "${b.host}${b.pid?.let { "  •  pid $it" } ?: ""}",
                    color = FG2, style = MaterialTheme.typography.labelSmall,
                )
            }
            Pill(if (b.running) "RUNNING" else "OFFLINE", if (b.running) Good else Bad)
        }
    }
}
