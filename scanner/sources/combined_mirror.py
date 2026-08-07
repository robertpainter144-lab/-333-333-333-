"""Combined-Ensemble → Alpaca Mirror.

Blends four sources into a single target composition:
  25% QC (weighted QC portfolio manager)
  25% Insiders (top-3 tickers from insider_bot leaderboard signals)
  25% Patents (top-3 tradable picks from patent_cliff portfolio_picks)
  25% Whales (top-3 Alpaca-tradable whale confluence hits)

Then rebalances Alpaca to match. Same whitelist-only guardrails as qc_mirror —
never sells anything outside the target set.
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

QC_STATE     = Path("/Users/quarki3/qc_portfolio/portfolio_state.json")
INSIDER_SIG  = Path("/Users/quarki3/insider_bot/signals.json")
PATENT_DIR   = Path("/Users/quarki3/patent_cliff/output")
SIGNALS_FEED = STATEDIR / "signals.jsonl"

STATE  = STATEDIR / "combined_mirror_state.json"
LEDGER = STATEDIR / "combined_mirror_ledger.jsonl"
# Every tick, append (ts, equity) to the equity_history CSV that the dashboard's
# /api/equity/alpaca_live endpoint reads. Without this, the portfolio value
# chart in Book is empty.
EQUITY_CSV = Path("/Users/quarki3/alpaca_live_arm_state/equity.csv")


def _append_equity_history(ts: int, equity: float) -> None:
    """Write an equity snapshot to the CSV the dashboard reads for the chart."""
    try:
        EQUITY_CSV.parent.mkdir(parents=True, exist_ok=True)
        header_needed = not EQUITY_CSV.exists() or EQUITY_CSV.stat().st_size == 0
        with EQUITY_CSV.open("a") as f:
            if header_needed:
                f.write("ts,equity\n")
            f.write(f"{ts},{equity}\n")
    except Exception:
        pass

WEIGHT_PER_SOURCE = 0.25       # default equal blend (used if governor hasn't published)

# Auto-switch integration: the strategy governor may publish recommended
# weights per source in state/governor_weights.json. combined_mirror reads
# that file on every tick and uses those weights instead of the equal blend.
GOVERNOR_WEIGHTS = STATEDIR / "governor_weights.json"


def _governor_weights() -> dict[str, float] | None:
    """Return per-source weights recommended by the strategy governor, or None
    if no fresh recommendation exists. Ignores stale (>4h old) files as a
    safety fallback to the equal blend."""
    if not GOVERNOR_WEIGHTS.exists():
        return None
    try:
        d = json.loads(GOVERNOR_WEIGHTS.read_text())
        age_s = int(time.time()) - d.get("ts", 0)
        if age_s > 4 * 3600:
            return None
        w = d.get("weights") or {}
        # Only keep sources we know how to build a composition from
        wanted = {"qc", "patent", "insider", "whale", "value", "autopilot", "congress"}
        w = {k: float(v) for k, v in w.items() if k in wanted}
        return w if w else None
    except Exception:
        return None
PAPER_BANKROLL_USD = 50.0

# Per-source mode: COMBINED_MIRROR_MODE overrides the global ALPACA_MODE so we
# can flip THIS source to live without dragging prop_desk / other paper sources
# with us. Defaults to paper on both.
ALPACA_MODE   = os.environ.get("COMBINED_MIRROR_MODE",
                                os.environ.get("ALPACA_MODE", "paper"))
ALPACA_BASE   = ("https://api.alpaca.markets" if ALPACA_MODE == "live"
                 else "https://paper-api.alpaca.markets")
ALPACA_KEY    = (os.environ.get("ALPACA_LIVE_KEY") if ALPACA_MODE == "live"
                 else os.environ.get("ALPACA_PAPER_KEY", ""))
ALPACA_SECRET = (os.environ.get("ALPACA_LIVE_SECRET") if ALPACA_MODE == "live"
                 else os.environ.get("ALPACA_PAPER_SECRET", ""))

MIN_ORDER_USD = 1.00
REBALANCE_THRESHOLD_USD = 0.50

# ── Daily circuit breaker (2026-07-31, Robert: auto pull-out on dip days) ──
# If today's live P/L (equity vs yesterday's close) breaches the freeze
# threshold, ALL trading halts for the rest of the session — no buys, no
# sells, no rotations. Positions are held, not dumped at the low; the freeze
# clears automatically at the next session's fresh last_equity. Config is
# hot-read every tick from state/risk_config.json.
RISK_CONFIG = STATEDIR / "risk_config.json"
RISK_STATE = STATEDIR / "risk_state.json"


def _risk_cfg() -> dict:
    try:
        return json.loads(RISK_CONFIG.read_text())
    except Exception:
        return {}


def _risk_freeze_pct() -> float:
    cfg = _risk_cfg()
    if cfg.get("enabled") is False:
        return -1e9
    try:
        return float(cfg.get("daily_freeze_pct", -1.5))
    except Exception:
        return -1.5


# ── Profit-taking audit outcome (2026-08-02 workflow) ──────────────────
# The 7/13–7/31 book showed rebalance sells landing at trailing-range lows
# (median fill: 12th percentile of the prior-48h range) and full exits being
# rebought days later at worse prices (TEVA sold 7/27, +11.6% pop 7/29,
# rebought at the top: -$0.44). Backtested TP/trailing rules did NOT survive
# adversarial verification (edge was one AMD event), so profit-taking runs in
# SHADOW mode only (pt_shadow.jsonl, zero orders) until the shadow ledger
# proves it. The two guards below are structural — they only re-time orders
# the rebalancer would place anyway.
PT_SHADOW = STATEDIR / "pt_shadow.jsonl"


def _pt_cfg() -> dict:
    d = {"mode": "shadow", "arm_pct": 1.0, "giveback_pct": 0.5,
         "min_notional_usd": 1.0, "rebuy_suppress_h": 24}
    d.update(_risk_cfg().get("profit_take") or {})
    return d


def _guard_cfg() -> dict:
    d = {"min_dwell_h": 24, "reentry_window_h": 48, "reentry_premium_pct": 1.0}
    d.update(_risk_cfg().get("sell_guards") or {})
    return d


# Per-symbol eligibility gate — SHADOW ONLY (round-2 audit 2026-08-02: +$0.36
# backtest delta was 103% one blocked RDY order; clustered P(<=0)=0.385; but
# the 7/29 RDY 8-entries-in-6h rebuy spam is a live-regime pathology no other
# guard catches). Logs would-block events to elig_shadow.jsonl, never touches
# order flow. Promotion bar: >=3 distinct symbols, >=10 events not covered by
# another guard, counterfactual positive ex-best-event, then a paper A/B arm.
ELIG_SHADOW = STATEDIR / "elig_shadow.jsonl"


def _elig_cfg() -> dict:
    d = {"mode": "shadow", "n_trips": 5, "block_days": 5,
         "loss_pct_notional": 0.5, "loss_usd": 0.10}
    d.update(_risk_cfg().get("elig_gate") or {})
    return d


def _latest_px(sym: str) -> float | None:
    """Best-effort latest trade price (IEX feed) for a symbol we don't hold.
    None on any failure — guards that need it fail open."""
    try:
        r = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/trades/latest",
                         params={"feed": "iex"}, headers=_headers(), timeout=8)
        if r.ok:
            return float(r.json()["trade"]["p"])
    except Exception:
        pass
    return None


def _ntfy(msg: str) -> None:
    try:
        topic = None
        for ln in (Path.home() / "ntfy.env").read_text().splitlines():
            if ln.startswith("NTFY_TOPIC="):
                topic = ln.split("=", 1)[1].strip()
        if topic:
            requests.post(f"https://ntfy.sh/{topic}", data=msg.encode(),
                          headers={"Title": "Circuit breaker", "Priority": "4",
                                   "Tags": "warning"}, timeout=10)
    except Exception:
        pass
BUY_COOLDOWN_S = 30 * 60       # back off a symbol's buys after a 403


def _headers():
    return {"APCA-API-KEY-ID": ALPACA_KEY or "",
            "APCA-API-SECRET-KEY": ALPACA_SECRET or "",
            "Content-Type": "application/json"}


# ─── Per-source composition builders ───────────────────────────────────
def qc_composition() -> dict[str, float]:
    if not QC_STATE.exists():
        return {}
    try:
        s = json.loads(QC_STATE.read_text())
    except Exception:
        return {}
    weights = s.get("weights", [])
    strategies = s.get("strategies", [])
    allocations = s.get("allocations", {})
    tgt: dict[str, float] = defaultdict(float)
    for i, strat in enumerate(strategies):
        w = weights[i] if i < len(weights) else 0.0
        sid = str(strat.get("id"))
        for ticker, frac in allocations.get(sid, {}).items():
            tgt[ticker] += w * frac
    total = sum(tgt.values())
    return {t: v / total for t, v in tgt.items()} if total > 0 else {}


def insider_composition(top_n: int = 3) -> dict[str, float]:
    if not INSIDER_SIG.exists():
        return {}
    try:
        d = json.loads(INSIDER_SIG.read_text())
        sigs = d.get("signals", [])
    except Exception:
        return {}
    tickers = [s.get("ticker") for s in sigs if s.get("ticker")][:top_n]
    if not tickers:
        return {}
    w = 1.0 / len(tickers)
    return {t: w for t in tickers}


def patent_composition(top_n: int = 3) -> dict[str, float]:
    files = sorted(PATENT_DIR.glob("portfolio_picks_*.csv")) if PATENT_DIR.exists() else []
    if not files:
        return {}
    try:
        with files[-1].open() as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return {}
    tradable = [r["ticker"] for r in rows
                if r.get("us_listed_alpaca_tradable") == "True"][:top_n]
    if not tradable:
        return {}
    w = 1.0 / len(tradable)
    return {t: w for t in tradable}


# Alpaca-tradable equity symbols the whale scanner tends to produce. Whales in
# crypto space are excluded from the equity mirror; we just use any equity
# tickers from the whale confluence list if they appear.
WHALE_EQUITY_UNIVERSE = {"NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOGL", "TSLA",
                         "AMD", "AVGO", "PLTR", "AAPL", "SPY", "QQQ"}


def whale_composition(top_n: int = 3, window_s: int = 60 * 60) -> dict[str, float]:
    """Scan scanner signals feed for whale-tagged Alpaca-tradable equity tickers.
    Real whale tracker is crypto-heavy, so this often returns empty — fallback
    is a mild equal-weight bet on SPY as placeholder."""
    if not SIGNALS_FEED.exists():
        return {}
    now = int(time.time())
    cutoff = now - window_s
    counts: dict[str, float] = defaultdict(float)
    try:
        for ln in SIGNALS_FEED.read_text().splitlines()[-2000:]:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get("ts", 0) < cutoff:
                continue
            src = r.get("source", "")
            sym = (r.get("symbol") or "").upper()
            if src in ("evm", "cex", "bridge") and sym in WHALE_EQUITY_UNIVERSE:
                counts[sym] += 1
    except Exception:
        pass
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    if not top:
        return {}
    total = sum(v for _, v in top)
    return {s: v / total for s, v in top}


def _congress_composition() -> dict[str, float]:
    """Pelosi PTR composition, maintained by congress_feed."""
    try:
        d = json.loads((STATEDIR / "congress_holdings.json").read_text())
        h = {str(t).upper(): float(w) for t, w in (d.get("holdings") or {}).items()
             if float(w) > 0}
        total = sum(h.values())
        return {t: w / total for t, w in h.items()} if total > 0 else {}
    except Exception:
        return {}


def _autopilot_composition() -> dict[str, float]:
    """Autopilot 'Actively Managed' holdings, maintained by autopilot_mirror."""
    try:
        from sources import autopilot_mirror
        return autopilot_mirror.composition()
    except Exception:
        return {}


def _value_composition(top_n: int = 3) -> dict[str, float]:
    """Read scanner/state/value_screen_state.json top-N picks."""
    p = STATEDIR / "value_screen_state.json"
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text())
        picks = [r["ticker"] for r in (d.get("top_10") or [])[:top_n]]
    except Exception:
        return {}
    if not picks:
        return {}
    w = 1.0 / len(picks)
    return {t: w for t in picks}


# Single-name concentration cap. Originally 0.15 after QC's AMD convergence
# bled -8.8% on 2026-07-16. Raised to 0.90 on 2026-07-27 (user call): QC is
# the rated leader and its single-winner composition must be expressible on
# the live book, else the cap reroutes its allocation to cash. The $5
# aggregate-drawdown review trigger remains the backstop.
MAX_SINGLE_NAME_PCT = 0.35  # D6 rev 2026-08-04 (restored from VM-only edit 2026-08-07)

# A source must carry at least this governor weight to receive another
# source's capped-out overflow (see build_target's second pass).
SPILL_MIN_SOURCE_WEIGHT = 0.10


def _fill(want_total: float, comp: dict[str, float], tgt: dict[str, float],
          cap: float = MAX_SINGLE_NAME_PCT) -> float:
    """Allocate up to `want_total` (fraction of book) across `comp`'s names,
    never letting any name's TOTAL book share exceed `cap`. Excess from a
    capped name spills to the composition's other names. Returns whatever
    fraction could not be placed anywhere in this composition."""
    remaining = want_total
    for _ in range(6):
        if remaining < 1e-9:
            break
        open_names = {t: f for t, f in comp.items() if tgt.get(t, 0.0) < cap - 1e-9}
        if not open_names:
            break
        ftotal = sum(open_names.values()) or 1.0
        placed = 0.0
        for t, f in open_names.items():
            alloc = min(remaining * f / ftotal, cap - tgt.get(t, 0.0))
            tgt[t] = tgt.get(t, 0.0) + alloc
            placed += alloc
        remaining -= placed
        if placed < 1e-9:
            break
    return max(remaining, 0.0)


def build_target() -> dict[str, float]:
    """Blend the sources into one composition, honoring governor weights.

    The single-name cap is enforced SOURCE-FIRST: a source's excess over the
    cap spills to that source's own other names before any of it reaches a
    lower-rated source. The old global redistribution silently rerouted the
    governor's 92% QC vote into the 2%-rated sources whenever QC converged on
    one winner (AAPL 89% → capped at 15% → the other 85% of the book went to
    patent/value). Whatever no source can absorb is deliberately left as cash,
    so the returned fractions may sum to less than 1."""
    gov_weights = _governor_weights()
    default = WEIGHT_PER_SOURCE
    sources = {
        "qc":      (qc_composition(),      gov_weights.get("qc", default) if gov_weights else default),
        "insider": (insider_composition(), gov_weights.get("insider", default) if gov_weights else default),
        "patent":  (patent_composition(),  gov_weights.get("patent", default) if gov_weights else default),
        "whale":   (whale_composition(),   gov_weights.get("whale", default) if gov_weights else default),
        "value":   (_value_composition(),  gov_weights.get("value", default) if gov_weights else default),
        # Autopilot 'Actively Managed' mirror: NO live allocation until the
        # governor has scored it (weight 0.0 when absent from the weights file,
        # unlike the founding sources which default to the equal blend).
        "autopilot": (_autopilot_composition(),
                      gov_weights.get("autopilot", 0.0) if gov_weights else 0.0),
        # Congress (Pelosi PTR) mirror: same rule — no live money until scored.
        "congress": (_congress_composition(),
                     gov_weights.get("congress", 0.0) if gov_weights else 0.0),
    }
    active = {n: (comp, w) for n, (comp, w) in sources.items() if comp and w > 0}
    if not active:
        return {}
    wtotal = sum(w for _, w in active.values()) or 1.0
    by_rating = sorted(active.items(), key=lambda kv: -kv[1][1])

    tgt: dict[str, float] = defaultdict(float)
    spill = 0.0
    for name, (comp, w) in by_rating:
        spill += _fill(w / wtotal, comp, tgt)
    # Second pass: offer the spill only to sources the governor actually
    # rates (weight ≥ SPILL_MIN_SOURCE_WEIGHT), best-rated first. Handing a
    # top source's overflow to a 2%-rated (i.e. losing) source just because
    # it has cap-room is worse than cash — cash is the governor's own verdict
    # on those sources. Unplaced spill stays uninvested.
    if spill > 1e-6:
        for name, (comp, w) in by_rating:
            if w / wtotal < SPILL_MIN_SOURCE_WEIGHT:
                continue
            spill = _fill(spill, comp, tgt)
            if spill < 1e-6:
                break
    return {t: round(v, 6) for t, v in tgt.items() if v > 1e-6}


# ─── Alpaca I/O ───────────────────────────────────────────────────────
def get_positions(target_syms: set[str] | None) -> dict[str, dict] | None:
    """Return positions (all of them when target_syms is None), or None on
    ANY fetch failure.

    FAIL CLOSED: returning {} on an API error makes the rebalancer think we
    hold nothing and re-buy the whole target with real money (this exact
    pattern 6x'd patent_mirror's paper book over 2026-07-10..15). A failed
    read must skip the cycle, never masquerade as an empty book."""
    if not ALPACA_KEY:
        return None
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=_headers(), timeout=10)
        if not r.ok:
            return None
        out = {}
        for p in r.json():
            sym = p["symbol"]
            if target_syms is not None and sym not in target_syms:
                continue
            if p.get("asset_class") != "us_equity":
                continue
            out[sym] = {
                "qty": float(p.get("qty", 0)),
                "mv":  float(p.get("market_value", 0)),
                "avg_entry": float(p.get("avg_entry_price", 0)),
                "unrealized_pl": float(p.get("unrealized_pl", 0)),
            }
        return out
    except Exception:
        return None


def submit_order(sym: str, side: str, notional_usd: float | None = None,
                 qty: float | None = None) -> dict:
    if not ALPACA_KEY:
        return {"skipped": True}
    body = {"symbol": sym, "side": side.lower(), "type": "market",
            "time_in_force": "day"}
    # Full liquidations must sell by QTY: a notional computed from a stale
    # market value gets rejected ("insufficient qty available") whenever the
    # price moved between the positions fetch and the order.
    if qty is not None:
        body["qty"] = str(qty)
    else:
        body["notional"] = round(notional_usd or 0.0, 2)
    try:
        r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=_headers(),
                          json=body, timeout=12)
        if not r.ok:
            return {"error": f"HTTP {r.status_code}: {r.text[:150]}"}
        return r.json()
    except Exception as e:
        return {"error": repr(e)}


def _market_open() -> bool:
    """True if US equities are open right now. Defaults False on API failure.
    While the market is closed, day market orders queue and positions never
    update, so every tick re-submits the same delta (the qc_mirror runaway:
    530 queued AMD buys filled at once on 2026-07-27). Never submit while
    closed — with REAL money here that runaway would be unrecoverable."""
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/clock", headers=_headers(), timeout=8)
        if r.ok:
            return bool(r.json().get("is_open"))
    except Exception:
        pass
    return False


def tick() -> None:
    now = int(time.time())
    targets = build_target()
    if not targets:
        log_tick("combined_mirror", "no sources produced any composition")
        return
    if not ALPACA_KEY:
        emit_live("combined_mirror", "[skip] no alpaca key")
        return

    # Track symbols beyond the current target set: when a symbol drops out of
    # the targets, the mirror must still SEE it to sell it — filtering to
    # targets only left orphaned live positions unmanaged forever (AMD/SPY
    # leftovers from the Jul-13 composition, found 2026-07-27).
    prev_state = {}
    try:
        if STATE.exists():
            prev_state = json.loads(STATE.read_text())
    except Exception:
        pass
    prev_syms = set(prev_state.get("tracked_syms") or [])
    target_syms = set(targets.keys())
    # Live account is DEDICATED to this mirror → see every position (an
    # unlisted symbol is an orphan to liquidate, not another bot's holding).
    # Paper shares an account with the other mirrors → restrict to our own
    # current+previously-tracked symbols so we never touch their positions.
    scope = None if ALPACA_MODE == "live" else (target_syms | prev_syms)
    positions = get_positions(scope)
    if positions is None:
        # Positions fetch failed — skip this cycle rather than trade blind.
        log_tick("combined_mirror", "[skip] positions fetch failed — no trades this cycle")
        return

    # Fetch live account state — we need equity + cash from the SAME call so
    # the P/L we display is always derived from the equity number the app
    # renders. No second-source drift.
    account_equity = None
    account_cash = None
    account_last_equity = None
    settled_bp = None
    if ALPACA_MODE == "live":
        try:
            r = requests.get(f"{ALPACA_BASE}/v2/account", headers=_headers(), timeout=10)
            if r.ok:
                acc = r.json()
                account_equity      = float(acc.get("equity", 0))
                account_cash        = float(acc.get("cash", 0))
                account_last_equity = float(acc.get("last_equity", 0))
                # MARGIN account (confirmed 2026-07-30, 4x agreement on file;
                # limited-margin tier while equity < $2k): unsettled sale
                # proceeds are spendable same-day and GFVs don't exist here.
                # NEVER BORROW though — cap spend at cash (positions ≤ equity)
                # so leverage and margin calls stay structurally impossible,
                # even when Alpaca offers 2-4x buying power.
                settled_bp = min(float(acc.get("buying_power",
                                               acc.get("cash", 0)) or 0),
                                 account_cash)
        except Exception:
            pass
        total_book = account_equity or 0
    else:
        total_book = PAPER_BANKROLL_USD
    if total_book <= 0:
        log_tick("combined_mirror", "no bankroll")
        return

    state_prev = {}
    try:
        if STATE.exists():
            state_prev = json.loads(STATE.read_text())
    except Exception:
        pass
    # Per-symbol buy cooldowns after a 403 — don't hammer the same rejected
    # order every tick while funds are unsettled.
    cooldowns = {s: t for s, t in (state_prev.get("buy_cooldowns") or {}).items()
                 if t > now}

    # Guard/PT bookkeeping (persisted across ticks in the state file).
    pos_since = {s: t for s, t in (state_prev.get("pos_since") or {}).items()
                 if s in positions}
    for s in positions:
        pos_since.setdefault(s, now)
    pt_peaks = {s: p for s, p in (state_prev.get("pt_peaks") or {}).items()
                if s in positions}
    last_exits = {s: e for s, e in (state_prev.get("last_exits") or {}).items()
                  if now - e.get("ts", 0) < 7 * 86400}
    pt_suppress = {s: t for s, t in (state_prev.get("pt_suppress") or {}).items()
                   if t > now}
    pt_logged = {s: t for s, t in (state_prev.get("pt_logged") or {}).items()
                 if s in positions}
    # Whole-position close history for the shadow eligibility gate: last
    # n_trips full exits per symbol, 30-day TTL.
    _eg = _elig_cfg()
    elig_hist = {s: [e for e in v if now - e.get("ts", 0) < 30 * 86400][-_eg["n_trips"]:]
                 for s, v in (state_prev.get("elig_hist") or {}).items()}
    elig_hist = {s: v for s, v in elig_hist.items() if v}
    elig_logged = {s: t for s, t in (state_prev.get("elig_logged") or {}).items()
                   if now - t < 7 * 86400}
    cur_px = {}
    for s, p in positions.items():
        if p.get("qty"):
            cur_px[s] = p["mv"] / p["qty"]
            pt_peaks[s] = max(pt_peaks.get(s, 0.0), cur_px[s])

    desired = {t: v * total_book for t, v in targets.items()}
    market_open = _market_open()
    if not market_open:
        desired = {}
        log_tick("combined_mirror", "market closed — orders skipped")

    # Daily circuit breaker: bad day → freeze all trading until tomorrow.
    frozen = False
    if (market_open and ALPACA_MODE == "live"
            and account_equity and account_last_equity):
        day_pnl_pct = (account_equity / account_last_equity - 1) * 100
        freeze_at = _risk_freeze_pct()
        if day_pnl_pct <= freeze_at:
            desired = {}
            frozen = True
            today_str = time.strftime("%Y-%m-%d", time.gmtime())
            rs = {}
            try:
                rs = json.loads(RISK_STATE.read_text())
            except Exception:
                pass
            emit_live("combined_mirror",
                      f"[CIRCUIT BREAKER] day P/L {day_pnl_pct:+.2f}% ≤ "
                      f"{freeze_at}% — trading frozen for the session "
                      f"(holding positions, resumes next market day)")
            if rs.get("alerted") != today_str:
                _ntfy(f"Alpaca live: day P/L {day_pnl_pct:+.2f}% breached "
                      f"{freeze_at}% freeze. All trading halted for today; "
                      f"positions held. Equity ${account_equity:.2f}.")
                RISK_STATE.write_text(json.dumps(
                    {"alerted": today_str, "day_pnl_pct": round(day_pnl_pct, 3)}))
    # Profit-taking check (position-level, armed at +arm_pct from avg entry,
    # fires on a giveback_pct retreat from the running peak). SHADOW mode
    # (the default, per the 2026-08-02 audit — no tested rule survived
    # verification) only appends the would-have-fired event to pt_shadow.jsonl.
    # LIVE mode (only after the shadow ledger earns it, D7) liquidates and
    # suppresses rebuys so the exit sticks instead of churning against the
    # governor. Never evaluated while frozen (D21 semantics: freeze = hold).
    pt = _pt_cfg()
    pt_reason: dict[str, str] = {}
    if market_open and not frozen and pt.get("mode") in ("shadow", "live"):
        for s, p in positions.items():
            px, entry = cur_px.get(s), p.get("avg_entry") or 0
            peak = pt_peaks.get(s, 0.0)
            if not px or not entry or p.get("mv", 0) < pt["min_notional_usd"]:
                continue
            armed = (peak / entry - 1) * 100 >= pt["arm_pct"]
            fired = armed and px <= peak * (1 - pt["giveback_pct"] / 100)
            if not fired:
                continue
            if pt["mode"] == "shadow":
                if now - pt_logged.get(s, 0) > 6 * 3600:
                    pt_logged[s] = now
                    try:
                        with PT_SHADOW.open("a") as f:
                            f.write(json.dumps({
                                "ts": now, "sym": s, "px": round(px, 4),
                                "peak": round(peak, 4), "entry": round(entry, 4),
                                "unreal_pct": round((px / entry - 1) * 100, 3),
                                "mv": round(p["mv"], 2),
                                "would": "sell_all+suppress_rebuy"}) + "\n")
                    except Exception:
                        pass
            else:
                desired[s] = 0.0
                pt_suppress[s] = now + int(pt["rebuy_suppress_h"] * 3600)
                pt_reason[s] = (f"PROFIT TAKE: +{(px / entry - 1) * 100:.1f}% "
                                f"(peak +{(peak / entry - 1) * 100:.1f}%, gave back "
                                f"{pt['giveback_pct']}%) — rebuy suppressed "
                                f"{pt['rebuy_suppress_h']}h")

    # Liquidate orphans: positions we hold that are no longer in any target.
    if market_open:
        for sym, pos in positions.items():
            if sym in desired:
                continue
            mv = pos.get("mv", 0.0)
            if mv >= MIN_ORDER_USD:
                desired[sym] = 0.0  # the sell branch below handles it
    # Human-readable driver string for ledger entries: who is steering the
    # blend right now (top governor weights), e.g. "patent 99% · qc 0%".
    gw = _governor_weights() or {}
    _top = sorted(gw.items(), key=lambda kv: -kv[1])[:2]
    driver = " · ".join(f"{s} {w*100:.0f}%" for s, w in _top) if _top else "equal blend"

    actions = []
    buy_budget = settled_bp  # None in paper mode → unconstrained
    skipped_buys = []
    for sym, want in desired.items():
        cur = positions.get(sym, {}).get("mv", 0.0)
        delta = want - cur
        if abs(delta) < REBALANCE_THRESHOLD_USD:
            continue
        if delta > 0 and delta >= MIN_ORDER_USD:
            blocked_by = []
            if sym in cooldowns:
                blocked_by.append("cooldown")
            if sym in pt_suppress:
                blocked_by.append("pt_suppress")
            # Re-entry premium guard: the 7/13–7/31 book's costliest exit
            # failure was full-exit→rebuy-days-later-at-a-worse-price (~$0.70
            # over 3 weeks, untouched by the D21 intraday breaker). If we
            # fully sold this symbol inside the window, only rebuy within
            # premium_pct of the exit price; price-fetch failure fails open
            # (D21's 4-eval leader confirmation still paces genuine flips).
            gd = _guard_cfg()
            le = last_exits.get(sym)
            prem_note = ""
            if (not blocked_by and le
                    and now - le.get("ts", 0) < gd["reentry_window_h"] * 3600):
                lp = _latest_px(sym)
                if lp and le.get("px") and lp > le["px"] * (1 + gd["reentry_premium_pct"] / 100):
                    blocked_by.append("reentry_premium")
                    prem_note = f"({lp:.2f}>{le['px']:.2f}+{gd['reentry_premium_pct']}%)"
            # Shadow eligibility gate: would we block this buy because the
            # symbol's last n_trips whole-position closes are a losing streak?
            # Log-only — NEVER constrains order flow (see _elig_cfg note).
            eg = _elig_cfg()
            h = elig_hist.get(sym) or []
            if (eg.get("mode") == "shadow" and len(h) >= eg["n_trips"]
                    and now - h[-1].get("ts", 0) < eg["block_days"] * 86400):
                cum_pl = sum(e.get("pl", 0) for e in h)
                cum_ntl = sum(e.get("notional", 0) for e in h)
                leg_usd = cum_pl < -eg["loss_usd"]
                leg_pct = cum_ntl > 0 and cum_pl < -eg["loss_pct_notional"] / 100 * cum_ntl
                if (leg_usd or leg_pct) and now - elig_logged.get(sym, 0) > 6 * 3600:
                    elig_logged[sym] = now
                    try:
                        with ELIG_SHADOW.open("a") as f:
                            f.write(json.dumps({
                                "ts": now, "sym": sym,
                                "cum_pl": round(cum_pl, 4),
                                "cum_notional": round(cum_ntl, 2),
                                "legs": {"usd": leg_usd, "pct": leg_pct},
                                "buy_delta_usd": round(delta, 2),
                                "also_blocked_by": blocked_by,
                                "would": f"block_buy_{eg['block_days']}d"}) + "\n")
                    except Exception:
                        pass
            if blocked_by:
                skipped_buys.append(f"{sym}:{blocked_by[0]}{prem_note}")
                continue
            if buy_budget is not None:
                # Leave a $0.10 cushion; clamp to what's actually settled.
                spendable = buy_budget - 0.10
                if spendable < MIN_ORDER_USD:
                    skipped_buys.append(f"{sym}:no_spendable_bp")
                    continue
                delta = min(delta, spendable)
            res = submit_order(sym, "buy", delta)
            err = res.get("error", "")
            if "403" in err:
                cooldowns[sym] = now + BUY_COOLDOWN_S
            elif buy_budget is not None and "error" not in res:
                buy_budget -= delta
            actions.append({"sym": sym, "side": "BUY",
                            "delta_usd": round(delta, 2),
                            "why": (f"under target: held {cur/total_book*100:.1f}%, "
                                    f"governor target {targets.get(sym, 0)*100:.1f}% "
                                    f"(blend: {driver})"),
                            "result": res.get("id", res.get("error", "skipped"))[:16]})
        elif delta < 0 and abs(delta) >= MIN_ORDER_USD:
            if want <= 0.0:
                # Minimum-dwell guard: don't full-exit a GREEN position
                # younger than min_dwell_h on a weight flip — the audit showed
                # sub-dwell sell→rebuy-higher cycles are pure churn (rebalance
                # sells land at the 12th percentile of the prior-48h range).
                # Losers are never dwell-held (D6/D10), and profit-take exits
                # (pt_reason) bypass the guard by design.
                gd = _guard_cfg()
                age_h = (now - pos_since.get(sym, now)) / 3600
                if (sym not in pt_reason and age_h < gd["min_dwell_h"]
                        and positions[sym].get("unrealized_pl", 0) >= 0):
                    log_tick("combined_mirror",
                             f"[dwell guard] {sym} green, {age_h:.1f}h < "
                             f"{gd['min_dwell_h']}h — full exit deferred")
                    continue
                # Orphan liquidation: sell the whole position by share qty.
                res = submit_order(sym, "sell", qty=positions[sym]["qty"])
                if "error" not in res and cur_px.get(sym):
                    last_exits[sym] = {"px": round(cur_px[sym], 4), "ts": now}
                if "error" not in res:
                    # Whole-position close record for the shadow eligibility
                    # gate. unrealized_pl at the exit tick ≈ realized P/L for
                    # a market fill — cent-level error, fine for shadow.
                    elig_hist.setdefault(sym, []).append({
                        "ts": now,
                        "pl": round(positions[sym].get("unrealized_pl", 0), 4),
                        "notional": round(positions[sym].get("mv", 0), 2)})
                    elig_hist[sym] = elig_hist[sym][-_elig_cfg()["n_trips"]:]
            else:
                res = submit_order(sym, "sell", abs(delta))
            actions.append({"sym": sym, "side": "SELL",
                            "delta_usd": round(delta, 2),
                            "why": (pt_reason.get(sym) or
                                    ((f"dropped from target — liquidating "
                                      f"(blend: {driver})") if want <= 0.0 else
                                     (f"over target: held {cur/total_book*100:.1f}%, "
                                      f"governor target {targets.get(sym, 0)*100:.1f}% "
                                      f"(blend: {driver})"))),
                            "result": res.get("id", res.get("error", "skipped"))[:80]})
    if skipped_buys:
        log_tick("combined_mirror", "buys deferred: " + ",".join(skipped_buys))

    invested = sum(p["mv"] for p in positions.values())
    # For live mode, derive P/L directly from account.equity so the displayed
    # value ALWAYS matches the equity number shown on the app. `last_equity`
    # is Alpaca's prior-day close; falls back to $50 deposit baseline.
    if ALPACA_MODE == "live" and account_equity is not None:
        # Persist first-seen equity as our baseline so all subsequent P/L is
        # measured against the same anchor (the account's first observed state).
        baseline = state_prev.get("baseline_equity") or account_equity
        pnl_pct = round((account_equity - baseline) / baseline * 100, 3) if baseline else 0.0
        total_unreal = round(account_equity - baseline, 4)
    else:
        # Paper mode: fall back to positions-based unreal for consistency.
        total_unreal = sum(p.get("unrealized_pl", 0) for p in positions.values())
        pnl_pct = round(total_unreal / max(invested, 1) * 100, 3) if invested else 0.0
        baseline = PAPER_BANKROLL_USD

    # Snapshot equity to the chart CSV so /api/equity/alpaca_live has data
    if account_equity is not None:
        _append_equity_history(now, account_equity)

    STATE.write_text(json.dumps({
        "ts": now,
        "mode": ALPACA_MODE,
        "bankroll": round(total_book, 2),
        "baseline_equity": round(baseline, 2),
        "account_equity": round(account_equity, 4) if account_equity is not None else None,
        "targets": {t: round(v * 100, 2) for t, v in targets.items()},
        "positions": positions,
        "invested": round(invested, 2),
        "unrealized": round(total_unreal, 4),
        "pnl_pct": pnl_pct,
        "sources": {
            "qc":      list(qc_composition().keys()),
            "insider": list(insider_composition().keys()),
            "patent":  list(patent_composition().keys()),
            "whale":   list(whale_composition().keys()),
        },
        "last_actions": actions,
        "buy_cooldowns": cooldowns,
        "settled_bp": settled_bp,
        "tracked_syms": sorted(target_syms | set(positions)),
        "pos_since": pos_since,
        "pt_peaks": {s: round(p, 4) for s, p in pt_peaks.items()},
        "last_exits": last_exits,
        "pt_suppress": pt_suppress,
        "pt_logged": pt_logged,
        "elig_hist": elig_hist,
        "elig_logged": elig_logged,
    }, indent=2, default=float))
    if actions:
        with LEDGER.open("a") as f:
            for a in actions:
                f.write(json.dumps({"ts": now, **a}) + "\n")
        emit_live("combined_mirror",
                  f"[ensemble] {len(actions)} orders  pnl_pct={pnl_pct:+.2f}%  "
                  + "  ".join(f"{a['sym']}:{a['side']}${a['delta_usd']:+.2f}"
                              for a in actions[:4]))
    else:
        emit_live("combined_mirror",
                  f"[ensemble] balanced  pnl_pct={pnl_pct:+.2f}%  book=${total_book:.2f}")
    log_tick("combined_mirror",
             f"tickers={len(targets)} acts={len(actions)} pnl_pct={pnl_pct:+.2f}%")


if __name__ == "__main__":
    tick()
