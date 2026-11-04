# 01 — Cycle Log

A chronological record of pipeline iterations. Each cycle = one full pipeline run on the test venture (Sleep Loop, then later TraceFlow + Workhive) → analyze the rendered report → fix the issues → re-run.

This log starts at cycle 14 (where the dedicated benchmark conversation begins) and runs through cycle 30 + benchmark v5 (current).

## Cycles 14-25: prompt-engineering era

Before the benchmark existed, iteration was pure prompt-engineering against subjective UI quality. Each cycle the user would walk through the rendered HTML report and call out things that looked wrong.

| Cycle | Headline focus | Key fixes |
|---|---|---|
| 14 | Initial pipeline working but flaky | timeout handling, basic retry |
| 15-17 | LLM truncation cascade fixes | JSON-field reordering (small/required first, longest narrative LAST so truncation kills only optional content) |
| 18 | Persona field completeness | backstop synthesis when LLM omits required fields |
| 19-20 | TAM 3-methods + segmentation rigor | post-process math reconciliation, share_pct normalization |
| 21 | TAM range-string coercion | `growth_cagr_pct: "18-28"` → numeric midpoint 23 |
| 22 | Reddit common-noun filtering | "Rest Space" was matching r/space, r/40kLore — added per-word capitalization check |
| 23-24 | Customer voice multi-source | added HackerNews (Algolia public API), Stack Exchange, DEV.to, Lobsters as additional taste sources |
| 25 | Anti-hallucinated citations | banned fabricated "(N=20)" sample sizes and "(Q3 2023)" date stamps in 4Ps prompt |
| 26-27 | Segment scoring rescue | flat-format prompt (one key per metric, no nested objects) + 0.5 default fallback when LLM still refuses |
| 28 | Customer voice rendered as 5 stacked sub-sections | template now shows Reddit / HN / SO / DEV.to / Lobsters with brand-colored accents |

## Cycle 28-29: benchmark v1 (the era of false positives)

By cycle 28 the pipeline scored "100/100" on a 7-dimension single-case benchmark. That looked great until cycle 29 when we tried to add more cases and dimensions.

**Cycle 28 — bench v1 baseline**
- 1 case (Sleep Loop), 7 dimensions
- Score: 100/100 ("perfect")
- Hidden bugs (not measured): TAM only had method_top_down filled; segment scoring returning empty; place prose was buzzword-heavy

**Cycle 29 — bench v1.5 (3 cases parallel)**
- Added 2 cases (TraceFlow APM, Workhive HRIS)
- Ran 3 in parallel, hit Gemini rate-limits hard
- sleep_loop **degraded to 70.2/C**: competitor_recall=0 (clustering rate-limited out), personas+place silently skipped
- Surfaced first real reliability bug: pipeline drops steps under cumulative load, no warning surfaced
- Real fixes shipped:
  1. Named-competitor seeding — extract `named_competitors` from venture description, force into discovery
  2. ICP band override — extract `target_employee_band`, prepend to ICP prompt + post-LLM hard-override

**Cycle 30 — bench v3 (16 dimensions, sequential)**

Score table:

```
                v1 (8-dim)    v2 (8-dim)    v3 (16-dim)
TraceFlow       N/A           88.7/B         92.2/A
Workhive        N/A           timed out      82.6/B
Sleep Loop      100/A          70.2/C        64.4/D
                              (parallel)    (sequential)
```

Going from 8 → 16 dimensions made the rubric expose 3 hidden bugs that the simpler version was missing:

1. **viability silently skipped under load** — pipeline reported "complete" but skipped step 16; benchmark caught it via `method_depth.viability=False`
2. **`validation_honesty: 20`** — pipeline returns "100% confidence, 0 flags" even on thin runs (over-reporting)
3. **`citation_grounding: 73`** — citations like "Customer Voice Analysis (Q4 2023)" were getting flagged because the date was fabricated even though the source was real

Three fixes shipped:

| Fix | Implementation |
|---|---|
| Viability retry | First call 90s timeout; on error, retry once with 180s timeout; surface as validation flag if both fail |
| Citation scorer split | Distinguish fabricated-source (-25 pts each) from fabricated-date-on-real-source (-5 pts each); 4Ps prompt now bans date-stamping real artifacts |
| Stricter validation gate | Re-runs at end-of-pipeline; 5 new flag types (low source breadth, partial segment defaults, <3 TAM methods, viability missing, 0 differentiators); audience confidence threshold tightened from <0.5 to <0.7 |

## Bench v4 (3 more fixes shipped)

Three more fixes triggered by remaining v3 gaps:

| Fix | Why | Implementation |
|---|---|---|
| Place prose 47 → 60+ | 4P "place" was the weakest section in every case (47/61/50 across cases) | Rewrote prompt: imperative-verb opener, named channels (Mercer/Aon/SHRM), metric-per-paragraph mandate, buzzword blocklist (synergies/leverage/holistic/best-in-class/streamline/cutting-edge/...) |
| TAM 1-2/3 methods → 3/3 | Even with retry, LLM regularly only filled `method_top_down` | Split into 3 PARALLEL single-method calls (`tam_top_down`, `tam_bottom_up`, `tam_analog`); TAM block assembled from method values; per-method retry on miss |
| hr_smb timeout | Sequential bench polling timed out at 1500s | Bumped orchestrator poll timeout to 1800s |

**Bench v4 surfaced a NEW bug in v5**: TraceFlow TAM came back as 0 because `method_bottom_up.value_usd` was returned as a dict `{"min": 2.0B, "max": 6.3B}` instead of a scalar.

## Bench v5 (value_usd coerce)

One more fix: `_coerce_value_usd` helper handles the dict shape `{min, max}` (takes midpoint), string `"1.5B"`, and numeric inputs.

| Case | v3 | v4 | v5 | Δ |
|---|---|---|---|---|
| TraceFlow APM | 92.2/A | 77.3/C | **91.0/A** | TAM coerce fix recovered the regression |
| Workhive HRIS | 82.6/B | 87.2/B | 85.2/B | stable B; differentiators dropped (LLM stochastic) |
| Sleep Loop | 64.4/D | 66.8/D | 65.7/D | persistently degraded under load — root cause not yet fixed |

## Bugs that remain

Per the v5 log:

1. **sleep_loop runs ~half the time of the other 2 cases** (94-148s vs 228-275s). Many steps silently skipped. Root cause unfixed.
2. **`prose_quality.place` plateaus at 60-65** across all cases. Stricter prompt helped hr_smb (+4) but didn't move TraceFlow or sleep_loop. Likely the input data is too thin for specific actions.
3. **LLM stochasticity is now the dominant variance** — same input gives ±10 score swings on `differentiators` and `personas` between runs.

## What changed in the rubric (v1 → v5)

```
v1 (cycle 28):
  coverage  tam_acc  cagr_acc  competitor_recall  icp_align  method_depth  source_breadth
  → 7 dimensions, single case, 100/100 ceiling

v2 (cycle 28b):
  + prose_quality (LLM judge, 17%)
  → 8 dimensions, single case

v3 (cycle 30):
  + differentiators
  + personas
  + pricing_psm
  + unit_economics
  + segment_authenticity
  + citation_grounding
  + validation_honesty
  + growth_scenarios
  → 16 dimensions, 3 cases

v4-v5: same 16 dimensions, fixes to scorer paths + 3 more pipeline fixes
```

## Highest-leverage cycle

**Cycle 30 → bench v3** — going from 8 → 16 dimensions is what made the benchmark useful. Before v3 the pipeline scored 100/100 and looked perfect; v3 dropped scores into the 60-95 range and exposed concrete bugs. The rubric expansion was higher-leverage than any single prompt fix.

**Lesson**: when your benchmark saturates at 100/100, the bench is broken, not the system.
