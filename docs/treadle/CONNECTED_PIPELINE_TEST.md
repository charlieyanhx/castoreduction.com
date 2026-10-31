# Connected business-object report pipeline

All new customer reports now run:

`survey → recursive business object → input-support review → report research specification → dependency workflow → consistency review/corrections → fixed report`

The business-object and specification stages share the existing isolated `prep` task.
Every research section receives the full accepted object plus its own starting-input
requirements. Corrections to downstream research reuse PREP. The report-wide maximum
remains two correction rounds, with shared deadlines and call budgets throughout.

## Inspect without calling a model

```sh
.venv/bin/python scripts/treadle_market_research.py --describe
```

Check that `preparation.business_object_enabled` is true, the PREP schema is version 2,
and source tracing is false. Construction defaults to six calls (including support
review) and depth six. The specification adds one call; all count against the same
execution allowances. Partial construction remains disclosed. Failed construction or
support review prevents research from starting.

## Live test when ready

Set `TREADLE_PREP_MODEL` to the model chosen for the test. Configure the existing model
credentials, research providers, and paid-provider opt-in outside source files. No live
report was run as part of this integration change.

```sh
.venv/bin/python scripts/treadle_market_research.py \
  --brief docs/treadle/example_hybrid_venture.json \
  --out out/connected-hybrid \
  --workers 4 --correction-rounds 2 \
  --business-calls 6 --business-depth 6 \
  --run-timeout 900 --task-timeout 300 --review-timeout 90 \
  --model-calls 120 --tool-calls 60
```

The sample is fictional test input. The command saves JSON and HTML, including partial
reports; exit 0 means the workflow completed without unresolved execution/review issues,
exit 1 means the report remains incomplete, and exit 2 indicates a command/input error.
Execution success is not a measurement of research quality.

## What to inspect

- `result.prep.business_context.object`: distinct revenue streams, both physical sites,
  online delivery, customer/buyer roles, relationships, and qualifiers survive extraction.
- `result.prep.venture.customer_inputs`: the original brief remains unchanged. Unknown
  pricing and provisional intake are not converted into confirmed facts.
- `result.prep.business_context.construction`: actual calls, pending branches, limits,
  excluded-record count, and complete/partial status are explicit.
- `result.prep.research_spec`: all eight sections have business-specific questions and
  code-owned `input_readiness`. Missing data stays visible rather than becoming numbers.
- Section findings: distinguish per-site, online, subscription, workshop, and licensing
  assumptions; do not double-count the same customers or aggregate incompatible units.
- `execution.history` and `execution.usage`: targeted corrections regenerate consumers,
  unused results are retained, no more than two correction rounds occur, and budgets hold.
- HTML: construction limitations, initially unanswered questions, research limitations,
  and unresolved review findings are visible and understandable.

Source tracing remains disabled. The customer survey uses Treadle exclusively. Numeric
modeling refinements and the known semantic extraction/projection gaps should be assessed
during these live tests; connecting the stages does not resolve them automatically.

## Run through a real browser

The customer `/survey` uses **Treadle** for every new report, with no pipeline or format
selector. Older saved drafts keep their answers and launch a full Treadle analysis.
`/plan` defaults to Treadle/full; explicit `pipeline: "legacy"`, memo, and operating
formats are rejected before a job or charge is created. Treadle runs the recursive business builder,
PREP specification, bounded dependency workflow, review, and fixed full report through
the normal owned job, progress, HTML/PDF, and library routes.

Current limits: full-analysis format only, at most two correction rounds, and source
tracing disabled. Incomplete runs retain readable partial sections and explicit
limitations. Progress updates appear after each research round. An interrupted Treadle
run is not automatically restarted with fresh budgets; its saved state remains available.
Legacy workshop, revision, and publishing actions are unavailable for Treadle reports,
and their purchase/mutation endpoints refuse them before charging.

Set `TREADLE_PREP_MODEL` explicitly before starting the customer app with `uvicorn api:app`.
Missing model/provider configuration is refused before a job or charge is created.

The separate localhost test page below also exercises the Treadle path with real providers.
It loads the repository `.env`; keep credentials there or in the process environment.
Select the PREP model explicitly.

```sh
.venv/bin/python scripts/treadle_browser_server.py \
  --port 8779 --prep-model YOUR_PREP_MODEL \
  --out-dir out/treadle-browser/server
```

In another terminal:

```sh
.venv/bin/python scripts/treadle_browser_e2e.py \
  --base-url http://127.0.0.1:8779 \
  --brief docs/treadle/example_hybrid_venture.json \
  --out-dir out/treadle-browser/browser
```

The driver uses Python Playwright and installed Google Chrome on macOS, with a fresh
browser profile. Use `--chrome /path/to/chrome` for another executable or `--headed`
to watch. Each invocation submits one paid report through the form; there are no
automatic report retries. The server allows one active report and applies the limits
shown on the page. An incomplete report fails the pipeline checks.

Artifacts include the report HTML/JSON, desktop/mobile screenshots, `trace.zip`,
`summary.json`, JUnit XML, and an HTML test summary. Browser checks and pipeline
checks have separate outcomes. These check execution and structure, not research
accuracy. To inspect the same run again without additional model work:

```sh
.venv/bin/python scripts/treadle_browser_e2e.py \
  --base-url http://127.0.0.1:8779 \
  --resume-run RUN_ID \
  --out-dir out/treadle-browser/resume
```

Read the run ID from the first driver's `run.json`. Let an active report finish
before shutting down the local server.

## Treadle-only verification — 2026-10-20

The final customer-route, delivery, adapter, payment, historical resume, format, and
boundary regression suite passed **90 tests and 3 subtests**. All **six Playwright
journeys** passed, including a restored draft containing `pipeline: "legacy"` and
`report_style: "memo"`; its answers survived and its new report ran Treadle/full.
The browser harness rejects any legacy runner invocation. It uses the actual business
builder, PREP, scheduler, persistence, and delivery with simulated model responses.
No paid model report was started during this verification.

Browser artifacts: `out/survey-revision-e2e/20261020-143010-38841/`.
Python, JavaScript, and general code reviews approved the final change. Ruff, JavaScript
syntax, and whitespace checks passed. Localhost port 8780 was restarted without an
active report; health, served survey code, API defaults, and rejection of explicit
legacy selection were verified on the running server.
