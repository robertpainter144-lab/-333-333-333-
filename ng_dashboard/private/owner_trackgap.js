// owner_trackgap.js — TRACKING GAP stat under Positions & Allocation Drift
// (Robert 2026-08-13: quantify the deliberate distance between the governor
// blend and actual holdings — the cost-damper gap — instead of eyeballing
// two bars). Owner-only via the D1 injection pattern; polls /api/trackgap.
(function () {
  const POLL_MS = 60_000;
  let last = null;

  function headingNode() {
    const els = document.querySelectorAll("h1,h2,h3,div,span");
    for (const el of els) {
      if (el.childElementCount === 0 &&
          /POSITIONS\s*&\s*ALLOCATION\s*DRIFT/i.test(el.textContent || "")) {
        return el;
      }
    }
    return null;
  }

  function render() {
    const h = headingNode();
    if (!h || !last || !last.available) return;
    let box = document.getElementById("qt-trackgap");
    if (!box) {
      box = document.createElement("div");
      box.id = "qt-trackgap";
      box.style.cssText =
        "margin:6px 0 2px;padding:6px 10px;font-family:inherit;" +
        "font-size:0.82em;color:#9aa3b2;background:rgba(255,255,255,0.03);" +
        "border-left:3px solid #caa93e;border-radius:4px;line-height:1.5";
    }
    if (h.parentElement && box.previousElementSibling !== h) {
      h.parentElement.insertBefore(box, h.nextSibling);
    }
    const worst = (last.top || [])
      .filter((r) => r.sym.toLowerCase() !== "cash")
      .slice(0, 3)
      .map((r) => `${r.sym} ${r.delta_pct > 0 ? "+" : ""}${r.delta_pct.toFixed(1)}`)
      .join(" · ");
    box.innerHTML =
      `<b style="color:#d7c56a">TRACKING GAP ${last.gap_pct.toFixed(1)}%</b>` +
      ` &nbsp;≈ $${last.gap_usd.toFixed(2)} of $${last.bankroll.toFixed(0)}` +
      ` book would trade to match the blend exactly` +
      (worst ? ` &nbsp;·&nbsp; drift: ${worst}` : "") +
      `<span style="opacity:.65"> — gap is the cost-damper working, not error</span>`;
  }

  async function poll() {
    try {
      const r = await fetch("/api/trackgap", { credentials: "same-origin" });
      if (r.ok) last = await r.json();
    } catch (e) { /* keep last */ }
    render();
  }

  poll();
  setInterval(poll, POLL_MS);
  setInterval(render, 5_000); // survive SPA re-renders between polls

  // ── Per-block P/L labels on the Book page's "current" allocation bar
  // (Robert 2026-08-14: signed dollars.cents, no $ sign, inside each
  // holdings block). Scoped via the dist's book-alloc-top slot so the
  // identical bars on paper/broker tabs are never mislabeled with live
  // numbers. Targets bar untouched — targets have no P/L.
  let plMap = null;

  async function pollPl() {
    try {
      const r = await fetch("/api/portfolio/alpaca_live",
                            { credentials: "same-origin" });
      if (r.ok) {
        const d = await r.json();
        const m = {};
        for (const p of d.positions || []) m[p.symbol] = p.unrealized_pl;
        plMap = m;
      }
    } catch (e) { /* keep last */ }
    decoratePl();
  }

  function decoratePl() {
    if (!plMap) return;
    const slot = document.querySelector('[data-qt-slot="book-alloc-top"]');
    if (!slot || !slot.parentElement) return;
    const bar = slot.parentElement.querySelector("div.h-4.w-full");
    if (!bar) return; // first h-4 bar after the slot = "current"
    const overflow = []; // blocks too narrow → diagonal labels above the bar
    for (const seg of bar.children) {
      const t = seg.getAttribute("title") || "";
      const sym = t.split(" ")[0];
      if (!(sym in plMap)) continue; // skips cash + unknown
      const pl = plMap[sym];
      const txt = (pl >= 0 ? "+" : "") + Number(pl).toFixed(2);
      let lbl = seg.querySelector(".qt-allocpl");
      if (!lbl) {
        lbl = document.createElement("span");
        lbl.className = "qt-allocpl num";
        lbl.style.cssText = "font-size:8px;font-weight:600;color:#0d1117;" +
          "pointer-events:none;white-space:nowrap";
        seg.style.display = "flex";
        seg.style.alignItems = "center";
        seg.style.justifyContent = "center";
        seg.style.overflow = "hidden";
        seg.appendChild(lbl);
      }
      if (lbl.textContent !== txt) lbl.textContent = txt;
      // in-block only where the text genuinely fits — measured, not guessed
      if (lbl.scrollWidth > seg.clientWidth - 2) {
        lbl.style.visibility = "hidden";
        overflow.push({ x: seg.offsetLeft - bar.offsetLeft, txt: txt, pl: pl });
      } else {
        lbl.style.visibility = "visible";
      }
      if (!/P\/L/.test(t)) seg.setAttribute("title", t + " · P/L " + txt);
    }
    // Diagonal labels above the bar (Robert 2026-08-14): each too-small
    // block's P/L starts at that block's x and slants up-right.
    let strip = bar.parentElement.querySelector(".qt-allocpl-strip");
    if (overflow.length) {
      if (!strip) {
        strip = document.createElement("div");
        strip.className = "qt-allocpl-strip";
        strip.style.cssText =
          "position:relative;height:26px;overflow:visible;pointer-events:none";
        bar.parentElement.insertBefore(strip, bar);
      }
      strip.innerHTML = "";
      for (const it of overflow) {
        const s = document.createElement("span");
        s.className = "num";
        s.textContent = it.txt;
        s.style.cssText = "position:absolute;bottom:-2px;left:" + it.x +
          "px;font-size:8px;font-weight:600;white-space:nowrap;" +
          "transform:rotate(-55deg);transform-origin:left bottom;color:" +
          (it.pl >= 0 ? "#3fb950" : "#f85149");
        strip.appendChild(s);
      }
    } else if (strip) {
      strip.remove();
    }
    // retire the earlier legend-P/L experiment — superseded by the strip
    const leg = bar.nextElementSibling;
    if (leg && leg.querySelectorAll) {
      leg.querySelectorAll(".qt-legpl").forEach((e2) => e2.remove());
    }
  }

  pollPl();
  setInterval(pollPl, 60_000);
  setInterval(decoratePl, 5_000); // survive SPA re-renders
})();
