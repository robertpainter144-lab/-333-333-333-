package com.lab333.terminal.ui

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.lab333.terminal.ui.theme.Accent
import com.lab333.terminal.ui.theme.BDR
import com.lab333.terminal.ui.theme.BG2
import com.lab333.terminal.ui.theme.BG3
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.FG2

@Composable
fun Card(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Box(
        modifier
            .clip(RoundedCornerShape(10.dp))
            .background(BG2)
            .border(1.dp, BDR, RoundedCornerShape(10.dp))
            .padding(12.dp),
    ) {
        content()
    }
}

@Composable
fun Pill(text: String, color: Color = Accent, modifier: Modifier = Modifier) {
    Box(
        modifier
            .clip(RoundedCornerShape(50))
            .background(color.copy(alpha = 0.12f))
            .border(1.dp, color.copy(alpha = 0.5f), RoundedCornerShape(50))
            .padding(horizontal = 8.dp, vertical = 2.dp),
    ) {
        Text(
            text = text,
            color = color,
            style = MaterialTheme.typography.labelSmall.copy(fontWeight = FontWeight.Bold),
        )
    }
}

@Composable
fun StatLabel(text: String) {
    Text(text.uppercase(), color = FG2, style = MaterialTheme.typography.labelSmall)
}

@Composable
fun StatValue(text: String, color: Color = FG, big: Boolean = false) {
    Text(
        text,
        color = color,
        style = if (big) MaterialTheme.typography.titleLarge
                else MaterialTheme.typography.titleSmall,
    )
}

@Composable
fun SegmentedTabs(
    items: List<Pair<String, String>>,   // id, label
    selected: String,
    onSelect: (String) -> Unit,
    accent: (String) -> Color = { Accent },
) {
    LazyRow(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        items(items.size) { idx ->
            val (id, label) = items[idx]
            val isSel = id == selected
            val color = accent(id)
            val bgColor by animateColorAsState(
                if (isSel) color.copy(alpha = 0.18f) else BG3,
                label = "seg-bg-$id",
            )
            val border by animateColorAsState(
                if (isSel) color else BDR,
                label = "seg-border-$id",
            )
            val textColor by animateColorAsState(
                if (isSel) color else FG2,
                label = "seg-fg-$id",
            )
            Box(
                Modifier
                    .clip(RoundedCornerShape(50))
                    .background(bgColor)
                    .border(1.dp, border, RoundedCornerShape(50))
                    .padding(horizontal = 14.dp, vertical = 8.dp)
                    .clip(RoundedCornerShape(50))
                    .clickableNoIndication { onSelect(id) },
                contentAlignment = Alignment.Center,
            ) {
                Text(label, color = textColor, style = MaterialTheme.typography.labelMedium.copy(fontWeight = FontWeight.Bold))
            }
        }
    }
}

@Composable
fun Dot(color: Color, size: Int = 8) {
    Box(
        Modifier
            .size(size.dp)
            .clip(CircleShape)
            .background(color),
    )
}

@Composable
fun BlinkingDot(color: Color, modifier: Modifier = Modifier) {
    val infinite = androidx.compose.animation.core.rememberInfiniteTransition(label = "dot")
    val alpha = infinite.animateFloat(
        initialValue = 0.35f, targetValue = 1f,
        animationSpec = androidx.compose.animation.core.infiniteRepeatable(
            animation = androidx.compose.animation.core.tween(900),
            repeatMode = androidx.compose.animation.core.RepeatMode.Reverse,
        ),
        label = "dot-alpha",
    )
    Box(
        modifier
            .size(8.dp)
            .clip(CircleShape)
            .background(color.copy(alpha = alpha.value)),
    )
}

@Composable
fun SectionTitle(text: String) {
    Text(
        text.uppercase(),
        color = FG2,
        style = MaterialTheme.typography.labelMedium.copy(letterSpacing = androidx.compose.ui.unit.TextUnit(1.2f, androidx.compose.ui.unit.TextUnitType.Sp)),
        modifier = Modifier.padding(start = 16.dp, top = 12.dp, bottom = 6.dp),
    )
}

/** Click without a ripple — keeps the chip tap silent and instant. */
fun Modifier.clickableNoIndication(onClick: () -> Unit): Modifier = composed {
    val mis = androidx.compose.runtime.remember { androidx.compose.foundation.interaction.MutableInteractionSource() }
    this.clickable(
        interactionSource = mis,
        indication = null,
        onClick = onClick,
    )
}
