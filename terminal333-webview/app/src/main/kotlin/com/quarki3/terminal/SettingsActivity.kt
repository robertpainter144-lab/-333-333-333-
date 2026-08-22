package com.quarki3.terminal

import android.app.Activity
import android.content.Context
import android.os.Bundle
import android.util.TypedValue
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView

/**
 * App-settings screen (Robert 2026-08-22): reachable from Android Settings >
 * Apps > Terminal 333 > "App settings" (APPLICATION_PREFERENCES intent) and
 * from a long-press on the launcher icon. One switch, same pref as the
 * "333 Lock" QS tile; takes effect on the app's next cold start.
 */
class SettingsActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        title = "Terminal 333 settings"
        val prefs = getSharedPreferences("quarki_terminal", Context.MODE_PRIVATE)

        fun dp(v: Int) = TypedValue.applyDimension(
            TypedValue.COMPLEX_UNIT_DIP, v.toFloat(), resources.displayMetrics).toInt()

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(24), dp(24), dp(24))
        }
        val sw = Switch(this).apply {
            text = "Require fingerprint to open"
            textSize = 18f
            isChecked = prefs.getBoolean(MainActivity.KEY_BIOMETRIC, false)
            setOnCheckedChangeListener { _, on ->
                prefs.edit().putBoolean(MainActivity.KEY_BIOMETRIC, on).apply()
            }
        }
        val hint = TextView(this).apply {
            text = ("Applies the next time the app starts. The “333 Lock” " +
                    "quick-settings tile flips the same switch.")
            textSize = 13f
            alpha = 0.7f
            setPadding(0, dp(12), 0, 0)
        }
        root.addView(sw)
        root.addView(hint)
        setContentView(root)
    }
}
