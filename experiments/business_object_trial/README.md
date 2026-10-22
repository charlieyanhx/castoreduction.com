# Recursive business-object trial

This standalone experiment asks whether one recursive representation can preserve
different business structures and project them into a fixed report. It does not
change production PREP, report generation, or the source/claim tracing subsystem.
The contract itself uses only the Python standard library and makes no model calls.

`contract.py` exports `PROMPT`, the permitted kinds/sections, and
`validate_business_object(document, statements)`. Input statements are a list of
objects with exactly `id` and `text`. The output has exactly:

- `version`: `business-object-trial/v1`.
- `company_alias`: the supplied anonymous company label.
- `root`: a recursive `{id, kind, label, statement_ids, children}` node.
- `relationships`: directed `{from, to, kind, statement_ids}` references.
- `missing_details`: `{id, node_id, detail, question}` entries.
- `report_projections`: `{section, node_ids, missing_detail_ids}` for each section.

Node kinds are `company`, `group`, `reported_segment`, `business_line`, `offering`,
`customer`, `user`, `supplier`, `partner`, `revenue_stream`, `metric`, and
`capability`. Labels retain the stated details; this deliberately small contract
does not add typed financial fields. Every factual node and relationship requires
at least one valid original statement ID. Only `group` permits empty evidence,
and its label must start `[inferred grouping] `. Unknowns belong in questions.

Relationship kinds are `serves`, `uses`, `generates`, `measures`, `pays`, `supplies`,
and `depends_on`. For example, an offering serves a customer, uses a capability,
and generates a revenue stream. A metric measures an offering. An actor pays a
revenue stream. The validator checks supported kinds and references; it does not
enforce a semantic node-kind combination for every edge.

Children form a tree with root ID `company` and kind `company`. Shared entities
appear once; cross-references connect them across branches. Cross-reference cycles
are allowed and never recursively expanded. Self links and duplicate typed edges
are rejected. Node and question IDs are unique lowercase snake_case identifiers,
at most 64 characters. The extraction prompt requests descriptive IDs stable across
revisions; structural validation cannot establish semantic identity across runs.

The eight report sections are `summary`, `target_personas`, `multi_perspective`,
`market_size`, `revenue`, `differentiators`, `competitive_landscape`, and
`customer_voice`. Every section occurs once, including empty projections. One node
may appear in several projections; the tree need not mirror report sections.

Validation is iterative and bounded: 200 nodes, depth 12 (root depth 0), 400
relationships, 100 missing details, and 400 input statements. It rejects malformed
fields, child cycles or aliases, duplicate IDs, dangling references, invalid input
IDs, and missing evidence on factual nodes. It returns counts, maximum depth,
node-kind counts, and referenced/unreferenced input IDs without mutating the object.

These checks establish structural integrity, not factual truth or entailment.
An existing statement ID does not prove a label is supported. Human review must
check the labels, grouping decisions, customer/user/supplier distinctions, reported
segments versus offerings, GMV/gross bookings versus revenue, and omissions.
No company knowledge, external research, source ledger, or claim-verification
subsystem is introduced by this contract. Fixture expectations must not be supplied
to an extracting model or used as a semantic oracle in these structural tests.

Run the focused structural tests from the repository root:

```sh
python3 -m unittest test_business_object_trial
```

To validate an extracted result, call:

```python
from experiments.business_object_trial.contract import validate_business_object

stats = validate_business_object(document, statements)
```

A successful result means the object satisfies the contract, not that extraction
was semantically correct. Keep model responses and human observations separate
from the structural test result when evaluating the trial.

`run.py` performs a small live extraction experiment with the existing configured
Anthropic credentials and paid-backend opt-in. It sends only alias and statement
text, preserves raw responses, records tokens/elapsed time, and validates objects.
It accepts plain JSON or exactly one complete JSON Markdown fence; that formatting
normalization is recorded. It never repairs JSON or requests a semantic retry.

```sh
.venv/bin/python experiments/business_object_trial/run.py \
  --model YOUR_SELECTED_MODEL_ID \
  --inputs docs/treadle/business_object_trial/inputs/amazon.json \
           docs/treadle/business_object_trial/inputs/uber.json \
           docs/treadle/business_object_trial/inputs/shopify.json \
  --out out/business-object-trial
```

Use a new output directory to preserve prior observations. The committed experiment
records under `docs/treadle/business_object_trial` include original parse failures
and the same responses after deterministic code-fence removal, without model reruns.
One call generates each recursive object in the original `run.py` runner.

## Bounded recursive prototype (v2)

`run_recursive.py` uses the reusable implementation under
`packs/market_research_next/business_object`. Each invocation builds a fresh object;
it does not share customer state or change the production report path.

The object separates organizational nodes, atomic scalar facts, typed relationships,
and missing-input questions. Parent links organize the object; financial inclusion,
bundle membership, customer relationships, and shared capabilities require explicit
relationships. Both stated claims and disclosed inferences reference their supplied
premises. These local input IDs are not a source-tracing subsystem.

Code manages a depth-ordered expansion queue. Each selected branch is expanded at
most once, using additions-only patches against a revision. A patch is committed
only after structural, reference, scope, and budget validation. Invalid patches stop
expansion and preserve the last accepted revision. The default six-call budget
includes one reserved final support-review call; it is independent of the report
workflow's two correction rounds. A budget or depth stop leaves pending work visible.
The first pass has smaller limits (16 new nodes, 6 facts, 6 relationships, 3 gaps,
and at most 4 expansion targets) to map meaningful branches before extracting their
details. Requested targets beyond the remaining budget stay pending.

The support reviewer sees the supplied statements and candidate, without fixture
expectations or public-company sources. Unsupported or uncertain claims are
quarantined; rejected nodes also remove dependent descendants, facts, and edges.
A failed review leaves no usable factual claims. This model review establishes a
judgment of input support, not independent factual verification.

Deterministic readiness rules then map accepted, stated material to the eight report
sections. They expose captured requirements, missing questions, and whether research
can start. Missing market scope, prices, adoption, competitors, and customer evidence
remain gaps. PREP never marks the finished report ready.

```sh
.venv/bin/python experiments/business_object_trial/run_recursive.py \
  --model YOUR_SELECTED_MODEL_ID \
  --inputs docs/treadle/business_object_trial/inputs/amazon.json \
           docs/treadle/business_object_trial/inputs/uber.json \
           docs/treadle/business_object_trial/inputs/shopify.json \
  --max-calls 6 --max-depth 6 \
  --out out/business-object-recursive-trial
```

Every request, raw response, provider usage record, committed candidate, accepted
object, quarantine decision, and readiness result is saved in a new run directory.
There are no provider retries, external search calls, or automatic model fallbacks.
The builder accepts an injectable `model(system=..., user=...) -> dict` callable,
so another provider can replace this experiment adapter without changing the object
contract or Treadle's report assembly.

```python
from packs.market_research_next.business_object.builder import build_business_object
from packs.market_research_next.business_object.readiness import assess_readiness

artifact = build_business_object(
    [{"id": "s1", "text": "We sell scheduling software to dental clinics."}],
    company_alias="New venture", model=your_explicit_model_adapter,
)
section_inputs = assess_readiness(artifact)
business = artifact["accepted"]
```

Focused offline validation:

```sh
.venv/bin/python -m pytest -q test_treadle_business_schema.py \
  test_treadle_business_builder.py test_treadle_business_readiness.py \
  test_business_object_recursive_trial.py test_business_object_trial.py
```
