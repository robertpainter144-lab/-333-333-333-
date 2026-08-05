package com.quarki3.trading.ui

import androidx.activity.compose.BackHandler
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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.quarki3.trading.TradingViewModel
import com.quarki3.trading.ui.screens.ActivityScreen
import com.quarki3.trading.ui.screens.BotsScreen
import com.quarki3.trading.ui.screens.ChartsScreen
import com.quarki3.trading.ui.screens.InsidersScreen
import com.quarki3.trading.ui.screens.PatentCliffScreen
import com.quarki3.trading.ui.screens.QcScreen
import com.quarki3.trading.ui.screens.RegimeOverlapCard
import com.quarki3.trading.ui.screens.StrategiesScreen
import com.quarki3.trading.ui.screens.SystemCard
import com.quarki3.trading.ui.screens.SystemState
import com.quarki3.trading.ui.screens.WhalesScreen
import com.quarki3.trading.ui.theme.Accent
import com.quarki3.trading.ui.theme.BG
import com.quarki3.trading.ui.theme.BG2
import com.quarki3.trading.ui.theme.FG
import com.quarki3.trading.ui.theme.FG2
import com.quarki3.trading.ui.theme.Good
import com.quarki3.trading.ui.theme.Purple
import com.quarki3.trading.ui.theme.Warn

private enum class LabFocus {
    None, Whales, Qc, Patent, Strategies, Bots, Insiders, Activity, Charts, PropDesk
}

/**
 * The Paper Realm: a mission-control bench. RegimeOverlapCard at the top
 * watches for confluence, then each system renders as a SystemCard that
 * taps through to its full screen.
 */
@Composable
fun LabScreen(vm: TradingViewModel) {
    var focus by rememberSaveable { mutableStateOf(LabFocus.None) }

    // Kick off refreshes so state pills reflect live data.
    LaunchedEffect(Unit) {
        vm.refreshWhales()
        vm.refreshQc()
        vm.refreshStrategies()
        vm.refreshPatentCliff()
        vm.refreshInsider()
        vm.refreshPropDesk()
        vm.refreshMarketPulse()
        while (true) {
            kotlinx.coroutines.delay(10000)
            vm.refreshPropDesk()
            vm.refreshWhales()
            vm.refreshMarketPulse()
        }
    }

    if (focus != LabFocus.None) {
        // Intercept the Android hardware/gesture back so it returns to Lab home
        // instead of exiting the app.
        BackHandler(enabled = true) { focus = LabFocus.None }
        Column(Modifier.fillMaxSize().background(BG)) {
            BackBar(onBack = { focus = LabFocus.None })
            Box(Modifier.fillMaxSize()) {
                when (focus) {
                    LabFocus.Whales     -> WhalesScreen(vm)
                    LabFocus.PropDesk   -> WhalesScreen(vm)   // reuses the rich block
                    LabFocus.Qc         -> QcScreen(vm)
                    LabFocus.Patent     -> PatentCliffScreen(vm)
                    LabFocus.Strategies -> StrategiesScreen(vm)
                    LabFocus.Bots       -> BotsScreen(vm)
                    LabFocus.Insiders   -> InsidersScreen(vm)
                    LabFocus.Activity   -> ActivityScreen(vm)
                    LabFocus.Charts     -> ChartsScreen(vm)
                    LabFocus.None       -> Unit
                }
            }
        }
        return
    }

    val whales   = vm.whales.collectAsState().value
    val qc       = vm.qc.collectAsState().value
    val strat    = vm.strategies.collectAsState().value
    val patent   = vm.patentCliff.collectAsState().value
    val ins      = vm.insider.collectAsState().value
    val propDesk = vm.propDesk.collectAsState().value
    val market   = vm.marketPulse.collectAsState().value

    LazyColumn(
        Modifier.fillMaxSize().background(BG),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(
            start = 12.dp, end = 12.dp, top = 12.dp, bottom = 96.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Header(subtitle = "Paper Realm · every idea, no capital at risk")
        }
        item { RegimeOverlapCard(vm) }
        item { Spacer(Modifier.height(4.dp)) }

        item {
            val (pdState, pdSub) = propDesk.pillFor()
            val pdData = (propDesk as? TradingViewModel.PropDeskState.Ok)?.data
            val pdPnl = pdData?.ledger?.totalPnl
            val pdStart = pdData?.counters?.startedTs
            SystemCard(
                title = "⚡ PROP DESK BENCH",
                blurb = "Three paper strategies — momentum, mean-reversion, breakout — vol-normalized, cluster-capped. Auto-tune promotes only arms that prove n≥30 trades with positive expectancy.",
                accent = Accent,
                state = pdState,
                summary = pdSub,
                consumes = listOf("CoinGecko", "Alpaca-paper"),
                emits = listOf("Signals", "Trades"),
                pnlPct = pdPnl,
                startedTs = pdStart,
                onClick = { focus = LabFocus.PropDesk },
            )
        }

        item {
            val (st, sub) = whales.pillFor()
            val whaleData = (whales as? TradingViewModel.WhalesState.Ok)?.data
            SystemCard(
                title = "🐳 WHALE TRACKER",
                blurb = "Watches tracked wallets for coordinated on-chain accumulation and turns confluence into buy signals.",
                accent = Purple,
                state = st,
                summary = sub,
                consumes = listOf("Dune", "Etherscan"),
                emits = listOf("Alpaca"),
                pnlPct = whaleData?.pnlPct,
                startedTs = whaleData?.startedTs,
                onClick = { focus = LabFocus.Whales },
            )
        }

        item {
            val qcData = (qc as? TradingViewModel.QcState.Ok)?.data
            val qcPnl = qcData?.let { d ->
                if (d.initialEquity > 0 && d.peakEquity > 0)
                    (d.peakEquity - d.initialEquity) / d.initialEquity * 100.0
                else null
            }
            SystemCard(
                title = "🧪 QC ENGINE",
                blurb = "Top-5 QC leaderboard blend with 2× winner weighting, rebalanced daily — the brain whose targets the live mirror follows.",
                accent = Accent,
                state = qc.simplePill(),
                summary = when (qc) {
                    is TradingViewModel.QcState.Loading -> "Loading…"
                    is TradingViewModel.QcState.Err     -> "Backend error."
                    is TradingViewModel.QcState.Ok      -> "5 QuantConnect strategies, \$50K notional backtest. Signal source — the \$50 real mirror lives in Book."
                },
                consumes = listOf("QuantConnect"),
                emits = listOf("Target weights"),
                pnlPct = qcPnl,
                startedTs = qcData?.qcStartedTs,
                onClick = { focus = LabFocus.Qc },
            )
        }

        item {
            val stratData = (strat as? TradingViewModel.StrategiesState.Ok)?.data
            SystemCard(
                title = "⚗️ STRATEGY LAB",
                blurb = "Where ideas earn a ledger entry: SOXL 5-condition regime gate, ES mean-rev research, parameter sweeps.",
                accent = Good,
                state = strat.simplePill(),
                summary = when (strat) {
                    is TradingViewModel.StrategiesState.Loading -> "Loading…"
                    is TradingViewModel.StrategiesState.Err     -> "Backend error."
                    is TradingViewModel.StrategiesState.Ok      -> "Backtests, sweeps, SOXL regime, ES mean-rev."
                },
                emits = listOf("Signals"),
                pnlPct = stratData?.aggregatePnlPct,
                startedTs = stratData?.startedTs,
                onClick = { focus = LabFocus.Strategies },
            )
        }

        item {
            val botsData = (propDesk as? TradingViewModel.PropDeskState.Ok)?.data
            val botsSub = if (botsData != null)
                "Prop-desk: ${botsData.ledger.nTrades} closed trades. A/B arm + PoC10 mock ledgers."
            else "A/B arm, PoC10, mock ledgers."
            SystemCard(
                title = "🤖 PAPER BOTS",
                blurb = "A/B arm (mirror vs broad universe) + PoC10 reliability loop, grinding out closed-trade promotion ledgers.",
                accent = Warn,
                state = SystemState.Emitting,
                summary = botsSub,
                emits = listOf("Ledger"),
                pnlPct = botsData?.ledger?.totalPnl,
                startedTs = botsData?.counters?.startedTs,
                onClick = { focus = LabFocus.Bots },
            )
        }

        item {
            val patentData = (patent as? TradingViewModel.PatentCliffState.Ok)?.data
            SystemCard(
                title = "💊 PATENT CLIFF",
                blurb = "Drugs losing patent exclusivity → exposed brand names → generic-maker beneficiaries, ranked from FDA Orange Book data.",
                accent = Accent,
                state = patent.simplePill(),
                summary = when (patent) {
                    is TradingViewModel.PatentCliffState.Loading -> "Loading…"
                    is TradingViewModel.PatentCliffState.Err     -> "Backend error."
                    is TradingViewModel.PatentCliffState.Ok      -> "FDA Orange Book LOE ranker + mirror of top-3 picks ($2 each)."
                },
                consumes = listOf("FDA"),
                pnlPct = patentData?.pnlPct,
                startedTs = patentData?.startedTs,
                onClick = { focus = LabFocus.Patent },
            )
        }

        item {
            val insData = (ins as? TradingViewModel.InsiderState.Ok)?.data
            SystemCard(
                title = "👁️ INSIDERS",
                blurb = "Clustered SEC Form-4 buys, quality-filtered by officer seniority and size, with a 201-config backtest leaderboard.",
                accent = Purple,
                state = ins.simplePill(),
                summary = when (ins) {
                    is TradingViewModel.InsiderState.Loading -> "Loading…"
                    is TradingViewModel.InsiderState.Err     -> "Backend error."
                    is TradingViewModel.InsiderState.Ok      -> "Insider trading feed + leaderboard."
                },
                consumes = listOf("SEC"),
                pnlPct = insData?.pnlPct,
                startedTs = insData?.startedTs,
                onClick = { focus = LabFocus.Insiders },
            )
        }

        item {
            SystemCard(
                title = "📈 CHARTS",
                blurb = "SPY market pulse plus any-symbol lookup with mini chart.",
                accent = FG,
                state = SystemState.Emitting,
                summary = "Market pulse: SPY intraday. Also: any-symbol lookup.",
                pnlPct = market?.pnlPct,
                startedTs = market?.startedTs,
                onClick = { focus = LabFocus.Charts },
            )
        }

        item {
            // Activity = aggregate P/L across every trading system in the Lab
            val propDeskPnl = (propDesk as? TradingViewModel.PropDeskState.Ok)?.data?.ledger?.totalPnl
            val qcMirrorPnl = (qc as? TradingViewModel.QcState.Ok)?.data?.let { d ->
                if (d.initialEquity > 0 && d.peakEquity > 0)
                    (d.peakEquity - d.initialEquity) / d.initialEquity * 100.0
                else null
            }
            val whalePnl = (whales as? TradingViewModel.WhalesState.Ok)?.data?.pnlPct
            val insPnl   = (ins as? TradingViewModel.InsiderState.Ok)?.data?.pnlPct
            val stratP   = (strat as? TradingViewModel.StrategiesState.Ok)?.data?.aggregatePnlPct
            val patentP  = (patent as? TradingViewModel.PatentCliffState.Ok)?.data?.pnlPct
            val components = listOfNotNull(propDeskPnl, qcMirrorPnl, whalePnl, insPnl, stratP, patentP)
            val activityPnl = if (components.isNotEmpty())
                components.average() else null
            // Earliest start across all Lab systems that report one
            val allStarts = listOfNotNull(
                (propDesk as? TradingViewModel.PropDeskState.Ok)?.data?.counters?.startedTs,
                (qc as? TradingViewModel.QcState.Ok)?.data?.qcStartedTs,
                (whales as? TradingViewModel.WhalesState.Ok)?.data?.startedTs,
                (ins as? TradingViewModel.InsiderState.Ok)?.data?.startedTs,
                (strat as? TradingViewModel.StrategiesState.Ok)?.data?.startedTs,
                (patent as? TradingViewModel.PatentCliffState.Ok)?.data?.startedTs,
            ).filter { it > 0 }
            val activityStart = allStarts.minOrNull()
            SystemCard(
                title = "🕒 ACTIVITY",
                blurb = "Cross-source tape of every paper fill; P/L averages all Lab systems for one honest number.",
                accent = FG,
                state = SystemState.Emitting,
                summary = "Cross-source tape.  P/L = avg across all Lab systems.",
                pnlPct = activityPnl,
                startedTs = activityStart,
                onClick = { focus = LabFocus.Activity },
            )
        }
    }
}

@Composable
private fun Header(subtitle: String) {
    Column(Modifier.fillMaxWidth().padding(bottom = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("🧪", color = Purple, style = MaterialTheme.typography.titleMedium)
            Spacer(Modifier.size(8.dp))
            Text(
                "THE LAB",
                color = FG,
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
            )
        }
        Text(subtitle, color = FG2, style = MaterialTheme.typography.labelSmall)
    }
}

@Composable
private fun BackBar(onBack: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().background(BG2).padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextButton(onClick = onBack) {
            Text("‹  Back to Lab", color = Accent, style = MaterialTheme.typography.labelMedium)
        }
    }
}

// ── Per-state helpers ─────────────────────────────────────────────────
// Any card whose backend is responding with data is "Emitting" — the user's
// mental model is "the system is doing work," which is true as long as we
// have live data.
private fun TradingViewModel.WhalesState.pillFor(): Pair<SystemState, String> = when (this) {
    is TradingViewModel.WhalesState.Loading -> SystemState.Loading to "Loading whale feed…"
    is TradingViewModel.WhalesState.Err     -> SystemState.Error   to "Backend error."
    is TradingViewModel.WhalesState.Ok      -> {
        val n = data.alerts.size
        SystemState.Emitting to
            if (n > 0) "$n live signals · ${data.seenCount} lifetime hits."
            else       "Watching. ${data.seenCount} lifetime hits."
    }
}

private fun TradingViewModel.PropDeskState.pillFor(): Pair<SystemState, String> = when (this) {
    is TradingViewModel.PropDeskState.Loading -> SystemState.Loading to "Loading prop-desk…"
    is TradingViewModel.PropDeskState.Err     -> SystemState.Error   to "Backend error."
    is TradingViewModel.PropDeskState.Ok      -> {
        val d = data
        val heldN  = d.held.size
        val decs   = d.decisions.size
        val enabled = d.autoTune.enabledStrategies.size
        val cumPnl = d.ledger.totalPnl
        val summary = "regime=${d.regime}  strats=$enabled active  held=$heldN  " +
                      "cum ${String.format("%+.2f", cumPnl)}%  brain=$decs decisions"
        SystemState.Emitting to summary
    }
}

private fun TradingViewModel.QcState.simplePill(): SystemState = when (this) {
    is TradingViewModel.QcState.Loading -> SystemState.Loading
    is TradingViewModel.QcState.Err     -> SystemState.Error
    is TradingViewModel.QcState.Ok      -> SystemState.Emitting
}

private fun TradingViewModel.StrategiesState.simplePill(): SystemState = when (this) {
    is TradingViewModel.StrategiesState.Loading -> SystemState.Loading
    is TradingViewModel.StrategiesState.Err     -> SystemState.Error
    is TradingViewModel.StrategiesState.Ok      -> SystemState.Emitting
}

private fun TradingViewModel.PatentCliffState.simplePill(): SystemState = when (this) {
    is TradingViewModel.PatentCliffState.Loading -> SystemState.Loading
    is TradingViewModel.PatentCliffState.Err     -> SystemState.Error
    is TradingViewModel.PatentCliffState.Ok      -> SystemState.Emitting
}

private fun TradingViewModel.InsiderState.simplePill(): SystemState = when (this) {
    is TradingViewModel.InsiderState.Loading -> SystemState.Loading
    is TradingViewModel.InsiderState.Err     -> SystemState.Error
    is TradingViewModel.InsiderState.Ok      -> SystemState.Emitting
}
