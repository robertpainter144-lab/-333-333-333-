"""
Patent-cliff scanner v1.

Pulls the FDA Orange Book (small molecules) AND Purple Book (biologics), filters
to branded drugs entering LOE inside a configurable window, joins with a seeded
branded-sales table, and ranks by an opportunity score.

For small molecules: LOE = max(earliest patent expiry, latest exclusivity end).
For biologics:       LOE = BLA approval date + 12 years (reference-product exclusivity).
                     Also flagged if a 351(k) biosimilar has already been licensed.

Read-only research. No trading wiring.

Run:
    python3.10 /Users/quarki3/patent_cliff/scan.py
    python3.10 /Users/quarki3/patent_cliff/scan.py --window-months 36
    python3.10 /Users/quarki3/patent_cliff/scan.py --refresh
    python3.10 /Users/quarki3/patent_cliff/scan.py --orange-only
    python3.10 /Users/quarki3/patent_cliff/scan.py --purple-only
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)

# Browser-UA: FDA file servers 404 our default UA but 200 for browsers
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

ORANGE_BOOK_URLS = [
    "https://www.fda.gov/media/76860/download",
    "https://www.accessdata.fda.gov/cder/orangebookdata.zip",
]
OB_ZIP = DATA / "orangebook.zip"
OB_EXTRACT = DATA / "orangebook_extract"

PURPLE_BOOK_INDEX = "https://purplebooksearch.fda.gov/downloads"
PB_XLSX = DATA / "purplebook.xlsx"

# Biologic reference-product exclusivity (BPCIA Hatch-Waxman analogue)
BIOLOGIC_EXCLUSIVITY_YEARS = 12

PARAGRAPH_IV_INDEX = (
    "https://www.fda.gov/drugs/abbreviated-new-drug-application-anda/"
    "patent-certifications-and-suitability-petitions"
)
PIV_PDF = DATA / "paragraph_iv.pdf"
PIV_CSV = DATA / "paragraph_iv_parsed.csv"


def fetch_orange_book(force: bool = False) -> Path:
    if OB_ZIP.exists() and not force:
        age_days = (datetime.now().timestamp() - OB_ZIP.stat().st_mtime) / 86400
        if age_days < 7:
            return OB_ZIP
    last_err = None
    for url in ORANGE_BOOK_URLS:
        try:
            print(f"Downloading Orange Book from {url} ...", flush=True)
            r = requests.get(url, timeout=60, allow_redirects=True,
                             headers={"User-Agent": "patent-cliff-scanner/0.1"})
            r.raise_for_status()
            if not r.content.startswith(b"PK"):
                raise RuntimeError(f"Response is not a ZIP (got {r.content[:8]!r})")
            OB_ZIP.write_bytes(r.content)
            print(f"  saved {len(r.content) / 1024:.0f} KB to {OB_ZIP}")
            return OB_ZIP
        except Exception as e:
            print(f"  fail: {e}", file=sys.stderr)
            last_err = e
    raise RuntimeError(f"All Orange Book URLs failed; last error: {last_err}")


def load_orange_book() -> dict[str, pd.DataFrame]:
    fetch_orange_book()
    OB_EXTRACT.mkdir(exist_ok=True)
    with zipfile.ZipFile(OB_ZIP) as zf:
        zf.extractall(OB_EXTRACT)
    files = {p.stem.lower(): p for p in OB_EXTRACT.rglob("*.txt")}
    needed = ["products", "patent", "exclusivity"]
    found = {}
    for key in needed:
        match = next((p for name, p in files.items() if key in name), None)
        if match is None:
            raise FileNotFoundError(f"Could not find {key} file in Orange Book extract; have: {list(files)}")
        found[key] = match

    def _read(p: Path) -> pd.DataFrame:
        return pd.read_csv(p, sep="~", dtype=str, keep_default_na=False, na_values=[""])

    out = {}
    for key, path in found.items():
        df = _read(path)
        df.columns = [c.strip() for c in df.columns]
        out[key] = df
    return out


def parse_expiry(s: str | None) -> pd.Timestamp | None:
    if not s or s in {"nan", "NaT"}:
        return None
    s = s.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return pd.Timestamp(datetime.strptime(s, fmt))
        except ValueError:
            continue
    try:
        return pd.Timestamp(s)
    except Exception:
        return None


def load_seed_sales() -> pd.DataFrame:
    df = pd.read_csv(DATA / "branded_sales_seed.csv")
    df["ingredient_key"] = df["ingredient"].str.upper().str.strip()
    df["brand_key"] = df["brand_example"].str.upper().str.strip()
    return df


def load_generic_map() -> dict[str, str]:
    raw = json.loads((DATA / "generic_tickers.json").read_text())
    return {k.upper(): v for k, v in raw.items() if not k.startswith("_")}


# ── Purple Book (biologics) ──────────────────────────────────────────

def fetch_purple_book(force: bool = False) -> Path:
    """Find the most recent .xlsx file linked on Purple Book's downloads page
    and cache it locally. Re-uses cache for 7 days."""
    if PB_XLSX.exists() and not force:
        age_days = (datetime.now().timestamp() - PB_XLSX.stat().st_mtime) / 86400
        if age_days < 7:
            return PB_XLSX

    print(f"Resolving latest Purple Book file from {PURPLE_BOOK_INDEX} ...", flush=True)
    r = requests.get(PURPLE_BOOK_INDEX, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    pattern = re.compile(
        r'https?://[^"\'\s]*PurpleBook/(\d{4})/purplebook-search-([A-Za-z]+)-data-download\.xlsx',
        re.IGNORECASE,
    )
    months = {m.lower(): i for i, m in enumerate(
        ["january","february","march","april","may","june",
         "july","august","september","october","november","december"], start=1)}
    matches = pattern.findall(r.text)
    if not matches:
        raise RuntimeError("No Purple Book .xlsx links found on downloads page")
    # Sort by (year, month-index) desc
    matches_keyed = sorted(matches, key=lambda t: (int(t[0]), months.get(t[1].lower(), 0)), reverse=True)
    year, month = matches_keyed[0]
    url = f"https://www.accessdata.fda.gov/drugsatfda_docs/PurpleBook/{year}/purplebook-search-{month}-data-download.xlsx"
    print(f"  → newest: {year}/{month}.  downloading ...", flush=True)
    rr = requests.get(url, headers={"User-Agent": UA}, timeout=120)
    rr.raise_for_status()
    if not rr.content.startswith(b"PK"):
        raise RuntimeError(f"Purple Book response not a zip: {rr.content[:8]!r}")
    PB_XLSX.write_bytes(rr.content)
    print(f"  saved {len(rr.content)/1024:.0f} KB to {PB_XLSX}")
    return PB_XLSX


def load_purple_book() -> pd.DataFrame:
    fetch_purple_book()
    # Header is at row index 3 (4th row) — first three rows are FDA cover text
    df = pd.read_excel(PB_XLSX, sheet_name=0, header=3)
    df.columns = [str(c).strip() for c in df.columns]
    # Drop blank or non-data rows that sometimes appear at the bottom
    df = df.dropna(subset=["BLA Number", "Proprietary Name"], how="any").copy()
    df["BLA Number"] = df["BLA Number"].astype(str).str.strip()
    df["Proprietary Name"] = df["Proprietary Name"].astype(str).str.strip()
    df["Proper Name"] = df["Proper Name"].astype(str).str.strip()
    df["License Type"] = df["License Type"].astype(str).str.strip()
    df["Applicant"] = df["Applicant"].astype(str).str.strip()
    df["Approval Date"] = pd.to_datetime(df["Approval Date"], errors="coerce")
    return df


def _canonical_proper(name: str) -> str:
    """'Pembrolizumab-aabc' / 'PEMBROLIZUMAB' → 'PEMBROLIZUMAB'. Biosimilars get
    4-letter suffixes; strip them so innovator ↔ biosimilar names join."""
    if not isinstance(name, str):
        return ""
    # Take left of first hyphen, uppercase, strip
    return name.split("-")[0].upper().strip()


def build_purple_cliff_table(window_months: int) -> pd.DataFrame:
    """Biologic LOE = BLA approval + 12y (BPCIA reference exclusivity).
    Also tags whether a 351(k) biosimilar already exists for the same Proper Name."""
    pb = load_purple_book()

    # 351(a) = innovator reference product; 351(k) = biosimilar
    is_innovator = pb["License Type"].str.contains("351(a)", regex=False, na=False)
    is_biosim    = pb["License Type"].str.contains("351(k)", regex=False, na=False)

    innovators = pb[is_innovator].copy()
    biosims    = pb[is_biosim].copy()

    today = pd.Timestamp(datetime.now().date())
    window_end = today + pd.Timedelta(days=window_months * 30)

    # Compute exclusivity end. For products that already have biosimilars licensed,
    # the cliff has technically arrived → set loe_date = today.
    innovators["exclusivity_end"] = innovators["Approval Date"] + pd.DateOffset(
        years=BIOLOGIC_EXCLUSIVITY_YEARS)

    # Canonical proper name (suffix-stripped) for innovator↔biosimilar join
    innovators["canonical"] = innovators["Proper Name"].map(_canonical_proper)
    biosims["canonical"] = biosims["Proper Name"].map(_canonical_proper)

    proper_to_biosim = (biosims.groupby("canonical")["Proprietary Name"]
                       .apply(lambda s: ",".join(sorted(set(s)))).to_dict())

    innovators["biosimilars_approved"] = innovators["canonical"].map(proper_to_biosim).fillna("")
    innovators["has_biosimilar"] = innovators["biosimilars_approved"].astype(bool)

    # Effective LOE date: if biosimilar approved → today; else exclusivity_end
    innovators["loe_date"] = innovators["exclusivity_end"].copy()
    innovators.loc[innovators["has_biosimilar"], "loe_date"] = today

    # Filter to window
    innovators = innovators.dropna(subset=["loe_date"])
    cliffs = innovators[(innovators["loe_date"] >= today) &
                        (innovators["loe_date"] <= window_end)].copy()

    cliffs["months_to_loe"] = (cliffs["loe_date"] - today).dt.days / 30.0
    cliffs["source"] = "purple_book"

    # Normalize column names to merge with orange book later
    cliffs = cliffs.rename(columns={
        "Proper Name": "Ingredient",
        "Proprietary Name": "Trade_Name",
        "Applicant": "Applicant_Full_Name",
    })

    cliffs["ingredient_key"] = cliffs["Ingredient"].str.upper().str.strip()
    cliffs["brand_key"] = cliffs["Trade_Name"].str.upper().str.strip()

    # Aggregate to one row per (Ingredient, Trade_Name) — collapse strength/dose variants
    cliffs = (cliffs
              .sort_values("loe_date")
              .groupby(["Ingredient", "Trade_Name", "Applicant_Full_Name"], as_index=False)
              .agg(loe_date=("loe_date", "min"),
                   months_to_loe=("months_to_loe", "min"),
                   has_biosimilar=("has_biosimilar", "any"),
                   biosimilars_approved=("biosimilars_approved", "first"),
                   approval_date=("Approval Date", "min"),
                   exclusivity_end=("exclusivity_end", "min")))
    cliffs["source"] = "purple_book"
    cliffs["ingredient_key"] = cliffs["Ingredient"].str.upper().str.strip()
    cliffs["brand_key"] = cliffs["Trade_Name"].str.upper().str.strip()
    return cliffs


# ── Paragraph IV first-filer list ───────────────────────────────────

def fetch_paragraph_iv_pdf(force: bool = False) -> Path:
    """Resolve the latest Paragraph IV PDF URL from the FDA landing page
    and download to PIV_PDF. Cached 7 days."""
    if PIV_PDF.exists() and not force:
        age_days = (datetime.now().timestamp() - PIV_PDF.stat().st_mtime) / 86400
        if age_days < 7:
            return PIV_PDF
    print(f"Resolving latest Paragraph IV PDF from {PARAGRAPH_IV_INDEX} ...", flush=True)
    r = requests.get(PARAGRAPH_IV_INDEX, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    # Find the /media/<id>/download link tagged as "Paragraph IV Patent Certifications"
    m = re.search(
        r'href="(/media/\d+/download[^"]*)"[^>]*>[^<]*Paragraph IV Patent Certifications',
        r.text, re.IGNORECASE,
    )
    if not m:
        # Fallback: any /media/<id>/download whose title contains "Paragraph IV"
        m = re.search(
            r'(/media/\d+/download[^"]*)"[^>]*title="[^"]*Paragraph IV',
            r.text, re.IGNORECASE,
        )
    if not m:
        raise RuntimeError("Could not locate Paragraph IV PDF on FDA landing page")
    url = "https://www.fda.gov" + m.group(1)
    print(f"  → {url}", flush=True)
    rr = requests.get(url, headers={"User-Agent": UA}, timeout=180)
    rr.raise_for_status()
    if not rr.content.startswith(b"%PDF"):
        raise RuntimeError(f"Response not a PDF: {rr.content[:8]!r}")
    PIV_PDF.write_bytes(rr.content)
    print(f"  saved {len(rr.content)/1024:.0f} KB to {PIV_PDF}")
    return PIV_PDF


def load_paragraph_iv() -> pd.DataFrame:
    """Parse the IV cert PDF into a DataFrame. Caches a CSV beside the PDF
    so we don't re-parse on every run."""
    fetch_paragraph_iv_pdf()
    if PIV_CSV.exists() and PIV_CSV.stat().st_mtime > PIV_PDF.stat().st_mtime:
        return pd.read_csv(PIV_CSV)

    import pdfplumber
    rows = []
    header = None
    print(f"Parsing Paragraph IV PDF ({PIV_PDF}) ...", flush=True)
    with pdfplumber.open(PIV_PDF) as pdf:
        for i, page in enumerate(pdf.pages):
            for table in page.extract_tables() or []:
                if not table:
                    continue
                # First row of first table looks like the header
                if header is None and any("DRUG NAME" in (c or "").upper() for c in table[0]):
                    header = [(c or "").replace("\n", " ").strip() for c in table[0]]
                    body = table[1:]
                else:
                    body = table
                for r in body:
                    if not r or all((c is None or c == "") for c in r):
                        continue
                    rows.append([(c or "").replace("\n", " ").strip() for c in r])
    if header is None:
        raise RuntimeError("Could not detect IV cert table header")
    # Some pages may have variable column count — pad/truncate
    width = len(header)
    rows = [(r + [""] * width)[:width] for r in rows]
    df = pd.DataFrame(rows, columns=header)
    # Drop spurious rows that don't look like data
    df = df[df["DRUG NAME"].astype(str).str.len() > 1].copy()
    df.to_csv(PIV_CSV, index=False)
    print(f"  cached {len(df)} rows to {PIV_CSV}")
    return df


def apply_paragraph_iv_flags(cliffs: pd.DataFrame) -> pd.DataFrame:
    """For each orange-book cliff, look up the matching IV cert by RLD name
    and attach: iv_status, iv_num_andas, iv_first_submission, iv_ftf_launch."""
    if cliffs.empty:
        for c in ("iv_status", "iv_num_andas", "iv_first_submission", "iv_ftf_launch"):
            cliffs[c] = None
        return cliffs
    try:
        piv = load_paragraph_iv()
    except Exception as e:
        print(f"Paragraph IV pull failed: {e}", file=sys.stderr)
        for c in ("iv_status", "iv_num_andas", "iv_first_submission", "iv_ftf_launch"):
            cliffs[c] = None
        return cliffs

    # Extract RLD trade name (first token before newline/space-newline) from RLD/NDA column
    piv = piv.copy()
    piv["rld_brand"] = piv["RLD/NDA"].astype(str).str.split().str[0].str.upper()
    def _join_unique(s):
        vals = sorted({str(x).strip() for x in s if x and str(x).strip().lower() not in ("nan", "")})
        return ",".join(vals)
    def _min_str(s):
        vals = [str(x).strip() for x in s if x and str(x).strip().lower() not in ("nan", "")]
        return min(vals) if vals else ""

    # Best status per RLD trade name (one cert may have multiple rows for strengths)
    grouped = (piv.groupby("rld_brand", as_index=False)
               .agg(iv_status=("180-DAY STATUS", _join_unique),
                    iv_num_andas=("NUMBER OF ANDAs SUBMITTED", _join_unique),
                    iv_first_submission=("DATE OF SUBMISSION", _min_str),
                    iv_ftf_launch=("DATE OF FIRST COMMERCIAL MARKETING BY FTF", _join_unique)))

    cliffs["rld_key"] = cliffs["Trade_Name"].astype(str).str.split().str[0].str.upper()
    out = cliffs.merge(grouped, left_on="rld_key", right_on="rld_brand", how="left")
    out = out.drop(columns=["rld_brand"])
    return out


# ── Orange Book (small molecules) ────────────────────────────────────

def build_orange_cliff_table(window_months: int) -> pd.DataFrame:
    ob = load_orange_book()
    products = ob["products"]
    patents = ob["patent"]
    exclusivity = ob["exclusivity"]

    # Normalize join keys
    for df in (products, patents, exclusivity):
        for col in ("Appl_No", "Product_No", "Appl_Type"):
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

    # Patents — parse expiry
    patents = patents.copy()
    patents["expiry"] = patents["Patent_Expire_Date_Text"].map(parse_expiry)
    patents = patents.dropna(subset=["expiry"])

    today = pd.Timestamp(datetime.now().date())
    window_end = today + pd.Timedelta(days=window_months * 30)
    patents = patents[(patents["expiry"] >= today) & (patents["expiry"] <= window_end)]

    # Join patent → product
    merged = patents.merge(
        products[["Appl_Type", "Appl_No", "Product_No", "Ingredient", "Trade_Name",
                  "Applicant_Full_Name", "DF;Route", "Strength", "Approval_Date", "RLD"]],
        on=["Appl_Type", "Appl_No", "Product_No"],
        how="left",
    )

    # Keep only NDA (branded) products — drop ANDA/generics
    merged = merged[merged["Appl_Type"] == "N"]
    merged = merged.dropna(subset=["Ingredient"])

    # One row per (ingredient, brand, applicant, patent) — take earliest expiry per brand
    grouped = (merged
               .sort_values("expiry")
               .groupby(["Ingredient", "Trade_Name", "Applicant_Full_Name"], as_index=False)
               .agg(earliest_patent_expiry=("expiry", "min"),
                    patent_count=("Patent_No", "nunique"),
                    patents=("Patent_No", lambda s: ",".join(sorted(set(s))))))

    # Layer in exclusivity — latest exclusivity end per NDA
    exc = exclusivity.copy()
    exc["exc_date"] = exc["Exclusivity_Date"].map(parse_expiry)
    exc = exc[exc["Appl_Type"] == "N"].dropna(subset=["exc_date"])
    exc_max = (exc.groupby(["Appl_Type", "Appl_No"])["exc_date"].max()
               .reset_index().rename(columns={"exc_date": "latest_exclusivity"}))
    # Map exclusivity onto grouped via Trade_Name
    nda_brand = products[products["Appl_Type"] == "N"][["Appl_Type", "Appl_No", "Trade_Name"]].drop_duplicates()
    exc_max = exc_max.merge(nda_brand, on=["Appl_Type", "Appl_No"], how="left")
    grouped = grouped.merge(exc_max[["Trade_Name", "latest_exclusivity"]].drop_duplicates(),
                            on="Trade_Name", how="left")

    # Effective LOE date = max(patent_expiry, latest_exclusivity)
    grouped["loe_date"] = grouped[["earliest_patent_expiry", "latest_exclusivity"]].max(axis=1)
    grouped["months_to_loe"] = (grouped["loe_date"] - today).dt.days / 30.0
    grouped["source"] = "orange_book"
    grouped["ingredient_key"] = grouped["Ingredient"].str.upper().str.strip()
    grouped["brand_key"] = grouped["Trade_Name"].str.upper().str.strip()
    return grouped


# ── Shared sales-join + scoring ───────────────────────────────────────

def apply_sales_and_score(df: pd.DataFrame) -> pd.DataFrame:
    """Annotate a pre-LOE table with sales_usd_b, time_weight, score.
    Expects columns: ingredient_key, brand_key, months_to_loe."""
    if df.empty:
        for col in ("sales_usd_b", "time_weight", "score"):
            df[col] = 0.0
        return df

    sales = load_seed_sales()
    by_ing = sales.set_index("ingredient_key")["approx_2024_us_sales_usd_b"].to_dict()
    by_brand = sales.set_index("brand_key")["approx_2024_us_sales_usd_b"].to_dict()

    def _sales(row):
        for bk, v in by_brand.items():
            for tok in bk.split("/"):
                if tok and tok in row["brand_key"]:
                    return v
        return by_ing.get(row["ingredient_key"], 0.0)

    df["sales_usd_b"] = df.apply(_sales, axis=1)

    def _time_weight(months: float) -> float:
        if months is None or months != months:  # NaN guard
            return 0.0
        if months <= 0:
            return 0.0
        if months <= 12:
            return months / 12.0
        if months <= 36:
            return max(0.0, (36 - months) / 24.0)
        return 0.0

    df["time_weight"] = df["months_to_loe"].map(_time_weight)
    df["score"] = df["sales_usd_b"] * df["time_weight"]
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def build_combined_cliffs(window_months: int, include_orange: bool = True,
                          include_purple: bool = True) -> pd.DataFrame:
    frames = []
    if include_orange:
        try:
            frames.append(build_orange_cliff_table(window_months))
        except Exception as e:
            print(f"Orange Book pull failed: {e}", file=sys.stderr)
    if include_purple:
        try:
            frames.append(build_purple_cliff_table(window_months))
        except Exception as e:
            print(f"Purple Book pull failed: {e}", file=sys.stderr)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True, sort=False)
    combined = apply_sales_and_score(combined)
    return apply_paragraph_iv_flags(combined)


def build_generic_exposure(cliffs: pd.DataFrame) -> pd.DataFrame:
    """For each cliff, identify likely generic/biosimilar filers and roll up by ticker.

    Strategy:
      - Orange-book cliff: look in the FULL Orange Book products table for ANDAs (Appl_Type='A')
        with the same Ingredient. Those Applicant_Full_Names are existing generic
        makers — a strong proxy for who'll file when the brand goes off-patent.
      - Purple-book cliff: look in the full Purple Book for 351(k) biosimilars with
        the same Proper Name. Those Applicants are existing biosimilar makers.
      - Map applicant name fragments (UPPERCASED, normalized) to tickers via
        data/generic_tickers.json.
      - Aggregate score per ticker (sum of cliff scores they're exposed to).
    """
    if cliffs.empty:
        return pd.DataFrame()

    name_to_ticker = load_generic_map()

    def _match_ticker(applicant: str) -> str | None:
        up = (applicant or "").upper()
        for frag, tkr in name_to_ticker.items():
            if frag in up:
                return tkr
        return None

    rows = []

    # ── Orange-book exposures ───────────────────────────────────────
    ob_cliffs = cliffs[cliffs["source"] == "orange_book"]
    if not ob_cliffs.empty:
        ob = load_orange_book()
        anda = ob["products"]
        anda = anda[anda["Appl_Type"].astype(str).str.strip() == "A"].copy()
        anda["ingredient_key"] = anda["Ingredient"].astype(str).str.upper().str.strip()
        anda_by_ing = anda.groupby("ingredient_key")["Applicant_Full_Name"].apply(
            lambda s: sorted(set(s.astype(str)))).to_dict()
        for _, c in ob_cliffs.iterrows():
            filers = anda_by_ing.get(c["ingredient_key"], [])
            for applicant in filers:
                tkr = _match_ticker(applicant)
                if tkr:
                    rows.append({"ticker": tkr, "applicant": applicant,
                                 "brand": c["Trade_Name"], "ingredient": c["Ingredient"],
                                 "loe_date": c["loe_date"], "cliff_score": c["score"],
                                 "source": "orange_book"})

    # ── Purple-book exposures (biosimilars of the same Proper Name) ─
    pb_cliffs = cliffs[cliffs["source"] == "purple_book"]
    if not pb_cliffs.empty:
        pb = load_purple_book()
        biosims = pb[pb["License Type"].str.contains("351(k)", regex=False, na=False)].copy()
        # Use canonical (suffix-stripped) Proper Name so "pembrolizumab-aabc" matches "PEMBROLIZUMAB"
        biosims["canonical"] = biosims["Proper Name"].map(_canonical_proper)
        biosim_by_ing = biosims.groupby("canonical")["Applicant"].apply(
            lambda s: sorted(set(s.astype(str)))).to_dict()
        for _, c in pb_cliffs.iterrows():
            canon = _canonical_proper(c["Ingredient"])
            filers = biosim_by_ing.get(canon, [])
            for applicant in filers:
                tkr = _match_ticker(applicant)
                if tkr:
                    rows.append({"ticker": tkr, "applicant": applicant,
                                 "brand": c["Trade_Name"], "ingredient": c["Ingredient"],
                                 "loe_date": c["loe_date"], "cliff_score": c["score"],
                                 "source": "purple_book"})

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Per-(ticker, brand) collapse so two ANDAs by same maker for same brand only count once
    per_position = (df.sort_values("cliff_score", ascending=False)
                    .drop_duplicates(subset=["ticker", "brand"]))
    # Per-ticker rollup
    agg = (per_position.groupby("ticker", as_index=False)
           .agg(total_cliff_exposure=("cliff_score", "sum"),
                cliff_count=("brand", "nunique"),
                top_brands=("brand", lambda s: ",".join(sorted(set(s))[:5]))))
    return agg.sort_values("total_cliff_exposure", ascending=False).reset_index(drop=True)


def write_first_filer_opportunities(df: pd.DataFrame) -> Path | None:
    """Filter to the high-EV first-filer trades:
       - 180-DAY STATUS = Eligible (not forfeited/extinguished)
       - low ANDA count (concentrated upside)
       - no commercial marketing yet (trade not over)
       - non-zero sales (something to compete for)
    Rank by sales × time_weight × concentration_bonus."""
    if df.empty or "iv_status" not in df.columns:
        return None
    cand = df[df["iv_status"].notna() & (df["iv_status"].astype(str) != "")].copy()
    if cand.empty:
        return None

    def _num_andas_min(s):
        try:
            parts = [int(x.strip()) for x in str(s).split(",") if x.strip().isdigit()]
            return min(parts) if parts else 999
        except Exception:
            return 999

    cand["andas_min"] = cand["iv_num_andas"].map(_num_andas_min)
    cand["eligible"]  = cand["iv_status"].astype(str).str.contains("Eligible")
    # "Launched" iff the IV cert lists a real FTF commercial-marketing date.
    # NaN cells become string "nan" after astype, so normalize first.
    _ftf = cand["iv_ftf_launch"].fillna("").astype(str).str.strip().str.lower()
    cand["launched"] = (_ftf != "") & (_ftf != "nan")
    # Concentration bonus: 1 ANDA → 3x, 2-3 → 2x, 4-6 → 1.5x, >6 → 1x
    def _conc(n):
        if n <= 1: return 3.0
        if n <= 3: return 2.0
        if n <= 6: return 1.5
        return 1.0
    cand["concentration_bonus"] = cand["andas_min"].map(_conc)
    cand["first_filer_score"] = (cand["sales_usd_b"] * cand["time_weight"]
                                 * cand["concentration_bonus"]
                                 * cand["eligible"].astype(int)
                                 * (~cand["launched"]).astype(int))
    cand = cand[cand["first_filer_score"] > 0].sort_values(
        "first_filer_score", ascending=False).reset_index(drop=True)
    if cand.empty:
        return None
    ts = datetime.now().strftime("%Y-%m-%d")
    out_path = OUTPUT / f"first_filer_opportunities_{ts}.csv"
    cand.to_csv(out_path, index=False)
    print()
    print("=== First-filer opportunities (Eligible + low-ANDA + not-yet-launched) ===")
    cols = ["Trade_Name", "Ingredient", "Applicant_Full_Name", "loe_date",
            "sales_usd_b", "andas_min", "iv_status", "iv_first_submission",
            "first_filer_score"]
    show = cand[[c for c in cols if c in cand.columns]].head(20).copy()
    if "loe_date" in show.columns:
        show["loe_date"] = pd.to_datetime(show["loe_date"]).dt.strftime("%Y-%m-%d")
    show["first_filer_score"] = show["first_filer_score"].round(2)
    show["sales_usd_b"] = show["sales_usd_b"].round(2)
    print(show.to_string(index=False))
    print(f"\nWrote {len(cand)} rows to {out_path}")
    return out_path


def write_outputs(df: pd.DataFrame, window_months: int) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d")
    csv_path = OUTPUT / f"cliff_scan_{ts}.csv"
    df.to_csv(csv_path, index=False)

    top = df.head(30).copy()
    print()
    print(f"=== Top patent/biologic-cliff candidates (next {window_months} months) ===")
    cols = ["source", "Ingredient", "Trade_Name", "Applicant_Full_Name",
            "loe_date", "months_to_loe", "sales_usd_b", "score"]
    fmt = top[[c for c in cols if c in top.columns]].copy()
    if "loe_date" in fmt.columns:
        fmt["loe_date"] = pd.to_datetime(fmt["loe_date"]).dt.strftime("%Y-%m-%d")
    if "months_to_loe" in fmt.columns:
        fmt["months_to_loe"] = fmt["months_to_loe"].round(1)
    if "sales_usd_b" in fmt.columns:
        fmt["sales_usd_b"] = fmt["sales_usd_b"].round(2)
    if "score" in fmt.columns:
        fmt["score"] = fmt["score"].round(2)
    print(fmt.to_string(index=False))
    print()
    print(f"Wrote {len(df)} rows to {csv_path}")
    return csv_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window-months", type=int, default=24,
                    help="Forward-look window for LOE (default 24)")
    ap.add_argument("--refresh", action="store_true",
                    help="Force re-download of Orange Book + Purple Book")
    ap.add_argument("--orange-only", action="store_true",
                    help="Only scan FDA Orange Book (small molecules)")
    ap.add_argument("--purple-only", action="store_true",
                    help="Only scan FDA Purple Book (biologics)")
    args = ap.parse_args()

    if args.refresh:
        for p in (OB_ZIP, PB_XLSX):
            if p.exists():
                p.unlink()

    df = build_combined_cliffs(
        args.window_months,
        include_orange=not args.purple_only,
        include_purple=not args.orange_only,
    )
    if df.empty:
        print("No cliff candidates in window. Try widening --window-months.")
        return
    write_outputs(df, args.window_months)
    write_first_filer_opportunities(df)

    # Generic-manufacturer exposure rollup
    exposure = build_generic_exposure(df)
    if not exposure.empty:
        ts = datetime.now().strftime("%Y-%m-%d")
        exp_path = OUTPUT / f"generic_exposure_{ts}.csv"
        exposure.to_csv(exp_path, index=False)
        print()
        print("=== Generic / biosimilar maker exposure to cliffs in window ===")
        fmt = exposure.copy()
        fmt["total_cliff_exposure"] = fmt["total_cliff_exposure"].round(2)
        print(fmt.to_string(index=False))
        print(f"\nWrote {len(exposure)} rows to {exp_path}")


if __name__ == "__main__":
    main()
