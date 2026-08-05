#!/usr/bin/env python3
"""
Build a standalone HTML dashboard for ES=F session-filtered long/short EMA crossover.

Layout style mirrors the mean reversion GUI:
- Left panel: strategy summary + decision list
- Right panel: candlestick chart + EMA lines + decision markers

No changes are made to mean_reversion_bot.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf


OUTPUT_HTML = Path("/Users/quarki3/cme_crossover_chart.html")


def download_es_5m(days: int = 30) -> pd.DataFrame:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    df = yf.download("ES=F", start=start, end=end, interval="5m", progress=False)
    if df.empty:
        raise RuntimeError("No data returned from yfinance for ES=F.")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    return df[["Open", "High", "Low", "Close"]].dropna().copy()


def compute_strategy(df: pd.DataFrame, fast: int = 30, slow: int = 75) -> tuple[pd.DataFrame, list[dict]]:
    out = df.copy()
    out["ema_fast"] = out["Close"].ewm(span=fast, adjust=False).mean()
    out["ema_slow"] = out["Close"].ewm(span=slow, adjust=False).mean()

    prev_fast = out["ema_fast"].shift(1)
    prev_slow = out["ema_slow"].shift(1)
    gc = (prev_fast <= prev_slow) & (out["ema_fast"] > out["ema_slow"])
    dc = (prev_fast >= prev_slow) & (out["ema_fast"] < out["ema_slow"])

    idx_et = out.index.tz_convert("America/New_York") if out.index.tz is not None else out.index
    is_rth = (
        ((idx_et.hour > 9) | ((idx_et.hour == 9) & (idx_et.minute >= 30)))
        & ((idx_et.hour < 16) | ((idx_et.hour == 16) & (idx_et.minute == 0)))
    )

    decisions: list[dict] = []
    side = 0  # -1 short, 0 flat, +1 long
    for i in range(1, len(out)):
        ts = out.index[i]
        if not bool(is_rth[i]):
            if side != 0:
                decisions.append(
                    {
                        "ts": ts,
                        "action": "FLAT (session end)",
                        "from_side": side,
                        "to_side": 0,
                        "price": float(out["Close"].iloc[i]),
                    }
                )
                side = 0
            continue

        old_side = side
        if bool(gc.iloc[i]):
            side = 1
        elif bool(dc.iloc[i]):
            side = -1

        if side != old_side:
            action = "GO LONG" if side == 1 else "GO SHORT"
            decisions.append(
                {
                    "ts": ts,
                    "action": action,
                    "from_side": old_side,
                    "to_side": side,
                    "price": float(out["Close"].iloc[i]),
                }
            )
    return out, decisions


def to_js_time(ts: pd.Timestamp) -> str:
    # ISO with timezone so browser interprets correctly.
    return ts.isoformat()


def build_html(df: pd.DataFrame, decisions: list[dict], fast: int = 30, slow: int = 75) -> str:
    times = [to_js_time(t) for t in df.index]
    opens = [float(v) for v in df["Open"]]
    highs = [float(v) for v in df["High"]]
    lows = [float(v) for v in df["Low"]]
    closes = [float(v) for v in df["Close"]]
    ema_fast = [float(v) for v in df["ema_fast"]]
    ema_slow = [float(v) for v in df["ema_slow"]]

    # Keep the first 25 decisions to match the request wording.
    shown = decisions[:25]
    marker_x = [to_js_time(d["ts"]) for d in shown]
    marker_y = [d["price"] for d in shown]
    marker_text = [f"#{i+1} {d['action']}" for i, d in enumerate(shown)]
    marker_color = ["#3fb950" if d["to_side"] == 1 else "#f85149" for d in shown]
    marker_symbol = ["triangle-up" if d["to_side"] == 1 else "triangle-down" for d in shown]

    rows = []
    for i, d in enumerate(shown, start=1):
        ts_local = d["ts"].tz_convert("America/New_York").strftime("%Y-%m-%d %H:%M:%S ET")
        rows.append(
            {
                "n": i,
                "time": ts_local,
                "action": d["action"],
                "price": round(d["price"], 2),
            }
        )

    payload = {
        "times": times,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "emaFast": ema_fast,
        "emaSlow": ema_slow,
        "markerX": marker_x,
        "markerY": marker_y,
        "markerText": marker_text,
        "markerColor": marker_color,
        "markerSymbol": marker_symbol,
        "rows": rows,
        "fast": fast,
        "slow": slow,
        "decisionsCount": len(shown),
    }
    data_json = json.dumps(payload)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CME Crossover Decisions</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 20px;
      font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
      background: linear-gradient(135deg, #0f1419 0%, #1a1f26 100%);
      color: #e6edf3;
      min-height: 100vh;
    }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
      flex-wrap: wrap;
      gap: 10px;
    }}
    .badge {{
      padding: 4px 10px;
      border-radius: 6px;
      background: #30363d;
      font-size: 12px;
      color: #c9d1d9;
    }}
    .split {{
      display: flex;
      gap: 10px;
      min-height: 720px;
    }}
    .left {{
      width: 36%;
      min-width: 330px;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 16px;
      overflow: auto;
    }}
    .right {{
      flex: 1;
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 12px;
      display: flex;
      flex-direction: column;
    }}
    .stat-grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 10px;
      margin-bottom: 14px;
    }}
    .stat {{
      background: #0d1117;
      border-radius: 8px;
      padding: 10px;
    }}
    .label {{ color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }}
    .value {{ font-size: 16px; font-weight: 600; margin-top: 4px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ border-bottom: 1px solid #21262d; padding: 7px 6px; text-align: left; }}
    th {{ position: sticky; top: 0; background: #161b22; color: #8b949e; }}
    tr:hover {{ background: #21262d; }}
    #chart {{ width: 100%; height: 100%; min-height: 640px; }}
    .hint {{ color: #8b949e; font-size: 12px; margin-bottom: 8px; }}
    @media (max-width: 1000px) {{
      .split {{ flex-direction: column; }}
      .left {{ width: 100%; min-width: 0; }}
    }}
  </style>
</head>
<body>
  <div class="header">
    <h2 style="margin:0;">CME ES Crossover Dashboard</h2>
    <div class="badge">Candles · 5m · Last 30 days · Session-filtered long/short</div>
  </div>
  <div class="split">
    <div class="left">
      <div class="stat-grid">
        <div class="stat">
          <div class="label">Symbol</div>
          <div class="value">ES=F</div>
        </div>
        <div class="stat">
          <div class="label">EMA Pair</div>
          <div class="value">{fast}/{slow}</div>
        </div>
        <div class="stat">
          <div class="label">Bar Size</div>
          <div class="value">5m</div>
        </div>
        <div class="stat">
          <div class="label">Decisions Shown</div>
          <div class="value" id="decision-count"></div>
        </div>
      </div>
      <div class="hint">All decision markers are numbered on the chart (1..N). Click a row to focus its marker.</div>
      <div style="max-height:520px; overflow:auto; border:1px solid #21262d; border-radius:8px;">
        <table>
          <thead>
            <tr><th>#</th><th>Time (ET)</th><th>Action</th><th>Price</th></tr>
          </thead>
          <tbody id="decision-table"></tbody>
        </table>
      </div>
    </div>
    <div class="right">
      <div class="hint">Green triangles = go long. Red triangles = go short. Candlesticks show price.</div>
      <div id="chart"></div>
    </div>
  </div>

  <script>
    const data = {data_json};
    document.getElementById("decision-count").textContent = data.decisionsCount;

    const tbody = document.getElementById("decision-table");
    tbody.innerHTML = data.rows.map((r, idx) =>
      `<tr data-idx="${{idx}}"><td>${{r.n}}</td><td>${{r.time}}</td><td>${{r.action}}</td><td>${{r.price}}</td></tr>`
    ).join("");

    const candle = {{
      type: "candlestick",
      x: data.times,
      open: data.opens,
      high: data.highs,
      low: data.lows,
      close: data.closes,
      name: "ES=F",
      increasing: {{ line: {{ color: "#26a69a" }} }},
      decreasing: {{ line: {{ color: "#ef5350" }} }},
    }};
    const emaFast = {{
      type: "scatter",
      mode: "lines",
      x: data.times,
      y: data.emaFast,
      name: `EMA ${{data.fast}}`,
      line: {{ color: "#00bcd4", width: 1.2 }},
    }};
    const emaSlow = {{
      type: "scatter",
      mode: "lines",
      x: data.times,
      y: data.emaSlow,
      name: `EMA ${{data.slow}}`,
      line: {{ color: "#e91e63", width: 1.2 }},
    }};
    const decisions = {{
      type: "scatter",
      mode: "markers+text",
      x: data.markerX,
      y: data.markerY,
      text: data.markerText.map((t, i) => String(i + 1)),
      textposition: "top center",
      textfont: {{ size: 10, color: "#c9d1d9" }},
      marker: {{
        color: data.markerColor,
        symbol: data.markerSymbol,
        size: 11,
        line: {{ color: "#0d1117", width: 1 }}
      }},
      name: "Decisions",
      customdata: data.markerText,
      hovertemplate: "%{{customdata}}<extra></extra>",
    }};

    const layout = {{
      paper_bgcolor: "#161b22",
      plot_bgcolor: "#0d1117",
      font: {{ color: "#c9d1d9" }},
      margin: {{ l: 50, r: 20, t: 20, b: 45 }},
      xaxis: {{
        rangeslider: {{ visible: false }},
        gridcolor: "#21262d",
      }},
      yaxis: {{
        gridcolor: "#21262d",
        tickformat: ".2f",
      }},
      legend: {{ orientation: "h" }},
      hovermode: "x unified",
    }};
    const config = {{ responsive: true, displaylogo: false }};
    Plotly.newPlot("chart", [candle, emaFast, emaSlow, decisions], layout, config);

    const rows = Array.from(document.querySelectorAll("#decision-table tr"));
    rows.forEach((row) => {{
      row.addEventListener("click", () => {{
        const i = Number(row.dataset.idx);
        const x = data.markerX[i];
        const y = data.markerY[i];
        Plotly.Fx.hover("chart", [{{ curveNumber: 3, pointNumber: i }}]);
        Plotly.relayout("chart", {{
          "xaxis.range[0]": new Date(new Date(x).getTime() - 6 * 60 * 60 * 1000),
          "xaxis.range[1]": new Date(new Date(x).getTime() + 6 * 60 * 60 * 1000),
          "yaxis.autorange": true
        }});
      }});
    }});
  </script>
</body>
</html>
"""


def main() -> None:
    df = download_es_5m(days=30)
    df2, decisions = compute_strategy(df, fast=30, slow=75)
    html = build_html(df2, decisions, fast=30, slow=75)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Saved: {OUTPUT_HTML}")
    print(f"Decision points in dataset: {len(decisions)}")
    print("Displaying first 25 decisions in the dashboard.")


if __name__ == "__main__":
    main()
