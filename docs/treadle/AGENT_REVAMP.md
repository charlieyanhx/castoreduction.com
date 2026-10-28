# Agent revamp: adapted methods in the market research pack

Status: built and tested offline, 2026-10-28; live smoke and scorecard comparison below. Target: `packs/market_research_next` (what customers get). The
legacy `plan.run_plan` modules the original plan named are not used.

Goal: make particular tasks more accurate by adapting open methods, and make the report
functional and coherent end to end. Measured by `scripts/treadle_scorecard.py` against the
`baseline-2026-10-28` label on the five-brief corpus.

## Decisions

| Area | Decision |
|---|---|
| Google Places | Places API (New) REST, key `GOOGLE_MAPS_API_KEY` in `.env`. No key: the section says so and the run goes on. |
| Reddit discovery | Reddit's subreddit search through app-only OAuth (`REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`), ranked locally with reddit-research-mcp's rules. Reddit closed its anonymous JSON API, and their hosted server needs its own OAuth; neither is used. Without credentials the section says so and makes no call. |
| Pricing | Our own extraction; confidence and validation from sandbase `multi-source-search` (its pricing skills are stubs). |
| Search and verification | RivalSearch's `score.py` and `detect.py` ported; our existing search tools stay. |
| Budget | $20 of live runs for the build. |

## Architecture

New sections live in `packs/market_research_next/analysis/`, one module each:

```python
KEY = "sizing"                         # section key
LABEL = "Sizing calculation"           # heading in the report
def produce(context: dict, **deps) -> dict      # the section envelope, see below
def validate_data(data: dict) -> None           # ValueError on a bad payload
def render_data(data: dict) -> str              # escaped HTML fragment
```

Every section returns the envelope the research sections already return, plus `data`:

```
{"title", "narrative", "findings": [str], "assumptions": [str], "uncertainties": [str],
 "research_status": derived|completed|partial|simulated, "metrics": {}, "data": {...}}
```

`contracts.validate_section_output` checks the envelope; the module's `validate_data`
checks `data`. Research sections may now receive analysis sections as upstream input.
PREP, the business object, readiness and the eight research specifications are unchanged.

A missing input is an honest section, not a failure: the section returns
`research_status: "partial"`, says what is missing in `uncertainties`, and leaves the
numbers out. A section that does not apply (a local count for an online business) is
`not_applicable`. Only a genuine error fails a section.

Direct HTTP calls charge `treadle.execution_limits.charge_tool_call()`; model calls go
through `treadle.llm.call_json`, which charges itself. Every outside call has a timeout.

## Sections and dependencies

Every section also needs `prep`. "opt" marks an optional input: the section waits for it
when it runs, and runs without it when it does not.

| Section | Waits for | Kind | Adapted from |
|---|---|---|---|
| `competitor_count` | prep | HTTP, deterministic | local-competitor-map |
| `community_discovery` | prep | HTTP, deterministic | reddit-research-mcp ranking |
| `competitive_landscape` | opt competitor_count | research (existing) | also emits `metrics.competitors` |
| `customer_voice` | opt community_discovery | research (existing) | searches the discovered subreddits |
| `target_personas` | customer_voice | research (existing) | |
| `multi_perspective` | target_personas | research (existing) | |
| `market_size` | target_personas, opt competitor_count | research (existing) | also emits `metrics.figures` |
| `pricing` | competitive_landscape, opt competitor_count | agent + deterministic | sandbase confidence rules |
| `sizing` | market_size, opt pricing, opt competitor_count | deterministic | market-recon |
| `revenue` | market_size, competitive_landscape, opt sizing, opt pricing | research (existing) | reads sizing and pricing |
| `differentiators` | competitive_landscape, customer_voice | research (existing) | |
| `five_forces` | competitive_landscape, opt pricing, opt sizing | model + validator | porter-strategy-skills |
| `generic_strategy` | five_forces, opt pricing, opt sizing | model + deterministic checks | porter-strategy-skills |
| `analog_markets` | competitive_landscape, opt sizing | model + validator | market-analysis-hd stage 5 |
| `positioning` | target_personas, competitive_landscape, customer_voice, differentiators, generic_strategy, opt sizing, opt pricing | model, statement assembled in code | the plan's frame |
| `go_to_market` | positioning, target_personas, opt pricing | model | 4Ps |
| `summary` | opt everything | research (existing) | |

The review step adds a deterministic cross-section number check (RivalSearch `detect.py`):
two sections stating different figures for the same topic become a review issue naming
both, which the existing correction rounds repair (two rounds at most, report-wide).

## Methods, exactly

**competitor_count.** `POST https://places.googleapis.com/v1/places:searchText`, headers
`X-Goog-Api-Key`, `X-Goog-FieldMask: places.id,places.displayName,places.formattedAddress,
places.rating,places.userRatingCount,places.priceLevel,places.businessStatus,
places.primaryType,places.websiteUri`, body `{"textQuery": "<category> in <location>",
"maxResultCount": 20, "languageCode": "en"}`. Keep Google's order; drop
`CLOSED_PERMANENTLY`; rank 1..n after filtering. `priceLevel` maps FREE=0, INEXPENSIVE=1,
MODERATE=2, EXPENSIVE=3, VERY_EXPENSIVE=4; unspecified is null. Data: `query`,
`available`, `competitors[]`, `count`, `median_rating`, `median_price_level`,
`price_level_counts`. Applies when the venture is in person or a mix and has a location.

**community_discovery.** `GET https://www.reddit.com/subreddits/search.json?q=<terms>&limit=25`
with a descriptive User-Agent, terms built from category and geography words only (never
the brief). Confidence is token overlap between the query terms and the subreddit name,
title and description; then reddit-research-mcp's rules: generic subreddits (funny, pics,
videos, gifs, memes, aww, news, science) ×0.3 unless the query names them; ≥1,000,000
subscribers ×1.1 (cap 1.0); <10,000 ×0.9; NSFW dropped; sort by (−confidence,
−subscribers); keep 10. Tiers by confidence: exact ≥0.8, semantic ≥0.6, adjacent ≥0.35,
else peripheral (their distance tiers cannot be reproduced without their embeddings).

**pricing.** For up to five competitors from `competitive_landscape.metrics.competitors`,
an agent with the existing search and price tools finds pricing tiers; the model returns
`{competitor, url, tiers[{name, price, currency, billing_model, unit, features[]}],
source_urls[]}`. `billing_model` ∈ one_time, per_unit, monthly, annual, usage, free,
custom. Confidence per competitor counts independent source domains (registered domain,
`www.` stripped): 1 low, 2 medium, ≥3 high; a competitor whose sources disagree on a
price is never high. Category median and range in monthly terms (annual ÷ 12) are
computed in code. Local venues add the Places price-level distribution.

**sizing.** market-recon's three methods, pure functions:
`top_down TAM = market_value`; `bottom_up TAM = customers × ARPU`;
`value_theory TAM = comparable_price × positioning_factor × customers`, factor
`clamp(price/comparable, 0.5, 2.0)`, 1.0 without a price. `SAM = TAM × serviceable_share`
(a sourced share in (0, 0.9], else 0.30 with a warning). `SOM = SAM × obtainable_share`,
`share = clamp(base × 1/(1 + competitors/10) × saturation, 0.005, 0.25)`, stage base
pre_launch 0.02 / early_customers 0.05 / scaling 0.10, saturation low 1.25 / medium 1.0
/ high 0.6. Inputs come from `market_size.metrics.figures`, the founder's price, the
Places or landscape competitor count. Every number carries its formula, inputs,
citations and confidence: none without a citation (low when only the founder's word),
high with ≥2 independent registered domains all ≤180 days old, otherwise medium. The
founder's own input never counts as a domain (upstream counts it; we do not). A method
missing an input is not computed and says which input is missing.

**five_forces.** The 32 sub-factors by name (entry 8, rivalry 8, buyer 7, supplier 6,
substitutes 3), each High/Medium/Low with a one-sentence justification; each force
High/Medium/Low; governing forces; profit potential; 2 to 3 implications. Government and
technology are read through the five forces, not added as a sixth. The validator rejects
a missing, renamed or extra sub-factor.

**generic_strategy.** Cost leadership, differentiation or focus, with confidence; the
requirements fit table (requirement, met/partial/unmet, gap); the strategy's risks; the
six stuck-in-the-middle flags, where three or more flags is "likely stuck", counted in
code.

**analog_markets.** Characterise the venture on the seven structural characteristics;
3 to 5 analogs from other verticals, each sharing at least 3 named characteristics;
per analog: period, trajectory, inflection points, winner traits, failure modes; 2 to 3
lessons each, "In X, Y showed Z. For this venture, this suggests W.", with confidence.
Labelled as model analysis; sources are listed when known and marked unverified.

**positioning.** The model fills the frame's fields, each naming the section it came
from; code assembles "For [target customer] who [need], [product] is a [category] that
[key benefit]. Unlike [competitor], [product] [key differentiator]."

**go_to_market.** Product, price, place, promotion, each with a summary and actions,
tied to the positioning and strategy.

**cross-section numbers.** RivalSearch `detect.py` numeric rule over section texts:
same unit class, values differing by ≥5%, ≥2 shared topic tokens within ±50 characters,
confidence `min(1, 0.5 + 0.1 × shared)`. Issues at confidence ≥0.7 between two
model-written sections become repair issues.

## Decisions made during the build

- **SOM needs a competitor count.** Upstream defaults to zero competitors, the largest
  share the formula allows. Here, without a Places or landscape count, SOM is left
  uncomputed and says why; TAM and SAM stand.
- **A competitor's own pricing page is at least medium.** Upstream counts independent
  sources only, which would rank an aggregator above the vendor's own page.
- **The go-to-market price P holds only money amounts to the data.** "A 14-day trial" or
  "20% off" is not a price; an invented $ amount is rejected.
- **Number review threshold 0.8, at most two issues a round.** Two shared topic words
  (0.7) proved too loose for repair work.
- **`build_report_type(analysis=False)`** gives the eight research sections alone, which
  the older tests pin; `analysis_producers` injects producers by key.
- **value_theory equals bottom_up** unless its positioning factor is clamped, so the two
  agreeing is not corroboration.

## Fixes from the independent review

- A failed Places call is a partial section, not a failure: a failure would have sent
  every section reading the count through both correction rounds for an outside fault.
- Pricing shows local price levels only from a search that ran.
- A founder price is annualised only by a period written right after it; "$5 per latte,
  about 300 a week" is not $260 a year, and a per-seat price is not a per-customer one.
- An empty competitor list is no count; a share is divided by 100 only above 1.
- Research figures keep only sources the run actually fetched, dated this run; a
  competitor's website stays only when its domain was seen.
- Pricing ignores parser output and bare snapshot addresses as evidence.
- A rerun analysis section is shown the review's issues; the number review names
  research sections only, which already rerun with their issues.
- The model reviewer may name only sections this report planned.

## Attribution

`packs/market_research_next/NOTICE.md` credits each source, its license and what was
changed: market-recon (MIT, market-recon contributors), local-competitor-map (Apache-2.0,
Daniel Agrici), sandbase-skills (Apache-2.0, SandBase), RivalSearchMCP (MIT,
damionrashford), porter-strategy-skills (CC BY 4.0, George Nurijanian), market-analysis-hd
-skill (MIT, dungnotnull), reddit-research-mcp (MIT, King of the Grackles).
