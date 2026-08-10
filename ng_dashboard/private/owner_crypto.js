/* Quarki webapp — owner-private CRYPTO sleeve add-on (D39).
 * Injected server-side into /app only for owner-mode sessions (no-XFF tailnet
 * or correct ?key=). Adds:
 *   1. A green crypto-equity overlay on the Book page's Alpaca chart, with a
 *      color-matched right-hand $ axis (yellow/left = Alpaca, green/right =
 *      crypto) and a small legend so both series stay readable at once.
 *   2. A "Crypto" tab in the bottom navbar with the sleeve's own full chart,
 *      rails, whale-gate state, open positions and recent closed trades.
 * Data: /api/equity/crypto_sleeve + /api/crypto_sleeve (both owner-only). */
(function () {
  "use strict";
  var GREEN = "#00E5A0";
  var POLL_MS = 30000;

  // key travels on our script src when loaded via the public funnel
  var KEY = null;
  try {
    var src = document.currentScript && document.currentScript.src;
    if (src) KEY = new URL(src).searchParams.get("key");
  } catch (e) {}
  function withKey(url) {
    return KEY ? url + (url.indexOf("?") >= 0 ? "&" : "?") + "key=" + KEY : url;
  }
  function jget(url) {
    return fetch(withKey(url)).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  // ── 1. Green overlay on the Book chart ────────────────────────────────
  var cachePts = null, cacheA = null, cacheAt = 0, cachePeriod = null;

  function refreshCache(period) {
    var now = Date.now();
    if (cachePts && cachePeriod === period && now - cacheAt < POLL_MS) return Promise.resolve(cachePts);
    return Promise.all([
      jget("/api/equity/crypto_sleeve?period=" + period),
      jget("/api/equity/alpaca_live?period=" + period).catch(function () { return null; }),
    ]).then(function (res) {
      cachePts = res[0]; cacheA = res[1]; cacheAt = now; cachePeriod = period;
      return cachePts;
    });
  }

  function fmtTs(ts, spanS) {
    var d = new Date(ts * 1000);
    if (spanS > 3 * 86400) return (d.getMonth() + 1) + "/" + d.getDate();
    return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
  }

  // time labels along one edge, each on a backing chip
  function timeAxis(pts, y, color, leftPad, W) {
    if (!pts || pts.length < 2) return "";
    var t0 = +pts[0].ts, t1 = +pts[pts.length - 1].ts, span = t1 - t0;
    if (!(span > 0)) return "";
    var out = "";
    // label CENTERS clamped so chips clear the legend (ends at leftPad+150,
    // plus half a chip) on the left and the $-axis chips (~44px) on the right
    var xL = leftPad + 150 + 22, xR = W - 48 - 18;
    for (var i = 0; i <= 2; i++) {
      var frac = i / 2;
      var x = xL + (xR - xL) * frac;
      var txt = fmtTs(t0 + span * ((x - leftPad) / (W - leftPad)), span);
      var cw = txt.length * 6 + 6;
      out += '<rect x="' + (x - cw / 2) + '" y="' + (y - 9) + '" width="' + cw +
        '" height="12" rx="2" fill="#0d1117" opacity="0.8"/>' +
        '<text x="' + x + '" y="' + y + '" fill="' + color +
        '" font-size="9" text-anchor="middle" font-family="monospace">' + txt + "</text>";
    }
    return out;
  }

  function overlay(svg) {
    var pts = cachePts;
    if (!svg || !pts || pts.length < 2) return;
    var values = pts.map(function (p) { return +p.equity; }).filter(function (v) { return !isNaN(v); });
    if (values.length < 2) return;
    var vb = (svg.getAttribute("viewBox") || "0 0 400 220").split(/\s+/).map(Number);
    var W = vb[2], H = vb[3];
    // mirror the base chart's geometry exactly (leftPad 44, no right gutter)
    // so both series' "now" land on the same right edge — axis labels get
    // backing chips instead of a reserved gutter
    var leftPad = 44, bottomPad = 4;
    var w = W - leftPad, h = H - bottomPad;
    var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
    var range = (max - min) || 1;
    var xStep = w / (values.length - 1);
    var d = "";
    values.forEach(function (v, i) {
      var x = leftPad + i * xStep;
      var y = h - ((v - min) / range) * h;
      d += (i === 0 ? "M " : "L ") + x.toFixed(1) + "," + y.toFixed(1) + " ";
    });
    var g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("class", "crypto-overlay");
    // right-hand $ axis in the series' own green — this is what makes the
    // two scales readable at once (each axis wears its line's color)
    var axis = "";
    for (var gr = 0; gr <= 4; gr++) {
      var frac = gr / 4;
      var yy = h * frac;
      var val = max - (max - min) * frac;
      var txt = "$" + (max - min < 8 ? val.toFixed(2) : val.toFixed(0));
      var cw = txt.length * 6 + 6;
      axis += '<rect x="' + (W - cw - 1) + '" y="' + (yy - 6).toFixed(1) +
        '" width="' + cw + '" height="12" rx="2" fill="#0d1117" opacity="0.8"/>' +
        '<text x="' + (W - 4) + '" y="' + (yy + 3).toFixed(1) +
        '" fill="' + GREEN + '" font-size="10" text-anchor="end" font-family="monospace">' +
        txt + "</text>";
    }
    var lastX = leftPad + (values.length - 1) * xStep;
    var lastY = h - ((values[values.length - 1] - min) / range) * h;
    // dual time axes: crypto time (green) along the TOP edge, Alpaca/market
    // time (amber) along the BOTTOM — the two series genuinely live on
    // different clocks (24/7 vs market hours), so each gets its own
    axis += timeAxis(pts, 11, GREEN, leftPad, W);
    axis += timeAxis(cacheA, H - 3, "#d29922", leftPad, W);
    g.innerHTML = axis +
      '<path d="' + d + '" fill="none" stroke="' + GREEN + '" stroke-width="2" ' +
      'stroke-linejoin="round" stroke-linecap="round" opacity="0.95"/>' +
      '<circle cx="' + lastX.toFixed(1) + '" cy="' + lastY.toFixed(1) + '" r="3.5" fill="' + GREEN + '" stroke="#0d1117" stroke-width="1.5"/>' +
      '<g font-size="9" font-family="monospace">' +
      '<rect x="' + leftPad + '" y="2" width="150" height="13" rx="3" fill="#0d1117" opacity="0.75"/>' +
      '<circle cx="' + (leftPad + 8) + '" cy="8.5" r="3" fill="var(--good)"/>' +
      '<text x="' + (leftPad + 14) + '" y="11" fill="#7d8590">ALPACA · left</text>' +
      '<circle cx="' + (leftPad + 82) + '" cy="8.5" r="3" fill="' + GREEN + '"/>' +
      '<text x="' + (leftPad + 88) + '" y="11" fill="#7d8590">CRYPTO · right</text></g>';
    svg.appendChild(g);
  }

  // wrap the shell's chart renderer: draw base first, then our layer on top
  function hookChart() {
    if (typeof window.drawEquityWithAxes !== "function") return setTimeout(hookChart, 500);
    var orig = window.drawEquityWithAxes;
    window.drawEquityWithAxes = function (svg, values, ref) {
      orig(svg, values, ref);
      try {
        var cp = document.getElementById("pane-crypto");
        if (cp && cp.style.display !== "none") return;  // crypto tab open: its own poll covers us
        if (typeof currentRealm !== "undefined" && currentRealm === "book" &&
            typeof currentBook !== "undefined" && currentBook === "alpaca_live") {
          var period = (typeof equityPeriod !== "undefined") ? equityPeriod : "1D";
          overlay(svg);                       // draw from cache immediately
          refreshCache(period).then(function () {
            var old = svg.querySelector(".crypto-overlay");
            if (old) old.remove();
            overlay(svg);                     // redraw with fresh data
          }).catch(function () {});
        }
      } catch (e) {}
    };
  }
  hookChart();

  // ── 2. Crypto tab ─────────────────────────────────────────────────────
  var css = document.createElement("style");
  css.textContent =
    "#pane-crypto .stat{display:inline-block;margin:0 14px 8px 0}" +
    "#pane-crypto .stat .k{font-size:10px;color:#7d8590;letter-spacing:.06em}" +
    "#pane-crypto .stat .v{font-size:16px;font-weight:700;font-variant-numeric:tabular-nums}" +
    "#pane-crypto .cpos{display:flex;justify-content:space-between;padding:7px 10px;" +
    "border:1px solid #21262d;border-radius:8px;margin-bottom:6px;font-size:12px}" +
    "#pane-crypto .grn{color:" + GREEN + "}" +
    "#pane-crypto .card{border:1px solid #21262d;border-radius:10px;padding:12px;margin:10px}" +
    "#pane-crypto table{width:100%;border-collapse:collapse;font-size:11px}" +
    "#pane-crypto td,#pane-crypto th{padding:4px 6px;text-align:right;border-bottom:1px solid #21262d}" +
    "#pane-crypto th{color:#7d8590;font-weight:600}" +
    "#pane-crypto td:first-child,#pane-crypto th:first-child{text-align:left}";
  document.head.appendChild(css);

  function el(tag, attrs, html) {
    var e = document.createElement(tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (html != null) e.innerHTML = html;
    return e;
  }

  function buildPane() {
    var pane = el("section", { id: "pane-crypto", style: "display:none" });
    pane.innerHTML =
      '<div class="header"><span class="dot blink" style="background:' + GREEN + '"></span>' +
      '<span class="app-title">🪙 CRYPTO SLEEVE</span>' +
      '<span class="subtitle">D39 · Kraken · 24/7 · $50 cap · −$15 freeze</span></div>' +
      '<div class="card"><svg id="crypto-svg" viewBox="0 0 400 200" style="width:100%;height:200px"></svg>' +
      '<div id="crypto-meta" style="font-size:10px;color:#7d8590;margin-top:4px"></div></div>' +
      '<div class="card" id="crypto-stats">loading…</div>' +
      '<div class="card" id="crypto-intel">loading whale intel…</div>' +
      '<div class="card"><div style="font-size:11px;color:#7d8590;margin-bottom:6px">RECENT CLOSED TRADES</div>' +
      '<table id="crypto-ledger"><tr><th>symbol</th><th>P/L</th><th>%</th><th>reason</th></tr></table></div>';
    document.querySelector(".wrap, body > div") // main container
      ? (document.getElementById("pane-book").parentNode.appendChild(pane))
      : document.body.appendChild(pane);
    return pane;
  }

  function drawCryptoChart(svg, pts) {
    if (!svg) return;
    var values = pts.map(function (p) { return +p.equity; });
    if (values.length < 2) { svg.innerHTML = ""; return; }
    var W = 400, H = 200, leftPad = 48, h = H - 6;
    var w = W - leftPad;
    var min = Math.min.apply(null, values), max = Math.max.apply(null, values);
    var range = (max - min) || 1;
    var xStep = w / (values.length - 1);
    var d = "", fill = "M " + leftPad + "," + h + " ";
    values.forEach(function (v, i) {
      var x = leftPad + i * xStep, y = h - ((v - min) / range) * h;
      d += (i === 0 ? "M " : "L ") + x.toFixed(1) + "," + y.toFixed(1) + " ";
      fill += "L " + x.toFixed(1) + "," + y.toFixed(1) + " ";
    });
    fill += "L " + (leftPad + (values.length - 1) * xStep).toFixed(1) + "," + h + " Z";
    var grid = "";
    for (var g = 0; g <= 4; g++) {
      var frac = g / 4, y = h * frac, val = max - (max - min) * frac;
      grid += '<line x1="' + leftPad + '" y1="' + y + '" x2="' + W + '" y2="' + y +
        '" stroke="#21262d" stroke-width="1"/>' +
        '<text x="' + (leftPad - 4) + '" y="' + (y + 3) + '" fill="' + GREEN +
        '" opacity="0.85" font-size="10" text-anchor="end" font-family="monospace">$' +
        (max - min < 8 ? val.toFixed(2) : val.toFixed(0)) + "</text>";
    }
    svg.innerHTML = '<defs><linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + GREEN + '" stop-opacity="0.3"/>' +
      '<stop offset="100%" stop-color="' + GREEN + '" stop-opacity="0"/></linearGradient></defs>' +
      grid + '<path d="' + fill + '" fill="url(#cg)"/>' +
      '<path d="' + d + '" fill="none" stroke="' + GREEN + '" stroke-width="2.5" stroke-linejoin="round"/>';
  }

  function money(v) { return (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2); }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }

  function big$(v) {
    if (v >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
    if (v >= 1e3) return "$" + (v / 1e3).toFixed(0) + "k";
    return "$" + Math.round(v);
  }

  function riskBar(r) {
    var pct = Math.round(r * 100);
    var col = r >= 0.5 ? GREEN : "#f85149";
    return '<div style="background:#21262d;border-radius:4px;height:8px;margin:4px 0">' +
      '<div style="width:' + pct + '%;background:' + col + ';height:8px;border-radius:4px"></div></div>' +
      '<div style="font-size:10px;color:#7d8590">risk-on ' + pct + "% — " +
      (r >= 0.5 ? "big players lean LONG" : "big players lean SHORT/de-risked") + "</div>";
  }

  function renderIntel(g) {
    var box = document.getElementById("crypto-intel");
    if (!box) return;
    if (!g || !g.legs) {
      box.innerHTML = '<div style="font-size:11px;color:#7d8590">WHALE INTEL</div>' +
        '<div style="font-size:12px;margin-top:4px">no composite feed yet</div>';
      return;
    }
    var authority = (g.signal_trades >= 5 && g.distinct_wallets >= 3 && g.signal_usd >= 50000);
    var headColor = g.risk_on >= 0.5 ? GREEN : "#f85149";
    var htm = '<div style="font-size:11px;color:#7d8590">WHALE INTEL · composite</div>' +
      '<div style="font-size:20px;font-weight:700;color:' + headColor + '">' +
      (g.risk_on * 100).toFixed(0) + "%</div>" +
      riskBar(g.risk_on) +
      '<div style="font-size:10px;color:#7d8590;margin:4px 0 8px">' +
      "evidence: " + g.signal_trades + " positions · " + g.distinct_wallets + " whales · " +
      big$(g.signal_usd) + " tracked → gate " +
      (authority ? '<b style="color:' + GREEN + '">ARMED</b>'
                 : "<b>advisory only</b> (thin)") + "</div>";
    var L = g.legs;
    if (L.hyperliquid) {
      var hl = L.hyperliquid;
      htm += '<div style="border-top:1px solid #21262d;padding-top:6px;margin-top:2px;font-size:11px">' +
        "<b>🐋 Hyperliquid top traders</b> — " + hl.distinct_wallets + " whales, " +
        hl.signal_trades + " positions, long " + big$(hl.long_usd) + " / short " + big$(hl.short_usd) + "</div>";
      var cb = hl.coin_bias || {};
      var coins = Object.keys(cb).sort(function (a, b) {
        return (cb[b].long_usd + cb[b].short_usd) - (cb[a].long_usd + cb[a].short_usd);
      }).slice(0, 5);
      coins.forEach(function (c) {
        var b = cb[c];
        var r = b.net_long_ratio;
        htm += '<div style="font-size:10px;color:#7d8590;margin-left:12px">' + esc(c) + ": " +
          b.traders_long + "L/" + b.traders_short + "S · " +
          (r == null ? "—" : (r >= 0.5 ? '<span style="color:' + GREEN + '">' : '<span style="color:#f85149">') +
            Math.round(r * 100) + "% long</span>") + " · " + big$(b.long_usd + b.short_usd) + "</div>";
      });
    }
    if (L.dune) {
      htm += '<div style="border-top:1px solid #21262d;padding-top:6px;margin-top:6px;font-size:11px">' +
        "<b>🔍 On-chain smart money (Dune)</b> — " + L.dune.signal_trades + " trades, " +
        L.dune.distinct_wallets + " wallets, " + big$(L.dune.signal_usd) +
        " · risk-on " + Math.round(L.dune.risk_on * 100) + "%</div>";
    }
    if (L.social) {
      var d = L.social.detail || {};
      htm += '<div style="border-top:1px solid #21262d;padding-top:6px;margin-top:6px;font-size:11px">' +
        "<b>💬 Sentiment</b> (mood only, never arms the gate) — " +
        (d.fear_greed != null ? "Fear&Greed " + d.fear_greed + "/100" : "") +
        (d.trending ? " · trending: " + d.trending.slice(0, 5).map(esc).join(" ") : "") + "</div>";
      if (d.reddit_buzz && Object.keys(d.reddit_buzz).length) {
        htm += '<div style="font-size:10px;color:#7d8590;margin-left:12px">reddit buzz: ' +
          Object.keys(d.reddit_buzz).map(function (c) { return esc(c) + "(" + (+d.reddit_buzz[c] || 0) + ")"; }).join(" ") + "</div>";
      }
      // Tappable source dropdowns fed by the separate /api/crypto/intel news
      // feed (news_intel.crypto — fear&greed/reddit/stocktwits/headlines).
      // Purely additive: populateNewsIntel() fills this in async and leaves
      // it empty (zero visual change) when news_intel hasn't been collected.
      htm += '<div id="crypto-intel-news"></div>';
    }
    htm += '<div style="font-size:9px;color:#484f58;margin-top:6px">updated ' +
      (g.asof || "?").replace("T", " ").slice(0, 16) + "Z</div>";
    box.innerHTML = htm;
    if (L.social) populateNewsIntel();
  }

  // ── tappable source dropdowns for news_intel (additive, decoupled from
  // the whale-composite gate poll above) ─────────────────────────────────
  function newsSourceLink(url, label) {
    var text = esc(label);
    if (typeof url === "string" && /^https?:\/\//i.test(url)) {
      try {
        var u = new URL(url);
        if (u.protocol === "http:" || u.protocol === "https:") {
          return '<a href="' + esc(url) + '" target="_blank" rel="noopener" style="color:#7d8590">' +
            text + "</a>";
        }
      } catch (e) {}
    }
    return text;
  }

  var newsIntelCache = null, newsIntelAt = 0;
  function fetchNewsIntel() {
    var now = Date.now();
    if (newsIntelCache && now - newsIntelAt < POLL_MS) return Promise.resolve(newsIntelCache);
    return jget("/api/crypto/intel").then(function (doc) {
      newsIntelCache = (doc && doc.news_intel) || null;
      newsIntelAt = now;
      return newsIntelCache;
    }).catch(function () { return null; });
  }

  function populateNewsIntel() {
    if (!document.getElementById("crypto-intel-news")) return;
    fetchNewsIntel().then(function (ni) {
      var host = document.getElementById("crypto-intel-news"); // re-grab: box may have re-rendered meanwhile
      if (!host) return;
      var c = ni && ni.crypto;
      if (!c) { host.innerHTML = ""; return; }
      var sumSty = "font-size:10px;color:#7d8590;cursor:pointer";
      var listSty = "font-size:10px;color:#7d8590;margin:3px 0 0 10px";
      var out = "";
      // (1) reddit — sources behind the reddit buzz line
      if (c.reddit && c.reddit.top_titles && c.reddit.top_titles.length) {
        out += '<details style="margin-left:12px;margin-top:2px">' +
          '<summary style="' + sumSty + '">sources</summary>' +
          '<div style="' + listSty + '">' +
          c.reddit.top_titles.map(function (t) {
            if (!t) return "";
            return '<div style="margin-bottom:2px">r/' + esc(c.reddit.sub || "") + " — " +
              newsSourceLink(t.link, t.title) + "</div>";
          }).join("") + "</div></details>";
      }
      // (2) stocktwits — sample messages behind the social/stocktwits leg
      var withSamples = (c.stocktwits || []).filter(function (s) { return s.samples && s.samples.length; });
      if (withSamples.length) {
        out += '<details style="margin-left:12px;margin-top:2px">' +
          '<summary style="' + sumSty + '">samples</summary>' +
          '<div style="' + listSty + '">' +
          withSamples.map(function (s) {
            return '<div style="margin-bottom:4px"><b>' + esc(s.symbol) + "</b>" +
              s.samples.map(function (m) {
                var tag = (m.sentiment || "").toLowerCase();
                var col = tag === "bullish" ? GREEN : tag === "bearish" ? "#f85149" : "#7d8590";
                return '<div style="margin-left:8px">' + esc(m.body) +
                  (m.sentiment ? ' <span style="color:' + col + '">[' + esc(m.sentiment) + "]</span>" : "") +
                  "</div>";
              }).join("") + "</div>";
          }).join("") + "</div></details>";
      }
      // (3) headlines — general feed plus per-coin groups
      var bySym = c.news_by_symbol || {};
      var symKeys = Object.keys(bySym).filter(function (k) { return bySym[k] && bySym[k].length; });
      if ((c.headlines && c.headlines.length) || symKeys.length) {
        out += '<details style="margin-left:12px;margin-top:2px">' +
          '<summary style="' + sumSty + '">📰 headlines</summary>' +
          '<div style="' + listSty + '">' +
          (c.headlines || []).map(function (h) {
            if (!h) return "";
            return '<div style="margin-bottom:2px">' + newsSourceLink(h.link, h.title) + "</div>";
          }).join("") +
          symKeys.map(function (sym) {
            return '<div style="margin-top:4px"><b>' + esc(sym) + "</b></div>" +
              bySym[sym].map(function (h) {
                if (!h) return "";
                return '<div style="margin-left:8px;margin-bottom:2px">' + newsSourceLink(h.link, h.title) + "</div>";
              }).join("");
          }).join("") + "</div></details>";
      }
      host.innerHTML = out;
    }).catch(function () { /* dropdown stays absent on fetch failure */ });
  }

  function refreshPane() {
    jget("/api/equity/crypto_sleeve?period=ALL").then(function (pts) {
      drawCryptoChart(document.getElementById("crypto-svg"), pts);
      var m = document.getElementById("crypto-meta");
      if (m) m.textContent = pts.length + " points · 5-min ticks · 24/7";
    }).catch(function (e) {
      var m = document.getElementById("crypto-meta");
      if (m) m.textContent = "chart error: " + e.message;
    });
    jget("/api/crypto_sleeve").then(function (s) {
      var box = document.getElementById("crypto-stats");
      if (!box || !s.available) return;
      var posHtml = "";
      var totalUnreal = 0;
      Object.keys(s.positions || {}).forEach(function (sym) {
        var p = s.positions[sym];
        posHtml += '<div class="cpos"><span>' + esc(sym) + " · " +
          esc((p.signal_mode || "").split(" (")[0]) + "</span><span>entry $" +
          (+p.entry_price).toFixed(2) + " · $" + (+p.notional).toFixed(2) + "</span></div>";
      });
      var gate = s.gate ? ("risk_on " + s.gate.risk_on + " · n=" +
        (s.gate.signal_trades != null ? s.gate.signal_trades : s.gate.rows)) : "no feed";
      box.innerHTML =
        '<div class="stat"><div class="k">EQUITY</div><div class="v grn">' +
        (s.equity != null ? money(s.equity) : "—") + "</div></div>" +
        '<div class="stat"><div class="k">REALIZED</div><div class="v">' + money(s.realized) + "</div></div>" +
        '<div class="stat"><div class="k">STATUS</div><div class="v">' +
        (s.frozen ? '<span style="color:#f85149">FROZEN</span>' : '<span class="grn">ACTIVE</span>') + "</div></div>" +
        '<div class="stat"><div class="k">WHALE GATE</div><div class="v" style="font-size:12px">' + gate + "</div></div>" +
        '<div style="margin-top:6px">' + (posHtml || '<span style="color:#7d8590;font-size:12px">no open positions</span>') + "</div>";
      renderIntel(s.gate);
      var tbl = document.getElementById("crypto-ledger");
      if (tbl) {
        tbl.innerHTML = "<tr><th>symbol</th><th>P/L</th><th>%</th><th>reason</th></tr>" +
          (s.closed_trades || []).slice().reverse().map(function (t) {
            var pl = +t.realized_pl;
            return "<tr><td>" + esc(t.symbol) + '</td><td style="color:' +
              (pl >= 0 ? GREEN : "#f85149") + '">' + money(pl) + "</td><td>" +
              (+t.pl_pct * 100).toFixed(1) + "%</td><td>" + esc(t.reason) + "</td></tr>";
          }).join("");
      }
    }).catch(function (e) {
      var box = document.getElementById("crypto-stats");
      if (box) box.innerHTML = '<span style="color:#f85149;font-size:12px">status error: ' +
        e.message + " — retrying every 30s</span>";
    });
  }

  function addTab() {
    var nav = document.getElementById("navbar");
    if (!nav) return setTimeout(addTab, 500);
    var pane = buildPane();
    var item = el("div", { class: "item", "data-realm": "crypto" },
      '<span class="icon">🪙</span>Crypto');
    nav.appendChild(item);
    var paneTimer = null;
    item.addEventListener("click", function () {
      document.querySelectorAll("#navbar .item").forEach(function (x) { x.classList.remove("active"); });
      item.classList.add("active");
      ["pane-book", "pane-lab"].forEach(function (id) {
        var p = document.getElementById(id); if (p) p.style.display = "none";
      });
      pane.style.display = "";
      refreshPane();
      if (paneTimer) clearInterval(paneTimer);
      paneTimer = setInterval(refreshPane, POLL_MS);
    });
    // when the stock tabs are tapped, hide our pane again
    nav.addEventListener("click", function (ev) {
      var it = ev.target.closest(".item");
      if (it && it !== item) {
        pane.style.display = "none";
        if (paneTimer) { clearInterval(paneTimer); paneTimer = null; }
      }
    }, true);
  }
  addTab();
})();
