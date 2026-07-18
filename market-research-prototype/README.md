# Market Research Prototype

**Paste a startup description → get a McKinsey-style market research report in 3-5 minutes.**

Output includes a 1-100 viability score, TAM/SAM/SOM with shown arithmetic, 3 buyer personas with recommended wedge, 3-year revenue scenarios with break-even year, competitive map, real customer voice insights, and a 4Ps marketing plan with citations. Renders as a polished HTML report (Cmd+P → PDF) plus a one-page investor summary.

```bash
./install.sh                                # creates .venv, installs deps
echo "GEMINI_API_KEY=..." >> .env           # or GROQ_API_KEY or ANTHROPIC_API_KEY
.venv/bin/python -m playwright install chromium  # one-time, 91MB
.venv/bin/uvicorn api:app --port 8000       # then open http://localhost:8000
```

---

## What's in the report

A buyer of paid market research expects to see:

| Section | What it answers | How we ground it |
|---|---|---|
| **Viability score 1-100** | Is this venture worth pursuing? | Anchored to data quality, competitor density, audience match — not LLM vibes |
| **Executive summary** | What's the 60-second verdict? | 3-5 bullet "so-what" |
| **Strengths / Risks** | What works and what could kill this? | Each cited to underlying signal |
| **Target Personas (1-3)** | Who do I sell to first? | Synthesized from real customer voice across top-3 competitors. One persona marked as recommended **wedge** |
| **TAM / SAM / SOM** | How big is the prize? | Arithmetic shown explicitly + range (low/mid/high) + sources to validate primary |
| **3-Year Revenue Scenarios** | What could revenue look like? | Conservative / base / aggressive with break-even year per scenario |
| **Competitive landscape** | Who am I up against? | Real DTC challenger brands (megabrand filter rejects Wrigley/Hershey/Apple-tier giants), each tagged direct/adjacent/reference, with inline 2D map showing whitespace |
| **Pricing tiers (Van Westendorp)** | What should I charge? | Simulated PSM, anchored to **scraped competitor prices** (not LLM guesses) |
| **4Ps marketing plan** | How do I go to market? | Each section cites specific signals + key takeaways |
| **Recommended next steps** | What do I do tomorrow? | Verb-first action list, prioritized |
| **Critical assumptions** | What could invalidate this? | If wrong, the score doesn't hold |
| **Numbered citations** | Where did each claim come from? | Every claim ties to a source |

Buyer can also:
- View as **full HTML report** (~12 pages)
- View as **investor one-pager** (single sheet, KPI strip + wedge persona + scenarios)
- **Compare two plans** side-by-side via `/compare?left=X&right=Y`
- **Submit thumbs-up/down + comment** feedback per report (stored locally)

---

## Architecture

13 modules · 3 LLM backends · 11 free data sources · 106 offline tests

**Pipeline (per `/plan` request):**
1. Profile extraction (LLM)
2. Discover competitors (Google Trends + LLM brand generation, with megabrand filter)
3. Cluster competitors (K-Means + PCA via scikit-learn)
4. Decode audience taste for **top-3 brands in parallel** (Trustpilot via playwright-stealth, Reddit, DDG review articles, brand homepage testimonials)
5. Synthesize 1-3 buyer personas with wedge recommendation
6. Max-Diff feature ranking (LLM-simulated panel)
7. Van Westendorp PSM, anchored to **scraped competitor prices**
8. Place analysis (channel detection from competitor homepages)
9. Validation gate (data-quality flags + confidence)
10. TAM/SAM/SOM with arithmetic
11. 4Ps plan + viability score (parallel)
12. 3-year financial projections (deterministic, no LLM)

**Data sources (10 free + 1 paid-tier optional):**
| Source | Used for |
|---|---|
| Google Trends (pytrends) | category & brand momentum |
| DuckDuckGo Python API (ddgs) | review article discovery |
| trafilatura | clean article extraction |
| Trustpilot (with playwright-stealth fallback for AWS WAF) | customer voice |
| Reddit search (when not 403) | community voice |
| Wayback Machine CDX | site activity proxy |
| Instagram public profile scrape | social scale |
| rdap.org | domain age |
| Competitor homepage scrape | channel signals + meta descriptions + pricing |
| Pattern-probe + DDG fallback | brand → domain resolution |
| Meta Ad Library | (optional, free FB token) ad copy + advertiser scale |

**LLM backends (auto-detect):**
- `GROQ_API_KEY` → Llama 3.3 70B (free tier, fastest)
- `GEMINI_API_KEY` → Gemini 2.5/2.0 Flash (free tier)
- `ANTHROPIC_API_KEY` → Claude (paid, highest quality)

---

## Endpoints

```
POST /plan {description, geo?, max_candidates?}    → start full pipeline
GET  /jobs/{id}                                    → poll status (with _steps_completed live progress)
GET  /jobs/{id}/report.html                        → polished HTML report
GET  /jobs/{id}/onepager.html                      → investor one-pager
GET  /compare?left=ID&right=ID                     → side-by-side comparison
POST /jobs/{id}/feedback {rating, comment}         → operator thumbs-up/down
GET  /feedback/stats                               → aggregate quality stats
GET  /usage                                        → LLM token + USD tracking
GET  /healthz                                      → liveness
```

Plus modular endpoints for individual steps: `POST /discover`, `POST /taste`, `POST /match`, `POST /full`.

---

## Engineering principles

These are codified in [CONTRIBUTING.md](CONTRIBUTING.md):

1. **Verified open-source first.** Use libraries with 500+ stars, active commits, tests. DIY only when no library fits.
2. **Honest output.** No hallucinated brands, no made-up customer quotes. If we can't get real data, we say so.
3. **Cited claims.** Every section of the report ties to a source (Trustpilot review, Wayback snapshot count, Van Westendorp simulation).
4. **Confidence bounded.** Validation gate flags low-data runs. Viability scoring includes `confidence_in_score` field. Market sizing includes `data_quality` (low/medium/high) + sources to validate primary.
5. **Graceful degradation.** When pytrends rate-limits → LLM brand generation. When Trustpilot 403s → playwright-stealth. When LLM JSON truncates → json-repair salvage. Never silently invent.
6. **Live progress.** UI sees green checkmarks per step, not a 5-minute spinner.
7. **Per-step timeouts.** No step stalls the pipeline forever.

---

## Tests

```bash
./test_all.sh           # all 106 tests, offline, <1s
```

3 test suites:
- `test_infra.py` — 80 tests covering retry/cache/cost-tracker/scoring/parsers/parking-domain detection/megabrand filter/persona synth/financial projections/market sizing
- `test_integration.py` — 9 tests covering discover/taste/match/plan pipelines with mocked LLM
- `test_api.py` — 17 tests covering API routes + jobs queue + report rendering

No live external calls in any test.

---

## File layout

```
market-research-prototype/
├── README.md             # you are here
├── SHIP.md               # ship checklist + status (READY)
├── CONTRIBUTING.md       # engineering policy + iteration changelog
├── docs/                 # canonical plan (CC_HARNESS_PLAN.md) + archive/ (historical)
├── install.sh, test_all.sh
├── requirements.txt
│
├── api.py                # FastAPI HTTP server
├── plan.py               # Full 14-step pipeline orchestrator
├── jobs.py               # SQLite job queue (with progress checkpoints)
│
├── discover.py           # Step 3: competitor discovery + scoring
├── taste.py              # Step 5/6: audience taste decoding (multi-source)
├── personas.py           # Step 6b: synthesize 1-3 buyer personas with wedge
├── pricing.py            # Step 9a/9b/10: Max-Diff + Van Westendorp PSM + break-even
├── place.py              # Step 11: channel detection + GTM recommendation
├── clustering.py         # Step 3c: K-Means + PCA + whitespace
├── competitor_pricing.py # Step 10b: scrape real competitor prices for PSM anchor
├── market_sizing.py      # Step 7: TAM/SAM/SOM with arithmetic
├── financials.py         # Step 10c: 3-year revenue scenarios (deterministic)
├── four_ps.py            # Step 13/14: 4Ps plan + viability score
├── match.py              # Score a product idea against a taste profile
├── profile.py            # Step 2: extract structured company profile
├── feedback.py           # Operator thumbs-up/down + stats
│
├── sources.py            # 10 free data sources (cached, retried)
├── charts.py             # Inline SVG competitor map
├── report.py             # Markdown report renderer (CLI artifact)
├── llm.py                # Multi-backend LLM (Groq/Gemini/Anthropic) + usage tracking
├── net.py                # tenacity-wrapped HTTP
├── cache.py              # SQLite 7-day cache
├── errors.py             # Error taxonomy
├── logger.py             # Stdlib logging config
│
├── templates/
│   ├── report.html       # Polished McKinsey-style HTML report
│   ├── onepager.html     # Investor one-pager
│   └── compare.html      # Side-by-side comparison
│
├── web/                  # Vanilla JS + Tailwind CDN frontend
│   ├── index.html
│   ├── app.js
│   └── style.css
│
└── test_*.py             # 106 offline tests
```

---

## What's missing (honest list)

These are real gaps for a v2:
- **B2B mode** — pipeline assumes DTC-style audience scraping. For B2B SaaS we'd want firmographic + buying-committee analysis instead.
- **Historical tracking** — running the same plan again should show deltas (viability up/down, new competitors, etc.). Currently each run is independent.
- **Operator edit/regenerate** — can't tweak a 4P section and ask LLM to regenerate just that part.
- **PDF native generation** — currently relies on browser Cmd+P. WeasyPrint needs system Pango/Cairo.
- **Real-time progress** — UI polls every 1.2s. WebSocket would feel snappier.
- **Reddit signal** — currently 403'd by anti-scraping. Needs operator's Reddit OAuth credentials via praw.
- **Multi-LLM ensemble** — running synthesis through Gemini AND Claude and surfacing disagreements would meaningfully raise rigor.

See SHIP.md and CONTRIBUTING.md changelog for the full evolution.
