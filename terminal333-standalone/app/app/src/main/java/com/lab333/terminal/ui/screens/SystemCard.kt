package com.lab333.terminal.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.lab333.terminal.ui.BlinkingDot
import com.lab333.terminal.ui.Dot
import com.lab333.terminal.ui.theme.BDR
import com.lab333.terminal.ui.theme.BG2
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.FG2
import com.lab333.terminal.ui.theme.Good
import com.lab333.terminal.ui.theme.Warn

/**
 * Shared visual grammar for research systems in the Lab.
 * Every experiment gets rendered with the same state pill / title / subtitle
 * shape so a glance at the Lab reads like a mission-control dashboard.
 */
enum class SystemState(val label: String, val color: Color, val blink: Boolean) {
    Idle      ("IDLE",      FG2,   false),
    Standby   ("STANDBY",   Warn,  false),
    Emitting  ("EMITTING",  Good,  true),
    Triggered ("TRIGGERED", Good,  true),
    Blocked   ("BLOCKED",   Warn,  false),
    Error     ("ERROR",     Color(0xFFF85149), false),
    Loading   ("…",         FG2,   false),
}

/**
 * A research-system card. Tap to deep-dive into the underlying screen.
 * Consumers/emitters render as small mono-labels so the reader can see
 * which systems are talking to which without opening anything.
 */
@Composable
fun SystemCard(
    title: String,
    accent: Color,
    state: SystemState,
    summary: String,
    blurb: String? = null,    // plain-language "what it does" dossier line
    consumes: List<String> = emptyList(),
    emits: List<String> = emptyList(),
    pnlPct: Double? = null,   // optional right-aligned P&L badge
    startedTs: Long? = null,  // unix seconds; renders as "since Jul 5" under P&L
    onClick: () -> Unit,
) {
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(BG2)
            .border(1.dp, if (state.blink) accent.copy(alpha = 0.6f) else BDR, RoundedCornerShape(12.dp))
            .clickable(onClick = onClick)
            .padding(14.dp),
    ) {
        Column {
            Row(
                Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    title,
                    color = accent,
                    style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                    maxLines = 1,
                    overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f, fill = false),
                )
                Spacer(Modifier.size(8.dp))
                if (state.blink) BlinkingDot(state.color) else Dot(state.color)
                Spacer(Modifier.size(6.dp))
                Text(
                    state.label,
                    color = state.color,
                    style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                    maxLines = 1,
                )
                // Filler pushes P&L block all the way to the right without
                // fighting the title's weight-based sizing.
                Spacer(Modifier.weight(1f))
                if (pnlPct != null) {
                    val col = if (pnlPct > 0) com.lab333.terminal.ui.theme.Good
                              else if (pnlPct < 0) com.lab333.terminal.ui.theme.Bad
                              else FG2
                    androidx.compose.foundation.layout.Column(
                        horizontalAlignment = androidx.compose.ui.Alignment.End,
                        modifier = Modifier.padding(end = 8.dp),
                    ) {
                                androidx.compose.foundation.layout.Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                                    Text(
                                        "P&L",
                                        color = FG2,
                                        style = MaterialTheme.typography.labelSmall,
                                        modifier = Modifier.padding(end = 4.dp),
                                    )
                                    Text(
                                        String.format("%+.2f%%", pnlPct),
                                        color = col,
                                        style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold),
                                    )
                                }
                                if (startedTs != null && startedTs > 0) {
                                    val nowS = System.currentTimeMillis() / 1000L
                                    val ageDays = ((nowS - startedTs) / 86400.0)
                                    val ageStr = when {
                                        ageDays < 1 -> "${((nowS - startedTs) / 3600).toInt()}h"
                                        ageDays < 60 -> "${ageDays.toInt()}d"
                                        else -> "${(ageDays / 30).toInt()}mo"
                                    }
                                    val startedDate = java.text.SimpleDateFormat("MMM d")
                                        .format(java.util.Date(startedTs * 1000))
                                    Text(
                                        "since $startedDate ($ageStr)",
                                        color = FG2,
                                        style = MaterialTheme.typography.labelSmall,
                                    )
                                }
                            }
                }
                Text("›", color = FG2, style = MaterialTheme.typography.titleMedium)
            }
            Spacer(Modifier.height(6.dp))
            Text(
                summary,
                color = FG,
                style = MaterialTheme.typography.bodySmall,
            )
            if (blurb != null) {
                Spacer(Modifier.height(4.dp))
                Text(
                    blurb,
                    color = FG2,
                    style = MaterialTheme.typography.labelSmall,
                )
            }
            if (consumes.isNotEmpty() || emits.isNotEmpty()) {
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    if (consumes.isNotEmpty()) BusLabel("in", consumes)
                    if (emits.isNotEmpty())    BusLabel("out", emits)
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(Modifier.fillMaxWidth()) {
                Spacer(Modifier.weight(1f))
                Text(
                    "tap for details ›",
                    color = accent,
                    style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
                )
            }
        }
    }
}

@Composable
private fun BusLabel(direction: String, items: List<String>) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            "$direction ▸ ",
            color = FG2,
            style = MaterialTheme.typography.labelSmall,
        )
        Text(
            items.joinToString(" · "),
            color = FG2,
            style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
        )
    }
}
