# 03 — Bugs the Benchmark Surfaced

A catalog of real pipeline bugs the benchmark caught — most of which would have shipped silently without it. This is the strongest argument for keeping (and expanding) the bench.

Each entry: bug, how it was hidden, how the bench caught it, and how we fixed it.

---

## 1. Named competitors silently dropped

**Symptom**: Sleep Loop venture description explicitly named 5 competitors (Calm Business, Headspace for Work, Lyra Health, BetterUp, Big Health). Pipeline only surfaced 1 in cluster output.

**How it was hidden**: rendered report still showed 6+ competitors total — they came from `discover()` LLM-generated queries, not from the named-list. The user reading the report had no way to know the named ones were missing because *other* names filled the slot.

**How bench caught it**: `competitor_recall: 1/5` (20/100) on cycle 28 v1.

**Fix**: extract `named_competitors` from venture description in `discover.py`; force them into candidate set with high opportunity score so they survive filtering.

**Cycle**: 29.

---

## 2. ICP loses input venture's stated employee band

**Symptom**: Sleep Loop description says "200-2000 employee companies"; pipeline ICP says "1,000-10,000 employees" (drift to bigger band the LLM thinks looks more "enterprise-y").

**How it was hidden**: ICP narrative read fluently. Operator wouldn't notice band mismatch unless they cross-referenced.

**How bench caught it**: `icp_alignment: 50/100` (band ✗) on cycle 28 v1.

**Fix**: extract `target_employee_band` from venture description; prepend to ICP prompt as authoritative; post-LLM hard-override if drift detected.

**Cycle**: 29.

---

## 3. Buyer role consistently null

**Symptom**: across all 3 cases (Sleep Loop, TraceFlow, Workhive), `customer_universe.icp_details.buyer_role` was `None`.

**How it was hidden**: ICP summary text described WHO the customer was (the firm/industry), so the report still read coherently. The buyer role field just didn't render anywhere visible.

**How bench caught it**: `icp_alignment: 50/100` (buyer-role ✗) across ALL cases — pattern, not noise.

**Fix**: ICP prompt now requires `buyer_role` as job title (e.g. "VP Engineering", "Head of Benefits"), with explicit examples + a heuristic backstop (`_derive_buyer_role_heuristic`) that pattern-matches venture text against known categories.

**Cycle**: 30.

---

## 4. Viability silently skipped under load

**Symptom**: in 3-way parallel benchmark runs, sleep_loop pipeline reported "complete" but `_steps_completed` was missing `viability`. Report rendered without a viability score section.

**How it was hidden**: pipeline's job state was "complete" not "error". Front-end had no banner indicating missing steps.

**How bench caught it**: `method_depth: viability_score=False`. Result: 87/100 instead of 100/100. Detail line: `"⚠ missing critical steps: ['viability']"`.

**Fix**: viability now retries once with 180s timeout (was 90s, no retry); validation gate raises explicit "Viability step skipped" flag if both attempts fail.

**Cycle**: 30 → bench v3.

---

## 5. Pipeline lies about confidence

**Symptom**: pipeline returns `validation: {flags: [], confidence_score: 1.0}` (100% confident) on a thin run with degraded steps, missing personas, single-segment data.

**How it was hidden**: report header showed "Pipeline confidence 100%". Operator reading the report would trust it absolutely.

**How bench caught it**: `validation_honesty: 20/100` (banded penalty: "0 flags + 100% confidence — pipeline may be over-reporting").

**Fix** (cycle 30):
- Audience confidence threshold tightened (was <0.5; now flags <0.7 too)
- 5 new flag types added: <3 voice sources with data, partial segment defaults, <3 TAM methods, viability skipped, 0 differentiators
- Validation gate now runs at end-of-pipeline so viability/segment flags actually surface (was running early, before viability ran)

**Result**: validation_honesty went from 20 → 100 across all 3 cases in v3.

---

## 6. Citations include fabricated sources

**Symptom**: 4Ps prose cited "HR Leader Feedback Interviews (N=20)", "LinkedIn Campaign Performance Report (Pilot, Oct-Nov 2023)", "Competitor Channel Analysis (Q3 2023)" — all fabricated by the LLM.

**How it was hidden**: citations rendered with proper formatting. Reader assumed they were real.

**How bench caught it**: `citation_grounding: 62/100` (3 of 13 flagged as suspicious based on fabricated-pattern regex).

**Fix**: 4Ps prompt now explicitly bans citation fabrication, lists known-fab patterns ("(N=20)", "(Pilot, [month])", "(Q3 2023)"), and requires citations to come from the provided evidence pool.

**Cycle**: 25-26.

---

## 7. Citation scorer over-penalized real sources with fab dates

**Symptom**: legitimate citation "Customer Voice Analysis (Q4 2023)" was getting flagged as suspicious. Real artifact, but the LLM stamped a fabricated quarter date on it.

**How it was hidden**: lost behind a single number (citation_grounding 62 on a run that mostly had real citations).

**How bench caught it**: introspecting the per-citation breakdown showed the false positive.

**Fix**: split scorer into two patterns:
- `FAB_SOURCE_TOKENS` — fully fabricated source patterns (-25 pts each)
- `FAB_DATE_REGEX` — fabricated dates on real sources (-5 pts each)

Plus 4Ps prompt now bans date-stamping real artifacts.

**Cycle**: 30.

---

## 8. Segment scoring silently defaulting to 0.5

**Symptom**: LLM segment scorer was returning `"scores": {}` empty dict. Pipeline default-fallback was filling all 5 metrics with 0.5 — which gave a final_weighted_score of 0.5 (looks plausible) but was 100% defaulted.

**How it was hidden**: report rendered a "Segment Prioritization" section with real-looking 0.5 / 0.5 / 0.5 / 0.5 / 0.5 / 0.50 scores. Without per-row authenticity tracking, no one could tell.

**How bench caught it**: `segment_authenticity: 0/100` flagged via `_scores_were_defaulted=True` on the segment object.

**Fix**:
- Flat-format prompt (one key per metric, no nested `{score, reasoning}` objects) — partially recovered scoring
- 0.5 fallback flagged with `_scores_were_defaulted: true` so the bench can dock points
- Report template now shows "⚠ LLM declined to score — values defaulted to 0.5 (median). Treat as directional placeholder."

**Cycle**: 27-28.

---

## 9. TAM only fills `method_top_down` despite 3-method prompt

**Symptom**: prompt asked for top-down + bottom-up + analog. LLM consistently returned only top-down, leaving the other two `null`. Even with retry, the LLM would still only return top-down.

**How it was hidden**: report TAM Triangulation table showed only one row. Operator might assume the others "weren't applicable".

**How bench caught it**: `method_depth: 87/100` (only 1/3 TAM methods filled; 20% × 33% = 6.7 lost).

**Fix** (cycle 30): split into 3 PARALLEL single-method calls (`tam_top_down`, `tam_bottom_up`, `tam_analog`) — forces commitment to each method independently. Per-method retry on miss.

**Result**: `method_depth` went from 87 → 100 reliably on TraceFlow + Workhive in v5.

---

## 10. TAM `value_usd` returned as dict instead of scalar

**Symptom**: bottom-up TAM call returned `value_usd: {"min": 2016000000, "max": 6300000000}` instead of a scalar number. TAM assembly choked silently and produced no headline TAM.

**How it was hidden**: only visible in the JSON; rendered report just had "—" in TAM cards.

**How bench caught it**: `tam_accuracy: 0/100` (TraceFlow v4 dropped from 100 → 0).

**Fix**: `_coerce_value_usd` helper handles dict `{min, max}` (takes midpoint), string `"1.5B"` (parses), and numeric inputs.

**Cycle**: 30 → bench v5.

---

## 11. Reddit common-noun brand matches noise

**Symptom**: brand "Rest Space" (sleep coaching) was matching r/space, r/40kLore, r/EscapefromTarkov, r/CozyPlaces — totally off-topic threads about literal cosmos / video games / interior design.

**How it was hidden**: Reddit signal section showed comments that looked plausible at first glance ("Vastness of space causes immense anxiety" — sounds related to sleep but is from r/space).

**How bench caught it**: didn't directly — caught by user reading the rendered report. Bench has `source_breadth` but doesn't currently score signal:noise ratio.

**Fix** (cycle 22-25):
- Multi-word brand requires both words within 60-char window
- Common-noun brands require capitalized phrase match in original text
- Subreddit blocklist (gaming, sci-fi, celebrity gossip, askreddit, etc.)

**Lesson**: not every bug needs to be scorable. The user reading the rendered report still catches issues the bench misses.

---

## 12. Place prose is uniformly the weakest section

**Symptom**: across all 3 cases, `place` prose scored 47-62/100 vs ~70+ for the other 3 sections.

**How it was hidden**: each individual report's place section read OK in isolation. Only when comparing across cases did the pattern emerge.

**How bench caught it**: prose_judge per-section breakdown shows `place 47/100` consistently.

**Fix** (cycle 30): rewrote place prompt:
- Imperative-verb opener mandate
- Named-channel mandate (Mercer/Aon/SHRM, not "B2B partnerships")
- Metric-per-paragraph mandate ("6 broker meetings/quarter", "$0.30 CPM")
- Buzzword blocklist (synergies, leverage, holistic, best-in-class, ...)

**Result**: hr_smb place 71 → 75. TraceFlow + Sleep Loop unchanged (input data too thin for specific actions).

---

## 13. ICP truncation mid-word

**Symptom**: `icp_summary` ended mid-word: `"Mid-market organizations seeking to improve productivity, reduce burnout, and enhance"` — sentence cut off.

**How it was hidden**: looked like a long-but-complete sentence at first glance.

**How bench caught it**: didn't directly — user-reported, but the pattern of "ICP completeness" could be a future bench dimension.

**Fix** (cycle 19-20): JSON-field reordering — `icp_summary` is now LAST in the schema so truncation only kills it; categorical fields (industry, company_size_employees, buyer_role) come FIRST. Plus a fallback that derives a synthetic icp_summary from the categorical fields if truncated.

---

## 14. Hr_smb timeout mid-bench

**Symptom**: bench v2 timed out polling hr_smb at 1500s (25min). Job was still running but orchestrator gave up.

**How it was hidden**: orchestrator just raised `TimeoutError` and crashed the bench script.

**How bench caught it**: directly — log showed "Job dfd541fe... did not complete within 1500s".

**Fix**: orchestrator poll timeout 1500 → 1800s.

**Cycle**: 30 → bench v4.

---

## 15. Sleep_loop runs ~50% faster than other cases (silent step skipping)

**Symptom**: Sleep Loop completed in 94-148s while TraceFlow / Workhive took 228-275s. The shorter run was missing several steps (clustering, personas, segment_ranking).

**How it was hidden**: pipeline reported "complete" with no warning about skipped steps.

**How bench caught it**: `competitor_recall: 0/100`, `personas: 0/100`, `segment_authenticity: 0/100` for sleep_loop in v3/v4/v5 — pattern across 3 runs.

**Fix**: ⏳ pending. Root cause likely: cumulative LLM rate-limit pressure → step timeouts → silent return of `{"error": "timed out"}` → step not appended to `_steps_completed`. Need a "degraded run" banner at top of report when critical steps were skipped.

**Cycle**: identified in 30, fix not yet shipped.

---

## Summary of bug detection by dimension

```
Dimension              # bugs caught   Highest impact
──────────────────────────────────────────────────────────────────────
competitor_recall            2          named-competitor seeding
icp_alignment                3          buyer_role + band drift + null buyer
method_depth                 3          TAM 1/3 methods, viability skip, mid-pipeline drops
validation_honesty           1          pipeline confidence lying
citation_grounding           2          fab sources + fab dates
segment_authenticity         1          0.5 default cover-up
tam_accuracy                 2          dict-shaped value_usd, scope drift
prose_quality                1          place prose plateau
source_breadth               1          (no bugs, but caught regressions)
```

The four highest-leverage dimensions (those that caught the most bugs) — competitor_recall, icp_alignment, method_depth, validation_honesty — collectively account for **9 of the 15 documented bugs**. If the bench rubric had been only these 4 dimensions, the system still would have improved meaningfully.
