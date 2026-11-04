# Castor Business-Vetting Audit — Results

Corpus: 16 ventures (15 generated; `vague_dogapp` rejected at intake, HTTP 422), each generated twice
(reproducibility). Method: [AUDIT_PLAN.md](AUDIT_PLAN.md). Two independent auditors per venture
(Claude, not the builder) scored 19 cells each; CRITICAL/HIGH findings adversarially verified.

**Completeness:** the audit ran twice (the first panel lost ~28/101 agents to transient Anthropic API
socket errors; a scoped re-run recovered all but one). Merged result below covers **15 of 16 ventures**;
only `deeptech_novel` has 0 scored cells (its 2 auditors died in both runs) — its verdict comes from the
routing/reproducibility data. Findings were merged across both runs (most-complete scoring per venture,
union of confirmed findings).

## Verdict

**NOT comprehensive.** Gate (≥90% pass, 0 CRITICAL): **FAILED hard — 21% of cells pass (46/224),
47 CRITICAL confirmed across 100 verified findings.** *Every* scored venture, including the hand-tuned
`cafe_la` and the SaaS baseline `saas_b2b`, carries CRITICAL or HIGH defects. The cafe success did not
generalize — not to a second cafe (Lisbon: € rendered as $, US Census math), nor to any other model.

## Per-venture scores (merged, 16 ventures)

| Venture | model | cells | fail | warn | crit | repro |
|---|---|---|---|---|---|---|
| cafe_la | transactional | 17 | 9 | 4 | 1 | run2 incomplete |
| restaurant_austin | transactional | 17 | 11 | 2 | 5 | stable |
| gym_denver | transactional | 17 | 9 | 5 | 4 | — |
| salon_brooklyn | transactional | 17 | 9 | 4 | 3 | — |
| foodtruck_portland | transactional | 10 | 7 | 2 | 4 | — |
| franchise_salad | transactional/regional | 17 | 11 | 2 | 4 | — |
| cafe_lisbon | transactional/intl | 10 | 3 | 6 | 4 | stable |
| saas_b2b | subscription | 7 | 3 | 1 | 1 | stable |
| app_membership | subscription | 10 | 3 | 4 | 0 | stable |
| ecom_dtc | ecommerce | 17 | 11 | 4 | 7 | — |
| hardware_iot | hybrid | 17 | 12 | 5 | 6 | **ΔTAM 88.8%** |
| marketplace_services | marketplace | 17 | 5 | 7 | 2 | ΔTAM 23.5% |
| adsupported_news | ad-supported | 17 | 5 | 6 | 0 | **ΔSOM 150%** |
| agency_design | B2B services | 17 | 6 | 5 | 5 | ΔSOM 25% |
| vague_dogapp | (vague) | 17 | 17 | 0 | 1 | no artifacts |
| deeptech_novel | thin-data | 0 | — | — | — | auditors died |

## Failure modes (ranked — each recurs across ventures)

### M1 — Geo-sourced local competitors are discarded everywhere except sizing  ★ biggest
*CRITICAL — cafe_la, restaurant_austin, salon_brooklyn, foodtruck_portland, cafe_lisbon*
The pipeline successfully finds the *real* local competitors (Intelligentsia, Go Get 'Em Tiger; 30 real
Austin restaurants) but then feeds **national DTC brands** (Javy, Chamberlain, Cometeer, Goldbelly, Blue
Apron — even **Quince, an apparel brand**) into clustering, differentiators, personas, AND
competitor_pricing. So the entire competitive analysis, positioning map, and price benchmark for every
hyperlocal venture is built on the wrong companies. *Fix:* when `discover.geo_sourced`, the geo list is
the canonical competitor set fed to ALL downstream steps; add a geo/category sanity filter.

### M2 — NULL TAM → 0-byte report (blank deliverable)
*CRITICAL — ecom_dtc, foodtruck_portland, franchise_salad, gym_denver, salon_brooklyn*
5 reports completed the pipeline (12–16 steps) with full JSON but rendered a **0-byte HTML report** — the
thing a paying customer opens is blank. Perfect 5/5 correlation with `market_sizing.tam.mid == null`: the
report template fails to render when sizing produced no TAM. *Fix:* guard the template against null
TAM/SOM (degrade to the honest banner), and make `/report.html` never return an empty body.

### M3 — SOM computed twice, two contradictory values
*CRITICAL — restaurant_austin ($1.44M vs $675K), cafe_la ($690K vs $1.5M), cafe_lisbon*
`market_sizing` and `financials/economics` independently derive SOM and disagree, producing the absurd
"economics says profitable at SOM, but every financial scenario loses money." *Fix:* one canonical SOM
flows through sizing → economics → financials; the existing "diverge 93%" validation warning should
hard-block, not warn.

### M4 — Business model misrouted (the 2-branch classifier)
*CRITICAL — hardware_iot (device revenue dropped), ecom_dtc (one-time $45 dropped), gym_denver
($30 drop-in dropped → "$149/mo account"); + marketplace, ad-supported, agency from routing data*
Marketplace/ecommerce/services/ad-supported/hybrid have no branch → jammed into subscription (or, for
agency, into *hyperlocal transactional* — a national studio sized as a local shop). A wrong model
poisons pricing, economics, financials, and TAM. *Fix:* add `marketplace` (take-rate/GMV), `ecommerce`
(AOV/repeat), `services` (project/retainer), `ad_supported` (eCPM/MAU), `hybrid` branches.

### M5 — Pricing benchmark table is NOT model-aware (cycle37 only fixed Pricing Detail)
*HIGH/CRITICAL — cafe, restaurant, gym, salon, foodtruck, hardware*
The benchmark still renders "$6.50/month per drink" and fabricates premiums by comparing a per-unit price
to a competitor's subscription/one-time price: "$75/mo per drink" (a bag of beans), "Quince $62.50 per
cover" (apparel), gym "396.8% premium", hardware "93.9% discount". *Fix:* make `build_benchmark_table` +
`competitor_pricing` model/unit-aware; never compare across units.

### M6 — Even the baselines are broken
*saas_b2b (CRITICAL):* per-seat vs per-company unit collision; bottom-up TAM uses monthly seat price as
annual ACV (**12× understatement**); financial customer counts exceed the entire serviceable firm
universe. *cafe_lisbon (CRITICAL/HIGH):* euros rendered with **$** throughout; TAM built on **US
Census/BLS** math for Portugal, then validated against the wrong (US) sources.

### M7 — Hyperlocal TAM inflated and city-invariant
*MEDIUM (quantitative)* — cafe_la and cafe_lisbon both = **exactly $40.25M** (115,000 households × $350);
restaurant $189M, gym $81M. `_estimate_households` returns ~115k for any neighborhood → TAM barely
depends on location.

### M8 — Degenerate clustering rendered as confident analysis
*HIGH — restaurant, gym, foodtruck* — silhouette ~0.03, **0% PCA variance** shown as a positioning
quadrant map. *Fix:* suppress/flag the map below a silhouette/variance floor.

### M9 — Price stated multiple ways across sections
*HIGH* — $35 vs $45 (restaurant), $80/cut vs $45 optimal (salon), $30 drop-in vs $149/mo (gym).

### M10 — Reproducibility breaks on cache miss
*quantitative* — hardware ΔTAM **88.8%**, adsupported ΔSOM **150%**, marketplace ΔTAM 23.5%.

### M11 — Reliability under load
Generating the corpus back-to-back on one free Gemini key degraded ~half the runs to incomplete;
individually they complete. Single-key production risk.

### M12 — Marketplace counts merchant GMV as platform revenue
*CRITICAL — marketplace_services* — a take-rate marketplace ($250 booking, 15% take) feeds the **full
$250 GMV** into SOM/economics/financials instead of the **$37.50** the platform actually earns →
revenue, margins, and break-even off by ~6.7×. No marketplace branch exists. *Fix:* model GMV and
platform revenue as two lines; drive economics off take-rate.

### M13 — Blind-spot models confirmed (now scored, not just inferred)
*CRITICAL* — with the re-run, the routing blind spots are confirmed by full scoring, not just the
classifier: **agency_design** (5 crit) — a national B2B studio routed to *hyperlocal physical premise*
with literal **cafe boilerplate** in its economics; **ecom_dtc** (7 crit) — one-time $45 serum as a SaaS
subscription; **hardware_iot** (6 crit) — $199 device dropped, and the recommended **$14.99/mo price is
5× the max simulated WTP ($3/mo)**; **adsupported_news** — free app, no price, pricing spine degrades
awkwardly.

### M14 — Validation gate errs in *both* directions
*HIGH* — it under-blocks the dual-SOM contradiction (M3, only a "diverge 93%" *warning*) yet
**over-blocks** `adsupported_news`, withholding an arithmetically-correct Market Size section as a
false positive. The gate needs both a hard-block on funnel contradictions and a relaxation for
legitimately price-free models.

### M15 — Smaller recurring defects
Single-domain price anchors rendered as a "category median" (app_membership: $4 from a dev studio →
"149.8% above median"; agency: $32/mo from Red Antler); CAC/CLV/competitor-count each stated 2–3 ways
in one report (app_membership, hardware); unit mis-extraction (`franchise_salad`: "$13.50/month per
**mo**"); degenerate WTP bands disconnected from price (franchise, hardware, marketplace).

## What works
- Scale routing (hyperlocal/regional/national/global) is mostly correct.
- Transactional & subscription *spines* are directionally right (when sizing + competitors don't break).
- Intake fails safe on a too-vague prompt (HTTP 422) rather than fabricating.
- Reproducibility is perfect (Δ0%) on the cleanly-handled ventures.

## Fix priority (root-first)
1. **M2** null-TAM → blank report (a blank deliverable is the worst possible outcome; cheap guard).
2. **M1** geo competitor set everywhere (invalidates competitive analysis for ALL local businesses).
3. **M3** single canonical SOM (kills the profitable/unprofitable contradiction).
4. **M4** business-model branches (unblocks marketplace/ecom/services/ad/hybrid).
5. **M5** model-aware benchmark; **M6** SaaS ACV + non-US currency/grounding; then M7–M11.
