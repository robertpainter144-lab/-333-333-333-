#!/usr/bin/env python3
"""Micro-meme sub-sleeve (D63, Robert 2026-08-24): a $100 live carve-out
that buys the thin movers the main sleeve's $20M liquidity floor refuses —
meme/new coins with $1M-$20M daily volume on a +8% day. Third sanctioned
live experiment sleeve (D36 turbo arm, D39 sleeve, this).

Rails (hard, in code):
  - total open notional <= $100, <= 3 positions, ~$30 each
  - entries only while whale risk_on >= 0.60
  - exits: stop -15% / take +30% / 7-day time exit (wide on purpose:
    sub-floor spreads eat tight stops)
  - FREEZE at realized <= -$30: no new buys ever again until a human
    clears state (exits keep running)
  - never trades anything the MAIN sleeve tracks or could trade (its
    seed universe + current positions are excluded)

Hardened patterns carried over from the 8/22-8/24 incidents: microsecond
nonces; fresh-Balance re-verify before booking any close; atomic state
writes; per-position mv marked each tick so the main equity chart counts
this sleeve's value (engine + fine_recorder read micro_meme_state.json).
D7-scoreable ledger: state/micro_meme_ledger.jsonl."""
import fcntl
import json
import time
import hmac
import base64
import hashlib
import platform
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# host-portable: resolves correctly on GCP (lab-stack/crypto_sleeve) AND on
# the Mac standby mirror — the engine_host gate below decides who trades
ROOT = Path(__file__).resolve().parent
(ROOT / "state").mkdir(parents=True, exist_ok=True)
STATE_F = ROOT / "state" / "micro_meme_state.json"
LEDGER_F = ROOT / "state" / "micro_meme_ledger.jsonl"
NOTIFIER = Path.home() / "bin" / "quarki_notify.py"

CAP_USD = 100.0
PER_COIN_USD = 30.0
MAX_POS = 3
FREEZE_AT = -30.0
STOP_PCT = -15.0
TAKE_PCT = 30.0
MAX_HOLD_D = 7
# DVOL_HI must stay == the main sleeve's min_dollar_vol_24h floor: that
# equality is what guarantees the two engines' candidate sets can NEVER
# overlap (main trades >= the floor, this trades strictly below it).
DVOL_LO, DVOL_HI = 1e6, 20e6
MIN_GAIN = 8.0
MIN_RISK_ON = 0.60

SKIP = {"XBT", "XDG", "EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "USDT",
        "USDC", "DAI", "TUSD", "PYUSD", "EURT", "USDS", "USDG", "EURR",
        "RLUSD", "USDQ", "USTS", "EURQ"}


def log(msg):
    print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}",
          flush=True)


def notify(level, title, msg):
    try:
        subprocess.run(["python3", str(NOTIFIER), level, title, msg],
                       timeout=30, check=False)
    except Exception:
        pass


ENV = {}
for ln in open(Path.home() / "kraken.env"):
    if "=" in ln:
        k, v = ln.strip().split("=", 1)
        ENV[k] = v


def pub(path, params=None):
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    with urllib.request.urlopen("https://api.kraken.com" + path + q,
                                timeout=10) as r:
        j = json.load(r)
    if j.get("error"):
        raise RuntimeError(f"kraken {path}: {j['error']}")
    return j["result"]


def priv(path, data=None):
    d = dict(data or {})
    d["nonce"] = str(int(time.time() * 1000000))   # us-nonce rule (2026-08-23)
    post = urllib.parse.urlencode(d)
    msg = path.encode() + hashlib.sha256((d["nonce"] + post).encode()).digest()
    sig = base64.b64encode(hmac.new(base64.b64decode(ENV["KRAKEN_API_SECRET"]),
                                    msg, hashlib.sha512).digest())
    req = urllib.request.Request("https://api.kraken.com" + path, post.encode(),
                                 {"API-Key": ENV["KRAKEN_API_KEY"],
                                  "API-Sign": sig.decode()})
    with urllib.request.urlopen(req, timeout=12) as r:
        j = json.load(r)
    if j.get("error"):
        raise RuntimeError(f"kraken {path}: {j['error']}")
    return j["result"]


def load_state():
    try:
        return json.loads(STATE_F.read_text())
    except Exception:
        return {"positions": {}, "realized": 0.0, "frozen": False}


def save_state(st):
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(STATE_F)


def ledger(row):
    with LEDGER_F.open("a") as f:
        f.write(json.dumps(row) + "\n")


def price_of(code):
    t = pub("/0/public/Ticker", {"pair": code})
    return float(next(iter(t.values()))["c"][0])


_LOCK_FH = None


def main():
    global _LOCK_FH
    # leader gate — hostname-keyed exactly like sleeve.py: only the
    # config-designated engine host trades; mirrors are display-only
    try:
        _eh = str(json.loads((ROOT / "config.json").read_text())
                  .get("engine_host", "")).lower()
    except Exception:
        # can't determine the leader -> DON'T trade (fail closed; a config
        # read error must never turn a standby mirror into a live trader)
        log("leader gate: config.json unreadable — refusing to trade")
        return
    if _eh:
        _on_gcp = "quarki-stack" in platform.node().lower()
        if (_eh == "gcp") != _on_gcp:
            log(f"standby: engine_host={_eh}, this host only displays")
            return
    # single-flight lock: overlapping ticks must never both trade
    _LOCK_FH = open(ROOT / "state" / ".micro_lock", "w")
    try:
        fcntl.flock(_LOCK_FH, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("skip: previous micro tick still active (lock held)")
        return
    st = load_state()
    now = time.time()

    # ---- context: main sleeve exclusions + whale gate -------------------
    try:
        cfg = json.loads((ROOT / "config.json").read_text())
        main_syms = {s.split("/")[0].upper() for s in cfg.get("universe", [])}
    except Exception:
        main_syms = set()
    try:
        main_st = json.loads((ROOT / "state" / "sleeve_state.json").read_text())
        main_syms |= {s.split("/")[0].upper()
                      for s in (main_st.get("positions") or {})}
    except Exception:
        pass
    try:
        risk_on = float(json.loads(
            (ROOT / "state" / "whale_signals.json").read_text())["risk_on"])
    except Exception:
        risk_on = 0.0

    codes = {}
    try:
        codes = json.loads(
            (ROOT / "state" / "assetpairs_cache.json").read_text())["codes"]
    except Exception:
        pass

    # ---- exits first (protection before growth) -------------------------
    for sym in list(st["positions"].keys()):
        p = st["positions"][sym]
        code = p.get("code") or codes.get(p.get("ws") or "")
        if not code:
            log(f"{sym}: no pair code — cannot mark or exit, skipping tick")
            continue
        try:
            px = price_of(code)
            p["px_fails"] = 0
        except Exception as e:
            p["px_fails"] = int(p.get("px_fails") or 0) + 1
            log(f"{sym}: price fetch failed ({e}) — holding "
                f"({p['px_fails']} straight)")
            if p["px_fails"] == 3 or (p["px_fails"] > 3 and p["px_fails"] % 12 == 0):
                notify("action", "Micro-meme position unpriceable",
                       f"{sym} has failed price lookups 3 ticks straight — "
                       "it may be delisted or renamed. Its exits cannot run "
                       "until pricing recovers; consider a manual look.")
            save_state(st)
            continue
        p["cur"] = px
        p["mv"] = round(p["qty"] * px, 4)
        chg = (px / p["entry"] - 1) * 100
        age_d = (now - p["opened_ts"]) / 86400
        reason = ("stop" if chg <= STOP_PCT else
                  "take" if chg >= TAKE_PCT else
                  "time" if age_d >= MAX_HOLD_D else None)
        if not reason:
            continue
        # fresh-Balance re-verify before booking anything (8/23 rule:
        # never trust a derived number about money)
        try:
            bal = priv("/0/private/Balance")
            asset = p.get("asset") or sym
            # sum spot + Earn alias like sleeve.py's positions(): a partial
            # auto-earn sweep must not read as "externally closed"
            spot = float(bal.get(asset, 0)) + float(bal.get(asset + ".F", 0))
        except Exception as e:
            log(f"{sym}: balance re-verify failed ({e}) — not selling this tick")
            continue
        if spot * px < 2:
            log(f"{sym}: balance shows ~nothing ({spot}) — treating as "
                "externally closed, booking at last mark")
        else:
            vol = min(p["qty"], spot) * 0.9995
            try:
                priv("/0/private/AddOrder",
                     {"pair": code, "type": "sell", "ordertype": "market",
                      "volume": f"{vol:.8f}"})
            except Exception as e:
                log(f"{sym}: SELL failed ({e}) — will retry next tick")
                continue
        pl = round((px / p["entry"] - 1) * p["notional"], 4)
        st["realized"] = round(st.get("realized", 0) + pl, 4)
        del st["positions"][sym]
        save_state(st)   # persist FIRST: ledger/notify failure after a real
                         # exchange sell must never resurrect the position
        try:
            ledger({"closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "sym": sym, "entry": p["entry"], "exit": px,
                    "notional": p["notional"], "pl": pl,
                    "pl_pct": round(chg, 2), "reason": reason})
        except Exception as e:
            log(f"ledger append failed ({e}) — state already persisted")
        log(f"CLOSED {sym} {reason} pl={pl:+.2f} (realized {st['realized']:+.2f})")
        notify("routine", "Micro-meme exit",
               f"Sold {sym} on {reason}: {pl:+.2f} (sleeve realized "
               f"{st['realized']:+.2f} of {FREEZE_AT} freeze line).")

    # ---- freeze check ----------------------------------------------------
    if st.get("realized", 0) <= FREEZE_AT and not st.get("frozen"):
        st["frozen"] = True
        save_state(st)
        notify("action", "Micro-meme sleeve FROZEN",
               f"The $100 micro-meme experiment hit its -$30 stop "
               f"(realized {st['realized']:+.2f}). No new buys; open "
               "positions still exit on their rules. This is the "
               "experiment ending, not a malfunction.")
    if st.get("frozen"):
        _mark_and_exit(st)
        return

    # ---- entries ---------------------------------------------------------
    open_notional = sum(p["notional"] for p in st["positions"].values())
    if (len(st["positions"]) >= MAX_POS or open_notional + PER_COIN_USD > CAP_USD
            or risk_on < MIN_RISK_ON or not codes):
        _mark_and_exit(st)
        return
    # the carve-out only exists as the main engine sells down below its
    # lowered cap — until actual ZUSD is there, don't fire orders at it
    try:
        _b = priv("/0/private/Balance")
        zusd = sum(float(_b.get(a, 0)) for a in ("ZUSD", "USD", "USD.F"))
    except Exception:
        zusd = 0.0
    if zusd < PER_COIN_USD + 2:
        log(f"cash ${zusd:.2f} < ${PER_COIN_USD + 2:.0f} — waiting for the "
            "main sleeve to free the carve-out")
        _mark_and_exit(st)
        return

    # whole-exchange screen for sub-floor movers
    rows = []
    vals = list(codes.values())
    res = {}
    for i in range(0, len(vals), 100):
        try:
            res.update(pub("/0/public/Ticker",
                           {"pair": ",".join(vals[i:i + 100])}))
        except Exception:
            pass
    for ws, code in codes.items():
        base = ws.split("/")[0].upper()
        if base in SKIP or base in main_syms or base in st["positions"]:
            continue
        t = res.get(code)
        if not t:
            continue
        try:
            last, op = float(t["c"][0]), float(t["o"])
            dvol = float(t["v"][1]) * float(t["p"][1])
        except Exception:
            continue
        if not (DVOL_LO <= dvol < DVOL_HI) or last <= 0 or op <= 0:
            continue
        gain = (last / op - 1) * 100
        if gain >= MIN_GAIN:
            rows.append((gain, dvol, ws, code, last))
    rows.sort(key=lambda r: -r[0])

    for gain, dvol, ws, code, last in rows[:1]:   # at most ONE entry per tick
        base = ws.split("/")[0].upper()
        pair_base = None   # authoritative Balance asset code (AssetPairs.base)
        try:
            meta = pub("/0/public/AssetPairs", {"pair": code})
            _pm = next(iter(meta.values()))
            om = float(_pm.get("ordermin") or 0)
            pair_base = _pm.get("base") or None
        except Exception:
            om = 0
        if om * last > PER_COIN_USD:
            log(f"skip {ws}: ordermin {om} x {last} > ${PER_COIN_USD}")
            continue
        vol = round(PER_COIN_USD * 0.995 / last, 8)
        try:
            r = priv("/0/private/AddOrder",
                     {"pair": code, "type": "buy", "ordertype": "market",
                      "volume": f"{vol:.8f}"})
        except Exception as e:
            log(f"BUY {ws} failed ({e})")
            continue
        fill = last
        try:
            txid = (r.get("txid") or [None])[0]
            if txid:
                time.sleep(1)
                o = priv("/0/private/QueryOrders", {"txid": txid}).get(txid) or {}
                fp = float(o.get("price") or 0)
                if fp > 0:
                    fill = fp
        except Exception:
            pass
        # Balance asset code comes from AssetPairs.base (authoritative —
        # never guessed against Balance: a wrong guess is exactly the
        # phantom-close vector from the 8/23 incidents)
        asset = pair_base or base
        actual_notional = round(vol * fill, 4)   # cap accounts real exposure
        st["positions"][base] = {
            "ws": ws, "code": code, "asset": asset, "qty": vol,
            "entry": fill, "cur": fill, "mv": round(vol * fill, 4),
            "notional": actual_notional,
            "opened": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "opened_ts": now}
        log(f"BUY {ws} ${PER_COIN_USD} @ ~{fill} (+{gain:.1f}% day, "
            f"${dvol/1e6:.1f}M dvol, risk_on {risk_on:.2f})")
        # first-ever buy pages Robert (his explicit ask); later entries are
        # silent-routine per D28
        _first = not st.get("ever_bought")
        st["ever_bought"] = True
        notify("action" if _first else "routine",
               "Micro-meme first buy" if _first else "Micro-meme entry",
               f"Bought ${PER_COIN_USD:.0f} of {base} at about {fill} "
               f"(+{gain:.1f}% today on ${dvol/1e6:.1f}M volume). Stop -15%, "
               f"take +30%, 7d max. Sleeve: {len(st['positions'])}/{MAX_POS} "
               f"slots, realized {st.get('realized', 0):+.2f}.")
        save_state(st)

    _mark_and_exit(st)


def _mark_and_exit(st):
    st["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(st)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"ERROR {e}")
