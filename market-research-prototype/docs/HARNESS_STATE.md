# Harness state — measured, 2026-07-28

Every number below came from a measurement, not a reading of the code. The live run is
`out/live/run1.json` / `.html`: an independent specialty coffee shop in the Mission District,
San Francisco, run through `plan.run_plan` end to end in **342s**, producing 27 top-level
keys and a 104,203-byte report.

## The one-sentence finding

**The harness has the right parts, declared correctly, and the orchestrator bypasses them.
The tools that would ground a number are precisely the ones that never run — and the LLM
fills the gap while citing those tools by name.**

## 1. The declared skill layer is dead

`skills/pipeline_steps.py` — 358 lines, 11 registered skills, each with a real
`@skill(produces=…, consumes=…)` dependency declaration:

```
profile_skill  taste_skill  customer_universe_skill  differentiators_skill  personas_skill
max_diff_skill  psm_skill  market_sizing_skill  four_ps_skill  viability_skill
```

**Imported by nothing outside tests.** Measured: every production reference to
`pipeline_steps` is a test file. The composable layer that makes this a harness — the
produces/consumes graph a scheduler could walk — is inert.

`run_plan` reimplements all of it inline: **989 lines, 28 `try/except` blocks, 71 `if`
statements, 29 step-bookkeeping calls.** That is the answer to "why is our orchestrator so
long compared to Claude Code or OpenHands": not because the domain is harder, but because
the declarative layer exists and is unused, so every step is hand-wired twice.

## 2. Registered vs actually reachable

| | registered | called anywhere in production code |
|---|---|---|
| tools | 37 | **22** |
| skills | 24 | **10** |

The 15 never-called tools are not a random sample. They are the grounding tools:

```
acs_demographics        census_business_counts   bls_cex_spend      census_land_area
osm_named_competitors   poi_competition          geocode_address    enrich_competitor
enrich_competitors_batch  extract_structured     wayback_snapshot_url
devto_mentions  lobsters_mentions  stackexchange_mentions  vertical_publication_mentions
```

Everything needed to compute a trade area from real data is present and unreachable.

## 3. What that produced on the live run

The scale classifier got it right and was then ignored:

```json
{"scale": "hyperlocal", "sizing_method": "trade_area_catchment",
 "sizing_skill": "size_hyperlocal", "rationale": "physical premise serving a local trade area"}
```

`size_hyperlocal` **never ran**. `_steps_completed` contains 15 steps and not one is a
sizing step — no `market_sizing`, no `hyperlocal`, no `geo`. Yet `result["market_sizing"]`
exists, with a TAM, three figures, and a `publishable` flag.

Every sizing number is LLM-narrated:

| method | value | stated source |
|---|---|---|
| top_down | $11,520,000 | "Statista US Specialty Coffee Report 2023" |
| bottom_up | $31,050,000 | **"Census ACS Mission District demographics & BLS QCEW NAICS 722515"** |
| analog | **$2,000,000,000** | "Blue Bottle Coffee press reports (2017)" |

Two things to sit with:

**The bottom-up figure cites Census ACS and BLS QCEW.** Measured: zero Census/BLS/ACS/OSM
calls this run, no transcript written at all, `CENSUS_API_KEY` unset, and
`data_origin: None`, `count_origin: None` — the very fields that exist to record where a
number came from are empty. So the LLM did not merely substitute for the script; it
**claimed the script's authority** for its own guess, and the origin fields that would have
exposed that were left blank.

**The analog method published a $2.0B TAM for one coffee shop** — 64× its own bottom-up —
and 35 of 51 gates passed the report.

## 4. The geo path also didn't fire

`D07` failed: `geo_sourced=None`. For the archetypal hyperlocal venture, the OSM
nearby-venue roster never ran. The competitor set is 8 LLM-recalled brand names (Sightglass,
Ritual, Linea Caffe — real SF shops, recalled from training, not discovered).

Consequence for the fix shipped in `2866d72`: `D49` is still `n/a` on this run. Not because
the payload mapping is wrong — that fix is correct — but because **the step that would
populate it never executes.** The gate is reachable in code and unreachable in practice.

Verified working on this run: the roster's three numbers are now consistent —
`opportunity_score` `[16, 16, 16, 10, 0, 0, 0, 0]`, published
`avg_opportunity_score` **7.2**, which is exactly their mean.

## 5. Provenance: 8 of 22 section attributions are broken

Checked against the repo files (not via `import_module`, which silently resolves `profile`
to the stdlib profiler):

```
Company profile        profile.profile_skill                  [module file does not exist]
Differentiators        differentiators.differentiators_skill
Competitive landscape  discover.discover_competitors_skill
Decoded audiences      taste.taste_skill
Pricing (PSM)          pricing.psm_skill
Market size            skills.sizing.dispatch.market_sizing_skill
4Ps                    four_ps.four_ps_skill
Viability              four_ps.viability_skill
```

Every one names a `*_skill` that lives **only** in the dead `skills/pipeline_steps.py`, while
crediting a live module that does not define it. So the eight most important sections of the
report are attributed to functions that do not exist where claimed — and the functions that
do exist never run. The earlier drift-guard did not catch these.

## 6. Gate and verifier state

Live run, all 51 gates: **35 pass, 1 fail (D07), 15 n/a.**
In-run verifier: **36 answered, 15 n/a, publishable=False, 3 blocking** — D07 plus two
dangling citations in the price section ("citation marker resolves to nothing — the footnote
looks sourced and is not").

The verifier now reports coverage, so the 15 n/a are visible rather than silent. Before
`2866d72` it ran with `html=None` and 10 of those were structurally unanswerable while the
report still read as verified.

Also: a direct `run_plan` call writes **no transcript**. The ledger only attaches inside the
job system, so any CLI or script run has no provenance record at all.

## 7. What "a good harness" means here, in order

The ordering is by whether a wrong number can still reach a reader.

1. **Make the scale decision binding.** `sizing_skill: size_hyperlocal` must dispatch, or the
   run must fail loudly. A classifier whose verdict is advisory is decoration. This single
   change is what makes D49 reachable in practice and kills the LLM sizing path for local
   ventures.
2. **`data_origin` must be mandatory, and a figure may not cite a source no tool produced.**
   The fabricated Census/BLS attribution is the worst defect found in this codebase — it is
   worse than a wrong number, because it defeats the reader's ability to check. A gate should
   fail any figure whose stated source names a data provider with no corresponding ledger
   event.
3. **Wire the grounding tools that already exist**: `acs_demographics` (ACS
   `median_hh_income` is already fetched and read by nothing), `census_business_counts`
   (SUSB is keyless in bulk — measured earlier: CA independent coffee shops $411,543/yr
   actual vs the LLM's $280k–$2.4M range), `census_land_area`, `poi_competition` /
   `osm_named_competitors` for the geo roster, `bls_cex_spend` for household spend.
4. **Retire one of the two orchestrators.** Either `run_plan` calls the declared skills, or
   `pipeline_steps.py` is deleted. Two implementations of the same pipeline, one of them
   inert, is how the provenance map came to name functions that never run.
5. **Fix the 8 provenance entries and extend the drift-guard** to check `produced_by`
   against the named module's own file, so this class cannot return.
6. **Transcript every run**, not only job-system runs.

Items 1–3 are the "stop using the LLM where a script exists" work. Item 4 is what makes the
orchestrator small. Items 5–6 are what make the answer to "which script produced this
sentence" trustworthy rather than approximately right.
