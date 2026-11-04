# Survey-driven research and review

Status: proposed migration, not the default production execution path. Inspected against
the local source on 2026-09-17. The accompanying cleanup removes one unreachable duplicate
return in `routes/research.py`; it does not switch orchestration modes.

## Current behavior and the gap

`routes/research.py` accepts the confirmed intake and starts `plan.run_plan` on the job
system. Most steps still run in an explicitly maintained order. `agents/planner.py`
selects among four specialist agents, not the whole capability registry. That selection
is used by the research crew in deep mode, late in `_finalize_run`, after the principal
sizing, financial, and viability computations.

The existing pieces are useful and should be reused:

| Existing component | What it supplies | What is still missing |
| --- | --- | --- |
| `core/section.py` | Declared required/optional inputs, dependency ordering, copied input context, local checks, input digests | Whole-run coverage and automatic downstream invalidation/recomputation |
| `core/evidence.py` | Common result envelope; errors distinguished from empty and inferred results | Versioned fact references with scope, units, and derivation inputs |
| Tool, skill, and agent registries | Discoverable, individually testable capabilities | Planner-visible input/output contracts and applicability for the complete run |
| `harness/agent.py` | Bounded tool-selection loop with isolated context and evidence | Central planning across agents, skills, and deterministic calculations |
| `capabilities/gateway.py`, `scheduler.py` | Argument checks, metered-call budgets, concurrency and waiting deadlines | A shared run-level ceiling covering model calls as well as source calls |
| `report/verifier.py` | Deterministic checks and optional LLM findings | Structured repair tasks and broad evidence context for semantic review |
| `harness/refine.py`, `skills/refine_report.py` | Bounded evaluation/refinement and retention of a higher-scoring candidate | Hard validity precedence, immutable candidate versions, downstream recomputation |
| `report/synthesis.py`, `report/check_writing.py` | Writing from evidence, then checks on that writing | Writing only from a review-accepted evidence snapshot |

The current optional LLM review receives the four marketing narratives and a small
sizing summary. Its summary reads `tam_usd`, `sam_usd`, and `som_usd`, although current
report paths commonly use nested sizing objects. It is not a complete cross-evidence
review. The optional refinement adapter regenerates sizing or consumer research; it
does not recompute every dependent financial or narrative output. Its generic acceptance
rule compares aggregate judge scores, which is insufficient for making hard correctness
requirements binding.

## Execution algorithm

1. **Freeze intake.** Store an immutable, versioned brief containing confirmed answers,
   source text, unresolved questions, geography, business model, requested report shape,
   and the allowed time/cost budget. A founder's target price is an assumption about their
   venture, not an observed competitor price. Preserve that distinction.
2. **Define the research contract.** Derive required decisions and evidence from the
   venture. A local bakery needs a trade area and venue competition; a digital service
   needs a defensible customer universe. Missing critical survey inputs become explicit
   questions or limitations rather than fabricated defaults.
3. **Propose a plan.** Give a planning LLM the brief, research contract, and applicable
   capability descriptions. It emits structured tasks with a registered capability,
   purpose, arguments, required inputs, expected outputs, and budget. It does not emit
   executable Python or invent capability names.
4. **Compile and validate the plan.** Code resolves registry names, validates arguments,
   assigns one owner per canonical artifact, adds required calculations/checks, rejects
   cycles, and checks coverage and budgets. Invalid plans get one bounded correction
   attempt, then use a vetted applicable plan or stop with a stated reason. The LLM
   cannot remove required evidence checks to make its plan pass.
5. **Execute ready tasks.** The scheduler runs independent tasks concurrently and waits
   for dependencies before running consumers. Each task reads a copied, versioned input
   snapshot and returns a result instead of mutating shared report state. Validate and
   commit the result before releasing its dependants. Failure and empty results remain
   distinct; optional inputs may be absent with a reason.
6. **Review the assembled evidence.** Run deterministic checks, then an independently
   prompted reviewer over an evidence index, relevant fact bundles, source references,
   assumptions, coverage, and detected failures. Give the reviewer a way to retrieve
   full referenced artifacts instead of truncating arbitrary JSON. Separate calls and
   roles provide context isolation, not proof of independent judgment.
7. **Repair and recompute.** Convert actionable findings into validated tasks. Fetch new
   evidence for a gap; resolve units/scope for a numerical conflict; rerun a calculation
   for a derived error. A changed artifact invalidates all transitive consumers whose
   recorded input digests no longer match. Rerun those consumers in dependency order,
   then review the new snapshot. Pure editorial findings are instructions for the writer.
8. **Accept or stop.** Start with at most two repair rounds, additionally bounded by the
   run deadline, model/source spend, and per-task retries. Stop earlier when all required
   checks pass, no executable repair exists, or the same issue recurs without new evidence.
   Persist the reason. An unresolved mandatory blocker yields a withheld report; an
   allowed limitation yields a report that explicitly states that limitation.
9. **Write and check.** Freeze the accepted evidence version. The writing LLM receives it,
   the founder's report style, and accepted editorial findings. It can explain and connect
   findings, but cannot silently change facts. Check citations, numbers, completeness,
   and consistency on the final text and rendered page. Allow a bounded writing-only
   correction; if new research is needed, reopen evidence review and issue a new version.
10. **Deliver.** Persist the evidence version, writing version, verification verdict,
    rendered artifact, and audit trail together. Resume/rewrite/share paths must retain
    this binding so an older approval cannot approve newer content.

Pseudocode (proposed interfaces):

```text
brief = freeze_confirmed_intake(survey)
contract = required_research(brief)
plan = validate_and_compile(planner(brief, contract, registry))
snapshot = execute_dependencies(plan, brief)

for round in 0..MAX_REPAIR_ROUNDS:
    review = deterministic_checks(snapshot) + review_agent(snapshot, contract)
    if acceptable(review, contract):
        break
    if round == MAX_REPAIR_ROUNDS or budget_exhausted() or no_new_repair(review):
        return persist_withheld_or_limited(snapshot, review)
    candidate = execute_repairs(snapshot, validated_repairs(review))
    candidate = recompute_stale_dependants(candidate)
    snapshot = retain_valid_progress(snapshot, candidate)

draft = write_report(freeze(snapshot), review.editorial_instructions)
return check_correct_and_render(draft, snapshot.version)
```

## Data and review contracts

Keep `Evidence` as the capability envelope; wrap persisted outputs in an artifact record
rather than forcing every tool to manufacture report-specific metadata.

| Record | Required fields |
| --- | --- |
| Task | ID, capability, purpose, arguments, input references, output key, deadline, budget, attempt |
| Artifact | ID/version, producer, input digests, payload, status, source references, timestamp |
| Fact | Artifact/path, value, unit, currency, period, geography, population/entity scope, observed/assumed/computed/inferred, sources, derivation inputs |
| Review finding | Stable ID, kind, severity, affected artifact paths, supporting evidence, explanation, requested action, resolution check |
| Run | Brief/plan versions, task states, committed artifact versions, findings, spent/remaining budgets, terminal reason |

Review actions are a closed set: `research`, `recompute`, `clarify_input`,
`editorial_revision`, `disclose_limitation`. The reviewer proposes; deterministic code
checks whether the action is permitted, relevant, and funded. Findings without resolvable
evidence references do not overwrite facts. Editorial preferences cannot erase blockers.

Conflict checks first compare like with like: geography, period, population, price basis,
and unit. Monthly versus annual prices need normalization; two different years may both
be correct. Derived values are recomputed. Competing observations retain both sources
until a justified resolution is recorded. A review must not average away a real conflict
or suppress a credible minority finding merely to make the report internally agreeable.

For example, a pricing repair that changes the chosen price invalidates revenue and
break-even calculations, then viability and recommendations that consume them, then the
writing. It does not require refetching unrelated demographic data.

Candidate acceptance is correctness-first: a higher prose score cannot introduce a hard
blocker. Compare mandatory-check failures and evidence coverage before advisory quality;
retain immutable previous snapshots. Missing/unrun mandatory checks are not passes.

Execution deadlines must be honest: the current scheduler can stop waiting for a thread
but cannot terminate its external request. Use request-level timeouts and reject late
results from superseded attempts. Calls that may still complete continue to count against
reserved spend; a replacement attempt must not silently spend the same allowance twice.

## Migration and cleanup

Implement incrementally behind an explicit execution-mode switch:

1. Extend existing section contracts to the remaining computations. Remove cross-section
   writes by making each output have a single producer. Preserve current endpoint/result
   contracts during migration.
2. Add versioned snapshots, dependency invalidation, and targeted recomputation. Exercise
   these without an LLM first.
3. Expand the planner from the four-agent roster to contract-backed capabilities. Keep
   the legacy plan as the temporary fallback and benchmark baseline.
4. Replace the narrow review context with structured findings and the bounded repair
   controller. Reuse existing detectors and source/citation infrastructure.
5. Bind the existing writer, delivery checks, and resume logic to accepted versions.
6. Compare fixture and live corpus runs, then switch the default. Remove the old path
   only after its supported behavior is covered by the new path.

Deletion requires reachability evidence from imports, registry discovery, routes, scripts,
templates/browser imports, configuration, tests, and serialized job compatibility. A
missing direct Python caller alone is insufficient: decorators and auto-discovery are
execution paths, and compatibility exports are deliberately patched by tests.

Cleanup audit from this pass:

| Item | Decision |
| --- | --- |
| Duplicate `return result` in the `/plan` worker | Removed: structurally unreachable immediately after an unconditional return |
| `plan.py` compatibility exports and old module shims | Keep until callers and patch sites migrate; not proven dead |
| `skills/refine_report.py` / `harness/refine.py` | Live through `refine=True`; adapt/replace deliberately, do not delete as unused |
| Crew/synthesis modules | Live through deep runs, direct API, and bench; consolidate only after replacement |
| Intermediate section prose and prompts | Still consumed by report/review paths; do not remove just because the final writer exists |
| Architecture/README snapshots | Some describe historical or planned behavior; reconcile documentation during migration |

An AST scan of tracked non-test Python files found that duplicate return as a direct
unreachable statement. That narrow scan does not prove there is no other dead code.

## Acceptance tests

Use the existing offline suite and capability bench, with behavioral cases for the new
controller:

- A bakery and a digital service select different applicable work and satisfy their
  respective research contracts; invalid names, cyclic plans, and missing required work
  are refused.
- A price correction updates every dependent calculation and narrative while leaving
  unrelated source fetches untouched.
- Conflicting geography, time period, and units are distinguished from arithmetic errors;
  unresolved observations remain visible.
- A persuasive reviewer cannot clear an arithmetic blocker or add unsupported facts.
- Empty, unavailable, failed, and intentionally skipped evidence retain distinct states.
- Repair cycles stop on repeated findings, deadline, and spend limits; late results do not
  overwrite accepted versions.
- Resume uses input versions to rerun stale work without repeating completed purchases.
- A failed final citation check blocks delivery; the writer cannot cite its previous prose
  as evidence for a new claim.

Compare completeness, unresolved blockers, supported citations, downstream freshness,
cost, and duration against the existing corpus. A green offline suite proves controlled
behavior, not that external sources currently answer; use `bench.sh` for source health
and deliberate live corpus runs for end-to-end report quality.
