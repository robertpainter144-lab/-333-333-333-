#!/usr/bin/env python3
"""Moonshot screener PROTOTYPE — hunts for asymmetric 5-10x+ setups (Robert's
dad's thesis), NOT quality/value. Scores a themed speculative universe on four
axes and ranks. No money, no lane — this just prints a candidate list to
sanity-check the criteria before anything is wired.

Axes (all 0..1, shown per-name so the ranking is legible):
  cap        — smaller is better (micro/small-cap = room to 10x)
  momentum   — early breakout / relative strength / volume surge (axis: growth+breakout)
  base       — beaten-down from 52w high (moonshot from a low base)
  catalyst   — biotech/regulatory/launch-driven theme (weighted highest per Robert)

Universe is a CURATED SEED grouped by theme (thematic-basket axis is baked in).
v2 TODO: data-driven universe (full small-cap list), real fundamentals (rev
growth), crypto low-cap sub-screen, live catalyst calendar (FDA/patent_cliff).
"""
from __future__ import annotations
import sys, math, warnings
warnings.filterwarnings("ignore")
import yfinance as yf
import pandas as pd
import catalyst_calendar

# ── Themed speculative seed universe ────────────────────────────────────────
THEMES = {
    "ai_infra":   ["SOUN","BBAI","AI","INOD","VERI","POET","LAES","GFAI"],
    "nuclear":    ["OKLO","SMR","LEU","UUUU","DNN","UEC","NNE","LTBR"],
    "space":      ["RKLB","ASTS","RDW","PL","LUNR","SPCE","ASTR","BKSY"],
    "quantum":    ["QBTS","RGTI","IONQ","QUBT","ARQQ"],
    "defense_drone":["KTOS","AVAV","ONDS","UMAC","RCAT","PDYN"],
    "robotics":   ["SERV","RR","PATH","SYM","NNDM","MVIS"],
    "biotech":    ["CRSP","BEAM","NTLA","RXRX","VERV","EDIT","DNA","CDTX",
                   "SANA","CRBU","RLAY","ABSI","RXST"],
    "ev_energy":  ["QS","ENVX","AMPX","SLDP","CHPT","RUN","FREY"],
    "crypto_eq":  ["MARA","RIOT","CLSK","BTBT","HUT","CIFR","WULF","IREN","BTDR"],
    "evtol_mob":  ["ACHR","JOBY","EVTL","EH","TSSI"],
}
THEME_OF = {t: th for th, ts in THEMES.items() for t in ts}
TICKERS = sorted(THEME_OF)

# Non-biotech catalyst weighting stays a theme proxy (v2 TODO: real calendars).
CATALYST = {"nuclear":0.8,"space":0.8,"quantum":0.6,"ev_energy":0.6,
            "evtol_mob":0.6,"defense_drone":0.5,"ai_infra":0.5,"robotics":0.5,"crypto_eq":0.4}

# Biotech names get a REAL catalyst score from clinicaltrials.gov (nearest
# upcoming Phase 2/3 readout), keyed by lead-sponsor name.
BIOTECH_SPONSOR = {
    "CRSP":"CRISPR Therapeutics", "BEAM":"Beam Therapeutics", "NTLA":"Intellia Therapeutics",
    "RXRX":"Recursion Pharmaceuticals", "EDIT":"Editas Medicine", "DNA":"Ginkgo Bioworks",
    "SANA":"Sana Biotechnology", "CRBU":"Caribou Biosciences", "RLAY":"Relay Therapeutics",
    "ABSI":"Absci", "RXST":"RxSight",
}

def clamp(x, lo=0.0, hi=1.0): return max(lo, min(hi, x))

def _mcap(t):
    """Market cap, robust across yfinance versions (fast_info key/attr, then info)."""
    try:
        fi = yf.Ticker(t).fast_info
        for k in ("market_cap", "marketCap"):
            try:
                v = fi[k]
                if v: return float(v)
            except Exception:
                pass
        v = getattr(fi, "market_cap", None)
        if v: return float(v)
    except Exception:
        pass
    try:
        v = yf.Ticker(t).info.get("marketCap")
        if v: return float(v)
    except Exception:
        pass
    return None

def cap_score(mc):
    if not mc: return 0.5
    b = mc/1e9
    if b < 0.3: return 1.0
    if b < 1:   return 0.85
    if b < 2:   return 0.70
    if b < 5:   return 0.50
    if b < 10:  return 0.30
    return 0.15

def score_one(t, hist, mc, cat, cat_detail):
    close = hist["Close"].dropna()
    vol = hist["Volume"].dropna()
    if len(close) < 60:
        return None
    px = float(close.iloc[-1])
    sma50 = float(close.tail(50).mean())
    hi52 = float(close.tail(252).max())
    ret3m = float(px/close.iloc[-63] - 1) if len(close) >= 63 else 0.0
    ret1m = float(px/close.iloc[-21] - 1) if len(close) >= 21 else 0.0
    vsurge = float(vol.tail(5).mean() / (vol.tail(60).mean() or 1))
    from_sma50 = (px/sma50 - 1) if sma50 else 0.0
    below_52h = (px/hi52 - 1) if hi52 else 0.0    # negative

    # momentum/breakout: above SMA50, positive RS, volume surge, but penalize over-extension
    mo = 0.0
    mo += 0.35 if px > sma50 else 0.0
    mo += clamp(ret3m/1.0) * 0.30          # +100% 3mo -> full
    mo += clamp((vsurge-1)/2) * 0.25       # 3x surge -> full
    mo += clamp(ret1m/0.4) * 0.10
    if from_sma50 > 0.5: mo -= 0.20        # >50% above SMA50 = extended
    mo = clamp(mo)

    # base/beaten-down: deeper below 52w high = more room (a low base)
    base = clamp((-below_52h - 0.4) / 0.5)  # -40%->0, -90%->1.0

    cap = cap_score(mc)

    composite = 0.25*cap + 0.30*mo + 0.20*base + 0.25*cat
    return {"t":t,"theme":THEME_OF[t],"px":px,"mc":mc,"from_sma50":from_sma50,
            "below_52h":below_52h,"ret3m":ret3m,"vsurge":vsurge,
            "cap":cap,"mo":mo,"base":base,"cat":cat,"cat_detail":cat_detail,
            "score":composite}

def screen() -> list[dict]:
    """Score the universe and return rows sorted by score DESC.

    The contract turbo_arm.py and moonshot_lane.py depend on (both call
    moonshot_screen.screen()). This was the missing refactor the lane's
    docstring warned about — its absence had the moonshot paper lane AND
    the $50 live turbo arm crashing on every entry leg since 2026-08-09
    (found by the 2026-08-12 stack audit).
    """
    print(f"Downloading 1y history for {len(TICKERS)} names…", file=sys.stderr)
    data = yf.download(TICKERS, period="1y", interval="1d",
                       group_by="ticker", auto_adjust=True, progress=False, threads=True)
    rows = []
    for t in TICKERS:
        try:
            hist = data[t] if isinstance(data.columns, pd.MultiIndex) else data
            if hist["Close"].dropna().empty: continue
            mc = _mcap(t)
            if t in BIOTECH_SPONSOR:
                cat, cat_detail = catalyst_calendar.catalyst_score(BIOTECH_SPONSOR[t])
            else:
                cat, cat_detail = CATALYST.get(THEME_OF[t], 0.5), f"theme:{THEME_OF[t]}"
            r = score_one(t, hist, mc, cat, cat_detail)
            if r: rows.append(r)
        except Exception as e:
            print(f"  skip {t}: {e}", file=sys.stderr)
    rows.sort(key=lambda r: -r["score"])
    return rows


def main():
    rows = screen()

    print(f"\nMOONSHOT CANDIDATES — top 25 of {len(rows)} scored "
          f"(seed universe {len(TICKERS)} names)\n")
    hdr = f"{'#':>2} {'TICK':<6}{'THEME':<14}{'PX':>8}{'MCAP':>8}  {'3moRS':>7}{'volX':>6}{'from52h':>8}  {'cap':>4}{'mo':>4}{'base':>5}{'cat':>4}  {'SCORE':>6}"
    print(hdr); print("-"*len(hdr))
    for i, r in enumerate(rows[:25], 1):
        mc = f"{r['mc']/1e9:.2f}B" if r['mc'] else "  ?"
        print(f"{i:>2} {r['t']:<6}{r['theme']:<14}{r['px']:>8.2f}{mc:>8}  "
              f"{r['ret3m']*100:>6.0f}%{r['vsurge']:>5.1f}x{r['below_52h']*100:>7.0f}%  "
              f"{r['cap']:>4.2f}{r['mo']:>4.2f}{r['base']:>5.2f}{r['cat']:>4.2f}  {r['score']:>6.3f}")

    print("\nCatalyst detail (biotech = real clinicaltrials.gov readouts):")
    for r in rows[:25]:
        if r["theme"] == "biotech":
            print(f"  {r['t']:<6} {r['cat_detail']}")

if __name__ == "__main__":
    main()
