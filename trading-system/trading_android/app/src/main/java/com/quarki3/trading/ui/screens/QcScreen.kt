package com.quarki3.trading.ui.screens

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
import com.quarki3.trading.TradingViewModel
import com.quarki3.trading.data.QcStrategyDto
import com.quarki3.trading.ui.Card
import com.quarki3.trading.ui.Pill
import com.quarki3.trading.ui.StatLabel
import com.quarki3.trading.ui.StatValue
import com.quarki3.trading.ui.money
import com.quarki3.trading.ui.pct
import com.quarki3.trading.ui.signColor
import com.quarki3.trading.ui.theme.Accent
import com.quarki3.trading.ui.theme.BG
import com.quarki3.trading.ui.theme.BG2
import com.quarki3.trading.ui.theme.FG
import com.quarki3.trading.ui.theme.FG2
import com.quarki3.trading.ui.theme.Good
import com.quarki3.trading.ui.theme.Warn
import com.quarki3.trading.ui.theme.Bad

@OptIn(ExperimentalMaterialApi::class)
@Composable
fun QcScreen(vm: TradingViewModel) {
    val state = vm.qc.collectAsState().value
    LaunchedEffect(Unit) { vm.refreshQc() }

    val refreshing = state is TradingViewModel.QcState.Loading
    val pull = rememberPullRefreshState(refreshing, { vm.refreshQc() })

    Box(Modifier.fillMaxSize().background(BG).pullRefresh(pull)) {
        Column {
            Box(
                Modifier.fillMaxWidth().background(BG2)
                    .padding(horizontal = 16.dp, vertical = 14.dp),
            ) {
                Text("QC Regime", color = Accent, style = MaterialTheme.typography.titleMedium)
            }
            when (state) {
                is TradingViewModel.QcState.Loading ->
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Text("Loading…", color = FG2)
                    }
                is TradingViewModel.QcState.Err ->
                    ErrorBanner(state.msg) { vm.refreshQc() }
                is TradingViewModel.QcState.Ok -> {
                    val q = state.data
                    LazyColumn(
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(
                            start = 12.dp, end = 12.dp, top = 8.dp, bottom = 80.dp,
                        ),
                    ) {
                        // ─── QC engine ($50K notional signal source only) ───
                        item {
                            Card(Modifier.fillMaxWidth()) {
                                Column {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Pill("QC ENGINE (signal source · $50K notional)", Accent)
                                        Spacer(Modifier.size(6.dp))
                                        Pill(
                                            "exposure ${"%.0f".format(q.exposureLevel * 100)}%",
                                            if (q.exposureLevel >= 1.0) Good else Warn
                                        )
                                    }
                                    Spacer(Modifier.height(4.dp))
                                    Text(
                                        "5 QuantConnect strategies run against a $50K notional book to produce " +
                                        "daily target weights. Real-money $50 mirror is in Book → Alpaca.",
                                        color = FG2, style = MaterialTheme.typography.labelSmall,
                                    )
                                    Spacer(Modifier.height(8.dp))
                                    Row(horizontalArrangement = Arrangement.spacedBy(16.dp)) {
                                        Column {
                                            StatLabel("QC INITIAL")
                                            StatValue(money(q.initialEquity), FG)
                                        }
                                        Column {
                                            StatLabel("QC PEAK")
                                            StatValue(money(q.peakEquity), FG)
                                        }
                                    }
                                    Spacer(Modifier.height(8.dp))
                                    val openPl = q.sessionOpenPl ?: 0.0
                                    Text(
                                        "Session P/L: ${money(openPl, withSign = true)}   " +
                                        "Peak: ${money(q.sessionPeakPl ?: 0.0, withSign = true)}",
                                        color = signColor(openPl),
                                        style = MaterialTheme.typography.labelMedium,
                                    )
                                    Spacer(Modifier.height(6.dp))
                                    Text(
                                        "QC refresh: ${q.lastQcRefresh?.take(19) ?: "—"}",
                                        color = FG2,
                                        style = MaterialTheme.typography.labelSmall,
                                    )
                                    Text(
                                        "Last rebalance: ${q.lastRebalance?.take(19) ?: "—"}",
                                        color = FG2,
                                        style = MaterialTheme.typography.labelSmall,
                                    )
                                    if (q.ratchetFiredToday) {
                                        Spacer(Modifier.height(4.dp))
                                        Pill("RATCHET FIRED TODAY", Warn)
                                    }
                                }
                            }
                        }
                        items(q.strategies.sortedByDescending { it.weight },
                              key = { it.id ?: it.name.hashCode() }) { s -> StrategyCard(s) }
                    }
                }
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
private fun StrategyCard(s: QcStrategyDto) {
    Card(Modifier.fillMaxWidth()) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill("${"%.0f".format(s.weight * 100)}%", Accent)
                Spacer(Modifier.size(8.dp))
                Text(s.name, color = FG,
                     style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold))
            }
            Spacer(Modifier.height(4.dp))
            if (s.holdings.isNotEmpty()) {
                Text(
                    "Holding: " + s.holdings.entries.joinToString { "${it.key} ${"%.0f".format(it.value * 100)}%" },
                    color = FG, style = MaterialTheme.typography.labelMedium,
                )
                Spacer(Modifier.height(2.dp))
            }
            Text(
                "Universe: " + s.universe.joinToString(", "),
                color = FG2, style = MaterialTheme.typography.labelSmall,
            )
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                s.cagr5y?.let {
                    Column {
                        StatLabel("CAGR 5Y")
                        StatValue("${"%.1f".format(it)}%", FG)
                    }
                }
                s.sharpe1y?.let {
                    Column {
                        StatLabel("SHARPE 1Y")
                        StatValue("%.2f".format(it), FG)
                    }
                }
                s.return3m?.let {
                    Column {
                        StatLabel("3M RET")
                        StatValue("${"%.1f".format(it)}%", signColor(it))
                    }
                }
                s.drawdown5y?.let {
                    Column {
                        StatLabel("5Y DD")
                        StatValue("${"%.0f".format(it)}%", Warn)
                    }
                }
            }
        }
    }
}
