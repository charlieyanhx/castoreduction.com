# Castor Advisories — Report Methodology & Software Architecture

How every section of a report is produced, what is **sourced** vs **estimated**, and how the
system is put together. Written for a manual review. Reflects the codebase as of cycle38
(post M1–M4 + the sourcing/sizing hardening).

The guiding principle: **every published number is either fetched from an authoritative source
or is a clearly-labeled model estimate — never a silent guess.** The defensibility of the numbers
(reproducible, sourced, validated, triangulated) is the moat; the harness and UI commoditize.

---

## Part 1 — Software Architecture

### 1.1 Shape: a deterministic pipeline + an agent loop
Two architectures coexist:
- **The report pipeline** (`plan.py::run_plan`) — a mostly-deterministic DAG of ~17 steps. This is
  what runs when you generate a report. Each step is a tool/skill call or a *scoped* LLM call.
- **The agent loop** (`harness/agent.py::run_agent`) — a perceive→decide→act→observe loop used by
  the open-ended **agents layer** (`agents/`) for research that can't be hard-coded.

Most of a report is the deterministic pipeline; the agent loop is the escape hatch for fan-out
research. The intelligence lives in the **routers** (scale, business-model) and **gates**
(validate → triangulate → refine → judge → health), not in a free-roaming agent.

### 1.2 The substrate: a 3-tier registry + one Evidence envelope
Every capability is registered by a decorator and returns the **same `Evidence` dataclass**
(`source, category, count, payload, duration_s, skeleton, error`). The uniform envelope lets the
orchestrator treat all three tiers interchangeably and lets the integrity layer reason about
provenance.
- `@tool` (`tools/`) — raw capability with I/O. `geo.py` (Census/Nominatim/FCC, ACS, OSM Overpass),
  `econ.py` (BLS CEX), `trend.py`, `social.py`, `domain.py`, `scrape.py`, `customer_voice.py`,
  `firmographic.py`, `ads.py`. The `@tool` wrapper catches exceptions and returns *error Evidence*
  — a failed tool degrades, never crashes the run.
- `@skill` (`skills/`) — pure-ish composition over tools with a `produces`/`consumes` contract:
  `sizing/*` (classify, hyperlocal, regional, national_digital, bottom_up, validate, dispatch),
  `triangulate`, `perspective` (consumer research), `price_intel`, `discovery`, `refine_report`.
- `@agent` (`agents/`) — a role + goal that drives the harness loop: market_scan, demand_signal,
  pricing_intel, local_market, plan_research, synthesis, run_research_crew.

### 1.3 The serving layer
- **`api.py`** — FastAPI. `POST /plan` enqueues a job; `GET /jobs/{id}` returns state+result;
  `GET /jobs/{id}/report.html` renders the report (Jinja). Three Jinja `Environment`s use a custom
  **`SafeUndefined`** (nullish on format/compare/arithmetic) so a single missing field degrades to
  a blank cell, never a 500/blank page.
- **`jobs.py`** — SQLite job store; `run_async` runs the pipeline in an in-process worker. Orphaned
  `running` jobs from a crash are swept to `error` on startup.
- **`cache.py`** — content-addressed cache (sha256 of prompt) with 7-day TTL. `LLM_CACHE_BYPASS=1`
  disables it for variance testing.

### 1.4 The LLM layer (`llm.py`) — hardened this cycle
- Provider chain: **Gemini** (configured) → Groq → Anthropic (the latter two if keys present).
- **`thinking_budget=0`** — newer Gemini models otherwise spend the entire output budget on hidden
  "thinking", truncating the JSON. Disabling it was the root fix for intermittent `parse_error`s.
- **Model IDs** are the live, verified aliases (`gemini-flash-latest`, …); the old `gemini-2.0-flash`
  404s for this key tier.
- **Whole-chain retry** with backoff (0/3/8/15s); SSL `UNEXPECTED_EOF` / "server disconnected" are
  classified transient (a single blip used to return `parse_error`).
- **`temperature=0` + `seed=42`** → "same input, same number." This + the cache is what lets the
  report claim *deterministic / reproducible*.

---

## Part 2 — The pipeline, step by step

Order (from `_steps_completed`): `profile → discover → market_scale → clustering → differentiators
→ hn_signal → multi_source_signal → consumer_research → max_diff → pricing → economics → place →
validation → market_sizing → four_ps → financials → viability`. Independent steps run in parallel
(sizing ∥ 4Ps; max_diff/PSM/place in a thread pool).

| # | Step | File(s) | Method | Sourced or Estimated |
|---|---|---|---|---|
| 1 | **Profile** | `company_profile.py` | LLM extracts name/category/geography/business_model/summary/features from the description | LLM extraction of the user's own text |
| 2 | **Discover** (competitors) | `discover.py`, `skills/discovery*.py`, `tools/geo.py` | For digital ventures: LLM-seeded web search → candidate brands → scored on traffic/Trustpilot/IG/Wayback. **For physical-local ventures (M1): the canonical competitor set is the real nearby venues from OpenStreetMap** (`geo_competitor_opps`), not LLM-guessed national brands | **Sourced** (OSM for local; web signals for digital) |
| 3 | **Market scale** | `skills/sizing/classify.py` | LLM classifies hyperlocal / regional / national_digital / global; sets `is_physical` | LLM classification (temp 0) |
| 4 | **Clustering + whitespace** | `clustering.py`, `charts.py` | TF-IDF/embeddings → KMeans/HDBSCAN → UMAP/PCA 2-D map; whitespace = largest empty grid cell. For geo-sourced ventures, clusters the real local set | Deterministic over the competitor set |
| 5 | **Differentiators** | `differentiators.py` | One LLM call cross-references our features vs each cluster to find gaps no cluster covers | LLM over real cluster data |
| 6–7 | **Customer voice** | `reddit_signal.py`, `sources.py`, `tools/customer_voice.py` | Reddit (pullpush+DDG) + **industry-gated** dev forums (HN/StackExchange/DEV.to/Lobsters **only for tech ventures**) + vertical trade pubs for all | **Sourced** organic mentions; thin for niche/local |
| 8 | **Consumer research** (personas + WTP) | `skills/perspective.py` | STORM-style: generate N distinct perspectives → simulate grounded interviews → deterministic aggregate (ranked needs/objections + WTP band). WTP unit is per-venture (`/drink`, not `/mo`) | **Estimated** (synthetic interviews) — labeled |
| 9 | **Max-Diff** | `pricing.py` | LLM simulates Best-Worst scaling over features → importance ranking (sums to 100) | **Estimated** (simulation) — "directional" |
| 10 | **Pricing (Van Westendorp PSM)** | `pricing.py` | Model-aware: per-unit price points for transactional/ecommerce/services/hybrid; monthly tiers only for subscription. Tier sanity-check vs optimal | **Estimated** (simulation), anchored to scraped competitor prices |
| 11 | **Economics** | `business_model.py`, `economics.py` | **Routed by business model:** per-unit kinds → `retail_unit_economics` (contribution margin, break-even units/day); subscription → CLV/CAC/EVC; marketplace → take-rate basis; ad-supported → ad-revenue basis | Mixed: cost structure **estimated** (or real BLS-adjacent); math deterministic |
| 12 | **Place** (channels) | `place.py` | Extract competitor channel signals (sales-led/PLG/community/partner); LLM recommendation grounded in the distribution | LLM over scraped channel data |
| 13 | **Validation gate** | `skills/sizing/validate.py` | Formula reconciliation + funnel ordering (SOM≤SAM≤TAM) + segmentation-sum checks; hard block sets `publishable=false` → report withholds the numbers | Deterministic checks |
| 14 | **Market sizing** | `skills/sizing/*`, `market_sizing.py` | Routed by scale (see Part 3). Hyperlocal = trade-area catchment (households × spend, OSM competitor density) | Mixed — see Part 4 (sourced vs estimated) |
| 15 | **4Ps synthesis** | `four_ps.py` | Four parallel focused LLM calls (Product/Price/Place/Promotion), each with a **model directive** forbidding cross-model bleed (no MRR/subscriber framing on a transactional venture) | LLM over the assembled evidence |
| 16 | **Financials** | `financials.py` | 3-year scenarios from the single canonical SOM (M3); model-aware (covers/units for transactional, customers for subscription) | Deterministic projection |
| 17 | **Viability** | `four_ps.py::score_viability` | LLM scores 5 weighted dimensions (1-100 each) against calibrated anchors; final = deterministic weighted sum; anchored to the real TAM/scale + model directive | LLM-graded, deterministically composed |

Wrapped around all of it: **triangulation** (`skills/triangulate.py`), the **integrity panel**
(`plan.py::build_integrity_summary`), the **generator-evaluator-refine** loop
(`harness/refine.py`, opt-in), an **independent LLM judge**, and the **honest-degradation banner**
(`plan.py::assess_run_health` — names what failed instead of presenting $0/blank as a finding).

---

## Part 3 — The numbers-right engine (market sizing)

The method generic TAM tools get wrong is a single physical premise. The scale router prevents it:

`classify_market_scale` → dispatch:
- **hyperlocal / regional** → `size_hyperlocal` (trade-area catchment). A physical venture is
  *deterministically forced* to trade-area sizing — it can never collapse to "national market ÷
  players."
- **national_digital / global** → `estimate_market_size` (3 methods: top-down, bottom-up, analog).

**Hyperlocal trade-area model** (`skills/sizing/hyperlocal.py`):
```
catchment radius  = category-aware (walk-in cafe ~1.5km, restaurant ~3km, gym ~5km)   [plan._radius_for_osm_value]
households         = π·r²·density            (density = real ACS w/ key, else labeled estimate)
spend/household/yr = BLS CEX (real)          (mappable categories) else labeled estimate
TAM_local = households × spend/hh/yr
SAM_local = TAM × serviceable_fraction (35%)
SOM       = min(single-unit capacity × ramp, SAM)     ← capacity-anchored, NOT fair-share ÷ N
            (the "equal-split fair share" appears only as a saturation footnote)
```

**Triangulation** (`skills/triangulate.py`): the 3 national TAM methods are tagged by *data origin*
(census/bls = independent; correlated LLM draws collapse to one origin). Headline = **median across
independent origins**, with convergence shown honestly.

---

## Part 4 — Data sources: what's REAL vs ESTIMATED (the honesty ledger)

| Quantity | Source | Status from this environment |
|---|---|---|
| Local competitors | OpenStreetMap Overpass | ✅ **real** (correct OSM key per category) |
| Geocode (lat/lng) | US Census Geocoder → **Nominatim fallback** | ✅ real (geocoder WAF-blocked here; Nominatim used) |
| FIPS (state/county/tract) | Census Geocoder → **FCC area API bypass** | ✅ real (FCC host not blocked) |
| Households / income | US Census ACS 5-yr | ✅ real **with a free `CENSUS_API_KEY`**; else a reproducible **density estimate** (labeled) |
| Spend / household / yr | BLS CEX (curated verified series) | ✅ **real** for mappable categories (restaurant/bar/salon/grocery/apparel/health/pet/entertainment); **estimated** where CEX has no clean line (e.g. coffee-at-cafes) |
| Macro anchors (GDP, e-comm) | FRED public API | ✅ real (when reachable) |
| Competitor pricing | homepage scrape / competitor_pricing | ✅ real where scrapeable; unit-matched |
| Personas, WTP | synthetic interviews (`perspective.py`) | ⚠️ **estimated** — labeled |
| Max-Diff, PSM | LLM simulation (`pricing.py`) | ⚠️ **estimated** — "directional, not a real survey" |
| Cost structure (break-even) | `estimate_cost_structure` (LLM, category-aware) | ⚠️ **estimated** — disclosed in the Assumptions box |
| Viability score | LLM-graded, deterministically composed | ⚠️ judgment, anchored to real data-quality signals |

**To make households + spend fully real:** drop a free `CENSUS_API_KEY` and `BLS_API_KEY` in `.env`.
The fetch chain (Nominatim → FCC FIPS → ACS) is already wired and bypasses the geocoder firewall.

---

## Part 5 — Business-model router (`business_model.py`)

`classify_business_model` (deterministic keywords, 7 kinds) routes the entire pricing → economics →
financials → narrative spine:

| Kind | Detection | Economics |
|---|---|---|
| transactional | physical premise (cafe/restaurant/salon/food truck) | per-unit: margin, break-even units/day |
| hybrid | drop-in + membership, or device + subscription | per-unit (one-time leg primary) |
| ecommerce | one-time DTC product | per-unit (per order/item) |
| services | agency/consultancy/project/retainer | per-unit (per project) |
| subscription | SaaS / membership-first / recurring | CLV / CAC / EVC |
| marketplace | take-rate / two-sided / commission | take-rate on GMV (not GMV-as-revenue) |
| ad_supported | free, ad-monetized | ad-revenue-per-user (no subscriber CLV:CAC) |

`is_per_unit()` groups the four per-unit kinds onto the retail economics path; `unit_for_model()`
derives the unit noun and **never returns `/mo` for a per-unit venture**. A **model directive** is
injected into the 4Ps + viability prompts so the narrative can't invent a different monetization
model (no "$12K MRR" on a cafe).

---

## Part 6 — Determinism, reproducibility, and failure handling

- **Reproducible:** temp=0 + seed=42 + content cache → same input → same number. Density-based
  households (vs an unanchored LLM total) removed the largest source of run-to-run TAM swing.
- **Graceful degradation:** every tool returns error-Evidence not an exception; the LLM layer
  retries; `SafeUndefined` keeps render alive; `assess_run_health` shows an **"Incomplete —
  regenerate"** banner naming what failed instead of presenting $0/blank as a real finding.
- **Validation gate** withholds numbers that fail formula/funnel/segmentation checks.

---

## Part 7 — Known limitations (honest)

1. **SOM scenario double-discount** (queued fix): the 3-year scenarios take 5/20/60% of an
   already-obtainable SOM, so the base case can read unprofitable while "profitable at SOM."
2. **Households/spend estimated without keys** here (Census/BLS keys not set); CEX has no
   coffee-at-cafes line so a cafe's spend stays an anchored estimate.
3. **Synthetic consumer research** (personas/WTP/Max-Diff/PSM) is LLM-simulated, not real surveys —
   labeled "directional."
4. **Single LLM provider** (Gemini free tier) → ~1-in-10 regens can drop a section under load; the
   banner makes any such run honest, and a second provider key removes it.
5. **Customer voice** is thin for local/niche ventures until Google Places + Yelp review sources
   (key-gated) and per-industry forums are wired (planned).
