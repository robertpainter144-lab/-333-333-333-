/* Quarki333 Terminal — owner-private Crypto tab (D39 Kraken sleeve).
 * Adds the 7th nav item, a dedicated crypto page, and feeds the sleeve
 * equity curve into the Book chart's overlay hook (green line, green $
 * axis on the right edge, yellow time axis along the top — pan/zoom is
 * shared with the main line because both draw on the same time domain).
 * Injected server-side only for owner-mode (no-XFF / owner-key) sessions;
 * /api/crypto/summary is owner-gated, so guests never see this data. */
(function () {
  "use strict";
  var POLL_MS = 30000;
  var open = false, data = null, err = null, alp = null, tick = null;
  var GREEN = "#3fb950", YELLOW = "#ffd60a", RED = "#f85149";

  var css = [
    ".qtcy-overlay{position:fixed;top:0;right:0;left:0;z-index:25;overflow-y:auto;",
    "background:var(--bg);color:var(--fg);padding:12px;",
    "bottom:calc(52px + env(safe-area-inset-bottom));-webkit-font-smoothing:antialiased}",
    "@media(min-width:768px){.qtcy-overlay{left:11rem;bottom:0}}",
    ".qtcy-card{border:1px solid var(--bd);background:var(--panel);border-radius:8px;",
    "padding:12px;margin:0 auto 10px;max-width:72rem}",
    ".qtcy-num{font-family:ui-monospace,SFMono-Regular,Menlo,Roboto Mono,monospace;",
    "font-variant-numeric:tabular-nums;letter-spacing:-.01em}",
    ".qtcy-muted{color:var(--muted)}.qtcy-faint{color:var(--faint,#6e7681)}",
    ".qtcy-pos{color:#3fb950}.qtcy-neg{color:#f85149}.qtcy-gold{color:#ffd60a}",
    ".qtcy-h{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}",
    ".qtcy-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px;flex-wrap:wrap}",
    ".qtcy-sec{margin-top:12px;border-top:1px solid var(--bd);padding-top:10px}",
    ".qtcy-stats{display:flex;gap:14px;flex-wrap:wrap;font-size:11px}",
    ".qtcy-stats b{display:block;font-size:13px;font-weight:600}",
    ".qtcy-table{width:100%;border-collapse:collapse;font-size:11px}",
    ".qtcy-table td,.qtcy-table th{padding:3px 0;border-top:1px solid var(--bd);text-align:right}",
    ".qtcy-table td:first-child,.qtcy-table th:first-child{text-align:left}",
    ".qtcy-table th{font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border-top:0}",
    ".qtcy-pill{display:inline-block;border:1px solid var(--bd);border-radius:9999px;",
    "padding:1px 8px;font-size:9px;font-weight:700;letter-spacing:.08em}",
    ".qtcy-log{font-family:ui-monospace,Menlo,monospace;font-size:9px;line-height:1.5;",
    "white-space:pre-wrap;word-break:break-all;color:var(--muted)}",
    ".qtcy-navbtn-active{color:var(--fg)!important;font-weight:600}",
    ".qtcy-tribar{display:flex;gap:8px;background:var(--panel);padding:10px 12px 9px;margin-bottom:12px}",
    ".qtcy-tribar>div{flex:1 1 0;min-width:0}",
    ".qtcy-tri-label{font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:1px}",
    ".qtcy-tri-val{font-family:ui-monospace,SFMono-Regular,Menlo,Roboto Mono,monospace;",
    "font-variant-numeric:tabular-nums;font-size:20px;font-weight:700;letter-spacing:-.02em;white-space:nowrap}",
    ".qtcy-bar{display:flex;gap:2px;height:16px;border-radius:4px;overflow:hidden;margin-bottom:4px}",
    ".qtcy-bar>div{min-width:2px;display:flex;align-items:center;justify-content:center;overflow:hidden}",
    ".qtcy-cash{background:repeating-linear-gradient(45deg,#30363d 0 5px,#21262d 5px 10px);color:var(--muted)}",
    ".qtcy-leg{display:flex;flex-wrap:wrap;gap:4px 12px;font-size:10px;color:var(--muted)}",
    ".qtcy-sw{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:4px}"
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
  function money(v, dec) {
    if (v == null || isNaN(v)) return "—";
    return "$" + Number(v).toFixed(dec == null ? 2 : dec);
  }
  function fmtPx(v) {
    if (v == null || isNaN(v)) return "—";
    v = Number(v);
    if (v >= 100) return "$" + v.toFixed(2);
    if (v >= 1) return "$" + v.toFixed(3);
    if (v >= 0.01) return "$" + v.toFixed(4);
    return "$" + v.toPrecision(3);
  }
  function signed(v, dec) {
    if (v == null || isNaN(v)) return "—";
    return (v >= 0 ? "+" : "−") + "$" + Math.abs(v).toFixed(dec == null ? 2 : dec);
  }
  function pnlCls(v) { return v > 0 ? "qtcy-pos" : v < 0 ? "qtcy-neg" : "qtcy-muted"; }
  function arrow(v) { return v > 0 ? "▲" : v < 0 ? "▼" : "•"; }
  function statHtml(label, val, cls) {
    return '<span><span class="qtcy-muted">' + label + '</span><b class="qtcy-num ' +
      (cls || "") + '">' + val + "</b></span>";
  }
  function shortTs(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return esc(String(iso || "—"));
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  /* ---- Book-chart overlay feed (always on, page open or not) ----
   * Same semantics as the yellow line: funding/adoption steps are not P/L,
   * so any jump too large to be a 5-min market move (>$4 — the sleeve's
   * holdings can't move that in one tick) is collapsed out of the curve.
   * Earlier points are shifted up/down so the line ends at the TRUE current
   * equity and only trading P/L gives it shape (PEPE adoption = deposit). */
  var STEP_USD = 4.0;
  function pushOverlay() {
    var pts = [];
    if (data && data.equity) {
      for (var i = 0; i < data.equity.length; i++) {
        var r = data.equity[i];
        var t = Date.parse(r.ts);
        if (!isNaN(t) && r.equity != null) pts.push({ t: t, v: Number(r.equity) });
      }
    }
    // Collapse funding steps backward: walk from the end; every step > $4
    // shifts all earlier points by that step, exactly like the main chart's
    // transfer-adjusted view.
    var shift = 0;
    for (var k = pts.length - 1; k > 0; k--) {
      pts[k].v += shift;
      var step = pts[k].v - (pts[k - 1].v + shift);
      if (Math.abs(step) > STEP_USD) shift += step;
    }
    if (pts.length) pts[0].v += shift;
    var reg = (window.__qtOverlaySeries = window.__qtOverlaySeries || []);
    for (var j = reg.length - 1; j >= 0; j--)
      if (reg[j] && reg[j].channel === "book" && reg[j].id === "crypto") reg.splice(j, 1);
    if (pts.length >= 2)
      reg.push({ id: "crypto", channel: "book", color: GREEN, topAxisColor: GREEN,
                 // Robert's spec: SOLID line sharing the FULL plot with the
                 // yellow series (own y-scale, green $ on the right, green
                 // clock on top) — no dashed lead-in, no separate lane.
                 leadIn: true, lane: 0, points: pts });
    try { window.dispatchEvent(new Event("qt-overlay-series")); } catch (e) { /* noop */ }
  }

  /* ---- dedicated page ---- */
  function sparkSVG() {
    var eq = (data && data.equity) || [];
    if (eq.length < 2) return '<div class="qtcy-faint" style="font-size:10px;padding:10px 0">Not enough equity points yet.</div>';
    var W = 640, H = 150, L = 46, B = 16;
    var vals = eq.map(function (r) { return Number(r.equity); });
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
    var pad = (hi - lo) * 0.1 || 0.02;
    lo -= pad; hi += pad;
    function X(i) { return L + i / (vals.length - 1) * (W - L - 6); }
    function Y(v) { return (H - B) - (v - lo) / (hi - lo) * (H - B - 8); }
    var pts = vals.map(function (v, i) { return X(i).toFixed(1) + "," + Y(v).toFixed(1); });
    var ticks = [lo + pad, (lo + hi) / 2, hi - pad].map(function (v) {
      return '<line x1="' + L + '" x2="' + (W - 6) + '" y1="' + Y(v).toFixed(1) + '" y2="' + Y(v).toFixed(1) +
        '" stroke="var(--bd)" stroke-dasharray="3 3"/>' +
        '<text x="' + (L - 6) + '" y="' + (Y(v) + 3).toFixed(1) +
        '" text-anchor="end" font-size="9" fill="' + GREEN + '" font-family="ui-monospace,Menlo,monospace">$' +
        v.toFixed(2) + "</text>";
    }).join("");
    var xlab = '<text x="' + L + '" y="' + (H - 2) + '" font-size="9" fill="' + GREEN +
      '" font-family="ui-monospace,Menlo,monospace">' + shortTs(eq[0].ts) + "</text>" +
      '<text x="' + (W - 6) + '" y="' + (H - 2) + '" text-anchor="end" font-size="9" fill="' + GREEN +
      '" font-family="ui-monospace,Menlo,monospace">' + shortTs(eq[eq.length - 1].ts) + "</text>";
    var area = "M" + X(0).toFixed(1) + "," + (H - B) + " L" + pts.join(" L") +
      " L" + X(vals.length - 1).toFixed(1) + "," + (H - B) + " Z";
    return '<svg viewBox="0 0 ' + W + " " + H + '" style="width:100%;display:block">' +
      ticks + xlab +
      '<path d="' + area + '" fill="' + GREEN + '" fill-opacity=".1"/>' +
      '<polyline fill="none" stroke="' + GREEN + '" stroke-width="1.8" points="' + pts.join(" ") + '"/></svg>';
  }

  function render() {
    var ov = document.getElementById("qtcy-overlay");
    if (!ov) return;
    ov.innerHTML = "";
    var hdr = el("div", "qtcy-card",
      '<div class="qtcy-row"><div><b>🪙 Crypto — D39 sleeve (Kraken, live)</b></div>' +
      (data ? "<span class='qtcy-num qtcy-muted' style='font-size:10px'>as of " +
        esc(new Date((data.ts || 0) * 1000).toLocaleTimeString()) + "</span>" : "") + "</div>" +
      '<div class="qtcy-muted" style="font-size:11px;line-height:1.5;margin-top:4px">' +
      "Whale-gated momentum sleeve on Kraken spot. Hard caps: $50 total, freeze at −$15 P/L. " +
      "The green line on the Book chart is this sleeve's equity; full detail lives here. Owner-private.</div>");
    ov.appendChild(hdr);
    if (err) { ov.appendChild(el("div", "qtcy-card qtcy-neg", "crypto fetch failed: " + esc(err))); return; }
    if (!data) { ov.appendChild(el("div", "qtcy-card qtcy-muted", "loading…")); return; }

    var eq = data.equity || [];
    var st = data.state || {};
    var cfg = data.config || {};
    var wh = data.whale || {};
    var last = eq.length ? eq[eq.length - 1] : null;
    var first = eq.length ? eq[0] : null;

    if (st.frozen)
      ov.appendChild(el("div", "qtcy-card qtcy-neg",
        "<b>⛔ SLEEVE FROZEN</b> — P/L hit the −$15 freeze rail. No new entries until reviewed."));

    // hero
    var card = el("div", "qtcy-card");
    if (last) {
      var d = first ? last.equity - first.equity : null;
      var dp = first && first.equity ? d / first.equity * 100 : null;
      card.appendChild(el("div", null,
        '<span class="qtcy-num qtcy-pos" style="font-size:30px;font-weight:700">' + money(last.equity) + "</span>" +
        (d != null ? ' <span class="qtcy-num ' + pnlCls(d) + '" style="font-weight:600">' +
          arrow(d) + " " + signed(d) + " (" + (dp >= 0 ? "+" : "") + dp.toFixed(2) + "%)</span>" +
          '<span class="qtcy-muted" style="font-size:10px"> since tracking (' + shortTs(first.ts) + ")</span>" : "")));
      var deployed = last.positions_mv, room = (cfg.cap_total_usd || 50) - deployed;
      card.appendChild(el("div", "qtcy-stats",
        statHtml("cash", money(last.cash)) +
        statHtml("deployed", money(deployed)) +
        statHtml("realized", signed(last.realized), pnlCls(last.realized)) +
        statHtml("room (of " + money(cfg.cap_total_usd || 50, 0) + ")", money(room))));
      card.lastChild.style.marginTop = "8px";
    } else {
      card.appendChild(el("div", "qtcy-muted", "No equity samples yet."));
    }
    card.appendChild(el("div", "qtcy-sec",
      '<div class="qtcy-h">Sleeve equity — green $ axis, yellow timeline (same colors as the Book overlay)</div>' + sparkSVG()));
    ov.appendChild(card);

    // allocation — the Book page's holdings-vs-target bar pair, sleeve edition.
    // Current bar = live marks as % of sleeve equity; target bar = the
    // engine's own per-tick wants (state.targets) as % of the $50 cap.
    var ac = el("div", "qtcy-card");
    ac.appendChild(el("div", "qtcy-h", "Allocation — holdings vs engine target"));
    (function () {
      var PAL = ["#ffd60a", "#58a6ff", "#f85149", "#3fb950", "#bc8cff", "#f78166",
                 "#39d2c0", "#ff7eb6", "#d2a8ff", "#9ccc2c", "#e3b341", "#79c0ff",
                 "#ffa657", "#56d364", "#ff9bce", "#a5d6ff"];
      var cap = cfg.cap_total_usd || 50;
      var equity = last ? last.equity : 0;
      function short(sym) { return String(sym).replace("/USD", ""); }

      var cur = [];
      Object.keys(st.positions || {}).forEach(function (sym) {
        var p = st.positions[sym] || {};
        var mv = (p.mv != null ? p.mv : p.notional) || 0;
        var pct = equity > 0 ? mv / equity * 100 : 0;
        // per-block P/L label (Robert 2026-08-14): live mark minus cost basis
        var pl = (p.mv != null && p.notional != null) ? p.mv - p.notional : null;
        if (pct > 0.01) cur.push({ sym: sym, pct: pct, pl: pl });
      });
      cur.sort(function (a, b) { return b.pct - a.pct; });
      var curCash = Math.max(0, 100 - cur.reduce(function (s, x) { return s + x.pct; }, 0));

      var tgt = [];
      Object.keys(st.targets || {}).forEach(function (sym) {
        var pct = (st.targets[sym] || 0) / cap * 100;
        if (pct > 0.01) tgt.push({ sym: sym, pct: pct });
      });
      tgt.sort(function (a, b) { return b.pct - a.pct; });
      var tgtCash = Math.max(0, 100 - tgt.reduce(function (s, x) { return s + x.pct; }, 0));

      // One color per pair, assigned by biggest footprint across both bars,
      // so the same coin matches between bars and big slices contrast.
      var rank = {};
      cur.concat(tgt).forEach(function (x) { rank[x.sym] = Math.max(rank[x.sym] || 0, x.pct); });
      var colorOf = {};
      Object.keys(rank).sort(function (a, b) { return rank[b] - rank[a]; })
        .forEach(function (sym, i) { colorOf[sym] = PAL[i % PAL.length]; });

      function bar(rows, cashPct, label, badge) {
        var h = '<div class="qtcy-h" style="margin-bottom:2px">' + label + (badge || "") + "</div>" +
          '<div class="qtcy-bar">';
        rows.forEach(function (r) {
          var plTxt = r.pl != null ? (r.pl >= 0 ? "+" : "") + r.pl.toFixed(2) : "";
          var lbl = (plTxt && r.pct >= 8)
            ? '<span class="qtcy-num" style="font-size:8px;font-weight:600;color:#0d1117">' +
              plTxt + "</span>"
            : "";
          h += '<div title="' + esc(short(r.sym)) + " " + r.pct.toFixed(1) + "%" +
            (plTxt ? " · P/L " + plTxt : "") + '" style="width:' +
            r.pct + "%;background:" + colorOf[r.sym] + '">' + lbl + "</div>";
        });
        if (cashPct > 0.5)
          h += '<div class="qtcy-cash" title="cash / uninvested ' + cashPct.toFixed(1) +
            '%" style="width:' + cashPct + '%">' +
            (cashPct >= 12 ? '<span class="qtcy-num" style="font-size:9px">' +
              cashPct.toFixed(0) + "% cash</span>" : "") + "</div>";
        h += '</div><div class="qtcy-leg">';
        rows.forEach(function (r) {
          h += '<span class="qtcy-num"><span class="qtcy-sw" style="background:' + colorOf[r.sym] +
            '"></span>' + esc(short(r.sym)) + ' <span class="qtcy-faint">' + r.pct.toFixed(1) + "%</span></span>";
        });
        if (cashPct > 0.5)
          h += '<span class="qtcy-num"><span class="qtcy-sw qtcy-cash"></span>cash <span class="qtcy-faint">' +
            cashPct.toFixed(1) + "%</span></span>";
        return h + "</div>";
      }

      var badge = "";
      if (st.targets_ts) {
        var age = (Date.now() - new Date(st.targets_ts).getTime()) / 60000;
        if (isFinite(age) && age > 20)
          badge = ' <span class="qtcy-pill qtcy-neg" style="margin-left:6px">stale ' +
            Math.round(age) + "m</span>";
      }
      if (!cur.length && !tgt.length) {
        ac.appendChild(el("div", "qtcy-muted",
          "No holdings and no targets yet — bars appear after the next engine tick."));
      } else {
        ac.appendChild(el("div", null,
          bar(cur, curCash, "holdings — % of sleeve equity " + money(equity), "") +
          '<div style="height:8px"></div>' +
          bar(tgt, tgtCash, "engine target — % of " + money(cap, 0) + " cap" +
            (st.targets_gate ? ' <span class="qtcy-faint">(' + esc(st.targets_gate) + ")</span>" : ""),
            badge)));
      }
    })();
    ov.appendChild(ac);

    // positions
    var posNames = Object.keys(st.positions || {});
    var pc = el("div", "qtcy-card");
    pc.appendChild(el("div", "qtcy-h", "Open positions (" + posNames.length + ")"));
    if (posNames.length) {
      // Same metric set as the Book page's stock PositionsTable:
      // Symbol | Qty | Mkt value | Unrl P/L | Unrl % (+ entry/now context),
      // with the same green/red row tint by P/L sign.
      var sleeveEq = last ? last.equity : 0;
      var rows = posNames.map(function (sym) {
        var p = st.positions[sym];
        var qty = p.entry_price > 0 ? p.notional / p.entry_price : 0;
        var mv = p.mv != null ? p.mv : p.notional;
        var cur2 = p.cur != null ? p.cur : (qty > 0 ? mv / qty : p.entry_price);
        var pl = mv - p.notional;
        var plPct = p.notional > 0 ? (mv / p.notional - 1) * 100 : 0;
        var wPct = sleeveEq > 0 ? mv / sleeveEq * 100 : 0;
        var tint = pl > 0.005 ? "background:rgba(63,185,80,.05)"
                 : pl < -0.005 ? "background:rgba(248,81,73,.05)" : "";
        var cls = pl > 0.005 ? "qtcy-pos" : pl < -0.005 ? "qtcy-neg" : "qtcy-muted";
        var arrowCh = pl > 0.005 ? "▲ " : pl < -0.005 ? "▼ " : "";
        return '<tr style="' + tint + '"><td><b>' + esc(sym.replace("/USD", "")) + "</b></td>" +
          '<td class="qtcy-num qtcy-muted">' + qty.toFixed(qty >= 1 ? 4 : 6) + "</td>" +
          '<td class="qtcy-num">' + fmtPx(p.entry_price) + "</td>" +
          '<td class="qtcy-num">' + fmtPx(cur2) + "</td>" +
          '<td class="qtcy-num">' + money(mv) + "</td>" +
          '<td class="qtcy-num ' + cls + '">' + arrowCh + (pl >= 0 ? "+" : "") + money(Math.abs(pl)).replace("$", (pl < 0 ? "-$" : "$")) + "</td>" +
          '<td class="qtcy-num ' + cls + '">' + (plPct >= 0 ? "+" : "") + plPct.toFixed(2) + "%</td>" +
          '<td class="qtcy-num qtcy-muted">' + wPct.toFixed(1) + "%</td></tr>";
      }).join("");
      pc.appendChild(el("table", "qtcy-table",
        "<tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Now</th><th>Mkt value</th>" +
        "<th>Unrl P/L</th><th>Unrl %</th><th>% sleeve</th></tr>" + rows));
    } else {
      pc.appendChild(el("div", "qtcy-muted", posNames.length === 0 ? "Flat — no open positions." : ""));
    }
    ov.appendChild(pc);

    // universe watch — live price on every coin we have access to, so
    // tracking needs zero dust positions. HELD/TARGET badges tie it to the
    // allocation bars above.
    var uni = (cfg.universe || []).slice();
    if (uni.length) {
      var uw = el("div", "qtcy-card");
      var tickTs = tick && tick.ts ? new Date(tick.ts * 1000) : null;
      uw.appendChild(el("div", "qtcy-h", "Universe watch — " + uni.length +
        " tradable coins, live Kraken prices" +
        (tickTs ? ' <span class="qtcy-faint">(as of ' +
          tickTs.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + ")</span>" : "")));
      var trows = tick && tick.rows ? tick.rows : {};
      var held2 = st.positions || {};
      var tgts2 = st.targets || {};
      var list = uni.map(function (pair) {
        var t = trows[pair] || {};
        return { pair: pair, price: t.price, chg: t.chg_pct,
                 held: held2[pair] != null, tgt: tgts2[pair] != null };
      });
      list.sort(function (a, b) {
        if (a.held !== b.held) return a.held ? -1 : 1;
        if (a.tgt !== b.tgt) return a.tgt ? -1 : 1;
        return (b.chg || -999) - (a.chg || -999);
      });
      var urows = list.map(function (r) {
        var chgCls = r.chg == null ? "qtcy-faint" : r.chg >= 0 ? "qtcy-pos" : "qtcy-neg";
        var badge = r.held
          ? '<span class="qtcy-pill" style="color:#3fb950;border-color:#3fb950">HELD</span>'
          : r.tgt
            ? '<span class="qtcy-pill qtcy-gold" style="border-color:#ffd60a">TARGET</span>'
            : '<span class="qtcy-faint" style="font-size:9px">watch</span>';
        return "<tr><td><b>" + esc(r.pair.replace("/USD", "")) + "</b></td>" +
          '<td class="qtcy-num">' + fmtPx(r.price) + "</td>" +
          '<td class="qtcy-num ' + chgCls + '">' +
          (r.chg == null ? "—" : (r.chg >= 0 ? "+" : "") + r.chg.toFixed(2) + "%") + "</td>" +
          '<td style="text-align:right">' + badge + "</td></tr>";
      }).join("");
      uw.appendChild(el("table", "qtcy-table",
        "<tr><th>Coin</th><th>Price</th><th>24h</th><th></th></tr>" + urows));
      ov.appendChild(uw);
    }

    // whale gate
    var wc = el("div", "qtcy-card");
    var ro = wh.risk_on;
    var gateLine = "";
    var lt = data.log_tail || [];
    for (var i = lt.length - 1; i >= 0; i--) {
      var m = /gate=(\S+.*)$/.exec(lt[i]);
      if (m) { gateLine = m[1]; break; }
    }
    wc.appendChild(el("div", "qtcy-h", "Whale gate (D39 evidence-aware)"));
    wc.appendChild(el("div", "qtcy-stats",
      statHtml("risk-on", ro != null ? (ro * 100).toFixed(1) + "%" : "—", ro >= 0.5 ? "qtcy-pos" : "qtcy-neg") +
      statHtml("signal trades", wh.signal_trades != null ? wh.signal_trades : "—") +
      statHtml("wallets", wh.distinct_wallets != null ? wh.distinct_wallets : "—") +
      statHtml("signal $", wh.signal_usd != null ? "$" + (wh.signal_usd / 1e6).toFixed(0) + "M" : "—") +
      statHtml("gate now", gateLine ? esc(gateLine) : "—")));
    if (wh.asof)
      wc.appendChild(el("div", "qtcy-faint",
        '<div style="font-size:9px;margin-top:6px">whale feed as of ' + shortTs(wh.asof) +
        " · legs: " + esc(Object.keys(wh.leg_weights || {}).map(function (k) {
          return k + " " + wh.leg_weights[k];
        }).join(" · ")) + "</div>"));
    ov.appendChild(wc);

    // config
    var cc = el("div", "qtcy-card");
    cc.appendChild(el("div", "qtcy-h", "Risk rails & config"));
    cc.appendChild(el("div", "qtcy-stats",
      statHtml("total cap", money(cfg.cap_total_usd, 0)) +
      statHtml("per trade", money(cfg.per_trade_usd, 0)) +
      statHtml("stop", cfg.stop_pct != null ? (cfg.stop_pct * 100).toFixed(0) + "%" : "—", "qtcy-neg") +
      statHtml("take", cfg.take_pct != null ? "+" + (cfg.take_pct * 100).toFixed(0) + "%" : "—", "qtcy-pos") +
      statHtml("freeze", money(cfg.freeze_pnl_usd, 0), "qtcy-neg") +
      statHtml("bars", esc(cfg.bar_timeframe || "—") + " EMA " + (cfg.ema_fast || "?") + "/" + (cfg.ema_slow || "?"))));
    if (cfg.universe)
      cc.appendChild(el("div", "qtcy-faint",
        '<div style="font-size:9px;margin-top:6px">universe: ' + esc(cfg.universe.join(" ")) + "</div>"));
    ov.appendChild(cc);

    // log tail
    if (lt.length) {
      var lc = el("div", "qtcy-card");
      lc.appendChild(el("div", "qtcy-h", "Sleeve log (latest)"));
      lc.appendChild(el("div", "qtcy-log", esc(lt.slice(-8).join("\n"))));
      ov.appendChild(lc);
    }
  }

  function apiHeaders() {
    var headers = { accept: "application/json" };
    try {
      var k = localStorage.getItem("qt.apiKey");
      if (k) headers["x-api-key"] = k;
    } catch (e) { /* noop */ }
    return headers;
  }

  function fetchData() {
    fetch("/api/crypto/summary", { headers: apiHeaders() })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) { data = d; err = null; pushOverlay(); renderTribar(); if (open) render(); })
      .catch(function (e) { err = e.message; if (open) render(); });
    fetch("/api/portfolio/alpaca_live", { headers: apiHeaders() })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) { alp = d; renderTribar(); })
      .catch(function () { /* keep last value */ });
    fetch("/api/crypto/ticker", { headers: apiHeaders() })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) { tick = d; if (open) render(); })
      .catch(function () { /* keep last value */ });
  }

  /* ---- tri-balance band at the top of the Book page ----
   * Same-sized numbers for the Alpaca USD book, the crypto book, and both
   * together — color-coded to match the two chart lines. */
  function onBook() {
    return !/\/(lab|governor|markets|health|settings)(\/|$)/.test(location.pathname);
  }
  function triCol(label, v, color) {
    return '<div><div class="qtcy-tri-label">' + label + "</div>" +
      '<div class="qtcy-tri-val" style="color:' + color + '">' +
      (v != null && !isNaN(v) ? money(v) : "—") + "</div></div>";
  }
  function renderTribar() {
    var bar = document.getElementById("qtcy-tribar");
    var main = document.querySelector("main");
    if (!onBook() || !main) { if (bar) bar.remove(); return; }
    if (!bar) {
      bar = el("div", "qtcy-tribar");
      bar.id = "qtcy-tribar";
      main.insertBefore(bar, main.firstChild);
    } else if (bar.parentNode !== main) {
      bar.remove();
      main.insertBefore(bar, main.firstChild);
    }
    var aEq = alp && alp.equity != null ? Number(alp.equity) : null;
    var eqArr = (data && data.equity) || [];
    var cEq = eqArr.length ? Number(eqArr[eqArr.length - 1].equity) : null;
    var tot = aEq != null && cEq != null ? aEq + cEq : null;
    bar.innerHTML =
      triCol("Alpaca USD", aEq, YELLOW) +
      triCol("Crypto", cEq, GREEN) +
      triCol("Total", tot, "var(--fg)");
  }

  function setOpen(v) {
    open = v;
    var ov = document.getElementById("qtcy-overlay");
    if (open) {
      // Close the catalog panel if it's up, so the two never stack.
      var cat = document.getElementById("qtcat-overlay");
      if (cat) {
        var cb = document.querySelector(".qtcat-navbtn");
        if (cb) cb.click(); else cat.remove();
      }
      if (!ov) {
        ov = el("div", "qtcy-overlay");
        ov.id = "qtcy-overlay";
        document.body.appendChild(ov);
      }
      render();
      fetchData();
    } else if (ov) {
      ov.remove();
    }
    document.querySelectorAll(".qtcy-navbtn").forEach(function (b) {
      b.classList.toggle("qtcy-navbtn-active", open);
    });
  }

  function makeBtn(mobile) {
    var b = el("button", "qtcy-navbtn");
    if (mobile) {
      b.style.cssText = "flex:1 1 0%;display:flex;flex-direction:column;align-items:center;" +
        "gap:2px;padding:8px 0;font-size:10px;background:none;border:0;color:var(--muted)";
      b.innerHTML = "<span style='font-size:16px;line-height:1'>🪙</span>Crypto";
    } else {
      b.style.cssText = "display:flex;align-items:center;gap:8px;border-radius:4px;" +
        "padding:8px 12px;font-size:14px;background:none;border:0;color:var(--muted);" +
        "text-align:left;width:100%;cursor:pointer";
      b.innerHTML = "<span>🪙</span> Crypto";
    }
    b.addEventListener("click", function () { setOpen(!open); });
    return b;
  }

  var mountTries = 0;
  function mount() {
    mountTries++;
    var mobileNav = document.querySelector("nav.fixed.inset-x-0.bottom-0");
    if (mobileNav && !mobileNav.querySelector(".qtcy-navbtn")) {
      // 7th slot: let the Catalog tab land first (both scripts poll-mount),
      // but don't wait forever if the catalog bundle failed to load.
      if (mobileNav.querySelector(".qtcat-navbtn") || mountTries > 4)
        mobileNav.appendChild(makeBtn(true));
    }
    var rail = document.querySelector("nav.hidden .flex-1.flex-col, nav .flex.flex-1.flex-col");
    if (rail && !rail.querySelector(".qtcy-navbtn")) {
      if (rail.querySelector(".qtcat-navbtn") || mountTries > 4)
        rail.appendChild(makeBtn(false));
    }
    renderTribar(); // keeps the band present across SPA route changes
  }

  // Book page: fill the generic [data-qt-slot="book-alloc-top"] mount point
  // (rendered by the shared shell directly above the current-allocation bar)
  // with the crypto sleeve's holdings bar. Owner-mode only by construction —
  // this script never reaches guest sessions. Fingerprinted so the 2s keeper
  // only rewrites the DOM when the numbers actually change.
  function injectBookAllocBar() {
    var slot = document.querySelector('[data-qt-slot="book-alloc-top"]');
    if (!slot || !data) return;
    var s2 = data.state || {};
    var eqArr = data.equity || [];
    var lastRow = eqArr.length ? eqArr[eqArr.length - 1] : null;
    var equity = lastRow ? lastRow.equity : 0;
    var PAL = ["#3fb950", "#39d2c0", "#bc8cff", "#ff7eb6", "#9ccc2c", "#79c0ff",
               "#f78166", "#d2a8ff", "#e3b341", "#56d364", "#ffa657", "#a5d6ff"];
    var cur = [];
    Object.keys(s2.positions || {}).forEach(function (sym) {
      var p = s2.positions[sym] || {};
      var mv = (p.mv != null ? p.mv : p.notional) || 0;
      var pct = equity > 0 ? mv / equity * 100 : 0;
      if (pct > 0.01) cur.push({ sym: String(sym).replace("/USD", ""), pct: pct });
    });
    cur.sort(function (a, b) { return b.pct - a.pct; });
    var cash = Math.max(0, 100 - cur.reduce(function (s, x) { return s + x.pct; }, 0));
    var fp = JSON.stringify([cur.map(function (x) { return [x.sym, x.pct.toFixed(1)]; }),
                             cash.toFixed(0), equity.toFixed(2)]);
    if (slot.getAttribute("data-qtcy-fp") === fp) return;
    slot.setAttribute("data-qtcy-fp", fp);
    var h = '<div class="qtcy-h" style="margin-bottom:2px;color:#3fb950">' +
      "crypto sleeve — % of sleeve equity " + money(equity) + "</div>" +
      '<div class="qtcy-bar">';
    cur.forEach(function (r, i) {
      h += '<div title="' + esc(r.sym) + " " + r.pct.toFixed(1) + '%" style="width:' +
        r.pct + "%;background:" + PAL[i % PAL.length] + '"></div>';
    });
    if (cash > 0.5)
      h += '<div class="qtcy-cash" title="cash / uninvested ' + cash.toFixed(1) +
        '%" style="width:' + cash + '%">' +
        (cash >= 12 ? '<span class="qtcy-num" style="font-size:9px">' +
          cash.toFixed(0) + "% cash</span>" : "") + "</div>";
    h += '</div><div class="qtcy-leg" style="margin-bottom:10px">';
    cur.forEach(function (r, i) {
      h += '<span class="qtcy-num"><span class="qtcy-sw" style="background:' +
        PAL[i % PAL.length] + '"></span>' + esc(r.sym) +
        ' <span class="qtcy-faint">' + r.pct.toFixed(1) + "%</span></span>";
    });
    if (cash > 0.5)
      h += '<span class="qtcy-num"><span class="qtcy-sw qtcy-cash"></span>cash ' +
        '<span class="qtcy-faint">' + cash.toFixed(1) + "%</span></span>";
    slot.innerHTML = h + "</div>";
  }

  function init() {
    var st = document.createElement("style");
    st.textContent = css;
    document.head.appendChild(st);
    document.addEventListener("click", function (ev) {
      if (!open) return;
      var t = ev.target;
      // Close on SPA navigation or when the catalog tab is opened.
      if (t && t.closest && (t.closest("a") || t.closest(".qtcat-navbtn"))) setOpen(false);
    }, true);
    mount();
    setInterval(mount, 2000);
    setInterval(injectBookAllocBar, 2000);
    fetchData();
    setInterval(fetchData, POLL_MS); // always-on: keeps the Book overlay fresh
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
