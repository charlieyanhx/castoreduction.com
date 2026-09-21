# Current architecture

Inspected against the local implementation on 2026-09-17. This replaces a design snapshot
that mixed implemented features with planned integrations. The proposed next architecture
is in [RESEARCH_REVIEW_DESIGN.md](RESEARCH_REVIEW_DESIGN.md).

## Execution path

```text
Confirmed survey → POST /plan → persisted background job
  → profile / competitor / customer evidence
  → pricing and economics
  → scale-specific sizing
  → marketing recommendations, financials, viability
  → optional refinement
  → deep-mode research crew (when requested)
  → verification of data and rendered page
  → analyst writing (when enabled)
  → writing checks → saved result → HTML / PDF / one-pager
```

`routes/research.py` owns submission, authorization, purchase/quota handling, and job
startup. `plan.run_plan` orchestrates research. Most research is still sequenced explicitly;
independent I/O and selected production steps run concurrently. Step implementations have
partly moved into `orchestrator/steps/`.

`orchestrator/sections.py` adapts selected producers to `core.section.Section`. Those
contracts declare required/optional reads, order dependencies, isolate input context,
check section-local invariants, and record input digests. They do not yet describe the
entire pipeline or automatically regenerate all downstream consumers after a repair.

## Tools, skills, and agents

- **Tools** are atomic source/processing operations registered in `tools/registry.py`.
- **Skills** compose operations and declare their output in `skills/registry.py`.
- **Agents** run bounded, isolated tool-selection loops through `harness/agent.py` and are
  registered in `agents/registry.py`.

All return the `core.Evidence` envelope: payload, source, count, timing/cost metadata,
error, and whether the result is inferred/fallback evidence. Empty results, source errors,
and inferred results are different states.

`agents/planner.py` selects from the four specialist agents in its roster. The planner is
not currently a general scheduler across every tool and skill. `agents/crew.py` dispatches
selected specialists concurrently and synthesizes their outputs into `research_brief`.
The normal pipeline calls this crew in deep mode, after the main calculations. Individual
agents and the crew are also available through the capability bench and relevant API.

`capabilities/gateway.py` validates arguments and reserves metered-source spend.
`capabilities/scheduler.py` handles concurrency and waiting deadlines. A thread timeout
stops waiting; it does not kill the external operation. `llm.py` handles model backends,
caching, usage accounting, and fallback. These are not yet one shared end-to-end budget
controller.

## Evidence and numerical computation

Source retrieval lives in `tools/`, `sources.py`, and `scrape/`. Model-assisted discovery,
classification, synthesis, and simulated customer/pricing exercises coexist with sourced
observations; outputs must preserve that distinction.

`plan.run_sizing_stage` and the sizing skills choose the applicable scale/method. Sizing,
pricing, economics, and financials exchange structured values. Validation and provenance
checks qualify or withhold unsupported results. The presence of an agent answer does not
by itself prove a number is sourced.

`persistence/` records transcripts, provenance, and costs. `jobs.py` stores job progress
and results. A checkpoint supports resumption; it does not replace dependency-version
validation for arbitrary revised inputs.

## Review and writing

`report/verifier.py` runs deterministic invariants from `gates/`, formula reconciliation,
and citation checks. Optional LLM review receives bounded structured blocks for sizing,
economics, financials, price reconciliation, viability, and marketing recommendations.
It returns findings; it does not dispatch repairs. Large blocks explicitly disclose
truncation and remain valid JSON.

`harness/refine.py` provides a generic bounded evaluate/refine loop. It isolates candidates
and evaluator inputs, preserves the retained version on failure, and refuses to trade a
satisfied contract requirement for an aggregate score gain. `skills/refine_report.py`
adapts an independent judge and the sizing validation gate to it. `refine=True` in the
main pipeline supplies sizing and consumer-research regenerators. That optional path
still lacks general downstream invalidation; see the cleanup review before relying on it
as a complete cross-section repair controller.

`report/synthesis.py` writes the final analyst narrative from the fact layer in the chosen
style. It is a separate writing call, not an autonomous researcher. Its backend/paid access
and effort-level checks live with the synthesis section adapter. `report/check_writing.py`
runs the writing-specific checks after generation.

HTML, PDF, and one-pager routes consult the report verdict. Blocking findings withhold
normal delivery. A verifier exception currently produces an explicit `not_run` status
that remains deliverable by policy; it must not be described as a verified report.

## Tests and change boundaries

`test_all.sh` collects the full offline pytest suite. Architectural tests cover module
layering, registry resolution, route/import compatibility, asset reachability, ownership,
and report contracts. `ruff check .` checks production Python for undefined/unused names
and related correctness errors. It excludes test modules and the vendored/legacy trees.

`bench.sh` exercises a capability directly and distinguishes unavailable evidence from
empty results. `tools/run_live.py` exercises the full report path with real configured
sources/models and writes inspectable artifacts. It exits nonzero unless the report was
verified, publishable, and rendered successfully.

The proposed adaptive orchestrator should reuse these components. Replacing the current
pipeline, enabling a central review agent by default, and automatic downstream
recomputation are a migration project, not completed cleanup.
