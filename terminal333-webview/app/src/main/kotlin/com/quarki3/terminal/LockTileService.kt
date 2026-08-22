package com.quarki3.terminal

import android.content.Context
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/**
 * "333 Lock" Quick Settings tile (Robert 2026-08-22): arms/disarms the
 * fingerprint gate on app launch without opening the app. Tile ACTIVE =
 * gate armed (fingerprint required), INACTIVE = disarmed. Takes effect on
 * the app's next cold start; it never kicks a running session.
 */
class LockTileService : TileService() {

    private val prefs
        get() = getSharedPreferences("quarki_terminal", Context.MODE_PRIVATE)

    override fun onStartListening() {
        super.onStartListening()
        refresh()
    }

    override fun onClick() {
        super.onClick()
        val armed = prefs.getBoolean(MainActivity.KEY_BIOMETRIC, false)
        prefs.edit().putBoolean(MainActivity.KEY_BIOMETRIC, !armed).apply()
        refresh()
    }

    private fun refresh() {
        val tile = qsTile ?: return
        val armed = prefs.getBoolean(MainActivity.KEY_BIOMETRIC, false)
        tile.state = if (armed) Tile.STATE_ACTIVE else Tile.STATE_INACTIVE
        tile.label = "333 Lock"
        if (android.os.Build.VERSION.SDK_INT >= 29)
            tile.subtitle = if (armed) "Fingerprint on" else "Fingerprint off"
        tile.updateTile()
    }
}
