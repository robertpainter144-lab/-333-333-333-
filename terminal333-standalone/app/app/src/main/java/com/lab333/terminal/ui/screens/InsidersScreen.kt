package com.lab333.terminal.ui.screens

import androidx.compose.foundation.background
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
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.lab333.terminal.TradingViewModel
import com.lab333.terminal.data.InsiderSignalDto
import com.lab333.terminal.ui.Card
import com.lab333.terminal.ui.Pill
import com.lab333.terminal.ui.money
import com.lab333.terminal.ui.signColor
import com.lab333.terminal.ui.theme.Accent
import com.lab333.terminal.ui.theme.BG
import com.lab333.terminal.ui.theme.BG2
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.FG2
import com.lab333.terminal.ui.theme.Good
import com.lab333.terminal.ui.theme.Warn

@OptIn(ExperimentalMaterialApi::class)
@Composable
fun InsidersScreen(vm: TradingViewModel) {
    val state = vm.insider.collectAsState().value

    LaunchedEffect(Unit) {
        vm.refreshInsider()
        vm.refreshLeaderboard()
    }

    val refreshing = state is TradingViewModel.InsiderState.Loading
    val pull = rememberPullRefreshState(refreshing, { vm.refreshInsider() })

    Box(Modifier.fillMaxSize().background(BG).pullRefresh(pull)) {
        Column {
            Box(
                Modifier
                    .fillMaxWidth()
                    .background(BG2)
                    .padding(horizontal = 16.dp, vertical = 14.dp),
            ) {
                Text("Insider Signals", color = Accent,
                     style = MaterialTheme.typography.titleMedium)
            }

            when (state) {
                is TradingViewModel.InsiderState.Loading -> {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text("Loading…", color = FG2)
                    }
                }
                is TradingViewModel.InsiderState.Err -> {
                    ErrorBanner(state.msg) { vm.refreshInsider() }
                }
                is TradingViewModel.InsiderState.Ok -> {
                    val sigs = state.data.signals
                    Column {
                        LeaderboardSummary(vm)
                        Text(
                            "${sigs.size} signals in last 35d  •  scanner ${state.data.ts.takeLast(8)}",
                            color = FG2,
                            style = MaterialTheme.typography.labelSmall,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 6.dp),
                        )
                        if (sigs.isEmpty()) {
                            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                                Text("No qualifying signals.", color = FG2)
                            }
                        } else {
                            LazyColumn(
                                verticalArrangement = Arrangement.spacedBy(6.dp),
                                contentPadding = androidx.compose.foundation.layout.PaddingValues(
                                    start = 12.dp, end = 12.dp, top = 6.dp, bottom = 80.dp,
                                ),
                            ) {
                                items(sigs, key = { it.ticker + it.signalDate }) { sig ->
                                    SignalCard(sig)
                                }
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
private fun LeaderboardSummary(vm: TradingViewModel) {
    val state = vm.leaderboard.collectAsState().value
    when (state) {
        is TradingViewModel.LeaderboardState.Loading -> Unit
        is TradingViewModel.LeaderboardState.Err -> Unit
        is TradingViewModel.LeaderboardState.Ok -> {
            val byHash = state.ledgers.ledgers.associateBy { it.configHash }
            val leaders = state.leaderboard.leaders.take(5)
            if (leaders.isEmpty()) return
            Column(Modifier.padding(horizontal = 12.dp, vertical = 6.dp)) {
                Text(
                    "Strategy Leaderboard — ${state.leaderboard.nConfigsTested} configs tested, top ${leaders.size}",
                    color = FG2,
                    style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                    modifier = Modifier.padding(bottom = 4.dp),
                )
                leaders.forEach { L ->
                    val ledger = byHash[L.configHash]
                    Card(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                        Column {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Pill("#${L.rank}", Accent)
                                Spacer(Modifier.size(6.dp))
                                Text(L.configHash, color = FG,
                                     style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
                                Spacer(Modifier.size(6.dp))
                                if (L.beatsSpyEveryFold) Pill("BEATS SPY EVERY FOLD", Good)
                                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                                    val pnl = ledger?.retPct ?: 0.0
                                    Text(
                                        "live ${"%.2f".format(pnl)}%",
                                        color = signColor(pnl),
                                        style = MaterialTheme.typography.labelMedium,
                                    )
                                }
                            }
                            Spacer(Modifier.height(2.dp))
                            Text(
                                "score ${"%.2f".format(L.compositeScore)}   " +
                                "sharpe ${"%.2f".format(L.meanSharpe)}   " +
                                "alpha ${"%.1f".format(L.meanAlphaPct)}%   " +
                                "DD ${"%.1f".format(L.worstDdPct)}%",
                                color = FG2,
                                style = MaterialTheme.typography.labelSmall,
                            )
                            if (ledger != null) {
                                Text(
                                    "ledger ${"%.0f".format(ledger.equity)} of ${"%.0f".format(ledger.initialCash)} " +
                                    "•  ${ledger.nPositions} pos  ${ledger.nClosed} closed",
                                    color = FG2,
                                    style = MaterialTheme.typography.labelSmall,
                                )
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SignalCard(sig: InsiderSignalDto) {
    var expanded by remember { mutableStateOf(false) }
    val gradeColor = when (sig.quality) {
        "A" -> Good             // single CEO mega-buy
        "B" -> Accent           // qualifying cluster
        else -> Warn            // watchlist
    }

    Card(Modifier.fillMaxWidth().clickable { expanded = !expanded }) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill(sig.quality, gradeColor)
                Spacer(Modifier.size(8.dp))
                Text(sig.ticker, color = FG,
                     style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.size(6.dp))
                Text(sig.company.take(28), color = FG2,
                     style = MaterialTheme.typography.labelSmall)
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                    Text(sig.signalDate, color = FG2,
                         style = MaterialTheme.typography.labelSmall)
                }
            }
            Spacer(Modifier.height(4.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "${sig.clusterSize} insider${if (sig.clusterSize == 1) "" else "s"}  •  " +
                    "total ${money(sig.totalValue)}  •  largest ${money(sig.largestBuy)}",
                    color = FG,
                    style = MaterialTheme.typography.labelMedium,
                )
            }
            if (sig.topOfficers.isNotEmpty()) {
                Spacer(Modifier.height(2.dp))
                Text(
                    "Officers: ${sig.topOfficers.joinToString(", ")}",
                    color = FG2,
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            if (expanded) {
                Spacer(Modifier.height(8.dp))
                Text("Purchases", color = FG2,
                     style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold))
                Spacer(Modifier.height(4.dp))
                sig.purchases.forEach { p ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                        Column {
                            Text("${p.insider}  •  ${p.title}",
                                 color = FG, style = MaterialTheme.typography.labelMedium)
                            Text("${p.txDate}  •  ${p.qty} @ ${money(p.price)}  =  ${money(p.value)}",
                                 color = FG2, style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }
        }
    }
}
