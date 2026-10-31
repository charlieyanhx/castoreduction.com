# 03 — Prose Judge

The `prose_quality` dimension scores the 4Ps narrative prose against the structural traits of professional consulting writeups (McKinsey/BCG/Bain/Deloitte/PwC public insights).

Implementation: `benchmarks/prose_judge.py`.

## Why no verbatim consulting prose?

Including real McKinsey/BCG passages in the repo would be a copyright/redistribution issue. So instead the rubric encodes the **structural traits** those firms publish in their style guides + research methodology pages:

- ≥1 citation per ~75 words
- ≥1 specific number per ~50 words
- ≥1 named entity (brand/person/place) per ~30 words
- imperative-verb opener for recommendations
- explicit hedging on data gaps
- short sentences, parallel structure
- zero buzzword padding ("synergies", "leverage", "holistic", "best-in-class")

The judge scores prose against these traits. Mixed deterministic + LLM.

## Six sub-traits per section

Each of the 4 sections (product / price / place / promotion) is scored 0-100 across 6 traits:

```
Trait                   Weight   Type
─────────────────────────────────────────────
specificity              25%     deterministic — numbers + proper-nouns per 100 words
citation_density         20%     deterministic — ¹² ³ markers per 100 words
no_buzzwords             15%     deterministic — penalty for buzzword regex hits
action_orientation       15%     LLM judge
hedging_discipline       10%     LLM judge
executive_readability    15%     LLM judge
─────────────────────────────────────────────
                        100%
```

Section score = weighted sum. Overall `prose_quality` = mean across the 4 sections.

## Deterministic traits (the cheap, fast layer)

### `specificity`

```python
nums_per_100w  = numbers / words × 100
nouns_per_100w = proper_nouns / words × 100

# Targets: 2 numbers/100w → full credit; 4 proper-nouns/100w → full credit
n_score = min(100, max(0, (nums_per_100w - 0.5) / (2 - 0.5) × 100))
p_score = min(100, max(0, (nouns_per_100w - 1.0) / (4 - 1.0) × 100))
specificity = (n_score + p_score) / 2
```

Patterns:
- `_NUMBER_RE = r"\$?\b\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|[KMB]?\b|million|billion)?"`
- `_BRAND_RE = r"\b[A-Z][a-zA-Z]+(?:\.[a-z]+)?\b"`

### `citation_density`

```python
# 1.3 citations per 100 words → full credit (≈ 1 per 75 words)
score = min(100, max(0, cites_per_100w / 1.3 × 100))
```

Pattern: `_CITATION_RE = r"[¹²³⁴⁵⁶⁷⁸⁹⁰]"`

### `no_buzzwords` (penalty)

```python
# 0/100w → 100; 0.5/100w → 50; ≥1/100w → 0
score = max(0, 100 - bw_per_100w × 100)
```

Pattern:
```python
_BUZZWORD_RE = r"\b(synergies|leverage(?:s|d|ing)?|paradigm|holistic|robust|
                    best-in-class|unlock value|streamline|cutting[- ]edge|
                    world-class|transformational journey|proactively)\b"
```

## LLM-judged traits (the expensive, qualitative layer)

The LLM gets prompted as **a senior consulting partner reviewing a junior associate's draft**:

```
You are a senior partner at a tier-1 consulting firm (McKinsey/BCG/Bain/Deloitte)
reviewing a junior associate's draft. Your reviews are blunt, specific, and grounded
in the public style standards your firm enforces. Return only JSON.
```

Then for each section it returns:

```json
{
  "action_orientation_score": 0-100,
  "action_orientation_reasoning": "1 sentence — quote BEST and WORST line",
  "hedging_discipline_score": 0-100,
  "hedging_discipline_reasoning": "1 sentence",
  "executive_readability_score": 0-100,
  "executive_readability_reasoning": "1 sentence",
  "blunt_partner_takeaway": "≤25 words — what would your senior partner say?"
}
```

### Trait definitions

| Trait | What "100" means | What "0" means |
|---|---|---|
| **action_orientation** | Every paragraph ends with a concrete action verb (do X, shift to Y, stop Z) | Pure description, no recommendations |
| **hedging_discipline** | Flags ≥1 data gap explicitly AND still commits to a directional view | Either fake conviction with no caveats OR endless qualifiers without a recommendation |
| **executive_readability** | A CFO could skim and act on it in 30 seconds | MBA word salad |

### "Blunt partner takeaway"

The judge returns a one-liner that surfaces in the benchmark output. Examples seen in the wild:

- *"Too much description, not enough action. I want to see concrete actions and associated KPIs."*
- *"This is corporate poetry, not consulting. Zero action, zero insight, pure buzzwords. Redraft immediately."*
- *"Too much marketing fluff. Focus on specific features and quantifiable benefits."*
- *"This is a brainstorm, not analysis. You've generated ideas, now prove they're good with [data]."*

## Failure modes

### LLM truncates output

`max_tokens=1200` is sized for the 6-key JSON. If the LLM truncates, `json_repair` salvages partial output and missing keys default to 50. A `_parse_error` flag is set so the operator can spot it.

### LLM rate-limited

The judge uses the same `llm.call_json` chain (Gemini → Groq → Anthropic). If all 3 are rate-limited, the judge returns a 50-default for the LLM traits and only deterministic traits contribute.

### Section too short to evaluate

If a section's narrative is <100 chars, the judge skips it and returns:
```json
{"_skipped": true, "blunt_partner_takeaway": "(prose too short to evaluate)"}
```
LLM traits default to 50 in this case.

## Calibration validation

Synthetic test in cycle 25 showed the judge correctly:
- Gave `place` (10 buzzwords, 0 numbers, 0 citations) **32/100**
- Gave `price` (10 numbers, 3 citations, 0 buzzwords) **80/100**
- Gave a 5-word promotion ("Sleep Loop should sell better") **48/100** (no buzzwords but no specifics either)

Reproduce:
```bash
python -m benchmarks.prose_judge path/to/job.json [--no-llm]
```

## What the judge does NOT do

- **No comparison against real consulting prose** — see "Why no verbatim consulting prose?" above
- **No multi-judge agreement** — single LLM call per section. A robust setup would average 3 judges from different models
- **No human alignment study** — we haven't measured how well the judge correlates with human consultant ratings yet
- **No section-level fine-grained feedback to the LLM writer** — the partner takeaway is for the operator, not fed back into the next prose-generation iteration

## Future work

1. **Multi-judge majority** — fire 3 LLM calls (Anthropic + Groq + Gemini) and take median per trait
2. **Human alignment** — have a consultant rate 5 of our outputs blindly against 5 real consulting outputs, compare to LLM judge scores
3. **Iterative re-prompting** — when partner takeaway flags an issue, automatically regenerate the section with the takeaway as steering
4. **Verbatim quote permission** — get permission to include short quoted passages from real reports (≤15 words) so the judge has concrete reference material
