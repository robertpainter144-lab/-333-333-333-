#!/usr/bin/env python3.10
"""
Trading Dashboard
  Tab 1 : SPY Live Trader  — position monitor, regime, activity log, start/stop
  Tab 2 : Market Scanner   — intraday momentum setups across S&P 100 + momentum names

Run with:  python3.10 trading_dashboard.py
"""

import json, re, shutil, subprocess, sys, threading, time, warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path
from tkinter import *
from tkinter import ttk
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.path import Path as MPath
import matplotlib.patches as mpatches
import matplotlib.ticker
import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

# ── Config ────────────────────────────────────────────────────────────────────
ET          = ZoneInfo("America/New_York")
STATE_FILE   = Path("spy_trader_state.json")
LOG_FILE     = Path("spy_trader.log")
TRADER_PY    = Path("spy_live_trader.py")
PORT_STATE   = Path("portfolio_state.json")
PORT_LOG     = Path("portfolio.log")
PORT_PY      = Path("mock_portfolio.py")
# ib_insync lives in python3.14; tkinter lives in python3.10
TRADER_PY3   = shutil.which("python3.14") or shutil.which("python3") or sys.executable

# ── Alpaca credentials (loaded from macOS Keychain via alpaca_keys.py) ────────
from alpaca_keys import BROKER_KEY as ALPACA_KEY, BROKER_SECRET as ALPACA_SECRET, BROKER_ACCT as ALPACA_ACCT
ALPACA_BASE = "https://broker-api.sandbox.alpaca.markets"

# ── Palette (dark terminal) ────────────────────────────────────────────────────
C = dict(
    bg      = "#0d1117",
    bg2     = "#161b22",
    bg3     = "#21262d",
    border  = "#30363d",
    fg      = "#c9d1d9",
    fg2     = "#8b949e",
    green   = "#3fb950",
    red     = "#f85149",
    yellow  = "#d29922",
    blue    = "#58a6ff",
    purple  = "#bc8cff",
)

# ── Scanner universe ──────────────────────────────────────────────────────────
UNIVERSE = [
    # Mega-cap / S&P 100
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","JPM","V",
    "LLY","XOM","MA","JNJ","HD","PG","BAC","WMT","CVX","KO",
    "MRK","COST","AMD","CSCO","MCD","GE","CAT","TXN","NFLX","QCOM",
    "GS","MS","SBUX","SCHW","AMAT","GILD","SYK","BKNG","ISRG","VRTX",
    # High-beta / momentum names
    "F","UBER","COIN","PLTR","SOFI","PYPL","MSTR","SMCI","RIVN","HOOD",
    "CRWD","SNOW","MELI","SHOP","SE","DKNG","RBLX","U","LYFT","LCID",
    # Sector ETFs (good trend barometers)
    "QQQ","IWM","SPY","XLK","XLF","XLE","XLV","XLI","SOXL","TQQQ",
]


# ── Indicator helpers (same math as backtest) ─────────────────────────────────

def _adx(df, period=14):
    up   = df["High"] - df["High"].shift(1)
    dn   = df["Low"].shift(1) - df["Low"]
    pdm  = np.where((up > dn) & (up > 0), up, 0.0)
    ndm  = np.where((dn > up) & (dn > 0), dn, 0.0)
    hl   = df["High"] - df["Low"]
    hcp  = (df["High"] - df["Close"].shift(1)).abs()
    lcp  = (df["Low"]  - df["Close"].shift(1)).abs()
    tr   = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
    atr  = tr.ewm(span=period, adjust=False).mean()
    pdi  = 100 * pd.Series(pdm, index=df.index).ewm(span=period, adjust=False).mean() / atr
    ndi  = 100 * pd.Series(ndm, index=df.index).ewm(span=period, adjust=False).mean() / atr
    dx   = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    adx  = dx.ewm(span=period, adjust=False).mean()
    df   = df.copy()
    df["_atr"] = atr
    df["_adx"] = adx
    df["_pdi"] = pdi
    df["_ndi"] = ndi
    return df

def _regime(adx, pdi, ndi, thresh=35):
    if adx <= thresh:
        return "ranging"
    return "bull" if pdi >= ndi else "bear"


# ── Per-symbol scan ───────────────────────────────────────────────────────────

def scan_symbol(sym):
    try:
        raw = yf.Ticker(sym).history(period="10d", interval="5m", auto_adjust=True)
        if raw.empty or len(raw) < 60:
            return None
        raw.index = raw.index.tz_convert(ET)
        today = date.today()
        today_bars = raw[raw.index.date == today]
        if len(today_bars) < 4:
            return None

        df = _adx(raw.copy(), period=14)
        df["_ema"] = df["Close"].ewm(span=25, adjust=False).mean()

        # VWAP (today only, reset daily)
        tb = df[df.index.date == today].copy()
        tp = (tb["High"] + tb["Low"] + tb["Close"]) / 3
        tb["_vwap"] = (tp * tb["Volume"]).cumsum() / tb["Volume"].cumsum()

        last  = df.iloc[-1]
        tlast = tb.iloc[-1]
        close = float(last["Close"])
        ema   = float(last["_ema"])
        atr   = float(last["_atr"])
        adx_v = float(last["_adx"])
        pdi_v = float(last["_pdi"])
        ndi_v = float(last["_ndi"])
        vwap  = float(tlast["_vwap"])
        reg   = _regime(adx_v, pdi_v, ndi_v)

        # Relative volume: today vs prior-day average
        vol_today = int(tb["Volume"].sum())
        prev = df[df.index.date < today]
        if not prev.empty:
            prev_dates = pd.Series(prev.index.date)
            bars_per_day = len(prev) / max(prev_dates.nunique(), 1)
            avg_vol = float(prev["Volume"].mean()) * bars_per_day
            rel_vol = vol_today / avg_vol if avg_vol > 0 else 1.0
        else:
            rel_vol = 1.0

        # Intraday % change from today's open
        open_p   = float(tb.iloc[0]["Open"])
        chg_pct  = (close - open_p) / open_p * 100 if open_p else 0.0

        # Gap vs prev close
        if not prev.empty:
            prev_close = float(prev.iloc[-1]["Close"])
            gap_pct    = (open_p - prev_close) / prev_close * 100
        else:
            gap_pct = 0.0

        # Signal
        if reg in ("ranging", "bull") and close > ema and rel_vol > 1.3:
            signal = "LONG"
        elif reg == "bear" and close < ema and rel_vol > 1.3:
            signal = "SHORT"
        else:
            signal = "watch"

        # Composite score (0-100)
        score = int(
            min(rel_vol / 3.0, 1.0) * 30 +         # volume conviction
            min(abs(chg_pct) / 5.0, 1.0) * 30 +    # intraday momentum
            min(adx_v / 50.0, 1.0) * 20 +           # trend strength
            max(0.0, 20 - abs((close - vwap) / max(vwap, 0.01) * 100) * 4)  # VWAP proximity
        )

        # Entry / stop / target
        if signal == "LONG":
            entry  = close
            stop   = round(close - 2 * atr, 2)
            target = round(close + 4 * atr, 2)
        elif signal == "SHORT":
            entry  = close
            stop   = round(close + 2 * atr, 2)
            target = round(close - 4 * atr, 2)
        else:
            entry = stop = target = None

        return dict(
            symbol=sym, price=round(close, 2), chg_pct=round(chg_pct, 2),
            gap_pct=round(gap_pct, 2), rel_vol=round(rel_vol, 2),
            adx=round(adx_v, 1), regime=reg, signal=signal, score=score,
            entry=entry, stop=stop, target=target,
            atr=round(atr, 2), vwap=round(vwap, 2), ema=round(ema, 2),
        )
    except Exception:
        return None


# ── Dashboard ─────────────────────────────────────────────────────────────────

class Dashboard:

    def __init__(self, root):
        self.root           = root
        self.trader_proc    = None
        self.portfolio_proc = None
        self.scan_results   = []
        self._port_log_pos  = 0
        self._live_prices        = {}   # sym -> float, updated by background thread
        self._next_scan_at       = 0.0  # epoch time of next auto-scan
        self._alpaca_equity_hist = []   # list of (datetime, float) from Alpaca portfolio/history

        root.title("Trading Dashboard")
        root.configure(bg=C["bg"])
        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{sw}x{sh}+0+0")
        root.minsize(1200, 700)

        self._apply_style()
        self._build_ui()
        self._refresh_trader()
        self._refresh_portfolio()
        self._refresh_sankey()
        threading.Thread(target=self._ah_scan_worker, daemon=True).start()
        threading.Thread(target=self._live_price_loop, daemon=True).start()
        threading.Thread(target=self._alpaca_equity_loop, daemon=True).start()
        self._tick_clock()

    # ── Styling ───────────────────────────────────────────────────────────────

    def _apply_style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure(".",
                      background=C["bg"], foreground=C["fg"],
                      fieldbackground=C["bg2"], font=("Courier", 12))
        st.configure("TNotebook",      background=C["bg"],  borderwidth=0)
        st.configure("TNotebook.Tab",  background=C["bg2"], foreground=C["fg2"],
                      padding=[18, 8],  font=("Courier", 12, "bold"))
        st.map("TNotebook.Tab",
               background=[("selected", C["bg3"])],
               foreground=[("selected", C["fg"])])
        st.configure("Treeview",
                      background=C["bg2"], foreground=C["fg"],
                      fieldbackground=C["bg2"], rowheight=30, borderwidth=0,
                      font=("Courier", 11))
        st.configure("Treeview.Heading",
                      background=C["bg3"], foreground=C["fg2"],
                      font=("Courier", 11, "bold"))
        st.map("Treeview", background=[("selected", C["bg3"])],
               foreground=[("selected", C["blue"])])
        # Compact log treeview — smaller row height and font
        st.configure("Log.Treeview",
                      background=C["bg2"], foreground=C["fg"],
                      fieldbackground=C["bg2"], rowheight=22, borderwidth=0,
                      font=("Courier", 10))
        st.configure("Log.Treeview.Heading",
                      background=C["bg3"], foreground=C["fg2"],
                      font=("Courier", 9, "bold"))
        st.map("Log.Treeview", background=[("selected", C["bg3"])],
               foreground=[("selected", C["blue"])])
        st.configure("Vertical.TScrollbar",
                      background=C["bg3"], troughcolor=C["bg2"],
                      borderwidth=0, arrowsize=14)
        st.configure("TPanedwindow", background=C["bg"])
        st.configure("Sash", sashthickness=6, sashpad=2,
                      background=C["border"], relief=FLAT)

    # ── UI builder ────────────────────────────────────────────────────────────

    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill=BOTH, expand=True, padx=8, pady=8)

        tf   = Frame(nb, bg=C["bg"])
        pf   = Frame(nb, bg=C["bg"])
        af   = Frame(nb, bg=C["bg"])
        ahf  = Frame(nb, bg=C["bg"])
        rthf = Frame(nb, bg=C["bg"])
        sf   = Frame(nb, bg=C["bg"])
        drf  = Frame(nb, bg=C["bg"])
        swf  = Frame(nb, bg=C["bg"])
        btf  = Frame(nb, bg=C["bg"])
        ctf  = Frame(nb, bg=C["bg"])
        wff  = Frame(nb, bg=C["bg"])
        nb.add(tf,   text="  SPY Trader  ")
        nb.add(pf,   text="  Portfolio  ")
        nb.add(af,   text="  Allocations  ")
        nb.add(ahf,  text="  Global Markets 24/7  ")
        nb.add(rthf, text="  US Regular Hours  ")
        nb.add(sf,   text="  Market Scanner  ")
        nb.add(drf,  text="  Daily Review  ")
        nb.add(swf,  text="  🤖 Swarm AI  ")
        nb.add(btf,  text="  📊 Backtest  ")
        nb.add(ctf,  text="  ₿ Crypto  ")
        nb.add(wff,  text="  🧠 20Y WalkFwd  ")

        self._build_trader(tf)
        self._build_portfolio(pf)
        self._build_allocations(af)
        self._build_afterhours(ahf)
        self._build_rth(rthf)
        self._build_scanner(sf)
        self._build_daily_review(drf)
        self._build_swarm_tab(swf)
        self._build_backtest_tab(btf)
        self._build_crypto_tab(ctf)
        self._build_wf20y_tab(wff)

    # ── After-Hours / Ops-Center tab ─────────────────────────────────────────

    @staticmethod
    def _ah_class(sym: str) -> str:
        if sym.endswith("=X"):   return "forex"
        if sym.endswith("-USD"): return "crypto"
        if sym in {"GLD","SLV","GDX","USO","UNG",
                   "WEAT","CORN","SOYB","DBA","PDBC"}: return "commodity"
        if sym in {"EWJ","EWG","EWU","EWC","EWA","FXI","KWEB",
                   "EEM","EWZ","INDA","EWH","EWT","EWY","VWO","IEMG",
                   "EWS","EWL","EWP","EWQ","EWI","EWD","EWN","EWK",
                   "HEWJ","MCHI","ASHR"}:          return "intl"
        return "us"

    @staticmethod
    def _is_global(sym: str) -> bool:
        return Dashboard._ah_class(sym) != "us"

    def _build_afterhours(self, parent):
        self.ah_stats   = {}
        self.ah_results = []
        self._ah_log_pos = 0

        # ── Top ops bar ───────────────────────────────────────────────────────
        top = Frame(parent, bg=C["bg2"])
        top.pack(fill=X, side=TOP)

        sess_f = Frame(top, bg=C["bg2"])
        sess_f.pack(side=LEFT, padx=16, pady=10)
        Label(sess_f, text="GLOBAL MARKETS 24/7", bg=C["bg2"],
              fg=C["purple"], font=("Courier", 11)).pack(anchor=W)
        self.ah_session = Label(sess_f, text="● OVERNIGHT", bg=C["bg2"],
                                 fg=C["purple"], font=("Courier", 20, "bold"))
        self.ah_session.pack(anchor=W)
        self.ah_crit1 = Label(sess_f, text="", bg=C["bg2"],
                               fg=C["fg2"], font=("Courier", 9))
        self.ah_crit1.pack(anchor=W)

        Frame(top, bg=C["border"], width=2).pack(side=LEFT, fill=Y, pady=6)

        for key, color in [("Equity", C["fg"]), ("P&L", C["green"]),
                            ("Positions", C["blue"]), ("Ready", C["green"]),
                            ("Watching", C["yellow"])]:
            col = Frame(top, bg=C["bg2"])
            col.pack(side=LEFT, padx=22, pady=8)
            Label(col, text=key, bg=C["bg2"], fg=C["fg2"],
                  font=("Courier", 9)).pack(anchor=W)
            v = Label(col, text="—", bg=C["bg2"], fg=color,
                      font=("Courier", 22, "bold"))
            v.pack(anchor=W)
            self.ah_stats[key] = v

        # Alpaca stat
        alp_f = Frame(top, bg=C["bg2"])
        alp_f.pack(side=LEFT, padx=22, pady=8)
        Label(alp_f, text="Alpaca", bg=C["bg2"], fg=C["fg2"],
              font=("Courier", 9)).pack(anchor=W)
        self.ah_alpaca_lbl = Label(alp_f, text="—", bg=C["bg2"], fg=C["blue"],
                                    font=("Courier", 22, "bold"))
        self.ah_alpaca_lbl.pack(anchor=W)

        # Clock + heartbeat (far right, before scan button)
        clk_f = Frame(top, bg=C["bg2"])
        clk_f.pack(side=RIGHT, padx=16, pady=8)
        self.ah_clock = Label(clk_f, text="00:00:00 ET", bg=C["bg2"],
                               fg=C["fg"], font=("Courier", 22, "bold"))
        self.ah_clock.pack(anchor=E)
        self.ah_heartbeat = Label(clk_f, text="bot: —", bg=C["bg2"],
                                   fg=C["fg2"], font=("Courier", 9))
        self.ah_heartbeat.pack(anchor=E, pady=(2, 0))
        self.ah_next_scan = Label(clk_f, text="", bg=C["bg2"],
                                   fg=C["fg2"], font=("Courier", 9))
        self.ah_next_scan.pack(anchor=E)

        btn_f = Frame(top, bg=C["bg2"])
        btn_f.pack(side=RIGHT, padx=8, pady=8)
        Button(btn_f, text="⟳  SCAN NOW", command=self._ah_scan_async,
               bg=C["purple"], fg="#fff", font=("Courier", 13, "bold"),
               relief=FLAT, padx=14, pady=8).pack(anchor=E)
        self.ah_status = Label(btn_f, text="Not scanned yet", bg=C["bg2"],
                                fg=C["fg2"], font=("Courier", 9))
        self.ah_status.pack(anchor=E, pady=(4, 0))

        Frame(parent, bg=C["border"], height=2).pack(fill=X)

        # ── Main body: resizable 3-column PanedWindow ─────────────────────────
        h_pw = ttk.PanedWindow(parent, orient=HORIZONTAL)
        h_pw.pack(fill=BOTH, expand=True, padx=4, pady=6)

        # ── LEFT PANE: Open Positions + Log (vertical split) ─────────────────
        left = Frame(h_pw, bg=C["bg"])
        h_pw.add(left, weight=1)

        lv_pw = ttk.PanedWindow(left, orient=VERTICAL)
        lv_pw.pack(fill=BOTH, expand=True)

        # Top: positions
        pos_pane = Frame(lv_pw, bg=C["bg"])
        lv_pw.add(pos_pane, weight=2)

        Label(pos_pane, text="● OPEN POSITIONS", bg=C["bg"], fg=C["blue"],
              font=("Courier", 13, "bold")).pack(anchor=W, padx=8, pady=(6, 4))

        pos_outer = Frame(pos_pane, bg=C["bg"])
        pos_outer.pack(fill=BOTH, expand=True, padx=6, pady=(0, 4))
        pos_cv = Canvas(pos_outer, bg=C["bg"], highlightthickness=0)
        pos_sb = ttk.Scrollbar(pos_outer, orient=VERTICAL, command=pos_cv.yview)
        pos_cv.configure(yscrollcommand=pos_sb.set)
        pos_cv.pack(side=LEFT, fill=BOTH, expand=True)
        pos_sb.pack(side=RIGHT, fill=Y)
        self._pos_inner = Frame(pos_cv, bg=C["bg"])
        self._pos_win   = pos_cv.create_window((0, 0), window=self._pos_inner, anchor="nw")
        self._pos_inner.bind("<Configure>",
            lambda e: pos_cv.configure(scrollregion=pos_cv.bbox("all")))
        pos_cv.bind("<Configure>",
            lambda e: pos_cv.itemconfig(self._pos_win, width=e.width))

        # Bottom: activity log
        log_pane = Frame(lv_pw, bg=C["bg"])
        lv_pw.add(log_pane, weight=1)

        Label(log_pane, text="ACTIVITY LOG", bg=C["bg"], fg=C["fg2"],
              font=("Courier", 11, "bold")).pack(anchor=W, padx=8, pady=(6, 3))
        log_outer = Frame(log_pane, bg=C["bg2"])
        log_outer.pack(fill=BOTH, expand=True, padx=6, pady=(0, 6))
        self.ah_log = self._build_log_tree(log_outer)

        # ── CENTER PANE: Charts (top) + Ready cards (bottom) — vertical split ─
        center = Frame(h_pw, bg=C["bg"])
        h_pw.add(center, weight=1)

        v_pw = ttk.PanedWindow(center, orient=VERTICAL)
        v_pw.pack(fill=BOTH, expand=True)

        # Top sub-pane: equity curve + allocation/pnl charts
        charts_pane = Frame(v_pw, bg=C["bg"])
        v_pw.add(charts_pane, weight=2)

        Label(charts_pane, text="PORTFOLIO EQUITY", bg=C["bg"],
              fg=C["fg2"], font=("Courier", 11, "bold")).pack(anchor=W, padx=6, pady=(6, 3))

        self.ah_eq_fig = Figure(figsize=(5, 2.2), facecolor=C["bg"])
        self.ah_eq_ax  = self.ah_eq_fig.add_subplot(111, facecolor=C["bg2"])
        self.ah_eq_fig.subplots_adjust(left=0.1, right=0.98, top=0.92, bottom=0.2)
        self.ah_eq_canvas = FigureCanvasTkAgg(self.ah_eq_fig, master=charts_pane)
        self.ah_eq_canvas.get_tk_widget().pack(fill=X, padx=4)
        self._draw_equity_chart()

        Frame(charts_pane, bg=C["border"], height=1).pack(fill=X, padx=6, pady=(4, 3))
        Label(charts_pane, text="ALLOCATION  ·  P&L", bg=C["bg"],
              fg=C["fg2"], font=("Courier", 11, "bold")).pack(anchor=W, padx=6)

        self.ah_charts_fig = Figure(figsize=(5, 2.4), facecolor=C["bg"])
        self.ah_charts_fig.subplots_adjust(
            left=0.03, right=0.97, top=0.88, bottom=0.08, wspace=0.35)
        self.ah_alloc_ax = self.ah_charts_fig.add_subplot(121)
        self.ah_pnl_ax   = self.ah_charts_fig.add_subplot(122)
        self.ah_charts_canvas = FigureCanvasTkAgg(self.ah_charts_fig, master=charts_pane)
        self.ah_charts_canvas.get_tk_widget().pack(fill=X, padx=4)
        self._draw_portfolio_charts()

        # Bottom sub-pane: ready to enter cards
        ready_pane = Frame(v_pw, bg=C["bg"])
        v_pw.add(ready_pane, weight=1)

        Label(ready_pane, text="✓ READY TO ENTER", bg=C["bg"], fg=C["green"],
              font=("Courier", 13, "bold")).pack(anchor=W, padx=8, pady=(6, 4))

        ready_outer = Frame(ready_pane, bg=C["bg"])
        ready_outer.pack(fill=BOTH, expand=True, padx=6, pady=(0, 6))
        ready_cv = Canvas(ready_outer, bg=C["bg"], highlightthickness=0)
        ready_sb = ttk.Scrollbar(ready_outer, orient=VERTICAL, command=ready_cv.yview)
        ready_cv.configure(yscrollcommand=ready_sb.set)
        ready_cv.pack(side=LEFT, fill=BOTH, expand=True)
        ready_sb.pack(side=RIGHT, fill=Y)
        self._ready_inner = Frame(ready_cv, bg=C["bg"])
        self._ready_win   = ready_cv.create_window((0, 0), window=self._ready_inner, anchor="nw")
        self._ready_inner.bind("<Configure>",
            lambda e: ready_cv.configure(scrollregion=ready_cv.bbox("all")))
        ready_cv.bind("<Configure>",
            lambda e: ready_cv.itemconfig(self._ready_win, width=e.width))

        # ── RIGHT PANE: On Watch ──────────────────────────────────────────────
        right = Frame(h_pw, bg=C["bg"])
        h_pw.add(right, weight=1)

        Label(right, text="○ ON WATCH", bg=C["bg"], fg=C["yellow"],
              font=("Courier", 13, "bold")).pack(anchor=W, padx=8, pady=(6, 2))
        self.ah_crit2 = Label(right, text="", bg=C["bg"],
                               fg=C["fg2"], font=("Courier", 9))
        self.ah_crit2.pack(anchor=W, padx=8, pady=(0, 4))

        wt_cols = ("Symbol", "Dir", "Price", "ADX", "Regime", "What's Blocking")
        wt_w = {"Symbol": 90, "Dir": 60, "Price": 88, "ADX": 58,
                "Regime": 80, "What's Blocking": 260}
        wt_f = Frame(right, bg=C["bg"])
        wt_f.pack(fill=BOTH, expand=True, padx=6, pady=(0, 6))
        self.ah_watch_tree = ttk.Treeview(wt_f, columns=wt_cols, show="headings",
                                           selectmode="browse")
        for c in wt_cols:
            self.ah_watch_tree.heading(c, text=c)
            self.ah_watch_tree.column(c, width=wt_w.get(c, 80), anchor=CENTER, stretch=False)
        wt_sb2 = ttk.Scrollbar(wt_f, orient=VERTICAL, command=self.ah_watch_tree.yview)
        self.ah_watch_tree.configure(yscrollcommand=wt_sb2.set)
        self.ah_watch_tree.pack(side=LEFT, fill=BOTH, expand=True)
        wt_sb2.pack(side=RIGHT, fill=Y)
        self.ah_watch_tree.tag_configure("long_w",  foreground=C["yellow"])
        self.ah_watch_tree.tag_configure("short_w", foreground=C["yellow"])
        self.ah_watch_tree.tag_configure("watch_w", foreground=C["fg2"])

        threading.Thread(target=self._ah_scan_worker, daemon=True).start()
        self._refresh_afterhours()

    # ── Alpaca portfolio history poller ───────────────────────────────────────

    def _alpaca_equity_loop(self):
        import urllib.request, base64
        auth = base64.b64encode(f"{ALPACA_KEY}:{ALPACA_SECRET}".encode()).decode()
        headers = {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
        }
        while True:
            try:
                url = (f"{ALPACA_BASE}/v1/trading/accounts/{ALPACA_ACCT}/account/portfolio/history"
                       f"?period=1W&timeframe=15Min&intraday_reporting=extended_hours"
                       f"&pnl_reset=no_reset")
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                timestamps = data.get("timestamp", [])
                equity     = data.get("equity", [])
                pairs = [
                    (datetime.fromtimestamp(ts, tz=ET).replace(tzinfo=None), float(eq))
                    for ts, eq in zip(timestamps, equity)
                    if eq is not None and eq > 0
                ]
                if pairs:
                    self._alpaca_equity_hist = pairs
                    # Also sync cash into portfolio state so bot's position sizing is accurate
                    try:
                        acct_req = urllib.request.Request(
                            f"{ALPACA_BASE}/v1/trading/accounts/{ALPACA_ACCT}/account",
                            headers=headers)
                        with urllib.request.urlopen(acct_req, timeout=10) as r:
                            acct = json.loads(r.read())
                        import pathlib
                        ps_path = pathlib.Path("portfolio_state.json")
                        ps = json.loads(ps_path.read_text()) if ps_path.exists() else {}
                        ps["alpaca_equity"] = float(acct.get("equity", 0))
                        ps_path.write_text(json.dumps(ps))
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(60)

    # ── Activity log (columnized Treeview) ───────────────────────────────────

    def _build_log_tree(self, parent) -> ttk.Treeview:
        cols = ("time", "type", "sym", "side", "qty", "price", "detail", "equity")
        hdr  = ("Time",   "Type",  "Sym",  "Side",  "Qty",  "Price",  "Detail",        "Equity")
        wids = (68,        58,      58,     48,       48,     68,       220,              90)
        tree = ttk.Treeview(parent, columns=cols, show="headings",
                            style="Log.Treeview", height=8)
        vsb  = ttk.Scrollbar(parent, orient=VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        for col, h, w in zip(cols, hdr, wids):
            tree.heading(col, text=h, anchor=W)
            tree.column(col, width=w, minwidth=w, stretch=(col == "detail"))
        tree.tag_configure("entry", foreground=C["green"])
        tree.tag_configure("exit",  foreground=C["yellow"])
        tree.tag_configure("scan",  foreground=C["purple"])
        tree.tag_configure("order", foreground=C["blue"])
        tree.tag_configure("dim",   foreground=C["fg2"])
        tree.pack(side=LEFT, fill=BOTH, expand=True)
        vsb.pack(side=RIGHT, fill=Y)
        return tree

    @staticmethod
    def _parse_log_line(line: str):
        """Return (tag, values-tuple) for a log line, or None to skip."""
        import re as _re
        # Strip leading timestamp  "2026-05-07 14:22:51  "
        m = _re.match(r'(\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}))\s+(.*)', line)
        if not m:
            return None
        ts, body = m.group(2), m.group(3).strip()

        # ENTRY  SNOW    LONG   64 @ 154.57  trail=152.48  regime=ranging  [scanner]  equity=$49,744
        me = _re.match(r'ENTRY\s+(\S+)\s+(LONG|SHORT)\s+(\d+)\s+@\s+([\d.]+)(.*)', body)
        if me:
            sym, side, qty, price, rest = me.groups()
            trail = (_re.search(r'trail=([\d.]+)', rest) or _re.search(r'stop=([\d.]+)', rest))
            trail_s = f"trail={trail.group(1)}" if trail else ""
            strat   = (_re.search(r'\[(\w+)\]', rest) or _re.search(r'\[([^\]]+)\]', rest))
            strat_s = strat.group(1) if strat else ""
            eq      = _re.search(r'equity=\$([\d,]+)', rest)
            eq_s    = f"${eq.group(1)}" if eq else ""
            detail  = "  ".join(filter(None, [trail_s, strat_s]))
            return ("entry", (ts, "ENTRY", sym, side, qty, price, detail, eq_s))

        # EXIT  SNOW    LONG   64 @ 154.57  pnl=+12  [trail_stop]  equity=$49,744
        mx = _re.match(r'EXIT\s+(\S+)\s+(LONG|SHORT)\s+(\d+)\s+@\s+([\d.]+)(.*)', body)
        if mx:
            sym, side, qty, price, rest = mx.groups()
            pnl  = _re.search(r'pnl=([+\-\d,]+)', rest)
            pnl_s = pnl.group(1) if pnl else ""
            rsn  = _re.search(r'\[([^\]]+)\]', rest)
            rsn_s = rsn.group(1) if rsn else ""
            eq   = _re.search(r'equity=\$([\d,]+)', rest)
            eq_s = f"${eq.group(1)}" if eq else ""
            detail = "  ".join(filter(None, [f"pnl={pnl_s}", rsn_s]))
            return ("exit", (ts, "EXIT", sym, side, qty, price, detail, eq_s))

        # REBAL close SYM  pnl=-12  → opening XYZ
        mr = _re.match(r'REBAL\s+close\s+(\S+)\s+pnl=([+\-\d,]+)(.*)', body)
        if mr:
            sym, pnl, rest = mr.groups()
            new_sym = _re.search(r'opening\s+(\S+)', rest)
            detail  = f"pnl={pnl}  → {new_sym.group(1)}" if new_sym else f"pnl={pnl}"
            return ("exit", (ts, "REBAL", sym, "", "", "", detail, ""))

        # Alpaca ORDER  SYM BUY 1 @ ~$24.72  id=abc123
        mo = _re.match(r'Alpaca ORDER\s+(\S+)\s+(BUY|SELL|SELL_SHORT|BUY_TO_COVER)\s+(\d+)\s+@\s+~?\$?([\d.]+)(.*)', body)
        if mo:
            sym, side, qty, price, rest = mo.groups()
            oid = _re.search(r'id=(\S+)', rest)
            detail = f"id={oid.group(1)}" if oid else ""
            return ("order", (ts, "ALPACA", sym, side, qty, price, detail, ""))

        # Scan lines
        if body.startswith("Scan"):
            syms = _re.search(r'(\d+)\s+symbols', body)
            detail = f"{syms.group(1)} symbols" if syms else body[5:40]
            return ("scan", (ts, "SCAN", "", "", "", "", detail, ""))

        if body.startswith("Scan done"):
            setups = _re.search(r'(\d+)\s+setups', body)
            return ("scan", (ts, "SCAN", "", "", "", "", body[:50], ""))

        return ("dim", (ts, "INFO", "", "", "", "", body[:60], ""))

    def _log_append(self, tree: ttk.Treeview, line: str):
        parsed = self._parse_log_line(line)
        if parsed is None:
            return
        tag, vals = parsed
        tree.insert("", END, values=vals, tags=(tag,))
        # Keep max 300 rows
        children = tree.get_children()
        if len(children) > 300:
            tree.delete(children[0])
        tree.see(tree.get_children()[-1])

    # ── Equity curve chart ────────────────────────────────────────────────────

    def _draw_equity_chart(self):
        self._draw_equity_chart_to(self.ah_eq_ax, self.ah_eq_canvas)

    def _draw_equity_chart_to(self, ax, canvas):
        import matplotlib.dates as mdates
        from matplotlib.ticker import FuncFormatter
        ax.clear()
        ax.set_facecolor(C["bg2"])

        hist = self._alpaca_equity_hist  # list of (datetime, float)
        y_range = 0
        if not hist:
            ax.text(0.5, 0.5, "Fetching Alpaca equity history…",
                    transform=ax.transAxes, ha="center", va="center",
                    color=C["fg2"], fontsize=10)
        else:
            timestamps = [p[0] for p in hist]
            equities   = [p[1] for p in hist]
            baseline   = equities[0] if equities else 100000
            y_range    = max(equities) - min(equities) if len(equities) > 1 else 0

            ax.plot(timestamps, equities, color=C["blue"], linewidth=2, zorder=3)
            ax.axhline(baseline, color=C["fg2"], linewidth=1, linestyle="--", alpha=0.7)
            ax.fill_between(timestamps, equities, baseline,
                            where=[e >= baseline for e in equities],
                            color=C["green"], alpha=0.18, zorder=2)
            ax.fill_between(timestamps, equities, baseline,
                            where=[e < baseline for e in equities],
                            color=C["red"], alpha=0.18, zorder=2)

            last_eq  = equities[-1]
            pnl_pct  = (last_eq - baseline) / baseline * 100
            pnl_color = C["green"] if last_eq >= baseline else C["red"]
            ax.annotate(f"${last_eq:,.0f}  ({'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%)",
                        xy=(timestamps[-1], last_eq), xytext=(-8, 8),
                        textcoords="offset points", color=pnl_color,
                        fontsize=9, fontweight="bold", ha="right")
            ax.set_title(f"PORTFOLIO EQUITY  vs ${baseline/1000:.0f}k baseline",
                         color=C["fg2"], fontsize=9, pad=3, loc="left")
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=6))
            for lbl in ax.get_xticklabels():
                lbl.set_rotation(25); lbl.set_fontsize(8)

        # Adaptive y-axis precision: tight ranges need decimals to be readable
        if y_range > 5000:
            ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))
        elif y_range > 200:
            ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"${x/1000:.1f}k"))
        else:
            ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"${x:,.0f}"))
        ax.tick_params(colors=C["fg2"], labelsize=9)
        ax.yaxis.label.set_color(C["fg2"])
        for spine in ax.spines.values(): spine.set_color(C["border"])
        ax.grid(True, color=C["border"], linewidth=0.5, alpha=0.6)
        canvas.draw()

    # ── Allocation pie + P&L bars ─────────────────────────────────────────────

    def _draw_portfolio_charts(self, sym_filter=None):
        self._draw_portfolio_charts_to(
            self.ah_alloc_ax, self.ah_pnl_ax, self.ah_charts_canvas, sym_filter)

    def _draw_portfolio_charts_to(self, ax1, ax2, canvas, sym_filter=None):
        from matplotlib.ticker import FuncFormatter as _FF
        port_data = {}
        if PORT_STATE.exists():
            try:
                port_data = json.loads(PORT_STATE.read_text())
            except Exception:
                pass
        all_pos   = port_data.get("positions", {})
        positions = {s: p for s, p in all_pos.items()
                     if sym_filter is None or sym_filter(s)}
        cash      = float(port_data.get("cash", 100000))
        closed    = [t for t in port_data.get("closed", [])
                     if sym_filter is None or sym_filter(t.get("symbol",""))]

        # ── Allocation donut ──────────────────────────────────────────────────
        ax1.clear()
        ax1.set_facecolor(C["bg"])

        long_palette  = ["#3fb950", "#2ea043", "#56d364", "#7ee787", "#1a7f37"]
        short_palette = ["#f85149", "#da3633", "#ff7b72", "#ffa198"]

        vals, lbls, clrs = [], [], []
        li = si = 0
        for sym, pos in positions.items():
            px  = self._live_prices.get(sym, pos.get("last_price", pos.get("entry_price", 0)))
            val = max(pos.get("shares", 0) * px, 0)
            vals.append(val); lbls.append(sym)
            if pos.get("side", "long") == "long":
                clrs.append(long_palette[li % len(long_palette)]); li += 1
            else:
                clrs.append(short_palette[si % len(short_palette)]); si += 1

        if cash > 0:
            vals.append(cash); lbls.append("CASH"); clrs.append(C["fg2"])

        total = sum(vals)
        if not vals or total == 0:
            ax1.text(0.5, 0.5, "No data", transform=ax1.transAxes,
                     ha="center", va="center", color=C["fg2"], fontsize=8)
            ax1.axis("off")
        else:
            wedge_props = dict(width=0.52, edgecolor=C["bg"], linewidth=1.5)
            pcts = [v / total * 100 for v in vals]
            ax1.pie(vals, colors=clrs, wedgeprops=wedge_props, startangle=90,
                    autopct=lambda p: f"{p:.0f}%" if p >= 6 else "",
                    pctdistance=0.78,
                    textprops=dict(color=C["fg"], fontsize=7, fontweight="bold"))
            # Legend inside the donut hole
            ax1.text(0, 0, f"${total/1000:.0f}k", ha="center", va="center",
                     color=C["fg"], fontsize=9, fontweight="bold")
            # Manual symbol legend below
            legend_txt = "  ".join(
                f"{l}" for l, p in zip(lbls, pcts) if p >= 4
            )
            ax1.set_xlabel(legend_txt, color=C["fg2"], fontsize=6, labelpad=3)

        ax1.set_title("Allocation", color=C["fg2"], fontsize=9, pad=3)

        # ── P&L horizontal bars ───────────────────────────────────────────────
        ax2.clear()
        ax2.set_facecolor(C["bg2"])

        syms_b, pnls_b, bar_clrs = [], [], []
        for sym, pos in positions.items():
            entry  = pos.get("entry_price", 0)
            last   = self._live_prices.get(sym, pos.get("last_price", entry))
            shares = pos.get("shares", 0)
            side   = pos.get("side", "long")
            pnl    = (last - entry) * shares if side == "long" else (entry - last) * shares
            syms_b.append(sym); pnls_b.append(pnl)
            bar_clrs.append(C["green"] if pnl >= 0 else C["red"])

        for t in list(reversed(closed))[:5]:
            pnl_t = t.get("pnl", 0)
            syms_b.append(t["symbol"] + "✓")
            pnls_b.append(pnl_t)
            bar_clrs.append("#1f6b2e" if pnl_t >= 0 else "#6e1515")

        if not syms_b:
            ax2.text(0.5, 0.5, "No trades yet", transform=ax2.transAxes,
                     ha="center", va="center", color=C["fg2"], fontsize=8)
            ax2.axis("off")
        else:
            y = list(range(len(syms_b)))
            ax2.barh(y, pnls_b, color=bar_clrs, edgecolor="none", height=0.6)
            ax2.set_yticks(y)
            ax2.set_yticklabels(syms_b, fontsize=7, color=C["fg"])
            ax2.axvline(0, color=C["border"], linewidth=1)
            ax2.xaxis.set_major_formatter(_FF(lambda x, _: f"${x:+.0f}"))
            ax2.tick_params(axis="x", colors=C["fg2"], labelsize=6)
            ax2.tick_params(axis="y", colors=C["fg"], labelsize=7)
            for sp in ax2.spines.values():
                sp.set_color(C["border"])
            ax2.grid(True, axis="x", color=C["border"], linewidth=0.4, alpha=0.5)
            ax2.set_facecolor(C["bg2"])

        ax2.set_title("Open P&L  (✓=closed)", color=C["fg2"], fontsize=9, pad=3)
        canvas.draw()

    # ── Position card ─────────────────────────────────────────────────────────

    def _build_pos_card(self, parent, sym, pos):
        entry   = pos.get("entry_price", 0)
        shares  = pos.get("shares", 0)
        last_px = pos.get("last_price", entry)
        trail   = pos.get("trail_stop", 0)
        side    = pos.get("side", "long")
        strat   = pos.get("strategy", "")

        if side == "long":
            pnl = (last_px - entry) * shares
            pnl_pct = (last_px - entry) / entry * 100 if entry else 0
            vs_stop = last_px - trail if trail else 0
        else:
            pnl = (entry - last_px) * shares
            pnl_pct = (entry - last_px) / entry * 100 if entry else 0
            vs_stop = trail - last_px if trail else 0

        pnl_c  = C["green"] if pnl >= 0 else C["red"]
        dir_c  = C["green"] if side == "long" else C["red"]
        stop_c = C["green"] if vs_stop > 0 else C["red"]

        card = Frame(parent, bg=C["bg2"], padx=12, pady=8)
        card.pack(fill=X, pady=(0, 8))
        Frame(card, bg=dir_c, height=2).pack(fill=X, pady=(0, 6))

        row1 = Frame(card, bg=C["bg2"])
        row1.pack(fill=X)
        Label(row1, text=sym, bg=C["bg2"], fg=C["fg"],
              font=("Courier", 18, "bold")).pack(side=LEFT)
        Label(row1, text=f"  {'▲ LONG' if side == 'long' else '▼ SHORT'}",
              bg=C["bg2"], fg=dir_c, font=("Courier", 13, "bold")).pack(side=LEFT)
        Label(row1, text=f"{'+' if pnl_pct >= 0 else ''}{pnl_pct:.2f}%",
              bg=C["bg2"], fg=pnl_c, font=("Courier", 16, "bold")).pack(side=RIGHT)

        row2 = Frame(card, bg=C["bg2"])
        row2.pack(fill=X, pady=(4, 2))
        Label(row2, text=f"Entry ${entry:.4f}", bg=C["bg2"], fg=C["fg2"],
              font=("Courier", 10)).pack(side=LEFT)
        Label(row2, text=f"  →  ${last_px:.4f} now", bg=C["bg2"], fg=C["fg"],
              font=("Courier", 11, "bold")).pack(side=LEFT)
        Label(row2, text=f"{shares} shares", bg=C["bg2"], fg=C["fg2"],
              font=("Courier", 10)).pack(side=RIGHT)

        row3 = Frame(card, bg=C["bg2"])
        row3.pack(fill=X)
        pnl_s = f"{'+' if pnl >= 0 else ''}{pnl:.2f}"
        Label(row3, text=f"P&L  {pnl_s}", bg=C["bg2"], fg=pnl_c,
              font=("Courier", 13, "bold")).pack(side=LEFT)
        if trail:
            Label(row3, text=f"  Trail ${trail:.4f}  ({'+' if vs_stop >= 0 else ''}{vs_stop:.4f} away)",
                  bg=C["bg2"], fg=stop_c, font=("Courier", 10)).pack(side=LEFT)
        if strat:
            Label(row3, text=strat, bg=C["bg2"], fg=C["fg2"],
                  font=("Courier", 9)).pack(side=RIGHT)

    # ── Ready-to-enter card ───────────────────────────────────────────────────

    def _build_ready_card(self, parent, r, adx_thresh):
        sig     = r["signal"]
        col     = C["green"] if sig == "long" else C["red"]
        cls     = r["asset_class"].upper()
        cls_colors = {"FOREX": C["purple"], "CRYPTO": C["yellow"],
                      "COMMODITY": "#e3b341", "INTL": C["blue"], "US": C["fg2"]}
        cls_col = cls_colors.get(cls, C["fg2"])
        trail_s = f"${r['trail']:.4f}" if r.get("trail") else "—"

        card = Frame(parent, bg=C["bg2"], padx=10, pady=8)
        card.pack(fill=X, pady=(0, 6))
        Frame(card, bg=col, height=2).pack(fill=X, pady=(0, 6))

        row1 = Frame(card, bg=C["bg2"])
        row1.pack(fill=X)
        Label(row1, text=r["symbol"], bg=C["bg2"], fg=col,
              font=("Courier", 16, "bold")).pack(side=LEFT)
        Label(row1, text=f"  {'▲ LONG' if sig == 'long' else '▼ SHORT'}",
              bg=C["bg2"], fg=col, font=("Courier", 12, "bold")).pack(side=LEFT)
        Label(row1, text=f" {cls} ", bg=cls_col, fg="#0d1117",
              font=("Courier", 9, "bold")).pack(side=RIGHT)

        row2 = Frame(card, bg=C["bg2"])
        row2.pack(fill=X, pady=(4, 2))
        Label(row2, text=f"${r['close']:.4f}", bg=C["bg2"], fg=C["fg"],
              font=("Courier", 14, "bold")).pack(side=LEFT)
        chg_c = C["green"] if r["chg"] >= 0 else C["red"]
        Label(row2, text=f"  {'+' if r['chg'] >= 0 else ''}{r['chg']:.2f}%",
              bg=C["bg2"], fg=chg_c, font=("Courier", 11)).pack(side=LEFT)
        Label(row2, text=f"Trail {trail_s}", bg=C["bg2"], fg=C["red"],
              font=("Courier", 10)).pack(side=RIGHT)

        row3 = Frame(card, bg=C["bg2"])
        row3.pack(fill=X)
        conds = [f"ADX {r['adx']:.0f}>{adx_thresh} ✓",
                 f"{r['regime'].upper()} ✓",
                 "EMA ✓"]
        if r.get("rv", 0) > 0:
            conds.append(f"Vol {r['rv']:.1f}× ✓")
        Label(row3, text="  ".join(conds), bg=C["bg2"], fg=C["green"],
              font=("Courier", 10)).pack(side=LEFT)

    def _refresh_afterhours(self):
        try:
            self._update_afterhours()
        except Exception:
            pass
        self.root.after(3_000, self._refresh_afterhours)

    def _update_afterhours(self):
        now  = datetime.now(ET)
        mins = now.hour * 60 + now.minute
        in_rth = (9 * 60 + 30) <= mins <= (16 * 60)
        in_ext = (4 * 60)      <= mins <= (20 * 60)

        # Global session name based on which major markets are open
        LOGIC = "Entry: ADX>35 + bull/bear regime + price × EMA + vol≥1.2×  ·  Exit: trail 3×ATR long / 1×ATR short  ·  No shorts when SPY > 50d EMA"
        if 0 <= mins < 4 * 60:
            sess, sess_c = "● ASIA OPEN  00:00–04:00", C["blue"]
            crit1 = "Tokyo, Sydney, Shanghai active  ·  ADX>40 + EMA cross  ·  forex, crypto, Asia ETFs"
            crit2 = LOGIC
        elif in_ext and not in_rth:
            sess, sess_c = "● LONDON / PRE-MARKET  04:00–09:30", C["yellow"]
            crit1 = "London open + NY pre-market  ·  ADX>35 + vol≥2.5×  ·  EUR/GBP forex, European ETFs"
            crit2 = LOGIC
        elif in_rth:
            sess, sess_c = "● NY OVERLAP  09:30–16:00", C["green"]
            crit1 = "London + New York overlap — highest global liquidity  ·  commodities, intl ETFs, forex, crypto"
            crit2 = LOGIC
        elif mins <= 20 * 60:
            sess, sess_c = "● NY CLOSE / LONDON CLOSE  16:00–20:00", C["yellow"]
            crit1 = "Post-market + London wind-down  ·  ADX>35 + vol≥2.5×  ·  commodities, forex, crypto, intl ETFs"
            crit2 = LOGIC
        else:
            sess, sess_c = "● OVERNIGHT  20:00–00:00", C["purple"]
            crit1 = "Quiet period  ·  ADX>40 + confirmed bull or bear trend  ·  crypto 24/7, forex rolling"
            crit2 = LOGIC

        self.ah_session.config(text=sess, fg=sess_c)
        self.ah_crit1.config(text=crit1)
        self.ah_crit2.config(text=crit2)

        # Portfolio state — filter to global instruments only on this tab
        port_data = {}
        if PORT_STATE.exists():
            try:
                port_data = json.loads(PORT_STATE.read_text())
            except Exception:
                pass
        all_positions  = port_data.get("positions", {})
        open_positions = {s: p for s, p in all_positions.items()
                          if self._is_global(s)}
        hist      = self._alpaca_equity_hist
        alp_now   = hist[-1][1] if hist else 0.0
        alp_start = hist[0][1]  if hist else 0.0
        equity    = port_data.get("equity") or alp_now or 0   # local is live; Alpaca is lagged
        baseline  = alp_start if alp_start > 0 else 50_000.0
        pnl       = equity - baseline
        pnl_pct   = pnl / baseline * 100 if baseline > 0 else 0.0
        pnl_c     = C["green"] if pnl >= 0 else C["red"]

        ready = [r for r in self.ah_results if r.get("entry_ok")]
        watch = [r for r in self.ah_results if not r.get("entry_ok")]

        self.ah_stats["Equity"].config(text=f"${equity:,.0f}" if equity else "—")
        self.ah_stats["P&L"].config(
            text=f"{'+' if pnl >= 0 else ''}{pnl_pct:.2f}%" if alp_start else "—", fg=pnl_c)
        self.ah_stats["Positions"].config(
            text=str(len(open_positions)),
            fg=C["blue"] if open_positions else C["fg2"])
        self.ah_stats["Ready"].config(
            text=str(len(ready)), fg=C["green"] if ready else C["fg2"])
        self.ah_stats["Watching"].config(
            text=str(len(watch)), fg=C["yellow"] if watch else C["fg2"])
        alp_live = self._alpaca_equity_hist[-1][1] if self._alpaca_equity_hist else 0
        if alp_live > 0:
            self.ah_alpaca_lbl.config(text=f"${alp_live:,.0f}", fg=C["blue"])
        else:
            self.ah_alpaca_lbl.config(text="fetching…", fg=C["fg2"])

        # Rebuild position cards — global instruments only
        for w in self._pos_inner.winfo_children():
            w.destroy()
        if open_positions:
            for sym, pos in open_positions.items():
                live = self._live_prices.get(sym)
                if live:
                    pos = dict(pos, last_price=live)
                self._build_pos_card(self._pos_inner, sym, pos)
        else:
            Label(self._pos_inner,
                  text="No global positions open\n(forex · crypto · intl ETFs · commodities)",
                  bg=C["bg"], fg=C["fg2"], font=("Courier", 11),
                  justify=CENTER).pack(pady=20)

        # Refresh charts — global instruments only
        self._draw_equity_chart_to(self.ah_eq_ax, self.ah_eq_canvas)
        self._draw_portfolio_charts_to(
            self.ah_alloc_ax, self.ah_pnl_ax, self.ah_charts_canvas,
            sym_filter=self._is_global)

        # Stream new log lines into columnized treeview
        if PORT_LOG.exists():
            try:
                text = PORT_LOG.read_text()
                new  = text[self._ah_log_pos:]
                if new:
                    self._ah_log_pos = len(text)
                    for line in new.splitlines():
                        if line.strip():
                            self._log_append(self.ah_log, line)
            except Exception:
                pass

    def _ah_scan_async(self):
        self.ah_status.config(text="Scanning universe…", fg=C["yellow"])
        self.root.update_idletasks()
        threading.Thread(target=self._ah_scan_worker, daemon=True).start()

    def _ah_scan_worker(self):
        from zoneinfo import ZoneInfo as _ZI
        _ET = _ZI("America/New_York")
        now  = datetime.now(_ET)
        mins = now.hour * 60 + now.minute
        in_rth = (9 * 60 + 30) <= mins <= 16 * 60
        in_ext = (4 * 60) <= mins <= 20 * 60
        adx_thresh = 35 if in_rth or in_ext else 40
        min_rv     = 1.2 if in_rth else 2.5 if in_ext else 0.0

        AH_UNIVERSE = [
            # Commodities (trade globally, nearly 24/7)
            "GLD","SLV","GDX","USO","UNG","WEAT","CORN","SOYB","DBA","PDBC",
            # International equity ETFs
            "EWJ","EWG","EWU","EWC","EWA","FXI","KWEB","EEM","EWZ","INDA",
            "EWH","EWT","EWY","VWO","IEMG","EWS","EWL","EWP","EWQ","EWI",
            # Forex majors + EM
            "EURUSD=X","GBPUSD=X","USDJPY=X","AUDUSD=X","USDCAD=X",
            "USDCHF=X","NZDUSD=X","USDMXN=X","USDBRL=X","USDCNY=X",
            "USDINR=X","USDKRW=X","USDHKD=X","USDSGD=X","USDTRY=X",
            # Crypto (24/7)
            "BTC-USD","ETH-USD","SOL-USD","BNB-USD","XRP-USD",
            "ADA-USD","AVAX-USD","DOT-USD","DOGE-USD","LINK-USD",
        ]

        def _score(sym):
            try:
                raw = yf.Ticker(sym).history(period="10d", interval="5m",
                                              auto_adjust=True, prepost=True)
                if raw.empty or len(raw) < 20:
                    return None
                raw.index = raw.index.tz_convert(_ET)
                df = raw.copy()
                df["ema"] = df["Close"].ewm(span=25, adjust=False).mean()
                hl  = df["High"] - df["Low"]
                hcp = (df["High"] - df["Close"].shift(1)).abs()
                lcp = (df["Low"]  - df["Close"].shift(1)).abs()
                tr  = pd.concat([hl, hcp, lcp], axis=1).max(axis=1)
                df["atr"] = tr.ewm(span=14, adjust=False).mean()
                up  = df["High"] - df["High"].shift(1)
                dn  = df["Low"].shift(1) - df["Low"]
                pdm = np.where((up > dn) & (up > 0), up, 0.0)
                ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
                aw  = tr.ewm(span=20, adjust=False).mean()
                pdi = 100 * pd.Series(pdm, index=df.index).ewm(span=20, adjust=False).mean() / aw
                ndi = 100 * pd.Series(ndm, index=df.index).ewm(span=20, adjust=False).mean() / aw
                dx  = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
                df["adx"] = dx.ewm(span=20, adjust=False).mean()
                df["pdi"] = pdi; df["ndi"] = ndi

                last  = df.iloc[-1]
                close = float(last["Close"])
                atr   = float(last["atr"])
                adx   = float(last["adx"])
                ema   = float(last["ema"])
                reg   = _regime(adx, float(last["pdi"]), float(last["ndi"]))

                last_date = last.name.date()
                day_bars  = df[df.index.date == last_date]
                day_vol   = float(day_bars["Volume"].sum())
                prev_bars = df[df.index.date < last_date]
                if not prev_bars.empty:
                    prev_adv = prev_bars.groupby(prev_bars.index.date)["Volume"].sum().mean()
                    rv = day_vol / prev_adv if prev_adv > 0 else 0.0
                else:
                    rv = 0.0

                open_p = float(day_bars.iloc[0]["Open"]) if len(day_bars) > 0 else close
                chg    = (close - open_p) / open_p * 100 if open_p else 0

                if reg in ("ranging", "bull") and close > ema:
                    sig = "long"
                elif reg == "bear" and close < ema:
                    sig = "short"
                else:
                    sig = "watch"

                # Entry eligibility
                entry_ok = (adx > adx_thresh and reg in ("bull", "bear") and sig != "watch"
                            and (min_rv == 0.0 or rv >= min_rv))

                # Plain-English block reason for the watch section
                if entry_ok:
                    block_reason = ""
                elif adx <= adx_thresh:
                    block_reason = f"ADX {adx:.0f} — need >{adx_thresh} ✗"
                elif reg == "ranging":
                    block_reason = "RANGING — need bull or bear trend ✗"
                elif sig == "watch":
                    block_reason = "price on wrong side of EMA ✗"
                elif min_rv > 0 and rv < min_rv:
                    block_reason = f"volume {rv:.1f}× — need >{min_rv:.1f}× ✗"
                else:
                    block_reason = "conditions not met ✗"

                score = int(min(abs(chg) / 5.0, 1.0) * 35 + min(adx / 50.0, 1.0) * 35 +
                            min(max(rv, 0.1) / 3.0, 1.0) * 30)

                trail = round(close - 3 * atr, 4) if sig == "long" \
                        else round(close + 3 * atr, 4) if sig == "short" else None

                return dict(symbol=sym, signal=sig, score=score, close=close,
                            chg=chg, adx=adx, rv=rv, regime=reg, atr=atr,
                            entry_ok=entry_ok, trail=trail, block_reason=block_reason,
                            asset_class=Dashboard._ah_class(sym))
            except Exception:
                return None

        results = []
        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = {ex.submit(_score, s): s for s in AH_UNIVERSE}
            for f in as_completed(futs):
                r = f.result()
                # Keep: entry_ok, or has directional signal, or ADX > 28 (near threshold)
                if r and (r["entry_ok"] or r["signal"] != "watch" or r["adx"] > 28):
                    results.append(r)

        results.sort(key=lambda x: (not x["entry_ok"], -x["score"]))
        self.ah_results = results

        # Write trigger file so the bot enters ready setups immediately
        ready = [r for r in results if r.get("entry_ok")]
        if ready:
            open_syms = set()
            if PORT_STATE.exists():
                try:
                    open_syms = set(json.loads(PORT_STATE.read_text()).get("positions", {}).keys())
                except Exception:
                    pass
            entries = [{"symbol": r["symbol"], "side": r["signal"]}
                       for r in ready if r["symbol"] not in open_syms]
            if entries:
                Path("portfolio_trigger.json").write_text(
                    json.dumps({"entries": entries}))

        self._next_scan_at = time.time() + 300  # auto-rescan every 5 minutes
        self.root.after(0, self._ah_populate_table)

    def _ah_populate_table(self):
        now = datetime.now(ET)
        mins = now.hour * 60 + now.minute
        in_rth = (9 * 60 + 30) <= mins <= 16 * 60
        in_ext = (4 * 60) <= mins <= 20 * 60
        adx_thresh = 35 if in_rth or in_ext else 40

        ready_ct = sum(1 for r in self.ah_results if r.get("entry_ok"))
        watch_ct = sum(1 for r in self.ah_results if not r.get("entry_ok"))
        self.ah_status.config(
            text=f"Last scan: {now.strftime('%H:%M:%S')}  ·  "
                 f"{ready_ct} ready  ·  {watch_ct} watching",
            fg=C["fg2"])

        # Rebuild ready cards
        for w in self._ready_inner.winfo_children():
            w.destroy()
        ready = [r for r in self.ah_results if r.get("entry_ok")]
        if ready:
            for r in ready:
                self._build_ready_card(self._ready_inner, r, adx_thresh)
        else:
            Label(self._ready_inner, text="No setups pass entry criteria yet",
                  bg=C["bg"], fg=C["fg2"], font=("Courier", 12)).pack(pady=20)

        # Rebuild watch table
        for row in self.ah_watch_tree.get_children():
            self.ah_watch_tree.delete(row)
        for r in self.ah_results:
            if r.get("entry_ok"):
                continue
            sig = r["signal"]
            tag = "long_w" if sig == "long" else "short_w" if sig == "short" else "watch_w"
            dir_s = "▲ LONG" if sig == "long" else "▼ SHORT" if sig == "short" else "— FLAT"
            self.ah_watch_tree.insert("", END, tags=(tag,), values=(
                r["symbol"], dir_s,
                f"${r['close']:.4f}",
                f"{r['adx']:.1f}",
                r["regime"].upper(),
                r.get("block_reason", "—")))

    # ── Live clock + heartbeat (1 s tick) ────────────────────────────────────

    def _tick_clock(self):
        try:
            now = datetime.now(ET)
            ts  = now.strftime("%H:%M:%S ET")
            self.ah_clock.config(text=ts)
            self.rth_clock.config(text=ts)

            # Bot heartbeat
            if PORT_STATE.exists():
                age = time.time() - PORT_STATE.stat().st_mtime
                if age < 30:
                    hb_txt, hb_c = f"bot ♥ {int(age)}s ago", C["green"]
                elif age < 120:
                    hb_txt, hb_c = f"bot {int(age)}s ago", C["yellow"]
                else:
                    hb_txt, hb_c = f"bot {int(age//60)}m ago", C["red"]
                self.ah_heartbeat.config(text=hb_txt, fg=hb_c)
                self.rth_heartbeat.config(text=hb_txt, fg=hb_c)

            # Global tab — auto-scan countdown
            secs_left = max(0, int(self._next_scan_at - time.time()))
            if secs_left > 0:
                self.ah_next_scan.config(text=f"next scan {secs_left}s", fg=C["fg2"])
            elif self._next_scan_at > 0:
                self.ah_next_scan.config(text="scanning…", fg=C["yellow"])
                self._next_scan_at = 0.0
                threading.Thread(target=self._ah_scan_worker, daemon=True).start()

            # RTH tab — auto-scan countdown
            rth_left = max(0, int(self._rth_next_scan_at - time.time()))
            if rth_left > 0:
                self.rth_next_scan_lbl.config(text=f"next scan {rth_left}s", fg=C["fg2"])
            elif self._rth_next_scan_at > 0:
                self.rth_next_scan_lbl.config(text="scanning…", fg=C["yellow"])
                self._rth_next_scan_at = 0.0
                threading.Thread(target=self._rth_scan_worker, daemon=True).start()
        except Exception:
            pass
        self.root.after(1_000, self._tick_clock)

    # ── Live price fetcher (background thread, every 12 s) ───────────────────

    def _live_price_loop(self):
        while True:
            time.sleep(12)
            try:
                if not PORT_STATE.exists():
                    continue
                positions = json.loads(PORT_STATE.read_text()).get("positions", {})
                syms = [s for s in positions
                        if not s.endswith("=X") and not s.endswith("-USD")]
                if not syms:
                    continue
                def _fetch(sym):
                    try:
                        fi = yf.Ticker(sym).fast_info
                        p  = getattr(fi, "last_price", None) or getattr(fi, "regularMarketPrice", None)
                        return sym, float(p) if p else None
                    except Exception:
                        return sym, None
                with ThreadPoolExecutor(max_workers=8) as ex:
                    futs = {ex.submit(_fetch, s): s for s in syms}
                    for f in as_completed(futs):
                        sym, price = f.result()
                        if price:
                            self._live_prices[sym] = price
            except Exception:
                pass

    # ── US Regular Trading Hours tab ─────────────────────────────────────────

    def _build_rth(self, parent):
        self._rth_results  = []
        self._rth_log_pos  = 0
        self.rth_stats     = {}
        self._rth_next_scan_at = 0.0

        # ── Top ops bar (mirrors Global tab) ──────────────────────────────────
        top = Frame(parent, bg=C["bg2"])
        top.pack(fill=X, side=TOP)

        sess_f = Frame(top, bg=C["bg2"])
        sess_f.pack(side=LEFT, padx=16, pady=10)
        Label(sess_f, text="US REGULAR HOURS", bg=C["bg2"],
              fg=C["blue"], font=("Courier", 11)).pack(anchor=W)
        self.rth_session = Label(sess_f, text="● MARKET CLOSED", bg=C["bg2"],
                                  fg=C["red"], font=("Courier", 20, "bold"))
        self.rth_session.pack(anchor=W)
        self.rth_crit1 = Label(sess_f, text="", bg=C["bg2"],
                                fg=C["fg2"], font=("Courier", 9))
        self.rth_crit1.pack(anchor=W)

        Frame(top, bg=C["border"], width=2).pack(side=LEFT, fill=Y, pady=6)

        for key, color in [("Equity", C["fg"]), ("P&L", C["green"]),
                            ("Positions", C["blue"]), ("Ready", C["green"]),
                            ("Watching", C["yellow"])]:
            col = Frame(top, bg=C["bg2"])
            col.pack(side=LEFT, padx=22, pady=8)
            Label(col, text=key, bg=C["bg2"], fg=C["fg2"],
                  font=("Courier", 9)).pack(anchor=W)
            v = Label(col, text="—", bg=C["bg2"], fg=color,
                      font=("Courier", 22, "bold"))
            v.pack(anchor=W)
            self.rth_stats[key] = v

        # Alpaca equity
        alp_f = Frame(top, bg=C["bg2"])
        alp_f.pack(side=LEFT, padx=22, pady=8)
        Label(alp_f, text="Alpaca", bg=C["bg2"],
              fg=C["fg2"], font=("Courier", 9)).pack(anchor=W)
        self.rth_alpaca_lbl = Label(alp_f, text="—", bg=C["bg2"], fg=C["blue"],
                                     font=("Courier", 22, "bold"))
        self.rth_alpaca_lbl.pack(anchor=W)

        # Clock + heartbeat
        clk_f = Frame(top, bg=C["bg2"])
        clk_f.pack(side=RIGHT, padx=16, pady=8)
        self.rth_clock = Label(clk_f, text="00:00:00 ET", bg=C["bg2"],
                                fg=C["fg"], font=("Courier", 22, "bold"))
        self.rth_clock.pack(anchor=E)
        self.rth_heartbeat = Label(clk_f, text="bot: —", bg=C["bg2"],
                                    fg=C["fg2"], font=("Courier", 9))
        self.rth_heartbeat.pack(anchor=E, pady=(2, 0))
        self.rth_next_scan_lbl = Label(clk_f, text="", bg=C["bg2"],
                                        fg=C["fg2"], font=("Courier", 9))
        self.rth_next_scan_lbl.pack(anchor=E)

        btn_f = Frame(top, bg=C["bg2"])
        btn_f.pack(side=RIGHT, padx=8, pady=8)
        Button(btn_f, text="⟳  SCAN RTH", command=self._rth_scan_async,
               bg=C["blue"], fg="#fff", font=("Courier", 13, "bold"),
               relief=FLAT, padx=14, pady=8).pack(anchor=E)
        self.rth_scan_status = Label(btn_f, text="Not scanned yet", bg=C["bg2"],
                                      fg=C["fg2"], font=("Courier", 9))
        self.rth_scan_status.pack(anchor=E, pady=(4, 0))

        Frame(parent, bg=C["border"], height=2).pack(fill=X)

        # ── Main body: 3-column resizable PanedWindow ─────────────────────────
        h_pw = ttk.PanedWindow(parent, orient=HORIZONTAL)
        h_pw.pack(fill=BOTH, expand=True, padx=4, pady=6)

        # ── LEFT PANE: Open Positions (US) + Log ──────────────────────────────
        left = Frame(h_pw, bg=C["bg"])
        h_pw.add(left, weight=1)

        lv_pw = ttk.PanedWindow(left, orient=VERTICAL)
        lv_pw.pack(fill=BOTH, expand=True)

        pos_pane = Frame(lv_pw, bg=C["bg"])
        lv_pw.add(pos_pane, weight=2)
        Label(pos_pane, text="● OPEN POSITIONS  (US)", bg=C["bg"], fg=C["blue"],
              font=("Courier", 13, "bold")).pack(anchor=W, padx=8, pady=(6, 4))
        pos_outer = Frame(pos_pane, bg=C["bg"])
        pos_outer.pack(fill=BOTH, expand=True, padx=6, pady=(0, 4))
        pos_cv = Canvas(pos_outer, bg=C["bg"], highlightthickness=0)
        pos_sb = ttk.Scrollbar(pos_outer, orient=VERTICAL, command=pos_cv.yview)
        pos_cv.configure(yscrollcommand=pos_sb.set)
        pos_cv.pack(side=LEFT, fill=BOTH, expand=True)
        pos_sb.pack(side=RIGHT, fill=Y)
        self._rth_pos_inner = Frame(pos_cv, bg=C["bg"])
        self._rth_pos_win   = pos_cv.create_window((0, 0), window=self._rth_pos_inner, anchor="nw")
        self._rth_pos_inner.bind("<Configure>",
            lambda e: pos_cv.configure(scrollregion=pos_cv.bbox("all")))
        pos_cv.bind("<Configure>",
            lambda e: pos_cv.itemconfig(self._rth_pos_win, width=e.width))

        log_pane = Frame(lv_pw, bg=C["bg"])
        lv_pw.add(log_pane, weight=1)
        Label(log_pane, text="ACTIVITY LOG", bg=C["bg"], fg=C["fg2"],
              font=("Courier", 11, "bold")).pack(anchor=W, padx=8, pady=(6, 3))
        log_outer = Frame(log_pane, bg=C["bg2"])
        log_outer.pack(fill=BOTH, expand=True, padx=6, pady=(0, 6))
        self.rth_log = self._build_log_tree(log_outer)

        # ── CENTER PANE: Charts + Ready cards (vertical split) ────────────────
        center = Frame(h_pw, bg=C["bg"])
        h_pw.add(center, weight=1)

        cv_pw = ttk.PanedWindow(center, orient=VERTICAL)
        cv_pw.pack(fill=BOTH, expand=True)

        charts_pane = Frame(cv_pw, bg=C["bg"])
        cv_pw.add(charts_pane, weight=2)

        Label(charts_pane, text="PORTFOLIO EQUITY", bg=C["bg"],
              fg=C["fg2"], font=("Courier", 11, "bold")).pack(anchor=W, padx=6, pady=(6, 3))
        self.rth_eq_fig = Figure(figsize=(5, 2.2), facecolor=C["bg"])
        self.rth_eq_ax  = self.rth_eq_fig.add_subplot(111, facecolor=C["bg2"])
        self.rth_eq_fig.subplots_adjust(left=0.1, right=0.98, top=0.92, bottom=0.2)
        self.rth_eq_canvas = FigureCanvasTkAgg(self.rth_eq_fig, master=charts_pane)
        self.rth_eq_canvas.get_tk_widget().pack(fill=X, padx=4)

        Frame(charts_pane, bg=C["border"], height=1).pack(fill=X, padx=6, pady=(4, 3))
        Label(charts_pane, text="ALLOCATION  ·  P&L  (US)", bg=C["bg"],
              fg=C["fg2"], font=("Courier", 11, "bold")).pack(anchor=W, padx=6)
        self.rth_charts_fig = Figure(figsize=(5, 2.4), facecolor=C["bg"])
        self.rth_charts_fig.subplots_adjust(
            left=0.03, right=0.97, top=0.88, bottom=0.08, wspace=0.35)
        self.rth_alloc_ax = self.rth_charts_fig.add_subplot(121)
        self.rth_pnl_ax   = self.rth_charts_fig.add_subplot(122)
        self.rth_charts_canvas = FigureCanvasTkAgg(self.rth_charts_fig, master=charts_pane)
        self.rth_charts_canvas.get_tk_widget().pack(fill=X, padx=4)

        ready_pane = Frame(cv_pw, bg=C["bg"])
        cv_pw.add(ready_pane, weight=1)
        Label(ready_pane, text="✓ READY TO ENTER  (US)", bg=C["bg"], fg=C["green"],
              font=("Courier", 13, "bold")).pack(anchor=W, padx=8, pady=(6, 4))
        ready_outer = Frame(ready_pane, bg=C["bg"])
        ready_outer.pack(fill=BOTH, expand=True, padx=6, pady=(0, 6))
        ready_cv = Canvas(ready_outer, bg=C["bg"], highlightthickness=0)
        ready_sb = ttk.Scrollbar(ready_outer, orient=VERTICAL, command=ready_cv.yview)
        ready_cv.configure(yscrollcommand=ready_sb.set)
        ready_cv.pack(side=LEFT, fill=BOTH, expand=True)
        ready_sb.pack(side=RIGHT, fill=Y)
        self._rth_ready_inner = Frame(ready_cv, bg=C["bg"])
        self._rth_ready_win   = ready_cv.create_window((0,0), window=self._rth_ready_inner, anchor="nw")
        self._rth_ready_inner.bind("<Configure>",
            lambda e: ready_cv.configure(scrollregion=ready_cv.bbox("all")))
        ready_cv.bind("<Configure>",
            lambda e: ready_cv.itemconfig(self._rth_ready_win, width=e.width))

        # ── RIGHT PANE: On Watch ──────────────────────────────────────────────
        right = Frame(h_pw, bg=C["bg"])
        h_pw.add(right, weight=1)

        Label(right, text="○ ON WATCH  (US)", bg=C["bg"], fg=C["yellow"],
              font=("Courier", 13, "bold")).pack(anchor=W, padx=8, pady=(6, 2))
        self.rth_crit2 = Label(right, text="Longs only when SPY is in bull regime  ·  Shorts force-close at 15:50  ·  Longs evaluated for overnight hold at 15:30",
                                bg=C["bg"], fg=C["fg2"], font=("Courier", 9))
        self.rth_crit2.pack(anchor=W, padx=8, pady=(0, 4))

        wt_cols = ("Symbol", "Dir", "Price", "ADX", "Regime", "What's Blocking")
        wt_w    = {"Symbol": 90, "Dir": 60, "Price": 88, "ADX": 58,
                   "Regime": 80, "What's Blocking": 260}
        wt_f = Frame(right, bg=C["bg"])
        wt_f.pack(fill=BOTH, expand=True, padx=6, pady=(0, 6))
        self.rth_watch_tree = ttk.Treeview(wt_f, columns=wt_cols,
                                            show="headings", selectmode="browse")
        for c in wt_cols:
            self.rth_watch_tree.heading(c, text=c)
            self.rth_watch_tree.column(c, width=wt_w.get(c, 80), anchor=CENTER, stretch=False)
        wt_sb2 = ttk.Scrollbar(wt_f, orient=VERTICAL, command=self.rth_watch_tree.yview)
        self.rth_watch_tree.configure(yscrollcommand=wt_sb2.set)
        self.rth_watch_tree.pack(side=LEFT, fill=BOTH, expand=True)
        wt_sb2.pack(side=RIGHT, fill=Y)
        self.rth_watch_tree.tag_configure("long_w",  foreground=C["yellow"])
        self.rth_watch_tree.tag_configure("short_w", foreground=C["yellow"])
        self.rth_watch_tree.tag_configure("watch_w", foreground=C["fg2"])

        threading.Thread(target=self._rth_scan_worker, daemon=True).start()
        self._refresh_rth()

    def _rth_scan_async(self):
        self.rth_scan_status.config(text="Scanning US equities…", fg=C["yellow"])
        self.root.update_idletasks()
        threading.Thread(target=self._rth_scan_worker, daemon=True).start()

    def _rth_scan_worker(self):
        RTH_UNIVERSE = [
            # Mega-cap / S&P 100
            "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","JPM","V",
            "LLY","XOM","MA","JNJ","HD","PG","BAC","WMT","CVX","KO","MRK","COST",
            "AMD","CSCO","MCD","GE","CAT","TXN","NFLX","QCOM","GS","MS","SBUX",
            "SCHW","AMAT","GILD","SYK","BKNG","ISRG","VRTX","ACN","NOW","INTU",
            "ADBE","CRM","ORCL","IBM","INTC","MU","LRCX","KLAC","SNPS","CDNS",
            # High-beta / momentum
            "F","UBER","COIN","PLTR","SOFI","PYPL","MSTR","SMCI","RIVN","HOOD",
            "CRWD","SNOW","MELI","SHOP","SE","DKNG","RBLX","U","LYFT","LCID",
            "IONQ","JOBY","RDDT","ARM","DELL","HPE","CX","AFRM","UPST","OPEN",
            # US sector ETFs
            "QQQ","IWM","SPY","DIA","XLK","XLF","XLE","XLV","XLI","XLY",
            "XLP","XLU","XLRE","XLB","XLC","SOXL","TQQQ","LABU","FNGU",
        ]
        results = []
        def _score(sym):
            try:
                raw = yf.Ticker(sym).history(period="5d", interval="5m", auto_adjust=True)
                if raw.empty or len(raw) < 40:
                    return None
                raw.index = raw.index.tz_convert(ET)
                today_bars = raw[raw.index.date == date.today()]
                if len(today_bars) < 4:
                    return None
                df = _adx(raw.copy())
                df["_ema"] = df["Close"].ewm(span=25, adjust=False).mean()
                last  = df.iloc[-1]
                close = float(last["Close"])
                ema   = float(last["_ema"])
                atr   = float(last["_atr"])
                adx_v = float(last["_adx"])
                pdi_v = float(last["_pdi"])
                ndi_v = float(last["_ndi"])
                reg   = _regime(adx_v, pdi_v, ndi_v)
                open_p = float(today_bars.iloc[0]["Open"])
                chg    = (close - open_p) / open_p * 100 if open_p else 0
                vol_today = float(today_bars["Volume"].sum())
                prev = raw[raw.index.date < date.today()]
                prev_adv = (prev.groupby(prev.index.date)["Volume"].sum().mean()
                            if not prev.empty else vol_today)
                rv = vol_today / prev_adv if prev_adv > 0 else 1.0
                if reg in ("ranging", "bull") and close > ema:
                    sig = "LONG"
                elif reg == "bear" and close < ema:
                    sig = "SHORT"
                else:
                    sig = "WATCH"
                entry_ok = adx_v > 35 and reg in ("bull", "bear") and sig != "WATCH" and rv >= 1.2
                score = int(min(rv/3,1)*30 + min(abs(chg)/5,1)*30 + min(adx_v/50,1)*20 +
                            max(0, 20 - abs(chg)*2))
                stop   = round(close - 2*atr, 2) if sig == "LONG" else round(close + 2*atr, 2)
                target = round(close + 4*atr, 2) if sig == "LONG" else round(close - 4*atr, 2)
                block_reason = ("" if entry_ok else
                               f"ADX {adx_v:.0f} — need >35 ✗" if adx_v <= 35 else
                               "RANGING — need bull or bear ✗" if reg == "ranging" else
                               "price on wrong side of EMA ✗" if sig == "WATCH" else
                               f"volume {rv:.1f}× — need >1.2× ✗" if rv < 1.2 else
                               "conditions not met ✗")
                return dict(symbol=sym, signal=sig.lower(), score=score, close=close,
                            chg=chg, adx=adx_v, rv=rv, regime=reg,
                            entry_ok=entry_ok, stop=stop, target=target, atr=atr,
                            trail=(round(close-2*atr,4) if sig=="LONG" else
                                   round(close+2*atr,4) if sig=="SHORT" else None),
                            block_reason=block_reason, asset_class="us")
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=20) as ex:
            futs = {ex.submit(_score, s): s for s in RTH_UNIVERSE}
            for f in as_completed(futs):
                r = f.result()
                if r:
                    results.append(r)

        results.sort(key=lambda x: (not x["entry_ok"], -x["score"]))
        self._rth_results = results
        self.root.after(0, self._rth_populate)

    def _rth_populate(self):
        now = datetime.now(ET)
        ready_ct = sum(1 for r in self._rth_results if r.get("entry_ok"))
        watch_ct = sum(1 for r in self._rth_results if not r.get("entry_ok"))
        self.rth_scan_status.config(
            text=f"Last scan: {now.strftime('%H:%M:%S')}  ·  {ready_ct} ready  ·  {watch_ct} watching",
            fg=C["fg2"])

        # Rebuild ready cards
        for w in self._rth_ready_inner.winfo_children():
            w.destroy()
        ready = [r for r in self._rth_results if r.get("entry_ok")]
        if ready:
            for r in ready:
                self._build_ready_card(self._rth_ready_inner, r, 35)
        else:
            Label(self._rth_ready_inner, text="No US setups pass entry criteria yet",
                  bg=C["bg"], fg=C["fg2"], font=("Courier", 12)).pack(pady=20)

        # Rebuild watch table
        for row in self.rth_watch_tree.get_children():
            self.rth_watch_tree.delete(row)
        for r in self._rth_results:
            if r.get("entry_ok"):
                continue
            sig = r["signal"].lower() if isinstance(r["signal"], str) else r["signal"]
            tag = "long_w" if sig == "long" else "short_w" if sig == "short" else "watch_w"
            dir_s = "▲ LONG" if sig == "long" else "▼ SHORT" if sig == "short" else "— FLAT"
            self.rth_watch_tree.insert("", END, tags=(tag,), values=(
                r["symbol"], dir_s, f"${r['close']:.2f}",
                f"{r['adx']:.1f}", r["regime"].upper(),
                r.get("block_reason", "—")))

        self._rth_next_scan_at = time.time() + 300

    def _refresh_rth(self):
        try:
            self._update_rth()
        except Exception:
            pass
        self.root.after(3_000, self._refresh_rth)

    def _update_rth(self):
        now  = datetime.now(ET)
        mins = now.hour * 60 + now.minute
        in_rth = (9 * 60 + 30) <= mins <= (16 * 60)

        sess   = ("● MARKET OPEN  09:30–16:00 ET" if in_rth
                  else "● PRE-MARKET" if mins < 9*60+30
                  else "● AFTER-HOURS" if mins <= 20*60
                  else "● MARKET CLOSED")
        sess_c = C["green"] if in_rth else C["yellow"] if 4*60 <= mins <= 20*60 else C["red"]
        self.rth_session.config(text=sess, fg=sess_c)
        self.rth_crit1.config(
            text="Entry: ADX>35 + bull/bear regime + price × EMA + vol≥1.2×  ·  Exit: trail 3×ATR long / 1×ATR short  ·  No shorts when SPY > 50d EMA")

        port_data = {}
        if PORT_STATE.exists():
            try:
                port_data = json.loads(PORT_STATE.read_text())
            except Exception:
                pass

        # US instruments only
        open_positions = {s: p for s, p in port_data.get("positions", {}).items()
                          if not self._is_global(s)}
        hist      = self._alpaca_equity_hist
        alp_now   = hist[-1][1] if hist else 0.0
        alp_start = hist[0][1]  if hist else 0.0
        equity    = port_data.get("equity") or alp_now or 0   # local is live; Alpaca is lagged
        baseline  = alp_start if alp_start > 0 else 50_000.0
        pnl       = equity - baseline
        pnl_pct   = pnl / baseline * 100 if baseline > 0 else 0.0
        pnl_c     = C["green"] if pnl >= 0 else C["red"]

        ready = [r for r in self._rth_results if r.get("entry_ok")]
        watch = [r for r in self._rth_results if not r.get("entry_ok")]

        self.rth_stats["Equity"].config(text=f"${equity:,.0f}" if equity else "—")
        self.rth_stats["P&L"].config(
            text=f"{'+' if pnl>=0 else ''}{pnl_pct:.2f}%" if alp_start else "—", fg=pnl_c)
        self.rth_stats["Positions"].config(
            text=str(len(open_positions)), fg=C["blue"] if open_positions else C["fg2"])
        self.rth_stats["Ready"].config(
            text=str(len(ready)), fg=C["green"] if ready else C["fg2"])
        self.rth_stats["Watching"].config(
            text=str(len(watch)), fg=C["yellow"] if watch else C["fg2"])

        alp_live = self._alpaca_equity_hist[-1][1] if self._alpaca_equity_hist else 0
        if alp_live > 0:
            self.rth_alpaca_lbl.config(text=f"${alp_live:,.0f}", fg=C["blue"])
        else:
            self.rth_alpaca_lbl.config(text="fetching…", fg=C["fg2"])

        # Rebuild position cards (US only)
        for w in self._rth_pos_inner.winfo_children():
            w.destroy()
        if open_positions:
            for sym, pos in open_positions.items():
                live = self._live_prices.get(sym)
                if live:
                    pos = dict(pos, last_price=live)
                self._build_pos_card(self._rth_pos_inner, sym, pos)
        else:
            Label(self._rth_pos_inner,
                  text="No US positions open",
                  bg=C["bg"], fg=C["fg2"], font=("Courier", 12)).pack(pady=20)

        # Equity curve (shared — shows full portfolio)
        self._draw_equity_chart_to(self.rth_eq_ax, self.rth_eq_canvas)

        # Allocation + P&L charts (US only)
        self._draw_portfolio_charts_to(
            self.rth_alloc_ax, self.rth_pnl_ax, self.rth_charts_canvas,
            sym_filter=lambda s: not self._is_global(s))

        # Stream new log lines into columnized treeview
        if PORT_LOG.exists():
            try:
                text = PORT_LOG.read_text()
                new  = text[self._rth_log_pos:]
                if new:
                    self._rth_log_pos = len(text)
                    for line in new.splitlines():
                        if line.strip():
                            self._log_append(self.rth_log, line)
            except Exception:
                pass

    # ── Trader tab ────────────────────────────────────────────────────────────

    def _build_trader(self, parent):

        def lbl(p, text, color=None, size=10, bold=False):
            fnt = ("Courier", size, "bold") if bold else ("Courier", size)
            return Label(p, text=text, bg=C["bg2"],
                         fg=color or C["fg"], font=fnt)

        # ── Header ────────────────────────────────────────────────────────────
        hdr = Frame(parent, bg=C["bg"])
        hdr.pack(fill=X, padx=10, pady=(8, 4))

        Label(hdr, text="SPY LIVE TRADER", bg=C["bg"], fg=C["blue"],
              font=("Courier", 13, "bold")).pack(side=LEFT)
        self.trader_status = Label(hdr, text="● STOPPED", bg=C["bg"],
                                    fg=C["red"], font=("Courier", 10))
        self.trader_status.pack(side=LEFT, padx=12)

        btn = Frame(hdr, bg=C["bg"])
        btn.pack(side=RIGHT)
        Button(btn, text="▶  START", command=self.start_trader,
               bg=C["green"], fg="#000", font=("Courier", 9, "bold"),
               relief=FLAT, padx=8, pady=3).pack(side=LEFT, padx=3)
        Button(btn, text="■  STOP", command=self.stop_trader,
               bg=C["red"], fg="#fff", font=("Courier", 9, "bold"),
               relief=FLAT, padx=8, pady=3).pack(side=LEFT, padx=3)

        Frame(parent, bg=C["border"], height=1).pack(fill=X, padx=10, pady=3)

        # ── Body ──────────────────────────────────────────────────────────────
        body = Frame(parent, bg=C["bg"])
        body.pack(fill=BOTH, expand=True, padx=10, pady=4)

        # Left column: position + session
        left = Frame(body, bg=C["bg2"])
        left.pack(side=LEFT, fill=Y, padx=(0, 5), ipadx=14, ipady=12)

        Label(left, text="POSITION", bg=C["bg2"], fg=C["fg2"],
              font=("Courier", 8)).pack(anchor=W, padx=10, pady=(10, 0))
        self.pos_big = Label(left, text="FLAT", bg=C["bg2"], fg=C["fg2"],
                              font=("Courier", 26, "bold"))
        self.pos_big.pack(anchor=W, padx=10, pady=(2, 8))

        self.pos_rows = {}
        for key in ("Entry", "Shares", "Trail Stop", "Unrealized"):
            row = Frame(left, bg=C["bg2"])
            row.pack(fill=X, padx=10, pady=1)
            Label(row, text=f"{key}:", bg=C["bg2"], fg=C["fg2"],
                  font=("Courier", 9), width=12, anchor=W).pack(side=LEFT)
            v = Label(row, text="—", bg=C["bg2"], fg=C["fg"], font=("Courier", 9))
            v.pack(side=LEFT)
            self.pos_rows[key] = v

        Frame(left, bg=C["border"], height=1).pack(fill=X, padx=10, pady=8)

        Label(left, text="SESSION", bg=C["bg2"], fg=C["fg2"],
              font=("Courier", 8)).pack(anchor=W, padx=10)
        self.sess_rows = {}
        for key in ("Equity", "P&L", "Trades"):
            row = Frame(left, bg=C["bg2"])
            row.pack(fill=X, padx=10, pady=1)
            Label(row, text=f"{key}:", bg=C["bg2"], fg=C["fg2"],
                  font=("Courier", 9), width=12, anchor=W).pack(side=LEFT)
            v = Label(row, text="—", bg=C["bg2"], fg=C["fg"], font=("Courier", 9))
            v.pack(side=LEFT)
            self.sess_rows[key] = v

        # Right column: regime + log
        right = Frame(body, bg=C["bg"])
        right.pack(side=LEFT, fill=BOTH, expand=True)

        reg_box = Frame(right, bg=C["bg2"])
        reg_box.pack(fill=X, pady=(0, 5), ipadx=14, ipady=10)

        Label(reg_box, text="REGIME & INDICATORS", bg=C["bg2"], fg=C["fg2"],
              font=("Courier", 8)).pack(anchor=W, padx=10, pady=(8, 0))
        self.regime_big = Label(reg_box, text="—", bg=C["bg2"], fg=C["fg"],
                                 font=("Courier", 20, "bold"))
        self.regime_big.pack(anchor=W, padx=10, pady=(2, 6))

        self.ind_rows = {}
        ind_row = Frame(reg_box, bg=C["bg2"])
        ind_row.pack(fill=X, padx=10, pady=(0, 8))
        for key in ("Last Price", "EMA(25)", "ADX(20)"):
            col = Frame(ind_row, bg=C["bg2"])
            col.pack(side=LEFT, padx=(0, 24))
            Label(col, text=key, bg=C["bg2"], fg=C["fg2"], font=("Courier", 8)).pack(anchor=W)
            v = Label(col, text="—", bg=C["bg2"], fg=C["fg"], font=("Courier", 12, "bold"))
            v.pack(anchor=W)
            self.ind_rows[key] = v

        # Log box
        log_box = Frame(right, bg=C["bg2"])
        log_box.pack(fill=BOTH, expand=True, ipadx=10, ipady=8)

        Label(log_box, text="ACTIVITY LOG", bg=C["bg2"], fg=C["fg2"],
              font=("Courier", 8)).pack(anchor=W, padx=10, pady=(8, 4))
        self.log_txt = Text(log_box, bg=C["bg"], fg=C["fg"],
                             font=("Courier", 9), height=12, relief=FLAT,
                             wrap=NONE, state=DISABLED, insertbackground=C["fg"])
        self.log_txt.pack(fill=BOTH, expand=True, padx=10, pady=(0, 8))
        self.log_txt.tag_config("entry", foreground=C["green"])
        self.log_txt.tag_config("exit",  foreground=C["yellow"])
        self.log_txt.tag_config("warn",  foreground=C["red"])
        self.log_txt.tag_config("dim",   foreground=C["fg2"])

    # ── Scanner tab ───────────────────────────────────────────────────────────

    def _build_scanner(self, parent):

        # Header
        hdr = Frame(parent, bg=C["bg"])
        hdr.pack(fill=X, padx=10, pady=(8, 4))
        Label(hdr, text="MARKET SCANNER", bg=C["bg"], fg=C["purple"],
              font=("Courier", 13, "bold")).pack(side=LEFT)
        self.scan_status = Label(hdr, text="Not scanned yet", bg=C["bg"],
                                  fg=C["fg2"], font=("Courier", 9))
        self.scan_status.pack(side=LEFT, padx=12)
        Button(hdr, text="⟳  RUN SCAN", command=self._scan_async,
               bg=C["purple"], fg="#fff", font=("Courier", 9, "bold"),
               relief=FLAT, padx=8, pady=3).pack(side=RIGHT)

        Frame(parent, bg=C["border"], height=1).pack(fill=X, padx=10, pady=3)

        # Results table
        cols = ("Rank", "Symbol", "Price", "Chg%", "RelVol", "ADX", "Regime", "Signal", "Score")
        tframe = Frame(parent, bg=C["bg"])
        tframe.pack(fill=BOTH, expand=True, padx=10, pady=(4, 0))

        self.scan_tree = ttk.Treeview(tframe, columns=cols, show="headings",
                                       height=14, selectmode="browse")
        col_w = dict(Rank=42, Symbol=75, Price=85, **{"Chg%":70}, RelVol=72,
                     ADX=55, Regime=90, Signal=72, Score=58)
        for c in cols:
            self.scan_tree.heading(c, text=c)
            self.scan_tree.column(c, width=col_w.get(c, 80), anchor=CENTER, stretch=False)
        self.scan_tree.pack(side=LEFT, fill=BOTH, expand=True)

        sb = ttk.Scrollbar(tframe, orient=VERTICAL, command=self.scan_tree.yview)
        sb.pack(side=RIGHT, fill=Y)
        self.scan_tree.configure(yscrollcommand=sb.set)
        self.scan_tree.bind("<<TreeviewSelect>>", self._on_select)

        # Detail strip
        det = Frame(parent, bg=C["bg2"])
        det.pack(fill=X, padx=10, pady=(4, 8))
        Label(det, text="SETUP DETAIL", bg=C["bg2"], fg=C["fg2"],
              font=("Courier", 8)).pack(anchor=W, padx=12, pady=(6, 0))
        drow = Frame(det, bg=C["bg2"])
        drow.pack(fill=X, padx=12, pady=(2, 8))
        self.det = {}
        for key in ("Symbol", "Signal", "Entry", "Stop", "Target", "R:R", "ATR", "VWAP"):
            col = Frame(drow, bg=C["bg2"])
            col.pack(side=LEFT, padx=14)
            Label(col, text=key, bg=C["bg2"], fg=C["fg2"], font=("Courier", 8)).pack(anchor=W)
            v = Label(col, text="—", bg=C["bg2"], fg=C["fg"], font=("Courier", 12, "bold"))
            v.pack(anchor=W)
            self.det[key] = v

    # ── Portfolio tab ─────────────────────────────────────────────────────────

    def _build_portfolio(self, parent):
        # Header
        hdr = Frame(parent, bg=C["bg"])
        hdr.pack(fill=X, padx=10, pady=(8, 4))
        Label(hdr, text="MOCK PORTFOLIO  $100k", bg=C["bg"], fg=C["yellow"],
              font=("Courier", 13, "bold")).pack(side=LEFT)
        self.port_status = Label(hdr, text="● STOPPED", bg=C["bg"],
                                  fg=C["red"], font=("Courier", 10))
        self.port_status.pack(side=LEFT, padx=12)
        btn = Frame(hdr, bg=C["bg"])
        btn.pack(side=RIGHT)
        Button(btn, text="▶  START", command=self.start_portfolio,
               bg=C["green"], fg="#000", font=("Courier", 9, "bold"),
               relief=FLAT, padx=8, pady=3).pack(side=LEFT, padx=3)
        Button(btn, text="■  STOP", command=self.stop_portfolio,
               bg=C["red"], fg="#fff", font=("Courier", 9, "bold"),
               relief=FLAT, padx=8, pady=3).pack(side=LEFT, padx=3)

        Frame(parent, bg=C["border"], height=1).pack(fill=X, padx=10, pady=3)

        # Summary strip
        summ = Frame(parent, bg=C["bg2"])
        summ.pack(fill=X, padx=10, pady=(0, 5), ipadx=10, ipady=8)
        self.port_stats = {}
        srow = Frame(summ, bg=C["bg2"])
        srow.pack(fill=X, padx=12, pady=(4, 6))
        for key in ("Equity", "Cash", "Total P&L", "Open", "Trades"):
            col = Frame(srow, bg=C["bg2"])
            col.pack(side=LEFT, padx=18)
            Label(col, text=key, bg=C["bg2"], fg=C["fg2"], font=("Courier", 8)).pack(anchor=W)
            v = Label(col, text="—", bg=C["bg2"], fg=C["fg"], font=("Courier", 13, "bold"))
            v.pack(anchor=W)
            self.port_stats[key] = v

        # Body: open positions (left) + closed trades (right)
        body = Frame(parent, bg=C["bg"])
        body.pack(fill=BOTH, expand=True, padx=10, pady=(0, 4))

        # Open positions
        left = Frame(body, bg=C["bg"])
        left.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 4))
        Label(left, text="OPEN POSITIONS", bg=C["bg"], fg=C["fg2"],
              font=("Courier", 8)).pack(anchor=W, pady=(2, 2))
        pos_cols = ("Sym", "Side", "Shares", "Entry", "Last", "P&L", "Stop", "Target", "Strat")
        pf2 = Frame(left, bg=C["bg"])
        pf2.pack(fill=BOTH, expand=True)
        self.pos_tree = ttk.Treeview(pf2, columns=pos_cols, show="headings",
                                      height=7, selectmode="none")
        pw = dict(Sym=58, Side=52, Shares=60, Entry=80, Last=80,
                  **{"P&L": 80}, Stop=80, Target=80, Strat=80)
        for c in pos_cols:
            self.pos_tree.heading(c, text=c)
            self.pos_tree.column(c, width=pw.get(c, 72), anchor=CENTER, stretch=False)
        sb_p = ttk.Scrollbar(pf2, orient=VERTICAL, command=self.pos_tree.yview)
        self.pos_tree.configure(yscrollcommand=sb_p.set)
        self.pos_tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb_p.pack(side=RIGHT, fill=Y)

        # Closed trades
        right = Frame(body, bg=C["bg"])
        right.pack(side=LEFT, fill=BOTH, expand=True)
        Label(right, text="RECENT CLOSED TRADES", bg=C["bg"], fg=C["fg2"],
              font=("Courier", 8)).pack(anchor=W, pady=(2, 2))
        cl_cols = ("Sym", "Side", "Entry", "Exit", "P&L", "Reason", "Strat")
        cf2 = Frame(right, bg=C["bg"])
        cf2.pack(fill=BOTH, expand=True)
        self.cl_tree = ttk.Treeview(cf2, columns=cl_cols, show="headings",
                                     height=7, selectmode="none")
        cw = dict(Sym=58, Side=52, Entry=75, Exit=75, **{"P&L": 80}, Reason=80, Strat=80)
        for c in cl_cols:
            self.cl_tree.heading(c, text=c)
            self.cl_tree.column(c, width=cw.get(c, 72), anchor=CENTER, stretch=False)
        sb_c = ttk.Scrollbar(cf2, orient=VERTICAL, command=self.cl_tree.yview)
        self.cl_tree.configure(yscrollcommand=sb_c.set)
        self.cl_tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb_c.pack(side=RIGHT, fill=Y)
        self.cl_tree.tag_configure("win",  foreground=C["green"])
        self.cl_tree.tag_configure("loss", foreground=C["red"])

        # Portfolio log
        log_box = Frame(parent, bg=C["bg2"])
        log_box.pack(fill=X, padx=10, pady=(0, 8), ipady=6)
        Label(log_box, text="PORTFOLIO LOG", bg=C["bg2"], fg=C["fg2"],
              font=("Courier", 8)).pack(anchor=W, padx=10, pady=(6, 2))
        self.port_log = Text(log_box, bg=C["bg"], fg=C["fg"],
                              font=("Courier", 9), height=7, relief=FLAT,
                              wrap=NONE, state=DISABLED, insertbackground=C["fg"])
        self.port_log.pack(fill=X, padx=10, pady=(0, 6))
        self.port_log.tag_config("entry",  foreground=C["green"])
        self.port_log.tag_config("exit",   foreground=C["yellow"])
        self.port_log.tag_config("warn",   foreground=C["red"])
        self.port_log.tag_config("scan",   foreground=C["blue"])
        self.port_log.tag_config("dim",    foreground=C["fg2"])

    # ── Trader controls ───────────────────────────────────────────────────────

    def start_trader(self):
        if self.trader_proc and self.trader_proc.poll() is None:
            return
        self.trader_proc = subprocess.Popen(
            [TRADER_PY3, str(TRADER_PY), "--paper", "--port", "4001", "--client", "20"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.trader_status.config(text="● RUNNING", fg=C["green"])

    def stop_trader(self):
        if self.trader_proc:
            self.trader_proc.terminate()
            self.trader_proc = None
        self.trader_status.config(text="● STOPPED", fg=C["red"])

    def start_portfolio(self):
        if self.portfolio_proc and self.portfolio_proc.poll() is None:
            return
        self.portfolio_proc = subprocess.Popen(
            [TRADER_PY3, str(PORT_PY)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.port_status.config(text="● RUNNING", fg=C["green"])

    def stop_portfolio(self):
        if self.portfolio_proc:
            self.portfolio_proc.terminate()
            self.portfolio_proc = None
        self.port_status.config(text="● STOPPED", fg=C["red"])

    # ── Trader refresh (every 10 s) ───────────────────────────────────────────

    def _refresh_trader(self):
        try:
            self._update_trader()
        except Exception:
            pass
        self.root.after(10_000, self._refresh_trader)

    def _update_trader(self):
        running = self.trader_proc and self.trader_proc.poll() is None
        self.trader_status.config(
            text="● RUNNING" if running else "● STOPPED",
            fg=C["green"] if running else C["red"],
        )

        if STATE_FILE.exists():
            s   = json.loads(STATE_FILE.read_text())
            pos = s.get("position")
            eq  = s.get("equity", 100_000)
            pnl = s.get("total_pnl", 0)
            n   = s.get("n_trades", 0)

            if pos:
                side  = pos["side"].upper()
                color = C["green"] if side == "LONG" else C["red"]
                self.pos_big.config(text=side, fg=color)
                self.pos_rows["Entry"].config(text=f"${pos['entry_price']:.2f}")
                self.pos_rows["Shares"].config(text=str(pos["shares"]))
                self.pos_rows["Trail Stop"].config(
                    text=f"${pos['trail_stop']:.2f}", fg=C["yellow"])
                self.pos_rows["Unrealized"].config(text="live")
            else:
                self.pos_big.config(text="FLAT", fg=C["fg2"])
                for v in self.pos_rows.values():
                    v.config(text="—", fg=C["fg"])

            self.sess_rows["Equity"].config(text=f"${eq:,.0f}")
            self.sess_rows["P&L"].config(
                text=f"${pnl:>+,.0f}",
                fg=C["green"] if pnl >= 0 else C["red"])
            self.sess_rows["Trades"].config(text=str(n))

        if LOG_FILE.exists():
            lines = LOG_FILE.read_text().splitlines()[-22:]
            self.log_txt.config(state=NORMAL)
            self.log_txt.delete("1.0", END)
            for line in lines:
                if   "ENTRY" in line: tag = "entry"
                elif "EXIT"  in line: tag = "exit"
                elif "WARNING" in line or "mismatch" in line: tag = "warn"
                elif "watching" in line or "→" in line:       tag = "dim"
                else:                 tag = None
                self.log_txt.insert(END, line + "\n", tag or "")
            self.log_txt.see(END)
            self.log_txt.config(state=DISABLED)

    # ── Portfolio refresh (every 5 s) ────────────────────────────────────────

    def _refresh_portfolio(self):
        try:
            self._update_portfolio()
        except Exception:
            pass
        self.root.after(5_000, self._refresh_portfolio)

    def _update_portfolio(self):
        running = self.portfolio_proc and self.portfolio_proc.poll() is None
        self.port_status.config(
            text="● RUNNING" if running else "● STOPPED",
            fg=C["green"] if running else C["red"],
        )

        if PORT_STATE.exists():
            try:
                s       = json.loads(PORT_STATE.read_text())
                eq      = s.get("equity", 100_000)
                cash    = s.get("cash",   100_000)
                pnl     = s.get("total_pnl", 0.0)
                n       = s.get("n_trades", 0)
                positions = s.get("positions", {})
                closed  = s.get("closed", [])

                self.port_stats["Equity"].config(text=f"${eq:,.0f}")
                self.port_stats["Cash"].config(text=f"${cash:,.0f}")
                pnl_c = C["green"] if pnl >= 0 else C["red"]
                self.port_stats["Total P&L"].config(text=f"${pnl:>+,.0f}", fg=pnl_c)
                self.port_stats["Open"].config(text=str(len(positions)))
                self.port_stats["Trades"].config(text=str(n))

                # Open positions table
                for r in self.pos_tree.get_children():
                    self.pos_tree.delete(r)
                for sym, pos in positions.items():
                    side  = pos["side"].upper()
                    unrl  = pos.get("unrealized", 0.0)
                    last  = pos.get("last_price", pos["entry_price"])
                    tag   = "LONG" if side == "LONG" else "SHORT"
                    self.pos_tree.insert("", END, tags=(tag,), values=(
                        sym, side, pos["shares"],
                        f"${pos['entry_price']:.2f}",
                        f"${last:.2f}",
                        f"${unrl:>+,.0f}",
                        f"${pos['trail_stop']:.2f}",
                        f"${pos['target']:.2f}",
                        pos.get("strategy", "—"),
                    ))
                self.pos_tree.tag_configure("LONG",  foreground=C["green"])
                self.pos_tree.tag_configure("SHORT", foreground=C["red"])

                # Closed trades (most recent 30)
                for r in self.cl_tree.get_children():
                    self.cl_tree.delete(r)
                for t in reversed(closed[-30:]):
                    pnl_t = t.get("pnl", 0.0)
                    tag   = "win" if pnl_t >= 0 else "loss"
                    self.cl_tree.insert("", END, tags=(tag,), values=(
                        t["symbol"], t["side"].upper(),
                        f"${t['entry']:.2f}",
                        f"${t['exit']:.2f}",
                        f"${pnl_t:>+,.0f}",
                        t.get("reason", "—"),
                        t.get("strategy", "—"),
                    ))
            except Exception:
                pass

        # Portfolio log — append only new lines
        if PORT_LOG.exists():
            try:
                text = PORT_LOG.read_text()
                new  = text[self._port_log_pos:]
                if new:
                    self._port_log_pos = len(text)
                    self.port_log.config(state=NORMAL)
                    for line in new.splitlines():
                        if   "ENTRY" in line:  tag = "entry"
                        elif "EXIT"  in line:  tag = "exit"
                        elif "Scan"  in line or "scan" in line: tag = "scan"
                        elif "ERROR" in line or "ERROR" in line: tag = "warn"
                        elif "→"     in line:  tag = "dim"
                        else:                  tag = ""
                        self.port_log.insert(END, line + "\n", tag)
                    self.port_log.see(END)
                    self.port_log.config(state=DISABLED)
            except Exception:
                pass

    # ── Allocations / Sankey tab ──────────────────────────────────────────────

    def _build_allocations(self, parent):
        from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk

        hdr = Frame(parent, bg=C["bg"])
        hdr.pack(fill=X, padx=10, pady=(8, 4))
        Label(hdr, text="LIVE ALLOCATIONS  —  SANKEY", bg=C["bg"], fg=C["blue"],
              font=("Courier", 13, "bold")).pack(side=LEFT)
        self.sank_ts = Label(hdr, text="", bg=C["bg"], fg=C["fg2"],
                              font=("Courier", 9))
        self.sank_ts.pack(side=LEFT, padx=12)
        Frame(parent, bg=C["border"], height=1).pack(fill=X, padx=10, pady=3)

        # ── Vertical split: live Sankey (top) | trade history (bottom) ──────
        pw = ttk.PanedWindow(parent, orient=VERTICAL)
        pw.pack(fill=BOTH, expand=True, padx=10, pady=(4, 6))

        # ── TOP pane: live allocations ────────────────────────────────────
        top_frame = Frame(pw, bg=C["bg"])
        pw.add(top_frame, weight=1)

        self.sank_fig = Figure(figsize=(13, 5.5), dpi=96, facecolor=C["bg"])
        self.sank_fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.sank_ax  = self.sank_fig.add_axes([0, 0, 1, 1])
        self.sank_ax.set_facecolor(C["bg"])
        self.sank_canvas = FigureCanvasTkAgg(self.sank_fig, master=top_frame)
        self.sank_canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        tb_live = Frame(top_frame, bg="#161b22")
        tb_live.pack(fill=X)
        toolbar_live = NavigationToolbar2Tk(self.sank_canvas, tb_live)
        toolbar_live.config(background="#161b22")
        for w in toolbar_live.winfo_children():
            try: w.config(background="#161b22")
            except Exception: pass
        toolbar_live.update()

        # ── BOTTOM pane: trade history ────────────────────────────────────
        bot_frame = Frame(pw, bg=C["bg"])
        pw.add(bot_frame, weight=1)

        hist_hdr = Frame(bot_frame, bg=C["bg"])
        hist_hdr.pack(fill=X, pady=(4, 2))
        Label(hist_hdr, text="TRADE HISTORY  —  ALL CLOSED POSITIONS",
              bg=C["bg"], fg=C["yellow"], font=("Courier", 10, "bold")).pack(side=LEFT)
        self.hist_ts = Label(hist_hdr, text="", bg=C["bg"], fg=C["fg2"],
                             font=("Courier", 9))
        self.hist_ts.pack(side=LEFT, padx=10)
        Frame(bot_frame, bg=C["border"], height=1).pack(fill=X)

        self.hist_fig = Figure(figsize=(13, 5), dpi=96, facecolor=C["bg"])
        self.hist_fig.subplots_adjust(left=0.01, right=0.99, top=0.92, bottom=0.12)
        self.hist_ax  = self.hist_fig.add_axes([0.01, 0.05, 0.98, 0.88])
        self.hist_ax.set_facecolor(C["bg"])
        self.hist_canvas = FigureCanvasTkAgg(self.hist_fig, master=bot_frame)
        self.hist_canvas.get_tk_widget().pack(fill=BOTH, expand=True)
        tb_hist = Frame(bot_frame, bg="#161b22")
        tb_hist.pack(fill=X)
        toolbar_hist = NavigationToolbar2Tk(self.hist_canvas, tb_hist)
        toolbar_hist.config(background="#161b22")
        for w in toolbar_hist.winfo_children():
            try: w.config(background="#161b22")
            except Exception: pass
        toolbar_hist.update()

    def _refresh_sankey(self):
        try:
            self._draw_sankey()
        except Exception:
            pass
        try:
            self._draw_history_sankey()
        except Exception:
            pass
        self.root.after(5_000, self._refresh_sankey)

    # ── Sankey drawing helpers ────────────────────────────────────────────────

    @staticmethod
    def _flow(ax, x0, y0, x1, y1, h, color, alpha=0.6):
        """Filled S-curve band.  y0/y1 = centre of flow at each end."""
        hw = h / 2
        mx = x0 + (x1 - x0) * 0.5
        verts = [
            (x0, y0 + hw),
            (mx, y0 + hw), (mx, y1 + hw), (x1, y1 + hw),
            (x1, y1 - hw),
            (mx, y1 - hw), (mx, y0 - hw), (x0, y0 - hw),
            (x0, y0 + hw),
        ]
        codes = [
            MPath.MOVETO,
            MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
            MPath.LINETO,
            MPath.CURVE4, MPath.CURVE4, MPath.CURVE4,
            MPath.CLOSEPOLY,
        ]
        ax.add_patch(mpatches.PathPatch(
            MPath(verts, codes),
            facecolor=color, edgecolor="none", alpha=alpha, zorder=2))

    @staticmethod
    def _node(ax, x, y, w, h, face, edge="#ffffff22", rounding=0.008):
        """Rounded-corner filled node rectangle. Returns (cx, cy)."""
        ax.add_patch(mpatches.FancyBboxPatch(
            (x, y), w, h,
            boxstyle=f"round,pad={rounding}",
            facecolor=face, edgecolor=edge,
            linewidth=0.6, zorder=3,
            clip_on=False))
        return x + w / 2, y + h / 2

    @staticmethod
    def _txt(ax, x, y, text, size=8.5, color="white", bold=False,
             ha="center", va="center", alpha=1.0):
        ax.text(x, y, text, ha=ha, va=va, fontsize=size, color=color,
                fontweight="bold" if bold else "normal",
                alpha=alpha, zorder=6, clip_on=False)

    def _draw_sankey(self):
        ax = self.sank_ax
        ax.clear()
        ax.set_facecolor(C["bg"])
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")
        self.sank_fig.patch.set_facecolor(C["bg"])

        # ── Load state ────────────────────────────────────────────────────
        if not PORT_STATE.exists():
            self._txt(ax, 0.5, 0.5, "No portfolio state yet",
                      size=13, color=C["fg2"])
            self.sank_canvas.draw_idle()
            return
        try:
            s = json.loads(PORT_STATE.read_text())
        except Exception:
            self.sank_canvas.draw_idle()
            return

        # Use Alpaca live equity as the source of truth
        hist      = self._alpaca_equity_hist
        alp_now   = hist[-1][1] if hist else 0.0
        alp_start = hist[0][1]  if hist else 0.0
        equity    = max(alp_now if alp_now > 0 else s.get("equity", s.get("cash", 1)), 1)
        cash      = max(s.get("cash", 0), 0)
        positions = s.get("positions", {})
        total_pnl = (alp_now - alp_start) if alp_start > 0 else s.get("total_pnl", 0.0)

        # ── Build items (positions + cash), sorted by allocation ──────────
        items = []
        for sym, pos in positions.items():
            last  = pos.get("last_price", pos["entry_price"])
            atr   = max(pos.get("last_atr", pos.get("atr", last * 0.01)), 1e-6)
            side  = pos["side"]
            # Target: 2×ATR forward move from current price (trend-continuation estimate)
            target = round(last + 2 * atr, 4) if side == "long" else round(last - 2 * atr, 4)
            mval   = pos["shares"] * last
            alloc  = mval / equity
            unrl   = pos.get("unrealized", 0.0)
            if side == "long":
                upside   = max((target           - last) * pos["shares"], 0)
                downside = max((last - pos["trail_stop"]) * pos["shares"], 0)
            else:
                upside   = max((last - target)            * pos["shares"], 0)
                downside = max((pos["trail_stop"] - last)  * pos["shares"], 0)
            strat = pos.get("strategy", "")
            is_global = self._is_global(sym)
            items.append(dict(
                sym=sym, alloc=alloc, mval=mval, side=side, unrl=unrl,
                upside=upside, downside=downside,
                entry=pos["entry_price"], last=last,
                target=target, stop=pos["trail_stop"],
                shares=pos["shares"], atr=atr,
                strategy=strat, is_global=is_global,
                entry_time=pos.get("entry_time", ""),
                is_cash=False,
            ))
        items.sort(key=lambda x: -x["alloc"])

        cash_frac = cash / equity
        if cash_frac > 0.002:
            items.append(dict(sym="CASH", alloc=cash_frac, mval=cash,
                              side="cash", unrl=0, is_cash=True,
                              upside=0, downside=0,
                              entry=0, last=0, target=0, stop=0, shares=0))

        if not items:
            self._txt(ax, 0.5, 0.5, "No open positions", size=13, color=C["fg2"])
            self.sank_canvas.draw_idle()
            return

        n_pos  = sum(1 for it in items if not it["is_cash"])

        # ── Layout ────────────────────────────────────────────────────────
        MY  = 0.05   # top/bottom margin
        GAP = 0.012  # gap between position nodes

        # Two columns only — equity source + wide position cards
        X0, W0 = 0.01,  0.08   # EQUITY source node
        X1, W1 = 0.14,  0.56   # POSITION nodes (wider — contains all detail inline)
        X_OUT  = X1 + W1       # outcome branches fan right from here to 0.99

        usable = 1.0 - 2 * MY

        # Heights strictly proportional to allocation — no artificial MIN_H
        # so a 50% position truly takes 50% of vertical space.
        total_alloc = sum(it["alloc"] for it in items)
        n_gaps      = max(len(items) - 1, 0)
        gap_total   = GAP * n_gaps
        node_budget = usable - gap_total
        heights     = [it["alloc"] / total_alloc * node_budget for it in items]

        # y-bottom coords, top-down (largest at top already because items sorted desc)
        y_bottoms = []
        cur = MY
        for h in heights:
            y_bottoms.append(cur)
            cur += h + GAP

        # Max outcome dollar value across all positions — used to scale branch thickness
        max_outcome = max(
            (max(it["upside"], it["downside"]) for it in items if not it["is_cash"]),
            default=1.0
        )

        # ── Source node (EQUITY) — full usable height ─────────────────────
        self._node(ax, X0, MY, W0, usable, face="#1158c7", edge="#4080ff44")
        cx0 = X0 + W0 / 2
        for frac, txt, sz, bold, col in [
            (0.80, "ALPACA",             7.5, True,  "#aac4ff"),
            (0.68, "PAPER",              7.5, True,  "#aac4ff"),
            (0.54, f"${equity:,.0f}",    11,  True,  "white"),
            (0.38, f"P&L  {(f'{total_pnl:>+,.0f}' if alp_start > 0 else '—')}",
                                          8,  False, "#3fb950" if total_pnl >= 0 else "#f85149"),
            (0.23, f"{(1-cash_frac)*100:.0f}% deployed",
                                         7.5, False, "#8b949e"),
            (0.10, "trail stop exits",   6.5, False, "#484f58"),
        ]:
            self._txt(ax, cx0, MY + usable * frac, txt,
                      size=sz, color=col, bold=bold)

        src_cursor = MY

        # ── Position nodes ────────────────────────────────────────────────
        for it, yb, h in zip(items, y_bottoms, heights):
            y_ctr = yb + h / 2

            # Node color
            if it["is_cash"]:
                face, edge, flow_c = "#21262d", "#30363d88", "#484f58"
            elif it["side"] == "long":
                face  = "#1b4d2e" if it["unrl"] < -50 else \
                        "#238636" if it["unrl"] >= 0  else "#1b3a28"
                edge, flow_c = "#3fb95066", "#3fb950"
            else:
                face  = "#5a1a1a" if it["unrl"] < -50 else \
                        "#da3633" if it["unrl"] >= 0  else "#7a2020"
                edge, flow_c = "#f8514966", "#f85149"

            self._node(ax, X1, yb, W1, h, face=face, edge=edge)
            cx1 = X1 + W1 / 2

            # ── Equity → position inflow ──────────────────────────────────
            src_y = src_cursor + h / 2
            self._flow(ax, X0 + W0, src_y, X1, y_ctr, h * 0.82,
                       color=flow_c, alpha=0.38)
            if h > 0.04:
                self._txt(ax, (X0 + W0 + X1) / 2, src_y,
                          f"{it['alloc']*100:.1f}%", size=7, color="white", alpha=0.75)
            src_cursor += h

            # ── CASH node ─────────────────────────────────────────────────
            if it["is_cash"]:
                self._txt(ax, cx1, y_ctr + h * 0.15, "CASH",
                          size=9, color="#8b949e", bold=True)
                self._txt(ax, cx1, y_ctr - h * 0.15,
                          f"${it['mval']:,.0f}  ({it['alloc']*100:.1f}%)",
                          size=8, color="#6e7681")
                continue

            # ── Position node content ─────────────────────────────────────
            side_col  = "#3fb950" if it["side"] == "long" else "#f85149"
            side_lbl  = "▲ LONG" if it["side"] == "long" else "▼ SHORT"
            strat_lbl = it["strategy"].replace("_", " ").upper() if it["strategy"] else ""
            mkt_lbl   = "global" if it["is_global"] else "US"
            chg       = (it["last"] - it["entry"]) if it["side"] == "long" \
                        else (it["entry"] - it["last"])
            chg_p     = chg / it["entry"] * 100 if it["entry"] else 0
            chg_c     = "#3fb950" if chg >= 0 else "#f85149"
            unrl_s    = f"{it['unrl']:>+,.0f}" if abs(it["unrl"]) > 0.5 else "—"
            cost      = it["entry"] * it["shares"]
            mult      = "1×ATR" if it["side"] == "short" else "3×ATR"
            rr        = it["upside"] / max(it["downside"], 0.01)
            rr_c      = "#3fb950" if rr >= 2.0 else "#d29922" if rr >= 1.0 else "#f85149"

            if it["entry"] > 0:
                tgt_pct = abs((it["target"] - it["entry"]) / it["entry"] * 100)
                stp_pct = abs((it["stop"]   - it["entry"]) / it["entry"] * 100)
            else:
                tgt_pct = stp_pct = 0.0

            if h >= 0.18:
                # Full layout — 6 rows
                rows = [
                    # (y_frac_from_ctr, text, size, color, bold, ha)
                    ( 0.38, f"{it['sym']}",                     13, "white",    True,  "left"),
                    ( 0.38, f"{side_lbl}  ·  {strat_lbl}  [{mkt_lbl}]",
                                                                  7.5, side_col, False, "right"),
                    ( 0.20, f"{it['shares']} sh @ ${it['entry']:.2f} = ${cost:,.0f}",
                                                                  7.5, "#c9d1d9", False, "center"),
                    ( 0.06, f"now  ${it['last']:.2f}  ({chg_p:>+.1f}%)   unrl {unrl_s}",
                                                                  7.5, chg_c,   False, "center"),
                    (-0.10, f"▲ 2×ATR  ${it['target']:.2f}  (+{tgt_pct:.1f}%)  est gain +${it['upside']:,.0f}",
                                                                  7,   "#3fb950", False, "center"),
                    (-0.24, f"▼ trail stop {mult}  ${it['stop']:.2f}  (-{stp_pct:.1f}%)  max loss -${it['downside']:,.0f}",
                                                                  7,   "#f85149", False, "center"),
                    (-0.38, f"R:R  {rr:.1f}×  ·  {it['alloc']*100:.1f}% of portfolio",
                                                                  7,   rr_c,     True,  "center"),
                ]
                for frac, txt, sz, col, bold, ha in rows:
                    if ha == "left":
                        self._txt(ax, X1 + W1 * 0.04, y_ctr + h * frac, txt,
                                  size=sz, color=col, bold=bold, ha="left")
                    elif ha == "right":
                        self._txt(ax, X1 + W1 * 0.96, y_ctr + h * frac, txt,
                                  size=sz, color=col, bold=bold, ha="right")
                    else:
                        self._txt(ax, cx1, y_ctr + h * frac, txt,
                                  size=sz, color=col, bold=bold)
            elif h >= 0.09:
                # Medium — 4 rows
                self._txt(ax, X1 + W1*0.04, y_ctr + h*0.32,
                          it["sym"], size=11, color="white", bold=True, ha="left")
                self._txt(ax, X1 + W1*0.96, y_ctr + h*0.32,
                          f"{side_lbl} · {mkt_lbl}", size=7, color=side_col, ha="right")
                self._txt(ax, cx1, y_ctr + h*0.06,
                          f"{it['shares']} sh @ ${it['entry']:.2f}  now ${it['last']:.2f}  ({chg_p:>+.1f}%)  unrl {unrl_s}",
                          size=7, color=chg_c)
                self._txt(ax, cx1, y_ctr - h*0.22,
                          f"▲ ${it['target']:.2f} +${it['upside']:,.0f}    ▼ ${it['stop']:.2f} -${it['downside']:,.0f}    R:R {rr:.1f}×",
                          size=6.5, color="#8b949e")
            else:
                # Compact — 2 rows
                self._txt(ax, X1 + W1*0.04, y_ctr + h*0.18,
                          it["sym"], size=9, color="white", bold=True, ha="left")
                self._txt(ax, cx1, y_ctr - h*0.18,
                          f"{side_lbl}  {it['alloc']*100:.1f}%  unrl {unrl_s}",
                          size=6.5, color=side_col)

            # ── Outflow branches: green (upside) + red (stop) ────────────
            # Branch thickness proportional to outcome $ vs max_outcome,
            # capped at half the node height so they fit visually
            max_branch_h = h * 0.46
            up_h  = max(it["upside"]   / max_outcome * max_branch_h, 0.003)
            dn_h  = max(it["downside"] / max_outcome * max_branch_h, 0.003)
            # green fans up-right from top half of right edge
            self._flow(ax, X_OUT, y_ctr + h * 0.18, 0.995,
                       y_ctr + h * 0.30, up_h, color="#3fb950", alpha=0.55)
            # red fans down-right from bottom half
            self._flow(ax, X_OUT, y_ctr - h * 0.18, 0.995,
                       y_ctr - h * 0.30, dn_h, color="#f85149", alpha=0.55)
            # terminal labels at right edge
            if h > 0.06:
                self._txt(ax, 0.997, y_ctr + h * 0.30,
                          f"▲ ${it['target']:.2f}  +${it['upside']:,.0f}",
                          size=6, color="#3fb950", ha="left", va="center")
                self._txt(ax, 0.997, y_ctr - h * 0.30,
                          f"▼ ${it['stop']:.2f}  -${it['downside']:,.0f}",
                          size=6, color="#f85149", ha="left", va="center")

        # ── Column headers ────────────────────────────────────────────────
        hdr_y = 1.0 - MY * 0.35
        for xc, lbl in [(X0 + W0/2, "ALPACA EQUITY"),
                        (X1 + W1/2, "OPEN POSITIONS"),
                        (0.91,       "OUTCOMES")]:
            self._txt(ax, xc, hdr_y, lbl, size=7.5, color="#484f58", bold=True)

        # ── Timestamp ─────────────────────────────────────────────────────
        ts = datetime.now(ET).strftime("%H:%M:%S")
        self._txt(ax, 0.995, 0.01, f"updated {ts}",
                  size=6.5, color="#484f58", ha="right", va="bottom")

        self.sank_canvas.draw_idle()
        self.sank_ts.config(
            text=f"updated {ts}  ·  {n_pos} position{'s' if n_pos != 1 else ''} open"
                 f"  ·  P&L {total_pnl:>+,.0f}")

    # ── Trade history Sankey ──────────────────────────────────────────────────

    def _draw_history_sankey(self):
        import matplotlib.dates as mdates
        import matplotlib.ticker as mticker
        from matplotlib.ticker import FuncFormatter

        fig  = self.hist_fig
        ax   = self.hist_ax
        ax.clear()
        ax.set_facecolor(C["bg"])
        fig.patch.set_facecolor(C["bg"])

        if not PORT_STATE.exists():
            ax.text(0.5, 0.5, "No trade history yet", ha="center", va="center",
                    color=C["fg2"], fontsize=12, transform=ax.transAxes)
            self.hist_canvas.draw_idle()
            return

        try:
            s = json.loads(PORT_STATE.read_text())
        except Exception:
            self.hist_canvas.draw_idle()
            return

        closed      = s.get("closed_trades", [])
        equity_hist = s.get("equity_history", [])
        account_size = s.get("account_size", 100000)

        # ── Parse equity history ──────────────────────────────────────────
        times, equities = [], []
        for pt in equity_hist:
            try:
                t = datetime.strptime(pt["time"][:16], "%Y-%m-%d %H:%M")
                times.append(t)
                equities.append(pt["equity"])
            except Exception:
                continue

        if not times:
            ax.text(0.5, 0.5, "Waiting for data…",
                    ha="center", va="center", color=C["fg2"], fontsize=11,
                    transform=ax.transAxes)
            self.hist_canvas.draw_idle()
            return

        eq_np = np.array(equities)
        y_pad = max((eq_np.max() - eq_np.min()) * 0.15, 50)
        ax.set_xlim(times[0], times[-1])
        ax.set_ylim(eq_np.min() - y_pad, eq_np.max() + y_pad * 2.5)

        # ── Equity curve with green/red fill ──────────────────────────────
        ax.fill_between(times, account_size, eq_np,
                        where=eq_np >= account_size,
                        color="#3fb950", alpha=0.18, interpolate=True)
        ax.fill_between(times, account_size, eq_np,
                        where=eq_np < account_size,
                        color="#f85149", alpha=0.22, interpolate=True)
        ax.plot(times, equities, color="#58a6ff", linewidth=1.4, zorder=4)
        ax.axhline(account_size, color="#484f58", linewidth=0.7,
                   linestyle="--", alpha=0.55, zorder=3)

        # ── Helper: equity value nearest to a given datetime ─────────────
        def eq_at(t_target):
            best_eq = account_size
            for t_h, eq_h in zip(times, equities):
                if t_h <= t_target:
                    best_eq = eq_h
                else:
                    break
            return best_eq

        # ── Trade hold-period bands + entry/exit markers ──────────────────
        # Stagger label rows to avoid overlap (alternate above/below)
        label_slots = {}   # exit_time minute bucket → count (for vertical stagger)

        for i, tr in enumerate(closed):
            try:
                t_exit = datetime.strptime(tr["exit_time"][:16], "%Y-%m-%d %H:%M")
                t_entry = (datetime.strptime(tr["entry_time"][:16], "%Y-%m-%d %H:%M")
                           if tr.get("entry_time") else None)
                pnl  = tr["realized_pnl"]
                sym  = tr["symbol"]
                col  = "#3fb950" if pnl >= 0 else "#f85149"
                col_faint = "#1b4d2e" if pnl >= 0 else "#5a1a1a"

                eq_exit  = eq_at(t_exit)
                eq_entry = eq_at(t_entry) if t_entry else eq_exit

                # Hold period shaded band at bottom of chart
                if t_entry and t_entry < t_exit:
                    ymin_ax, ymax_ax = ax.get_ylim()
                    band_h = (ymax_ax - ymin_ax) * 0.022
                    band_y = ymin_ax + (i % 6) * band_h * 1.1
                    ax.barh(band_y, width=mdates.date2num(t_exit) - mdates.date2num(t_entry),
                            left=mdates.date2num(t_entry), height=band_h * 0.75,
                            color=col_faint, alpha=0.55, zorder=2,
                            align="edge")
                    # Ticker label inside band
                    mid_t = mdates.num2date(
                        (mdates.date2num(t_entry) + mdates.date2num(t_exit)) / 2
                    )
                    ax.text(mid_t, band_y + band_h * 0.35, sym,
                            ha="center", va="center", fontsize=5.5,
                            color=col, alpha=0.85, zorder=5)

                # Entry dot
                if t_entry:
                    ax.scatter([t_entry], [eq_entry], color=col, s=28,
                               marker="o", zorder=6, alpha=0.7,
                               edgecolors="#0d1117", linewidths=0.5)

                # Exit marker (triangle)
                ax.scatter([t_exit], [eq_exit], color=col, s=60,
                           marker="^" if pnl >= 0 else "v",
                           zorder=7, alpha=0.95,
                           edgecolors="#0d1117", linewidths=0.5)

                # Label above/below exit marker — stagger by bucket
                bucket = t_exit.strftime("%Y%m%d%H%M")
                slot   = label_slots.get(bucket, 0)
                label_slots[bucket] = slot + 1
                y_off = 16 + slot * 18 if pnl >= 0 else -(20 + slot * 18)
                pnl_s = f"{pnl:+.0f}" if abs(pnl) >= 1 else "±0"
                ax.annotate(f"{sym}\n{pnl_s}", (t_exit, eq_exit),
                            xytext=(0, y_off), textcoords="offset points",
                            ha="center", fontsize=6.5, color=col, zorder=8,
                            arrowprops=dict(arrowstyle="-", color=col,
                                           alpha=0.45, lw=0.6))

            except Exception:
                continue

        # ── Axes formatting ───────────────────────────────────────────────
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"${v:,.0f}"))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d\n%H:%M"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.yaxis.set_major_locator(mticker.MaxNLocator(6))
        ax.tick_params(axis="x", colors="#6e7681", labelsize=6.5, pad=2)
        ax.tick_params(axis="y", colors="#6e7681", labelsize=7)
        for sp in ["bottom", "left"]:
            ax.spines[sp].set_color("#30363d")
            ax.spines[sp].set_visible(True)
        for sp in ["top", "right"]:
            ax.spines[sp].set_visible(False)

        # ── Summary box ───────────────────────────────────────────────────
        if closed:
            wins       = [t for t in closed if t["realized_pnl"] >= 0]
            losses     = [t for t in closed if t["realized_pnl"] <  0]
            total_won  = sum(t["realized_pnl"] for t in wins)
            total_lost = sum(t["realized_pnl"] for t in losses)
            net        = sum(t["realized_pnl"] for t in closed)
            win_rate   = len(wins) / len(closed) * 100
            lines = [
                (f"{len(closed)} trades",                   "#8b949e"),
                (f"W {len(wins)} / L {len(losses)}  ({win_rate:.0f}%)", "#8b949e"),
                (f"Gross win  +${total_won:,.0f}",          "#3fb950"),
                (f"Gross loss  -${abs(total_lost):,.0f}",   "#f85149"),
                (f"Net  {'+'if net>=0 else ''}{net:,.0f}",  "#3fb950" if net >= 0 else "#f85149"),
            ]
            for i, (txt, col) in enumerate(lines):
                ax.text(0.995, 0.98 - i * 0.07, txt,
                        transform=ax.transAxes, ha="right", va="top",
                        fontsize=8, color=col, fontweight="bold",
                        bbox=dict(boxstyle="round,pad=0.25", facecolor="#0d1117",
                                  edgecolor="#30363d66", alpha=0.9))

        ts = datetime.now(ET).strftime("%H:%M:%S")
        ax.set_title(
            f"Full Trading History  ·  {len(equity_hist)} equity snapshots  ·  "
            f"{len(closed)} closed",
            color="#6e7681", fontsize=8, loc="left", pad=5)

        self.hist_canvas.draw_idle()
        net_str = f"{s.get('realized_pnl', 0):>+,.2f}"
        self.hist_ts.config(
            text=f"updated {ts}  ·  {len(closed)} closed trades  ·  realized {net_str}")

    # ── Scanner ───────────────────────────────────────────────────────────────

    def _scan_async(self):
        self.scan_status.config(
            text=f"Scanning {len(UNIVERSE)} symbols…", fg=C["yellow"])
        self.root.update_idletasks()
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        results = []
        with ThreadPoolExecutor(max_workers=16) as ex:
            futs = {ex.submit(scan_symbol, s): s for s in UNIVERSE}
            for f in as_completed(futs):
                r = f.result()
                if r:
                    results.append(r)
        # LONG/SHORT first, then by score descending
        results.sort(key=lambda x: (x["signal"] == "watch", -x["score"]))
        self.scan_results = results
        self.root.after(0, self._populate_table)

    def _populate_table(self):
        for row in self.scan_tree.get_children():
            self.scan_tree.delete(row)

        now     = datetime.now(ET).strftime("%H:%M:%S")
        setups  = sum(1 for r in self.scan_results if r["signal"] != "watch")
        self.scan_status.config(
            text=f"Last scan: {now}  ·  {setups} setups  ·  {len(self.scan_results)} symbols",
            fg=C["fg2"])

        for i, r in enumerate(self.scan_results[:25], 1):
            sig = r["signal"]
            self.scan_tree.insert("", END, iid=str(i), tags=(sig,), values=(
                i,
                r["symbol"],
                f"${r['price']:.2f}",
                f"{r['chg_pct']:>+.1f}%",
                f"{r['rel_vol']:.1f}×",
                f"{r['adx']:.0f}",
                r["regime"],
                sig,
                r["score"],
            ))

        self.scan_tree.tag_configure("LONG",  foreground=C["green"])
        self.scan_tree.tag_configure("SHORT", foreground=C["red"])
        self.scan_tree.tag_configure("watch", foreground=C["fg2"])

    def _on_select(self, _):
        sel = self.scan_tree.selection()
        if not sel:
            return
        idx = int(sel[0]) - 1
        if idx < 0 or idx >= len(self.scan_results):
            return
        r = self.scan_results[idx]

        sig_c = C["green"] if r["signal"] == "LONG" else \
                C["red"]   if r["signal"] == "SHORT" else C["fg2"]

        self.det["Symbol"].config(text=r["symbol"], fg=C["blue"])
        self.det["Signal"].config(text=r["signal"],  fg=sig_c)

        if r["entry"]:
            risk = abs(r["entry"] - r["stop"])
            rr   = abs(r["target"] - r["entry"]) / risk if risk else 0
            self.det["Entry"].config( text=f"${r['entry']:.2f}",  fg=C["fg"])
            self.det["Stop"].config(  text=f"${r['stop']:.2f}",   fg=C["red"])
            self.det["Target"].config(text=f"${r['target']:.2f}", fg=C["green"])
            self.det["R:R"].config(   text=f"{rr:.1f}:1",          fg=C["fg"])
        else:
            for k in ("Entry", "Stop", "Target", "R:R"):
                self.det[k].config(text="—", fg=C["fg2"])

        self.det["ATR"].config( text=f"${r['atr']:.2f}")
        self.det["VWAP"].config(text=f"${r['vwap']:.2f}")


    # ── Daily Review tab ─────────────────────────────────────────────────────

    def _build_daily_review(self, parent):
        REVIEW_FILE   = Path("daily_review.json")
        PREMARKET_FILE = Path("premarket_picks.json")
        self._dr_review_file   = REVIEW_FILE
        self._dr_premarket_file = PREMARKET_FILE

        # ── Stat bar ──────────────────────────────────────────────────────────
        stat_frame = Frame(parent, bg=C["bg2"], pady=6)
        stat_frame.pack(fill=X, padx=10, pady=(8, 4))
        self._dr_stats = {}
        for label in ("Date", "Trades", "Wins", "Losses", "Win Rate", "Day P&L", "Total P&L"):
            col = Frame(stat_frame, bg=C["bg2"], padx=18)
            col.pack(side=LEFT)
            Label(col, text=label, font=("Courier", 10), fg=C["fg2"], bg=C["bg2"]).pack()
            v = Label(col, text="—", font=("Courier", 13, "bold"), fg=C["fg"], bg=C["bg2"])
            v.pack()
            self._dr_stats[label] = v

        # ── Main pane ─────────────────────────────────────────────────────────
        pw = ttk.PanedWindow(parent, orient=HORIZONTAL)
        pw.pack(fill=BOTH, expand=True, padx=10, pady=4)

        # Left: top movers + missed opps stacked
        left = Frame(pw, bg=C["bg"])
        pw.add(left, weight=1)

        Label(left, text="Today's Top Movers", font=("Courier", 11, "bold"),
              fg=C["blue"], bg=C["bg"]).pack(anchor=W, padx=4, pady=(4, 2))
        cols = ("Symbol", "Day %", "Open", "Close")
        self._dr_movers = ttk.Treeview(left, columns=cols, show="headings",
                                        style="Log.Treeview", height=10)
        for c in cols:
            self._dr_movers.heading(c, text=c)
            self._dr_movers.column(c, width=100, anchor=CENTER)
        self._dr_movers.column("Symbol", width=80)
        self._dr_movers.pack(fill=BOTH, expand=True, padx=4)

        Label(left, text="Missed Opportunities", font=("Courier", 11, "bold"),
              fg=C["yellow"], bg=C["bg"]).pack(anchor=W, padx=4, pady=(8, 2))
        miss_cols = ("Symbol", "Day %", "Reason")
        self._dr_missed = ttk.Treeview(left, columns=miss_cols, show="headings",
                                        style="Log.Treeview", height=8)
        for c in miss_cols:
            self._dr_missed.heading(c, text=c)
        self._dr_missed.column("Symbol", width=70, anchor=CENTER)
        self._dr_missed.column("Day %",  width=70, anchor=CENTER)
        self._dr_missed.column("Reason", width=380, anchor=W)
        self._dr_missed.pack(fill=BOTH, expand=True, padx=4, pady=(0, 4))

        # Right: suggestions + pre-market picks
        right = Frame(pw, bg=C["bg"])
        pw.add(right, weight=1)

        Label(right, text="Suggestions / Tomorrow's Game Plan",
              font=("Courier", 11, "bold"), fg=C["green"], bg=C["bg"]).pack(
              anchor=W, padx=4, pady=(4, 2))
        self._dr_suggest = Text(right, height=8, bg=C["bg2"], fg=C["fg"],
                                 font=("Courier", 11), relief=FLAT,
                                 state=DISABLED, wrap=WORD)
        self._dr_suggest.pack(fill=X, padx=4)

        Label(right, text="Pre-Market Picks (Tomorrow 09:35 entries)",
              font=("Courier", 11, "bold"), fg=C["purple"], bg=C["bg"]).pack(
              anchor=W, padx=4, pady=(10, 2))
        pm_cols = ("Rank", "Symbol", "Score", "Gap %", "PM Vol×", "ADX", "Dir")
        self._dr_pm = ttk.Treeview(right, columns=pm_cols, show="headings",
                                    style="Log.Treeview", height=12)
        for c in pm_cols:
            self._dr_pm.heading(c, text=c)
            self._dr_pm.column(c, width=75, anchor=CENTER)
        self._dr_pm.column("Rank",   width=50)
        self._dr_pm.column("Symbol", width=80)
        self._dr_pm.column("Score",  width=60)
        self._dr_pm.pack(fill=BOTH, expand=True, padx=4, pady=(0, 4))

        self._dr_status = Label(right, text="Waiting for daily review (runs at 16:05 ET)…",
                                 font=("Courier", 10), fg=C["fg2"], bg=C["bg"])
        self._dr_status.pack(anchor=W, padx=4)

        self._refresh_daily_review()

    def _refresh_daily_review(self):
        REVIEW_FILE    = self._dr_review_file
        PREMARKET_FILE = self._dr_premarket_file

        try:
            # ── Performance stats ──────────────────────────────────────────────
            if PORT_STATE.exists():
                s = json.loads(PORT_STATE.read_text())
                total_pnl = s.get("total_pnl", 0)
                self._dr_stats["Total P&L"].config(
                    text=f"${total_pnl:+,.0f}",
                    fg=C["green"] if total_pnl >= 0 else C["red"])

            if REVIEW_FILE.exists():
                rev = json.loads(REVIEW_FILE.read_text())
                perf = rev.get("performance", {})
                self._dr_stats["Date"].config(text=rev.get("date", "—"))
                self._dr_stats["Trades"].config(text=str(perf.get("trades", 0)))
                self._dr_stats["Wins"].config(
                    text=str(perf.get("wins", 0)), fg=C["green"])
                self._dr_stats["Losses"].config(
                    text=str(perf.get("losses", 0)), fg=C["red"])
                wr = perf.get("win_rate", 0)
                self._dr_stats["Win Rate"].config(
                    text=f"{wr:.0%}", fg=C["green"] if wr >= 0.4 else C["yellow"] if wr >= 0.25 else C["red"])
                day_pnl = perf.get("total_pnl", 0)
                self._dr_stats["Day P&L"].config(
                    text=f"${day_pnl:+,.0f}",
                    fg=C["green"] if day_pnl >= 0 else C["red"])

                # ── Top movers ─────────────────────────────────────────────────
                self._dr_movers.delete(*self._dr_movers.get_children())
                for m in rev.get("top_movers_today", [])[:15]:
                    chg = m.get("day_chg", 0)
                    tag = "up" if chg >= 0 else "dn"
                    self._dr_movers.insert("", END, values=(
                        m["symbol"],
                        f"{chg:+.1f}%",
                        f"${m.get('open', 0):.2f}",
                        f"${m.get('close', 0):.2f}",
                    ), tags=(tag,))
                self._dr_movers.tag_configure("up", foreground=C["green"])
                self._dr_movers.tag_configure("dn", foreground=C["red"])

                # ── Missed opportunities ───────────────────────────────────────
                self._dr_missed.delete(*self._dr_missed.get_children())
                for m in rev.get("missed_opportunities", []):
                    chg = m.get("day_chg", 0)
                    self._dr_missed.insert("", END, values=(
                        m["symbol"],
                        f"{chg:+.1f}%",
                        m.get("miss_reason", ""),
                    ))

                # ── Suggestions ────────────────────────────────────────────────
                self._dr_suggest.config(state=NORMAL)
                self._dr_suggest.delete("1.0", END)
                for i, s_text in enumerate(rev.get("suggestions", []), 1):
                    self._dr_suggest.insert(END, f"{i}. {s_text}\n\n")
                self._dr_suggest.config(state=DISABLED)

                self._dr_status.config(
                    text=f"Review updated: {rev.get('generated', '?')[:19]}",
                    fg=C["fg2"])

        except Exception:
            pass

        # ── Pre-market picks ───────────────────────────────────────────────────
        try:
            if PREMARKET_FILE.exists():
                pm = json.loads(PREMARKET_FILE.read_text())
                picks = pm.get("picks", [])
                self._dr_pm.delete(*self._dr_pm.get_children())
                for i, p in enumerate(picks[:20], 1):
                    dir_c = "up" if p.get("direction") == "long" else "dn"
                    self._dr_pm.insert("", END, values=(
                        i,
                        p["symbol"],
                        f"{p['score']:.0f}",
                        f"{p.get('gap_pct', 0):+.1f}%",
                        f"{p.get('pm_vol_ratio', 0):.1f}×",
                        f"{p.get('adx', 0):.0f}",
                        p.get("direction", "?").upper(),
                    ), tags=(dir_c,))
                self._dr_pm.tag_configure("up", foreground=C["green"])
                self._dr_pm.tag_configure("dn", foreground=C["red"])
                if picks:
                    self._dr_status.config(
                        text=f"Pre-market picks: {len(picks)} picks from {pm.get('generated', '?')[:19]}",
                        fg=C["purple"])
        except Exception:
            pass

        self.root.after(30_000, self._refresh_daily_review)

    # ══════════════════════════════════════════════════════════════════════════
    # SWARM AI TAB
    # ══════════════════════════════════════════════════════════════════════════

    def _build_swarm_tab(self, parent):
        from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
        SWARM_STATE = Path("swarm_state.json")

        # ── Top control bar ───────────────────────────────────────────────────
        ctrl = Frame(parent, bg=C["bg"])
        ctrl.pack(fill=X, padx=12, pady=(10, 4))

        self._sw_regime_lbl = Label(ctrl, text="Regime: —", font=("Courier", 13, "bold"),
                                    bg=C["bg"], fg=C["blue"])
        self._sw_regime_lbl.pack(side=LEFT, padx=(0, 20))

        self._sw_fear_lbl = Label(ctrl, text="Fear: —", font=("Courier", 12),
                                  bg=C["bg"], fg=C["fg2"])
        self._sw_fear_lbl.pack(side=LEFT, padx=(0, 20))

        self._sw_sector_lbl = Label(ctrl, text="Leader: —", font=("Courier", 12),
                                    bg=C["bg"], fg=C["fg2"])
        self._sw_sector_lbl.pack(side=LEFT, padx=(0, 20))

        self._sw_time_lbl = Label(ctrl, text="Equity: —", font=("Courier", 11, "bold"),
                                  bg=C["bg"], fg=C["fg2"])
        self._sw_time_lbl.pack(side=LEFT)

        run_btn = Button(ctrl, text="  ▶  Run Now  ",
                         font=("Courier", 11, "bold"),
                         bg=C["bg3"], fg=C["green"], relief=FLAT, cursor="hand2",
                         command=lambda: threading.Thread(
                             target=self._run_portfolio_now, daemon=True).start())
        run_btn.pack(side=RIGHT, padx=4)

        # ── Full-width equity chart ───────────────────────────────────────────
        chart_frame = Frame(parent, bg=C["bg"])
        chart_frame.pack(fill=X, padx=8, pady=(0, 2))

        eq_fig = Figure(facecolor=C["bg"])
        eq_fig.set_tight_layout({"pad": 0.6, "h_pad": 0.2})
        self._sw_eq_ax  = eq_fig.add_subplot(111)
        self._sw_eq_ax.set_facecolor(C["bg2"] if "bg2" in C else "#1a1a2e")
        self._sw_eq_ax.tick_params(colors=C["fg2"], labelsize=8, which="both")
        self._sw_eq_ax.grid(True, color=C["bg3"], linewidth=0.4, linestyle="--")
        for sp in self._sw_eq_ax.spines.values():
            sp.set_color(C["bg3"])

        self._sw_eq_canvas = FigureCanvasTkAgg(eq_fig, master=chart_frame)
        self._sw_eq_canvas.get_tk_widget().pack(fill=X, expand=False)

        # Navigation toolbar (zoom box, pan, home, back/forward, save)
        toolbar_frame = Frame(chart_frame, bg="#2b2b2b")
        toolbar_frame.pack(fill=X)
        toolbar = NavigationToolbar2Tk(self._sw_eq_canvas, toolbar_frame)
        toolbar.config(background="#2b2b2b")
        for child in toolbar.winfo_children():
            try: child.config(background="#2b2b2b", foreground=C["fg2"])
            except Exception: pass
        toolbar.update()

        # Hover crosshair annotation
        self._sw_eq_annot = self._sw_eq_ax.annotate(
            "", xy=(0, 0), xytext=(8, 8), textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.3", fc=C["bg3"], ec=C["fg2"], lw=0.8),
            fontsize=8, color=C["fg"], fontfamily="Courier New")
        self._sw_eq_annot.set_visible(False)
        self._sw_eq_vline = self._sw_eq_ax.axvline(x=0, color=C["fg2"],
                                                     linewidth=0.7, linestyle=":", visible=False)
        self._sw_eq_hline = self._sw_eq_ax.axhline(y=0, color=C["fg2"],
                                                     linewidth=0.7, linestyle=":", visible=False)
        self._sw_eq_history = []   # kept in sync so hover can look up data

        def _on_eq_hover(event):
            if event.inaxes != self._sw_eq_ax or not self._sw_eq_history:
                self._sw_eq_annot.set_visible(False)
                self._sw_eq_vline.set_visible(False)
                self._sw_eq_hline.set_visible(False)
                self._sw_eq_canvas.draw_idle()
                return
            xs = [i for i in range(len(self._sw_eq_history))]
            if not xs: return
            # Find nearest data point by x distance
            idx = min(range(len(xs)), key=lambda i: abs(xs[i] - event.xdata))
            pt  = self._sw_eq_history[idx]
            eq  = pt["equity"]
            start = 500.0
            delta = eq - start
            sign  = "+" if delta >= 0 else ""
            label = f"{pt['time'][-8:]}\n${eq:.2f}  ({sign}{delta:.2f})"
            self._sw_eq_annot.set_text(label)
            self._sw_eq_annot.xy = (idx, eq)
            self._sw_eq_annot.set_visible(True)
            self._sw_eq_vline.set_xdata([idx])
            self._sw_eq_vline.set_visible(True)
            self._sw_eq_hline.set_ydata([eq])
            self._sw_eq_hline.set_visible(True)
            self._sw_eq_canvas.draw_idle()

        self._sw_eq_canvas.mpl_connect("motion_notify_event", _on_eq_hover)

        # ── Bottom split: positions (left) | signal board + log (right) ──────
        pw = ttk.PanedWindow(parent, orient=HORIZONTAL)
        pw.pack(fill=BOTH, expand=True, padx=8, pady=(0, 4))

        left  = Frame(pw, bg=C["bg"])
        right = Frame(pw, bg=C["bg"])
        pw.add(left,  weight=3)
        pw.add(right, weight=2)

        # ── Position cards container (scrollable) ─────────────────────────────
        pos_title = Label(left, text="  ACTIVE POSITIONS  (5 slots)",
                          font=("Courier", 12, "bold"), bg=C["bg"], fg=C["fg2"])
        pos_title.pack(anchor=W, padx=6, pady=(4, 2))

        pos_canvas   = Canvas(left, bg=C["bg"], highlightthickness=0)
        pos_scroll   = ttk.Scrollbar(left, orient=VERTICAL, command=pos_canvas.yview)
        self._sw_pos_frame = Frame(pos_canvas, bg=C["bg"])
        self._sw_pos_frame.bind("<Configure>",
            lambda e: pos_canvas.configure(scrollregion=pos_canvas.bbox("all")))
        pos_canvas.create_window((0, 0), window=self._sw_pos_frame, anchor=NW)
        pos_canvas.configure(yscrollcommand=pos_scroll.set)
        pos_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        pos_scroll.pack(side=RIGHT, fill=Y)

        # ── Right panel: signal board + activity log ──────────────────────────
        sig_title = Label(right, text="  SIGNAL BOARD  (all signals ranked)",
                          font=("Courier", 12, "bold"), bg=C["bg"], fg=C["fg2"])
        sig_title.pack(anchor=W, padx=6, pady=(4, 2))

        sig_cols = ("Symbol","Score","Cat","ADX","Mom5d","Earnings","IV Signal","Options")
        self._sw_sig_tree = ttk.Treeview(right, columns=sig_cols, show="headings",
                                          height=9)
        widths = (60, 55, 40, 50, 65, 75, 175, 150)
        for col, w in zip(sig_cols, widths):
            self._sw_sig_tree.heading(col, text=col)
            self._sw_sig_tree.column(col,  width=w, anchor=CENTER)
        sig_sb = ttk.Scrollbar(right, orient=VERTICAL, command=self._sw_sig_tree.yview)
        self._sw_sig_tree.configure(yscrollcommand=sig_sb.set)
        self._sw_sig_tree.pack(side=LEFT, fill=BOTH, expand=True, padx=(4,0))
        sig_sb.pack(side=LEFT, fill=Y)

        log_title = Label(right, text="  SWARM ACTIVITY LOG",
                          font=("Courier", 12, "bold"), bg=C["bg"], fg=C["fg2"])
        log_title.pack(anchor=W, padx=6, pady=(8, 2))

        log_cols = ("Time", "Action", "Reason")
        self._sw_log_tree = ttk.Treeview(right, columns=log_cols, show="headings",
                                          height=8, style="Log.Treeview")
        for col, w in zip(log_cols, (110, 130, 300)):
            self._sw_log_tree.heading(col, text=col)
            self._sw_log_tree.column(col, width=w, anchor=W)
        log_sb = ttk.Scrollbar(right, orient=VERTICAL, command=self._sw_log_tree.yview)
        self._sw_log_tree.configure(yscrollcommand=log_sb.set)
        self._sw_log_tree.pack(side=LEFT, fill=BOTH, expand=True, padx=(4,0), pady=(0,6))
        log_sb.pack(side=LEFT, fill=Y, pady=(0,6))

        self._sw_state_file  = SWARM_STATE
        self._sw_pos_widgets = []
        self.root.after(2000, self._refresh_swarm_tab)

    def _run_portfolio_now(self):
        """Called from 'Run Now' button — runs one portfolio cycle in background."""
        import subprocess, sys
        try:
            subprocess.run(
                [sys.executable, "trading_swarm.py", "--once", "--size", "100000", "--live"],
                timeout=180)
        except Exception:
            pass

    def _make_position_card(self, parent, pos: dict, idx: int) -> Frame:
        """Build one rich position card with all signal layers."""
        direction  = pos.get("direction", "long")
        is_rocket  = pos.get("is_rocket", False)
        dir_color  = C["green"] if direction == "long" else C["red"]
        dir_badge  = "▲ LONG" if direction == "long" else "▼ SHORT"
        # Rocket card gets an orange/amber border and header background
        ROCKET_CLR = "#ff9500"
        card_bg    = "#1e1200" if is_rocket else C["bg2"]
        hdr_bg     = "#2e1e00" if is_rocket else C["bg3"]
        border_clr = ROCKET_CLR if is_rocket else C.get("border", C["bg3"])

        unreal_pnl = pos.get("unrealized_pnl", 0)
        unreal_pct = pos.get("unrealized_pct", 0)
        pnl_color  = C["green"] if unreal_pnl >= 0 else C["red"]
        pnl_sign   = "+" if unreal_pnl >= 0 else ""

        card = Frame(parent, bg=card_bg, relief=FLAT,
                     highlightbackground=border_clr, highlightthickness=2 if is_rocket else 1)
        card.pack(fill=X, padx=6, pady=5)

        # ── Header row ────────────────────────────────────────────────────────
        hdr = Frame(card, bg=hdr_bg)
        hdr.pack(fill=X)
        if is_rocket:
            Label(hdr, text=" ⚡ ROCKET SLOT — 40% ", font=("Courier", 9, "bold"),
                  bg=ROCKET_CLR, fg="black").pack(side=LEFT, pady=0)
        Label(hdr, text=f"  {pos['symbol']}", font=("Courier", 15, "bold"),
              bg=hdr_bg, fg=ROCKET_CLR if is_rocket else C["fg"]).pack(side=LEFT, pady=6)
        Label(hdr, text=f" {dir_badge} ", font=("Courier", 11, "bold"),
              bg=dir_color, fg="white").pack(side=LEFT, padx=8, pady=6)
        Label(hdr, text=f"entry ${pos['entry']:.2f}",
              font=("Courier", 10), bg=hdr_bg, fg=C["fg2"]).pack(side=LEFT)

        # P&L
        Label(hdr, text=f" {pnl_sign}${unreal_pnl:.2f}  ({pnl_sign}{unreal_pct:.2f}%)",
              font=("Courier", 12, "bold"), bg=C["bg3"], fg=pnl_color).pack(side=RIGHT, padx=12)
        cur = pos.get("current_price", pos["entry"])
        Label(hdr, text=f"${cur:.2f}",
              font=("Courier", 12), bg=hdr_bg, fg=C["fg"]).pack(side=RIGHT)

        body = Frame(card, bg=card_bg)
        body.pack(fill=X, padx=10, pady=6)

        # ── Stop / Target progress bar ────────────────────────────────────────
        stop   = pos.get("stop",   pos["entry"] * 0.97)
        target = pos.get("target", pos["entry"] * 1.06)
        rng    = target - stop
        if rng > 0:
            fill_ratio = max(0, min(1, (cur - stop) / rng))
        else:
            fill_ratio = 0.5

        bar_frame = Frame(body, bg=card_bg)
        bar_frame.pack(fill=X, pady=(0, 6))
        Label(bar_frame, text=f"STOP ${stop:.2f}",
              font=("Courier", 9), bg=card_bg, fg=C["red"]).pack(side=LEFT)
        Label(bar_frame, text=f"TARGET ${target:.2f}",
              font=("Courier", 9), bg=card_bg, fg=C["green"]).pack(side=RIGHT)

        bar_bg = Frame(body, bg=C["bg3"], height=8)
        bar_bg.pack(fill=X, pady=(0, 8))
        bar_bg.update_idletasks()
        bar_fill_color = ROCKET_CLR if is_rocket else dir_color
        bar_fill = Frame(bar_bg, bg=bar_fill_color, height=8)
        bar_fill.place(relx=0, rely=0, relwidth=fill_ratio, relheight=1)

        # ── Signal grid (4 rows) ──────────────────────────────────────────────
        def sig_row(label_text, value_text, bar_pct=None, bar_color=C["blue"], hint=""):
            row = Frame(body, bg=card_bg)
            row.pack(fill=X, pady=1)
            Label(row, text=f"{label_text:<14}", font=("Courier", 10),
                  bg=card_bg, fg=C["fg2"]).pack(side=LEFT)
            if bar_pct is not None:
                bar = Frame(row, bg=C["bg3"], width=80, height=10)
                bar.pack(side=LEFT, padx=(4, 6), pady=2)
                bar.update_idletasks()
                inner = Frame(bar, bg=bar_color, height=10)
                inner.place(relx=0, rely=0, relwidth=min(1, max(0, bar_pct)), relheight=1)
            Label(row, text=value_text, font=("Courier", 10, "bold"),
                  bg=card_bg, fg=C["fg"]).pack(side=LEFT)
            if hint:
                Label(row, text=f"  {hint}", font=("Courier", 9),
                      bg=card_bg, fg=C["fg2"]).pack(side=LEFT)

        scanner_score = pos.get("scanner_score", 0)
        sig_row("Scanner Score", f"{scanner_score}/100",
                bar_pct=scanner_score/100, bar_color=C["blue"],
                hint=pos.get("signals", {}).get("scanner", "")[:50])

        hv_rank = pos.get("hv_rank", 50)
        iv_signal = pos.get("iv_signal", "NEUTRAL")
        iv_color = (C["yellow"] if "EXPANSION" in iv_signal else
                    C["purple"] if "COILING" in iv_signal else C["fg2"])
        sig_row("IV / HV Rank", f"{hv_rank:.0f}th pct",
                bar_pct=hv_rank/100, bar_color=iv_color,
                hint=iv_signal[:35])

        earn_days = pos.get("earnings_days")
        earn_win  = pos.get("earnings_window", "no_data_or_etf")
        earn_color = (C["red"] if earn_win in ("TODAY — avoid or size tiny",
                                                "TOMORROW — gap risk high")
                      else C["yellow"] if earn_win == "pre_earnings_momentum_window"
                      else C["fg2"])
        earn_text  = (f"{earn_days}d to earnings" if earn_days is not None else "ETF / no data")
        sig_row("Earnings", earn_text, bar_color=earn_color, hint=earn_win[:35])

        opt_bias  = pos.get("options_bias", "NEUTRAL")
        cvi       = pos.get("call_vol_oi", 0)
        opt_color = (C["green"] if "BULLISH_SWEEP" in opt_bias else
                     C["red"]   if "BEARISH_SWEEP" in opt_bias else C["fg2"])
        opt_bar   = min(1, cvi / 2.0)
        sig_row("Options Flow", f"{cvi:.2f}× vol/OI",
                bar_pct=opt_bar, bar_color=opt_color,
                hint=opt_bias[:35])

        cat_score = pos.get("catalyst_score", 0)
        cat_color = C["green"] if cat_score >= 4 else C["yellow"] if cat_score >= 2 else C["fg2"]
        sig_row("Catalyst Score", f"{cat_score:+d} pts",
                bar_pct=max(0, cat_score) / 8, bar_color=cat_color)

        # ── Trade thesis ──────────────────────────────────────────────────────
        thesis = pos.get("trade_thesis", "")
        if thesis:
            Frame(body, bg=C["border"], height=1).pack(fill=X, pady=4)
            thesis_color = ROCKET_CLR if is_rocket else C["fg"]
            Label(body, text=f"  \"{thesis}\"",
                  font=("Courier", 10, "italic"), bg=card_bg, fg=thesis_color,
                  wraplength=550, justify=LEFT, anchor=W).pack(fill=X, pady=(0, 4))

        # ── Signals detail ────────────────────────────────────────────────────
        signals = pos.get("signals", {})
        if signals:
            for k, v in signals.items():
                Label(body, text=f"  [{k}] {v}",
                      font=("Courier", 9), bg=card_bg, fg=C["fg2"],
                      anchor=W).pack(fill=X)

        # ── Entry time ────────────────────────────────────────────────────────
        notional = pos.get("notional", 0)
        notional_str = f"${notional:.0f} (ROCKET 40%)" if is_rocket else f"${notional:.0f}"
        Label(card, text=f"  Entered: {pos.get('entry_time', '?')}  |  "
                         f"Notional: {notional_str}  |  "
                         f"Shares: {pos.get('shares', 0):.4f}",
              font=("Courier", 9), bg=card_bg, fg=C["fg2"]).pack(anchor=W, pady=(0, 4))

        return card

    def _refresh_swarm_tab(self):
        try:
            if not self._sw_state_file.exists():
                self.root.after(10_000, self._refresh_swarm_tab)
                return

            state = json.loads(self._sw_state_file.read_text())

            # ── Top bar ───────────────────────────────────────────────────────
            regime = state.get("regime", "—")
            fear   = state.get("fear", "—")
            leader = state.get("sector_leader", "—")
            regime_color = (C["green"] if "BULL" in regime else
                            C["red"]   if "BEAR" in regime else C["yellow"])
            fear_color   = (C["red"]   if "HIGH" in fear else
                            C["green"] if "LOW"  in fear else C["yellow"])
            eq_now   = state.get("portfolio", {}).get("equity", 500.0)
            eq_start = state.get("account_size", 500.0)
            eq_delta = eq_now - eq_start
            eq_sign  = "+" if eq_delta >= 0 else ""
            eq_color = C["green"] if eq_delta >= 0 else C["red"]
            # Rocket slot summary for top bar
            positions   = state.get("portfolio", {}).get("positions", [])
            rocket_pos  = next((p for p in positions if p.get("is_rocket")), None)
            if rocket_pos:
                rp = rocket_pos.get("unrealized_pnl", 0)
                rs = "+" if rp >= 0 else ""
                rocket_label = (f"  ⚡{rocket_pos['symbol']} {rs}${rp:.2f}"
                                f"({rocket_pos.get('unrealized_pct',0):+.1f}%)")
                rocket_color = C["green"] if rp >= 0 else C["red"]
            else:
                rocket_label = "  ⚡ no rocket signal"
                rocket_color = "#ff9500"
            self._sw_regime_lbl.config(text=f"Regime: {regime}", fg=regime_color)
            self._sw_fear_lbl.config(  text=f"Fear: {fear}",     fg=fear_color)
            self._sw_sector_lbl.config(text=rocket_label,         fg=rocket_color)
            session     = state.get("session", "")
            session_tag = f"  [{session}]" if session else ""
            self._sw_time_lbl.config(
                text=f"Equity: ${eq_now:,.2f}  ({eq_sign}{eq_delta:,.2f}){session_tag}  |  {state.get('last_run','—')}",
                fg=eq_color)

            # ── Equity chart (full-width, interactive) ────────────────────────
            history = state.get("equity_history", [])
            self._sw_eq_history = history   # keep in sync for hover handler
            ax = self._sw_eq_ax
            # Preserve zoom/pan view if user has already navigated
            cur_xlim = ax.get_xlim() if ax.lines else None
            ax.cla()
            bg2 = C.get("bg2", "#1a1a2e")
            ax.set_facecolor(bg2)
            ax.tick_params(colors=C["fg2"], labelsize=8, which="both")
            ax.grid(True, color=C["bg3"], linewidth=0.4, linestyle="--", alpha=0.6)
            for sp in ax.spines.values():
                sp.set_color(C["bg3"])

            if len(history) >= 2:
                xs = list(range(len(history)))
                ys = [pt["equity"] for pt in history]
                line_color = C["green"] if ys[-1] >= eq_start else C["red"]
                ax.plot(xs, ys, color=line_color, linewidth=1.8, zorder=3)
                # Colored dots at each cycle
                for i, (x, y) in enumerate(zip(xs, ys)):
                    dot_c = C["green"] if y >= eq_start else C["red"]
                    ax.scatter(x, y, color=dot_c, s=22, zorder=4)
                # Green/red fill bands
                ax.fill_between(xs, ys, eq_start,
                                where=[y >= eq_start for y in ys],
                                alpha=0.18, color=C["green"], zorder=2)
                ax.fill_between(xs, ys, eq_start,
                                where=[y < eq_start for y in ys],
                                alpha=0.18, color=C["red"], zorder=2)
                # $500 baseline
                ax.axhline(eq_start, color=C["fg2"], linewidth=0.8,
                           linestyle="--", alpha=0.6, label=f"${eq_start:.0f} start")
                # X-axis: show time label at every point (rotated), or sample if dense
                step  = max(1, len(history) // 20)   # at most ~20 labels
                ticks = list(range(0, len(history), step))
                if ticks[-1] != len(history) - 1:
                    ticks.append(len(history) - 1)
                ax.set_xticks(ticks)
                ax.set_xticklabels([history[i]["time"][-8:] for i in ticks],
                                   rotation=35, ha="right", fontsize=7,
                                   color=C["fg2"])
                ax.set_xlim(-0.5, len(history) - 0.5)
                ax.yaxis.set_major_formatter(
                    matplotlib.ticker.FormatStrFormatter("$%.2f"))
                # Title with current P&L
                pnl = ys[-1] - eq_start
                sign = "+" if pnl >= 0 else ""
                ax.set_title(f"Swarm Portfolio  ·  ${ys[-1]:.2f}  ({sign}{pnl:.2f})  ·  "
                             f"{len(history)} cycles",
                             color=C["fg2"], fontsize=9, pad=3)
                # Restore zoom if user had panned/zoomed
                if cur_xlim and cur_xlim != (0.0, 1.0):
                    ax.set_xlim(cur_xlim)
            else:
                ax.text(0.5, 0.5, "accumulating data…  (next point in ~30 min)",
                        ha="center", va="center", transform=ax.transAxes,
                        color=C["fg2"], fontsize=10)
                ax.set_xticks([])
                ax.set_yticks([])

            # Re-attach crosshair elements after cla()
            self._sw_eq_annot = ax.annotate(
                "", xy=(0, 0), xytext=(8, 8), textcoords="offset points",
                bbox=dict(boxstyle="round,pad=0.3", fc=C["bg3"], ec=C["fg2"], lw=0.8),
                fontsize=8, color=C["fg"], fontfamily="Courier New")
            self._sw_eq_annot.set_visible(False)
            self._sw_eq_vline = ax.axvline(x=0, color=C["fg2"], linewidth=0.7,
                                           linestyle=":", visible=False)
            self._sw_eq_hline = ax.axhline(y=0, color=C["fg2"], linewidth=0.7,
                                           linestyle=":", visible=False)
            self._sw_eq_canvas.draw_idle()

            # ── Rebuild position cards ────────────────────────────────────────
            for w in self._sw_pos_frame.winfo_children():
                w.destroy()

            positions = state.get("portfolio", {}).get("positions", [])
            if positions:
                for i, pos in enumerate(positions):
                    self._make_position_card(self._sw_pos_frame, pos, i)
            else:
                Label(self._sw_pos_frame, text="  No active positions  —  run portfolio cycle",
                      font=("Courier", 13), bg=C["bg"], fg=C["fg2"]).pack(pady=40)

            # ── Signal board ──────────────────────────────────────────────────
            self._sw_sig_tree.delete(*self._sw_sig_tree.get_children())
            for sig in state.get("signal_board", []):
                ed = sig.get("earnings_days")
                earn_str = f"{ed}d" if ed is not None else "ETF"
                bias = sig.get("options_bias", "NEUTRAL")
                bias_short = (bias.split(" — ")[0] if " — " in bias else bias)[:20]
                iv_sig = sig.get("iv_signal", "NEUTRAL")
                iv_short = iv_sig.split(" — ")[0] if " — " in iv_sig else iv_sig
                iv_short = iv_short[:18]
                tag = "up" if sig.get("direction") == "long" else "dn"
                self._sw_sig_tree.insert("", END, values=(
                    sig["symbol"],
                    sig["score"],
                    sig.get("catalyst_score", 0),
                    sig.get("adx", "—"),
                    f"{sig.get('mom_5day_pct',0):+.1f}%",
                    earn_str,
                    iv_short,
                    bias_short,
                ), tags=(tag,))
            self._sw_sig_tree.tag_configure("up", foreground=C["green"])
            self._sw_sig_tree.tag_configure("dn", foreground=C["red"])

            # ── Activity log ──────────────────────────────────────────────────
            self._sw_log_tree.delete(*self._sw_log_tree.get_children())
            for entry in state.get("swarm_log", [])[:30]:
                action = entry.get("action", "")
                tag = ("enter" if "ENTER" in action else
                       "exit"  if "EXIT"  in action else "neutral")
                self._sw_log_tree.insert("", END, values=(
                    entry.get("time", "")[-8:],
                    action,
                    entry.get("reason", "")[:80],
                ), tags=(tag,))
            self._sw_log_tree.tag_configure("enter",   foreground=C["green"])
            self._sw_log_tree.tag_configure("exit",    foreground=C["red"])
            self._sw_log_tree.tag_configure("neutral", foreground=C["fg2"])

        except Exception:
            pass

        self.root.after(15_000, self._refresh_swarm_tab)

    # ══════════════════════════════════════════════════════════════════════════
    # BACKTEST TAB
    # ══════════════════════════════════════════════════════════════════════════

    def _build_backtest_tab(self, parent):
        import subprocess, sys

        # ── Control bar ───────────────────────────────────────────────────────
        ctrl = Frame(parent, bg=C["bg"])
        ctrl.pack(fill=X, padx=12, pady=10)

        Label(ctrl, text="Days:", font=("Courier", 12), bg=C["bg"], fg=C["fg"]).pack(side=LEFT)
        self._bt_days = Entry(ctrl, width=5, font=("Courier", 12),
                              bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"])
        self._bt_days.insert(0, "30")
        self._bt_days.pack(side=LEFT, padx=(4, 16))

        Label(ctrl, text="Account:", font=("Courier", 12), bg=C["bg"], fg=C["fg"]).pack(side=LEFT)
        self._bt_size = Entry(ctrl, width=8, font=("Courier", 12),
                              bg=C["bg3"], fg=C["fg"], insertbackground=C["fg"])
        self._bt_size.insert(0, "500")
        self._bt_size.pack(side=LEFT, padx=(4, 16))

        Label(ctrl, text="Mode:", font=("Courier", 12), bg=C["bg"], fg=C["fg"]).pack(side=LEFT)
        self._bt_mode = ttk.Combobox(ctrl, values=["Normal", "Aggressive", "YOLO"],
                                     width=12, font=("Courier", 11), state="readonly")
        self._bt_mode.set("Aggressive")
        self._bt_mode.pack(side=LEFT, padx=(4, 16))

        self._bt_run_btn = Button(ctrl, text="  ▶  Run Backtest  ",
                                  font=("Courier", 12, "bold"),
                                  bg=C["green"], fg=C["bg"], relief=FLAT, cursor="hand2",
                                  command=self._run_backtest)
        self._bt_run_btn.pack(side=LEFT, padx=4)

        self._bt_status = Label(ctrl, text="", font=("Courier", 11),
                                bg=C["bg"], fg=C["fg2"])
        self._bt_status.pack(side=LEFT, padx=16)

        # ── Main split: chart (top) | output text (bottom) ────────────────────
        pw = ttk.PanedWindow(parent, orient=VERTICAL)
        pw.pack(fill=BOTH, expand=True, padx=8, pady=4)

        chart_frame = Frame(pw, bg=C["bg"])
        text_frame  = Frame(pw, bg=C["bg"])
        pw.add(chart_frame, weight=2)
        pw.add(text_frame,  weight=3)

        # Equity curve chart
        self._bt_fig = Figure(figsize=(12, 3), facecolor=C["bg"])
        self._bt_ax  = self._bt_fig.add_subplot(111, facecolor=C["bg2"])
        self._bt_ax.tick_params(colors=C["fg2"], labelsize=9)
        for spine in self._bt_ax.spines.values():
            spine.set_edgecolor(C["border"])
        self._bt_ax.set_ylabel("Equity ($)", color=C["fg2"], fontsize=9)
        self._bt_ax.set_title("Run a backtest to see the equity curve",
                               color=C["fg2"], fontsize=10)
        self._bt_canvas = FigureCanvasTkAgg(self._bt_fig, master=chart_frame)
        self._bt_canvas.get_tk_widget().pack(fill=BOTH, expand=True)

        # Output text
        Label(text_frame, text="  Backtest Output",
              font=("Courier", 11, "bold"), bg=C["bg"], fg=C["fg2"]).pack(anchor=W, padx=6)
        txt_scroll = ttk.Scrollbar(text_frame, orient=VERTICAL)
        self._bt_text = Text(text_frame, font=("Courier", 10), bg=C["bg2"], fg=C["fg"],
                             insertbackground=C["fg"], state=DISABLED, wrap=NONE,
                             yscrollcommand=txt_scroll.set)
        txt_scroll.config(command=self._bt_text.yview)
        self._bt_text.pack(side=LEFT, fill=BOTH, expand=True, padx=(6, 0))
        txt_scroll.pack(side=LEFT, fill=Y)

        # Colour tags for output
        self._bt_text.tag_configure("win",    foreground=C["green"])
        self._bt_text.tag_configure("loss",   foreground=C["red"])
        self._bt_text.tag_configure("header", foreground=C["blue"], font=("Courier", 10, "bold"))
        self._bt_text.tag_configure("summary",foreground=C["yellow"], font=("Courier", 10, "bold"))

    def _run_backtest(self):
        import subprocess, sys, re

        try:
            days = int(self._bt_days.get())
            size = float(self._bt_size.get())
            mode = self._bt_mode.get()
        except ValueError:
            self._bt_status.config(text="Invalid input", fg=C["red"])
            return

        self._bt_run_btn.config(state=DISABLED, text="  Running…  ")
        self._bt_status.config(text="Running backtest…", fg=C["yellow"])

        def worker():
            cmd = [sys.executable, "trading_swarm.py",
                   "--backtest", str(days), "--size", str(size)]
            if mode == "Aggressive": cmd.append("--aggressive")
            elif mode == "YOLO":     cmd += ["--aggressive", "--yolo"]

            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                output = proc.stdout + (proc.stderr or "")
            except subprocess.TimeoutExpired:
                output = "ERROR: backtest timed out after 5 minutes."
            except Exception as e:
                output = f"ERROR: {e}"

            self.root.after(0, lambda: self._display_backtest(output, size))

        threading.Thread(target=worker, daemon=True).start()

    def _display_backtest(self, output: str, start_equity: float):
        import re

        self._bt_run_btn.config(state=NORMAL, text="  ▶  Run Backtest  ")

        # ── Parse equity series from output ───────────────────────────────────
        dates, equities = [], []
        for line in output.splitlines():
            m = re.search(r"(\d{4}-\d{2}-\d{2})\s+\S+.*?\$([\d,]+\.\d{2})\s*$", line)
            if m:
                try:
                    dates.append(m.group(1))
                    equities.append(float(m.group(2).replace(",", "")))
                except ValueError:
                    pass

        # ── Update equity chart ───────────────────────────────────────────────
        self._bt_ax.clear()
        self._bt_ax.set_facecolor(C["bg2"])
        self._bt_ax.tick_params(colors=C["fg2"], labelsize=9)
        for spine in self._bt_ax.spines.values():
            spine.set_edgecolor(C["border"])

        if len(equities) > 1:
            xs = list(range(len(equities)))
            final = equities[-1]
            color = C["green"] if final >= start_equity else C["red"]
            self._bt_ax.plot(xs, equities, color=color, linewidth=1.8)
            self._bt_ax.fill_between(xs, start_equity, equities,
                                     alpha=0.15, color=color)
            self._bt_ax.axhline(start_equity, color=C["border"],
                                linestyle="--", linewidth=0.8)
            pct = (final / start_equity - 1) * 100
            self._bt_ax.set_title(
                f"${start_equity:,.0f} → ${final:,.0f}  ({pct:+.1f}%)",
                color=color, fontsize=11, fontweight="bold")
            self._bt_ax.set_ylabel("Equity ($)", color=C["fg2"], fontsize=9)
            self._bt_ax.set_xlabel("Trading Day", color=C["fg2"], fontsize=9)

            # Mark max drawdown point
            peak = equities[0]
            max_dd_idx, max_dd = 0, 0
            for i, e in enumerate(equities):
                peak = max(peak, e)
                dd = (peak - e) / peak
                if dd > max_dd:
                    max_dd = dd
                    max_dd_idx = i
            if max_dd > 0.01:
                self._bt_ax.scatter([max_dd_idx], [equities[max_dd_idx]],
                                    color=C["red"], zorder=5, s=40)
                self._bt_ax.annotate(f"Max DD\n{max_dd*100:.1f}%",
                                     xy=(max_dd_idx, equities[max_dd_idx]),
                                     xytext=(max_dd_idx+0.5, equities[max_dd_idx]*0.985),
                                     fontsize=8, color=C["red"])
        else:
            self._bt_ax.set_title("No trade data parsed", color=C["fg2"], fontsize=10)

        self._bt_canvas.draw()

        # ── Colour-coded output text ──────────────────────────────────────────
        self._bt_text.config(state=NORMAL)
        self._bt_text.delete("1.0", END)
        for line in output.splitlines():
            tag = ""
            if any(h in line for h in ("BACKTEST", "SUMMARY", "═══", "───", "###")):
                tag = "header"
            elif any(h in line for h in ("→", "Win rate", "Best", "Worst", "Avg")):
                tag = "summary"
            elif "✓" in line or re.search(r"\+\d+\.\d+%", line):
                tag = "win"
            elif "✗" in line or re.search(r"-\d+\.\d+%\s", line):
                tag = "loss"
            self._bt_text.insert(END, line + "\n", tag or None)
        self._bt_text.config(state=DISABLED)
        self._bt_text.see("1.0")

        # ── Status summary ────────────────────────────────────────────────────
        import re as _re
        m = _re.search(r"\(([+-][\d.]+%)\)", output)
        if m:
            pct_str = m.group(1)
            color = C["green"] if pct_str.startswith("+") else C["red"]
            self._bt_status.config(text=f"Completed: {pct_str}", fg=color)
        else:
            self._bt_status.config(text="Completed", fg=C["fg2"])


    # ══════════════════════════════════════════════════════════════════════════
    # 20-YEAR WALK-FORWARD ADAPTIVE BACKTEST TAB
    # ══════════════════════════════════════════════════════════════════════════

    def _build_wf20y_tab(self, parent):
        import subprocess, sys

        # ── Control bar ───────────────────────────────────────────────────────
        ctrl = Frame(parent, bg=C["bg"])
        ctrl.pack(fill=X, padx=12, pady=10)

        self._wf_run_btn = Button(
            ctrl, text="  ▶  Run 20Y Walk-Forward  ",
            font=("Courier", 12, "bold"), bg=C["blue"], fg=C["bg"],
            relief=FLAT, cursor="hand2", command=self._run_wf20y)
        self._wf_run_btn.pack(side=LEFT, padx=4)

        self._wf_load_btn = Button(
            ctrl, text="  📂  Load Last Results  ",
            font=("Courier", 12), bg=C["bg3"], fg=C["fg"],
            relief=FLAT, cursor="hand2", command=self._load_wf20y_results)
        self._wf_load_btn.pack(side=LEFT, padx=(8, 4))

        self._wf_status = Label(ctrl, text="No results loaded — press Run or Load.",
                                font=("Courier", 11), bg=C["bg"], fg=C["fg2"])
        self._wf_status.pack(side=LEFT, padx=16)

        # ── Paned: chart top | text bottom ────────────────────────────────────
        pw = ttk.PanedWindow(parent, orient=VERTICAL)
        pw.pack(fill=BOTH, expand=True, padx=8, pady=4)

        chart_frame = Frame(pw, bg=C["bg"])
        text_frame  = Frame(pw, bg=C["bg"])
        pw.add(chart_frame, weight=2)
        pw.add(text_frame,  weight=3)

        # ── Equity chart (strategy vs SPY, regime-shaded) ─────────────────────
        self._wf_fig = Figure(figsize=(14, 4), facecolor=C["bg"])
        self._wf_ax  = self._wf_fig.add_subplot(111, facecolor=C["bg2"])
        self._wf_ax.set_title(
            "20-Year Walk-Forward Adaptive Strategy vs SPY Buy & Hold",
            color=C["fg2"], fontsize=11)
        self._wf_ax.set_ylabel("Equity ($)", color=C["fg2"], fontsize=9)
        self._wf_ax.tick_params(colors=C["fg2"], labelsize=8)
        for sp in self._wf_ax.spines.values():
            sp.set_edgecolor(C["border"])
        self._wf_canvas = FigureCanvasTkAgg(self._wf_fig, master=chart_frame)
        self._wf_canvas.get_tk_widget().pack(fill=BOTH, expand=True)

        # ── Results text ──────────────────────────────────────────────────────
        hdr = Frame(text_frame, bg=C["bg"])
        hdr.pack(fill=X)
        Label(hdr, text="  Results & Analysis", font=("Courier", 11, "bold"),
              bg=C["bg"], fg=C["fg2"]).pack(side=LEFT, padx=6)

        scr = ttk.Scrollbar(text_frame, orient=VERTICAL)
        self._wf_text = Text(text_frame, font=("Courier", 10), bg=C["bg2"], fg=C["fg"],
                             insertbackground=C["fg"], state=DISABLED, wrap=NONE,
                             yscrollcommand=scr.set)
        scr.config(command=self._wf_text.yview)
        self._wf_text.pack(side=LEFT, fill=BOTH, expand=True, padx=(6, 0))
        scr.pack(side=LEFT, fill=Y)

        # colour tags
        self._wf_text.tag_configure("head",  foreground=C["blue"],   font=("Courier", 10, "bold"))
        self._wf_text.tag_configure("good",  foreground=C["green"])
        self._wf_text.tag_configure("bad",   foreground=C["red"])
        self._wf_text.tag_configure("hi",    foreground=C["yellow"], font=("Courier", 10, "bold"))
        self._wf_text.tag_configure("dim",   foreground=C["fg2"])

        # auto-load if results exist
        self.root.after(500, self._load_wf20y_results)

    def _run_wf20y(self):
        import subprocess, sys
        self._wf_run_btn.config(state=DISABLED, text="  Running…  ")
        self._wf_status.config(text="Running 20Y walk-forward (3–5 min)…", fg=C["yellow"])

        def worker():
            try:
                proc = subprocess.run(
                    [sys.executable, "walk_forward_20y.py"],
                    capture_output=True, text=True, timeout=600)
                out = proc.stdout + (proc.stderr or "")
            except subprocess.TimeoutExpired:
                out = "ERROR: timed out after 10 minutes."
            except Exception as e:
                out = f"ERROR: {e}"
            self.root.after(0, lambda: self._finish_wf20y(out))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_wf20y(self, output: str):
        self._wf_run_btn.config(state=NORMAL, text="  ▶  Run 20Y Walk-Forward  ")
        self._wf_write_text(output)
        self._load_wf20y_results()
        self._wf_status.config(text="Run complete — chart updated.", fg=C["green"])

    def _load_wf20y_results(self):
        import csv
        try:
            df = __import__('pandas').read_csv('wf20y_comparison.csv',
                                               index_col=0, parse_dates=True)
        except Exception:
            return   # no results yet
        self._wf_draw_chart(df)

        # Load and display report text if equity-curve CSV exists too
        try:
            with open('wf20y_rebalance_log.csv') as f:
                content = f.read()
            self._wf_status.config(
                text=f"Loaded  {df.index[0].date()} → {df.index[-1].date()}  "
                     f"({len(df)} days)", fg=C["fg2"])
        except Exception:
            pass

    def _wf_draw_chart(self, df):
        import matplotlib.patches as mpatches

        ax = self._wf_ax
        ax.clear()
        ax.set_facecolor(C["bg2"])
        ax.tick_params(colors=C["fg2"], labelsize=8)
        for sp in ax.spines.values():
            sp.set_edgecolor(C["border"])

        dates   = df.index
        strat   = df['strategy_equity']
        spy     = df['spy_equity']
        regimes = df.get('regime', None)

        # Shade regime backgrounds
        if regimes is not None:
            reg_colors = {
                'BULL_TREND': ("#1a3a1a", 0.35),
                'BEAR_TREND': ("#3a1a1a", 0.35),
                'HIGH_VOL':   ("#3a2a00", 0.45),
                'RANGING':    ("#1a1a2a", 0.25),
            }
            prev_reg, prev_date = regimes.iloc[0], dates[0]
            for i in range(1, len(dates)):
                if regimes.iloc[i] != prev_reg or i == len(dates) - 1:
                    col, alpha = reg_colors.get(prev_reg, ("#1a1a1a", 0.2))
                    ax.axvspan(prev_date, dates[i], color=col, alpha=alpha)
                    prev_reg, prev_date = regimes.iloc[i], dates[i]

        ax.plot(dates, strat, color=C["green"], linewidth=1.8, label="Adaptive Strategy")
        ax.plot(dates, spy,   color=C["blue"],  linewidth=1.2, alpha=0.7, label="SPY Buy & Hold")
        ax.axhline(100_000, color=C["border"], linestyle="--", linewidth=0.6)

        # Annotate final values
        final_s = strat.iloc[-1]
        final_b = spy.iloc[-1]
        cagr_s  = (final_s / 100_000) ** (1/20) - 1
        cagr_b  = (final_b / 100_000) ** (1/20) - 1
        ax.set_title(
            f"Strategy: \${final_s:,.0f}  CAGR {cagr_s*100:.1f}%  |  "
            f"SPY B&H: \${final_b:,.0f}  CAGR {cagr_b*100:.1f}%  "
            f"(Sharpe advantage: strategy wins on risk-adjusted)",
            color=C["fg2"], fontsize=10)

        # Legend patches for regimes
        leg_patches = [
            mpatches.Patch(color="#1a3a1a", label="Bull Trend"),
            mpatches.Patch(color="#3a1a1a", label="Bear Trend"),
            mpatches.Patch(color="#3a2a00", label="High Vol"),
            mpatches.Patch(color="#1a1a2a", label="Ranging"),
        ]
        ax.legend(handles=[
            __import__('matplotlib.lines', fromlist=['Line2D']).Line2D(
                [], [], color=C["green"], label=f"Strategy  CAGR {cagr_s*100:.1f}%"),
            __import__('matplotlib.lines', fromlist=['Line2D']).Line2D(
                [], [], color=C["blue"],  label=f"SPY B&H   CAGR {cagr_b*100:.1f}%"),
        ] + leg_patches,
            loc='upper left', fontsize=8,
            facecolor=C["bg2"], edgecolor=C["border"], labelcolor=C["fg2"])

        ax.set_ylabel("Equity ($)", color=C["fg2"], fontsize=9)
        ax.yaxis.set_major_formatter(
            __import__('matplotlib.ticker', fromlist=['FuncFormatter']).FuncFormatter(
                lambda x, _: f"${x/1e3:.0f}k"))
        self._wf_fig.tight_layout()
        self._wf_canvas.draw()

    def _wf_write_text(self, text: str):
        self._wf_text.config(state=NORMAL)
        self._wf_text.delete("1.0", END)
        for line in text.splitlines():
            tag = None
            if "═" in line or "BACKTEST" in line or "RESULTS" in line:
                tag = "head"
            elif "✓" in line or line.strip().startswith("+"):
                tag = "good"
            elif "✗" in line or "ERROR" in line:
                tag = "bad"
            elif "─" in line or "Metric" in line or "Year" in line or "Crisis" in line:
                tag = "dim"
            elif any(k in line for k in ("CAGR", "Sharpe", "Sortino", "Calmar", "Final")):
                tag = "hi"
            self._wf_text.insert(END, line + "\n", tag or None)
        self._wf_text.config(state=DISABLED)
        self._wf_text.see("1.0")

    # ══════════════════════════════════════════════════════════════════════════
    # CRYPTO TAB
    # ══════════════════════════════════════════════════════════════════════════

    def _build_crypto_tab(self, parent):
        CRYPTO_COINS = [
            ("BTC-USD",  "Bitcoin"),
            ("ETH-USD",  "Ethereum"),
            ("SOL-USD",  "Solana"),
            ("DOGE-USD", "Dogecoin"),
            ("AVAX-USD", "Avalanche"),
            ("LINK-USD", "Chainlink"),
            ("ADA-USD",  "Cardano"),
            ("XRP-USD",  "XRP"),
        ]
        AMBER = "#ff9500"

        # ── Status bar ────────────────────────────────────────────────────────
        sb = Frame(parent, bg=C["bg"])
        sb.pack(fill=X, padx=12, pady=(8, 2))

        self._cr_scan_btn = Button(sb, text="  ▶  Scan Now  ",
                                   font=("Courier", 11, "bold"),
                                   bg=C["bg3"], fg=AMBER, relief=FLAT, cursor="hand2",
                                   command=lambda: threading.Thread(
                                       target=self._run_crypto_scan, daemon=True).start())
        self._cr_scan_btn.pack(side=RIGHT, padx=(8, 0))

        self._cr_session_lbl = Label(sb, text="● CRYPTO 24/7",
                                     font=("Courier", 13, "bold"), bg=C["bg"], fg=AMBER)
        self._cr_session_lbl.pack(side=LEFT, padx=(0, 16))

        self._cr_time_lbl = Label(sb, text="Last scan: —",
                                  font=("Courier", 10), bg=C["bg"], fg=C["fg2"])
        self._cr_time_lbl.pack(side=LEFT, padx=(0, 16))

        self._cr_equity_lbl = Label(sb, text="Crypto P&L: —",
                                    font=("Courier", 11, "bold"), bg=C["bg"], fg=C["fg2"])
        self._cr_equity_lbl.pack(side=LEFT)

        # ── Price tiles row ───────────────────────────────────────────────────
        tiles_frame = Frame(parent, bg=C["bg"])
        tiles_frame.pack(fill=X, padx=8, pady=(4, 6))

        self._cr_tiles = {}
        for sym, name in CRYPTO_COINS:
            tile = Frame(tiles_frame, bg="#1a1200",
                         highlightbackground=AMBER, highlightthickness=1)
            tile.pack(side=LEFT, padx=4, pady=2, ipadx=8, ipady=6)

            Label(tile, text=sym.replace("-USD",""),
                  font=("Courier", 11, "bold"), bg="#1a1200", fg=AMBER).pack()
            price_lbl = Label(tile, text="$—", font=("Courier", 13, "bold"),
                              bg="#1a1200", fg=C["fg"])
            price_lbl.pack()
            chg_lbl = Label(tile, text="—%", font=("Courier", 10),
                            bg="#1a1200", fg=C["fg2"])
            chg_lbl.pack()
            self._cr_tiles[sym] = {"price": price_lbl, "chg": chg_lbl, "frame": tile}

        # ── Main split: positions (left) | signals + log (right) ─────────────
        pw = ttk.PanedWindow(parent, orient=HORIZONTAL)
        pw.pack(fill=BOTH, expand=True, padx=8, pady=4)

        left  = Frame(pw, bg=C["bg"])
        right = Frame(pw, bg=C["bg"])
        pw.add(left,  weight=2)
        pw.add(right, weight=3)

        # ── Left: active crypto positions ─────────────────────────────────────
        Label(left, text="  OPEN CRYPTO POSITIONS",
              font=("Courier", 12, "bold"), bg=C["bg"], fg=C["fg2"]).pack(anchor=W, padx=6, pady=(4,2))

        cr_pos_canvas = Canvas(left, bg=C["bg"], highlightthickness=0)
        cr_pos_scroll = ttk.Scrollbar(left, orient=VERTICAL, command=cr_pos_canvas.yview)
        self._cr_pos_frame = Frame(cr_pos_canvas, bg=C["bg"])
        self._cr_pos_frame.bind("<Configure>",
            lambda e: cr_pos_canvas.configure(scrollregion=cr_pos_canvas.bbox("all")))
        cr_pos_canvas.create_window((0, 0), window=self._cr_pos_frame, anchor=NW)
        cr_pos_canvas.configure(yscrollcommand=cr_pos_scroll.set)
        cr_pos_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        cr_pos_scroll.pack(side=RIGHT, fill=Y)

        # ── Right: signal board ───────────────────────────────────────────────
        Label(right, text="  CRYPTO SIGNAL BOARD",
              font=("Courier", 12, "bold"), bg=C["bg"], fg=C["fg2"]).pack(anchor=W, padx=6, pady=(4,2))

        cr_sig_cols = ("Coin","Score","ADX","Mom 5d","Vol Rank","IV Signal","Options Bias")
        self._cr_sig_tree = ttk.Treeview(right, columns=cr_sig_cols,
                                          show="headings", height=10)
        for col, w in zip(cr_sig_cols, (90, 55, 55, 80, 80, 160, 180)):
            self._cr_sig_tree.heading(col, text=col)
            self._cr_sig_tree.column(col, width=w, anchor=CENTER)
        cr_sig_sb = ttk.Scrollbar(right, orient=VERTICAL, command=self._cr_sig_tree.yview)
        self._cr_sig_tree.configure(yscrollcommand=cr_sig_sb.set)
        self._cr_sig_tree.pack(side=LEFT, fill=BOTH, expand=True, padx=(4,0))
        cr_sig_sb.pack(side=LEFT, fill=Y)

        # ── Right: activity log ───────────────────────────────────────────────
        Label(right, text="  CRYPTO ACTIVITY",
              font=("Courier", 12, "bold"), bg=C["bg"], fg=C["fg2"]).pack(anchor=W, padx=6, pady=(8,2))

        cr_log_cols = ("Time", "Action", "Detail")
        self._cr_log_tree = ttk.Treeview(right, columns=cr_log_cols,
                                          show="headings", height=9)
        for col, w in zip(cr_log_cols, (110, 160, 360)):
            self._cr_log_tree.heading(col, text=col)
            self._cr_log_tree.column(col, width=w, anchor=W)
        cr_log_sb = ttk.Scrollbar(right, orient=VERTICAL, command=self._cr_log_tree.yview)
        self._cr_log_tree.configure(yscrollcommand=cr_log_sb.set)
        self._cr_log_tree.pack(side=LEFT, fill=BOTH, expand=True, padx=(4,0), pady=(0,6))
        cr_log_sb.pack(side=LEFT, fill=Y, pady=(0,6))
        self._cr_log_tree.tag_configure("enter",   foreground=C["green"])
        self._cr_log_tree.tag_configure("exit",    foreground=C["red"])
        self._cr_log_tree.tag_configure("neutral", foreground=C["fg2"])

        self._cr_coins = CRYPTO_COINS
        self._cr_prices: dict = {}   # sym -> (price, chg_pct) updated by price thread
        self.root.after(1500, self._refresh_crypto_tab)
        # Background price poller
        threading.Thread(target=self._poll_crypto_prices, daemon=True).start()

    def _run_crypto_scan(self):
        import subprocess
        # Disable button while running
        try:
            self._cr_scan_btn.config(state=DISABLED, text="  ⏳ Scanning…  ")
        except Exception:
            pass
        try:
            subprocess.run(
                ["python3.14", "-u", "trading_swarm.py", "--once",
                 "--size", "100000", "--live"],
                timeout=180, capture_output=False)
        except Exception:
            pass
        try:
            self._cr_scan_btn.config(state=NORMAL, text="  ▶  Scan Now  ")
        except Exception:
            pass

    def _poll_crypto_prices(self):
        """Background thread: refresh crypto prices from yfinance every 45s."""
        import yfinance as yf
        while True:
            try:
                syms = [s for s, _ in self._cr_coins]
                tickers = yf.download(syms, period="2d", progress=False,
                                      auto_adjust=True, group_by="ticker")
                for sym in syms:
                    try:
                        if len(syms) == 1:
                            closes = tickers["Close"].dropna()
                        else:
                            closes = tickers[sym]["Close"].dropna()
                        if len(closes) >= 2:
                            price = float(closes.iloc[-1])
                            prev  = float(closes.iloc[-2])
                            chg   = (price - prev) / prev * 100
                            self._cr_prices[sym] = (price, chg)
                    except Exception:
                        pass
            except Exception:
                pass
            import time; time.sleep(45)

    def _refresh_crypto_tab(self):
        AMBER = "#ff9500"
        try:
            # ── Update price tiles ────────────────────────────────────────────
            for sym, _ in self._cr_coins:
                if sym not in self._cr_tiles: continue
                tile_lbls = self._cr_tiles[sym]
                price_data = self._cr_prices.get(sym)
                if price_data:
                    price, chg = price_data
                    # Format price nicely
                    price_str = (f"${price:,.0f}" if price > 100 else
                                 f"${price:,.3f}" if price < 1 else
                                 f"${price:,.2f}")
                    chg_color  = C["green"] if chg >= 0 else C["red"]
                    chg_str    = f"{chg:+.2f}%"
                    tile_lbls["price"].config(text=price_str)
                    tile_lbls["chg"].config(text=chg_str, fg=chg_color)
                    # Border glows green/red based on direction
                    tile_lbls["frame"].config(highlightbackground=chg_color
                                              if abs(chg) > 1 else AMBER)

            # ── Read swarm state ──────────────────────────────────────────────
            sf = Path("swarm_state.json")
            if not sf.exists():
                self.root.after(15_000, self._refresh_crypto_tab)
                return
            state = json.loads(sf.read_text())

            # Session label
            session = state.get("session", "")
            is_crypto = "CRYPTO" in session or "AFTER" in session or "WEEKEND" in session
            sess_color = AMBER if is_crypto else C["fg2"]
            sess_text  = f"● {session}" if session else "● CRYPTO 24/7"
            self._cr_session_lbl.config(text=sess_text, fg=sess_color)
            self._cr_time_lbl.config(text=f"Last scan: {state.get('last_run','—')}")

            # Crypto P&L from positions
            all_pos    = state.get("portfolio", {}).get("positions", [])
            crypto_pos = [p for p in all_pos if "-USD" in p.get("symbol","")]
            crypto_pnl = sum(p.get("unrealized_pnl", 0) for p in crypto_pos)
            eq_start   = state.get("account_size", 100000)
            real_pnl   = state.get("realized_pnl", 0)
            pnl_color  = C["green"] if crypto_pnl + real_pnl >= 0 else C["red"]
            sign       = "+" if crypto_pnl >= 0 else ""
            self._cr_equity_lbl.config(
                text=f"Crypto P&L: {sign}${crypto_pnl:.2f}  |  Realized: ${real_pnl:+.2f}",
                fg=pnl_color)

            # ── Rebuild position cards ────────────────────────────────────────
            for w in self._cr_pos_frame.winfo_children():
                w.destroy()
            if crypto_pos:
                for pos in crypto_pos:
                    sym       = pos["symbol"]
                    is_rocket = pos.get("is_rocket", False)
                    pnl       = pos.get("unrealized_pnl", 0)
                    pnl_color = C["green"] if pnl >= 0 else C["red"]
                    card_bg   = "#1e1200" if is_rocket else C["bg2"]
                    border    = AMBER     if is_rocket else C.get("border", C["bg3"])
                    card = Frame(self._cr_pos_frame, bg=card_bg,
                                 highlightbackground=border,
                                 highlightthickness=2 if is_rocket else 1)
                    card.pack(fill=X, padx=6, pady=4)
                    hdr = Frame(card, bg=C["bg3"] if not is_rocket else "#2e1e00")
                    hdr.pack(fill=X)
                    if is_rocket:
                        Label(hdr, text=" ⚡ ROCKET 40% ", font=("Courier", 9, "bold"),
                              bg=AMBER, fg="black").pack(side=LEFT)
                    Label(hdr, text=f"  {sym}", font=("Courier", 14, "bold"),
                          bg=hdr["bg"], fg=AMBER if is_rocket else C["fg"]).pack(side=LEFT, pady=4)
                    dir_badge = "▲ LONG" if pos.get("direction")=="long" else "▼ SHORT"
                    dir_color = C["green"] if pos.get("direction")=="long" else C["red"]
                    Label(hdr, text=f" {dir_badge} ", font=("Courier", 10, "bold"),
                          bg=dir_color, fg="white").pack(side=LEFT, padx=8)
                    Label(hdr, text=f"${pnl:+.2f}  ({pos.get('unrealized_pct',0):+.2f}%)",
                          font=("Courier", 11, "bold"), bg=hdr["bg"],
                          fg=pnl_color).pack(side=RIGHT, padx=10)

                    body = Frame(card, bg=card_bg)
                    body.pack(fill=X, padx=10, pady=4)
                    cur   = pos.get("current_price", pos["entry"])
                    entry = pos["entry"]
                    Label(body, text=f"Entry ${entry:.4f}  →  Now ${cur:.4f}  |  "
                                     f"Stop ${pos.get('stop',0):.4f}  |  "
                                     f"Target ${pos.get('target',0):.4f}",
                          font=("Courier", 10), bg=card_bg, fg=C["fg2"]).pack(anchor=W)
                    thesis = pos.get("trade_thesis","")
                    if thesis:
                        Label(body, text=f'  "{thesis}"',
                              font=("Courier", 10, "italic"), bg=card_bg,
                              fg=AMBER if is_rocket else C["fg"],
                              wraplength=400, justify=LEFT).pack(anchor=W, pady=(2,0))
                    Label(body,
                          text=f"Notional: ${pos.get('notional',0):,.0f}  |  "
                               f"Score: {pos.get('scanner_score',0)}  |  "
                               f"Cat: {pos.get('catalyst_score',0)}  |  "
                               f"Entered: {pos.get('entry_time','?')}",
                          font=("Courier", 9), bg=card_bg, fg=C["fg2"]).pack(anchor=W)
            else:
                Label(self._cr_pos_frame,
                      text="  No crypto positions open\n  (bot enters when ADX + momentum signal fires)",
                      font=("Courier", 12), bg=C["bg"], fg=C["fg2"],
                      justify=LEFT).pack(padx=10, pady=30)

            # ── Crypto signal board ───────────────────────────────────────────
            self._cr_sig_tree.delete(*self._cr_sig_tree.get_children())
            crypto_signals = [s for s in state.get("signal_board", [])
                              if "-USD" in s.get("symbol","")]
            for sig in crypto_signals:
                iv  = sig.get("iv_signal","—").split(" — ")[0][:18]
                opt = sig.get("options_bias","—").split(" — ")[0][:20]
                tag = "up" if sig.get("direction")=="long" else "dn"
                self._cr_sig_tree.insert("", END, values=(
                    sig["symbol"],
                    sig["score"],
                    f"{sig.get('adx',0):.0f}",
                    f"{sig.get('mom_5day_pct',0):+.1f}%",
                    f"{sig.get('vol_rank',0):.2f}",
                    iv, opt,
                ), tags=(tag,))
            self._cr_sig_tree.tag_configure("up", foreground=C["green"])
            self._cr_sig_tree.tag_configure("dn", foreground=C["red"])

            # ── Crypto activity log ───────────────────────────────────────────
            self._cr_log_tree.delete(*self._cr_log_tree.get_children())
            for entry in state.get("swarm_log", []):
                action = entry.get("action","")
                reason = entry.get("reason","")
                # show crypto actions OR any action during a crypto session
                sym_in_action = any(c in action for c in
                                    ["BTC","ETH","SOL","DOGE","AVAX","LINK","ADA","XRP",
                                     "AAVE","LTC","ADA","UNI","CRYPTO","RESET"])
                if not sym_in_action: continue
                tag = ("enter"  if "ENTER"  in action else
                       "exit"   if "EXIT"   in action else "neutral")
                self._cr_log_tree.insert("", END, values=(
                    entry.get("time","")[-8:],
                    action[:25],
                    reason[:70],
                ), tags=(tag,))

        except Exception:
            pass
        self.root.after(15_000, self._refresh_crypto_tab)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    root = Tk()
    app  = Dashboard(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.stop_trader(), app.stop_portfolio(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
