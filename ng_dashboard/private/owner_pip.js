/* Quarki333 Terminal — owner-private Picture-in-Picture equity chart
 * (Robert 2026-08-19: "always see the chart in whatever timespan I want —
 * basically just the portfolio value chart and a minimized version of all
 * the chart timespan option buttons").
 * A draggable floating bubble (📈) toggles a compact always-on-top panel:
 * live equity line + tiny timespan chips. Survives SPA tab switches (body-
 * level fixed element). Owner-mode injection only. */
(function () {
  "use strict";
  var POLL_MS = 60000;
  var SPANS = [["1H", 3600], ["3H", 10800], ["6H", 21600], ["12H", 43200],
               ["1D", 86400], ["3D", 259200], ["1W", 604800],
               ["2W", 1209600], ["3W", 1814400]];
  var state = { on: false, span: "1D", mode: "alp", x: null, y: null };
  try { state = Object.assign(state, JSON.parse(localStorage.getItem("qtpip") || "{}")); } catch (e) {}
  // D61 one-time migration to Kraken-only (equities retired 2026-08-22);
  // manual re-picks stick afterward via the same flag as the main chart.
  try {
    if (state.mode !== "kr" && !localStorage.getItem("qt_d61_kr_pip")) {
      state.mode = "kr";
    }
    localStorage.setItem("qt_d61_kr_pip", "1");
  } catch (e) {}
  if (["alp", "kr", "both"].indexOf(state.mode) < 0) state.mode = "alp";
  var series = [];    // alpaca [{ts, equity}]
  var crySeries = []; // crypto, deposit-adjusted, ts in epoch seconds
  var cryCoarse = []; // frozen-engine equity.csv (5-min), for 2W/3W
  var cryFine = [];   // /api/crypto/equity_fine (live NAV tail)
  var cryTf = [];

  function save() { try { localStorage.setItem("qtpip", JSON.stringify(state)); } catch (e) {} }
  function key() { try { return localStorage.getItem("qt.apiKey") || ""; } catch (e) { return ""; } }
  function withKey(u) {
    var k = key();
    return k ? u + (u.indexOf("?") < 0 ? "?" : "&") + "key=" + encodeURIComponent(k) : u;
  }
  function toPts(rows) {
    var out = [];
    if (!rows) return out;
    for (var i = 0; i < rows.length; i++) {
      var r2 = rows[i];
      var ts = Date.parse(r2.ts) / 1000;
      var eq = +r2.equity;
      if (isFinite(ts) && isFinite(eq) && eq > 0) out.push({ ts: ts, equity: eq });
    }
    return out;
  }
  // PiP used to read ONLY /api/crypto/summary.equity (engine equity.csv).
  // The engine froze 2026-08-26 15:30Z, so a 1D window (now−86400) had
  // zero points and painted "no data in span" — while 3D/1W still showed
  // the older frozen series (Robert 2026-08-27: "pip window still doesn't
  // have data for 1D view"). Same merge the Book chart uses: coarse
  // history + equity_fine live tail, then transfer-collapse.
  function rebuildCry() {
    var src = cryCoarse;
    if (cryFine.length > 10) {
      var f0 = cryFine[0].ts;
      src = cryCoarse.filter(function (q) { return q.ts < f0; }).concat(cryFine);
    }
    // Copy — transfer collapse mutates equity; a second poll must not
    // add the same transfers onto already-shifted points.
    var pts = [];
    for (var i = 0; i < src.length; i++)
      pts.push({ ts: src[i].ts, equity: src[i].equity });
    if (pts.length < 2) return;
    var tf = cryTf.slice().sort(function (a, b) { return a.ts - b.ts; });
    var ti = tf.length - 1, after = 0;
    for (var k2 = pts.length - 1; k2 >= 0; k2--) {
      while (ti >= 0 && +tf[ti].ts > pts[k2].ts) { after += (+tf[ti].usd) || 0; ti--; }
      pts[k2] = { ts: pts[k2].ts, equity: pts[k2].equity + after };
    }
    crySeries = pts;
    if (state.on) draw();
    var fs = document.getElementById("qtpip-fs");
    if (fs) drawInto(fs, true);
  }
  function fetchSeries() {
    fetch(withKey("/api/equity/alpaca_live?period=1M"), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (Array.isArray(j) && j.length) { series = j; if (state.on) draw(); }
      }).catch(function () {});
    // Summary first: warms the server's live-NAV cache so equity_fine
    // can truncate the frozen-engine recorder tail (same order as Book).
    fetch(withKey("/api/crypto/summary"), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (!j) return;
        cryTf = j.transfers || [];
        cryCoarse = toPts(j.equity);
        rebuildCry();
      }).catch(function () {})
      .then(function () {
        return fetch(withKey("/api/crypto/equity_fine?hours=168"), { credentials: "same-origin" });
      })
      .then(function (r) { return r && r.ok ? r.json() : null; })
      .then(function (j) {
        if (j && j.rows && j.rows.length) cryFine = toPts(j.rows);
        rebuildCry();
      }).catch(function () {});
  }

  function el(tag, css, html) {
    var e = document.createElement(tag);
    if (css) e.style.cssText = css;
    if (html != null) e.innerHTML = html;
    return e;
  }

  var MONO = "font-family:ui-monospace,SFMono-Regular,Menlo,monospace;" +
             "font-variant-numeric:tabular-nums;";

  function draw() {
    var fs = document.getElementById("qtpip-fs");
    if (fs) { drawInto(fs, true); }
    var panel = document.getElementById("qtpip-panel");
    if (!panel) return;
    drawInto(panel, false);
  }

  function pathFor(pts, W, H, color, width) {
    var eq = pts.map(function (r) { return +r.equity; });
    var lo = Math.min.apply(null, eq), hi = Math.max.apply(null, eq);
    var pad = (hi - lo) * 0.08 || 0.01; lo -= pad; hi += pad;
    var t0 = +pts[0].ts, t1 = +pts[pts.length - 1].ts || t0 + 1;
    var d = pts.map(function (r, i) {
      var x = ((+r.ts - t0) / (t1 - t0) * (W - 4) + 2).toFixed(1);
      var y = (H - 3 - (+r.equity - lo) / (hi - lo) * (H - 6)).toFixed(1);
      return (i ? "L" : "M") + x + "," + y;
    }).join("");
    return '<path d="' + d + '" fill="none" stroke="' + color + '" stroke-width="' + width + '"/>';
  }
  function headSpan(pts, label, big) {
    var eq = pts.map(function (r) { return +r.equity; });
    var last = eq[eq.length - 1], first = eq[0];
    var chg = last - first, pct = first ? chg / first * 100 : 0;
    var col = chg >= 0 ? "#3fb950" : "#f85149";
    return '<span style="' + MONO + 'font-size:' + (big ? 15 : 11) + 'px;font-weight:700">' +
      (label ? label + " " : "") + "$" + last.toFixed(0) +
      '</span> <span style="' + MONO + 'font-size:10px;font-weight:600;color:' + col + '">' +
      (chg >= 0 ? "+" : "") + chg.toFixed(2) + " (" + (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%)</span>";
  }

  function drawInto(panel, fill) {
    var now = Date.now() / 1000;
    var secs = 0;
    for (var i = 0; i < SPANS.length; i++) if (SPANS[i][0] === state.span) secs = SPANS[i][1];
    var aPts = series.filter(function (r) { return +r.ts >= now - secs; });
    var kPts = crySeries.filter(function (r) { return +r.ts >= now - secs; });
    // Tiny SVG; 10s fine samples are ~8k pts/day. Keep first+last.
    function thin(pts, n) {
      if (pts.length <= n) return pts;
      var step = Math.ceil(pts.length / n), out = [];
      for (var i = 0; i < pts.length; i += step) out.push(pts[i]);
      if (out[out.length - 1] !== pts[pts.length - 1]) out.push(pts[pts.length - 1]);
      return out;
    }
    aPts = thin(aPts, 400);
    kPts = thin(kPts, 400);
    var head = panel.querySelector(".qtpip-head");
    var body = panel.querySelector(".qtpip-svg");
    var W = 288, H = 96;
    var paths = "", heads = [];
    if (state.mode !== "kr" && aPts.length >= 2) {
      paths += pathFor(aPts, W, H, "#ffd60a", state.mode === "alp" ? 1.6 : 1.3);
      heads.push(headSpan(aPts, state.mode === "both" ? "A" : "", state.mode !== "both"));
    }
    if (state.mode !== "alp" && kPts.length >= 2) {
      paths += pathFor(kPts, W, H, "#3fb950", state.mode === "kr" ? 1.6 : 1.3);
      heads.unshift(headSpan(kPts, state.mode === "both" ? "K" : "", state.mode !== "both"));
    }
    if (!paths) { body.innerHTML = ""; head.innerHTML = "no data in span"; return; }
    head.innerHTML = heads.join(" · ");
    body.innerHTML = '<svg width="100%" height="' + (fill ? '100%' : H) +
      '" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none">' + paths + "</svg>";
  }

  // System PiP layout (the Android app calls window.__qtSetPip(true) when
  // the OS puts the WebView in a floating window): chart-only, full-bleed.
  // NO buttons (Robert 2026-08-22): Android PiP windows never receive
  // touches, so chips there only LOOKED tappable. A passive one-line
  // "KR · 1D" label states what's shown; pick mode/span in the in-app
  // panel BEFORE entering PiP.
  window.__qtSetPip = function (on) {
    var fs = document.getElementById("qtpip-fs");
    if (on) {
      if (!fs) {
        fs = el("div",
          "position:fixed;inset:0;z-index:9999;background:#0d1117;" +
          "display:flex;flex-direction:column;padding:4px 6px");
        fs.id = "qtpip-fs";
        fs.innerHTML = '<div class="qtpip-head" style="line-height:1.1"></div>' +
          '<div class="qtpip-svg" style="flex:1;min-height:0"></div>' +
          '<div class="qtpip-modeline" style="font-size:9px;color:#6e7681;' +
          'letter-spacing:.08em;text-transform:uppercase"></div>';
        document.body.appendChild(fs);
      }
      var ml = fs.querySelector(".qtpip-modeline");
      if (ml) ml.textContent =
        (state.mode === "both" ? "ALP+KR" : state.mode.toUpperCase()) +
        " · " + state.span;
      drawInto(fs, true);
    } else if (fs) {
      fs.remove();
    }
  };

  function chipRow() {
    var modes = [["alp", "ALP", "#ffd60a"], ["kr", "KR", "#3fb950"], ["both", "BOTH", "#9aa3b2"]]
      .map(function (m) {
        var on = state.mode === m[0];
        return '<button data-mode="' + m[0] + '" style="border:1px solid ' +
          (on ? m[2] : "var(--bd,#30363d)") + ";background:none;color:" + m[2] +
          ';opacity:' + (on ? 1 : 0.5) +
          ';border-radius:4px;padding:1px 5px;font-size:9px;font-weight:700;cursor:pointer">' +
          m[1] + "</button>";
      }).join("");
    return modes + '<span style="width:6px"></span>' + SPANS.map(function (s) {
      var on = s[0] === state.span;
      return '<button data-span="' + s[0] + '" style="border:1px solid ' +
        (on ? "#3fb950" : "var(--bd,#30363d)") + ";background:none;color:" +
        (on ? "#3fb950" : "var(--muted,#8b949e)") +
        ';border-radius:4px;padding:1px 5px;font-size:9px;font-weight:600;cursor:pointer">' +
        s[0] + "</button>";
    }).join("");
  }

  function place(node, bubble) {
    var x = state.x, y = state.y;
    if (x == null || y == null) {
      node.style.right = "10px";
      node.style.bottom = bubble ? "86px" : "96px";
    } else {
      node.style.left = Math.max(2, Math.min(x, window.innerWidth - 60)) + "px";
      node.style.top = Math.max(2, Math.min(y, window.innerHeight - 60)) + "px";
    }
  }

  function drag(node) {
    var sx, sy, ox, oy, moved;
    node.addEventListener("pointerdown", function (ev) {
      ev.stopPropagation();
      if (ev.target.closest("button")) return;
      sx = ev.clientX; sy = ev.clientY; moved = false;
      var r = node.getBoundingClientRect(); ox = r.left; oy = r.top;
      function mv(e2) {
        var dx = e2.clientX - sx, dy = e2.clientY - sy;
        if (Math.abs(dx) + Math.abs(dy) > 6) moved = true;
        state.x = ox + dx; state.y = oy + dy;
        node.style.left = state.x + "px"; node.style.top = state.y + "px";
        node.style.right = "auto"; node.style.bottom = "auto";
      }
      function up() {
        document.removeEventListener("pointermove", mv);
        document.removeEventListener("pointerup", up);
        if (moved) save();
      }
      document.addEventListener("pointermove", mv);
      document.addEventListener("pointerup", up);
    });
  }

  function render() {
    var old = document.getElementById("qtpip-panel");
    var oldB = document.getElementById("qtpip-bubble");
    if (old) old.remove();
    if (oldB) oldB.remove();
    if (state.on) {
      var p = el("div",
        "position:fixed;z-index:60;width:300px;background:var(--panel,#161b22);" +
        "border:1px solid var(--bd,#30363d);border-radius:10px;padding:8px 10px;" +
        "box-shadow:0 4px 18px rgba(0,0,0,.5);touch-action:none;user-select:none");
      p.id = "qtpip-panel";
      var hasPip = typeof window.QTPip !== "undefined";
      var pipOn = false;
      try {
        if (hasPip && state.pipAuto != null) window.QTPip.setEnabled(!!state.pipAuto);
        pipOn = hasPip && window.QTPip.isEnabled();
      } catch (e) {}
      // ALWAYS render the toggle (Robert 2026-08-22: it "disappeared" when
      // the app bridge wasn't detected). Without the bridge it reads
      // "PiP n/a"; a poll below upgrades it if the bridge arrives late.
      var pipBtns =
        '<button class="qtpip-auto" style="border:1px solid ' +
        (pipOn ? "#3fb950" : "var(--bd,#30363d)") + ";background:none;color:" +
        (pipOn ? "#3fb950" : "var(--muted,#8b949e)") +
        ';border-radius:4px;padding:1px 5px;font-size:9px;font-weight:600;cursor:pointer" ' +
        'title="' + (hasPip ? "auto picture-in-picture when leaving the app"
                             : "PiP needs the Terminal 333 app (bridge not detected)") + '">' +
        (hasPip ? ("PiP " + (pipOn ? "on" : "off")) : "PiP n/a") + "</button>" +
        '<button class="qtpip-now" style="border:0;background:none;color:var(--muted,#8b949e);' +
        'font-size:13px;cursor:pointer;padding:0 2px" title="float now">⧉</button>';
      if (!hasPip) {
        var _tries = 0;
        var _probe = setInterval(function () {
          _tries++;
          if (typeof window.QTPip !== "undefined") {
            clearInterval(_probe);
            var btn = p.querySelector(".qtpip-auto");
            if (btn) {
              var on3 = false;
              try {
                if (state.pipAuto != null) window.QTPip.setEnabled(!!state.pipAuto);
                on3 = window.QTPip.isEnabled();
              } catch (e) {}
              btn.textContent = "PiP " + (on3 ? "on" : "off");
              btn.title = "auto picture-in-picture when leaving the app";
              btn.style.borderColor = on3 ? "#3fb950" : "var(--bd,#30363d)";
              btn.style.color = on3 ? "#3fb950" : "var(--muted,#8b949e)";
            }
          } else if (_tries > 40) clearInterval(_probe);
        }, 500);
      }
      p.innerHTML = '<div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">' +
        '<div class="qtpip-head" style="flex:1;min-width:0"></div>' + pipBtns +
        '<button class="qtpip-min" style="border:0;background:none;color:var(--muted,#8b949e);' +
        'font-size:14px;cursor:pointer;padding:0 2px">▁</button></div>' +
        '<div class="qtpip-svg"></div>' +
        '<div style="display:flex;gap:3px;flex-wrap:wrap;margin-top:5px">' + chipRow() + "</div>";
      place(p, false);
      document.body.appendChild(p);
      p.addEventListener("click", function (ev) {
        ev.stopPropagation();
        var b = ev.target.closest("button");
        if (!b) return;
        if (b.className === "qtpip-min") { state.on = false; save(); render(); return; }
        if (b.className === "qtpip-auto") {
          // no bridge → stay "PiP n/a" (2026-08-22: a swallowed exception
          // here used to repaint the button as a fake "off")
          if (typeof window.QTPip === "undefined") return;
          var on2 = false;
          try { on2 = !window.QTPip.isEnabled(); window.QTPip.setEnabled(on2); } catch (e) { return; }
          state.pipAuto = on2; save();   // survives app relaunches
          b.style.borderColor = on2 ? "#3fb950" : "var(--bd,#30363d)";
          b.style.color = on2 ? "#3fb950" : "var(--muted,#8b949e)";
          b.textContent = "PiP " + (on2 ? "on" : "off");
          return;
        }
        if (b.className === "qtpip-now") {
          try { window.QTPip.enterNow(); } catch (e) {}
          return;
        }
        var md = b.getAttribute("data-mode");
        if (md) {
          state.mode = md; save();
          p.querySelector("div:last-child").innerHTML = chipRow();
          draw();
          return;
        }
        var sp = b.getAttribute("data-span");
        if (sp) {
          state.span = sp; save();
          p.querySelector("div:last-child").innerHTML = chipRow();
          draw();
        }
      });
      drag(p);
      draw();
    } else {
      var b2 = el("div",
        "position:fixed;z-index:60;width:40px;height:40px;border-radius:50%;" +
        "background:var(--panel,#161b22);border:1px solid var(--bd,#30363d);" +
        "display:flex;align-items:center;justify-content:center;font-size:19px;" +
        "box-shadow:0 3px 10px rgba(0,0,0,.45);cursor:pointer;touch-action:none;user-select:none");
      b2.id = "qtpip-bubble";
      b2.textContent = "📈";
      place(b2, true);
      b2.addEventListener("click", function (ev) {
        ev.stopPropagation(); state.on = true; save(); render();
      });
      drag(b2);
      document.body.appendChild(b2);
    }
  }

  function keepAlive() {
    // SPA re-renders never touch body-level nodes, but belt-and-suspenders
    if (!document.getElementById("qtpip-panel") && !document.getElementById("qtpip-bubble")) {
      render();
    }
  }

  function init() {
    render();
    fetchSeries();
    setInterval(fetchSeries, POLL_MS);
    setInterval(keepAlive, 3000);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
