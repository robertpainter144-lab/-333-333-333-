package com.lab333.terminal.ui

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
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.lab333.terminal.TradingViewModel
import com.lab333.terminal.ui.screens.PortfolioScreen
import com.lab333.terminal.ui.theme.Accent
import com.lab333.terminal.ui.theme.BG
import com.lab333.terminal.ui.theme.BG2
import com.lab333.terminal.ui.theme.FG
import com.lab333.terminal.ui.theme.FG2
import com.lab333.terminal.ui.theme.Good

/**
 * The Live Realm: boring on purpose. Just money.
 * Segmented control at the top switches broker sub-page.
 * Alpaca is default (per user's stated preference for real money home).
 */
@Composable
fun BookScreen(vm: TradingViewModel) {
    var broker by rememberSaveable { mutableStateOf("alpaca") }

    Column(Modifier.fillMaxSize().background(BG)) {
        Header()
        Spacer(Modifier.height(8.dp))
        SegmentedTabs(
            items = listOf(
                "alpaca" to "ALPACA · live",
                "ibkr"   to "IBKR · live",
            ),
            selected = broker,
            onSelect = { broker = it },
            accent = { id -> if (id == "alpaca") Good else Accent },
        )
        Spacer(Modifier.height(6.dp))
        Box(Modifier.fillMaxSize()) {
            when (broker) {
                "alpaca" -> PortfolioScreen(vm, pinnedSource = "alpaca_live")
                "ibkr"   -> PortfolioScreen(vm, pinnedSource = "ibkr")
            }
        }
    }
}

@Composable
private fun Header() {
    Row(
        Modifier.fillMaxWidth().background(BG2).padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("💵", color = Good, style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.size(8.dp))
        Text(
            "THE BOOK",
            color = FG,
            style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
        )
        Spacer(Modifier.size(10.dp))
        Text(
            "Live Realm · real capital, real P&L",
            color = FG2,
            style = MaterialTheme.typography.labelSmall,
        )
    }
}
