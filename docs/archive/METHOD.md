# Methodology — Scientific Synthesis

**Goal:** A market research pipeline that combines the rigor of the original 14-step spec (bottom-up simulation: PSM, Max-Diff, 4Ps) with the empirical power of the Seena Rez DTC method (top-down revealed preference: traffic, ads, customer voice).

The thesis of this document: **the two methods are not alternatives — they are complementary lenses on the same market.** The spec predicts what *should* work using synthesized buyer panels. Seena's method measures what *is* working using real-world momentum signals. Combining them gives you both validation and novelty.

---

## 1. Two research traditions, one market

| Dimension | Spec method (bottom-up) | Seena method (top-down) |
|---|---|---|
| **Epistemology** | Stated preference — ask what buyers say they'd do | Revealed preference — measure what buyers actually do |
| **Data source** | Simulated panels, LLM-generated responses | Live web signals, real customer voice |
| **Statistical approach** | Distribution fitting (PSM curves, Max-Diff importance) | Time-series momentum + clustering |
| **Failure mode** | Hallucinated panels if no real buyers exist | Survivorship bias — you only see winners |
| **Strength** | Works for products that don't exist yet | Grounded in real money flowing now |
| **Scientific lineage** | Conjoint analysis (Green & Rao 1971) | A/B testing + behavioral economics |

Neither is sufficient alone. A pure spec-approach tool tells you "the optimal price is $47.50" based on a simulated panel — but you don't know if real buyers in this category will spend that. A pure Seena-approach tool tells you "brands like X are winning" — but you don't know how to position *your* product against them.

---

## 2. The Seena method, deconstructed scientifically

Seena's informal framing: **"Find what's rising → decode why → build the match."** Here is the same method expressed as measurable hypotheses.

### Move 1: Opportunity discovery via traffic momentum

**Informal claim:** "Rising brands are opportunities."

**Scientific claim:** A brand's rate-of-change in public attention metrics is a revealed-preference signal of product-market fit *above and beyond* its current absolute size. Specifically, the first derivative of traffic/search volume over time (d(attention)/dt) correlates with the brand's current PMF score more reliably than its level.

**Why it works:**
- Levels are dominated by incumbents with historical advantage (brand equity, SEO accumulation)
- Derivatives are dominated by *current* product-market fit — buyers discovering and sharing NOW
- Darwinian selection kills ads that don't convert, so long-lived ads = high CPA ROI
- Trustpilot review velocity is a direct demand signal (someone had to buy + use + post)

**Observable signals we measure (all in `discover.py`):**
| Signal | What it reveals |
|---|---|
| Google Trends 12mo slope on brand query | Real search-demand acceleration |
| Meta Ad Library ad count × longevity | Profit-proven advertising (dead ads get killed) |
| Trustpilot review velocity (reviews/month over time) | Purchase velocity (real money flowing) |
| Wayback Machine snapshot frequency | Site update cadence → site is alive and growing |
| Instagram follower count and posts/follower ratio | Social momentum + content efficiency |
| Domain age (via rdap) | Opportunity freshness — younger = less saturated |

**Falsifiable predictions:**
1. Brands with higher composite momentum scores (`_signal_score > 70`) should have higher 18-month survival rates than brands with low scores.
2. Removing any single signal from the score should degrade predictive power only marginally (robust to any single source going down).

### Move 2: Taste decoding via customer voice mining

**Informal claim:** "Reverse-engineer WHO is buying."

**Scientific claim:** Unprompted customer voice (reviews, Reddit, blog comments) is a high-signal / low-bias sample of the purchase-motivated population. LLM extraction from this corpus recovers the psychographic dimensions of the buyer segment with ≥0.7 confidence when ≥30 text samples exist, degrading gracefully below that threshold.

**Why it works:**
- Survey respondents lie about their motivations (social desirability bias). Review writers just vent.
- The *ratio* of celebrated vs. complained signals approximates the net promoter structure of the audience
- Vocabulary repeats within reviews indicate tribal language — critical for ad hook resonance (homophily hypothesis in marketing: audiences convert better when addressed in their own vocabulary)

**Observable signals we measure (all in `taste.py`):**
| Source | Evidence type |
|---|---|
| Trustpilot reviews | Structured ratings + unstructured prose |
| Reddit mentions (where unblocked) | Community discussion context |
| Review articles (DDG search for "{brand} review") | Journalist/blogger perspective |
| Brand homepage testimonials | Curated customer voice (biased but useful) |

**Falsifiable predictions:**
1. Hook angles extracted from real customer voice should out-CTR hook angles written by the brand's own marketing team (empirically testable via Meta Ads A/B).
2. The vocabulary we extract should appear verbatim in the top 10% of already-running ads in that category.

**Critical constraint we enforce:** No LLM hallucination. If we have zero sources, we return an error, not a made-up profile. The Stasher experiment proved why — when we tried LLM-knowledge fallback, Gemini produced a completely wrong profile (described it as a browser extension instead of silicone bags). The taste profile must be grounded in real scraped text or it's worthless.

### Move 3: Product-audience match scoring

**Informal claim:** "Build a product that matches the taste."

**Scientific claim:** The fit between a product idea and an audience taste profile is a semantic-similarity problem with four independent dimensions — pain alignment, aesthetic fit, vocabulary fit, motivation match. Each is independently scoreable 0-100, and the unweighted mean is a reliable predictor of the idea's commercial response rate within that audience.

**Why four dimensions:**
- **Pain alignment** — does the product solve a complaint the audience voiced?
- **Aesthetic fit** — does the product's vibe match the audience's celebrated descriptors?
- **Vocabulary fit** — can you market it in the audience's own words without translation?
- **Motivation match** — does the purchase motivation the audience described actually drive them to this product?

All four must be high for a confident score — a product can be a pain-solver but aesthetically off (e.g., a hospital-grade device for a beauty-conscious audience) and fail.

**Falsifiable predictions:**
1. Ideas scoring >80 should achieve meaningfully better CTR in test ads than ideas scoring <50 in the same audience.
2. The four sub-scores should be only weakly correlated — each measures a different construct.

---

## 3. How Seena's method integrates with each spec step

The spec was designed before Seena's method existed. Many spec steps were placeholders — "use MiroFish simulation" — because there was no better data source. Seena's method *replaces the placeholders with empirical signal*.

| Spec step | Spec's original approach | Seena-enhanced approach | Module |
|---|---|---|---|
| **Step 2** (extract profile) | LLM parse user description | *Unchanged* — this step is pure NLP | `profile.py` |
| **Step 3a** (search queries) | Generate queries to find competitors | *Unchanged* — LLM still does this | `discover.py` |
| **Step 3b** (scrape 50 competitors) | Google Search → scrape homepages | **Enhanced:** Google Trends rising queries surface momentum-validated brands directly; LLM fallback for category-knowledge backup | `discover.py` |
| **Step 3c** (cluster + PCA) | K-Means on description embeddings | **Deferred** — the top-3-by-score approach replaces PCA for prototype use | — |
| **Step 3d** (whitespace detection) | LLM identifies unfilled gaps | **Enhanced:** audience complaints from taste decoding ARE whitespace — unmet needs quoted verbatim | via `taste.py` → `four_ps.py` |
| **Step 5/6** (customer segmentation) | Scrape competitor customers + ICP LLM | **Replaced:** decode psychographic profile from real customer voice per-brand (more honest than firmographics for DTC) | `taste.py` |
| **Step 7** (opportunity scoring) | "MiroFish multi-agent simulation" — fictional | **Replaced:** deterministic composite score from 10 real signals | `_signal_score` in `discover.py` |
| **Step 8** (segment selection) | Operator picks top segment | **Same UI pattern** — surfaces top-K for operator review | `discover.py` synthesis |
| **Step 9a** (Max-Diff features) | Simulated panel via LLM | *Keeps the simulation* but inputs are enriched by discovered competitor feature sets | `pricing.py` |
| **Step 9b** (Van Westendorp PSM) | Simulated 4-question price panel | *Keeps the simulation* but inputs are enriched by real pricing from competitor homepages when available | `pricing.py` |
| **Step 10** (pricing framework) | Break-even + CLV + tier recommendation | *Unchanged* — classical unit economics | `pricing.py` |
| **Step 11** (place analysis) | LLM suggests channels from category | **Enhanced:** real homepage scraping detects competitor channel signals ("Book a demo" vs "Add to cart") | `place.py` |
| **Step 12** (validation gate) | Confidence flags on data quality | **Enhanced:** now has richer signal set to validate against (source counts, scraping success rates) | `plan.py` |
| **Step 13** (4Ps plan) | LLM synthesis with company + segment | **Enhanced:** synthesis now cites real customer quotes in Promotion section, real channel distribution in Place section | `four_ps.py` |
| **Step 14** (viability score 1-100) | "MiroFish multi-agent simulation" — fictional | **Replaced:** LLM scoring anchored to the concrete signal confidence, competitor density, audience match quality | `four_ps.py` |

**Result:** every "MiroFish simulation" placeholder in the spec is replaced by a real empirical signal from Seena's method. The spec's output structure (4Ps plan + viability score 1-100) is preserved — we just deliver it with much stronger inputs.

---

## 4. Why the combined method is scientifically stronger than either alone

### Argument 1: Cross-validation of signals

A hook angle extracted from taste decoding (Seena-side) can be cross-validated by checking if it appears in long-lived Meta ads for the category (traffic momentum side). A pricing recommendation from PSM simulation (spec-side) can be cross-checked against actual competitor prices scraped from homepages (Seena-side). If they disagree, the validation gate flags it — that's exactly when human judgment should kick in.

### Argument 2: Degrades gracefully on each axis

When Google Trends is rate-limited, LLM knowledge of the category still provides brand candidates. When Trustpilot is empty, DDG review-article search provides alternate customer voice. When Gemini is over-quota, previous jobs in the cache still power the UI. Every step has a fallback and no step silently invents data.

### Argument 3: The output is *falsifiable* in a way that the pure-simulation spec never was

Old spec: "MiroFish simulates how this business performs over 18 months and returns a viability score." There's no way to check that. It's an LLM confabulation dressed up as research.

New pipeline: viability score is a weighted function of measurable priors (competitor density, audience confidence, signal count) and interpretable LLM reasoning grounded in those priors. If the score says 72 but the brand bombs, you can trace which specific signal was wrong — was the density underestimated? Was the audience confidence inflated? Was a key complaint missed?

### Argument 4: Every step's output has an explicit confidence marker

Both sides — spec and Seena — produce confidence scores. The validation gate (step 12) aggregates them. A 4Ps plan produced with 0.9 audience confidence + 8 competitor signals has fundamentally different epistemic status than one produced with 0.3 audience confidence + 2 signals. The UI must show both and the user must trust them differently.

---

## 5. What this means for the UI and the operator

The operator should read every plan output with **three questions in mind**:

1. **What's the viability score and tier?** (top of report — the headline)
2. **What's the validation confidence and what flags fire?** (amber card — the trust qualifier)
3. **Where specifically did each 4P section get its evidence?** (each 4P card should cite competitor + audience + signal)

If the confidence is high and the signals are dense, trust the 4Ps and start building. If the confidence is low, the report is a *hypothesis to be tested*, not a verdict — commission real customer interviews before spending ad budget.

This is the same standard any scientific research paper holds itself to: report the confidence interval and the data sources, don't just give the point estimate.

---

## 6. Open research questions (for later iterations)

1. **Ground truth calibration.** We need to score ~50 real plans, run the recommended campaigns, and record actual CTR/conversion/24mo survival. Then we can calibrate the viability score against observed outcomes and retune weights.

2. **Embedding-space matching.** Current match uses LLM reasoning. A better approach: embed the taste profile and the idea into the same semantic space and compute cosine distance. Faster, deterministic, testable. Add as `match_v2` module.

3. **Ad-copy regurgitation check.** Verify that hook angles extracted from taste decoding actually appear in real running ads for similar products. If yes, strong validation signal. If no, either the audience is over-served (ads already cover it) or the extraction picked up fluff.

4. **Competitor pricing scrape.** `pricing.py` currently passes `competitor_prices=None`. Adding homepage price-scraping would anchor the PSM simulation to real prices, dramatically improving calibration.

5. **Longitudinal tracking.** If we run `/plan` on the same company monthly, the trend of its viability score over time is itself a signal (the company's market position is improving or eroding). Build a dashboard around this.

---

**TL;DR:** Seena's method provides the *empirical ground truth* that the spec's simulations always needed. The combined pipeline — profile → discover → decode → pricing sim → place analysis → 4Ps → viability — is more rigorous than either alone because every simulation is anchored to a real-world signal and every signal is contextualized inside a classical marketing framework. The output is a 4Ps plan with a viability score between 1–100, backed by explicit confidence metrics, cited evidence per section, and falsifiable predictions.
