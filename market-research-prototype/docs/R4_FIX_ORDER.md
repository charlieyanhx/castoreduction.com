# R4 Fix Order

Ranked fix order for the report generator, derived from 46 root-cause clusters produced by 10 per-rubric-row analysts over the 16-venture wave-4 corpus, adversarially verified (48 of 84 findings checked, 0 refuted). Every code location below was re-confirmed against HEAD (`729a872`). Rows are referenced by analyst topic: **fin** (financials/R12), **econ** (unit economics/R6), **pricing** (R5), **sizing**, **comp** (competitors/R8), **diff** (differentiators/R10), **wtp** (consumer research), **viab** (viability/R11), **prov** (provenance/withholding), **render** (template honesty).

## Already fixed this session — collapsed, not ranked

- **at_som_volume computed at som.high but labelled 100% of SOM** — fixed in `5a840f8` (plan.py `_enrich_economics_at_som` now pins to the base Y3 ceiling), gate `d23_at_som_matches_its_label` live. Residue folded into rank 3 below: the template's `< 100` caveat gate at report.html:1302 is now a dead branch.
- **Withheld profit rendered as a fabricated $0/mo in alarm red** — fixed in `2c6a2c9`, gate `d24_withheld_profit_not_fabricated` live.
- **Withhold stops at one Jinja block (rank 5)** — FIXED, data-layer. The sizing
  withhold ended at report.html's endif; the 3-Year Revenue table (computed from the
  withheld SOM) rendered unflagged directly below the "do not rely" banner, and
  score_viability was fed the raw sizing dict labelled "authoritative" — all four
  blocked reports scored Market Opportunity (22% of the composite) on the number the
  same page withheld. Now: (a) viability never RECEIVES withheld numbers — it gets
  an explicit "failed its integrity gate; score market_opportunity as UNKNOWN,
  neutral 50, say why" (the model cannot restate a number it never sees);
  (b) `financials.mark_derived_from_withheld` stamps the projection
  (`derived_from_withheld_sizing` + note) — the data-layer decision JSON consumers
  read — and the template renders the scenarios section under a matching red banner.
  Gate d29: unstamped financials on a blocked sizing fails; a scenarios REGION
  (heading to next h2) with no withhold language fails. Stored corpus: 4/4 blocked
  reports fail; the re-render with the stamp passes.
- **Identity-blind competitor discovery (rank 4)** — FIXED, four rules.
  (a) probe_domain_patterns now splits IDENTITY patterns ({slug}.com/.co — the
  brand's own name) from AFFIX lookalikes (eat/try/get/the{core}.com, {core}foods,
  {slug}.shop): affix hits are capped at "low", which the consumer already refuses —
  a live page at a manufactured host is a lead, never an identity. That closes the
  purpleair.shop hole (a squatter storefront whose prices became the category anchor).
  (b) A redirect landing on a DIFFERENT ROOT is a different company: the candidate
  yields nothing (kona.com -> deltek.com no longer makes deltek "Kona's domain").
  (c) "medium" additionally requires brand_names_match(brand, host label) — a shared
  stem (Kona vs konafoods) is not an identity.
  (d) PARKING_PATTERNS learned the observed domain-marketplace strings (afternic,
  dan.com, sedo, "parked free", "purchase this domain");
  RELEVANCE_THRESHOLD 0.45 -> 0.50 (0.45 sat at the 4th percentile of its own score
  distribution; 0.50 fires on the bottom tail while keeping the known-real 0.52 case
  — the grey zone above that is the identity rules' job, not the threshold's).
  Plus: _merge_enrichment_provenance carries domain_source/domain_confidence through
  the synthesis (251/251 stored records had neither, so no gate could ever see how a
  domain was adopted). Gate d28: pattern-probed "medium" domains must pass the
  brand-identity match, and off_category firing on 0 of >=50 scored records is
  itself a FAIL (a gate that never fires is decoration).
- **"% of SOM" label (rank 3)** — FIXED. The label divided each scenario's Y3
  ceiling by som_mid and printed the ratio as a capture claim — but the ceilings ARE
  the SOM band, so base always read "100.0% of SOM" and aggressive read 120-200%,
  an impossible number on 16/16 stored reports. The three scenario tables now name
  what the ceiling IS ("Y3 ceiling = SOM high end", ladder rungs as "N% of SOM"),
  all three assumptions blocks render `scenario_basis` (previously JSON-only), and
  the at-SOM caveat no longer attributes the base ceiling to "the aggressive
  scenario" (a mislabel D23's fix had exposed on the ladder path). Old stored JSON
  degrades to the basis tag, never back to the percent claim. Gate
  `d27_som_share_claims_possible`: >100% share fails; an unrendered scenario_basis
  fails; comparison is against UNESCAPED html — Jinja's &#39; for the apostrophe made
  the first draft report a rendered sentence as missing. Stored corpus: 16/16 FAIL;
  re-render passes.
- **P&L cost side (rank 2)** — FIXED, in four parts.
  (a) `business_model.multi_site_withhold_reason()` is now the ONE predicate for
  "SOM spans more sites than the fixed cost covers" — regional AND national_physical
  — consulted by both the at-SOM block and `project_three_year_transactional`, so the
  scenario table withholds profit/break-even with the SAME sentence economics uses
  (revenue and unit volumes stay; they are sound).
  (b) The subscription break-even is CAC-feasibility-checked against the venture's
  own `unit_economics.typical_cac_usd`: a break-even year whose acquisition spend
  meets or exceeds that year's revenue is reported as no-break-even, with the caveat
  naming the arithmetic; the no-CAC case now DISCLOSES that acquisition is excluded.
  (c) `estimate_cost_structure` is scale-aware — digital/global ventures are costed
  as early-stage company overhead (team+infra+tooling), not a storefront, and every
  result carries a `basis` naming which cost model produced it.
  (d) report.html's scenario profit cell is guarded — a withheld profit renders
  "profit withheld" + an amber banner with the reason, not a SafeUndefined "$0/mo
  profit" in red (the D24 class, one table up).
  Gate `d26_pnl_cost_side_honest`: withhold binds BOTH surfaces (the stored de34e328
  cross-surface contradiction now fails); CAC-infeasible break-evens fail (stored
  4a755faa fails on all three scenarios: base = 952 x $4,500 = $4.28M spend vs $160K
  revenue, claimed break-even Y1); implied op margin may never exceed the disclosed
  contribution margin.
- **Fabricated provenance chip (rank 1)** — FIXED. `n_sourced` split into `n_cited`
  (citation strings the model wrote) vs `n_grounded` (data_origin records a real
  fetch); the chip is green only for the second, amber "Citations: model-asserted —
  not retrieved" for the first; validate.py's F6 classifies grounded strictly by
  origin field (never substrings of source prose, never anything self-labelled
  UNSOURCED), and plan.py's figure builder now carries `origin` so F6 can fire when
  the census path genuinely runs. Gate `d25_provenance_chip_not_fabricated` fails all
  10 stored national reports and passes their re-render. Two stale tests updated —
  both had encoded the substring behaviour, and one sibling ("agree -> no warn")
  was passing vacuously with no grounded bucket at all.
- **Withheld TAM restated in narrative prose (D09 checked disclosure, not obedience)** — fixed in `f5758b1`. What that fix does NOT cover is ranked at #5: the SOM-derived revenue *table* and the raw sizing dict fed to viability sit outside both the template guard and D09's prose scan.

## 1. Dedupe: 46 clusters → 24 causes

The 46 clusters collapse hard. The biggest merges:

| Merged cause | Constituent clusters | Shared mechanism |
|---|---|---|
| Fabricated provenance chip | 2 (integrity chip; Sourced N/M degenerate) | plan.py:1066-1071 counts the LLM's free-text `source` string; `data_origin` never consulted |
| Cost classes missing from the P&L | 4 (flat fixed cost; no CAC; single-site prompt; regional-only guard) | one scalar "single-site rent+staff+utilities" is the entire cost side |
| Identity-blind discovery | 2 (pattern_probe lookalikes; inert 0.45 threshold) | sources.py accepts topicality as identity |
| No canonical competitor roster | 6 (4 counts/4 surfaces; clustering length-drop; stale active_signal_density; scope-free directive; 2 Overpass counts; viability cites raw counters) | every surface derives its own count/set |
| Price-vs-WTP reconciliation blind | 4 (0.1–10x deadband ×2; broken note; EVC no WTP input) | plan.py:329-395 compares scalars inside a deadband |
| WTP aggregation arithmetic | 2 (order-statistic median; n=2 bands / $0 payer) | skills/perspective.py:99-108 |
| Benchmark fabrication | 3 (mixed-SKU median; no n floor; synthesized anchor) | competitor_pricing.py medians unitless regex harvest, pricing.py relabels it |
| Withhold does not propagate | 4 (scenario table ×2; chip claim false; viability fed raw dict) | guard is one Jinja block, not a data-layer decision |
| Warns dropped | 3 (identical finding from 3 analysts) | template iterates only `.blocks` |
| Two prices of record | 3 (model vs PSM price; unit-blind reconcile; "Your stated price" label) | plan.py:1855-1862 fallback chain never reconciled |
| Differentiators fabricated | 3 (pre-evidence + forced yield; count-pinned strength; pre-PSM price claims) | step 3d runs before all evidence, prompt forbids empty |
| Hybrid erased | 3 (financials, economics, viability `_NO_SUB`) | `is_per_unit()` folds "hybrid" into per-unit |
| Non-priced models fall through | 3 (subscription catch-all branch; $0 falsy anchor; per-section ARPU invention) | falsy-zero checks + template `{% else %}` |
| Convergence fabricated | 2 (spread 0% single origin; analog degenerates to $1.5B) | triangulate.py collapses to one origin; analog prompt unconstrained |

## 2. Ranked fix order

Rank = ventures affected × how badly a buyer is misled. False numbers and fabricated provenance outrank incoherence; incoherence outranks cosmetics.

| # | Cause (one line) | Rows | Ventures | Code | Code-fixable? | Conf |
|---|---|---|---|---|---|---|
| 1 | Green "Sourced: 3/3" chip counts LLM-invented citation strings as sourcing; `data_origin` never read; "UNSOURCED" classed grounded by substring | prov, sizing | 16/16 | plan.py:1066-1071; skills/sizing/validate.py:200-206; templates/report.html:317-322 | Yes | high |
| 2 | P&L cost side is one single-site scalar — multi-site fixed cost, delivery labour and CAC all absent, so 50–70% operating margins and false Y1 break-evens are published; withhold guard covers only "regional" | fin, econ, viab | 14/16 | financials.py:96; pricing.py:329-374; business_model.py:190-263 | Yes | high |
| 3 | Scenario share divides by som_mid: base always "100.0% of SOM", aggressive 120–200%; `scenario_basis` written but never rendered | fin | 16/16 | financials.py:68-69; templates/report.html:699,734,763 | Yes | high |
| 4 | Discovery manufactures lookalike domains (`{slug}.shop`, `{core}official.com`) and accepts them on substring match; 0.45 relevance threshold sits below its own empirical floor — 67% of competitor records carry unverified identity, poisoning prices/trends downstream | comp, pricing | 15/16 | sources.py:381-416,178,341-342; discover.py:513-515 | Yes | high |
| 5 | Withhold stops at one Jinja block: the SOM-derived 3-Year Revenue table renders unflagged below the "do not rely" banner, and viability is fed the raw withheld dict labelled "authoritative" | prov, fin, viab | 4/16 | templates/report.html:540-674 vs 679; plan.py:2186; four_ps.py:989-1001 | Yes | high |
| 6 | Differentiators generated before any evidence exists, under a prompt that forbids returning zero; strength = f(count) is pinned "high" 16/16 and anchors the viability score | diff, viab | 16/16 | differentiators.py:138-157,200-211,342-348; plan.py:1478-1482; four_ps.py:1002-1003 | Mostly (sequencing, evidence-injection, dedup are code) | high |
| 7 | Competitor benchmark = median of a mixed-SKU $-regex harvest relabeled in the venture's own unit, with n=1 "category medians" and a false "normalized" claim | pricing | 7/16 | competitor_pricing.py:63-190; pricing.py:258-308; templates/report.html:1345 | Yes | high |
| 8 | WTP "median" is the upper middle order statistic (wrong in 10/16), bands minted from 2 respondents, $0 counts as a payer, "N of N would pay" counts anyone who named a number | wtp | 11/16 | skills/perspective.py:99-118 | Yes | high |
| 9 | No canonical roster: 4 competitor counts on 4 surfaces; clustering drops rows on text length (30→3); `active_signal_density` survives geo promotion from a discarded universe; scope-free directive invents "20 local competitors" | comp, viab, sizing | 16/16 | clustering.py:157-173,243-244; discover.py:198-207,709; plan.py:955-991; templates/report.html:952; four_ps.py:107-133 | Yes | high |
| 10 | Self-flagged junk competitors are relabeled, never excluded — they inflate density, occupy map dots, name PCA poles; misattribution verdicts travel in free-text `thesis`, not a flag | comp, diff | 14/16 | discover.py:453-467,143-146,736-742; plan.py:1456-1458,1482 | Yes | high |
| 11 | Price-above-WTP-ceiling never flagged: 0.1–10x deadband, point-to-point compare, falsy-zero skip; tiers unchecked; EVC has no WTP input; the note prints "0x" with a hardcoded consumer-vs-business excuse | wtp, pricing, econ, viab | 15/16 | plan.py:329-395; gates.py:376-394; economics.py:349-355 | Yes | high |
| 12 | Convergence fabricated: single-origin spread renders "0%" above tables spanning 8–28x; ±15% pad band excludes the report's own methods; analog method returns the identical $1.5B in 8/10 ventures | sizing | 10/16 | skills/triangulate.py:76-86; report/forecast.py:143-158; market_sizing.py:288-297 | Yes | high |
| 13 | `validation.warns` ("estimates diverge 11.1x — at least one is wrong") computed, stored, rendered nowhere; green "Validated" chip over live warnings | sizing, prov, viab | 9/16 | skills/sizing/validate.py:230-243; templates/report.html:546,308-311; plan.py:1080 | Yes | high |
| 14 | Viability's unit-econ anchor gated on `model == "transactional"` so hybrid/services/ecommerce get an invented margin (15% asserted vs 72.4% computed); financials never passed in | viab, econ | 8/16 | four_ps.py:1015-1021; plan.py:2173-2189 | Yes | high |
| 15 | SAM slice back-formed from the ratio while `key_assumption` contradicts it by >5pp and is rendered in 0/16 HTMLs; flat 0.35 hyperlocal default | sizing | 14/16 | market_sizing.py:607-633; skills/sizing/hyperlocal.py:156 | Yes | high |
| 16 | Two prices of record: fallback-chain price vs PSM optimal never reconciled; PSM-won price labelled "Your stated price"; `reconcile_pricing` unit-blind with hardcoded "/mo" | pricing, fin | 9/16 | plan.py:1855-1862,244-257,368-395; templates/report.html:1265 | Yes | high |
| 17 | Funnel ordering enforced and gated on mids only; the clamp mathematically guarantees SAM.high > TAM.high | sizing | 4/16 | market_sizing.py:636-676; gates.py:79-84; skills/sizing/validate.py:112-121 | Yes | high |
| 18 | Hyperlocal: "The SOM above is capacity-based" printed off a dead branch (SOM is an UNSOURCED LLM guess); TAM = πr² × LLM density × the whole BLS parent aggregate (gym sized on apparel spend) | sizing | 6/16 | skills/sizing/hyperlocal.py:46-72,282-308; tools/econ.py:45-79 | Yes | high |
| 19 | Hybrid ventures erased: routed per-unit in financials and economics, then `_NO_SUB` forbids viability from mentioning the recurring leg the profile defines | fin, econ, viab | 3/16 | business_model.py:35-38; plan.py:2141-2143,1901-1912; four_ps.py:30-45 | Yes | high |
| 20 | Non-priced models fall through: ad_supported hits the subscription `{% else %}` ("Annual price per customer: $ ", "cust"); $0.0 PSM falsy-disables the price anchor and turns gate d21 N/A | fin, pricing, render | 3/16 | templates/report.html:678-776; plan.py:1770-1788; four_ps.py:94-98; gates.py:476-483 | Yes | high |
| 21 | Bottom-up ACV fed the monthly/one-time price with no period label → 10–12x TAM errors against the pipeline's own annual price | sizing, pricing | 5/16 | market_sizing.py:201-246 | Yes | high |
| 22 | Near-dupe collapse never runs on the geo set; same-domain and corporate-family pairs plotted as rival camps ("Brooklyn Barber" twice, Angi vs HomeAdvisor as poles) | comp | 7/16 | discover.py:893-894; tools/geo.py:345-361 | Yes | high |
| 23 | Unit resolver emits bare "unit" (two prompts resolve it 1000x apart); one WTP band pools demand/supply/advertiser sides; hardcoded "$" and "/mo" | wtp, pricing | 6/16 | plan.py:440-512; skills/perspective.py:60-68; market_sizing.py:731-748 | Yes | high |
| 24 | Render/honesty small fry: dead `#sensitivity` anchor + methodology promise (14); self-refuting cannot-decode notice (10); "3 weakest assumptions" heading (5); churn 5.0 default (2); round() vs ceil() on units (3, mislabeled LLM in input — it is code); "· 0.0/day" (3); "b2b"→SaaS anchor substring (4); seat/account scale mixing (2); formula tokenizer phantom suffix wrongly blocking whole sections (2) | render, econ, sizing | up to 14 | report.html:291,655,1638; taste.py:271-291; financials.py:100,170; business_model.py:234,250; macro_anchors.py:361-368; economics.py:120-132,337-355; skills/sizing/validate.py:36-38 | Yes | high |

## 3. Top 8 — engineer-ready briefs

### 1. Fabricated provenance chip (16/16)
**Mechanism:** `build_integrity_summary` counts a TAM method as "sourced" when its LLM-authored `source` string is non-empty, and `validate.py` classifies a figure as grounded when that same string contains a substring like "census" — the real provenance field, `data_origin`, is written by exactly one code path that never fired in this corpus. **Buyer reads:** a green chip, "Sourced: 3/3 headline methods with a cited source", on all 10 national reports whose every triangulation path is `origin='llm'` — 7 of them naming "US Census Bureau / SUSB / BLS QCEW" for numbers no fetch produced; the 6 hyperlocal reports say "Model-estimated · origins: llm" even when BLS CEX genuinely fired. **Minimal fix:** count `n_sourced` from `data_origin != 'llm'` (plan.py:1068), classify grounded/modeled in validate.py:200-206 by `fig['origin']` not substring, and render "3 model-asserted citations (not retrieved)" in amber when origins are all llm. **Gate:** chip numerator must equal `len([p for p in triangulation.paths if p.origin != 'llm'])`; any figure whose source contains "UNSOURCED" must never land in the grounded bucket. Pure fixtures.

### 2. Cost classes missing from the P&L (14/16)
**Mechanism:** the only cost inputs are one LLM-guessed "single-site rent + staff + utilities" scalar and a per-transaction variable cost, so `monthly_profit = rev/12 × margin − monthly_fixed` holds fixed cost flat while revenue grows 1.7–12.5x, CAC is never subtracted anywhere, and the multi-site guard business_model.py already has (line 250-262) is not consulted by financials. **Buyer reads:** de34e328's scenario table prints $827.8K/mo profit at 15-store volume against one store's $28,500 rent — on the same page where economics *withheld* its profit verdict for exactly that reason; 4a755faa claims break-even year 1 while its own published CAC implies ~$4.3M acquisition spend against $160K Y1 revenue. **Minimal fix:** pass `market_scale` into the profit computation and withhold `monthly_operating_profit_usd`/`break_even_year` when SOM spans more sites than the fixed cost covers (mirroring business_model.py:259); pass `typical_cac_usd` into `compute_break_even` and the profit line, printing "not computable without CAC" when absent; derive `fixed_cost_basis` from a venture-shape-aware cost breakdown instead of the two hardcoded storefront strings. **Gate:** if `profit_withheld_reason` is set, no scenario carries `monthly_operating_profit_usd`; flag any scenario whose implied Y3 operating margin exceeds contribution margin minus 5pp; if CAC > 0 and `break_even_year == 1`, Y1 acquisition spend must be < Y1 revenue.

### 3. "% of SOM" label divides by the wrong denominator (16/16)
**Mechanism:** `_share_pct` (financials.py:68-69) divides each scenario's Y3 ceiling by `som_mid`, but the ceilings *are* som_low/mid/high, so base always prints "100.0% of SOM by Y3" and aggressive prints 120–200% — and the one field that explains this (`assumptions.scenario_basis`) is emitted in JSON but rendered by none of the three template branches. **Buyer reads:** every report's base case claiming full SOM capture and an aggressive case claiming up to "200.0% of SOM by Y3" — an impossible number. **Minimal fix:** swap the three `<td>`s at report.html:699/734/763 to a `y3_basis`-driven label ("ceiling = SOM low/mid/high") and append `{{ financials.assumptions.scenario_basis }}` to each Assumptions block. Also delete the now-dead `< 100` caveat gate at report.html:1302 and print the basis unconditionally. **Gate:** regex over every HTML — no match for `(1[0-9]{2}|[2-9][0-9]{2})(\.\d)?% of SOM`; if `scenario_basis` is in the JSON its first clause must appear verbatim in the HTML.

### 4. Identity-blind competitor discovery (15/16)
**Mechanism:** `probe_domain_patterns` manufactures lookalike hosts (`{slug}.shop`, `{core}official.com`, `try/get/the/eat{core}.com`) and accepts the first live one at "medium" on a ≥4-char substring match, silently adopting redirects to different companies; the only backstop is a 0.45 cosine threshold that sits at the 4th percentile of its own score distribution, so `off_category` fired on 9 of 263 records and passed every wrong-entity record the audit named. **Buyer reads:** purpleair.shop's squatter prices [9.99…349] as the category price anchor in 8add1fa2; becc8783's "fitness market momentum" computed from ladder.com, form.com and copilot.com (none of them the fitness apps); two domain-marketplace pages presented as competitor homepages. **Minimal fix:** drop the affix patterns (or demote their output to "low"); accept "medium" only when `brand_names_match(brand, root_label)` passes (the function exists at sources.py:224, unused on this path); treat `root(final_url) != root(candidate)` as a different entity requiring re-confirmation; add the observed parked-page strings to PARKING_PATTERNS. **Gate:** D-R8a — fail any record with `domain_source == "pattern_probe"`, confidence "medium", and a resolved root that fails `brand_names_match`; fixture asserts PurpleAir never yields purpleair.shop and a kona.com→deltek.com redirect yields no domain; plus the calibration test that `off_category` firing on 0 of ≥50 signals is itself a FAIL.

### 5. Withhold stops at one Jinja block (4/16)
**Mechanism:** `sizing_blocked` wraps report.html:550-674 only; the 3-Year Revenue Scenarios section opens at :679 and is computed entirely from the withheld SOM, and plan.py:2186 hands the raw sizing dict to viability labelled "authoritative". **Buyer reads:** 3219f4db's red banner ("formula computes 1.2e14 but value is 1.2e8, 1e+06x off — do not rely on these figures") followed immediately by an unflagged $96K/$420K/$1.2M revenue table built from that same funnel; all four blocked reports score Market Opportunity (22% of the composite) on the withheld TAM. **Minimal fix:** make the withhold a data-layer decision — when `publishable is False`, null out `financials.scenarios` upstream (or render the table inside the same red frame with a "derived from figures that failed the integrity gate" line), and pass viability a redacted sizing object with an explicit "score market_opportunity as unknown" instruction. The prose-restatement half is already covered by `f5758b1`/D09. **Gate:** extend D09 — when `publishable is False`, the HTML slice from `<h2>3-Year Revenue Scenarios</h2>` to the next `<h2>` must contain a withhold banner (or the section must be absent), and no formatting of som.low/mid/high or tam.mid may appear outside the flagged region. Currently 4/4 fail.

### 6. Differentiators fabricated before evidence, strength pinned "high" (16/16)
**Mechanism:** step 3d runs at plan.py:1478 on brand + 120-char blobs — before competitor pricing, channel evidence and scrapes exist — under a prompt that mandates ≥1 entry per dimension ("your job is to FIND it, not validate it"), and `differentiation_strength` is a pure function of the count that structure pins at 8–10, so it is "high" 16/16 and is handed to viability with an anchoring instruction. **Buyer reads:** geo-sourced ventures where the model's entire competitor input was "Ginger Lily: " (name, empty description) still get 10 differentiators asserting specific competitor pricing and booking behavior as unhedged fact, next to a viability score of 78–85 for "highly defensible" positioning on an unbuilt product. **Minimal fix:** move step 3d after the parallel evidence phase (no consumer before plan.py:1502), inject the evidence block into the dimension prompt, invert the mandate ("every why_unique must quote the evidence; return [] when it says nothing — the empty case already renders correctly"), add `evidence_ref` per entry, and dedupe by token-Jaccard before counting so strength derives from `n_distinct` evidence-backed entries. **Gate:** D-R10a — fail any pricing-dimension entry matching competitor-price language while `competitor_pricing` is absent (fires 16/16 today); fail any two entries with feature Jaccard ≥ 0.5 (11/16 today); corpus canary that strength is not 100% "high".

### 7. Benchmark rows are fabricated prices (7/16)
**Mechanism:** `scrape_brand_prices` pools every dollar amount regexed off up to 4 pages, medians the unitless mixed-SKU list, `gather_competitor_prices` medians those with no n floor, and `_label()` stamps the venture's own pricing unit onto the result while report.html:1345 claims "all normalized" — nothing normalizes anything. **Buyer reads:** "$59.99/month per account" attributed to Strava (real: $11.99/mo — the median of monthly, annual and gift-card prices), "our $13.50 is 66.2% below the $40.00 per bowl price of competitor Souvla" from a single scraped number, "$44 per project" benchmarked against an $18,500 project price. Zero of the 11 benchmark rows this pipeline has ever emitted came from a same-unit price list. **Minimal fix:** require per-domain coherence (n ≥ 3 and max/min ≤ 3) before a median becomes a row, else `price=None` with "no comparable per-unit price found"; return `category_median=None` when fewer than 3 domains priced; pass `n_competitors_with_prices` and psm.notes into the Price-section blob and retitle the header "scraped list prices, unit UNVERIFIED, n=N"; delete the "normalized" claim. **Gate:** d24/d25-style — fail any numeric row whose backing `prices_found` has n<3 or spread >3x (reds on 11/11 rows today); fail `category_median` non-null with n<3 (reds 6/16).

### 8. WTP aggregation arithmetic (11/16)
**Mechanism:** `_aggregate` takes `wtps_sorted[len//2]` (the upper middle order statistic, not a median), admits any 2 numeric answers as a full low/median/high band, counts a $0 "would not buy" answer as a payer, and reports `n_would_pay = len(wtps_sorted)` — segments that named any number, not segments at or above the recommendation. **Buyer reads:** 4a755faa's "median" WTP of $4,500 computed from [15, 4500]; 3219f4db's band 0/150/150 from two answers, with the headline "2 of 4 would pay" contradicting the per-segment panel showing one payer; ten reports printing an arithmetically wrong median, six of which collapse onto `high`. **Minimal fix:** `statistics.median()` at perspective.py:105; filter to strictly positive values; require n ≥ 3 for a band (else a labelled two-point range, `thin: True`); recompute the rendered count as "K of N named a price at or above the recommended $X". **Gate:** extend d10 — recompute the median from `consumer_research.interviews` and fail on >0.01 divergence; fail when any $0 interview is inside `n_would_pay`; fail `single_point == False` with n < 3. Pure JSON.

## 4. LLM-judgement clusters — what no code change fixes

These are the causes where the inputs can be correct and complete and the model still writes the wrong thing. Code can remove bad inputs, tag provenance, and gate lexical proxies; it cannot make the model's recall true or prevent restatement in words a regex misses. The load-bearing controls are retrieval, prompt constraints, and a seeded verifier pass (`report/verifier.py`) — tracked as trend, not pass/fail.

- **Top-down TAM anchors and named citations are recalled, wrong by 4–50x** (10 ventures). The prompt licenses inventing a number when no real report is known. Only real fix: retrieve the anchor or bound it against fetched macro data. Deterministic fence worth having anyway: a "global" anchor may not be smaller than the same payload's US bottom-up (8add1fa2 fails today).
- **SOM analog anchored on an invented competitor ARR** (10 ventures; 10/10 anchors uncited, 4/10 with capture ≥100%). The ≥100% multiplier and the missing-citation check ARE gateable; the ARR's truth is not.
- **Roster composition** (6 ventures): a cryptography firm ranked #1 in superconductors, the genuine category leaders absent. Needs a per-competitor verification search + a recall pass; gate coverage of the verification, not its verdict.
- **Epistemic upgrade in prose**: simulated panels called "empirical consumer data", Max-Diff scores called "58.5% of customers", "aligned with consumer willingness to pay" printed beside a WTP table that contradicts it, computed margins editorialized into "exceptional"/"highly realistic" (94008e7c calls a 4,718/mo break-even realistic against a 4,000/mo base case — that one IS gateable arithmetic), wrong world facts about Planet Fitness/Sweetgreen/food carts (9 ventures across four clusters). Fix path: provenance-tag every simulated input in the blob, forbid alignment claims when the mismatch flag is set, verifier check on provenance words.
- Note: the "round() vs ceil()" cluster arrived flagged `is_llm_judgement_not_code: true`; that flag is wrong — it is a two-line code fix (business_model.py:234,250) and is ranked in row 24.

## 5. Honest outlook

If the top 8 land and the corpus regenerates, R4 should move meaningfully but not dramatically. The top 8 directly clear the highest-frequency critical classes: fabricated sourcing (16 reports), the impossible %-of-SOM label (16), fabricated profit at scale (12–14), forced differentiators feeding viability (16), wrong-entity competitor data (15), fabricated price benchmarks (7), wrong WTP medians (10–11), and the four worst withhold leaks. That is plausibly 25–30 of the 53 criticals, concentrated in the fin, prov, pricing, comp, diff and wtp rows — so a pass rate moving from 13.5% to somewhere in the mid-20s to low-30s is defensible; doubling is realistic, tripling is not. What will still be broken: every national report's TAM magnitude still rests on a recalled top-down anchor and a degenerate analog method (ranks 12, 21 and the LLM section — untouched by the top 8); the sizing row keeps failing on SAM/key_assumption contradictions and band inversions (ranks 15, 17); viability keeps inheriting invented margins for non-transactional kinds (rank 14) and keeps overclaiming in prose no gate can fully catch; hyperlocal TAMs are still πr² × a guess × the wrong BLS series (rank 18); and the count-coherence and warns-rendering fixes (ranks 9, 13), while cheap, are not in the eight. Several fixes also *reveal* problems rather than remove them — honest provenance turns 10 green chips amber, benchmark coherence floors delete most pricing anchors, and WTP band minimums will mark several consumer-research sections "thin" — which is correct behavior, but rubric rows that score *evidence sufficiency* rather than honesty will keep failing those cells until the underlying collection improves. Expect criticals in the low 30s, a pass rate around 25–30%, and the residual dominated by model-recalled numbers and epistemic overclaim — the two things only retrieval and a verifier pass, not template or arithmetic fixes, can address.