package com.lab333.terminal

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.lifecycleScope
import com.lab333.terminal.data.SettingsRepo
import com.lab333.terminal.data.TradingRepo
import com.lab333.terminal.ui.AppShell
import com.lab333.terminal.ui.theme.TradingTheme
import com.lab333.terminal.updater.UpdateChecker
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()

        val app = applicationContext
        val settings = SettingsRepo(app)
        val repo = TradingRepo(BuildConfig.DEFAULT_BACKEND, BuildConfig.DEFAULT_API_KEY)

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
