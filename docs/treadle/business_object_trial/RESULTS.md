# Business-object trial — 2026-10-19

Follow-up: [bounded recursive prototype and comparison](RESULTS_RECURSIVE.md).
The original one-call observations below are preserved.

This is an exploratory test of a recursive business representation, separate from
production PREP. It uses three source-derived, simulated customer descriptions:
Amazon, Uber, and Shopify, each based on FY2025 public disclosures. These deliberately
incomplete inputs are neither verbatim filings nor complete models of the companies.

## Method

Each description contains 12 statements, approximately 200 words. Company and product
brand names were generalized. The same extraction prompt and recursive contract were
used for all three. Each model request received only a Company A/B/C alias and the
statement IDs/text; source references and seven expected checks per company stayed
local. Familiar business characteristics may still be recognizable, so this is not a
proof that pretrained company knowledge had no influence.

Three actual API calls used `claude-opus-5-5`, confirmed available through the configured
provider. There were no model tools, hidden retries, weaker fallbacks, or model-generated
semantic corrections. Total usage was 6,247 input tokens and 23,968 output tokens.
Elapsed time per call was approximately 55–69 seconds; the calls ran concurrently.
No dollar cost is asserted because this model's current tariff was not verified.

The output contains recursive nodes, explicit cross-references, missing-information
questions, and selections of relevant nodes for each of eight fixed report sections.
This trial tests a recursive *representation* generated in one call per company.
It does not yet implement repeated model calls that recursively expand incomplete
nodes, and it does not generate or validate a complete business report.

## Observed structure

| Company | Nodes | Maximum depth below root | Cross-references | Missing-detail questions |
| --- | ---: | ---: | ---: | ---: |
| Amazon | 34 | 3 | 26 | 14 |
| Uber | 29 | 3 | 23 | 10 |
| Shopify | 32 | 5 | 24 | 13 |

All three objects reference all 12 supplied statement IDs and project into all eight
report sections. Reference coverage does not establish that every fact was correctly
represented; that requires the separate semantic review.

The first strict parser rejected all three responses because the model wrapped the
JSON in a Markdown code block. The original responses and failure metadata are
preserved under `runs/initial`. Removing only the complete outer code fence, without
changing JSON content or making further model calls, yielded the objects under
`runs/normalized`; all three pass structural validation. The adapter now handles
that exact wrapper and rejects malformed JSON, truncated responses, and extra prose.

## Independent semantic review

An independent model reviewer receives the original inputs, withheld expectations,
and unchanged extracted objects. It judges input support rather than filling gaps
from public-company knowledge. These are exploratory model-review judgments, not
a human-labeled benchmark or a measured production accuracy rate.

The 21 checks distinguish semantic preservation from machine-readable connections:
"nest" and "connect" require explicit structure; "preserve" and "distinguish" can
be satisfied by a faithful label. Per-check evidence and additional issues are in
the JSON audit files.

| Company | Passed checks | Partially captured checks | Failed checks |
| --- | ---: | ---: | ---: |
| Amazon | 4 | 3 | 0 |
| Uber | 5 | 2 | 0 |
| Shopify | 7 | 0 | 0 |
| Total | 16 | 5 | 0 |

These counts cover the 21 predefined checks, not all possible errors. The review
also found two unsupported assertions outside that rubric: an Uber carrier-to-service
supplier edge broader than the input establishes, and a Shopify physical-payment
classification that the input leaves unresolved.

The partially captured requirements were Amazon's separately addressable cloud
offerings, internal infrastructure usage, and explicit shipping-benefit connection;
and Uber's shipper/payment relationships and membership links across its businesses.
Relevant words often survived in labels without the corresponding graph structure.

See the [Amazon audit](audits/amazon.json), [Uber audit](audits/uber.json), and
[Shopify audit](audits/shopify.json) for every check and additional finding. All three
are judged insufficient to support a full eight-section report from these inputs alone.

## What this changes in the design

The same object shape can represent materially different businesses. Nesting alone
is insufficient: shared resources, marketplace interactions, and reporting
classifications require explicit references between branches.

The next schema iteration should:

1. Separate independently useful facts into addressable nodes or typed fields, so
   computing, storage, and databases do not disappear into a single prose label.
2. Represent bundle benefits, customer payments, internal resource use, and shared
   capabilities with explicit, directionally correct relationships. Test those
   relationships against the supplied statements; valid IDs alone are insufficient.
3. Model reporting segments, revenue components, and channel classifications as
   different dimensions. A cross-cutting category must not be accidentally summed
   with the components it overlaps.
4. Permit multiple contextual roles for one participant and preserve constraints,
   exclusions, pricing units, geography, and time periods in structured fields.
5. Define readiness per report section: supplied facts, reasonable disclosed
   inferences, and missing inputs. Expand a node only when a required field can be
   recovered from available input; otherwise record a focused question and stop.

These objects provide useful scope and research instructions. They do not supply
missing market sizes, prices, competitor comparisons, or observed customer feedback.
A longer object is not additional evidence. The report still needs research or
customer clarification for facts absent from the input.

## Files and reproduction

- [Amazon input and source references](inputs/amazon.json) · [Object](runs/normalized/amazon/object.json)
- [Uber input and source references](inputs/uber.json) · [Object](runs/normalized/uber/object.json)
- [Shopify input and source references](inputs/shopify.json) · [Object](runs/normalized/shopify/object.json)
- [Standalone contract and runner](../../../experiments/business_object_trial/README.md)

The experiment is outside production PREP. Source-to-claim tracing remains disabled
in the report pipeline; statement IDs here are local extraction-test anchors only.

Structural and parser tests: `python3 -m unittest test_business_object_trial`.
The current test run passes 12 tests and 38 subtests. These tests validate schema,
references, bounds, and exact wrapper normalization, not semantic truth.
