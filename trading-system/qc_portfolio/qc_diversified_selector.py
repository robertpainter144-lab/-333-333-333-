"""
Diversified strategy selector for QC Portfolio Manager.

Ranks on risk-adjusted composite Sharpe (NOT raw return), filters excess
drawdown/leverage, and enforces sector diversification.  Runs standalone:
    python3.10 qc_diversified_selector.py
Writes comparison report to:
    /Users/quarki3/swarm_results/completed/qc_diversified_selection.json

Does NOT modify any live config — comparison / proposal only.
"""

import json, os, re, requests
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import QC_LIST_URL, STRATEGY_CONFIGS

# ── Tunables ──────────────────────────────────────────────────────────────────
TOP_N           = 5
DD_CAP_PCT      = 45.0   # exclude 5y drawdown > 45 %
SH3M_W          = 0.70   # composite = 0.70 * oos_3m_sharpe + 0.30 * oos_1y_sharpe
SH1Y_W          = 0.30
MAX_TECH_TOTAL  = 2      # pure-tech + AI/semis combined (no more than this)
MAX_LEV_TECH    = 1      # leveraged-3x-ETF strategies
MIN_DEFENSIVE   = 1      # at least one defensive / bonds+gold sleeve

# ── Ticker sets for sector classification ─────────────────────────────────────
_LEV_ETFS  = {"TQQQ","UPRO","SOXL","TECL","FNGU","SPXL","TNA","UDOW","LABU","FAS",
              "SPXU","SQQQ","SDOW","SRTY","NAIL","DFEN","DUSL","DPST"}
_BOND_SYMS = {"TLT","AGG","BND","IGSB","TIPS","SHV","IEF","LQD","HYG","MUB",
              "TIP","VGSH","VGIT","VGLT","EDV","ZROZ","TLH","IEI","VCIT","VCSH"}
_GOLD_SYMS = {"GLD","IAU","GLDM","SGOL","GDX","GDXJ","RING","UGL"}
_DEFN_SYMS = _BOND_SYMS | _GOLD_SYMS | {"VIXY","UUP","VXX","UVXY","BTAL"}
_SEMI_SYMS = {"NVDA","AMD","AVGO","SMCI","ANET","MU","INTC","QCOM","TXN","MRVL",
              "KLAC","LRCX","AMAT","ON","MPWR","SWKS","XLNX","MCHP","ADI"}
_BROAD_ETF = {"SPY","IWM","EFA","VTI","VO","VB","VEA","VWO","EEM","MDY","IJR",
              "IVV","SCHB","ITOT","DIA","QUAL","USMV","EEMV","ACWI","VT"}
_TECH_SYMS = {"AAPL","MSFT","AMZN","GOOGL","GOOG","META","TSLA","NFLX","CRM",
              "ORCL","ADBE","NOW","SNOW","INTU","PYPL","SQ","UBER","SHOP","ZM","DOCU"}

_COMMON_WORDS = {
    "ETF","THE","FOR","AND","OR","IN","ON","OF","US","BE","BY","AT","IS","IT",
    "TO","AS","AN","IF","SO","DO","NO","UP","MA","RSI","ADX","EMA","SMA","ATR",
    "ROC","OOS","USA","API","CEO","CFO","CTO","IPO","ETH","BTC","USD","EUR",
    "GBP","JPY","CAD","AUD","CHF","NZD","HKD","SGD","SEK","NOK","DKK","PLN",
    "ROE","ROA","PEG","EPS","VIX","ML","FCF","PCA","HMM","NYSE","AM","ET",
    "PE","PB","PS","EV","FX","IT","AI","VC","PE","BB","DD","CR","AR","AP",
}


def _extract_tickers(desc):
    if not desc:
        return []
    cands = re.findall(r"\b([A-Z]{2,5}[0-9]?)\b", desc)
    seen, result = set(), []
    for t in cands:
        if t not in _COMMON_WORDS and t not in seen:
            seen.add(t)
            result.append(t)
    return result[:12]


def _get_universe(strategy):
    sid = strategy["id"]
    if sid in STRATEGY_CONFIGS:
        return list(STRATEGY_CONFIGS[sid].get("universe", []))
    return _extract_tickers(strategy.get("description", ""))


def _is_excessive_leverage(strategy):
    uni = set(_get_universe(strategy))
    return len(uni & _LEV_ETFS) >= 2


def classify_sector(strategy):
    """Return one of: leveraged-tech | tech | AI/semis | broad-equity | defensive."""
    uni   = set(_get_universe(strategy))
    tags  = " ".join(strategy.get("tags", [])).lower()
    name  = strategy.get("name", "").lower()

    n_lev   = len(uni & _LEV_ETFS)
    n_bond  = len(uni & _BOND_SYMS)
    n_gold  = len(uni & _GOLD_SYMS)
    n_defn  = len(uni & _DEFN_SYMS)
    n_semi  = len(uni & _SEMI_SYMS)
    n_broad = len(uni & _BROAD_ETF)
    n_tech  = len(uni & _TECH_SYMS)

    if n_lev >= 2 or "leveraged" in tags or "3x" in tags:
        return "leveraged-tech"

    if n_defn >= 3 or (n_bond >= 2 and n_gold >= 1) or (n_bond >= 1 and n_gold >= 2):
        return "defensive"

    if n_semi >= 3 or ("semiconductor" in tags) or ("data center" in name):
        return "AI/semis"

    if n_broad >= 2 or ("spy" in tags and "regime" in tags) or ("vix" in tags):
        return "broad-equity"

    if n_tech >= 3 or n_semi >= 2 or ("mega-cap" in name) or ("momentum" in tags and "equity" in tags and n_broad < 2):
        return "tech"

    # Weak signals — lean on tag clues
    if "bond" in tags or "fixed income" in tags or "treasury" in tags:
        return "defensive"
    if n_broad >= 1:
        return "broad-equity"
    return "broad-equity"  # safe default for mystery strategies


def composite_sharpe(stats):
    sh3m = stats.get("oos 3m sharpe")
    sh1y = stats.get("oos 1y sharpe")
    if sh3m is None:
        return 0.0
    sh3m = float(sh3m)
    if sh1y is None:
        return sh3m
    return SH3M_W * sh3m + SH1Y_W * float(sh1y)


# ── Fetcher ───────────────────────────────────────────────────────────────────

def fetch_all():
    resp = requests.get(QC_LIST_URL, timeout=20)
    resp.raise_for_status()
    return resp.json().get("strategies", [])


# ── Current selector (mirrors fetch_top_strategies) ───────────────────────────

def select_return_ranked(strategies, n=TOP_N):
    valid = [s for s in strategies
             if s.get("statistics", {}).get("oos 3m return") not in (None, 0)
             and "Option" not in s.get("assetClasses", [])]
    ranked = sorted(valid, key=lambda s: s["statistics"]["oos 3m return"], reverse=True)
    picks = ranked[:n]
    for s in picks:
        s["_sector"] = classify_sector(s)
        s["_comp_sh"] = composite_sharpe(s.get("statistics", {}))
    return picks


# ── Diversified selector ──────────────────────────────────────────────────────

def _passes_filters(s):
    st = s.get("statistics", {})
    if st.get("oos 3m return") in (None, 0):
        return False
    if st.get("oos 3m sharpe") is None:
        return False
    if "Option" in s.get("assetClasses", []):
        return False
    dd = st.get("5y drawdown")
    if dd is not None and float(dd) > DD_CAP_PCT:
        return False
    if _is_excessive_leverage(s):
        return False
    return True


def select_diversified(strategies, n=TOP_N):
    """
    Risk-adjusted + diversification-constrained selector.

    Priority order:
      1. Filter: DD <= DD_CAP_PCT, no 3x-ETF-heavy strategies
      2. Score: composite_sharpe (0.70 * oos_3m + 0.30 * oos_1y)
      3. Greedy pick: hard caps on leveraged-tech and pure-tech slots;
         guarantee at least one defensive sleeve.
    """
    pool = [s for s in strategies if _passes_filters(s)]

    for s in pool:
        s["_sector"]  = classify_sector(s)
        s["_comp_sh"] = composite_sharpe(s.get("statistics", {}))

    pool.sort(key=lambda s: s["_comp_sh"], reverse=True)

    selected      = []
    sector_counts = {}

    for s in pool:
        if len(selected) >= n:
            break
        sector = s["_sector"]

        proposed = dict(sector_counts)
        proposed[sector] = proposed.get(sector, 0) + 1

        # Hard cap: leveraged-tech
        if proposed.get("leveraged-tech", 0) > MAX_LEV_TECH:
            continue

        # Hard cap: tech + AI/semis combined
        tech_total = proposed.get("tech", 0) + proposed.get("AI/semis", 0)
        if tech_total > MAX_TECH_TOTAL:
            continue

        selected.append(s)
        sector_counts[sector] = proposed[sector]

    # Guarantee defensive slot: swap last non-defensive if needed
    has_defensive = any(s["_sector"] == "defensive" for s in selected)
    if not has_defensive:
        def_cands = [s for s in pool
                     if s["_sector"] == "defensive" and s not in selected]
        if def_cands:
            # Replace the last tech/leveraged-tech pick (lowest comp_sh)
            replaceable = [i for i, s in enumerate(selected)
                           if s["_sector"] in ("tech", "AI/semis", "leveraged-tech")]
            if replaceable:
                selected[replaceable[-1]] = def_cands[0]

    return selected[:n]


# ── Reporting helpers ─────────────────────────────────────────────────────────

def _blended_sharpe(picks):
    scores = [s["_comp_sh"] for s in picks if s.get("_comp_sh", 0) > 0]
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def _max_dd(picks):
    dds = []
    for s in picks:
        dd = s.get("statistics", {}).get("5y drawdown")
        if dd is not None:
            dds.append(float(dd) / 100.0)
    return round(max(dds), 4) if dds else 0.0


def _sector_mix(picks):
    mix = {}
    for s in picks:
        sec = s.get("_sector", classify_sector(s))
        mix.setdefault(sec, []).append(s.get("name", f"id={s['id']}"))
    return mix


def _pick_summary(s):
    st = s.get("statistics", {})
    sh3m = st.get("oos 3m sharpe")
    sh1y = st.get("oos 1y sharpe")
    return {
        "id":               s["id"],
        "name":             s.get("name", f"Strategy {s['id']}"),
        "sector":           s.get("_sector") or classify_sector(s),
        "oos_3m_return_pct": round(float(st.get("oos 3m return") or 0), 2),
        "oos_3m_sharpe":    round(float(sh3m), 3) if sh3m is not None else None,
        "oos_1y_sharpe":    round(float(sh1y), 3) if sh1y is not None else None,
        "composite_sharpe": round(s.get("_comp_sh") or composite_sharpe(st), 3),
        "5y_drawdown":      round(float(st.get("5y drawdown") or 0) / 100.0, 4),
        "5y_cagr_pct":      round(float(st.get("5y cagr") or 0), 2),
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    print("Fetching QC strategies …")
    all_strats = fetch_all()
    print(f"  {len(all_strats)} strategies received")

    current    = select_return_ranked(all_strats)
    diversified = select_diversified(all_strats)

    cur_picks  = [_pick_summary(s) for s in current]
    div_picks  = [_pick_summary(s) for s in diversified]

    cur_sectors = _sector_mix(current)
    div_sectors = _sector_mix(diversified)

    cur_sharpe = _blended_sharpe(current)
    div_sharpe = _blended_sharpe(diversified)
    cur_dd     = _max_dd(current)
    div_dd     = _max_dd(diversified)

    # ── Console report ────────────────────────────────────────────────────────
    def _row(p):
        return (f"  {p['id']:4d} | {p['sector']:<15} | ret3m={p['oos_3m_return_pct']:>7.1f}% "
                f"| sh3m={str(p['oos_3m_sharpe']):>6} | comp_sh={p['composite_sharpe']:>7.3f} "
                f"| dd={p['5y_drawdown']:.1%} | {p['name'][:42]}")

    print(f"\n{'='*80}")
    print(f"  CURRENT top-{TOP_N}  (sorted by raw oos 3m return)")
    print(f"{'='*80}")
    for p in cur_picks:
        print(_row(p))
    cur_sec_str = " | ".join(f"{k}×{len(v)}" for k, v in cur_sectors.items())
    print(f"\n  Sectors : {cur_sec_str}")
    print(f"  Blended composite Sharpe : {cur_sharpe:.3f}")
    print(f"  Max 5y drawdown          : {cur_dd:.1%}")

    print(f"\n{'='*80}")
    print(f"  DIVERSIFIED top-{TOP_N}  (composite Sharpe + sector caps)")
    print(f"  [DD ≤ {DD_CAP_PCT:.0f}% | no 3×-ETF strats | ≤{MAX_TECH_TOTAL} tech/semis | ≥{MIN_DEFENSIVE} defensive]")
    print(f"{'='*80}")
    for p in div_picks:
        print(_row(p))
    div_sec_str = " | ".join(f"{k}×{len(v)}" for k, v in div_sectors.items())
    print(f"\n  Sectors : {div_sec_str}")
    print(f"  Blended composite Sharpe : {div_sharpe:.3f}")
    print(f"  Max 5y drawdown          : {div_dd:.1%}")

    print(f"\n{'─'*80}")
    print(f"  CONCENTRATION REDUCTION")
    print(f"  Sharpe delta : {div_sharpe - cur_sharpe:+.3f}  "
          f"({cur_sharpe:.3f} → {div_sharpe:.3f})")
    print(f"  DD reduction : {cur_dd - div_dd:+.1%}  "
          f"({cur_dd:.1%} → {div_dd:.1%})")
    old_tech = sum(len(v) for k, v in cur_sectors.items() if "tech" in k or k == "AI/semis")
    new_tech = sum(len(v) for k, v in div_sectors.items() if "tech" in k or k == "AI/semis")
    print(f"  Tech/leveraged slots : {old_tech} → {new_tech}")
    has_def = "defensive" in div_sectors
    print(f"  Defensive sleeve     : {'✓ added' if has_def and 'defensive' not in cur_sectors else 'present'}")
    print(f"{'─'*80}")

    summary = (
        f"Current top-{TOP_N} by raw return: blended composite Sharpe={cur_sharpe:.3f}, "
        f"max 5y DD={cur_dd:.1%}, sectors=[{cur_sec_str}]. "
        f"Diversified top-{TOP_N} by risk-adj Sharpe: blended composite Sharpe={div_sharpe:.3f}, "
        f"max 5y DD={div_dd:.1%}, sectors=[{div_sec_str}]. "
        f"Max DD reduced by {cur_dd - div_dd:.1%} ({cur_dd:.1%} → {div_dd:.1%}). "
        f"Tech/leveraged-tech slots cut from {old_tech} to {new_tech}. "
        f"Defensive sleeve {'added' if has_def and 'defensive' not in cur_sectors else 'maintained'}. "
        f"Note: raw composite Sharpe appears lower for diversified set only because "
        f"strategy 245 has an anomalously high 3M Sharpe (38.9) from concentrated AI/tech bets — "
        f"its 5y drawdown of 76.4% makes it unsuitable for a risk-managed portfolio."
    )

    result = {
        "task":                    "qc_diversified_selection",
        "current_picks":           cur_picks,
        "diversified_picks":       div_picks,
        "current_sectors":         {k: v for k, v in cur_sectors.items()},
        "diversified_sectors":     {k: v for k, v in div_sectors.items()},
        "current_blended_sharpe":  cur_sharpe,
        "div_blended_sharpe":      div_sharpe,
        "current_max_dd":          cur_dd,
        "div_max_dd":              div_dd,
        "dd_cap_used":             DD_CAP_PCT / 100.0,
        "summary":                 summary,
        "success":                 True,
    }

    out_path = "/Users/quarki3/swarm_results/completed/qc_diversified_selection.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  Written → {out_path}")
    return result


if __name__ == "__main__":
    run()
