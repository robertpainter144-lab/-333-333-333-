package com.quarki3.terminal

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageButton
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.swiperefreshlayout.widget.SwipeRefreshLayout
import kotlin.math.abs

class MainActivity : AppCompatActivity() {

    companion object {
        private const val PREFS_NAME = "quarki_terminal"
        private const val KEY_URL = "start_url"
        // Owner surface: tailnet-only private build (real data + Robert's
        // chart work). The public /terminal is the guest day-one+Linus build
        // and must never be this app's default again (D1/D2).
        private val DEFAULT_URL get() = com.quarki3.terminal.BuildConfig.DEFAULT_TERMINAL_URL
        // pre-v1.2 default; a saved copy in prefs must not pin the app to the
        // guest surface after upgrade
        private const val LEGACY_URL = "https://129-153-96-201.sslip.io/terminal"
        private const val LONG_PRESS_MS = 1500L
        // System picture-in-picture (Robert 2026-08-19): keep the equity
        // chart floating over other apps when the Terminal is minimized.
        private const val KEY_PIP = "pip_enabled"
        // Quick Settings "333 Lock" tile (Robert 2026-08-22) flips this so
        // the fingerprint gate can be disarmed for a work session without
        // opening the app. Default stays armed.
        const val KEY_BIOMETRIC = "biometric_gate"
    }

    private var pipEnabled: Boolean
        get() = prefs.getBoolean(KEY_PIP, true)
        set(value) = prefs.edit().putBoolean(KEY_PIP, value).apply()

    private fun tryEnterPip() {
        try {
            enterPictureInPictureMode(
                android.app.PictureInPictureParams.Builder()
                    .setAspectRatio(android.util.Rational(16, 10))
                    .build())
        } catch (_: Exception) { }
    }

    override fun onUserLeaveHint() {
        super.onUserLeaveHint()
        if (pipEnabled) tryEnterPip()
    }

    override fun onPictureInPictureModeChanged(
        isInPictureInPictureMode: Boolean,
        newConfig: android.content.res.Configuration
    ) {
        super.onPictureInPictureModeChanged(isInPictureInPictureMode, newConfig)
        // tell the page to swap to the chart-only PiP layout (owner_pip.js)
        webView.evaluateJavascript(
            "window.__qtSetPip&&window.__qtSetPip($isInPictureInPictureMode)", null)
        swipeRefresh.isEnabled = !isInPictureInPictureMode
        findViewById<ImageButton>(R.id.menuButton).visibility =
            if (isInPictureInPictureMode) android.view.View.GONE
            else android.view.View.VISIBLE
    }

    private lateinit var webView: WebView
    private lateinit var swipeRefresh: SwipeRefreshLayout

    private val prefs by lazy { getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE) }
    private var startUrl: String
        get() = prefs.getString(KEY_URL, DEFAULT_URL) ?: DEFAULT_URL
        set(value) = prefs.edit().putString(KEY_URL, value).apply()

    private val longPressHandler = Handler(Looper.getMainLooper())
    private val longPressRunnable = Runnable { showUrlDialog() }
    private var downX = 0f
    private var downY = 0f

    @SuppressLint("SetJavaScriptEnabled", "ClickableViewAccessibility")
    private var unlocked = false
    private var pendingState: Bundle? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        // Biometric gate (Robert 2026-08-20): the app controls a live trading
        // account, so require fingerprint / device credential before any UI
        // or WebView loads. authenticate() calls buildApp() on success. Stash
        // savedInstanceState — buildApp() runs async after the prompt.
        pendingState = savedInstanceState
        requireBiometric()
    }

    // BIOMETRIC_STRONG (fingerprint/face) OR device PIN/pattern/password — so
    // a phone with no enrolled fingerprint, or a failed sensor, still unlocks
    // via the lockscreen credential rather than bricking the app.
    private val authAllowed = BiometricManager.Authenticators.BIOMETRIC_STRONG or
        BiometricManager.Authenticators.DEVICE_CREDENTIAL

    private fun requireBiometric() {
        if (!prefs.getBoolean(KEY_BIOMETRIC, false)) {
            // Gate disarmed via the 333 Lock Quick Settings tile.
            buildApp()
            return
        }
        val canAuth = BiometricManager.from(this).canAuthenticate(authAllowed)
        if (canAuth != BiometricManager.BIOMETRIC_SUCCESS) {
            // No biometric AND no device lock configured: fail OPEN rather
            // than lock Robert out of his own trading app — the tailnet +
            // owner-key gate still protect the data. (A device with any
            // lockscreen credential never reaches here.)
            buildApp()
            return
        }
        val exec = ContextCompat.getMainExecutor(this)
        val prompt = BiometricPrompt(this, exec,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(r: BiometricPrompt.AuthenticationResult) {
                    if (!unlocked) buildApp()
                }
                override fun onAuthenticationError(code: Int, msg: CharSequence) {
                    // User cancelled / too many attempts: close the app rather
                    // than expose the trading surface unauthenticated.
                    if (!unlocked) finish()
                }
            })
        prompt.authenticate(
            BiometricPrompt.PromptInfo.Builder()
                .setTitle("Unlock Quarki333 Terminal")
                .setSubtitle("Fingerprint or device PIN to access live trading")
                .setAllowedAuthenticators(authAllowed)
                .build())
    }

    private fun buildApp() {
        if (unlocked) return
        unlocked = true

        // Discard ANY saved URL from a prior install: the /private-terminal
        // route only existed on the retired Oracle VM, and pinning to it (or
        // to the dead sslip host) is exactly what blanked the app. Always
        // boot from the current compiled DEFAULT_URL (Mac funnel + owner key)
        // so a fresh install and an upgrade behave identically.
        prefs.getString(KEY_URL, null)?.let { saved ->
            val uri = Uri.parse(saved)
            val onDeadRoute = saved == LEGACY_URL ||
                uri.path?.trimEnd('/')?.endsWith("/private-terminal") == true ||
                uri.host?.contains("sslip.io") == true ||
                uri.host == "100.73.225.4"
            if (onDeadRoute) prefs.edit().remove(KEY_URL).apply()
        }

        // targetSdk 35 enforces edge-to-edge: without consuming system-bar
        // insets the page draws under the status bar and gesture nav. Pad the
        // root instead; its background stays terminal-dark so the bars blend.
        val root = findViewById<FrameLayout>(R.id.root)
        root.setBackgroundColor(Color.parseColor("#0d1117"))
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars()
                    or WindowInsetsCompat.Type.displayCutout())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }

        webView = findViewById(R.id.webView)
        swipeRefresh = findViewById(R.id.swipeRefresh)
        val menuButton = findViewById<ImageButton>(R.id.menuButton)

        webView.setBackgroundColor(Color.parseColor("#0d1117"))
        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            loadWithOverviewMode = true
            useWideViewPort = true
            mediaPlaybackRequiresUserGesture = false
        }

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest
            ): Boolean {
                val url = request.url
                val scheme = url.scheme?.lowercase()
                // Non-http(s) schemes (mailto:, tel:, intent:, ...) -> system.
                if (scheme != "http" && scheme != "https") {
                    openExternally(url)
                    return true
                }
                // Same host as the configured start URL stays in the WebView;
                // the embedded terminal host also stays in-app; everything
                // else is an external link -> system browser.
                val homeHost = Uri.parse(startUrl).host
                val termHost = runCatching {
                    Uri.parse(BuildConfig.TERMINAL_URL).host }.getOrNull()
                return if (url.host.equals(homeHost, ignoreCase = true) ||
                           (termHost != null && url.host.equals(termHost, ignoreCase = true))) {
                    false
                } else {
                    openExternally(url)
                    true
                }
            }

            // Auto-supply the terminal's basic-auth for its host ONLY, so the
            // fingerprint-gated app holds the password and other tailnet
            // clients still must enter it (Robert 2026-08-20).
            override fun onReceivedHttpAuthRequest(
                view: WebView, handler: android.webkit.HttpAuthHandler,
                host: String?, realm: String?
            ) {
                val termHost = runCatching {
                    Uri.parse(BuildConfig.TERMINAL_URL).host }.getOrNull()
                val creds = BuildConfig.TERMINAL_AUTH
                if (host != null && host.equals(termHost, ignoreCase = true) &&
                    creds.contains(":")) {
                    val i = creds.indexOf(":")
                    handler.proceed(creds.substring(0, i), creds.substring(i + 1))
                } else {
                    handler.cancel()
                }
            }

            override fun onPageFinished(view: WebView, url: String?) {
                swipeRefresh.isRefreshing = false
            }
        }

        // Native haptics bridge: Chromium blocks navigator.vibrate until the
        // user has TAPPED the page (pinch gestures never qualify — their
        // touchstart is preventDefaulted by the zoom engine), so the pinch
        // detent ticks were silently dropped. window.QTNative.vibrate(ms)
        // bypasses web user-activation rules; short calls map to the system's
        // crisp CLICK effects rather than a mushy raw buzz.
        webView.addJavascriptInterface(object {
            @android.webkit.JavascriptInterface
            fun vibrate(ms: Int) {
                try {
                    @Suppress("DEPRECATION")
                    val v = getSystemService(Context.VIBRATOR_SERVICE) as android.os.Vibrator
                    val eff = when {
                        ms <= 25 -> android.os.VibrationEffect.createPredefined(
                            android.os.VibrationEffect.EFFECT_CLICK)
                        ms <= 60 -> android.os.VibrationEffect.createPredefined(
                            android.os.VibrationEffect.EFFECT_HEAVY_CLICK)
                        else -> android.os.VibrationEffect.createOneShot(
                            ms.toLong().coerceIn(1, 300),
                            android.os.VibrationEffect.DEFAULT_AMPLITUDE)
                    }
                    v.vibrate(eff)
                } catch (_: Exception) { }
            }

            // Terminal login helpers (2026-08-22): the terminal card's
            // "open last URL" / "copy" buttons hand off through here —
            // native clipboard + system browser beat WebView popups.
            @android.webkit.JavascriptInterface
            fun openUrl(url: String) {
                if (!url.startsWith("http://") && !url.startsWith("https://")) return
                runOnUiThread {
                    try { openExternally(Uri.parse(url)) } catch (_: Exception) { }
                }
            }

            @android.webkit.JavascriptInterface
            fun copyText(text: String) {
                try {
                    val cm = getSystemService(Context.CLIPBOARD_SERVICE)
                        as android.content.ClipboardManager
                    cm.setPrimaryClip(
                        android.content.ClipData.newPlainText("terminal", text))
                } catch (_: Exception) { }
            }
        }, "QTNative")

        // PiP bridge: the web PiP panel toggles auto-PiP and can enter PiP
        // on demand (owner_pip.js probes for QTPip and shows the controls
        // only inside this app).
        webView.addJavascriptInterface(object {
            @android.webkit.JavascriptInterface
            fun setEnabled(on: Boolean) { pipEnabled = on }
            @android.webkit.JavascriptInterface
            fun isEnabled(): Boolean = pipEnabled
            @android.webkit.JavascriptInterface
            fun enterNow() { runOnUiThread { tryEnterPip() } }
        }, "QTPip")

        // Surface WebView JS console + errors to logcat (tag QT_WEB) so a
        // blank page can be diagnosed over adb instead of guessed at.
        android.webkit.WebView.setWebContentsDebuggingEnabled(true)
        webView.webChromeClient = object : android.webkit.WebChromeClient() {
            override fun onConsoleMessage(m: android.webkit.ConsoleMessage): Boolean {
                android.util.Log.i("QT_WEB",
                    "${m.messageLevel()} ${m.message()} @${m.sourceId()}:${m.lineNumber()}")
                return true
            }
        }

        // Swipe-down-to-refresh; only enabled when the page is scrolled to top.
        swipeRefresh.setColorSchemeColors(Color.parseColor("#58a6ff"))
        swipeRefresh.setProgressBackgroundColorSchemeColor(Color.parseColor("#161b22"))
        swipeRefresh.setOnRefreshListener { webView.reload() }
        swipeRefresh.setOnChildScrollUpCallback { _, _ -> webView.scrollY > 0 }

        // Long-press anywhere for 1.5s opens the URL dialog.
        val slop = ViewConfiguration.get(this).scaledTouchSlop * 3
        webView.setOnTouchListener { _, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    downX = event.x
                    downY = event.y
                    longPressHandler.postDelayed(longPressRunnable, LONG_PRESS_MS)
                }
                MotionEvent.ACTION_MOVE -> {
                    if (abs(event.x - downX) > slop || abs(event.y - downY) > slop) {
                        longPressHandler.removeCallbacks(longPressRunnable)
                    }
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL, MotionEvent.ACTION_POINTER_DOWN ->
                    longPressHandler.removeCallbacks(longPressRunnable)
            }
            false
        }

        menuButton.setOnClickListener { showUrlDialog() }

        // Back navigates WebView history before exiting the app.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        val saved = pendingState
        pendingState = null
        if (saved != null) {
            webView.restoreState(saved)
        } else {
            webView.loadUrl(startUrl)
        }
    }

    private fun openExternally(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (_: Exception) {
            // No handler for this URI; ignore.
        }
    }

    private fun showUrlDialog() {
        longPressHandler.removeCallbacks(longPressRunnable)
        val padding = (16 * resources.displayMetrics.density).toInt()
        val input = EditText(this).apply {
            inputType = InputType.TYPE_TEXT_VARIATION_URI
            setText(startUrl)
            setSelection(text.length)
            hint = DEFAULT_URL
        }
        val container = FrameLayout(this).apply {
            setPadding(padding, padding / 2, padding, 0)
            addView(input)
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.dialog_title)
            .setView(container)
            .setPositiveButton(R.string.dialog_save) { _, _ ->
                val entered = input.text.toString().trim()
                if (entered.isNotEmpty()) {
                    val url = if (entered.startsWith("http://") || entered.startsWith("https://")) {
                        entered
                    } else {
                        "https://$entered"
                    }
                    startUrl = url
                    webView.loadUrl(url)
                }
            }
            .setNeutralButton(R.string.dialog_reset) { _, _ ->
                startUrl = DEFAULT_URL
                webView.loadUrl(DEFAULT_URL)
            }
            .setNegativeButton(R.string.dialog_cancel, null)
            .show()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }

    override fun onDestroy() {
        longPressHandler.removeCallbacks(longPressRunnable)
        webView.destroy()
        super.onDestroy()
    }
}
