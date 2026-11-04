# 02 — Architectural & Rubric Decisions

A log of design choices made during cycles 14-30, with the rationale for each. Read this before changing one of these — the choice was usually deliberate and the alternatives were considered.

## Pipeline architecture

### Why split the 4Ps into 4 parallel LLM calls instead of one giant prompt?

**Decision**: `four_ps.assemble_4ps_split` runs product / price / place / promotion as 4 parallel calls, each with only the context it needs.

**Rationale**:
- Single-prompt 4Ps was hitting truncation regularly (~2500 token output)
- Each section has different context needs (product wants Max-Diff, price wants PSM, place wants channel scrapes, promotion wants Reddit themes)
- Parallel calls 4× faster wall-clock
- Per-section max_tokens tunable independently
- One section can fail without breaking the others

**Cost**: 4× LLM calls. Worth it.

### Why split TAM into 3 single-method calls?

**Decision** (cycle 30): `market_sizing` runs `tam_top_down`, `tam_bottom_up`, `tam_analog` as 3 separate parallel calls + `sam_seg` + `som` + `meta` (6 total).

**Rationale**:
- Single-prompt TAM with all 3 methods consistently filled only 1-2/3, even with retry
- Each method has very different reasoning (industry report vs firm-count math vs comparable ARR)
- Splitting forces the model to commit to each method independently
- Per-method retry is cheap (one method's failure doesn't trigger full TAM retry)

**Cost**: 3× LLM calls instead of 1. Cycle 30 v5 shows this gets `method_depth` to 100/100 reliably.

### Why JSON-field reordering (short fields first, narrative last)?

**Decision**: every prompt with multiple required fields places short categorical fields FIRST, longest narrative LAST.

**Rationale**:
- LLM truncation kills the END of output
- If we put narrative first, we lose all required fields when truncated
- If we put narrative last, we lose only the prose — required fields survive
- Documented inline in each prompt: "Place this LAST so truncation only kills it"

**Cost**: The prose has to be defined *after* the structured fields, which is slightly less natural to read. Worth it.

### Why retry viability with longer timeout instead of shortening?

**Decision** (cycle 30): on viability fail, retry with 180s timeout (was 90s, no retry).

**Rationale**:
- Viability is critical (3% rubric weight, but high-impact for the report's narrative)
- Better to take +90s than silently skip
- The validation gate's "Viability step skipped" flag is honest but expensive (drops score 20+)
- 180s is enough to handle Gemini's occasional 30s+ tail latency

**Cost**: Worst-case run time +90s if first call fails.

### Why does the validation gate run twice?

**Decision**: `_validation_gate` runs early (after step 12) and again at end-of-pipeline.

**Rationale**:
- Early run flags problems with discovery / pricing / place
- Late run catches viability + segment + voice-source flags that didn't exist at the early run
- The merge takes the union of flags and the MIN confidence (more conservative)

**Cost**: Negligible — gate is pure Python, no LLM call.

## Benchmark design decisions

### Why exactly 16 dimensions?

**Decision**: 16 dimensions covering every major report component.

**Rationale**:
- 7 dimensions ceiling-saturated at 100/100 — bench was broken
- Each dimension should be able to fail the pipeline independently
- Each dimension corresponds to a visible report artifact (TAM table, persona cards, citations, etc.)
- 16 is enough to discriminate but few enough to fit in a printable dashboard

**Counter-considered**: a "scientific approach" with statistical sampling and IRR. Rejected because we have 3 cases and need actionable signal, not a research paper.

### Why use wide TAM bands (sometimes spanning 50× range)?

**Decision**: Sleep Loop reference band is `$500M-$25B`.

**Rationale**:
- Multiple defensible TAM scopings exist for the same venture (narrow ICP slice / mid-narrow segment / broad category)
- The bench shouldn't punish a pipeline that picks "narrow" over "broad" if both are coherent
- Real consulting reports also defend wide TAM ranges with explicit bracketing
- Anything inside the band gets 100; outside gets order-of-magnitude decay

**Counter-considered**: tight bands forcing a "correct" TAM. Rejected because there is no single correct TAM.

### Why include `validation_honesty` as its own dimension?

**Decision** (cycle 30): pipeline that says "100% confident, 0 flags" on thin data scores 20/100; pipeline that flags 5 issues with 60% confidence scores 100/100.

**Rationale**:
- The most insidious failure mode is "looks confident but isn't"
- A pipeline that lies about confidence is *worse* than a pipeline that admits weakness
- Without this dimension, the benchmark would reward over-reporters

**Counter-considered**: rolling honesty into `prose_quality`. Rejected because honesty about data quality is a structural property of the JSON output, not the prose.

### Why use LLM-as-judge for some prose traits but not others?

**Decision**: 3 deterministic + 3 LLM-judged traits per section.

**Rationale**:
- Specificity, citation density, buzzword density — countable via regex; deterministic
- Action orientation, hedging discipline, executive readability — require interpretation; LLM
- Mixing keeps the bench fast (deterministic traits run in <100ms total) while still capturing qualitative signal
- LLM-only would be expensive (~$0.05/case × 3 cases × every iteration)
- Deterministic-only would miss the "consultant feel" of prose

**Cost**: LLM judging adds ~3 LLM calls per case × 4 sections = 12 calls × ~$0.005 = ~$0.06/case.

### Why no verbatim consulting prose comparison?

**Decision**: judge against publicly-published *style traits* of consulting firms, not their actual prose.

**Rationale**:
- Including verbatim McKinsey/BCG passages would be a copyright/redistribution issue
- Style traits (≥1 number per 50 words, action-verb openers, etc.) are publicly documented in firm style guides
- An LLM trained on the open web has seen enough consulting prose to evaluate against the style implicitly
- Future: could license short quoted passages (≤15 words) for direct comparison

**Counter-considered**: scrape 100 free McKinsey insights and use them as embedding-similarity reference. Rejected: noisy, slow, copyright-grey.

### Why hand-curated reference whitelists for citation_grounding?

**Decision**: maintain a hard-coded set of "real artifact" tokens and "fabricated source" tokens.

**Rationale**:
- Pipeline outputs cite a small known set of artifacts (Max-Diff, PSM, Company Profile, Competitor Scrape, Reddit, etc.)
- Whitelist is short (~25 tokens) and stable
- Any new "Customer Voice" or "HR Leader Interviews (N=20)" pattern is suspicious by default
- Scoring is fast (substring match), debuggable, and updatable

**Counter-considered**: LLM-judged citation grounding. Rejected: too expensive and too fuzzy for a determination that should be deterministic.

### Why TAM key names still say `corporate_wellness`?

**Decision**: in `cases/*.json`, TAM band keys are `tam_us_corporate_wellness_usd_low/mid/high` even for non-wellness ventures (TraceFlow APM, Workhive HRIS).

**Rationale**:
- Sleep Loop was the original case; the keys baked in
- Renaming would require updating `score.py` to handle multiple key names
- Renaming would also require updating all 3 case files in lockstep
- The keys are opaque labels — `score.py` reads them by name without inspecting meaning

**Cost**: minor cosmetic confusion when reading a TraceFlow case. Documented in `04-test-cases.md`.

**Future**: when a 4th case is added, refactor to `tam_usd_low/mid/high` everywhere.

### Why CLV/CAC banded scoring instead of continuous?

**Decision**:
- 2-5 → 100 (healthy)
- 1-2 or 5-10 → 70 (marginal)
- >10 → 40 (implausibly high)
- <1 → 20 (broken)

**Rationale**:
- Real CLV/CAC has a "healthy plateau" — you're as good at 3:1 as 4:1
- A 0.1 difference at 3:1 vs 3.1:1 isn't meaningful
- Bands match how investors actually evaluate (top-of-band vs middle vs broken)
- >10:1 is a flag because it usually means CAC is wrong (under-counted)

### Why not deploy the bench to CI?

**Decision**: bench runs manually via `python -m benchmarks.run_all`.

**Rationale**:
- Each full bench takes ~15-20 min and ~50+ LLM calls per case × 3 cases = ~150 LLM calls
- Running on every commit would burn through Gemini free tier in a day
- LLM stochasticity (±10 score swings) means a failed CI bench wouldn't reliably indicate regression

**Future**: nightly bench run with averaged 3 runs per case, comparing to a moving baseline.

## Things we explicitly didn't do

- **No multi-judge majority voting**: single LLM call per section. A robust setup would average 3 judges from different models. Cost-benefit not justified yet.
- **No human-alignment study**: we haven't measured how well the LLM judge correlates with human consultant ratings. Plan to do this when we have a real consultant on-staff.
- **No statistical significance testing**: 1 run per case; LLM stochasticity ±10. To trust a single score statistically you'd want 5+ runs averaged.
- **No A/B harness**: we run the bench against the current pipeline, not against a held-out version. Could add `run_all --compare-to <commit>`.
- **No DSPy or prompt optimization**: prompts are hand-tuned. The bench provides the metric to optimize against, but we haven't wired it into a prompt-optimization loop yet.
