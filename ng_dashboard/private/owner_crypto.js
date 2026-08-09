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
  var cachePts = null, cacheAt = 0, cachePeriod = null;

  function refreshCache(period) {
    var now = Date.now();
    if (cachePts && cachePeriod === period && now - cacheAt < POLL_MS) return Promise.resolve(cachePts);
    return jget("/api/equity/crypto_sleeve?period=" + period).then(function (pts) {
      cachePts = pts; cacheAt = now; cachePeriod = period;
      return pts;
    });
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
        posHtml += '<div class="cpos"><span>' + sym + " · " +
          (p.signal_mode || "").split(" (")[0] + "</span><span>entry $" +
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
      var tbl = document.getElementById("crypto-ledger");
      if (tbl) {
        tbl.innerHTML = "<tr><th>symbol</th><th>P/L</th><th>%</th><th>reason</th></tr>" +
          (s.closed_trades || []).slice().reverse().map(function (t) {
            var pl = +t.realized_pl;
            return "<tr><td>" + t.symbol + '</td><td style="color:' +
              (pl >= 0 ? GREEN : "#f85149") + '">' + money(pl) + "</td><td>" +
              (+t.pl_pct * 100).toFixed(1) + "%</td><td>" + t.reason + "</td></tr>";
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
