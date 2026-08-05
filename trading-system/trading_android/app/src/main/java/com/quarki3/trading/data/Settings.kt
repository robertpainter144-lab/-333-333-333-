package com.quarki3.trading.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.quarki3.trading.BuildConfig
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "trading_settings")

class SettingsRepo(private val context: Context) {
    private val backendKey = stringPreferencesKey("backend_url")

    val backendUrl: Flow<String> = context.dataStore.data.map { prefs ->
        prefs[backendKey] ?: BuildConfig.DEFAULT_BACKEND
    }

    suspend fun setBackendUrl(url: String) {
        context.dataStore.edit { prefs -> prefs[backendKey] = url.trim() }
    }
}
