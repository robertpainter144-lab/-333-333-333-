---
name: Patent Cliff Scanner
description: Read-only FDA Orange Book + Purple Book + Paragraph IV + Claude SEC-signal extractor + Alpaca paper trading + Plotly dashboard; v2 functional 2026-06-26
type: project
originSessionId: d207120d-c6c4-4118-a720-bca459d749a8
---
End-to-end research → ranking → paper-trading pipeline for pharma patent-cliff opportunities. Built 2026-06-26 across a single session while waiting on Oracle A1 capacity for the headless IBKR gateway.

**Location:** `/Users/quarki3/patent_cliff/`

**Data sources (live):**
| Source | What | Update cadence |
|---|---|---|
| FDA Orange Book ZIP | Small-molecule patents + exclusivities by NDA | Weekly |
| FDA Purple Book XLSX | Biologics + biosimilars by BLA | Monthly |
| FDA Paragraph IV PDF | ANDA first-filer status + 180-day exclusivity | Roughly monthly |
| SEC EDGAR | 10-K / 20-F / 10-Q for US-listed generic makers | Quarterly + ad-hoc |
| Claude API | Forward-looking generic-launch signals extracted from filings | Per run |

**Files:**
| File | Purpose |
|---|---|
| `scan.py` | FDA pulls + cliff scoring + first-filer ranking + generic-maker exposure rollup |
| `sec_signals.py` | Per-ticker SEC EDGAR fetch + Claude (`claude-opus-4-7`) structured extraction of forward-looking signals. Graceful no-op without `ANTHROPIC_API_KEY` |
| `portfolio.py` | Combines all signals into ranked ticker list + sizes + fires Alpaca paper orders |
| `dashboard.py` | Single self-contained HTML with Plotly tables + bar charts + LOE timeline |
| `run.py` | Orchestrator. Default dry-run. `--paper` to fire Alpaca paper orders. `--live` gated. |
| `data/branded_sales_seed.csv` | ~60-drug seed of approx US sales for cliff ranking |
| `data/generic_tickers.json` | Manufacturer-name fragment → ticker map (TEVA, VTRS, RDY, SDZNY, etc.) |
| `data/orangebook.zip`, `data/purplebook.xlsx`, `data/paragraph_iv.pdf` | Cached FDA pulls (7-day TTL) |
| `data/sec_cache/` | Cached SEC EDGAR responses |
| `data/sec_signals.csv` | Claude-extracted forward-looking SEC signals |
| `output/cliff_scan_YYYY-MM-DD.csv` | Main ranked output (Orange + Purple combined) |
| `output/first_filer_opportunities_YYYY-MM-DD.csv` | Eligible + low ANDA count + not yet launched |
| `output/generic_exposure_YYYY-MM-DD.csv` | Per-ticker cumulative cliff exposure |
| `output/portfolio_picks_*.csv` | Sized picks from last portfolio.py run |
| `output/alpaca_orders_*.csv` | Last Alpaca paper order results |
| `output/dashboard.html` | Self-contained HTML dashboard (Plotly via CDN) |

**Run:**
```bash
# Default — full pipeline, dry-run, builds dashboard, no orders
python3.10 /Users/quarki3/patent_cliff/run.py

# Fire Alpaca paper orders (~$200 each into top 5 generic-maker tickers)
python3.10 /Users/quarki3/patent_cliff/run.py --paper

# Skip the LLM step (cheaper, faster, useful when ANTHROPIC_API_KEY is unset)
python3.10 /Users/quarki3/patent_cliff/run.py --no-sec

# Force re-download of all FDA + SEC caches
python3.10 /Users/quarki3/patent_cliff/run.py --refresh

# Open the dashboard in browser after run
python3.10 /Users/quarki3/patent_cliff/run.py --open
```

**Trading rules baked into portfolio.py:**
- Default mode is **dry-run** — prints picks, places no orders.
- `--paper` fires Alpaca paper orders via `alpaca-py` (uses `ALPACA_PAPER_KEY` / `ALPACA_PAPER_SECRET` from `~/.zshenv`).
- `--live` is intentionally disabled. To enable, both `--live` AND `--strategy-proven-on-paper` must be passed. Even then, the file enforces a $20 aggregate IBKR exposure cap (per `feedback_trade_threshold.md`) and refuses to actually place orders — directs the user to the existing `~/ibkr_bot/` "mother bird" pattern with manual approval.
- Per `feedback_paper_vs_real.md`: "Real money is never for experimenting; only deployed after paper proves a strategy."

**Scoring:**
- `cliff.score` = `sales_usd_b × time_weight` (triangular peak at 12mo)
- `first_filer_score` = `cliff.score × concentration_bonus × eligible × not_launched`
  - concentration_bonus: 1 ANDA=3×, 2-3=2×, 4-6=1.5×, >6=1×
- `exposure.total_cliff_exposure` = sum of `cliff.score` for each ticker's likely ANDA filings
- `portfolio.combined_score` = `base_exposure + first_filer_bonus + sec_signal_score`
  - first_filer_bonus = overlap_count × 0.5 (capped at 1.5)
  - sec_signal_score = sum(confidence × sentiment_sign) over per-ticker signals

**LLM module specifics (`sec_signals.py`):**
- Model: `claude-opus-4-7` with adaptive thinking + `effort: "high"`
- Structured outputs: Pydantic schema with `signal_type` literal, `drug_mentioned`, `target_event_window`, `sentiment`, `confidence`, `evidence_quote`
- Per-ticker: fetches latest 10-K/20-F/10-Q from SEC EDGAR (User-Agent: Robert's contact info), extracts Risk Factors + Competition + IP + Pipeline sections, sends ~80KB context to Claude
- Cost: ~$1-3 per ticker run with current scope and Opus 4.7 pricing — cheap relative to value
- **Gated on `ANTHROPIC_API_KEY` env var.** Without it, sec_signals.py emits an empty CSV and the pipeline continues without LLM enrichment.

**v2 results (run 2026-06-26 / 24-mo window, no SEC because no API key):**
- Top scored cliffs: OZEMPIC, KEYTRUDA (2.3mo!), ELIQUIS, DARZALEX, JARDIANCE, GARDASIL 9, OPDIVO, FARXIGA, COSENTYX, REVLIMID
- Top first-filer opportunity: XTANDI ($2.5B sales, 1 ANDA filed, Eligible status, LOE 2026-11) — concentrated trade
- Top generic-maker tickers: VTRS, RDY, TEVA, AMRX, SDZNY (all US-listed; Indian generics excluded from Alpaca paper but visible in exposure CSV)
- Portfolio (top 5, $1000 budget): VTRS $200, RDY $200, TEVA $200, AMRX $200, SDZNY $200

**Known limitations / v2 gaps:**
- Sales seed covers ~60 names; many smaller cliff candidates score 0. Append to `data/branded_sales_seed.csv` to expand.
- Triangular time-weight is heuristic; not calibrated to historical brand-price-decline curves.
- Paragraph IV PDF parse is brittle to FDA reformatting. If FDA changes the table layout, the parser breaks.
- SEC EDGAR fetch is text-only — no XBRL parsing for fundamentals (revenue, margins). Could add for ranking quality.
- Equal-weight sizing only. No volatility scaling, no Kelly. Defensible default for a small budget.
- No backtest. The strategy's edge over equal-weight generic-basket buy-and-hold is unverified.
- Dashboard uses Plotly CDN — needs internet to render.
- IBKR live wiring is DELIBERATELY not implemented in `portfolio.py`. To go live, use existing `~/ibkr_bot/` with manual approval per the mother-bird pattern.

**Open Pushover / scheduling items (not built):**
- Daily cron / launchd job for `run.py --paper`
- Pushover on portfolio drift > N% (e.g. cliff scores jumped, new high-confidence SEC signal landed)

**Why this exists:** Robert wanted a new research vertical orthogonal to his equities momentum/mean-rev work; pharma patent cliffs scratch that itch. v2 added the LLM signal extraction + paper trading wiring + dashboard so the research becomes actionable rather than purely descriptive.

**How to apply:**
- For day-to-day research: `python3.10 run.py` then check `output/dashboard.html`.
- To paper-trade: `python3.10 run.py --paper` (uses Alpaca paper account, equity tracked at `app.alpaca.markets` once `PKNEW4EW6GHCKCDDLZAZSUFBQZ` paper keys are visible).
- To enable Claude SEC extraction: `export ANTHROPIC_API_KEY=sk-ant-...` in `~/.zshenv` and re-source.
- To extend universe: add tickers to `data/generic_tickers.json` (manufacturer-name fragment → ticker).
- For NEW cliff drugs missing from output: append to `data/branded_sales_seed.csv` with ingredient + brand_example + approximate US sales.
