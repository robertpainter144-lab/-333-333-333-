"""
SEC EDGAR + Claude signal extractor.

For each US-listed ticker in data/generic_tickers.json, fetches the latest 10-K
(or 20-F for ADRs), extracts the Risk Factors and Competition discussions, and
asks Claude to surface forward-looking generic-launch / Paragraph IV / biosimilar
intent.

Output: data/sec_signals.csv — one row per (ticker, drug_mentioned) signal.

No-ops if ANTHROPIC_API_KEY is unset. SEC EDGAR has no key requirement but
requires a User-Agent header with contact info.

Run:
    python3.10 /Users/quarki3/patent_cliff/sec_signals.py
    python3.10 /Users/quarki3/patent_cliff/sec_signals.py --tickers TEVA,VTRS,AMGN
    python3.10 /Users/quarki3/patent_cliff/sec_signals.py --refresh
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CACHE = DATA / "sec_cache"
CACHE.mkdir(exist_ok=True)
OUT_CSV = DATA / "sec_signals.csv"

UA = "Robert Painter patent-cliff-scanner robert.m.painter@gmail.com"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_HEADERS = {"User-Agent": UA, "Accept": "application/json,text/html"}

# Only attempt for tickers that file with SEC. Skip foreign-domestic-only (.NS, .L,
# .KS, .DE) — they may have ADRs but those file 20-F, which is also caught here.
US_LISTED_REGEX = re.compile(r"^[A-Z]{1,5}$")


def load_target_tickers(filter_list: list[str] | None = None) -> list[str]:
    raw = json.loads((DATA / "generic_tickers.json").read_text())
    tickers = sorted({v for k, v in raw.items() if not k.startswith("_")})
    out = [t for t in tickers if US_LISTED_REGEX.match(t)]
    if filter_list:
        wanted = {t.upper().strip() for t in filter_list}
        out = [t for t in out if t in wanted]
    return out


# ── SEC EDGAR ─────────────────────────────────────────────────────────

def _sec_get(url: str, retries: int = 3, sleep: float = 0.2) -> requests.Response:
    last = None
    for i in range(retries):
        r = requests.get(url, headers=SEC_HEADERS, timeout=30)
        if r.status_code == 200:
            time.sleep(sleep)  # SEC asks for ≤ 10 req/sec
            return r
        last = r
        time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"SEC {url} → {last.status_code}")


def ticker_to_cik(ticker: str) -> str | None:
    cache_file = CACHE / "company_tickers.json"
    if not cache_file.exists() or (datetime.now().timestamp() - cache_file.stat().st_mtime) > 86400:
        r = _sec_get(SEC_TICKERS_URL)
        cache_file.write_bytes(r.content)
    data = json.loads(cache_file.read_text())
    for row in data.values():
        if row.get("ticker", "").upper() == ticker.upper():
            return str(row["cik_str"]).zfill(10)
    return None


def latest_filing(cik: str, forms=("10-K", "20-F", "10-Q")) -> dict | None:
    """Return {accession, primary_doc, form, filing_date} for the most recent
    matching filing, or None."""
    r = _sec_get(SEC_SUBMISSIONS_URL.format(cik=cik))
    sub = r.json()
    recent = sub["filings"]["recent"]
    for i, form in enumerate(recent["form"]):
        if form in forms:
            return {
                "form": form,
                "accession": recent["accessionNumber"][i],
                "primary_doc": recent["primaryDocument"][i],
                "filing_date": recent["filingDate"][i],
            }
    return None


def fetch_filing_text(cik: str, accession: str, primary_doc: str) -> str:
    accession_nodash = accession.replace("-", "")
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{primary_doc}"
    cache_file = CACHE / f"{cik}_{accession}.html"
    if cache_file.exists():
        return cache_file.read_text()
    r = _sec_get(url)
    cache_file.write_text(r.text)
    return r.text


def extract_relevant_sections(html: str, max_chars: int = 80_000) -> str:
    """Strip HTML, extract risk-factors + competition + intellectual-property +
    pipeline sections. Crude but cheap."""
    from html.parser import HTMLParser

    class Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
        def handle_data(self, d):
            self.parts.append(d)

    s = Stripper()
    s.feed(html)
    text = " ".join(s.parts)
    text = re.sub(r"\s+", " ", text).strip()

    # Find anchor headings (case-insensitive) and grab the section after each.
    # Crude: take everything from the heading until the next "Item N" heading.
    anchors = [
        r"Risk Factors",
        r"Competition",
        r"Intellectual Property",
        r"Patents",
        r"Government Regulation",
        r"Pipeline",
        r"Generic",
        r"Biosimilar",
        r"Paragraph IV",
    ]
    chunks = []
    for a in anchors:
        for m in re.finditer(rf"\b{a}\b", text, re.IGNORECASE):
            start = m.start()
            # Find the next "Item N." heading after start
            stop_m = re.search(r"\bItem\s+\d+[A-Z]?\.\s", text[start + 50:], re.IGNORECASE)
            stop = (start + 50 + stop_m.start()) if stop_m else min(len(text), start + 6000)
            chunks.append(text[start:stop])
    if not chunks:
        chunks = [text[:max_chars]]
    combined = "\n\n---\n\n".join(dict.fromkeys(chunks))  # de-dup, preserve order
    return combined[:max_chars]


# ── Claude extraction ─────────────────────────────────────────────────

SYSTEM = """You are a pharma equity analyst extracting forward-looking signals from SEC filings.

The user will paste relevant sections (Risk Factors, Competition, Intellectual Property, Pipeline) of a single company's most recent SEC filing.

Your task: identify forward-looking statements about that company's plans, intentions, or expectations for:
  (a) Generic drug launches (ANDA filings, Paragraph IV certifications, first-filer status, 180-day exclusivity)
  (b) Biosimilar launches or interchangeability designations
  (c) Patent litigation outcomes that gate a launch (settled, won, lost, pending)
  (d) Authorized-generic agreements or pay-for-delay settlements
  (e) Loss-of-exclusivity (LOE) on the company's OWN brand-name drugs that will pressure revenue

Focus on FORWARD-LOOKING signals — what the company says it expects, plans, or anticipates. Ignore purely historical statements.

Be conservative. If a section doesn't mention any of the above, return an empty list — do not invent signals. Confidence should reflect specificity: a dated launch plan ("expected Q3 2026") is high confidence; a generic boilerplate disclosure is low."""


def _signal_schema():
    """Build a Pydantic model for structured extraction."""
    from pydantic import BaseModel, Field

    class Signal(BaseModel):
        signal_type: Literal[
            "generic_launch_plan",
            "biosimilar_launch_plan",
            "paragraph_iv_first_filer",
            "patent_litigation_outcome",
            "authorized_generic_deal",
            "own_brand_loe_pressure",
        ] = Field(description="Category of signal")
        drug_mentioned: str = Field(description="Brand or ingredient name of the drug, UPPERCASE")
        target_event_window: str = Field(
            description="When the company expects the event (e.g. '2026 Q4', '2027', 'pending litigation outcome', or 'unspecified')"
        )
        sentiment: Literal["positive", "negative", "neutral"] = Field(
            description="positive = revenue accretion likely; negative = revenue at risk; neutral = ambiguous"
        )
        confidence: float = Field(ge=0.0, le=1.0, description="0=boilerplate, 1=dated specific plan")
        evidence_quote: str = Field(description="Direct quote (≤30 words) supporting the signal")

    class Response(BaseModel):
        signals: list[Signal] = Field(description="Signals extracted from the filing. Empty list if none.")

    return Response


def extract_signals_for_ticker(ticker: str, text: str) -> list[dict]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return []
    import anthropic
    Response = _signal_schema()

    client = anthropic.Anthropic()
    try:
        # Adaptive thinking + xhigh effort for extraction quality on Opus 4.7
        result = client.messages.parse(
            model="claude-opus-4-7",
            max_tokens=8000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            output_format=Response,
            messages=[{
                "role": "user",
                "content": f"Ticker: {ticker}\n\nFiling sections:\n\n{text}",
            }],
        )
        return [s.model_dump() | {"ticker": ticker} for s in result.parsed_output.signals]
    except Exception as e:
        print(f"  [Claude error for {ticker}] {e}", file=sys.stderr)
        return []


# ── Pipeline ─────────────────────────────────────────────────────────

def run(tickers: list[str], refresh: bool = False) -> Path:
    if refresh and OUT_CSV.exists():
        OUT_CSV.unlink()

    schema_cols = ["ticker", "signal_type", "drug_mentioned",
                   "target_event_window", "sentiment", "confidence",
                   "evidence_quote", "form", "filing_date"]

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set — sec_signals.py is a no-op.")
        print("To enable: add `export ANTHROPIC_API_KEY=<your-real-key>` to ~/.zshenv and re-source.")
        pd.DataFrame(columns=schema_cols).to_csv(OUT_CSV, index=False)
        return OUT_CSV

    all_signals = []
    for ticker in tickers:
        print(f"[{ticker}]", flush=True)
        cik = ticker_to_cik(ticker)
        if not cik:
            print(f"  no CIK for {ticker} — skip")
            continue
        filing = latest_filing(cik)
        if not filing:
            print(f"  no recent 10-K/20-F/10-Q for {ticker}")
            continue
        print(f"  {filing['form']} filed {filing['filing_date']}", flush=True)
        try:
            html = fetch_filing_text(cik, filing["accession"], filing["primary_doc"])
        except Exception as e:
            print(f"  fetch error: {e}", file=sys.stderr)
            continue
        text = extract_relevant_sections(html)
        if len(text) < 500:
            print(f"  warning: only {len(text)} chars extracted — filing may be exhibit-only")
            continue
        sigs = extract_signals_for_ticker(ticker, text)
        for s in sigs:
            s["form"] = filing["form"]
            s["filing_date"] = filing["filing_date"]
        print(f"  → {len(sigs)} signals")
        all_signals.extend(sigs)

    df = pd.DataFrame(all_signals)
    # Always write with the canonical column header so downstream consumers
    # (portfolio.py, dashboard.py) can read an empty result without crashing.
    if df.empty:
        df = pd.DataFrame(columns=schema_cols)
    df.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {len(df)} signals to {OUT_CSV}")
    if not df.empty:
        print("\nTop confidence signals:")
        top = df.sort_values("confidence", ascending=False).head(10)
        cols = ["ticker", "signal_type", "drug_mentioned", "target_event_window",
                "sentiment", "confidence", "evidence_quote"]
        print(top[[c for c in cols if c in top.columns]].to_string(index=False))
    return OUT_CSV


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", help="Comma-separated ticker subset")
    ap.add_argument("--refresh", action="store_true", help="Wipe cache + output and re-pull")
    args = ap.parse_args()
    if args.refresh:
        for p in CACHE.glob("*"):
            p.unlink()
    filt = [t.strip() for t in args.tickers.split(",")] if args.tickers else None
    tickers = load_target_tickers(filt)
    if not tickers:
        print("No US-listed tickers to process.")
        return
    print(f"Processing {len(tickers)} tickers: {', '.join(tickers)}\n")
    run(tickers, refresh=args.refresh)


if __name__ == "__main__":
    main()
