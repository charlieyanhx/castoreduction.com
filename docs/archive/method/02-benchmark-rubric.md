# 02 — Benchmark Rubric

The benchmark scores a pipeline run on **16 dimensions**, weighted-averaged to a 0-100 final score with letter grade (A ≥90, B ≥80, C ≥70, D ≥60, F <60).

Each dimension has a **deterministic scorer** in `benchmarks/score.py`, except `prose_quality` which combines deterministic + LLM-as-judge sub-scores.

## Weight table

```
Dimension             Weight   Type         Module
─────────────────────────────────────────────────────────────────────
prose_quality          17%     LLM+det      score.py + prose_judge.py
coverage                8%     deterministic
tam_accuracy            8%     deterministic
competitor_recall       8%     deterministic
method_depth            8%     deterministic
differentiators         7%     deterministic
personas                7%     deterministic
pricing_psm             6%     deterministic
icp_alignment           6%     deterministic
unit_economics          4%     deterministic
segment_authenticity    4%     deterministic
citation_grounding      4%     deterministic
source_breadth          4%     deterministic
cagr_accuracy           4%     deterministic
validation_honesty      3%     deterministic
growth_scenarios        2%     deterministic
─────────────────────────────────────────────────────────────────────
                      100%
```

Two weight tables exist (`WEIGHTS_NO_PROSE`, `WEIGHTS_WITH_PROSE`) — passing `--with-prose` to the runner enables the LLM judge and uses the prose-weighted table.

## Per-dimension formulas

### `coverage` — pipeline completeness

```python
score = min(100, n_steps_completed / minimum_pipeline_steps × 100)
```
Default `minimum_pipeline_steps = 14`. Critical-step set tracked separately for warning bullets:
```
{profile, discover, differentiators, personas, max_diff, pricing, place,
 market_sizing, four_ps, viability}
```

### `tam_accuracy` — does TAM mid land in the reference band?

```python
if ref_low ≤ tam_mid ≤ ref_high:
    score = 100
else:
    distance_oom = |log10(tam_mid) - log10(geometric_mid_of_band)|
    score = max(0, 100 - distance_oom × 50)
```
1 order-of-magnitude off → score 50; 2 OOM off → score 0. Reference bands are intentionally wide because each venture description allows multiple defensible scopings (narrow ICP slice vs adjacent vs broad category).

### `cagr_accuracy` — does growth_cagr_pct land in the reference band?

```python
if ref_low ≤ cagr ≤ ref_high:
    score = 100
else:
    delta = abs distance to nearest band edge
    score = max(0, 100 - delta × 10)   # -10 per pp outside band
```

### `competitor_recall` — % of `competitor_must_include` found in pipeline output

Search locations checked, in order:
1. `discover.competitors[].brand`
2. `competitors[].brand` (top-level)
3. `clustering.clusters[].members[]`
4. `clustering.coordinates[].brand`

Match is **substring-bidirectional**: `e.lower() in d or d in e.lower()`. So "ADP" matches "ADP RUN", "Calm" matches "Calm Business".

### `icp_alignment` — band + buyer-role match

```python
band_match = expected_band in (icp_details.company_size_employees + " " + icp_summary)
buyer_match = any(kw.lower() in (icp_details.buyer_role + " " + icp_summary)
                  for kw in expected_buyer_keywords)
score = 50 × band_match + 50 × buyer_match
```

### `method_depth` — rigor checks

```python
score = (
    20 × (tam_3_methods_filled / 3)
  + 15 × psm_present
  + 15 × (max_diff_features ≥ 5)
  + 15 × (segment_ranking_top_n ≥ 1)
  + 20 × (four_ps_sections / 4)
  + 15 × viability_score_present
)
```

### `source_breadth` — # voice sources

```python
sources_attempted = count of {reddit, hackernews, stackoverflow, devto, lobsters, trustpilot}
score = min(100, n_attempted / expected_min × 100)   # expected_min default = 5
```

### `differentiators` — count + dimension coverage + strength

```python
score = min(100, n_total × 20 + dims_covered × 10)
score += {high: +5, moderate: 0, low: -10}[strength_rating]
```

### `personas` — count + field-completeness with backstop detection

```python
required = ["name", "core_motivation", "key_pain", "winning_message", "best_channel"]

# Field is "filled" only if non-empty AND not backstop placeholder
filled = sum(1 for f in required
             if persona[f].strip() and "TBD" not in persona[f] 
             and "not directly evidenced" not in persona[f].lower())

avg_completeness = avg(filled / 5 × 100 for persona in personas)
score = (60 if n_personas ≥ 2 else 30 if n == 1 else 0) + 0.4 × avg_completeness
```

This makes backstop placeholders visible — if a persona's `core_motivation` is "Motivation not directly evidenced — synthesize from interviews", the dimension counts that as **not filled**, so the operator sees the gap.

### `pricing_psm` — PSM completeness

```python
score = (50 × has_optimal_price_point) + (25 × has_acceptable_range) + (25 × has_tiers)
```
Looks at `pricing.psm.{optimal_price_point, acceptable_range, recommended_tiers}`.

### `unit_economics` — CLV/CAC ratio sanity

```python
ratio = clv / cac

if 2.0 ≤ ratio ≤ 5.0:    score = 100  ("healthy")
elif 1.0 ≤ ratio < 2.0 
  or 5.0 < ratio ≤ 10.0:  score = 70   ("marginal")
elif ratio > 10.0:        score = 40   ("implausibly-high — probably wrong inputs")
else:                     score = 20   ("broken")
```
3:1 is the canonical B2B SaaS healthy ratio, with a generous band.

### `segment_authenticity` — penalize defaulted scores

```python
n_defaulted = count of segments with _scores_were_defaulted = True
n_partial = count of segments with at least one score == 0.5

score = (n_total - n_defaulted) / n_total × 100 - n_partial × 10
```
The `_scores_were_defaulted` flag is set by `segment_scoring.score_segment` when the LLM refuses to score — we then default all 5 metrics to 0.5 to avoid a UI failure, but mark the flag so the benchmark can dock points.

### `citation_grounding` — real vs fabricated source detection

Two pattern lists:

```python
GROUNDED_TOKENS = {"max-diff", "psm", "company profile", "competitor", "pricing",
                   "max diff", "target audience", "evidence", "unit economics",
                   "clv", "cac", "evc", "reddit", "hacker news", "stack",
                   "lobsters", "dev.to", "competitor scrape", "homepage",
                   "trustpilot", "discover", "clustering", "customer voice",
                   "internal sleep loop"}

FAB_SOURCE_TOKENS = ["interviews (n=", "campaign performance report",
                     "internal study", "consulting analysis", "client brief"]

FAB_DATE_REGEX = r"\(q[1-4]\s*20\d{2}\)|\(pilot,\s*[a-z]{3}"
```

Scoring:
```python
real     = count of citations matching GROUNDED_TOKENS (and not in FAB_SOURCE)
fab_src  = count of citations matching FAB_SOURCE_TOKENS
fab_date = count of citations that are GROUNDED but ALSO have a fabricated date stamp

score = round(real/total × 100 - fab_src × 25 - fab_date × 5)
```

The fab-source vs fab-date split was added in cycle 30 because the original scorer was over-penalizing real artifacts that happened to have date stamps (e.g. "Customer Voice Analysis (Q4 2023)" — real source, fabricated date).

### `validation_honesty` — does pipeline self-flag thin data?

```python
has_flags = len(validation.flags) > 0
honest_confidence = (confidence < 100)

if not has_flags and confidence == 100:
    score = 20  # over-reporting — pipeline lies about thinness
elif has_flags and honest_confidence:
    score = 100  # honest signal
elif has_flags and not honest_confidence:
    score = 60   # mixed signal
elif honest_confidence:
    score = 70   # partial honesty
else:
    score = 0
```

This was the highest-leverage fix in cycle 30: a pipeline that says "100% confident, no caveats" on a thin venture is *worse* than one that says "27% confident, 8 flags raised" because it lies. The dimension reflects that.

### `growth_scenarios` — Y1<Y2<Y3 sanity

```python
sane = count of scenarios where y1 > 0 AND y2 > y1 AND y3 > y2
score = sane / n_scenarios × 100
```

Looks at `financials.scenarios.{conservative, base, aggressive}.year_{1,2,3}.revenue_usd`.

### `prose_quality` — see [`03-prose-judge.md`](./03-prose-judge.md)

## How the rubric was designed

Two principles:

1. **Cover every major report component.** If the report shows a TAM, score TAM. If it shows personas, score personas. The 16 dimensions exhaustively cover the rendered report.

2. **Each dimension can fail the pipeline independently.** That's why `validation_honesty` exists separately — a pipeline can produce "perfect" numbers but lie about confidence; the dimension catches that.

## What the rubric does NOT score

Be explicit about limits:

- **No copyright-bound prose comparison.** We don't include verbatim McKinsey/BCG passages. The "head-to-head" with consulting prose is against publicly-published *style traits* (specificity, citation density, action orientation), not real prose.
- **TAM bands are intentionally wide.** A pipeline that lands anywhere in `$500M-$25B` for Sleep Loop gets 100 — the bench cannot tell whether $1B or $20B is "more correct" (it's a scoping judgment).
- **Substring competitor matching.** Designed for fuzzy fits ("ADP RUN" matches "ADP") — by design liberal.
- **Citation whitelist is hand-curated.** Some real sources may not be in the whitelist and get marked "unrecognized".
- **LLM judge is itself an LLM** — non-deterministic, occasional malformed JSON salvaged via `json_repair`, ±5 score swings on identical input.
- **No correctness check** — the bench tells you "the TAM is plausible and the prose reads professional", not "the strategic recommendation is right". A real consultant would need to evaluate.

## Letter-grade thresholds

```python
A: ≥ 90
B: ≥ 80
C: ≥ 70
D: ≥ 60
F: < 60
```

Standard academic curve. No curve-fitting to make scores look better — a 65 should feel like a D and a 95 should feel like an A.
