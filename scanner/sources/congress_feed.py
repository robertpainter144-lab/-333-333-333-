"""Congressional PTR feed: House Clerk filings → congress_holdings.json.

RESEARCH DRAFT (2026-07-27) — not registered in main.py, no trading code.

Source choice (evaluated 2026-07-27):
  * disclosures-clerk.house.gov  — CHOSEN. Official public government data,
    no auth, no ToS friction. Year index ZIP at
      https://disclosures-clerk.house.gov/public_disc/financial-pdfs/<YEAR>FD.zip
    (contains <YEAR>FD.xml: one <Member> per filing with FilingType, DocID,
    FilingDate; 'P' = Periodic Transaction Report). PTR PDFs at
      https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/<YEAR>/<DocID>.pdf
    E-filed PTR PDFs are RC4-128 "permissions" encrypted (empty user
    password) with CID fonts — this module carries a minimal stdlib-only
    extractor (RC4 + Flate + ToUnicode CMap) verified against Pelosi DocID
    20034836. Index observed updating daily (Last-Modified same-day).
  * housestockwatcher.com / senatestockwatcher.com S3 JSON — DEAD in 2026
    (both buckets return AccessDenied; project unmaintained).
  * capitoltrades.com bff API — unofficial, returned 503 when probed; ToS
    murky. Rejected.
  * Quiver Quantitative — congress endpoints behind paid API tiers. Rejected.
  * efdsearch.senate.gov — Senate only (session/CSRF dance, PDFs and HTML
    tables). Not needed for the default roster (Pelosi is House). Senators
    in the politician list are logged as unsupported, never guessed at.

Latency caveat: the STOCK Act allows up to 30/45 days between a transaction
and its PTR; this feed can only ever be as fresh as the disclosure.

Composition heuristic (all parameters config-overridable):
  1. Collect parsed transactions for the configured politicians from the
     current + previous year indexes, keep those in the last LOOKBACK_DAYS.
  2. Replay chronologically into a dollar book:
       purchase  (P)          → add the disclosed amount-range midpoint,
                                decayed by exp2(-age_days / HALF_LIFE_DAYS)
       full sale (S)          → drop the symbol
       partial sale           → multiply the symbol by PARTIAL_SALE_FACTOR
       exchange  (E)          → ignored
     Option trades ([OP]) count as exposure to the underlying ticker
     (Pelosi's book is mostly deep LEAPS) unless include_options=false.
  3. Keep the MAX_POSITIONS largest, normalize to weights summing to 1.

Fail closed: any index/PDF fetch or parse failure, or an unfetched backlog,
leaves the previous congress_holdings.json untouched. Image-only (paper)
filings are recorded as unparseable and skipped rather than blocking forever.

Config (optional) — STATEDIR/congress_feed_config.json:
  {"politicians": ["Nancy Pelosi"], "lookback_days": 365,
   "half_life_days": 180, "partial_sale_factor": 0.5,
   "max_positions": 12, "include_options": true}
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import sys
import time
import zipfile
import zlib
from hashlib import md5
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from signals import emit_live, log_tick, STATEDIR

CONFIG   = STATEDIR / "congress_feed_config.json"
CACHE    = STATEDIR / "congress_feed_cache.json"
HOLDINGS = STATEDIR / "congress_holdings.json"

CLERK = "https://disclosures-clerk.house.gov/public_disc"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) research"}

DEFAULTS = {
    "politicians": ["Nancy Pelosi"],
    "lookback_days": 365,
    "half_life_days": 180,
    "partial_sale_factor": 0.5,
    "max_positions": 12,
    "include_options": True,
}
MAX_NEW_PDFS_PER_TICK = 10   # politeness cap; backlog defers publishing
MIN_TEXT_CHARS = 100         # below this the PDF is image-only (paper filing)

# ---------------------------------------------------------------------------
# Minimal PDF text extraction (House e-filing generator only).
# Handles: RC4-128 standard security handler w/ empty user password (V2/R3),
# unencrypted files, FlateDecode, CID fonts via ToUnicode bfchar/bfrange.
# ---------------------------------------------------------------------------

_PAD = bytes([
    0x28, 0xBF, 0x4E, 0x5E, 0x4E, 0x75, 0x8A, 0x41, 0x64, 0x00, 0x4E, 0x56,
    0xFF, 0xFA, 0x01, 0x08, 0x2E, 0x2E, 0x00, 0xB6, 0xD0, 0x68, 0x3E, 0x80,
    0x2F, 0x0C, 0xA9, 0xFE, 0x64, 0x53, 0x69, 0x7A])


def _rc4(key: bytes, data: bytes) -> bytes:
    S = list(range(256))
    j = 0
    for i in range(256):
        j = (j + S[i] + key[i % len(key)]) % 256
        S[i], S[j] = S[j], S[i]
    out = bytearray()
    i = j = 0
    for c in data:
        i = (i + 1) % 256
        j = (j + S[i]) % 256
        S[i], S[j] = S[j], S[i]
        out.append(c ^ S[(S[i] + S[j]) % 256])
    return bytes(out)


def _file_key(raw: bytes) -> bytes | None:
    """Encryption key for the standard handler with an empty user password.
    None → document is not encrypted."""
    enc = re.search(rb"/Encrypt\s+(\d+)\s+\d+\s+R", raw)
    if not enc:
        return None
    m = re.search(rb"\b%d\s+0\s+obj(.*?)endobj" % int(enc.group(1)), raw, re.S)
    d = m.group(1)
    O = bytes.fromhex(re.search(rb"/O\s*<([0-9A-Fa-f]+)>", d).group(1).decode())
    P = int(re.search(rb"/P\s+(-?\d+)", d).group(1))
    r = int(re.search(rb"/R\s+(\d+)", d).group(1))
    lm = re.search(rb"/Length\s+(\d+)", d)
    length = (int(lm.group(1)) // 8) if lm else 5
    idpos = raw.rfind(b"/ID")
    id0 = bytes.fromhex(
        re.search(rb"<([0-9A-Fa-f]{32})>", raw[idpos:idpos + 200]).group(1).decode())
    h = md5(_PAD + O[:32] + P.to_bytes(4, "little", signed=True) + id0).digest()
    if r >= 3:
        for _ in range(50):
            h = md5(h[:length]).digest()
    else:
        length = 5
    return h[:length]


def _obj_key(fkey: bytes, num: int, gen: int) -> bytes:
    k = md5(fkey + num.to_bytes(3, "little") + gen.to_bytes(2, "little")).digest()
    return k[:min(len(fkey) + 5, 16)]


def _parse_cmap(data: bytes) -> dict[int, str]:
    cmap: dict[int, str] = {}
    for m in re.finditer(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for a, b in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", m.group(1)):
            cmap[int(a, 16)] = chr(int(b[:4], 16))
    for m in re.finditer(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for a, b, c in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
                m.group(1)):
            lo, hi, dst = int(a, 16), int(b, 16), int(c, 16)
            for i in range(min(hi - lo + 1, 512)):
                cmap[lo + i] = chr(dst + i)
    return cmap


def pdf_text(raw: bytes) -> str:
    """Text of a House e-filed PTR PDF, one visual line per text block."""
    fkey = _file_key(raw)
    objs: dict[int, tuple[int, bytes]] = {}
    for m in re.finditer(rb"(\d+)\s+(\d+)\s+obj(.*?)endobj", raw, re.S):
        objs[int(m.group(1))] = (int(m.group(2)), m.group(3))

    def stream(num: int) -> bytes | None:
        gen, body = objs[num]
        sm = re.search(rb"stream\r?\n(.*?)\r?\nendstream", body, re.S)
        if not sm:
            return None
        data = sm.group(1)
        if fkey is not None:
            data = _rc4(_obj_key(fkey, num, gen), data)
        if b"/FlateDecode" in body[:body.find(b"stream")]:
            try:
                data = zlib.decompress(data)
            except Exception:
                return None
        return data

    # /Fn name → ToUnicode cmap (via the font objects the page resources name)
    fontmap: dict[str, int] = {}
    for num, (_gen, body) in objs.items():
        fm = re.search(rb"/Font\s*<<(.*?)>>", body, re.S)
        if fm:
            for fn, ref in re.findall(rb"/(F\d+)\s+(\d+)\s+0\s+R", fm.group(1)):
                fontmap[fn.decode()] = int(ref)
    cmaps: dict[str, dict[int, str]] = {}
    for fn, ref in fontmap.items():
        tm = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", objs[ref][1])
        if tm:
            tu = stream(int(tm.group(1)))
            if tu:
                cmaps[fn] = _parse_cmap(tu)

    def decode_content(data: bytes) -> str:
        cur, out = None, []
        toks = re.finditer(
            rb"/(F\d+)\s+[\d.]+\s+Tf"          # font select
            rb"|<([0-9A-Fa-f]+)>\s*Tj"          # hex show
            rb"|\[((?:<[0-9A-Fa-f]+>|[^]\\])*)\]\s*TJ"  # array show
            rb"|ET", data)
        for tok in toks:
            if tok.group(1):
                cur = tok.group(1).decode()
            elif tok.group(2) is not None or tok.group(3) is not None:
                cm = cmaps.get(cur or "", {})
                hexes = ([tok.group(2)] if tok.group(2) is not None
                         else re.findall(rb"<([0-9A-Fa-f]+)>", tok.group(3)))
                for h in hexes:
                    out.append("".join(
                        cm.get(int(h[i:i + 4], 16), "")
                        for i in range(0, len(h) - len(h) % 4, 4)))
            else:
                out.append("\n")
        return "".join(out)

    parts = []
    for num in objs:
        d = stream(num)
        if d and (b"Tj" in d or b"TJ" in d):
            parts.append(decode_content(d))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# PTR transaction parsing
# ---------------------------------------------------------------------------

# ticker "(NVDA)", optional "[OP]"/"[ST]" tag, type, tx date, notif date,
# "$lo - $hi" (hi may be absent on the open-ended top bracket)
_TX = re.compile(
    r"\(([A-Z]{1,5}(?:\.[A-Z])?)\)\s*"
    r"(?:\[([A-Z]{2})\]\s*)?"
    r"(P|S\s*\(partial\)|S|E)\s+"
    r"(\d{2}/\d{2}/\d{4})\s+"
    r"(\d{2}/\d{2}/\d{4})\s+"
    r"(?:\$([\d,]+)\s*-\s*\$([\d,]+)|Over\s+\$([\d,]+))",
    re.S)


def parse_ptr_text(text: str) -> list[dict]:
    out = []
    for m in _TX.finditer(text):
        sym, tag, typ, txd, _nd, lo, hi, over = m.groups()
        typ = re.sub(r"\s+", " ", typ)
        if over:
            lo_v = float(over.replace(",", ""))
            mid = lo_v * 1.5
        else:
            lo_v = float(lo.replace(",", ""))
            mid = (lo_v + float(hi.replace(",", ""))) / 2
        out.append({
            "sym": sym.upper(),
            "kind": {"P": "buy", "S": "sell", "S (partial)": "sell_partial",
                     "E": "exchange"}.get(typ, "other"),
            "option": tag == "OP",
            "date": txd,               # MM/DD/YYYY transaction date
            "mid_usd": round(mid, 2),
        })
    return out


# ---------------------------------------------------------------------------
# Feed
# ---------------------------------------------------------------------------

def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _cfg() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(_load(CONFIG) or {})
    return cfg


def _index(year: int) -> list[dict] | None:
    """[{first,last,doc_id,filed}] of PTR ('P') filings for a year.
    None on any fetch/parse failure — fail closed."""
    import xml.etree.ElementTree as ET
    try:
        r = requests.get(f"{CLERK}/financial-pdfs/{year}FD.zip",
                         headers=UA, timeout=25)
        if not r.ok:
            return None
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        xml_name = next(n for n in zf.namelist() if n.endswith(".xml"))
        root = ET.fromstring(zf.read(xml_name).decode("utf-8-sig"))
        out = []
        for mem in root:
            if (mem.findtext("FilingType") or "").strip() != "P":
                continue
            out.append({
                "first": (mem.findtext("First") or "").strip(),
                "last": (mem.findtext("Last") or "").strip(),
                "doc_id": (mem.findtext("DocID") or "").strip(),
                "filed": (mem.findtext("FilingDate") or "").strip(),
                "year": year,
            })
        return out
    except Exception:
        return None


def _matches(filing: dict, politicians: list[str]) -> str | None:
    for p in politicians:
        parts = p.strip().split()
        if len(parts) < 2:
            continue
        first, last = parts[0].lower(), parts[-1].lower()
        if (filing["last"].lower() == last
                and filing["first"].lower().startswith(first)):
            return p
    return None


def _fetch_doc(doc_id: str, year: int) -> dict | None:
    """Fetch+parse one PTR PDF → cache record. None on failure (fail closed).
    Image-only paper filings come back as {'image_only': True, 'tx': []}."""
    try:
        r = requests.get(f"{CLERK}/ptr-pdfs/{year}/{doc_id}.pdf",
                         headers=UA, timeout=25)
        if not r.ok:
            return None
        text = pdf_text(r.content)
        if len(text.strip()) < MIN_TEXT_CHARS:
            return {"image_only": True, "tx": []}
        return {"image_only": False, "tx": parse_ptr_text(text)}
    except Exception:
        return None


def _compose(txs: list[dict], cfg: dict, now: int) -> dict[str, float]:
    """Replay transactions chronologically into normalized weights."""
    lookback_s = cfg["lookback_days"] * 86400
    half_life = max(float(cfg["half_life_days"]), 1.0)
    book: dict[str, float] = {}
    seen = set()
    dated = []
    for t in txs:
        try:
            ts = int(dt.datetime.strptime(t["date"], "%m/%d/%Y")
                     .replace(tzinfo=dt.timezone.utc).timestamp())
        except Exception:
            continue
        if now - ts > lookback_s:
            continue
        key = (t["sym"], t["kind"], t["date"], t["mid_usd"])
        if key in seen:            # amendments re-list the same rows
            continue
        seen.add(key)
        dated.append((ts, t))
    for ts, t in sorted(dated, key=lambda x: x[0]):
        if t["option"] and not cfg["include_options"]:
            continue
        sym = t["sym"]
        if t["kind"] == "buy":
            age_d = max((now - ts) / 86400, 0.0)
            decay = 2 ** (-age_d / half_life)
            book[sym] = book.get(sym, 0.0) + t["mid_usd"] * decay
        elif t["kind"] == "sell":
            book.pop(sym, None)
        elif t["kind"] == "sell_partial":
            if sym in book:
                book[sym] *= cfg["partial_sale_factor"]
    book = {s: v for s, v in book.items() if v > 0}
    top = dict(sorted(book.items(), key=lambda kv: -kv[1])[:cfg["max_positions"]])
    total = sum(top.values())
    if total <= 0:
        return {}
    return {s: round(v / total, 4) for s, v in
            sorted(top.items(), key=lambda kv: -kv[1])}


def tick() -> None:
    now = int(time.time())
    cfg = _cfg()
    politicians = cfg.get("politicians") or []
    if not politicians:
        log_tick("congress_feed", "no politicians configured — idle")
        return

    year = dt.datetime.now(dt.timezone.utc).year
    filings: list[dict] = []
    for y in (year, year - 1):
        idx = _index(y)
        if idx is None:
            log_tick("congress_feed",
                     f"{y} index fetch failed — holdings unchanged")
            return
        filings += idx

    targets = []
    for f in filings:
        who = _matches(f, politicians)
        if who:
            targets.append({**f, "who": who})
    if not targets:
        log_tick("congress_feed", "no PTR filings for configured politicians")
        return

    cache = _load(CACHE) or {"docs": {}}
    docs = cache["docs"]
    new = [t for t in targets if t["doc_id"] not in docs]
    fetched = 0
    for t in new[:MAX_NEW_PDFS_PER_TICK]:
        rec = _fetch_doc(t["doc_id"], t["year"])
        if rec is None:
            log_tick("congress_feed",
                     f"doc {t['doc_id']} fetch/parse failed — holdings unchanged")
            return
        rec.update({"who": t["who"], "filed": t["filed"]})
        docs[t["doc_id"]] = rec
        fetched += 1
    try:
        CACHE.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass
    if len(new) > MAX_NEW_PDFS_PER_TICK:
        log_tick("congress_feed",
                 f"backlog {len(new) - MAX_NEW_PDFS_PER_TICK} PDFs remain — "
                 "publish deferred")
        return

    txs = []
    for t in targets:
        txs += docs.get(t["doc_id"], {}).get("tx", [])
    holdings = _compose(txs, cfg, now)
    if not holdings:
        log_tick("congress_feed", "no live composition — holdings unchanged")
        return

    prev = _load(HOLDINGS) or {}
    changed = holdings != (prev.get("holdings") or {})
    HOLDINGS.write_text(json.dumps({
        "updated_ts": now,
        "source": "House Clerk PTRs — " + ", ".join(politicians),
        "politicians": politicians,
        "filings_parsed": len(targets),
        "holdings": holdings,
    }, indent=2))
    if changed:
        top = next(iter(holdings))
        emit_live("congress_feed",
                  f"[congress] composition updated: {len(holdings)} names, "
                  f"top {top} {holdings[top] * 100:.1f}%")
    log_tick("congress_feed",
             f"{len(holdings)} names from {len(targets)} PTRs "
             f"(+{fetched} new) changed={changed}")


if __name__ == "__main__":
    tick()
