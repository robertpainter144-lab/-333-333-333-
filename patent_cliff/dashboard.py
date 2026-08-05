"""
Renders a single self-contained HTML dashboard from the latest CSV outputs.

Run:
    python3.10 dashboard.py
    python3.10 dashboard.py --open       # also open in browser
"""
from __future__ import annotations

import argparse
import webbrowser
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
DATA = ROOT / "data"
DASH = OUTPUT / "dashboard.html"


def _latest(glob: str, dir: Path = OUTPUT) -> Path | None:
    files = sorted(dir.glob(glob))
    return files[-1] if files else None


def _table_fig(df: pd.DataFrame, title: str, height: int = 360) -> go.Figure:
    fig = go.Figure(data=[go.Table(
        header=dict(values=[f"<b>{c}</b>" for c in df.columns],
                    fill_color="#222", font=dict(color="white", size=12),
                    align="left", height=30),
        cells=dict(values=[df[c].astype(str).tolist() for c in df.columns],
                   align="left", fill_color=["#f9f9f9", "white"] * (len(df) // 2 + 1),
                   font=dict(size=11), height=24),
    )])
    fig.update_layout(title=title, margin=dict(l=10, r=10, t=40, b=10), height=height)
    return fig


def _bar_fig(df: pd.DataFrame, x: str, y: str, title: str, height: int = 400) -> go.Figure:
    fig = go.Figure(go.Bar(x=df[x], y=df[y], marker_color="#4a90e2"))
    fig.update_layout(title=title, margin=dict(l=10, r=10, t=40, b=10),
                      height=height, xaxis_title=x, yaxis_title=y)
    return fig


def _timeline_fig(df: pd.DataFrame, title: str) -> go.Figure:
    """Gantt-ish horizontal bar: each cliff plotted at its LOE date."""
    fig = go.Figure()
    if df.empty: return fig
    df = df.copy()
    df["loe_date"] = pd.to_datetime(df["loe_date"])
    df = df.sort_values("loe_date").head(20)
    fig.add_trace(go.Bar(
        x=df["loe_date"],
        y=df["Trade_Name"],
        orientation="h",
        marker_color=df["score"],
        marker_colorscale="Viridis",
        hovertext=df.apply(
            lambda r: f"{r['Trade_Name']} ({r['Ingredient']})<br>"
                      f"LOE {r['loe_date'].date()}<br>"
                      f"${r.get('sales_usd_b', 0):.1f}B  score {r.get('score', 0):.2f}",
            axis=1),
        hoverinfo="text",
        width=86400_000 * 30,  # one bar = 30 days wide on a millisecond x axis
    ))
    fig.update_layout(title=title, height=600, margin=dict(l=120, r=10, t=40, b=10),
                      xaxis_title="LOE date", yaxis_title="")
    return fig


def render() -> Path:
    cliff_path = _latest("cliff_scan_*.csv")
    ff_path = _latest("first_filer_opportunities_*.csv")
    exp_path = _latest("generic_exposure_*.csv")
    picks_path = _latest("portfolio_picks_*.csv")
    orders_path = _latest("alpaca_orders_*.csv")
    sec_path = DATA / "sec_signals.csv"

    if not cliff_path:
        raise SystemExit("No cliff_scan output yet. Run scan.py first.")

    def _safe_read(p):
        if not p or not Path(p).exists():
            return pd.DataFrame()
        try:
            return pd.read_csv(p)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()

    cliff = _safe_read(cliff_path)
    ff = _safe_read(ff_path)
    exp = _safe_read(exp_path)
    picks = _safe_read(picks_path)
    orders = _safe_read(orders_path)
    sec = _safe_read(sec_path)

    # ── Headline numbers
    n_cliffs = len(cliff)
    n_orange = (cliff["source"] == "orange_book").sum() if "source" in cliff else 0
    n_purple = (cliff["source"] == "purple_book").sum() if "source" in cliff else 0
    n_scored = (cliff["score"] > 0).sum() if "score" in cliff else 0

    # ── Top cliffs table
    top_cliffs_cols = ["source", "Trade_Name", "Ingredient", "Applicant_Full_Name",
                       "loe_date", "months_to_loe", "sales_usd_b", "score"]
    top_cliffs = cliff[[c for c in top_cliffs_cols if c in cliff.columns]].head(20).copy()
    for c in ("months_to_loe", "sales_usd_b", "score"):
        if c in top_cliffs.columns: top_cliffs[c] = top_cliffs[c].round(2)

    figs = []
    figs.append(_table_fig(top_cliffs, "Top 20 patent/biologic cliffs"))
    figs.append(_timeline_fig(cliff.head(50), "Timeline of upcoming LOE events"))

    if not ff.empty:
        ff_cols = ["Trade_Name", "Ingredient", "Applicant_Full_Name", "loe_date",
                   "sales_usd_b", "andas_min", "iv_status", "first_filer_score"]
        ff_show = ff[[c for c in ff_cols if c in ff.columns]].copy()
        if "first_filer_score" in ff_show: ff_show["first_filer_score"] = ff_show["first_filer_score"].round(2)
        if "sales_usd_b" in ff_show: ff_show["sales_usd_b"] = ff_show["sales_usd_b"].round(2)
        figs.append(_table_fig(ff_show, "First-filer opportunities (Eligible + low-ANDA + not-yet-launched)"))

    if not exp.empty:
        exp_show = exp.copy()
        exp_show["total_cliff_exposure"] = exp_show["total_cliff_exposure"].round(2)
        figs.append(_bar_fig(exp_show.head(15), "ticker", "total_cliff_exposure",
                             "Generic / biosimilar maker exposure to upcoming cliffs"))

    if not sec.empty:
        sec_cols = ["ticker", "signal_type", "drug_mentioned", "target_event_window",
                    "sentiment", "confidence", "evidence_quote"]
        sec_show = sec[[c for c in sec_cols if c in sec.columns]].copy()
        if "confidence" in sec_show: sec_show["confidence"] = sec_show["confidence"].round(2)
        figs.append(_table_fig(sec_show.head(25),
                               "Forward-looking SEC signals (Claude-extracted)"))

    if not picks.empty:
        picks_cols = ["ticker", "combined_score", "base_score", "sec_signal_score",
                      "first_filer_bonus", "target_usd"]
        picks_show = picks[[c for c in picks_cols if c in picks.columns]].copy()
        for c in ("combined_score", "base_score", "sec_signal_score", "first_filer_bonus"):
            if c in picks_show: picks_show[c] = picks_show[c].round(2)
        figs.append(_table_fig(picks_show, "Sized portfolio picks (last run)"))

    if not orders.empty:
        ord_cols = ["ticker", "action", "delta_usd", "order_status"]
        ord_show = orders[[c for c in ord_cols if c in orders.columns]].copy()
        figs.append(_table_fig(ord_show, "Last Alpaca paper orders"))

    # ── Compose
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    head = f"""
    <html><head><meta charset="utf-8"><title>Patent Cliff Dashboard</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif;
              max-width: 1400px; margin: 0 auto; padding: 20px;
              background: #fafafa; color: #222; }}
      h1 {{ margin-bottom: 4px; }}
      .ts {{ color: #888; font-size: 13px; margin-bottom: 24px; }}
      .stats {{ display: flex; gap: 24px; margin: 16px 0 32px;
                padding: 16px; background: white; border-radius: 8px;
                border: 1px solid #e0e0e0; }}
      .stat {{ flex: 1; text-align: center; }}
      .stat-val {{ font-size: 28px; font-weight: 600; color: #4a90e2; }}
      .stat-lbl {{ color: #888; font-size: 12px; text-transform: uppercase; }}
      .fig {{ background: white; border-radius: 8px;
              border: 1px solid #e0e0e0; margin-bottom: 20px;
              padding: 8px; }}
    </style>
    </head><body>
    <h1>Patent Cliff Dashboard</h1>
    <div class="ts">Generated {ts} — orange book + purple book + paragraph IV</div>
    <div class="stats">
      <div class="stat"><div class="stat-val">{n_cliffs}</div><div class="stat-lbl">Cliffs in window</div></div>
      <div class="stat"><div class="stat-val">{n_orange}</div><div class="stat-lbl">Orange Book</div></div>
      <div class="stat"><div class="stat-val">{n_purple}</div><div class="stat-lbl">Purple Book</div></div>
      <div class="stat"><div class="stat-val">{n_scored}</div><div class="stat-lbl">Score &gt; 0</div></div>
      <div class="stat"><div class="stat-val">{len(ff)}</div><div class="stat-lbl">First-filer ops</div></div>
      <div class="stat"><div class="stat-val">{len(sec)}</div><div class="stat-lbl">SEC signals</div></div>
    </div>
    """
    body_parts = [head]
    for f in figs:
        body_parts.append('<div class="fig">')
        body_parts.append(f.to_html(include_plotlyjs="cdn", full_html=False))
        body_parts.append("</div>")
    body_parts.append("</body></html>")
    DASH.write_text("".join(body_parts))
    print(f"Wrote dashboard to {DASH}")
    return DASH


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--open", action="store_true", help="Open dashboard in browser")
    args = ap.parse_args()
    path = render()
    if args.open:
        webbrowser.open(f"file://{path}")


if __name__ == "__main__":
    main()
