"""
Flask web dashboard for IBKR live trading.

Endpoints:
  GET  /                    — dashboard (password-gated)
  GET  /api/status          — account + nav metrics
  GET  /api/positions       — open positions
  GET  /api/strategies      — QC strategy leaderboard
  GET  /api/equity_history  — equity curve data
  GET  /api/transactions    — recent transactions
  POST /api/rebalance       — trigger manual rebalance
  POST /api/qc_refresh      — force QC leaderboard refresh
  GET  /api/log             — tail of log buffer
"""
import os
os.environ.setdefault("QC_BROKER", "ibkr")

import json
import os
import threading
import datetime as dt
from collections import deque
from functools import wraps
from zoneinfo import ZoneInfo

from flask import (Flask, jsonify, request, render_template_string,
                   Response, session, redirect, url_for)

import portfolio_engine as pe
import broker as md
from config import WEB_PASSWORD

ET = ZoneInfo("America/New_York")

# ── In-memory log buffer (last 200 lines) ─────────────────────────────────
_log_buffer: deque = deque(maxlen=200)

import logging as _logging

class _BufHandler(_logging.Handler):
    def emit(self, record):
        _log_buffer.appendleft(self.format(record))

_buf_handler = _BufHandler()
_buf_handler.setFormatter(_logging.Formatter("%(asctime)s  %(message)s",
                                              datefmt="%H:%M:%S"))
_logging.getLogger("ibkr_portfolio").addHandler(_buf_handler)
_logging.getLogger("ibkr_portfolio").setLevel(_logging.INFO)


# ── Login page ────────────────────────────────────────────────────────────

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QC · IBKR — Sign In</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f0f1a; color: #e0e0e0;
         font-family: 'Helvetica Neue', sans-serif;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; }
  .card { background: #1a1a2e; border: 1px solid #1e1e3a; border-radius: 12px;
          padding: 40px 36px; width: 100%; max-width: 360px; }
  .logo { color: #00d4ff; font-size: 22px; font-weight: 700; margin-bottom: 4px; }
  .sub  { color: #888; font-size: 13px; margin-bottom: 32px; }
  .badge { display: inline-block; background: #ff9f43; color: #000;
           font-size: 11px; font-weight: 700; padding: 2px 8px;
           border-radius: 3px; margin-left: 8px; vertical-align: middle; }
  label { display: block; font-size: 11px; color: #888; margin-bottom: 6px; }
  input { width: 100%; background: #111128; border: 1px solid #1e1e3a;
          border-radius: 6px; color: #e0e0e0; font-size: 16px;
          padding: 12px 14px; margin-bottom: 16px; outline: none;
          -webkit-appearance: none; }
  input:focus { border-color: #00d4ff; }
  button { width: 100%; background: #00d4ff; color: #000; font-size: 15px;
           font-weight: 700; padding: 13px; border: none; border-radius: 6px;
           cursor: pointer; margin-top: 4px; -webkit-appearance: none; }
  button:active { opacity: .85; }
  .err { color: #ff4d4d; font-size: 13px; margin-bottom: 14px; text-align: center; }
</style>
</head>
<body>
<div class="card">
  <div class="logo">QC Portfolio <span class="badge">IBKR LIVE</span></div>
  <div class="sub">Trading dashboard — sign in to continue</div>
  {% if error %}<div class="err">{{ error }}</div>{% endif %}
  <form method="post" action="/login">
    <label>Password</label>
    <input type="password" name="password" placeholder="••••••••"
           autofocus autocomplete="current-password">
    <button type="submit">Sign In</button>
  </form>
</div>
</body>
</html>
"""

# ── Dashboard HTML ─────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QC · IBKR Live</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg:       #0f0f1a;
    --card:     #1a1a2e;
    --nav:      #0a0a14;
    --div:      #1e1e3a;
    --accent:   #00d4ff;
    --green:    #00ff88;
    --red:      #ff4d4d;
    --amber:    #f9ca24;
    --text:     #e0e0e0;
    --dim:      #888888;
    --c1: #00d4ff; --c2: #00ff88; --c3: #ff9f43; --c4: #a29bfe; --c5: #fd79a8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: 'Helvetica Neue', sans-serif; min-height: 100vh; }

  /* NAV */
  nav { background: var(--nav); height: 52px; display: flex; align-items: center;
        padding: 0 20px; gap: 12px; border-bottom: 1px solid var(--div); position: sticky; top: 0; z-index: 100; }
  .nav-title { color: var(--accent); font-size: 16px; font-weight: 700; margin-right: 8px; }
  .badge { background: #ff9f43; color: #000; font-size: 10px; font-weight: 700;
           padding: 2px 8px; border-radius: 3px; }
  .badge.paper { background: var(--green); }
  .nav-spacer { flex: 1; }
  .nav-metric { text-align: right; padding: 0 14px; }
  .nav-metric .val { font-size: 13px; font-weight: 700; }
  .nav-metric .lbl { font-size: 10px; color: var(--dim); }
  .nav-btn { background: transparent; border: 1px solid var(--div); color: var(--dim);
             font-size: 11px; padding: 4px 12px; border-radius: 4px; cursor: pointer;
             transition: all .15s; }
  .nav-btn:hover { border-color: var(--accent); color: var(--accent); }
  .nav-btn.danger:hover { border-color: var(--amber); color: var(--amber); }
  .nav-btn:disabled { opacity: .4; cursor: not-allowed; }

  /* MAIN GRID */
  .grid { display: grid; grid-template-columns: 300px 1fr 320px;
          grid-template-rows: auto auto; gap: 10px; padding: 10px; height: calc(100vh - 52px); }

  /* CARDS */
  .card { background: var(--card); border-radius: 6px; border: 1px solid var(--div); overflow: hidden; }
  .card-hdr { background: #12122a; padding: 8px 14px; font-size: 11px; font-weight: 700;
              color: var(--accent); letter-spacing: .5px; border-bottom: 1px solid var(--div); }
  .card-body { padding: 10px; overflow-y: auto; }

  /* LEFT: STRATEGIES */
  .strat-col { grid-row: 1 / 3; display: flex; flex-direction: column; gap: 8px;
               overflow-y: auto; max-height: calc(100vh - 72px); }
  .strat-card { background: var(--card); border: 1px solid var(--div); border-radius: 6px;
                padding: 10px 12px; cursor: pointer; transition: border-color .15s; }
  .strat-card:hover { border-color: var(--accent); }
  .strat-top { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .rank-badge { font-size: 9px; font-weight: 700; padding: 2px 7px; border-radius: 3px; color: #000; }
  .strat-name { font-size: 11px; font-weight: 600; flex: 1; }
  .strat-metrics { display: flex; gap: 10px; font-size: 10px; }
  .strat-bar-bg { height: 3px; background: #111128; border-radius: 2px; margin-top: 6px; }
  .strat-bar-fg { height: 3px; border-radius: 2px; }

  /* CENTER: EQUITY */
  .center-col { grid-row: 1 / 2; display: flex; flex-direction: column; }
  .eq-summary { display: flex; gap: 0; padding: 12px 16px; border-bottom: 1px solid var(--div); }
  .eq-metric { padding: 0 20px; border-right: 1px solid var(--div); }
  .eq-metric:first-child { padding-left: 0; }
  .eq-metric:last-child { border-right: none; }
  .eq-metric .lbl { font-size: 10px; color: var(--dim); margin-bottom: 2px; }
  .eq-metric .val { font-size: 20px; font-weight: 700; }
  .chart-wrap { flex: 1; padding: 8px; min-height: 200px; }

  /* RIGHT: POSITIONS */
  .pos-col { grid-row: 1 / 3; display: flex; flex-direction: column; gap: 8px;
             overflow-y: auto; max-height: calc(100vh - 72px); }
  .pos-card { background: var(--card); border-radius: 6px; padding: 0; overflow: hidden; }
  .pos-top { display: flex; align-items: center; padding: 8px 10px; background: #16213e; gap: 8px; }
  .pos-sym { font-size: 15px; font-weight: 700; }
  .dir-badge { font-size: 9px; font-weight: 700; padding: 2px 6px; border-radius: 3px; color: #fff; }
  .pos-pnl { margin-left: auto; font-size: 12px; font-weight: 700; }
  .pos-body { padding: 8px 10px; font-size: 11px; color: var(--dim); }
  .pos-price { font-size: 13px; font-weight: 700; color: var(--text); margin-bottom: 4px; }
  .pos-bar-bg { height: 4px; background: #111128; border-radius: 2px; margin: 6px 0 4px; }
  .pos-bar-fg { height: 4px; border-radius: 2px; }

  /* BOTTOM: LOG */
  .log-col { grid-column: 2; grid-row: 2; }
  .log-body { font-family: 'Courier New', monospace; font-size: 11px;
              color: var(--dim); max-height: 180px; overflow-y: auto; }
  .log-body div { padding: 1px 0; border-bottom: 1px solid #111; }
  .log-body div:hover { background: #111128; }

  /* TX TABLE */
  .tx-col { grid-column: 1 / 3; grid-row: 3; }
  table { width: 100%; border-collapse: collapse; font-size: 11px; }
  th { color: var(--dim); font-weight: 600; padding: 5px 10px; border-bottom: 1px solid var(--div);
       text-align: left; background: #12122a; }
  td { padding: 5px 10px; border-bottom: 1px solid var(--div); }
  tr:hover td { background: #12122a; }
  .buy  { color: var(--green); }
  .sell { color: var(--red); }

  .green  { color: var(--green); }
  .red    { color: var(--red); }
  .amber  { color: var(--amber); }
  .dim    { color: var(--dim); }
  .accent { color: var(--accent); }

  #spinner { display: none; width: 12px; height: 12px; border: 2px solid var(--div);
             border-top-color: var(--accent); border-radius: 50%; animation: spin .6s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #last-update { font-size: 10px; color: var(--dim); }

  @media (max-width: 1100px) {
    .grid { grid-template-columns: 260px 1fr; grid-template-rows: auto auto auto; }
    .pos-col { grid-column: 1 / 3; grid-row: 2; flex-direction: row; flex-wrap: wrap; max-height: none; }
    .pos-card { min-width: 240px; flex: 1; }
    .log-col { grid-column: 1 / 3; grid-row: 3; }
  }
</style>
</head>
<body>

<nav>
  <span class="nav-title">QC Portfolio</span>
  <span class="badge">IBKR LIVE</span>
  <div style="width:20px"></div>
  <div class="nav-metric">
    <div class="lbl">Equity</div>
    <div class="val" id="nav-equity">—</div>
  </div>
  <div class="nav-metric">
    <div class="lbl">Day P&amp;L</div>
    <div class="val" id="nav-daypnl">—</div>
  </div>
  <div class="nav-metric">
    <div class="lbl">Total P&amp;L</div>
    <div class="val" id="nav-totalpnl">—</div>
  </div>
  <div class="nav-metric">
    <div class="lbl">Session</div>
    <div class="val" id="nav-session">—</div>
  </div>
  <div class="nav-spacer"></div>
  <div id="spinner"></div>
  <span id="last-update"></span>
  <button class="nav-btn" id="btn-refresh" onclick="refresh()">⟳ Refresh</button>
  <button class="nav-btn" id="btn-qc"      onclick="qcRefresh()">QC Refresh</button>
  <button class="nav-btn" id="btn-reb"     onclick="rebalance()">⚖ Rebalance</button>
  <button class="nav-btn" onclick="window.open('/flow','_blank')" title="Trade Flow Visualizer">⬡ Flow</button>
</nav>

<div class="grid">
  <!-- LEFT: strategies -->
  <div class="strat-col" id="strat-col">
    <div class="card">
      <div class="card-hdr">TOP 5 STRATEGIES <span id="qc-ts" class="dim" style="font-weight:400;float:right"></span></div>
    </div>
    <div id="strat-cards"></div>
  </div>

  <!-- CENTER: equity curve -->
  <div class="center-col card">
    <div class="eq-summary">
      <div class="eq-metric"><div class="lbl">Starting Capital</div><div class="val" id="eq-start">Awaiting funding</div></div>
      <div class="eq-metric"><div class="lbl">Total Equity</div><div class="val" id="eq-equity">—</div></div>
      <div class="eq-metric"><div class="lbl">Total Return</div><div class="val" id="eq-pctpnl">—</div></div>
      <div class="eq-metric"><div class="lbl">Total P&amp;L</div><div class="val" id="eq-totalpnl">—</div></div>
      <div class="eq-metric"><div class="lbl">Cash</div><div class="val dim" id="eq-cash">—</div></div>
      <div class="eq-metric"><div class="lbl">Positions</div><div class="val dim" id="eq-poscount">0</div></div>
    </div>
    <div class="chart-wrap">
      <canvas id="eq-chart"></canvas>
    </div>
  </div>

  <!-- RIGHT: positions -->
  <div class="pos-col" id="pos-col">
    <div class="card">
      <div class="card-hdr">
        OPEN POSITIONS
        <span id="pos-summary" class="dim" style="font-weight:400;float:right"></span>
      </div>
    </div>
    <div id="pos-cards"></div>
  </div>

  <!-- BOTTOM: log -->
  <div class="log-col card">
    <div class="card-hdr">SYSTEM LOG</div>
    <div class="card-body log-body" id="log-body"></div>
  </div>
</div>

<script>
const COLORS = ['#00d4ff','#00ff88','#ff9f43','#a29bfe','#fd79a8'];
const GREEN = '#00ff88', RED = '#ff4d4d', DIM = '#888888', TEXT = '#e0e0e0';

function clr(v) { return parseFloat(v) >= 0 ? GREEN : RED; }
function fmt(v, pre='$') { const f=parseFloat(v); return pre+(f>=0?'+':'')+f.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}); }
function fmtD(v) { return parseFloat(v) >= 0 ? '+'+parseFloat(v).toFixed(2)+'%' : parseFloat(v).toFixed(2)+'%'; }

// Equity chart
const ctx = document.getElementById('eq-chart').getContext('2d');
const eqChart = new Chart(ctx, {
  type: 'line',
  data: { labels: [], datasets: [{
    label: 'Equity', data: [], borderColor: '#00d4ff',
    backgroundColor: 'rgba(0,212,255,0.06)', fill: true,
    borderWidth: 2, pointRadius: 0, tension: 0.3,
  }]},
  options: {
    responsive: true, maintainAspectRatio: false, animation: false,
    plugins: { legend: { display: false },
               tooltip: { mode: 'index', intersect: false,
                 callbacks: { label: ctx => '$'+ctx.parsed.y.toLocaleString() } } },
    scales: {
      x: { grid: { color: '#1e1e3a' }, ticks: { color: '#888', maxTicksLimit: 8 } },
      y: { grid: { color: '#1e1e3a' }, ticks: { color: '#888',
           callback: v => '$'+Math.round(v/1000)+'k' } },
    },
  }
});

function updateChart(hist) {
  if (!hist || !hist.timestamp) return;
  const labels = hist.timestamp.map(ts => {
    const d = new Date(ts*1000);
    return d.toLocaleDateString('en-US',{month:'short',day:'numeric'});
  });
  eqChart.data.labels = labels;
  eqChart.data.datasets[0].data = hist.equity;
  eqChart.update('none');
}

// Strategies
function renderStrategies(data) {
  const wrap = document.getElementById('strat-cards');
  if (!data.strategies || !data.strategies.length) {
    wrap.innerHTML = '<div class="card card-body dim">Loading strategies…</div>';
    return;
  }
  document.getElementById('qc-ts').textContent = data.last_qc_refresh
    ? data.last_qc_refresh.slice(0,16) : '';

  wrap.innerHTML = data.strategies.map((s, i) => {
    const c = COLORS[i % COLORS.length];
    const stats = s.statistics || {};
    const r3m   = (stats['oos 3m return'] || 0).toFixed(1);
    const sh    = (stats['oos 3m sharpe'] || 0).toFixed(1);
    const w     = (data.weights[i] * 100).toFixed(0);
    const val   = (data.equity * data.weights[i]).toLocaleString('en-US',{maximumFractionDigits:0});
    const pct   = Math.min(100, data.weights[i] * 100);
    return `<div class="strat-card">
      <div class="strat-top">
        <span class="rank-badge" style="background:${c}">#${i+1}</span>
        <span class="strat-name">${s.name}</span>
      </div>
      <div class="strat-metrics">
        <span style="color:${clr(r3m)}">OOS ${parseFloat(r3m)>=0?'+':''}${r3m}%</span>
        <span style="color:${TEXT}">Sh ${sh}</span>
        <span style="color:${c}">Wt ${w}%</span>
        <span style="color:${TEXT}">$${val}</span>
      </div>
      <div class="strat-bar-bg">
        <div class="strat-bar-fg" style="background:${c};width:${pct}%"></div>
      </div>
    </div>`;
  }).join('');
}

// Positions
function renderPositions(positions) {
  const wrap = document.getElementById('pos-cards');
  const summary = document.getElementById('pos-summary');
  if (!positions || !positions.length) {
    wrap.innerHTML = '<div class="card card-body dim" style="text-align:center;padding:30px">No open positions</div>';
    summary.textContent = '';
    return;
  }
  const totalPL = positions.reduce((s,p) => s + parseFloat(p.unrealized_pl||0), 0);
  summary.textContent = `${positions.length} pos  ${totalPL>=0?'+':''}$${totalPL.toFixed(2)}`;
  summary.style.color = clr(totalPL);

  const sorted = [...positions].sort((a,b) =>
    parseFloat(b.unrealized_pl||0) - parseFloat(a.unrealized_pl||0));

  wrap.innerHTML = sorted.map(p => {
    const unpl  = parseFloat(p.unrealized_pl || 0);
    const unpc  = parseFloat(p.unrealized_plpc || 0) * 100;
    const dc    = p.side === 'long' ? GREEN : RED;
    const pc    = clr(unpl);
    const fill  = Math.max(0, Math.min(100, 50 + unpc / 40 * 50));
    const dirTx = p.side === 'long' ? '▲ LONG' : '▼ SHORT';
    return `<div class="pos-card" style="border-left:3px solid ${dc}">
      <div class="pos-top">
        <span class="pos-sym">${p.symbol}</span>
        <span class="dir-badge" style="background:${dc}">${dirTx}</span>
        <span class="pos-pnl" style="color:${pc}">${unpl>=0?'+':''}$${unpl.toFixed(2)} (${unpc>=0?'+':''}${unpc.toFixed(2)}%)</span>
      </div>
      <div class="pos-body">
        <div class="pos-price">$${parseFloat(p.current_price).toFixed(4)}
          <span style="color:${DIM}; font-size:10px"> entry $${parseFloat(p.avg_entry_price).toFixed(4)}</span>
          <span style="float:right; color:${DIM}; font-size:10px">${parseFloat(p.qty).toFixed(4)} sh</span>
        </div>
        <div class="pos-bar-bg"><div class="pos-bar-fg" style="background:${pc};width:${fill}%"></div></div>
        <span style="color:${DIM}">MktVal $${parseFloat(p.market_value).toLocaleString('en-US',{maximumFractionDigits:2})}</span>
      </div>
    </div>`;
  }).join('');
}

// Transactions
function renderTransactions(txs) {
  const tbody = document.getElementById('tx-body');
  if (!tbody) return;
  if (!txs || !txs.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="dim" style="text-align:center;padding:20px">No transactions yet</td></tr>';
    return;
  }
  tbody.innerHTML = txs.map(t => {
    const cls = t.side === 'buy' ? 'buy' : 'sell';
    return `<tr>
      <td class="dim">${(t.timestamp||'').slice(11,19)}</td>
      <td style="font-weight:700">${t.symbol}</td>
      <td class="${cls}">${(t.side||'').toUpperCase()}</td>
      <td>${parseFloat(t.qty||0).toFixed(4)}</td>
      <td>$${parseFloat(t.price||0).toFixed(4)}</td>
      <td style="color:${TEXT}">$${parseFloat(t.value||0).toLocaleString('en-US',{maximumFractionDigits:2})}</td>
      <td class="dim">${t.reason||''}</td>
    </tr>`;
  }).join('');
}

// Session label
function sessionLabel() {
  const now = new Date();
  const et  = new Date(now.toLocaleString('en-US',{timeZone:'America/New_York'}));
  const h = et.getHours(), m = et.getMinutes(), day = et.getDay();
  const mins = h*60+m;
  if (day===0||day===6) return ['● WEEKEND', DIM];
  if (mins >= 570 && mins < 960) return ['● MARKET OPEN', GREEN];
  if (mins >= 240 && mins < 570) return ['● PRE-MARKET', '#f9ca24'];
  if (mins >= 960 && mins < 1200) return ['● AFTER HOURS', '#f9ca24'];
  return ['● OVERNIGHT', DIM];
}

let _busy = false;
function setLoading(on) {
  _busy = on;
  document.getElementById('spinner').style.display = on ? 'block' : 'none';
  ['btn-refresh','btn-qc','btn-reb'].forEach(id => {
    document.getElementById(id).disabled = on;
  });
}

async function refresh() {
  if (_busy) return;
  setLoading(true);
  try {
    const [status, positions, hist, txs] = await Promise.all([
      fetch('/api/status').then(r=>r.json()),
      fetch('/api/positions').then(r=>r.json()),
      fetch('/api/equity_history').then(r=>r.json()),
      fetch('/api/transactions').then(r=>r.json()),
    ]);

    // Nav
    document.getElementById('nav-equity').textContent   = '$'+parseFloat(status.equity||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
    document.getElementById('nav-equity').style.color   = TEXT;

    const dpnl = parseFloat(status.day_pnl||0);
    document.getElementById('nav-daypnl').textContent   = (dpnl>=0?'+':'')+'$'+Math.abs(dpnl).toFixed(2);
    document.getElementById('nav-daypnl').style.color   = clr(dpnl);

    const tpnl    = parseFloat(status.total_pnl||0);
    const hasStart = !!status.initial_equity;
    document.getElementById('nav-totalpnl').textContent = hasStart
      ? (tpnl>=0?'+':'')+'$'+Math.abs(tpnl).toFixed(2) : '—';
    document.getElementById('nav-totalpnl').style.color = hasStart ? clr(tpnl) : DIM;

    const [sess, sc] = sessionLabel();
    document.getElementById('nav-session').textContent = sess;
    document.getElementById('nav-session').style.color = sc;

    // Equity pane
    const startEq = status.initial_equity ? parseFloat(status.initial_equity) : null;
    document.getElementById('eq-start').textContent  = startEq
      ? '$'+startEq.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})
      : 'Awaiting funding';
    document.getElementById('eq-start').style.color  = startEq ? TEXT : DIM;
    document.getElementById('eq-equity').textContent = '$'+parseFloat(status.equity||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
    const pctPnl = startEq > 0 ? (tpnl / startEq * 100) : null;
    document.getElementById('eq-pctpnl').textContent = pctPnl !== null
      ? (pctPnl>=0?'+':'')+pctPnl.toFixed(2)+'%' : '—';
    document.getElementById('eq-pctpnl').style.color = pctPnl !== null ? clr(pctPnl) : DIM;
    document.getElementById('eq-totalpnl').textContent = startEq
      ? (tpnl>=0?'+':'')+'$'+Math.abs(tpnl).toFixed(2) : '—';
    document.getElementById('eq-totalpnl').style.color = clr(tpnl);
    document.getElementById('eq-cash').textContent     = '$'+parseFloat(status.cash||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
    document.getElementById('eq-poscount').textContent = positions.length;

    renderStrategies(status);
    renderPositions(positions);
    updateChart(hist);
    renderTransactions(txs);
    updateLog();

    document.getElementById('last-update').textContent =
      'Updated ' + new Date().toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  } catch(e) {
    console.error(e);
  } finally {
    setLoading(false);
  }
}

async function updateLog() {
  try {
    const lines = await fetch('/api/log').then(r=>r.json());
    const el = document.getElementById('log-body');
    el.innerHTML = lines.map(l => `<div>${l}</div>`).join('');
  } catch(e) {}
}

async function qcRefresh() {
  if (_busy) return;
  setLoading(true);
  try {
    const r = await fetch('/api/qc_refresh', {method:'POST'});
    const d = await r.json();
    alert(d.message || 'QC refresh done');
    await refresh();
  } catch(e) { alert('QC refresh failed: '+e); }
  finally { setLoading(false); }
}

async function rebalance() {
  if (_busy) return;
  if (!confirm('Trigger a full IBKR rebalance now? This will place live orders.')) return;
  setLoading(true);
  try {
    const r = await fetch('/api/rebalance', {method:'POST'});
    const d = await r.json();
    alert(d.message || 'Rebalance done');
    await refresh();
  } catch(e) { alert('Rebalance failed: '+e); }
  finally { setLoading(false); }
}

// Boot
refresh();
setInterval(refresh, 30000);  // auto-refresh every 30s
setInterval(updateLog, 10000); // log refreshes faster
</script>

<!-- Transaction log below the main grid -->
<div style="padding:10px;padding-top:0">
  <div class="card">
    <div class="card-hdr">TRANSACTION LOG</div>
    <div class="card-body" style="padding:0;max-height:200px;overflow-y:auto">
      <table>
        <thead><tr>
          <th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th>
          <th>Price</th><th>Value</th><th>Reason</th>
        </tr></thead>
        <tbody id="tx-body">
          <tr><td colspan="7" class="dim" style="text-align:center;padding:20px">Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

</body>
</html>
"""


# ── Flask app factory ─────────────────────────────────────────────────────

def create_app(rebalance_fn=None, stop_check_fn=None) -> Flask:
    app = Flask(__name__)
    app.secret_key = os.urandom(32)   # session signing key (regenerates on restart — fine)

    _rebalance_lock = threading.Lock()

    def _logged_in():
        return session.get("auth") is True

    def login_required(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            if not _logged_in():
                # API calls get 401; page requests get redirect
                if request.path.startswith("/api/"):
                    return jsonify({"error": "not authenticated"}), 401
                return redirect("/login")
            return f(*args, **kwargs)
        return wrapper

    # ── Auth pages ─────────────────────────────────────────────────────────

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            pw = request.form.get("password", "")
            if pw == WEB_PASSWORD:
                session["auth"] = True
                return redirect("/")
            return render_template_string(LOGIN_HTML, error="Incorrect password")
        return render_template_string(LOGIN_HTML, error=None)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect("/login")

    # ── Pages ──────────────────────────────────────────────────────────────

    @app.route("/")
    @login_required
    def index():
        return render_template_string(DASHBOARD_HTML)

    # ── API ────────────────────────────────────────────────────────────────

    @app.route("/api/status")
    @login_required
    def api_status():
        account   = md.get_account()
        positions = md.get_positions()
        equity    = float(account.get("equity", 0))
        cash      = float(account.get("cash", 0))
        day_pnl   = sum(float(p.get("unrealized_intraday_pl", 0)) for p in positions)
        initial   = pe.get_initial_equity() or equity
        total_pnl = equity - initial

        strats  = pe.get_strategies()
        weights = pe.get_weights()
        state   = pe.get_state()

        return jsonify({
            "equity":           equity,
            "cash":             cash,
            "day_pnl":          day_pnl,
            "total_pnl":        total_pnl,
            "initial_equity":   pe.get_initial_equity() or None,
            "strategies":       strats,
            "weights":          weights,
            "last_qc_refresh":  str(state.get("last_qc_refresh") or ""),
        })

    @app.route("/api/positions")
    @login_required
    def api_positions():
        return jsonify(md.get_positions())

    @app.route("/api/equity_history")
    @login_required
    def api_equity_history():
        return jsonify(pe.get_equity_history())

    @app.route("/api/transactions")
    @login_required
    def api_transactions():
        txs = pe.load_transactions(limit=100)
        return jsonify(list(reversed(txs)))

    @app.route("/api/log")
    @login_required
    def api_log():
        return jsonify(list(_log_buffer))

    @app.route("/api/strategies")
    @login_required
    def api_strategies():
        return jsonify({
            "strategies": pe.get_strategies(),
            "weights":    pe.get_weights(),
        })

    @app.route("/api/rebalance", methods=["POST"])
    @login_required
    def api_rebalance():
        if not rebalance_fn:
            return jsonify({"message": "Rebalance function not configured"}), 500
        if not _rebalance_lock.acquire(blocking=False):
            return jsonify({"message": "Rebalance already running"}), 429
        try:
            threading.Thread(target=_run_and_release,
                             args=(rebalance_fn, _rebalance_lock), daemon=True).start()
            return jsonify({"message": "Rebalance started — check log for progress"})
        except Exception as e:
            _rebalance_lock.release()
            return jsonify({"message": f"Error: {e}"}), 500

    @app.route("/api/qc_refresh", methods=["POST"])
    @login_required
    def api_qc_refresh():
        def _refresh():
            pe.load_state()
            changed = pe.refresh_qc(force=True)
            msg = "Rankings changed" if changed else "No change in top 5"
            _log_buffer.appendleft(f"{dt.datetime.now().strftime('%H:%M:%S')}  QC refresh — {msg}")
        threading.Thread(target=_refresh, daemon=True).start()
        return jsonify({"message": "QC refresh triggered"})

    @app.route("/flow")
    @login_required
    def trade_flow():
        """Winamp-skin fractal Sankey trade-flow visualization."""
        import csv, pathlib
        tx_path = pathlib.Path(__file__).parent / "transactions.csv"
        trades = []
        if tx_path.exists():
            with open(tx_path, newline="") as f:
                for row in csv.DictReader(f):
                    trades.append({
                        "ts": row["timestamp"],
                        "symbol": row["symbol"],
                        "side": row["side"],
                        "qty": float(row["qty"]),
                        "price": float(row["price"]),
                        "value": float(row["value"]),
                        "reason": row.get("reason",""),
                    })
        # Serve the static HTML with live trade data injected
        viz_path = pathlib.Path(__file__).parent / "sankey_viz.html"
        html = viz_path.read_text()
        # Replace static TRADES constant with live data from CSV
        import json as _json
        live_js = f"const TRADES = {_json.dumps(trades)};"
        html = html.replace(
            html[html.find("const TRADES ="):html.find("];", html.find("const TRADES ="))+2],
            live_js
        )
        return html

    return app


def _run_and_release(fn, lock):
    try:
        fn()
    except Exception as e:
        _log_buffer.appendleft(
            f"{dt.datetime.now().strftime('%H:%M:%S')}  ERROR rebalance: {e}")
    finally:
        lock.release()
