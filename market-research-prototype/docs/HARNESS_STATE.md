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

**CORRECTED 2026-07-29.** The first version of this section said the 15 never-called tools
"are the grounding tools" and that everything needed for a trade area was "present and
unreachable". **That was wrong**, and wrong in a way that pointed the remediation at the
wrong problem. The AST measure counted only calls by identifier, and the grounding tools are
invoked through `get_tool("name").fn(...)` string dispatch, which it could not see.

There are three different questions here, with three different answers:

| question | tools | skills |
|---|---|---|
| called by identifier anywhere (AST `ast.Call`) | 22/37 | 10/24 |
| has *any* call path, incl. `get_tool("name")` | **29**/37 | 10/24 |
| actually fired in 3 real end-to-end runs | **9**/37 | — |

The nine that actually ran, from the runs' own ledgers:

```
live    web_search x30   filter_aggregator_domains x17   fetch_page x15
        extract_prices x14   geocode_address x6   osm_named_competitors x4
        poi_competition x2
FAILED  acs_demographics x4   bls_cex_spend x2
```

So the grounding path is **wired and failing at runtime**, not unwired. `acs_demographics`
and `bls_cex_spend` are called on every relevant run and both fail — the first for a missing
Census key, the second because it cannot resolve a CEX series. That is a materially different
diagnosis, and a much cheaper fix, than "wire up the dead tools".

Genuinely never invoked in any live run (28), though many are legitimately conditional — the
Instagram and Meta tools need tokens, Trustpilot suits DTC brands, Google Trends suits
national ventures, so a hyperlocal coffee-shop run has no reason to touch them:

```
census_business_counts  census_land_area  enrich_competitor  enrich_competitors_batch
extract_structured  wayback_snapshot_url  fetch_via_wayback  wayback_activity
brand_trend_slope  google_trends_rising  trustpilot_reviews  trustpilot_momentum
reddit_mentions  hackernews_mentions  stackexchange_mentions  devto_mentions
lobsters_mentions  vertical_publication_mentions  instagram_handle_from_domain
instagram_profile  instagram_signal  meta_ad_library  rank_meta_advertisers
is_parked_domain  validate_domain  probe_domain_patterns  resolve_brand_domain
estimate_domain_age_days
```

`census_business_counts` and `census_land_area` are the two worth chasing: both have a call
path and neither fired, so something upstream skips them.

THE MEASURE ITSELF WAS BROKEN TOO. `test_one_orchestrator.py`'s reachability ratchet matched
a word-boundary-plus-paren regex over raw source, so `def geocode_address(` counted as a call:
it computed **37/37 and 24/24** and asserted `>= 22`, which can never fail. A guard against
decoration that was decoration. It now counts `ast.Call` nodes, and asserts an upper bound as
well, so "every tool reads as reachable" fails as a broken measure rather than passing as
good news.

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
3. **Fix the grounding tools' RUNTIME failures** (corrected: they are already wired and
   called — see section 2 — they fail when invoked): `acs_demographics` (ACS
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


---

# Status after the remediation pass (same day)

Suite: **2010 passed, 6 skipped, 6 warnings.** New gates D52, D53. All six items attempted;
one deliberately left partial, marked below.

| # | item | state |
|---|---|---|
| 1 | scale decision binds | **done** |
| 2 | no fabricated agency citation | **done** |
| 3 | grounding tools wired | **partial — Census needs a free key** |
| 4 | retire one orchestrator | **partial — deliberately; see below** |
| 5 | provenance entries + drift-guard | **done** |
| 6 | transcript every run | **done** |

## 1. The decision binds

Root cause was one regex. `extract_location("...opening in the Mission District of San
Francisco")` returned **None** — `_PLACE_RE` required a capitalised word straight after "in",
so the article in "in **the** Mission District" ended the match, and it chained localities on
commas so "District **of** San Francisco" had no continuation either. That single miss gated
BOTH the trade-area sizing and the OSM competitor roster.

After: `size_hyperlocal` runs (radius 1500m, catchment 7.07km²), and a physical venture whose
trade-area model did NOT run is now `publishable: False` with a note naming the skill, plus a
machine-readable `scale_skill_ran: False`. **D52 fails 9 of 17 stored reports** — every
physical venture in the corpus had published sizing its own classifier never produced.

Not geocoding neighbourhoods on purpose: measured, three phrasings of "the Mission" land up to
4km apart and only one is in the Mission. That would swap an LLM guess for a geocoder guess.

## 2. Fabricated citations

**14 of 15** agency-citing figures across corpus + live run had no origin proving a call.
D53 fails 8, passes 6 — and the 6 passes are exactly the honest ones
(`"LLM estimate (UNSOURCED — validate vs US Census ACS)"`). Failing those would have punished
the disclosure. Every figure now ships with a `data_origin`, defaulting to `"unattributed"`
rather than a silent absence or an invented `"llm"`.

## 3. Grounding tools — what actually works

Probed live, every tool called for real:

| tool | result |
|---|---|
| `geocode_address` | **works**, keyless |
| `poi_competition` | **works** — 197 cafes within 1500m |
| `osm_named_competitors` | **works** — 30 real Mission venues |
| `census_land_area` | **works** — SF County 120.913551 km² |
| `acs_demographics` | blocked — needs key |
| `census_business_counts` | blocked — needs key |
| `bls_cex_spend` | broken — series won't resolve |

**api.census.gov returns HTTP 200 with an HTML "Missing Key" page** for ACS, CBP *and* SUSB.
Because it is a 200, every caller reported "returned no data" — indistinguishable from "this
county has no data". Now raises `MissingApiKey` naming the env var and the free signup URL, and
`.env.example` documents it. (An earlier project note recorded SUSB as keyless in bulk;
re-measured, the *API* needs a key like the rest.)

The keyless half is wired and the improvement is large. Competitor roster, same venture:

    before (LLM recall):  Sightglass (SoMa), Andytown (Outer Sunset), Saint Frank (Russian
                          Hill), Linea Caffe, ... — 8 brands, 3 outside the trade area
    after  (OSM):         Four Barrel, Ritual, Philz, Muddy Waters, Angel Cafe & Deli,
                          Noe Cafe, ... — 30 venues, all in the catchment

Trade-area households stay `None` until a key exists — so the report now refuses to size rather
than publishing $31M citing Census.

## 4. Two orchestrators — partial, deliberately

Not done, and the reason is not fatigue. Measured: six production files reference the dead
layer's names and all ten skills are exercised by tests. Rewiring `run_plan` to call the
declared skills is a rewrite of the pipeline's spine — larger than the other five items
combined — and doing it in the same pass as five other fixes is how a cleanup ships a
regression.

What was done instead: **no section is attributed to inert code any more** (three still were —
Customer universe, Feature importance, Personas), and the registered-vs-reachable gap is
ratcheted as a number so the duplication cannot quietly grow. `dispatch.py`'s docstring claimed
"this is the seam the deterministic pipeline (`plan.py`) calls" — it is not; corrected, with a
test that fails if it drifts back.

## 5. Provenance

**22/22** entries now resolve to code that runs. The hole that let 8 drift: the guards split
the table by `kind`, so a skill-entry was validated against `SKILL_REGISTRY` — where
`four_ps_skill` genuinely is — and never against the module it claimed. Registry membership was
satisfied and the attribution was still wrong. The new guard is file-based, because
`import_module("profile")` resolves to the **stdlib profiler** and nearly hid this defect while
I was measuring it.

## 6. Transcripts

`attach`/`detach` moved into `persistence.transcript`, shared by both entrypoints. Idempotent,
and reclaims only a stale `direct-` sink — `run_plan` has many early returns, so one `finally`
cannot cover them; self-healing beats silently unrecorded.

## Still open

D22's regex; the 16 audit highs; full orchestrator consolidation (item 4); `bls_cex_spend`
series resolution; and everything gated behind `CENSUS_API_KEY` — which is free, and is now the
single highest-leverage thing a human can do for report quality.
