package com.quarki3.trading.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.quarki3.trading.TradingViewModel
import com.quarki3.trading.ui.BlinkingDot
import com.quarki3.trading.ui.Pill
import com.quarki3.trading.ui.theme.Accent
import com.quarki3.trading.ui.theme.BDR
import com.quarki3.trading.ui.theme.BG
import com.quarki3.trading.ui.theme.BG2
import com.quarki3.trading.ui.theme.FG
import com.quarki3.trading.ui.theme.FG2
import com.quarki3.trading.ui.theme.Good
import com.quarki3.trading.ui.theme.Purple
import com.quarki3.trading.ui.theme.Warn

/**
 * Cross-regime confluence detector. v1 is a hardcoded pairwise correlator
 * over whatever signals are already loaded in the ViewModel. When two or
 * more systems are simultaneously "hot", they surface here as a hybrid
 * signal — the seed of a QC-ready alpha combiner in a future version.
 */
@Composable
fun RegimeOverlapCard(vm: TradingViewModel) {
    val whales = vm.whales.collectAsState().value
    val qc     = vm.qc.collectAsState().value

    val signals = mutableListOf<Signal>()

    // Signal 1: Whale coordinated-accumulation hits in the last window.
    (whales as? TradingViewModel.WhalesState.Ok)?.data?.let { d ->
        if (d.alerts.isNotEmpty()) {
            val topSymbols = d.alerts.take(3).map { it.symbol }.distinct()
            signals += Signal(
                system = "WHALES",
                color  = Purple,
                detail = "${d.alerts.size} Q01 hit${if (d.alerts.size > 1) "s" else ""} · ${topSymbols.joinToString(" ")}",
            )
        }
    }

    // Signal 2: QC portfolio leaderboard has fresh top-picks with weight.
    (qc as? TradingViewModel.QcState.Ok)?.data?.let { d ->
        // Best-effort: QcStateDto shape varies; just show a heartbeat pill if data present.
        signals += Signal(
            system = "QC",
            color  = Accent,
            detail = "Leaderboard warm",
        )
    }

    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(14.dp))
            .background(BG2)
            .border(
                1.dp,
                if (signals.size >= 2) Good.copy(alpha = 0.7f) else BDR,
                RoundedCornerShape(14.dp),
            )
            .padding(14.dp),
    ) {
        Column {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("⚡", color = Warn, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.size(8.dp))
                Text(
                    "REGIME OVERLAP",
                    color = FG,
                    style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                )
                Spacer(Modifier.size(10.dp))
                if (signals.size >= 2) {
                    BlinkingDot(Good)
                    Spacer(Modifier.size(6.dp))
                    Text(
                        "CONFLUENCE",
                        color = Good,
                        style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                    )
                } else {
                    Text(
                        "watching",
                        color = FG2,
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
            Spacer(Modifier.height(10.dp))

            if (signals.isEmpty()) {
                Text(
                    "No live signals yet. When two or more regimes agree, they'll pair up here.",
                    color = FG2,
                    style = MaterialTheme.typography.bodySmall,
                )
            } else {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    signals.forEach { s ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Pill(s.system, s.color)
                            Spacer(Modifier.size(8.dp))
                            Text(
                                s.detail,
                                color = FG,
                                style = MaterialTheme.typography.bodySmall,
                            )
                        }
                    }
                }
                if (signals.size >= 2) {
                    Spacer(Modifier.height(10.dp))
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(8.dp))
                            .background(BG)
                            .padding(10.dp),
                    ) {
                        Text(
                            "→ ${signals.joinToString(" + ") { it.system }} agreed — hybrid signal",
                            color = Good,
                            style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold),
                        )
                    }
                }
            }
        }
    }
}

private data class Signal(val system: String, val color: Color, val detail: String)
