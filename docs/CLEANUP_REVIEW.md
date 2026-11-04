# Repository cleanup review

Reviewed 2026-09-17. Scope: tracked application Python, routes, templates/browser assets,
capability discovery, dependencies, documentation, and the offline regression suite.
Generated artifacts, local databases/caches, the ignored `data/` directory, and the
vendored `claude-code-main/` reference tree were not treated as application source.

## Outcome

The cleanup removed code only where reachability or redundancy was demonstrable. It also
fixed review-path defects found while tracing the code. The adaptive planning and central
review/rework architecture in `RESEARCH_REVIEW_DESIGN.md` remains a migration proposal;
cleanup did not silently replace the current pipeline.

### Removed

- The unused monolithic `assemble_4ps` writer and its prompt. Production and tests use
  `assemble_4ps_split`; retaining both made whole-module searches and audits pass against
  code that never generated a report.
- Unreferenced helpers: the old workshop-note partition, standalone chart bar, legacy
  domain HEAD probe, and an unused intake request model.
- Unused imports, local calculations, duplicate imports, redundant f-string markers, and
  an unreachable duplicate return.
- Three declared-but-unused runtime dependencies: `instructor`, `diskcache`, and
  `python-whois`.
- The mutually incompatible installed `crawl4ai 0.8` / `trafilatura 2.1` lxml ranges;
  requirements now select crawl4ai 0.9 and their shared lxml 6 range.
- Stale README/architecture claims about fixed test counts, report duration, implemented
  orchestration, and historical documentation paths.

### Fixed during review

- The semantic report reviewer read obsolete flat `tam_usd` / `sam_usd` / `som_usd`
  fields. It now receives bounded, valid JSON for current sizing, economics, financials,
  price reconciliation, viability, and marketing outputs.
- A rejected refinement could mutate nested values in the retained report because the
  candidate shared its objects. Evaluators and refiners now receive deep copies.
- Refinement could improve an aggregate judge score while breaking a contract requirement
  that already passed. Candidate acceptance now preserves satisfied requirements, while a
  candidate meeting the full contract wins even if an unnecessary excess score falls.
- A candidate-evaluation exception now retains the last evaluated artifact.
- Duplicate specialist names from the planning model could execute the same worker more
  than once. Selection is type-checked and deduplicated in priority order; malformed
  selections fall back to the applicable roster.
- The live report runner returned success after a report-level error, blocked or missing
  verification, or render failure. It now preserves diagnostic artifacts and exits
  nonzero for those states.
- Container build context now excludes local credentials/session state, transcripts,
  databases, caches, and the vendored reference tree.
- Gate detectors are explicitly imported by the invariant table, so undefined detector
  names are caught statically instead of hidden behind wildcard imports.
- Static production checks are reproducible through `ruff.toml`; development-only tools
  live in `requirements-dev.txt`.
- Setup invokes pip as `.venv/bin/python -m pip`, so moving the checkout does not depend
  on a stale absolute shebang in the venv's generated `pip` launcher.

## Verification performed

- `python -m ruff check .` checks production code for undefined names, unused bindings,
  redefinitions, and syntax-level failures. Test files are excluded from this lint policy
  because the suite contains intentional fixtures and compatibility imports.
- `./bench.sh doctor` verifies every registered tool, skill, and agent has fixture
  arguments and can be addressed independently without running it.
- `./test_all.sh -q` is the authoritative offline behavior suite. It blocks provider
  credentials and external sockets by default.
- `git diff --check` checks whitespace/patch integrity.

Static analysis was used as a candidate generator, not an automatic deletion list.
FastAPI handlers, decorators, registry entries, Jinja references, scripts, compatibility
exports, and names patched by tests all create reachability that a simple call graph misses.

## Kept deliberately

- `harness/refine.py` and `skills/refine_report.py`: live through the `/plan` `refine`
  option and tests.
- Research crew/planner/synthesis: live in deep reports, the direct crew API, and the
  capability bench.
- `plan.py` compatibility aliases: tests and callers patch/import these addresses. Remove
  them only with a caller migration.
- Public exception types and package re-exports: small compatibility surfaces, with
  insufficient evidence that external/local consumers do not import them.
- Historical audits and plans: preserved under `docs/` or `docs/archive/` as dated
  evidence; current docs now state that their metrics and status are snapshots.
- `legacy-site/`: excluded from active static checks, but not deleted because repository
  history alone does not prove the user no longer needs the preserved site.

## Remaining material limitations

These are architectural work, not dead-code cleanup:

1. `plan.run_plan` is still the primary, mostly explicit orchestrator. The planner does
   not choose across the complete tool/skill registry.
2. The optional refinement adapter regenerates sizing or consumer research but does not
   automatically invalidate and recompute every transitive consumer. Use its result as a
   bounded quality experiment, not proof of whole-report convergence.
3. The deep-mode research crew runs late in finalization; it supplements the report and
   does not currently drive the earlier sizing/economics calculations.
4. A verifier exception produces an explicit `not_run` report that remains deliverable by
   current policy. The live audit runner treats it as failure, but product delivery does
   not block it.
5. Python thread timeouts stop waiting but cannot terminate an in-flight external call.
   Request-level timeouts and budget reservations remain necessary.
6. External source health and live model behavior cannot be certified by the offline
   suite. Use deliberate bench/live runs and inspect their artifacts and costs.

## Next migration boundary

The next safe change is to add versioned artifacts and dependency invalidation around the
existing producers, then exercise targeted recomputation without an LLM. After that is
stable, expand planner selection and structured reviewer repair actions behind an explicit
execution-mode switch. The old path should be deleted only after corpus comparisons and
supported endpoint/result compatibility are covered by the replacement.
