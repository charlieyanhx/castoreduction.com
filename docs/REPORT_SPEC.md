# Castor Marketing Report — Content & Functionality Specification

The target definition of what a Castor report *should* contain: every section, the script/tool that
generates it, its data source(s), how it adapts to the business model, the quality bar, and current
status. This is the north-star spec — reconcile the pipeline and template against it.

**Legend:** ✅ at-spec · 🟡 works but has known gaps (see [AUDIT_RESULTS.md](AUDIT_RESULTS.md)) · 🔲 wished, not built.

---

## 0. Report principles (the bar every section is held to)

1. **Defensible & sourced** — every number is either from a real source (labeled with it) or an LLM
   estimate *explicitly labeled UNSOURCED*. No unlabeled guesses. No sentence ships without backing.
2. **Model-aware** — pricing / economics / financials / WTP adapt to the venture's monetization model
   (transactional · subscription · marketplace · ecommerce · services · ad-supported · hybrid). Never
   force a SaaS frame onto a cafe.
3. **Scale-aware** — sizing method matches market scale (hyperlocal trade-area · regional · national
   digital · global). A single cafe is sized by catchment, never national-market-÷-players.
4. **Internally consistent** — one canonical value per quantity (SOM, CAC, CLV, price, competitor
   count) across *every* section. No contradictions.
5. **Reproducible** — temperature 0 + seed + content cache → same input, same numbers.
6. **Honest under failure** — if a step fails, the report says so (degradation banner + integrity
   panel), never presents a $0/blank as a finding.

---

## 1. Cover & scorecard header
- **Contents:** venture title, one-line description, **Viability /100** + confidence, **TAM (mid)**,
  category, geography, **business model**, generated timestamp, pipeline-steps-completed list.
- **Generator:** `plan.py` assembly + `templates/report.html` header; viability from `four_ps.py:score_viability`.
- **Sources:** profile extraction (LLM) + downstream computed values.
- **Status:** ✅

## 2. Viability Score (per-dimension breakdown)
- **Contents:** composite 0-100 from **5 weighted dimensions** — Market Opportunity, Differentiation,
  Unit Economics, GTM Feasibility, Execution/Data Confidence — each with raw score, weight,
  contribution, and one-line reasoning; headline verdict + confidence flag.
- **Generator:** `four_ps.py:score_viability` (LLM judge against a calibrated rubric; deterministic weighted sum).
- **Sources:** the full assembled result (4Ps, sizing, economics, data-quality signals).
- **Model-aware:** Market-Opportunity rubric switches national $-bands ↔ hyperlocal single-unit framing.
- **Quality bar:** reasoning must cite the *right* model's numbers and never contradict the sizing/financials.
- **Status:** 🟡 — can cite a figure the sizing gate marked unpublishable; must gate on `publishable`.

## 3. Report Integrity panel ("how to trust these numbers")
- **Contents:** reproducible ✓, validation gate passed/failed + block/warn counts, sourced N/N headline
  methods, model-estimated data origins (census/bls/llm), grounded flag.
- **Generator:** `plan.py:build_integrity_summary` (pure read over result). **Sources:** provenance of the sizing methods.
- **Status:** ✅ (the "make backend rigor felt" surface).

## 4. Run-health / degradation banner
- **Contents:** when a step failed or was rate-limited, a visible "Incomplete — regenerate" banner naming what failed.
- **Generator:** `plan.py:assess_run_health`. **Status:** ✅

## 5. Executive Summary
- **Contents:** 3-5 bullets (the 60-second "so-what") + a one-liner per P (Product/Price/Place/Promotion).
- **Generator:** `four_ps.py` (LLM, one focused call per P) → `executive_summary`.
- **Quality bar:** must reflect the *model's* pricing (per-drink, not "monthly subscription").
- **Status:** 🟡 — historically leaked subscription framing; now gated by the model_directive.

## 6. Target Personas
- **Contents:** decoded buyer segments; a recommended **wedge** persona (attractiveness score, motivation,
  key pain, message, reach channels, vs-others); why-this-wedge rationale.
- **Generator:** `personas.py` from decoded audiences (`taste.py`).
- **Sources:** competitor Trustpilot reviews + Reddit + homepage scrape (taste decode).
- **Status:** 🟡 — depends on competitor customer-voice; thin when reviews are sparse.

## 7. Consumer Research — Multi-Perspective Simulation
- **Contents:** N synthetic buyer perspectives interviewed independently → ranked **needs**, **objections**,
  cross-segment agreement, a **willingness-to-pay band** (low/median/high or single-point), per-segment voice quotes.
- **Generator:** `skills/perspective.py` (STORM-style) — LLM perspective-gen + interviews, deterministic aggregation.
- **Model-aware:** WTP unit derived from `unit_for_model` (per-drink / per-visit / /mo) — never fake a monthly band for a per-visit venture.
- **Quality bar:** no degenerate band (median==high on one data point); connect WTP to the stated price.
- **Status:** 🟡 — WTP can still collapse to median==high when synthetic values cluster.

## 8. Market Size (TAM / SAM / SOM)
- **Contents:** three figures with **range band**, the **method** (trade-area-catchment / top-down·bottom-up·analog
  triangulated), the **calculation string** per figure, 3 weakest assumptions, data-quality flag,
  recommended sources to validate, funnel-consistency (SOM≤SAM≤TAM).
- **Generator:** scale-router `classify_market_scale` → `size_hyperlocal` (hyperlocal) or `estimate_market_size`
  (digital) → `ground_sizing_bottom_up` → `triangulate_sizing` → `validate_numbers` gate.
- **Sources:** **Census ACS** households, **BLS CEX** spend, **Census CBP** business counts, **OSM** competitor
  density (real when keys/network allow; labeled LLM estimate otherwise). Geocode via **Nominatim + FCC FIPS bypass**.
- **Quality bar:** one canonical SOM flows to economics + financials; band disclosed as a modeling band, not a measured CI.
- **Status:** 🟡 — SOM double-discount vs financials scenarios is the #1 open modeling bug.

## 9. 3-Year Revenue Scenarios (financials)
- **Contents:** conservative/base/aggressive; per-year revenue, volume (in the model's unit), monthly
  operating profit, break-even year; disclosed assumptions (price, margin, fixed cost, ramp).
- **Generator:** `financials.py:project_three_year` (deterministic, no LLM), model-aware (retail covers vs subscription customers).
- **Quality bar:** consumes the *single canonical* SOM; "profitable at SOM" must reconcile with the achievable scenario.
- **Status:** 🟡 — scenarios (% of SOM) can make base perpetually unprofitable while economics says "profitable at SOM".

## 10. Differentiators & Market Gaps
- **Contents:** differentiation strength + reasoning; differentiators across 5 dimensions (Feature, Pricing,
  Channel/GTM, Delivery/Experience, IP/Trust), each grounded against competitor clusters.
- **Generator:** `differentiators.py` (LLM cross-reference of our features vs cluster members).
- **Quality bar:** grounded in the *real* (geo-sourced when local) competitor set, not national brands.
- **Status:** 🟡 — quality tied to competitor-set quality (M1 fixed for local; digital still search-dependent).

## 11. Customer Universe (B2B mode only)
- **Contents:** real named companies matching the ICP, to target as first customers.
- **Generator:** `customer_universe.py` (scrape /customers pages + LLM ICP → search). **Status:** 🟡 (search-dependent).

## 12. Segment Prioritization (5-metric weighted)
- **Contents:** each segment scored on WTP×size, low elasticity, low competition, reach ease, growth;
  operator-weighted mean; top pick + radar chart.
- **Generator:** `segment_scoring.py`. **Status:** ✅ (flags when LLM defaults scores).

## 13. Competitive Landscape
- **Contents:** 2D positioning map (clusters + whitespace region), ranked competitors (relevance/score or
  **"nearby"** for OSM venues), cluster axis interpretations.
- **Generator:** `clustering.py` (embed → HDBSCAN/KMeans → UMAP) + `charts.py:competitor_map_svg`; competitors from
  `discover.py` or **geo_competitor_opps** (OSM) for local ventures.
- **Quality bar:** suppress/hedge the "whitespace" strategic claim when clustering is degenerate (silhouette≤0).
- **Status:** 🟡 — degenerate clustering (0% variance) still rendered as a confident map (M8).

## 14. Customer Voice — Multi-Source
- **Contents:** organic mentions, **industry-selected**: Reddit + trade/industry publications for all; dev
  forums (HN/StackExchange/DEV.to/Lobsters) only for tech ventures; sentiment skew, complaint/praise themes, verbatim quotes.
- **Generator:** `reddit_signal.py`, `sources.py`, gated by `_is_tech_venture`.
- **Status:** 🟡 — thin for local/consumer (Reddit-only); **wished:** Google Maps / Yelp reviews of nearby competitors.

## 15. Feature Importance (Max-Diff)
- **Contents:** features ranked 0-100 by Best-Worst Scaling; must-haves vs deprioritize.
- **Generator:** `pricing.py:simulate_max_diff` (LLM panel). **Status:** ✅

## 16. Decoded Audiences (taste profiles)
- **Contents:** per top-competitor decoded audience (who buys, why, what they praise/complain); undecodable
  brands flagged with the reason.
- **Generator:** `taste.py` from Trustpilot (playwright)/Reddit/homepage. **Status:** 🟡 (source-blocking fragile).

## 17. 4Ps Marketing Plan
- **Contents:** Product / Price / Place / Promotion — 2-3 paragraphs each + key takeaways, cited to evidence.
- **Generator:** `four_ps.py` — one focused LLM call per P, injected with a **model_directive** forbidding wrong-model framing.
- **Quality bar:** Price leads with the primary transaction unit; no subscription/MRR bleed for transactional.
- **Status:** 🟡 — model_directive added; residual bleed possible in narrative.

## 18. Pricing Detail
- **Contents (model-aware):** transactional → menu price + contribution margin + break-even volume/day;
  subscription → tiered per-seat/mo + PSM optimal; marketplace → take-rate; with a competitor benchmark.
- **Generator:** `pricing.py:simulate_van_westendorp` (model/unit-aware) + `build_benchmark_table`; `plan.py` unit routing.
- **Quality bar:** benchmark must compare like-for-like units; never "$X/month per drink"; reject parked/wrong-category price sources.
- **Status:** 🟡 — Pricing Detail fixed; **benchmark table** + price-source validation still weak (parked-domain prices).

## 19. Unit Economics
- **Contents (model-aware):** retail → margin/unit, break-even covers/day, monthly profit at SOM; subscription →
  CLV, CAC target, churn, EVC; marketplace → take-rate economics; hybrid → both legs.
- **Generator:** `business_model.py:retail_unit_economics` / `economics.py:full_economics` / marketplace path.
- **Quality bar:** one churn/CAC/CLV value across sections; hybrid must not drop the one-time (hardware) leg.
- **Status:** 🟡 — hybrid leg-dropping + cross-section CAC/churn inconsistency remain.

## 20. Sensitivity Analysis
- **Contents:** fragility map across churn (½×–3×) and price (±20%) scenarios (subscription).
- **Generator:** `economics.py:sensitivity_analysis`. **Status:** ✅ (subscription); 🔲 transactional analog.

## 21. Recommended Next Steps — 30/60/90
- **Contents:** dated, owner-assigned validation actions (door counts, interviews, price audits).
- **Generator:** `four_ps.py:score_viability` → recommended_next_steps. **Status:** ✅

## 22. Macro & Industry Anchors
- **Contents:** GDP / sector / e-commerce anchors grounding TAM/SAM (FRED etc.).
- **Generator:** `macro_anchors.py` (FRED, 24h cache). **Status:** 🟡 (wrong-vertical anchors sometimes attached).

## 23. Data Provenance (debug) — NEW
- **Contents:** per-tool-call source, live/fallback status, sample; LLM call count (fresh vs cached) + models.
- **Generator:** `provenance.py` (captured at the `@tool` choke point) + panel in template. **Status:** ✅ (just shipped).

## 24. Methodology Appendix + Sources & Citations
- **Contents:** per-step methodology prose; citation list mapping claims → source.
- **Status:** 🟡 — citations are section-level, not **sentence/claim-level** (the big literature gap).

---

## Cross-cutting functionality (not a section — the engine)
| Function | File | Status |
|---|---|---|
| Business-model classifier → routes pricing/econ/financials | `business_model.py` | 🟡 (marketplace/ad/hybrid edges) |
| Market-scale classifier → routes sizing method | `skills/sizing/classify.py` | ✅ |
| Numbers validation gate (formula/funnel/segmentation) | `skills/sizing/validate.py` | ✅ |
| Triangulation by data-origin | `skills/triangulate.py` | ✅ |
| Generator→evaluator→refine loop | `harness/refine.py` | ✅ |
| Independent blind judge | `benchmarks/judge.py` | ✅ |
| LLM layer (thinking-off, retry, cache, temp0+seed) | `llm.py` | ✅ |
| Provenance trace | `provenance.py` | ✅ |

---

## What we WISH to add (ranked — from audit + literature)
1. 🔲 **Claim→source citation store + post-draft CitationAgent** — no sentence renders unbacked. *The #1 paid-report trust lever.*
2. 🔲 **Single canonical SOM** end-to-end (kills the double-discount + "profitable at SOM" contradiction).
3. 🔲 **Gate every reuse on `publishable`** — an unpublishable TAM can't reappear as a headline.
4. 🔲 **Validated outline artifact** (`plan.json` of sections/figures referencing only existing fields, gated before render).
5. 🔲 **Visual/render QA pass** (render → inspect for overflow / empty sections / broken tables).
6. 🔲 **Real sources on by default** — Census ACS + BLS keys, **Tavily** search, **trafilatura+fastembed** relevance gate, **Google/Yelp** local reviews.
7. 🔲 **Non-US support** — currency symbol + skip US-only data sources for non-US ventures.
8. 🔲 **Model-complete economics** — marketplace take-rate, ad-supported eCPM, hybrid dual-leg, transactional sensitivity.
9. 🔲 **Suppress degenerate analysis** — hide/hedge clustering maps when silhouette≤0; no noise-as-analysis.
10. 🔲 **Routing-grade descriptions** (WHAT+WHEN+negative-scope) on every `@tool/@skill/@agent`.
