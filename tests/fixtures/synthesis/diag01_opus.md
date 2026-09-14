# Mission District specialty coffee and micro-roastery: what the evidence supports

## The three things that should move your decision

**One. The revenue anchor is a county-wide average, not a read on your corner.** Every projection in this pack descends from a single obtainable figure of $645,289 a year [market_sizing.som.mid]. Its derivation is published in full and is honest about itself: $884,029 average annual receipts per establishment across 525 establishments in San Francisco County under NAICS 722515, from the 2022 Economic Census, multiplied by 0.638 for single-unit firms and 1.145 for CPI-U drift [market_sizing.som_anchor_chain]. The pipeline attaches a warning to it in its own words: this is a mean across every establishment in the county, so "a particular location can plausibly run at half or double it" [market_sizing.som_anchor.note]. The scenario band does not honour that warning. The conservative case is set at 70% of the mid [financials.scenarios.conservative.year3_market_share_pct], a 30% haircut, when the source itself says 50% is plausible. Treat the conservative column as the middle of your planning range, not the floor. Overall data quality on the sizing is flagged low [market_sizing.data_quality].

**Two. The cost side, which decides whether this works, is invented.** Break-even of 127.9 drinks a day [economics.break_even_units_per_day] rests on $16,500 a month of fixed cost [economics.monthly_fixed_cost] whose source is recorded as "LLM estimate (UNSOURCED — operator should validate)" [economics.cost_source]. Variable cost of $1.45 a drink [economics.variable_cost_per_unit], which produces the 74.8% contribution margin [economics.contribution_margin_pct], carries the same provenance. The pipeline's own scoring capped unit-economics health at 45 out of 100 for exactly this reason [viability.score_composition]. The tolerance is published: the base-case verdict holds only while real fixed costs stay under about $40,223 a month [financials.scenarios.base.fixed_cost_ceiling_usd], but that ceiling is measured against year-three volume. In year one of the conservative case the shop clears $394 a month of operating profit [financials.scenarios.conservative.year_1.monthly_operating_profit_usd]. A signed Mission lease, a payroll schedule and a roaster finance quote would move this report from "moderate" to decided. Nothing else on the list is worth more money.

**Three. The pipeline found zero differentiators, and the wholesale leg you are counting on as a hedge is the weakest-evidenced part of the plan.** The differentiation audit returned an empty list across all five dimensions, strength "low", reasoning "0 evidence-backed differentiators across 0/5 dimensions" [differentiators.differentiation_strength, differentiators.strength_reasoning]. That sits against 84 venues of this type inside the catchment [market_sizing.competitors], among them Ritual Coffee Roasters and Four Barrel on Valencia Street, both roaster-cafes doing the thing you propose to do, on the street you propose to do it on [discover.synthesis.ranked_opportunities]. Meanwhile the simulated Max-Diff put wholesale beans last of five features with an importance score of 10 and listed it under "deprioritize" [max_diff.ranked_features, max_diff.deprioritize], and the wholesale buyer segment scored 0.48 on a 0 to 1 scale with competition rated 0.3 [segment_ranking.top_pick.final_weighted_score].

---

## What the market numbers actually say

The trade-area model is the most defensible thing here. It measures 28,871 households within a 1.5 km, 7.07 km² catchment from ACS 5-year 2022 data joined to TIGERweb, and applies a BLS Consumer Expenditure figure of $3,945 per household per year scaled by 1.320 for this tract union's income distribution, giving $5,208 per household [market_sizing.tam.calculation, market_sizing.spend_income_adjustment.multiplier]. That yields TAM of $150.3M [market_sizing.tam.mid] and a 35% serviceable slice of $52.6M [market_sizing.sam.mid].

Ignore both. A 14-seat room cannot address $52.6M; the SAM exists only so the SOM can be taken as the lesser of supply and demand anchors, which it is ($645,289 supply-anchored versus $619,087 fair-share demand [market_sizing.som_supply_usd, market_sizing.som_demand_usd], a spread of 1.0x). The two anchors agreeing is mildly reassuring. Note one unreconciled item: the evidence carries both 84 competing venues [market_sizing.competitors] and 251 same-category venues [market_sizing.n_same_category] in the same catchment, and does not explain the gap. The fair-share division used 84. If 251 is the right denominator, the demand anchor falls.

## Volumes, and the throughput question the evidence does not answer

| Rung | Drinks/day | Source |
|---|---|---|
| Break-even | 127.9 | [economics.break_even_units_per_day] |
| Year 1 planning target (base) | 187.0 | [financials.scenarios.base.year_1.units_per_day] |
| Year 3 ceiling (base) | 311.7 | [financials.scenarios.base.year_3.units_per_day] |
| Year 3 (aggressive) | 405.3 | [financials.scenarios.aggressive.year_3.units_per_day] |

Base-case revenue runs $387,173 in year one, $548,495 in year two, $645,289 in year three, on a retail ramp of 60/85/100% [financials.scenarios.base, financials.assumptions.growth_curve]. Monthly operating profit in base year one is $7,634 [financials.scenarios.base.year_1.monthly_operating_profit_usd].

The evidence contains no hourly throughput calculation, no peak-hour model and no queue estimate. That is a real gap: 311.7 drinks a day across an 11-hour window, from one espresso bar that also does manual pour-over, with 14 seats, is an operations question, not a demand question, and nothing here tests it. Your own service-time measurement on a rented machine, or a day spent counting tickets at Ritual, would settle it.

## Price: built on $5.75, not your $5.50

Every financial figure above uses $5.75 a drink [economics.price_per_unit]. The pipeline recorded no stated price from you [pricing.price_of_record.no_stated_price] and defaulted to the PSM optimal of $5.75, with an acceptable range of $4.25 to $7.50 [pricing.psm.optimal_price_point, pricing.psm.acceptable_range]. There is no run at $5.50 anywhere in the evidence, so if you hold your number, break-even units rise and every profit line drops by an amount this pack does not compute.

The PSM itself comes from a simulated panel of 40 [pricing.psm.panel_size]; the willingness-to-pay range of $4.50 to $8.50 with a $6.50 median comes from four simulated interview personas [consumer_research.synthesis.willingness_to_pay]. Neither is field data.

The competitor price benchmark was dropped, and the reason is worth reading: scraped venue pages rarely publish a clean per-unit price, and the one domain that yielded prices produced a median of $21 [competitor_pricing.per_domain[0].median] from what the page text shows to be bagged retail coffee, not a drink. With only one domain returning comparable prices against a threshold of three, no category median was published [competitor_pricing.category_median_reason]. So there is no evidence in this pack for what a cup costs on Valencia Street. The nearest signal is Google price bands on four venues:

| Venue | Rating | Reviews | Price band |
|---|---|---|---|
| Sightglass Coffee | 4.4 | 3,420 | $10–20 |
| Ritual Coffee Roasters | 4.3 | 1,730 | $10–20 |
| CoffeeShop | 4.7 | 303 | $1–10 |
| Grand Coffee Too | 4.4 | 179 | $1–10 |

Source: [market_sizing.geo_competitors]. These are per-visit spend bands, not per-drink prices. Four mystery-shop visits would produce better pricing evidence than anything in this report.

## The wholesale leg has almost no evidence behind it

The customer universe returned 12 companies [customer_universe.count]. Reading them: one is a plausible local prospect (Noe Cafe), one is a TikTok discovery page, one is a wholesale supplier in Melbourne, one a UK catering distributor, one a cafe in Batumi, one an espresso bar in Toronto, one a Craigslist barista ad in San Diego, one the Specialty Coffee Association. That is not a prospect list; it is search noise. The segment structure built on top of it (70% independent cafes, 30% multi-unit brands) inherits that weakness, and the pipeline's own key risk on the top segment names high churn and incumbent discounting compressing margin [segment_ranking.top_pick.key_risk].

No wholesale price per pound, no account size, no gross margin on beans appears anywhere in the evidence. The financial model treats the business as purely transactional at $5.75 a drink [economics.model], so wholesale revenue is neither included nor sized. If wholesale is meant to carry part of the rent, that case is currently unmodelled.

## What the pipeline could not see, stated plainly

The audience decode and personas sections were dropped because no consumer signal existed for the three brands queried; the underlying evidence shows why, and it is not a small thing: 0 Google reviews and 0 Trustpilot reviews returned for Noe Cafe, Another Cafe and Ritual Coffee Roasters, with Reddit unavailable on HTTP 403 across all three [audiences_undecodable]. The validation step flags that only one of three customer-voice sources returned data [validation.flags]. So there is no decoded voice of the Mission coffee customer here. The four personas in the consumer research are LLM constructs, useful as an objection checklist, not as demand evidence.

The discovery scan is largely irrelevant to a hyperlocal venture. Of the signals harvested, the top-scored names include a Turkish/Qatari roastery, a Copenhagen coffee bar, a Singapore roaster, a Hudson Valley roaster and a Colorado Springs shop [discover.steps.signals]. Active web-momentum signal was found on 7 of 47 profiled names [discover.active_signal_density], average opportunity score 6.1 [discover.avg_opportunity_score]. Instagram follower counts for cafes in other hemispheres tell you nothing about your corner.

The positioning map places 26 of 47 rostered competitors, 55% coverage, with a silhouette score of 0.194 and PCA explained variance reported as 0.0 [clustering.coverage_pct, clustering.silhouette_score, clustering.pca_explained_variance]. The pipeline's own coverage warning says the axes describe only the plotted subset, not the market. I would not use it.

Verification passed with 0 blocking and 12 advisory findings, all of them uncited or unsupported numbers inside the 4Ps narrative (for example the "42 combined importance points" figure and the $450 marketing budget) [verification.summary, verification.findings]. Fifteen checks could not be run at all [verification.summary.coverage.blind_ids]. Composite viability is 46 of 100, tier "moderate" [viability.viability_score], on an evidence confidence of 0.47 [validation.confidence_score].

## Risks, ranked by money at stake

1. **Fixed costs.** The entire break-even verdict rests on an unsourced $16,500 a month. A Mission corner lease plus a roaster plus SF payroll could plausibly sit well above that, and the base case tolerates only up to about $40,223 [financials.scenarios.base.fixed_cost_ceiling_usd] at mature volume. This is the number that decides solvency.
2. **Revenue anchor error.** The source admits half-or-double [market_sizing.som_anchor.note]; the published downside only goes to $451,702 [market_sizing.som.low]. At half the mid you are below the conservative floor and, on the stated cost base, likely below break-even.
3. **Undifferentiated entry into a dense field.** Zero differentiators [differentiators.differentiation_strength] against 84 venues [market_sizing.competitors], including two established roaster-cafes on your street. The pipeline rates "failing to convert foot traffic amidst 47 local competitors" as high likelihood, high impact [viability.risks].
4. **Wholesale as an unmodelled hedge.** Last in Max-Diff, weakest segment scores, no pricing or margin data, a prospect list that is mostly noise.
5. **Price anchor.** $5.75 is simulated, not benchmarked, and is not your stated $5.50.

## The cheapest things that would change this report

Before the lease: get a real rent quote, a real payroll schedule and a roaster cost, and rerun break-even. Mystery-shop four Valencia and Mission competitors for actual per-drink prices. Run the pipeline's own 30-day counter pilot, targeting 150 or more daily orders sustained by day 14 [viability.recommended_next_steps], and measure service time per drink while you do it. On wholesale, the stated 60-day test is three signed accounts [viability.recommended_next_steps]; get one signed at a known price per pound before you buy the roaster.

Hold the published kill criteria: 30 consecutive days below 127.9 drinks a day, or contribution margin below 50% [viability.kill_criteria]. Those are the right triggers. They are only meaningful once the $16,500 becomes a real number.