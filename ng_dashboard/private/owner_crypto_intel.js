/* Quarki333 Terminal — owner-private Whale & Social Intel card.
 * Companion to owner_crypto.js: does NOT touch that file or its DOM tree
 * directly. Instead it polls for the #qtcy-overlay pane that owner_crypto.js
 * mounts when the Crypto tab is opened, and appends its own card into it —
 * re-appending whenever owner_crypto.js's own render() wipes and rebuilds
 * that pane (it does `innerHTML = ""` on every fetch tick while open).
 *
 * Data: GET /api/crypto/intel — the composite whale/social signal consumed
 * by crypto_sleeve's whale_gate (source: crypto_sleeve/state/whale_signals.json).
 * Legs: hyperliquid (top-trader perp positioning — real $ evidence), dune
 * (smart-money DEX flows — real $ evidence + token_flows), social (Reddit +
 * Fear&Greed — mood only, zero evidence weight), stocktwits (mood only).
 * The gate's own minimum-evidence floors: n>=5 trades, >=3 wallets, >=$50k —
 * mirrored here as an ARMED/ADVISORY line so the phone view matches what the
 * sleeve is actually gating on.
 *
 * Injected server-side only for owner-mode sessions alongside owner_crypto.js,
 * so /api/crypto/intel is assumed to be owner-gated the same way. Never
 * mounts on the shared/guest Terminal.
 */
(function () {
  "use strict";

  var CONTAINER_ID = "qtcy-overlay";   // owned by owner_crypto.js — read-only
  var CARD_ID = "qtwi-card";
  var STYLE_ID = "qtwi-style";
  var MOUNT_POLL_MS = 1000;            // watch for the pane / re-renders
  var FETCH_POLL_MS = 60000;
  var FLOOR_N = 5, FLOOR_W = 3, FLOOR_VOL = 50000;
  var GREEN = "#3fb950", RED = "#f85149", AMBER = "#d29922";

  var data = null, err = null, cardEl = null, fetchTimer = null, wasMounted = false;

  var css = [
    ".qtwi-card{border:1px solid var(--bd);background:var(--panel);border-radius:8px;",
    "padding:12px;margin:0 auto 10px;max-width:72rem}",
    ".qtwi-num{font-family:ui-monospace,SFMono-Regular,Menlo,Roboto Mono,monospace;",
    "font-variant-numeric:tabular-nums;letter-spacing:-.01em}",
    ".qtwi-muted{color:var(--muted)}.qtwi-faint{color:var(--faint,#6e7681)}",
    ".qtwi-pos{color:#3fb950}.qtwi-neg{color:#f85149}.qtwi-amber{color:#d29922}",
    ".qtwi-h{font-size:9px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}",
    ".qtwi-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px;flex-wrap:wrap}",
    ".qtwi-sec{margin-top:12px;border-top:1px solid var(--bd);padding-top:10px}",
    ".qtwi-stats{display:flex;gap:14px;flex-wrap:wrap;font-size:11px}",
    ".qtwi-stats b{display:block;font-size:13px;font-weight:600}",
    ".qtwi-table{width:100%;border-collapse:collapse;font-size:11px}",
    ".qtwi-table td,.qtwi-table th{padding:3px 0;border-top:1px solid var(--bd);text-align:right}",
    ".qtwi-table td:first-child,.qtwi-table th:first-child{text-align:left}",
    ".qtwi-table th{font-size:9px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);border-top:0}",
    ".qtwi-pill{display:inline-block;border:1px solid currentColor;border-radius:9999px;",
    "padding:1px 8px;font-size:9px;font-weight:700;letter-spacing:.08em}",
    ".qtwi-bar{height:10px;background:var(--panel2,#161b22);border-radius:5px;overflow:hidden}",
    ".qtwi-barfill{height:100%;border-radius:5px}"
  ].join("");

  function esc(s) {
    return String(s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function fmtUsd(v) {
    if (v == null || isNaN(v)) return "—";
    var n = Number(v), sign = n < 0 ? "-" : "";
    n = Math.abs(n);
    if (n >= 1e9) return sign + "$" + (n / 1e9).toFixed(2) + "B";
    if (n >= 1e6) return sign + "$" + (n / 1e6).toFixed(2) + "M";
    if (n >= 1e3) return sign + "$" + (n / 1e3).toFixed(1) + "K";
    return sign + "$" + n.toFixed(0);
  }
  function pctStr(frac) {
    return frac != null && !isNaN(frac) ? (frac * 100).toFixed(1) + "%" : "—";
  }
  function riskCls(frac) {
    return frac != null && frac >= 0.5 ? "qtwi-pos" : frac != null ? "qtwi-neg" : "qtwi-muted";
  }
  function statHtml(label, val, cls) {
    return '<span><span class="qtwi-muted">' + label + '</span><b class="qtwi-num ' +
      (cls || "") + '">' + val + "</b></span>";
  }
  function shortTs(iso) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return esc(String(iso || "—"));
    return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  /* ---- section renderers (every externally-sourced string passes esc()) ---- */
  function renderComposite(d) {
    var ro = d.risk_on;
    var pctVal = ro != null && !isNaN(ro) ? Math.max(0, Math.min(100, ro * 100)) : null;
    var barColor = pctVal != null && pctVal >= 50 ? GREEN : RED;
    var n = d.signal_trades || 0, w = d.distinct_wallets || 0, vol = d.signal_usd || 0;
    var armed = n >= FLOOR_N && w >= FLOOR_W && vol >= FLOOR_VOL;
    var html = '<div class="qtwi-row"><div><b>🐋 WHALE &amp; SOCIAL INTEL</b></div>' +
      (d.asof ? "<span class='qtwi-num qtwi-muted' style='font-size:10px'>as of " +
        esc(shortTs(d.asof)) + "</span>" : "") + "</div>";
    html += '<div style="margin-top:10px"><div class="qtwi-row">' +
      '<span class="qtwi-h" style="margin:0">Composite risk-on</span>' +
      '<span class="qtwi-num" style="font-weight:700;font-size:16px;color:' + barColor + '">' +
      (pctVal != null ? pctVal.toFixed(1) + "%" : "—") + "</span></div>" +
      '<div class="qtwi-bar"><div class="qtwi-barfill" style="width:' +
      (pctVal != null ? pctVal : 0) + "%;background:" + barColor + '"></div></div></div>';
    html += '<div class="qtwi-row" style="margin-top:10px">' +
      '<span class="qtwi-pill ' + (armed ? "qtwi-pos" : "qtwi-amber") + '">' +
      (armed ? "GATE ARMED" : "ADVISORY ONLY") + "</span>" +
      '<span class="qtwi-num qtwi-muted" style="font-size:10px">evidence ' + n + " trades / " + w +
      " wallets / " + esc(fmtUsd(vol)) + " — floors 5 / 3 / $50K</span></div>";
    if (d.leg_weights)
      html += '<div class="qtwi-faint" style="font-size:9px;margin-top:8px">leg weights: ' +
        Object.keys(d.leg_weights).map(function (k) {
          return esc(k) + " " + esc(String(d.leg_weights[k]));
        }).join(" · ") + "</div>";
    return html;
  }

  function renderHyperliquid(leg, fallbackBias) {
    if (!leg) return '<div class="qtwi-h">🐋 Hyperliquid — top-trader positioning</div>' +
      '<div class="qtwi-muted" style="font-size:11px">No Hyperliquid data.</div>';
    var cb = leg.coin_bias || fallbackBias || {};
    var syms = Object.keys(cb).sort(function (a, b) {
      var ta = (cb[a].long_usd || 0) + (cb[a].short_usd || 0);
      var tb = (cb[b].long_usd || 0) + (cb[b].short_usd || 0);
      return tb - ta;
    }).slice(0, 5);
    var rows = syms.map(function (s) {
      var c = cb[s] || {};
      var netCls = c.net_long_ratio != null ? riskCls(c.net_long_ratio) : "qtwi-muted";
      return "<tr><td><b>" + esc(s) + "</b></td>" +
        '<td class="qtwi-num">' + esc(fmtUsd(c.long_usd)) + "</td>" +
        '<td class="qtwi-num">' + esc(fmtUsd(c.short_usd)) + "</td>" +
        '<td class="qtwi-num">' + (c.traders_long || 0) + "L/" + (c.traders_short || 0) + "S</td>" +
        '<td class="qtwi-num ' + netCls + '">' + pctStr(c.net_long_ratio) + "</td></tr>";
    }).join("");
    var html = '<div class="qtwi-h">🐋 Hyperliquid — top-trader positioning</div>' +
      '<div class="qtwi-stats">' +
      statHtml("risk-on", pctStr(leg.risk_on), riskCls(leg.risk_on)) +
      statHtml("long $", esc(fmtUsd(leg.long_usd))) +
      statHtml("short $", esc(fmtUsd(leg.short_usd))) +
      statHtml("wallets", leg.distinct_wallets != null ? leg.distinct_wallets : "—") +
      "</div>";
    if (rows)
      html += '<table class="qtwi-table" style="margin-top:8px"><tr><th>Coin</th><th>Long $</th>' +
        "<th>Short $</th><th>Traders</th><th>Net-long</th></tr>" + rows + "</table>";
    if (leg.asof)
      html += '<div class="qtwi-faint" style="font-size:9px;margin-top:6px">as of ' + esc(shortTs(leg.asof)) + "</div>";
    return html;
  }

  function renderDune(leg, fallbackFlows) {
    if (!leg) return '<div class="qtwi-h">⛓ Dune — smart-money on-chain flows</div>' +
      '<div class="qtwi-muted" style="font-size:11px">No on-chain data.</div>';
    var html = '<div class="qtwi-h">⛓ Dune — smart-money on-chain flows</div>' +
      '<div class="qtwi-stats">' +
      statHtml("risk-on", pctStr(leg.risk_on), riskCls(leg.risk_on)) +
      statHtml("trades", leg.signal_trades != null ? leg.signal_trades : "—") +
      statHtml("wallets", leg.distinct_wallets != null ? leg.distinct_wallets : "—") +
      statHtml("vol", esc(fmtUsd(leg.signal_usd))) +
      "</div>";
    var flows = (leg.token_flows && leg.token_flows.length) ? leg.token_flows : (fallbackFlows || []);
    if (flows.length) {
      var rows = flows.slice(0, 8).map(function (t) {
        return "<tr><td><b>" + esc(t.symbol) + "</b></td>" +
          '<td class="qtwi-num">' + esc(fmtUsd(t.buy_usd)) + "</td>" +
          '<td class="qtwi-num">' + (t.wallets != null ? t.wallets : "—") + "</td></tr>";
      }).join("");
      html += '<div class="qtwi-h" style="margin-top:10px">🛒 WHAT THE WHALES ARE BUYING</div>' +
        '<table class="qtwi-table"><tr><th>Symbol</th><th>Buy $</th><th>#Wallets</th></tr>' + rows + "</table>";
    }
    if (leg.asof)
      html += '<div class="qtwi-faint" style="font-size:9px;margin-top:6px">as of ' + esc(shortTs(leg.asof)) + "</div>";
    return html;
  }

  function renderSocial(leg) {
    if (!leg) return '<div class="qtwi-h">💬 Reddit &amp; sentiment</div>' +
      '<div class="qtwi-muted" style="font-size:11px">No social data.</div>';
    var det = leg.detail || {};
    var html = '<div class="qtwi-h">💬 Reddit &amp; sentiment</div>' +
      '<div class="qtwi-stats">' +
      statHtml("mood risk-on", pctStr(leg.risk_on), riskCls(leg.risk_on)) +
      statHtml("fear &amp; greed", det.fear_greed != null ? det.fear_greed : "—") +
      statHtml("reddit bull", det.reddit_bull != null ? det.reddit_bull : "—", "qtwi-pos") +
      statHtml("reddit bear", det.reddit_bear != null ? det.reddit_bear : "—", "qtwi-neg") +
      "</div>";
    var buzz = det.reddit_buzz || {};
    var bsyms = Object.keys(buzz).sort(function (a, b) { return (buzz[b] || 0) - (buzz[a] || 0); });
    if (bsyms.length) {
      var rows = bsyms.map(function (s) {
        return "<tr><td><b>" + esc(s) + "</b></td><td class='qtwi-num'>" + esc(String(buzz[s])) + "</td></tr>";
      }).join("");
      html += '<table class="qtwi-table" style="margin-top:8px"><tr><th>Coin</th><th>Buzz</th></tr>' + rows + "</table>";
    }
    if (det.trending && det.trending.length)
      html += '<div class="qtwi-faint" style="font-size:10px;margin-top:6px">CoinGecko trending: ' +
        det.trending.map(function (s) { return esc(s); }).join(" · ") + "</div>";
    if (leg.asof)
      html += '<div class="qtwi-faint" style="font-size:9px;margin-top:6px">as of ' + esc(shortTs(leg.asof)) + "</div>";
    return html;
  }

  function renderStocktwits(leg) {
    if (!leg) return '<div class="qtwi-h">🐦 StockTwits</div>' +
      '<div class="qtwi-muted" style="font-size:11px">No StockTwits data.</div>';
    var det = leg.detail || {};
    var html = '<div class="qtwi-h">🐦 StockTwits</div>' +
      '<div class="qtwi-stats">' +
      statHtml("mood risk-on", pctStr(leg.risk_on), riskCls(leg.risk_on)) +
      statHtml("bullish", det.bullish != null ? det.bullish : "—", "qtwi-pos") +
      statHtml("bearish", det.bearish != null ? det.bearish : "—", "qtwi-neg") +
      "</div>";
    var per = det.per_coin || {};
    var syms = Object.keys(per).sort(function (a, b) {
      var pa = per[a] || {}, pb = per[b] || {};
      return ((pb.bullish || 0) + (pb.bearish || 0)) - ((pa.bullish || 0) + (pa.bearish || 0));
    });
    if (syms.length) {
      var rows = syms.map(function (s) {
        var p = per[s] || {};
        return "<tr><td><b>" + esc(s) + "</b></td>" +
          '<td class="qtwi-num qtwi-pos">' + (p.bullish != null ? p.bullish : 0) + "</td>" +
          '<td class="qtwi-num qtwi-neg">' + (p.bearish != null ? p.bearish : 0) + "</td></tr>";
      }).join("");
      html += '<table class="qtwi-table" style="margin-top:8px"><tr><th>Coin</th><th>Bull</th><th>Bear</th></tr>' + rows + "</table>";
    }
    if (leg.asof)
      html += '<div class="qtwi-faint" style="font-size:9px;margin-top:6px">as of ' + esc(shortTs(leg.asof)) + "</div>";
    return html;
  }

  function render() {
    if (!cardEl) return;
    var body;
    if (err) {
      body = '<div class="qtwi-row"><div><b>🐋 WHALE &amp; SOCIAL INTEL</b></div></div>' +
        '<div class="qtwi-neg" style="margin-top:8px;font-size:11px">intel fetch failed: ' + esc(err) + "</div>";
    } else if (!data) {
      body = '<div class="qtwi-row"><div><b>🐋 WHALE &amp; SOCIAL INTEL</b></div></div>' +
        '<div class="qtwi-muted" style="margin-top:8px;font-size:11px">loading…</div>';
    } else {
      var legs = data.legs || {};
      body = renderComposite(data) +
        '<div class="qtwi-sec">' + renderHyperliquid(legs.hyperliquid, data.coin_bias) + "</div>" +
        '<div class="qtwi-sec">' + renderDune(legs.dune, data.token_flows) + "</div>" +
        '<div class="qtwi-sec">' + renderSocial(legs.social) + "</div>" +
        '<div class="qtwi-sec">' + renderStocktwits(legs.stocktwits) + "</div>";
    }
    cardEl.innerHTML = body;
  }

  function fetchData() {
    var headers = { accept: "application/json" };
    try {
      var k = localStorage.getItem("qt.apiKey");
      if (k) headers["x-api-key"] = k;
    } catch (e) { /* noop */ }
    fetch("/api/crypto/intel", { headers: headers })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) { data = d; err = null; render(); })
      .catch(function (e) { err = e.message; render(); });
  }

  function buildCardShell() {
    var c = document.createElement("div");
    c.id = CARD_ID;
    c.className = "qtwi-card";
    return c;
  }

  /* Poll for the owner_crypto.js pane and (re-)mount our card into it. Runs
   * every MOUNT_POLL_MS regardless of whether the pane is currently open —
   * cheap DOM lookups only; the network fetch loop is gated on the pane
   * actually existing so we never poll /api/crypto/intel while the Crypto
   * tab is closed. */
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
      cardEl = buildCardShell();
      ov.appendChild(cardEl);
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

  function init() {
    injectCss();
    ensureMounted();
    setInterval(ensureMounted, MOUNT_POLL_MS);
  }

  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
