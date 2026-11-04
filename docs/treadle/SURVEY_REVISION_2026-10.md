# Customer survey revision — 2026-10-20

The user requested replacing the current survey with the supplied “Claude revisions”
opening, Core Pack, and More Specific Questions. The canonical wording and order live
in `survey_questions.py`; the browser and intake API use that catalog.

## Customer journey

1. Original business description.
2. Seven Core Pack questions.
3. Seven More Specific Questions, plus the existing report-style choice.
4. Review the description and all answers, then explicitly launch the report.

Every follow-up is optional and accepts a paragraph. Revenue streams, location details,
capacity intervals, and cost breakdowns remain intact. Back, Skip, reload, account
creation, and payment return retain the existing journey. Skip preserves previously
saved answers and discards unsaved edits. Clearing a field and choosing Continue clears
that answer.

The former arithmetic preview is absent from this journey because these free-form
answers cannot safely drive single-price or single-location break-even calculations.

## Data contract

| Group | Fields, in order |
|---|---|
| Core | `status_quo`, `monthly_cost_estimate`, `customer_evidence`, `named_competitors`, `success_target`, `business_model`, `delivery_mode` |
| Specific | `geography`, `service_capacity`, `scalability`, `acquisition_channels`, `unit_economics`, `fixed_costs`, `constraints` |

New sessions use `survey_version: "2026-10"`. The browser includes that marker in form
submissions, allowing old drafts to adopt the new paragraph semantics. Unsupported
versions are rejected; legacy clients without the marker retain their existing typed
answer behavior.

The initial description is retained as `original_description`. Form answers are
customer-stated text. Extractor suggestions are labelled unconfirmed and are not silently
submitted or promoted into confirmed facts.

`intake.unknowns` continues to record unavailable values. `intake.unanswered` identifies
optional blanks separately from explicit unknown answers. PREP can interpret information
already present in the original description when a follow-up was skipped. It must
preserve the uncertainty of an explicit unknown and cannot restore stale structured
answers for skipped fields.

Monthly cost paragraphs remain research input. The legacy financial anchor accepts only
an unambiguous monthly USD total from revised text slots; breakdowns, ranges, other
currencies, or unclear units do not become a founder-sourced numeric total. Existing
legacy numeric slots retain their prior contract.

## Scope

This is an explicitly authorized product revision during the Treadle extraction work.
The exact file exception is `APPROVED_SURVEY_FILES` in `scripts/treadle_guard.py`, including
the narrow cost-anchor compatibility fix in `pricing.py`. It does not expand permitted
directory prefixes or alter the golden report baseline.

The customer survey launches through the existing `/plan` route. The subsequent
connection request wired Treadle into the customer flow. The subsequent Treadle-only
request removed pipeline and format choices: every new survey or API report uses
Treadle/full, including drafts saved with legacy preferences. Explicit legacy API
selection is refused before creating a job or charging.
See [the connected pipeline guide](CONNECTED_PIPELINE_TEST.md).

## Verification

`test_revised_survey.py` covers the question contract, raw answers, authorship, skipped
answers, restored groups, version migration, and financial-anchor compatibility.
`test_treadle_revised_survey_input.py` covers PREP's treatment of optional blanks versus
explicit unknowns.

Run the isolated browser suite with:

```sh
CASTOR_RUN_SURVEY_BROWSER=1 .venv/bin/python -m unittest -v test_revised_survey_browser
```

It starts the real application on temporary storage, drives the survey with Playwright,
and verifies the intake passed through `/plan`. Model extraction and report research are
test doubles; external connections are blocked. Screenshots and traces are saved under
`out/survey-revision-e2e/`. These checks do not constitute a new live research run.

The final combined backend regression run passed 360 tests and 167 subtests, including
the unchanged golden report baseline. Python and general code reviews approved the
changes. JavaScript syntax and whitespace checks passed.

All four final Chrome/Playwright journeys passed in 105 seconds with zero browser
errors: a completed hybrid business, mobile optional skips, account creation with
competing launch actions, and legacy typed-cost restoration. Artifacts are in
`out/survey-revision-e2e/20261020-114939-40870/`.

The worktree-wide file guard still reports the pre-existing `docs/README.md` edit. That
file is outside this survey change and was neither reverted nor added to the exception.
