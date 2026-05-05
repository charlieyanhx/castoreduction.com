# Method

This branch explains **what** the system does and **how** it does it. Each file is a focused module of the design.

| File | Topic |
|---|---|
| [`01-pipeline-overview.md`](./01-pipeline-overview.md) | The 22-step market research pipeline — inputs, outputs, parallelism, retry behavior |
| [`02-benchmark-rubric.md`](./02-benchmark-rubric.md) | The 16-dimension benchmark rubric — every scorer formula, weight, and pass criterion |
| [`03-prose-judge.md`](./03-prose-judge.md) | LLM-as-judge for the 4Ps prose — deterministic vs LLM-judged traits, partner-style rubric |
| [`04-test-cases.md`](./04-test-cases.md) | How benchmark cases are constructed, reference data, and how to add a new case |
| [`05-scoring-formula.md`](./05-scoring-formula.md) | Scoring math reference card — letter grades, edge cases, weight tables |

## At-a-glance system diagram

```
              venture description (text)
                       │
          ┌────────────▼────────────┐
          │  /plan API endpoint     │
          │  (api.py)               │
          └────────────┬────────────┘
                       │ submit job
                       ▼
          ┌─────────────────────────────────────────────────────────┐
          │                    plan.run_plan                        │
          │                                                         │
          │  Step 1-2: profile extraction                           │
          │  Step 3:   competitor discovery (parallel scrapers)     │
          │  Step 3b:  firmographics                                │
          │  Step 3c:  semantic clustering (HDBSCAN+UMAP)           │
          │  Step 3d:  differentiators (5-dim parallel)             │
          │  Step 5:   customer universe (5 methods, 1 LLM ICP)     │
          │  Step 6a:  taste decoder × top-3 competitors            │
          │  Step 6b:  competitor pricing scrapes                   │
          │  Step 6c:  reddit signal                                │
          │  Step 6d:  hackernews signal                            │
          │  Step 6e:  stackoverflow + dev.to + lobsters (parallel) │
          │  Step 7-8: persona synthesis + segment ranking          │
          │  Step 9:   max-diff feature ranking                     │
          │  Step 10:  van-westendorp PSM + unit economics          │
          │  Step 11:  place / channel strategy                     │
          │  Step 12:  validation gate (intermediate)               │
          │  Step 13:  market sizing TAM (3 parallel methods)       │
          │  Step 13b: SAM + SOM + segmentation + meta              │
          │  Step 14:  4Ps narrative (4 parallel sections)          │
          │  Step 15:  growth scenarios (Y1/Y2/Y3)                  │
          │  Step 16:  viability score (with retry on fail)         │
          │  Step 17:  validation gate (final)                      │
          └────────────┬────────────────────────────────────────────┘
                       │ persisted to job-store
                       ▼
          ┌────────────────────────────────────────────┐
          │  /jobs/<id>/report.html — Jinja2 template  │
          │  /jobs/<id>/report.pdf — Playwright print  │
          └────────────────────────────────────────────┘
```

## Where to look in code

| Layer | Files |
|---|---|
| HTTP API | `api.py` |
| Pipeline orchestrator | `plan.py` |
| LLM provider abstraction | `llm.py` (Anthropic, Groq, Gemini fallback chain) |
| Component modules | `taste.py`, `customer_universe.py`, `differentiators.py`, `personas.py`, `pricing.py`, `place.py`, `market_sizing.py`, `four_ps.py`, `clustering.py`, `firmographics.py`, `reddit_signal.py`, `economics.py`, `financials.py`, `segment_scoring.py` |
| Scrapers | `sources.py`, `scrape/{http.py, search.py, structured.py, wayback.py, crawl.py}` |
| Templates | `templates/report.html`, `templates/onepager.html` |
| Benchmark | `benchmarks/{score.py, prose_judge.py, run_all.py, cases/*.json}` |
| Tests | `test_infra.py`, `test_integration.py`, `test_api.py` |
