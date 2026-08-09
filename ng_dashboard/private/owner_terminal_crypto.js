/* Quarki333 Terminal — owner-private CRYPTO tab (D39 sleeve).
 * Same injection pattern as owner_catalog.js: a navbtn appended into the
 * Terminal SPA's bottom nav (mobile) / side rail (desktop), toggling a
 * fixed overlay pane. Plus a best-effort green overlay of the sleeve's
 * equity on the Book page's ALPACA_LIVE chart card (hides during touch —
 * the React chart pans/zooms and a static overlay must never misalign).
 * Data: /api/crypto_sleeve + /api/equity/crypto_sleeve (owner-only, D1). */
(function () {
  "use strict";
  var GREEN = "#00E5A0", AMBER = "#d29922", RED = "#f85149";
  var POLL_MS = 30000;
  var open = false, timer = null, data = null, eq = null, err = null;

  function key() {
    try { return localStorage.getItem("qt.apiKey") || null; } catch (e) { return null; }
  }
  function jget(url) {
    var h = { accept: "application/json" };
    var k = key();
    if (k) h["x-api-key"] = k;
    return fetch(url, { headers: h }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function money(v) { return (v < 0 ? "-$" : "$") + Math.abs(+v || 0).toFixed(2); }
  function big$(v) {
    if (v >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
    if (v >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
    if (v >= 1e3) return "$" + (v / 1e3).toFixed(0) + "k";
    return "$" + Math.round(v);
  }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  var css = [
    ".qtcr-overlay{position:fixed;top:0;right:0;left:0;z-index:25;overflow-y:auto;",
    "background:var(--bg,#0d1117);color:var(--fg,#e6edf3);padding:12px;",
    "bottom:calc(52px + env(safe-area-inset-bottom));-webkit-font-smoothing:antialiased}",
    "@media(min-width:768px){.qtcr-overlay{left:11rem;bottom:0}}",
    ".qtcr-card{border:1px solid var(--bd,#21262d);background:var(--panel,#161b22);",
    "border-radius:8px;padding:12px;margin:0 auto 10px;max-width:72rem}",
    ".qtcr-num{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums}",
    ".qtcr-muted{color:var(--muted,#7d8590)}.qtcr-grn{color:" + GREEN + "}.qtcr-red{color:" + RED + "}",
    ".qtcr-row{display:flex;justify-content:space-between;gap:8px;padding:5px 0;",
    "border-top:1px solid var(--bd,#21262d);font-size:12px}",
    ".qtcr-navbtn-active{color:var(--fg,#e6edf3)!important;font-weight:600}",
  ].join("");

  // ── data ──────────────────────────────────────────────────────────────
  function fetchData() {
    Promise.all([
      jget("/api/crypto_sleeve"),
      jget("/api/equity/crypto_sleeve?period=ALL"),
    ]).then(function (res) {
      data = res[0]; eq = res[1]; err = null;
      if (open) render();
      drawBookOverlay();
    }).catch(function (e) {
      err = e.message;
      if (open) render();
    });
  }

  // ── sleeve chart (green) ──────────────────────────────────────────────
  function chartSvg(pts, W, H) {
    if (!pts || pts.length < 2) return '<div class="qtcr-muted" style="font-size:12px">no equity history yet</div>';
    var vals = pts.map(function (p) { return +p.equity; });
    var L = 46, B = 16, T = 8;
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var rng = (hi - lo) || 1;
    function X(i) { return L + i / (vals.length - 1) * (W - L - 8); }
    function Y(v) { return T + (1 - (v - lo) / rng) * (H - T - B); }
    var line = vals.map(function (v, i) { return X(i).toFixed(1) + "," + Y(v).toFixed(1); }).join(" ");
    var grid = "";
    for (var g = 0; g <= 3; g++) {
      var v = hi - rng * (g / 3), y = Y(v).toFixed(1);
      grid += '<line x1="' + L + '" y1="' + y + '" x2="' + (W - 4) + '" y2="' + y +
        '" stroke="#21262d" stroke-width="1"/>' +
        '<text x="' + (L - 4) + '" y="' + (+y + 3) + '" fill="' + GREEN + '" opacity="0.85"' +
        ' font-size="9" text-anchor="end" class="qtcr-num">$' +
        (rng < 8 ? v.toFixed(2) : v.toFixed(0)) + "</text>";
    }
    var t0 = +pts[0].ts, t1 = +pts[pts.length - 1].ts;
    function ft(ts) {
      var d = new Date(ts * 1000);
      return (t1 - t0 > 3 * 86400) ? (d.getMonth() + 1) + "/" + d.getDate()
        : ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    }
    grid += '<text x="' + L + '" y="' + (H - 3) + '" fill="' + GREEN + '" opacity="0.7" font-size="9" class="qtcr-num">' + ft(t0) + "</text>" +
      '<text x="' + (W - 4) + '" y="' + (H - 3) + '" fill="' + GREEN + '" opacity="0.7" font-size="9" text-anchor="end" class="qtcr-num">' + ft(t1) + "</text>";
    return '<svg viewBox="0 0 ' + W + " " + H + '" style="width:100%;height:auto">' + grid +
      '<polyline points="' + line + '" fill="none" stroke="' + GREEN + '" stroke-width="2" stroke-linejoin="round"/>' +
      '<circle cx="' + X(vals.length - 1).toFixed(1) + '" cy="' + Y(vals[vals.length - 1]).toFixed(1) +
      '" r="3.5" fill="' + GREEN + '" stroke="#0d1117" stroke-width="1.5"/></svg>';
  }

  // ── whale intel ───────────────────────────────────────────────────────
  function intelHtml(g) {
    if (!g || !g.legs) return '<div class="qtcr-muted" style="font-size:12px">no composite whale feed yet</div>';
    var armed = (g.signal_trades >= 5 && g.distinct_wallets >= 3 && g.signal_usd >= 50000);
    var col = g.risk_on >= 0.5 ? GREEN : RED;
    var h = '<div style="display:flex;align-items:baseline;gap:10px">' +
      '<span class="qtcr-num" style="font-size:24px;font-weight:700;color:' + col + '">' +
      Math.round(g.risk_on * 100) + "%</span>" +
      '<span style="font-size:11px;color:' + col + '">' +
      (g.risk_on >= 0.5 ? "big players lean LONG" : "big players lean SHORT / de-risked") + "</span></div>" +
      '<div style="background:#21262d;border-radius:4px;height:8px;margin:6px 0">' +
      '<div style="width:' + Math.round(g.risk_on * 100) + '%;background:' + col + ';height:8px;border-radius:4px"></div></div>' +
      '<div class="qtcr-muted" style="font-size:10px;margin-bottom:6px">evidence: ' +
      g.signal_trades + " positions · " + g.distinct_wallets + " whales · " + big$(g.signal_usd) +
      " → gate " + (armed ? '<b style="color:' + GREEN + '">ARMED</b>' : "<b>advisory</b> (thin)") + "</div>";
    var L = g.legs;
    if (L.hyperliquid) {
      var hl = L.hyperliquid;
      h += '<div class="qtcr-row"><span>🐋 Hyperliquid top traders</span><span class="qtcr-num">' +
        hl.distinct_wallets + " whales · L " + big$(hl.long_usd) + " / S " + big$(hl.short_usd) + "</span></div>";
      var cb = hl.coin_bias || {};
      Object.keys(cb).sort(function (a, b) {
        return (cb[b].long_usd + cb[b].short_usd) - (cb[a].long_usd + cb[a].short_usd);
      }).slice(0, 5).forEach(function (c) {
        var b = cb[c], r = b.net_long_ratio;
        h += '<div class="qtcr-muted qtcr-num" style="font-size:10px;padding-left:14px">' + esc(c) + ": " +
          b.traders_long + "L/" + b.traders_short + "S · " +
          (r == null ? "—" : '<span style="color:' + (r >= 0.5 ? GREEN : RED) + '">' + Math.round(r * 100) + "% long</span>") +
          " · " + big$(b.long_usd + b.short_usd) + "</div>";
      });
    }
    if (L.dune) h += '<div class="qtcr-row"><span>🔍 On-chain smart money</span><span class="qtcr-num">' +
      L.dune.signal_trades + " trades · " + L.dune.distinct_wallets + " wallets · " +
      Math.round(L.dune.risk_on * 100) + "% risk-on</span></div>";
    if (L.social) {
      var d = L.social.detail || {};
      h += '<div class="qtcr-row"><span>💬 Sentiment (mood only)</span><span class="qtcr-num">' +
        (d.fear_greed != null ? "F&G " + (+d.fear_greed) + "/100" : "—") + "</span></div>";
      if (d.trending) h += '<div class="qtcr-muted" style="font-size:10px;padding-left:14px">trending: ' +
        d.trending.slice(0, 5).map(esc).join(" ") + "</div>";
    }
    h += '<div class="qtcr-muted" style="font-size:9px;margin-top:6px">updated ' +
      esc((g.asof || "?").replace("T", " ").slice(0, 16)) + "Z</div>";
    return h;
  }

  // ── pane ──────────────────────────────────────────────────────────────
  function render() {
    var ov = document.querySelector(".qtcr-overlay");
    if (!ov) return;
    ov.innerHTML = "";
    var head = el("div", "qtcr-card",
      '<div style="display:flex;align-items:center;gap:8px"><span style="font-size:18px">🪙</span>' +
      '<b>CRYPTO SLEEVE</b><span class="qtcr-muted" style="font-size:10px">D39 · Kraken · 24/7 · $50 cap · −$15 freeze</span></div>');
    ov.appendChild(head);
    if (err) { ov.appendChild(el("div", "qtcr-card qtcr-red", "fetch failed: " + esc(err))); return; }
    if (!data) { ov.appendChild(el("div", "qtcr-card qtcr-muted", "loading…")); return; }

    var unreal = 0, posHtml = "";
    Object.keys(data.positions || {}).forEach(function (sym) {
      var p = data.positions[sym];
      posHtml += '<div class="qtcr-row"><span>' + esc(sym) +
        ' <span class="qtcr-muted" style="font-size:10px">' + esc((p.signal_mode || "").split(" (")[0]) + "</span></span>" +
        '<span class="qtcr-num">entry $' + (+p.entry_price).toFixed(2) + " · " + money(p.notional) + "</span></div>";
    });
    var eqNow = data.equity != null ? money(data.equity) : "—";
    var stat = el("div", "qtcr-card",
      '<div style="display:flex;gap:18px;flex-wrap:wrap">' +
      '<div><div class="qtcr-muted" style="font-size:10px">EQUITY</div><div class="qtcr-num qtcr-grn" style="font-size:18px;font-weight:700">' + eqNow + "</div></div>" +
      '<div><div class="qtcr-muted" style="font-size:10px">REALIZED</div><div class="qtcr-num" style="font-size:18px;font-weight:700">' + money(data.realized) + "</div></div>" +
      '<div><div class="qtcr-muted" style="font-size:10px">STATUS</div><div style="font-size:18px;font-weight:700" class="' +
      (data.frozen ? "qtcr-red" : "qtcr-grn") + '">' + (data.frozen ? "FROZEN" : "ACTIVE") + "</div></div></div>" +
      (posHtml || '<div class="qtcr-muted" style="font-size:11px;margin-top:6px">no open positions</div>'));
    ov.appendChild(stat);
    ov.appendChild(el("div", "qtcr-card",
      '<div class="qtcr-muted" style="font-size:10px;margin-bottom:6px">EQUITY — CRYPTO SLEEVE (' +
      (eq ? eq.length : 0) + " pts · 5-min ticks · 24/7)</div>" + chartSvg(eq, 400, 180)));
    ov.appendChild(el("div", "qtcr-card",
      '<div class="qtcr-muted" style="font-size:10px;margin-bottom:4px">WHALE INTEL · composite</div>' +
      intelHtml(data.gate)));
    var rows = (data.closed_trades || []).slice().reverse().map(function (t) {
      var pl = +t.realized_pl;
      return '<div class="qtcr-row"><span>' + esc(t.symbol) +
        ' <span class="qtcr-muted" style="font-size:10px">' + esc(t.reason) + "</span></span>" +
        '<span class="qtcr-num" style="color:' + (pl >= 0 ? GREEN : RED) + '">' + money(pl) +
        " (" + (+t.pl_pct * 100).toFixed(1) + "%)</span></div>";
    }).join("");
    ov.appendChild(el("div", "qtcr-card",
      '<div class="qtcr-muted" style="font-size:10px;margin-bottom:4px">CLOSED TRADES</div>' +
      (rows || '<div class="qtcr-muted" style="font-size:11px">none yet — ledger starts with the first exit</div>')));
  }

  function setOpen(v) {
    open = v;
    var ov = document.querySelector(".qtcr-overlay");
    if (open) {
      if (!ov) { ov = el("div", "qtcr-overlay"); document.body.appendChild(ov); }
      render();
      fetchData();
      if (!timer) timer = setInterval(fetchData, POLL_MS);
    } else {
      if (ov) ov.remove();
      if (timer) { clearInterval(timer); timer = null; }
    }
    document.querySelectorAll(".qtcr-navbtn").forEach(function (b) {
      b.classList.toggle("qtcr-navbtn-active", open);
    });
  }

  function makeBtn(mobile) {
    var b = el("button", "qtcr-navbtn");
    if (mobile) {
      b.style.cssText = "flex:1 1 0%;display:flex;flex-direction:column;align-items:center;" +
        "gap:2px;padding:8px 0;font-size:10px;background:none;border:0;color:var(--muted,#7d8590)";
      b.innerHTML = "<span style='font-size:16px;line-height:1'>🪙</span>Crypto";
    } else {
      b.style.cssText = "display:flex;align-items:center;gap:8px;border-radius:4px;" +
        "padding:8px 12px;font-size:14px;background:none;border:0;color:var(--muted,#7d8590);" +
        "text-align:left;width:100%;cursor:pointer";
      b.innerHTML = "<span>🪙</span> Crypto";
    }
    b.addEventListener("click", function () { setOpen(!open); });
    return b;
  }

  // ── best-effort green overlay on the Book ALPACA chart card ──────────
  // The React chart pans/zooms; a static overlay must never pretend to track
  // it. So: draw over the DEFAULT view, hide on any pointer interaction with
  // the card, reappear (redrawn) after 4s of quiet.
  var hideUntil = 0;
  function findBookChartCard() {
    var els = document.querySelectorAll("h2,h3,div,span");
    for (var i = 0; i < els.length; i++) {
      var t = els[i].textContent || "";
      if (t.indexOf("EQUITY") >= 0 && t.indexOf("ALPACA_LIVE") >= 0 && t.length < 60) {
        var card = els[i].closest("section,article,div");
        var probe = card;
        for (var d = 0; d < 4 && probe; d++) {
          var svg = probe.querySelector("svg,canvas");
          if (svg && svg.clientHeight > 80) return { card: probe, plot: svg };
          probe = probe.parentElement;
        }
      }
    }
    return null;
  }
  function drawBookOverlay() {
    var old = document.getElementById("qtcr-book-ov");
    if (old) old.remove();
    if (!eq || eq.length < 2 || Date.now() < hideUntil) return;
    var hit = findBookChartCard();
    if (!hit) return;
    var r = hit.plot.getBoundingClientRect();
    var cr = hit.card.getBoundingClientRect();
    if (r.width < 100) return;
    var host = hit.card;
    if (getComputedStyle(host).position === "static") host.style.position = "relative";
    var vals = eq.map(function (p) { return +p.equity; });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var rng = (hi - lo) || 1;
    var W = Math.round(r.width), H = Math.round(r.height);
    var pts = vals.map(function (v, i) {
      return (i / (vals.length - 1) * (W - 50)).toFixed(1) + "," +
        (8 + (1 - (v - lo) / rng) * (H - 30)).toFixed(1);
    }).join(" ");
    var axis = "";
    for (var g = 0; g <= 2; g++) {
      var v = hi - rng * (g / 2);
      var y = (8 + (g / 2) * (H - 30)).toFixed(0);
      axis += '<text x="' + (W - 4) + '" y="' + y + '" fill="' + GREEN +
        '" font-size="9" text-anchor="end" font-family="monospace">$' +
        (rng < 8 ? v.toFixed(2) : v.toFixed(0)) + "</text>";
    }
    var t0 = new Date(eq[0].ts * 1000), t1 = new Date(eq[eq.length - 1].ts * 1000);
    function hm(d) { return ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2); }
    axis += '<text x="4" y="10" fill="' + GREEN + '" font-size="8" font-family="monospace">' + hm(t0) + " ▸</text>" +
      '<text x="' + (W - 54) + '" y="10" fill="' + GREEN + '" font-size="8" text-anchor="end" font-family="monospace">▸ ' + hm(t1) + "</text>";
    var ov = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    ov.id = "qtcr-book-ov";
    ov.setAttribute("width", W); ov.setAttribute("height", H);
    ov.style.cssText = "position:absolute;pointer-events:none;z-index:5;" +
      "left:" + Math.round(r.left - cr.left) + "px;top:" + Math.round(r.top - cr.top) + "px";
    ov.innerHTML = axis + '<polyline points="' + pts + '" fill="none" stroke="' + GREEN +
      '" stroke-width="2" opacity="0.9" stroke-linejoin="round"/>' +
      '<text x="4" y="' + (H - 4) + '" fill="' + GREEN + '" font-size="8" font-family="monospace">CRYPTO · green (top clock, right $)</text>';
    host.appendChild(ov);
  }
  document.addEventListener("pointerdown", function (ev) {
    var ovEl = document.getElementById("qtcr-book-ov");
    if (ovEl && ev.target && ovEl.parentElement && ovEl.parentElement.contains(ev.target)) {
      hideUntil = Date.now() + 4000;
      ovEl.remove();
      setTimeout(drawBookOverlay, 4200);
    }
  }, true);

  function mount() {
    var mobileNav = document.querySelector("nav.fixed.inset-x-0.bottom-0");
    if (mobileNav && !mobileNav.querySelector(".qtcr-navbtn"))
      mobileNav.appendChild(makeBtn(true));
    var rail = document.querySelector("nav.hidden .flex-1.flex-col, nav .flex.flex-1.flex-col");
    if (rail && !rail.querySelector(".qtcr-navbtn"))
      rail.appendChild(makeBtn(false));
    if (!open) drawBookOverlay();
  }

  function init() {
    var st = document.createElement("style");
    st.textContent = css;
    document.head.appendChild(st);
    fetchData();
    setInterval(fetchData, POLL_MS * 2);
    mount();
    setInterval(mount, 2000);
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
