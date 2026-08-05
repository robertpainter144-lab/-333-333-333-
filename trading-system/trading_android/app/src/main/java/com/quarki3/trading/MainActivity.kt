package com.quarki3.trading

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.lifecycleScope
import com.quarki3.trading.data.SettingsRepo
import com.quarki3.trading.data.TradingRepo
import com.quarki3.trading.ui.AppShell
import com.quarki3.trading.ui.theme.TradingTheme
import com.quarki3.trading.updater.UpdateChecker
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val app = applicationContext
        val settings = SettingsRepo(app)
        val repo = TradingRepo(BuildConfig.DEFAULT_BACKEND)

        val factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T =
                TradingViewModel(repo, settings) as T
        }

        setContent {
            TradingTheme {
                val vm: TradingViewModel = viewModel(factory = factory)
                AppShell(vm)
            }
        }

        lifecycleScope.launch {
            val backend = settings.backendUrl.first()
            val available = UpdateChecker.check(backend) ?: return@launch
            runCatching { UpdateChecker.downloadAndInstall(this@MainActivity, available) }
        }
    }
}
