/* Quarki333 Terminal — owner-private TraderGrader tab.
 * Adds a "Grader" nav button (mobile bottom nav + desktop rail, same mount
 * idiom as owner_catalog.js) opening an overlay with two sections:
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

  var POLL_MS = 120000;
  var open = false, data = null, err = null, timer = null;

  var css = [
    ".qttg-overlay{position:fixed;inset:0;z-index:60;overflow-y:auto;background:var(--bg,#0d1117);",
    "padding:12px 12px 84px}",
    ".qttg-card{border:1px solid var(--bd);background:var(--panel);border-radius:8px;",
    "padding:12px;margin:0 auto 10px;max-width:72rem}",
    ".qttg-num{font-family:ui-monospace,SFMono-Regular,Menlo,Roboto Mono,monospace;",
    "font-variant-numeric:tabular-nums;letter-spacing:-.01em}",
    ".qttg-muted{color:var(--muted)}.qttg-faint{color:var(--faint,#6e7681)}",
    ".qttg-pos{color:#3fb950}.qttg-neg{color:#f85149}.qttg-amber{color:#d29922}",
    ".qttg-h{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}",
    ".qttg-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px;flex-wrap:wrap}",
    ".qttg-stats{display:flex;gap:14px;flex-wrap:wrap;font-size:11px}",
    ".qttg-stats b{display:block;font-size:13px;font-weight:600}",
    ".qttg-table{width:100%;border-collapse:collapse;font-size:11px}",
    ".qttg-table td,.qttg-table th{padding:3px 0;border-top:1px solid var(--bd);text-align:right}",
    ".qttg-table td:first-child,.qttg-table th:first-child{text-align:left}",
    ".qttg-table th{font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border-top:0}",
    ".qttg-note td{border-top:0;padding-top:0;text-align:left;font-size:10px;color:var(--faint,#6e7681)}",
    ".qttg-pill{display:inline-block;border:1px solid currentColor;border-radius:9999px;",
    "padding:1px 8px;font-size:9px;font-weight:700;letter-spacing:.08em}",
    ".qttg-navbtn-active{color:var(--fg)!important;font-weight:600}"
  ].join("");

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
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

  function gradesCard(acct, title, blurb) {
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

  function boardCard(lb) {
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
    var ov = document.getElementById("qttg-overlay");
    if (!ov) return;
    ov.innerHTML = "";
    var lb = (data || {}).leaderboard || {};
    var hdr = el("div", "qttg-card",
      '<div class="qttg-row"><div><b>🎯 TRADERGRADER</b> ' +
      '<span class="qttg-muted" style="font-size:11px">@tradergrader on X — grades other options traders</span></div>' +
      (lb.ts ? "<span class='qttg-num qttg-muted' style='font-size:10px'>as of " + esc(shortTs(lb.ts)) + "</span>" : "") +
      "</div>" +
      '<div class="qttg-faint" style="font-size:10px;margin-top:6px">' +
      (lb.n_posts != null ? lb.n_posts + " posts tracked · " : "") +
      "auto-ingested daily 15:35 MT · his posts are retrospective recaps, so grades below test " +
      "<b>following</b> the highlight the day it lands, not his entry timing · " +
      '<span class="qttg-pill qttg-amber">ADVISORY — no live weight without D7</span></div>');
    ov.appendChild(hdr);
    if (err) { ov.appendChild(el("div", "qttg-card qttg-neg", "fetch failed: " + esc(err))); return; }
    if (!data) { ov.appendChild(el("div", "qttg-card qttg-muted", "loading…")); return; }
    var accts = ((data.grades || {}).accounts) || {};
    ov.appendChild(el("div", "qttg-card", gradesCard(accts.tradergrader_auto,
      "📊 Auto-graded callouts — follow-the-highlight vs SPY",
      "Callouts are parsed from Sniper-of-the-Day / Trade-of-the-Week posts as they land.")));
    var man = accts.tradergrader;
    if (man && man.n)
      ov.appendChild(el("div", "qttg-card", gradesCard(man,
        "✍️ Hand-pasted recs (tracked_accounts.json)", "")));
    ov.appendChild(el("div", "qttg-card", boardCard(lb)));
    ov.appendChild(el("div", null,
      "<div class='qttg-muted' style='font-size:10px;text-align:center;padding:4px 0 8px'>" +
      "source: equity_social.py x_fetch/grade · owner-private</div>"));
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

  function setOpen(v) {
    open = v;
    var ov = document.getElementById("qttg-overlay");
    if (open) {
      if (!ov) {
        ov = el("div", "qttg-overlay");
        ov.id = "qttg-overlay";
        document.body.appendChild(ov);
      }
      render();
      fetchData();
      if (!timer) timer = setInterval(fetchData, POLL_MS);
    } else {
      if (ov) ov.remove();
      if (timer) { clearInterval(timer); timer = null; }
    }
    document.querySelectorAll(".qttg-navbtn").forEach(function (b) {
      b.classList.toggle("qttg-navbtn-active", open);
    });
  }

  function makeBtn(mobile) {
    var b = el("button", "qttg-navbtn");
    if (mobile) {
      b.style.cssText = "flex:1 1 0%;display:flex;flex-direction:column;align-items:center;" +
        "gap:2px;padding:8px 0;font-size:10px;background:none;border:0;color:var(--muted)";
      b.innerHTML = "<span style='font-size:16px;line-height:1'>🎯</span>Grader";
    } else {
      b.style.cssText = "display:flex;align-items:center;gap:8px;border-radius:4px;" +
        "padding:8px 12px;font-size:14px;background:none;border:0;color:var(--muted);" +
        "text-align:left;width:100%;cursor:pointer";
      b.innerHTML = "<span>🎯</span> Grader";
    }
    b.addEventListener("click", function () { setOpen(!open); });
    return b;
  }

  function mount() {
    var mobileNav = document.querySelector("nav.fixed.inset-x-0.bottom-0");
    if (mobileNav && !mobileNav.querySelector(".qttg-navbtn"))
      mobileNav.appendChild(makeBtn(true));
    var rail = document.querySelector("nav.hidden .flex-1.flex-col, nav .flex.flex-1.flex-col");
    if (rail && !rail.querySelector(".qttg-navbtn"))
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
