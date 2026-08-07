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
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

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
                // everything else is an external link -> system browser.
                val homeHost = Uri.parse(startUrl).host
                return if (url.host.equals(homeHost, ignoreCase = true)) {
                    false
                } else {
                    openExternally(url)
                    true
                }
            }

            override fun onPageFinished(view: WebView, url: String?) {
                swipeRefresh.isRefreshing = false
            }
        }

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

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState)
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
