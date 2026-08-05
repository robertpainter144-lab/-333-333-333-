package com.quarki3.trading.ui

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.AccountBalanceWallet
import androidx.compose.material.icons.outlined.Leaderboard
import androidx.compose.material.icons.outlined.Science
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import com.quarki3.trading.TradingViewModel
import com.quarki3.trading.ui.screens.CatalogScreen
import com.quarki3.trading.ui.theme.Accent
import com.quarki3.trading.ui.theme.BG
import com.quarki3.trading.ui.theme.BG2
import com.quarki3.trading.ui.theme.FG2

enum class Realm(val label: String, val icon: ImageVector) {
    Book("Book", Icons.Outlined.AccountBalanceWallet), // Live money — default home
    Lab ("Lab",  Icons.Outlined.Science),              // Paper research
    Catalog("Catalog", Icons.Outlined.Leaderboard),    // Private strategy-catalog benchmarks
}

@Composable
fun AppShell(vm: TradingViewModel) {
    var current by rememberSaveable { mutableStateOf(Realm.Book) }

    Scaffold(
        containerColor = BG,
        contentColor = MaterialTheme.colorScheme.onBackground,
        bottomBar = {
            NavigationBar(
                containerColor = BG2,
                contentColor = FG2,
                tonalElevation = 0.dp,
            ) {
                Realm.entries.forEach { realm ->
                    val selected = realm == current
                    NavigationBarItem(
                        selected = selected,
                        onClick = { current = realm },
                        icon = { Icon(realm.icon, contentDescription = realm.label) },
                        label = {
                            Text(
                                realm.label,
                                style = MaterialTheme.typography.labelMedium,
                                maxLines = 1,
                                softWrap = false,
                            )
                        },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = Accent,
                            selectedTextColor = Accent,
                            indicatorColor = BG,
                            unselectedIconColor = FG2,
                            unselectedTextColor = FG2,
                        ),
                    )
                }
            }
        },
    ) { inner ->
        Box(
            Modifier
                .fillMaxSize()
                .padding(inner)
                .background(BG),
        ) {
            AnimatedContent(
                targetState = current,
                transitionSpec = {
                    val goingRight = targetState.ordinal > initialState.ordinal
                    val dir = if (goingRight) 1 else -1
                    (slideInHorizontally(tween(220)) { w -> dir * w } + fadeIn(tween(220)))
                        .togetherWith(
                            slideOutHorizontally(tween(220)) { w -> -dir * w } + fadeOut(tween(220))
                        )
                },
                label = "realm-switch",
            ) { realm ->
                when (realm) {
                    Realm.Book    -> BookScreen(vm)
                    Realm.Lab     -> LabScreen(vm)
                    Realm.Catalog -> CatalogScreen(vm)
                }
            }
        }
    }
}
