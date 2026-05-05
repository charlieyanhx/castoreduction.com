# 05 — Scoring Formula Reference Card

A one-page reference for every scoring formula in the benchmark. For full context and design rationale, see [`02-benchmark-rubric.md`](./02-benchmark-rubric.md).

## Quick lookup

```
Dimension              Formula (compressed)                           Weight
──────────────────────────────────────────────────────────────────────────────
coverage               min(100, n_steps / 14 × 100)                     8%
tam_accuracy           100 if in band, else 100 - 50×log10_distance     8%
cagr_accuracy          100 if in band, else 100 - 10×|pp_outside|       4%
competitor_recall      |matches| / |expected| × 100                     8%
icp_alignment          50×band_match + 50×buyer_match                   6%
method_depth           20×(tam/3) + 15×psm + 15×(md≥5) + 15×seg
                       + 20×(4ps/4) + 15×viab                           8%
source_breadth         min(100, n_attempted / 5 × 100)                  4%
differentiators        min(100, n_total×20 + dims×10) + strength_bonus  7%
personas               (60 if ≥2 else 30 if 1 else 0) + 0.4×completeness 7%
pricing_psm            50×opp + 25×range + 25×tiers                     6%
unit_economics         banded by clv/cac ratio                          4%
segment_authenticity   real/total × 100 - n_partial×10                  4%
citation_grounding     real/total×100 - fab_src×25 - fab_date×5         4%
validation_honesty     banded by (has_flags, confidence < 100)          3%
growth_scenarios       monotonic_count / 3 × 100                        2%
prose_quality          mean across 4 sections of weighted 6-trait sum  17%
                                                                      ─────
                                                                      100%
```

## Final score & letter grade

```python
final_score = sum(dimension.score × WEIGHTS[dimension] for dimension in 16_dims)

letter = "A" if final ≥ 90
       else "B" if final ≥ 80
       else "C" if final ≥ 70
       else "D" if final ≥ 60
       else "F"
```

## Banding reference for `unit_economics`

```
CLV/CAC ratio       Score    Verdict
─────────────────────────────────────────────────────
2.0 - 5.0           100      healthy
1.0 - 2.0           70       marginal (CAC too high)
5.0 - 10.0          70       marginal (CAC too low — leaving growth on table)
> 10.0              40       implausibly-high (probably wrong inputs)
< 1.0               20       broken
```

## Banding reference for `validation_honesty`

```
flags > 0?    confidence    Score   Interpretation
────────────────────────────────────────────────────────────────────────
False         100%          20      over-reporting — pipeline lies about thinness
True          < 100         100     honest signal
True          100           60      mixed signal (raised flags but still 100%)
False         < 100         70      partial honesty (acknowledges but no specifics)
False         missing       0       no validation at all
```

## Prose quality — 6-trait weights

Each section (product / price / place / promotion):

```
specificity              25%   deterministic
citation_density         20%   deterministic
no_buzzwords             15%   deterministic
action_orientation       15%   LLM
hedging_discipline       10%   LLM
executive_readability    15%   LLM
                       ─────
                        100%
```

Section score = weighted sum. `prose_quality` = mean of 4 section scores.

## Targets used in deterministic prose traits

```
Trait                Target threshold (per 100 words)
─────────────────────────────────────────────────────
specificity (nums)   ≥2 → 100; <0.5 → 0
specificity (nouns)  ≥4 → 100; <1.0 → 0
citation_density     ≥1.3 → 100 (= ≥1 per 75 words)
no_buzzwords         0 → 100; 0.5 → 50; ≥1 → 0
```

## Patterns used by deterministic scorers

```python
_NUMBER_RE   = r"\$?\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|[KMB]?\b|million|billion)?"
_BRAND_RE    = r"\b[A-Z][a-zA-Z]+(?:\.[a-z]+)?\b"
_CITATION_RE = r"[¹²³⁴⁵⁶⁷⁸⁹⁰]"
_BUZZWORD_RE = r"\b(synergies|leverage(?:s|d|ing)?|paradigm|holistic|robust|
                    best-in-class|unlock value|streamline|cutting[- ]edge|
                    world-class|transformational journey|proactively)\b"
_FAB_DATE_RE = r"\(q[1-4]\s*20\d{2}\)|\(pilot,\s*[a-z]{3}"
```

## Edge cases

- **Empty pipeline output**: every numeric scorer returns 0; `_validation_gate` raises a "no result" flag
- **Pipeline crashed mid-run**: only steps with results are scored; missing steps register as missing-critical bullets
- **LLM judge truncated**: missing sub-trait scores default to 50; `_parse_error` flag set
- **Cached pipeline result loaded with old schema**: legacy paths checked (e.g. `pricing.optimal_price_point` AND `pricing.psm.optimal_price_point`)

## CLI invocations

```bash
# Score a single completed job
python -m benchmarks.score path/to/job.json [--case=NAME] [--with-prose]
python -m benchmarks.score http://127.0.0.1:8765/jobs/<id> --case=sleep_loop

# Run all cases through /plan + score
python -m benchmarks.run_all                                # default sequential
python -m benchmarks.run_all --parallel 3                   # parallel (rate-limit risk)
python -m benchmarks.run_all --with-prose                   # enable LLM judge
python -m benchmarks.run_all --cases sleep_loop,hr_smb      # subset
python -m benchmarks.run_all --out /tmp/dash.json           # persist dashboard

# Standalone prose judge (no pipeline run, just score 4Ps from a saved job)
python -m benchmarks.prose_judge path/to/job.json [--no-llm]
```
