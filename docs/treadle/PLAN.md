# Treadle

Treadle is the report engine underneath Castor, carved out so it can run on its own and
serve other report types and other agent systems. One engine, six stages:

```
Elicit  ->  Plan  ->  Gather  ->  Derive & Judge  ->  Write  ->  Deliver & Iterate
(survey)   (DAG)    (agents)    (compute, gates)    (LLM)     (render, workshop)
```

A report type (a "pack") declares its sections, the inputs only a person can give, the
tools it may use, its domain invariants and its template. It never writes orchestration.
Castor's market research becomes the first pack, wrapped from the outside.

## The rule

**Treadle touches the frame and nothing else.** The product (market research, billing,
auth, quota, intake, pages, routes, `plan.py`) is not edited. Two witnesses hold this:

- `scripts/treadle_guard.py`: every file changed since `docs/treadle/BASE` is a frame
  module or a new Treadle path.
- `scripts/treadle_baseline.py --check`: the 19 corpus reports render to the same bytes
  and draw the same verifier verdict as at the base commit (`tests/treadle/golden.json`).

`test_treadle_leaves_the_product_alone.py` runs both. Delete `docs/treadle/BASE` when the
project ends and both stand down.

### Approved survey revision — 2026-10-20

The user subsequently requested replacing the customer survey with a supplied opening,
seven Core Pack questions, and seven More Specific Questions. That request authorizes
the survey catalog, intake persistence/routes, browser survey, and their regression
tests. It also requires the cost anchor in `pricing.py` to accept the new paragraph
answers without mistaking a breakdown's first number for a total.
`scripts/treadle_guard.py` lists these exact files as a narrow exception.
Research execution, billing, auth, and the golden report baseline retain their existing
boundaries. See [the revision record](SURVEY_REVISION_2026-10.md).

The user's subsequent “I want it all connected” request also authorizes the exact
customer dispatch/delivery/workshop guard files in `APPROVED_CONNECTION_FILES`.
The later “only doing treadle in the future” request makes Treadle the sole path for new
customer reports, including API requests without a selector and restored survey drafts.
The full-analysis format is fixed; explicit legacy API selection is rejected. This
includes owned HTML/PDF delivery, honest partial outcomes, and blocking purchases of
unsupported workshop actions. Historical report delivery and legacy interrupted-job
recovery remain compatible; the legacy research implementation itself is unchanged.

On 2026-10-22 the user approved aligning the product suite with that route. `/plan`
now checks the Treadle config after the paywall and quota, and undoes the credit, slot
and job when it answers 503. A free attempt still counts toward the daily cap, because
quota has no inverse for its ledger. Product tests that start a run use conftest's
`treadle_stand_in`, and tests that assumed revisions or re-runs through `/plan` expect
409. The files are listed in `APPROVED_TREADLE_ONLY_TEST_FILES`. The server's `.env`
sets `TREADLE_PREP_MODEL`; `.env.example` documents it.

## Phases

| # | Phase | Done when |
|---|---|---|
| 0 | Golden baseline and product guard | both witnesses green (done) |
| 1 | `ReportType` registry; market research registered by an adapter around `run_plan` | a second pack (travel guide) runs through the same registry (done) |
| 2 | Carve `treadle/` as an installable package; re-export shims at old paths | clean venv installs it and runs a pack written outside the repo, no Castor module imported (done: `scripts/treadle_standalone_check.py`) |
| 3 | `FactStore`: every value carries source, tier and writer; reads are recorded | travel guide: every figure in its report resolves to a fact with a tier (done: `treadle/facts.py`) |
| 4 | Survey derived from section inputs (`ask` / `find` / `assume`, load-bearing first) | travel guide's survey asks only what its plan cannot run without (done: `test_treadle_survey_asks_only_what_the_plan_needs.py`) |
| 5 | Service: CLI, HTTP and MCP; survey and result as JSON | an external script drives survey to report with the Castor app not running (done offline: `treadle/service/`, `test_treadle_service.py`, [SERVICE.md](SERVICE.md); no live market-research run through it yet) |
| 7 | Per-pack eval corpus and scorecard in CI | an engine change ships only if no pack regresses |

Phase 6 (moving Castor's steps onto `Section`, adopting FactStore and the derived survey)
is product work and is out of Treadle's scope. Castor adopts when it chooses to.

Estimate: 4 to 5 focused weeks; standalone after phase 2, about 2 weeks.

## Notes carried forward

- The customer `/plan` route now uses Treadle exclusively. Historical interrupted jobs
  and the legacy CLI still use `plan.run_plan`. The 12 call-time leaks capped in `test_the_frame_does_not_import_the_soul.py`
  close when the product chooses to dispatch through `treadle.run_report`, not before.
- `progress` receives a `SectionResult` per section from a sections pack, but a partial
  result dict from a runner pack (what `run_plan` already emits). Phase 5 must give the
  service one event shape before anything outside Castor subscribes to it.
- Phase 2 left exactly one reach from the engine into its host for tools
  (`treadle/defaults.py`, inside Castor only) and one for refunds (`treadle/jobs.py`,
  skipped when `billing` is absent). `paths.py` stayed at the root: it is Castor's map of
  its own files. `cache` and `jobs` keep their default files at the repo root.
- Not yet moved: the report and gate engine (`report/verifier`, `report/render_*`,
  `report/citation`, `report/trace`, `report/section_provenance`, `gates/common`,
  `gates/runner`) and the three registries inside `tools/`, `skills/`, `agents/`. They
  live inside domain packages, so each needs its own look; the standalone engine runs
  without them today.
- Phase 3 records provenance per section key, not per leaf: a figure's tier is the tier
  of the section that wrote it. Two gaps it made visible, both for phase 4: (1) a pack
  that reads the brief by closure (the travel guide's food-trip check) shows the brief
  with no readers, because the store only sees declared reads; section inputs must be
  declared to be traced. (2) A computed value inherits nothing from its inputs' tiers
  yet: a budget computed from inferred restaurant picks reads as `computed`. The survey
  work should surface the weakest input behind each figure.
- Phase 4 closed both phase-3 gaps. Sections name outside values in `consumes`,
  `optional` and the new `decided_by`, so every read of an input (and the brief) is traced;
  every fact carries `rests_on` and `weakest_from`, the weakest tier beneath it. Reports
  carry `disclosures` (one sentence per input read that no person stated) and
  `open_inputs`. The survey lives in `treadle/survey/` (engine, demand, question, entry,
  finding, unfound, session, disclose); the spec it was built from was produced by a
  three-design judge panel and each step was verified independently.
- Phase 5 (2026-10-22) closed four of the carried items. Surveys are kept server-side
  under an id, so their unsigned JSON never reaches a client. Runs write one event shape,
  `treadle.run/1`, for sections and runner packs alike. The wire protocols are versioned
  side by side (`treadle.service/1`, `treadle.survey/1`, `treadle.run/1`,
  `treadle.form/1`). An unknown report key is `unknown_report` on the wire. Still open:
  callers are not authenticated, so STATED still means "someone called answer()"; and no
  call submits a finding. The old progress-shape note above is answered by `treadle.run/1`.
- Carried to phase 5 (the wire): sessions are unsigned and `from_dict` cannot prove who
  answered, so a served survey keeps sessions server-side or signs them; `answer()` cannot
  tell a person from an agent, so STATED means "someone called answer()" until callers
  are authenticated; a finder loop keyed on `Input.look_for` is not built; questions are a
  third event shape (`treadle.survey/1`) to version beside progress events; an unknown
  report key raises the registry's KeyError, not a SurveyError code.
- Known limits, recorded rather than fixed: fingerprints compare spelling (NYC and
  New York disagree); a finding loaded from JSON is not re-capped by its basis' tier; two
  disagreeing finders never raise a dispute between themselves; each survey call
  re-derives the plan, which is trivial at 5 sections and worth caching at Castor's 25;
  legacy zero-argument `inapplicable` closures are opaque to the survey, so only
  `decided_by` rules are guaranteed never to be asked about early; `period_choices`
  (Castor's recurring-price period) waits for Castor to adopt the survey.
