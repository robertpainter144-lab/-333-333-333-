/* Quarki333 Terminal — owner-private Catalog tab.
 * Each card opens a DATA CENTER: hero NAV + deltas, an interactive
 * performance chart (1D/1W/1M/3M/6M/ALL ranges; tap or drag anywhere to
 * scrub a date indicator), and holdings/allocation that recompute LIVE for
 * the scrubbed date from per-ticker relative price history (backfill v2).
 * Injected server-side only for owner-mode (no-XFF) sessions. */
(function () {
  "use strict";
  var POLL_MS = 30000;
  var open = false, data = null, timer = null, err = null;
  var openCards = {}, dcState = {};

  var RANGES = [["1D", 1], ["1W", 7], ["1M", 31], ["3M", 92], ["6M", 183], ["ALL", 1e9]];

  var css = [
    ".qtcat-overlay{position:fixed;top:0;right:0;left:0;z-index:25;overflow-y:auto;",
    "background:var(--bg);color:var(--fg);padding:12px;",
    "bottom:calc(52px + env(safe-area-inset-bottom));-webkit-font-smoothing:antialiased}",
    "@media(min-width:768px){.qtcat-overlay{left:11rem;bottom:0}}",
    ".qtcat-card{border:1px solid var(--bd);background:var(--panel);border-radius:8px;",
    "padding:12px;margin:0 auto 10px;max-width:72rem}",
    ".qtcat-qc{border-color:#ffd60a66}",
    ".qtcat-num{font-family:ui-monospace,SFMono-Regular,Menlo,Roboto Mono,monospace;",
    "font-variant-numeric:tabular-nums;letter-spacing:-.01em}",
    ".qtcat-pill{display:inline-block;border:1px solid var(--bd);border-radius:9999px;",
    "padding:1px 8px;font-size:9px;font-weight:700;letter-spacing:.08em}",
    ".qtcat-muted{color:var(--muted)}.qtcat-faint{color:var(--faint,#6e7681)}",
    ".qtcat-pos{color:#3fb950}.qtcat-neg{color:#f85149}",
    ".qtcat-gold{color:#ffd60a}.qtcat-amber{color:#d29922}",
    ".qtcat-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px;flex-wrap:wrap}",
    ".qtcat-sec{margin-top:12px;border-top:1px solid var(--bd);padding-top:10px}",
    ".qtcat-h{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}",
    ".qtcat-stats{display:flex;gap:14px;flex-wrap:wrap;font-size:11px}",
    ".qtcat-stats b{display:block;font-size:13px;font-weight:600}",
    ".qtcat-table{width:100%;border-collapse:collapse;font-size:11px}",
    ".qtcat-table td,.qtcat-table th{padding:3px 0;border-top:1px solid var(--bd);text-align:right}",
    ".qtcat-table td:first-child,.qtcat-table th:first-child{text-align:left}",
    ".qtcat-table th{font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border-top:0}",
    ".qtcat-bar{display:flex;height:16px;width:100%;gap:2px;overflow:hidden;border-radius:4px}",
    ".qtcat-tabs{display:flex;gap:2px;border:1px solid var(--bd);border-radius:6px;padding:2px}",
    ".qtcat-tab{border:0;background:none;color:var(--muted);font-size:10px;font-weight:600;",
    "padding:2px 7px;border-radius:4px;cursor:pointer}",
    ".qtcat-tab.on{background:var(--panel2);color:var(--fg)}",
    ".qtcat-chartwrap{touch-action:none;user-select:none;-webkit-user-select:none;cursor:crosshair}",
    ".qtcat-pnl{margin-left:auto;text-align:right;white-space:nowrap;flex-shrink:0}",
    ".qtcat-navbtn-active{color:var(--fg)!important;font-weight:600}"
  ].join("");

  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fmtPct(v, signed) {
    if (v == null || isNaN(v)) return "—";
    var s = Number(v).toFixed(2) + "%";
    return signed && v > 0 ? "+" + s : s;
  }
  function pnlCls(v) { return v > 0 ? "qtcat-pos" : v < 0 ? "qtcat-neg" : "qtcat-muted"; }
  function verdictCls(v) {
    if (v === "PROMISING" || v === "PROVEN") return "qtcat-pos";
    if (v === "FAILING" || v === "REJECTED" || v === "LOSING") return "qtcat-neg";
    return "qtcat-amber";
  }
  function symColor(sym) {
    var h = 0;
    for (var i = 0; i < sym.length; i++) h = (h * 31 + sym.charCodeAt(i)) % 360;
    return "hsl(" + h + " 55% 55%)";
  }
  function arrow(v) { return v > 0 ? "▲" : v < 0 ? "▼" : "•"; }
  function statHtml(label, val, cls) {
    return '<span><span class="qtcat-muted">' + label + '</span><b class="qtcat-num ' +
      (cls || "") + '">' + val + "</b></span>";
  }
  function busRow(label, value) {
    return value
      ? '<div style="display:flex;gap:8px;margin-top:4px;font-size:11px">' +
        '<span class="qtcat-muted" style="min-width:86px;text-transform:uppercase;' +
        'font-size:9px;letter-spacing:.08em;padding-top:1px">' + label + "</span>" +
        "<span>" + esc(value) + "</span></div>"
      : "";
  }

  /* ---- series model: backfill v2 (dates/nav/rel arrays) + forward curve ---- */
  function buildSeries(curveInfo) {
    var bf = curveInfo && curveInfo.backfill;
    var fwd = (curveInfo && curveInfo.curve) || {};
    var dates = [], navs = [], rel = {}, bfLen = 0;
    if (bf && bf.dates && bf.nav) {
      dates = bf.dates.slice();
      navs = bf.nav.slice();
      rel = bf.rel || {};
      bfLen = dates.length;
    }
    Object.keys(fwd).sort().forEach(function (d) {
      var j = dates.indexOf(d);
      if (j >= 0) navs[j] = fwd[d];          // live mark wins on shared dates
      else { dates.push(d); navs.push(fwd[d]); }
    });
    return { dates: dates, navs: navs, rel: rel, bfLen: bfLen,
             inception: curveInfo && curveInfo.inception,
             weights: (curveInfo && curveInfo.weights) || {},
             shares: (curveInfo && curveInfo.shares) || {} };
  }
  function relAt(S, sym, i) {
    var a = S.rel[sym];
    if (a && i < a.length) return a[i];
    return 1.0; // forward region ≈ latest composition pricing
  }
  function weightsAt(S, i) {
    var syms = Object.keys(S.weights);
    var raw = {}, tot = 0;
    syms.forEach(function (s) {
      var v = S.weights[s] * relAt(S, s, i);
      raw[s] = v; tot += v;
    });
    if (tot <= 0) return S.weights;
    var out = {};
    syms.forEach(function (s) { out[s] = raw[s] / tot; });
    return out;
  }
  function rangeStart(S, rangeId) {
    var days = 1e9;
    for (var i = 0; i < RANGES.length; i++) if (RANGES[i][0] === rangeId) days = RANGES[i][1];
    if (days >= 1e9 || !S.dates.length) return 0;
    var end = new Date(S.dates[S.dates.length - 1] + "T00:00:00");
    var cut = new Date(end.getTime() - days * 86400e3);
    var cs = cut.toISOString().slice(0, 10);
    for (var j = 0; j < S.dates.length; j++) if (S.dates[j] >= cs) return j;
    return Math.max(0, S.dates.length - 2);
  }

  /* ---- interactive data center ---- */
  function renderChartSVG(S, st, i0) {
    var n = S.dates.length;
    var idx = st.idx;
    var dates = S.dates.slice(i0), navs = S.navs.slice(i0);
    if (navs.length < 2)
      return '<div class="qtcat-faint" style="font-size:10px;padding:12px 0">Not enough points in this range yet.</div>';
    var lo = Math.min.apply(null, navs), hi = Math.max.apply(null, navs);
    var pad = (hi - lo) * 0.08 || 1;
    lo -= pad; hi += pad;
    var W = 640, H = 170, L = 46, B = 16;
    function X(i) { return L + i / (navs.length - 1) * (W - L - 6); }
    function Y(v) { return (H - B) - (v - lo) / (hi - lo) * (H - B - 8); }
    var ptsArr = navs.map(function (v, i) { return X(i).toFixed(1) + "," + Y(v).toFixed(1); });
    var color = navs[navs.length - 1] >= navs[0] ? "#3fb950" : "#f85149";
    var area = "M" + X(0).toFixed(1) + "," + (H - B) + " L" + ptsArr.join(" L") +
      " L" + X(navs.length - 1).toFixed(1) + "," + (H - B) + " Z";
    var ticks = [lo + pad, (lo + hi) / 2, hi - pad].map(function (v) {
      return '<text x="' + (L - 6) + '" y="' + (Y(v) + 3).toFixed(1) +
        '" text-anchor="end" font-size="9" fill="var(--muted)" font-family="ui-monospace,Menlo,monospace">' +
        v.toFixed(1) + "</text>";
    }).join("");
    var xlab = '<text x="' + L + '" y="' + (H - 2) + '" font-size="9" fill="var(--muted)" font-family="ui-monospace,Menlo,monospace">' +
      esc(dates[0]) + '</text><text x="' + (W - 6) + '" y="' + (H - 2) +
      '" text-anchor="end" font-size="9" fill="var(--muted)" font-family="ui-monospace,Menlo,monospace">' +
      esc(dates[dates.length - 1]) + "</text>";
    var marker = "";
    if (S.inception) {
      var gi = -1;
      for (var k = 0; k < dates.length; k++) if (dates[k] >= S.inception) { gi = k; break; }
      if (gi >= 0) {
        var mx = X(gi).toFixed(1);
        marker = '<line x1="' + mx + '" x2="' + mx + '" y1="6" y2="' + (H - B) +
          '" stroke="var(--muted)" stroke-dasharray="4 3" stroke-opacity=".6"/>' +
          '<text x="' + (Number(mx) - 4) + '" y="13" text-anchor="end" font-size="9" fill="var(--muted)" ' +
          'font-family="ui-monospace,Menlo,monospace">live ▸</text>';
      }
    }
    var scrub = "";
    if (idx != null && idx >= i0 && idx < n) {
      var li = idx - i0;
      var sx = X(li).toFixed(1), sy = Y(navs[li]).toFixed(1);
      scrub = '<line x1="' + sx + '" x2="' + sx + '" y1="6" y2="' + (H - B) +
        '" stroke="#ffd60a" stroke-width="1.4"/>' +
        '<circle cx="' + sx + '" cy="' + sy + '" r="4" fill="#ffd60a"/>';
    }
    return '<svg viewBox="0 0 ' + W + " " + H + '" style="width:100%;display:block" data-l="' + L +
      '" data-n="' + navs.length + '" data-i0="' + i0 + '">' +
      '<path d="' + area + '" fill="' + color + '" fill-opacity=".08"/>' +
      ticks + xlab + marker +
      '<polyline fill="none" stroke="' + color + '" stroke-width="1.8" points="' + ptsArr.join(" ") + '"/>' +
      scrub + "</svg>";
  }

  function heroHTML(S, p, meta, idx) {
    var n = S.dates.length;
    if (!n) return "";
    var i = idx == null ? n - 1 : idx;
    var nav = S.navs[i], date = S.dates[i];
    var sinceInc = nav - 100;
    var dayD = i > 0 ? (nav / S.navs[i - 1] - 1) * 100 : null;
    var vsNow = idx != null ? (S.navs[n - 1] / nav - 1) * 100 : null;
    return '<div style="padding-top:6px">' +
      '<span class="qtcat-num" style="font-size:30px;font-weight:700;letter-spacing:-.02em">' +
      nav.toFixed(2) + "</span>" +
      '<span class="qtcat-muted" style="font-size:11px"> NAV · ' + esc(date) +
      (idx != null ? ' <button class="qtcat-pill qtcat-nowbtn" style="cursor:pointer">● now</button>' : "") +
      "</span>" +
      '<div style="display:flex;gap:20px;flex-wrap:wrap;margin-top:6px">' +
      '<div><div class="qtcat-h" style="margin-bottom:1px">vs inception (' +
      esc(S.inception || "?") + ", 100.00)</div>" +
      '<div class="qtcat-num ' + pnlCls(sinceInc) + '" style="font-weight:600">' +
      arrow(sinceInc) + " " + fmtPct(Math.abs(sinceInc)) + "</div></div>" +
      (dayD != null ? '<div><div class="qtcat-h" style="margin-bottom:1px">day change</div>' +
        '<div class="qtcat-num ' + pnlCls(dayD) + '" style="font-weight:600">' +
        arrow(dayD) + " " + fmtPct(Math.abs(dayD)) + "</div></div>" : "") +
      (vsNow != null ? '<div><div class="qtcat-h" style="margin-bottom:1px">now vs this date</div>' +
        '<div class="qtcat-num ' + pnlCls(vsNow) + '" style="font-weight:600">' +
        arrow(vsNow) + " " + fmtPct(Math.abs(vsNow)) + "</div></div>" : "") +
      "</div></div>";
  }

  function allocHTML(S, idx) {
    var n = S.dates.length;
    if (!n || !Object.keys(S.weights).length) return "";
    var i = idx == null ? n - 1 : idx;
    var w = weightsAt(S, i); // rel arrays cover the backfill region; forward dates → latest mix
    var nav = S.navs[i];
    var syms = Object.keys(w).sort(function (a, b) { return w[b] - w[a]; });
    var segs = syms.map(function (s) {
      return '<div title="' + esc(s) + " " + (w[s] * 100).toFixed(1) + '%" style="width:' +
        (w[s] * 100) + "%;background:" + symColor(s) + ';min-width:2px"></div>';
    }).join("");
    var legend = syms.slice(0, 10).map(function (s) {
      return '<span style="display:inline-flex;align-items:center;gap:4px;font-size:10px" class="qtcat-muted">' +
        '<span style="width:8px;height:8px;border-radius:2px;background:' + symColor(s) + '"></span>' +
        esc(s) + "</span>";
    }).join(" ");
    var rows = syms.map(function (s) {
      return "<tr><td><b>" + esc(s) + "</b></td>" +
        '<td class="qtcat-num">' + (S.shares[s] != null ? S.shares[s].toFixed(4) : "—") + "</td>" +
        '<td class="qtcat-num">' + (nav * w[s]).toFixed(2) + "</td>" +
        '<td class="qtcat-num">' + (w[s] * 100).toFixed(2) + "%</td></tr>";
    }).join("");
    return '<div class="qtcat-h">Holdings & allocation — as of ' + esc(S.dates[i]) +
      (idx == null ? " (now)" : "") + "</div>" +
      '<div class="qtcat-bar">' + segs + "</div>" +
      '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:5px">' + legend + "</div>" +
      '<table class="qtcat-table" style="margin-top:8px"><tr><th>Symbol</th><th>Index shares</th>' +
      "<th>Value @ date</th><th>Weight @ date</th></tr>" + rows + "</table>";
  }

  function ddBarHTML(S) {
    if (S.navs.length < 2) return "";
    var peak = -1e9, maxDD = 0, cur = 0;
    for (var i = 0; i < S.navs.length; i++) {
      var v = S.navs[i];
      if (v > peak) peak = v;
      var dd = (peak - v) / peak * 100;
      if (dd > maxDD) maxDD = dd;
      cur = dd;
    }
    var frac = Math.min(1, cur / 10);
    var color = cur >= 7 ? "#f85149" : cur >= 4 ? "#d29922" : "#3fb950";
    return '<div class="qtcat-sec"><div class="qtcat-h">Drawdown vs peak NAV ' +
      '<span class="qtcat-num" style="float:right;color:' + color + '">' +
      cur.toFixed(2) + "% now · " + maxDD.toFixed(2) + "% max</span></div>" +
      '<div style="height:8px;background:var(--panel2);border-radius:4px;overflow:hidden">' +
      '<div style="height:100%;width:' + (frac * 100).toFixed(1) + "%;background:" + color +
      ';border-radius:4px"></div></div></div>';
  }

  function activityHTML(curveInfo) {
    var curve = (curveInfo && curveInfo.curve) || {};
    var keys = Object.keys(curve).sort().reverse();
    var items = [];
    if (curveInfo && curveInfo.last_update)
      items.push('<li style="padding:3px 0" class="qtcat-muted">📝 holdings hand-updated ' +
        esc(curveInfo.last_update) + "</li>");
    keys.slice(0, 14).forEach(function (k, i) {
      var v = curve[k], pk = keys[i + 1];
      var d = pk != null ? (v / curve[pk] - 1) * 100 : null;
      items.push('<li style="display:flex;justify-content:space-between;padding:3px 0;border-top:1px solid var(--bd)">' +
        '<span class="qtcat-num">' + esc(k) + '</span><span class="qtcat-num">NAV ' + v.toFixed(2) +
        (d != null ? ' <span class="' + pnlCls(d) + '">' + arrow(d) + " " + fmtPct(Math.abs(d)) + "</span>" : "") +
        "</span></li>");
    });
    if (curveInfo && curveInfo.inception)
      items.push('<li style="padding:3px 0;border-top:1px solid var(--bd)" class="qtcat-muted">🏁 ' +
        esc(curveInfo.inception) + " — index struck at 100.00 (live tracking begins)</li>");
    if (!items.length) return "";
    return '<div class="qtcat-sec"><div class="qtcat-h">Activity log (live daily marks)</div>' +
      '<ul style="list-style:none;margin:0;padding:0;font-size:11px">' + items.join("") + "</ul></div>";
  }

  function buildDataCenter(p, curveInfo, meta) {
    var S = buildSeries(curveInfo);
    var st = dcState[p.name] || (dcState[p.name] = { range: "ALL", idx: null });
    var root = el("div");

    var heroEl = el("div");
    var chartSec = el("div", null);
    var allocEl = el("div");
    var chartWrap = el("div", "qtcat-chartwrap");

    function i0() { return rangeStart(S, st.range); }
    function redraw() {
      heroEl.innerHTML = heroHTML(S, p, meta, st.idx);
      chartWrap.innerHTML = renderChartSVG(S, st, i0());
      allocEl.innerHTML = allocHTML(S, st.idx);
      var nb = heroEl.querySelector(".qtcat-nowbtn");
      if (nb) nb.addEventListener("click", function (ev) {
        ev.stopPropagation();
        st.idx = null; redraw();
      });
    }

    // range tabs
    var tabs = el("div", "qtcat-tabs");
    RANGES.forEach(function (r) {
      var b = el("button", "qtcat-tab" + (st.range === r[0] ? " on" : ""), r[0]);
      b.addEventListener("click", function (ev) {
        ev.stopPropagation();
        st.range = r[0]; st.idx = null;
        tabs.querySelectorAll(".qtcat-tab").forEach(function (t) {
          t.classList.toggle("on", t.textContent === r[0]);
        });
        redraw();
      });
      tabs.appendChild(b);
    });

    // scrub: tap or drag anywhere on the chart
    var raf = null;
    function scrubTo(clientX) {
      var svg = chartWrap.querySelector("svg");
      if (!svg) return;
      var rect = svg.getBoundingClientRect();
      var L = Number(svg.getAttribute("data-l")), nvis = Number(svg.getAttribute("data-n"));
      var base = Number(svg.getAttribute("data-i0"));
      var plotW = rect.width * (640 - L - 6) / 640;
      var lpx = rect.width * L / 640;
      var f = (clientX - rect.left - lpx) / Math.max(plotW, 1);
      var li = Math.round(f * (nvis - 1));
      li = Math.max(0, Math.min(nvis - 1, li));
      var idx = base + li;
      if (idx === st.idx) return;
      st.idx = idx;
      if (raf) return;
      raf = requestAnimationFrame(function () { raf = null; redraw(); });
    }
    var scrubbing = false;
    chartWrap.addEventListener("pointerdown", function (ev) {
      ev.stopPropagation(); scrubbing = true;
      chartWrap.setPointerCapture && chartWrap.setPointerCapture(ev.pointerId);
      scrubTo(ev.clientX);
    });
    chartWrap.addEventListener("pointermove", function (ev) {
      if (scrubbing) { ev.stopPropagation(); scrubTo(ev.clientX); }
    });
    ["pointerup", "pointercancel"].forEach(function (t) {
      chartWrap.addEventListener(t, function () { scrubbing = false; });
    });
    chartWrap.addEventListener("click", function (ev) { ev.stopPropagation(); });

    root.appendChild(el("div", "qtcat-sec", "")).appendChild(heroEl);
    if (meta && meta.record)
      root.appendChild(el("div", null,
        '<div class="qtcat-sec"><div class="qtcat-h">Real track record</div>' +
        '<div style="font-size:11px;line-height:1.5">' + esc(meta.record) + "</div></div>"));
    if (meta && meta.detail)
      root.appendChild(el("div", null,
        '<div class="qtcat-sec"><div class="qtcat-h">What this book is doing</div>' +
        '<div style="font-size:12px;line-height:1.55">' + esc(meta.detail) + "</div>" +
        busRow("consumes", meta.consumes) + busRow("emits", meta.emits) +
        busRow("cadence", meta.cadence) + "</div>"));

    var chartHead = el("div", "qtcat-row",
      '<div class="qtcat-h" style="margin:0">Performance — virtual index (base 100)</div>');
    chartHead.appendChild(tabs);
    chartSec.className = "qtcat-sec";
    chartSec.appendChild(chartHead);
    chartSec.appendChild(chartWrap);
    chartSec.appendChild(el("div", "qtcat-faint",
      "tap or drag on the chart to inspect any date — holdings below follow · left of ▸ = composition marked backward · right = live"));
    chartSec.lastChild.style.cssText = "font-size:9px;margin-top:3px";
    root.appendChild(chartSec);
    root.appendChild(el("div", null, ddBarHTML(S)));

    var allocSec = el("div", "qtcat-sec");
    allocSec.appendChild(allocEl);
    root.appendChild(allocSec);

    var stats = [];
    stats.push(statHtml("days of history", S.dates.length));
    stats.push(statHtml("live marks", Object.keys((curveInfo && curveInfo.curve) || {}).length));
    stats.push(statHtml("trades", p.n_closed_trades != null ? p.n_closed_trades : "—"));
    stats.push(statHtml("win", p.win_rate != null ? p.win_rate.toFixed(1) + "%" : "—"));
    stats.push(statHtml("sharpe", p.sharpe != null ? p.sharpe.toFixed(3) : "—"));
    root.appendChild(el("div", null,
      '<div class="qtcat-sec"><div class="qtcat-h">Scoreboard stats</div>' +
      '<div class="qtcat-stats">' + stats.join("") + "</div></div>"));

    root.appendChild(el("div", null, activityHTML(curveInfo)));
    if (meta && meta.note)
      root.appendChild(el("div", "qtcat-muted",
        '<div class="qtcat-sec" style="font-size:10px;font-style:italic">' + esc(meta.note) + "</div>"));

    redraw();
    return root;
  }

  function pilotCard(p, curveInfo, isQC, meta) {
    meta = meta || {};
    var name = p.name.replace(/^autopilot_cat:/, "").replace(/_/g, " ");
    var card = el("div", "qtcat-card" + (isQC ? " qtcat-qc" : ""));
    card.style.cursor = "pointer";
    var isOpen = !!openCards[p.name];

    var head = el("div", "qtcat-row");
    head.appendChild(el("div", null,
      (isQC ? '<span class="qtcat-gold">★</span> ' : "") +
      "<b>" + esc(name) + "</b> " +
      '<span class="qtcat-pill ' + verdictCls(p.verdict) + '">' + esc(p.verdict || "?") + "</span>" +
      (meta.source ? ' <span class="qtcat-pill qtcat-muted">' + esc(meta.source) + "</span>" : "") +
      (isQC ? ' <span class="qtcat-muted" style="font-size:10px">reference — real paper record</span>' : "")));
    var S0 = buildSeries(curveInfo);
    var headPnl, headLabel;
    if (isQC || S0.navs.length < 2) {
      headPnl = p.total_pnl_pct;
      headLabel = isQC ? "total" : "";
    } else {
      // Trailing performance over the whole backfilled year — far more
      // informative than the ~0% since-inception on day one of live tracking.
      headPnl = (S0.navs[S0.navs.length - 1] / S0.navs[0] - 1) * 100;
      headLabel = "1y";
    }
    head.appendChild(el("div", "qtcat-num qtcat-pnl " + pnlCls(headPnl),
      "<b style='font-size:16px'>" + fmtPct(headPnl, true) + "</b>" +
      (headLabel ? ' <span class="qtcat-muted" style="font-size:9px">' + headLabel + "</span>" : "")));
    card.appendChild(head);

    var blurb = isQC
      ? "Your QuantConnect leaderboard portfolio's real paper record — the bar every cataloged pilot has to beat."
      : meta.blurb;
    if (blurb) {
      var bl = el("div", "qtcat-muted", esc(blurb));
      bl.style.cssText = "font-size:11px;line-height:1.45;margin-top:6px";
      card.appendChild(bl);
    }

    var body = el("div");
    body.style.display = isOpen ? "block" : "none";
    if (isOpen) body.appendChild(buildDataCenter(p, curveInfo, meta));
    card.appendChild(body);

    var aff = el("div", "qtcat-muted", isOpen ? "close data center ▴" : "open data center ›");
    aff.style.cssText = "font-size:10px;margin-top:8px;text-align:right";
    card.appendChild(aff);

    card.addEventListener("click", function () {
      openCards[p.name] = !openCards[p.name];
      var o = openCards[p.name];
      if (o && !body.firstChild) body.appendChild(buildDataCenter(p, curveInfo, meta));
      body.style.display = o ? "block" : "none";
      aff.textContent = o ? "close data center ▴" : "open data center ›";
    });
    return card;
  }

  function render() {
    var ov = document.getElementById("qtcat-overlay");
    if (!ov) return;
    ov.innerHTML = "";
    var hdr = el("div", "qtcat-card",
      '<div class="qtcat-row"><div><b>🗂 Catalog — the benchmark shelf.</b></div>' +
      (data ? "<span class='qtcat-num qtcat-muted' style='font-size:10px'>as of " +
        esc(data.ts || "") + "</span>" : "") + "</div>" +
      '<div class="qtcat-muted" style="font-size:11px;line-height:1.5;margin-top:4px">' +
      "Famous managers' portfolios (13F/ETF/hand-entered) tracked as virtual indexes from public prices, " +
      "raced head-to-head against your QC engine. Display-only — these can never receive real money. " +
      "Tap any card to open its data center; owner-private, never on the shared Terminal.</div>");
    ov.appendChild(hdr);
    if (err) { ov.appendChild(el("div", "qtcat-card qtcat-neg", "catalog fetch failed: " + esc(err))); return; }
    if (!data) { ov.appendChild(el("div", "qtcat-card qtcat-muted", "loading…")); return; }
    if (data.qc_reference) ov.appendChild(pilotCard(data.qc_reference, null, true, null));
    function yrPerf(p) {
      var ci = (data.curves || {})[p.name.replace(/^autopilot_cat:/, "")];
      var S = buildSeries(ci);
      if (S.navs.length < 2) return p.total_pnl_pct || 0;
      return (S.navs[S.navs.length - 1] / S.navs[0] - 1) * 100;
    }
    var pilots = (data.pilots || []).slice().sort(function (a, b) {
      return yrPerf(b) - yrPerf(a);
    });
    pilots.forEach(function (p) {
      var short = p.name.replace(/^autopilot_cat:/, "");
      ov.appendChild(pilotCard(p, (data.curves || {})[short], false, (data.meta || {})[short]));
    });
    if (!pilots.length)
      ov.appendChild(el("div", "qtcat-card qtcat-muted",
        "No pilots yet — add them to scanner/state/autopilot_catalog.json"));
    ov.appendChild(el("div", null,
      "<div class='qtcat-muted' style='font-size:10px;text-align:center;padding:4px 0 8px'>" +
      "1y composition history + forward daily marks · owner-private</div>"));
  }

  function fetchData() {
    var hdrs = { accept: "application/json" };
    try { var k = localStorage.getItem("qt.apiKey"); if (k) hdrs["x-api-key"] = k; } catch (e) { /* noop */ }
    fetch("/api/catalog", { headers: hdrs })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) { data = d; err = null; render(); })
      .catch(function (e) { err = e.message; render(); });
  }

  function setOpen(v) {
    open = v;
    var ov = document.getElementById("qtcat-overlay");
    if (open) {
      if (!ov) {
        ov = el("div", "qtcat-overlay");
        ov.id = "qtcat-overlay";
        document.body.appendChild(ov);
      }
      render();
      fetchData();
      if (!timer) timer = setInterval(fetchData, POLL_MS);
    } else {
      if (ov) ov.remove();
      if (timer) { clearInterval(timer); timer = null; }
    }
    document.querySelectorAll(".qtcat-navbtn").forEach(function (b) {
      b.classList.toggle("qtcat-navbtn-active", open);
    });
  }

  function makeBtn(mobile) {
    var b = el("button", "qtcat-navbtn");
    if (mobile) {
      b.style.cssText = "flex:1 1 0%;display:flex;flex-direction:column;align-items:center;" +
        "gap:2px;padding:8px 0;font-size:10px;background:none;border:0;color:var(--muted)";
      b.innerHTML = "<span style='font-size:16px;line-height:1'>🗂</span>Catalog";
    } else {
      b.style.cssText = "display:flex;align-items:center;gap:8px;border-radius:4px;" +
        "padding:8px 12px;font-size:14px;background:none;border:0;color:var(--muted);" +
        "text-align:left;width:100%;cursor:pointer";
      b.innerHTML = "<span>🗂</span> Catalog";
    }
    b.addEventListener("click", function () { setOpen(!open); });
    return b;
  }

  function mount() {
    var mobileNav = document.querySelector("nav.fixed.inset-x-0.bottom-0");
    if (mobileNav && !mobileNav.querySelector(".qtcat-navbtn"))
      mobileNav.appendChild(makeBtn(true));
    var rail = document.querySelector("nav.hidden .flex-1.flex-col, nav .flex.flex-1.flex-col");
    if (rail && !rail.querySelector(".qtcat-navbtn"))
      rail.appendChild(makeBtn(false));
  }

  function init() {
    var st = document.createElement("style");
    st.textContent = css;
    document.head.appendChild(st);
    document.addEventListener("click", function (ev) {
      if (!open) return;
      var a = ev.target && ev.target.closest && ev.target.closest("a");
      if (a) setOpen(false);
    }, true);
    mount();
    setInterval(mount, 2000);
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
