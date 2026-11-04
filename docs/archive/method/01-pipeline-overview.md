# 01 — Pipeline Overview

The pipeline takes a free-text venture description (≥30 chars) and produces a 22-step structured market-research artifact, persisted as JSON and renderable as HTML/PDF.

## Entry points

- **`POST /plan`** — accepts `{description, geo, max_candidates, operator_weights}`, returns `{job_id}`. Worker runs asynchronously.
- **`GET /jobs/<id>`** — returns full job state (`{state, result, error, created_at, updated_at}`).
- **`GET /jobs/<id>/report.html`** — Jinja2-rendered report (renders on every request from current job data).
- **`GET /jobs/<id>/report.pdf`** — Playwright print-to-PDF of the same.

## Step-by-step

The orchestrator (`plan.run_plan`) executes these steps. Steps marked `parallel` run inside a `ThreadPoolExecutor`; the rest are sequential.

| # | Step | Module | Notes |
|---|---|---|---|
| 1 | profile extraction | `company_profile.py` | LLM extracts category, business model, pricing, competitors, target |
| 2 | competitor discovery | `discover.py` | LLM-generated DDG/Brave queries → candidate brands → scoring |
| 3 | firmographics | `firmographics.py` | Wikidata SPARQL + GitHub Orgs + DDG fallback per top brand (parallel) |
| 4 | clustering | `clustering.py` | fastembed-bge-small + HDBSCAN + UMAP (with K-Means + PCA fallbacks) |
| 5 | differentiators | `differentiators.py` | 5 parallel LLM calls (one per dimension: feature/pricing/channel/delivery/IP) |
| 6 | customer_universe | `customer_universe.py` | 5 methods merged: competitor /customers scrape, ICP+DDG, vertical seeds, Crunchbase Wayback, G2 reviewers |
| 6a | taste decoder × top-3 | `taste.py` | parallel; pulls Trustpilot + Reddit + DDG articles + HackerNews per top competitor |
| 6b | competitor pricing | `competitor_pricing.py` | scrapes /pricing pages |
| 6c | reddit signal | `reddit_signal.py` | pullpush.io + DDG fallback; sentiment + theme extraction |
| 6d | hn signal | `sources.hackernews_mentions` | Algolia public API |
| 6e | multi-source signal | `sources.{stackexchange,devto,lobsters}_mentions` | all parallel |
| 7 | audience synthesis | merged from taste decoder | top audience picked by confidence |
| 8 | personas | `personas.py` | LLM clusters audiences into 1-2 buyer personas |
| 9 | max-diff | `pricing.simulate_max_diff` | LLM panel of 30 simulated buyers |
| 9b | van-westendorp PSM | `pricing.simulate_van_westendorp` | LLM panel of 40 simulated buyers, returns optimal price + acceptable range |
| 10a | competitor pricing benchmark | derived from 6b | category median, our-price vs median |
| 10b | unit economics | `economics.py` | CLV, max-sustainable-CAC, EVC decomposition, sensitivity table |
| 11 | place / channel | `place.py` | LLM rec grounded in competitor channel signal |
| 12 | validation gate (early) | `plan._validation_gate` | flags raised on missing/thin data; runs early to checkpoint |
| 13 | market sizing | `market_sizing.py` | 6 parallel LLM calls: TAM × 3 single-method calls + SAM/segmentation + SOM + meta. Headline TAM is mean of method values. |
| 13b | macro anchors | `macro_anchors.py` | FRED public API, no key |
| 13c | TAM segmentation post-process | `market_sizing` | recomputes per-segment tam_usd from share_pct × tam_mid (math discipline) |
| 14a | 4Ps split | `four_ps.assemble_4ps_split` | 4 parallel LLM calls (product/price/place/promotion) — each gets only the context it needs |
| 14b | growth scenarios | `financials.py` | deterministic Y1/Y2/Y3 from SOM + PSM optimal price + S-curve adoption |
| 15 | segment ranking | `segment_scoring.py` | LLM-scored on 5 metrics (WTP×size, elasticity, competition, reach, growth), operator-weighted |
| 16 | viability | `four_ps.score_viability` | 5-dim weighted to 1-100, retries once with 180s timeout if first call errors |
| 17 | validation gate (final) | `plan._validation_gate` | second pass after viability; merges with early flags, takes min confidence |

## Parallelism

The orchestrator uses three layers of parallelism:

1. **Inside a step** — e.g. differentiators runs 5 LLM calls in parallel (one per dimension)
2. **Across steps** — e.g. taste decoder × 3 competitors + reddit + HN + multi-source + competitor pricing all submit to a single ThreadPoolExecutor
3. **Inside a sub-call** — market_sizing has 6 parallel LLM calls (3 TAM methods + sam_seg + som + meta)

Worker pool sizes are tuned to avoid LLM rate-limit cascades:
- competitor discovery: `max_workers=4`
- taste decoder + reddit + HN + multi-source: `max_workers=8`
- market_sizing: `max_workers=6`
- differentiators: `max_workers=5`
- multi-source signal (within step): `max_workers=3`

## Retry behavior

The pipeline has a 3-tier retry strategy:

1. **`_run_with_timeout`** — every critical step gets a hard timeout. On timeout, returns `{"error": "timed out after Ns"}` instead of crashing.
2. **Step-level retry** — viability and TAM 3-method retries on per-method failure (cycle 30).
3. **LLM provider chain** — `llm.call_json` cascades Gemini → Groq → Anthropic; on parse error, json_repair salvages malformed JSON.

## Validation gate (the honesty layer)

`plan._validation_gate` runs twice:
- **Early** (after step 12) to checkpoint validation flags partially
- **Final** (after step 17) to catch viability + segment + voice-source flags

It tracks 9 flag categories:
1. <3 competitors found
2. Audience confidence missing or below thresholds (0.3 / 0.5 / 0.7)
3. Pricing/PSM failed
4. Place analysis incomplete
5. <3 customer-voice sources returned data
6. <3 TAM methods filled
7. Segment scores defaulted to 0.5
8. Viability step skipped or errored
9. 0 differentiators found (commodity copycat warning)

The merged validation block is `{flags: [...], confidence_score: 0.0-1.0}`. The benchmark dimension `validation_honesty` rewards pipelines that **honestly flag thin data** — a pipeline returning "0 flags + 100% confidence" is treated as over-reporting and scored 20/100.

## Output schema (top-level keys)

```jsonc
{
  "_steps_completed": ["profile", "discover", ...],   // accountability
  "_elapsed_seconds": 286.5,
  "_duration_seconds": 286.5,
  "profile": {...},
  "discover": {...},
  "differentiators": {differentiators: [...], gaps: [...], strength: "..."},
  "customer_universe": {count, icp_summary, icp_details, companies, segments},
  "audience": {...}, "audiences": [...],
  "personas": {personas: [...], wedge_persona, wedge_reasoning},
  "max_diff": {ranked_features, must_haves, deprioritize},
  "competitor_pricing": {...},
  "pricing": {psm, break_even, benchmark},
  "economics": {clv, cac_target, evc, sensitivity},
  "place": {primary_channel, secondary_channels, gtm_motion, ...},
  "validation": {flags, confidence_score},
  "market_sizing": {tam, sam, som, segmentation, growth_cagr_pct, weakest_assumptions, sources_to_validate, macro_anchors},
  "four_ps": {product, price, place, promotion, citations},
  "financials": {scenarios: {conservative, base, aggressive}, assumptions},
  "segment_ranking": {ranked, top_5, top_pick, weights_applied},
  "reddit_signal": {threads, top_subreddits, sentiment, themes},
  "hn_signal": {hits, hits_found, query},
  "multi_source_signal": {stackoverflow, devto, lobsters, counts},
  "viability": {viability_score, breakdown, narrative, risks, kill_criteria, ...}
}
```
