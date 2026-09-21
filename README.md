# Castor Advisories

Castor turns a founder’s survey answers into a market-research report: competitive
landscape, customer evidence, market sizing, pricing, economics, projections, and
recommendations. Reports have source/provenance disclosures and automated checks.

The current implementation combines an explicit Python research pipeline with registered
tools, skills, and bounded research agents. A fully adaptive planner and central
review-and-repair controller are proposed, not the default execution path.

## Run locally

```bash
./install.sh
# Edit .env with the provider credentials and configuration you intend to use.
.venv/bin/python -m playwright install chromium
.venv/bin/uvicorn api:app --port 8000
```

Open `http://localhost:8000`. See `.env.example` and [DEPLOY.md](DEPLOY.md) for
configuration and deployment. Report duration depends on effort, provider limits, source
availability, and writing; there is no fixed completion-time guarantee.

## How a report is produced

1. The survey records confirmed inputs and unresolved questions.
2. `/plan` starts a background job that runs `plan.run_plan`, saving progress/checkpoints.
3. Research steps gather competitor, pricing, customer-voice, geographic, and economic
   evidence. Some analysis is model-generated or simulated and must be disclosed as such.
4. Sizing routes by venture scale: local trade area, city, regional rollout, or national /
   digital market. Economics and financial calculations use the resulting structured data.
5. The pipeline builds recommendations and viability, then verifies the evidence and page.
6. When enabled and configured, a separate analyst-writing pass reads the assembled
   evidence and produces a memo, full report, or operating plan. Writing receives its own
   citation/number checks.
7. The app renders HTML, a downloadable PDF, and an investor one-pager. Blocking findings
   withhold normal delivery. A verification failure is currently disclosed as unverified;
   it is not automatically treated as a blocking verdict.

| Effort | Current behavior |
| --- | --- |
| Quick | Narrower research, no final analyst-writing pass |
| Standard | Normal research plus analyst writing when its backend is enabled |
| Deep | Broader research, dynamically selected specialist crew, optional LLM verification, and analyst writing |

The analyst writer uses the model configured in `report/synthesis.py` and requires the
Anthropic key and paid-backend opt-in. Other model calls use `llm.py` and its configured
backend/fallback policy. Missing or failed sections are recorded rather than presented as
successful research.

The workshop supports evidence-based questions, private notes, rewriting existing facts,
rerunning corrected inputs, and finalizing a report for sharing. A rewrite does not perform
fresh research. Ownership and purchase checks apply at the API, not only in the browser.

## Test and inspect

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
./test_all.sh -q                         # full offline regression suite
.venv/bin/python -m ruff check .         # production static checks
./bench.sh list                          # registered tools, skills, and agents
./bench.sh doctor                        # fixture coverage; makes no source/model calls
```

The offline suite scrubs model credentials, prevents dotenv from restoring them, blocks
external socket connections, and substitutes deterministic model responses. It exercises
wiring and behavior, not external-source availability. See `conftest.py` for explicit
live-test exceptions. Test counts change; use the suite output instead of a README count.

One test drives the product in a real browser. `test_the_founder_path_in_a_real_browser.py`
starts the stand-in server and, in headless Chromium, reads the report, asks the analyst,
explains a selected passage, leaves a note, rewrites, runs out of credits and buys more,
marks the report final, follows a new run from the progress page to its report, and opens
it on a phone. It needs Playwright's Chromium (`.venv/bin/python -m playwright install
chromium`) and skips, saying so, without it.

### Work on the page without a key

```bash
.venv/bin/python scripts/dev_workshop_server.py        # then open http://127.0.0.1:8767/dev/claim
```

The real app on a throwaway database with one finished report and a stand-in analyst: every
route, credit and gate is real, only the model is a stand-in. Type "invent" in a question to
see an answer refused by the number audit, "fail" to see a failed turn refund, "slow" for a
twelve-second answer. With `CASTOR_DEV_FAST_RUNS=1` a new report or a re-run hands back the
fixture in two seconds instead of running the research.

### Exercise a capability without a report

```bash
./bench.sh hackernews_mentions query="specialty coffee"
./bench.sh list --kind skill
./bench.sh list --match census
./bench.sh market_scan_agent --prompt "a mobile dog grooming van in Portland" --llm real --full
./bench.sh smoke --kind tool --llm off --fresh-http
./bench.sh smoke                         # cached model calls by default
```

Single-capability calls default to real model access. Smoke runs default to cached model
responses and skip metered and whole-report capabilities unless explicitly included.
`--llm off` disables model calls, not network requests. `--fresh-http` bypasses the HTTP
cache. Read `./bench.sh smoke --help` for filters and budget-related options.

The bench reports `ok`, `empty`, `skeleton`, `refused`, `error`, `timeout`, and model-cache
misses separately. Smoke results and changes from the previous sweep are stored under
`out/bench/`; a cache miss does not certify that a model-dependent capability works.

For a deliberate end-to-end source/model run:

```bash
.venv/bin/python -m tools.run_live audit01 "a specialty coffee shop in Portland" US standard
```

This writes JSON/HTML under `out/live/`. Failed, blocked, unverified, and render-failed runs
exit nonzero. Inspect the saved verification findings and report, not just the exit code.
Live research can consume configured provider quotas and paid model usage.

## Code locations

| Area | Source |
| --- | --- |
| HTTP entry and ownership/billing | `api.py`, `routes/`, `auth.py`, `billing.py` |
| Survey and workshop | `intake.py`, `intake_tree.py`, `iteration.py`, `workshop.py`, `web/` |
| Jobs, persistence, and resume | `jobs.py`, `persistence/` |
| Main orchestration | `plan.py`, `orchestrator/steps/`, `orchestrator/sections.py` |
| Dependency contracts and evidence | `core/section.py`, `core/evidence.py` |
| Capability registries and execution | `tools/`, `skills/`, `agents/`, `harness/`, `capabilities/` |
| Numerical models | `market_sizing.py`, `skills/sizing/`, `economics.py`, `financials.py` |
| Verification and report writing | `gates/`, `report/verifier.py`, `report/synthesis.py` |
| HTML/PDF rendering | `report/render_html.py`, `report/pdf.py`, `templates/` |
| Independent capability testing | `bench/`, `bench.sh` |
| Regression tests and corpus evaluation | `test_*.py`, `tests/fixtures/`, `benchmarks/` |

See [current architecture](docs/ARCHITECTURE.md), [proposed research/review
migration](docs/RESEARCH_REVIEW_DESIGN.md), and [cleanup review](docs/CLEANUP_REVIEW.md).
Historical audit results describe the revision and corpus they measured; they are not a
claim that those results apply unchanged to this checkout.
