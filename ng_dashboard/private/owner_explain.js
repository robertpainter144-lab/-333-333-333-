/* Quarki333 Terminal — owner-private Book-page explain bundle
 * (D1 pattern: private/ dir, owner-mode-only injection; NEVER lands in
 * trading-gui source). Two features, both scoped to the Book page's
 * "Positions & allocation drift" card and its layout column:
 *
 *  1. Holding explain dropdowns — tap a position row to expand an inline
 *     panel (as a sibling <tr>) explaining why the book holds that symbol.
 *     GET /api/activity_detail/alpaca_live?symbol=SYM&side=HOLD&mode=holding
 *     Cached 2 min per symbol.
 *
 *  2. Trade History card — inserted as a sibling section directly below the
 *     positions card, listing GET /api/activity/alpaca_live?limit=25 with
 *     tap-to-expand rows hitting the same activity_detail endpoint (no
 *     mode param, real side/reason), refreshed every 60s while mounted.
 *
 * Mount anchor: [data-qt-slot="book-alloc-top"] (SPA-exposed, unconditional
 * whenever the Book page has a loaded, non-stale portfolio — present even
 * with zero open positions, unlike the <table> itself which the SPA swaps
 * for an EmptyState when there's nothing to show).
 *
 * The SPA re-renders position rows on its poll cycle (React owns that
 * table), so nothing here assumes a DOM node survives forever: row click
 * handlers are re-attached idempotently via data-qtxp-bound, and any open
 * explain panel is tracked in our own state map and re-inserted if React's
 * reconciliation wipes it. The Trade History card and its rows are entirely
 * ours (full innerHTML replace on each render), so no such fighting is
 * needed there.
 */
(function () {
  "use strict";
  if (window.__qtExplain) return;
  window.__qtExplain = true;

  var EXPLAIN_CACHE_MS = 120000;      // 2 min, per symbol / per trade-row key
  var TRADE_HISTORY_POLL_MS = 60000;  // refresh trade history every 60s
  var BACKUP_TICK_MS = 1000;          // safety-net re-scan, matches sibling bundles
  var STYLE_ID = "qtxp-style";

  // ---- shared state -------------------------------------------------
  var explainCache = {};   // symbol -> { loading, ts, data, error }
  var openSymbols = {};    // symbol -> true (holding row currently expanded)

  var tradeHistoryData = null;   // last fetched activity array (or null = never loaded)
  var tradeHistoryErr = null;
  var tradeHistoryFetchedAt = 0;
  var tradeHistoryMounted = false;
  var tradeHistoryTimer = null;
  var tradeHistoryCardEl = null;

  var thOpen = {};          // row key -> true
  var thExplainCache = {};  // row key -> { loading, ts, data, error }

  // ---- helpers --------------------------------------------------------
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function money(v) {
    var n = Number(v);
    if (!isFinite(n)) n = 0;
    return "$" + n.toFixed(2);
  }

  function fmtQty(v) {
    var n = Number(v);
    if (!isFinite(n)) n = 0;
    var s = n.toFixed(4);
    s = s.replace(/0+$/, "");
    s = s.replace(/\.$/, "");
    return s;
  }

  function fmtTs(ts) {
    var d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts == null ? "" : ts);
    try {
      return d.toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      });
    } catch (e) {
      return d.toString();
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

  function fetchJson(url) {
    return fetch(url, { credentials: "same-origin", headers: apiHeaders() }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }

  function sectionHtml(label, arr) {
    if (!arr || !arr.length) return "";
    var out = '<div class="qtxp-sec">' +
      '<div class="text-[9px] uppercase tracking-wider text-faint">' + esc(label) + "</div>";
    for (var i = 0; i < arr.length; i++) {
      out += '<p class="mt-0.5 text-[11px] leading-4 text-muted">' + esc(arr[i]) + "</p>";
    }
    out += "</div>";
    return out;
  }

  function explainSectionsHtml(entry) {
    if (!entry || entry.loading) {
      return '<p class="text-[10px] text-faint">Assembling the decision trail…</p>';
    }
    if (entry.error) {
      return '<p class="text-[10px] text-faint">Decision detail is unavailable for this entry.</p>';
    }
    var d = entry.data || {};
    return (
      sectionHtml("What fired", d.how) +
      sectionHtml("Why it's in the book", d.picked_by) +
      sectionHtml("Regime right now", d.regime)
    );
  }

  // ---- shared mount anchor --------------------------------------------
  // Present whenever the Book page's "Positions & allocation drift" card
  // has a loaded, non-stale portfolio — even with zero open positions
  // (unlike the <table>, which the SPA swaps out entirely in that case).
  function findBookAllocSection() {
    var anchor = document.querySelector('[data-qt-slot="book-alloc-top"]');
    if (anchor) {
      var section = anchor.closest ? anchor.closest("section") : null;
      if (section) return section;
    }
    // Fallback for older SPA builds (e.g. the GCP dist) that predate the
    // book-alloc-top slot: identify the positions table by its header row —
    // require both SYMBOL and a value/P-L column so a symbol table on some
    // other tab can't false-match — and use its enclosing section Card.
    var tables = document.querySelectorAll("table.w-full.border-collapse");
    for (var i = 0; i < tables.length; i++) {
      var head = tables[i].querySelector("thead");
      var txt = head ? (head.textContent || "") : "";
      if (/symbol/i.test(txt) && /(mkt value|unrl|market value|p\/l)/i.test(txt)) {
        var sec = tables[i].closest ? tables[i].closest("section") : null;
        if (sec) return sec;
      }
    }
    return null;
  }

  function findPositionsTable(section) {
    if (!section) return null;
    return section.querySelector("table.w-full.border-collapse");
  }

  // ======================================================================
  // Feature 1 — holding explain dropdowns
  // ======================================================================

  function rowSymbol(tr) {
    var td = tr.querySelector("td.font-semibold");
    return td ? td.textContent.replace(/^\s+|\s+$/g, "") : "";
  }

  function findRowBySymbol(table, symbol) {
    var rows = table.querySelectorAll("tbody tr:not(.qtxp-panel)");
    for (var i = 0; i < rows.length; i++) {
      if (rowSymbol(rows[i]) === symbol) return rows[i];
    }
    return null;
  }

  function headerColCount(table) {
    var ths = table.querySelectorAll("thead th");
    return ths.length || 5;
  }

  function removeHoldingPanel(table, symbol) {
    var panels = table.querySelectorAll('tbody tr.qtxp-panel[data-qtxp-symbol="' + cssEscape(symbol) + '"]');
    for (var i = 0; i < panels.length; i++) panels[i].parentNode.removeChild(panels[i]);
  }

  // Minimal CSS.escape shim — symbols are plain tickers (A-Z0-9.-) so this
  // is just a defensive belt for anything odd slipping through.
  function cssEscape(s) {
    return String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  function buildHoldingPanelRow(table, symbol) {
    var tr = document.createElement("tr");
    tr.className = "qtxp-panel";
    tr.setAttribute("data-qtxp-symbol", symbol);
    var td = document.createElement("td");
    td.colSpan = headerColCount(table);
    td.className = "qtxp-td px-2 py-2";
    td.innerHTML =
      '<div class="qtxp-body ml-1 flex flex-col gap-2 rounded border border-bd/60 bg-panel2 px-2.5 py-2">' +
      explainSectionsHtml(explainCache[symbol]) +
      "</div>";
    tr.appendChild(td);
    return tr;
  }

  function refreshHoldingPanel(table, symbol) {
    var panel = table.querySelector('tbody tr.qtxp-panel[data-qtxp-symbol="' + cssEscape(symbol) + '"]');
    if (!panel) return;
    var td = panel.querySelector("td");
    if (!td) return;
    var body = td.querySelector(".qtxp-body");
    if (body) body.innerHTML = explainSectionsHtml(explainCache[symbol]);
  }

  function fetchHoldingExplain(symbol, done) {
    var entry = explainCache[symbol];
    var now = Date.now();
    if (entry && !entry.loading && !entry.error && (now - entry.ts) < EXPLAIN_CACHE_MS) {
      done();
      return;
    }
    explainCache[symbol] = { loading: true, ts: now };
    var url = "/api/activity_detail/alpaca_live?symbol=" + encodeURIComponent(symbol) +
      "&side=HOLD&mode=holding";
    fetchJson(url).then(function (d) {
      explainCache[symbol] = { loading: false, ts: Date.now(), data: d };
      done();
    }, function () {
      explainCache[symbol] = { loading: false, ts: Date.now(), error: true };
      done();
    });
  }

  function insertOrRefreshHoldingPanel(table, tr, symbol) {
    var next = tr.nextElementSibling;
    if (next && next.classList.contains("qtxp-panel") && next.getAttribute("data-qtxp-symbol") === symbol) {
      refreshHoldingPanel(table, symbol);
      return;
    }
    removeHoldingPanel(table, symbol);
    var panelRow = buildHoldingPanelRow(table, symbol);
    if (tr.nextSibling) tr.parentNode.insertBefore(panelRow, tr.nextSibling);
    else tr.parentNode.appendChild(panelRow);
  }

  function onHoldingRowClick(table, tr) {
    var symbol = rowSymbol(tr);
    if (!symbol) return; // defensive: selector missed, do nothing
    if (openSymbols[symbol]) {
      delete openSymbols[symbol];
      removeHoldingPanel(table, symbol);
      return;
    }
    openSymbols[symbol] = true;
    insertOrRefreshHoldingPanel(table, tr, symbol);
    fetchHoldingExplain(symbol, function () {
      // table may have re-rendered by the time the fetch resolves — re-find it
      var freshSection = findBookAllocSection();
      var freshTable = findPositionsTable(freshSection);
      if (freshTable) refreshHoldingPanel(freshTable, symbol);
    });
  }

  function bindPositionRows() {
    var section = findBookAllocSection();
    var table = findPositionsTable(section);
    if (!table) return; // no positions table right now (not on Book page, or zero holdings) — do nothing

    var rows = table.querySelectorAll("tbody tr:not(.qtxp-panel)");
    for (var i = 0; i < rows.length; i++) {
      var tr = rows[i];
      if (tr.getAttribute("data-qtxp-bound") === "1") continue;
      tr.setAttribute("data-qtxp-bound", "1");
      tr.style.cursor = "pointer";
      tr.setAttribute("title", "Tap for why this holding is in the book");
      (function (rowEl) {
        rowEl.addEventListener("click", function () { onHoldingRowClick(table, rowEl); });
      })(tr);
    }

    // Re-insert any open panel React's reconciliation wiped out.
    for (var symbol in openSymbols) {
      if (!openSymbols.hasOwnProperty(symbol)) continue;
      var ownerTr = findRowBySymbol(table, symbol);
      if (!ownerTr) continue; // symbol no longer held — leave state, harmless
      var next = ownerTr.nextElementSibling;
      if (!next || !next.classList.contains("qtxp-panel") || next.getAttribute("data-qtxp-symbol") !== symbol) {
        insertOrRefreshHoldingPanel(table, ownerTr, symbol);
      }
    }
  }

  // ======================================================================
  // Feature 2 — Trade History card
  // ======================================================================

  function tradeRowKey(a, i) {
    return a.order_id ? String(a.order_id) : (String(a.ts) + "-" + i);
  }

  function tradeExplainBodyHtml(key, a) {
    var out = "";
    if (a.reason) out += '<p class="text-[10px] italic text-faint">' + esc(a.reason) + "</p>";
    out += explainSectionsHtml(thExplainCache[key]);
    return out;
  }

  function tradeHistoryRowHtml(a, key, open) {
    var side = String(a.side || "").toUpperCase();
    var sideClass = side === "BUY" ? "text-pos" : side === "SELL" ? "text-neg" : "text-muted";
    var out = '<li class="flex flex-col gap-0.5 py-1.5">' +
      '<button type="button" class="qtxp-th-row flex w-full items-center justify-between gap-2 text-left" ' +
      'data-qtxp-th-key="' + esc(key) + '">' +
      '<div class="flex min-w-0 items-center gap-2">' +
      '<span class="text-[9px] transition-transform' + (open ? " qtxp-rot" : "") + '">▶</span>' +
      '<span class="' + sideClass + ' text-[10px] font-semibold uppercase">' + esc(side) + "</span>" +
      '<span class="text-xs font-semibold">' + esc(a.symbol) + "</span>" +
      '<span class="num text-[10px] text-muted">' + fmtQty(a.qty) + " @ " + money(a.price) + "</span>" +
      '<span class="text-[9px] text-faint">' + esc(a.status || "") + "</span>" +
      "</div>" +
      '<span class="num shrink-0 whitespace-nowrap text-[10px] text-faint">' + esc(fmtTs(a.ts)) + "</span>" +
      "</button>";
    if (!open && a.reason) {
      out += '<p class="pl-5 text-[10px] leading-4 text-muted">' + esc(a.reason) + "</p>";
    }
    if (open) {
      out += '<div class="qtxp-body ml-5 mt-1 flex flex-col gap-2 rounded border border-bd/60 bg-panel2 px-2.5 py-2">' +
        tradeExplainBodyHtml(key, a) + "</div>";
    }
    out += "</li>";
    return out;
  }

  function fetchTradeExplain(key, a) {
    var entry = thExplainCache[key];
    var now = Date.now();
    if (entry && !entry.loading && !entry.error && (now - entry.ts) < EXPLAIN_CACHE_MS) return;
    thExplainCache[key] = { loading: true, ts: now };
    var url = "/api/activity_detail/alpaca_live?symbol=" + encodeURIComponent(a.symbol) +
      "&side=" + encodeURIComponent(a.side) +
      "&reason=" + encodeURIComponent(a.reason || "");
    fetchJson(url).then(function (d) {
      thExplainCache[key] = { loading: false, ts: Date.now(), data: d };
      renderTradeHistory();
    }, function () {
      thExplainCache[key] = { loading: false, ts: Date.now(), error: true };
      renderTradeHistory();
    });
  }

  function onTradeRowClick(key) {
    if (!tradeHistoryData) return;
    var idx = -1, a = null;
    for (var i = 0; i < tradeHistoryData.length; i++) {
      if (tradeRowKey(tradeHistoryData[i], i) === key) { idx = i; a = tradeHistoryData[i]; break; }
    }
    if (idx === -1 || !a) return;
    if (thOpen[key]) {
      delete thOpen[key];
    } else {
      thOpen[key] = true;
      fetchTradeExplain(key, a);
    }
    renderTradeHistory();
  }

  function bindTradeHistoryRows(body) {
    var btns = body.querySelectorAll(".qtxp-th-row");
    for (var i = 0; i < btns.length; i++) {
      (function (btn) {
        btn.addEventListener("click", function () {
          onTradeRowClick(btn.getAttribute("data-qtxp-th-key"));
        });
      })(btns[i]);
    }
  }

  function renderTradeHistory() {
    if (!tradeHistoryCardEl) return;
    var body = tradeHistoryCardEl.querySelector("#qtxp-th-body");
    var ageEl = tradeHistoryCardEl.querySelector("#qtxp-th-age");
    if (!body) return;

    if (ageEl) {
      if (tradeHistoryFetchedAt) {
        var ageS = Math.max(0, Math.round((Date.now() - tradeHistoryFetchedAt) / 1000));
        ageEl.textContent = ageS < 90 ? ageS + "s" : Math.round(ageS / 60) + "m";
      } else {
        ageEl.textContent = "";
      }
    }

    if (!tradeHistoryData) {
      if (tradeHistoryErr) {
        body.innerHTML = '<p class="text-[10px] text-faint">Trade history unavailable.</p>';
      } else {
        body.innerHTML = '<p class="text-[10px] text-faint">Loading…</p>';
      }
      return;
    }
    if (!tradeHistoryData.length) {
      body.innerHTML = '<p class="text-[10px] text-faint">No trade history yet.</p>';
      return;
    }

    var html = '<ul class="qtxp-th-list flex flex-col divide-y divide-bd/60">';
    for (var i = 0; i < tradeHistoryData.length; i++) {
      var a = tradeHistoryData[i];
      var key = tradeRowKey(a, i);
      html += tradeHistoryRowHtml(a, key, !!thOpen[key]);
    }
    html += "</ul>";
    body.innerHTML = html;
    bindTradeHistoryRows(body);
  }

  function fetchTradeHistory() {
    fetchJson("/api/activity/alpaca_live?limit=25").then(function (list) {
      tradeHistoryData = list || [];
      tradeHistoryErr = null;
      tradeHistoryFetchedAt = Date.now();
      renderTradeHistory();
    }, function (e) {
      tradeHistoryErr = e;
      renderTradeHistory();
    });
  }

  function buildTradeHistoryCard() {
    var card = document.createElement("section");
    card.id = "qtxp-trade-history";
    card.className = "qtxp-card min-w-0 overflow-hidden bg-panel -mx-3 md:mx-0 md:max-w-full md:rounded-lg md:border md:border-bd";
    card.innerHTML =
      '<header class="flex items-center justify-between gap-2 border-b border-bd px-3 py-2">' +
      '<h2 class="text-xs font-semibold uppercase tracking-wider text-muted">Trade History</h2>' +
      '<span class="num text-[10px] text-faint" id="qtxp-th-age"></span>' +
      "</header>" +
      '<div class="py-3 md:p-3" id="qtxp-th-body">' +
      '<p class="text-[10px] text-faint">Loading…</p>' +
      "</div>";
    return card;
  }

  function ensureTradeHistoryCard() {
    var section = findBookAllocSection();
    if (!section || !section.parentNode) {
      // Not on the Book page (or the positions card isn't mounted) — tear down.
      if (tradeHistoryMounted) {
        tradeHistoryMounted = false;
        if (tradeHistoryTimer) { clearInterval(tradeHistoryTimer); tradeHistoryTimer = null; }
      }
      tradeHistoryCardEl = null;
      return;
    }

    var existing = document.getElementById("qtxp-trade-history");
    if (!existing || existing.parentNode !== section.parentNode) {
      var card = buildTradeHistoryCard();
      section.parentNode.insertBefore(card, section.nextSibling);
      tradeHistoryCardEl = card;
      // Fresh node: nothing rendered into it yet even if we already have data.
      renderTradeHistory();
    } else {
      tradeHistoryCardEl = existing;
      // Keep it directly below the positions card even if something else
      // reshuffles the column.
      if (existing.previousElementSibling !== section) {
        section.parentNode.insertBefore(existing, section.nextSibling);
      }
    }

    if (!tradeHistoryMounted) {
      tradeHistoryMounted = true;
      fetchTradeHistory();
      tradeHistoryTimer = setInterval(fetchTradeHistory, TRADE_HISTORY_POLL_MS);
    }
  }

  // ======================================================================
  // Wiring — style injection, MutationObserver, backup poll
  // ======================================================================

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var st = document.createElement("style");
    st.id = STYLE_ID;
    st.textContent =
      ".qtxp-panel td{background:rgba(110,118,129,.05)}" +
      ".qtxp-body{animation:qtxp-fade .15s ease-out}" +
      "@keyframes qtxp-fade{from{opacity:0;transform:translateY(-2px)}to{opacity:1;transform:translateY(0)}}" +
      ".qtxp-th-row{cursor:pointer}" +
      ".qtxp-th-row:hover{opacity:.85}" +
      ".qtxp-rot{display:inline-block;transform:rotate(90deg)}" +
      ".qtxp-card{margin-top:0}" +
      ".qtxp-sec+.qtxp-sec{margin-top:2px}";
    document.head.appendChild(st);
  }

  function tick() {
    try {
      bindPositionRows();
      ensureTradeHistoryCard();
    } catch (e) { /* defensive: never let a selector miss break the SPA */ }
  }

  // Ignore mutation batches made up entirely of our own qtxp- prefixed
  // inserts/removes, so re-inserting a panel (or the trade-history card)
  // doesn't itself re-trigger a full re-scan.
  function isOwnMutationBatch(mutations) {
    for (var i = 0; i < mutations.length; i++) {
      var m = mutations[i];
      var nodes = [];
      var j;
      for (j = 0; j < m.addedNodes.length; j++) nodes.push(m.addedNodes[j]);
      for (j = 0; j < m.removedNodes.length; j++) nodes.push(m.removedNodes[j]);
      if (!nodes.length) return false;
      for (j = 0; j < nodes.length; j++) {
        var n = nodes[j];
        if (n.nodeType !== 1) return false;
        var id = n.id || "";
        var cn = typeof n.className === "string" ? n.className : "";
        if (id.indexOf("qtxp-") !== 0 && cn.indexOf("qtxp-") === -1) return false;
      }
    }
    return true;
  }

  function init() {
    injectStyle();
    var mo = new MutationObserver(function (mutations) {
      if (isOwnMutationBatch(mutations)) return;
      tick();
    });
    mo.observe(document.body, { childList: true, subtree: true });
    setInterval(tick, BACKUP_TICK_MS);
    tick();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
