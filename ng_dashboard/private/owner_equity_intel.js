/* Quarki333 Terminal — owner-private News & Social Intel card, equities side.
 * Sibling of owner_crypto_intel.js (same structure, ported to the Book realm).
 * Does NOT touch webapp.html's own render logic directly — it polls for the
 * #book-body node that webapp.html's renderBook() owns and appends its own
 * card into it, re-appending whenever renderBook() wipes and rebuilds that
 * node (it does `body.innerHTML = ""` on every refresh tick — REFRESH_MS —
 * while the Book realm is the active one; see static/webapp.html).
 *
 * Data: GET /api/equity/intel — composite equities news/social read:
 *   {asof, risk_on_social, held:[symbols],
 *    reddit_wsb:{sub,posts,sentiment,top_mentions:{SYM:count},top_titles:[{title}]},
 *    reddit_stocks:{same shape as reddit_wsb},
 *    stocktwits:[{symbol,messages,bullish,bearish,ratio}],
 *    news_by_symbol:{SYM:[{title,link}]}}
 * Plus GET /api/intel/history — up to 96 rows of
 *   {ts, risk_on_social, fear_greed, errors} used only for the sparkline.
 * Every field above may be null/missing on any given tick; every renderer
 * degrades to a placeholder instead of throwing. Mood/headline data only —
 * this card never arms or informs any trading gate.
 *
 * Injected server-side only for owner-mode sessions alongside the rest of
 * private/*.js, so /api/equity/intel and /api/intel/history are assumed to
 * be owner-gated the same way. Never mounts on the shared/guest Terminal.
 */
(function () {
  "use strict";

  var CONTAINER_ID = "book-body";      // owned by webapp.html renderBook() — read-only
  var CARD_ID = "qtei-card";
  var STYLE_ID = "qtei-style";
  var MOUNT_POLL_MS = 1000;            // watch for the pane / re-renders
  var FETCH_POLL_MS = 60000;
  var GREEN = "#3fb950", RED = "#f85149";

  var data = null, history = [], err = null, cardEl = null, fetchTimer = null, wasMounted = false;
  var loggedError = false;
  function logOnce(e) {
    // tagged, single-shot: never spam the console on a per-tick basis
    if (loggedError) return;
    loggedError = true;
    try { console.error("[owner_equity_intel]", e); } catch (e2) { /* noop */ }
  }

  // key travels on our script src when loaded via the public funnel — captured
  // once at load time, same as owner_crypto.js (document.currentScript is only
  // valid synchronously during initial execution, not from later callbacks).
  var SCRIPT_KEY = null;
  try {
    var _src = document.currentScript && document.currentScript.src;
    if (_src) SCRIPT_KEY = new URL(_src).searchParams.get("key");
  } catch (e) { /* noop */ }

  var css = [
    ".qtei-card{border:1px solid var(--bd);background:var(--panel);border-radius:8px;",
    "padding:12px;margin:10px auto 0;max-width:72rem}",
    ".qtei-num{font-family:ui-monospace,SFMono-Regular,Menlo,Roboto Mono,monospace;",
    "font-variant-numeric:tabular-nums;letter-spacing:-.01em}",
    ".qtei-muted{color:var(--muted)}.qtei-faint{color:var(--faint,#6e7681)}",
    ".qtei-pos{color:#3fb950}.qtei-neg{color:#f85149}.qtei-amber{color:#d29922}",
    ".qtei-h{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}",
    ".qtei-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px;flex-wrap:wrap}",
    ".qtei-sec{margin-top:12px;border-top:1px solid var(--bd);padding-top:10px}",
    ".qtei-stats{display:flex;gap:14px;flex-wrap:wrap;font-size:11px}",
    ".qtei-stats b{display:block;font-size:13px;font-weight:600}",
    ".qtei-table{width:100%;border-collapse:collapse;font-size:11px}",
    ".qtei-table td,.qtei-table th{padding:3px 0;border-top:1px solid var(--bd);text-align:right}",
    ".qtei-table td:first-child,.qtei-table th:first-child{text-align:left}",
    ".qtei-table th{font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border-top:0}",
    ".qtei-pill{display:inline-block;border:1px solid currentColor;border-radius:9999px;",
    "padding:1px 8px;font-size:9px;font-weight:700;letter-spacing:.08em}",
    ".qtei-bar{height:10px;background:var(--panel2,#161b22);border-radius:5px;overflow:hidden}",
    ".qtei-barfill{height:100%;border-radius:5px}",
    ".qtei-chip{display:inline-block;background:var(--panel2,#161b22);border:1px solid var(--bd);",
    "border-radius:6px;padding:1px 8px;font-size:10px;margin:2px 4px 2px 0}",
    ".qtei-chip b{margin-left:4px}",
    ".qtei-headline{font-size:11px;margin-top:3px;line-height:1.35}",
    ".qtei-headline a{color:inherit;text-decoration:underline;text-decoration-color:var(--bd)}"
  ].join("");

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function statHtml(label, val, cls) {
    return '<span><span class="qtei-muted">' + label + '</span><b class="qtei-num ' +
      (cls || "") + '">' + val + "</b></span>";
  }
  function shortTs(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return esc(String(iso || "—"));
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }
  // Only http/https absolute URLs are ever rendered as a link; anything else
  // (relative, javascript:, data:, malformed) falls back to plain text.
  function safeHref(u) {
    if (typeof u !== "string") return null;
    var s = u.trim();
    if (!/^https?:\/\//i.test(s)) return null;
    try {
      var p = new URL(s);
      if (p.protocol !== "http:" && p.protocol !== "https:") return null;
      return p.href;
    } catch (e) { return null; }
  }
  function fmtSentiment(v) {
    if (v == null) return { text: "—", cls: "qtei-muted" };
    if (typeof v === "number" && !isNaN(v)) {
      var cls = v > 0 ? "qtei-pos" : v < 0 ? "qtei-neg" : "qtei-muted";
      return { text: v.toFixed(2), cls: cls };
    }
    var s = String(v);
    var cls2 = /bull/i.test(s) ? "qtei-pos" : /bear/i.test(s) ? "qtei-neg" : "qtei-muted";
    return { text: esc(s), cls: cls2 };
  }

  /* ---- small inline sparkline of risk_on_social over /api/intel/history ---- */
  function sparklineSvg(rows) {
    var pts = (rows || [])
      .filter(function (r) { return r && r.risk_on_social != null && isFinite(+r.risk_on_social); })
      .map(function (r) { return { t: r.ts * 1000, v: +r.risk_on_social }; })
      .filter(function (p) { return isFinite(p.t); })
      .sort(function (a, b) { return a.t - b.t; });
    if (pts.length < 2)
      return '<span class="qtei-muted" style="font-size:10px">not enough history</span>';
    var W = 160, H = 26, pad = 2;
    var vals = pts.map(function (p) { return p.v; });
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    var range = (max - min) || 1;
    var xStep = (W - pad * 2) / (pts.length - 1);
    var d = pts.map(function (p, i) {
      var x = pad + i * xStep;
      var y = H - pad - ((p.v - min) / range) * (H - pad * 2);
      return (i === 0 ? "M" : "L") + x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    var last = vals[vals.length - 1];
    var color = last >= 0.5 ? GREEN : RED;
    return '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H +
      '" style="display:block" aria-hidden="true"><path d="' + d +
      '" fill="none" stroke="' + color + '" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>';
  }

  /* ---- section renderers (every externally-sourced string passes esc()) ---- */
  function renderHeader(d, hist) {
    var ro = d.risk_on_social;
    var pctVal = ro != null && !isNaN(ro) ? Math.max(0, Math.min(100, ro * 100)) : null;
    var barColor = pctVal != null && pctVal >= 50 ? GREEN : RED;
    var html = '<div class="qtei-row"><div><b>📰 NEWS &amp; SOCIAL INTEL — EQUITIES</b></div>' +
      (d.asof ? "<span class='qtei-num qtei-muted' style='font-size:10px'>as of " +
        esc(shortTs(d.asof)) + "</span>" : "") + "</div>";
    html += '<div style="margin-top:10px"><div class="qtei-row">' +
      '<span class="qtei-h" style="margin:0">Risk-on (social)</span>' +
      '<span class="qtei-num" style="font-weight:700;font-size:16px;color:' + barColor + '">' +
      (pctVal != null ? pctVal.toFixed(1) + "%" : "—") + "</span></div>" +
      '<div class="qtei-bar"><div class="qtei-barfill" style="width:' +
      (pctVal != null ? pctVal : 0) + "%;background:" + barColor + '"></div></div></div>';
    html += '<div class="qtei-row" style="margin-top:8px;align-items:center">' +
      '<span class="qtei-h" style="margin:0">Trend (up to 96 ticks)</span>' +
      sparklineSvg(hist) + "</div>";
    return html;
  }

  function renderRedditLeg(label, leg) {
    if (!leg) return '<div class="qtei-h">' + esc(label) + '</div>' +
      '<div class="qtei-muted" style="font-size:11px">No data.</div>';
    var sent = fmtSentiment(leg.sentiment);
    var subLabel = leg.sub ? " · r/" + esc(String(leg.sub).replace(/^r\//i, "")) : "";
    var html = '<div class="qtei-row"><span class="qtei-h" style="margin:0">' + esc(label) +
      subLabel + '</span><span class="qtei-pill ' + sent.cls + '">' + sent.text + "</span></div>";
    html += '<div class="qtei-stats" style="margin-top:4px">' +
      statHtml("posts", leg.posts != null ? esc(String(leg.posts)) : "—") + "</div>";
    var tm = leg.top_mentions || {};
    var syms = Object.keys(tm).sort(function (a, b) { return (tm[b] || 0) - (tm[a] || 0); });
    if (syms.length) {
      html += '<div style="margin-top:6px">' + syms.map(function (s) {
        return '<span class="qtei-chip">' + esc(s) + " <b>" + esc(String(tm[s])) + "</b></span>";
      }).join("") + "</div>";
    }
    var titles = (leg.top_titles || []).map(function (t) {
      return t && t.title != null ? esc(String(t.title)) : "";
    }).filter(Boolean);
    if (titles.length)
      html += '<div class="qtei-faint" style="font-size:10px;margin-top:6px">' +
        titles.slice(0, 3).join(" · ") + "</div>";
    return html;
  }

  function renderReddit(d) {
    return renderRedditLeg("💬 r/WallStreetBets", d.reddit_wsb) +
      '<div style="margin-top:10px"></div>' +
      renderRedditLeg("💬 r/stocks", d.reddit_stocks);
  }

  function renderStocktwits(list) {
    if (!list || !list.length) return '<div class="qtei-h">🐦 StockTwits</div>' +
      '<div class="qtei-muted" style="font-size:11px">No StockTwits data.</div>';
    var rows = list.map(function (r) {
      r = r || {};
      var ratio = r.ratio;
      var ratioOk = ratio != null && !isNaN(ratio);
      var cls = ratioOk ? (ratio >= 0.5 ? "qtei-pos" : "qtei-neg") : "qtei-muted";
      return "<tr><td><b>" + (r.symbol != null ? esc(String(r.symbol)) : "—") + "</b></td>" +
        '<td class="qtei-num">' + (r.messages != null ? esc(String(r.messages)) : "—") + "</td>" +
        '<td class="qtei-num qtei-pos">' + (r.bullish != null ? esc(String(r.bullish)) : "—") + "</td>" +
        '<td class="qtei-num qtei-neg">' + (r.bearish != null ? esc(String(r.bearish)) : "—") + "</td>" +
        '<td class="qtei-num ' + cls + '">' + (ratioOk ? Number(ratio).toFixed(2) : "—") + "</td></tr>";
    }).join("");
    return '<div class="qtei-h">🐦 StockTwits</div>' +
      '<table class="qtei-table"><tr><th>Symbol</th><th>Msgs</th><th>Bull</th><th>Bear</th><th>Ratio</th></tr>' +
      rows + "</table>";
  }

  function renderHeadlines(held, newsBySymbol) {
    if (!held || !held.length) return '<div class="qtei-h">📰 Headlines — held symbols</div>' +
      '<div class="qtei-muted" style="font-size:11px">No held symbols.</div>';
    var news = newsBySymbol || {};
    var html = '<div class="qtei-h">📰 Headlines — held symbols</div>';
    html += held.map(function (sym) {
      var arts = (news[sym] || []).slice(0, 3);
      var rows = arts.map(function (a) {
        var title = a && a.title != null ? String(a.title) : "(untitled)";
        var href = safeHref(a && a.link);
        return href
          ? '<div class="qtei-headline"><a href="' + esc(href) +
            '" target="_blank" rel="noopener">' + esc(title) + "</a></div>"
          : '<div class="qtei-headline qtei-muted">' + esc(title) + "</div>";
      }).join("");
      return '<div style="margin-top:8px"><b class="qtei-num">' + esc(sym) + "</b>" +
        (rows || '<div class="qtei-muted" style="font-size:11px">no headlines</div>') + "</div>";
    }).join("");
    return html;
  }

  function render() {
    if (!cardEl) return;
    var body;
    try {
      if (err) {
        body = '<div class="qtei-row"><div><b>📰 NEWS &amp; SOCIAL INTEL — EQUITIES</b></div></div>' +
          '<div class="qtei-neg" style="margin-top:8px;font-size:11px">intel fetch failed: ' + esc(err) + "</div>";
      } else if (!data) {
        body = '<div class="qtei-row"><div><b>📰 NEWS &amp; SOCIAL INTEL — EQUITIES</b></div></div>' +
          '<div class="qtei-muted" style="margin-top:8px;font-size:11px">loading…</div>';
      } else {
        var d = data;
        body = renderHeader(d, history) +
          '<div class="qtei-sec">' + renderReddit(d) + "</div>" +
          '<div class="qtei-sec">' + renderStocktwits(d.stocktwits) + "</div>" +
          '<div class="qtei-sec">' + renderHeadlines(d.held, d.news_by_symbol) + "</div>";
      }
    } catch (e) {
      logOnce(e);
      body = '<div class="qtei-row"><div><b>📰 NEWS &amp; SOCIAL INTEL — EQUITIES</b></div></div>' +
        '<div class="qtei-neg" style="margin-top:8px;font-size:11px">render error — malformed intel payload</div>';
    }
    cardEl.innerHTML = body;
  }

  function authFetchJson(url) {
    var headers = { accept: "application/json" };
    var k = null;
    try { k = localStorage.getItem("qt.apiKey"); } catch (e) { /* noop */ }
    if (k) {
      headers["x-api-key"] = k;
    } else if (SCRIPT_KEY) {
      url += (url.indexOf("?") >= 0 ? "&" : "?") + "key=" + encodeURIComponent(SCRIPT_KEY);
    }
    return fetch(url, { headers: headers }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function fetchData() {
    try {
      authFetchJson("/api/equity/intel")
        .then(function (d) { data = d; err = null; render(); })
        .catch(function (e) { err = (e && e.message) || "fetch failed"; render(); });
      authFetchJson("/api/intel/history")
        .then(function (rows) { history = Array.isArray(rows) ? rows : []; render(); })
        .catch(function () { /* sparkline just shows its own placeholder */ });
    } catch (e) { logOnce(e); }
  }

  function buildCardShell() {
    var c = document.createElement("div");
    c.id = CARD_ID;
    c.className = "qtei-card";
    return c;
  }

  /* Poll for the Book realm's #book-body node and (re-)mount our card into
   * it, appended after whatever cards renderBook() built for that tick — it
   * runs every REFRESH_MS and does `innerHTML = ""` first, so on ~1s ticks we
   * detect the wipe (our card's parentNode goes stale) and re-append. Cheap
   * DOM lookups only; the network fetch loop is gated on the container
   * actually existing so we never poll while unmounted. */
  function ensureMounted() {
    var host = document.getElementById(CONTAINER_ID);
    if (!host) {
      if (wasMounted) {
        wasMounted = false;
        cardEl = null;
        if (fetchTimer) { clearInterval(fetchTimer); fetchTimer = null; }
      }
      return;
    }
    var existing = document.getElementById(CARD_ID);
    if (!existing || existing.parentNode !== host) {
      cardEl = buildCardShell();
      host.appendChild(cardEl);
      render();
    } else {
      cardEl = existing;
    }
    if (!wasMounted) {
      wasMounted = true;
      fetchData();
      fetchTimer = setInterval(fetchData, FETCH_POLL_MS);
    }
  }

  function injectCss() {
    if (document.getElementById(STYLE_ID)) return;
    var st = document.createElement("style");
    st.id = STYLE_ID;
    st.textContent = css;
    document.head.appendChild(st);
  }

  function safeEnsureMounted() {
    try { ensureMounted(); } catch (e) { logOnce(e); }
  }

  function init() {
    try { injectCss(); } catch (e) { logOnce(e); }
    safeEnsureMounted();
    setInterval(safeEnsureMounted, MOUNT_POLL_MS);
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
