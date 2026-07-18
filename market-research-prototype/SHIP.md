# Ship Checklist — What's done, what's broken, what to do next

Last updated: 2026-04-21 (after iterations 1-11).

## Status: ✅ READY TO SHIP

Verified end-to-end with MintBox v5 (subscription mint candy box) — 2026-04-21 21:35:
- **10/10 spec steps + competitor_pricing** completed in 442s (cold) / ~35s (warm cache)
- **Viability: 45/100, tier "moderate", confidence "medium"** — all 9 fields populated:
  - headline: "Niche market and strong competition present significant adoption hurdles"
  - 3 strengths, 3 risks, 5 next steps, critical assumptions
- **Executive summary + citations + key_takeaways** populated per 4Ps section
- **Competitors tagged with relevance labels**: Candy Club (direct), Mouth/Universal Yums/TokyoTreat/Bokksu (adjacent)
  - **Zero megabrands** — filter working; the Mentos/Wrigley/Altoids problem is fixed
- **Real competitor pricing scraped**: 2 brands with prices, $9.60 median → PSM anchored in real market data
- Print-ready HTML report with: cover page, viability hero, validation flags, methodology box, transparent data sources used (checkmarks show what fired), executive summary, strengths/risks split, inline SVG competitor map with whitespace overlay, color-coded 4Ps cards with key takeaways + relevance labels on competitors + thesis per competitor, pricing tier cards, recommended next steps, critical assumptions, numbered citations, feedback widget
- Side-by-side `/compare?left=&right=` endpoint for head-to-head plan comparison
- **TAM/SAM/SOM market sizing** with explicit arithmetic + assumptions + data quality + recommended primary sources to validate
- **3-year revenue projections** in 3 scenarios (conservative/base/aggressive) with break-even year per scenario
- **Investor One-Pager view** (`/jobs/{id}/onepager.html`) — McKinsey-style single-page summary with KPI strip + strengths/risks + top 5 competitors + revenue scenarios. Buyer can print and hand to board.
- **5 example seed startups** in the UI for instant demo (Protein bar / Skincare / Cold brew / Pet sub box / B2B SaaS)
- **Geography fallback** to request param when LLM extracts "unknown"
- **103 unit + integration + API tests passing** offline in <1s

## TL;DR

**The product:** Paste a startup description → get a McKinsey-style 4Ps marketing plan + viability score 1–100 + downloadable HTML report (Cmd+P → PDF).

**Pipeline:** 14 spec steps automated. Profile extraction → competitor discovery → audience taste decoding → Max-Diff feature ranking → Van Westendorp PSM (anchored to real competitor prices) → channel/place analysis → 4Ps synthesis → viability scoring.

**Cost per report:** ~$0.10–0.30 in LLM calls (Gemini free tier sufficient for low volume; Anthropic Claude available as drop-in for higher quality).

**Tests:** 82/82 passing offline. Live tested against 5+ real categories.

---

## ✅ What's done (paid-grade quality)

### Pipeline
- [x] Step 2 — LLM-based company profile extraction (`profile.py`)
- [x] Step 3 — Competitor discovery via Google Trends + LLM brand generation fallback (`discover.py`)
- [x] Step 3c — K-Means + PCA competitor clustering with whitespace detection (`clustering.py`)
- [x] Step 5/6 — Audience taste decoding from real customer voice (`taste.py`)
- [x] Step 9a — Max-Diff feature ranking via LLM-simulated panel (`pricing.py`)
- [x] Step 9b — Van Westendorp PSM, anchored to real competitor prices (`pricing.py` + `competitor_pricing.py`)
- [x] Step 10 — Break-even math (`pricing.py`)
- [x] Step 11 — Channel detection from competitor homepages + LLM recommendation (`place.py`)
- [x] Step 12 — Validation gate with confidence scoring (`plan.py`)
- [x] Step 13 — 4Ps plan with executive summary, key takeaways per section, numbered citations (`four_ps.py`)
- [x] Step 14 — Viability score 1–100 with tier, headline, strengths, risks, critical assumptions, next steps (`four_ps.py`)
- [x] Live progress checkpointing — UI sees green checkmarks land in real-time, not just at completion

### Data sources (10 free + 1 optional)
- [x] Google Trends (pytrends) — category & brand momentum
- [x] DuckDuckGo Python API (ddgs) — review article discovery
- [x] Trafilatura — clean article extraction
- [x] Trustpilot (with playwright-stealth fallback for AWS WAF) — customer voice
- [x] Reddit search (when not 403) — community voice
- [x] Wayback Machine CDX API — site activity proxy
- [x] Instagram public profile scraping — social scale
- [x] rdap.org — domain age (opportunity freshness)
- [x] Competitor homepage scraping — channel signals + meta descriptions
- [x] Competitor pricing scrape (Schema.org → og:price → regex)
- [ ] (Optional) Meta Ad Library — needs free FB token from operator

### Engineering
- [x] FastAPI server with `/plan`, `/discover`, `/taste`, `/match`, `/full` endpoints
- [x] SQLite job queue with threaded async runner
- [x] HTTP retry/backoff via tenacity
- [x] LLM JSON repair via json-repair (handles truncation)
- [x] 7-day SQLite cache for all external calls
- [x] Multi-backend LLM (Groq → Gemini → Anthropic, auto-detect from env)
- [x] Per-step timeout wrapper so no step stalls pipeline forever
- [x] Parallel I/O for taste + place + pricing scrapes
- [x] Parked domain detection (35+ marketplace hosts + content patterns)
- [x] Honest-error policy: never invents data, returns "no data found" rather than hallucinate

### Report
- [x] Polished HTML report via Jinja2 (`/jobs/{id}/report.html`)
- [x] Cover page with brand name, summary, metadata
- [x] Viability hero with big score + tier + headline
- [x] Executive summary (5-bullet "so what")
- [x] Strengths/Risks split panel
- [x] Inline SVG competitor map (PCA scatter + whitespace overlay)
- [x] Color-coded 4Ps cards (Product/Price/Place/Promotion) with key takeaways
- [x] Pricing tier cards
- [x] Recommended next steps numbered list
- [x] Critical assumptions disclosure
- [x] Numbered source citations
- [x] Print-CSS for Cmd+P → Save as PDF
- [x] "View Report →" button in main UI

### Testing
- [x] 82 offline tests in 3 suites: infra (56), integration (9), API (17)
- [x] Run with `./test_all.sh`, all under 1 second
- [x] Live tested on 5+ categories: protein bars, skincare, wireless earbuds, sleep supplements, cold brew, electric toothbrush, hydration drinks

### Docs
- [x] README.md — setup + usage
- [x] docs/archive/PLAN.md — original architecture roadmap (archived)
- [x] docs/archive/METHOD.md — scientific synthesis (archived)
- [x] CONTRIBUTING.md — engineering policy + iteration changelog
- [x] SHIP.md — this file
- [x] CLAUDE.md (implicit via CONTRIBUTING) — verified-OSS-first policy

---

## ⚠️ Known issues / accepted tradeoffs

| Issue | Severity | Workaround |
|---|---|---|
| Pipeline takes 3-10 min depending on Gemini quota | medium | Live progress checkpoints make wait visible; UI shows step-by-step status |
| Gemini 429s common on free tier | medium | Auto-fallback through gemini-2.0-flash → gemini-2.5-flash → gemini-2.5-flash-lite. Or use Groq/Anthropic key. |
| Reddit search.json blocked (403) | low | Already documented; DDG review articles + Trustpilot now do most customer-voice work |
| Google Trends rate-limited | low | LLM brand generation fallback covers this |
| Trustpilot playwright fallback adds ~7s per fetch | low | Cached for 7 days; only first fetch hits playwright |
| Match between LLM-generated brand and real domain has ~70% accuracy | medium | Validation step rejects parked + pattern-probes alternatives |
| Bigger competitor count (>10) requires longer LLM call (more risk of truncation) | low | json-repair salvages truncated output; max set to 12 in prompt |

---

## 🔧 To run

```bash
# 1. Install
cd market-research-prototype
./install.sh

# 2. Set ONE LLM API key in .env:
#    GROQ_API_KEY=...      (free, fastest)
#    GEMINI_API_KEY=...    (free, what we tested with)
#    ANTHROPIC_API_KEY=... (paid, best quality)

# 3. Install playwright browser (one-time, ~91MB)
.venv/bin/python -m playwright install chromium

# 4. Run
.venv/bin/uvicorn api:app --host 0.0.0.0 --port 8765
# open http://localhost:8765
```

## 🔗 Endpoints

```
POST /plan {description, geo?}         → start full pipeline
GET  /jobs/{id}                        → poll status
GET  /jobs/{id}/report.html            → polished HTML report
GET  /jobs                             → list recent
GET  /usage                            → LLM cost tracking
GET  /healthz                          → liveness
```

Plus the original modular endpoints: `/discover`, `/taste`, `/match`, `/full`.

---

## 🚀 Future iterations (not blocking ship)

1. **Real-time progress UI** — currently UI polls every 2s; could be WebSocket
2. **Multi-LLM ensemble** — run synthesis with both Gemini + Claude, surface disagreements
3. **Comparable/historical reports** — run plan on same company monthly, track viability over time
4. **Reddit via praw** — register free app, restore Reddit signal
5. **TikTok signal** — fragile but valuable for DTC; need official API or accepting scrape risk
6. **Embedding-space match v2** — sentence-transformers for deterministic match scoring
7. **B2B mode toggle** — pivot Step 5/6 from psychographic to firmographic for B2B SaaS
8. **PDF generation** — WeasyPrint when system Pango/Cairo available, else HTML print-friendly (current)
9. **Operator feedback loop** — thumbs-up/down on plans, retrain scoring weights
10. **Pricing page deep scrape** — currently we get medians; could parse tier names + features

---

## 📊 What "paid-grade" means in our output

A buyer should be able to:
- ✅ Read the executive summary in 60 seconds and know the verdict
- ✅ Trace every claim back to its source via numbered citations
- ✅ See the competitive landscape visually (clusters + whitespace)
- ✅ See real prices from real competitor pages, not LLM guesses
- ✅ Understand the confidence level explicitly (validation flags + score confidence)
- ✅ Get specific next steps (not generic "do more research")
- ✅ Print or share as PDF without any reformatting
- ✅ Know what assumptions would invalidate the score

A buyer should NOT find:
- ❌ Hallucinated brand names (we use real scraping + validation)
- ❌ Made-up customer quotes (we cite the actual scraped review)
- ❌ Fake confidence numbers (validation gate flags low-data runs)
- ❌ Generic marketing fluff ("leverage synergies") — prompts explicitly forbid
- ❌ Domain marketplace links posing as competitors (parked-domain blocklist)
