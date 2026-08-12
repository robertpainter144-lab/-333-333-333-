/* Quarki333 Terminal — owner-private TraderGrader card (Catalog tab).
 * Companion to owner_catalog.js, same pattern as owner_crypto_intel.js: it
 * does NOT own a tab or nav button. It polls for the #qtcat-overlay pane
 * that owner_catalog.js mounts when the Catalog tab is opened and inserts
 * one card right below the catalog header — re-inserting whenever the
 * catalog's own render() wipes and rebuilds that pane.
 * Card sections:
 *   1. Auto-graded @tradergrader callouts (Sniper of the Day / Trade of the
 *      Week) — forward returns vs SPY at 1d/20d per rec, account rollups.
 *      Honest framing: his posts are retrospective recaps, so the grades
 *      test FOLLOW-the-highlight, not his entry timing.
 *   2. His leaderboard — the traders he medals most (golds/medals, avg
 *      claimed ROI, last seen). Claimed ROIs are HIS numbers, not ours.
 * Data: GET /api/tradergrader (owner-gated; fed daily 15:35 MT by
 * equity_social.py x_fetch+grade). Advisory only — no live weight without
 * a D7 record. Never mounts on the shared/guest Terminal (server-side
 * injection is owner-mode only; the endpoint 403s guests regardless).
 */
(function () {
  "use strict";

  var CONTAINER_ID = "qtcat-overlay";   // owned by owner_catalog.js — read-only
  var CARD_ID = "qttg-card";
  var STYLE_ID = "qttg-style";
  var MOUNT_POLL_MS = 1000;
  var FETCH_POLL_MS = 120000;

  var data = null, err = null, cardEl = null, fetchTimer = null, wasMounted = false;

  var css = [
    ".qttg-card{border:1px solid var(--bd);background:var(--panel);border-radius:8px;",
    "padding:12px;margin:0 auto 10px;max-width:72rem}",
    ".qttg-num{font-family:ui-monospace,SFMono-Regular,Menlo,Roboto Mono,monospace;",
    "font-variant-numeric:tabular-nums;letter-spacing:-.01em}",
    ".qttg-muted{color:var(--muted)}.qttg-faint{color:var(--faint,#6e7681)}",
    ".qttg-pos{color:#3fb950}.qttg-neg{color:#f85149}.qttg-amber{color:#d29922}",
    ".qttg-h{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}",
    ".qttg-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px;flex-wrap:wrap}",
    ".qttg-sec{margin-top:12px;border-top:1px solid var(--bd);padding-top:10px}",
    ".qttg-stats{display:flex;gap:14px;flex-wrap:wrap;font-size:11px}",
    ".qttg-stats b{display:block;font-size:13px;font-weight:600}",
    ".qttg-table{width:100%;border-collapse:collapse;font-size:11px}",
    ".qttg-table td,.qttg-table th{padding:3px 0;border-top:1px solid var(--bd);text-align:right}",
    ".qttg-table td:first-child,.qttg-table th:first-child{text-align:left}",
    ".qttg-table th{font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border-top:0}",
    ".qttg-note td{border-top:0;padding-top:0;text-align:left;font-size:10px;color:var(--faint,#6e7681)}",
    ".qttg-pill{display:inline-block;border:1px solid currentColor;border-radius:9999px;",
    "padding:1px 8px;font-size:9px;font-weight:700;letter-spacing:.08em}"
  ].join("");

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function pctCell(h) {
    if (!h || h.ret_pct == null)
      return '<td class="qttg-num qttg-muted">' +
        (h && h.status === "pending" ? "…" : "—") + "</td>";
    var cls = h.ret_pct >= 0 ? "qttg-pos" : "qttg-neg";
    return '<td class="qttg-num ' + cls + '">' +
      (h.ret_pct >= 0 ? "+" : "") + h.ret_pct.toFixed(1) + "%</td>";
  }
  function rate(v) { return v == null ? "—" : (v * 100).toFixed(0) + "%"; }
  function exc(v) {
    if (v == null) return "—";
    return (v >= 0 ? "+" : "") + v.toFixed(1) + "%";
  }
  function statHtml(label, val, cls) {
    return '<span><span class="qttg-muted">' + label + '</span><b class="qttg-num ' +
      (cls || "") + '">' + val + "</b></span>";
  }
  function shortTs(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return esc(String(iso || "—"));
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function gradesHtml(acct, title, blurb) {
    var html = '<div class="qttg-h">' + title + "</div>";
    if (!acct || !acct.n) {
      return html + '<div class="qttg-muted" style="font-size:11px">' +
        "No recs yet. " + blurb + "</div>";
    }
    html += '<div class="qttg-stats">' +
      statHtml("recs", acct.n) +
      statHtml("1d win", rate(acct.win_rate_1d),
        acct.win_rate_1d != null && acct.win_rate_1d >= 0.5 ? "qttg-pos" : "qttg-neg") +
      statHtml("1d vs SPY", exc(acct.avg_excess_1d),
        acct.avg_excess_1d != null && acct.avg_excess_1d >= 0 ? "qttg-pos" : "qttg-neg") +
      statHtml("20d win", rate(acct.win_rate_20d),
        acct.win_rate_20d != null && acct.win_rate_20d >= 0.5 ? "qttg-pos" : "qttg-neg") +
      statHtml("20d vs SPY", exc(acct.avg_excess_20d),
        acct.avg_excess_20d != null && acct.avg_excess_20d >= 0 ? "qttg-pos" : "qttg-neg") +
      "</div>";
    var recs = (acct.recs || []).slice().sort(function (a, b) {
      return String(b.ts).localeCompare(String(a.ts));
    });
    var rows = recs.map(function (r) {
      var sideCls = r.side === "short" ? "qttg-neg" : "qttg-pos";
      return "<tr><td class='qttg-num'>" + esc(String(r.ts).slice(0, 10)) + "</td>" +
        "<td><b>" + esc(r.sym) + "</b></td>" +
        '<td class="' + sideCls + '">' + esc(r.side) + "</td>" +
        pctCell(r.h1) + pctCell(r.h20) + "</tr>" +
        (r.note ? '<tr class="qttg-note"><td colspan="5">' + esc(r.note) + "</td></tr>" : "");
    }).join("");
    html += '<table class="qttg-table" style="margin-top:8px"><tr><th>Date</th><th>Sym</th>' +
      "<th>Side</th><th>1d</th><th>20d</th></tr>" + rows + "</table>";
    return html;
  }

  function boardHtml(lb) {
    var html = '<div class="qttg-h">🏆 His leaderboard — traders he medals most</div>';
    var traders = (lb && lb.traders) || {};
    var keys = Object.keys(traders);
    if (!keys.length)
      return html + '<div class="qttg-muted" style="font-size:11px">No medal posts parsed yet.</div>';
    var rows = keys.slice(0, 15).map(function (k) {
      var t = traders[k] || {};
      return "<tr><td><b>@" + esc(t.handle || k) + "</b></td>" +
        '<td class="qttg-num">' + (t.golds || 0) + "</td>" +
        '<td class="qttg-num">' + (t.medals || 0) + "</td>" +
        '<td class="qttg-num">' + (t.avg_claimed_roi != null ? "+" + t.avg_claimed_roi + "%" : "—") + "</td>" +
        '<td class="qttg-num qttg-faint">' + esc(String(t.last_seen || "").slice(0, 10)) + "</td></tr>";
    }).join("");
    html += '<table class="qttg-table"><tr><th>Trader</th><th>🥇</th><th>Medals</th>' +
      "<th>Avg claimed ROI</th><th>Last seen</th></tr>" + rows + "</table>" +
      '<div class="qttg-faint" style="font-size:9px;margin-top:6px">' +
      "\"Claimed ROI\" is TraderGrader's own per-contract number for the day he medaled them — " +
      "not verified by us and not comparable to buy-and-hold returns.</div>";
    return html;
  }

  function render() {
    if (!cardEl) return;
    var lb = (data || {}).leaderboard || {};
    var body = '<div class="qttg-row"><div><b>🎯 TRADERGRADER</b> ' +
      '<span class="qttg-muted" style="font-size:11px">@tradergrader on X — grades other options traders</span></div>' +
      (lb.ts ? "<span class='qttg-num qttg-muted' style='font-size:10px'>as of " + esc(shortTs(lb.ts)) + "</span>" : "") +
      "</div>" +
      '<div class="qttg-faint" style="font-size:10px;margin-top:6px">' +
      (lb.n_posts != null ? lb.n_posts + " posts tracked · " : "") +
      "auto-ingested daily 15:35 MT · his posts are retrospective recaps, so grades below test " +
      "<b>following</b> the highlight the day it lands, not his entry timing · " +
      '<span class="qttg-pill qttg-amber">ADVISORY — no live weight without D7</span></div>';
    if (err) {
      body += '<div class="qttg-sec qttg-neg" style="font-size:11px">fetch failed: ' + esc(err) + "</div>";
    } else if (!data) {
      body += '<div class="qttg-sec qttg-muted" style="font-size:11px">loading…</div>';
    } else {
      var accts = ((data.grades || {}).accounts) || {};
      body += '<div class="qttg-sec">' + gradesHtml(accts.tradergrader_auto,
        "📊 Auto-graded callouts — follow-the-highlight vs SPY",
        "Callouts are parsed from Sniper-of-the-Day / Trade-of-the-Week posts as they land.") + "</div>";
      var man = accts.tradergrader;
      if (man && man.n)
        body += '<div class="qttg-sec">' + gradesHtml(man,
          "✍️ Hand-pasted recs (tracked_accounts.json)", "") + "</div>";
      body += '<div class="qttg-sec">' + boardHtml(lb) + "</div>";
    }
    cardEl.innerHTML = body;
  }

  function fetchData() {
    var headers = { accept: "application/json" };
    try {
      var k = localStorage.getItem("qt.apiKey");
      if (k) headers["x-api-key"] = k;
    } catch (e) { /* noop */ }
    fetch("/api/tradergrader", { headers: headers })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) { data = d; err = null; render(); })
      .catch(function (e) { err = e.message; render(); });
  }

  /* Poll for the catalog pane and (re-)insert our card right below the
   * catalog header (first child) so it isn't buried under 20+ pilot cards.
   * The network fetch loop only runs while the pane exists. */
  function ensureMounted() {
    var ov = document.getElementById(CONTAINER_ID);
    if (!ov) {
      if (wasMounted) {
        wasMounted = false;
        cardEl = null;
        if (fetchTimer) { clearInterval(fetchTimer); fetchTimer = null; }
      }
      return;
    }
    var existing = document.getElementById(CARD_ID);
    if (!existing || existing.parentNode !== ov) {
      cardEl = document.createElement("div");
      cardEl.id = CARD_ID;
      cardEl.className = "qttg-card";
      ov.insertBefore(cardEl, ov.children[1] || null);
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

  function init() {
    if (!document.getElementById(STYLE_ID)) {
      var st = document.createElement("style");
      st.id = STYLE_ID;
      st.textContent = css;
      document.head.appendChild(st);
    }
    ensureMounted();
    setInterval(ensureMounted, MOUNT_POLL_MS);
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
