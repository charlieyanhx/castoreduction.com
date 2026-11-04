# Treadle: next iteration for PREP and dependency execution

Status: mixed implementation and planned refinements, 2026-10-19. The selectable
`market_research_next` foundation is implemented. A separate bounded recursive
business-object prototype now implements atomic facts, explicit relationships,
input-support review, quarantine, and per-section input readiness. Its
[three-company comparison](business_object_trial/RESULTS_RECURSIVE.md) improved
structured coverage but found semantic and projection gaps. It is now wired into the
selectable `market_research_next` PREP/research pipeline. Customer service routes remain
on the existing pipeline; live evaluation of the connected path is pending.
The typed analysis-chain and writing refinements below remain planned.
The selectable orchestrator now enforces finite process deadlines, shared call budgets,
input/output shape contracts, and at most two global correction rounds. These execution
guarantees do not establish numerical or semantic correctness of the business analysis.
See [the current implementation](PREP_AND_ORCHESTRATION.md) and
[the business-object experiment](../../experiments/business_object_trial/README.md).

## Recommendation

Keep the fixed report contract, deterministic workflow, replaceable research backend,
and report-wide limit of two correction rounds. Improve the information crossing those
boundaries. Treadle continues to assemble the report; analysis and orchestration can
evolve without changing that responsibility. Source-to-claim tracing stays disabled.

Borrow three patterns:

- STORM uses different perspectives to develop better research questions. Apply buyer,
  end-user, operator, and commercial perspectives inside PREP, mapped to existing
  sections. Start with one bounded model call, not a team of conversational agents.
  [STORM](https://github.com/stanford-oval/storm)
- Anthropic's research architecture gives workers explicit objectives, output formats,
  tools, and boundaries. Apply those constraints to each research function and let it
  adapt searches within its assigned budget.
  [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system)
- GPT Researcher's LangGraph example separates research, review, writing, and export.
  Give these distinct contracts in our implementation. They need not be separate agents
  or require a framework migration.
  [GPT Researcher](https://docs.gptr.dev/docs/gpt-researcher/multi_agents/langgraph)

## Problem 1: define the object of analysis and its research specification

### Inputs

Original survey, existing typed answers, and a fixed report contract describing what
each section must deliver. The report contract is application-owned.

### PREP output

1. **Venture definition.** Product, problem, buyer versus user, customer segment,
   workflow being replaced, business model, geography, unit of sale, alternatives,
   constraints, and the decision this report should support. Every value remains
   customer-stated, inferred, or unknown. Research must not silently rewrite customer
   statements or expand the scope.
2. **Assumptions and gaps.** Stable identifiers for shared assumptions and unresolved
   questions. Specify which section needs each answer and whether absence blocks a
   calculation or permits an explicitly conditional scenario.
3. **Section research specifications.** For each section: scope, question IDs,
   assumptions to test, required output fields, and a definition of completion.
   Completion means a supported answer or an explicit unresolved result; empty prose
   is not completion. Code attaches the declared output schema and task dependencies;
   the model does not invent either.

Use buyer/user/operator/commercial perspectives as a question-generation checklist.
PREP interprets and plans; it does not invent market evidence or simulate customer
interviews. No new mandatory customer-confirmation step is needed for this version.
Missing scope can remain explicitly unresolved, and dependent claims must respect it.

For “AI receptionist for dental clinics,” the specification should ask whether the
buyer is an independent clinic owner or a chain, which call workflows are covered,
whether the unit of sale is a clinic or a seat, which alternatives qualify for
comparison, and what geography applies. Geography and pricing must remain unknown
when the customer has not supplied them; proposed assumptions must be labeled.

### Acceptance

- Every fixed section has venture-specific questions and declared deliverables.
- No inferred statement becomes customer-stated; explicit unknowns survive.
- Shared scope and assumptions have stable identities that downstream results refer to.
- In test cases with materially different businesses, research specifications change
  meaningfully rather than merely replacing the business name.

## Problem 2: execute functions from declared inputs and outputs

### First finish the task contracts

For each function, declare its required artifacts, optional context, output schema,
allowed tools, execution budget, and validators. Each output has one producing task.
Multiple tasks may contribute to one report section, and one artifact may feed several
sections. The current one-task-per-section graph is a starting configuration.

Separate machine-consumed analysis from presentation. For example, market sizing should
produce population count, market definition, geography, time period, assumptions, and
calculation inputs/results. Revenue consumes those fields and a price assumption with
currency, billing period, and unit. It should not recover those values by reading a
market-size paragraph. A proposed shared assumption is not treated as verified evidence.

Start with the most consequential chain: customer scope -> market size + pricing ->
revenue -> summary. Port useful deterministic calculations from the existing Castor
pipeline. Then apply the same contracts to competition and differentiation. Final edges
must follow the agreed fields rather than the order in which sections appear in a report.

### Scheduling and corrections

- Start a task when required producer results are available and concurrency capacity
  exists. Independent ready tasks can run together.
- Give workers immutable copies of declared inputs. The coordinator validates and
  commits outputs; workers do not edit a global venture object.
- Validate required fields, compatible units, geography, periods, and calculations in
  code. Use an LLM reviewer for semantic conflicts that code cannot express easily.
- A review issue identifies the conflicting values and producing task that needs
  repair. The scheduler reruns that task and all affected consumers; it retains
  unrelated outputs.
- Count corrections globally: initial execution, correction round 1, correction round 2,
  then stop. Do not add independent per-section review loops. Tool attempts have their
  own finite budgets and are not extra report correction rounds.
- Remaining contradictions and missing information are visible. Exhausting the budget
  does not imply the report is internally consistent.

Dependency declarations establish valid execution orders. Finding the fastest schedule
also requires measured durations, provider limits, and an optimization objective. This
iteration targets correct readiness and useful concurrency; it does not claim a globally
optimal schedule. Within-task adaptive search remains compatible with deterministic
cross-task scheduling.

### Writing and assembly

Keep analysis artifacts independent of section prose. A writer consumes the selected
analysis versions and cannot introduce new computed values or resolve scope conflicts
silently. Treadle receives the fixed section payloads for assembly and export. This is
a logical separation: functions in the existing backend are sufficient initially.
No additional writing/review loop may evade the global correction limit.

### Acceptance

- No task uses missing or obsolete required output; independent tasks overlap in tests.
- A changed market-size artifact causes dependent revenue and summary regeneration.
- Unit/currency/geography mismatches are detected before those quantities are combined.
- A persistent contradiction triggers no more than two global correction rounds and
  remains disclosed afterward.
- Replacing a research implementation or scheduling implementation preserves the
  report's input/output contract.

## Ordered delivery plan

| Step | Deliverable | Current status / completion gate |
| --- | --- | --- |
| 1. Specify contracts | Section requirements, shared quantities, task input/output table | Versioned PREP and finite section payload contracts enforced; exact semantic quantities and ownership are next |
| 2. Refine PREP | Recursive business object, question/assumption IDs, gap handling, section completion criteria | Bounded recursive object and deterministic coverage connected to selectable PREP; accepted full graph and section gaps reach research; live evaluation pending |
| 3. Build one complete analysis chain | Customer scope, market sizing, pricing, revenue, summary with typed artifacts and deterministic calculations | Existing results are primarily prose; implement and validate this chain first |
| 4. Connect review and writing | Code checks plus semantic review, typed repair issues, prose derived from accepted analysis | Isolated global reviewer/correction engine, deadlines, and call budgets implemented; enrich numerical checks and separate analysis from prose |
| 5. Extend remaining sections | Competition, customer voice, differentiation, simulated perspectives | Reuse the contract pattern; keep simulations visibly labeled |
| 6. Compare and roll out | Matched old/new reports and scorecard | Run live comparison while the new path remains selectable |

For evaluation, start with a small reference set spanning SaaS, a local service, a
marketplace, an ambiguous business description, and missing/conflicting inputs. Include
expected customer scope and critical research questions. Compare relevance, unanswered
questions, unsupported assumptions, consistency, completeness, latency, and cost.
Run a few repeated cases to observe model variability. Automated structural tests do
not establish live report quality.

The previous implementation passed 338 tests and 254 subtests, including existing
regressions, and an offline PDF smoke test. That verifies the foundation, not these
proposed refinements or their live quality. Customer-route replacement is a later
rollout decision after comparison.

## Deferred

Claim-to-source tracing, automatic task creation, autonomous lead-agent scheduling,
distributed workers, and framework migration remain outside this iteration. Preserve
the existing `Workflow` interface and task artifacts so those capabilities can be added
when measured needs justify them.
