package com.quarki3.trading.ui.screens

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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.quarki3.trading.TradingViewModel
import com.quarki3.trading.data.StrategyDto
import com.quarki3.trading.ui.Card
import com.quarki3.trading.ui.Pill
import com.quarki3.trading.ui.money
import com.quarki3.trading.ui.signColor
import com.quarki3.trading.ui.theme.Accent
import com.quarki3.trading.ui.theme.BG
import com.quarki3.trading.ui.theme.BG2
import com.quarki3.trading.ui.theme.FG
import com.quarki3.trading.ui.theme.FG2
import com.quarki3.trading.ui.theme.Good
import com.quarki3.trading.ui.theme.Warn

@Composable
fun StrategiesScreen(vm: TradingViewModel) {
    val state = vm.strategies.collectAsState().value
    LaunchedEffect(Unit) { vm.refreshStrategies() }
    var showAdd by remember { mutableStateOf(false) }

    Column(Modifier.fillMaxSize().background(BG)) {
        Row(
            Modifier.fillMaxWidth().background(BG2).padding(horizontal = 16.dp, vertical = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Strategies", color = Accent, style = MaterialTheme.typography.titleMedium)
            Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                TextButton(onClick = { showAdd = true }) { Text("+ Add") }
            }
        }

        when (state) {
            is TradingViewModel.StrategiesState.Loading ->
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("Loading…", color = FG2)
                }
            is TradingViewModel.StrategiesState.Err ->
                ErrorBanner(state.msg) { vm.refreshStrategies() }
            is TradingViewModel.StrategiesState.Ok -> {
                if (state.data.strategies.isEmpty()) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Text("No strategies running.", color = FG2)
                            Spacer(Modifier.height(10.dp))
                            TextButton(onClick = { showAdd = true }) {
                                Text("Paste or pull from QuantConnect")
                            }
                        }
                    }
                } else {
                    LazyColumn(
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(
                            start = 12.dp, end = 12.dp, top = 10.dp, bottom = 80.dp,
                        ),
                    ) {
                        items(state.data.strategies, key = { it.name }) { s -> StrategyCard(vm, s) }
                    }
                }
            }
        }
    }

    if (showAdd) {
        AddStrategyDialog(vm, onClose = { showAdd = false })
    }
}

@Composable
private fun StrategyCard(vm: TradingViewModel, s: StrategyDto) {
    var alloc by remember(s.allocationPct) { mutableStateOf(s.allocationPct.toFloat()) }
    var showDelete by remember { mutableStateOf(false) }
    Card(Modifier.fillMaxWidth()) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Pill(if (s.broker == "alpaca_paper") "PAPER" else "REAL",
                     if (s.broker == "alpaca_paper") Accent else Warn)
                Spacer(Modifier.size(6.dp))
                Text(s.name, color = FG,
                     style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold))
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                    Switch(
                        checked = s.enabled,
                        onCheckedChange = { vm.updateStrategy(s.name, enabled = it) },
                        colors = SwitchDefaults.colors(
                            checkedThumbColor = Good, uncheckedThumbColor = FG2,
                        ),
                    )
                }
            }
            Spacer(Modifier.height(2.dp))
            Text("source: ${s.source}" + (s.qcProjectId?.let { "  •  QC #$it" } ?: ""),
                 color = FG2, style = MaterialTheme.typography.labelSmall)
            Spacer(Modifier.height(6.dp))
            Text("Allocation: ${"%.0f".format(alloc)}%",
                 color = FG, style = MaterialTheme.typography.labelMedium)
            Slider(
                value = alloc,
                onValueChange = { alloc = it },
                onValueChangeFinished = { vm.updateStrategy(s.name, allocationPct = alloc.toDouble()) },
                valueRange = 0f..100f, steps = 99,
                colors = SliderDefaults.colors(
                    thumbColor = Accent, activeTrackColor = Accent, inactiveTrackColor = FG2.copy(alpha = 0.3f),
                ),
            )
            Spacer(Modifier.height(4.dp))
            Row {
                Text("Equity: ${money(s.lastEquity)}", color = FG2,
                     style = MaterialTheme.typography.labelMedium)
                Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.CenterEnd) {
                    TextButton(onClick = { showDelete = true }) {
                        Text("Delete", color = Warn)
                    }
                }
            }
        }
    }
    if (showDelete) {
        AlertDialog(
            onDismissRequest = { showDelete = false },
            containerColor = BG2,
            title = { Text("Delete '${s.name}'?", color = FG) },
            text = { Text("Strategy positions will not be liquidated; stop the strategy first if needed.", color = FG2) },
            confirmButton = {
                TextButton(onClick = {
                    vm.deleteStrategy(s.name); showDelete = false
                }) { Text("Delete", color = Warn) }
            },
            dismissButton = {
                TextButton(onClick = { showDelete = false }) { Text("Cancel") }
            },
        )
    }
}

@Composable
private fun AddStrategyDialog(vm: TradingViewModel, onClose: () -> Unit) {
    var name by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var broker by remember { mutableStateOf("alpaca_paper") }
    var alloc by remember { mutableStateOf(10f) }
    val qcProjects = vm.qcLabProjects.collectAsState().value
    var loadedFromQc by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) { vm.loadQcLabProjects() }

    AlertDialog(
        onDismissRequest = onClose,
        containerColor = BG2,
        title = { Text("Add Strategy", color = FG) },
        text = {
            Column {
                if (qcProjects.isNotEmpty() && !loadedFromQc) {
                    Text("Pull from QuantConnect", color = FG,
                         style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
                    Spacer(Modifier.height(4.dp))
                    LazyColumn(Modifier.height(120.dp)) {
                        items(qcProjects) { p ->
                            Row(Modifier.fillMaxWidth().padding(vertical = 2.dp).clickable {
                                name = p.name.replace("[^A-Za-z0-9_-]".toRegex(), "_")
                                code = "// loading from QC project ${p.projectId}…"
                                loadedFromQc = true
                            }) {
                                Text("• ${p.name}", color = FG2,
                                     style = MaterialTheme.typography.labelMedium)
                            }
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                }
                OutlinedTextField(
                    value = name, onValueChange = { name = it.filter { c -> c.isLetterOrDigit() || c == '_' || c == '-' } },
                    label = { Text("Strategy name") }, singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(6.dp))
                OutlinedTextField(
                    value = code, onValueChange = { code = it },
                    label = { Text("Paste strategy code (Python / QC)") },
                    minLines = 4, maxLines = 10,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
                Text("Broker", color = FG, style = MaterialTheme.typography.labelMedium)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    TextButton(onClick = { broker = "alpaca_paper" }) {
                        Text("Alpaca Paper", color = if (broker == "alpaca_paper") Accent else FG2)
                    }
                    TextButton(onClick = { broker = "ibkr_live" }) {
                        Text("IBKR Live", color = if (broker == "ibkr_live") Warn else FG2)
                    }
                }
                Spacer(Modifier.height(6.dp))
                Text("Allocation: ${"%.0f".format(alloc)}%",
                     color = FG, style = MaterialTheme.typography.labelMedium)
                Slider(
                    value = alloc, onValueChange = { alloc = it },
                    valueRange = 1f..100f, steps = 98,
                    colors = SliderDefaults.colors(
                        thumbColor = Accent, activeTrackColor = Accent, inactiveTrackColor = FG2.copy(alpha = 0.3f),
                    ),
                )
            }
        },
        confirmButton = {
            TextButton(onClick = {
                if (name.isNotBlank() && code.isNotBlank()) {
                    vm.createStrategy(name, code, broker, alloc.toDouble()) { onClose() }
                }
            }) { Text("Deploy") }
        },
        dismissButton = { TextButton(onClick = onClose) { Text("Cancel") } },
    )
}
