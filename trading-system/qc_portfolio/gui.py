"""
QC Portfolio Manager — clean, pane-based GUI.

Layout
  Top nav bar  : [Portfolio] [Backtest] [Web]  +  live account metrics
  Portfolio    : [Strategies | Equity Curve | Positions]  +  [Transactions ▼]
  Backtest     : controls + stacked equity/drawdown charts
  Web          : embedded browser (Alpaca paper / QuantConnect)
"""

import os
import threading
import tkinter as tk
from tkinter import ttk
from zoneinfo import ZoneInfo
import datetime as dt
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from config import (
    STRATEGY_COLORS, BG_DARK, BG_CARD, FG_TEXT, FG_DIM, GREEN, RED,
    REFRESH_INTERVAL_MS, TRAIL_STOP_PCT,
)
import portfolio_engine as pe
import broker as md
from backtest import run_backtest

matplotlib.rcParams.update({
    "figure.facecolor": BG_CARD,
    "axes.facecolor":   BG_CARD,
    "axes.edgecolor":   "#1e1e3a",
    "axes.labelcolor":  FG_TEXT,
    "text.color":       FG_TEXT,
    "xtick.color":      FG_DIM,
    "ytick.color":      FG_DIM,
    "grid.color":       "#1e1e3a",
    "grid.linewidth":   0.5,
})

ET       = ZoneInfo("America/New_York")
ACCENT   = "#00d4ff"
AMBER    = "#f9ca24"
NAV_BG   = "#0a0a14"
DIVIDER  = "#1e1e3a"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(v, decimals=2):
    try:
        f = float(v)
        sign = "+" if f > 0 else ""
        return f"${f:,.{decimals}f}"
    except Exception:
        return str(v)

def _pct(v, decimals=1):
    try:
        return f"{float(v):+.{decimals}f}%"
    except Exception:
        return "—"

def _clr(v):
    try:
        return GREEN if float(v) >= 0 else RED
    except Exception:
        return FG_TEXT


# ─────────────────────────────────────────────────────────────────────────────
# CollapsiblePane  — reusable section with toggle header
# ─────────────────────────────────────────────────────────────────────────────

class CollapsiblePane(tk.Frame):
    """Frame with a clickable header that shows/hides its content."""

    def __init__(self, parent, title, *, accent=ACCENT, start_open=True, **kw):
        kw.setdefault("bg", BG_DARK)
        super().__init__(parent, **kw)

        self._open = start_open

        hdr = tk.Frame(self, bg="#12122a", pady=0)
        hdr.pack(fill="x")

        self._arrow = tk.Label(hdr, text="▼" if start_open else "▶",
                               bg="#12122a", fg=accent,
                               font=("Helvetica", 9), cursor="hand2")
        self._arrow.pack(side="left", padx=(8, 4), pady=5)
        self._arrow.bind("<Button-1>", lambda e: self.toggle())

        tk.Label(hdr, text=title, bg="#12122a", fg=accent,
                 font=("Helvetica", 10, "bold")).pack(side="left", pady=5)
        hdr.bind("<Button-1>", lambda e: self.toggle())

        self.content = tk.Frame(self, bg=self["bg"])
        if start_open:
            self.content.pack(fill="both", expand=True)

    def toggle(self):
        if self._open:
            self.content.pack_forget()
            self._arrow.config(text="▶")
        else:
            self.content.pack(fill="both", expand=True)
            self._arrow.config(text="▼")
        self._open = not self._open

    def is_open(self):
        return self._open


# ─────────────────────────────────────────────────────────────────────────────
# Main dashboard
# ─────────────────────────────────────────────────────────────────────────────

class QCDashboard:

    def __init__(self, root):
        self.root = root
        self.root.title("QC Portfolio Manager")
        self.root.geometry("1440x900")
        self.root.configure(bg=NAV_BG)
        self.root.minsize(1100, 750)

        # Live data
        self._account   = {}
        self._positions = []
        self._prices    = {}
        self._port_hist = {}
        self._bt_running = False

        # Equity curve hover data
        self._eq_dates = []
        self._eq_vals  = []

        # Positions inner frame (rebuilt on each refresh)
        self._pos_inner = None

        # Strategy detail: which slot is expanded
        self._expanded_slot = None
        self._strat_detail_frame = None

        self._build_layout()
        self._schedule_refresh()

    # ─── Layout skeleton ────────────────────────────────────────────────────

    def _build_layout(self):
        # ── Top nav bar ──────────────────────────────────────────────────────
        nav = tk.Frame(self.root, bg=NAV_BG, height=52)
        nav.pack(fill="x", side="top")
        nav.pack_propagate(False)

        # App title + broker badge
        from config import ACTIVE_BROKER
        broker_color = "#00ff88" if ACTIVE_BROKER == "alpaca" else "#ff9f43"
        broker_label = "ALPACA" if ACTIVE_BROKER == "alpaca" else "IBKR LIVE"
        tk.Label(nav, text="QC Portfolio", bg=NAV_BG, fg=ACCENT,
                 font=("Helvetica", 14, "bold")).pack(side="left", padx=(20, 8))
        tk.Label(nav, text=broker_label, bg=broker_color, fg="#000",
                 font=("Helvetica", 8, "bold"), padx=6).pack(side="left", padx=(0, 24))

        # Nav buttons
        self._nav_buttons = {}
        self._current_view = tk.StringVar(value="portfolio")
        for view, label in [("portfolio", "Portfolio"), ("backtest", "Backtest"), ("web", "Web")]:
            btn = tk.Button(nav, text=label,
                            bg=NAV_BG, fg=FG_DIM,
                            activebackground=NAV_BG, activeforeground=ACCENT,
                            relief="flat", cursor="hand2",
                            font=("Helvetica", 10, "bold"),
                            command=lambda v=view: self._show_view(v))
            btn.pack(side="left", padx=4, pady=8, ipady=4, ipadx=10)
            self._nav_buttons[view] = btn

        # Live account metrics (right side of nav)
        self._nav_labels = {}
        right = tk.Frame(nav, bg=NAV_BG)
        right.pack(side="right", padx=20)
        for key, default, color in [
            ("equity",   "$100,000", FG_TEXT),
            ("day_pnl",  "+$0.00",   GREEN),
            ("session",  "—",        FG_DIM),
        ]:
            col = tk.Frame(right, bg=NAV_BG, padx=12)
            col.pack(side="left")
            lbl = tk.Label(col, text=default, bg=NAV_BG, fg=color,
                           font=("Helvetica", 11, "bold"))
            lbl.pack()
            self._nav_labels[key] = lbl

        # Refresh button
        tk.Button(right, text="⟳ Refresh",
                  bg=NAV_BG, fg=FG_DIM, relief="flat", cursor="hand2",
                  font=("Helvetica", 9),
                  command=lambda: threading.Thread(
                      target=self._fetch_all, daemon=True).start()
                  ).pack(side="left", padx=8)

        # Thin accent line under nav
        tk.Frame(self.root, bg=DIVIDER, height=1).pack(fill="x")

        # ── Content area: holds one view at a time ───────────────────────────
        self._content = tk.Frame(self.root, bg=BG_DARK)
        self._content.pack(fill="both", expand=True)

        # Build all three view frames
        self._views = {}
        for name in ("portfolio", "backtest", "web"):
            f = tk.Frame(self._content, bg=BG_DARK)
            self._views[name] = f

        self._build_portfolio_view(self._views["portfolio"])
        self._build_backtest_view(self._views["backtest"])
        self._build_web_view(self._views["web"])

        self._show_view("portfolio")

    def _show_view(self, name):
        for v in self._views.values():
            v.pack_forget()
        self._views[name].pack(fill="both", expand=True)
        self._current_view.set(name)

        # Highlight active nav button
        for k, btn in self._nav_buttons.items():
            btn.config(fg=ACCENT if k == name else FG_DIM)

    # ─── Market session ──────────────────────────────────────────────────────

    def _market_session(self):
        now  = dt.datetime.now(ET)
        mins = now.hour * 60 + now.minute
        if (9 * 60 + 30) <= mins < (16 * 60):
            return "● MARKET OPEN", GREEN
        elif (4 * 60) <= mins < (9 * 60 + 30):
            return "● PRE-MARKET", AMBER
        elif (16 * 60) <= mins < (20 * 60):
            return "● AFTER HOURS", AMBER
        else:
            return "● OVERNIGHT", FG_DIM

    # ─────────────────────────────────────────────────────────────────────────
    # PORTFOLIO VIEW
    # ─────────────────────────────────────────────────────────────────────────

    def _build_portfolio_view(self, parent):
        """3-pane layout: strategies | equity curve | positions + collapsible log."""

        # ── Main 3-column paned area ─────────────────────────────────────────
        pw = ttk.PanedWindow(parent, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=0, pady=0)

        # ttk style for sash
        style = ttk.Style()
        style.configure("TPanedwindow", background=DIVIDER)

        # LEFT — strategy leaderboard
        left_pane = CollapsiblePane(pw, "TOP 5 STRATEGIES", accent=ACCENT, start_open=True)
        pw.add(left_pane, weight=0)
        self._build_strategy_pane(left_pane.content)

        # CENTER — equity curve + account summary
        center_pane = tk.Frame(pw, bg=BG_DARK)
        pw.add(center_pane, weight=2)
        self._build_equity_pane(center_pane)

        # RIGHT — positions
        right_pane = CollapsiblePane(pw, "OPEN POSITIONS", accent=ACCENT, start_open=True)
        pw.add(right_pane, weight=0)
        self._build_positions_pane(right_pane.content)

        # ── Bottom: collapsible transaction log ──────────────────────────────
        tk.Frame(parent, bg=DIVIDER, height=1).pack(fill="x")
        self._tx_pane = CollapsiblePane(parent, "TRANSACTION LOG",
                                         accent=FG_DIM, start_open=False,
                                         bg=BG_DARK)
        self._tx_pane.pack(fill="x", side="bottom")
        self._build_tx_pane(self._tx_pane.content)

    # ── Strategy pane ────────────────────────────────────────────────────────

    def _build_strategy_pane(self, parent):
        parent.configure(bg=BG_DARK, width=280)

        # QC refresh label
        self._strat_refresh_lbl = tk.Label(
            parent, text="QC: —", bg=BG_DARK, fg=FG_DIM, font=("Helvetica", 8))
        self._strat_refresh_lbl.pack(anchor="e", padx=8, pady=(4, 0))

        # Scrollable card list
        canvas = tk.Canvas(parent, bg=BG_DARK, highlightthickness=0, width=280)
        vsb    = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._strat_list_inner = tk.Frame(canvas, bg=BG_DARK)
        win = canvas.create_window((0, 0), window=self._strat_list_inner, anchor="nw")
        self._strat_list_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))

        # Strategy detail expand area (below list, rebuilt on click)
        self._strat_detail_frame = tk.Frame(parent, bg=BG_DARK)
        self._strat_detail_frame.pack(fill="x", padx=4, pady=4)

    def _update_strategy_pane(self, strategies, weights, equity):
        inner = self._strat_list_inner
        for w in inner.winfo_children():
            w.destroy()

        ts = pe.get_state().get("last_qc_refresh") or "never"
        self._strat_refresh_lbl.config(text=f"QC: {str(ts)[:16]}")

        if not strategies:
            tk.Label(inner, text="Loading strategies…",
                     bg=BG_DARK, fg=FG_DIM, font=("Helvetica", 10)).pack(pady=20)
            return

        for i, (cfg, w) in enumerate(zip(strategies, weights)):
            self._make_strategy_card(inner, cfg, w, equity, i)

    def _make_strategy_card(self, parent, cfg, weight, equity, slot):
        name   = cfg.get("name", f"Strategy {slot+1}")
        stats  = cfg.get("statistics", {})
        r3m    = stats.get("oos 3m return", 0)
        sharpe = stats.get("oos 3m sharpe", 0)
        val    = equity * weight
        color  = STRATEGY_COLORS[slot % len(STRATEGY_COLORS)]

        card = tk.Frame(parent, bg=BG_CARD, highlightbackground=DIVIDER,
                        highlightthickness=1, cursor="hand2")
        card.pack(fill="x", padx=6, pady=3)

        # Rank badge + name
        top = tk.Frame(card, bg=BG_CARD)
        top.pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(top, text=f"#{slot+1}", bg=color, fg="#000",
                 font=("Helvetica", 9, "bold"), padx=6).pack(side="left")
        tk.Label(top, text=f"  {name[:26]}",
                 bg=BG_CARD, fg=FG_TEXT,
                 font=("Helvetica", 9, "bold"),
                 wraplength=200, justify="left").pack(side="left")

        # Metrics row
        bot = tk.Frame(card, bg=BG_CARD)
        bot.pack(fill="x", padx=8, pady=(0, 6))
        for txt, clr in [
            (f"OOS {r3m:+.0f}%", _clr(r3m)),
            (f"Sh {sharpe:.1f}", FG_TEXT),
            (f"Wt {weight*100:.0f}%", color),
            (f"${val:,.0f}", FG_TEXT),
        ]:
            tk.Label(bot, text=txt, bg=BG_CARD, fg=clr,
                     font=("Helvetica", 8)).pack(side="left", padx=(0, 8))

        # Weight bar
        bar_bg = tk.Frame(card, bg="#111128", height=3)
        bar_bg.pack(fill="x", padx=0)
        bar_fg = tk.Frame(bar_bg, bg=color, height=3)
        bar_fg.place(relx=0, rely=0, relwidth=min(1.0, weight), relheight=1)

        # Click to expand detail below
        def _click(e, s=slot, c=cfg, ww=weight, eq=equity, col=color):
            self._show_strategy_detail(c, ww, eq, col)
        for widget in (card, top, bot, bar_bg):
            widget.bind("<Button-1>", _click)

    def _show_strategy_detail(self, cfg, weight, equity, color):
        frame = self._strat_detail_frame
        for w in frame.winfo_children():
            w.destroy()

        name   = cfg.get("name", "Strategy")
        stats  = cfg.get("statistics", {})
        r3m    = stats.get("oos 3m return", 0)
        sh     = stats.get("oos 3m sharpe", 0)
        dd     = stats.get("5y drawdown", 0)
        allocs = pe.get_allocations().get(cfg["id"], {})
        val    = equity * weight

        tk.Frame(frame, bg=color, height=2).pack(fill="x")
        hdr = tk.Frame(frame, bg="#12122a", pady=4)
        hdr.pack(fill="x")
        tk.Label(hdr, text=name[:32], bg="#12122a", fg=color,
                 font=("Helvetica", 9, "bold")).pack(side="left", padx=8)
        tk.Button(hdr, text="✕", bg="#12122a", fg=FG_DIM, relief="flat",
                  font=("Helvetica", 8), cursor="hand2",
                  command=lambda: [w.destroy() for w in frame.winfo_children()]
                  ).pack(side="right", padx=6)

        row = tk.Frame(frame, bg=BG_CARD, pady=4)
        row.pack(fill="x")
        for label, val_str, clr in [
            ("OOS 3M",  f"{r3m:+.1f}%",  _clr(r3m)),
            ("Sharpe",  f"{sh:.2f}",      FG_TEXT),
            ("5Y DD",   f"{dd:.1f}%",     RED),
            ("Weight",  f"{weight*100:.1f}%", color),
            ("Value",   f"${val:,.0f}",   FG_TEXT),
        ]:
            col = tk.Frame(row, bg=BG_CARD, padx=8)
            col.pack(side="left")
            tk.Label(col, text=label, bg=BG_CARD, fg=FG_DIM,
                     font=("Helvetica", 7)).pack()
            tk.Label(col, text=val_str, bg=BG_CARD, fg=clr,
                     font=("Helvetica", 10, "bold")).pack()

        # Universe tags
        universe = cfg.get("universe", [])
        if universe:
            tags = tk.Frame(frame, bg=BG_DARK, pady=4)
            tags.pack(fill="x")
            for sym in universe[:10]:
                is_top = allocs and sym == max(allocs, key=allocs.get, default=None)
                bg_c   = color if is_top else "#16213e"
                fg_c   = "#000" if is_top else FG_DIM
                tk.Label(tags, text=sym, bg=bg_c, fg=fg_c,
                         font=("Helvetica", 8, "bold" if is_top else "normal"),
                         padx=5, pady=2).pack(side="left", padx=2)

    # ── Equity pane ──────────────────────────────────────────────────────────

    def _build_equity_pane(self, parent):
        # Summary row
        self._eq_summary = tk.Frame(parent, bg=BG_DARK, pady=10)
        self._eq_summary.pack(fill="x", padx=16)

        self._eq_labels = {}
        for key, label, default, clr in [
            ("equity",   "Total Equity", "$100,000", FG_TEXT),
            ("total_pnl","All-Time P&L", "$0.00",    FG_TEXT),
            ("cash",     "Cash",         "$100,000", FG_DIM),
            ("positions","Positions",    "0",         FG_DIM),
        ]:
            col = tk.Frame(self._eq_summary, bg=BG_DARK, padx=20)
            col.pack(side="left")
            tk.Label(col, text=label, bg=BG_DARK, fg=FG_DIM,
                     font=("Helvetica", 8)).pack(anchor="w")
            lbl = tk.Label(col, text=default, bg=BG_DARK, fg=clr,
                           font=("Helvetica", 17, "bold"))
            lbl.pack(anchor="w")
            self._eq_labels[key] = lbl

        tk.Frame(parent, bg=DIVIDER, height=1).pack(fill="x")

        # Chart
        self._eq_fig = Figure(dpi=96)
        self._eq_fig.patch.set_facecolor(BG_DARK)
        self._eq_ax  = self._eq_fig.add_subplot(111)
        self._eq_ax.set_facecolor(BG_DARK)

        self._eq_canvas = FigureCanvasTkAgg(self._eq_fig, parent)
        self._eq_canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)

        # Hover crosshair setup
        self._eq_annot = self._eq_ax.annotate(
            "", xy=(0, 0), xytext=(12, 12), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.4", fc="#16213e", ec=ACCENT, lw=0.8),
            fontsize=9, color=FG_TEXT, fontfamily="monospace")
        self._eq_annot.set_visible(False)
        self._eq_vline = self._eq_ax.axvline(
            x=0, color=FG_DIM, linewidth=0.6, linestyle=":", visible=False)
        self._eq_hline = self._eq_ax.axhline(
            y=0, color=FG_DIM, linewidth=0.6, linestyle=":", visible=False)

        def _on_hover(event):
            if event.inaxes != self._eq_ax or not self._eq_dates:
                self._eq_annot.set_visible(False)
                self._eq_vline.set_visible(False)
                self._eq_hline.set_visible(False)
                self._eq_canvas.draw_idle()
                return
            xs  = np.array(mdates.date2num(self._eq_dates))
            idx = int(np.argmin(np.abs(xs - event.xdata)))
            eq  = self._eq_vals[idx]
            d   = self._eq_dates[idx]
            delta = eq - 100_000.0
            sign  = "+" if delta >= 0 else ""
            self._eq_annot.set_text(
                f"{d.strftime('%b %d')}\n${eq:,.0f}  ({sign}${delta:,.0f})")
            xval = mdates.date2num(d)
            self._eq_annot.xy = (xval, eq)
            self._eq_annot.set_visible(True)
            self._eq_vline.set_xdata([xval])
            self._eq_vline.set_visible(True)
            self._eq_hline.set_ydata([eq])
            self._eq_hline.set_visible(True)
            self._eq_canvas.draw_idle()

        self._eq_canvas.mpl_connect("motion_notify_event", _on_hover)

    def _update_equity_curve(self, port_hist, equity):
        ax = self._eq_ax
        ax.clear()
        ax.set_facecolor(BG_DARK)
        for sp in ax.spines.values():
            sp.set_color(DIVIDER)

        equity_vals = port_hist.get("equity", [])
        timestamps  = port_hist.get("timestamp", [])

        if equity_vals and timestamps:
            dates = [dt.datetime.fromtimestamp(t) for t in timestamps]
            vals  = list(equity_vals)

            self._eq_dates = dates
            self._eq_vals  = vals
            start = 100_000.0

            ax.plot(dates, vals, color=ACCENT, linewidth=1.8, zorder=3)
            ax.fill_between(dates, vals, start,
                            where=[v >= start for v in vals],
                            color=GREEN, alpha=0.15, zorder=2)
            ax.fill_between(dates, vals, start,
                            where=[v < start for v in vals],
                            color=RED, alpha=0.15, zorder=2)
            ax.axhline(start, color=FG_DIM, linewidth=0.6, linestyle="--", alpha=0.5)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(
                lambda x, _: f"${x:,.0f}"))
            self._eq_fig.autofmt_xdate(rotation=20, ha="right")

            delta = vals[-1] - start
            sign  = "+" if delta >= 0 else ""
            ax.set_title(f"${vals[-1]:,.0f}  ({sign}${delta:,.0f})",
                         color=_clr(delta), fontsize=11, pad=6)

            # Re-attach crosshair after clear
            self._eq_annot = ax.annotate(
                "", xy=(mdates.date2num(dates[0]), vals[0]),
                xytext=(12, 12), textcoords="offset points",
                bbox=dict(boxstyle="round,pad=0.4", fc="#16213e", ec=ACCENT, lw=0.8),
                fontsize=9, color=FG_TEXT, fontfamily="monospace")
            self._eq_annot.set_visible(False)
            self._eq_vline = ax.axvline(
                x=mdates.date2num(dates[0]), color=FG_DIM,
                linewidth=0.6, linestyle=":", visible=False)
            self._eq_hline = ax.axhline(
                y=vals[0], color=FG_DIM,
                linewidth=0.6, linestyle=":", visible=False)
        else:
            self._eq_dates = []
            self._eq_vals  = []
            ax.text(0.5, 0.5, "Equity history builds after first session",
                    ha="center", va="center", color=FG_DIM,
                    transform=ax.transAxes, fontsize=10)
            ax.set_xticks([])
            ax.set_yticks([])

        ax.tick_params(colors=FG_DIM, labelsize=8)
        ax.grid(True, alpha=0.2)
        self._eq_fig.tight_layout(pad=0.8)
        self._eq_canvas.draw_idle()

    # ── Positions pane ───────────────────────────────────────────────────────

    def _build_positions_pane(self, parent):
        parent.configure(width=340)

        # Position summary bar
        self._pos_bar = tk.Frame(parent, bg=BG_DARK, pady=6)
        self._pos_bar.pack(fill="x", padx=8)
        self._pos_lbl = {}
        for key, label, default in [
            ("count",    "Positions", "0"),
            ("open_pnl", "Open P&L",  "$0.00"),
        ]:
            col = tk.Frame(self._pos_bar, bg=BG_DARK, padx=12)
            col.pack(side="left")
            tk.Label(col, text=label, bg=BG_DARK, fg=FG_DIM,
                     font=("Helvetica", 7)).pack(anchor="w")
            lbl = tk.Label(col, text=default, bg=BG_DARK, fg=FG_TEXT,
                           font=("Helvetica", 12, "bold"))
            lbl.pack(anchor="w")
            self._pos_lbl[key] = lbl

        tk.Frame(parent, bg=DIVIDER, height=1).pack(fill="x")

        # Scrollable cards
        outer  = tk.Frame(parent, bg=BG_DARK)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG_DARK, highlightthickness=0)
        vsb    = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._pos_inner = tk.Frame(canvas, bg=BG_DARK)
        win = canvas.create_window((0, 0), window=self._pos_inner, anchor="nw")
        self._pos_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))

        # Mousewheel
        canvas.bind_all("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-1 * e.delta / 120), "units"))

    def _update_positions_pane(self, positions):
        if self._pos_inner is None:
            return

        # Update summary bar
        total_pl = sum(float(p.get("unrealized_pl", 0)) for p in positions)
        self._pos_lbl["count"].config(text=str(len(positions)))
        self._pos_lbl["open_pnl"].config(
            text=f"${total_pl:+,.2f}", fg=_clr(total_pl))

        # Rebuild cards
        for w in self._pos_inner.winfo_children():
            w.destroy()

        if not positions:
            tk.Label(self._pos_inner, text="No open positions",
                     bg=BG_DARK, fg=FG_DIM, font=("Helvetica", 11)).pack(pady=40)
            return

        sorted_pos = sorted(positions,
                            key=lambda p: float(p.get("unrealized_pl", 0)),
                            reverse=True)
        for pos in sorted_pos:
            self._make_pos_card(self._pos_inner, pos)

    def _make_pos_card(self, parent, pos):
        sym      = pos.get("symbol", "?")
        side     = pos.get("side", "long")
        entry    = float(pos.get("avg_entry_price", 0))
        cur      = float(pos.get("current_price", entry))
        qty      = float(pos.get("qty", 0))
        mktval   = float(pos.get("market_value", 0))
        unpl     = float(pos.get("unrealized_pl", 0))
        unpc     = float(pos.get("unrealized_plpc", 0)) * 100

        dir_clr  = GREEN if side == "long" else RED
        pnl_clr  = GREEN if unpl >= 0 else RED
        dir_txt  = "▲ LONG" if side == "long" else "▼ SHORT"

        card = tk.Frame(parent, bg=BG_CARD,
                        highlightbackground=dir_clr, highlightthickness=1)
        card.pack(fill="x", padx=6, pady=4)

        # Top row: symbol + direction + P&L
        top = tk.Frame(card, bg="#16213e")
        top.pack(fill="x")
        tk.Label(top, text=f" {sym}", bg="#16213e", fg=FG_TEXT,
                 font=("Helvetica", 13, "bold")).pack(side="left", pady=5)
        tk.Label(top, text=f" {dir_txt} ", bg=dir_clr, fg="white",
                 font=("Helvetica", 8, "bold")).pack(side="left", padx=6, pady=5)
        tk.Label(top, text=f"{unpl:+,.2f} ({unpc:+.2f}%) ",
                 bg="#16213e", fg=pnl_clr,
                 font=("Helvetica", 10, "bold")).pack(side="right", pady=5)

        body = tk.Frame(card, bg=BG_CARD, padx=10, pady=6)
        body.pack(fill="x")

        # Price row
        pr = tk.Frame(body, bg=BG_CARD)
        pr.pack(fill="x")
        tk.Label(pr, text=f"${cur:.4f}", bg=BG_CARD, fg=FG_TEXT,
                 font=("Helvetica", 11, "bold")).pack(side="left")
        tk.Label(pr, text=f"  entry ${entry:.4f}", bg=BG_CARD, fg=FG_DIM,
                 font=("Helvetica", 8)).pack(side="left")
        tk.Label(pr, text=f"{qty:.4f} shares", bg=BG_CARD, fg=FG_DIM,
                 font=("Helvetica", 8)).pack(side="right")

        if TRAIL_STOP_PCT is not None:
            # Stop → current → target progress bar (only when trail stop is active)
            trail_highs = pe.get_state().get("trail_highs", {})
            peak   = trail_highs.get(sym, cur)
            stop   = peak * (1 - TRAIL_STOP_PCT)
            risk   = abs(entry - stop) if entry > stop else entry * TRAIL_STOP_PCT
            target = entry + risk * 2.0 if side == "long" else entry - risk * 2.0
            rng    = target - stop
            fill   = max(0.0, min(1.0, (cur - stop) / rng)) if rng else 0.5

            sl = tk.Frame(body, bg=BG_CARD)
            sl.pack(fill="x", pady=(6, 2))
            tk.Label(sl, text=f"Stop ${stop:.2f}", bg=BG_CARD, fg=RED,
                     font=("Helvetica", 7)).pack(side="left")
            tk.Label(sl, text=f"Target ${target:.2f}", bg=BG_CARD, fg=GREEN,
                     font=("Helvetica", 7)).pack(side="right")

            bar_bg = tk.Frame(body, bg="#111128", height=5)
            bar_bg.pack(fill="x", pady=(0, 4))
            tk.Frame(bar_bg, bg=dir_clr, height=5).place(
                relx=0, rely=0, relwidth=fill, relheight=1)

            tk.Label(body,
                     text=f"MktVal ${mktval:,.2f}  ·  Trail Stop ${stop:.4f}  ·  Peak ${peak:.4f}",
                     bg=BG_CARD, fg=FG_DIM, font=("Helvetica", 7)).pack(anchor="w")
        else:
            # DD-ladder mode: show P&L progress bar (entry → current)
            fill = max(0.0, min(1.0, 0.5 + unpc / 40.0))  # centres at 0%, ±20% fills bar

            sl = tk.Frame(body, bg=BG_CARD)
            sl.pack(fill="x", pady=(6, 2))
            tk.Label(sl, text="DD ladder active", bg=BG_CARD, fg=FG_DIM,
                     font=("Helvetica", 7)).pack(side="left")
            tk.Label(sl, text=f"P&L {unpc:+.2f}%", bg=BG_CARD, fg=pnl_clr,
                     font=("Helvetica", 7)).pack(side="right")

            bar_bg = tk.Frame(body, bg="#111128", height=5)
            bar_bg.pack(fill="x", pady=(0, 4))
            tk.Frame(bar_bg, bg=pnl_clr, height=5).place(
                relx=0, rely=0, relwidth=fill, relheight=1)

            tk.Label(body, text=f"MktVal ${mktval:,.2f}  ·  entry ${entry:.4f}",
                     bg=BG_CARD, fg=FG_DIM, font=("Helvetica", 7)).pack(anchor="w")

    # ── Transactions pane ────────────────────────────────────────────────────

    def _build_tx_pane(self, parent):
        parent.configure(height=160)
        cols = ("time", "symbol", "side", "qty", "price", "value", "reason")
        self._tx_tree = ttk.Treeview(parent, columns=cols, show="headings", height=5)
        widths = [90, 60, 45, 70, 75, 90, 120]
        for col, w in zip(cols, widths):
            self._tx_tree.heading(col, text=col.title())
            self._tx_tree.column(col, width=w, anchor="center", minwidth=w)

        vsb = ttk.Scrollbar(parent, orient="vertical", command=self._tx_tree.yview)
        self._tx_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._tx_tree.pack(fill="both", expand=True, padx=4, pady=(2, 4))
        self._tx_tree.tag_configure("buy",  foreground=GREEN)
        self._tx_tree.tag_configure("sell", foreground=RED)

    def _update_transactions(self):
        tree = self._tx_tree
        for row in tree.get_children():
            tree.delete(row)
        for tx in reversed(pe.load_transactions(limit=200)):
            side = tx.get("side", "")
            tree.insert("", "end", tags=(side,), values=(
                tx.get("timestamp", "")[-8:],
                tx.get("symbol", ""),
                side.upper(),
                tx.get("qty", ""),
                tx.get("price", ""),
                f"${float(tx.get('value', 0)):,.2f}",
                tx.get("reason", ""),
            ))

    # ─────────────────────────────────────────────────────────────────────────
    # BACKTEST VIEW
    # ─────────────────────────────────────────────────────────────────────────

    def _build_backtest_view(self, parent):
        # Controls
        ctrl = tk.Frame(parent, bg=BG_CARD, pady=10)
        ctrl.pack(fill="x", padx=8, pady=(10, 4))

        for label, attr, values, default, width in [
            ("Lookback",    "_bt_period", ["1 Year","2 Years","3 Years"], "2 Years", 10),
            ("Trail Stop",  "_bt_stop",   ["3%","5%","7%","10%","15%"],   "5%",       8),
            ("Port DD Lim", "_bt_dd",     ["10%","15%","20%","25%"],      "15%",      8),
        ]:
            tk.Label(ctrl, text=label + ":", bg=BG_CARD, fg=FG_DIM,
                     font=("Helvetica", 9)).pack(side="left", padx=(16, 4))
            cb = ttk.Combobox(ctrl, values=values, state="readonly", width=width)
            cb.set(default)
            cb.pack(side="left", padx=4)
            setattr(self, attr, cb)

        self._bt_btn = tk.Button(ctrl, text="▶  Run Backtest",
                                 bg="#1a3a6a", fg=ACCENT,
                                 font=("Helvetica", 10, "bold"),
                                 relief="flat", cursor="hand2",
                                 command=self._run_backtest_thread)
        self._bt_btn.pack(side="left", padx=20)
        self._bt_status = tk.Label(ctrl, text="", bg=BG_CARD, fg=FG_DIM,
                                   font=("Helvetica", 9))
        self._bt_status.pack(side="left")

        # Metrics bar
        mbar = tk.Frame(parent, bg=BG_CARD, pady=8)
        mbar.pack(fill="x", padx=8, pady=(0, 4))
        self._bt_metric_labels = {}
        for key in ["CAGR","Sharpe","Max DD","Win Rate","Trades","Final Value"]:
            col = tk.Frame(mbar, bg=BG_CARD, padx=22)
            col.pack(side="left")
            tk.Label(col, text=key, bg=BG_CARD, fg=FG_DIM,
                     font=("Helvetica", 8)).pack()
            lbl = tk.Label(col, text="—", bg=BG_CARD, fg=FG_TEXT,
                           font=("Helvetica", 15, "bold"))
            lbl.pack()
            self._bt_metric_labels[key] = lbl

        # Stacked charts
        charts = tk.Frame(parent, bg=BG_DARK)
        charts.pack(fill="both", expand=True, padx=8, pady=4)
        self._bt_fig = Figure(figsize=(12, 5), dpi=96)
        self._bt_fig.patch.set_facecolor(BG_DARK)
        self._bt_ax1 = self._bt_fig.add_subplot(211)
        self._bt_ax2 = self._bt_fig.add_subplot(212)
        self._bt_canvas = FigureCanvasTkAgg(self._bt_fig, charts)
        self._bt_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _run_backtest_thread(self):
        if self._bt_running:
            return
        self._bt_running = True
        self._bt_btn.config(state="disabled")
        self._bt_status.config(text="Running…")

        period_map = {"1 Year": 1, "2 Years": 2, "3 Years": 3}
        years  = period_map.get(self._bt_period.get(), 2)
        stop   = float(self._bt_stop.get().rstrip("%")) / 100
        dd_lim = float(self._bt_dd.get().rstrip("%")) / 100
        ids    = [c["id"] for c in pe.get_strategies()]

        def _run():
            result = run_backtest(
                strategy_ids=ids if ids else None,
                lookback_years=years,
                trail_stop_pct=stop,
                portfolio_dd_limit=dd_lim,
            )
            self.root.after(0, lambda: self._on_backtest_done(result))

        threading.Thread(target=_run, daemon=True).start()

    def _on_backtest_done(self, result):
        self._bt_running = False
        self._bt_btn.config(state="normal")
        if result is None:
            self._bt_status.config(text="Failed — no data")
            return
        self._bt_status.config(text="Complete")
        m = result.get("metrics", {})
        for key, (text, clr) in {
            "CAGR":        (f"{m.get('cagr', 0):+.1f}%",   _clr(m.get('cagr', 0))),
            "Sharpe":      (f"{m.get('sharpe', 0):.2f}",    FG_TEXT),
            "Max DD":      (f"{m.get('max_dd', 0):.1f}%",   RED),
            "Win Rate":    (f"{m.get('win_rate', 0):.1f}%", FG_TEXT),
            "Trades":      (str(m.get('total_trades', 0)),   FG_TEXT),
            "Final Value": (f"${m.get('final_value', 0):,.0f}", GREEN),
        }.items():
            self._bt_metric_labels[key].config(text=text, fg=clr)

        ec  = result["equity_curve"]
        ddc = result["drawdown_curve"]
        bm  = result.get("benchmark", pd.Series())
        ax1, ax2 = self._bt_ax1, self._bt_ax2
        ax1.clear(); ax2.clear()
        ax1.set_facecolor(BG_DARK); ax2.set_facecolor(BG_DARK)
        for ax in (ax1, ax2):
            for sp in ax.spines.values():
                sp.set_color(DIVIDER)

        if not ec.empty:
            ax1.plot(ec.index, ec.values, color=ACCENT, linewidth=1.6, label="Portfolio")
            if not bm.empty:
                ax1.plot(bm.index, bm.values, color=FG_DIM, linewidth=1,
                         linestyle="--", label="SPY B&H")
            ax1.yaxis.set_major_formatter(mticker.FuncFormatter(
                lambda x, _: f"${x/1000:.0f}k"))
            ax1.legend(fontsize=8, loc="upper left",
                       facecolor=BG_CARD, edgecolor=DIVIDER, labelcolor=FG_TEXT)
            ax1.grid(True, alpha=0.2)
            ax1.set_title("Equity Curve", color=FG_DIM, fontsize=9)

        if not ddc.empty:
            ax2.fill_between(ddc.index, ddc.values, color=RED, alpha=0.45)
            ax2.plot(ddc.index, ddc.values, color=RED, linewidth=0.8)
            ax2.grid(True, alpha=0.2)
            ax2.set_title("Drawdown", color=FG_DIM, fontsize=9)

        for ax in (ax1, ax2):
            ax.tick_params(colors=FG_DIM, labelsize=8)
        self._bt_fig.tight_layout(pad=1.2)
        self._bt_canvas.draw()

    # ─────────────────────────────────────────────────────────────────────────
    # WEB VIEW  (embedded browser)
    # ─────────────────────────────────────────────────────────────────────────

    def _build_web_view(self, parent):
        try:
            from tkinterweb import HtmlFrame

            # Address bar
            bar = tk.Frame(parent, bg=NAV_BG, pady=6)
            bar.pack(fill="x")

            self._url_var = tk.StringVar(value="https://app.alpaca.markets")
            url_entry = tk.Entry(bar, textvariable=self._url_var,
                                 bg="#16213e", fg=FG_TEXT, insertbackground=FG_TEXT,
                                 relief="flat", font=("Helvetica", 10))
            url_entry.pack(side="left", fill="x", expand=True, padx=(12, 4), ipady=4)

            def _go(e=None):
                self._webframe.load_url(self._url_var.get())

            url_entry.bind("<Return>", _go)

            # Quick-nav buttons
            for label, url in [
                ("Alpaca",      "https://app.alpaca.markets"),
                ("QuantConnect","https://www.quantconnect.com"),
            ]:
                tk.Button(bar, text=label,
                          bg="#16213e", fg=ACCENT, relief="flat",
                          font=("Helvetica", 9), cursor="hand2",
                          command=lambda u=url: (
                              self._url_var.set(u),
                              self._webframe.load_url(u)
                          )).pack(side="left", padx=4)

            tk.Button(bar, text="Go ▶",
                      bg=ACCENT, fg="#000", relief="flat",
                      font=("Helvetica", 9, "bold"), cursor="hand2",
                      command=_go).pack(side="left", padx=(4, 12))

            tk.Frame(parent, bg=DIVIDER, height=1).pack(fill="x")

            self._webframe = HtmlFrame(parent, messages_enabled=False)
            self._webframe.pack(fill="both", expand=True)
            self._webframe.load_url("https://app.alpaca.markets")

        except Exception as e:
            # Fallback if tkinterweb not available or fails
            tk.Label(parent,
                     text=f"Browser unavailable: {e}\n\nOpen links manually:",
                     bg=BG_DARK, fg=FG_DIM, font=("Helvetica", 11)).pack(pady=40)
            for label, url in [
                ("Alpaca Paper Trading",  "https://app.alpaca.markets"),
                ("QuantConnect",          "https://www.quantconnect.com"),
            ]:
                tk.Button(parent, text=label,
                          bg=BG_CARD, fg=ACCENT, relief="flat",
                          font=("Helvetica", 11), cursor="hand2",
                          command=lambda u=url: __import__("webbrowser").open(u)
                          ).pack(pady=6)

    # ─────────────────────────────────────────────────────────────────────────
    # DATA REFRESH CYCLE
    # ─────────────────────────────────────────────────────────────────────────

    def _schedule_refresh(self):
        pe.load_state()
        self._do_refresh()

    def _do_refresh(self):
        threading.Thread(target=self._fetch_all, daemon=True).start()

    def _fetch_all(self):
        try:
            if not pe.get_strategies():
                pe.refresh_qc()

            account   = md.get_account()
            positions = md.get_positions()

            all_syms = set()
            for cfg in pe.get_strategies():
                all_syms.update(cfg.get("universe", []))
            for pos in positions:
                all_syms.add(pos.get("symbol", ""))
            all_syms.discard("")

            prices   = md.get_current_prices(list(all_syms)) if all_syms else {}
            port_hist = pe.get_equity_history()

            self.root.after(0, lambda: self._update_all(
                account, positions, prices, port_hist))
        except Exception as e:
            print(f"[REFRESH] {e}")
        finally:
            self.root.after(REFRESH_INTERVAL_MS, self._do_refresh)

    def _update_all(self, account, positions, prices, port_hist):
        self._account   = account
        self._positions = positions
        self._prices    = prices
        self._port_hist = port_hist

        equity    = float(account.get("equity",      100_000))
        cash      = float(account.get("cash",         equity))
        day_pnl   = sum(float(p.get("unrealized_intraday_pl", 0)) for p in positions)
        initial   = pe.get_initial_equity() or equity
        total_pnl = equity - initial

        # Nav bar
        sess_str, sess_clr = self._market_session()
        self._nav_labels["equity"].config(text=f"${equity:,.2f}")
        self._nav_labels["day_pnl"].config(
            text=f"{day_pnl:+,.2f}", fg=_clr(day_pnl))
        self._nav_labels["session"].config(text=sess_str, fg=sess_clr)

        # Portfolio panes
        self._eq_labels["equity"].config(text=f"${equity:,.2f}")
        self._eq_labels["total_pnl"].config(
            text=f"${total_pnl:+,.2f}", fg=_clr(total_pnl))
        self._eq_labels["cash"].config(text=f"${cash:,.2f}")
        self._eq_labels["positions"].config(text=str(len(positions)))

        self._update_equity_curve(port_hist, equity)
        self._update_strategy_pane(pe.get_strategies(), pe.get_weights(), equity)
        self._update_positions_pane(positions)
        self._update_transactions()


# ─────────────────────────────────────────────────────────────────────────────

def _on_close(root):
    from config import ACTIVE_BROKER
    if ACTIVE_BROKER == "ibkr":
        try:
            from ibkr_data import disconnect
            disconnect()
        except Exception:
            pass
    root.destroy()


def launch():
    root = tk.Tk()
    app  = QCDashboard(root)
    root.protocol("WM_DELETE_WINDOW", lambda: _on_close(root))
    root.mainloop()
