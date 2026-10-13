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

## Phases

| # | Phase | Done when |
|---|---|---|
| 0 | Golden baseline and product guard | both witnesses green (done) |
| 1 | `ReportType` registry; market research registered by an adapter around `run_plan` | a second pack (travel guide) runs through the same registry (done) |
| 2 | Carve `treadle/` as an installable package; re-export shims at old paths | clean venv installs it and runs a pack written outside the repo, no Castor module imported (done: `scripts/treadle_standalone_check.py`) |
| 3 | `FactStore`: every value carries source, tier and writer; reads are recorded | travel guide: every figure in its report resolves to a fact with a tier |
| 4 | Survey derived from section inputs (`ask` / `find` / `assume`, load-bearing first) | travel guide's survey asks only what its plan cannot run without |
| 5 | Service: CLI, HTTP and MCP; survey and result as JSON | an external script drives survey to report with the Castor app not running |
| 7 | Per-pack eval corpus and scorecard in CI | an engine change ships only if no pack regresses |

Phase 6 (moving Castor's steps onto `Section`, adopting FactStore and the derived survey)
is product work and is out of Treadle's scope. Castor adopts when it chooses to.

Estimate: 4 to 5 focused weeks; standalone after phase 2, about 2 weeks.

## Notes carried forward

- Castor's own routes and CLI still call `plan.run_plan` directly. That is deliberate:
  they are product. The 12 call-time leaks capped in `test_the_frame_does_not_import_the_soul.py`
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
