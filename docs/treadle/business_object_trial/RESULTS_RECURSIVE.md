# Bounded recursive business objects — 2026-10-19

This prototype tests the next PREP refinement using the same twelve-statement
Amazon, Uber, and Shopify fixtures as the [original pilot](RESULTS.md). It mines
supplied text into a business object; it does not research or generate a full report.
The customer execution path remains unchanged.

Integration update: the current code now connects this builder to selectable
`market_research_next` PREP, passing the full accepted graph and explicit coverage gaps
to research. The experiments below remain historical results, not evaluations of the
connected report pipeline. See [the current architecture](../PREP_AND_ORCHESTRATION.md)
and [the connected test guide](../CONNECTED_PIPELINE_TEST.md).

## Implemented behavior

- Fresh object instances contain organizational nodes, atomic scalar facts, typed
  relationships, and missing-input questions.
- A deterministic queue expands selected branches through actual model calls.
  Validated additions commit atomically against a revision. The default six-call
  budget includes one reserved support review; pending work stays explicit.
- The support reviewer assesses factual nodes, facts, and relationships against the
  input. Unsupported or uncertain assertions are quarantined, with dependent-node
  removal. A failed review exposes no accepted factual claims.
- Code projects accepted, stated input into eight fixed report sections. Numeric
  coverage requires matching scope and units. Research readiness and finished-report
  readiness are separate; PREP always leaves the latter false.

These modules live under `packs/market_research_next/business_object`. They are
provider-independent and callable through an injected model. The standalone trial
runner records actual provider requests and responses. Treadle assembly, production
PREP, and the report workflow's two global correction rounds are unchanged. Local
statement references do not enable the deferred claim-to-source tracing subsystem.

## Experimental method

Each extracting and support-reviewing request receives only an anonymous company
alias, supplied statement IDs/text, the current object, and generic schema/readiness
rules. Company names, source URLs, and expected evaluation checks stay outside the
model request. Familiar descriptions may still be recognizable. No external search,
provider retry, automatic fallback, or hidden repair is used.

All calls use the explicitly configured `claude-opus-5-5`. Responses must finish
normally and parse as JSON; one exact complete JSON code fence may be removed and
is recorded. Token-limit responses are preserved but rejected. Calls have a 12,000
output-token ceiling and a six-call per-company workflow limit. Any implementation
change and subsequent experiment is a separate recorded run, not a hidden retry.

An independent model audit uses the withheld seven checks per company and reviews
additional claims. It is exploratory model evaluation, not a human-labeled benchmark
or an accuracy measurement. The built-in support reviewer and independent evaluator
can both make mistakes.

## First recursive attempt

Saved under [`runs/recursive_v2`](runs/recursive_v2/summary.json).

| Company | Calls | Accepted nodes / facts / relationships | Outcome |
| --- | ---: | --- | --- |
| Amazon | 2 | 1 / 0 / 0 | Initial response hit the output limit; no factual object accepted |
| Uber | 3 | 37 / 41 / 36 | One root call, one branch expansion, one support review; 5 edges quarantined |
| Shopify | 2 | 31 / 25 / 33 | One extraction call plus support review; 5 edges quarantined |

The model still tried to put most detail in the root call. This made Amazon fail
and left Shopify without a separate branch expansion. The correction is a smaller
first-pass mapping contract with explicit record limits, followed by branch calls.
The initial failed response and both completed objects remain preserved.

Some quarantines also illustrate the review tradeoff: the model can reject ordinary
business implications too aggressively, such as the payer implied by a service-fee
description. The independent audit must assess both missed errors and useful detail
lost through rejection. More review is not automatically better extraction.

## Bounded first-pass rerun

Saved under [`runs/recursive_v2_bounded`](runs/recursive_v2_bounded/summary.json).
All three cases completed one root mapping call, four branch calls, and one support
review, with no pending branches and no malformed or truncated responses. Expansion
completion means the selected queue is empty; it does not prove semantic completeness.

| Company | Accepted nodes | Structured facts | Relationships | Quarantined records | Elapsed seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Amazon | 34 | 63 | 28 | 7 | 207.21 |
| Uber | 31 | 22 | 29 | 5 | 144.47 |
| Shopify | 29 | 32 | 21 | 8 | 159.64 |

The 18 calls used 156,539 input tokens and 64,144 output tokens. The three company
workflows ran concurrently. This is considerably more work than the original
three-call pilot; the small quality change does not yet justify always using six
calls. No dollar cost is asserted because the model tariff was not verified.

The rerun's recorded root contract allowed no root questions. A subsequent code
review identified that this drops company-wide uncertainties for simple businesses.
The current implementation permits three root questions and passes an offline
preservation test. This additive change was **not** live-rerun; the saved prompts
accurately retain the earlier zero-question limit.

## Independent comparison

| Company | Original pass / partial / fail | Recursive pass / partial / fail |
| --- | --- | --- |
| Amazon | 4 / 3 / 0 | 6 / 1 / 0 |
| Uber | 5 / 2 / 0 | 6 / 1 / 0 |
| Shopify | 7 / 0 / 0 | 6 / 1 / 0 |
| Total | 16 / 5 / 0 | 18 / 3 / 0 |

The improvements are concrete: Amazon's shipping-benefit and internal-use
connections, and Uber's membership links across Mobility and Delivery, are explicit
in the accepted graph. The three partial checks are:

- **Amazon:** computing, storage, and databases remain combined in one label rather
  than independently addressable services or fields. Repeated calls alone did not
  fix the required granularity.
- **Uber:** shipper-to-service and fee-stream connections exist, but no shipper payer
  link survives. The fixture identifies service beneficiaries more explicitly than
  payers; the unchanged expected check asks for more than that explicit wording.
- **Shopify:** the integrated back end's functions are separate facts, but the support
  reviewer removed its connection to the platform. This loses a relationship that
  the independent auditor judged supported in context.

The independent audit also found one unsupported **typed interpretation** outside
the 21 checks: Shopify's statement that larger merchants receive extra functionality
and support was stored as a stated customer `need`. The feature text is present in
the input; the need classification is not established. The built-in support reviewer
accepted it. This is especially consequential because readiness treats `need` as
persona evidence. No unsupported numerical prices or market totals were found by
this review, but that is not a guarantee that all accepted assertions are correct.

Full evidence and over-rejection findings:
[Amazon](audits/recursive_v2/amazon.json),
[Uber](audits/recursive_v2/uber.json),
[Shopify](audits/recursive_v2/shopify.json).

## Readiness and remaining integration work

The code correctly keeps every finished report unready: these descriptions lack
market scope, prices, adoption, competitor evidence, and customer observations.
However, some `needs_input` flags also reflect a mapping limitation. For example,
Uber's segment-to-actor `connects` relationships and role facts are present, while
some readiness rules expect offering-to-actor `serves` relationships. The current
all-offerings check can discard partial coverage when just one offering is missing
a required link. These statuses must not yet become automatic customer questions.

Before attaching this object to the selectable report path:

1. Evaluate granularity and semantic field meaning separately from input support.
   A scalar string may still combine several services; feature delivery is not
   automatically evidence of customer need.
2. Calibrate support review against ordinary sentence meaning, preserve disclosed
   inferences, and distinguish uncertain payer identity from an absent relationship.
3. Make section projection recognize equivalent supported relationships and retain
   partial coverage. Attach a missing question to the specific offering or scope.
4. Pass the accepted object and explicit unresolved requirements to the existing
   selectable PREP/research specification adapter. Compare resulting reports before
   changing customer execution.

This trial supports the representation and bounded-execution design. It does not
establish that the resulting object is complete, that six calls are necessary, or
that the report can be generated accurately without additional inputs and research.

## Engineering validation

The final focused and integration suite passes **172 tests and 172 subtests** across
the new schema, recursive builder, readiness rules, recording adapter, original
pilot, PREP, research backend, and deterministic report workflow. Ruff passes on the
new modules and tests. Code and Python reviews covered bounds, atomic rollback,
quarantine, error privacy, scope matching, and the company-gap regression.

The result browser was checked in local Chrome for all three company selections,
recursive expansion, JavaScript errors, and 360-pixel layout. These engineering
checks do not override the live semantic findings above.
